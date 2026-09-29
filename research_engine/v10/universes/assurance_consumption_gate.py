"""Stage IV Wave 4 → Q71+ hypothesis-generation consumption gate.

Future generated research questions (Q71+) must never consume an unverified
Q1–Q70 result as if it were trusted scientific truth.  This module is the
smallest governed gate that sits between question assurance and hypothesis
generation.

The gate separates two independent axes:

``assurance_state``
    The Stage IV assurance verdict.  Only ``VERIFIED`` may be handed
    downstream.  ``INDETERMINATE``, ``INVALIDATED`` and ``LEGACY`` never may.

``scientific_state``
    The meaning carried *underneath* a ``VERIFIED`` verdict.  A verified
    ``WAITING_DATA`` result may drive evidence-collection or observability
    hypotheses, but its unresolved scientific claim must never be asserted as
    true.

This module is read-only: it inspects assurance output and raises.  It never
requalifies a question, never rewrites a result, and never reaches into
Wave 2 or Wave 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


ASSURANCE_VERIFIED = "VERIFIED"

#: The only assurance verdict a downstream engine may consume.
ALLOWED_ASSURANCE_STATES = frozenset({ASSURANCE_VERIFIED})

#: Assurance verdicts that may never be consumed as scientific truth.
FORBIDDEN_ASSURANCE_STATES = frozenset({
    "INDETERMINATE", "INVALIDATED", "LEGACY", "PARTIAL", "DEGRADED", "UNAVAILABLE",
    "", "MISSING", "UNKNOWN",
})

#: Scientific states that may sit underneath a VERIFIED verdict.
ALLOWED_SCIENTIFIC_STATES = frozenset({
    "COMPLETE",
    "NEGATIVE_RESULT",
    "NO_EFFECT",
    "INSUFFICIENT_DATA",
    "WAITING_DATA",
    "HISTORICALLY_UNANSWERABLE",
})

#: Scientific meaning may be used for these hypothesis classes only when the
#: underlying claim is itself settled.
SETTLED_SCIENTIFIC_STATES = frozenset({
    "COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT",
})

#: Unsettled-but-verified states may only drive evidence work, never a claim.
EVIDENCE_COLLECTING_SCIENTIFIC_STATES = frozenset({
    "INSUFFICIENT_DATA", "WAITING_DATA", "HISTORICALLY_UNANSWERABLE",
})


class UnverifiedAssuranceConsumption(RuntimeError):
    """A downstream engine tried to consume an unverified question result."""


@dataclass(frozen=True)
class GateDecision:
    question_id: str
    assurance_state: str
    scientific_state: str
    admitted: bool
    scientific_truth_consumable: bool
    permitted_hypothesis_classes: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "assurance_state": self.assurance_state,
            "scientific_state": self.scientific_state,
            "admitted": self.admitted,
            "scientific_truth_consumable": self.scientific_truth_consumable,
            "permitted_hypothesis_classes": list(self.permitted_hypothesis_classes),
            "reason": self.reason,
        }


def admit(
    *,
    question_id: str,
    assurance_state: str,
    scientific_state: str,
) -> GateDecision:
    """Decide whether a Q1–Q70 result may be consumed downstream."""
    verdict = str(assurance_state or "").upper()
    science = str(scientific_state or "").upper()

    if verdict == ASSURANCE_VERIFIED:
        if science in SETTLED_SCIENTIFIC_STATES:
            return GateDecision(
                question_id=question_id,
                assurance_state=verdict,
                scientific_state=science,
                admitted=True,
                scientific_truth_consumable=True,
                permitted_hypothesis_classes=("OPTIMISATION", "INVESTIGATION"),
                reason="verified result with a settled scientific state",
            )
        if science in EVIDENCE_COLLECTING_SCIENTIFIC_STATES:
            return GateDecision(
                question_id=question_id,
                assurance_state=verdict,
                scientific_state=science,
                admitted=True,
                scientific_truth_consumable=False,
                permitted_hypothesis_classes=("EVIDENCE_COLLECTION", "OBSERVABILITY"),
                reason="verified but unresolved state may only drive evidence work",
            )
        return GateDecision(
            question_id=question_id,
            assurance_state=verdict,
            scientific_state=science,
            admitted=False,
            scientific_truth_consumable=False,
            permitted_hypothesis_classes=(),
            reason=f"scientific state {science!r} is not a governed downstream state",
        )

    return GateDecision(
        question_id=question_id,
        assurance_state=verdict,
        scientific_state=science,
        admitted=False,
        scientific_truth_consumable=False,
        permitted_hypothesis_classes=(),
        reason=f"assurance state {verdict!r} is not ASSURANCE VERIFIED",
    )


def assert_consumable(decision: GateDecision) -> GateDecision:
    """Fail closed when a downstream engine tries to consume the result.

    A result is only consumable when it is admitted and the underlying scientific
    state is settled enough to support a real conclusion.  Verified-but-unresolved
    evidence states may still be routed through the dedicated hypothesis source
    for evidence collection work, but they are not valid general-purpose
    consumption paths.
    """
    if not decision.admitted or not decision.scientific_truth_consumable:
        raise UnverifiedAssuranceConsumption(f"{decision.question_id}: {decision.reason}")
    return decision


def consume_scientific_truth(decision: GateDecision) -> GateDecision:
    """Fail closed when an unsettled claim is used as if it were a finding."""
    assert_consumable(decision)
    if not decision.scientific_truth_consumable:
        raise UnverifiedAssuranceConsumption(
            f"{decision.question_id}: verified {decision.scientific_state} state may not be "
            "consumed as a scientific conclusion"
        )
    return decision


def gate_hypothesis_source(
    *,
    question_id: str,
    assurance_state: str,
    scientific_state: str,
    claim_is_asserted: bool = False,
) -> GateDecision:
    """The single governed entry point for Q71+ hypothesis generation."""
    decision = admit(
        question_id=question_id,
        assurance_state=assurance_state,
        scientific_state=scientific_state,
    )
    if claim_is_asserted:
        return consume_scientific_truth(decision)
    if decision.admitted and not decision.scientific_truth_consumable:
        return decision
    return assert_consumable(decision)


def decision_from_qualification(qualification: Mapping[str, Any]) -> GateDecision:
    """Build a gate decision from one persisted Wave 4 qualification record."""
    return admit(
        question_id=str(qualification.get("question_id", "")),
        assurance_state=str(qualification.get("qualification_status", "")),
        scientific_state=str(
            (qualification.get("downstream_gate") or {}).get("scientific_state", "")
            or qualification.get("existing_research_state", "")
        ),
    )


def unverified_consumable_paths(
    qualifications: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Question ids a downstream path could wrongly treat as scientific truth.

    A qualification may only be consumable when it is VERIFIED *and* it says so
    itself.  Anything that self-declares consumability while unverified is a
    live Q71+ leak and must be reported, never suppressed.
    """
    leaks: list[str] = []
    for item in qualifications:
        gate = item.get("downstream_gate") or {}
        if not gate:
            continue
        if not gate.get("consumable_as_scientific_truth"):
            continue
        if str(item.get("qualification_status", "")).upper() != ASSURANCE_VERIFIED:
            leaks.append(str(item.get("question_id", "")))
        elif gate.get("scientific_state") not in SETTLED_SCIENTIFIC_STATES:
            leaks.append(str(item.get("question_id", "")))
    return tuple(sorted(leaks))


