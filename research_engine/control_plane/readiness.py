"""Derive canonical readiness from resolved evidence and dependency state."""
from __future__ import annotations

from typing import Any, Mapping

from research_engine.control_plane.evidence_resolver import EvidenceResolution
from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus


def resolve_readiness(
    question: Any,
    evidence: EvidenceResolution,
    runner_status: RunnerStatus,
    report_validity: ReportValidity,
    report_status: str,
    dependency_states: Mapping[str, str],
) -> tuple[ReadinessStatus, str]:
    """Apply readiness precedence without executing the experiment."""
    if runner_status == RunnerStatus.NO_RUNNER:
        return ReadinessStatus.NO_RUNNER, "No canonical runner is declared"
    if runner_status == RunnerStatus.ERROR:
        return ReadinessStatus.ERROR, "Declared runner cannot be resolved safely"

    for dependency in question.depends_on:
        state = dependency_states.get(dependency, "UNKNOWN")
        if state == "COMPLETE":
            continue
        if state == "ERROR":
            return ReadinessStatus.ERROR, f"Dependency {dependency} is ERROR"
        if state in ("INVALIDATED", "BLOCKED", "NO_RUNNER"):
            return ReadinessStatus.BLOCKED, f"Dependency {dependency} is {state}"
        if state == "UNKNOWN":
            return ReadinessStatus.UNKNOWN, f"Dependency {dependency} readiness is UNKNOWN"
        return ReadinessStatus.WAITING_DATA, f"Dependency {dependency} is not COMPLETE ({state})"

    if report_validity == ReportValidity.VALID_CURRENT and report_status == "COMPLETE":
        return ReadinessStatus.COMPLETE, "A VALID_CURRENT completed report exists"
    if question.id in {"M1", "M3", "M7", "M8", "M11", "D2"} and report_validity == ReportValidity.VALID_CURRENT:
        if report_status == "WAITING_DATA":
            return ReadinessStatus.WAITING_DATA, "The CURRENT RW2 report has not met chronological/sample/cell sufficiency"
        if report_status == "BLOCKED":
            return ReadinessStatus.BLOCKED, "The CURRENT RW2 report could not establish required authority, chronology, or join integrity"

    if question.id in {"S5", "S6", "S7"} and report_validity == ReportValidity.VALID_CURRENT:
        # Implementation-complete is distinct from scientific completion: a
        # A CURRENT S5/S6 report below the 100/30/20 distinct canonical-opportunity
        # gates stays WAITING_DATA, never a finding.
        if report_status == "INSUFFICIENT_DATA":
            return (
                ReadinessStatus.WAITING_DATA,
                (
                    f"The CURRENT {question.id} report has not met the 150/30/minimum-2x2 "
                    "distinct canonical-opportunity interaction gates"
                    if question.id == "S7" else
                    f"The CURRENT {question.id} report has not met the 100/30/20 distinct "
                    "canonical-opportunity sufficiency gates"
                ),
            )
        if report_status == "BLOCKED":
            return (
                ReadinessStatus.BLOCKED,
                f"The CURRENT {question.id} report could not establish required evidence authority",
            )

    if evidence.error:
        missing = all(
            requirement.type != "dataset_presence"
            or requirement.satisfied is True
            or "not supplied" in requirement.reason.lower()
            for requirement in evidence.requirements
        )
        if "not supplied" in evidence.error.lower() and missing:
            return ReadinessStatus.BLOCKED, f"Required dataset unavailable: {evidence.error}"
        return ReadinessStatus.ERROR, f"Evidence resolution failed: {evidence.error}"

    unresolved = [item for item in evidence.requirements if item.satisfied is None]
    if unresolved:
        names = ", ".join(item.name for item in unresolved)
        return ReadinessStatus.UNKNOWN, f"Unresolved evidence requirement(s): {names}"

    blocking = [item for item in evidence.requirements if item.satisfied is False and item.blocking]
    if blocking:
        first = blocking[0]
        return ReadinessStatus.BLOCKED, first.reason

    unmet = [item for item in evidence.requirements if item.satisfied is False]
    if unmet:
        first = unmet[0]
        if first.type == "required_field" and evidence.metrics.get("total_current_population", 0) > 0:
            return ReadinessStatus.BLOCKED, first.reason
        return ReadinessStatus.WAITING_DATA, first.reason

    if evidence.usable_count == 0:
        return ReadinessStatus.WAITING_DATA, "No eligible CURRENT records"
    if evidence.usable_count is None:
        return ReadinessStatus.UNKNOWN, "Usable CURRENT population cannot be resolved"
    return ReadinessStatus.READY, f"All declared requirements pass on {evidence.usable_count} eligible CURRENT records"
