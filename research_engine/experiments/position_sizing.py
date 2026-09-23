"""Dedicated canonical R5 closed-vocabulary position-sizing runner."""
from __future__ import annotations

from typing import Any, Mapping

from research_engine.control_plane.risk_policy_evidence import RiskPolicyEvidence
from research_engine.experiments.risk_simulation_governed import (
    R5_SCHEMA_VERSION,
    analyse_r5,
    load_governed_risk_evidence,
    load_upstream_report,
    persist_report,
    validate_report,
)
from research_engine.registry.risk_policy_adjudication import (
    R5_CONTRACT,
    REPORT_OWNERSHIP,
    SIZING_MODELS_V1,
)

REPORT_FILENAME = REPORT_OWNERSHIP["R5"]


def run_position_sizing(
    evidence: RiskPolicyEvidence | None = None,
    r3_report: Mapping[str, Any] | None = None,
    r4_report: Mapping[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    report = analyse_r5(
        evidence or load_governed_risk_evidence(),
        r3_report or load_upstream_report(REPORT_OWNERSHIP["R3"]),
        r4_report or load_upstream_report(REPORT_OWNERSHIP["R4"]),
    )
    if persist:
        persist_report(report, REPORT_FILENAME)
    return report


def validate_r5_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    valid, reason = validate_report(
        report, question_id="R5", schema_version=R5_SCHEMA_VERSION, contract=R5_CONTRACT,
    )
    if not valid or report.get("status") != "COMPLETE":
        return valid, reason
    overall = report.get("overall")
    if not isinstance(overall, Mapping):
        return False, "R5 output missing"
    vocabulary = tuple(model["model_id"] for model in SIZING_MODELS_V1)
    if tuple(overall.get("model_vocabulary", ())) != vocabulary:
        return False, "R5 closed sizing vocabulary mismatch"
    models = overall.get("models")
    if not isinstance(models, list) or tuple(item.get("model_id") for item in models) != vocabulary:
        return False, "R5 model evaluation set mismatch"
    if overall.get("selection_objective") != R5_CONTRACT["objective"]:
        return False, "R5 geometric-growth objective mismatch"
    if any("sharpe_approx" in item or "terminal_equity" in item for item in models):
        return False, "R5 forbidden selection objective present"
    return True, reason
