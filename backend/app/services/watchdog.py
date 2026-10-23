"""Idempotent deadline transitions for scheduled actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.enums import (
    AggregateType,
    AssignmentStatus,
    EventType,
    LeaseStatus,
    LotState,
    OfferStatus,
    ReasonCode,
)
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.domain.inventory import BatchBuckets
from app.models import (
    Assignment,
    Batch,
    BatchInventory,
    Demand,
    Event,
    Lease,
    Lot,
    Offer,
    Rider,
    Scenario,
    Shelter,
)
from app.repositories.demands import demand_ledger, save_demand_ledger
from app.repositories.jobs import ClaimedAction, LEASE_EXPIRY, LEASE_FIRST_PROGRESS, OFFER_EXPIRY


def process_scheduled_action(action: ClaimedAction, *, now: datetime) -> bool:
    """Apply a deadline if it is still current; stale/replayed jobs safely no-op."""
    now = _utc(now)
    if action.action_type == OFFER_EXPIRY:
        return _expire_offer(action, now)
    if action.action_type in {LEASE_FIRST_PROGRESS, LEASE_EXPIRY}:
        return _expire_lease(action, now)
    raise ValueError(f"unsupported scheduled action type: {action.action_type}")


def _expire_offer(action: ClaimedAction, now: datetime) -> bool:
    offer_id = UUID(str(action.payload["offer_id"]))
    expected_version = int(action.payload["expected_version"])

    def operation(session: Session) -> bool:
        ref = session.get(Offer, offer_id)
        if ref is None:
            return False
        order = LockOrder()
        if action.scenario_id is not None:
            scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [action.scenario_id], order)
            if not scenarios or not scenarios[0].active:
                return False
        lots = lock_rows(session, Lot, LockRank.LOT, [ref.lot_id], order)
        offers = lock_rows(session, Offer, LockRank.OFFER, [offer_id], order)
        if not lots or not offers:
            return False
        lot, offer = lots[0], offers[0]
        if (
            offer.scenario_id != action.scenario_id
            or lot.scenario_id != action.scenario_id
            or offer.version != expected_version
            or offer.status != OfferStatus.PUBLISHED.value
            or lot.state != LotState.OFFERED.value
            or now < _utc(offer.expires_at)
        ):
            return False
        before = {"offer_status": offer.status, "offer_version": offer.version, "lot_state": lot.state, "lot_version": lot.version}
        offer.status = OfferStatus.EXPIRED.value
        offer.version += 1
        lot.state = LotState.OPEN.value
        lot.version += 1
        after = {"offer_status": offer.status, "offer_version": offer.version, "lot_state": lot.state, "lot_version": lot.version}
        order.acquire(LockRank.EVENT_AND_SCHEDULE, [lot.id])
        _add_event(
            session,
            order,
            scenario_id=action.scenario_id,
            aggregate_type=AggregateType.LOT.value,
            aggregate_id=lot.id,
            event_type=EventType.OFFER_EXPIRED.value,
            reason_code=None,
            occurred_at=now,
            payload={"offer_id": str(offer.id), "before": before, "after": after},
        )
        return True

    return run_transaction(operation)


def _expire_lease(action: ClaimedAction, now: datetime) -> bool:
    lease_id = UUID(str(action.payload["lease_id"]))
    expected_version = int(action.payload["expected_version"])

    def operation(session: Session) -> bool:
        refs = session.execute(
            select(Lease, Assignment, Lot, Batch, Demand, Rider)
            .join(Assignment, Assignment.id == Lease.assignment_id)
            .join(Lot, Lot.id == Lease.lot_id)
            .join(Batch, Batch.id == Lot.batch_id)
            .join(Demand, Demand.id == Assignment.demand_id)
            .join(Rider, Rider.id == Lease.rider_id)
            .where(Lease.id == lease_id)
        ).one_or_none()
        if refs is None:
            return False
        lease_ref, assignment_ref, lot_ref, batch_ref, demand_ref, rider_ref = refs
        order = LockOrder()
        if action.scenario_id is not None:
            scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [action.scenario_id], order)
            if not scenarios or not scenarios[0].active:
                return False
        batches = lock_rows(session, Batch, LockRank.BATCH, [batch_ref.id], order)
        inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_ref.id], order)
        lots = lock_rows(session, Lot, LockRank.LOT, [lot_ref.id], order)
        shelters = lock_rows(session, Shelter, LockRank.SHELTER, [demand_ref.shelter_id], order)
        demands = lock_rows(session, Demand, LockRank.DEMAND, [demand_ref.id], order)
        riders = lock_rows(session, Rider, LockRank.RIDER, [rider_ref.id], order)
        assignments = lock_rows(session, Assignment, LockRank.ASSIGNMENT, [assignment_ref.id], order)
        leases = lock_rows(session, Lease, LockRank.LEASE, [lease_id], order)
        if not all((batches, inventories, lots, shelters, demands, riders, assignments, leases)):
            return False
        batch, inventory, lot, shelter, demand, rider, assignment, lease = (
            batches[0], inventories[0], lots[0], shelters[0], demands[0], riders[0], assignments[0], leases[0]
        )
        if any(
            scope != action.scenario_id
            for scope in (lease.scenario_id, lot.scenario_id, batch.scenario_id, shelter.scenario_id,
                          demand.scenario_id, rider.scenario_id, assignment.scenario_id)
        ):
            return False
        if (
            lease.version != expected_version
            or lease.status != LeaseStatus.ACTIVE.value
            or assignment.status != AssignmentStatus.RESERVED.value
            or lot.state != LotState.LEASED.value
        ):
            # Includes the pickup race: once pickup is committed, custody belongs to the rider.
            return False
        reason: str | None = None
        if action.action_type == LEASE_EXPIRY and now >= _utc(lease.expires_at):
            reason = ReasonCode.LEASE_EXPIRED.value
        elif (
            action.action_type == LEASE_FIRST_PROGRESS
            and now >= _utc(lease.first_progress_by)
            and lease.last_progress_at is None
        ):
            reason = ReasonCode.NO_PROGRESS.value
        if reason is None:
            return False

        before = {
            "lease_status": lease.status, "lease_version": lease.version,
            "assignment_status": assignment.status, "lot_state": lot.state,
            "inventory": _inventory_snapshot(inventory),
            "demand": _demand_snapshot(demand), "rider_available": rider.available,
        }
        buckets = BatchBuckets(
            total=batch.total_qty, available=inventory.available, reserved=inventory.reserved,
            carried=inventory.carried, delivered=inventory.delivered, disposed=inventory.disposed,
        ).cancel_reservation(assignment.reserved_qty)
        inventory.available = buckets.available
        inventory.reserved = buckets.reserved
        inventory.version += 1
        ledger = demand_ledger(demand).cancel_reservation(assignment.reserved_qty)
        save_demand_ledger(demand, ledger)
        assignment.status = AssignmentStatus.CANCELLED.value
        assignment.version += 1
        lot.state = LotState.OPEN.value
        lot.version += 1
        rider.available = True
        rider.version += 1
        lease.status = LeaseStatus.REVOKED.value
        lease.version += 1
        after = {
            "lease_status": lease.status, "lease_version": lease.version,
            "assignment_status": assignment.status, "lot_state": lot.state,
            "inventory": _inventory_snapshot(inventory),
            "demand": _demand_snapshot(demand), "rider_available": rider.available,
        }
        order.acquire(LockRank.EVENT_AND_SCHEDULE, [lot.id, batch.id, demand.id])
        _add_event(
            session, order, scenario_id=action.scenario_id,
            aggregate_type=AggregateType.LOT.value, aggregate_id=lot.id,
            event_type=EventType.LEASE_SUSPECT.value, reason_code=reason,
            occurred_at=now,
            payload={"lease_id": str(lease.id), "before": before, "suspect": True},
        )
        _add_event(
            session, order, scenario_id=action.scenario_id,
            aggregate_type=AggregateType.LOT.value, aggregate_id=lot.id,
            event_type=EventType.LEASE_REVOKED.value, reason_code=reason,
            occurred_at=now,
            payload={"lease_id": str(lease.id), "assignment_id": str(assignment.id), "before": before, "after": after},
        )
        _add_event(
            session, order, scenario_id=action.scenario_id,
            aggregate_type=AggregateType.BATCH.value, aggregate_id=batch.id,
            event_type=EventType.LOT_RELEASED.value, reason_code=reason,
            occurred_at=now,
            payload={"lot_id": str(lot.id), "before": before["inventory"], "after": after["inventory"]},
        )
        _add_event(
            session, order, scenario_id=action.scenario_id,
            aggregate_type=AggregateType.DEMAND.value, aggregate_id=demand.id,
            event_type=EventType.LOT_RELEASED.value, reason_code=reason,
            occurred_at=now,
            payload={"lot_id": str(lot.id), "before": before["demand"], "after": after["demand"]},
        )
        return True

    return run_transaction(operation)


def _add_event(
    session: Session,
    order: LockOrder,
    *,
    scenario_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    reason_code: str | None,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> None:
    session.add(Event(
        scenario_id=scenario_id,
        actor_id=None,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        reason_code=reason_code,
        occurred_at=occurred_at,
        payload=payload,
    ))


def _inventory_snapshot(inventory: BatchInventory) -> dict[str, int]:
    return {
        "available": inventory.available, "reserved": inventory.reserved,
        "carried": inventory.carried, "delivered": inventory.delivered,
        "disposed": inventory.disposed, "version": inventory.version,
    }


def _demand_snapshot(demand: Demand) -> dict[str, int]:
    return {
        "received": demand.received_qty, "reserved": demand.reserved_qty,
        "transit": demand.transit_qty, "version": demand.version,
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("worker timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
