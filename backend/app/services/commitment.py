"""Transactional offer publication and acceptance commands for the I0 slice."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.enums import (
    AggregateType,
    ApiErrorCode,
    AssignmentStatus,
    EventType,
    LeaseStatus,
    LotState,
    OfferStatus,
    ReasonCode,
    ItemClass,
)
from app.core.clock import RealClock, ScenarioClock
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.core.idempotency import execute_idempotently
from app.domain.eta import Coordinates, DeterministicEtaProvider
from app.domain.inventory import BatchBuckets
from app.domain.rules import (
    CapabilityEvidence,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
    evaluate_compatibility,
)
from app.domain.transitions import transition_lot
from app.engines.feasibility import evaluate_pre_pickup
from app.models import (
    Assignment,
    Batch,
    BatchInventory,
    Demand,
    Donor,
    Event,
    Lease,
    Lot,
    Offer,
    Rider,
    Scenario,
    Shelter,
    User,
)
from app.repositories.demands import demand_ledger
from app.repositories.jobs import LEASE_EXPIRY, LEASE_FIRST_PROGRESS, OFFER_EXPIRY, schedule_action
from app.services.lease import create_lease


HANDOVER_SECONDS = 120
HANDLING_BUFFER_SECONDS = 120
DROPOFF_SECONDS = 60
RIDER_LOCATION_MAX_AGE_SECONDS = 300


def publish_offer(
    *,
    actor_id: UUID,
    batch_id: UUID,
    demand_id: UUID,
    rider_id: UUID,
    quantity: int,
    expires_at: datetime,
    expected_batch_version: int,
    expected_demand_version: int,
    expected_rider_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    """Publish one advisory offer without reserving inventory or demand."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise _error(ApiErrorCode.INVALID_QUANTITY, "quantity must be a positive integer", 422)
    _aware(expires_at, "expires_at")

    def operation(session: Session) -> dict[str, object]:
        actor_snapshot = session.get(User, actor_id)
        if actor_snapshot is None:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        scenario_id = actor_snapshot.scenario_id
        order = LockOrder()

        def create() -> dict[str, object]:
            if scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
            actor = _lock_actor(session, actor_id, scenario_id, order)
            if actor.role not in {"DONOR", "COORDINATOR", "JUDGE"}:
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Only a donor or coordinator can publish offers", 403)
            # Resolve stable foreign keys without locks, then acquire locks in the published order.
            batch_ref = session.get(Batch, batch_id)
            demand_ref = session.get(Demand, demand_id)
            rider_ref = session.get(Rider, rider_id)
            if batch_ref is None or demand_ref is None or rider_ref is None:
                raise _error(ApiErrorCode.NOT_FOUND, "Batch, demand, or rider was not found", 404)
            donor_rows = lock_rows(session, Donor, LockRank.DONOR, [batch_ref.donor_id], order)
            if not donor_rows or not donor_rows[0].verified:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Donor location or verification evidence is unavailable", 422)
            donor = donor_rows[0]
            if actor.role == "DONOR":
                if donor.user_id != actor.id:
                    raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Batch does not belong to the authenticated donor", 403)

            batches = lock_rows(session, Batch, LockRank.BATCH, [batch_id], order)
            inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_id], order)
            if not batches or not inventories:
                raise _error(ApiErrorCode.NOT_FOUND, "Batch inventory was not found", 404)
            batch, inventory = batches[0], inventories[0]
            lot_id = uuid4()
            order.acquire(LockRank.LOT, [lot_id])
            shelters = lock_rows(session, Shelter, LockRank.SHELTER, [demand_ref.shelter_id], order)
            demands = lock_rows(session, Demand, LockRank.DEMAND, [demand_id], order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_id], order)
            if not shelters or not demands or not riders:
                raise _error(ApiErrorCode.NOT_FOUND, "Demand, shelter, or rider was not found", 404)
            shelter, demand, rider = shelters[0], demands[0], riders[0]
            now = _now(session, scenario_id)
            _same_scope(scenario_id, batch.scenario_id, demand.scenario_id, shelter.scenario_id, rider.scenario_id)
            if actor.scenario_id != scenario_id:
                raise _error(ApiErrorCode.PLAN_STALE, "Actor scenario changed; refresh and retry", 409)
            if batch.version != expected_batch_version or demand.version != expected_demand_version or rider.version != expected_rider_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Batch, demand, or rider version is stale", 409)
            if expires_at <= now:
                raise _error(ApiErrorCode.PLAN_STALE, "Offer expiry must be later than authoritative time", 422)
            if expires_at > batch.deliver_by:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Offer cannot remain valid beyond the batch delivery deadline", 422)
            if batch.ready_at > now or now >= batch.deliver_by:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Batch is not ready or has passed its delivery deadline", 422)
            if batch.status != "AVAILABLE" or quantity > inventory.available:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Insufficient currently available batch quantity", 409)
            if batch.item_class != demand.item_class or batch.unit != demand.unit:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Batch item class and unit do not match demand", 422)
            ledger = demand_ledger(demand)
            if not shelter.intake_open or not ledger.intake_open:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Shelter intake is closed", 409)
            if quantity > ledger.uncommitted_need:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Offer exceeds uncommitted demand", 409)
            if not rider.available or quantity > rider.capacity or _has_active_assignment(session, rider.id):
                raise _error(ApiErrorCode.RIDER_ALREADY_ASSIGNED, "Rider is unavailable or cannot carry this lot", 409)
            _check_dispatch_safety(batch, donor, rider, shelter, now, scenario_id)
            buckets = BatchBuckets(
                total=batch.total_qty,
                available=inventory.available,
                reserved=inventory.reserved,
                carried=inventory.carried,
                delivered=inventory.delivered,
                disposed=inventory.disposed,
            )
            lot = Lot(
                id=lot_id,
                scenario_id=scenario_id,
                batch_id=batch.id,
                qty=quantity,
                state=LotState.OFFERED.value,
                version=0,
            )
            offer = Offer(
                id=uuid4(),
                scenario_id=scenario_id,
                lot_id=lot.id,
                rider_id=rider.id,
                demand_id=demand.id,
                qty=quantity,
                status=OfferStatus.PUBLISHED.value,
                expires_at=expires_at.astimezone(timezone.utc),
                version=0,
            )
            order.acquire(LockRank.OFFER, [offer.id])
            session.add_all([lot, offer])
            _add_event(
                session,
                order=order,
                actor_id=actor.id,
                scenario_id=scenario_id,
                aggregate_type=AggregateType.LOT.value,
                aggregate_id=lot.id,
                event_type=EventType.OFFER_PUBLISHED.value,
                occurred_at=now,
                payload={
                    "offer_id": str(offer.id),
                    "batch_id": str(batch.id),
                    "demand_id": str(demand.id),
                    "rider_id": str(rider.id),
                    "quantity": quantity,
                    "inventory_reserved": False,
                    "demand_reserved": False,
                    "inventory_available": buckets.available,
                    "demand_uncommitted_need": ledger.uncommitted_need,
                },
            )
            schedule_action(
                session,
                scenario_id=scenario_id,
                action_type=OFFER_EXPIRY,
                aggregate_id=lot.id,
                due_at=offer.expires_at,
                payload={"offer_id": str(offer.id), "expected_version": offer.version},
                dedupe_key=f"offer-expiry:{offer.id}:v{offer.version}",
                generation=offer.version,
            )
            session.flush()
            return _offer_snapshot(offer, lot)

        try:
            result, _ = execute_idempotently(
                session,
                lock_order=order,
                scenario_id=scenario_id,
                actor_id=actor_id,
                endpoint="POST /api/v1/dispatch/commit",
                key=idempotency_key,
                request_payload={
                    "batch_id": batch_id,
                    "demand_id": demand_id,
                    "rider_id": rider_id,
                    "quantity": quantity,
                    "expires_at": expires_at,
                    "expected_batch_version": expected_batch_version,
                    "expected_demand_version": expected_demand_version,
                    "expected_rider_version": expected_rider_version,
                },
                action=create,
                serialize=jsonable_encoder,
                deserialize=lambda body: body,
                response_status=201,
            )
            return result
        except IntegrityError as exc:
            raise _error(ApiErrorCode.STATE_CONFLICT, "Offer conflicts with current resource state", 409) from exc

    return run_transaction(operation)


