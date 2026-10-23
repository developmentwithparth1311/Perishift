"""Transactional, isolated simulated-scenario bootstrap for the MVP."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4, uuid5

import jwt
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.contracts.enums import (
    ApiErrorCode,
    AggregateType,
    EventType,
    ItemClass,
    ITEM_CLASS_UNIT,
    QuantityUnit,
    ScenarioMode,
    SimulatedRiderMode,
)
from app.core.clock import RealClock, ScenarioClock
from app.core.config import settings
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.models import (
    Batch,
    BatchInventory,
    Demand,
    Donor,
    Event,
    Rider,
    Scenario,
    Shelter,
    User,
)


_DEMO_LOCK_KEY = 8_104_223_761
_ITEM_CLASS = ItemClass.HOT_COOKED_MEAL
_STARTER_QTY = 20
_DONOR_POINT = (19.0705, 72.8740)
_SHELTER_POINT = (19.0760, 72.8777)
_RIDER_POINT = (19.0780, 72.8790)


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    """Safe discovery result; contains public resource/actor IDs, never credentials."""

    scenario_id: UUID
    scenario_time: datetime
    donor_actor_id: UUID
    donor_id: UUID
    rider_actor_id: UUID
    rider_id: UUID
    shelter_actor_id: UUID
    shelter_id: UUID
    coordinator_actor_id: UUID
    judge_actor_id: UUID
    demand_id: UUID
    batch_id: UUID
    item_class: str
    unit: str
    quantity: int
    batch_balance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value
                for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class DemoSeedPlan:
    users: tuple[User, ...]
    donor: Donor
    rider: Rider
    shelter: Shelter
    demand: Demand
    batch: Batch
    inventory: BatchInventory
    result: DemoSeedResult


def build_seed_plan(scenario_id: UUID, now: datetime) -> DemoSeedPlan:
    """Build deterministic records for one scenario identity without database I/O."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scenario time must include a UTC offset")
    now = now.astimezone(timezone.utc)

    def id_for(name: str) -> UUID:
        return uuid5(scenario_id, name)

    actor_roles = ("DONOR", "RIDER", "SHELTER", "COORDINATOR", "JUDGE")
    users = tuple(
        User(
            id=id_for(f"actor:{role.lower()}"),
            scenario_id=scenario_id,
            role=role,
            external_id=f"demo-{role.lower()}",
            display_name=f"Demo {role.title()}",
            active=True,
        )
        for role in actor_roles
    )
    by_role = {user.role: user for user in users}
    donor = Donor(
        id=id_for("donor"),
        user_id=by_role["DONOR"].id,
        verified=True,
        latitude=_DONOR_POINT[0],
        longitude=_DONOR_POINT[1],
    )
    shelter = Shelter(
        id=id_for("shelter"),
        scenario_id=scenario_id,
        user_id=by_role["SHELTER"].id,
        name="PeriShift Demo Shelter",
        latitude=_SHELTER_POINT[0],
        longitude=_SHELTER_POINT[1],
        intake_open=True,
        capabilities={
            "clean_water": True,
            "cooking_fuel": True,
            "cooking_facility": True,
            "refrigeration": True,
            "trained_receiver": True,
            "authorized_recipient": True,
        },
        capability_verified_at=now,
        capability_source="SIMULATED_SEED",
        version=0,
    )
    demand = Demand(
        id=id_for("demand:hot-cooked-meal"),
        scenario_id=scenario_id,
        shelter_id=shelter.id,
        item_class=_ITEM_CLASS.value,
        unit=ITEM_CLASS_UNIT[_ITEM_CLASS].value,
        required_qty=_STARTER_QTY,
        received_qty=0,
        reserved_qty=0,
        transit_qty=0,
        intake_open=True,
        service_window_start=now,
        service_window_end=now + timedelta(hours=6),
        version=0,
    )
    rider = Rider(
        id=id_for("rider"),
        scenario_id=scenario_id,
        user_id=by_role["RIDER"].id,
        capacity=40,
        available=True,
        latitude=_RIDER_POINT[0],
        longitude=_RIDER_POINT[1],
        last_heartbeat_at=now,
        last_progress_at=now,
        handling_capabilities={"food_safe_container": True},
        handling_verified_at=now,
        handling_source="SIMULATED_SEED",
        simulated_mode=SimulatedRiderMode.AUTO.value,
        heartbeat_period_seconds=60,
        accept_delay_seconds=5,
        pickup_delay_seconds=30,
        generation=0,
        version=0,
    )
    batch = Batch(
        id=id_for("batch:hot-cooked-meal"),
        scenario_id=scenario_id,
        donor_id=donor.id,
        item_class=_ITEM_CLASS.value,
        unit=QuantityUnit.MEAL.value,
        total_qty=_STARTER_QTY,
        ready_at=now,
        safe_by=now + timedelta(hours=2, minutes=20),
        deliver_by=now + timedelta(hours=2),
        safety_evidence={"seeded_demo": True, "safety_window_source": "SIMULATED_SEED"},
        status="AVAILABLE",
        version=0,
    )
    inventory = BatchInventory(
        batch_id=batch.id,
        available=_STARTER_QTY,
        reserved=0,
        carried=0,
        delivered=0,
        disposed=0,
        version=0,
    )
    # Use the same contract snapshot as the donor batch API, including the
    # five-bucket conservation check consumed by the MVP client.
    from app.services.resources import _batch_snapshot

    batch_balance = _batch_snapshot(batch, inventory).model_dump(mode="json")
    result = DemoSeedResult(
        scenario_id=scenario_id,
        scenario_time=now,
        donor_actor_id=by_role["DONOR"].id,
        donor_id=donor.id,
        rider_actor_id=by_role["RIDER"].id,
        rider_id=rider.id,
        shelter_actor_id=by_role["SHELTER"].id,
        shelter_id=shelter.id,
        coordinator_actor_id=by_role["COORDINATOR"].id,
        judge_actor_id=by_role["JUDGE"].id,
        demand_id=demand.id,
        batch_id=batch.id,
        item_class=_ITEM_CLASS.value,
        unit=ITEM_CLASS_UNIT[_ITEM_CLASS].value,
        quantity=_STARTER_QTY,
        batch_balance=batch_balance,
    )
    return DemoSeedPlan(users, donor, rider, shelter, demand, batch, inventory, result)


