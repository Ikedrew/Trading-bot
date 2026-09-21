"""S5 canonical horizon-adjusted V10 strategy-family expectancy research.

S5 is the sole scientific owner of ``s5_strategy_identity_expectancy.json``.

S5 asks which CURRENT V10 StrategyFamily values retain expectancy AFTER
accounting for the evaluated trade horizon (SCALP / INTRADAY / EXTENDED) under
which each outcome was simulated.  This is distinct from E3, which asks whether
strategy families show expectancy at one PRIMARY_HORIZON_SIMULATION observation
per canonical opportunity without horizon adjustment.

Per the adjudicated HD06 contract:

* one canonical opportunity (``canonical_opportunity_id``) is one statistical
  cluster;
* the horizon simulations belonging to one canonical opportunity are repeated
  measurements of the SAME opportunity, never independent observations, and an
  opportunity's total statistical influence is fixed at weight one;
* sufficiency requires >=100 distinct canonical opportunities overall,
  >=30 per StrategyFamily, and >=20 in every required StrategyFamily x horizon
  cell (all 6 x 3 canonical cells; no structural exemptions exist);
* uncertainty is cluster-aware (opportunity-clustered sandwich), never an
  ordinary row-level interval that pseudo-replicates horizon rows.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import statistics
from typing import Any

from core.horizon.horizon_models import TradeHorizon
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
    ACTIVE_FAMILY_SET,
    LEGACY_TAXONOMY,
    _finite_r,
    _identity,
)

REPORT_FILENAME = "s5_strategy_identity_expectancy.json"

# Completed legitimate horizon-simulation rows: the selected incumbent
# horizon outcome plus the governed counterfactual horizon alternatives.
HORIZON_SIMULATION_TYPES = ("PRIMARY_HORIZON_SIMULATION", "HORIZON_ALTERNATIVE")

# Authoritative horizon taxonomy (reused, never redefined).
CANONICAL_HORIZONS = tuple(horizon.value for horizon in TradeHorizon)

MIN_OVERALL_OPPORTUNITIES = 100
MIN_FAMILY_OPPORTUNITIES = 30
MIN_CELL_OPPORTUNITIES = 20
Z_95 = 1.96

REQUIRED_CELL_COUNT = len(ACTIVE_FAMILIES) * len(CANONICAL_HORIZONS)

S5_SUFFICIENCY_CONTRACT = {
    "minimum_distinct_canonical_opportunities_overall": 100,
    "minimum_distinct_canonical_opportunities_per_family": 30,
    "minimum_distinct_canonical_opportunities_per_family_horizon_cell": 20,
    "required_family_horizon_cells": REQUIRED_CELL_COUNT,
    "structural_cell_exemptions": 0,
    "uncertainty": (
        "95% opportunity-clustered sandwich interval (mean +/- 1.96 cluster-robust SEs)"
    ),
    "below_minimum": "INSUFFICIENT_EVIDENCE",
}


# ─────────────────────────────────────────────────────────────────────────────
# Eligible CURRENT horizon-simulation rows
# ─────────────────────────────────────────────────────────────────────────────


def _select_horizon_rows(
    current_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Keep only legitimate completed horizon simulations, excluding by count."""
    exclusions: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    for record in current_records:
        ident = _identity(record)
        shadow_type = str(ident.get("shadow_type", "") or "")
        if shadow_type not in HORIZON_SIMULATION_TYPES:
            exclusions["non_horizon_simulation_row"] += 1
            continue
        opportunity_id = str(ident.get("canonical_opportunity_id", "") or "")
        if not opportunity_id:
            exclusions["missing_canonical_opportunity_id"] += 1
            continue
        family = str(ident.get("strategy_id", "") or "")
        if family == StrategyFamily.NONE.value:
            exclusions["strategy_family_none"] += 1
            continue
        if family not in ACTIVE_FAMILY_SET:
            # Historical three-family names are compatibility/history only and
            # are never silently mapped onto a V10 family.
            exclusions["unknown_or_missing_strategy_family"] += 1
            continue
        horizon = str(ident.get("evaluated_horizon", "") or "")
        if horizon not in CANONICAL_HORIZONS:
            # Unknown/unsupported horizons are never silently bucketed.
            exclusions["unknown_or_unsupported_horizon"] += 1
            continue
        if _finite_r(record) is None:
            exclusions["missing_or_non_finite_outcome"] += 1
            continue
        eligible.append(record)
    return eligible, exclusions