def accept_lot(
    *,
    actor_id: UUID,
    lot_id: UUID,
    rider_id: UUID,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    """Atomically reserve stock and demand and issue an exclusive pickup lease."""
    def operation(session: Session) -> dict[str, object]:
        actor_snapshot = session.get(User, actor_id)
        if actor_snapshot is None:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        scenario_id = actor_snapshot.scenario_id
        order = LockOrder()

        def accept() -> dict[str, object]:
            if scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
            actor = _lock_actor(session, actor_id, scenario_id, order)
            if actor.role != "RIDER":
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "RIDER role is required to accept an offer", 403)
            lot_ref = session.get(Lot, lot_id)
            offer_ref = session.scalar(
                select(Offer).where(
                    Offer.lot_id == lot_id,
                    Offer.rider_id == rider_id,
                    Offer.status == OfferStatus.PUBLISHED.value,
                    Offer.scenario_id.is_not_distinct_from(scenario_id),
                ).order_by(Offer.id).limit(1)
            )
            rider_ref = session.get(Rider, rider_id)
            if lot_ref is None or offer_ref is None or rider_ref is None:
                raise _error(ApiErrorCode.NOT_FOUND, "Published offer was not found", 404)
            if rider_ref.user_id != actor.id:
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Offer belongs to another rider", 403)
            batch_ref = session.get(Batch, lot_ref.batch_id)
            demand_ref = session.get(Demand, offer_ref.demand_id)
            if batch_ref is None or demand_ref is None:
                raise _error(ApiErrorCode.NOT_FOUND, "Offer resources were not found", 404)

            donor_rows = lock_rows(session, Donor, LockRank.DONOR, [batch_ref.donor_id], order)
            batches = lock_rows(session, Batch, LockRank.BATCH, [batch_ref.id], order)
            inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_ref.id], order)
            lots = lock_rows(session, Lot, LockRank.LOT, [lot_id], order)
            shelters = lock_rows(session, Shelter, LockRank.SHELTER, [demand_ref.shelter_id], order)
            demands = lock_rows(session, Demand, LockRank.DEMAND, [demand_ref.id], order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_id], order)
            offers = lock_rows(session, Offer, LockRank.OFFER, [offer_ref.id], order)
            if not all((donor_rows, batches, inventories, lots, shelters, demands, riders, offers)):
                raise _error(ApiErrorCode.NOT_FOUND, "Offer resources were not found", 404)
            donor, batch, inventory, lot, shelter, demand, rider, offer = (
                donor_rows[0], batches[0], inventories[0], lots[0], shelters[0], demands[0], riders[0], offers[0]
            )
            now = _now(session, scenario_id)
            _same_scope(scenario_id, batch.scenario_id, lot.scenario_id, offer.scenario_id, demand.scenario_id, shelter.scenario_id, rider.scenario_id)
            if lot.version != expected_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Lot version is stale", 409)
            if lot.state != LotState.OFFERED.value or offer.status != OfferStatus.PUBLISHED.value:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Lot no longer has a published offer", 409)
            if now >= offer.expires_at:
                raise _error(ApiErrorCode.LEASE_INVALID, "Offer has expired", 409)
            if now < batch.ready_at or now >= batch.deliver_by:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Batch is not ready or has passed its delivery deadline", 422)
            if not shelter.intake_open or not demand.intake_open:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Shelter intake is closed", 409)
            if batch.item_class != demand.item_class or batch.unit != demand.unit:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Batch and demand compatibility changed", 409)
            if not rider.available or offer.qty > rider.capacity:
                raise _error(ApiErrorCode.RIDER_ALREADY_ASSIGNED, "Rider is unavailable or over capacity", 409)
            buckets = _buckets(batch, inventory)
            ledger = demand_ledger(demand)
            if offer.qty > buckets.available:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Batch stock changed after offer publication", 409)
            if offer.qty > ledger.uncommitted_need:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Demand changed after offer publication", 409)
            if _has_active_assignment(session, rider.id):
                raise _error(ApiErrorCode.RIDER_ALREADY_ASSIGNED, "Rider already has an active assignment", 409)
            if not donor.verified:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Donor location or verification evidence is unavailable", 422)
            _check_dispatch_safety(batch, donor, rider, shelter, now, scenario_id)
            active_lease_id = session.scalar(
                select(Lease.id).where(Lease.rider_id == rider.id, Lease.status == LeaseStatus.ACTIVE.value)
            )
            if active_lease_id is not None:
                active_leases = lock_rows(session, Lease, LockRank.LEASE, [active_lease_id], order)
                if active_leases:
                    raise _error(ApiErrorCode.RIDER_ALREADY_ASSIGNED, "Rider already has an active lease", 409)

            reservation = buckets.reserve(offer.qty)
            inventory.available = reservation.available
            inventory.reserved = reservation.reserved
            inventory.version += 1
            reserved_ledger = ledger.reserve(offer.qty)
            demand.reserved_qty = reserved_ledger.reserved
            demand.version += 1
            assignment_id = uuid4()
            order.acquire(LockRank.ASSIGNMENT, [assignment_id])
            lease = create_lease(
                scenario_id=scenario_id,
                lot_id=lot.id,
                rider_id=rider.id,
                assignment_id=assignment_id,
                now=now,
                physical_cutoff_at=batch.deliver_by,
            )
            order.acquire(LockRank.LEASE, [lease.id])
            transition_lot(lot, LotState.LEASED)
            offer.status = OfferStatus.ACCEPTED.value
            offer.version += 1
            rider.available = False
            rider.version += 1
            assignment = Assignment(
                id=assignment_id,
                scenario_id=scenario_id,
                lot_id=lot.id,
                rider_id=rider.id,
                demand_id=demand.id,
                status=AssignmentStatus.RESERVED.value,
                reserved_qty=offer.qty,
                picked_qty=0,
                accepted_qty=0,
                rejected_qty=0,
                carried_qty=0,
                risk_flags=[],
                version=0,
            )
            session.add_all([assignment, lease])
            _add_events(
                session,
                order=order,
                actor_id=actor.id,
                scenario_id=scenario_id,
                occurred_at=now,
                events=(
                    (AggregateType.LOT.value, lot.id, EventType.LEASE_ISSUED.value, {
                        "offer_id": str(offer.id), "assignment_id": str(assignment.id), "lease_id": str(lease.id),
                        "qty": offer.qty, "expires_at": lease.expires_at.isoformat(), "first_progress_by": lease.first_progress_by.isoformat(),
                    }),
                    (AggregateType.DEMAND.value, demand.id, EventType.LEASE_ISSUED.value, {
                        "reserved_qty": offer.qty, "reserved_total": demand.reserved_qty, "version": demand.version,
                    }),
                ),
            )
            schedule_action(
                session,
                scenario_id=scenario_id,
                action_type=LEASE_FIRST_PROGRESS,
                aggregate_id=lot.id,
                due_at=lease.first_progress_by,
                payload={"lease_id": str(lease.id), "expected_version": lease.version},
                dedupe_key=f"lease-first-progress:{lease.id}:v{lease.version}",
                generation=lease.version,
            )
            schedule_action(
                session,
                scenario_id=scenario_id,
                action_type=LEASE_EXPIRY,
                aggregate_id=lot.id,
                due_at=lease.expires_at,
                payload={"lease_id": str(lease.id), "expected_version": lease.version},
                dedupe_key=f"lease-expiry:{lease.id}:v{lease.version}",
                generation=lease.version,
            )
            session.flush()
            return {
                "lot_id": lot.id,
                "state": lot.state,
                "version": lot.version,
                "offer_id": offer.id,
                "assignment_id": assignment.id,
                "lease_id": lease.id,
                "lease_status": lease.status,
                "lease_expires_at": lease.expires_at,
                "first_progress_by": lease.first_progress_by,
                "reserved_qty": assignment.reserved_qty,
            }

        try:
            result, _ = execute_idempotently(
                session,
                lock_order=order,
                scenario_id=scenario_id,
                actor_id=actor_id,
                endpoint=f"POST /api/v1/lots/{lot_id}/accept",
                key=idempotency_key,
                request_payload={"lot_id": lot_id, "rider_id": rider_id, "expected_version": expected_version},
                action=accept,
                serialize=jsonable_encoder,
                deserialize=lambda body: body,
            )
            return result
        except IntegrityError as exc:
            raise _error(ApiErrorCode.STATE_CONFLICT, "Lot or rider is already exclusively assigned", 409) from exc

    return run_transaction(operation)


