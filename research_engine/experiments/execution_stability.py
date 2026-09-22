"""Adjudicated HD08 X6 execution-stability condition analysis.

The canonical entry point is ``execution_stability.run_x6``.  This module
isolates X6's governed statistical implementation from X3/EXEC1 and answers:

    "Under what conditions (symbol, session, spread, volatility) does
     execution quality degrade?"

Frozen HD08 contract (research_engine.registry.wave_a_no_runner_definitions
``X6_HD08_ADJUDICATED_CONTRACT``):

* evidence     : CURRENT execution_results_v1 + execution_context +
                 decision_trace_v1, strictly joined on correlation_id;
* grain        : one distinct account/broker execution result, clustered by
                 correlation_id (fan-out children keep one shared cluster);
* primary      : producer-measured ABSOLUTE execution slippage
                 (slippage_semantic == measured_execution_slippage, finite);
* secondary    : explicit result_ok failure rate (True/False/UNKNOWN tri-state,
                 UNKNOWN excluded from the denominator and counted); retcodes
                 are descriptive only; no composite endpoint;
* dimensions   : canonical symbol, session, spread band, volatility state,
                 each analysed individually with no unrestricted interactions;
* inference    : cluster-aware categorical WLS per sufficient dimension,
                 correlation_id sandwich covariance, global Wald test with
                 df = S - 1, within-dimension Holm pairwise follow-ups, and
                 degradation claims only from Holm-supported higher fitted
                 absolute measured slippage.
"""
from __future__ import annotations

from collections import defaultdict
import math
import statistics
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
from research_engine.experiments.strategy_horizon_interaction import (
    _chi_square_survival,
    _holm_adjust,
    _quadratic,
    _two_sided_normal_p,
)
from research_engine.experiments.strategy_identity_expectancy import (
    Z_95,
    _invert,
    _solve_linear,
)
from research_engine.experiments.x3_session_quality import rejection_interval


REPORT_FILENAME = "x6_execution_stability.json"
ALPHA = 0.05
MIN_OVERALL_RESULTS = 100
MIN_OVERALL_DECISIONS = 30
MIN_CELL_RESULTS = 30
MIN_CELL_DECISIONS = 10
DIMENSIONS = ("symbol", "session", "spread_band", "volatility")
MISSING_INVALID = "MISSING_INVALID"
NONCELL_STATES = frozenset({"", "UNKNOWN", "MISSING", "NONE", "UNCLASSIFIED"})
DIMENSION_AUTHORITIES = {
    "symbol": (
        "canonical symbol from the governed strict execution identity/context "
        "consistency check; broker aliases are never separate research symbols"
    ),
    "session": "execution_context.market_access.session_state",
    "spread_band": "execution_context.market_access.spread_atr_ratio (frozen bands)",
    "volatility": "decision_trace_v1.v10_market_state.regime.volatility_state",
}
SPREAD_BANDS = {
    "LOW": "spread_atr_ratio < 0.10",
    "NORMAL": "0.10 <= spread_atr_ratio < 0.25",
    "HIGH": "spread_atr_ratio >= 0.25",
    "MISSING_INVALID": "missing/malformed/non-finite/negative spread_atr_ratio",
}


# ---------------------------------------------------------------------------
# Dimension cell authorities
# ---------------------------------------------------------------------------

def canonical_symbol(observation: ExecutionObservation) -> str | None:
    """Return the governed canonical symbol; broker aliases are never used."""
    value = observation.result_fields.get("symbol")
    text = "" if value is None else str(value).strip()
    return text or None


def session_state(observation: ExecutionObservation) -> str | None:
    """Authoritative execution_context session; never timestamp-derived."""
    value = str(observation.session_state or "").strip()
    return None if value.upper() in NONCELL_STATES else value


