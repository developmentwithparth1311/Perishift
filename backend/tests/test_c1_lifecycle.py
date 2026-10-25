from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.contracts.enums import LeaseStatus, LotState
from app.domain.transitions import InvalidTransition, transition_lease
from app.models import Lease
from app.services.lease import build_lease_window, create_lease, release_lease, renew_lease_for_progress


UTC = timezone.utc


def test_initial_lease_window_respects_physical_and_backup_cutoffs():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    physical_cutoff = now + timedelta(seconds=120)
    window = build_lease_window(now, physical_cutoff)

    assert window.expires_at == physical_cutoff
    assert window.first_progress_by == now + timedelta(seconds=90)
    assert window.backup_cutoff_at == physical_cutoff


def test_lease_renewal_requires_new_progress_and_is_generation_fenced():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    lease = create_lease(
        scenario_id=uuid4(),
        lot_id=uuid4(),
        rider_id=uuid4(),
        assignment_id=uuid4(),
        now=now,
        physical_cutoff_at=now + timedelta(hours=1),
    )

    extended = renew_lease_for_progress(lease, now=now + timedelta(seconds=60))
    assert extended == now + timedelta(seconds=240)
    assert lease.last_progress_at == now + timedelta(seconds=60)
    assert lease.renewals == 1
    assert lease.version == 1

    with pytest.raises(ValueError, match="must advance"):
        renew_lease_for_progress(lease, now=now + timedelta(seconds=60))
    with pytest.raises(ValueError, match="after lease expiry"):
        renew_lease_for_progress(lease, now=extended)


def test_lease_renewal_never_crosses_backup_or_physical_cutoff():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    lease = create_lease(
        scenario_id=None,
        lot_id=uuid4(),
        rider_id=uuid4(),
        assignment_id=uuid4(),
        now=now,
        physical_cutoff_at=now + timedelta(hours=1),
    )

    renew_lease_for_progress(lease, now=now + timedelta(seconds=60))
    renew_lease_for_progress(lease, now=now + timedelta(seconds=210))
    extended = renew_lease_for_progress(lease, now=now + timedelta(seconds=360))
    assert extended == now + timedelta(seconds=540)
    extended = renew_lease_for_progress(lease, now=now + timedelta(seconds=510))
    assert extended == lease.backup_cutoff_at == now + timedelta(seconds=600)
    with pytest.raises(ValueError):
        renew_lease_for_progress(lease, now=now + timedelta(seconds=600))


def test_picked_up_lease_cannot_be_renewed():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    lease = create_lease(
        scenario_id=None,
        lot_id=uuid4(),
        rider_id=uuid4(),
        assignment_id=uuid4(),
        now=now,
        physical_cutoff_at=now + timedelta(hours=1),
    )
    transition_lease(lease, LeaseStatus.PICKED_UP)

    with pytest.raises(ValueError, match="active pre-pickup"):
        renew_lease_for_progress(lease, now=now + timedelta(seconds=30))


def test_release_helper_only_accepts_terminal_pre_pickup_outcomes():
    lease = Lease(status=LeaseStatus.ACTIVE.value, version=0)
    release_lease(lease, status=LeaseStatus.REVOKED)

    assert (lease.status, lease.version) == (LeaseStatus.REVOKED.value, 1)
    with pytest.raises(InvalidTransition):
        release_lease(lease, status=LeaseStatus.RELEASED)
