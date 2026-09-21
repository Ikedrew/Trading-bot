"""S7 canonical StrategyFamily by evaluated-horizon interaction research.

The runner implements the adjudicated HD07 full-factorial model on CURRENT
normalized shadow horizon simulations.  Canonical opportunities are clusters,
their repeated rows have total weight one, and all claims are bounded to the
deterministically selected sufficient rectangular grid.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
import statistics
from typing import Any

from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.evidence_provenance import (
    attest_current_subset,
    build_evidence_provenance,
    select_current_evidence,
)
from research_engine.experiments.experiment_base import (
    build_fingerprint_from_provenance,
    build_report,
)
from research_engine.experiments.strategy_expectancy import (
    ACTIVE_FAMILIES,
    LEGACY_TAXONOMY,
    _identity,
)
from research_engine.experiments.strategy_identity_expectancy import (
    CANONICAL_HORIZONS,
    Z_95,
    _cluster_opportunities,
    _invert,
    _select_horizon_rows,
    _solve_linear,
)


REPORT_FILENAME = "s7_strategy_horizon_interaction.json"
MIN_OVERALL_OPPORTUNITIES = 150
MIN_CELL_OPPORTUNITIES = 30
MIN_GRID_FAMILIES = 2
MIN_GRID_HORIZONS = 2
ALPHA = 0.05

S7_SUFFICIENCY_CONTRACT = {
    "minimum_distinct_canonical_opportunities_overall": MIN_OVERALL_OPPORTUNITIES,
    "minimum_distinct_canonical_opportunities_per_included_cell": MIN_CELL_OPPORTUNITIES,
    "minimum_grid_families": MIN_GRID_FAMILIES,
    "minimum_grid_horizons": MIN_GRID_HORIZONS,
    "grid_selection": (
        "maximize valid cells, then families, then horizons, then canonical "
        "StrategyFamily order and canonical horizon order"
    ),
    "grid_selection_uses_outcomes": False,
}


def _cell_counts(clusters: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    cells = {
        (family, horizon): set()
        for family in ACTIVE_FAMILIES
        for horizon in CANONICAL_HORIZONS
    }
    for cluster in clusters:
        for horizon, _ in cluster["observations"]:
            cells[(cluster["family"], horizon)].add(cluster["opportunity_id"])
    return {cell: len(opportunities) for cell, opportunities in cells.items()}


def _select_maximal_grid(
    cell_counts: dict[tuple[str, str], int],
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Select HD07's maximal sufficient rectangle without inspecting outcomes."""
    candidates: list[tuple[tuple[int, int, int, tuple[int, ...], tuple[int, ...]],
                                tuple[str, ...], tuple[str, ...]]] = []
    for family_count in range(MIN_GRID_FAMILIES, len(ACTIVE_FAMILIES) + 1):
        for families in combinations(ACTIVE_FAMILIES, family_count):
            family_indices = tuple(ACTIVE_FAMILIES.index(item) for item in families)
            for horizon_count in range(MIN_GRID_HORIZONS, len(CANONICAL_HORIZONS) + 1):
                for horizons in combinations(CANONICAL_HORIZONS, horizon_count):
                    if not all(
                        cell_counts.get((family, horizon), 0) >= MIN_CELL_OPPORTUNITIES
                        for family in families for horizon in horizons
                    ):
                        continue
                    horizon_indices = tuple(CANONICAL_HORIZONS.index(item) for item in horizons)
                    key = (
                        -(family_count * horizon_count),
                        -family_count,
                        -horizon_count,
                        family_indices,
                        horizon_indices,
                    )
                    candidates.append((key, families, horizons))
    if not candidates:
        return None
    _, families, horizons = min(candidates, key=lambda item: item[0])
    return families, horizons


