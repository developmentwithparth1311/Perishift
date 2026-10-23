"""Pure UTC-safe safety deadline and duration calculations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite


def as_utc(value: datetime, *, name: str = "timestamp") -> datetime:
    if value is None or not isinstance(value, datetime):
        raise ValueError(f"{name} is required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def nonnegative_duration(seconds: int | float, *, name: str) -> timedelta:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative number of seconds")
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(f"{name} must be a finite nonnegative number of seconds")
    return timedelta(seconds=seconds)


def delivery_deadline(safe_by: datetime, receiving_buffer_seconds: int | float) -> datetime:
    """Return `safe_by - receiving buffer`; no default safety cutoff is invented."""
    safe_by_utc = as_utc(safe_by, name="safe_by")
    buffer = nonnegative_duration(receiving_buffer_seconds, name="receiving_buffer_seconds")
    return safe_by_utc - buffer


def latest_donor_arrival(
    deliver_by: datetime,
    *,
    handover_seconds: int | float,
    donor_to_shelter_eta_seconds: int | float,
    handling_buffer_seconds: int | float,
) -> datetime:
    """Latest donor arrival that still allows the full delivery path.

    Handover is subtracted here once. Callers must not subtract it again when
    deriving pre-pickup slack.
    """
    deadline = as_utc(deliver_by, name="deliver_by")
    durations = (
        nonnegative_duration(handover_seconds, name="handover_seconds"),
        nonnegative_duration(
            donor_to_shelter_eta_seconds, name="donor_to_shelter_eta_seconds"
        ),
        nonnegative_duration(handling_buffer_seconds, name="handling_buffer_seconds"),
    )
    return deadline - sum(durations, timedelta())


def seconds_between(start: datetime, end: datetime) -> float:
    """Signed UTC duration `end - start`; useful for boundary/slack evidence."""
    return (as_utc(end, name="end") - as_utc(start, name="start")).total_seconds()
