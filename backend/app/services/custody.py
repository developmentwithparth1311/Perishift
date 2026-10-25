"""Transactional pickup and delivery commands that preserve physical custody."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

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
    OperationalFlag,
    ReasonCode,
    ItemClass,
)
from app.core.clock import RealClock, ScenarioClock
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.core.idempotency import execute_idempotently
from app.domain.eta import Coordinates, DeterministicEtaProvider
from app.domain.inventory import BatchBuckets, DemandLedger
from app.domain.rules import evaluate_compatibility
from app.domain.transitions import transition_lease, transition_lot
from app.engines.feasibility import evaluate_in_transit, evaluate_pre_pickup
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
from app.services.commitment import (
    DROPOFF_SECONDS,
    HANDLING_BUFFER_SECONDS,
    HANDOVER_SECONDS,
    _batch_requirements,
    _destination_capabilities,
    _rider_capabilities,
)


def get_active_dispatch(*, actor_id: UUID, rider_id: UUID) -> dict[str, object] | None:
    """Recover the rider's committed work before considering another open offer."""
    def operation(session: Session) -> dict[str, object] | None:
        actor = session.get(User, actor_id)
        if actor is None or not actor.active:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        if actor.role not in {"COORDINATOR", "JUDGE", "RIDER"}:
            raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Coordinator or rider role is required", 403)
        rider = session.get(Rider, rider_id)
        if rider is None or rider.scenario_id != actor.scenario_id:
            raise _error(ApiErrorCode.NOT_FOUND, "Rider was not found in this scenario", 404)
        if actor.role == "RIDER":
            _require_owner(actor, rider)
        if actor.scenario_id is not None:
            scenario = session.get(Scenario, actor.scenario_id)
            if scenario is None or not scenario.active:
                raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
        assignment = session.scalar(select(Assignment).where(
            Assignment.rider_id == rider_id,
            Assignment.scenario_id == actor.scenario_id,
            Assignment.status.in_([AssignmentStatus.RESERVED.value, AssignmentStatus.IN_TRANSIT.value]),
        ))
        offer_query = select(Offer).join(Lot, Lot.id == Offer.lot_id).where(
            Offer.rider_id == rider_id,
            Offer.scenario_id == actor.scenario_id,
            Lot.scenario_id == actor.scenario_id,
        )
        if assignment is not None:
            offer_query = offer_query.where(Offer.lot_id == assignment.lot_id, Offer.status == OfferStatus.ACCEPTED.value)
        else:
            offer_query = offer_query.where(
                Offer.status == OfferStatus.PUBLISHED.value,
                Offer.expires_at > _now(session, actor.scenario_id),
                Lot.state == LotState.OFFERED.value,
            ).order_by(Offer.expires_at, Offer.id)
        offer = session.scalar(offer_query.limit(1))
        if offer is None:
            return None
        lot = session.get(Lot, offer.lot_id)
        lease = session.scalar(select(Lease).where(
            Lease.assignment_id == assignment.id,
            Lease.status == LeaseStatus.ACTIVE.value,
        )) if assignment is not None else None
        return {
            "lot_id": lot.id, "batch_id": lot.batch_id, "offer_id": offer.id,
            "rider_id": rider.id, "demand_id": offer.demand_id, "qty": offer.qty,
            "state": lot.state, "lot_version": lot.version, "rider_version": rider.version,
            "picked_qty": assignment.picked_qty if assignment is not None else 0,
            "accepted_qty": assignment.accepted_qty if assignment is not None else 0,
            "rejected_qty": assignment.rejected_qty if assignment is not None else 0,
            "carried_qty": assignment.carried_qty if assignment is not None else 0,
            "lease_expires_at": lease.expires_at if lease is not None else None,
        }

    return run_transaction(operation)


