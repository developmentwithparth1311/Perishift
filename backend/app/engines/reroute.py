"""Pure P0 recovery proposals for cargo already in rider custody."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.rules import CompatibilityRequirements, RiderHandlingFeatures
from app.engines.fallback import (
    FallbackEvaluation,
    FallbackSnapshot,
    Position,
    rank_fallbacks,
)


@dataclass(frozen=True, slots=True)
class RerouteProposal:
    """Advisory result; the transactional service must revalidate and commit."""

    cargo_quantity: int
    item_class: ItemClass
    current_destination_id: str
    evaluated_at: datetime
    current_position: Position
    selected: FallbackEvaluation | None
    ranked_candidates: tuple[FallbackEvaluation, ...]
    partial_options: tuple[FallbackEvaluation, ...]
    reason_code: ReasonCode | None
    explanation: str

    @property
    def feasible(self) -> bool:
        return self.selected is not None


@dataclass(frozen=True, slots=True)
class RerouteRevalidation:
    """Whether a previously proposed destination still matches fresh inputs."""

    valid: bool
    reason_code: ReasonCode | None
    refreshed_proposal: RerouteProposal
    explanation: str


def propose_reroute(
    *,
    cargo_quantity: int,
    item_class: ItemClass | str,
    current_position: Position,
    now: datetime,
    deliver_by: datetime,
    current_destination_id: str,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    candidates: Iterable[FallbackSnapshot],
    max_snapshot_age_seconds: int = 60,
) -> RerouteProposal:
    """Select the best fresh, compatible destination that accepts all cargo.

    No inventory or demand is reserved. Destination/route snapshots and their
    versions are returned to the caller so the committing transaction can
    lock and verify current state. Partial-only sites appear in
    ``partial_options`` for coordinator review, never as a selected proposal.
    """
    try:
        canonical_class = ItemClass(item_class)
    except ValueError as exc:
        raise ValueError(f"unsupported item class: {item_class!r}") from exc
    snapshots = tuple(candidates)
    ranked = rank_fallbacks(
        cargo_quantity=cargo_quantity,
        item_class=canonical_class,
        current_position=current_position,
        now=now,
        deliver_by=deliver_by,
        rider=rider,
        requirements=requirements,
        candidates=snapshots,
        max_snapshot_age_seconds=max_snapshot_age_seconds,
    )
    selected = next((candidate for candidate in ranked if candidate.full_cargo_feasible), None)
    partial = tuple(
        candidate
        for candidate in ranked
        if candidate.available_quantity > 0
        and candidate.available_quantity < cargo_quantity
        and candidate.reasons == (ReasonCode.CAPACITY_SHELTER,)
    )
    if selected is None:
        reason = ReasonCode.NO_FEASIBLE_DESTINATION
        detail = (
            f"No fresh, open, compatible destination can safely accept all {cargo_quantity} carried units."
        )
    else:
        reason = None
        detail = (
            f"{selected.destination_id} is the highest-ranked advisory destination for all "
            f"{cargo_quantity} carried units; revalidate its current demand and route before committing."
        )
    return RerouteProposal(
        cargo_quantity=cargo_quantity,
        item_class=canonical_class,
        current_destination_id=current_destination_id,
        evaluated_at=now,
        current_position=current_position,
        selected=selected,
        ranked_candidates=ranked,
        partial_options=partial,
        reason_code=reason,
        explanation=detail,
    )


def revalidate_reroute_proposal(
    *,
    proposal: RerouteProposal,
    current_position: Position,
    now: datetime,
    deliver_by: datetime,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    current_candidates: Iterable[FallbackSnapshot],
    max_snapshot_age_seconds: int = 60,
) -> RerouteRevalidation:
    """Recompute a proposal and reject changed destination/traffic snapshots.

    A proposal is valid only while the same destination remains the best safe
    full-cargo choice and its destination version, traffic version, ETA and
    position anchor match the original proposal. On failure, the refreshed
    proposal is returned for an explicit fresh decision; this function never
    silently commits or mutates state.
    """
    refreshed = propose_reroute(
        cargo_quantity=proposal.cargo_quantity,
        item_class=proposal.item_class,
        current_position=current_position,
        now=now,
        deliver_by=deliver_by,
        current_destination_id=proposal.current_destination_id,
        rider=rider,
        requirements=requirements,
        candidates=current_candidates,
        max_snapshot_age_seconds=max_snapshot_age_seconds,
    )
    old, new = proposal.selected, refreshed.selected
    valid = bool(
        old is not None
        and new is not None
        and old.destination_id == new.destination_id
        and old.destination_version == new.destination_version
        and old.traffic_version == new.traffic_version
        and old.eta_seconds == new.eta_seconds
        and proposal.current_position == current_position
    )
    if valid:
        return RerouteRevalidation(
            valid=True,
            reason_code=None,
            refreshed_proposal=refreshed,
            explanation="The same full-cargo destination remains feasible with unchanged destination and traffic versions.",
        )
    reason = (
        ReasonCode.DESTINATION_REVALIDATION_REQUIRED
        if old is not None
        else ReasonCode.NO_FEASIBLE_DESTINATION
    )
    return RerouteRevalidation(
        valid=False,
        reason_code=reason,
        refreshed_proposal=refreshed,
        explanation="The proposal is stale or no longer the same safe full-cargo choice; use the refreshed result and revalidate inside the committing transaction.",
    )
