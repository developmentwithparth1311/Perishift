"""Pure deterministic greedy planner for capacity- and demand-limited trips.

The planner consumes immutable snapshots and emits nonbinding proposals. It
never writes inventory, demand, rider, or assignment state; those decisions
must be revalidated and committed by the transaction service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable
from uuid import UUID

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.eta import Coordinates, DeterministicEtaProvider, RoutingProvider
from app.domain.rules import (
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
    evaluate_compatibility,
)
from app.engines.feasibility import FeasibilityEvidence, evaluate_pre_pickup


@dataclass(frozen=True, slots=True)
class PlannerBatch:
    batch_id: UUID | str
    item_class: ItemClass
    available_qty: int
    donor_location: Coordinates
    ready_at: datetime
    deliver_by: datetime
    handover_seconds: int | float = 0
    handling_buffer_seconds: int | float = 0
    requirements: CompatibilityRequirements = field(default_factory=CompatibilityRequirements)


@dataclass(frozen=True, slots=True)
class PlannerRider:
    rider_id: UUID | str
    location: Coordinates
    capacity_qty: int
    handling: RiderHandlingFeatures
    has_active_trip: bool = False


@dataclass(frozen=True, slots=True)
class PlannerDemand:
    demand_id: UUID | str
    shelter_id: UUID | str
    item_class: ItemClass
    location: Coordinates
    uncommitted_need: int
    intake_open: bool
    capabilities: DestinationCapabilities
    required_dropoff_seconds: int | float = 0
    service_window_start: datetime | None = None
    service_window_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class FallbackCandidate:
    demand_id: UUID | str
    shelter_id: UUID | str
    estimated_qty: int
    eta_seconds: int
    slack_seconds: float
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedTrip:
    batch_id: UUID | str
    rider_id: UUID | str
    demand_id: UUID | str
    shelter_id: UUID | str
    quantity: int
    rider_to_donor_eta_seconds: int
    donor_to_shelter_eta_seconds: int
    feasibility: FeasibilityEvidence
    fallback_candidates: tuple[FallbackCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class UnallocatedQuantity:
    batch_id: UUID | str
    quantity: int
    reason_codes: tuple[ReasonCode, ...]


@dataclass(frozen=True, slots=True)
class GreedyPlan:
    proposals: tuple[PlannedTrip, ...]
    unallocated: tuple[UnallocatedQuantity, ...]
    batch_available_qty: tuple[tuple[UUID | str, int], ...]

    @property
    def planned_qty(self) -> int:
        return sum(proposal.quantity for proposal in self.proposals)

    @property
    def unallocated_qty(self) -> int:
        return sum(entry.quantity for entry in self.unallocated)


@dataclass(frozen=True, slots=True)
class _FeasiblePair:
    rider: PlannerRider
    demand: PlannerDemand
    quantity: int
    rider_eta: int
    route_eta: int
    feasibility: FeasibilityEvidence


def plan_greedy(
    *,
    now: datetime,
    batches: Iterable[PlannerBatch],
    riders: Iterable[PlannerRider],
    demands: Iterable[PlannerDemand],
    scenario_id: UUID | None = None,
    eta_provider: RoutingProvider | None = None,
) -> GreedyPlan:
    """Build a deterministic split plan from a point-in-time snapshot.

    Candidate quantities are ``min(batch remaining, rider capacity,
    destination uncommitted need)``. Batches are considered by earliest
    deadline; each next candidate maximizes quantity, then remaining safety
    slack, then uses stable rider/demand/shelter identifiers as tie-breakers.
    A rider is used at most once per plan. Fallbacks are advisory and do not
    reserve stock or shelter capacity.
    """

    now_utc = _as_utc(now, "now")
    batch_list, rider_list, demand_list = tuple(batches), tuple(riders), tuple(demands)
    _validate_unique(batch_list, lambda item: item.batch_id, "batch")
    _validate_unique(rider_list, lambda item: item.rider_id, "rider")
    _validate_unique(demand_list, lambda item: item.demand_id, "demand")
    for batch in batch_list:
        _validate_batch(batch)
    for rider in rider_list:
        _validate_rider(rider)
    for demand in demand_list:
        _validate_demand(demand)

    provider = eta_provider or DeterministicEtaProvider()
    remaining_batch = {batch.batch_id: batch.available_qty for batch in batch_list}
    remaining_demand = {demand.demand_id: demand.uncommitted_need for demand in demand_list}
    used_riders = {rider.rider_id for rider in rider_list if rider.has_active_trip}
    proposals: list[PlannedTrip] = []
    unallocated: list[UnallocatedQuantity] = []

    for batch in sorted(batch_list, key=lambda item: (_as_utc(item.deliver_by, "deliver_by"), _stable(item.batch_id))):
        left = remaining_batch[batch.batch_id]
        if left == 0:
            continue
        reasons: set[ReasonCode] = set()
        while left > 0:
            candidates: list[_FeasiblePair] = []
            for rider in rider_list:
                if rider.rider_id in used_riders:
                    reasons.add(ReasonCode.RIDER_ALREADY_ASSIGNED)
                    continue
                if rider.capacity_qty == 0:
                    reasons.add(ReasonCode.CAPACITY_RIDER)
                    continue
                for demand in demand_list:
                    if demand.item_class is not batch.item_class:
                        continue
                    reason = _demand_unavailable_reason(demand, batch, now_utc)
                    if reason is not None:
                        reasons.add(reason)
                        continue
                    need = remaining_demand[demand.demand_id]
                    if need == 0:
                        reasons.add(ReasonCode.CAPACITY_SHELTER)
                        continue
                    quantity = min(left, rider.capacity_qty, need)
                    compatibility = evaluate_compatibility(
                        batch.item_class,
                        demand.capabilities,
                        rider.handling,
                        batch.requirements,
                        now_utc,
                    )
                    if not compatibility.eligible:
                        reasons.update(compatibility.reasons)
                        continue
                    rider_eta = provider.eta_seconds(rider.location, batch.donor_location, scenario_id)
                    route_eta = provider.eta_seconds(batch.donor_location, demand.location, scenario_id)
                    available_at = _as_utc(batch.ready_at, "ready_at")
                    arrival_at_donor = now_utc.timestamp() + rider_eta
                    wait_seconds = max(0, available_at.timestamp() - arrival_at_donor)
                    decision = evaluate_pre_pickup(
                        now=now_utc,
                        rider_to_donor_eta_seconds=rider_eta + wait_seconds,
                        handover_seconds=batch.handover_seconds,
                        donor_to_shelter_eta_seconds=route_eta,
                        handling_buffer_seconds=(
                            batch.handling_buffer_seconds + demand.required_dropoff_seconds
                        ),
                        deliver_by=batch.deliver_by,
                    )
                    if not decision.feasible:
                        if decision.reason_code is not None:
                            reasons.add(decision.reason_code)
                        continue
                    candidates.append(
                        _FeasiblePair(
                            rider=rider,
                            demand=demand,
                            quantity=quantity,
                            rider_eta=rider_eta,
                            route_eta=route_eta,
                            feasibility=decision.evidence,
                        )
                    )

            if not candidates:
                break
            chosen = min(
                candidates,
                key=lambda candidate: (
                    -candidate.quantity,
                    -candidate.feasibility.slack_seconds,
                    _stable(candidate.rider.rider_id),
                    _stable(candidate.demand.demand_id),
                    _stable(candidate.demand.shelter_id),
                ),
            )
            fallback = _fallbacks(candidates, chosen)
            proposals.append(
                PlannedTrip(
                    batch_id=batch.batch_id,
                    rider_id=chosen.rider.rider_id,
                    demand_id=chosen.demand.demand_id,
                    shelter_id=chosen.demand.shelter_id,
                    quantity=chosen.quantity,
                    rider_to_donor_eta_seconds=chosen.rider_eta,
                    donor_to_shelter_eta_seconds=chosen.route_eta,
                    feasibility=chosen.feasibility,
                    fallback_candidates=fallback,
                )
            )
            left -= chosen.quantity
            remaining_batch[batch.batch_id] = left
            remaining_demand[chosen.demand.demand_id] -= chosen.quantity
            used_riders.add(chosen.rider.rider_id)

        if left:
            if not reasons:
                reasons.add(ReasonCode.INSUFFICIENT_CAPACITY)
            unallocated.append(
                UnallocatedQuantity(
                    batch_id=batch.batch_id,
                    quantity=left,
                    reason_codes=tuple(sorted(reasons, key=lambda reason: reason.value)),
                )
            )

    return GreedyPlan(
        proposals=tuple(proposals),
        unallocated=tuple(unallocated),
        batch_available_qty=tuple(
            (batch.batch_id, batch.available_qty)
            for batch in sorted(batch_list, key=lambda item: _stable(item.batch_id))
        ),
    )


def _fallbacks(candidates: list[_FeasiblePair], selected: _FeasiblePair) -> tuple[FallbackCandidate, ...]:
    alternatives: dict[tuple[str, str], FallbackCandidate] = {}
    for candidate in candidates:
        if (
            candidate.rider.rider_id != selected.rider.rider_id
            or candidate.demand.demand_id == selected.demand.demand_id
        ):
            continue
        item = FallbackCandidate(
            demand_id=candidate.demand.demand_id,
            shelter_id=candidate.demand.shelter_id,
            estimated_qty=candidate.quantity,
            eta_seconds=candidate.route_eta,
            slack_seconds=candidate.feasibility.slack_seconds,
        )
        alternatives[(_stable(item.demand_id), _stable(item.shelter_id))] = item
    return tuple(alternatives[key] for key in sorted(alternatives))


def _demand_unavailable_reason(
    demand: PlannerDemand, batch: PlannerBatch, now: datetime
) -> ReasonCode | None:
    if demand.item_class is not batch.item_class:
        return None
    if not demand.intake_open:
        return ReasonCode.DEMAND_CLOSED
    start = _as_utc(demand.service_window_start, "service_window_start") if demand.service_window_start else None
    end = _as_utc(demand.service_window_end, "service_window_end") if demand.service_window_end else None
    if start is not None and now < start or end is not None and now >= end:
        return ReasonCode.DEMAND_CLOSED
    return None


def _validate_batch(batch: PlannerBatch) -> None:
    _quantity(batch.available_qty, "available_qty")
    _as_utc(batch.ready_at, "ready_at")
    _as_utc(batch.deliver_by, "deliver_by")
    _duration(batch.handover_seconds, "handover_seconds")
    _duration(batch.handling_buffer_seconds, "handling_buffer_seconds")
    if not isinstance(batch.item_class, ItemClass):
        raise ValueError("item_class must be an ItemClass")
    if not isinstance(batch.donor_location, Coordinates):
        raise ValueError("donor_location must be Coordinates")


def _validate_rider(rider: PlannerRider) -> None:
    _quantity(rider.capacity_qty, "capacity_qty")
    if not isinstance(rider.location, Coordinates):
        raise ValueError("rider location must be Coordinates")
    if not isinstance(rider.handling, RiderHandlingFeatures):
        raise ValueError("rider handling must be RiderHandlingFeatures")


def _validate_demand(demand: PlannerDemand) -> None:
    _quantity(demand.uncommitted_need, "uncommitted_need")
    _duration(demand.required_dropoff_seconds, "required_dropoff_seconds")
    if not isinstance(demand.item_class, ItemClass):
        raise ValueError("demand item_class must be an ItemClass")
    if not isinstance(demand.location, Coordinates):
        raise ValueError("demand location must be Coordinates")
    if not isinstance(demand.capabilities, DestinationCapabilities):
        raise ValueError("demand capabilities must be DestinationCapabilities")
    if (demand.service_window_start is None) != (demand.service_window_end is None):
        raise ValueError("both service window bounds must be supplied together")
    if demand.service_window_start is not None:
        if _as_utc(demand.service_window_start, "service_window_start") >= _as_utc(demand.service_window_end, "service_window_end"):
            raise ValueError("service window end must be later than its start")


def _validate_unique(items: tuple[object, ...], key, name: str) -> None:
    values = [key(item) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"{name} identifiers must be unique in a planning snapshot")


def _quantity(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _duration(value: int | float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative duration")


def _as_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stable(value: UUID | str) -> str:
    return str(value)
