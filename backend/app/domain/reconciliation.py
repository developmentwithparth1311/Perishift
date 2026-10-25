"""Read-only audit of persisted inventory and demand custody ledgers."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Assignment, Batch, BatchInventory, Demand, Event, Lot


ACTIVE_ASSIGNMENT_STATES = ("RESERVED", "IN_TRANSIT")
INVENTORY_BUCKETS = ("available", "reserved", "carried", "delivered", "disposed")


def reconcile_inventory(session: Session, *, scenario_id: UUID | None = None) -> dict[str, Any]:
    """Compare persisted totals with their source-of-truth active assignments.

    Walk-in overcommitment is surfaced as a warning because it represents aid
    already received by a shelter. Ledger drift remains a discrepancy.
    """
    discrepancies: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    batch_statement = (
        select(Batch, BatchInventory)
        .outerjoin(BatchInventory, BatchInventory.batch_id == Batch.id)
        .order_by(Batch.id)
    )
    if scenario_id is not None:
        batch_statement = batch_statement.where(Batch.scenario_id == scenario_id)
    batch_rows = list(session.execute(batch_statement))

    assignment_statement = (
        select(Assignment, Lot)
        .join(Lot, Lot.id == Assignment.lot_id)
        .where(Assignment.status.in_(ACTIVE_ASSIGNMENT_STATES))
        .order_by(Assignment.id)
    )
    if scenario_id is not None:
        assignment_statement = assignment_statement.where(Assignment.scenario_id == scenario_id)
    active_assignments = list(session.execute(assignment_statement))

    reserved_by_batch: dict[UUID, int] = defaultdict(int)
    carried_by_batch: dict[UUID, int] = defaultdict(int)
    reserved_ids_by_batch: dict[UUID, list[str]] = defaultdict(list)
    carried_ids_by_batch: dict[UUID, list[str]] = defaultdict(list)
    reserved_by_demand: dict[UUID, int] = defaultdict(int)
    transit_by_demand: dict[UUID, int] = defaultdict(int)
    reserved_ids_by_demand: dict[UUID, list[str]] = defaultdict(list)
    transit_ids_by_demand: dict[UUID, list[str]] = defaultdict(list)

    active_lot_ids = {lot.id for _, lot in active_assignments}
    assignment_events: dict[str, list[Event]] = defaultdict(list)
    if active_lot_ids:
        event_statement = select(Event).where(
            Event.aggregate_type == "lot",
            Event.aggregate_id.in_(active_lot_ids),
            Event.event_type.in_((
                "REROUTE_COMMITTED",
                "DELIVERY_CONFIRMED",
                "PARTIAL_DELIVERY_CONFIRMED",
            )),
        ).order_by(Event.id)
        if scenario_id is not None:
            event_statement = event_statement.where(Event.scenario_id == scenario_id)
        for event in session.scalars(event_statement):
            assignment_id = event.payload.get("assignment_id") if isinstance(event.payload, dict) else None
            if isinstance(assignment_id, str):
                assignment_events[assignment_id].append(event)

    for assignment, lot in active_assignments:
        assignment_id = str(assignment.id)
        if assignment.status == "RESERVED":
            reserved_by_batch[lot.batch_id] += assignment.reserved_qty
            reserved_ids_by_batch[lot.batch_id].append(assignment_id)
            reserved_by_demand[assignment.demand_id] += assignment.reserved_qty
            reserved_ids_by_demand[assignment.demand_id].append(assignment_id)
            expected_lot_state = "LEASED"
        else:
            carried_by_batch[lot.batch_id] += assignment.carried_qty
            carried_ids_by_batch[lot.batch_id].append(assignment_id)
            # Rejected units are released from the current destination's
            # commitment. A committed reroute deliberately rebinds all cargo,
            # including previously rejected units, to the new destination.
            history = assignment_events[assignment_id]
            last_reroute = max(
                (index for index, event in enumerate(history)
                 if event.event_type == "REROUTE_COMMITTED"
                 and event.payload.get("to_demand_id") == str(assignment.demand_id)),
                default=-1,
            )
            rejected_since_destination = sum(
                int(event.payload.get("rejected_qty", 0))
                for event in history[last_reroute + 1:]
                if event.event_type in {"DELIVERY_CONFIRMED", "PARTIAL_DELIVERY_CONFIRMED"}
                and isinstance(event.payload.get("rejected_qty", 0), int)
            )
            committed_transit = assignment.carried_qty - rejected_since_destination
            transit_by_demand[assignment.demand_id] += committed_transit
            transit_ids_by_demand[assignment.demand_id].append(assignment_id)
            expected_lot_state = "IN_TRANSIT"

        if lot.state != expected_lot_state:
            discrepancies.append({
                "code": "ACTIVE_LOT_STATE_MISMATCH",
                "entity_type": "lot",
                "entity_id": str(lot.id),
                "assignment_id": assignment_id,
                "expected": expected_lot_state,
                "actual": lot.state,
            })

    for batch, inventory in batch_rows:
        if inventory is None:
            discrepancies.append({
                "code": "BATCH_INVENTORY_MISSING",
                "entity_type": "batch",
                "entity_id": str(batch.id),
                "expected_total": batch.total_qty,
            })
            continue

        buckets = {name: getattr(inventory, name) for name in INVENTORY_BUCKETS}
        accounted = sum(buckets.values())
        if accounted != batch.total_qty:
            discrepancies.append({
                "code": "BATCH_CONSERVATION_MISMATCH",
                "entity_type": "batch",
                "entity_id": str(batch.id),
                "expected_total": batch.total_qty,
                "actual_total": accounted,
                "buckets": buckets,
            })

        expected_reserved = reserved_by_batch[batch.id]
        if inventory.reserved != expected_reserved:
            discrepancies.append({
                "code": "BATCH_RESERVED_CUSTODY_MISMATCH",
                "entity_type": "batch",
                "entity_id": str(batch.id),
                "expected": expected_reserved,
                "actual": inventory.reserved,
                "assignment_ids": sorted(reserved_ids_by_batch[batch.id]),
            })

        expected_carried = carried_by_batch[batch.id]
        if inventory.carried != expected_carried:
            discrepancies.append({
                "code": "BATCH_CARRIED_CUSTODY_MISMATCH",
                "entity_type": "batch",
                "entity_id": str(batch.id),
                "expected": expected_carried,
                "actual": inventory.carried,
                "assignment_ids": sorted(carried_ids_by_batch[batch.id]),
            })

    demand_statement = select(Demand).order_by(Demand.id)
    if scenario_id is not None:
        demand_statement = demand_statement.where(Demand.scenario_id == scenario_id)
    demands = list(session.scalars(demand_statement))

    demand_ids = [demand.id for demand in demands]
    walkin_by_demand: dict[UUID, int] = defaultdict(int)
    if demand_ids:
        event_statement = select(Event).where(
            Event.aggregate_type == "demand",
            Event.aggregate_id.in_(demand_ids),
            Event.event_type == "SHELTER_WALKIN_RECEIVED",
        )
        if scenario_id is not None:
            event_statement = event_statement.where(Event.scenario_id == scenario_id)
        for event in session.scalars(event_statement):
            quantity = event.payload.get("qty") if isinstance(event.payload, dict) else None
            if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
                walkin_by_demand[event.aggregate_id] += quantity

    for demand in demands:
        expected_reserved = reserved_by_demand[demand.id]
        if demand.reserved_qty != expected_reserved:
            discrepancies.append({
                "code": "DEMAND_RESERVED_COMMITMENT_MISMATCH",
                "entity_type": "demand",
                "entity_id": str(demand.id),
                "expected": expected_reserved,
                "actual": demand.reserved_qty,
                "assignment_ids": sorted(reserved_ids_by_demand[demand.id]),
            })

        expected_transit = transit_by_demand[demand.id]
        if demand.transit_qty != expected_transit:
            discrepancies.append({
                "code": "DEMAND_TRANSIT_COMMITMENT_MISMATCH",
                "entity_type": "demand",
                "entity_id": str(demand.id),
                "expected": expected_transit,
                "actual": demand.transit_qty,
                "assignment_ids": sorted(transit_ids_by_demand[demand.id]),
            })

        committed = demand.received_qty + demand.reserved_qty + demand.transit_qty
        excess = max(0, committed - demand.required_qty)
        external_excess = min(excess, walkin_by_demand[demand.id])
        if external_excess:
            warnings.append({
                "code": "EXTERNAL_WALKIN_OVERCOMMITMENT",
                "entity_type": "demand",
                "entity_id": str(demand.id),
                "overcommitment_qty": external_excess,
                "walkin_received_qty": walkin_by_demand[demand.id],
                "message": "Recorded walk-in receipts explain this portion of demand overcommitment.",
            })

    return {
        "scenario_id": str(scenario_id) if scenario_id else None,
        "status": "inconsistent" if discrepancies else "consistent",
        "clean": not discrepancies,
        "checked_batches": len(batch_rows),
        "checked_demands": len(demands),
        "discrepancy_count": len(discrepancies),
        "warning_count": len(warnings),
        "discrepancies": discrepancies,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit batch inventory and demand commitment ledgers.")
    parser.add_argument("--scenario-id", type=UUID, help="Limit the audit to one scenario UUID.")
    arguments = parser.parse_args()
    with SessionLocal() as session:
        report = reconcile_inventory(session, scenario_id=arguments.scenario_id)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