def _lock_actor(session: Session, actor_id: UUID, scenario_id: UUID | None, order: LockOrder) -> User:
    actors = lock_rows(session, User, LockRank.ACTOR, [actor_id], order)
    if not actors or not actors[0].active:
        raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
    if actors[0].scenario_id != scenario_id:
        raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Actor is outside the requested scenario", 403)
    return actors[0]


def _now(session: Session, scenario_id: UUID | None) -> datetime:
    value = RealClock().now() if scenario_id is None else ScenarioClock(session, scenario_id).now()
    return value.astimezone(timezone.utc)


def _buckets(batch: Batch, inventory: BatchInventory) -> BatchBuckets:
    return BatchBuckets(batch.total_qty, inventory.available, inventory.reserved, inventory.carried, inventory.delivered, inventory.disposed)


def _has_active_assignment(session: Session, rider_id: UUID) -> bool:
    return session.scalar(
        select(Assignment.id).where(
            Assignment.rider_id == rider_id,
            Assignment.status.in_([AssignmentStatus.RESERVED.value, AssignmentStatus.IN_TRANSIT.value]),
        ).limit(1)
    ) is not None


def _same_scope(expected: UUID | None, *actual: UUID | None) -> None:
    if any(value != expected for value in actual):
        raise _error(ApiErrorCode.NOT_FOUND, "Resource is outside the current scenario", 404)


