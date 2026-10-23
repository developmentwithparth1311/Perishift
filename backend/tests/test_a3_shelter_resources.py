from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.shelters import (
    CapabilityEvent,
    CreateDemandRequest,
    IntakeEvent,
    WalkinEvent,
    router,
)
from app.contracts.enums import ItemClass
from app.core.db import LockOrder
from app.models import Demand
from app.services.resources_shelter import (
    ShelterResourceError,
    _event,
    _demand_dict,
    create_demand,
    record_walkin,
)


def test_demand_snapshot_uses_authoritative_reserved_and_transit_ledger() -> None:
    demand = Demand(
        id=uuid4(),
        scenario_id=None,
        shelter_id=uuid4(),
        item_class=ItemClass.HOT_COOKED_MEAL.value,
        unit="MEAL",
        required_qty=100,
        received_qty=15,
        reserved_qty=20,
        transit_qty=25,
        intake_open=True,
        service_window_start=None,
        service_window_end=None,
        version=3,
    )

    snapshot = _demand_dict(demand)

    assert snapshot["uncommitted_need"] == 40
    assert snapshot["received_qty"] == 15
    assert snapshot["reserved_qty"] == 20
    assert snapshot["transit_qty"] == 25
    assert snapshot["version"] == 3


def test_audit_event_before_and_after_payload_is_json_serializable() -> None:
    class EventCollector:
        def __init__(self) -> None:
            self.events = []

        def add(self, event) -> None:
            self.events.append(event)

    collector = EventCollector()
    actor = type("Actor", (), {"id": uuid4()})()
    at = datetime.now(timezone.utc)
    _event(
        collector,  # type: ignore[arg-type]
        order=LockOrder(),
        actor=actor,  # type: ignore[arg-type]
        scenario_id=None,
        aggregate_type="demand",
        aggregate_id=uuid4(),
        event_type="DEMAND_UPDATED",
        at=at,
        before={"verified_at": at},
        after={"received_qty": 5},
    )

    assert collector.events[0].payload["before"]["verified_at"] == at.isoformat()


def test_create_demand_contract_requires_complete_aware_service_window() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="Both service window bounds"):
        CreateDemandRequest(
            item_class=ItemClass.RAW_RATION_KIT,
            required_qty=5,
            service_window_start=now,
        )

    with pytest.raises(ValidationError, match="timestamp must include a UTC offset"):
        CreateDemandRequest(
            item_class=ItemClass.RAW_RATION_KIT,
            required_qty=5,
            service_window_start=datetime(2026, 10, 23, 10),
            service_window_end=datetime(2026, 10, 23, 11, tzinfo=timezone.utc),
        )


def test_event_contracts_reject_invalid_walkin_and_empty_or_future_capabilities() -> None:
    with pytest.raises(ValidationError):
        WalkinEvent(type="WALKIN_RECEIVED", demand_id=uuid4(), qty=0, expected_version=0)
    with pytest.raises(ValidationError):
        WalkinEvent(type="WALKIN_RECEIVED", demand_id=uuid4(), qty=True, expected_version=0)
    with pytest.raises(ValidationError, match="At least one named capability"):
        CapabilityEvent(
            type="CAPABILITY_CHANGED",
            capabilities={},
            verified_at=datetime.now(timezone.utc),
            source="inspection",
            expected_version=0,
        )
    with pytest.raises(ValidationError):
        IntakeEvent(type="CLOSE_INTAKE", expected_version=-1)


def test_service_rejects_invalid_mutations_before_database_access() -> None:
    actor_id, shelter_id, demand_id = uuid4(), uuid4(), uuid4()
    with pytest.raises(ShelterResourceError, match="positive integer"):
        record_walkin(
            actor_id,
            shelter_id,
            demand_id,
            qty=-1,
            expected_version=0,
            idempotency_key="walkin-invalid",
        )
    with pytest.raises(ShelterResourceError, match="nonnegative"):
        create_demand(
            actor_id,
            item_class=ItemClass.HOT_COOKED_MEAL,
            required_qty=-2,
            service_window_start=None,
            service_window_end=None,
            idempotency_key="demand-invalid",
        )


def test_capability_timestamp_must_be_aware_and_not_future() -> None:
    from app.services.resources_shelter import update_capabilities

    with pytest.raises(ShelterResourceError, match="UTC offset"):
        update_capabilities(
            uuid4(), uuid4(), capabilities={"water": True},
            verified_at=datetime(2026, 10, 23, 10), source="inspection",
            expected_version=0, idempotency_key="cap-naive",
        )
    with pytest.raises(ShelterResourceError, match="future"):
        update_capabilities(
            uuid4(), uuid4(), capabilities={"water": True},
            verified_at=datetime.now(timezone.utc) + timedelta(days=1),
            source="inspection", expected_version=0, idempotency_key="cap-future",
        )


def test_shelter_router_exposes_resource_and_event_paths() -> None:
    paths = {(route.path, tuple(sorted(route.methods or ()))) for route in router.routes}
    assert ("/demands", ("GET",)) in paths
    assert ("/demands", ("POST",)) in paths
    assert ("/demands/{demand_id}", ("PATCH",)) in paths
    assert ("/shelters/{shelter_id}/events", ("POST",)) in paths

    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    event_post = schema["paths"]["/shelters/{shelter_id}/events"]["post"]
    assert "Idempotency-Key" in {
        parameter["name"]
        for parameter in event_post["parameters"]
    }