def _row_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    ident = _identity(record)
    return (
        str(ident.get("evaluated_horizon", "") or ""),
        str(ident.get("shadow_type", "") or ""),
        str(ident.get("shadow_trade_id", "") or ""),
    )


def _cluster_opportunities(
    eligible_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Cluster eligible rows by canonical_opportunity_id, failing closed.

    One cluster is one canonical opportunity.  A cluster is rejected with
    explicit accounting when its rows carry conflicting strategy identities or
    conflicting/duplicate horizon simulations; such evidence is ambiguous and
    is never silently merged.
    """
    exclusions: Counter[str] = Counter()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in eligible_rows:
        grouped[str(_identity(record)["canonical_opportunity_id"])].append(record)

    clusters: list[dict[str, Any]] = []
    for opportunity_id in sorted(grouped):
        rows = sorted(grouped[opportunity_id], key=_row_sort_key)
        families = {str(_identity(row)["strategy_id"]) for row in rows}
        if len(families) != 1:
            exclusions["conflicting_strategy_identity_within_opportunity"] += len(rows)
            continue
        horizons = [str(_identity(row)["evaluated_horizon"]) for row in rows]
        if len(set(horizons)) != len(horizons):
            exclusions["duplicate_or_conflicting_horizon_simulation"] += len(rows)
            continue
        clusters.append({
            "opportunity_id": opportunity_id,
            "family": families.pop(),
            "observations": [
                (str(_identity(row)["evaluated_horizon"]), _finite_r(row))
                for row in rows
            ],
        })
    return clusters, exclusions


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic horizon-adjusted estimator (cluster-aware)
# ─────────────────────────────────────────────────────────────────────────────


def _design_row(family_index: int, horizon_index: int, n_params: int) -> list[float]:
    """Reference-coded two-way additive design row.

    Parameter 0 is the intercept; parameters 1..5 are family effects for
    families 1..5 (family 0 is the reference); parameters 6..7 are horizon
    effects for horizons 1..2 (SCALP is the reference).
    """
    x = [0.0] * n_params
    x[0] = 1.0
    if family_index > 0:
        x[family_index] = 1.0
    if horizon_index > 0:
        x[len(ACTIVE_FAMILIES) - 1 + horizon_index] = 1.0
    return x


def _family_contrast(
    family_index: int, n_params: int, n_horizons: int, n_family_dummies: int
) -> list[float]:
    """Contrast for the horizon-adjusted family effect.

    The adjusted effect is the equal-weighted average of the family's fitted
    means across ALL canonical horizons, removing horizon-composition
    differences: c = intercept + family dummy + mean of horizon effects.
    """
    c = [0.0] * n_params
    c[0] = 1.0
    if family_index > 0:
        c[family_index] = 1.0
    for horizon_index in range(1, n_horizons):
        c[1 + n_family_dummies + (horizon_index - 1)] = 1.0 / n_horizons
    return c


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Deterministic Gaussian elimination with partial pivoting."""
    n = len(rhs)
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        for row in range(n):
            if row != col and augmented[row][col] != 0.0:
                factor = augmented[row][col] / augmented[col][col]
                for k in range(col, n + 1):
                    augmented[row][k] -= factor * augmented[col][k]
    return [augmented[i][n] / augmented[i][i] for i in range(n)]


def _invert(matrix: list[list[float]]) -> list[list[float]] | None:
    n = len(matrix)
    columns: list[list[float]] = []
    for i in range(n):
        unit = [1.0 if j == i else 0.0 for j in range(n)]
        column = _solve_linear(matrix, unit)
        if column is None:
            return None
        columns.append(column)
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def _estimate_family_effects(
    clusters: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Fit the horizon-adjusted family model on opportunity clusters.

    Model (deterministic weighted least squares, one row per horizon
    simulation):

        y_oh = mu + beta_f(o) + alpha_h(o) + e_oh,     w_oh = 1 / k_o

    where k_o is the number of horizon simulations for opportunity o, so every
    canonical opportunity contributes total weight exactly one regardless of
    how many horizons were simulated for it.  The horizon-adjusted family
    effect is the equal-weighted average of the family's fitted means across
    all canonical horizons.  Uncertainty is the opportunity-clustered sandwich
    (cluster-robust) covariance of the fitted contrast.
    """
    family_index = {family: index for index, family in enumerate(ACTIVE_FAMILIES)}
    horizon_index = {horizon: index for index, horizon in enumerate(CANONICAL_HORIZONS)}
    n_families = len(ACTIVE_FAMILIES)
    n_horizons = len(CANONICAL_HORIZONS)
    n_params = 1 + (n_families - 1) + (n_horizons - 1)

    rows: list[tuple[str, int, int, float, float]] = []
    for cluster in clusters:
        weight = 1.0 / len(cluster["observations"])
        family = family_index[cluster["family"]]
        for horizon, value in sorted(cluster["observations"]):
            rows.append((
                cluster["opportunity_id"], family, horizon_index[horizon],
                float(value), weight,
            ))

    if len({row[1] for row in rows}) < n_families or len({row[2] for row in rows}) < n_horizons:
        # The additive model is not estimable; every family stays insufficient.
        return None

    normal: list[list[float]] = [[0.0] * n_params for _ in range(n_params)]
    rhs = [0.0] * n_params
    for _, family, horizon, value, weight in rows:
        x = _design_row(family, horizon, n_params)
        for i in range(n_params):
            if x[i] == 0.0:
                continue
            rhs[i] += weight * x[i] * value
            normal_row = normal[i]
            x_i = x[i]
            for j in range(n_params):
                normal_row[j] += weight * x_i * x[j]

    beta = _solve_linear(normal, rhs)
    if beta is None:
        return None
    normal_inverse = _invert(normal)
    if normal_inverse is None:
        return None

    # Opportunity-clustered sandwich meat: sum of outer products of the
    # weight-one-clustered score contributions.
    scores: dict[str, list[float]] = {}
    for opportunity_id, family, horizon, value, weight in rows:
        x = _design_row(family, horizon, n_params)
        fitted = sum(beta[i] * x[i] for i in range(n_params))
        residual = value - fitted
        score = scores.setdefault(opportunity_id, [0.0] * n_params)
        for i in range(n_params):
            if x[i] != 0.0:
                score[i] += weight * residual * x[i]

    meat: list[list[float]] = [[0.0] * n_params for _ in range(n_params)]
    for opportunity_id in sorted(scores):
        score = scores[opportunity_id]
        for i in range(n_params):
            if score[i] == 0.0:
                continue
            meat_row = meat[i]
            s_i = score[i]
            for j in range(n_params):
                meat_row[j] += s_i * score[j]

    covariance = [
        [
            sum(
                normal_inverse[i][k] * meat[k][l] * normal_inverse[l][j]
                for k in range(n_params)
                for l in range(n_params)
            )
            for j in range(n_params)
        ]
        for i in range(n_params)
    ]

    n_family_dummies = n_families - 1
    effects: dict[str, dict[str, Any]] = {}
    for family in ACTIVE_FAMILIES:
        contrast = _family_contrast(
            family_index[family], n_params, n_horizons, n_family_dummies,
        )
        estimate = sum(contrast[i] * beta[i] for i in range(n_params))
        variance = sum(
            contrast[i] * covariance[i][j] * contrast[j]
            for i in range(n_params)
            for j in range(n_params)
        )
        standard_error = math.sqrt(max(variance, 0.0))
        effects[family] = {
            "estimate_r": round(estimate, 6),
            "standard_error": round(standard_error, 6),
            "interval_95": {
                "method": "opportunity_clustered_sandwich",
                "cluster": "canonical_opportunity_id",
                "lower_r": round(estimate - Z_95 * standard_error, 6),
                "upper_r": round(estimate + Z_95 * standard_error, 6),
                "z": Z_95,
            },
        }

    horizon_effects = {
        horizon: round(
            beta[1 + n_family_dummies + index - 1] if index > 0 else 0.0,
            6,
        )
        for index, horizon in enumerate(CANONICAL_HORIZONS)
    }
    return {
        "effects": effects,
        "horizon_effects_r_vs_scalp": horizon_effects,
        "estimable": True,
    }




# ─────────────────────────────────────────────────────────────────────────────
# Canonical runner
# ─────────────────────────────────────────────────────────────────────────────


def _descriptive_result(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Row-level descriptive metrics; never a substitute for the adjusted effect."""
    values = [
        value
        for cluster in clusters
        for _, value in cluster["observations"]
    ]
    n = len(values)
    return {
        "n_distinct_canonical_opportunities": len(clusters),
        "row_count": n,
        "mean_r_rows": round(statistics.mean(values), 6) if values else None,
        "median_r_rows": round(statistics.median(values), 6) if values else None,
        "win_rate_rows": (
            round(sum(value > 0 for value in values) / n, 6) if n else None
        ),
    }


def run_s5(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Answer S5 from the canonical completed shadow lifecycle population.

    Unlike E3 (PRIMARY_HORIZON_SIMULATION only), S5 uses the governed
    PRIMARY_HORIZON_SIMULATION and HORIZON_ALTERNATIVE completed rows — the
    canonical horizon simulations — clustered by canonical_opportunity_id.
    """
    if shadow_trades is None:
        from research_engine.data_access.shadow_runtime_ingestion import (
            ingest_completed_shadow_trades,
        )
        raw = ingest_completed_shadow_trades()
    else:
        raw = list(shadow_trades)

    epoch_selection = select_current_evidence("shadow_trades", raw)
    current = epoch_selection.records_for_analysis()
    eligible_rows, exclusions = _select_horizon_rows(current)
    analysis_selection = attest_current_subset("shadow_trades", raw, eligible_rows)
    analysed = analysis_selection.records_for_analysis()
    provenance = build_evidence_provenance(analysis_selection)
    fingerprint = build_fingerprint_from_provenance(provenance, validation_score="CURRENT")

    clusters, cluster_exclusions = _cluster_opportunities(analysed)
    exclusions.update(cluster_exclusions)

    grouped: dict[str, list[dict[str, Any]]] = {
        family: [] for family in ACTIVE_FAMILIES
    }
    cell_clusters: dict[tuple[str, str], set[str]] = {
        (family, horizon): set()
        for family in ACTIVE_FAMILIES
        for horizon in CANONICAL_HORIZONS
    }
    for cluster in clusters:
        grouped[cluster["family"]].append(cluster)
        for horizon, _value in cluster["observations"]:
            cell_clusters[(cluster["family"], horizon)].add(cluster["opportunity_id"])

    cell_n = {
        f"{family}|{horizon}": len(cell_clusters[(family, horizon)])
        for family in ACTIVE_FAMILIES
        for horizon in CANONICAL_HORIZONS
    }
    insufficient_cells = sorted(
        cell for cell, count in cell_n.items() if count < MIN_CELL_OPPORTUNITIES
    )

    overall_n = len(clusters)
    overall_sufficient = overall_n >= MIN_OVERALL_OPPORTUNITIES
    families_sufficient = all(
        len(grouped[family]) >= MIN_FAMILY_OPPORTUNITIES for family in ACTIVE_FAMILIES
    )
    cells_sufficient = not insufficient_cells

    estimation = _estimate_family_effects(clusters)
    estimable = bool(estimation and estimation["estimable"])

    family_results: dict[str, dict[str, Any]] = {}
    classifications: list[str] = []
    for family in ACTIVE_FAMILIES:
        family_clusters = grouped[family]
        result = _descriptive_result(family_clusters)
        result["horizon_coverage"] = {
            horizon: {
                "n_distinct_canonical_opportunities": len(
                    cell_clusters[(family, horizon)]
                ),
            }
            for horizon in CANONICAL_HORIZONS
        }
        family_gate_ok = (
            overall_sufficient
            and len(family_clusters) >= MIN_FAMILY_OPPORTUNITIES
            and all(
                len(cell_clusters[(family, horizon)]) >= MIN_CELL_OPPORTUNITIES
                for horizon in CANONICAL_HORIZONS
            )
        )
        adjusted = estimation["effects"][family] if estimation else None
        if not family_gate_ok or not estimable or adjusted is None:
            classification = "INSUFFICIENT_EVIDENCE"
        else:
            lower = adjusted["interval_95"]["lower_r"]
            classification = (
                "POSITIVE_EVIDENCE" if lower > 0 else "NON_POSITIVE_EVIDENCE"
            )
        classifications.append(classification)
        result["adjusted_effect"] = {
            "estimate_r": adjusted["estimate_r"] if adjusted else None,
            "standard_error": adjusted["standard_error"] if adjusted else None,
            "interval_95": adjusted["interval_95"] if adjusted else None,
            "classification": classification,
        }
        family_results[family] = result

    all_classified = all(
        item in ("POSITIVE_EVIDENCE", "NON_POSITIVE_EVIDENCE")
        for item in classifications
    )
    complete = (
        overall_sufficient and families_sufficient and cells_sufficient
        and estimable and all_classified
    )
    status = "COMPLETE" if complete else "INSUFFICIENT_DATA"
    confidence = (
        "HIGH" if overall_n >= 200 else "MEDIUM" if overall_n >= 100 else (
            "LOW" if overall_n >= 30 else "INSUFFICIENT_DATA"
        )
    )


    overall = {
        "finding": (
            "V10 strategy-family expectancy classified after accounting for evaluated horizon"
            if complete
            else "Insufficient evidence for complete horizon-adjusted strategy-family classification"
        ),
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
        "estimand": "HORIZON_ADJUSTED_STRATEGY_FAMILY_EFFECT",
        "estimator": {
            "model": (
                "weighted least squares: pnl_r_multiple ~ intercept + StrategyFamily "
                "+ evaluated_horizon (two-way additive, reference coding)"
            ),
            "weights": (
                "row weight 1/k_o; each canonical opportunity's total weight is fixed at one"
            ),
            "adjusted_family_effect": (
                "equal-weighted average of the family's fitted means across all "
                "canonical horizons (removes horizon-composition differences)"
            ),
            "uncertainty": S5_SUFFICIENCY_CONTRACT["uncertainty"],
            "estimable": estimable,
            "horizon_effects_r_vs_scalp": (
                estimation["horizon_effects_r_vs_scalp"] if estimation else None
            ),
        },
        "unit_of_analysis": (
            "one canonical_opportunity_id cluster; horizon simulations are repeated "
            "measurements and never independent opportunities"
        ),
        "distinct_canonical_opportunities": overall_n,
        "analysed_row_count": len(analysed),
        "families": family_results,
        "cells": {
            "required_cells": REQUIRED_CELL_COUNT,
            "cell_rule": (
                "all 6 canonical StrategyFamily x 3 canonical horizon cells are "
                "required; no structural exemptions exist"
            ),
            "cell_n_distinct_canonical_opportunities": cell_n,
            "insufficient_cells": insufficient_cells,
        },
        "sufficiency": {
            **S5_SUFFICIENCY_CONTRACT,
            "overall_sufficient": overall_sufficient,
            "families_sufficient": families_sufficient,
            "cells_sufficient": cells_sufficient,
        },
        "exclusions": {
            **exclusions,
            "non_current_or_incompatible": analysis_selection.component["input_records"]
            - epoch_selection.component["records_used"],
            "total_not_analysed": analysis_selection.component["records_excluded"],
        },
        "classification_rule": (
            "Gates unmet (overall <100, family <30, any required cell <20, or a "
            "non-estimable model) => INSUFFICIENT_EVIDENCE; otherwise POSITIVE_"
            "EVIDENCE only when the adjusted-effect 95% cluster-aware interval "
            "lower bound is >0, else NON_POSITIVE_EVIDENCE. A positive raw mean "
            "alone never establishes positive evidence."
        ),
    }

    return build_report(
        question_id="S5",
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
        recommendation=(
            "HORIZON_ADJUSTED_STRATEGY_FAMILY_CLASSIFIED" if complete
            else "WAIT_FOR_EVIDENCE"
        ),
        assumptions=[
            "Eligible rows are completed horizon simulations (shadow_type "
            "PRIMARY_HORIZON_SIMULATION or HORIZON_ALTERNATIVE) with a canonical "
            "evaluated horizon and finite simulated pnl_r_multiple.",
            "Strategy identity is identity.strategy_id frozen at shadow OPEN; it is "
            "never inferred from pattern, symbol, score, regime, horizon, voters, "
            "concrete strategy IDs, or historical family names.",
            "canonical_opportunity_id is the scientific cluster; horizon rows are "
            "repeated measurements and total opportunity weight is one.",
            "Adjusted family effect = equal-weighted average of fitted family means "
            "across SCALP/INTRADAY/EXTENDED; 95% interval = effect +/- 1.96 "
            "opportunity-clustered sandwich standard errors.",
            "POSITIVE_EVIDENCE requires the adjusted 95% interval lower bound > 0 "
            "with every 100/30/20 distinct-opportunity gate satisfied.",
        ],
        warnings=[
            "Shadow outcomes are simulated research evidence, not realised live P&L.",
            "S5 adjusts for horizon composition; it is not a strategy x horizon "
            "interaction analysis (S7) and not a horizon ranking (S6).",
        ],
        provenance={
            "experiment_module": __name__,
            "registry_id": "S5",
            "scientific_owner": "S5",
            "estimand": "HORIZON_ADJUSTED_STRATEGY_FAMILY_EFFECT",
            "hd06": (
                "ADJUDICATED: cluster-aware contracts with 100/30/20 distinct "
                "canonical-opportunity gates; repeated horizons are never "
                "pseudo-replicated"
            ),
        },
    )