def _design_row(
    family_index: int, horizon_index: int, n_families: int, n_horizons: int,
) -> list[float]:
    """Reference-coded full-factorial design row in deterministic block order."""
    family_dummies = n_families - 1
    horizon_dummies = n_horizons - 1
    n_params = n_families * n_horizons
    row = [0.0] * n_params
    row[0] = 1.0
    if family_index:
        row[family_index] = 1.0
    if horizon_index:
        row[1 + family_dummies + horizon_index - 1] = 1.0
    if family_index and horizon_index:
        start = 1 + family_dummies + horizon_dummies
        offset = (family_index - 1) * horizon_dummies + horizon_index - 1
        row[start + offset] = 1.0
    return row


def _quadratic(vector: list[float], matrix: list[list[float]]) -> float:
    return sum(
        vector[i] * matrix[i][j] * vector[j]
        for i in range(len(vector)) for j in range(len(vector))
    )


def _chi_square_survival(statistic: float, degrees_of_freedom: int) -> float | None:
    """Regularized upper incomplete gamma Q(df/2, statistic/2), dependency-free."""
    if degrees_of_freedom <= 0 or not math.isfinite(statistic) or statistic < 0:
        return None
    if statistic == 0:
        return 1.0
    a = degrees_of_freedom / 2.0
    x = statistic / 2.0
    epsilon = 1e-14
    tiny = 1e-300
    iterations = 10000
    if x < a + 1.0:
        term = 1.0 / a
        total = term
        ap = a
        for _ in range(iterations):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) <= abs(total) * epsilon:
                lower = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
                return min(1.0, max(0.0, 1.0 - lower))
        return None
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / max(abs(b), tiny)
    if b < 0:
        d = -d
    fraction = d
    for index in range(1, iterations + 1):
        coefficient = -index * (index - a)
        b += 2.0
        d = coefficient * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        fraction *= delta
        if abs(delta - 1.0) <= epsilon:
            upper = math.exp(-x + a * math.log(x) - math.lgamma(a)) * fraction
            return min(1.0, max(0.0, upper))
    return None


def _two_sided_normal_p(estimate: float, standard_error: float) -> float | None:
    if not math.isfinite(estimate) or not math.isfinite(standard_error) or standard_error < 0:
        return None
    if standard_error == 0:
        return 1.0 if abs(estimate) <= 1e-12 else 0.0
    return math.erfc(abs(estimate / standard_error) / math.sqrt(2.0))


def _holm_adjust(p_values: list[float]) -> list[float]:
    """Deterministic Holm step-down adjusted p-values for one global family."""
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in p_values):
        raise ValueError("Holm adjustment requires finite p-values in [0, 1]")
    count = len(p_values)
    order = sorted(range(count), key=lambda index: (p_values[index], index))
    adjusted = [1.0] * count
    running = 0.0
    for rank, original_index in enumerate(order):
        running = max(running, (count - rank) * p_values[original_index])
        adjusted[original_index] = min(1.0, running)
    return adjusted


