"""
Candidate Registry — Lifecycle management.

Enforces valid status transitions. Invalid transitions raise errors.
"""

from __future__ import annotations

from research_engine.v10.candidates.models import CandidateStatus


# Valid transitions: from_status → set of allowed next statuses
_VALID_TRANSITIONS: dict[str, set[str]] = {
    CandidateStatus.PROPOSED: {
        CandidateStatus.VALIDATING,
        CandidateStatus.SHADOW_TESTING,  # Direct activation for pre-validated candidates
        CandidateStatus.ARCHIVED,
        CandidateStatus.REJECTED,
    },
    CandidateStatus.VALIDATING: {
        CandidateStatus.VALIDATED,
        CandidateStatus.FAILED_VALIDATION,
        CandidateStatus.REGRESSION_DETECTED,
        CandidateStatus.REJECTED,
        CandidateStatus.ARCHIVED,
    },
    CandidateStatus.VALIDATED: {
        CandidateStatus.SHADOW_TESTING,
        CandidateStatus.READY_FOR_REVIEW,
        CandidateStatus.ARCHIVED,
        CandidateStatus.REJECTED,
    },
    CandidateStatus.SHADOW_TESTING: {
        CandidateStatus.READY_FOR_REVIEW,
        CandidateStatus.REGRESSION_DETECTED,
        CandidateStatus.REJECTED,
        CandidateStatus.ARCHIVED,
    },
    CandidateStatus.READY_FOR_REVIEW: {
        CandidateStatus.ACCEPTED,
        CandidateStatus.REJECTED,
        CandidateStatus.ARCHIVED,
    },
    CandidateStatus.FAILED_VALIDATION: {
        CandidateStatus.VALIDATING,  # Allow retry
        CandidateStatus.ARCHIVED,
        CandidateStatus.REJECTED,
    },
    CandidateStatus.REGRESSION_DETECTED: {
        CandidateStatus.VALIDATING,  # Allow retry with modified params
        CandidateStatus.ARCHIVED,
        CandidateStatus.REJECTED,
    },
    # Terminal states
    CandidateStatus.ACCEPTED: {CandidateStatus.ARCHIVED},
    CandidateStatus.REJECTED: {CandidateStatus.ARCHIVED},
    CandidateStatus.ARCHIVED: set(),
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check if a status transition is allowed."""
    allowed = _VALID_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def validate_transition(from_status: str, to_status: str) -> None:
    """Validate a transition. Raises ValueError if invalid."""
    if not is_valid_transition(from_status, to_status):
        raise ValueError(
            f"Invalid status transition: {from_status} -> {to_status}. "
            f"Allowed: {sorted(_VALID_TRANSITIONS.get(from_status, set()))}"
        )


def is_terminal(status: str) -> bool:
    """Check if a status is terminal (no further transitions)."""
    return status in (CandidateStatus.ARCHIVED,)


def is_active(status: str) -> bool:
    """Check if a candidate is in an active (non-terminal) state."""
    return status not in (
        CandidateStatus.ACCEPTED,
        CandidateStatus.REJECTED,
        CandidateStatus.ARCHIVED,
    )
