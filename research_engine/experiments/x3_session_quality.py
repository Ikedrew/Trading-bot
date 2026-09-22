"""Adjudicated X3 session-conditioned execution-quality analysis.

The canonical entry point remains
``execution_protection_research.run_x3``.  This module isolates X3's governed
statistical implementation from the legacy X1/X2/EXEC1 compatibility code.
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


REPORT_FILENAME = "w4_x3_session_quality.json"
ALPHA = 0.05
MIN_OVERALL_RESULTS = 30
MIN_SESSION_RESULTS = 30
MIN_SESSION_DECISIONS = 10
CANONICAL_SESSION_ORDER = (
    "ASIA", "ASIAN", "LONDON", "LONDON_NY_OVERLAP", "NY", "NEW_YORK",
    "OFF_SESSION",
)
_UNKNOWN_SESSIONS = frozenset({"", "UNKNOWN", "MISSING", "UNCLASSIFIED", "NONE"})


def _session(observation: ExecutionObservation) -> str | None:
    value = str(observation.session_state or "").strip()
    return None if value.upper() in _UNKNOWN_SESSIONS else value


def _session_sort_key(session: str) -> tuple[int, str]:
    upper = session.upper()
    try:
        return CANONICAL_SESSION_ORDER.index(upper), upper
    except ValueError:
        return len(CANONICAL_SESSION_ORDER), upper


def _design_row(session_index: int, session_count: int) -> list[float]:
    row = [0.0] * session_count
    row[0] = 1.0
    if session_index:
        row[session_index] = 1.0
    return row


def fit_session_model(
    observations: list[ExecutionObservation], sessions: tuple[str, ...],
) -> dict[str, Any] | None:
    """Fit weighted categorical-session WLS with decision-cluster covariance."""
    if len(sessions) < 2:
        return None
    session_index = {session: index for index, session in enumerate(sessions)}
    grouped: dict[str, list[ExecutionObservation]] = defaultdict(list)
    for observation in observations:
        name = _session(observation)
        if name in session_index and observation.absolute_measured_slippage is not None:
            grouped[observation.cluster_id].append(observation)

    rows: list[tuple[str, int, float, float]] = []
    cluster_total_weights: dict[str, float] = {}
    for cluster_id in sorted(grouped):
        members = sorted(grouped[cluster_id], key=lambda item: item.observation_identity)
        weight = 1.0 / len(members)
        cluster_total_weights[cluster_id] = sum(weight for _ in members)
        for observation in members:
            name = _session(observation)
            rows.append((
                cluster_id,
                session_index[str(name)],
                float(observation.absolute_measured_slippage),
                weight,
            ))
    if not rows:
        return None

    parameter_count = len(sessions)
    bread = [[0.0] * parameter_count for _ in range(parameter_count)]
    rhs = [0.0] * parameter_count
    for _, session_i, outcome, weight in rows:
        design = _design_row(session_i, parameter_count)
        for i in range(parameter_count):
            rhs[i] += weight * design[i] * outcome
            for j in range(parameter_count):
                bread[i][j] += weight * design[i] * design[j]
    beta = _solve_linear(bread, rhs)
    bread_inverse = _invert(bread)
    if beta is None or bread_inverse is None:
        return None

    scores: dict[str, list[float]] = {}
    for cluster_id, session_i, outcome, weight in rows:
        design = _design_row(session_i, parameter_count)
        residual = outcome - sum(beta[index] * design[index] for index in range(parameter_count))
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
    for name, index in session_index.items():
        design = _design_row(index, parameter_count)
        fitted_means[name] = sum(beta[i] * design[i] for i in range(parameter_count))
    return {
        "beta": beta,
        "covariance": covariance,
        "rows": rows,
        "sessions": sessions,
        "fitted_means": fitted_means,
        "cluster_total_weights": cluster_total_weights,
        "degrees_of_freedom": len(coefficient_indices),
        "wald_statistic": statistic,
        "omnibus_p_value": p_value,
    }


def pairwise_contrasts(
    fit: dict[str, Any], *, omnibus_rejected: bool,
) -> list[dict[str, Any]]:
    sessions = fit["sessions"]
    beta = fit["beta"]
    covariance = fit["covariance"]
    results: list[dict[str, Any]] = []
    raw_values: list[float] = []
    for left_i, left in enumerate(sessions):
        for right_i in range(left_i + 1, len(sessions)):
            right = sessions[right_i]
            left_design = _design_row(left_i, len(sessions))
            right_design = _design_row(right_i, len(sessions))
            contrast = [a - b for a, b in zip(left_design, right_design)]
            estimate = sum(contrast[index] * beta[index] for index in range(len(beta)))
            variance = _quadratic(contrast, covariance)
            if variance < -1e-10 or not math.isfinite(variance):
                raise ValueError("invalid X3 clustered contrast variance")
            standard_error = math.sqrt(max(0.0, variance))
            raw_p = _two_sided_normal_p(estimate, standard_error)
            if raw_p is None:
                raise ValueError("invalid X3 clustered contrast p-value")
            raw_values.append(raw_p)
            results.append({
                "left_session": left,
                "right_session": right,
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
                "supported_lower_session": None,
            })
    if omnibus_rejected:
        for result, adjusted in zip(results, _holm_adjust(raw_values)):
            supported = adjusted <= ALPHA
            result["holm_adjusted_p_value"] = round(adjusted, 12)
            result["adjusted_significant"] = supported
            result["inferential_claim_permitted"] = supported
            if supported:
                estimate = result["_unrounded_estimate"]
                if estimate < 0:
                    result["supported_lower_session"] = result["left_session"]
                elif estimate > 0:
                    result["supported_lower_session"] = result["right_session"]
    for result in results:
        result.pop("_unrounded_estimate", None)
    return results


def unique_supported_best(
    sessions: tuple[str, ...], contrasts: list[dict[str, Any]],
) -> str:
    wins = {session: set() for session in sessions}
    for item in contrasts:
        winner = item["supported_lower_session"]
        if not winner or not item["inferential_claim_permitted"]:
            continue
        loser = item["right_session"] if winner == item["left_session"] else item["left_session"]
        wins[winner].add(loser)
    supported = [session for session in sessions if wins[session] == set(sessions) - {session}]
    return supported[0] if len(supported) == 1 else "NO_UNIQUE_SUPPORTED_BEST"


def rejection_interval(observations: list[ExecutionObservation]) -> dict[str, Any] | None:
    """Cluster-sandwich interval for the cluster-normalized failure proportion."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for observation in observations:
        if observation.status is ExecutionStatus.SUCCESS:
            grouped[observation.cluster_id].append(0.0)
        elif observation.status is ExecutionStatus.FAILURE:
            grouped[observation.cluster_id].append(1.0)
    if not grouped:
        return None
    weighted: list[tuple[str, float, float]] = []
    for cluster_id in sorted(grouped):
        weight = 1.0 / len(grouped[cluster_id])
        weighted.extend((cluster_id, value, weight) for value in grouped[cluster_id])
    bread = sum(weight for _, _, weight in weighted)
    estimate = sum(weight * value for _, value, weight in weighted) / bread
    scores: dict[str, float] = defaultdict(float)
    for cluster_id, value, weight in weighted:
        scores[cluster_id] += weight * (value - estimate)
    variance = sum(score * score for score in scores.values()) / (bread * bread)
    if variance < 0 or not math.isfinite(variance):
        return None
    standard_error = math.sqrt(variance)
    return {
        "method": "correlation_id_clustered_sandwich_for_weighted_proportion",
        "estimate": round(estimate, 12),
        "standard_error": round(standard_error, 12),
        "lower": round(max(0.0, estimate - Z_95 * standard_error), 12),
        "upper": round(min(1.0, estimate + Z_95 * standard_error), 12),
        "z": Z_95,
        "cluster_normalized_weighting": "w_di = 1/k_d within session known-status rows",
    }


