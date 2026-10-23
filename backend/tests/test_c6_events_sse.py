import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.events import _parse_cursor, event_page_statement
from app.services.events import serialize_event, stream_event_frames


SCENARIO_A = UUID("00000000-0000-0000-0000-0000000000a1")
SCENARIO_B = UUID("00000000-0000-0000-0000-0000000000b2")
NOW = datetime(2026, 10, 23, 13, 5, tzinfo=timezone.utc)


def run_immediate(coroutine):
    """Drive pure coroutines whose test callbacks never perform I/O."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("pure test coroutine unexpectedly awaited external I/O")


def event(event_id: int, scenario_id: UUID | None, *, payload=None):
    return SimpleNamespace(
        id=event_id,
        scenario_id=scenario_id,
        event_type="LOT_AT_RISK",
        aggregate_type="lot",
        aggregate_id=UUID(int=event_id),
        occurred_at=NOW,
        reason_code="SAFETY_ETA_INFEASIBLE",
        payload=payload or {"message": "needs attention"},
    )


def test_event_query_is_cursor_based_scoped_and_id_ordered_for_scenarios():
    statement = event_page_statement(SCENARIO_A, after_id=17, limit=40)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "events.id >" in sql
    assert "events.scenario_id =" in sql
    assert "ORDER BY events.id ASC" in sql
    assert "LIMIT" in sql


def test_event_query_filters_live_rows_separately_from_simulation():
    live_sql = str(
        event_page_statement(None, after_id=0, limit=100).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "events.scenario_id IS NULL" in live_sql
    assert "events.scenario_id =" not in live_sql


def test_sse_frame_uses_standard_id_event_data_and_json_encoding():
    frame = serialize_event(
        event(18, SCENARIO_A, payload={"line": "first\nsecond", "unicode": "café"})
    )
    assert frame.startswith("id: 18\nevent: LOT_AT_RISK\ndata: ")
    assert frame.endswith("\n\n")
    data = json.loads(frame.split("data: ", 1)[1].splitlines()[0])
    assert data == {
        "id": 18,
        "scenario_id": str(SCENARIO_A),
        "type": "LOT_AT_RISK",
        "aggregate_type": "lot",
        "aggregate_id": str(UUID(int=18)),
        "occurred_at": "2026-10-23T13:05:00Z",
        "reason_code": "SAFETY_ETA_INFEASIBLE",
        "payload": {"line": "first\nsecond", "unicode": "café"},
    }


def test_sse_replay_passes_scope_and_cursor_and_emits_ordered_filtered_rows():
    rows = [
        event(2, SCENARIO_A),
        event(3, SCENARIO_B),
        event(5, SCENARIO_A),
        event(8, None),
        event(10, SCENARIO_A),
    ]
    fetch_calls = []

    async def fetch(scope, cursor, limit):
        fetch_calls.append((scope, cursor, limit))
        return tuple(
            sorted(
                (row for row in rows if row.scenario_id == scope and row.id > cursor),
                key=lambda row: row.id,
            )[:limit]
        )

    async def disconnected():
        return False

    async def no_sleep(_):
        return None

    async def collect():
        stream = stream_event_frames(
            fetch_events=fetch,
            scenario_id=SCENARIO_A,
            after_id=1,
            is_disconnected=disconnected,
            batch_size=2,
            sleep=no_sleep,
        )
        try:
            return [await anext(stream) for _ in range(3)]
        finally:
            await stream.aclose()

    frames = run_immediate(collect())
    ids = [int(frame.split("\n", 1)[0].removeprefix("id: ")) for frame in frames]
    assert ids == [2, 5, 10]
    assert fetch_calls == [
        (SCENARIO_A, 1, 2),
        (SCENARIO_A, 5, 2),
    ]


def test_live_stream_only_queries_null_scenario_scope():
    rows = [event(1, SCENARIO_A), event(2, None)]
    fetch_calls = []

    async def fetch(scope, cursor, limit):
        fetch_calls.append((scope, cursor))
        return [row for row in rows if row.scenario_id is None and row.id > cursor]

    async def disconnected():
        return False

    async def collect():
        stream = stream_event_frames(
            fetch_events=fetch,
            scenario_id=None,
            after_id=0,
            is_disconnected=disconnected,
        )
        try:
            return [await anext(stream)]
        finally:
            await stream.aclose()

    frames = run_immediate(collect())
    assert len(frames) == 1
    assert frames[0].startswith("id: 2\n")
    assert fetch_calls[0] == (None, 0)


def test_idle_stream_sends_comment_keepalive_without_event_data():
    now = [0.0]

    async def fetch(_scope, _cursor, _limit):
        return []

    async def disconnected():
        return False

    async def advance(seconds):
        now[0] += seconds

    async def run_one_keepalive():
        stream = stream_event_frames(
            fetch_events=fetch,
            scenario_id=SCENARIO_A,
            after_id=0,
            is_disconnected=disconnected,
            poll_interval_seconds=5,
            keepalive_interval_seconds=10,
            sleep=advance,
            monotonic=lambda: now[0],
        )
        try:
            for _ in range(5):
                frame = await anext(stream)
                if frame.startswith(":"):
                    return frame
            raise AssertionError("expected keepalive after idle polling")
        finally:
            await stream.aclose()

    assert run_immediate(run_one_keepalive()) == ": keepalive\n\n"


def test_disconnect_before_poll_terminates_without_fetch():
    fetch_calls = []

    async def fetch(*args):
        fetch_calls.append(args)
        return []

    async def disconnected():
        return True

    async def collect():
        return [
            frame
            async for frame in stream_event_frames(
                fetch_events=fetch,
                scenario_id=SCENARIO_A,
                after_id=0,
                is_disconnected=disconnected,
            )
        ]

    assert run_immediate(collect()) == []
    assert fetch_calls == []


@pytest.mark.parametrize(
    ("header", "query", "expected"),
    [("42", 7, 42), (None, 7, 7), (None, None, 0), ("0", None, 0)],
)
def test_sse_cursor_prefers_last_event_id_header(header, query, expected):
    assert _parse_cursor(header, query) == expected


@pytest.mark.parametrize("header", ["-1", "abc", "2.5"])
def test_sse_rejects_invalid_last_event_id_header(header):
    with pytest.raises(HTTPException) as error:
        _parse_cursor(header, None)
    assert error.value.status_code == 400


def test_sse_rejects_event_name_injection_and_naive_persisted_timestamps():
    with pytest.raises(ValueError):
        serialize_event(SimpleNamespace(**{**event(1, None).__dict__, "event_type": "X\ndata: forged"}))
    with pytest.raises(ValueError):
        serialize_event(SimpleNamespace(**{**event(1, None).__dict__, "occurred_at": datetime(2026, 10, 23)}))
