from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.eta import Coordinates
from app.domain.rules import (
    CapabilityEvidence,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
)
from app.engines.planner_greedy import (
    PlannerBatch,
    PlannerDemand,
    PlannerRider,
    plan_greedy,
)


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
ORIGIN = Coordinates(28.6139, 77.209)


def uid(value: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{value:012d}")


def _verified(**capabilities: bool):
    return {
        name: CapabilityEvidence(available=value, verified_at=NOW)
        for name, value in capabilities.items()
    }


def make_batch(qty: int = 180, *, batch_id: int = 1, deliver_by=None) -> PlannerBatch:
    return PlannerBatch(
        batch_id=uid(batch_id),
        item_class=ItemClass.HOT_COOKED_MEAL,
        available_qty=qty,
        donor_location=ORIGIN,
        ready_at=NOW,
        deliver_by=deliver_by or NOW + timedelta(hours=3),
    )


def make_rider(rider_id: int, capacity: int = 40, *, active: bool = False) -> PlannerRider:
    return PlannerRider(
        rider_id=uid(rider_id),
        location=ORIGIN,
        capacity_qty=capacity,
        handling=RiderHandlingFeatures(
            capabilities=_verified(food_safe_container=True)
        ),
        has_active_trip=active,
    )


def make_demand(
    demand_id: int = 1,
    need: int = 500,
    *,
    intake_open: bool = True,
    location: Coordinates = ORIGIN,
) -> PlannerDemand:
    return PlannerDemand(
        demand_id=uid(demand_id),
        shelter_id=uid(100 + demand_id),
        item_class=ItemClass.HOT_COOKED_MEAL,
        location=location,
        uncommitted_need=need,
        intake_open=intake_open,
        capabilities=DestinationCapabilities(),
    )


def test_180_quantity_splits_across_five_riders_without_rounding():
    result = plan_greedy(
        now=NOW,
        batches=[make_batch()],
        riders=[make_rider(i) for i in (5, 4, 3, 2, 1)],
        demands=[make_demand()],
    )

    assert [proposal.quantity for proposal in result.proposals] == [40, 40, 40, 40, 20]
    assert len({proposal.rider_id for proposal in result.proposals}) == 5
    assert result.planned_qty == 180
    assert result.unallocated_qty == 0
    assert result.planned_qty + result.unallocated_qty == 180


def test_partial_plan_keeps_exact_unallocated_remainder_and_reasons():
    result = plan_greedy(
        now=NOW,
        batches=[make_batch(123)],
        riders=[make_rider(1, 40), make_rider(2, 50)],
        demands=[make_demand(need=500)],
    )

    assert result.planned_qty == 90
    assert result.unallocated_qty == 33
    assert result.proposals[0].quantity == 50
    assert result.unallocated[0].reason_codes == (ReasonCode.RIDER_ALREADY_ASSIGNED,)
    assert result.planned_qty + result.unallocated_qty == 123


def test_full_shelters_active_riders_and_empty_stock_are_truthfully_reported():
    full = plan_greedy(
        now=NOW,
        batches=[make_batch(37)],
        riders=[make_rider(1)],
        demands=[make_demand(need=0)],
    )
    active = plan_greedy(
        now=NOW,
        batches=[make_batch(37, batch_id=2)],
        riders=[make_rider(1, active=True)],
        demands=[make_demand()],
    )
    empty = plan_greedy(
        now=NOW,
        batches=[make_batch(0, batch_id=3)],
        riders=[make_rider(1)],
        demands=[make_demand()],
    )

    assert full.proposals == ()
    assert full.unallocated_qty == 37
    assert ReasonCode.CAPACITY_SHELTER in full.unallocated[0].reason_codes
    assert active.proposals == ()
    assert ReasonCode.RIDER_ALREADY_ASSIGNED in active.unallocated[0].reason_codes
    assert empty.proposals == ()
    assert empty.unallocated_qty == 0


def test_closed_destination_and_incompatible_item_do_not_become_proposals():
    closed = replace(make_demand(intake_open=True), intake_open=False)
    medicine = replace(
        closed,
        demand_id=uid(2),
        item_class=ItemClass.COLD_CHAIN_MEDICINE,
        intake_open=True,
    )
    result = plan_greedy(
        now=NOW,
        batches=[make_batch(8)],
        riders=[make_rider(1)],
        demands=[closed, medicine],
    )

    assert result.proposals == ()
    assert result.unallocated_qty == 8
    assert result.unallocated[0].reason_codes == (ReasonCode.DEMAND_CLOSED,)


def test_compatibility_and_deadline_are_hard_filters_with_reason_codes():
    unsafe_rider = replace(
        make_rider(1),
        handling=RiderHandlingFeatures(capabilities={}),
    )
    late_batch = make_batch(9, deliver_by=NOW + timedelta(seconds=1))
    result_compatibility = plan_greedy(
        now=NOW,
        batches=[make_batch(10)],
        riders=[unsafe_rider],
        demands=[make_demand()],
    )
    result_deadline = plan_greedy(
        now=NOW,
        batches=[late_batch],
        riders=[make_rider(1)],
        demands=[make_demand(location=Coordinates(35.0, 139.0))],
    )

    assert result_compatibility.proposals == ()
    assert ReasonCode.SAFETY_EVIDENCE_MISSING in result_compatibility.unallocated[0].reason_codes
    assert result_deadline.proposals == ()
    assert ReasonCode.SAFETY_ETA_INFEASIBLE in result_deadline.unallocated[0].reason_codes


def test_fallbacks_are_feasible_candidates_and_proposals_do_not_reserve_demand():
    demands = [
        make_demand(1, need=10, location=Coordinates(28.614, 77.209)),
        make_demand(2, need=25, location=Coordinates(28.615, 77.209)),
    ]
    result = plan_greedy(
        now=NOW,
        batches=[make_batch(20)],
        riders=[make_rider(1, 20)],
        demands=demands,
    )

    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.quantity == 20
    assert len(proposal.fallback_candidates) == 1
    fallback = proposal.fallback_candidates[0]
    assert fallback.demand_id == uid(1)
    assert fallback.estimated_qty == 10
    # Input snapshots are immutable; publication of proposals changes no stock or demand.
    assert demands[0].uncommitted_need == 10
    assert demands[1].uncommitted_need == 25


def test_tie_breaking_is_stable_independent_of_input_order():
    batch = make_batch(10)
    riders = [make_rider(9, 10), make_rider(2, 10)]
    demands = [make_demand(8, 10), make_demand(3, 10)]

    first = plan_greedy(now=NOW, batches=[batch], riders=riders, demands=demands)
    second = plan_greedy(now=NOW, batches=[batch], riders=reversed(riders), demands=reversed(demands))

    assert first == second
    assert first.proposals[0].rider_id == uid(2)
    assert first.proposals[0].demand_id == uid(3)


def test_invalid_fractional_or_duplicate_snapshot_values_fail_closed():
    with pytest.raises(ValueError, match="nonnegative integer"):
        plan_greedy(
            now=NOW,
            batches=[replace(make_batch(), available_qty=2.5)],  # type: ignore[arg-type]
            riders=[make_rider(1)],
            demands=[make_demand()],
        )
    with pytest.raises(ValueError, match="identifiers must be unique"):
        plan_greedy(
            now=NOW,
            batches=[make_batch(1), make_batch(2)],
            riders=[],
            demands=[],
        )
