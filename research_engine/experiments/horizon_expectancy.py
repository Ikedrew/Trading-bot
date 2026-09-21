"""S6 canonical strategy-adjusted horizon expectancy research.

S6 is the sole scientific owner of ``s6_horizon_expectancy.json``.  It uses
the same governed CURRENT shadow population and additive opportunity-clustered
fit as S5, but reports the dual marginal estimand: expected simulated R for
each canonical horizon at an equal-weighted mixture of the six V10 strategy
families.  It does not estimate strategy-by-horizon interactions (S7).
"""
from __future__ import annotations

from collections import Counter, defaultdict
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
)
from research_engine.experiments.strategy_identity_expectancy import (
    CANONICAL_HORIZONS,
    MIN_CELL_OPPORTUNITIES,
    MIN_FAMILY_OPPORTUNITIES,
    MIN_OVERALL_OPPORTUNITIES,
    REQUIRED_CELL_COUNT,
    S5_SUFFICIENCY_CONTRACT,
    _cluster_opportunities,
    _estimate_family_effects,
    _select_horizon_rows,
)


REPORT_FILENAME = "s6_horizon_expectancy.json"

S6_SUFFICIENCY_CONTRACT = {
    **S5_SUFFICIENCY_CONTRACT,
    "required_canonical_horizons": list(CANONICAL_HORIZONS),
    "all_horizons_must_be_classified": True,
}


