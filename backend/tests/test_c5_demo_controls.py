from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.demo import (
    AdvanceScenarioRequest,
    CapabilityLossRequest,
    ShelterVersionRequest,
    WalkinRequest,
    router,
)
from app.contracts.enums import ScenarioMode
from app.demo.scenarios import (
    DemoScenarioError,
    _advance_due_work,
    _authorized_actor_scope,
    _authorized_scenario,
)
from app.core.config import settings
from app.models import Scenario, User


SCENARIO_ID = UUID("00000000-0000-0000-0000-0000000000a1")
OTHER_SCENARIO_ID = UUID("00000000-0000-0000-0000-0000000000b2")
ACTOR_ID = UUID("00000000-0000-0000-0000-0000000000c3")
START = datetime(2026, 10, 23, 23, 50, tzinfo=timezone.utc)


def test_advance_steps_due_work_in_order_and_drains_new_due_actions():
    first_due = START + timedelta(minutes=5)
    second_due = START + timedelta(minutes=20)
    target = START + timedelta(minutes=30)
    due_jobs = [(first_due, "one"), (first_due, "two"), (second_due, "three")]
    clock_steps = []
    processed = []

    def next_due(until):
        values = [at for at, _ in due_jobs if at <= until]
        return min(values) if values else None

    def process_due(at):
        ready = sorted((job for job in due_jobs if job[0] <= at), key=lambda job: job)
        for job in ready:
            due_jobs.remove(job)
            processed.append(job[1])
            if job[1] == "one":
                due_jobs.append((at, "created-at-current-time"))
        return len(ready)

    count, steps = _advance_due_work(
        scenario_id=SCENARIO_ID,
        start=START,
        target=target,
        set_clock=clock_steps.append,
        process_due=process_due,
        materialize=lambda: None,
        next_due=next_due,
        due_work_remains=lambda _at: False,
        wait=lambda _seconds: None,
    )

    assert count == 4
    assert processed == ["one", "two", "created-at-current-time", "three"]
    assert steps == [first_due, second_due]
    assert clock_steps == [first_due, second_due, target]
    assert target.day == 24


def test_advance_handles_no_due_actions_and_rejects_backwards_target():
    final_time = START + timedelta(seconds=1)
    clocks = []
    count, due_times = _advance_due_work(
        scenario_id=SCENARIO_ID,
        start=START,
        target=final_time,
        set_clock=clocks.append,
        process_due=lambda _at: 0,
        materialize=lambda: None,
        next_due=lambda _target: None,
        due_work_remains=lambda _at: False,
        wait=lambda _seconds: None,
    )
    assert count == 0
    assert due_times == []
    assert clocks == [final_time]

    with pytest.raises(ValueError, match="cannot precede"):
        _advance_due_work(
            scenario_id=SCENARIO_ID,
            start=START,
            target=START - timedelta(seconds=1),
            set_clock=lambda _at: None,
            process_due=lambda _at: 0,
            materialize=lambda: None,
            next_due=lambda _target: None,
            due_work_remains=lambda _at: False,
            wait=lambda _seconds: None,
        )


def test_advance_waits_for_worker_claim_then_retries_processor():
    attempts = []
    waits = []
    remains = [True, False]
    next_due_calls = [0]

    def next_due(_target):
        if next_due_calls[0] == 0:
            next_due_calls[0] += 1
            return START + timedelta(seconds=3)
        return None

    count, steps = _advance_due_work(
        scenario_id=SCENARIO_ID,
        start=START,
        target=START + timedelta(seconds=10),
        set_clock=lambda _at: None,
        process_due=lambda _at: attempts.append("process") or 0,
        materialize=lambda: None,
        next_due=next_due,
        due_work_remains=lambda _at: remains.pop(0),
        wait=waits.append,
    )
    assert count == 0
    assert steps == [START + timedelta(seconds=3)]
    assert attempts == ["process", "process"]
    assert waits == [0.1]


