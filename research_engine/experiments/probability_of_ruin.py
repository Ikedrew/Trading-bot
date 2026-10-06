"""Dedicated canonical R3 runner for HD10 ``ruin_model_v1``."""
from __future__ import annotations

from typing import Any, Mapping

from research_engine.control_plane.risk_policy_evidence import RiskPolicyEvidence
from research_engine.experiments.risk_simulation_governed import (
    R3_SCHEMA_VERSION,
    RiskSimulationError,
    analyse_r3,
    persist_report,
    validate_report,
)
from research_engine.registry.risk_policy_adjudication import (
    R3_CONTRACT,
    REPORT_OWNERSHIP,
    RUIN_MODEL_V1,
)

REPORT_FILENAME = REPORT_OWNERSHIP["R3"]


def run_probability_of_ruin(
    evidence: RiskPolicyEvidence | None = None,
    *,
    governed_risk_evidence: RiskPolicyEvidence | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    evidence = governed_risk_evidence or evidence
    if evidence is None:
        raise RiskSimulationError("GOVERNED_RISK_EVIDENCE_REQUIRED:R3")
    report = analyse_r3(evidence)
    if persist:
        persist_report(report, REPORT_FILENAME)
    return report


def validate_r3_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    valid, reason = validate_report(
        report, question_id="R3", schema_version=R3_SCHEMA_VERSION, contract=R3_CONTRACT,
    )
    if not valid or report.get("status") != "COMPLETE":
        return valid, reason
    overall = report.get("overall")
    if not isinstance(overall, Mapping) or overall.get("parameters") != RUIN_MODEL_V1:
        return False, "R3 frozen ruin-model parameters missing or drifted"
    simulation = overall.get("monte_carlo")
    if not isinstance(simulation, Mapping):
        return False, "R3 Monte Carlo output missing"
    if (
        simulation.get("simulations") != RUIN_MODEL_V1["monte_carlo"]["n_simulations"]
        or simulation.get("trade_horizon") != RUIN_MODEL_V1["monte_carlo"]["trade_horizon"]
        or simulation.get("risk_per_trade_pct") != RUIN_MODEL_V1["risk_per_trade_pct"]
        or simulation.get("ruin_threshold_pct") != RUIN_MODEL_V1["ruin_threshold_pct"]
        or simulation.get("seed_base") != RUIN_MODEL_V1["seed_contract"]["seed_base"]
        or "wilson_interval_95" not in simulation
    ):
        return False, "R3 Monte Carlo contract mismatch"
    if overall.get("agreement_absolute_difference", 1.0) > RUIN_MODEL_V1["agreement_tolerance"]:
        return False, "R3 analytical/Monte Carlo disagreement"
    return True, reason
