from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.schemas import BatchBalance, CreateBatchRequest
from app.core.auth import current_actor
from app.core.db import get_db
from app.models import User
from app.services.resources import _batch_snapshot, create_batch, list_batches, read_batch


router = APIRouter(prefix="/api/v1/batches", tags=["batches"])


@router.post("", response_model=BatchBalance, status_code=status.HTTP_201_CREATED)
def post_batch(
    request: CreateBatchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> BatchBalance:
    if actor.role != "DONOR":
        raise HTTPException(status_code=403, detail="DONOR role is required")
    return create_batch(request, actor_id=actor.id, idempotency_key=idempotency_key)


@router.get("", response_model=list[BatchBalance])
def get_batches(
    scenario_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    actor: User = Depends(current_actor),
    session: Session = Depends(get_db),
) -> list[BatchBalance]:
    if actor.role not in {"DONOR", "COORDINATOR", "JUDGE"}:
        raise HTTPException(status_code=403, detail="Actor role cannot read batches")
    return [
        _batch_snapshot(batch, inventory)
        for batch, inventory in list_batches(
            session,
            actor_id=actor.id,
            role=actor.role,
            offset=offset,
            limit=limit,
            scenario_id=scenario_id,
        )
    ]


@router.get("/{batch_id}", response_model=BatchBalance)
def get_batch(
    batch_id: UUID,
    actor: User = Depends(current_actor),
    session: Session = Depends(get_db),
) -> BatchBalance:
    if actor.role not in {"DONOR", "COORDINATOR", "JUDGE"}:
        raise HTTPException(status_code=403, detail="Actor role cannot read batches")
    row = read_batch(session, batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch, inventory = row
    if actor.role == "DONOR":
        from app.models import Donor

        owner = session.scalar(
            select(Donor).where(Donor.id == batch.donor_id, Donor.user_id == actor.id)
        )
        if owner is None:
            raise HTTPException(status_code=403, detail="Batch belongs to another donor")
    return _batch_snapshot(batch, inventory)