class MemorySession:
    def __init__(self, actor, scenario):
        self.rows = {(User, actor.id): actor, (Scenario, scenario.id): scenario}

    def get(self, model, entity_id):
        return self.rows.get((model, entity_id))


def test_demo_authority_requires_feature_flag_role_matching_scope_and_active_simulation(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    coordinator = SimpleNamespace(id=ACTOR_ID, active=True, role="COORDINATOR", scenario_id=SCENARIO_ID)
    scenario = SimpleNamespace(id=SCENARIO_ID, active=True, mode=ScenarioMode.SIMULATED.value)
    actor, scoped = _authorized_scenario(MemorySession(coordinator, scenario), ACTOR_ID, SCENARIO_ID)
    assert actor is coordinator and scoped is scenario

    with pytest.raises(DemoScenarioError) as mismatch:
        _authorized_scenario(MemorySession(coordinator, scenario), ACTOR_ID, OTHER_SCENARIO_ID)
    assert mismatch.value.status_code == 404

    for role in ("DONOR", "RIDER", "SHELTER"):
        forbidden = SimpleNamespace(**{**coordinator.__dict__, "role": role})
        with pytest.raises(DemoScenarioError) as error:
            _authorized_scenario(MemorySession(forbidden, scenario), ACTOR_ID, SCENARIO_ID)
        assert error.value.status_code == 403

    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(DemoScenarioError) as disabled:
        _authorized_scenario(MemorySession(coordinator, scenario), ACTOR_ID, SCENARIO_ID)
    assert disabled.value.status_code == 404


def test_reset_replay_scope_allows_retired_scenario_but_normal_controls_do_not(monkeypatch):
    actor = SimpleNamespace(id=ACTOR_ID, active=True, role="COORDINATOR", scenario_id=SCENARIO_ID)
    retired = SimpleNamespace(id=SCENARIO_ID, active=False, mode=ScenarioMode.SIMULATED.value)
    monkeypatch.setattr(settings, "demo_mode", True)
    assert _authorized_actor_scope(MemorySession(actor, retired), ACTOR_ID, SCENARIO_ID) == (
        actor,
        retired,
    )
    with pytest.raises(DemoScenarioError, match="Active simulated"):
        _authorized_scenario(MemorySession(actor, retired), ACTOR_ID, SCENARIO_ID)


def test_demo_request_contracts_are_strict_and_routes_cover_mvp_controls():
    assert AdvanceScenarioRequest(seconds=900).seconds == 900
    with pytest.raises(ValidationError):
        AdvanceScenarioRequest(seconds=True)
    with pytest.raises(ValidationError):
        AdvanceScenarioRequest(seconds=86_401)
    with pytest.raises(ValidationError):
        ShelterVersionRequest(expected_shelter_version=-1)
    with pytest.raises(ValidationError):
        CapabilityLossRequest(expected_shelter_version=0, verified_at="2026-10-23T13:00:00")
    with pytest.raises(ValidationError):
        WalkinRequest(demand_id=UUID(int=5), qty=1.5, expected_demand_version=0)

    paths = {route.path for route in router.routes}
    assert "/api/v1/demo/scenarios/{scenario_id}/advance" in paths
    assert "/api/v1/demo/scenarios/{scenario_id}/reset" in paths
    assert "/api/v1/demo/scenarios/{scenario_id}/riders/{rider_id}/silent" in paths
    assert "/api/v1/demo/scenarios/{scenario_id}/shelters/{shelter_id}/close" in paths
    assert "/api/v1/demo/scenarios/{scenario_id}/shelters/{shelter_id}/walk-in" in paths
    assert "/api/v1/demo/scenarios/{scenario_id}/shelters/{shelter_id}/capabilities/{capability}/lose" in paths

    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    for path in (
        "/api/v1/demo/scenarios/{scenario_id}/advance",
        "/api/v1/demo/scenarios/{scenario_id}/reset",
    ):
        assert any(
            parameter["name"] == "Idempotency-Key" and parameter["required"]
            for parameter in schema["paths"][path]["post"]["parameters"]
        )
