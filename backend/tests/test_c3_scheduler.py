from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.jobs import (
    ClaimedAction,
    due_actions_statement,
    _job_values,
)
from app.workers import scheduler


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def _action(*, attempt: int = 1) -> ClaimedAction:
    return ClaimedAction(
        id=uuid4(),
        scenario_id=None,
        action_type="OFFER_EXPIRY",
        aggregate_id=uuid4(),
        due_at=NOW,
        payload={"offer_id": str(uuid4()), "expected_version": 2},
        dedupe_key="offer-expiry:test:v2",
        generation=2,
        claim_id="claim-1",
        attempt=attempt,
    )


def test_due_query_uses_skip_locked_claims_and_scope_isolation() -> None:
    compiled = due_actions_statement(now=NOW, scenario_id=uuid4(), limit=10).compile(
        dialect=postgresql.dialect()
    )
    sql = str(compiled).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "IS NOT DISTINCT FROM" in sql
    assert {"PENDING", "RUNNING"}.issubset({str(value) for value in compiled.params.values()})


def test_job_values_validate_utc_and_capture_generation() -> None:
    values = _job_values(
        scenario_id=None,
        action_type="LEASE_EXPIRY",
        aggregate_id=uuid4(),
        due_at=NOW + timedelta(seconds=1),
        dedupe_key="lease:test:v4",
        generation=4,
        payload={"lease_id": "abc"},
    )
    assert values["status"] == "PENDING"
    assert values["generation"] == 4
    assert values["due_at"].tzinfo == timezone.utc
    with pytest.raises(ValueError, match="UTC offset"):
        _job_values(
            scenario_id=None,
            action_type="LEASE_EXPIRY",
            aggregate_id=uuid4(),
            due_at=NOW.replace(tzinfo=None),
            dedupe_key="bad",
            generation=0,
            payload={},
        )


def test_run_due_once_completes_transition_then_marks_claim_done(monkeypatch) -> None:
    action = _action()
    calls: list[object] = []
    monkeypatch.setattr(scheduler, "materialize_deadline_actions", lambda **kwargs: calls.append(("materialize", kwargs)))
    monkeypatch.setattr(scheduler, "claim_due_actions", lambda **kwargs: [action])
    monkeypatch.setattr(scheduler, "process_scheduled_action", lambda current, *, now: calls.append(("process", current, now)))
    monkeypatch.setattr(scheduler, "mark_action_done", lambda current: calls.append(("done", current)) or True)
    monkeypatch.setattr(scheduler, "retry_action", lambda *args, **kwargs: pytest.fail("unexpected retry"))

    assert scheduler.run_due_once(clock=scheduler.FixedClock(NOW), scenario_id=None) == 1
    assert calls[0] == ("materialize", {"scenario_id": None})
    assert calls[1] == ("process", action, NOW)
    assert calls[2] == ("done", action)


def test_run_due_once_retries_failed_transition_without_marking_done(monkeypatch) -> None:
    action = _action()
    retries: list[object] = []
    monkeypatch.setattr(scheduler, "materialize_deadline_actions", lambda **kwargs: None)
    monkeypatch.setattr(scheduler, "claim_due_actions", lambda **kwargs: [action])

    def fail(*args, **kwargs):
        raise RuntimeError("transient database fault")

    monkeypatch.setattr(scheduler, "process_scheduled_action", fail)
    monkeypatch.setattr(scheduler, "mark_action_done", lambda *_: pytest.fail("failed action marked done"))
    monkeypatch.setattr(scheduler, "retry_action", lambda current, **kwargs: retries.append((current, kwargs)) or "RETRY")

    assert scheduler.run_due_once(clock=scheduler.FixedClock(NOW), scenario_id=None) == 1
    assert retries[0][0] == action
    assert retries[0][1]["now"] == NOW
    assert isinstance(retries[0][1]["error"], RuntimeError)


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler.FixedClock(NOW.replace(tzinfo=None)).now()
