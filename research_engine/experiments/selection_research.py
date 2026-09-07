"""
Horizon + Strategy Selection Research — S2 / S3 / S4 / HORIZON-1 / STRAT-1 (Wave 3)

Implements five canonical research questions interrogating the selection
decisions the bot already records:

    S2        - Does horizon affect expectancy?
    S3        - Which strategy x horizon combinations have edge?
    S4        - Are strategies specialised for particular market phases?
    HORIZON-1 - Was the selected horizon better than the alternatives
                considered for the SAME opportunity?
    STRAT-1   - Does strategy-ranking confidence predict subsequent
                outcome quality?

SCIENTIFIC BOUNDARIES:

    1. Shadow outcomes are SIMULATED. S2/S3/S4/HORIZON-1 describe simulated
       horizon/strategy performance on shadow-observed opportunities —
       observational research evidence, not proof that changing horizon
       selection will improve live expectancy.

    2. HORIZON-1 is a genuine WITHIN-OPPORTUNITY comparison: the shadow
       runtime simulates ALL three horizons for every shadow-observed
       opportunity (selected -> PRIMARY_HORIZON_SIMULATION, others ->
       HORIZON_ALTERNATIVE), all sharing the same canonical_opportunity_id.
       Only same-opportunity records are compared — never across opportunities.

    3. STRAT-1 confidence/rank are PRE-DECISION selection evidence (persisted
       by strategy_candidates_v1 before any outcome exists). The shadow
       runtime simulates only the SELECTED strategy — rejected strategies
       have NO simulated outcome, so selection optimality cannot be proven.
       Confidence is a relative score -> ranking/monotonicity, NOT calibration.

    4. All outcome R values are post-decision evidence used only for
       historical research. No outcome field enters any pre-decision
       feature in this module.

Populations come exclusively from canonical ingestion:
    - ingest_completed_shadow_trades()      (shadow_runtime_v1, S3)
    - load_horizon_candidates()             (horizon_candidates_v1, S3)
    - load_strategy_candidates()            (strategy_candidates_v1, S3)
No local fallback, no parallel path, no retired loaders.
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

_MIN_SAMPLE = 30          # overall status threshold (engine convention)
_MIN_CELL = 10            # per-cell minimum (horizon / combo / phase cells)
_MIN_BUCKET = 15          # per confidence bucket (STRAT-1)
_TIE_EPS = 0.05           # R tie tolerance for HORIZON-1
_PRIMARY = "PRIMARY_HORIZON_SIMULATION"


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION
# ═══════════════════════════════════════════════════════════════════════════════


def _load_simulation_population() -> list[dict[str, Any]]:
    """
    Load ALL completed shadow lifecycles (primary + horizon alternatives)
    from the canonical shadow_runtime_v1 ingestion as flat research records.
    Callers filter per-question so exclusion accounting stays honest.
    """
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    raw = ingest_completed_shadow_trades()
    population: list[dict[str, Any]] = []
    for rec in raw:
        sim = rec.get("simulated_outcome") or {}
        ident = rec.get("identity") or {}
        snap = rec.get("decision_snapshot") or {}

        pnl = sim.get("pnl_r_multiple")
        population.append({
            "shadow_trade_id": ident.get("shadow_trade_id", ""),
            "canonical_opportunity_id": str(
                ident.get("canonical_opportunity_id", "") or ""),
            "symbol": ident.get("symbol", ""),
            "shadow_type": str(ident.get("shadow_type", "") or ""),
            "evaluated_horizon": str(ident.get("evaluated_horizon", "") or ""),
            "strategy": str(
                snap.get("strategy_id", "") or snap.get("strategy", "") or ""),
            "pattern": snap.get("pattern", ""),
            "market_phase": snap.get("market_phase", ""),
            "h4_regime": snap.get("h4_regime", ""),
            "pnl_r": float(pnl) if pnl is not None else None,
            "mfe_r": sim.get("mfe_r"),
            "mae_r": sim.get("mae_r"),
            "exit_reason": str(sim.get("exit_reason", "") or ""),
        })

    logger.info(
        "[SELECTION_POPULATION] raw_completed=%d flat=%d",
        len(raw), len(population),
    )
    return population


def _outcome_records(population: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records with a finite realised R (outcome analysis)."""
    return [
        rec for rec in population
        if rec.get("pnl_r") is not None and math.isfinite(rec["pnl_r"])
    ]