def spread_band(value: Any) -> str:
    """Frozen spread_atr_ratio bands; raw spread can never substitute."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MISSING_INVALID
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return MISSING_INVALID
    if number < 0.10:
        return "LOW"
    if number < 0.25:
        return "NORMAL"
    return "HIGH"


def volatility_state(observation: ExecutionObservation) -> str | None:
    """Exactly decision_trace_v1.v10_market_state.regime.volatility_state."""
    value = str(observation.volatility_state or "").strip()
    return None if value.upper() in NONCELL_STATES else value


def dimension_cell(observation: ExecutionObservation, dimension: str) -> str | None:
    if dimension == "symbol":
        return canonical_symbol(observation)
    if dimension == "session":
        return session_state(observation)
    if dimension == "spread_band":
        return spread_band(observation.spread_atr_ratio)
    if dimension == "volatility":
        return volatility_state(observation)
    raise ValueError(f"unknown X6 condition dimension: {dimension}")


# ---------------------------------------------------------------------------
# Per-dimension descriptive metrics and cell sufficiency
# ---------------------------------------------------------------------------

def dimension_cells(
    observations: tuple[ExecutionObservation, ...], dimension: str,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...], int]:
    """Descriptive per-cell accounting with endpoint-specific sufficiency."""
    grouped: dict[str, list[ExecutionObservation]] = defaultdict(list)
    unclassified = 0
    for observation in observations:
        cell = dimension_cell(observation, dimension)
        if cell is None:
            unclassified += 1
        else:
            grouped[cell].append(observation)
    metrics: dict[str, dict[str, Any]] = {}
    primary_sufficient: list[str] = []
    for name in sorted(grouped):
        rows = grouped[name]
        measured = [row for row in rows if row.absolute_measured_slippage is not None]
        values = [float(row.absolute_measured_slippage) for row in measured]
        known = [row for row in rows if row.status is not ExecutionStatus.UNKNOWN]
        primary_ok = (
            len(measured) >= MIN_CELL_RESULTS
            and len({row.cluster_id for row in measured}) >= MIN_CELL_DECISIONS
            and not (dimension == "spread_band" and name == MISSING_INVALID)
        )
        rejection_ok = (
            len(known) >= MIN_CELL_RESULTS
            and len({row.cluster_id for row in known}) >= MIN_CELL_DECISIONS
        )
        # MISSING_INVALID is an accounting bucket, never an inferential cell.
        if primary_ok:
            primary_sufficient.append(name)
        retcodes: dict[str, int] = defaultdict(int)
        for row in rows:
            value = row.result_fields.get("retcode")
            retcodes[str(value) if value is not None else "UNKNOWN"] += 1
        metrics[name] = {
            "total_joined_execution_results": len(rows),
            "distinct_correlation_id_decisions": len({row.cluster_id for row in rows}),
            "measured_slippage_eligible_n": len(measured),
            "measured_slippage_decision_n": len({row.cluster_id for row in measured}),
            "mean_absolute_measured_slippage": (
                round(statistics.fmean(values), 12) if values else None
            ),
            "median_absolute_measured_slippage": (
                round(statistics.median(values), 12) if values else None
            ),
            "primary_sufficient": primary_ok,
            "known_status_n": len(known),
            "unknown_status_n": len(rows) - len(known),
            "successes": sum(row.status is ExecutionStatus.SUCCESS for row in rows),
            "failures": sum(row.status is ExecutionStatus.FAILURE for row in rows),
            "failure_rate": (
                round(sum(row.status is ExecutionStatus.FAILURE for row in known) / len(known), 12)
                if known else None
            ),
            "rejection_decision_n": len({row.cluster_id for row in known}),
            "rejection_sufficient": rejection_ok,
            "rejection_interval_95": rejection_interval(rows),
            "retcode_distribution": dict(sorted(retcodes.items())),
        }
    return metrics, tuple(sorted(primary_sufficient)), unclassified


# ---------------------------------------------------------------------------
# Cluster-aware categorical comparison machinery (mirrors validated X3)
# ---------------------------------------------------------------------------

def _design_row(cell_index: int, cell_count: int) -> list[float]:
    row = [0.0] * cell_count
    row[0] = 1.0
    if cell_index:
        row[cell_index] = 1.0
    return row


def fit_dimension_model(
    observations: list[ExecutionObservation],
    cells: tuple[str, ...],
    dimension: str,
) -> dict[str, Any] | None:
    """Fit weighted categorical WLS with correlation_id cluster covariance."""
    if len(cells) < 2:
        return None
    cell_index = {cell: index for index, cell in enumerate(cells)}
    grouped: dict[str, list[ExecutionObservation]] = defaultdict(list)
    for observation in observations:
        cell = dimension_cell(observation, dimension)
        if cell in cell_index and observation.absolute_measured_slippage is not None:
            grouped[observation.cluster_id].append(observation)

    rows: list[tuple[str, int, float, float]] = []
    cluster_total_weights: dict[str, float] = {}
    for cluster_id in sorted(grouped):
        members = sorted(grouped[cluster_id], key=lambda item: item.observation_identity)
        weight = 1.0 / len(members)
        cluster_total_weights[cluster_id] = sum(weight for _ in members)
        for observation in members:
            cell = str(dimension_cell(observation, dimension))
            rows.append((
                cluster_id,
                cell_index[cell],
                float(observation.absolute_measured_slippage),
                weight,
            ))
    if not rows:
        return None

    parameter_count = len(cells)
    bread = [[0.0] * parameter_count for _ in range(parameter_count)]
    rhs = [0.0] * parameter_count
    for _, cell_i, outcome, weight in rows:
        design = _design_row(cell_i, parameter_count)
        for i in range(parameter_count):
            rhs[i] += weight * design[i] * outcome
            for j in range(parameter_count):
                bread[i][j] += weight * design[i] * design[j]
    beta = _solve_linear(bread, rhs)
    bread_inverse = _invert(bread)
    if beta is None or bread_inverse is None:
        return None

    scores: dict[str, list[float]] = {}
    for cluster_id, cell_i, outcome, weight in rows:
        design = _design_row(cell_i, parameter_count)
        residual = outcome - sum(
            beta[index] * design[index] for index in range(parameter_count)
        )
        score = scores.setdefault(cluster_id, [0.0] * parameter_count)
        for index in range(parameter_count):
            score[index] += weight * design[index] * residual
    meat = [[0.0] * parameter_count for _ in range(parameter_count)]
    for cluster_id in sorted(scores):
        score = scores[cluster_id]
        for i in range(parameter_count):
            for j in range(parameter_count):
                meat[i][j] += score[i] * score[j]
    covariance = [[
        sum(
            bread_inverse[i][k] * meat[k][l] * bread_inverse[l][j]
            for k in range(parameter_count) for l in range(parameter_count)
        )
        for j in range(parameter_count)
    ] for i in range(parameter_count)]
    if any(not math.isfinite(value) for row in covariance for value in row):
        return None

    coefficient_indices = list(range(1, parameter_count))
    coefficient_block = [beta[index] for index in coefficient_indices]
    covariance_block = [
        [covariance[i][j] for j in coefficient_indices] for i in coefficient_indices
    ]
    covariance_block_inverse = _invert(covariance_block)
    if covariance_block_inverse is None:
        return None
    statistic = max(0.0, _quadratic(coefficient_block, covariance_block_inverse))
    p_value = _chi_square_survival(statistic, len(coefficient_indices))
    if p_value is None or not math.isfinite(p_value):
        return None

    fitted_means = {}
    for name, index in cell_index.items():
        design = _design_row(index, parameter_count)
        fitted_means[name] = sum(beta[i] * design[i] for i in range(parameter_count))
    return {
        "beta": beta,
        "covariance": covariance,
        "rows": rows,
        "cells": cells,
        "fitted_means": fitted_means,
        "cluster_total_weights": cluster_total_weights,
        "degrees_of_freedom": len(coefficient_indices),
        "wald_statistic": statistic,
        "omnibus_p_value": p_value,
    }


def pairwise_contrasts(
    fit: dict[str, Any], *, omnibus_rejected: bool,
) -> list[dict[str, Any]]:
    """All C(S,2) model-derived contrasts with within-dimension Holm family."""
    cells = fit["cells"]
    beta = fit["beta"]
    covariance = fit["covariance"]
    results: list[dict[str, Any]] = []
    raw_values: list[float] = []
    for left_i, left in enumerate(cells):
        for right_i in range(left_i + 1, len(cells)):
            right = cells[right_i]
            left_design = _design_row(left_i, len(cells))
            right_design = _design_row(right_i, len(cells))
            contrast = [a - b for a, b in zip(left_design, right_design)]
            estimate = sum(
                contrast[index] * beta[index] for index in range(len(beta))
            )
            variance = _quadratic(contrast, covariance)
            if variance < -1e-10 or not math.isfinite(variance):
                raise ValueError("invalid X6 clustered contrast variance")
            standard_error = math.sqrt(max(0.0, variance))
            raw_p = _two_sided_normal_p(estimate, standard_error)
            if raw_p is None:
                raise ValueError("invalid X6 clustered contrast p-value")
            raw_values.append(raw_p)
            results.append({
                "left_cell": left,
                "right_cell": right,
                "contrast": f"{left} vs {right}",
                "estimate_absolute_slippage_difference": round(estimate, 12),
                "_unrounded_estimate": estimate,
                "standard_error": round(standard_error, 12),
                "interval_95": {
                    "method": "correlation_id_clustered_sandwich",
                    "lower": round(estimate - Z_95 * standard_error, 12),
                    "upper": round(estimate + Z_95 * standard_error, 12),
                    "z": Z_95,
                },
                "raw_p_value": round(raw_p, 12),
                "holm_adjusted_p_value": None,
                "adjusted_significant": False,
                "inferential_claim_permitted": False,
                "supported_higher_slippage_cell": None,
            })
    if omnibus_rejected:
        for result, adjusted in zip(results, _holm_adjust(raw_values)):
            supported = adjusted <= ALPHA
            result["holm_adjusted_p_value"] = round(adjusted, 12)
            result["adjusted_significant"] = supported
            result["inferential_claim_permitted"] = supported
            if supported:
                estimate = result["_unrounded_estimate"]
                if estimate > 0:
                    result["supported_higher_slippage_cell"] = result["left_cell"]
                elif estimate < 0:
                    result["supported_higher_slippage_cell"] = result["right_cell"]
    for result in results:
        result.pop("_unrounded_estimate", None)
    return results


# ---------------------------------------------------------------------------
# Dimension evaluation
# ---------------------------------------------------------------------------

def evaluate_dimension(
    evidence: Any, dimension: str, *, overall_sufficient: bool,
) -> tuple[dict[str, Any], list[ExecutionObservation], dict[str, Any] | None]:
    """Evaluate one condition dimension; insufficient dimensions fail closed."""
    metrics, sufficient, unclassified = dimension_cells(evidence.observations, dimension)
    comparable = overall_sufficient and len(sufficient) >= 2
    analytical = [
        observation for observation in evidence.observations
        if dimension_cell(observation, dimension) in sufficient
        and observation.absolute_measured_slippage is not None
    ] if comparable else []
    result: dict[str, Any] = {
        "dimension": dimension,
        "authority": DIMENSION_AUTHORITIES[dimension],
        "cells": metrics,
        "missing_or_invalid_unclassified_results": unclassified,
        "sufficient_primary_cells": list(sufficient) if comparable else [],
        "sufficient_cell_count": len(sufficient) if comparable else 0,
        "comparatively_sufficient": comparable,
        "dimension_state": "SUFFICIENT_COMPARABLE" if comparable else "INSUFFICIENT",
        "analytical_account_results": len(analytical),
        "analytical_distinct_decisions": len({row.cluster_id for row in analytical}),
    }
    if not comparable:
        result["insufficiency_reason"] = (
            "overall X6 sufficiency not met" if not overall_sufficient
            else f"only {len(sufficient)} cell(s) meet the 30-result/10-decision cell gates"
        )
    fit: dict[str, Any] | None = None
    contrasts: list[dict[str, Any]] = []
    omnibus_rejected = False
    if comparable:
        fit = fit_dimension_model(analytical, sufficient, dimension)
    if fit is not None:
        omnibus_rejected = bool(fit["omnibus_p_value"] <= ALPHA)
        try:
            contrasts = pairwise_contrasts(fit, omnibus_rejected=omnibus_rejected)
        except ValueError:
            fit = None
            omnibus_rejected = False
            contrasts = []
    if fit is None:
        result["classification"] = "INSUFFICIENT_DIMENSION_EVIDENCE"
        result["dimension_state"] = "INSUFFICIENT"
        result["insufficiency_reason"] = result.get("insufficiency_reason") or (
            "dimension design or cluster covariance is not estimable"
        )
        result["supported_degradation_contrasts"] = []
    else:
        result["classification"] = (
            "RELIABLE_EXECUTION_QUALITY_DIFFERENCE" if omnibus_rejected
            else "NO_RELIABLE_EXECUTION_QUALITY_DIFFERENCE"
        )
        result["model"] = {
            "model": "absolute_measured_slippage ~ categorical dimension_cell",
            "coding": "deterministic reference coding",
            "reference_cell": sufficient[0],
            "evaluated_cells": list(sufficient),
            "weights": "w_di = 1/k_d within the dimension analytical population",
            "cluster": "correlation_id",
            "cluster_score": "sum_i w_di * x_di * residual_di within correlation_id",
            "covariance": "A^-1 (sum_d s_d s_d') A^-1",
            "naive_row_independent_covariance_used": False,
            "cluster_total_weights": fit["cluster_total_weights"],
            "fitted_cell_means": {
                name: round(value, 12)
                for name, value in fit["fitted_means"].items()
            },
        }
        result["global_test"] = {
            "method": "cluster-robust Wald joint cell-coefficient test",
            "null": (
                "all sufficient evaluated cells have equal mean "
                "absolute measured slippage"
            ),
            "alpha": ALPHA,
            "degrees_of_freedom": fit["degrees_of_freedom"],
            "statistic": round(fit["wald_statistic"], 12),
            "p_value": round(fit["omnibus_p_value"], 12),
            "reliable_difference": omnibus_rejected,
        }
        result["pairwise_followups"] = {
            "family": "all pairs among this dimension's sufficient cells",
            "declared_count": math.comb(len(sufficient), 2),
            "multiplicity": "one Holm step-down family within this dimension only",
            "alpha": ALPHA,
            "omnibus_rejection_required": True,
            "omnibus_in_holm_family": False,
            "cross_dimension_family_combination": False,
            "results": contrasts,
        }
        result["supported_degradation_contrasts"] = [
            {
                "dimension": dimension,
                "contrast": item["contrast"],
                "higher_fitted_absolute_slippage_cell": item[
                    "supported_higher_slippage_cell"
                ],
                "estimate_absolute_slippage_difference": item[
                    "estimate_absolute_slippage_difference"
                ],
                "holm_adjusted_p_value": item["holm_adjusted_p_value"],
            }
            for item in contrasts
            if item["inferential_claim_permitted"]
            and item["supported_higher_slippage_cell"] is not None
        ]
    result["analytical_provenance"] = attest_execution_analytical_subset(
        evidence, analytical,
    )
    return result, analytical, fit


def _invalid_evidence_report(exc: Exception) -> dict[str, Any]:
    return build_report(
        question_id="X6",
        status="INSUFFICIENT_DATA",
        overall={
            "n": 0,
            "classification": "INSUFFICIENT_EVIDENCE",
            "finding": "Invalid governed execution evidence",
            "evidence_error": f"{type(exc).__name__}: {exc}",
        },
        confidence="INSUFFICIENT_DATA",
        dataset={
            "sample_size": 0,
            "source": "execution_results_v1 + execution_context + decision_trace_v1",
        },
        fingerprint={
            "dataset_id": "evidence_unverified", "records_used": 0,
            "records_excluded": 0, "source": "MULTI_SOURCE", "sources": [],
            "epoch": "UNVERIFIED", "architecture_version": "new_pipeline_v1.2",
            "validation_score": "INVALID_GOVERNED_EVIDENCE",
        },
        recommendation="WAIT_FOR_EVIDENCE",
        assumptions=["X6 fails closed when governed evidence cannot be constructed."],
        provenance={
            "experiment_module": "research_engine.experiments.execution_stability",
            "registry_id": "X6", "scientific_owner": "X6",
            "report_identity": REPORT_FILENAME,
        },
    )


def build_x6_report(
    execution_results: list[dict[str, Any]],
    execution_contexts: list[dict[str, Any]],
    decision_traces: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the canonical X6 report from supplied producer evidence."""
    raw_results = list(execution_results)
    raw_contexts = list(execution_contexts)
    raw_traces = list(decision_traces)
    try:
        evidence = build_governed_execution_evidence(
            raw_results, raw_contexts, raw_traces,
            require_decision_trace=True,
        )
        overall_sufficient = (
            evidence.account_result_count >= MIN_OVERALL_RESULTS
            and evidence.distinct_decision_count >= MIN_OVERALL_DECISIONS
        )
        dimension_results: dict[str, dict[str, Any]] = {}
        dimension_rows: dict[str, list[ExecutionObservation]] = {}
        fitted_dimensions: list[str] = []
        for dimension in DIMENSIONS:
            result, analytical, fit = evaluate_dimension(
                evidence, dimension, overall_sufficient=overall_sufficient,
            )
            dimension_results[dimension] = result
            dimension_rows[dimension] = analytical
            if fit is not None:
                fitted_dimensions.append(dimension)
        comparable_states = [
            dimension_results[dimension]["classification"]
            for dimension in fitted_dimensions
        ]
        missing_condition_accounting = {
            dimension: {
                "unclassified_results": dimension_results[dimension][
                    "missing_or_invalid_unclassified_results"
                ],
            }
            for dimension in DIMENSIONS
        }
        union: list[ExecutionObservation] = []
        seen_identities: set[tuple[tuple[str, str], ...]] = set()
        for dimension in DIMENSIONS:
            for observation in dimension_rows[dimension]:
                if observation.observation_identity not in seen_identities:
                    seen_identities.add(observation.observation_identity)
                    union.append(observation)
        analytical_provenance = attest_execution_analytical_subset(evidence, union)
    except (TypeError, ValueError) as exc:
        return _invalid_evidence_report(exc)

    any_reliable = any(
        state == "RELIABLE_EXECUTION_QUALITY_DIFFERENCE" for state in comparable_states
    )
    any_supported_degradation = any(
        dimension_results[dimension]["supported_degradation_contrasts"]
        for dimension in fitted_dimensions
    )
    if not overall_sufficient or not fitted_dimensions:
        classification = "INSUFFICIENT_EVIDENCE"
        status = "INSUFFICIENT_DATA"
    else:
        if any_supported_degradation:
            classification = "RELIABLE_EXECUTION_QUALITY_DEGRADATION_DIFFERENCE"
        elif any_reliable:
            classification = "RELIABLE_EXECUTION_QUALITY_DIFFERENCE"
        else:
            classification = "NO_RELIABLE_EXECUTION_QUALITY_DIFFERENCE"
        status = "COMPLETE"
    unknown_status_count = sum(
        row.status is ExecutionStatus.UNKNOWN for row in evidence.observations
    )
    measured_eligible_count = sum(
        row.absolute_measured_slippage is not None for row in evidence.observations
    )
    finding = {
        "INSUFFICIENT_EVIDENCE": (
            "Insufficient evidence for governed execution-stability inference"
        ),
        "RELIABLE_EXECUTION_QUALITY_DEGRADATION_DIFFERENCE": (
            "Reliable condition-conditioned difference in producer-measured "
            "absolute execution slippage"
        ),
        "RELIABLE_EXECUTION_QUALITY_DIFFERENCE": (
            "Reliable omnibus execution-quality difference without a Holm-supported "
            "directional degradation contrast"
        ),
        "NO_RELIABLE_EXECUTION_QUALITY_DIFFERENCE": (
            "No reliable condition-conditioned difference in producer-measured "
            "absolute execution slippage"
        ),
    }[classification]
    overall: dict[str, Any] = {
        "n": evidence.account_result_count,
        "finding": finding,
        "classification": classification,
        "estimand": "CONDITION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",
        "canonical_question": (
            "Under what conditions (symbol, session, spread, volatility) does "
            "execution quality degrade?"
        ),
        "analytical_population": {
            "observation_grain": "one distinct account/broker execution result",
            "cluster": "correlation_id",
            "fan_out_children_remain_observations": True,
            "governed_matched_account_results": evidence.account_result_count,
            "distinct_correlation_id_decisions": evidence.distinct_decision_count,
            "measured_slippage_eligible_results": measured_eligible_count,
            "unknown_execution_status_results": unknown_status_count,
        },
        "overall_sufficiency": {
            "minimum_governed_matched_account_results": MIN_OVERALL_RESULTS,
            "minimum_distinct_correlation_id_decisions": MIN_OVERALL_DECISIONS,
            "overall_sufficient": overall_sufficient,
            "evaluated_dimension_count": len(DIMENSIONS),
            "dimensions_with_comparative_model": len(fitted_dimensions),
            "at_least_one_comparatively_sufficient_dimension": bool(fitted_dimensions),
            "all_dimensions_explicitly_evaluated": set(dimension_results) == set(DIMENSIONS),
        },
        "condition_dimensions": dimension_results,
        "overall_classification": classification,
        "supported_degradation_conditions": [
            contrast
            for dimension in DIMENSIONS
            for contrast in dimension_results[dimension]["supported_degradation_contrasts"]
        ],
        "missing_invalid_condition_accounting": missing_condition_accounting,
        "primary_endpoint": {
            "name": "producer-measured absolute execution slippage",
            "semantic_required": "measured_execution_slippage",
            "reconstructed_slippage_used": False,
            "spread_substitution_used": False,
            "absolute_value_used": True,
        },
        "secondary_rejection_endpoint": {
            "role": "descriptive only with cluster-aware uncertainty",
            "success": "result_ok is explicitly True",
            "failure": "result_ok is explicitly False",
            "unknown": "excluded from denominator and counted",
            "retcode_role": "descriptive only",
            "pairwise_hypothesis_tests_performed": False,
            "composite_score_used": False,
            "can_override_primary_classification": False,
        },
        "evidence_accounting": {
            "components": evidence.component_accounting,
            "join_and_identity_exclusions": evidence.exclusion_accounting,
        },
        "claim_boundary": (
            "CURRENT evidence and the listed sufficient evaluated cells only; "
            "producer-measured execution slippage does not establish strategy "
            "profitability, causal broker behaviour, or future execution performance"
        ),
        "limitations": [
            "Failed executions reveal nothing about what those trades would have earned.",
            "Broker rejections lacking a persisted account-result record are invisible "
            "to this population and are never fabricated.",
            "Dimensions are analysed individually; no unrestricted interaction "
            "discovery is performed.",
            "MISSING_INVALID condition cells are reported but never used as "
            "comparable analytical cells.",
        ],
    }
    fingerprint = build_fingerprint_from_provenance(
        analytical_provenance, validation_score="CURRENT_GOVERNED_X6",
    )
    return build_report(
        question_id="X6",
        status=status,
        overall=overall,
        confidence=(
            "HIGH" if evidence.account_result_count >= 200 else
            "MEDIUM" if evidence.account_result_count >= 60 else
            "LOW" if evidence.account_result_count else "INSUFFICIENT_DATA"
        ),
        dataset={
            "sample_size": evidence.account_result_count,
            "analysed_row_count": len(union),
            "source": "execution_results_v1 + execution_context + decision_trace_v1",
            "records_loaded": len(raw_results) + len(raw_contexts) + len(raw_traces),
        },
        fingerprint=fingerprint,
        recommendation=classification if status == "COMPLETE" else "WAIT_FOR_EVIDENCE",
        assumptions=[
            "CURRENT execution results, contexts, and decision traces are selected before strict correlation_id joining.",
            "Account fan-out children remain observations but each shared decision has total estimator weight one.",
            "The rejection endpoint is descriptive and cannot alter the primary slippage inference.",
        ],
        warnings=[
            "Claims are bounded to the listed sufficient cells and CURRENT evidence population.",
            "Execution quality is not strategy profitability.",
        ],
        provenance={
            "experiment_module": "research_engine.experiments.execution_stability",
            "registry_id": "X6",
            "scientific_owner": "X6",
            "report_identity": REPORT_FILENAME,
            "shared_evidence_foundation": "research_engine.control_plane.execution_evidence",
            "adjudicated_contract": "X6_HD08_ADJUDICATED_CONTRACT",
            "definition_version": 1,
        },
    )


def _load_results() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_execution_results
    return load_execution_results()


def _load_context() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_execution_context
    return load_execution_context()


def _load_decision_trace() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_decision_trace
    return load_decision_trace()


def run_x6(
    execution_results: list[dict[str, Any]] | None = None,
    execution_contexts: list[dict[str, Any]] | None = None,
    decision_traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Canonical X6 entry point over the shared governed evidence foundation."""
    results = _load_results() if execution_results is None else execution_results
    contexts = _load_context() if execution_contexts is None else execution_contexts
    traces = _load_decision_trace() if decision_traces is None else decision_traces
    return build_x6_report(list(results), list(contexts), list(traces))
