"""Durable PostgreSQL scheduled-action worker and injectable iteration API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.clock import Clock, RealClock
from app.core.db import SessionLocal
from app.models import Scenario
from app.repositories.jobs import (
    active_simulated_scenario_ids,
    claim_due_actions,
    mark_action_done,
    materialize_deadline_actions,
    retry_action,
)
from app.services.watchdog import process_scheduled_action

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A timestamp snapshot for one scheduler pass."""

    value: datetime

    def now(self) -> datetime:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("scheduler clock must be timezone-aware")
        return self.value.astimezone(timezone.utc)


def run_due_once(
    *,
    clock: Clock,
    scenario_id: UUID | None,
    limit: int = 100,
) -> int:
    """Materialize, claim and process one scope's due actions.

    `clock` is injectable for deterministic simulated time. The return value is
    the number of claimed jobs; state transitions are independently idempotent,
    so a crash after transition and before DONE safely replays as a no-op.
    """
    now = _utc(clock.now())
    materialize_deadline_actions(scenario_id=scenario_id)
    actions = claim_due_actions(now=now, scenario_id=scenario_id, limit=limit)
    for action in actions:
        try:
            process_scheduled_action(action, now=now)
        except Exception as exc:
            try:
                result = retry_action(action, now=now, error=exc)
                logger.exception("Scheduled action %s failed (%s)", action.id, result)
            except Exception:
                logger.exception("Could not persist retry for scheduled action %s", action.id)
            continue
        try:
            if not mark_action_done(action):
                logger.info("Scheduled action %s claim was superseded before completion", action.id)
        except Exception as exc:
            # The claim lease makes this action recoverable if marking it done
            # failed after the domain transition committed.
            try:
                retry_action(action, now=now, error=exc)
            except Exception:
                logger.exception("Could not release failed completion claim %s", action.id)
            logger.exception("Could not mark scheduled action %s done", action.id)
    return len(actions)


def run_worker_iteration(*, live_clock: Clock | None = None, limit: int = 100) -> int:
    """Run a live scope plus each active simulated scope using its DB clock."""
    processed = run_due_once(clock=live_clock or RealClock(), scenario_id=None, limit=limit)
    for scenario_id in active_simulated_scenario_ids():
        with SessionLocal() as session:
            scenario = session.get(Scenario, scenario_id)
            if scenario is None or not scenario.active:
                continue
            timestamp = _utc(scenario.current_time)
        processed += run_due_once(
            clock=FixedClock(timestamp),
            scenario_id=scenario_id,
            limit=limit,
        )
    return processed


def worker_loop(
    *,
    stop_event: Event,
    poll_interval_seconds: float = 0.5,
    iteration: Callable[[], int] = run_worker_iteration,
) -> None:
    """Poll until stopped; `iteration` is injectable for loop-level tests."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll interval must be positive")
    while not stop_event.is_set():
        try:
            iteration()
        except Exception:
            logger.exception("Scheduler iteration failed; will retry on next poll")
        stop_event.wait(poll_interval_seconds)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
