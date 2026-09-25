"""Frozen HD15 governance for canonical G3; no operational wiring.

This module defines the future nonrecursive global research-validity
instrument.  It deliberately does not discover evidence, execute G1/G2/L6,
write a report, alter readiness, or authorize production behaviour.
"""
from __future__ import annotations

from typing import Final


HD15_VERSION: Final = "hd15_g3_global_research_validity_v1"

ASSESSMENT_POPULATION: Final = (
    "Exactly the 69 canonical identities in research_question_registry.REGISTRY "
    "other than G3, once each. G3 is never a population member. No identity may "
    "be omitted because it is WAITING_DATA, BLOCKED, non-operational, negative, "
    "insufficient, or lacks a CURRENT report."
)

QUESTION_TRUST_STATES: Final = (
    "TRUSTED_CURRENT_CONCLUSION",
    "WAITING_DATA",
    "BLOCKED",
    "NON_OPERATIONAL",
    "REPORT_NOT_VALID_CURRENT",
    "UNKNOWN",
)

INPUT_AUTHORITIES: Final = {
    "canonical_question_identity": (
        "research_engine.registry.research_question_registry.REGISTRY"
    ),
    "structural_operational_state": (
        "research_engine.registry.master_repair_ledger.MASTER_REPAIR_LEDGER "
        "and its derived 18-gate structurally_operational state"
    ),
    "report_ownership": (
        "the registry-declared report_filename plus "
        "research_engine.control_plane.report_ownership canonical ownership"
    ),
    "report_validity": (
        "research_engine.control_plane.report_resolver.resolve_report_validity"
    ),
    "current_validity": (
        "research_engine.control_plane.models.ReportValidity.VALID_CURRENT only"
    ),
    "readiness_and_completion": (
        "the immutable QuestionState produced by the canonical control-plane "
        "state builder, its ReadinessStatus, the question completion contract, "
        "and authoritative report status"
    ),
    "evidence_and_sample_sufficiency": (
        "the question's frozen definition requirements/completion rule and the "
        "same-snapshot canonical readiness/evidence accounting; never raw row, "
        "trade, file, dashboard, R-multiple, or pattern counts"
    ),
    "G1": "owned VALID_CURRENT g1_dataset_suitability.json under HD13",
    "G2": "owned VALID_CURRENT g2_lineage_coverage.json under HD14",
    "L6": (
        "owned VALID_CURRENT l6_learning_cycle_validation.json satisfying frozen "
        "HD12; until it exists, record MISSING_REQUIRED_DEPENDENCY"
    ),
}

CLASSIFICATION_PRECEDENCE: Final = (
    "UNKNOWN first when required canonical authority is absent, contradictory, "
    "corrupt, or cannot deterministically classify the identity; then an explicit "
    "canonical WAITING_DATA; then an explicit canonical BLOCKED; then "
    "NON_OPERATIONAL when the runner/structural implementation is absent and no "
    "more-specific governed WAITING_DATA or BLOCKED state exists; then "
    "TRUSTED_CURRENT_CONCLUSION only when the question is structurally operational, "
    "has its uniquely owned VALID_CURRENT COMPLETE report, satisfies its frozen "
    "evidence/sample/completion contract, and has a determinable conclusion; all "
    "remaining missing, stale, legacy, superseded, invalidated, non-CURRENT, or "
    "non-complete report cases are REPORT_NOT_VALID_CURRENT. Every identity gets "
    "exactly one state."
)

NEGATIVE_FINDING_SEMANTICS: Final = (
    "Finding direction or favourability is not a trust gate. A valid COMPLETE "
    "negative edge, no reliable effect, threshold-not-met, unsuitable-dataset, "
    "null, adverse, or other negative conclusion is TRUSTED_CURRENT_CONCLUSION "
    "when the same validity, ownership, CURRENT, and sufficiency gates pass."
)

G1_ROLE: Final = (
    "G1 supplies the exhaustive HD13 dataset-suitability result for the bound "
    "snapshot. G3 consumes, but never reruns or reinterprets, its owned "
    "VALID_CURRENT report. Overall positive trust requires G1 overall SUITABLE; "
    "a valid negative G1 result instead deterministically prevents positive trust."
)
G2_ROLE: Final = (
    "G2 supplies the exhaustive HD14 lineage result for the bound snapshot. G3 "
    "consumes, but never reruns or reinterprets, its owned VALID_CURRENT report. "
    "Overall positive trust requires a COMPLETE LINEAGE_THRESHOLD_MET result; a "
    "valid LINEAGE_THRESHOLD_NOT_MET result prevents positive trust."
)
L6_DEPENDENCY_DECISION: Final = "B"
L6_ROLE: Final = (
    "Option B is frozen. G3 may execute and COMPLETE a valid "
    "RESEARCH_TRUST_NOT_YET_DEMONSTRATED result while L6 is unavailable, provided "
    "the canonical absence is explicitly represented as MISSING_REQUIRED_DEPENDENCY. "
    "Overall positive trust requires an owned VALID_CURRENT COMPLETE L6 report "
    "satisfying HD12 and its required per-conclusion learning-cycle result. G3 "
    "never invents, reconstructs, defaults, or recursively invokes L6."
)