def get_dispatch_map(
    *, actor_id: UUID, rider_id: UUID, batch_id: UUID | None = None,
    demand_id: UUID | None = None, lot_id: UUID | None = None,
) -> dict[str, object]:
    """Return scoped locations; committed lots determine their own destination."""
    def operation(session: Session) -> dict[str, object]:
        actor = session.get(User, actor_id)
        if actor is None or not actor.active:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        if actor.role not in {"COORDINATOR", "JUDGE", "RIDER"}:
            raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Coordinator or rider role is required", 403)
        scenario = session.get(Scenario, actor.scenario_id) if actor.scenario_id else None
        if actor.scenario_id and (scenario is None or not scenario.active):
            raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
        rider = session.get(Rider, rider_id)
        if rider is None or rider.scenario_id != actor.scenario_id:
            raise _error(ApiErrorCode.NOT_FOUND, "Rider was not found in this scenario", 404)
        if actor.role == "RIDER":
            _require_owner(actor, rider)
        lot = None
        if lot_id is not None:
            lot = session.get(Lot, lot_id)
            if lot is None or lot.scenario_id != actor.scenario_id:
                raise _error(ApiErrorCode.NOT_FOUND, "Lot was not found in this scenario", 404)
            assignment = session.scalar(select(Assignment).where(
                Assignment.lot_id == lot.id, Assignment.rider_id == rider.id,
                Assignment.scenario_id == actor.scenario_id,
            ).order_by(
                Assignment.status.in_([AssignmentStatus.RESERVED.value, AssignmentStatus.IN_TRANSIT.value]).desc(),
                Assignment.version.desc(), Assignment.id,
            ).limit(1))
            offer = session.scalar(select(Offer).where(
                Offer.lot_id == lot.id, Offer.rider_id == rider.id,
                Offer.scenario_id == actor.scenario_id,
            ).order_by((Offer.status == OfferStatus.ACCEPTED.value).desc(), Offer.expires_at.desc(), Offer.id).limit(1))
            if assignment is None and offer is None:
                raise _error(ApiErrorCode.NOT_FOUND, "Rider has no dispatch for this lot", 404)
            resolved_batch_id = lot.batch_id
            resolved_demand_id = assignment.demand_id if assignment else offer.demand_id
        else:
            resolved_batch_id, resolved_demand_id = batch_id, demand_id
        if resolved_batch_id is None or resolved_demand_id is None:
            raise _error(ApiErrorCode.NOT_FOUND, "Choose a batch and demand to preview a route", 422)
        batch = session.get(Batch, resolved_batch_id)
        demand = session.get(Demand, resolved_demand_id)
        if batch is None or demand is None or batch.scenario_id != actor.scenario_id or demand.scenario_id != actor.scenario_id:
            raise _error(ApiErrorCode.NOT_FOUND, "Route was not found in this scenario", 404)
        donor = session.get(Donor, batch.donor_id)
        shelter = session.get(Shelter, demand.shelter_id)
        donor_user = session.get(User, donor.user_id) if donor else None
        if donor_user is None or shelter is None or donor_user.scenario_id != actor.scenario_id or shelter.scenario_id != actor.scenario_id:
            raise _error(ApiErrorCode.NOT_FOUND, "Route locations were not found in this scenario", 404)
        rider_user = session.get(User, rider.user_id)
        location_known = rider.latitude is not None and rider.longitude is not None
        return {
            "lot_id": lot.id if lot else None, "state": lot.state if lot else None,
            "batch_id": batch.id, "demand_id": demand.id,
            "simulated": scenario is not None and scenario.mode == "SIMULATED",
            "pickup": {"name": donor_user.display_name, "latitude": donor.latitude, "longitude": donor.longitude},
            "destination": {"name": shelter.name, "latitude": shelter.latitude, "longitude": shelter.longitude},
            "rider": {
                "name": rider_user.display_name if rider_user else "Rider",
                "latitude": rider.latitude if location_known else donor.latitude,
                "longitude": rider.longitude if location_known else donor.longitude,
                "location_known": location_known,
            },
        }

    return run_transaction(operation)


