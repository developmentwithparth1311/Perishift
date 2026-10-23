"""Transactional single destination recovery for cargo already in rider custody."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.enums import (
    AggregateType, ApiErrorCode, AssignmentStatus, EventType, ItemClass,
    LotState, OperationalFlag, ReasonCode,
)
from app.core.clock import RealClock, ScenarioClock
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.core.idempotency import execute_idempotently
from app.domain.eta import Coordinates, DeterministicEtaProvider
from app.domain.inventory import DemandLedger
from app.domain.rules import (
    CapabilityEvidence, CompatibilityRequirements, DestinationCapabilities,
    RiderHandlingFeatures,
)
from app.engines.fallback import FallbackSnapshot, Position
from app.engines.reroute import propose_reroute, revalidate_reroute_proposal
from app.models import (
    Assignment, Batch, BatchInventory, Demand, Event, Lot, Rider, Scenario,
    Shelter, User,
)
from app.repositories.demands import demand_ledger
from app.services.commitment import (
    DROPOFF_SECONDS, _batch_requirements, _destination_capabilities,
    _rider_capabilities,
)
from app.services.custody import _add_events, _lock_actor, _same_scope, _save_ledger


RIDER_LOCATION_MAX_AGE_SECONDS = 300
MAX_REROUTE_RETRIES = 3


class _RetryStaleProposal(Exception):
    """Roll back and rebuild the advisory proposal from a fresh snapshot."""


def reroute_in_transit(
    *,
    actor_id: UUID,
    lot_id: UUID,
    expected_lot_version: int,
    expected_assignment_version: int,
    expected_current_demand_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    """Move an in-transit assignment's destination atomically, retaining custody.

    B4 ranking is advisory. Its winner is re-evaluated after destination and
    demand rows are locked. No new demand is written unless the complete
    physically carried quantity remains safe and uncommitted there.
    """
    for value, name in (
        (expected_lot_version, "expected_lot_version"),
        (expected_assignment_version, "expected_assignment_version"),
        (expected_current_demand_version, "expected_current_demand_version"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _error(ApiErrorCode.PLAN_STALE, f"{name} must be a nonnegative version", 422)

    def operation(session: Session) -> dict[str, object]:
        actor_snapshot = session.get(User, actor_id)
        if actor_snapshot is None or not actor_snapshot.active:
            raise _error(ApiErrorCode.UNAUTHENTICATED, "Actor is inactive or unknown", 401)
        scenario_id = actor_snapshot.scenario_id
        order = LockOrder()

        def execute() -> dict[str, object]:
            if scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise _error(ApiErrorCode.NOT_FOUND, "Active scenario was not found", 404)
            actor = _lock_actor(session, actor_id, scenario_id, order)
            if actor.role not in {"COORDINATOR", "JUDGE", "RIDER"}:
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Coordinator, judge, or assigned rider role is required", 403)

            refs = _recovery_refs(session, lot_id)
            batch_ref, old_demand_ref, old_shelter_ref, rider_ref, assignment_ref = refs
            now = _now(session, scenario_id)
            _check_rider_location(rider_ref, now)
            if actor.role == "RIDER" and rider_ref.user_id != actor.id:
                raise _error(ApiErrorCode.FORBIDDEN_ROLE, "Lot is assigned to another rider", 403)

            # The initial ranking is computed from immutable read snapshots. The
            # chosen destination and both versioned rows are then locked below.
            proposal, candidate_versions = _proposal_for_current_rows(
                session=session,
                batch=batch_ref,
                old_demand=old_demand_ref,
                rider=rider_ref,
                cargo_quantity=assignment_ref.carried_qty,
                now=now,
                scenario_id=scenario_id,
            )
            target_id = UUID(proposal.selected.destination_id) if proposal.selected else None
            target_ref = session.get(Demand, target_id) if target_id else None
            target_shelter_ref = session.get(Shelter, target_ref.shelter_id) if target_ref else None

            batches = lock_rows(session, Batch, LockRank.BATCH, [batch_ref.id], order)
            inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_ref.id], order)
            lots = lock_rows(session, Lot, LockRank.LOT, [lot_id], order)
            shelter_ids = [old_shelter_ref.id]
            if target_shelter_ref is not None:
                shelter_ids.append(target_shelter_ref.id)
            shelters = lock_rows(session, Shelter, LockRank.SHELTER, shelter_ids, order)
            demand_ids = [old_demand_ref.id]
            if target_ref is not None:
                demand_ids.append(target_ref.id)
            demands = lock_rows(session, Demand, LockRank.DEMAND, demand_ids, order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_ref.id], order)
            assignments = lock_rows(session, Assignment, LockRank.ASSIGNMENT, [assignment_ref.id], order)
            if not all((batches, inventories, lots, shelters, demands, riders, assignments)):
                raise _error(ApiErrorCode.NOT_FOUND, "In-transit assignment resources were not found", 404)
            batch, inventory, lot, rider, assignment = batches[0], inventories[0], lots[0], riders[0], assignments[0]
            demand_map = {row.id: row for row in demands}
            shelter_map = {row.id: row for row in shelters}
            old_demand = demand_map.get(assignment.demand_id)
            if old_demand is None:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Assignment destination changed during recovery", 409)
            old_shelter = shelter_map[old_demand.shelter_id]

            _same_scope(scenario_id, batch.scenario_id, lot.scenario_id, assignment.scenario_id,
                        old_demand.scenario_id, old_shelter.scenario_id, rider.scenario_id)
            if lot.version != expected_lot_version or assignment.version != expected_assignment_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Lot or assignment version is stale", 409)
            if old_demand.version != expected_current_demand_version:
                raise _error(ApiErrorCode.PLAN_STALE, "Current destination demand version is stale", 409)
            if lot.state != LotState.IN_TRANSIT.value or assignment.status != AssignmentStatus.IN_TRANSIT.value:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Cargo is not in transit", 409)
            if assignment.carried_qty <= 0 or inventory.carried < assignment.carried_qty:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Physical carried inventory does not match the assignment", 409)
            if rider.available:
                raise _error(ApiErrorCode.STATE_CONFLICT, "Rider occupancy is inconsistent with in-transit custody", 409)

            if target_id is None:
                return _mark_at_risk(
                    session, order, actor.id, scenario_id, now, lot, assignment, rider,
                    proposal, old_demand, reason=ReasonCode.NO_FEASIBLE_DESTINATION,
                )

            target = demand_map.get(target_id)
            if target is None:
                raise _RetryStaleProposal()
            target_shelter = shelter_map.get(target.shelter_id)
            if target_shelter is None:
                raise _RetryStaleProposal()
            _same_scope(scenario_id, target.scenario_id, target_shelter.scenario_id)
            fresh_proposal, fresh_versions = _proposal_for_candidate_rows(
                batch=batch,
                old_demand=old_demand,
                rider=rider,
                cargo_quantity=assignment.carried_qty,
                now=now,
                scenario_id=scenario_id,
                rows=[(target, target_shelter)],
            )
            old_versions = candidate_versions.get(target.id)
            current_versions = fresh_versions.get(target.id)
            revalidation = revalidate_reroute_proposal(
                proposal=proposal,
                current_position=Position(rider.latitude, rider.longitude),
                now=now,
                deliver_by=batch.deliver_by,
                rider=_rider_capabilities(rider),
                requirements=_batch_requirements(batch),
                current_candidates=_snapshots_from_proposal_rows(
                    batch=batch, rider=rider, now=now, scenario_id=scenario_id,
                    rows=[(target, target_shelter)],
                ),
            )
            if (old_versions != current_versions or not revalidation.valid
                    or fresh_proposal.selected is None
                    or fresh_proposal.selected.destination_id != str(target.id)):
                raise _RetryStaleProposal()

            # Commitment per destination is remaining unconfirmed quantity.
            # Rejected goods remain physically carried and are committed in full
            # to the new single-stop destination.
            old_commitment = assignment.picked_qty - assignment.accepted_qty - assignment.rejected_qty
            if old_commitment < 0 or old_demand.transit_qty < old_commitment:
                raise _error(ApiErrorCode.DEMAND_CAPACITY_CHANGED, "Old destination transit ledger is inconsistent", 409)
            target_ledger = demand_ledger(target)
            if target_ledger.uncommitted_need < assignment.carried_qty:
                raise _RetryStaleProposal()
            old_ledger = demand_ledger(old_demand)
            if old_commitment:
                _save_ledger(old_demand, old_ledger.release_transit(old_commitment))
            target.transit_qty += assignment.carried_qty
            target.version += 1
            previous_id = assignment.demand_id
            assignment.demand_id = target.id
            assignment.risk_flags = [flag for flag in assignment.risk_flags if flag not in {
                OperationalFlag.AT_RISK.value, OperationalFlag.REROUTE_PENDING.value,
                OperationalFlag.LOCATION_STALE.value,
            }]
            assignment.version += 1
            lot.version += 1
            _add_events(
                session, order=order, actor_id=actor.id, scenario_id=scenario_id,
                occurred_at=now,
                events=(
                    (AggregateType.LOT.value, lot.id, EventType.REROUTE_REQUESTED.value, {
                        "assignment_id": str(assignment.id), "from_demand_id": str(previous_id),
                        "to_demand_id": str(target.id), "carried_qty": assignment.carried_qty,
                    }),
                    (AggregateType.LOT.value, lot.id, EventType.REROUTE_COMMITTED.value, {
                        "assignment_id": str(assignment.id), "from_demand_id": str(previous_id),
                        "to_demand_id": str(target.id), "carried_qty": assignment.carried_qty,
                        "lot_version": lot.version, "assignment_version": assignment.version,
                        "rider_available": rider.available,
                    }),
                    (AggregateType.DEMAND.value, old_demand.id, EventType.REROUTE_COMMITTED.value, {
                        "transit_qty": old_demand.transit_qty, "version": old_demand.version,
                    }),
                    (AggregateType.DEMAND.value, target.id, EventType.REROUTE_COMMITTED.value, {
                        "transit_qty": target.transit_qty, "version": target.version,
                    }),
                ),
            )
            session.flush()
            return _response(lot, assignment, rider, old_demand.id, target.id, True, None, (),
                             "Full cargo was atomically reassigned to a safe destination.")

        endpoint = f"POST /api/v1/lots/{lot_id}/reroute"
        try:
            result, _ = execute_idempotently(
                session, lock_order=order, scenario_id=scenario_id,
                actor_id=actor_id, endpoint=endpoint, key=idempotency_key,
                request_payload={
                    "lot_id": lot_id,
                    "expected_lot_version": expected_lot_version,
                    "expected_assignment_version": expected_assignment_version,
                    "expected_current_demand_version": expected_current_demand_version,
                },
                action=execute,
                serialize=jsonable_encoder,
                deserialize=lambda body: body,
            )
            return result
        except IntegrityError as exc:
            raise _error(ApiErrorCode.STATE_CONFLICT, "Reroute conflicts with a concurrent custody transition", 409) from exc

    for attempt in range(MAX_REROUTE_RETRIES):
        try:
            return run_transaction(operation)
        except _RetryStaleProposal:
            if attempt + 1 == MAX_REROUTE_RETRIES:
                raise _error(ApiErrorCode.PLAN_STALE, "Destination changed during bounded recovery revalidation", 409)
    raise AssertionError("unreachable")


def _proposal_for_current_rows(*, session: Session, batch: Batch, old_demand: Demand,
                              rider: Rider, cargo_quantity: int, now: datetime,
                              scenario_id: UUID | None):
    rows = session.execute(
        select(Demand, Shelter).join(Shelter, Shelter.id == Demand.shelter_id).where(
            Demand.scenario_id == scenario_id,
            Demand.item_class == batch.item_class,
            Demand.id != old_demand.id,
        )
    ).all()
    return _proposal_for_candidate_rows(
        batch=batch, old_demand=old_demand, rider=rider, cargo_quantity=cargo_quantity,
        now=now, scenario_id=scenario_id, rows=rows,
    )


def _proposal_for_candidate_rows(*, batch: Batch, old_demand: Demand, rider: Rider,
                                 cargo_quantity: int, now: datetime,
                                 scenario_id: UUID | None, rows):
    candidates, versions = _build_candidates(batch, rider, cargo_quantity, now, scenario_id, rows)
    proposal = propose_reroute(
        cargo_quantity=cargo_quantity,
        item_class=ItemClass(batch.item_class),
        current_position=Position(rider.latitude, rider.longitude),
        now=now,
        deliver_by=batch.deliver_by,
        current_destination_id=str(old_demand.id),
        rider=_rider_capabilities(rider),
        requirements=_batch_requirements(batch),
        candidates=candidates,
    )
    return proposal, versions


def _snapshots_from_proposal_rows(*, batch: Batch, rider: Rider, now: datetime,
                                  scenario_id: UUID | None, rows):
    return _build_candidates(batch, rider, 1, now, scenario_id, rows)[0]


def _build_candidates(batch: Batch, rider: Rider, cargo_quantity: int, now: datetime,
                      scenario_id: UUID | None, rows):
    if rider.latitude is None or rider.longitude is None:
        return (), {}
    origin = Position(rider.latitude, rider.longitude)
    eta_provider = DeterministicEtaProvider()
    candidates = []
    versions = {}
    for demand, shelter in rows:
        if demand.item_class != batch.item_class:
            continue
        ledger = demand_ledger(demand)
        available = ledger.uncommitted_need
        service_open = (demand.service_window_start is None or now >= demand.service_window_start) and (
            demand.service_window_end is None or now < demand.service_window_end
        )
        destination = Position(shelter.latitude, shelter.longitude)
        eta = eta_provider.eta_seconds(
            Coordinates(origin.latitude, origin.longitude),
            Coordinates(destination.latitude, destination.longitude), scenario_id,
        )
        versions[demand.id] = (demand.version, shelter.version)
        candidates.append(FallbackSnapshot(
            destination_id=str(demand.id), label=shelter.name, position=destination,
            intake_open=bool(shelter.intake_open and demand.intake_open and service_open),
            available_quantity=available, snapshot_at=now, version=demand.version,
            capabilities=_destination_capabilities(shelter), eta_seconds=eta,
            eta_origin=origin, eta_destination=destination, eta_as_of=now,
            traffic_version=f"scenario:{scenario_id}:rider:{rider.version}:shelter:{shelter.version}",
            dropoff_seconds=DROPOFF_SECONDS,
        ))
    return tuple(candidates), versions


def _recovery_refs(session: Session, lot_id: UUID):
    row = session.execute(
        select(Batch, Demand, Shelter, Rider, Assignment)
        .join(Lot, Lot.batch_id == Batch.id)
        .join(Assignment, Assignment.lot_id == Lot.id)
        .join(Demand, Demand.id == Assignment.demand_id)
        .join(Shelter, Shelter.id == Demand.shelter_id)
        .join(Rider, Rider.id == Assignment.rider_id)
        .where(Lot.id == lot_id)
    ).one_or_none()
    if row is None:
        raise _error(ApiErrorCode.NOT_FOUND, "Lot assignment was not found", 404)
    return row


def _mark_at_risk(session: Session, order: LockOrder, actor_id: UUID,
                   scenario_id: UUID | None, now: datetime, lot: Lot,
                   assignment: Assignment, rider: Rider, proposal, old_demand: Demand,
                   *, reason: ReasonCode):
    flags = list(assignment.risk_flags or [])
    if OperationalFlag.AT_RISK.value not in flags:
        flags.append(OperationalFlag.AT_RISK.value)
    assignment.risk_flags = flags
    assignment.version += 1
    lot.version += 1
    partial = tuple({
        "demand_id": item.destination_id,
        "label": item.label,
        "available_quantity": item.available_quantity,
        "carried_quantity": assignment.carried_qty,
        "reason_codes": [code.value for code in item.reasons],
        "explanation": item.explanation,
    } for item in proposal.partial_options)
    _add_events(
        session, order=order, actor_id=actor_id, scenario_id=scenario_id,
        occurred_at=now,
        events=(
            (AggregateType.LOT.value, lot.id, EventType.REROUTE_REQUESTED.value, {
                "assignment_id": str(assignment.id), "from_demand_id": str(old_demand.id),
                "carried_qty": assignment.carried_qty,
            }),
            (AggregateType.LOT.value, lot.id, EventType.LOT_AT_RISK.value, {
                "assignment_id": str(assignment.id), "carried_qty": assignment.carried_qty,
                "partial_options": partial, "coordinator_escalation": True,
                "lot_version": lot.version, "assignment_version": assignment.version,
            }),
        ),
        reason_code=reason.value,
    )
    session.flush()
    return _response(lot, assignment, rider, old_demand.id, None, False, reason.value,
                     partial, proposal.explanation)


def _response(lot: Lot, assignment: Assignment, rider: Rider, from_id: UUID,
              to_id: UUID | None, rerouted: bool, reason: str | None,
              partial_options, explanation: str) -> dict[str, object]:
    return {
        "lot_id": lot.id, "lot_state": lot.state, "lot_version": lot.version,
        "assignment_id": assignment.id, "assignment_status": assignment.status,
        "assignment_version": assignment.version, "from_demand_id": from_id,
        "to_demand_id": to_id, "carried_qty": assignment.carried_qty,
        "rerouted": rerouted, "reason_code": reason,
        "risk_flags": assignment.risk_flags, "rider_id": rider.id,
        "rider_available": rider.available, "partial_options": list(partial_options),
        "explanation": explanation,
    }


def _now(session: Session, scenario_id: UUID | None) -> datetime:
    value = RealClock().now() if scenario_id is None else ScenarioClock(session, scenario_id).now()
    return value.astimezone(timezone.utc)


def _check_rider_location(rider: Rider, now: datetime) -> None:
    heartbeat = rider.last_heartbeat_at
    if (rider.latitude is None or rider.longitude is None or heartbeat is None
            or heartbeat.tzinfo is None or (now - heartbeat.astimezone(timezone.utc)).total_seconds() > RIDER_LOCATION_MAX_AGE_SECONDS
            or (now - heartbeat.astimezone(timezone.utc)).total_seconds() < 0):
        raise _error(ApiErrorCode.SAFETY_BLOCKED, "Rider location evidence is missing or stale", 422)


def _error(code: ApiErrorCode, message: str, status: int) -> ServiceError:
    return ServiceError(code=code, message=message, status_code=status)
