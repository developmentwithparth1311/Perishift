from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts.enums import AssignmentStatus, ItemClass, LeaseStatus, LotState, OfferStatus
from app.contracts.schemas import AcceptLotRequest, DeliveryConfirmationRequest, PickupRequest
from app.domain.inventory import BatchBuckets, DemandLedger
from app.core.errors import ServiceError
from app.models import Batch, Donor, Rider, Shelter
from app.api.lots import PublishOfferRequest, router
from app.services.commitment import _check_dispatch_safety


UTC = timezone.utc


def test_nonbinding_offer_request_has_versions_and_aware_expiry():
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    body = PublishOfferRequest(
        batch_id=uuid4(),
        demand_id=uuid4(),
        rider_id=uuid4(),
        quantity=40,
        expires_at=now + timedelta(minutes=3),
        expected_batch_version=0,
        expected_demand_version=2,
        expected_rider_version=1,
    )
    assert body.quantity == 40
    assert body.expires_at.utcoffset() == timedelta(0)
    with pytest.raises(ValidationError):
        PublishOfferRequest(
            batch_id=uuid4(),
            demand_id=uuid4(),
            rider_id=uuid4(),
            quantity=40,
            expires_at="2026-10-23T12:00:00",
            expected_batch_version=0,
            expected_demand_version=0,
            expected_rider_version=0,
        )


def test_i0_command_dtos_preserve_version_and_quantity_bounds():
    rider_id = uuid4()
    assert AcceptLotRequest(rider_id=rider_id, expected_version=0).rider_id == rider_id
    assert PickupRequest(picked_qty=25, expected_version=1).picked_qty == 25
    assert DeliveryConfirmationRequest(accepted_qty=20, rejected_qty=5, expected_version=2).accepted_qty == 20
    with pytest.raises(ValidationError):
        PickupRequest(picked_qty=0, expected_version=1)
    with pytest.raises(ValidationError):
        DeliveryConfirmationRequest(accepted_qty=-1, rejected_qty=0, expected_version=2)


def test_router_exposes_nonbinding_offer_and_lifecycle_commands():
    routes = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/api/v1/dispatch/commit", "POST") in routes
    assert ("/api/v1/lots/{lot_id}/accept", "POST") in routes
    assert ("/api/v1/lots/{lot_id}/pickup", "POST") in routes
    assert ("/api/v1/lots/{lot_id}/deliver", "POST") in routes


def test_partial_pickup_conserves_inventory_and_moves_only_physical_cargo():
    buckets = BatchBuckets(total=40, available=0, reserved=40).pickup(40, 30)
    demand = DemandLedger(required=50, reserved=40).depart(30).cancel_reservation(10)
    assert (buckets.available, buckets.reserved, buckets.carried) == (10, 0, 30)
    assert buckets.accounted == 40
    assert (demand.reserved, demand.transit, demand.uncommitted_need) == (0, 30, 20)


def test_partial_delivery_keeps_rejected_quantity_in_physical_custody():
    buckets = BatchBuckets(total=30, available=0, carried=30).deliver(25)
    demand = DemandLedger(required=30, transit=30).receive(25).release_transit(5)
    carried_after_acceptance = 30 - 25
    assignment_state = AssignmentStatus.IN_TRANSIT if carried_after_acceptance else AssignmentStatus.DELIVERED
    lot_state = LotState.IN_TRANSIT if carried_after_acceptance else LotState.DELIVERED

    assert buckets.accounted == 30
    assert (buckets.carried, buckets.delivered) == (5, 25)
    assert (demand.received, demand.transit) == (25, 0)
    assert carried_after_acceptance == 5
    assert assignment_state is AssignmentStatus.IN_TRANSIT
    assert lot_state is LotState.IN_TRANSIT


def test_contract_lifecycle_states_are_distinct():
    assert OfferStatus.PUBLISHED.value != LeaseStatus.ACTIVE.value
    assert LeaseStatus.PICKED_UP.value != AssignmentStatus.IN_TRANSIT.value
    assert LotState.IN_TRANSIT.value == "IN_TRANSIT"


def test_offer_safety_gate_runs_b1_and_b2_and_fails_closed_on_stale_handling():
    now = datetime.now(UTC)
    donor = Donor(verified=True, latitude=28.6139, longitude=77.2090)
    rider = Rider(
        latitude=28.6200,
        longitude=77.2100,
        last_heartbeat_at=now,
        handling_capabilities={"food_safe_container": True},
        handling_verified_at=now,
    )
    shelter = Shelter(
        latitude=28.5355,
        longitude=77.3910,
        capabilities={},
        capability_verified_at=now,
    )
    batch = Batch(
        item_class=ItemClass.HOT_COOKED_MEAL.value,
        safety_evidence={},
        deliver_by=now + timedelta(hours=3),
    )
    _check_dispatch_safety(batch, donor, rider, shelter, now, None)

    rider.handling_verified_at = now - timedelta(days=2)
    with pytest.raises(ServiceError, match="missing or unsafe"):
        _check_dispatch_safety(batch, donor, rider, shelter, now, None)
