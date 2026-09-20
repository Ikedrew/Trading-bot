"""E3 canonical V10 strategy-family expectancy research.

E3 is the sole scientific owner.  S1 is a governed superseded alias and has
no runner.  The analytical grain is one completed PRIMARY_HORIZON_SIMULATION
per canonical opportunity from normalized ``shadow_trades_v1`` evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
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

REPORT_FILENAME = "e3_strategy_family_expectancy.json"
PRIMARY_OUTCOME = "PRIMARY_HORIZON_SIMULATION"
MIN_FAMILY_OPPORTUNITIES = 30
MIN_TOTAL_OBSERVATIONS = 50
MIN_STRATEGY_COVERAGE = 0.50
MIN_OUTCOME_COVERAGE = 0.95
Z_95 = 1.96

ACTIVE_FAMILIES = tuple(
    family.value for family in StrategyFamily if family is not StrategyFamily.NONE
)
ACTIVE_FAMILY_SET = frozenset(ACTIVE_FAMILIES)
LEGACY_TAXONOMY = ("REVERSAL", "CONTINUATION", "FALSE_BREAK")


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("identity")
    return value if isinstance(value, dict) else {}


def _outcome(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("simulated_outcome")
    return value if isinstance(value, dict) else {}


def _finite_r(record: dict[str, Any]) -> float | None:
    value = _outcome(record).get("pnl_r_multiple")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _family_result(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n < MIN_FAMILY_OPPORTUNITIES:
        return {
            "n_distinct_canonical_opportunities": n,
            "mean_r": round(statistics.mean(values), 6) if values else None,
            "win_rate": round(sum(value > 0 for value in values) / n, 6) if n else None,
            "interval_95": None,
            "classification": "INSUFFICIENT_EVIDENCE",
        }

    mean_r = statistics.mean(values)
    standard_error = statistics.stdev(values) / math.sqrt(n) if n > 1 else 0.0
    lower = mean_r - Z_95 * standard_error
    upper = mean_r + Z_95 * standard_error
    return {
        "n_distinct_canonical_opportunities": n,
        "mean_r": round(mean_r, 6),
        "win_rate": round(sum(value > 0 for value in values) / n, 6),
        "interval_95": {
            "method": "normal_approximation_sample_standard_error",
            "lower_r": round(lower, 6),
            "upper_r": round(upper, 6),
            "z": Z_95,
        },
        # Positive expectancy is established only when the entire interval is
        # above zero.  Every other sufficient observation is non-positive
        # evidence; a positive point estimate alone is never enough.
        "classification": (
            "POSITIVE_EVIDENCE" if lower > 0 else "NON_POSITIVE_EVIDENCE"
        ),
    }


def _select_observations(
    current_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float]]:
    exclusions: Counter[str] = Counter()
    primary = []
    for record in current_records:
        ident = _identity(record)
        if str(ident.get("shadow_type", "") or "") != PRIMARY_OUTCOME:
            exclusions["non_primary_horizon_simulation"] += 1
            continue
        primary.append(record)

    primary_count = len(primary)
    strategy_known = 0
    outcome_known = 0
    candidates: list[dict[str, Any]] = []
    for record in primary:
        ident = _identity(record)
        opportunity_id = str(ident.get("canonical_opportunity_id", "") or "")
        family = str(ident.get("strategy_id", "") or "")
        if family in ACTIVE_FAMILY_SET:
            strategy_known += 1
        elif family == StrategyFamily.NONE.value:
            exclusions["strategy_family_none"] += 1
            continue
        else:
            exclusions["unknown_or_missing_strategy_family"] += 1
            continue
        if not opportunity_id:
            exclusions["missing_canonical_opportunity_id"] += 1
            continue
        if _finite_r(record) is None:
            exclusions["missing_or_non_finite_outcome"] += 1
            continue
        outcome_known += 1
        candidates.append(record)

    by_opportunity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in candidates:
        by_opportunity[str(_identity(record)["canonical_opportunity_id"])].append(record)

    observations: list[dict[str, Any]] = []
    for opportunity_id in sorted(by_opportunity):
        records = by_opportunity[opportunity_id]
        if len(records) != 1:
            exclusions["duplicate_or_conflicting_primary_outcome"] += len(records)
            continue
        observations.append(records[0])

    coverage = {
        "strategy_coverage": strategy_known / primary_count if primary_count else 0.0,
        "outcome_coverage": outcome_known / strategy_known if strategy_known else 0.0,
    }
    return observations, dict(sorted(exclusions.items())), coverage


def run_e3(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Answer E3 from the canonical completed shadow lifecycle population."""
    if shadow_trades is None:
        from research_engine.data_access.shadow_runtime_ingestion import (
            ingest_completed_shadow_trades,
        )
        raw = ingest_completed_shadow_trades()
    else:
        raw = list(shadow_trades)

    epoch_selection = select_current_evidence("shadow_trades", raw)
    current = epoch_selection.records_for_analysis()
    observations, exclusions, coverage = _select_observations(current)
    raw_unknown_families = sum(
        1 for record in raw
        if (family := str(_identity(record).get("strategy_id", "") or ""))
        and family not in ACTIVE_FAMILY_SET
        and family != StrategyFamily.NONE.value
    )
    if raw_unknown_families:
        exclusions["unknown_or_missing_strategy_family"] = (
            exclusions.get("unknown_or_missing_strategy_family", 0)
            + raw_unknown_families
        )
    analysis_selection = attest_current_subset(
        "shadow_trades", raw, observations,
    )
    analysed = analysis_selection.records_for_analysis()
    provenance = build_evidence_provenance(analysis_selection)

    grouped: dict[str, list[float]] = {family: [] for family in ACTIVE_FAMILIES}
    for record in analysed:
        family = str(_identity(record)["strategy_id"])
        grouped[family].append(_finite_r(record))  # type: ignore[arg-type]
    family_results = {
        family: _family_result(grouped[family]) for family in ACTIVE_FAMILIES
    }

    n = len(analysed)
    all_families_sufficient = all(
        result["n_distinct_canonical_opportunities"] >= MIN_FAMILY_OPPORTUNITIES
        for result in family_results.values()
    )
    complete = (
        n >= MIN_TOTAL_OBSERVATIONS
        and coverage["strategy_coverage"] >= MIN_STRATEGY_COVERAGE
        and coverage["outcome_coverage"] >= MIN_OUTCOME_COVERAGE
        and all_families_sufficient
    )
    status = "COMPLETE" if complete else "INSUFFICIENT_DATA"
    confidence = "HIGH" if n >= 200 else "MEDIUM" if n >= 50 else (
        "LOW" if n >= 20 else "INSUFFICIENT_DATA"
    )

    overall = {
        "finding": (
            "V10 strategy-family expectancy classified with 95% uncertainty"
            if complete else "Insufficient evidence for complete six-family expectancy classification"
        ),
        "taxonomy": {
            "authority": "core.v10.strategy_family.StrategyFamily",
            "active_families": list(ACTIVE_FAMILIES),
            "excluded_value": StrategyFamily.NONE.value,
            "legacy_compatibility_only": list(LEGACY_TAXONOMY),
            "legacy_mapping_inferred": False,
        },
        "unit_of_analysis": "one completed primary outcome per canonical_opportunity_id",
        "observation_count": n,
        "families": family_results,
        "coverage": {key: round(value, 6) for key, value in coverage.items()},
        "requirements": {
            "minimum_per_family": MIN_FAMILY_OPPORTUNITIES,
            "minimum_total_observations": MIN_TOTAL_OBSERVATIONS,
            "minimum_strategy_coverage": MIN_STRATEGY_COVERAGE,
            "minimum_outcome_coverage": MIN_OUTCOME_COVERAGE,
            "all_families_must_be_sufficient": True,
        },
        "exclusions": {
            **exclusions,
            "non_current_or_incompatible": analysis_selection.component["input_records"]
            - epoch_selection.component["records_used"],
            "total_not_analysed": analysis_selection.component["records_excluded"],
        },
        "classification_rule": (
            "N<30 => INSUFFICIENT_EVIDENCE; otherwise POSITIVE_EVIDENCE only "
            "when the 95% interval lower bound is >0, else NON_POSITIVE_EVIDENCE"
        ),
    }
    fingerprint = build_fingerprint_from_provenance(
        provenance, validation_score=confidence,
    )
    return build_report(
        question_id="E3",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={
            "source": "shadow_trades_v1 normalized from shadow_runtime_v1",
            "sample_size": n,
            "records_loaded": len(raw),
        },
        fingerprint=fingerprint,
        recommendation=("STRATEGY_EXPECTANCY_CLASSIFIED" if complete else "WAIT_FOR_EVIDENCE"),
        assumptions=[
            "Only PRIMARY_HORIZON_SIMULATION outcomes are eligible.",
            "Strategy family is identity.strategy_id frozen at shadow OPEN.",
            "The 95% interval is mean +/- 1.96 sample-standard-errors.",
        ],
        warnings=[
            "Shadow outcomes are simulated research evidence, not realised live P&L."
        ],
        provenance={
            "experiment_module": __name__,
            "registry_id": "E3",
            "scientific_owner": "E3",
            "superseded_aliases": ["S1"],
            "legacy_q24_role": "compatibility/history only; activation frequency is not expectancy",
        },
    )
