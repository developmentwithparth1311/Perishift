"""Durable PostgreSQL scheduled-action queue operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, and_, cast, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, run_transaction
from app.models import Lease, Offer, Scenario, ScheduledAction


OFFER_EXPIRY = "OFFER_EXPIRY"
LEASE_FIRST_PROGRESS = "LEASE_FIRST_PROGRESS"
LEASE_EXPIRY = "LEASE_EXPIRY"
_CLAIM_ID = "_worker_claim_id"
_CLAIM_UNTIL = "_worker_claim_until"
_ATTEMPT = "_worker_attempt"
_LAST_ERROR = "_worker_last_error"


@dataclass(frozen=True, slots=True)
class ClaimedAction:
    id: UUID
    scenario_id: UUID | None
    action_type: str
    aggregate_id: UUID
    due_at: datetime
    payload: dict[str, Any]
    dedupe_key: str
    generation: int
    claim_id: str
    attempt: int


def _aware_utc(value: datetime, name: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def _scenario_filter(scenario_id: UUID | None):
    return ScheduledAction.scenario_id.is_not_distinct_from(scenario_id)


def schedule_action(
    session: Session,
    *,
    scenario_id: UUID | None,
    action_type: str,
    aggregate_id: UUID,
    due_at: datetime,
    payload: dict[str, Any],
    dedupe_key: str,
    generation: int = 0,
) -> ScheduledAction:
    """Insert one durable action inside the caller's transaction, idempotently.

    Returns the existing row when another caller already scheduled the same
    scenario/dedupe key. Use this helper from domain mutations so the resource
    change and its deadline action commit or roll back together.
    """
    values = _job_values(
        scenario_id=scenario_id,
        action_type=action_type,
        aggregate_id=aggregate_id,
        due_at=due_at,
        dedupe_key=dedupe_key,
        generation=generation,
        payload=payload,
    )
    row_id = session.scalar(
        insert(ScheduledAction)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[ScheduledAction.scenario_id, ScheduledAction.dedupe_key]
        )
        .returning(ScheduledAction.id)
    )
    row = session.get(ScheduledAction, row_id) if row_id else session.scalar(
        select(ScheduledAction).where(
            _scenario_filter(scenario_id),
            ScheduledAction.dedupe_key == dedupe_key,
        )
    )
    if row is None:
        raise RuntimeError("scheduled action insert did not produce a row")
    return row


def materialize_deadline_actions(*, scenario_id: UUID | None) -> int:
    """Persist deterministic offer and lease deadline jobs from authoritative rows.

    This is also the crash-gap repair path: if a process dies after an offer/lease
    commits but before the next scheduler poll, the next poll recreates its job.
    Dedupe keys are versioned so renewed or transitioned resources invalidate old
    jobs without deleting their audit history.
    """

    def operation(session: Session) -> int:
        if scenario_id is not None:
            scenario = session.get(Scenario, scenario_id)
            if scenario is None or not scenario.active:
                return 0
        offers = session.scalars(
            select(Offer).where(
                _scenario_filter(scenario_id),
                Offer.status == "PUBLISHED",
            )
        ).all()
        leases = session.scalars(
            select(Lease).where(
                _scenario_filter(scenario_id),
                Lease.status == "ACTIVE",
            )
        ).all()
        scheduled: list[dict[str, Any]] = []
        for offer in offers:
            scheduled.append(
                _job_values(
                    scenario_id=scenario_id,
                    action_type=OFFER_EXPIRY,
                    aggregate_id=offer.lot_id,
                    due_at=offer.expires_at,
                    dedupe_key=f"offer-expiry:{offer.id}:v{offer.version}",
                    generation=offer.version,
                    payload={"offer_id": str(offer.id), "expected_version": offer.version},
                )
            )
        for lease in leases:
            scheduled.extend(
                (
                    _job_values(
                        scenario_id=scenario_id,
                        action_type=LEASE_FIRST_PROGRESS,
                        aggregate_id=lease.lot_id,
                        due_at=lease.first_progress_by,
                        dedupe_key=f"lease-first-progress:{lease.id}:v{lease.version}",
                        generation=lease.version,
                        payload={"lease_id": str(lease.id), "expected_version": lease.version},
                    ),
                    _job_values(
                        scenario_id=scenario_id,
                        action_type=LEASE_EXPIRY,
                        aggregate_id=lease.lot_id,
                        due_at=lease.expires_at,
                        dedupe_key=f"lease-expiry:{lease.id}:v{lease.version}",
                        generation=lease.version,
                        payload={"lease_id": str(lease.id), "expected_version": lease.version},
                    ),
                )
            )
        if not scheduled:
            return 0
        insert_result = session.execute(
            insert(ScheduledAction)
            .values(scheduled)
            .on_conflict_do_nothing(
                index_elements=[ScheduledAction.scenario_id, ScheduledAction.dedupe_key]
            )
        )
        return max(0, insert_result.rowcount or 0)

    return run_transaction(operation)


def _job_values(
    *,
    scenario_id: UUID | None,
    action_type: str,
    aggregate_id: UUID,
    due_at: datetime,
    dedupe_key: str,
    generation: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "scenario_id": scenario_id,
        "action_type": action_type,
        "aggregate_id": aggregate_id,
        "due_at": _aware_utc(due_at, "due_at"),
        "payload": payload,
        "dedupe_key": dedupe_key,
        "status": "PENDING",
        "generation": generation,
    }


def due_actions_statement(
    *, now: datetime, scenario_id: UUID | None, limit: int
):
    """Build the PostgreSQL due-job claim query; exposed for SQL contract tests."""
    now = _aware_utc(now, "now")
    claim_until = cast(
        ScheduledAction.payload[_CLAIM_UNTIL].as_string(),
        DateTime(timezone=True),
    )
    pending_due = and_(
        ScheduledAction.status == "PENDING",
        ScheduledAction.due_at <= now,
    )
    abandoned_claim = and_(
        ScheduledAction.status == "RUNNING",
        claim_until.is_not(None),
        claim_until <= now,
    )
    return (
        select(ScheduledAction)
        .where(_scenario_filter(scenario_id), or_(pending_due, abandoned_claim))
        .order_by(ScheduledAction.due_at, ScheduledAction.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def claim_due_actions(
    *,
    now: datetime,
    scenario_id: UUID | None,
    limit: int = 100,
    claim_seconds: int = 30,
) -> list[ClaimedAction]:
    now = _aware_utc(now, "now")
    if limit < 1 or claim_seconds < 1:
        raise ValueError("limit and claim_seconds must be positive")
    claim_until = now + timedelta(seconds=claim_seconds)

    def operation(session: Session) -> list[ClaimedAction]:
        rows = list(session.scalars(due_actions_statement(now=now, scenario_id=scenario_id, limit=limit)))
        claimed: list[ClaimedAction] = []
        for row in rows:
            current = dict(row.payload or {})
            attempt = int(current.get(_ATTEMPT, 0)) + 1
            claim_id = str(uuid4())
            current[_CLAIM_ID] = claim_id
            current[_CLAIM_UNTIL] = claim_until.isoformat()
            current[_ATTEMPT] = attempt
            row.payload = current
            row.status = "RUNNING"
            claimed.append(
                ClaimedAction(
                    id=row.id,
                    scenario_id=row.scenario_id,
                    action_type=row.action_type,
                    aggregate_id=row.aggregate_id,
                    due_at=row.due_at,
                    payload=dict(current),
                    dedupe_key=row.dedupe_key,
                    generation=row.generation,
                    claim_id=claim_id,
                    attempt=attempt,
                )
            )
        return claimed

    return run_transaction(operation)


def mark_action_done(action: ClaimedAction) -> bool:
    def operation(session: Session) -> bool:
        row = session.scalar(
            select(ScheduledAction)
            .where(ScheduledAction.id == action.id)
            .with_for_update()
        )
        if row is None or row.status != "RUNNING" or (row.payload or {}).get(_CLAIM_ID) != action.claim_id:
            return False
        payload = dict(row.payload or {})
        _clear_worker_metadata(payload)
        row.payload = payload
        row.status = "DONE"
        return True

    return run_transaction(operation)


def retry_action(
    action: ClaimedAction,
    *,
    now: datetime,
    error: BaseException,
    max_attempts: int = 8,
    base_delay_seconds: float = 1.0,
) -> str:
    """Reschedule with capped exponential backoff, or mark terminal failure."""
    now = _aware_utc(now, "now")
    if max_attempts < 1 or base_delay_seconds <= 0:
        raise ValueError("retry policy must be positive")

    def operation(session: Session) -> str:
        row = session.scalar(
            select(ScheduledAction)
            .where(ScheduledAction.id == action.id)
            .with_for_update()
        )
        if row is None or row.status != "RUNNING" or (row.payload or {}).get(_CLAIM_ID) != action.claim_id:
            return "STALE_CLAIM"
        payload = dict(row.payload or {})
        payload[_LAST_ERROR] = f"{type(error).__name__}: {error}"[:500]
        _clear_claim_metadata(payload)
        if action.attempt >= max_attempts:
            row.status = "FAILED"
            row.payload = payload
            return "FAILED"
        delay = min(300.0, base_delay_seconds * (2 ** (action.attempt - 1)))
        row.due_at = now + timedelta(seconds=delay)
        row.status = "PENDING"
        row.payload = payload
        return "RETRY"

    return run_transaction(operation)


def active_simulated_scenario_ids() -> list[UUID]:
    def operation(session: Session) -> list[UUID]:
        return list(
            session.scalars(
                select(Scenario.id)
                .where(Scenario.mode == "SIMULATED", Scenario.active.is_(True))
                .order_by(Scenario.id)
            )
        )

    return run_transaction(operation)


def _clear_claim_metadata(payload: dict[str, Any]) -> None:
    payload.pop(_CLAIM_ID, None)
    payload.pop(_CLAIM_UNTIL, None)


def _clear_worker_metadata(payload: dict[str, Any]) -> None:
    _clear_claim_metadata(payload)
    payload.pop(_ATTEMPT, None)
    payload.pop(_LAST_ERROR, None)
