from datetime import datetime, timedelta, timezone

import pytest

from app.contracts.enums import ItemClass, ReasonCode
from app.domain.rules import (
    CapabilityEvidence,
    CompatibilityAlternative,
    CompatibilityRequirements,
    DestinationCapabilities,
    RiderHandlingFeatures,
    evaluate_compatibility,
)


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def evidence(available: bool | None = True, *, at: datetime = NOW) -> CapabilityEvidence:
    return CapabilityEvidence(available=available, verified_at=at)


def destination(**capabilities: bool) -> DestinationCapabilities:
    return DestinationCapabilities(
        capabilities={name: evidence(value) for name, value in capabilities.items()}
    )


def rider(**capabilities: bool) -> RiderHandlingFeatures:
    return RiderHandlingFeatures(
        capabilities={name: evidence(value) for name, value in capabilities.items()}
    )


@pytest.mark.parametrize(
    ("item_class", "destination_caps", "rider_caps", "requirements"),
    [
        (
            ItemClass.HOT_COOKED_MEAL,
            {},
            {"food_safe_container": True},
            CompatibilityRequirements(),
        ),
        (
            ItemClass.RAW_RATION_KIT,
            {
                "clean_water": True,
                "cooking_fuel": True,
                "cooking_facility": True,
            },
            {"food_safe_container": True},
            CompatibilityRequirements(),
        ),
        (
            ItemClass.POWDER_FORMULA,
            {
                "clean_water": True,
                "trained_receiver": True,
                "authorized_recipient": True,
            },
            {"clean_container": True},
            CompatibilityRequirements(formula_form="POWDER"),
        ),
        (
            ItemClass.POWDER_FORMULA,
            {"trained_receiver": True, "authorized_recipient": True},
            {"clean_container": True},
            CompatibilityRequirements(formula_form="READY_TO_FEED"),
        ),
        (
            ItemClass.COLD_CHAIN_MEDICINE,
            {
                "refrigeration": True,
                "trained_receiver": True,
                "authorized_recipient": True,
            },
            {"validated_cold_chain_carrier": True},
            CompatibilityRequirements(),
        ),
    ],
)
def test_each_core_class_passes_with_verified_required_capabilities(
    item_class, destination_caps, rider_caps, requirements
):
    result = evaluate_compatibility(
        item_class,
        destination(**destination_caps),
        rider(**rider_caps),
        requirements,
        NOW,
    )

    assert result.eligible
    assert result.reasons == ()
    assert result.findings == ()
    assert "all required" in result.explanation


def test_raw_ration_missing_water_and_fuel_fails_with_canonical_reasons():
    result = evaluate_compatibility(
        ItemClass.RAW_RATION_KIT,
        destination(cooking_facility=True),
        rider(food_safe_container=True),
        CompatibilityRequirements(),
        NOW,
    )

    assert not result.eligible
    assert result.reasons == (
        ReasonCode.COMPATIBILITY_MISSING_WATER,
        ReasonCode.COMPATIBILITY_MISSING_FUEL,
    )
    assert "clean water" in result.explanation
    assert "cooking fuel" in result.explanation


@pytest.mark.parametrize(
    ("item_class", "destination_caps", "rider_caps", "requirements", "expected_reason"),
    [
        (
            ItemClass.HOT_COOKED_MEAL,
            {},
            {},
            CompatibilityRequirements(),
            ReasonCode.SAFETY_EVIDENCE_MISSING,
        ),
        (
            ItemClass.RAW_RATION_KIT,
            {"clean_water": True, "cooking_fuel": True, "cooking_facility": True},
            {},
            CompatibilityRequirements(),
            ReasonCode.SAFETY_EVIDENCE_MISSING,
        ),
        (
            ItemClass.POWDER_FORMULA,
            {"trained_receiver": True, "authorized_recipient": True},
            {"clean_container": True},
            CompatibilityRequirements(formula_form="POWDER"),
            ReasonCode.COMPATIBILITY_MISSING_WATER,
        ),
        (
            ItemClass.COLD_CHAIN_MEDICINE,
            {"trained_receiver": True, "authorized_recipient": True},
            {"validated_cold_chain_carrier": True},
            CompatibilityRequirements(),
            ReasonCode.COMPATIBILITY_COLD_CHAIN,
        ),
    ],
)
def test_each_core_class_has_a_hard_blocking_negative_fixture(
    item_class, destination_caps, rider_caps, requirements, expected_reason
):
    result = evaluate_compatibility(
        item_class,
        destination(**destination_caps),
        rider(**rider_caps),
        requirements,
        NOW,
    )

    assert not result.eligible
    assert expected_reason in result.reasons
    assert result.explanation.startswith("Blocked:")


def test_stale_and_future_evidence_are_unknown_and_fail_closed():
    stale = evidence(at=NOW - timedelta(days=1, seconds=1))
    future = evidence(at=NOW + timedelta(seconds=1))
    result = evaluate_compatibility(
        ItemClass.COLD_CHAIN_MEDICINE,
        DestinationCapabilities(
            capabilities={
                "refrigeration": stale,
                "trained_receiver": future,
                "authorized_recipient": evidence(),
            }
        ),
        rider(validated_cold_chain_carrier=True),
        CompatibilityRequirements(),
        NOW,
    )

    assert not result.eligible
    assert ReasonCode.COMPATIBILITY_COLD_CHAIN in result.reasons
    assert ReasonCode.SAFETY_EVIDENCE_MISSING in result.reasons
    assert any("stale" in finding.message for finding in result.findings)
    assert any("future" not in finding.message for finding in result.findings)


