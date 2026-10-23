"""Deadline feasibility decisions for dispatch and already-carried cargo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.contracts.enums import ReasonCode
from app.domain.safety import (
    as_utc,
    latest_donor_arrival,
    nonnegative_duration,
    seconds_between,
)


class FeasibilityPhase(StrEnum):
    PRE_PICKUP = "PRE_PICKUP"
    IN_TRANSIT = "IN_TRANSIT"


@dataclass(frozen=True, slots=True)
class FeasibilityEvidence:
    phase: FeasibilityPhase
    evaluated_at: datetime
    deliver_by: datetime
    estimated_delivery_at: datetime
    travel_to_donor_seconds: float
    donor_to_shelter_eta_seconds: float
    handover_seconds: float
    handling_buffer_seconds: float
    dropoff_seconds: float
    latest_donor_arrival: datetime | None
    slack_seconds: float
    exact_deadline_is_feasible: bool = True


@dataclass(frozen=True, slots=True)
class FeasibilityDecision:
    feasible: bool
    reason_code: ReasonCode | None
    evidence: FeasibilityEvidence


def evaluate_pre_pickup(
    *,
    now: datetime,
    rider_to_donor_eta_seconds: int | float,
    handover_seconds: int | float,
    donor_to_shelter_eta_seconds: int | float,
    handling_buffer_seconds: int | float,
    deliver_by: datetime | None,
) -> FeasibilityDecision:
    """Evaluate rider→donor→shelter before assigning/picking up a lot.

    Exact deadline (`estimated_delivery_at == deliver_by`) is feasible because
    the governing policy uses <=. Any later arrival is blocked.
    """
    evaluated_at = as_utc(now, name="now")
    deadline = as_utc(deliver_by, name="deliver_by")
    rider_eta = nonnegative_duration(
        rider_to_donor_eta_seconds, name="rider_to_donor_eta_seconds"
    )
    handover = nonnegative_duration(handover_seconds, name="handover_seconds")
    route_eta = nonnegative_duration(
        donor_to_shelter_eta_seconds, name="donor_to_shelter_eta_seconds"
    )
    handling = nonnegative_duration(
        handling_buffer_seconds, name="handling_buffer_seconds"
    )
    latest = latest_donor_arrival(
        deadline,
        handover_seconds=handover.total_seconds(),
        donor_to_shelter_eta_seconds=route_eta.total_seconds(),
        handling_buffer_seconds=handling.total_seconds(),
    )
    donor_arrival = evaluated_at + rider_eta
    estimated_delivery = donor_arrival + handover + route_eta + handling
    slack = seconds_between(donor_arrival, latest)
    feasible = estimated_delivery <= deadline
    reason = None if feasible else _deadline_reason(evaluated_at, deadline)
    evidence = FeasibilityEvidence(
        phase=FeasibilityPhase.PRE_PICKUP,
        evaluated_at=evaluated_at,
        deliver_by=deadline,
        estimated_delivery_at=estimated_delivery,
        travel_to_donor_seconds=rider_eta.total_seconds(),
        donor_to_shelter_eta_seconds=route_eta.total_seconds(),
        handover_seconds=handover.total_seconds(),
        handling_buffer_seconds=handling.total_seconds(),
        dropoff_seconds=0,
        latest_donor_arrival=latest,
        slack_seconds=slack,
    )
    return FeasibilityDecision(feasible=feasible, reason_code=reason, evidence=evidence)


def evaluate_in_transit(
    *,
    now: datetime,
    rider_to_shelter_eta_seconds: int | float,
    required_dropoff_seconds: int | float,
    deliver_by: datetime | None,
) -> FeasibilityDecision:
    """Revalidate already-carried cargo from its current rider position.

    Do not model donor travel or pickup handover again after pickup.
    """
    evaluated_at = as_utc(now, name="now")
    deadline = as_utc(deliver_by, name="deliver_by")
    route_eta = nonnegative_duration(
        rider_to_shelter_eta_seconds, name="rider_to_shelter_eta_seconds"
    )
    dropoff = nonnegative_duration(
        required_dropoff_seconds, name="required_dropoff_seconds"
    )
    estimated_delivery = evaluated_at + route_eta + dropoff
    slack = seconds_between(estimated_delivery, deadline)
    feasible = estimated_delivery <= deadline
    evidence = FeasibilityEvidence(
        phase=FeasibilityPhase.IN_TRANSIT,
        evaluated_at=evaluated_at,
        deliver_by=deadline,
        estimated_delivery_at=estimated_delivery,
        travel_to_donor_seconds=0,
        donor_to_shelter_eta_seconds=route_eta.total_seconds(),
        handover_seconds=0,
        handling_buffer_seconds=0,
        dropoff_seconds=dropoff.total_seconds(),
        latest_donor_arrival=None,
        slack_seconds=slack,
    )
    reason = None if feasible else _deadline_reason(evaluated_at, deadline)
    return FeasibilityDecision(feasible=feasible, reason_code=reason, evidence=evidence)


def pre_pickup_slack_seconds(
    *,
    now: datetime,
    rider_to_donor_eta_seconds: int | float,
    deliver_by: datetime,
    handover_seconds: int | float,
    donor_to_shelter_eta_seconds: int | float,
    handling_buffer_seconds: int | float,
) -> float:
    """Time remaining at donor arrival; handover is counted exactly once."""
    evaluated_at = as_utc(now, name="now")
    rider_eta = nonnegative_duration(
        rider_to_donor_eta_seconds, name="rider_to_donor_eta_seconds"
    )
    latest = latest_donor_arrival(
        deliver_by,
        handover_seconds=handover_seconds,
        donor_to_shelter_eta_seconds=donor_to_shelter_eta_seconds,
        handling_buffer_seconds=handling_buffer_seconds,
    )
    return seconds_between(evaluated_at + rider_eta, latest)


def _deadline_reason(now: datetime, deliver_by: datetime) -> ReasonCode:
    # A deadline exactly equal to now can still pass only when the calculated
    # completion is also exactly now; a later projection is ETA-infeasible.
    return (
        ReasonCode.SAFETY_EXPIRED
        if now > deliver_by
        else ReasonCode.SAFETY_ETA_INFEASIBLE
    )
