"""Governed EXEC1 pre-execution spread / execution-realization association."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any

from research_engine.control_plane.execution_evidence import (
    ExecutionObservation,
    ExecutionStatus,
    attest_execution_analytical_subset,
    build_governed_execution_evidence,
)
from research_engine.experiments.experiment_base import (
    build_fingerprint_from_provenance,
    build_report,
)
from research_engine.experiments.strategy_horizon_interaction import _two_sided_normal_p
from research_engine.experiments.strategy_identity_expectancy import Z_95, _invert, _solve_linear


REPORT_FILENAME = "w4_exec1_execution_failures.json"
ALPHA = 0.05
MIN_ANALYTICAL_ROWS = 30
MIN_DISTINCT_DECISIONS = 10


def valid_spread_atr_ratio(value: Any) -> float | None:
    """Return the governed continuous exposure, with no fallback or bool coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _is_execute(observation: ExecutionObservation) -> bool:
    return isinstance(observation.action, str) and observation.action.strip() == "EXECUTE"


def _outcome(observation: ExecutionObservation) -> float | None:
    if observation.status is ExecutionStatus.SUCCESS:
        return 1.0
    if observation.status is ExecutionStatus.FAILURE:
        return 0.0
    return None


def fit_primary_model(observations: list[ExecutionObservation]) -> dict[str, Any] | None:
    """Fit the frozen 1/k weighted LPM and correlation-cluster sandwich."""
    grouped: dict[str, list[ExecutionObservation]] = defaultdict(list)
    for observation in observations:
        if _outcome(observation) is not None and valid_spread_atr_ratio(
            observation.spread_atr_ratio
        ) is not None:
            grouped[observation.cluster_id].append(observation)
    rows: list[tuple[str, float, float, float]] = []
    cluster_total_weights: dict[str, float] = {}
    for cluster_id in sorted(grouped):
        members = sorted(grouped[cluster_id], key=lambda item: item.observation_identity)
        weight = 1.0 / len(members)
        cluster_total_weights[cluster_id] = sum(weight for _ in members)
        for observation in members:
            exposure = valid_spread_atr_ratio(observation.spread_atr_ratio)
            outcome = _outcome(observation)
            assert exposure is not None and outcome is not None
            rows.append((cluster_id, exposure, outcome, weight))
    if not rows:
        return None

    bread = [[0.0, 0.0], [0.0, 0.0]]
    rhs = [0.0, 0.0]
    for _, exposure, outcome, weight in rows:
        design = [1.0, exposure]
        for i in range(2):
            rhs[i] += weight * design[i] * outcome
            for j in range(2):
                bread[i][j] += weight * design[i] * design[j]
    beta = _solve_linear(bread, rhs)
    bread_inverse = _invert(bread)
    if beta is None or bread_inverse is None:
        return None

    scores: dict[str, list[float]] = {}
    for cluster_id, exposure, outcome, weight in rows:
        design = [1.0, exposure]
        residual = outcome - beta[0] - beta[1] * exposure
        score = scores.setdefault(cluster_id, [0.0, 0.0])
        for index in range(2):
            score[index] += weight * design[index] * residual
    meat = [[0.0, 0.0], [0.0, 0.0]]
    for cluster_id in sorted(scores):
        score = scores[cluster_id]
        for i in range(2):
            for j in range(2):
                meat[i][j] += score[i] * score[j]
    covariance = [[
        sum(
            bread_inverse[i][k] * meat[k][l] * bread_inverse[l][j]
            for k in range(2) for l in range(2)
        )
        for j in range(2)
    ] for i in range(2)]
    if any(not math.isfinite(value) for row in covariance for value in row):
        return None
    slope_variance = covariance[1][1]
    if slope_variance < -1e-10:
        return None
    standard_error = math.sqrt(max(0.0, slope_variance))
    p_value = _two_sided_normal_p(beta[1], standard_error)
    if p_value is None or not math.isfinite(p_value):
        return None
    return {
        "beta": beta,
        "covariance": covariance,
        "rows": rows,
        "cluster_total_weights": cluster_total_weights,
        "beta_spread": beta[1],
        "standard_error": standard_error,
        "p_value": p_value,
        "interval_lower": beta[1] - Z_95 * standard_error,
        "interval_upper": beta[1] + Z_95 * standard_error,
    }


def _classification(fit: dict[str, Any]) -> str:
    slope = fit["beta_spread"]
    if fit["p_value"] <= ALPHA and slope < 0 and fit["interval_upper"] < 0:
        return "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION"
    if fit["p_value"] <= ALPHA and slope > 0 and fit["interval_lower"] > 0:
        return "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION"
    return "NO_RELIABLE_ASSOCIATION"


