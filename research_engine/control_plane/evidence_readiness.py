"""Mechanical readiness for governed evidence populations.

This module compares evidence counts with caller-supplied requirements.  It
does not know research-question IDs, estimands, reports, or scientific result
semantics.
"""
from __future__ import annotations

from dataclasses import dataclass


READY = "READY"
WAITING_DATA = "WAITING_DATA"


@dataclass(frozen=True)
class EvidenceRequirement:
    """Minimum mechanical evidence gates supplied by a scientific caller."""

    minimum_valid_count: int
    minimum_distinct_count: int = 0
    minimum_coverage: float = 0.0

    def __post_init__(self) -> None:
        if self.minimum_valid_count < 0 or self.minimum_distinct_count < 0:
            raise ValueError("Evidence count requirements must be non-negative")
        if not 0.0 <= self.minimum_coverage <= 1.0:
            raise ValueError("minimum_coverage must be between 0 and 1")


@dataclass(frozen=True)
class EvidenceReadiness:
    """A non-scientific count/coverage readiness result."""

    state: str
    current_valid_count: int
    required_valid_count: int
    remaining_valid_count: int
    current_distinct_count: int
    required_distinct_count: int
    remaining_distinct_count: int
    current_coverage: float
    required_coverage: float
    valid_count_gate_satisfied: bool
    distinct_count_gate_satisfied: bool
    coverage_gate_satisfied: bool
    blockers: tuple[str, ...]

    @property
    def all_gates_satisfied(self) -> bool:
        return not self.blockers


def evaluate_evidence_readiness(
    *,
    total_count: int,
    valid_count: int,
    distinct_valid_count: int,
    requirement: EvidenceRequirement,
) -> EvidenceReadiness:
    """Compare one governed population with caller-supplied evidence gates."""
    if min(total_count, valid_count, distinct_valid_count) < 0:
        raise ValueError("Evidence population counts must be non-negative")
    if valid_count > total_count:
        raise ValueError("valid_count cannot exceed total_count")
    if distinct_valid_count > valid_count:
        raise ValueError("distinct_valid_count cannot exceed valid_count")

    coverage = valid_count / total_count if total_count else 0.0
    valid_ok = valid_count >= requirement.minimum_valid_count
    distinct_ok = distinct_valid_count >= requirement.minimum_distinct_count
    coverage_ok = coverage >= requirement.minimum_coverage
    blockers: list[str] = []
    if not valid_ok:
        blockers.append("VALID_COUNT_BELOW_REQUIRED")
    if not distinct_ok:
        blockers.append("DISTINCT_COUNT_BELOW_REQUIRED")
    if not coverage_ok:
        blockers.append("COVERAGE_BELOW_REQUIRED")

    return EvidenceReadiness(
        state=READY if not blockers else WAITING_DATA,
        current_valid_count=valid_count,
        required_valid_count=requirement.minimum_valid_count,
        remaining_valid_count=max(0, requirement.minimum_valid_count - valid_count),
        current_distinct_count=distinct_valid_count,
        required_distinct_count=requirement.minimum_distinct_count,
        remaining_distinct_count=max(
            0, requirement.minimum_distinct_count - distinct_valid_count,
        ),
        current_coverage=coverage,
        required_coverage=requirement.minimum_coverage,
        valid_count_gate_satisfied=valid_ok,
        distinct_count_gate_satisfied=distinct_ok,
        coverage_gate_satisfied=coverage_ok,
        blockers=tuple(blockers),
    )
