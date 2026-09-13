"""Research Control Plane - Canonical read-only state for all research questions.

This module provides a unified view of the research portfolio by joining:
- The canonical 70-question registry (authority for question identity)
- Existing report artifacts (via report_resolver)
- V10 candidate lifecycle (read-only)
- Production application evidence (read-only)
- Canonical report truth/history (Phase 3)

The control plane does NOT:
- Run experiments
- Modify trading logic
- Mutate candidate lifecycle
- Invent research calculations

It is a read-only projection layer.
"""
from research_engine.control_plane.models import (
    ApplicationStatus,
    CandidateState,
    CanonicalReportRecord,
    QuestionState,
    ReadinessStatus,
    ReportValidity,
    RunnerStatus,
)
from research_engine.control_plane.evidence_resolver import EvidenceSnapshot
from research_engine.control_plane.invalidation import (
    InvalidationEntry,
    build_invalidations_dict,
    get_invalidations_for_dataset,
    get_invalidations_for_question,
    is_report_invalidated,
    record_invalidations_json,
)
from research_engine.control_plane.report_history import (
    AmbiguousReportError,
    resolve_report_history,
)
from research_engine.control_plane.state_builder import (
    build_all_question_states,
    build_question_state,
)
from research_engine.control_plane.operator_projection import build_operator_projection

__all__ = [
    "ApplicationStatus",
    "CandidateState",
    "CanonicalReportRecord",
    "InvalidationEntry",
    "QuestionState",
    "ReadinessStatus",
    "ReportValidity",
    "RunnerStatus",
    "EvidenceSnapshot",
    "AmbiguousReportError",
    "build_all_question_states",
    "build_operator_projection",
    "build_invalidations_dict",
    "build_question_state",
    "get_invalidations_for_dataset",
    "get_invalidations_for_question",
    "is_report_invalidated",
    "record_invalidations_json",
    "resolve_report_history",
]