def _invalid_report(exc: Exception) -> dict[str, Any]:
    return build_report(
        question_id="EXEC1", status="INSUFFICIENT_DATA",
        overall={
            "n": 0, "classification": "INSUFFICIENT_EVIDENCE",
            "finding": "Invalid governed execution evidence",
            "evidence_error": f"{type(exc).__name__}: {exc}",
        },
        confidence="INSUFFICIENT_DATA",
        dataset={"sample_size": 0, "source": "execution_results_v1 + execution_context + decision_trace_v1"},
        fingerprint={
            "dataset_id": "evidence_unverified", "records_used": 0,
            "records_excluded": 0, "source": "MULTI_SOURCE", "sources": [],
            "epoch": "UNVERIFIED", "architecture_version": "new_pipeline_v1.2",
            "validation_score": "INVALID_GOVERNED_EVIDENCE",
        },
        recommendation="WAIT_FOR_EVIDENCE",
        assumptions=["EXEC1 fails closed when governed evidence cannot be constructed."],
        provenance={
            "experiment_module": "research_engine.experiments.execution_protection_research",
            "registry_id": "EXEC1", "scientific_owner": "EXEC1",
            "report_identity": REPORT_FILENAME,
        },
    )


def build_exec1_report(
    execution_results: list[dict[str, Any]],
    execution_contexts: list[dict[str, Any]],
    decision_traces: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the canonical EXEC1 report from the frozen three-source contract."""
    raw_results = list(execution_results)
    raw_contexts = list(execution_contexts)
    raw_traces = list(decision_traces)
    try:
        evidence = build_governed_execution_evidence(
            raw_results, raw_contexts, raw_traces, require_decision_trace=True,
        )
        execute_rows = [row for row in evidence.observations if _is_execute(row)]
        missing_action = sum(
            row.action is None or str(row.action).strip() == ""
            for row in evidence.observations
        )
        non_execute = evidence.account_result_count - len(execute_rows) - missing_action
        unknown_rows = [row for row in execute_rows if _outcome(row) is None]
        known_rows = [row for row in execute_rows if _outcome(row) is not None]
        invalid_exposure_rows = [
            row for row in known_rows
            if valid_spread_atr_ratio(row.spread_atr_ratio) is None
        ]
        analytical = [
            row for row in known_rows
            if valid_spread_atr_ratio(row.spread_atr_ratio) is not None
        ]
        analytical_provenance = attest_execution_analytical_subset(evidence, analytical)
    except (TypeError, ValueError) as exc:
        return _invalid_report(exc)

    outcomes = [int(_outcome(row)) for row in analytical]
    exposures = [float(valid_spread_atr_ratio(row.spread_atr_ratio)) for row in analytical]
    decisions = {row.cluster_id for row in analytical}
    sufficient = {
        "minimum_eligible_rows": len(analytical) >= MIN_ANALYTICAL_ROWS,
        "minimum_distinct_decisions": len(decisions) >= MIN_DISTINCT_DECISIONS,
        "exposure_variation": len(set(exposures)) >= 2,
        "outcome_variation": len(set(outcomes)) >= 2,
    }
    fit = fit_primary_model(analytical) if all(sufficient.values()) else None
    sufficient["full_rank_design_and_valid_cluster_inference"] = fit is not None
    classification = _classification(fit) if fit else "INSUFFICIENT_EVIDENCE"
    status = "COMPLETE" if fit else "INSUFFICIENT_DATA"

    successes = sum(row.status is ExecutionStatus.SUCCESS for row in execute_rows)
    failures = sum(row.status is ExecutionStatus.FAILURE for row in execute_rows)
    known_count = successes + failures
    retcodes = Counter(
        str(row.result_fields.get("retcode"))
        if row.result_fields.get("retcode") is not None else "UNKNOWN"
        for row in execute_rows
    )
    finding = (
        "Higher pre-execution spread_atr_ratio was associated with lower successful execution realization within the evaluated CURRENT EXECUTE population"
        if classification == "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION" else
        "Higher pre-execution spread_atr_ratio was associated with higher successful execution realization within the evaluated CURRENT EXECUTE population"
        if classification == "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION" else
        "No reliable association between pre-execution spread_atr_ratio and successful execution realization within the evaluated CURRENT EXECUTE population"
        if classification == "NO_RELIABLE_ASSOCIATION" else
        "Insufficient evidence for the governed EXEC1 spread-realization association"
    )
    overall = {
        "n": len(analytical), "classification": classification, "finding": finding,
        "otherwise_valid_population": {
            "governed_joined_account_results": evidence.account_result_count,
            "execute_account_results": len(execute_rows),
            "distinct_execute_decisions": len({row.cluster_id for row in execute_rows}),
            "excluded_non_execute": non_execute,
            "excluded_missing_action": missing_action,
        },
        "descriptive_execution_reliability": {
            "successes": successes, "failures": failures,
            "unknown_status": len(unknown_rows), "known_status_n": known_count,
            "successful_realization_rate": round(successes / known_count, 12) if known_count else None,
            "failure_rate": round(failures / known_count, 12) if known_count else None,
            "retcode_distribution": dict(sorted(retcodes.items())),
            "inferential_exposure": False,
        },
        "primary_analytical_population": {
            "account_result_n": len(analytical),
            "distinct_correlation_id_decisions": len(decisions),
            "excluded_unknown_status": len(unknown_rows),
            "excluded_invalid_spread_atr_ratio": len(invalid_exposure_rows),
            "distinct_spread_atr_ratio_values": len(set(exposures)),
            "successes": sum(outcomes), "failures": len(outcomes) - sum(outcomes),
        },
        "primary_association": {
            "model": "successful_execution ~ intercept + spread_atr_ratio",
            "model_family": "cluster-weighted linear probability model",
            "exposure": "execution_context.market_access.spread_atr_ratio",
            "exposure_treatment": "continuous; no bins, fallback, reconstruction, or standardization",
            "outcome": "explicit result_ok True=1, False=0; UNKNOWN excluded",
            "beta_intercept": round(fit["beta"][0], 12) if fit else None,
            "beta_spread": round(fit["beta_spread"], 12) if fit else None,
            "effect_per_0_10": round(0.10 * fit["beta_spread"], 12) if fit else None,
            "cluster_robust_standard_error": round(fit["standard_error"], 12) if fit else None,
            "interval_95": {
                "lower": round(fit["interval_lower"], 12),
                "upper": round(fit["interval_upper"], 12), "z": Z_95,
            } if fit else None,
            "two_sided_p_value": round(fit["p_value"], 12) if fit else None,
            "null": "beta_spread = 0", "alpha": ALPHA,
            "cluster": "correlation_id",
            "weights": "w_di = 1/k_d within the final primary analytical population",
            "cluster_total_weights": fit["cluster_total_weights"] if fit else {},
            "covariance": "A^-1 (sum_d s_d s_d') A^-1",
            "naive_row_independent_covariance_used": False,
            "result": classification,
        },
        "sufficiency": {
            "minimum_eligible_rows_required": MIN_ANALYTICAL_ROWS,
            "minimum_distinct_decisions_required": MIN_DISTINCT_DECISIONS,
            "minimum_distinct_exposure_values_required": 2,
            **sufficient,
        },
        "optional_successful_execution_trade_truth_secondary": {
            "status": "NOT_EVALUATED", "completion_dependency": False,
            "reason": "No secondary inferential model is frozen; failed executions receive no synthetic outcome.",
        },
        "measured_slippage": {
            "role": "not used as the primary result_ok exposure", "reconstructed": False,
        },
        "evidence_accounting": {
            "components": evidence.component_accounting,
            "join_and_identity_exclusions": evidence.exclusion_accounting,
        },
        "claim_boundary": (
            "Associative within the evaluated CURRENT EXECUTE population only; "
            "no causal, counterfactual-profit, or strategy-expectancy claim."
        ),
    }
    fingerprint = build_fingerprint_from_provenance(
        analytical_provenance, validation_score="CURRENT_GOVERNED_EXEC1",
    )
    return build_report(
        question_id="EXEC1", status=status, overall=overall,
        confidence=(
            "HIGH" if len(analytical) >= 200 else
            "MEDIUM" if len(analytical) >= 60 else
            "LOW" if analytical else "INSUFFICIENT_DATA"
        ),
        dataset={
            "sample_size": evidence.account_result_count,
            "analysed_row_count": len(analytical),
            "source": "execution_results_v1 + execution_context + decision_trace_v1",
            "records_loaded": len(raw_results) + len(raw_contexts) + len(raw_traces),
        },
        fingerprint=fingerprint,
        recommendation=classification if status == "COMPLETE" else "WAIT_FOR_EVIDENCE",
        assumptions=[
            "The analysis is associative, not causal.",
            "Each correlation_id contributes total estimator weight one.",
            "Otherwise-valid means authoritative pre-execution action exactly EXECUTE.",
        ],
        warnings=[
            "A failed execution is not a losing trade and receives no synthetic R.",
            "Measured slippage is not used to predict result_ok.",
        ],
        provenance={
            "experiment_module": "research_engine.experiments.execution_protection_research",
            "registry_id": "EXEC1", "scientific_owner": "EXEC1",
            "report_identity": REPORT_FILENAME,
            "shared_evidence_foundation": "research_engine.control_plane.execution_evidence",
        },
    )


__all__ = [
    "ALPHA", "MIN_ANALYTICAL_ROWS", "MIN_DISTINCT_DECISIONS", "REPORT_FILENAME",
    "build_exec1_report", "fit_primary_model", "valid_spread_atr_ratio",
]
