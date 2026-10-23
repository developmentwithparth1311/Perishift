from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from app.contracts.enums import ItemClass, QuantityUnit
from app.core.config import settings
from app.demo.seed import build_seed_plan, issue_demo_bearer_tokens
from app.domain.rules import (
    CapabilityEvidence,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
    evaluate_compatibility,
)


def test_seed_plan_is_repeatable_for_a_scenario_and_has_conserved_starter_stock() -> None:
    scenario_id = uuid4()
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    first = build_seed_plan(scenario_id, now)
    second = build_seed_plan(scenario_id, now)

    assert first.result == second.result
    assert first.batch.id == first.inventory.batch_id
    assert first.batch.total_qty == first.inventory.available == first.demand.required_qty
    assert sum(
        (
            first.inventory.available,
            first.inventory.reserved,
            first.inventory.carried,
            first.inventory.delivered,
            first.inventory.disposed,
        )
    ) == first.batch.total_qty
    assert first.batch.item_class == ItemClass.HOT_COOKED_MEAL.value
    assert first.batch.unit == QuantityUnit.MEAL.value
    assert first.batch.ready_at < first.batch.deliver_by < first.batch.safe_by
    assert first.demand.service_window_start == now
    assert (first.donor.latitude, first.donor.longitude) == (19.0705, 72.8740)
    assert -90 <= first.donor.latitude <= 90
    assert -180 <= first.donor.longitude <= 180
    assert first.rider.available is True
    assert first.rider.capacity >= first.batch.total_qty


def test_seeded_destination_and_rider_evidence_pass_real_compatibility_rules() -> None:
    scenario_id = uuid4()
    now = datetime.now(timezone.utc)
    plan = build_seed_plan(scenario_id, now)
    destination = DestinationCapabilities(
        {
            key: CapabilityEvidence(True, plan.shelter.capability_verified_at)
            for key, value in plan.shelter.capabilities.items()
            if value
        }
    )
    rider = RiderHandlingFeatures(
        {
            key: CapabilityEvidence(True, plan.rider.handling_verified_at)
            for key, value in plan.rider.handling_capabilities.items()
            if value
        }
    )

    decision = evaluate_compatibility(
        ItemClass.HOT_COOKED_MEAL,
        destination,
        rider,
        CompatibilityRequirements(),
        now,
    )

    assert decision.eligible, decision.explanation


def test_new_scenario_scope_produces_fresh_actor_and_resource_ids() -> None:
    now = datetime.now(timezone.utc)
    first = build_seed_plan(uuid4(), now)
    second = build_seed_plan(uuid4(), now)

    assert first.result.scenario_id != second.result.scenario_id
    assert first.result.donor_actor_id != second.result.donor_actor_id
    assert first.result.demand_id != second.result.demand_id
    assert first.result.batch_id != second.result.batch_id


def test_result_serializes_discovery_ids_without_credentials() -> None:
    plan = build_seed_plan(uuid4(), datetime.now(timezone.utc))

    result = plan.result.to_dict()

    assert result["scenario_id"] == str(plan.result.scenario_id)
    assert result["batch_id"] == str(plan.batch.id)
    assert result["scenario_time"].endswith("+00:00")
    assert not any("token" in key or "secret" in key for key in result)


def test_demo_tokens_are_role_scoped_and_short_lived() -> None:
    plan = build_seed_plan(uuid4(), datetime.now(timezone.utc))
    issued_at = datetime.now(timezone.utc)
    tokens, expires_at = issue_demo_bearer_tokens(plan.result, now=issued_at, lifetime=timedelta(minutes=15))

    assert set(tokens) == {"donor", "rider", "shelter", "coordinator"}
    assert timedelta(minutes=14, seconds=59) < expires_at - issued_at <= timedelta(minutes=15)
    assert jwt.decode(tokens["shelter"], settings.jwt_secret, algorithms=["HS256"])["sub"] == str(
        plan.result.shelter_actor_id
    )
    with pytest.raises(ValueError, match="at most one hour"):
        issue_demo_bearer_tokens(plan.result, lifetime=timedelta(hours=2))


def test_default_local_demo_tokens_have_no_expiration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    plan = build_seed_plan(uuid4(), datetime.now(timezone.utc))
    tokens, expires_at = issue_demo_bearer_tokens(plan.result, now=datetime(2000, 1, 1, tzinfo=timezone.utc))

    assert expires_at is None
    assert set(tokens) == {"donor", "rider", "shelter", "coordinator"}
    for token in tokens.values():
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert "exp" not in claims
        assert claims["token_use"] == "local_demo"
        assert claims["scenario_id"] == str(plan.result.scenario_id)


def test_nonexpiring_credentials_cannot_be_issued_outside_demo_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", False)
    plan = build_seed_plan(uuid4(), datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="DEMO_MODE=true"):
        issue_demo_bearer_tokens(plan.result)


def test_seed_plan_requires_utc_aware_clock_value() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        build_seed_plan(uuid4(), datetime(2026, 10, 23, 12))
