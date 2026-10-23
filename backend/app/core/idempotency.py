import hashlib
import json
import uuid
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import LockOrder, LockRank
from app.core.errors import ServiceError
from app.contracts.enums import ApiErrorCode
from app.models import IdempotencyRecord

T = TypeVar("T")


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def execute_idempotently(
    session: Session,
    *,
    lock_order: LockOrder,
    scenario_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    endpoint: str,
    key: str,
    request_payload: object,
    action: Callable[[], T],
    serialize: Callable[[T], dict],
    deserialize: Callable[[dict], T],
    response_status: int = 200,
) -> tuple[T, bool]:
    """Claim a scope-specific key and persist the response in the same transaction."""
    if not key.strip() or len(key) > 180:
        raise ValueError("Idempotency-Key must contain 1 to 180 characters")
    request_hash = _canonical_hash(request_payload)
    identity = uuid.uuid4()
    lock_order.acquire(LockRank.IDEMPOTENCY, [f"{scenario_id or 'live'}:{actor_id}:{endpoint}:{key}"])
    insert_result = session.execute(
        insert(IdempotencyRecord)
        .values(
            id=identity,
            scenario_id=scenario_id,
            actor_id=actor_id,
            endpoint=endpoint,
            idempotency_key=key,
            request_hash=request_hash,
        )
        .on_conflict_do_nothing(
            index_elements=[
                IdempotencyRecord.scenario_id,
                IdempotencyRecord.actor_id,
                IdempotencyRecord.endpoint,
                IdempotencyRecord.idempotency_key,
            ]
        )
        .returning(IdempotencyRecord.id)
    )
    claimed_id = insert_result.scalar_one_or_none()
    if claimed_id is None:
        criteria = (
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.endpoint == endpoint,
            IdempotencyRecord.idempotency_key == key,
        )
        criteria += (
            IdempotencyRecord.scenario_id.is_(None)
            if scenario_id is None
            else IdempotencyRecord.scenario_id == scenario_id,
        )
        record = session.scalar(select(IdempotencyRecord).where(*criteria).with_for_update())
        if record is None:
            raise RuntimeError("idempotency conflict row disappeared")
        if record.request_hash != request_hash:
            raise ServiceError(
                code=ApiErrorCode.IDEMPOTENCY_MISMATCH,
                message="Idempotency-Key was already used with a different request body.",
                status_code=409,
            )
        if record.response_body is None or record.response_status is None:
            raise RuntimeError("idempotency record committed without a response")
        return deserialize(record.response_body), True

    record = session.get(IdempotencyRecord, claimed_id)
    if record is None:
        raise RuntimeError("claimed idempotency record is unavailable")
    result = action()
    record.response_status = response_status
    record.response_body = serialize(result)
    return result, False
