from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.contracts.enums import ReasonCode
from app.domain.eta import Coordinates, DeterministicEtaProvider, haversine_km
from app.domain.safety import (
    as_utc,
    delivery_deadline,
    latest_donor_arrival,
    seconds_between,
)
from app.engines.feasibility import (
    FeasibilityPhase,
    evaluate_in_transit,
    evaluate_pre_pickup,
    pre_pickup_slack_seconds,
)


UTC = timezone.utc
SCENARIO_A = UUID("00000000-0000-0000-0000-0000000000a1")
SCENARIO_B = UUID("00000000-0000-0000-0000-0000000000b2")


def dt(hour: int, minute: int = 0, *, day: int = 23) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("rider_eta", "handover", "route_eta", "handling", "expected_slack"),
    [
        (60, 30, 120, 10, 780),
        (780, 30, 120, 10, 60),
        (840, 30, 120, 10, 0),
        (841, 30, 120, 10, -1),
    ],
)
def test_pre_pickup_formula_counts_handover_once_and_has_explicit_deadline_policy(
    rider_eta: int,
    handover: int,
    route_eta: int,
    handling: int,
    expected_slack: int,
) -> None:
    now = dt(13)
    deadline = now + timedelta(seconds=1000)
    decision = evaluate_pre_pickup(
        now=now,
        rider_to_donor_eta_seconds=rider_eta,
        handover_seconds=handover,
        donor_to_shelter_eta_seconds=route_eta,
        handling_buffer_seconds=handling,
        deliver_by=deadline,
    )
    assert decision.evidence.slack_seconds == expected_slack
    assert decision.evidence.latest_donor_arrival == deadline - timedelta(
        seconds=handover + route_eta + handling
    )
    assert decision.evidence.estimated_delivery_at == now + timedelta(
        seconds=rider_eta + handover + route_eta + handling
    )
    assert decision.feasible is (expected_slack >= 0)
    assert decision.evidence.exact_deadline_is_feasible is True
    if expected_slack == 0:
        assert decision.reason_code is None
    elif expected_slack < 0:
        assert decision.reason_code is ReasonCode.SAFETY_ETA_INFEASIBLE


def test_pre_pickup_across_utc_midnight_normalizes_offsets() -> None:
    now = datetime(2026, 10, 23, 23, 55, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    deadline = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)
    decision = evaluate_pre_pickup(
        now=now,
        rider_to_donor_eta_seconds=300,
        handover_seconds=60,
        donor_to_shelter_eta_seconds=900,
        handling_buffer_seconds=0,
        deliver_by=deadline,
    )
    assert decision.feasible
    assert decision.evidence.evaluated_at == datetime(2026, 10, 23, 18, 25, tzinfo=UTC)
    assert decision.evidence.estimated_delivery_at == datetime(
        2026, 10, 23, 18, 46, tzinfo=UTC
    )


def test_in_transit_feasibility_does_not_add_pickup_handover_again() -> None:
    now = dt(13, 59)
    decision = evaluate_in_transit(
        now=now,
        rider_to_shelter_eta_seconds=60,
        required_dropoff_seconds=0,
        deliver_by=dt(14),
    )
    assert decision.feasible
    assert decision.evidence.phase is FeasibilityPhase.IN_TRANSIT
    assert decision.evidence.estimated_delivery_at == dt(14)
    assert decision.evidence.handover_seconds == 0

    late = evaluate_in_transit(
        now=now,
        rider_to_shelter_eta_seconds=61,
        required_dropoff_seconds=0,
        deliver_by=dt(14),
    )
    assert not late.feasible
    assert late.reason_code is ReasonCode.SAFETY_ETA_INFEASIBLE


def test_already_expired_deadline_returns_expired_reason() -> None:
    result = evaluate_pre_pickup(
        now=dt(13, 1),
        rider_to_donor_eta_seconds=0,
        handover_seconds=0,
        donor_to_shelter_eta_seconds=0,
        handling_buffer_seconds=0,
        deliver_by=dt(13),
    )
    assert not result.feasible
    assert result.reason_code is ReasonCode.SAFETY_EXPIRED


def test_latest_arrival_slack_and_buffer_deadline_helpers() -> None:
    assert delivery_deadline(dt(14), 600) == dt(13, 50)
    latest = latest_donor_arrival(
        dt(14),
        handover_seconds=60,
        donor_to_shelter_eta_seconds=600,
        handling_buffer_seconds=120,
    )
    assert latest == dt(13, 47)
    assert pre_pickup_slack_seconds(
        now=dt(13, 30),
        rider_to_donor_eta_seconds=900,
        deliver_by=dt(14),
        handover_seconds=60,
        donor_to_shelter_eta_seconds=600,
        handling_buffer_seconds=120,
    ) == 120
    assert seconds_between(dt(13, 59), dt(14)) == 60


def test_eta_is_repeatable_uses_scenario_multiplier_and_ceil_rounding() -> None:
    provider = DeterministicEtaProvider(
        average_speed_kph=20,
        road_factor=1.25,
        scenario_multipliers={SCENARIO_A: 1.5, SCENARIO_B: 2.0},
    )
    origin = Coordinates(28.6139, 77.2090)
    destination = Coordinates(28.5355, 77.3910)
    baseline = provider.eta_seconds(origin, destination)
    assert baseline > 0
    assert provider.eta_seconds(origin, destination) == baseline
    assert provider.eta_seconds(origin, destination, SCENARIO_A) == (baseline * 3 + 1) // 2
    assert provider.eta_seconds(origin, destination, SCENARIO_B) > provider.eta_seconds(
        origin, destination, SCENARIO_A
    )
    assert haversine_km(origin, destination) > 0
    assert provider.eta_seconds(origin, origin) == 0


@pytest.mark.parametrize(
    "call",
    [
        lambda: as_utc(datetime(2026, 10, 23, 13), name="now"),
        lambda: delivery_deadline(dt(14), -1),
        lambda: latest_donor_arrival(
            dt(14),
            handover_seconds=-1,
            donor_to_shelter_eta_seconds=0,
            handling_buffer_seconds=0,
        ),
        lambda: evaluate_pre_pickup(
            now=dt(13),
            rider_to_donor_eta_seconds=-1,
            handover_seconds=0,
            donor_to_shelter_eta_seconds=0,
            handling_buffer_seconds=0,
            deliver_by=dt(14),
        ),
        lambda: evaluate_in_transit(
            now=dt(13),
            rider_to_shelter_eta_seconds=0,
            required_dropoff_seconds=0,
            deliver_by=None,
        ),
        lambda: Coordinates(float("nan"), 77.2),
        lambda: Coordinates(91, 77.2),
        lambda: DeterministicEtaProvider(scenario_multipliers={SCENARIO_A: 0}),
        lambda: DeterministicEtaProvider().eta_seconds(None, Coordinates(0, 0)),
    ],
)
def test_invalid_or_missing_safety_inputs_fail_closed(call) -> None:
    with pytest.raises(ValueError):
        call()
