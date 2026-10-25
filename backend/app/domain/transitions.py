"""Canonical state transitions for dispatch lots and pickup leases.

The database constraints remain the last line of defense. These transitions make
invalid lifecycle changes fail at the command boundary before other ledger rows
are mutated.
"""

from __future__ import annotations

from typing import Protocol

from app.contracts.enums import LeaseStatus, LotState


class VersionedState(Protocol):
    state: str
    version: int


class VersionedLease(Protocol):
    status: str
    version: int


class InvalidTransition(ValueError):
    """Raised when a lifecycle command requests a forbidden state change."""


_LOT_TRANSITIONS: dict[str, frozenset[str]] = {
    LotState.OPEN.value: frozenset({LotState.OFFERED.value, LotState.EXPIRED.value, LotState.DISPOSED.value}),
    LotState.OFFERED.value: frozenset({LotState.OPEN.value, LotState.LEASED.value, LotState.EXPIRED.value}),
    LotState.LEASED.value: frozenset({LotState.OPEN.value, LotState.IN_TRANSIT.value, LotState.EXPIRED.value}),
    LotState.IN_TRANSIT.value: frozenset({LotState.DELIVERED.value, LotState.DISPOSED.value}),
    LotState.DELIVERED.value: frozenset(),
    LotState.EXPIRED.value: frozenset(),
    LotState.DISPOSED.value: frozenset(),
}

_LEASE_TRANSITIONS: dict[str, frozenset[str]] = {
    LeaseStatus.ACTIVE.value: frozenset({
        LeaseStatus.PICKED_UP.value,
        LeaseStatus.RELEASED.value,
        LeaseStatus.EXPIRED.value,
        LeaseStatus.REVOKED.value,
    }),
    LeaseStatus.PICKED_UP.value: frozenset(),
    LeaseStatus.RELEASED.value: frozenset(),
    LeaseStatus.EXPIRED.value: frozenset(),
    LeaseStatus.REVOKED.value: frozenset(),
}


def transition_lot(lot: VersionedState, target: LotState | str) -> None:
    """Apply an allowed lot transition and increment its optimistic version."""
    next_state = target.value if isinstance(target, LotState) else target
    current = lot.state
    if next_state not in _LOT_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"lot cannot transition from {current!r} to {next_state!r}")
    lot.state = next_state
    lot.version += 1


def transition_lease(lease: VersionedLease, target: LeaseStatus | str) -> None:
    """Apply an allowed lease transition and increment its generation/version."""
    next_status = target.value if isinstance(target, LeaseStatus) else target
    current = lease.status
    if next_status not in _LEASE_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"lease cannot transition from {current!r} to {next_status!r}")
    lease.status = next_status
    lease.version += 1