def _fit_interaction(
    clusters: list[dict[str, Any]],
    families: tuple[str, ...],
    horizons: tuple[str, ...],
) -> dict[str, Any] | None:
    """Fit HD07 weighted full factorial and clustered sandwich covariance."""
    family_index = {family: index for index, family in enumerate(families)}
    horizon_index = {horizon: index for index, horizon in enumerate(horizons)}
    n_families = len(families)
    n_horizons = len(horizons)
    n_params = n_families * n_horizons
    rows: list[tuple[str, int, int, float, float]] = []
    opportunity_weights: dict[str, float] = {}
    for cluster in clusters:
        if cluster["family"] not in family_index:
            continue
        observations = sorted(
            (horizon, float(value))
            for horizon, value in cluster["observations"] if horizon in horizon_index
        )
        if not observations:
            continue
        weight = 1.0 / len(observations)
        opportunity_weights[cluster["opportunity_id"]] = sum(weight for _ in observations)
        for horizon, value in observations:
            rows.append((cluster["opportunity_id"], family_index[cluster["family"]],
                         horizon_index[horizon], value, weight))
    if not rows:
        return None

    normal = [[0.0] * n_params for _ in range(n_params)]
    rhs = [0.0] * n_params
    for _, family, horizon, value, weight in rows:
        x = _design_row(family, horizon, n_families, n_horizons)
        for i in range(n_params):
            rhs[i] += weight * x[i] * value
            for j in range(n_params):
                normal[i][j] += weight * x[i] * x[j]
    beta = _solve_linear(normal, rhs)
    bread_inverse = _invert(normal)
    if beta is None or bread_inverse is None:
        return None

    scores: dict[str, list[float]] = {}
    for opportunity_id, family, horizon, value, weight in rows:
        x = _design_row(family, horizon, n_families, n_horizons)
        residual = value - sum(beta[index] * x[index] for index in range(n_params))
        score = scores.setdefault(opportunity_id, [0.0] * n_params)
        for index in range(n_params):
            score[index] += weight * x[index] * residual
    meat = [[0.0] * n_params for _ in range(n_params)]
    for opportunity_id in sorted(scores):
        score = scores[opportunity_id]
        for i in range(n_params):
            for j in range(n_params):
                meat[i][j] += score[i] * score[j]
    covariance = [[
        sum(
            bread_inverse[i][k] * meat[k][l] * bread_inverse[l][j]
            for k in range(n_params) for l in range(n_params)
        )
        for j in range(n_params)] for i in range(n_params)
    ]

    interaction_start = 1 + (n_families - 1) + (n_horizons - 1)
    interaction_indices = list(range(interaction_start, n_params))
    interaction_beta = [beta[index] for index in interaction_indices]
    interaction_covariance = [
        [covariance[i][j] for j in interaction_indices] for i in interaction_indices
    ]
    interaction_inverse = _invert(interaction_covariance)
    if interaction_inverse is None:
        return None
    statistic = max(0.0, _quadratic(interaction_beta, interaction_inverse))
    p_value = _chi_square_survival(statistic, len(interaction_indices))
    if p_value is None or not math.isfinite(p_value):
        return None

    return {
        "beta": beta,
        "covariance": covariance,
        "rows": rows,
        "opportunity_total_weights": opportunity_weights,
        "interaction_df": len(interaction_indices),
        "wald_statistic": statistic,
        "omnibus_p_value": p_value,
        "n_params": n_params,
    }


def _cell_contrast(
    family: str, horizon: str, families: tuple[str, ...], horizons: tuple[str, ...],
) -> list[float]:
    return _design_row(families.index(family), horizons.index(horizon), len(families), len(horizons))