def _event(
    session: Session,
    *,
    scenario_id: UUID,
    actor_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: EventType,
    at: datetime,
    payload: dict[str, Any],
) -> None:
    session.add(
        Event(
            scenario_id=scenario_id,
            actor_id=actor_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type.value,
            payload=payload,
            occurred_at=at,
        )
    )


def reset_demo_scenario(*, session: Session | None = None, replacing_scenario_id: UUID | None = None) -> DemoSeedResult:
    """Create a fresh isolated simulation, retiring but never deleting its predecessor.

    All seed rows and audit records commit together. A PostgreSQL transaction-scoped
    advisory lock makes concurrent reset invocations serialize before replacing the
    unique active simulation.
    """

    def operation(session: Session) -> DemoSeedResult:
        # This lock applies only to explicit demo reset/bootstrap operations; normal
        # entity transactions remain row-scoped and concurrent.
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": _DEMO_LOCK_KEY})
        order = LockOrder()
        previous_ids = list(
            session.scalars(
                select(Scenario.id)
                .where(Scenario.mode == ScenarioMode.SIMULATED.value, Scenario.active.is_(True))
                .order_by(Scenario.id)
            )
        )
        if replacing_scenario_id is not None and replacing_scenario_id not in previous_ids:
            raise ServiceError(
                code=ApiErrorCode.STATE_CONFLICT,
                message="This scenario has already been reset. Load the replacement scenario.",
                status_code=409,
            )
        previous = lock_rows(session, Scenario, LockRank.SCENARIO, previous_ids, order)
        now = RealClock().now()
        prior_states: list[tuple[Scenario, dict[str, Any]]] = []
        for scenario in previous:
            before = {"active": scenario.active, "version": scenario.version}
            scenario.active = False
            scenario.version += 1
            prior_states.append((scenario, before))
        if prior_states:
            session.flush()

        scenario_id = uuid4()
        scenario = Scenario(
            id=scenario_id,
            mode=ScenarioMode.SIMULATED.value,
            current_time=now,
            active=True,
            generation=0,
            version=0,
        )
        session.add(scenario)
        session.flush()
        sim_now = ScenarioClock(session, scenario_id).now()
        plan = build_seed_plan(scenario_id, sim_now)

        order.acquire(LockRank.ACTOR, [user.id for user in plan.users])
        session.add_all(plan.users)
        session.flush()

        order.acquire(LockRank.DONOR, [plan.donor.id])
        session.add(plan.donor)
        session.flush()
        order.acquire(LockRank.BATCH, [plan.batch.id])
        session.add(plan.batch)
        session.flush()
        order.acquire(LockRank.BATCH_INVENTORY, [plan.inventory.batch_id])
        session.add(plan.inventory)
        session.flush()

        order.acquire(LockRank.SHELTER, [plan.shelter.id])
        session.add(plan.shelter)
        session.flush()
        order.acquire(LockRank.DEMAND, [plan.demand.id])
        session.add(plan.demand)
        order.acquire(LockRank.RIDER, [plan.rider.id])
        session.add(plan.rider)
        session.flush()

        user_id_by_role = {user.role: user.id for user in plan.users}
        event_aggregate_ids = [scenario_id, plan.batch.id, plan.demand.id, plan.shelter.id]
        event_aggregate_ids.extend(old.id for old, _ in prior_states)
        order.acquire(LockRank.EVENT_AND_SCHEDULE, event_aggregate_ids)
        for old, before in prior_states:
            _event(
                session,
                scenario_id=old.id,
                actor_id=None,
                aggregate_type=AggregateType.SCENARIO.value,
                aggregate_id=old.id,
                event_type=EventType.SCENARIO_RESET,
                at=sim_now,
                payload={"before": before, "after": {"active": False, "version": old.version}, "replacement_scenario_id": str(scenario_id)},
            )
        _event(
            session,
            scenario_id=scenario_id,
            actor_id=user_id_by_role["COORDINATOR"],
            aggregate_type=AggregateType.SCENARIO.value,
            aggregate_id=scenario_id,
            event_type=EventType.SCENARIO_RESET,
            at=sim_now,
            payload={
                "before": None,
                "after": {"mode": ScenarioMode.SIMULATED.value, "active": True, "current_time": sim_now.isoformat()},
                "previous_scenario_ids": [str(item.id) for item, _ in prior_states],
                "seeded_actor_ids": {role.lower(): str(user_id_by_role[role]) for role in user_id_by_role},
                "seeded_resource_ids": {
                    "donor_id": str(plan.donor.id),
                    "rider_id": str(plan.rider.id),
                    "shelter_id": str(plan.shelter.id),
                    "demand_id": str(plan.demand.id),
                    "batch_id": str(plan.batch.id),
                },
            },
        )
        _event(
            session,
            scenario_id=scenario_id,
            actor_id=user_id_by_role["DONOR"],
            aggregate_type=AggregateType.BATCH.value,
            aggregate_id=plan.batch.id,
            event_type=EventType.BATCH_CREATED,
            at=sim_now,
            payload={
                "qty": plan.batch.total_qty,
                "item_class": plan.batch.item_class,
                "unit": plan.batch.unit,
                "ready_at": plan.batch.ready_at.isoformat(),
                "safe_by": plan.batch.safe_by.isoformat(),
                "deliver_by": plan.batch.deliver_by.isoformat(),
            },
        )
        _event(
            session,
            scenario_id=scenario_id,
            actor_id=user_id_by_role["SHELTER"],
            aggregate_type=AggregateType.SHELTER.value,
            aggregate_id=plan.shelter.id,
            event_type=EventType.CAPABILITY_CHANGED,
            at=sim_now,
            payload={
                "before": None,
                "after": {
                    "capabilities": plan.shelter.capabilities,
                    "verified_at": sim_now.isoformat(),
                    "source": plan.shelter.capability_source,
                },
            },
        )
        _event(
            session,
            scenario_id=scenario_id,
            actor_id=user_id_by_role["SHELTER"],
            aggregate_type=AggregateType.DEMAND.value,
            aggregate_id=plan.demand.id,
            event_type=EventType.DEMAND_CREATED,
            at=sim_now,
            payload={
                "before": None,
                "after": {
                    "required_qty": plan.demand.required_qty,
                    "received_qty": plan.demand.received_qty,
                    "reserved_qty": plan.demand.reserved_qty,
                    "transit_qty": plan.demand.transit_qty,
                    "item_class": plan.demand.item_class,
                    "unit": plan.demand.unit,
                    "shelter_id": str(plan.shelter.id),
                },
            },
        )
        session.flush()
        return plan.result

    return operation(session) if session is not None else run_transaction(operation)


