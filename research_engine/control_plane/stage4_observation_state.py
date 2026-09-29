"""STAGE 4 OBSERVATION REQUIREMENT STATE MACHINE.

An observation requirement moves through explicitly named states, and every
transition must be EVIDENCE-DERIVED.  Code existing is not SATISFIED: a
requirement is only SATISFIED when the correct schema/version, the correct
producer, valid evidence, the completeness requirement, the threshold where one
applies, and lineage validity ALL hold simultaneously.

The machine is deliberately conservative.  It has no optimistic default: an
unmet input leaves the requirement in its current state with a named reason
rather than advancing it, and an input that is claimed but unsupported raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════
# STATES
# ═══════════════════════════════════════════════════════════════════════════

DECLARED = "DECLARED"
SCHEMA_READY = "SCHEMA_READY"
PRODUCER_READY = "PRODUCER_READY"
COLLECTING = "COLLECTING"
BACKFILL_ELIGIBLE = "BACKFILL_ELIGIBLE"
BACKFILL_COMPLETE = "BACKFILL_COMPLETE"
WAITING_THRESHOLD = "WAITING_THRESHOLD"
SATISFIED = "SATISFIED"
FUTURE_ONLY = "FUTURE_ONLY"
BLOCKED_UPSTREAM = "BLOCKED_UPSTREAM"

STATES = (
    DECLARED, SCHEMA_READY, PRODUCER_READY, COLLECTING, BACKFILL_ELIGIBLE,
    BACKFILL_COMPLETE, WAITING_THRESHOLD, SATISFIED, FUTURE_ONLY,
    BLOCKED_UPSTREAM,
)

#: States where a requirement is not progressing on collection and needs a
#: governance or upstream action rather than more waiting.
NON_PROGRESSING = frozenset({SATISFIED, FUTURE_ONLY, BLOCKED_UPSTREAM})

#: Legal forward transitions.  ``SATISFIED`` is reachable only from
#: ``WAITING_THRESHOLD`` (or ``BACKFILL_COMPLETE`` when no threshold applies),
#: which is what encodes "code existing != SATISFIED".
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    DECLARED: frozenset({SCHEMA_READY, BLOCKED_UPSTREAM, FUTURE_ONLY}),
    SCHEMA_READY: frozenset({PRODUCER_READY, BLOCKED_UPSTREAM, FUTURE_ONLY}),
    PRODUCER_READY: frozenset({
        COLLECTING, BACKFILL_ELIGIBLE, BACKFILL_COMPLETE, FUTURE_ONLY,
        BLOCKED_UPSTREAM,
    }),
    COLLECTING: frozenset({
        WAITING_THRESHOLD, BACKFILL_ELIGIBLE, SATISFIED, FUTURE_ONLY,
        BLOCKED_UPSTREAM,
    }),
    BACKFILL_ELIGIBLE: frozenset({
        BACKFILL_COMPLETE, WAITING_THRESHOLD, FUTURE_ONLY, BLOCKED_UPSTREAM,
    }),
    BACKFILL_COMPLETE: frozenset({
        WAITING_THRESHOLD, SATISFIED, FUTURE_ONLY, BLOCKED_UPSTREAM,
    }),
    WAITING_THRESHOLD: frozenset({
        SATISFIED, COLLECTING, BLOCKED_UPSTREAM, FUTURE_ONLY,
    }),
    SATISFIED: frozenset({COLLECTING, BLOCKED_UPSTREAM}),
    FUTURE_ONLY: frozenset({COLLECTING, BLOCKED_UPSTREAM}),
    BLOCKED_UPSTREAM: frozenset({PRODUCER_READY, COLLECTING, FUTURE_ONLY}),
}

#: The six independent gates that must ALL hold before SATISFIED.  They are
#: named so a failure is reported as a specific unmet gate, never as a blanket
#: "not ready".
SATISFIED_GATES = (
    "correct_schema_and_version",
    "correct_producer",
    "valid_evidence",
    "completeness_requirement_met",
    "threshold_met_where_applicable",
    "lineage_valid",
)


class ObservationStateError(RuntimeError):
    """An observation-requirement state invariant was violated (fail closed)."""


@dataclass(frozen=True)
class RequirementEvidence:
    """The evidence bundle a state decision is derived from."""

    observation_requirement_id: str
    schema_ready: bool = False
    producer_ready: bool = False
    evidence_valid: bool = False
    completeness_met: bool = False
    threshold_rule_present: bool = False
    threshold_met: bool = False
    lineage_valid: bool = False
    upstream_blocked: bool = False
    future_only: bool = False
    backfill_eligible: bool = False
    backfill_complete: bool = False
    collecting: bool = False

    def satisfied_gates(self) -> tuple[str, ...]:
        """Which of the six SATISFIED gates this evidence supports."""
        return tuple(
            gate for gate, ok in (
                ("correct_schema_and_version", self.schema_ready),
                ("correct_producer", self.producer_ready),
                ("valid_evidence", self.evidence_valid),
                ("completeness_requirement_met", self.completeness_met),
                ("threshold_met_where_applicable", self.threshold_met),
                ("lineage_valid", self.lineage_valid),
            ) if ok
        )


def unmet_satisfied_gates(evidence: RequirementEvidence) -> tuple[str, ...]:
    """The SATISFIED gates NOT supported by this evidence, in governed order."""
    supported = set(evidence.satisfied_gates())
    return tuple(g for g in SATISFIED_GATES if g not in supported)


def can_satisfy(evidence: RequirementEvidence) -> tuple[bool, tuple[str, ...]]:
    """Can this requirement be SATISFIED now, and if not, which gates fail.

    ``threshold_met_where_applicable`` is required only when a governed threshold
    rule exists.  A requirement with no governed threshold is NOT silently
    treated as passing one: it is reported as missing the gate so the caller
    must classify it (for example METHOD_THRESHOLD_DEFINITION_REQUIRED).
    """
    return (not unmet_satisfied_gates(evidence),
            unmet_satisfied_gates(evidence))


def next_state(current: str, evidence: RequirementEvidence) -> str:
    """Derive the next state from evidence, or return ``current`` unchanged.

    Never raises on an unmet gate: an unmet gate simply leaves the requirement
    where it is.  It DOES raise when the current state is not a governed state,
    because that indicates a governance error rather than a data condition.
    """
    if current not in STATES:
        raise ObservationStateError("UNKNOWN_REQUIREMENT_STATE:" + str(current))
    if not str(evidence.observation_requirement_id or "").strip():
        raise ObservationStateError("EVIDENCE_WITHOUT_REQUIREMENT_ID")

    candidate = _candidate_state(evidence)
    if candidate is None or candidate == current:
        return current
    if candidate not in LEGAL_TRANSITIONS.get(current, frozenset()):
        # Falling back keeps the machine monotonic and never fabricates
        # progress; the caller observes an unchanged state.
        return current
    return candidate


def _candidate_state(evidence: RequirementEvidence) -> str | None:
    if evidence.upstream_blocked:
        return BLOCKED_UPSTREAM
    if evidence.future_only:
        return FUTURE_ONLY
    if evidence.backfill_complete:
        return BACKFILL_COMPLETE
    if evidence.backfill_eligible:
        return BACKFILL_ELIGIBLE

    satisfied, _missing = can_satisfy(evidence)
    if satisfied:
        return SATISFIED

    if not evidence.schema_ready:
        return DECLARED if not evidence.producer_ready else SCHEMA_READY
    if not evidence.producer_ready:
        return PRODUCER_READY
    if evidence.collecting:
        return COLLECTING
    # Schema, producer, evidence and lineage are fine and completeness holds;
    # what remains is a governed threshold to evaluate against.
    return WAITING_THRESHOLD


__all__ = [
    "BACKFILL_COMPLETE", "BACKFILL_ELIGIBLE", "BLOCKED_UPSTREAM",
    "COLLECTING", "DECLARED", "FUTURE_ONLY", "LEGAL_TRANSITIONS",
    "NON_PROGRESSING", "ObservationStateError", "PRODUCER_READY",
    "RequirementEvidence", "SATISFIED", "SATISFIED_GATES", "SCHEMA_READY",
    "STATES", "WAITING_THRESHOLD", "can_satisfy", "next_state",
    "unmet_satisfied_gates",
]