def test_verification_is_fresh_at_the_exact_maximum_age_boundary():
    result = evaluate_compatibility(
        ItemClass.HOT_COOKED_MEAL,
        destination(),
        RiderHandlingFeatures(
            capabilities={
                "food_safe_container": evidence(at=NOW - timedelta(days=1))
            }
        ),
        CompatibilityRequirements(),
        NOW,
    )

    assert result.eligible


def test_explicitly_unknown_and_conflicting_capability_evidence_block():
    result = evaluate_compatibility(
        ItemClass.RAW_RATION_KIT,
        DestinationCapabilities(
            capabilities={
                "clean_water": evidence(None),
                "cooking_fuel": (evidence(True), evidence(False)),
                "cooking_facility": evidence(True),
            }
        ),
        rider(food_safe_container=True),
        CompatibilityRequirements(),
        NOW,
    )

    assert not result.eligible
    assert result.reasons == (
        ReasonCode.COMPATIBILITY_MISSING_WATER,
        ReasonCode.SAFETY_EVIDENCE_MISSING,
    )
    assert {finding.code for finding in result.findings} == {
        "CAPABILITY_NOT_VERIFIED",
        "CAPABILITY_EVIDENCE_CONFLICT",
    }


def test_formula_form_is_mandatory_and_ready_to_feed_does_not_require_water():
    caps = destination(trained_receiver=True, authorized_recipient=True)
    handling = rider(clean_container=True)

    unknown = evaluate_compatibility(
        ItemClass.POWDER_FORMULA,
        caps,
        handling,
        CompatibilityRequirements(),
        NOW,
    )
    ready_to_feed = evaluate_compatibility(
        ItemClass.POWDER_FORMULA,
        caps,
        handling,
        CompatibilityRequirements(formula_form="ready-to-feed"),
        NOW,
    )
    powder = evaluate_compatibility(
        ItemClass.POWDER_FORMULA,
        caps,
        handling,
        CompatibilityRequirements(formula_form="powder"),
        NOW,
    )

    assert not unknown.eligible
    assert unknown.findings[0].code == "FORMULA_FORM_UNKNOWN"
    assert ready_to_feed.eligible
    assert not powder.eligible
    assert ReasonCode.COMPATIBILITY_MISSING_WATER in powder.reasons


def test_dietary_allergen_and_unknown_item_profile_block():
    base_caps = destination()
    handling = rider(food_safe_container=True)
    incompatible = evaluate_compatibility(
        ItemClass.HOT_COOKED_MEAL,
        base_caps,
        handling,
        CompatibilityRequirements(
            item_allergens=frozenset({"Milk", "WHEAT"}),
            recipient_allergen_exclusions=frozenset({"milk"}),
        ),
        NOW,
    )
    unknown_profile = evaluate_compatibility(
        ItemClass.HOT_COOKED_MEAL,
        base_caps,
        handling,
        CompatibilityRequirements(
            recipient_allergen_exclusions=frozenset({"milk"}),
        ),
        NOW,
    )

    assert not incompatible.eligible
    assert incompatible.reasons == (ReasonCode.COMPATIBILITY_DIETARY_ALLERGEN,)
    assert "milk" in incompatible.explanation
    assert not unknown_profile.eligible
    assert unknown_profile.reasons == (ReasonCode.SAFETY_EVIDENCE_MISSING,)


def test_only_fully_eligible_alternatives_are_returned_in_stable_order():
    ready = destination(clean_water=True, cooking_fuel=True, cooking_facility=True)
    missing_fuel = destination(clean_water=True, cooking_facility=True)
    result = evaluate_compatibility(
        ItemClass.RAW_RATION_KIT,
        missing_fuel,
        rider(food_safe_container=True),
        CompatibilityRequirements(),
        NOW,
        alternatives=(
            CompatibilityAlternative("z-good", "Verified site Z", ready),
            CompatibilityAlternative("a-bad", "Fuel-unverified site A", missing_fuel),
            CompatibilityAlternative("b-good", "Verified site B", ready),
        ),
    )

    assert not result.eligible
    assert [(item.destination_id, item.label) for item in result.safe_alternatives] == [
        ("b-good", "Verified site B"),
        ("z-good", "Verified site Z"),
    ]


def test_naive_datetime_is_rejected_instead_of_assumed_utc():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_compatibility(
            ItemClass.HOT_COOKED_MEAL,
            destination(),
            rider(food_safe_container=True),
            CompatibilityRequirements(),
            datetime(2026, 10, 23, 12, 0),
        )


def test_unknown_item_class_is_rejected():
    with pytest.raises(ValueError, match="unsupported item class"):
        evaluate_compatibility(
            "UNRECOGNIZED",
            destination(),
            rider(),
            CompatibilityRequirements(),
            NOW,
        )
