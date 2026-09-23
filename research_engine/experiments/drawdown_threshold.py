"""Dedicated canonical R4 chronological drawdown-threshold runner."""
from __future__ import annotations

from typing import Any, Mapping

from research_engine.control_plane.risk_policy_evidence import RiskPolicyEvidence
from research_engine.experiments.risk_simulation_governed import (
    R4_SCHEMA_VERSION,
    analyse_r4,
    load_governed_risk_evidence,
    load_upstream_report,
    persist_report,
    validate_report,
)
from research_engine.registry.risk_policy_adjudication import (
    HALT_THRESHOLD_GRID_V1,
    R4_CONTRACT,
    REPORT_OWNERSHIP,
)

REPORT_FILENAME = REPORT_OWNERSHIP["R4"]


def run_drawdown_threshold(
    evidence: RiskPolicyEvidence | None = None,
    r3_report: Mapping[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    report = analyse_r4(
        evidence or load_governed_risk_evidence(),
        r3_report or load_upstream_report(REPORT_OWNERSHIP["R3"]),
    )
    if persist:
        persist_report(report, REPORT_FILENAME)
    return report


def validate_r4_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    valid, reason = validate_report(
        report, question_id="R4", schema_version=R4_SCHEMA_VERSION, contract=R4_CONTRACT,
    )
    if not valid or report.get("status") != "COMPLETE":
        return valid, reason
    overall = report.get("overall")
    if not isinstance(overall, Mapping):
        return False, "R4 output missing"
    grid = overall.get("threshold_grid")
    if not isinstance(grid, list) or tuple(item.get("threshold") for item in grid) != HALT_THRESHOLD_GRID_V1:
        return False, "R4 closed threshold grid mismatch"
    halt = overall.get("selected_halt_threshold")
    if halt not in HALT_THRESHOLD_GRID_V1 or overall.get("resume_threshold") != halt / 2.0:
        return False, "R4 halt/resume rule mismatch"
    if overall.get("runtime_effect") != "NONE":
        return False, "R4 runtime boundary mismatch"
    required = {
        "recovery_probability", "breach_episodes", "recovery_episodes",
        "mean_recovery_duration_lifecycles", "maximum_recovery_duration_lifecycles",
    }
    if any(not required.issubset(item) for item in grid):
        return False, "R4 required recovery outputs missing"
    return True, reason
