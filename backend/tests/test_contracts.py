from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.contracts.enums import EventType, ItemClass, QuantityUnit, ReasonCode
from app.contracts.schemas import (
    BatchBalance,
    CreateBatchRequest,
    DemandSnapshot,
    EventEnvelope,
)
from app.main import app


def test_contract_models_enforce_units_time_and_ledger_invariants() -> None:
    request = CreateBatchRequest(
        donor_id=UUID("00000000-0000-0000-0000-000000000001"),
        item_class=ItemClass.HOT_COOKED_MEAL,
        unit=QuantityUnit.MEAL,
        qty=180,
        ready_at="2026-10-23T13:00:00+05:30",
        declared_safe_by="2026-10-23T14:15:00+05:30",
    )
    assert request.ready_at.utcoffset().total_seconds() == 0

    with pytest.raises(ValidationError):
        CreateBatchRequest(
            donor_id=request.donor_id,
            item_class=ItemClass.HOT_COOKED_MEAL,
            unit=QuantityUnit.KIT,
            qty=10,
            ready_at="2026-10-23T13:00:00Z",
            declared_safe_by="2026-10-23T14:15:00Z",
        )
    with pytest.raises(ValidationError):
        CreateBatchRequest(
            donor_id=request.donor_id,
            item_class=ItemClass.HOT_COOKED_MEAL,
            unit=QuantityUnit.MEAL,
            qty=10,
            ready_at="2026-10-23T13:00:00",
            declared_safe_by="2026-10-23T14:15:00Z",
        )

    balance = BatchBalance(
        batch_id=UUID("00000000-0000-0000-0000-000000000002"),
        item_class=ItemClass.HOT_COOKED_MEAL,
        unit=QuantityUnit.MEAL,
        total_qty=180,
        available_qty=20,
        reserved_qty=40,
        carried_qty=80,
        delivered_qty=40,
        disposed_qty=0,
        safe_by=datetime(2026, 10, 23, 14, 15, tzinfo=timezone.utc),
        deliver_by=datetime(2026, 10, 23, 14, 5, tzinfo=timezone.utc),
        version=8,
    )
    assert balance.total_qty == 180
    with pytest.raises(ValidationError):
        BatchBalance.model_validate({**balance.model_dump(), "available_qty": 19})

    demand = DemandSnapshot(
        demand_id=UUID("00000000-0000-0000-0000-000000000003"),
        shelter_id=UUID("00000000-0000-0000-0000-000000000004"),
        item_class=ItemClass.HOT_COOKED_MEAL,
        unit=QuantityUnit.MEAL,
        required_qty=50,
        received_qty=10,
        reserved_qty=20,
        transit_qty=15,
        uncommitted_need=5,
        intake_open=True,
        version=4,
    )
    assert demand.uncommitted_need == 5
    with pytest.raises(ValidationError):
        DemandSnapshot.model_validate({**demand.model_dump(), "uncommitted_need": 6})

    event = EventEnvelope(
        id=1038,
        type=EventType.LEASE_REVOKED,
        aggregate_type="lot",
        aggregate_id=balance.batch_id,
        occurred_at="2026-10-23T13:17:25Z",
        reason_code=ReasonCode.NO_PROGRESS,
    )
    assert event.model_dump(mode="json")["type"] == "LEASE_REVOKED"


def test_openapi_publishes_contract_components_and_health_routes() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]

    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
    assert "BatchBalance" in components
    assert "CreateBatchRequest" in components
    assert "DemandSnapshot" in components
    assert "EventEnvelope" in components
    assert "ErrorEnvelope" in components
    assert "AT_RISK" not in components["LotState"]["enum"]
    assert "AT_RISK" in components["OperationalFlag"]["enum"]