def _offer_snapshot(offer: Offer, lot: Lot) -> dict[str, object]:
    return {
        "offer_id": offer.id,
        "lot_id": lot.id,
        "rider_id": offer.rider_id,
        "demand_id": offer.demand_id,
        "qty": offer.qty,
        "status": offer.status,
        "expires_at": offer.expires_at,
        "version": offer.version,
        "lot_version": lot.version,
    }


def _add_event(
    session: Session,
    *,
    order: LockOrder,
    actor_id: UUID,
    scenario_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
) -> None:
    order.acquire(LockRank.EVENT_AND_SCHEDULE, [aggregate_id])
    session.add(Event(
        scenario_id=scenario_id,
        actor_id=actor_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=jsonable_encoder(payload),
        occurred_at=occurred_at,
    ))


def _add_events(session: Session, *, order: LockOrder, actor_id: UUID, scenario_id: UUID | None, occurred_at: datetime, events: tuple[tuple[str, UUID, str, dict[str, object]], ...]) -> None:
    order.acquire(LockRank.EVENT_AND_SCHEDULE, [item[1] for item in events])
    for aggregate_type, aggregate_id, event_type, payload in events:
        session.add(Event(
            scenario_id=scenario_id,
            actor_id=actor_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=jsonable_encoder(payload),
            occurred_at=occurred_at,
        ))


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _error(ApiErrorCode.PLAN_STALE, f"{name} must include a UTC offset", 422)


