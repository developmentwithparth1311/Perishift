"""Deterministic, advisory fallback candidate ranking.

This module is deliberately pure: it ranks immutable snapshots and never
reserves shelter demand or changes rider/lot custody. Capacity is evaluated
against the complete physical cargo quantity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.rules import (
    CompatibilityDecision,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
    evaluate_compatibility,
)
from app.engines.feasibility import evaluate_in_transit


@dataclass(frozen=True, slots=True)
class Position:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not (isfinite(self.latitude) and -90 <= self.latitude <= 90):
            raise ValueError("latitude must be finite and between -90 and 90")
        if not (isfinite(self.longitude) and -180 <= self.longitude <= 180):
            raise ValueError("longitude must be finite and between -180 and 180")


@dataclass(frozen=True, slots=True)
class FallbackSnapshot:
    """One versioned destination, demand, capability and route snapshot."""

    destination_id: str
    label: str
    position: Position
    intake_open: bool
    available_quantity: int
    snapshot_at: datetime
    version: int
    capabilities: DestinationCapabilities
    eta_seconds: float
    eta_origin: Position
    eta_destination: Position
    eta_as_of: datetime
    traffic_version: str
    dropoff_seconds: float = 0
    reliability_score: float = 0


@dataclass(frozen=True, slots=True)
class FallbackEvaluation:
    destination_id: str
    label: str
    full_cargo_feasible: bool
    available_quantity: int
    cargo_quantity: int
    eta_seconds: float
    slack_seconds: float | None
    destination_version: int
    traffic_version: str
    reasons: tuple[ReasonCode, ...]
    explanation: str
    compatibility: CompatibilityDecision | None = None

    @property
    def partial_capacity(self) -> int:
        """Quantity this snapshot could accept, capped at physical cargo."""
        return min(self.cargo_quantity, max(0, self.available_quantity))


def rank_fallbacks(
    *,
    cargo_quantity: int,
    item_class: ItemClass | str,
    current_position: Position,
    now: datetime,
    deliver_by: datetime,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    candidates: Iterable[FallbackSnapshot],
    max_snapshot_age_seconds: int = 60,
) -> tuple[FallbackEvaluation, ...]:
    """Evaluate and rank snapshots for a single-stop, full-cargo reroute.

    Freshness, intake, hard compatibility, capacity and the B1 in-transit
    deadline gate all apply. Ranking is deterministic: safe full-cargo choices
    first, then greatest deadline slack, shorter ETA, greater reliability,
    and stable destination ID. Partial-only possibilities remain visible after
    full-cargo choices and can never be reported as feasible reroutes.
    """
    if isinstance(cargo_quantity, bool) or not isinstance(cargo_quantity, int) or cargo_quantity <= 0:
        raise ValueError("cargo_quantity must be a positive integer")
    if isinstance(max_snapshot_age_seconds, bool) or max_snapshot_age_seconds < 0:
        raise ValueError("max_snapshot_age_seconds must be nonnegative")
    try:
        canonical_class = ItemClass(item_class)
    except ValueError as exc:
        raise ValueError(f"unsupported item class: {item_class!r}") from exc
    now_utc = _utc(now, "now")
    deadline_utc = _utc(deliver_by, "deliver_by")
    snapshots = tuple(candidates)
    candidate_by_id = {candidate.destination_id: candidate for candidate in snapshots}
    ranked: list[FallbackEvaluation] = []
    seen_ids: set[str] = set()

    for candidate in snapshots:
        if not candidate.destination_id or candidate.destination_id in seen_ids:
            raise ValueError("candidate destination IDs must be nonempty and unique")
        seen_ids.add(candidate.destination_id)
        evaluation = _evaluate_candidate(
            candidate=candidate,
            cargo_quantity=cargo_quantity,
            item_class=canonical_class,
            current_position=current_position,
            now=now_utc,
            deliver_by=deadline_utc,
            rider=rider,
            requirements=requirements,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
        )
        ranked.append(evaluation)

    ranked.sort(
        key=lambda item: (
            not item.full_cargo_feasible,
            -(item.slack_seconds if item.slack_seconds is not None else float("-inf")),
            item.eta_seconds,
            -candidate_by_id[item.destination_id].reliability_score,
            item.destination_id,
        )
    )
    return tuple(ranked)


def _evaluate_candidate(
    *,
    candidate: FallbackSnapshot,
    cargo_quantity: int,
    item_class: ItemClass,
    current_position: Position,
    now: datetime,
    deliver_by: datetime,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    max_snapshot_age_seconds: int,
) -> FallbackEvaluation:
    reasons: list[ReasonCode] = []
    eta = _duration(candidate.eta_seconds, "eta_seconds")
    dropoff = _duration(candidate.dropoff_seconds, "dropoff_seconds")
    if candidate.available_quantity < 0:
        raise ValueError("available_quantity cannot be negative")
    if (
        isinstance(candidate.version, bool)
        or not isinstance(candidate.version, int)
        or candidate.version < 0
    ):
        raise ValueError("version must be a nonnegative integer")
    if (
        isinstance(candidate.available_quantity, bool)
        or not isinstance(candidate.available_quantity, int)
    ):
        raise ValueError("available_quantity must be an integer")
    if not isfinite(candidate.reliability_score):
        raise ValueError("reliability_score must be finite")

    compatibility = evaluate_compatibility(
        item_class,
        candidate.capabilities,
        rider,
        requirements,
        now,
    )
    if not compatibility.eligible:
        reasons.extend(compatibility.reasons)

    fresh = _fresh(candidate.snapshot_at, now, max_snapshot_age_seconds)
    eta_fresh = _fresh(candidate.eta_as_of, now, max_snapshot_age_seconds)
    if (
        not fresh
        or not eta_fresh
        or candidate.eta_origin != current_position
        or candidate.eta_destination != candidate.position
    ):
        reasons.append(ReasonCode.DESTINATION_REVALIDATION_REQUIRED)
    if not candidate.intake_open:
        reasons.append(ReasonCode.DEMAND_CLOSED)
    if candidate.available_quantity < cargo_quantity:
        reasons.append(ReasonCode.CAPACITY_SHELTER)

    feasibility = evaluate_in_transit(
        now=now,
        rider_to_shelter_eta_seconds=eta,
        required_dropoff_seconds=dropoff,
        deliver_by=deliver_by,
    )
    if not feasibility.feasible:
        reasons.append(feasibility.reason_code or ReasonCode.SAFETY_ETA_INFEASIBLE)

    reasons = list(dict.fromkeys(reasons))
    feasible = not reasons
    explanation = (
        "Fresh destination and traffic snapshots accept the full cargo, meet compatibility requirements, and meet the deadline."
        if feasible
        else "Not a safe full-cargo reroute: " + ", ".join(reason.value for reason in reasons) + "."
    )
    return FallbackEvaluation(
        destination_id=candidate.destination_id,
        label=candidate.label,
        full_cargo_feasible=feasible,
        available_quantity=candidate.available_quantity,
        cargo_quantity=cargo_quantity,
        eta_seconds=eta,
        slack_seconds=feasibility.evidence.slack_seconds,
        destination_version=candidate.version,
        traffic_version=candidate.traffic_version,
        reasons=tuple(reasons),
        explanation=explanation,
        compatibility=compatibility,
    )


def _fresh(value: datetime, now: datetime, max_age: int) -> bool:
    try:
        timestamp = _utc(value, "snapshot timestamp")
    except ValueError:
        return False
    age = (now - timestamp).total_seconds()
    return 0 <= age <= max_age


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def _duration(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative duration")
    return float(value)