def _followup_contrasts(
    fit: dict[str, Any], families: tuple[str, ...], horizons: tuple[str, ...],
    *, omnibus_rejected: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    raw_p_values: list[float] = []
    beta = fit["beta"]
    covariance = fit["covariance"]
    for family in families:
        for left_index, left in enumerate(horizons):
            for right in horizons[left_index + 1:]:
                left_design = _cell_contrast(family, left, families, horizons)
                right_design = _cell_contrast(family, right, families, horizons)
                contrast = [a - b for a, b in zip(left_design, right_design)]
                estimate = sum(contrast[index] * beta[index] for index in range(len(beta)))
                variance = _quadratic(contrast, covariance)
                if variance < -1e-10 or not math.isfinite(variance):
                    raise ValueError("invalid clustered contrast variance")
                standard_error = math.sqrt(max(0.0, variance))
                raw_p = _two_sided_normal_p(estimate, standard_error)
                if raw_p is None:
                    raise ValueError("invalid clustered contrast p-value")
                raw_p_values.append(raw_p)
                results.append({
                    "strategy_family": family,
                    "left_horizon": left,
                    "right_horizon": right,
                    "contrast": f"{left} vs {right}",
                    "estimate_r": round(estimate, 6),
                    "standard_error": round(standard_error, 6),
                    "interval_95": {
                        "method": "opportunity_clustered_sandwich",
                        "lower_r": round(estimate - Z_95 * standard_error, 6),
                        "upper_r": round(estimate + Z_95 * standard_error, 6),
                        "z": Z_95,
                    },
                    "raw_p_value": round(raw_p, 12),
                    "holm_adjusted_p_value": None,
                    "adjusted_significant": False,
                    "inferential_claim_permitted": False,
                })
    if omnibus_rejected:
        adjusted = _holm_adjust(raw_p_values)
        for result, adjusted_p in zip(results, adjusted):
            result["holm_adjusted_p_value"] = round(adjusted_p, 12)
            result["adjusted_significant"] = adjusted_p <= ALPHA
            result["inferential_claim_permitted"] = adjusted_p <= ALPHA
    return results


def _descriptive_cells(
    clusters: list[dict[str, Any]], families: tuple[str, ...], horizons: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for family in families:
        for horizon in horizons:
            observations = [
                (cluster["opportunity_id"], float(value))
                for cluster in clusters if cluster["family"] == family
                for observed_horizon, value in cluster["observations"]
                if observed_horizon == horizon
            ]
            values = [value for _, value in observations]
            result[f"{family}|{horizon}"] = {
                "n_distinct_canonical_opportunities": len({item[0] for item in observations}),
                "row_count": len(values),
                "mean_r": round(statistics.fmean(values), 6) if values else None,
                "median_r": round(statistics.median(values), 6) if values else None,
                "win_rate": round(sum(value > 0 for value in values) / len(values), 6) if values else None,
            }
    return result


def run_s7(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Answer S7 from the exact CURRENT selected-grid repeated-row population."""
    if shadow_trades is None:
        from research_engine.data_access.shadow_runtime_ingestion import ingest_completed_shadow_trades
        raw = ingest_completed_shadow_trades()
    else:
        raw = list(shadow_trades)

    epoch_selection = select_current_evidence("shadow_trades", raw)
    current = epoch_selection.records_for_analysis()
    eligible_rows, exclusions = _select_horizon_rows(current)
    eligible_clusters, cluster_exclusions = _cluster_opportunities(eligible_rows)
    exclusions.update(cluster_exclusions)
    counts = _cell_counts(eligible_clusters)
    grid = _select_maximal_grid(counts)
    overall_n = len(eligible_clusters)

    selected_families: tuple[str, ...] = grid[0] if grid else ()
    selected_horizons: tuple[str, ...] = grid[1] if grid else ()
    accepted_opportunities = {
        cluster["opportunity_id"] for cluster in eligible_clusters
        if cluster["family"] in selected_families
        and any(horizon in selected_horizons for horizon, _ in cluster["observations"])
    }
    analytical_rows = [
        row for row in eligible_rows
        if str(_identity(row).get("canonical_opportunity_id", "")) in accepted_opportunities
        and str(_identity(row).get("strategy_id", "")) in selected_families
        and str(_identity(row).get("evaluated_horizon", "")) in selected_horizons
    ]
    analysis_selection = attest_current_subset("shadow_trades", raw, analytical_rows)
    analysed = analysis_selection.records_for_analysis()
    provenance = build_evidence_provenance(analysis_selection)
    fingerprint = build_fingerprint_from_provenance(provenance, validation_score="CURRENT")
    analytical_clusters, analytical_cluster_exclusions = _cluster_opportunities(analysed)
    exclusions.update(analytical_cluster_exclusions)

    overall_sufficient = overall_n >= MIN_OVERALL_OPPORTUNITIES
    fit = (
        _fit_interaction(analytical_clusters, selected_families, selected_horizons)
        if grid and overall_sufficient else None
    )
    estimable = fit is not None
    omnibus_rejected = bool(estimable and fit["omnibus_p_value"] <= ALPHA)
    if estimable:
        classification = "RELIABLE_INTERACTION" if omnibus_rejected else "NO_RELIABLE_INTERACTION"
        status = "COMPLETE"
    else:
        classification = "INSUFFICIENT_EVIDENCE"
        status = "INSUFFICIENT_DATA"

    try:
        followups = _followup_contrasts(
            fit, selected_families, selected_horizons, omnibus_rejected=omnibus_rejected,
        ) if fit else []
    except ValueError:
        fit = None
        estimable = False
        omnibus_rejected = False
        classification = "INSUFFICIENT_EVIDENCE"
        status = "INSUFFICIENT_DATA"
        followups = []

    selected_cells = {
        (family, horizon) for family in selected_families for horizon in selected_horizons
    }
    excluded_sparse_cells = [
        f"{family}|{horizon}" for family in ACTIVE_FAMILIES for horizon in CANONICAL_HORIZONS
        if counts[(family, horizon)] < MIN_CELL_OPPORTUNITIES
    ]
    all_cell_counts = {
        f"{family}|{horizon}": counts[(family, horizon)]
        for family in ACTIVE_FAMILIES for horizon in CANONICAL_HORIZONS
    }
    analytical_opportunities = len({cluster["opportunity_id"] for cluster in analytical_clusters})
    overall = {
        "finding": (
            "Reliable StrategyFamily by evaluated-horizon interaction in the selected sufficient grid"
            if classification == "RELIABLE_INTERACTION" else
            "No reliable StrategyFamily by evaluated-horizon interaction in the selected sufficient grid"
            if classification == "NO_RELIABLE_INTERACTION" else
            "Insufficient evidence for the governed StrategyFamily by horizon interaction test"
        ),
        "classification": classification,
        "estimand": "STRATEGY_FAMILY_X_EVALUATED_HORIZON_INTERACTION",
        "taxonomy": {
            "authority": "core.v10.strategy_family.StrategyFamily",
            "active_families": list(ACTIVE_FAMILIES),
            "excluded_value": StrategyFamily.NONE.value,
            "legacy_compatibility_only": list(LEGACY_TAXONOMY),
            "legacy_mapping_inferred": False,
        },
        "horizon_taxonomy": {
            "authority": "core.horizon.horizon_models.TradeHorizon",
            "canonical_horizons": list(CANONICAL_HORIZONS),
            "unsupported_horizons_bucketed": False,
        },
        "selected_grid": {
            "families": list(selected_families),
            "horizons": list(selected_horizons),
            "dimensions": [len(selected_families), len(selected_horizons)],
            "included_cell_count": len(selected_cells),
            "selection_objective": S7_SUFFICIENCY_CONTRACT["grid_selection"],
            "outcome_values_used_for_selection": False,
            "claim_boundary": "claims apply only to this selected sufficient evaluated grid",
        },
        "population": {
            "total_distinct_eligible_opportunities": overall_n,
            "analytical_distinct_opportunities": analytical_opportunities,
            "analytical_repeated_row_count": len(analysed),
        },
        "cells": {
            "canonical_cell_count": len(ACTIVE_FAMILIES) * len(CANONICAL_HORIZONS),
            "included_cell_count": len(selected_cells),
            "cell_n_distinct_canonical_opportunities": all_cell_counts,
            "excluded_sparse_cells": excluded_sparse_cells,
            "excluded_families": [item for item in ACTIVE_FAMILIES if item not in selected_families],
            "excluded_horizons": [item for item in CANONICAL_HORIZONS if item not in selected_horizons],
            "excluded_cell_reasons": {
                f"{family}|{horizon}": (
                    "below_30_distinct_opportunities"
                    if counts[(family, horizon)] < MIN_CELL_OPPORTUNITIES
                    else "outside_deterministic_maximal_grid"
                )
                for family in ACTIVE_FAMILIES for horizon in CANONICAL_HORIZONS
                if (family, horizon) not in selected_cells
            },
            "descriptive_selected_cells": _descriptive_cells(
                analytical_clusters, selected_families, selected_horizons,
            ),
        },
        "estimator": {
            "model": "pnl_r_multiple ~ StrategyFamily + Horizon + StrategyFamily:Horizon",
            "coding": "deterministic reference coding",
            "weights": "w_oh = 1/k_o within the selected analytical grid",
            "cluster": "identity.canonical_opportunity_id",
            "cluster_score_aggregation": "sum row scores within canonical opportunity before outer product",
            "covariance": "A^-1 (sum_o s_o s_o') A^-1",
            "naive_row_independent_covariance_used": False,
            "estimable": estimable,
            "interaction_degrees_of_freedom": fit["interaction_df"] if fit else None,
            "opportunity_total_weights": fit["opportunity_total_weights"] if fit else {},
        },
        "omnibus_test": {
            "method": "cluster-robust Wald joint interaction-block test",
            "null": "all interaction terms in the selected sufficient grid are jointly zero",
            "alpha": ALPHA,
            "degrees_of_freedom": fit["interaction_df"] if fit else None,
            "statistic": round(fit["wald_statistic"], 12) if fit else None,
            "p_value": round(fit["omnibus_p_value"], 12) if fit else None,
            "reliable_interaction": omnibus_rejected if fit else None,
        },
        "followup_contrasts": {
            "family": "within-strategy pairwise horizon contrasts only",
            "multiplicity": "one global Holm step-down family",
            "alpha": ALPHA,
            "omnibus_rejection_required_for_inferential_claim": True,
            "omnibus_in_holm_family": False,
            "declared_count": len(selected_families) * math.comb(len(selected_horizons), 2) if grid else 0,
            "results": followups,
        },
        "sufficiency": {
            **S7_SUFFICIENCY_CONTRACT,
            "overall_sufficient": overall_sufficient,
            "valid_minimum_grid": grid is not None,
            "selected_cells_sufficient": bool(grid and all(
                counts[cell] >= MIN_CELL_OPPORTUNITIES for cell in selected_cells
            )),
            "model_and_covariance_estimable": estimable,
        },
        "exclusions": {
            **Counter(exclusions),
            "outside_selected_grid": len(eligible_rows) - len(analysed),
            "non_current_or_incompatible": (
                analysis_selection.component["input_records"]
                - epoch_selection.component["records_used"]
            ),
            "total_not_analysed": analysis_selection.component["records_excluded"],
        },
    }
    confidence = (
        "HIGH" if overall_n >= 300 else "MEDIUM" if overall_n >= 150 else
        "LOW" if overall_n >= 30 else "INSUFFICIENT_DATA"
    )
    return build_report(
        question_id="S7",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={
            "source": "shadow_trades_v1 normalized from shadow_runtime_v1",
            "sample_size": overall_n,
            "analysed_row_count": len(analysed),
            "records_loaded": len(raw),
        },
        fingerprint=fingerprint,
        recommendation=(classification if status == "COMPLETE" else "WAIT_FOR_EVIDENCE"),
        assumptions=[
            "CURRENT completed horizon simulations are selected before analysis.",
            "Grid selection uses identity and distinct-opportunity counts only.",
            "Repeated horizons are clustered by canonical_opportunity_id and total weight one.",
            "Interaction claims are bounded to the selected sufficient grid.",
        ],
        warnings=[
            "Shadow outcomes are simulated research evidence, not realised live P&L.",
            "Descriptive cell metrics are not additional hypothesis tests.",
        ],
        provenance={
            "experiment_module": __name__,
            "registry_id": "S7",
            "scientific_owner": "S7",
            "estimand": "STRATEGY_FAMILY_X_EVALUATED_HORIZON_INTERACTION",
            "hd07": "ADJUDICATED",
        },
    )


__all__ = [
    "ALPHA", "MIN_CELL_OPPORTUNITIES", "MIN_OVERALL_OPPORTUNITIES",
    "REPORT_FILENAME", "S7_SUFFICIENCY_CONTRACT", "run_s7",
]
