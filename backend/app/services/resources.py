from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.enums import AggregateType, ApiErrorCode, EventType
from app.contracts.schemas import BatchBalance, CreateBatchRequest
from app.core.clock import RealClock
from app.core.db import LockOrder, LockRank, lock_rows, run_transaction
from app.core.errors import ServiceError
from app.core.idempotency import execute_idempotently
from app.models import Batch, BatchInventory, Donor, Event, Scenario, User


def _batch_snapshot(batch: Batch, inventory: BatchInventory) -> BatchBalance:
    return BatchBalance(
        batch_id=batch.id,
        item_class=batch.item_class,
        unit=batch.unit,
        total_qty=batch.total_qty,
        available_qty=inventory.available,
        reserved_qty=inventory.reserved,
        carried_qty=inventory.carried,
        delivered_qty=inventory.delivered,
        disposed_qty=inventory.disposed,
        safe_by=batch.safe_by,
        deliver_by=batch.deliver_by,
        version=batch.version,
    )


def create_batch(
    request: CreateBatchRequest,
    *,
    actor_id: UUID,
    idempotency_key: str,
) -> BatchBalance:
    safe_by = request.declared_safe_by
    deliver_by = safe_by - timedelta(seconds=request.serving_buffer_seconds)
    if request.ready_at >= deliver_by:
        raise ServiceError(
            code=ApiErrorCode.INVALID_QUANTITY,
            message="ready_at must precede deliver_by after applying the serving buffer.",
            status_code=422,
        )

    def operation(session: Session) -> BatchBalance:
        lock_order = LockOrder()

        def create() -> BatchBalance:
            if request.scenario_id is not None:
                scenarios = lock_rows(session, Scenario, LockRank.SCENARIO, [request.scenario_id], lock_order)
                if not scenarios or not scenarios[0].active:
                    raise ServiceError(ApiErrorCode.NOT_FOUND, "Active scenario was not found.", 404)
            actors = lock_rows(session, User, LockRank.ACTOR, [actor_id], lock_order)
            if not actors or not actors[0].active or actors[0].scenario_id != request.scenario_id:
                raise ServiceError(ApiErrorCode.FORBIDDEN_ROLE, "Actor is not active in the requested scenario scope.", 403)
            donors = lock_rows(session, Donor, LockRank.DONOR, [request.donor_id], lock_order)
            if not donors or donors[0].user_id != actor_id:
                raise ServiceError(ApiErrorCode.FORBIDDEN_ROLE, "Donor does not belong to the authenticated actor.", 403)

            batch = Batch(
                id=uuid4(),
                scenario_id=request.scenario_id,
                donor_id=request.donor_id,
                item_class=str(request.item_class),
                unit=str(request.unit),
                total_qty=request.qty,
                ready_at=request.ready_at,
                safe_by=safe_by,
                deliver_by=deliver_by,
                safety_evidence={},
                status="AVAILABLE",
                version=0,
            )
            lock_order.acquire(LockRank.BATCH, [batch.id])
            inventory = BatchInventory(
                batch_id=batch.id,
                available=request.qty,
                reserved=0,
                carried=0,
                delivered=0,
                disposed=0,
                version=0,
            )
            lock_order.acquire(LockRank.BATCH_INVENTORY, [batch.id])
            session.add_all([batch, inventory])
            lock_order.acquire(LockRank.EVENT_AND_SCHEDULE, [batch.id])
            session.add(
                Event(
                    scenario_id=request.scenario_id,
                    actor_id=actor_id,
                    aggregate_type=AggregateType.BATCH.value,
                    aggregate_id=batch.id,
                    event_type=EventType.BATCH_CREATED.value,
                    payload={
                        "qty": request.qty,
                        "item_class": str(request.item_class),
                        "unit": str(request.unit),
                        "ready_at": request.ready_at.isoformat(),
                        "safe_by": safe_by.isoformat(),
                        "deliver_by": deliver_by.isoformat(),
                    },
                    occurred_at=RealClock().now(),
                )
            )
            session.flush()
            return _batch_snapshot(batch, inventory)

        result, _ = execute_idempotently(
            session,
            lock_order=lock_order,
            scenario_id=request.scenario_id,
            actor_id=actor_id,
            endpoint="POST /api/v1/batches",
            key=idempotency_key,
            request_payload=request.model_dump(mode="json"),
            action=create,
            serialize=lambda value: value.model_dump(mode="json"),
            deserialize=BatchBalance.model_validate,
            response_status=201,
        )
        return result

    return run_transaction(operation)


def read_batch(session: Session, batch_id: UUID) -> tuple[Batch, BatchInventory] | None:
    row = session.execute(
        select(Batch, BatchInventory)
        .join(BatchInventory, BatchInventory.batch_id == Batch.id)
        .where(Batch.id == batch_id)
    ).one_or_none()
    return row


def list_batches(
    session: Session, *, actor_id: UUID, role: str, offset: int, limit: int, scenario_id: UUID | None
) -> list[tuple[Batch, BatchInventory]]:
    statement = (
        select(Batch, BatchInventory)
        .join(BatchInventory, BatchInventory.batch_id == Batch.id)
        .order_by(Batch.created_at.desc(), Batch.id)
        .offset(offset)
        .limit(limit)
    )
    if scenario_id is not None:
        statement = statement.where(Batch.scenario_id == scenario_id)
    if role == "DONOR":
        statement = statement.join(Donor, Donor.id == Batch.donor_id).where(Donor.user_id == actor_id)
    return list(session.execute(statement).all())
