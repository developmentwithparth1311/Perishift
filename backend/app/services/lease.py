"""Lease window creation and progress-aware renewal rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.contracts.enums import LeaseStatus
from app.domain.transitions import transition_lease
from app.models import Lease


LEASE_SECONDS = 180
FIRST_PROGRESS_SECONDS = 90
BACKUP_CUTOFF_SECONDS = 600


@dataclass(frozen=True, slots=True)
class LeaseWindow:
    expires_at: datetime
    first_progress_by: datetime
    physical_cutoff_at: datetime
    backup_cutoff_at: datetime


def build_lease_window(
    now: datetime,
    physical_cutoff_at: datetime,
    *,
    lease_seconds: int = LEASE_SECONDS,
    first_progress_seconds: int = FIRST_PROGRESS_SECONDS,
    backup_cutoff_seconds: int = BACKUP_CUTOFF_SECONDS,
) -> LeaseWindow:
    """Build bounded lease deadlines, never extending beyond delivery safety."""
    now = _utc(now, "now")
    physical_cutoff_at = _utc(physical_cutoff_at, "physical_cutoff_at")
    if min(lease_seconds, first_progress_seconds, backup_cutoff_seconds) <= 0:
        raise ValueError("lease durations must be positive")
    if physical_cutoff_at <= now:
        raise ValueError("no physical pickup window remains")
    expires_at = min(now + timedelta(seconds=lease_seconds), physical_cutoff_at)
    first_progress_by = min(now + timedelta(seconds=first_progress_seconds), expires_at)
    backup_cutoff_at = max(expires_at, min(physical_cutoff_at, now + timedelta(seconds=backup_cutoff_seconds)))
    return LeaseWindow(expires_at, first_progress_by, physical_cutoff_at, backup_cutoff_at)


def create_lease(
    *,
    scenario_id: UUID | None,
    lot_id: UUID,
    rider_id: UUID,
    assignment_id: UUID,
    now: datetime,
    physical_cutoff_at: datetime,
) -> Lease:
    """Construct the initial active lease with server-calculated deadlines."""
    window = build_lease_window(now, physical_cutoff_at)
    return Lease(
        id=uuid4(),
        scenario_id=scenario_id,
        lot_id=lot_id,
        rider_id=rider_id,
        assignment_id=assignment_id,
        status=LeaseStatus.ACTIVE.value,
        issued_at=_utc(now, "now"),
        expires_at=window.expires_at,
        first_progress_by=window.first_progress_by,
        physical_cutoff_at=window.physical_cutoff_at,
        backup_cutoff_at=window.backup_cutoff_at,
        last_progress_at=None,
        renewals=0,
        version=0,
    )


def renew_lease_for_progress(lease: Lease, *, now: datetime) -> datetime:
    """Renew an unpicked lease after fresh, server-timed physical progress.

    Merely sending a heartbeat does not renew a lease. Callers must first verify
    new physical progress. The lease can never cross either safety cutoff, and
    each renewal advances the generation so older expiry jobs become stale.
    """
    progress_at = _utc(now, "now")
    expires_at = _utc(lease.expires_at, "expires_at")
    physical_cutoff = _utc(lease.physical_cutoff_at, "physical_cutoff_at")
    backup_cutoff = _utc(lease.backup_cutoff_at, "backup_cutoff_at")
    previous_progress = _utc(lease.last_progress_at, "last_progress_at") if lease.last_progress_at else None
    if lease.status != LeaseStatus.ACTIVE.value:
        raise ValueError("only an active pre-pickup lease can be renewed")
    if progress_at >= expires_at:
        raise ValueError("progress arrived after lease expiry")
    if previous_progress is not None and progress_at <= previous_progress:
        raise ValueError("progress timestamp must advance")
    if progress_at >= min(physical_cutoff, backup_cutoff):
        raise ValueError("progress arrived after the safety cutoff")
    renewed_until = min(progress_at + timedelta(seconds=LEASE_SECONDS), physical_cutoff, backup_cutoff)
    if renewed_until <= expires_at:
        raise ValueError("no safe lease extension remains")
    lease.last_progress_at = progress_at
    lease.expires_at = renewed_until
    lease.renewals += 1
    lease.version += 1
    return renewed_until


def release_lease(lease: Lease, *, status: LeaseStatus = LeaseStatus.RELEASED) -> None:
    """End an unpicked lease through the canonical terminal transition."""
    if status not in {LeaseStatus.RELEASED, LeaseStatus.EXPIRED, LeaseStatus.REVOKED}:
        raise ValueError("lease release status must be RELEASED, EXPIRED, or REVOKED")
    transition_lease(lease, status)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)
