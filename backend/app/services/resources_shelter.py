from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, TypeVar
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.enums import AggregateType, EventType, ItemClass, ITEM_CLASS_UNIT, ReasonCode
from app.core.clock import RealClock, ScenarioClock
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.core.idempotency import execute_idempotently
from app.domain.inventory import DemandLedger
from app.models import Demand, Event, Scenario, Shelter, User
from app.repositories.demands import demand_ledger, lock_demand, save_demand_ledger


T = TypeVar("T")


class ShelterResourceError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str = "STATE_CONFLICT") -> None:
        self.status_code = status_code
        self.message = message
        self.code = code
        super().__init__(message)


def _scenario_now(session: Session, scenario_id: UUID | None) -> datetime:
    return RealClock().now() if scenario_id is None else ScenarioClock(session, scenario_id).now()


def _require_aware(value: datetime | None, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ShelterResourceError(422, f"{field} must include a UTC offset")


def _actor(session: Session, actor_id: UUID) -> User:
    actor = session.get(User, actor_id)
    if actor is None or not actor.active:
        raise ShelterResourceError(401, "Actor is inactive or unknown", code="UNAUTHENTICATED")
    if actor.role != "SHELTER":
        raise ShelterResourceError(403, "Actor role is not permitted for this operation", code="FORBIDDEN_ROLE")
    return actor


def _shelter_for_actor(session: Session, actor: User) -> Shelter:
    shelter = session.scalar(
        select(Shelter).where(
            Shelter.user_id == actor.id,
            Shelter.scenario_id.is_not_distinct_from(actor.scenario_id),
        )
    )
    if shelter is None:
        raise ShelterResourceError(404, "Shelter not found")
    return shelter


def _owned_shelter(session: Session, actor_id: UUID, shelter_id: UUID, order: LockOrder) -> tuple[User, Shelter]:
    actor = _actor(session, actor_id)
    # Resolve ownership before locking so a foreign resource is indistinguishable from a missing one.
    owned_id = session.scalar(
        select(Shelter.id).where(
            Shelter.id == shelter_id,
            Shelter.user_id == actor.id,
            Shelter.scenario_id.is_not_distinct_from(actor.scenario_id),
        )
    )
    if owned_id is None:
        raise ShelterResourceError(404, "Shelter not found")
    shelter = lock_rows(session, Shelter, LockRank.SHELTER, [shelter_id], order)
    if not shelter:
        raise ShelterResourceError(404, "Shelter not found")
    # Recheck after lock to prevent authorization succeeding against a changed owner/scenario.
    row = shelter[0]
    if row.user_id != actor.id or row.scenario_id != actor.scenario_id:
        raise ShelterResourceError(404, "Shelter not found")
    return actor, row


def _event(
    session: Session,
    *,
    order: LockOrder,
    actor: User,
    scenario_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    at: datetime,
    before: dict[str, object] | None,
    after: dict[str, object],
    reason_code: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    order.acquire(LockRank.EVENT_AND_SCHEDULE, [aggregate_id])
    payload: dict[str, object] = {"before": before, "after": after}
    if extra:
        payload.update(extra)
    session.add(
        Event(
            scenario_id=scenario_id,
            actor_id=actor.id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            reason_code=reason_code,
            payload=jsonable_encoder(payload),
            occurred_at=at,
        )
    )


def _mutate(
    actor_id: UUID,
    *,
    endpoint: str,
    idempotency_key: str,
    request_payload: object,
    action: Callable[[Session, User, LockOrder], T],
    serialize: Callable[[T], dict],
    deserialize: Callable[[dict], T],
    response_status: int = 200,
) -> T:
    def transaction(session: Session) -> T:
        actor = _actor(session, actor_id)
        order = LockOrder()

        def guarded_action() -> T:
            if actor.scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [actor.scenario_id], order)
                if not scenarios or not scenarios[0].active:
                    raise ShelterResourceError(404, "Active scenario not found")
            return action(session, actor, order)

        result, _ = execute_idempotently(
            session,
            lock_order=order,
            scenario_id=actor.scenario_id,
            actor_id=actor.id,
            endpoint=endpoint,
            key=idempotency_key,
            request_payload=request_payload,
            action=guarded_action,
            serialize=serialize,
            deserialize=deserialize,
            response_status=response_status,
        )
        return result

    try:
        return run_transaction(transaction)
    except ServiceError as exc:
        raise ShelterResourceError(exc.status_code, exc.message, code=exc.code.value) from exc
    except ValueError as exc:
        raise ShelterResourceError(422, str(exc), code="INVALID_REQUEST") from exc
    except IntegrityError as exc:
        raise ShelterResourceError(409, "The requested shelter resource conflicts with existing data") from exc


def _demand_dict(demand: Demand) -> dict[str, object]:
    ledger = demand_ledger(demand)
    return {
        "demand_id": demand.id,
        "shelter_id": demand.shelter_id,
        "item_class": demand.item_class,
        "unit": demand.unit,
        "required_qty": ledger.required,
        "received_qty": ledger.received,
        "reserved_qty": ledger.reserved,
        "transit_qty": ledger.transit,
        "uncommitted_need": ledger.uncommitted_need,
        "intake_open": ledger.intake_open,
        "version": demand.version,
        "service_window_start": demand.service_window_start,
        "service_window_end": demand.service_window_end,
    }


def _replace_demand_ledger(demand: Demand, ledger: DemandLedger) -> None:
    save_demand_ledger(demand, ledger)


def list_my_demands(actor_id: UUID) -> tuple[dict[str, object], ...]:
    def operation(session: Session) -> tuple[dict[str, object], ...]:
        actor = _actor(session, actor_id)
        shelter = _shelter_for_actor(session, actor)
        rows = session.scalars(
            select(Demand)
            .where(Demand.shelter_id == shelter.id, Demand.scenario_id.is_not_distinct_from(actor.scenario_id))
            .order_by(Demand.service_window_start, Demand.id)
        )
        return tuple(_demand_dict(row) for row in rows)

    return run_transaction(operation)


def create_demand(
    actor_id: UUID,
    *,
    item_class: ItemClass,
    required_qty: int,
    service_window_start: datetime | None,
    service_window_end: datetime | None,
    idempotency_key: str,
) -> dict[str, object]:
    _require_aware(service_window_start, "service_window_start")
    _require_aware(service_window_end, "service_window_end")
    if required_qty < 0:
        raise ShelterResourceError(422, "required_qty must be nonnegative", code="INVALID_QUANTITY")
    if (service_window_start is None) != (service_window_end is None):
        raise ShelterResourceError(422, "Both service window bounds must be supplied together")
    if service_window_start and service_window_end and service_window_start >= service_window_end:
        raise ShelterResourceError(422, "service_window_end must be later than its start")

    def action(session: Session, actor: User, order: LockOrder) -> dict[str, object]:
        _, shelter = _owned_shelter(session, actor.id, _shelter_for_actor(session, actor).id, order)
        now = _scenario_now(session, actor.scenario_id)
        demand = Demand(
            scenario_id=actor.scenario_id,
            shelter_id=shelter.id,
            item_class=item_class.value,
            unit=ITEM_CLASS_UNIT[item_class].value,
            required_qty=required_qty,
            received_qty=0,
            reserved_qty=0,
            transit_qty=0,
            intake_open=shelter.intake_open,
            service_window_start=service_window_start,
            service_window_end=service_window_end,
            version=0,
        )
        session.add(demand)
        session.flush()
        after = _demand_dict(demand)
        _event(
            session, order=order, actor=actor, scenario_id=actor.scenario_id,
            aggregate_type=AggregateType.DEMAND.value, aggregate_id=demand.id,
            event_type=EventType.DEMAND_CREATED.value, at=now, before=None, after=after,
        )
        return after

    return _mutate(
        actor_id,
        endpoint="POST /demands",
        idempotency_key=idempotency_key,
        request_payload={
            "item_class": item_class.value,
            "required_qty": required_qty,
            "service_window_start": service_window_start,
            "service_window_end": service_window_end,
        },
        action=action,
        serialize=jsonable_encoder,
        deserialize=lambda value: value,
        response_status=201,
    )


def update_demand(
    actor_id: UUID,
    demand_id: UUID,
    *,
    required_qty: int | None,
    intake_open: bool | None,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    if required_qty is not None and required_qty < 0:
        raise ShelterResourceError(422, "required_qty must be nonnegative", code="INVALID_QUANTITY")
    if required_qty is None and intake_open is None:
        raise ShelterResourceError(422, "At least one demand field must be updated")

    def action(session: Session, actor: User, order: LockOrder) -> dict[str, object]:
        _, shelter = _owned_shelter(session, actor.id, _shelter_for_actor(session, actor).id, order)
        candidates = session.scalars(
            select(Demand.id).where(
                Demand.id == demand_id,
                Demand.shelter_id == shelter.id,
                Demand.scenario_id.is_not_distinct_from(actor.scenario_id),
            )
        ).first()
        if candidates is None:
            raise ShelterResourceError(404, "Demand not found")
        demand = lock_demand(session, demand_id, order)
        if demand.shelter_id != shelter.id or demand.scenario_id != actor.scenario_id:
            raise ShelterResourceError(404, "Demand not found")
        if demand.version != expected_version:
            raise ShelterResourceError(409, "Demand version is stale", code="PLAN_STALE")
        if intake_open is True and not shelter.intake_open:
            raise ShelterResourceError(409, "Shelter intake is closed")
        before = _demand_dict(demand)
        ledger = demand_ledger(demand)
        if required_qty is not None:
            # Preserve existing reservations/transit even when the new requirement is lower.
            demand.required_qty = required_qty
        if intake_open is not None:
            ledger = DemandLedger(
                required=ledger.required,
                received=ledger.received,
                reserved=ledger.reserved,
                transit=ledger.transit,
                intake_open=intake_open,
            )
        _replace_demand_ledger(demand, ledger)
        now = _scenario_now(session, actor.scenario_id)
        after = _demand_dict(demand)
        _event(
            session, order=order, actor=actor, scenario_id=actor.scenario_id,
            aggregate_type=AggregateType.DEMAND.value, aggregate_id=demand.id,
            event_type=EventType.DEMAND_UPDATED.value, at=now, before=before, after=after,
        )
        return after

    return _mutate(
        actor_id,
        endpoint=f"PATCH /demands/{demand_id}",
        idempotency_key=idempotency_key,
        request_payload={"required_qty": required_qty, "intake_open": intake_open, "expected_version": expected_version},
        action=action,
        serialize=jsonable_encoder,
        deserialize=lambda value: value,
    )


def record_walkin(
    actor_id: UUID,
    shelter_id: UUID,
    demand_id: UUID,
    *,
    qty: int,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    if qty <= 0:
        raise ShelterResourceError(422, "qty must be a positive integer", code="INVALID_QUANTITY")

    def action(session: Session, actor: User, order: LockOrder) -> dict[str, object]:
        _, shelter = _owned_shelter(session, actor.id, shelter_id, order)
        demand = lock_demand(session, demand_id, order)
        if demand.shelter_id != shelter.id or demand.scenario_id != actor.scenario_id:
            raise ShelterResourceError(404, "Demand not found")
        if demand.version != expected_version:
            raise ShelterResourceError(409, "Demand version is stale", code="PLAN_STALE")
        before = _demand_dict(demand)
        ledger = demand_ledger(demand).record_walk_in(qty)
        _replace_demand_ledger(demand, ledger)
        now = _scenario_now(session, actor.scenario_id)
        after = _demand_dict(demand)
        overcommitment = max(0, ledger.received + ledger.reserved + ledger.transit - ledger.required)
        _event(
            session, order=order, actor=actor, scenario_id=actor.scenario_id,
            aggregate_type=AggregateType.DEMAND.value, aggregate_id=demand.id,
            event_type=EventType.SHELTER_WALKIN_RECEIVED.value, at=now,
            before=before, after=after,
            reason_code=ReasonCode.DEMAND_OVERCOMMITTED.value if overcommitment else None,
            extra={"qty": qty, "overcommitment_qty": overcommitment},
        )
        return after

    return _mutate(
        actor_id,
        endpoint=f"POST /shelters/{shelter_id}/events:WALKIN_RECEIVED",
        idempotency_key=idempotency_key,
        request_payload={"demand_id": demand_id, "qty": qty, "expected_version": expected_version},
        action=action,
        serialize=jsonable_encoder,
        deserialize=lambda value: value,
    )


def set_intake(
    actor_id: UUID,
    shelter_id: UUID,
    *,
    open_for_intake: bool,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    def action(session: Session, actor: User, order: LockOrder) -> dict[str, object]:
        _, shelter = _owned_shelter(session, actor.id, shelter_id, order)
        if shelter.version != expected_version:
            raise ShelterResourceError(409, "Shelter version is stale", code="PLAN_STALE")
        before = {
            "intake_open": shelter.intake_open,
            "version": shelter.version,
        }
        demand_ids = list(session.scalars(
            select(Demand.id)
            .where(Demand.shelter_id == shelter.id, Demand.scenario_id.is_not_distinct_from(actor.scenario_id))
            .order_by(Demand.id)
        ))
        demands = []
        if demand_ids:
            demands = lock_rows(session, Demand, LockRank.DEMAND, demand_ids, order)
        now = _scenario_now(session, actor.scenario_id)
        shelter.intake_open = open_for_intake
        shelter.version += 1
        for demand in demands:
            ledger = demand_ledger(demand)
            _replace_demand_ledger(
                demand,
                DemandLedger(
                    required=ledger.required,
                    received=ledger.received,
                    reserved=ledger.reserved,
                    transit=ledger.transit,
                    intake_open=open_for_intake,
                ),
            )
        after = {"intake_open": shelter.intake_open, "version": shelter.version}
        event_type = EventType.SHELTER_CLOSED.value if not open_for_intake else EventType.SHELTER_REOPENED.value
        _event(
            session, order=order, actor=actor, scenario_id=actor.scenario_id,
            aggregate_type=AggregateType.SHELTER.value, aggregate_id=shelter.id,
            event_type=event_type, at=now, before=before, after=after,
            extra={"affected_demand_ids": [str(item) for item in demand_ids]},
        )
        return {"shelter_id": shelter.id, **after}

    return _mutate(
        actor_id,
        endpoint=f"POST /shelters/{shelter_id}/events:{'REOPEN_INTAKE' if open_for_intake else 'CLOSE_INTAKE'}",
        idempotency_key=idempotency_key,
        request_payload={"open_for_intake": open_for_intake, "expected_version": expected_version},
        action=action,
        serialize=jsonable_encoder,
        deserialize=lambda value: value,
    )


def update_capabilities(
    actor_id: UUID,
    shelter_id: UUID,
    *,
    capabilities: dict[str, bool],
    verified_at: datetime,
    source: str,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    _require_aware(verified_at, "verified_at")
    # Reject objectively future-dated assertions before opening a transaction;
    # scenario time is checked again below while holding the scenario lock.
    if verified_at > RealClock().now():
        raise ShelterResourceError(422, "verified_at cannot be in the future")
    if not capabilities or any(not key.strip() for key in capabilities):
        raise ShelterResourceError(422, "At least one named capability is required")
    if not source.strip() or len(source) > 80:
        raise ShelterResourceError(422, "source must contain 1 to 80 characters")

    def action(session: Session, actor: User, order: LockOrder) -> dict[str, object]:
        _, shelter = _owned_shelter(session, actor.id, shelter_id, order)
        now = _scenario_now(session, actor.scenario_id)
        if verified_at > now:
            raise ShelterResourceError(422, "verified_at cannot be in the future")
        if shelter.version != expected_version:
            raise ShelterResourceError(409, "Shelter version is stale", code="PLAN_STALE")
        before = {
            "capabilities": dict(shelter.capabilities),
            "verified_at": shelter.capability_verified_at,
            "source": shelter.capability_source,
            "version": shelter.version,
        }
        shelter.capabilities = dict(capabilities)
        shelter.capability_verified_at = verified_at.astimezone(timezone.utc)
        shelter.capability_source = source.strip()
        shelter.version += 1
        after = {
            "capabilities": dict(shelter.capabilities),
            "verified_at": shelter.capability_verified_at,
            "source": shelter.capability_source,
            "version": shelter.version,
        }
        _event(
            session, order=order, actor=actor, scenario_id=actor.scenario_id,
            aggregate_type=AggregateType.SHELTER.value, aggregate_id=shelter.id,
            event_type=EventType.CAPABILITY_CHANGED.value, at=now,
            before=before, after=after,
        )
        return {"shelter_id": shelter.id, **after}

    return _mutate(
        actor_id,
        endpoint=f"POST /shelters/{shelter_id}/events:CAPABILITY_CHANGED",
        idempotency_key=idempotency_key,
        request_payload={
            "capabilities": capabilities,
            "verified_at": verified_at,
            "source": source,
            "expected_version": expected_version,
        },
        action=action,
        serialize=jsonable_encoder,
        deserialize=lambda value: value,
    )
