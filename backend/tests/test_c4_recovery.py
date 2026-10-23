from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.contracts.enums import ItemClass, ReasonCode
from app.engines.reroute import propose_reroute
from app.engines.fallback import Position
from app.services.recovery import _build_candidates
from app.domain.rules import CapabilityEvidence, CompatibilityRequirements, RiderHandlingFeatures


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def setup_candidate(*, required=40, received=0, reserved=0, transit=0,
                    intake_open=True, service_end=None):
    batch = SimpleNamespace(
        item_class=ItemClass.HOT_COOKED_MEAL.value,
        safety_evidence={},
        deliver_by=NOW + timedelta(minutes=30),
    )
    rider = SimpleNamespace(
        latitude=28.6139,
        longitude=77.2090,
        version=3,
        handling_capabilities={"food_safe_container": True},
        handling_verified_at=NOW,
    )
    demand = SimpleNamespace(
        id=uuid4(), item_class=ItemClass.HOT_COOKED_MEAL.value,
        required_qty=required, received_qty=received, reserved_qty=reserved,
        transit_qty=transit, intake_open=intake_open,
        service_window_start=None, service_window_end=service_end, version=5,
    )
    shelter = SimpleNamespace(
        id=uuid4(), name="North Shelter", latitude=28.6200, longitude=77.2100,
        intake_open=True, capabilities={}, capability_verified_at=None, version=7,
    )
    return batch, rider, demand, shelter


def test_recovery_capacity_uses_uncommitted_need_and_never_selects_partial_cargo():
    batch, rider, demand, shelter = setup_candidate(required=50, received=5, reserved=5, transit=10)
    candidates, versions = _build_candidates(
        batch, rider, 40, NOW, uuid4(), [(demand, shelter)]
    )

    assert candidates[0].available_quantity == 30
    assert versions[demand.id] == (5, 7)
    proposal = propose_reroute(
        cargo_quantity=40,
        item_class=batch.item_class,
        current_position=Position(rider.latitude, rider.longitude),
        now=NOW,
        deliver_by=batch.deliver_by,
        current_destination_id="closed-site",
        rider=RiderHandlingFeatures({"food_safe_container": CapabilityEvidence(True, NOW)}),
        requirements=CompatibilityRequirements(),
        candidates=candidates,
    )

    assert proposal.selected is None
    assert proposal.reason_code is ReasonCode.NO_FEASIBLE_DESTINATION
    assert proposal.partial_options[0].partial_capacity == 30


def test_recovery_candidate_is_closed_outside_demand_service_window():
    batch, rider, demand, shelter = setup_candidate(service_end=NOW)
    candidates, _ = _build_candidates(batch, rider, 40, NOW, uuid4(), [(demand, shelter)])

    assert candidates[0].intake_open is False