def session_metrics(
    observations: tuple[ExecutionObservation, ...],
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...], int]:
    grouped: dict[str, list[ExecutionObservation]] = defaultdict(list)
    unknown_session_count = 0
    for observation in observations:
        name = _session(observation)
        if name is None:
            unknown_session_count += 1
        else:
            grouped[name].append(observation)
    ordered_sessions = tuple(sorted(grouped, key=_session_sort_key))
    metrics: dict[str, dict[str, Any]] = {}
    primary_sufficient: list[str] = []
    for name in ordered_sessions:
        rows = grouped[name]
        measured = [row for row in rows if row.absolute_measured_slippage is not None]
        values = [float(row.absolute_measured_slippage) for row in measured]
        known = [row for row in rows if row.status is not ExecutionStatus.UNKNOWN]
        successes = sum(row.status is ExecutionStatus.SUCCESS for row in known)
        failures = sum(row.status is ExecutionStatus.FAILURE for row in known)
        primary_ok = (
            len(measured) >= MIN_SESSION_RESULTS
            and len({row.cluster_id for row in measured}) >= MIN_SESSION_DECISIONS
        )
        rejection_ok = (
            len(known) >= MIN_SESSION_RESULTS
            and len({row.cluster_id for row in known}) >= MIN_SESSION_DECISIONS
        )
        if primary_ok:
            primary_sufficient.append(name)
        endpoint_state = (
            "BOTH" if primary_ok and rejection_ok else
            "PRIMARY_SUFFICIENT" if primary_ok else
            "REJECTION_SUFFICIENT" if rejection_ok else
            "INSUFFICIENT"
        )
        retcodes: dict[str, int] = defaultdict(int)
        for row in rows:
            value = row.result_fields.get("retcode")
            retcodes[str(value) if value is not None else "UNKNOWN"] += 1
        metrics[name] = {
            "total_joined_execution_results": len(rows),
            "distinct_correlation_id_decisions": len({row.cluster_id for row in rows}),
            "measured_slippage_eligible_n": len(measured),
            "measured_slippage_decision_n": len({row.cluster_id for row in measured}),
            "mean_absolute_measured_slippage": round(statistics.fmean(values), 12) if values else None,
            "median_absolute_measured_slippage": round(statistics.median(values), 12) if values else None,
            "primary_sufficient": primary_ok,
            "known_status_n": len(known),
            "unknown_status_n": len(rows) - len(known),
            "successes": successes,
            "failures": failures,
            "failure_rate": round(failures / len(known), 12) if known else None,
            "rejection_decision_n": len({row.cluster_id for row in known}),
            "rejection_sufficient": rejection_ok,
            "rejection_interval_95": rejection_interval(rows),
            "endpoint_state": endpoint_state,
            "retcode_distribution": dict(sorted(retcodes.items())),
        }
    return metrics, tuple(primary_sufficient), unknown_session_count