def _horizon_descriptive(
    horizon: str,
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    observations: list[tuple[str, str, float]] = []
    family_opportunities: dict[str, set[str]] = {
        family: set() for family in ACTIVE_FAMILIES
    }
    for cluster in clusters:
        for observed_horizon, value in cluster["observations"]:
            if observed_horizon != horizon:
                continue
            observations.append((cluster["opportunity_id"], cluster["family"], float(value)))
            family_opportunities[cluster["family"]].add(cluster["opportunity_id"])

    values = [value for _, _, value in observations]
    return {
        "n_distinct_canonical_opportunities": len({item[0] for item in observations}),
        "eligible_row_count": len(observations),
        "raw_mean_r": round(statistics.fmean(values), 6) if values else None,
        "raw_median_r": round(statistics.median(values), 6) if values else None,
        "raw_win_rate": round(sum(value > 0 for value in values) / len(values), 6) if values else None,
        "strategy_family_coverage": {
            family: len(family_opportunities[family]) for family in ACTIVE_FAMILIES
        },
    }


def run_s6(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Answer S6 from CURRENT normalized canonical shadow horizon outcomes."""
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

    family_clusters: dict[str, set[str]] = {
        family: set() for family in ACTIVE_FAMILIES
    }
    cell_clusters: dict[tuple[str, str], set[str]] = {
        (family, horizon): set()
        for family in ACTIVE_FAMILIES
        for horizon in CANONICAL_HORIZONS
    }
    horizon_clusters: dict[str, set[str]] = defaultdict(set)
    for cluster in clusters:
        opportunity_id = cluster["opportunity_id"]
        family = cluster["family"]
        family_clusters[family].add(opportunity_id)
        for horizon, _ in cluster["observations"]:
            cell_clusters[(family, horizon)].add(opportunity_id)
            horizon_clusters[horizon].add(opportunity_id)

    cell_n = {
        f"{family}|{horizon}": len(cell_clusters[(family, horizon)])
        for family in ACTIVE_FAMILIES
        for horizon in CANONICAL_HORIZONS
    }
    insufficient_cells = sorted(
        cell for cell, count in cell_n.items() if count < MIN_CELL_OPPORTUNITIES
    )
    pairwise_availability = {
        f"{left}|{right}": len(horizon_clusters[left] & horizon_clusters[right])
        for left_index, left in enumerate(CANONICAL_HORIZONS)
        for right in CANONICAL_HORIZONS[left_index + 1:]
    }
    overall_n = len(clusters)
    overall_sufficient = overall_n >= MIN_OVERALL_OPPORTUNITIES
    families_sufficient = all(
        len(family_clusters[family]) >= MIN_FAMILY_OPPORTUNITIES
        for family in ACTIVE_FAMILIES
    )
    cells_sufficient = not insufficient_cells

    estimation = _estimate_family_effects(clusters)
    estimable = bool(estimation and estimation["estimable"])
    adjusted_horizons = estimation["horizon_adjusted_effects"] if estimation else {}

    horizon_results: dict[str, dict[str, Any]] = {}
    classifications: list[str] = []
    scientific_gates_satisfied = (
        overall_sufficient and families_sufficient and cells_sufficient and estimable
    )
    for horizon in CANONICAL_HORIZONS:
        result = _horizon_descriptive(horizon, clusters)
        adjusted = adjusted_horizons.get(horizon)
        if not scientific_gates_satisfied or adjusted is None:
            classification = "INSUFFICIENT_EVIDENCE"
        else:
            classification = (
                "POSITIVE_EVIDENCE"
                if adjusted["interval_95"]["lower_r"] > 0
                else "NON_POSITIVE_EVIDENCE"
            )
        classifications.append(classification)
        result["adjusted_effect"] = {
            "estimate_r": adjusted["estimate_r"] if adjusted else None,
            "standard_error": adjusted["standard_error"] if adjusted else None,
            "interval_95": adjusted["interval_95"] if adjusted else None,
            "classification": classification,
        }
        horizon_results[horizon] = result

    all_classified = all(
        classification in {"POSITIVE_EVIDENCE", "NON_POSITIVE_EVIDENCE"}
        for classification in classifications
    )
    complete = scientific_gates_satisfied and all_classified
    status = "COMPLETE" if complete else "INSUFFICIENT_DATA"
    confidence = (
        "HIGH" if overall_n >= 200 else "MEDIUM" if overall_n >= 100 else (
            "LOW" if overall_n >= 30 else "INSUFFICIENT_DATA"
        )
    )

    overall = {
        "finding": (
            "Canonical horizon expectancy classified after accounting for StrategyFamily"
            if complete
            else "Insufficient evidence for complete strategy-adjusted horizon classification"
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
        "estimand": "STRATEGY_ADJUSTED_HORIZON_EFFECT",
        "estimator": {
            "model": (
                "weighted least squares: pnl_r_multiple ~ intercept + StrategyFamily "
                "+ evaluated_horizon (two-way additive, reference coding)"
            ),
            "weights": "row weight 1/k_o; each canonical opportunity's total weight is fixed at one",
            "adjusted_horizon_effect": (
                "equal-weighted average of the horizon's fitted means across all six "
                "canonical StrategyFamily values"
            ),
            "uncertainty": S6_SUFFICIENCY_CONTRACT["uncertainty"],
            "estimable": estimable,
        },
        "unit_of_analysis": (
            "one canonical_opportunity_id cluster; horizon simulations are repeated "
            "measurements and never independent opportunities"
        ),
        "distinct_canonical_opportunities": overall_n,
        "analysed_row_count": len(analysed),
        "horizons": horizon_results,
        "pairwise_availability": {
            "unit": "distinct canonical_opportunity_id with both horizon simulations",
            "counts": pairwise_availability,
        },
        "cells": {
            "required_cells": REQUIRED_CELL_COUNT,
            "observed_cells": sum(count > 0 for count in cell_n.values()),
            "cell_rule": "all 6 canonical StrategyFamily x 3 canonical horizon cells are required",
            "cell_n_distinct_canonical_opportunities": cell_n,
            "insufficient_cells": insufficient_cells,
        },
        "sufficiency": {
            **S6_SUFFICIENCY_CONTRACT,
            "overall_sufficient": overall_sufficient,
            "families_sufficient": families_sufficient,
            "cells_sufficient": cells_sufficient,
            "all_horizons_present": all(horizon_clusters[h] for h in CANONICAL_HORIZONS),
        },
        "exclusions": {
            **Counter(exclusions),
            "non_current_or_incompatible": (
                analysis_selection.component["input_records"]
                - epoch_selection.component["records_used"]
            ),
            "total_not_analysed": analysis_selection.component["records_excluded"],
        },
        "classification_rule": (
            "Gates unmet (overall <100, family <30, any required cell <20, missing "
            "horizon, or non-estimable model) => INSUFFICIENT_EVIDENCE; otherwise "
            "POSITIVE_EVIDENCE only when the adjusted-effect 95% cluster-aware "
            "interval lower bound is >0, else NON_POSITIVE_EVIDENCE."
        ),
    }

    return build_report(
        question_id="S6",
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
            "STRATEGY_ADJUSTED_HORIZONS_CLASSIFIED" if complete else "WAIT_FOR_EVIDENCE"
        ),
        assumptions=[
            "Eligible rows are completed PRIMARY_HORIZON_SIMULATION or HORIZON_ALTERNATIVE simulations.",
            "identity.strategy_id and identity.evaluated_horizon are frozen pre-outcome authorities.",
            "canonical_opportunity_id is the cluster; repeated horizons have total opportunity weight one.",
            "Adjusted horizon effect is the equal-family marginal fitted mean; its interval is estimate +/- 1.96 opportunity-clustered sandwich SEs.",
            "Every 100/30/20 distinct-opportunity gate must pass before classification.",
        ],
        warnings=[
            "Shadow outcomes are simulated research evidence, not realised live P&L.",
            "S6 estimates marginal horizon expectancy, not strategy-by-horizon interaction (S7).",
        ],
        provenance={
            "experiment_module": __name__,
            "registry_id": "S6",
            "scientific_owner": "S6",
            "estimand": "STRATEGY_ADJUSTED_HORIZON_EFFECT",
            "hd06": (
                "ADJUDICATED: cluster-aware contracts with 100/30/20 distinct "
                "canonical-opportunity gates; repeated horizons are never pseudo-replicated"
            ),
        },
    )


__all__ = ["REPORT_FILENAME", "S6_SUFFICIENCY_CONTRACT", "run_s6"]