def confirm_pickup(
    *,
    actor_id: UUID,
    lot_id: UUID,
    picked_quantity: int,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    """Move picked quantity from reservation into physical rider custody."""
    if isinstance(picked_quantity, bool) or not isinstance(picked_quantity, int) or picked_quantity <= 0:
        raise _error(ApiErrorCode.INVALID_QUANTITY, "picked_quantity must be a positive integer", 422)

    def operation(session: Session) -> dict[str, object]:
        actor_snapshot = session.get(User, actor_id)
        if actor_snapshot is None:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        scenario_id = actor_snapshot.scenario_id
        order = LockOrder()

        def pickup() -> dict[str, object]:
            if scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
            actor = _lock_actor(session, actor_id, scenario_id, order)
            if actor.role != "RIDER":
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "RIDER role is required to confirm pickup", 403)
            refs = _command_refs(session, lot_id)
            donor_ref, batch, demand, rider_ref, assignment_ref = refs
            donors = lock_rows(session, Donor, LockRank.DONOR, [donor_ref.id], order)
            batches = lock_rows(session, Batch, LockRank.BATCH, [batch.id], order)
            inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch.id], order)
            lots = lock_rows(session, Lot, LockRank.LOT, [lot_id], order)
            shelters = lock_rows(session, Shelter, LockRank.SHELTER, [demand.shelter_id], order)
            demands = lock_rows(session, Demand, LockRank.DEMAND, [demand.id], order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_ref.id], order)
            assignments = lock_rows(session, Assignment, LockRank.ASSIGNMENT, [assignment_ref.id], order)
            leases_ref = session.scalar(
                select(Lease.id).where(
                    Lease.assignment_id == assignment_ref.id,
                    Lease.status == LeaseStatus.ACTIVE.value,
                )
            )
            leases = lock_rows(session, Lease, LockRank.LEASE, [leases_ref], order) if leases_ref else []
            if not all((donors, batches, inventories, lots, shelters, demands, riders, assignments, leases)):
                raise _error(ApiErrorCode.LEASE_INVALID, "Active pickup lease or commitment was not found", 409)
            donor, batch, inventory, lot, shelter, demand, rider, assignment, lease = (
                donors[0], batches[0], inventories[0], lots[0], shelters[0], demands[0], riders[0], assignments[0], leases[0]
            )
            now = _now(session, scenario_id)
            _same_scope(scenario_id, batch.scenario_id, lot.scenario_id, demand.scenario_id, shelter.scenario_id, rider.scenario_id, assignment.scenario_id, lease.scenario_id)
            _require_owner(actor, rider)
            if lot.version != expected_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Lot version is stale", 409)
            if lot.state != LotState.LEASED.value or assignment.status != AssignmentStatus.RESERVED.value:
                raise _error(ApiErrorCode.LEASE_INVALID, "Lot is not awaiting pickup", 409)
            if lease.status != LeaseStatus.ACTIVE.value or now >= lease.expires_at or now > lease.first_progress_by:
                raise _error(ApiErrorCode.LEASE_INVALID, "Pickup lease is expired or missed its first-progress deadline", 409)
            if now > lease.physical_cutoff_at or now > batch.deliver_by:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Pickup is past the physical delivery cutoff", 422)
            if not shelter.intake_open or not demand.intake_open:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Destination intake closed before pickup", 409)
            if not donor.verified:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "Donor verification was revoked before pickup", 422)
            _check_compatibility(batch, rider, shelter, now)
            route_eta = DeterministicEtaProvider().eta_seconds(
                Coordinates(donor.latitude, donor.longitude),
                Coordinates(shelter.latitude, shelter.longitude),
                scenario_id,
            )
            feasibility = evaluate_pre_pickup(
                now=now,
                rider_to_donor_eta_seconds=0,
                handover_seconds=0,
                donor_to_shelter_eta_seconds=route_eta,
                handling_buffer_seconds=HANDLING_BUFFER_SECONDS + DROPOFF_SECONDS,
                deliver_by=batch.deliver_by,
            )
            if not feasibility.feasible:
                raise _error(ApiErrorCode.SAFETY_BLOCKED, "B1 revalidation blocks pickup before the delivery deadline", 422)
            if picked_quantity > assignment.reserved_qty:
                raise _error(ApiErrorCode.INVALID_QUANTITY, "Picked quantity exceeds the accepted reservation", 422)
            buckets = _buckets(batch, inventory)
            try:
                moved = buckets.pickup(assignment.reserved_qty, picked_quantity)
                ledger = demand_ledger(demand)
                departed = ledger.depart(picked_quantity)
                unpicked_quantity = assignment.reserved_qty - picked_quantity
                if unpicked_quantity:
                    departed = departed.cancel_reservation(unpicked_quantity)
            except ValueError as exc:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, str(exc), 409) from exc
            _save_buckets(inventory, moved)
            _save_ledger(demand, departed)
            assignment.picked_qty = picked_quantity
            assignment.carried_qty = picked_quantity
            assignment.status = AssignmentStatus.IN_TRANSIT.value
            assignment.version += 1
            transition_lot(lot, LotState.IN_TRANSIT)
            transition_lease(lease, LeaseStatus.PICKED_UP)
            lease.last_progress_at = now
            rider.last_progress_at = now
            rider.last_heartbeat_at = now
            rider.version += 1
            _add_events(
                session,
                order=order,
                actor_id=actor.id,
                scenario_id=scenario_id,
                occurred_at=now,
                events=(
                    (AggregateType.LOT.value, lot.id, EventType.PICKUP_CONFIRMED.value, {
                        "assignment_id": str(assignment.id), "reserved_qty": assignment.reserved_qty,
                        "picked_qty": picked_quantity, "released_unpicked_qty": assignment.reserved_qty - picked_quantity,
                        "carried_qty": assignment.carried_qty, "lot_version": lot.version,
                    }),
                    (AggregateType.BATCH.value, batch.id, EventType.PICKUP_CONFIRMED.value, {
                        "available": inventory.available, "reserved": inventory.reserved, "carried": inventory.carried,
                        "delivered": inventory.delivered, "disposed": inventory.disposed, "version": inventory.version,
                    }),
                    (AggregateType.DEMAND.value, demand.id, EventType.PICKUP_CONFIRMED.value, {
                        "reserved_qty": demand.reserved_qty, "transit_qty": demand.transit_qty, "version": demand.version,
                    }),
                ),
            )
            session.flush()
            return _command_snapshot(lot, assignment, inventory, demand, rider)

        try:
            result, _ = execute_idempotently(
                session,
                lock_order=order,
                scenario_id=scenario_id,
                actor_id=actor_id,
                endpoint=f"POST /api/v1/lots/{lot_id}/pickup",
                key=idempotency_key,
                request_payload={"lot_id": lot_id, "picked_quantity": picked_quantity, "expected_version": expected_version},
                action=pickup,
                serialize=jsonable_encoder,
                deserialize=lambda body: body,
            )
            return result
        except IntegrityError as exc:
            raise _error(ApiErrorCode.STATE_CONFLICT, "Pickup conflicts with current custody state", 409) from exc

    return run_transaction(operation)