def _invalid_evidence_report(exc: Exception) -> dict[str, Any]:
    return build_report(
        question_id="X3",
        status="INSUFFICIENT_DATA",
        overall={
            "n": 0,
            "classification": "INSUFFICIENT_EVIDENCE",
            "finding": "Invalid governed execution evidence",
            "evidence_error": f"{type(exc).__name__}: {exc}",
        },
        confidence="INSUFFICIENT_DATA",
        dataset={"sample_size": 0, "source": "execution_results_v1 + execution_context"},
        fingerprint={
            "dataset_id": "evidence_unverified", "records_used": 0,
            "records_excluded": 0, "source": "MULTI_SOURCE", "sources": [],
            "epoch": "UNVERIFIED", "architecture_version": "new_pipeline_v1.2",
            "validation_score": "INVALID_GOVERNED_EVIDENCE",
        },
        recommendation="WAIT_FOR_EVIDENCE",
        assumptions=["X3 fails closed when governed evidence cannot be constructed."],
        provenance={
            "experiment_module": "research_engine.experiments.execution_protection_research",
            "registry_id": "X3", "scientific_owner": "X3",
            "report_identity": REPORT_FILENAME,
        },
    )


def build_x3_report(
    execution_results: list[dict[str, Any]],
    execution_contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the canonical X3 report from supplied producer evidence."""
    raw_results = list(execution_results)
    raw_contexts = list(execution_contexts)
    try:
        evidence = build_governed_execution_evidence(raw_results, raw_contexts)
        metrics, primary_sessions, unknown_session_count = session_metrics(evidence.observations)
        overall_sufficient = evidence.account_result_count >= MIN_OVERALL_RESULTS
        analytical = [
            observation for observation in evidence.observations
            if _session(observation) in primary_sessions
            and observation.absolute_measured_slippage is not None
        ] if overall_sufficient and len(primary_sessions) >= 2 else []
        analytical_provenance = attest_execution_analytical_subset(evidence, analytical)
    except (TypeError, ValueError) as exc:
        return _invalid_evidence_report(exc)

    fit = fit_session_model(analytical, primary_sessions) if analytical else None
    omnibus_rejected = bool(fit and fit["omnibus_p_value"] <= ALPHA)
    try:
        contrasts = pairwise_contrasts(fit, omnibus_rejected=omnibus_rejected) if fit else []
    except ValueError:
        fit = None
        omnibus_rejected = False
        contrasts = []

    if fit:
        classification = (
            "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE" if omnibus_rejected
            else "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE"
        )
        status = "COMPLETE"
    else:
        classification = "INSUFFICIENT_EVIDENCE"
        status = "INSUFFICIENT_DATA"
    unique_best = (
        unique_supported_best(primary_sessions, contrasts)
        if fit and omnibus_rejected else "NO_UNIQUE_SUPPORTED_BEST"
    )
    measured_known_session = sum(
        1 for row in evidence.observations
        if _session(row) is not None and row.absolute_measured_slippage is not None
    )
    unknown_status_count = sum(
        row.status is ExecutionStatus.UNKNOWN for row in evidence.observations
    )
    overall = {
        "n": evidence.account_result_count,
        "finding": (
            "Reliable session-conditioned difference in producer-measured absolute slippage"
            if classification == "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE" else
            "No reliable session-conditioned difference in producer-measured absolute slippage"
            if classification == "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE" else
            "Insufficient evidence for governed session execution-quality inference"
        ),
        "classification": classification,
        "estimand": "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",
        "analytical_population": {
            "observation_grain": "one distinct account/broker execution result",
            "cluster": "correlation_id",
            "governed_matched_account_results": evidence.account_result_count,
            "distinct_correlation_id_decisions": evidence.distinct_decision_count,
            "analytical_account_results": len(analytical),
            "analytical_distinct_decisions": len({row.cluster_id for row in analytical}),
            "unknown_or_missing_session_results": unknown_session_count,
            "known_session_unmeasured_slippage_results": (
                evidence.account_result_count - unknown_session_count - measured_known_session
            ),
            "unknown_execution_status_results": unknown_status_count,
        },
        "sessions": metrics,
        "evaluated_primary_sessions": list(primary_sessions),
        "sufficiency": {
            "minimum_governed_matched_account_results": MIN_OVERALL_RESULTS,
            "minimum_measured_results_per_primary_session": MIN_SESSION_RESULTS,
            "minimum_distinct_decisions_per_primary_session": MIN_SESSION_DECISIONS,
            "minimum_primary_sufficient_sessions": 2,
            "overall_sufficient": overall_sufficient,
            "primary_sufficient_session_count": len(primary_sessions),
            "at_least_two_primary_sufficient_sessions": len(primary_sessions) >= 2,
            "model_and_cluster_covariance_estimable": fit is not None,
        },
        "estimator": {
            "model": "absolute_measured_slippage ~ categorical session",
            "coding": "deterministic reference coding",
            "reference_session": primary_sessions[0] if primary_sessions else None,
            "weights": "w_di = 1/k_d within the evaluated primary-session population",
            "cluster": "correlation_id",
            "cluster_score": "sum_i w_di * x_di * residual_di within correlation_id",
            "covariance": "A^-1 (sum_d s_d s_d') A^-1",
            "naive_row_independent_covariance_used": False,
            "cluster_total_weights": fit["cluster_total_weights"] if fit else {},
            "fitted_session_means": {
                name: round(value, 12)
                for name, value in (fit["fitted_means"].items() if fit else [])
            },
        },
        "global_primary_test": {
            "method": "cluster-robust Wald joint session-coefficient test",
            "null": "all sufficient evaluated sessions have equal mean absolute measured slippage",
            "alpha": ALPHA,
            "degrees_of_freedom": fit["degrees_of_freedom"] if fit else None,
            "statistic": round(fit["wald_statistic"], 12) if fit else None,
            "p_value": round(fit["omnibus_p_value"], 12) if fit else None,
            "reliable_session_slippage_difference": omnibus_rejected if fit else None,
        },
        "pairwise_primary_contrasts": {
            "family": "all pairs among primary-sufficient sessions",
            "declared_count": math.comb(len(primary_sessions), 2) if len(primary_sessions) >= 2 else 0,
            "multiplicity": "one global Holm step-down family",
            "alpha": ALPHA,
            "omnibus_rejection_required": True,
            "omnibus_in_holm_family": False,
            "results": contrasts,
        },
        "unique_best_session": unique_best,
        "secondary_rejection_endpoint": {
            "role": "descriptive only with cluster-aware uncertainty",
            "success": "result_ok is explicitly True",
            "failure": "result_ok is explicitly False",
            "unknown": "excluded from denominator and counted",
            "pairwise_hypothesis_tests_performed": False,
            "composite_score_used": False,
            "can_override_primary_classification": False,
        },
        "evidence_accounting": {
            "components": evidence.component_accounting,
            "join_and_identity_exclusions": evidence.exclusion_accounting,
            "primary_rows_outside_sufficient_sessions": measured_known_session - len(analytical),
        },
        "claim_boundary": (
            "CURRENT evidence and the listed primary-sufficient sessions only; "
            "measured execution slippage does not establish strategy expectancy or profitability"
        ),
    }
    fingerprint = build_fingerprint_from_provenance(
        analytical_provenance, validation_score="CURRENT_GOVERNED_X3"
    )
    return build_report(
        question_id="X3",
        status=status,
        overall=overall,
        confidence=(
            "HIGH" if len(analytical) >= 200 else
            "MEDIUM" if len(analytical) >= 60 else
            "LOW" if analytical else "INSUFFICIENT_DATA"
        ),
        dataset={
            "sample_size": evidence.account_result_count,
            "analysed_row_count": len(analytical),
            "source": "execution_results_v1 + execution_context",
            "records_loaded": len(raw_results) + len(raw_contexts),
        },
        fingerprint=fingerprint,
        recommendation=classification if status == "COMPLETE" else "WAIT_FOR_EVIDENCE",
        assumptions=[
            "CURRENT execution results and contexts are selected before strict correlation_id joining.",
            "Account fan-out children remain observations but each shared decision has total estimator weight one.",
            "The rejection endpoint is descriptive and cannot alter the primary slippage inference.",
        ],
        warnings=[
            "Claims are bounded to the listed sufficient sessions and CURRENT evidence population.",
            "Execution quality is not strategy profitability.",
        ],
        provenance={
            "experiment_module": "research_engine.experiments.execution_protection_research",
            "registry_id": "X3",
            "scientific_owner": "X3",
            "report_identity": REPORT_FILENAME,
            "shared_evidence_foundation": "research_engine.control_plane.execution_evidence",
        },
    )


__all__ = [
    "ALPHA", "CANONICAL_SESSION_ORDER", "MIN_OVERALL_RESULTS",
    "MIN_SESSION_DECISIONS", "MIN_SESSION_RESULTS", "REPORT_FILENAME",
    "build_x3_report", "fit_session_model", "pairwise_contrasts",
    "rejection_interval", "session_metrics", "unique_supported_best",
]
