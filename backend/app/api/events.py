"""Authenticated event replay and PostgreSQL-polled SSE stream."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_actor
from app.core.db import SessionLocal
from app.models import Event, User
from app.services.events import EventRecord, stream_event_frames


router = APIRouter(prefix="/api/v1/events", tags=["events"])
_POLL_INTERVAL_SECONDS = 1.0
_KEEPALIVE_INTERVAL_SECONDS = 15.0
_BATCH_SIZE = 100


def _read_committed_events(
    scenario_id: UUID | None,
    after_id: int,
    limit: int,
) -> Sequence[EventRecord]:
    """Read a scoped page in a short-lived DB session (one read per poll)."""
    statement = event_page_statement(scenario_id, after_id, limit)
    with SessionLocal() as session:
        return tuple(session.scalars(statement).all())


def event_page_statement(scenario_id: UUID | None, after_id: int, limit: int):
    """Build a deterministic cursor page scoped to live or one scenario."""
    statement = select(Event).where(Event.id > after_id)
    if scenario_id is None:
        statement = statement.where(Event.scenario_id.is_(None))
    else:
        statement = statement.where(Event.scenario_id == scenario_id)
    return statement.order_by(Event.id.asc()).limit(limit)


async def _fetch_events(
    scenario_id: UUID | None,
    after_id: int,
    limit: int,
) -> Sequence[EventRecord]:
    # Keep synchronous SQLAlchemy work off FastAPI's event loop. A new session
    # and transaction per poll ensures each query sees newly committed rows.
    return await asyncio.to_thread(_read_committed_events, scenario_id, after_id, limit)


@router.get("/stream")
async def event_stream(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after_id: int | None = Query(default=None, ge=0),
    actor: User = Depends(current_actor),
) -> StreamingResponse:
    """Stream only the authenticated actor's scenario, or shared live events.

    The standard `Last-Event-ID` header takes precedence over the `after_id`
    query parameter when reconnecting.
    """
    cursor = _parse_cursor(last_event_id, after_id)
    frames = stream_event_frames(
        fetch_events=_fetch_events,
        scenario_id=actor.scenario_id,
        after_id=cursor,
        is_disconnected=request.is_disconnected,
        poll_interval_seconds=_POLL_INTERVAL_SECONDS,
        keepalive_interval_seconds=_KEEPALIVE_INTERVAL_SECONDS,
        batch_size=_BATCH_SIZE,
    )
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _parse_cursor(last_event_id: str | None, after_id: int | None) -> int:
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Last-Event-ID must be a nonnegative integer")
        if cursor < 0:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be a nonnegative integer")
        return cursor
    return after_id or 0