def _error(code: ApiErrorCode, message: str, status: int) -> ServiceError:
    return ServiceError(code=code, message=message, status_code=status)


def _check_dispatch_safety(batch: Batch, donor: Donor, rider: Rider, shelter: Shelter, now: datetime, scenario_id: UUID | None) -> None:
    if rider.latitude is None or rider.longitude is None:
        raise _error(ApiErrorCode.SAFETY_BLOCKED, "Rider location is required for a conservative ETA", 422)
    heartbeat = rider.last_heartbeat_at
    if (
        heartbeat is None
        or heartbeat.tzinfo is None
        or heartbeat.utcoffset() is None
        or (now - heartbeat.astimezone(timezone.utc)).total_seconds() < 0
        or (now - heartbeat.astimezone(timezone.utc)).total_seconds() > RIDER_LOCATION_MAX_AGE_SECONDS
    ):
        raise _error(ApiErrorCode.SAFETY_BLOCKED, "Rider location evidence is missing or stale", 422)
    try:
        requirements = _batch_requirements(batch)
        destination = _destination_capabilities(shelter)
        handling = _rider_capabilities(rider)
        compatibility = evaluate_compatibility(
            ItemClass(batch.item_class), destination, handling, requirements, now
        )
        if not compatibility.eligible:
            codes = ", ".join(reason.value for reason in compatibility.reasons)
            raise _error(ApiErrorCode.SAFETY_BLOCKED, f"Compatibility evidence is missing or unsafe: {codes}", 422)
        provider = DeterministicEtaProvider()
        route_eta = provider.eta_seconds(
            Coordinates(donor.latitude, donor.longitude),
            Coordinates(shelter.latitude, shelter.longitude),
            scenario_id,
        )
        rider_eta = provider.eta_seconds(
            Coordinates(rider.latitude, rider.longitude),
            Coordinates(donor.latitude, donor.longitude),
            scenario_id,
        )
        decision = evaluate_pre_pickup(
            now=now,
            rider_to_donor_eta_seconds=rider_eta,
            handover_seconds=HANDOVER_SECONDS,
            donor_to_shelter_eta_seconds=route_eta,
            handling_buffer_seconds=HANDLING_BUFFER_SECONDS + DROPOFF_SECONDS,
            deliver_by=batch.deliver_by,
        )
        if not decision.feasible:
            code = decision.reason_code.value if decision.reason_code else ReasonCode.SAFETY_ETA_INFEASIBLE.value
            raise _error(ApiErrorCode.SAFETY_BLOCKED, f"Dispatch fails B1 deadline feasibility: {code}", 422)
    except ValueError as exc:
        if isinstance(exc, ServiceError):
            raise
        raise _error(ApiErrorCode.SAFETY_BLOCKED, f"Dispatch safety evidence is invalid: {exc}", 422) from exc


def _batch_requirements(batch: Batch) -> CompatibilityRequirements:
    evidence = batch.safety_evidence or {}
    allergens = evidence.get("item_allergens")
    dietary = evidence.get("item_dietary_tags")
    return CompatibilityRequirements(
        formula_form=evidence.get("formula_form"),
        item_allergens=frozenset(allergens) if isinstance(allergens, list) and all(isinstance(value, str) for value in allergens) else None,
        item_dietary_tags=frozenset(dietary) if isinstance(dietary, list) and all(isinstance(value, str) for value in dietary) else None,
    )


def _destination_capabilities(shelter: Shelter) -> DestinationCapabilities:
    return DestinationCapabilities({
        key: CapabilityEvidence(value if isinstance(value, bool) else None, shelter.capability_verified_at)
        for key, value in (shelter.capabilities or {}).items()
    })


def _rider_capabilities(rider: Rider) -> RiderHandlingFeatures:
    return RiderHandlingFeatures({
        key: CapabilityEvidence(value if isinstance(value, bool) else None, rider.handling_verified_at)
        for key, value in (rider.handling_capabilities or {}).items()
    })
