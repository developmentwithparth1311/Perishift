"""Pure, fail-closed compatibility rules for aid and destination snapshots.

The YAML file is stored as JSON-compatible YAML so the backend can load its
small immutable ruleset with the standard library and without a runtime YAML
dependency. No database or network access occurs in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from app.contracts.enums import ItemClass, ReasonCode


_RULES_PATH = Path(__file__).resolve().parents[1] / "rules" / "compatibility.yaml"
_FORMULA_FORMS = frozenset({"POWDER", "READY_TO_FEED"})


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """A capability assertion and the time at which it was verified.

    ``available=None`` means the value is unknown. Multiple evidence records
    for one capability can be supplied to detect contradictory current claims.
    """

    available: bool | None
    verified_at: datetime | None
    valid_until: datetime | None = None
    conflicting_values: tuple[bool, ...] = ()


EvidenceValue = CapabilityEvidence | Sequence[CapabilityEvidence]


@dataclass(frozen=True, slots=True)
class DestinationCapabilities:
    capabilities: Mapping[str, EvidenceValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiderHandlingFeatures:
    capabilities: Mapping[str, EvidenceValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompatibilityRequirements:
    """Requirements declared for the item and for its intended recipients.

    ``item_allergens`` and ``item_dietary_tags`` must be known when recipient
    constraints are supplied. A missing profile is not interpreted as safe.
    Formula form is mandatory for formula batches and forbidden for other
    canonical item classes.
    """

    formula_form: str | None = None
    item_allergens: frozenset[str] | None = None
    recipient_allergen_exclusions: frozenset[str] = frozenset()
    item_dietary_tags: frozenset[str] | None = None
    recipient_required_dietary_tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CompatibilityAlternative:
    destination_id: str
    label: str
    destination: DestinationCapabilities


@dataclass(frozen=True, slots=True)
class SafeAlternative:
    destination_id: str
    label: str


@dataclass(frozen=True, slots=True)
class CompatibilityFinding:
    code: str
    capability: str | None
    message: str
    reason: ReasonCode


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    eligible: bool
    reasons: tuple[ReasonCode, ...]
    explanation: str
    findings: tuple[CompatibilityFinding, ...]
    safe_alternatives: tuple[SafeAlternative, ...] = ()


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, object]:
    try:
        rules = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load compatibility rules at {_RULES_PATH}") from exc
    if rules.get("schema_version") != 1 or not isinstance(rules.get("items"), dict):
        raise RuntimeError("compatibility rules have an unsupported schema")
    return rules


def evaluate_compatibility(
    item_class: ItemClass | str,
    destination: DestinationCapabilities,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    now: datetime,
    alternatives: Iterable[CompatibilityAlternative] = (),
) -> CompatibilityDecision:
    """Evaluate hard constraints deterministically using a single UTC instant.

    Missing, stale, future-dated, unknown, and conflicting safety evidence is
    never treated as a positive capability. Alternatives are included only if
    the same complete hard-constraint evaluation passes for their destination.
    """

    try:
        canonical_class = ItemClass(item_class)
    except ValueError as exc:
        raise ValueError(f"unsupported item class: {item_class!r}") from exc
    now_utc = _aware_utc(now, "now")
    rule = _item_rule(canonical_class)
    findings: list[CompatibilityFinding] = []

    formula_form: str | None = None
    if canonical_class is ItemClass.POWDER_FORMULA:
        formula_form = _normalize_formula_form(requirements.formula_form)
        if formula_form is None:
            findings.append(
                _finding(
                    "FORMULA_FORM_UNKNOWN",
                    None,
                    "Formula form is unknown; verify whether the product is powder or ready-to-feed before dispatch.",
                    ReasonCode.SAFETY_EVIDENCE_MISSING,
                )
            )
    elif requirements.formula_form is not None:
        findings.append(
            _finding(
                "FORMULA_FORM_NOT_APPLICABLE",
                None,
                "Formula form was supplied for a non-formula item.",
                ReasonCode.SAFETY_EVIDENCE_MISSING,
            )
        )

    destination_keys = list(rule.get("destination_all", []))
    by_form = rule.get("destination_by_form", {})
    if isinstance(by_form, dict) and formula_form is not None:
        destination_keys.extend(by_form.get(formula_form, []))
    rider_keys = list(rule.get("rider_all", []))
    reason_map = rule.get("reason_by_capability", {})
    max_age = int(_load_rules()["verification_max_age_seconds"])

    for capability in destination_keys:
        _check_capability(
            findings,
            capability,
            destination.capabilities.get(capability),
            now_utc,
            max_age,
            _reason(reason_map, capability),
            "destination",
        )
    for capability in rider_keys:
        _check_capability(
            findings,
            capability,
            rider.capabilities.get(capability),
            now_utc,
            max_age,
            _reason(reason_map, capability),
            "rider",
        )

    _check_dietary_constraints(findings, requirements)
    eligible = not findings
    safe_alternatives: tuple[SafeAlternative, ...] = ()
    if not eligible:
        safe_alternatives = _eligible_alternatives(
            canonical_class,
            rider,
            requirements,
            now_utc,
            alternatives,
        )
    reasons = tuple(dict.fromkeys(finding.reason for finding in findings))
    explanation = (
        "Compatible: all required destination, handling, and dietary checks pass."
        if eligible
        else "Blocked: " + " ".join(finding.message for finding in findings)
    )
    return CompatibilityDecision(
        eligible=eligible,
        reasons=reasons,
        explanation=explanation,
        findings=tuple(findings),
        safe_alternatives=safe_alternatives,
    )


def _eligible_alternatives(
    item_class: ItemClass,
    rider: RiderHandlingFeatures,
    requirements: CompatibilityRequirements,
    now: datetime,
    alternatives: Iterable[CompatibilityAlternative],
) -> tuple[SafeAlternative, ...]:
    eligible: dict[str, SafeAlternative] = {}
    for candidate in alternatives:
        decision = evaluate_compatibility(
            item_class,
            candidate.destination,
            rider,
            requirements,
            now,
        )
        if decision.eligible:
            eligible[candidate.destination_id] = SafeAlternative(
                destination_id=candidate.destination_id,
                label=candidate.label,
            )
    return tuple(eligible[key] for key in sorted(eligible))


def _item_rule(item_class: ItemClass) -> dict[str, object]:
    rules = _load_rules()["items"]
    rule = rules.get(item_class.value)  # type: ignore[union-attr]
    if not isinstance(rule, dict):
        raise RuntimeError(f"no compatibility rules defined for {item_class.value}")
    return rule


def _check_capability(
    findings: list[CompatibilityFinding],
    capability: str,
    raw_evidence: EvidenceValue | None,
    now: datetime,
    max_age: int,
    missing_reason: ReasonCode,
    owner: str,
) -> None:
    records = _evidence_records(raw_evidence)
    if not records:
        findings.append(
            _missing_finding(capability, missing_reason, owner, "evidence is missing")
        )
        return

    fresh: list[CapabilityEvidence] = []
    for evidence in records:
        try:
            verified_at = _aware_utc(evidence.verified_at, "verified_at")
            valid_until = (
                _aware_utc(evidence.valid_until, "valid_until")
                if evidence.valid_until is not None
                else None
            )
        except ValueError:
            continue
        age_seconds = (now - verified_at).total_seconds()
        if age_seconds < 0 or age_seconds > max_age:
            continue
        if valid_until is not None and now >= valid_until:
            continue
        if evidence.available is None or not isinstance(evidence.available, bool):
            continue
        fresh.append(evidence)

    declared_conflicts = any(record.conflicting_values for record in records)
    distinct_current_values = {record.available for record in fresh}
    if declared_conflicts or len(distinct_current_values) > 1:
        findings.append(
            _finding(
                "CAPABILITY_EVIDENCE_CONFLICT",
                capability,
                f"{owner.capitalize()} has conflicting verified {capability.replace('_', ' ')} evidence; verify the capability before dispatch.",
                ReasonCode.SAFETY_EVIDENCE_MISSING,
            )
        )
        return
    if not fresh:
        findings.append(
            _missing_finding(
                capability,
                missing_reason,
                owner,
                "evidence is unknown, stale, expired, or has an invalid timestamp",
            )
        )
        return
    if fresh[0].available is not True:
        findings.append(
            _missing_finding(capability, missing_reason, owner, "capability is not verified as available")
        )


def _check_dietary_constraints(
    findings: list[CompatibilityFinding], requirements: CompatibilityRequirements
) -> None:
    has_recipient_constraints = bool(
        requirements.recipient_allergen_exclusions
        or requirements.recipient_required_dietary_tags
    )
    if requirements.recipient_allergen_exclusions:
        if requirements.item_allergens is None:
            findings.append(
                _finding(
                    "ALLERGEN_PROFILE_UNKNOWN",
                    "item_allergens",
                    "Recipient allergy constraints exist but the item allergen profile is unknown; verify the item before dispatch.",
                    ReasonCode.SAFETY_EVIDENCE_MISSING,
                )
            )
        else:
            conflicts = sorted(
                _normalized_set(requirements.item_allergens)
                & _normalized_set(requirements.recipient_allergen_exclusions)
            )
            if conflicts:
                findings.append(
                    _finding(
                        "DIETARY_ALLERGEN_CONFLICT",
                        "item_allergens",
                        "Item allergen profile conflicts with recipient exclusions: "
                        + ", ".join(conflicts)
                        + ".",
                        ReasonCode.COMPATIBILITY_DIETARY_ALLERGEN,
                    )
                )
    if requirements.recipient_required_dietary_tags:
        if requirements.item_dietary_tags is None:
            findings.append(
                _finding(
                    "DIETARY_PROFILE_UNKNOWN",
                    "item_dietary_tags",
                    "Recipient dietary requirements exist but the item dietary profile is unknown; verify the item before dispatch.",
                    ReasonCode.SAFETY_EVIDENCE_MISSING,
                )
            )
        else:
            missing_tags = sorted(
                _normalized_set(requirements.recipient_required_dietary_tags)
                - _normalized_set(requirements.item_dietary_tags)
            )
            if missing_tags:
                findings.append(
                    _finding(
                        "DIETARY_REQUIREMENT_MISMATCH",
                        "item_dietary_tags",
                        "Item is not verified for required dietary tag(s): "
                        + ", ".join(missing_tags)
                        + ".",
                        ReasonCode.COMPATIBILITY_DIETARY_ALLERGEN,
                    )
                )
    elif has_recipient_constraints and requirements.item_allergens is None:
        # Kept explicit for future constraint kinds without treating unknown as safe.
        findings.append(
            _finding(
                "DIETARY_PROFILE_UNKNOWN",
                "item_allergens",
                "Item dietary profile is unknown; verify it before dispatch.",
                ReasonCode.SAFETY_EVIDENCE_MISSING,
            )
        )


def _evidence_records(value: EvidenceValue | None) -> tuple[CapabilityEvidence, ...]:
    if value is None:
        return ()
    if isinstance(value, CapabilityEvidence):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if all(isinstance(record, CapabilityEvidence) for record in value):
            return tuple(value)
    return ()


def _missing_finding(
    capability: str,
    reason: ReasonCode,
    owner: str,
    detail: str,
) -> CompatibilityFinding:
    return _finding(
        "CAPABILITY_NOT_VERIFIED",
        capability,
        f"{owner.capitalize()} lacks verified {capability.replace('_', ' ')} ({detail}); dispatch is blocked until verified.",
        reason,
    )


def _finding(
    code: str, capability: str | None, message: str, reason: ReasonCode
) -> CompatibilityFinding:
    return CompatibilityFinding(code, capability, message, reason)


def _reason(mapping: object, capability: str) -> ReasonCode:
    if isinstance(mapping, dict):
        raw = mapping.get(capability, "SAFETY_EVIDENCE_MISSING")
        try:
            return ReasonCode(raw)
        except ValueError:
            pass
    return ReasonCode.SAFETY_EVIDENCE_MISSING


def _normalize_formula_form(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("-", "_").replace(" ", "_").upper()
    aliases = {"READY_TO_FEED": "READY_TO_FEED", "POWDER": "POWDER"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _FORMULA_FORMS else None


def _normalized_set(values: Iterable[str]) -> set[str]:
    return {value.strip().casefold() for value in values}


def _aware_utc(value: datetime | None, name: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)