def confirm_delivery(
    *,
    actor_id: UUID,
    lot_id: UUID,
    accepted_quantity: int,
    rejected_quantity: int,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    """Record accepted quantity and keep rejected/unresolved goods in custody."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (accepted_quantity, rejected_quantity)):
        raise _error(ApiErrorCode.INVALID_QUANTITY, "accepted and rejected quantities must be nonnegative integers", 422)
    if accepted_quantity + rejected_quantity == 0:
        raise _error(ApiErrorCode.INVALID_QUANTITY, "At least one accepted or rejected unit is required", 422)

    def operation(session: Session) -> dict[str, object]:
        actor_snapshot = session.get(User, actor_id)
        if actor_snapshot is None:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        scenario_id = actor_snapshot.scenario_id
        order = LockOrder()

        def deliver() -> dict[str, object]:
            if scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
            actor = _lock_actor(session, actor_id, scenario_id, order)
            if actor.role not in {"RIDER", "SHELTER"}:
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "RIDER or destination SHELTER role is required", 403)
            donor_ref, batch_ref, demand_ref, rider_ref, assignment_ref = _command_refs(session, lot_id)
            batches = lock_rows(session, Batch, LockRank.BATCH, [batch_ref.id], order)
            inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_ref.id], order)
            lots = lock_rows(session, Lot, LockRank.LOT, [lot_id], order)
            shelters = lock_rows(session, Shelter, LockRank.SHELTER, [demand_ref.shelter_id], order)
            demands = lock_rows(session, Demand, LockRank.DEMAND, [demand_ref.id], order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_ref.id], order)
            assignments = lock_rows(session, Assignment, LockRank.ASSIGNMENT, [assignment_ref.id], order)
            if not all((batches, inventories, lots, shelters, demands, riders, assignments)):
                raise _error(ApiErrorCode.NOT_FOUND, "Active in-transit assignment was not found", 404)
            batch, inventory, lot, shelter, demand, rider, assignment = (
                batches[0], inventories[0], lots[0], shelters[0], demands[0], riders[0], assignments[0]
            )
            now = _now(session, scenario_id)
            _same_scope(scenario_id, batch.scenario_id, lot.scenario_id, demand.scenario_id, shelter.scenario_id, rider.scenario_id, assignment.scenario_id)
            _require_delivery_authority(actor, rider, shelter)
            if lot.version != expected_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Lot version is stale", 409)
            if lot.state != LotState.IN_TRANSIT.value or assignment.status != AssignmentStatus.IN_TRANSIT.value:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Lot has no in-transit cargo", 409)
            if accepted_quantity and (not shelter.intake_open or not demand.intake_open):
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Destination intake is closed", 409)
            if accepted_quantity:
                _check_compatibility(batch, rider, shelter, now)
                feasibility = evaluate_in_transit(
                    now=now,
                    rider_to_shelter_eta_seconds=0,
                    required_dropoff_seconds=DROPOFF_SECONDS,
                    deliver_by=batch.deliver_by,
                )
                if not feasibility.feasible:
                    raise _error(ApiErrorCode.SAFETY_BLOCKED, "B1 delivery confirmation is past the safe handover deadline", 422)
            unconfirmed_for_destination = assignment.picked_qty - assignment.accepted_qty - assignment.rejected_qty
            if accepted_quantity + rejected_quantity > unconfirmed_for_destination:
                raise _error(ApiErrorCode.INVALID_QUANTITY, "Confirmation exceeds the remaining destination commitment", 422)
            if accepted_quantity > demand.transit_qty or rejected_quantity > demand.transit_qty - accepted_quantity:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Delivery exceeds the remaining transit commitment", 409)
            buckets = _buckets(batch, inventory)
            ledger = demand_ledger(demand)
            try:
                moved = buckets.deliver(accepted_quantity) if accepted_quantity else buckets
                updated_ledger = ledger
                if accepted_quantity:
                    updated_ledger = updated_ledger.receive(accepted_quantity)
                if rejected_quantity:
                    updated_ledger = updated_ledger.release_transit(rejected_quantity)
            except ValueError as exc:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, str(exc), 409) from exc
            if accepted_quantity:
                _save_buckets(inventory, moved)
            if accepted_quantity or rejected_quantity:
                _save_ledger(demand, updated_ledger)
            assignment.accepted_qty += accepted_quantity
            assignment.rejected_qty += rejected_quantity
            assignment.carried_qty -= accepted_quantity
            if rejected_quantity and OperationalFlag.AT_RISK.value not in assignment.risk_flags:
                assignment.risk_flags = [*assignment.risk_flags, OperationalFlag.AT_RISK.value]
            assignment.version += 1
            if assignment.carried_qty == 0:
                assignment.status = AssignmentStatus.DELIVERED.value
                transition_lot(lot, LotState.DELIVERED)
                rider.available = True
                assignment.risk_flags = []
            else:
                lot.version += 1
            rider.version += 1
            rider.last_progress_at = now
            event_type = (
                EventType.DELIVERY_CONFIRMED.value
                if assignment.carried_qty == 0
                else EventType.PARTIAL_DELIVERY_CONFIRMED.value
            )
            reason = ReasonCode.DELIVERY_BLOCKED_UNSAFE.value if rejected_quantity else None
            _add_events(
                session,
                order=order,
                actor_id=actor.id,
                scenario_id=scenario_id,
                occurred_at=now,
                events=(
                    (AggregateType.LOT.value, lot.id, event_type, {
                        "assignment_id": str(assignment.id), "accepted_qty": accepted_quantity,
                        "rejected_qty": rejected_quantity, "carried_qty": assignment.carried_qty,
                        "assignment_status": assignment.status, "risk_flags": assignment.risk_flags,
                        "lot_state": lot.state, "lot_version": lot.version,
                    }),
                    (AggregateType.BATCH.value, batch.id, event_type, {
                        "available": inventory.available, "reserved": inventory.reserved,
                        "carried": inventory.carried, "delivered": inventory.delivered,
                        "disposed": inventory.disposed, "version": inventory.version,
                    }),
                    (AggregateType.DEMAND.value, demand.id, event_type, {
                        "received_qty": demand.received_qty, "transit_qty": demand.transit_qty,
                        "version": demand.version,
                    }),
                ),
                reason_code=reason,
            )
            session.flush()
            return _command_snapshot(lot, assignment, inventory, demand, rider)

        try:
            result, _ = execute_idempotently(
                session,
                lock_order=order,
                scenario_id=scenario_id,
                actor_id=actor_id,
                endpoint=f"POST /api/v1/lots/{lot_id}/deliver",
                key=idempotency_key,
                request_payload={
                    "lot_id": lot_id,
                    "accepted_quantity": accepted_quantity,
                    "rejected_quantity": rejected_quantity,
                    "expected_version": expected_version,
                },
                action=deliver,
                serialize=jsonable_encoder,
                deserialize=lambda body: body,
            )
            return result
        except IntegrityError as exc:
            raise _error(ApiErrorCode.STATE_CONFLICT, "Delivery conflicts with current custody state", 409) from exc

    return run_transaction(operation)


def _command_refs(session: Session, lot_id: UUID) -> tuple[Donor, Batch, Demand, Rider, Assignment]:
    row = session.execute(
        select(Donor, Batch, Demand, Rider, Assignment)
        .join(Batch, Batch.donor_id == Donor.id)
        .join(Lot, Lot.batch_id == Batch.id)
        .join(Assignment, Assignment.lot_id == Lot.id)
        .join(Demand, Demand.id == Assignment.demand_id)
        .join(Rider, Rider.id == Assignment.rider_id)
        .where(Lot.id == lot_id)
    ).one_or_none()
    if row is None:
        raise _error(ApiErrorCode.NOT_FOUND, "Lot assignment was not found", 404)
    return row


def _lock_actor(session: Session, actor_id: UUID, scenario_id: UUID | None, order: LockOrder) -> User:
    actors = lock_rows(session, User, LockRank.ACTOR, [actor_id], order)
    if not actors or not actors[0].active:
        raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
    if actors[0].scenario_id != scenario_id:
        raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Actor is outside the requested scenario", 403)
    return actors[0]


def _require_owner(actor: User, rider: Rider) -> None:
    if rider.user_id != actor.id:
        raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Lot is assigned to another rider", 403)


def _require_delivery_authority(actor: User, rider: Rider, shelter: Shelter) -> None:
    if actor.role == "RIDER" and rider.user_id == actor.id:
        return
    if actor.role == "SHELTER" and shelter.user_id == actor.id:
        return
    raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Actor is not the assigned rider or destination shelter", 403)


def _same_scope(expected: UUID | None, *actual: UUID | None) -> None:
    if any(value != expected for value in actual):
        raise _error(ApiErrorCode.NOT_FOUND, "Resource is outside the current scenario", 404)


def _now(session: Session, scenario_id: UUID | None) -> datetime:
    value = RealClock().now() if scenario_id is None else ScenarioClock(session, scenario_id).now()
    return value.astimezone(timezone.utc)


def _buckets(batch: Batch, inventory: BatchInventory) -> BatchBuckets:
    return BatchBuckets(batch.total_qty, inventory.available, inventory.reserved, inventory.carried, inventory.delivered, inventory.disposed)


def _save_buckets(inventory: BatchInventory, buckets: BatchBuckets) -> None:
    inventory.available = buckets.available
    inventory.reserved = buckets.reserved
    inventory.carried = buckets.carried
    inventory.delivered = buckets.delivered
    inventory.disposed = buckets.disposed
    inventory.version += 1


def _save_ledger(demand: Demand, ledger: DemandLedger) -> None:
    demand.received_qty = ledger.received
    demand.reserved_qty = ledger.reserved
    demand.transit_qty = ledger.transit
    demand.intake_open = ledger.intake_open
    demand.version += 1


def _command_snapshot(lot: Lot, assignment: Assignment, inventory: BatchInventory, demand: Demand, rider: Rider) -> dict[str, object]:
    return {
        "lot_id": lot.id,
        "lot_state": lot.state,
        "lot_version": lot.version,
        "assignment_id": assignment.id,
        "assignment_status": assignment.status,
        "assignment_version": assignment.version,
        "picked_qty": assignment.picked_qty,
        "accepted_qty": assignment.accepted_qty,
        "rejected_qty": assignment.rejected_qty,
        "carried_qty": assignment.carried_qty,
        "risk_flags": assignment.risk_flags,
        "rider_id": rider.id,
        "rider_available": rider.available,
        "inventory": {
            "available": inventory.available,
            "reserved": inventory.reserved,
            "carried": inventory.carried,
            "delivered": inventory.delivered,
            "disposed": inventory.disposed,
            "version": inventory.version,
        },
        "demand": {
            "received": demand.received_qty,
            "reserved": demand.reserved_qty,
            "transit": demand.transit_qty,
            "version": demand.version,
        },
    }


def _add_events(
    session: Session,
    *,
    order: LockOrder,
    actor_id: UUID,
    scenario_id: UUID | None,
    occurred_at: datetime,
    events: tuple[tuple[str, UUID, str, dict[str, object]], ...],
    reason_code: str | None = None,
) -> None:
    order.acquire(LockRank.EVENT_AND_SCHEDULE, [item[1] for item in events])
    for aggregate_type, aggregate_id, event_type, payload in events:
        session.add(Event(
            scenario_id=scenario_id,
            actor_id=actor_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            reason_code=reason_code,
            payload=jsonable_encoder(payload),
            occurred_at=occurred_at,
        ))


def _error(code: ApiErrorCode, message: str, status: int) -> ServiceError:
    return ServiceError(code=code, message=message, status_code=status)


def _check_compatibility(batch: Batch, rider: Rider, shelter: Shelter, now: datetime) -> None:
    decision = evaluate_compatibility(
        ItemClass(batch.item_class),
        _destination_capabilities(shelter),
        _rider_capabilities(rider),
        _batch_requirements(batch),
        now,
    )
    if not decision.eligible:
        codes = ", ".join(reason.value for reason in decision.reasons)
        raise _error(ApiErrorCode.SAFETY_BLOCKED, f"Compatibility evidence is missing or unsafe: {codes}", 422)