def issue_demo_bearer_tokens(
    result: DemoSeedResult,
    *,
    now: datetime | None = None,
    lifetime: timedelta | None = None,
) -> tuple[dict[str, str], datetime | None]:
    """Issue scenario-bound demo JWTs, optionally with a limited lifetime."""
    issued_at = now or RealClock().now()
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise ValueError("token issue time must include a UTC offset")
    if lifetime is None and not settings.demo_mode:
        raise ValueError("non-expiring demo tokens require DEMO_MODE=true")
    if lifetime is not None and (lifetime <= timedelta(0) or lifetime > timedelta(hours=1)):
        raise ValueError("demo token lifetime must be positive and at most one hour")
    issued_at = issued_at.astimezone(timezone.utc)
    expires_at = issued_at + lifetime if lifetime is not None else None
    actor_ids = {
        "donor": result.donor_actor_id,
        "rider": result.rider_actor_id,
        "shelter": result.shelter_actor_id,
        "coordinator": result.coordinator_actor_id,
    }
    tokens = {}
    for role, actor_id in actor_ids.items():
        claims: dict[str, object] = {"sub": str(actor_id), "iat": issued_at}
        if expires_at is None:
            claims.update({"token_use": "local_demo", "scenario_id": str(result.scenario_id)})
        else:
            claims["exp"] = expires_at
        tokens[role] = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    return tokens, expires_at


def main() -> None:
    if not settings.demo_mode:
        raise ValueError("demo seeding requires DEMO_MODE=true")
    result = reset_demo_scenario()
    tokens, token_exp = issue_demo_bearer_tokens(result)
    output = result.to_dict()
    output["bearer_tokens"] = tokens
    output["token_expires_at"] = token_exp.isoformat() if token_exp is not None else None
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