def gate_report(qualifications: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Persistable summary of the Q71+ gate over a Wave 4 report."""
    decisions = [decision_from_qualification(item) for item in qualifications]
    return {
        "gate": "ASSURANCE_VERIFIED_ONLY",
        "question_count": len(qualifications),
        "admitted_count": sum(1 for item in decisions if item.admitted),
        "scientific_truth_consumable_count": sum(
            1 for item in decisions if item.scientific_truth_consumable
        ),
        "unverified_consumable_paths": list(unverified_consumable_paths(qualifications)),
        "forbidden_assurance_states": sorted(FORBIDDEN_ASSURANCE_STATES),
        "allowed_scientific_states": sorted(ALLOWED_SCIENTIFIC_STATES),
    }


__all__ = [
    "ALLOWED_ASSURANCE_STATES",
    "ALLOWED_SCIENTIFIC_STATES",
    "ASSURANCE_VERIFIED",
    "EVIDENCE_COLLECTING_SCIENTIFIC_STATES",
    "FORBIDDEN_ASSURANCE_STATES",
    "GateDecision",
    "SETTLED_SCIENTIFIC_STATES",
    "UnverifiedAssuranceConsumption",
    "admit",
    "assert_consumable",
    "consume_scientific_truth",
    "decision_from_qualification",
    "gate_hypothesis_source",
    "gate_report",
    "unverified_consumable_paths",
]


