import pytest

from app.contracts.enums import LeaseStatus, LotState
from app.domain.transitions import InvalidTransition, transition_lease, transition_lot
from app.models import Lease, Lot


def test_lot_state_machine_advances_versions_and_rejects_skips():
    lot = Lot(state=LotState.OFFERED.value, version=4)

    transition_lot(lot, LotState.LEASED)
    assert (lot.state, lot.version) == (LotState.LEASED.value, 5)

    with pytest.raises(InvalidTransition, match="cannot transition"):
        transition_lot(lot, LotState.DELIVERED)
    assert (lot.state, lot.version) == (LotState.LEASED.value, 5)


def test_lease_state_machine_is_terminal_after_pickup_or_release():
    lease = Lease(status=LeaseStatus.ACTIVE.value, version=2)
    transition_lease(lease, LeaseStatus.PICKED_UP)

    assert (lease.status, lease.version) == (LeaseStatus.PICKED_UP.value, 3)
    with pytest.raises(InvalidTransition):
        transition_lease(lease, LeaseStatus.REVOKED)
    assert lease.version == 3
