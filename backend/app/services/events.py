"""PostgreSQL-backed event replay and Server-Sent Events framing."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID


class EventRecord(Protocol):
    id: int
    scenario_id: UUID | None
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    occurred_at: datetime
    reason_code: str | None
    payload: dict[str, object]


EventFetcher = Callable[[UUID | None, int, int], Awaitable[Sequence[EventRecord]]]
DisconnectCheck = Callable[[], Awaitable[bool]]


def serialize_event(event: EventRecord) -> str:
    """Encode one persisted event as a standards-compliant SSE frame."""
    if not isinstance(event.id, int) or isinstance(event.id, bool) or event.id <= 0:
        raise ValueError("persisted event id must be a positive integer")
    event_name = str(event.event_type)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", event_name):
        raise ValueError("persisted event type is not a valid SSE event name")
    payload = {
        "id": event.id,
        "scenario_id": str(event.scenario_id) if event.scenario_id is not None else None,
        "type": event_name,
        "aggregate_type": str(event.aggregate_type),
        "aggregate_id": str(event.aggregate_id),
        "occurred_at": _iso_utc(event.occurred_at),
        "reason_code": str(event.reason_code) if event.reason_code is not None else None,
        "payload": event.payload,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event_name}\ndata: {data}\n\n"


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("persisted event timestamp must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


async def stream_event_frames(
    *,
    fetch_events: EventFetcher,
    scenario_id: UUID | None,
    after_id: int,
    is_disconnected: DisconnectCheck,
    poll_interval_seconds: float = 1.0,
    keepalive_interval_seconds: float = 15.0,
    batch_size: int = 100,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Poll committed rows, yielding events in id order and SSE keepalives.

    The fetch callback must execute a fresh read-committed database transaction
    per call. That lets an API process observe events committed by a worker in a
    separate process without an in-process notification bus.
    """
    if after_id < 0:
        raise ValueError("after_id must be nonnegative")
    if poll_interval_seconds <= 0 or keepalive_interval_seconds <= 0:
        raise ValueError("poll and keepalive intervals must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    cursor = after_id
    last_keepalive = monotonic()
    while not await is_disconnected():
        events = await fetch_events(scenario_id, cursor, batch_size)
        for event in sorted(events, key=lambda row: row.id):
            if await is_disconnected():
                return
            # Defend the cursor contract even if a test adapter or alternate
            # fetcher returns unordered/duplicate rows.
            if event.id <= cursor:
                continue
            cursor = event.id
            yield serialize_event(event)

        if not events:
            now = monotonic()
            if now - last_keepalive >= keepalive_interval_seconds:
                yield ": keepalive\n\n"
                last_keepalive = now
            await sleep(poll_interval_seconds)

