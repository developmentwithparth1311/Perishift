from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.rules import (
    CapabilityEvidence,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
)
from app.engines.fallback import FallbackSnapshot, Position, rank_fallbacks
from app.engines.reroute import propose_reroute, revalidate_reroute_proposal


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
POS = Position(28.6139, 77.2090)
OTHER_POS = Position(28.6200, 77.2100)
DEADLINE = NOW + timedelta(minutes=30)
RIDER = RiderHandlingFeatures(
    {"food_safe_container": CapabilityEvidence(True, NOW)}
)
REQUIREMENTS = CompatibilityRequirements()
CAPABILITIES = DestinationCapabilities()


def candidate(
    destination_id: str,
    *,
    capacity: int = 40,
    eta: float = 300,
    open_: bool = True,
    snapshot_at: datetime = NOW,
    eta_as_of: datetime = NOW,
    eta_origin: Position = POS,
    version: int = 1,
    traffic_version: str = "traffic-1",
    reliability: float = 0,
) -> FallbackSnapshot:
    return FallbackSnapshot(
        destination_id=destination_id,
        label=destination_id.title(),
        position=OTHER_POS,
        intake_open=open_,
        available_quantity=capacity,
        snapshot_at=snapshot_at,
        version=version,
        capabilities=CAPABILITIES,
        eta_seconds=eta,
        eta_origin=eta_origin,
        eta_destination=OTHER_POS,
        eta_as_of=eta_as_of,
        traffic_version=traffic_version,
        reliability_score=reliability,
    )


def propose(candidates, **changes):
    args = {
        "cargo_quantity": 40,
        "item_class": ItemClass.HOT_COOKED_MEAL,
        "current_position": POS,
        "now": NOW,
        "deliver_by": DEADLINE,
        "current_destination_id": "closed-original",
        "rider": RIDER,
        "requirements": REQUIREMENTS,
        "candidates": candidates,
    }
    args.update(changes)
    return propose_reroute(**args)


def test_reroute_selects_fresh_alternative_for_all_cargo_deterministically():
    choices = [
        candidate("z-slack", eta=600),
        candidate("a-nearest", eta=120, reliability=0.2),
        candidate("closed-original", open_=False),
    ]
    first = propose(choices)
    second = propose(reversed(choices))

    assert first.feasible
    assert first.selected.destination_id == "a-nearest"
    assert first.selected.full_cargo_feasible
    assert first.selected.destination_version == 1
    assert first.ranked_candidates == second.ranked_candidates
    assert "revalidate" in first.explanation


def test_partial_capacity_is_visible_but_never_selected_or_claimed_safe():
    result = propose([candidate("only-30", capacity=30)])

    assert not result.feasible
    assert result.selected is None
    assert result.reason_code is ReasonCode.NO_FEASIBLE_DESTINATION
    assert len(result.partial_options) == 1
    assert result.partial_options[0].destination_id == "only-30"
    assert result.partial_options[0].partial_capacity == 30
    assert not result.partial_options[0].full_cargo_feasible


def test_stale_destination_or_route_evidence_is_not_trusted():
    stale = NOW - timedelta(seconds=61)
    result = propose(
        [candidate("stale", snapshot_at=stale, eta_as_of=stale)],
        max_snapshot_age_seconds=60,
    )

    assert result.selected is None
    assert ReasonCode.DESTINATION_REVALIDATION_REQUIRED in result.ranked_candidates[0].reasons
    assert not result.partial_options


def test_traffic_snapshot_from_another_position_is_rejected():
    result = propose([candidate("wrong-origin", eta_origin=OTHER_POS)])

    assert not result.feasible
    assert ReasonCode.DESTINATION_REVALIDATION_REQUIRED in result.ranked_candidates[0].reasons


def test_exact_deadline_is_feasible_but_traffic_spike_is_not():
    exact = candidate("exact", eta=DEADLINE.timestamp() - NOW.timestamp())
    result = propose([exact])
    assert result.feasible
    assert result.selected.slack_seconds == 0

    late = replace(exact, eta_seconds=exact.eta_seconds + 1, traffic_version="traffic-spike")
    result_late = propose([late])
    assert not result_late.feasible
    assert ReasonCode.SAFETY_ETA_INFEASIBLE in result_late.ranked_candidates[0].reasons


def test_destination_change_before_commit_invalidates_previous_proposal():
    original = candidate("fallback")
    proposal = propose([original])
    assert proposal.feasible

    changed = replace(original, version=2, available_quantity=20)
    result = revalidate_reroute_proposal(
        proposal=proposal,
        current_position=POS,
        now=NOW + timedelta(seconds=1),
        deliver_by=DEADLINE,
        rider=RIDER,
        requirements=REQUIREMENTS,
        current_candidates=[changed],
    )
    assert not result.valid
    assert result.reason_code is ReasonCode.DESTINATION_REVALIDATION_REQUIRED
    assert result.refreshed_proposal.selected is None
    assert result.refreshed_proposal.reason_code is ReasonCode.NO_FEASIBLE_DESTINATION


def test_traffic_version_change_invalidates_previous_proposal_even_if_still_safe():
    original = candidate("fallback")
    proposal = propose([original])
    changed_traffic = replace(original, traffic_version="traffic-2")
    result = revalidate_reroute_proposal(
        proposal=proposal,
        current_position=POS,
        now=NOW + timedelta(seconds=1),
        deliver_by=DEADLINE,
        rider=RIDER,
        requirements=REQUIREMENTS,
        current_candidates=[changed_traffic],
    )
    assert not result.valid
    assert result.reason_code is ReasonCode.DESTINATION_REVALIDATION_REQUIRED
    assert result.refreshed_proposal.feasible


def test_partial_capacity_site_is_not_shown_if_closed_or_past_deadline():
    closed = candidate("closed-partial", capacity=30, open_=False)
    too_slow = candidate("late-partial", capacity=30, eta=DEADLINE.timestamp() - NOW.timestamp() + 1)
    result = propose([closed, too_slow])
    assert result.selected is None
    assert result.partial_options == ()


def test_fallback_rank_uses_slack_then_eta_then_stable_id():
    result = rank_fallbacks(
        cargo_quantity=40,
        item_class=ItemClass.HOT_COOKED_MEAL,
        current_position=POS,
        now=NOW,
        deliver_by=DEADLINE,
        rider=RIDER,
        requirements=REQUIREMENTS,
        candidates=[candidate("z", eta=200), candidate("a", eta=100)],
    )
    assert [item.destination_id for item in result] == ["a", "z"]