def _group_stats(rs: list[float]) -> dict[str, Any]:
    """Descriptive statistics for one group of realised R values."""
    if not rs:
        return {"n": 0, "mean_r": None, "median_r": None,
                "win_rate": None, "total_r": None, "std_r": None}
    wins = [r for r in rs if r > 0]
    stats: dict[str, Any] = {
        "n": len(rs),
        "mean_r": round(statistics.mean(rs), 4),
        "median_r": round(statistics.median(rs), 4),
        "win_rate": round(len(wins) / len(rs), 4),
        "total_r": round(sum(rs), 2),
    }
    stats["std_r"] = round(statistics.pstdev(rs), 4) if len(rs) > 1 else None
    return stats


def _report(
    question_id: str,
    status: str,
    overall: dict[str, Any],
    confidence: str,
    dataset: dict[str, Any],
    recommendation: str,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Canonical Gap-4 report for selection-research questions."""
    from research_engine.experiments.experiment_base import (
        build_report, build_fingerprint,
    )

    sample = dataset.get("sample_size", 0)
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=build_fingerprint(sample, 0, "shadow_runtime_v1"),
        recommendation=recommendation,
        assumptions=assumptions or [],
        warnings=warnings or [],
        provenance={
            "experiment_module": "research_engine.experiments.selection_research",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output -> "
                        "Knowledge -> Command Centre",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S2 — DOES HORIZON AFFECT EXPECTANCY?
# ═══════════════════════════════════════════════════════════════════════════════


def run_s2(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    S2: Does trade horizon (SCALP/INTRADAY/EXTENDED) affect expectancy?

    Uses the FULL shadow simulation population (primary + horizon-alternative
    simulations): every shadow-observed opportunity contributes one simulated
    outcome per horizon, so horizon groups share the same underlying
    opportunity composition. Records are NOT independent (up to 3 per
    opportunity) — reported, not hidden.

    OBSERVATIONAL: horizon is not randomly assigned in the live system.
    """
    population = (shadow_trades if shadow_trades is not None
                  else _load_simulation_population())
    records = _outcome_records(population)

    by_horizon: dict[str, list[float]] = defaultdict(list)
    primary_by_horizon: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        hz = rec["evaluated_horizon"]
        if not hz:
            continue
        by_horizon[hz].append(rec["pnl_r"])
        if rec["shadow_type"] == _PRIMARY:
            primary_by_horizon[hz].append(rec["pnl_r"])

    n_total = len(records)
    if n_total < _MIN_SAMPLE or len(by_horizon) < 2:
        conf = "INSUFFICIENT_DATA" if n_total < 10 else "LOW"
        return _report(
            question_id="S2",
            status="INSUFFICIENT_DATA",
            overall={"sample_size": n_total,
                     "horizons_with_outcomes": sorted(by_horizon.keys())},
            confidence=conf,
            dataset={"sample_size": n_total,
                     "source": "shadow_runtime_v1 (simulation population)"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=["Requires >=30 outcome records across >=2 horizons."],
        )

    horizons = {hz: _group_stats(rs) for hz, rs in sorted(by_horizon.items())}
    primary_view = {
        hz: _group_stats(rs)
        for hz, rs in sorted(primary_by_horizon.items()) if rs
    }

    sufficient = {hz: s for hz, s in horizons.items() if s["n"] >= _MIN_CELL}
    spread = None
    if len(sufficient) >= 2:
        means = {hz: s["mean_r"] for hz, s in sufficient.items()}
        hz_max = max(means, key=lambda k: means[k])
        hz_min = min(means, key=lambda k: means[k])
        spread = {
            "best_horizon": hz_max, "best_mean_r": means[hz_max],
            "worst_horizon": hz_min, "worst_mean_r": means[hz_min],
            "spread_r": round(means[hz_max] - means[hz_min], 4),
        }

    if spread and spread["spread_r"] >= 0.25:
        recommendation = "HORIZON_OUTCOMES_DIFFER"
    elif spread:
        recommendation = "NO_MEANINGFUL_HORIZON_DIFFERENCE"
    else:
        recommendation = "OBSERVATIONAL_ONLY"

    return _report(
        question_id="S2",
        status="COMPLETE",
        overall={
            "sample_size": n_total,
            "horizons": horizons,
            "primary_horizon_view": primary_view,
            "spread_assessment": spread,
        },
        confidence=_confidence(n_total),
        dataset={
            "sample_size": n_total,
            "source": "shadow_runtime_v1 (simulation population: "
                      "primary + horizon alternatives)",
        },
        recommendation=recommendation,
        assumptions=[
            "Population = completed shadow lifecycles with finite realised R.",
            "Horizons come from the shadow runtime's evaluated_horizon fact "
            "(known values SCALP/INTRADAY/EXTENDED validated from evidence).",
        ],
        warnings=[
            "Shadow outcomes are SIMULATED — observational evidence only.",
            "Records are not independent: each opportunity contributes up to "
            "3 horizon simulations (primary + alternatives).",
            "Horizon is not randomly assigned in the live system; "
            "associations are not causal.",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S3 — STRATEGY × HORIZON COMBINATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def _confidence(n: int) -> str:
    """Canonical confidence band (mirrors exit_management._confidence)."""
    if n >= 200:
        return "HIGH"
    if n >= _MIN_SAMPLE:
        return "MEDIUM"
    if n > 0:
        return "LOW"
    return "INSUFFICIENT_DATA"


def run_s3(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    S3: Which strategy × horizon combinations have edge?

    Cells are built from the full shadow simulation population. Tiny cells
    (< _MIN_CELL) are excluded from conclusions but counted. The number of
    combinations TESTED is reported explicitly (multiple-comparison risk).
    A positive historical expectancy is research evidence only — never a
    production-ready claim.
    """
    population = (shadow_trades if shadow_trades is not None
                  else _load_simulation_population())
    records = _outcome_records(population)

    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    excluded_no_strategy = 0
    excluded_no_horizon = 0
    for rec in records:
        if not rec["strategy"]:
            excluded_no_strategy += 1
            continue
        if not rec["evaluated_horizon"]:
            excluded_no_horizon += 1
            continue
        cells[(rec["strategy"], rec["evaluated_horizon"])].append(rec["pnl_r"])

    n_total = len(records)
    if n_total < _MIN_SAMPLE or not cells:
        conf = "INSUFFICIENT_DATA" if n_total < 10 else "LOW"
        return _report(
            question_id="S3",
            status="INSUFFICIENT_DATA",
            overall={"sample_size": n_total, "cells_formed": len(cells)},
            confidence=conf,
            dataset={"sample_size": n_total,
                     "source": "shadow_runtime_v1 (simulation population)"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "Requires >=30 outcome records with strategy+horizon identity."],
        )

    combinations_tested = len(cells)
    populated = {
        f"{strategy}|{hz}": _group_stats(rs)
        for (strategy, hz), rs in sorted(cells.items())
    }
    sufficient_cells = {
        k: s for k, s in populated.items() if s["n"] >= _MIN_CELL
    }
    excluded_small = combinations_tested - len(sufficient_cells)
    positive = {k: s for k, s in sufficient_cells.items()
                if (s["mean_r"] or 0) > 0}

    warnings = [
        f"Multiple-comparison risk: {combinations_tested} combinations "
        f"tested; {excluded_small} excluded for N < {_MIN_CELL}.",
        "Positive historical expectancy is research evidence only — it does "
        "NOT make a combination production-ready.",
        "Shadow outcomes are SIMULATED — observational evidence, not causal.",
    ]
    if excluded_no_strategy or excluded_no_horizon:
        warnings.append(
            f"Excluded records: {excluded_no_strategy} without strategy, "
            f"{excluded_no_horizon} without horizon."
        )

    return _report(
        question_id="S3",
        status="COMPLETE",
        overall={
            "sample_size": n_total,
            "combinations_tested": combinations_tested,
            "cells_sufficient_n": sufficient_cells,
            "cells_positive_mean_r": sorted(positive.keys()),
            "n_positive_cells": len(positive),
        },
        confidence=_confidence(n_total),
        dataset={
            "sample_size": n_total,
            "source": "shadow_runtime_v1 (simulation population: "
                      "primary + horizon alternatives)",
        },
        recommendation=(
            "COMBINATION_EVIDENCE_REPORTED" if positive
            else "NO_SUFFICIENT_POSITIVE_COMBINATION"
        ),
        assumptions=[
            "Cells with N < 10 are excluded from conclusions but counted.",
            "All cells share the same shadow opportunity composition across "
            "horizons (each opportunity simulated at every horizon).",
        ],
        warnings=warnings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S4 — STRATEGY SPECIALISATION ACROSS MARKET PHASES
# ═══════════════════════════════════════════════════════════════════════════════


def run_s4(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    S4: Are strategies specialised for particular market phases?

    OVERLAP BOUNDARY (audited):
        M10 already reports descriptive phase x strategy-family cells
        (EV / win rate / MFE / MAE per cell). S4 adds the distinct
        WITHIN-STRATEGY specialisation contrast: for each strategy, how
        materially do outcomes differ ACROSS phases (max-minus-min mean R
        between sufficient phase cells), i.e. does one strategy belong to
        one phase rather than performing uniformly.

    Uses the PRIMARY_HORIZON_SIMULATION population (the incumbent surface,
    consistent with Q16/edge conventions) so strategy cells are not inflated
    by horizon-alternative duplicates of the same opportunity.
    """
    population = (shadow_trades if shadow_trades is not None
                  else _load_simulation_population())
    primary = [rec for rec in population if rec["shadow_type"] == _PRIMARY]
    records = _outcome_records(primary)

    by_strategy_phase: dict[tuple[str, str], list[float]] = defaultdict(list)
    excluded_no_strategy = 0
    excluded_no_phase = 0
    for rec in records:
        if not rec["strategy"]:
            excluded_no_strategy += 1
            continue
        if not rec["market_phase"]:
            excluded_no_phase += 1
            continue
        by_strategy_phase[
            (rec["strategy"], rec["market_phase"])
        ].append(rec["pnl_r"])

    n_total = len(records)
    if n_total < _MIN_SAMPLE or not by_strategy_phase:
        conf = "INSUFFICIENT_DATA" if n_total < 10 else "LOW"
        return _report(
            question_id="S4",
            status="INSUFFICIENT_DATA",
            overall={"sample_size": n_total,
                     "strategy_phase_cells": len(by_strategy_phase)},
            confidence=conf,
            dataset={"sample_size": n_total,
                     "source": "shadow_runtime_v1 (primary-horizon population)"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "Requires >=30 primary-horizon outcome records with "
                "strategy+phase identity."],
        )

    by_strategy: dict[str, dict[str, list[float]]] = defaultdict(dict)
    cell_stats: dict[str, dict[str, Any]] = {}
    for (strategy, phase), rs in sorted(by_strategy_phase.items()):
        cell_stats[f"{strategy}|{phase}"] = _group_stats(rs)
        by_strategy[strategy][phase] = rs

    specialisation: dict[str, Any] = {}
    for strategy, phases in sorted(by_strategy.items()):
        sufficient = {p: _group_stats(rs) for p, rs in phases.items()
                      if len(rs) >= _MIN_CELL}
        if len(sufficient) < 2:
            specialisation[strategy] = {
                "sufficient_phase_cells": len(sufficient),
                "assessment": "INSUFFICIENT_PHASE_COVERAGE",
                "phase_spread_r": None,
            }
            continue
        means = {p: s["mean_r"] for p, s in sufficient.items()}
        p_best = max(means, key=lambda k: means[k])
        p_worst = min(means, key=lambda k: means[k])
        spread = round(means[p_best] - means[p_worst], 4)
        specialisation[strategy] = {
            "sufficient_phase_cells": len(sufficient),
            "best_phase": p_best, "best_mean_r": means[p_best],
            "worst_phase": p_worst, "worst_mean_r": means[p_worst],
            "phase_spread_r": spread,
            "assessment": (
                "PHASE_SPECIALISED" if spread >= 0.5
                else "MILD_PHASE_VARIATION" if spread >= 0.25
                else "NO_MATERIAL_PHASE_SPECIALISATION"
            ),
        }

    warnings = [
        "OVERLAP BOUNDARY: M10 reports descriptive phase x strategy-family "
        "cells; S4 adds the within-strategy cross-phase specialisation "
        "contrast. Both remain active, distinct questions.",
        "Shadow outcomes are SIMULATED — observational evidence, not causal.",
        "Primary-horizon population only (horizon alternatives excluded to "
        "avoid opportunity duplication).",
    ]
    if excluded_no_strategy or excluded_no_phase:
        warnings.append(
            f"Excluded records: {excluded_no_strategy} without strategy, "
            f"{excluded_no_phase} without market_phase."
        )

    return _report(
        question_id="S4",
        status="COMPLETE",
        overall={
            "sample_size": n_total,
            "cell_stats": cell_stats,
            "specialisation": specialisation,
        },
        confidence=_confidence(n_total),
        dataset={
            "sample_size": n_total,
            "source": "shadow_runtime_v1 (primary-horizon population)",
        },
        recommendation=(
            "PHASE_SPECIALISATION_EVIDENCE_REPORTED"
            if any(s.get("assessment") == "PHASE_SPECIALISED"
                   for s in specialisation.values())
            else "NO_MATERIAL_PHASE_SPECIALISATION_OBSERVED"
        ),
        assumptions=[
            "Specialisation contrast requires >=2 phase cells with N >= 10 "
            "per strategy; otherwise INSUFFICIENT_PHASE_COVERAGE is reported.",
        ],
        warnings=warnings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON-1 — WAS THE SELECTED HORIZON BETTER THAN THE ALTERNATIVES?
# ═══════════════════════════════════════════════════════════════════════════════


def build_horizon1_population(
    population: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Group shadow simulation records by canonical opportunity (within-opportunity
    comparison — the core HORIZON-1 contract).

    Returns {canonical_opportunity_id: {...}} with:
        selected_horizon  — evaluated_horizon of the PRIMARY_HORIZON_SIMULATION
        selected_r        — its realised R (None if missing)
        alternatives      — {horizon: r} for other horizons, SAME opportunity
        ambiguous         — True if >1 primary-horizon record (replay duplicates)
    """
    by_opp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_no_lineage = 0
    for rec in population:
        opp = rec["canonical_opportunity_id"]
        if not opp:
            excluded_no_lineage += 1
            continue
        by_opp[opp].append(rec)

    opportunities: dict[str, dict[str, Any]] = {}
    for opp, recs in by_opp.items():
        primaries = [r for r in recs if r["shadow_type"] == _PRIMARY]
        ambiguous = len(primaries) > 1
        primary = primaries[0] if primaries else None

        alternatives: dict[str, float] = {}
        for r in recs:
            if r is primary:
                continue
            hz = r["evaluated_horizon"]
            if not hz or r["pnl_r"] is None or not math.isfinite(r["pnl_r"]):
                continue
            # Deterministic: first record per horizon within the opportunity.
            alternatives.setdefault(hz, r["pnl_r"])

        opportunities[opp] = {
            "selected_horizon": primary["evaluated_horizon"] if primary else "",
            "selected_r": (
                primary["pnl_r"]
                if primary and primary["pnl_r"] is not None
                and math.isfinite(primary["pnl_r"])
                else None
            ),
            "alternatives": alternatives,
            "ambiguous": ambiguous,
        }

    if excluded_no_lineage:
        logger.info(
            "[HORIZON1] excluded_no_canonical_opportunity_id=%d",
            excluded_no_lineage,
        )
    return opportunities


def run_horizon1(
    shadow_trades: list[dict[str, Any]] | None = None,
    horizon_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    HORIZON-1: Was the selected horizon better than the alternatives
    considered for the SAME canonical opportunity?

    WITHIN-OPPORTUNITY ONLY. The shadow runtime simulates all three horizons
    per shadow-observed opportunity, so both sides share identical opportunity
    context. No cross-opportunity comparison is ever made. When
    horizon_candidates_v1 records are available, the shadow's primary-horizon
    selection is cross-checked against the selection engine's SELECTED fact
    for the same opportunity (agreement statistic).
    """
    population = (shadow_trades if shadow_trades is not None
                  else _load_simulation_population())
    opportunities = build_horizon1_population(population)

    comparable: list[dict[str, Any]] = []
    n_no_alternatives = 0
    n_no_selected_outcome = 0
    n_ambiguous = 0
    for opp, info in opportunities.items():
        if info["ambiguous"]:
            n_ambiguous += 1
            continue
        if info["selected_r"] is None:
            n_no_selected_outcome += 1
            continue
        if not info["alternatives"]:
            n_no_alternatives += 1
            continue
        best_alt_hz = max(info["alternatives"],
                          key=lambda k: info["alternatives"][k])
        best_alt_r = info["alternatives"][best_alt_hz]
        sel_r = info["selected_r"]
        if sel_r > best_alt_r + _TIE_EPS:
            classification = "SELECTED_BEST"
        elif sel_r >= best_alt_r - _TIE_EPS:
            classification = "SELECTED_TIED_BEST"
        else:
            classification = "SELECTED_UNDERPERFORMED_ALTERNATIVE"
        comparable.append({
            "canonical_opportunity_id": opp,
            "selected_horizon": info["selected_horizon"],
            "selected_r": sel_r,
            "best_alternative_horizon": best_alt_hz,
            "best_alternative_r": best_alt_r,
            "delta_r": round(sel_r - best_alt_r, 4),
            "classification": classification,
        })

    n_comparable = len(comparable)
    if n_comparable < _MIN_SAMPLE:
        conf = "INSUFFICIENT_DATA" if n_comparable < 10 else "LOW"
        return _report(
            question_id="HORIZON-1",
            status="INSUFFICIENT_DATA",
            overall={
                "opportunities_total": len(opportunities),
                "comparable_opportunities": n_comparable,
                "insufficient_alternative_outcomes": n_no_alternatives,
                "missing_selected_outcome": n_no_selected_outcome,
                "ambiguous_excluded": n_ambiguous,
            },
            confidence=conf,
            dataset={"sample_size": n_comparable,
                     "source": "shadow_runtime_v1 (within-opportunity)"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "Requires >=30 opportunities with a selected-horizon outcome "
                "AND >=1 alternative-horizon outcome for the SAME opportunity.",
            ],
        )

    n_best = sum(1 for c in comparable
                 if c["classification"] == "SELECTED_BEST")
    n_tied = sum(1 for c in comparable
                 if c["classification"] == "SELECTED_TIED_BEST")
    n_worse = sum(1 for c in comparable
                  if c["classification"]
                  == "SELECTED_UNDERPERFORMED_ALTERNATIVE")
    mean_sel = statistics.mean(c["selected_r"] for c in comparable)
    mean_alt = statistics.mean(c["best_alternative_r"] for c in comparable)
    mean_delta = statistics.mean(c["delta_r"] for c in comparable)

    by_selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in comparable:
        by_selected[c["selected_horizon"]].append(c)
    per_horizon = {
        hz: {
            "n": len(cs),
            "selected_best_rate": round(
                sum(1 for c in cs
                    if c["classification"] == "SELECTED_BEST") / len(cs), 4),
            "underperformed_rate": round(
                sum(1 for c in cs
                    if c["classification"]
                    == "SELECTED_UNDERPERFORMED_ALTERNATIVE") / len(cs), 4),
            "mean_delta_r": round(
                statistics.mean(c["delta_r"] for c in cs), 4),
        }
        for hz, cs in sorted(by_selected.items())
    }

    selection_agreement = _selection_engine_agreement(
        comparable, horizon_candidates)

    if n_worse / n_comparable > 0.5:
        recommendation = "SELECTION_WEAKNESS_SIGNAL"
    elif n_best / n_comparable > 0.5:
        recommendation = "SELECTION_SUPPORTIVE"
    else:
        recommendation = "MIXED_SELECTION_EVIDENCE"

    return _report(
        question_id="HORIZON-1",
        status="COMPLETE",
        overall={
            "opportunities_total": len(opportunities),
            "comparable_opportunities": n_comparable,
            "selected_best": n_best,
            "selected_tied_best": n_tied,
            "selected_underperformed_alternative": n_worse,
            "selected_best_rate": round(n_best / n_comparable, 4),
            "selected_tied_best_rate": round(n_tied / n_comparable, 4),
            "selected_underperformed_rate": round(n_worse / n_comparable, 4),
            "mean_selected_r": round(mean_sel, 4),
            "mean_best_alternative_r": round(mean_alt, 4),
            "mean_selected_minus_best_alternative_r": round(mean_delta, 4),
            "per_selected_horizon": per_horizon,
            "selection_engine_agreement": selection_agreement,
            "insufficient_alternative_outcomes": n_no_alternatives,
            "missing_selected_outcome": n_no_selected_outcome,
            "ambiguous_excluded": n_ambiguous,
        },
        confidence=_confidence(n_comparable),
        dataset={
            "sample_size": n_comparable,
            "source": "shadow_runtime_v1 (within-opportunity) + "
                      "horizon_candidates_v1 (cross-check)",
        },
        recommendation=recommendation,
        assumptions=[
            "Comparison is WITHIN-opportunity only: every comparable "
            "opportunity has simulated outcomes for its selected horizon and "
            ">=1 alternative horizon from the same shadow lifecycle family.",
            "Selection facts (PRIMARY_HORIZON_SIMULATION label, "
            "horizon_candidates SELECTED status) are pre-outcome; realised R "
            "is post-outcome research evidence only.",
            "Tie tolerance: delta within +/-0.05R counts as tied-best.",
        ],
        warnings=[
            "Shadow outcomes are SIMULATED — this evaluates whether the "
            "selection was supported by simulated counterfactuals on "
            "shadow-observed opportunities, not live profitability.",
            "Alternative-horizon simulations are the sanctioned "
            "HORIZON_ALTERNATIVE shadow population; the Q16 primary-horizon "
            "contract is untouched.",
        ],
    )


def _selection_engine_agreement(
    comparable: list[dict[str, Any]],
    horizon_candidates: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Cross-check shadow primary horizon vs horizon_candidates SELECTED."""
    if horizon_candidates is None:
        try:
            from research_engine.data_access.loaders import (
                load_horizon_candidates,
            )
            horizon_candidates = load_horizon_candidates()
        except Exception:  # noqa: BLE001 — cross-check is optional evidence
            return None
    if not horizon_candidates:
        return None
    selected_by_opp: dict[str, str] = {}
    for rec in horizon_candidates:
        opp = str(rec.get("canonical_opportunity_id", "") or "")
        if not opp or str(rec.get("selection_status", "")) != "SELECTED":
            continue
        selected_by_opp.setdefault(opp, str(rec.get("horizon", "") or ""))
    checks = [c for c in comparable
              if c["canonical_opportunity_id"] in selected_by_opp]
    if not checks:
        return None
    agreements = sum(
        1 for c in checks
        if selected_by_opp[c["canonical_opportunity_id"]]
        == c["selected_horizon"]
    )
    return {
        "checked": len(checks),
        "agreements": agreements,
        "agreement_rate": round(agreements / len(checks), 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STRAT-1 — DOES STRATEGY-RANKING CONFIDENCE PREDICT OUTCOME QUALITY?
# ═══════════════════════════════════════════════════════════════════════════════


def _spearman_rho(pairs: list[tuple[float, float]]) -> float | None:
    """
    Spearman rank correlation with average ranks for ties.
    Dependency-safe (no scipy). Returns None for degenerate inputs.
    """
    if len(pairs) < 3:
        return None

    def _ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while (j + 1 < len(order)
                   and values[order[j + 1]] == values[order[i]]):
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx = _ranks(xs)
    ry = _ranks(ys)
    n = len(pairs)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    den_x = math.sqrt(sum((a - mean_rx) ** 2 for a in rx))
    den_y = math.sqrt(sum((b - mean_ry) ** 2 for b in ry))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def build_strat1_pairs(
    strategy_candidates: list[dict[str, Any]],
    shadow_population: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Join strategy_candidates (pre-decision confidence/rank/selected) to the
    PRIMARY_HORIZON shadow outcome of the same canonical opportunity.
    """
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for rec in strategy_candidates:
        cid = str(rec.get("candidate_id", "") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        candidates.append(rec)

    outcome_by_opp: dict[str, float] = {}
    for rec in shadow_population:
        if rec["shadow_type"] != _PRIMARY:
            continue
        opp = rec["canonical_opportunity_id"]
        if not opp:
            continue
        r = rec.get("pnl_r")
        if r is None or not math.isfinite(r):
            continue
        outcome_by_opp.setdefault(opp, r)

    selected_by_opp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    n_no_lineage = 0
    for rec in candidates:
        opp = str(rec.get("canonical_opportunity_id", "") or "")
        if not opp:
            n_no_lineage += 1
            continue
        if rec.get("selected") is True:
            selected_by_opp[opp].append(rec)

    pairs: list[dict[str, Any]] = []
    ambiguous = 0
    n_no_outcome = 0
    for opp, sels in selected_by_opp.items():
        if len(sels) > 1:
            ambiguous += 1
            continue
        cand = sels[0]
        if opp not in outcome_by_opp:
            n_no_outcome += 1
            continue
        conf = cand.get("confidence")
        if conf is None:
            continue
        conf = float(conf)
        if not math.isfinite(conf):
            continue
        pairs.append({
            "canonical_opportunity_id": opp,
            "confidence": conf,
            "rank": cand.get("rank"),
            "r_multiple": outcome_by_opp[opp],
        })

    return {
        "pairs": pairs,
        "n_candidates": len(candidates),
        "n_no_lineage": n_no_lineage,
        "n_no_outcome": n_no_outcome,
        "ambiguous": ambiguous,
    }


_CONF_BUCKETS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01))


def run_strat1(
    strategy_candidates: list[dict[str, Any]] | None = None,
    shadow_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    STRAT-1: Does strategy-ranking confidence predict subsequent outcome
    quality?

    Confidence/rank are PRE-DECISION selection evidence; the outcome is the
    post-decision primary-horizon shadow R for the same opportunity. This is
    a RANKING/MONOTONICITY analysis — confidence is a relative score, not a
    probability, so no calibration claim is made. Rejected strategies have
    no simulated outcomes, so selection optimality CANNOT be proven here.
    """
    if strategy_candidates is None:
        from research_engine.data_access.loaders import (
            load_strategy_candidates,
        )
        strategy_candidates = load_strategy_candidates()
    population = (shadow_trades if shadow_trades is not None
                  else _load_simulation_population())

    built = build_strat1_pairs(strategy_candidates, population)
    pairs = built["pairs"]
    n = len(pairs)

    if n < _MIN_SAMPLE:
        conf = "INSUFFICIENT_DATA" if n < 10 else "LOW"
        return _report(
            question_id="STRAT-1",
            status="INSUFFICIENT_DATA",
            overall={
                "matched_pairs": n,
                "candidates_deduplicated": built["n_candidates"],
                "candidates_without_lineage": built["n_no_lineage"],
                "opportunities_without_outcome": built["n_no_outcome"],
                "ambiguous_selected_excluded": built["ambiguous"],
            },
            confidence=conf,
            dataset={"sample_size": n,
                     "source": "strategy_candidates_v1 + shadow_runtime_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "Requires >=30 selected-candidate/opportunity matches with "
                "confidence and a primary shadow outcome.",
            ],
        )

    buckets: dict[str, list[float]] = defaultdict(list)
    for p in pairs:
        for lo, hi in _CONF_BUCKETS:
            if lo <= p["confidence"] < hi:
                buckets[f"{lo:.2f}-{hi:.2f}"].append(p["r_multiple"])
                break
    bucket_stats = {k: _group_stats(rs) for k, rs in sorted(buckets.items())}
    sufficient_buckets = {k: s for k, s in bucket_stats.items()
                          if s["n"] >= _MIN_BUCKET}

    rho = _spearman_rho([(p["confidence"], p["r_multiple"]) for p in pairs])

    means = [s["mean_r"] for s in sufficient_buckets.values()]
    if len(sufficient_buckets) >= 3:
        monotonic = all(
            means[i] < means[i + 1] for i in range(len(means) - 1)
        ) or all(
            means[i] > means[i + 1] for i in range(len(means) - 1)
        )
        direction = "POSITIVE" if means[-1] > means[0] else "NEGATIVE"
        monotonicity = f"MONOTONIC_{direction}" if monotonic else "NON_MONOTONIC"
    else:
        monotonicity = "INSUFFICIENT_BUCKETS_FOR_MONOTONICITY"

    has_signal = (
        rho is not None and abs(rho) >= 0.1
    ) or monotonicity.startswith("MONOTONIC")

    return _report(
        question_id="STRAT-1",
        status="COMPLETE",
        overall={
            "matched_pairs": n,
            "confidence_distribution": {
                "min": round(min(p["confidence"] for p in pairs), 4),
                "max": round(max(p["confidence"] for p in pairs), 4),
                "mean": round(statistics.mean(
                    p["confidence"] for p in pairs), 4),
            },
            "rank_distribution": {
                "ranks_observed": sorted(
                    {p["rank"] for p in pairs if p["rank"] is not None}
                ),
            },
            "confidence_buckets": bucket_stats,
            "sufficient_buckets": sorted(sufficient_buckets.keys()),
            "spearman_rho": rho,
            "monotonicity": monotonicity,
            "candidates_deduplicated": built["n_candidates"],
            "candidates_without_lineage": built["n_no_lineage"],
            "opportunities_without_outcome": built["n_no_outcome"],
            "ambiguous_selected_excluded": built["ambiguous"],
        },
        confidence=_confidence(n),
        dataset={
            "sample_size": n,
            "source": "strategy_candidates_v1 + shadow_runtime_v1 "
                      "(primary-horizon outcomes)",
        },
        recommendation=(
            "CONFIDENCE_CONTAINS_OUTCOME_SIGNAL" if has_signal
            else "NO_CONFIDENCE_OUTCOME_SIGNAL_DETECTED"
        ),
        assumptions=[
            "Confidence/rank are pre-decision selection facts (persisted by "
            "strategy_candidates_v1 before any outcome exists); outcome R is "
            "post-decision research evidence only.",
            "RANKING/MONOTONICITY analysis — confidence is a relative score, "
            "NOT a probability; no calibration claim is made.",
        ],
        warnings=[
            "Rejected strategy candidates have NO simulated outcomes — "
            "selection confidence can be evaluated against the selected "
            "outcome, but SELECTION OPTIMALITY CANNOT YET BE PROVEN.",
            "Correlation/monotonicity is association, not causation.",
            f"Confidence buckets examined: {len(bucket_stats)} (multiple "
            "comparisons acknowledged).",
        ],
    )
