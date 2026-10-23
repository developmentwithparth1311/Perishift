"""Authenticated scenario-clock advancement and demo-only chaos controls."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.contracts.enums import AggregateType, EventType, ScenarioMode, SimulatedRiderMode
from app.core.config import settings
from app.core.db import LockOrder, LockRank, SessionLocal, lock_rows, run_transaction
from app.core.idempotency import execute_idempotently
from app.demo.seed import _DEMO_LOCK_KEY, build_seed_plan, issue_demo_bearer_tokens, reset_demo_scenario
from app.models import Batch, BatchInventory, Demand, Event, Rider, Scenario, ScheduledAction, Shelter, User
from app.repositories.jobs import materialize_deadline_actions
from app.services.resources_shelter import (
    ShelterResourceError,
    record_walkin,
    set_intake,
    update_capabilities,
)
from app.workers.scheduler import FixedClock, run_due_once


class DemoScenarioError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str = "DEMO_ACTION_REJECTED") -> None:
        self.status_code = status_code
        self.message = message
        self.code = code
        super().__init__(message)


def connect_demo_session(actor_id: UUID) -> dict[str, object]:
    """Let a demo coordinator reconnect to the current run using its stable credential."""
    def operation(session: Session) -> dict[str, object]:
        actor = session.get(User, actor_id)
        if actor is None:
            raise DemoScenarioError(401, "Demo coordinator was not found")
        _authorized_actor_scope(session, actor_id, actor.scenario_id)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DEMO_LOCK_KEY})
        scenario = session.scalar(select(Scenario).where(
            Scenario.mode == ScenarioMode.SIMULATED.value,
            Scenario.active.is_(True),
        ))
        if scenario is None:
            result = reset_demo_scenario(session=session)
        else:
            result = build_seed_plan(scenario.id, _utc(scenario.current_time)).result
        batch = session.get(Batch, result.batch_id)
        inventory = session.get(BatchInventory, result.batch_id)
        rider = session.get(Rider, result.rider_id)
        if batch is None or inventory is None or rider is None:
            raise DemoScenarioError(409, "The current demo is missing seeded resources")
        for role, field in (("DONOR", "donor_actor_id"), ("RIDER", "rider_actor_id"), ("SHELTER", "shelter_actor_id"), ("COORDINATOR", "coordinator_actor_id")):
            role_actor = session.get(User, getattr(result, field))
            if role_actor is None or not role_actor.active or role_actor.role != role or role_actor.scenario_id != result.scenario_id:
                raise DemoScenarioError(409, "The current demo is missing an active role actor")
        from app.services.resources import _batch_snapshot

        result = replace(result, quantity=batch.total_qty, batch_balance=_batch_snapshot(batch, inventory).model_dump(mode="json"))
        tokens, expires_at = issue_demo_bearer_tokens(result)
        return {
            "scenario": result.to_dict(), "bearer_tokens": tokens,
            "token_expires_at": expires_at.isoformat() if expires_at is not None else None,
            "rider_version": rider.version,
        }

    response = run_transaction(operation)
    from app.services.custody import get_active_dispatch

    response["active_dispatch"] = get_active_dispatch(
        actor_id=UUID(response["scenario"]["coordinator_actor_id"]),
        rider_id=UUID(response["scenario"]["rider_id"]),
    )
    return response


def require_demo_control(actor_id: UUID, scenario_id: UUID) -> dict[str, object]:
    """Validate that actor is a coordinator/judge in this active demo scenario."""
    if not settings.demo_mode:
        raise DemoScenarioError(404, "Demo controls are disabled")

    def operation(session: Session) -> dict[str, object]:
        actor, scenario = _authorized_scenario(session, actor_id, scenario_id)
        return {
            "scenario_id": scenario.id,
            "current_time": _utc(scenario.current_time),
            "version": scenario.version,
            "actor_role": actor.role,
        }

    return run_transaction(operation)


def advance_scenario(
    actor_id: UUID,
    scenario_id: UUID,
    *,
    seconds: int,
    idempotency_key: str,
) -> dict[str, object]:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 86_400:
        raise DemoScenarioError(422, "seconds must be an integer from 1 to 86400", code="INVALID_QUANTITY")

    def operation(session: Session) -> dict[str, object]:
        actor, _scenario = _authorized_scenario(session, actor_id, scenario_id)
        order = LockOrder()

        def action() -> dict[str, object]:
            # Serialize same-key retries and other advances for this scenario;
            # this session holds no scenario row lock while C3 runs its TXs.
            key_a, key_b = _advisory_keys(scenario_id)
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key_a, :key_b)"),
                {"key_a": key_a, "key_b": key_b},
            )
            start = _read_scenario_time(scenario_id)
            target = start + timedelta(seconds=seconds)
            processed, due_times = _advance_due_work(
                scenario_id=scenario_id,
                start=start,
                target=target,
                set_clock=lambda at: _set_scenario_time(scenario_id, at),
                process_due=lambda at: run_due_once(
                    clock=FixedClock(at), scenario_id=scenario_id, limit=100
                ),
                materialize=lambda: materialize_deadline_actions(scenario_id=scenario_id),
                next_due=lambda until: _next_pending_due(scenario_id, until),
                due_work_remains=lambda at: _has_claimable_work(scenario_id, at),
                wait=time.sleep,
            )
            before_version, after_version = _set_scenario_time(scenario_id, target)
            _lock_scenario(session, scenario_id, order)
            _scenario_event_in_session(
                session,
                order,
                actor_id=actor.id,
                scenario_id=scenario_id,
                occurred_at=target,
                payload={
                    "before": {"current_time": start.isoformat(), "version": before_version},
                    "after": {"current_time": target.isoformat(), "version": after_version},
                    "advanced_seconds": seconds,
                    "processed_scheduled_actions": processed,
                    "processed_due_times": [value.isoformat() for value in due_times],
                },
            )
            return {
                "scenario_id": scenario_id,
                "previous_time": start,
                "current_time": target,
                "advanced_seconds": seconds,
                "processed_scheduled_actions": processed,
                "processed_due_times": due_times,
            }

        result, _ = execute_idempotently(
            session,
            lock_order=order,
            scenario_id=scenario_id,
            actor_id=actor.id,
            endpoint=f"POST /demo/scenarios/{scenario_id}/advance",
            key=idempotency_key,
            request_payload={"seconds": seconds},
            action=action,
            serialize=jsonable_encoder,
            deserialize=_deserialize_advance,
        )
        return result

    return run_transaction(operation)


def _advance_due_work(
    *,
    scenario_id: UUID,
    start: datetime,
    target: datetime,
    set_clock: Callable[[datetime], object],
    process_due: Callable[[datetime], int],
    materialize: Callable[[], object],
    next_due: Callable[[datetime], datetime | None],
    due_work_remains: Callable[[datetime], bool],
    wait: Callable[[float], object],
    max_due_times: int = 10_000,
    max_wait_attempts: int = 320,
) -> tuple[int, list[datetime]]:
    """Advance through every due timestamp in order, draining each timestamp.

    The injected seams keep the orchestration testable without claiming that a
    PostgreSQL scheduler transaction has run.
    """
    start, target = _utc(start), _utc(target)
    if target < start:
        raise ValueError("target scenario time cannot precede its current time")
    current = start
    due_times: list[datetime] = []
    processed = 0
    while True:
        materialize()
        next_timestamp = next_due(target)
        if next_timestamp is None:
            break
        step = max(current, _utc(next_timestamp))
        if step > target:
            break
        if len(due_times) >= max_due_times:
            raise DemoScenarioError(409, "Too many scheduled actions to advance safely")
        set_clock(step)
        due_times.append(step)
        attempts_without_progress = 0
        while True:
            claimed = process_due(step)
            processed += claimed
            if claimed:
                attempts_without_progress = 0
                materialize()
                continue
            if not due_work_remains(step):
                break
            attempts_without_progress += 1
            if attempts_without_progress >= max_wait_attempts:
                raise DemoScenarioError(
                    409,
                    "A due scheduled action is still being processed; retry the time advance",
                )
            # C3 claims are leased for 30 seconds. Re-poll and call the C3
            # processor again so an expired claim can be recovered safely.
            wait(0.1)
        current = step
    set_clock(target)
    return processed, due_times


def silence_rider(
    actor_id: UUID,
    scenario_id: UUID,
    rider_id: UUID,
    *,
    idempotency_key: str,
) -> dict[str, object]:
    def operation(session: Session) -> dict[str, object]:
        actor, _scenario = _authorized_scenario(session, actor_id, scenario_id)
        order = LockOrder()

        def action() -> dict[str, object]:
            _lock_scenario(session, scenario_id, order)
            riders = lock_rows(session, Rider, LockRank.RIDER, [rider_id], order)
            if not riders or riders[0].scenario_id != scenario_id:
                raise DemoScenarioError(404, "Simulated rider not found")
            rider = riders[0]
            if rider.simulated_mode is None:
                raise DemoScenarioError(409, "Rider is not a simulated actor")
            before = {"mode": rider.simulated_mode, "generation": rider.generation, "version": rider.version}
            rider.simulated_mode = SimulatedRiderMode.SILENT.value
            rider.generation += 1
            rider.version += 1
            after = {"mode": rider.simulated_mode, "generation": rider.generation, "version": rider.version}
            _scenario_event_in_session(
                session, order, actor_id=actor.id, scenario_id=scenario_id,
                occurred_at=_utc(session.get(Scenario, scenario_id).current_time),
                payload={"action": "RIDER_SILENT", "rider_id": str(rider.id), "before": before, "after": after},
                event_type=EventType.DEMO_ACTION_APPLIED.value,
            )
            return {"rider_id": rider.id, **after}

        result, _ = execute_idempotently(
            session,
            lock_order=order,
            scenario_id=scenario_id,
            actor_id=actor.id,
            endpoint=f"POST /demo/scenarios/{scenario_id}/riders/{rider_id}/silent",
            key=idempotency_key,
            request_payload={"rider_id": rider_id},
            action=action,
            serialize=lambda value: {key: str(item) if isinstance(item, UUID) else item for key, item in value.items()},
            deserialize=lambda value: {**value, "rider_id": UUID(value["rider_id"])},
        )
        return result

    return run_transaction(operation)


def close_shelter(
    actor_id: UUID,
    scenario_id: UUID,
    shelter_id: UUID,
    *,
    expected_shelter_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    shelter_actor_id, _version, _time = _resolve_shelter_action(
        actor_id, scenario_id, shelter_id
    )
    try:
        return set_intake(
            shelter_actor_id,
            shelter_id,
            open_for_intake=False,
            expected_version=expected_shelter_version,
            idempotency_key=idempotency_key,
        )
    except ShelterResourceError as exc:
        raise DemoScenarioError(exc.status_code, exc.message, code=exc.code) from exc


def add_shelter_walkin(
    actor_id: UUID,
    scenario_id: UUID,
    shelter_id: UUID,
    demand_id: UUID,
    *,
    qty: int,
    expected_demand_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    shelter_actor_id, _version, _time = _resolve_shelter_action(
        actor_id, scenario_id, shelter_id
    )
    try:
        return record_walkin(
            shelter_actor_id,
            shelter_id,
            demand_id,
            qty=qty,
            expected_version=expected_demand_version,
            idempotency_key=idempotency_key,
        )
    except ShelterResourceError as exc:
        raise DemoScenarioError(exc.status_code, exc.message, code=exc.code) from exc


def remove_shelter_capability(
    actor_id: UUID,
    scenario_id: UUID,
    shelter_id: UUID,
    capability: str,
    *,
    expected_shelter_version: int,
    verified_at: datetime,
    idempotency_key: str,
) -> dict[str, object]:
    shelter_actor_id, _version, _scenario_time = _resolve_shelter_action(
        actor_id, scenario_id, shelter_id
    )
    allowed = {
        "clean_water", "cooking_fuel", "cooking_facility", "refrigeration",
        "trained_receiver", "authorized_recipient",
    }
    if capability not in allowed:
        raise DemoScenarioError(422, "Unknown safety-critical shelter capability", code="INVALID_QUANTITY")
    with SessionLocal() as session:
        shelter = session.get(Shelter, shelter_id)
        if shelter is None or shelter.scenario_id != scenario_id:
            raise DemoScenarioError(404, "Shelter not found")
        capabilities = dict(shelter.capabilities)
    capabilities[capability] = False
    try:
        return update_capabilities(
            shelter_actor_id,
            shelter_id,
            capabilities=capabilities,
            verified_at=verified_at,
            source="DEMO_CHAOS",
            expected_version=expected_shelter_version,
            idempotency_key=idempotency_key,
        )
    except ShelterResourceError as exc:
        raise DemoScenarioError(exc.status_code, exc.message, code=exc.code) from exc


def reset_scenario(
    actor_id: UUID, scenario_id: UUID, *, idempotency_key: str
) -> dict[str, object]:
    def operation(session: Session) -> dict[str, object]:
        # Permit a cached reset replay after the original scenario was retired;
        # first execution is separately required to target an active scenario.
        actor, _scenario = _authorized_actor_scope(session, actor_id, scenario_id)
        order = LockOrder()

        def action() -> dict[str, object]:
            _authorized_scenario(session, actor_id, scenario_id)
            result = reset_demo_scenario(session=session, replacing_scenario_id=scenario_id)
            tokens, expires_at = issue_demo_bearer_tokens(result)
            return {
                "scenario": result.to_dict(),
                "bearer_tokens": tokens,
                "token_expires_at": expires_at.isoformat() if expires_at is not None else None,
            }

        result, _ = execute_idempotently(
            session,
            lock_order=order,
            scenario_id=scenario_id,
            actor_id=actor.id,
            endpoint=f"POST /demo/scenarios/{scenario_id}/reset",
            key=idempotency_key,
            request_payload={},
            action=action,
            serialize=lambda value: value,
            deserialize=lambda value: value,
            response_status=201,
        )
        return result

    return run_transaction(operation)


def _resolve_shelter_action(
    actor_id: UUID, scenario_id: UUID, shelter_id: UUID
) -> tuple[UUID, int, datetime]:
    def operation(session: Session) -> tuple[UUID, int, datetime]:
        _authorized_scenario(session, actor_id, scenario_id)
        shelter = session.scalar(
            select(Shelter).where(
                Shelter.id == shelter_id,
                Shelter.scenario_id == scenario_id,
                Shelter.user_id.is_not(None),
            )
        )
        if shelter is None:
            raise DemoScenarioError(404, "Shelter not found")
        demand_actor = session.get(User, shelter.user_id)
        scenario = session.get(Scenario, scenario_id)
        if demand_actor is None or demand_actor.role != "SHELTER" or scenario is None:
            raise DemoScenarioError(404, "Shelter actor not found")
        return demand_actor.id, shelter.version, _utc(scenario.current_time)

    return run_transaction(operation)


def _authorized_scenario(session: Session, actor_id: UUID, scenario_id: UUID) -> tuple[User, Scenario]:
    actor, scenario = _authorized_actor_scope(session, actor_id, scenario_id)
    if not scenario.active:
        raise DemoScenarioError(404, "Active simulated scenario not found")
    return actor, scenario


def _authorized_actor_scope(
    session: Session, actor_id: UUID, scenario_id: UUID
) -> tuple[User, Scenario]:
    if not settings.demo_mode:
        raise DemoScenarioError(404, "Demo controls are disabled")
    actor = session.get(User, actor_id)
    if actor is None or not actor.active:
        raise DemoScenarioError(401, "Actor is inactive or unknown", code="UNAUTHENTICATED")
    if actor.role not in {"COORDINATOR", "JUDGE"}:
        raise DemoScenarioError(403, "Coordinator or judge role is required", code="FORBIDDEN_ROLE")
    if actor.scenario_id != scenario_id:
        raise DemoScenarioError(404, "Scenario not found")
    scenario = session.get(Scenario, scenario_id)
    if scenario is None or scenario.mode != ScenarioMode.SIMULATED.value:
        raise DemoScenarioError(404, "Simulated scenario not found")
    return actor, scenario


def _lock_scenario(session: Session, scenario_id: UUID, order: LockOrder) -> Scenario:
    scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [scenario_id], order)
    if not scenarios or not scenarios[0].active:
        raise DemoScenarioError(404, "Active simulated scenario not found")
    return scenarios[0]


def _read_scenario_time(scenario_id: UUID) -> datetime:
    with SessionLocal() as session:
        scenario = session.get(Scenario, scenario_id)
        if scenario is None or not scenario.active:
            raise DemoScenarioError(404, "Active simulated scenario not found")
        return _utc(scenario.current_time)


def _set_scenario_time(scenario_id: UUID, at: datetime) -> tuple[int, int]:
    at = _utc(at)

    def operation(session: Session) -> tuple[int, int]:
        order = LockOrder()
        scenario = _lock_scenario(session, scenario_id, order)
        previous = _utc(scenario.current_time)
        if at < previous:
            raise DemoScenarioError(409, "Scenario time cannot move backwards")
        before_version = scenario.version
        if at > previous:
            scenario.current_time = at
            scenario.version += 1
        return before_version, scenario.version

    return run_transaction(operation)


def _next_pending_due(scenario_id: UUID, until: datetime) -> datetime | None:
    with SessionLocal() as session:
        return session.scalar(
            select(ScheduledAction.due_at)
            .where(
                ScheduledAction.scenario_id == scenario_id,
                ScheduledAction.status == "PENDING",
                ScheduledAction.due_at <= until,
            )
            .order_by(ScheduledAction.due_at, ScheduledAction.id)
            .limit(1)
        )


def _has_claimable_work(scenario_id: UUID, at: datetime) -> bool:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count())
            .select_from(ScheduledAction)
            .where(
                ScheduledAction.scenario_id == scenario_id,
                ScheduledAction.status.in_(("PENDING", "RUNNING")),
                ScheduledAction.due_at <= at,
            )
        ) > 0


def _scenario_event_in_session(
    session: Session,
    order: LockOrder,
    *,
    actor_id: UUID,
    scenario_id: UUID,
    occurred_at: datetime,
    payload: dict[str, object],
    event_type: str = EventType.SCENARIO_ADVANCED.value,
) -> None:
    order.acquire(LockRank.EVENT_AND_SCHEDULE, [scenario_id])
    session.add(
        Event(
            scenario_id=scenario_id,
            actor_id=actor_id,
            aggregate_type=AggregateType.SCENARIO.value,
            aggregate_id=scenario_id,
            event_type=event_type,
            reason_code=None,
            payload=payload,
            occurred_at=_utc(occurred_at),
        )
    )


def _advisory_keys(scenario_id: UUID) -> tuple[int, int]:
    raw = scenario_id.int.to_bytes(16, "big")
    return int.from_bytes(raw[:4], "big", signed=True), int.from_bytes(raw[4:8], "big", signed=True)


def _deserialize_advance(value: dict[str, object]) -> dict[str, object]:
    def parse_datetime(raw: object) -> datetime:
        if not isinstance(raw, str):
            raise ValueError("stored advance timestamp is invalid")
        return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))

    return {
        "scenario_id": UUID(str(value["scenario_id"])),
        "previous_time": parse_datetime(value["previous_time"]),
        "current_time": parse_datetime(value["current_time"]),
        "advanced_seconds": int(value["advanced_seconds"]),
        "processed_scheduled_actions": int(value["processed_scheduled_actions"]),
        "processed_due_times": [parse_datetime(item) for item in value["processed_due_times"]],
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scenario timestamps must include a UTC offset")
    return value.astimezone(timezone.utc)