RESULT_VOCABULARY: Final = (
    "RESEARCH_TRUST_DEMONSTRATED",
    "RESEARCH_TRUST_NOT_YET_DEMONSTRATED",
    "EVALUATION_BLOCKED",
    "EVALUATION_UNKNOWN",
)

GLOBAL_TRUST_RULE: Final = (
    "Categorical and fail-closed; no scalar, weighting, points, or score. Emit "
    "RESEARCH_TRUST_DEMONSTRATED iff all 69 question states are "
    "TRUSTED_CURRENT_CONCLUSION, G1 is owned VALID_CURRENT COMPLETE and SUITABLE, "
    "G2 is owned VALID_CURRENT COMPLETE and LINEAGE_THRESHOLD_MET, and L6 is owned "
    "VALID_CURRENT COMPLETE under HD12 with every required learning-cycle trust "
    "condition satisfied. If the evaluation authority is valid and exhaustive but "
    "any question is WAITING_DATA, BLOCKED, NON_OPERATIONAL, or "
    "REPORT_NOT_VALID_CURRENT, or a represented G1/G2/L6 requirement is validly "
    "negative or missing (including unavailable L6), emit "
    "RESEARCH_TRUST_NOT_YET_DEMONSTRATED."
)

WAITING_DATA_SEMANTICS: Final = (
    "WAITING_DATA is a legitimate explicit question state. It prevents "
    "RESEARCH_TRUST_DEMONSTRATED but does not prevent G3 completion when its "
    "classification and provenance are determinate."
)
BLOCKED_SEMANTICS: Final = (
    "A determinately BLOCKED population question prevents "
    "RESEARCH_TRUST_DEMONSTRATED but does not prevent G3 completion. This is "
    "distinct from EVALUATION_BLOCKED, where G3's own mandatory snapshot, "
    "provenance, identity, ownership, or aggregation authority is invalid or "
    "missing and G3 cannot COMPLETE."
)
UNKNOWN_SEMANTICS: Final = (
    "UNKNOWN is never coerced to WAITING_DATA, BLOCKED, zero, valid, or trusted. "
    "Any UNKNOWN question or mandatory input yields EVALUATION_UNKNOWN and "
    "prevents G3 COMPLETE until canonical authority resolves it."
)

SNAPSHOT_CONTRACT: Final = (
    "Freeze one immutable validity-approved CURRENT snapshot once and atomically "
    "for the entire assessment. All question states and G1/G2/L6 dependency "
    "records must bind to exactly that snapshot identity and compatible epoch. "
    "The canonical provenance manifest records snapshot identity and as-of time, "
    "HD15/registry/definition/contract versions and digests, exactly 69 ordered "
    "non-G3 canonical identities and states, G1/G2/L6 canonical identities and "
    "availability/result states, canonical report owner/filename/status/validity/"
    "evidence epoch/content digest where a report exists, included/excluded "
    "accounting, and duplicate-preserving component digests. Compute the snapshot "
    "digest as SHA-256 over a canonical UTF-8 JSON serialization with recursively "
    "sorted object keys, preserved array order, and no insignificant whitespace. "
    "Reject mixed epochs, duplicate/missing identities, self-reference, mutable "
    "inputs, digest mismatch, or provenance mismatch."
)
NONRECURSION: Final = (
    "G3 never reads a prior G3 report to classify G3, never includes G3 in the 69, "
    "never uses dashboard-derived G3 state, never invokes G1/G2/L6, never "
    "reinterprets underlying evidence, and never mutates question state, runtime, "
    "configuration, Data Collection, production, or history."
)

COMPLETION_SEMANTICS: Final = (
    "Future G3 COMPLETE requires exactly 69 unique non-self question states, a "
    "valid immutable same-snapshot provenance manifest and deterministic digest, "
    "all mandatory G1/G2/L6 inputs represented (an authoritative recorded L6 "
    "absence is representation), deterministic categorical aggregation, zero "
    "UNKNOWN, no G3-level blocking authority defect, and an owned VALID_CURRENT "
    "g3_research_validity.json whose content validates this contract. Report "
    "existence or self-declared status never completes G3. "
    "RESEARCH_TRUST_NOT_YET_DEMONSTRATED may COMPLETE; EVALUATION_BLOCKED and "
    "EVALUATION_UNKNOWN never COMPLETE."
)

FUTURE_IMPLEMENTATION: Final = {
    "module": "research_engine.experiments.research_validity",
    "function": "run_g3",
    "report": "g3_research_validity.json",
    "owner": "G3",
}

