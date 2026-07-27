"""
M10 — Strategy Family Required Per Phase.

Question:
    Does each market phase require a different strategy family (reversal,
    continuation, momentum, breakout) rather than different pattern weighting
    within one family?

Classifies each pattern into a strategy family, then analyses performance
by market_phase × strategy_family to determine whether family selection
should precede pattern selection.

Uses CURRENT-epoch shadow trades only.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    compute_confidence,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)
from research_engine.data_quality.classifier import classify_record, DataEpoch


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY FAMILY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_FAMILIES: dict[str, str] = {
    # REVERSAL — patterns that signal trend exhaustion and direction change
    "TWEEZER_TOP": "REVERSAL",
    "TWEEZER_BOTTOM": "REVERSAL",
    "HAMMER": "REVERSAL",
    "HANGING_MAN": "REVERSAL",
    "INVERTED_HAMMER": "REVERSAL",
    "SHOOTING_STAR": "REVERSAL",
    "MORNING_STAR": "REVERSAL",
    "EVENING_STAR": "REVERSAL",
    # CONTINUATION — patterns that confirm existing trend continuation
    "THREE_WHITE_SOLDIERS": "CONTINUATION",
    "THREE_BLACK_CROWS": "CONTINUATION",
    "THREE_INSIDE_UP": "CONTINUATION",
    "THREE_INSIDE_DOWN": "CONTINUATION",
    # BREAKOUT — patterns that signal range escape
    "BULLISH_ENGULFING": "BREAKOUT",
    "BEARISH_ENGULFING": "BREAKOUT",
}

FAMILY_ORDER = ["REVERSAL", "CONTINUATION", "BREAKOUT", "UNKNOWN"]


def classify_strategy_family(pattern: str) -> str:
    """Classify a pattern name into its strategy family."""
    return STRATEGY_FAMILIES.get(pattern, "UNKNOWN")


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_TOTAL_SAMPLES = 100
_MIN_CELL_SAMPLES = 10
_HIGH_CONFIDENCE_N = 50
_VALID_PHASES = {"IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXPERIMENT
# ═══════════════════════════════════════════════════════════════════════════════


def run_m10_strategy_family_per_phase(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Run M10: Strategy Family Required Per Phase.

    Classifies patterns into families, then analyses performance by
    market_phase × strategy_family.
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    # Filter to CURRENT epoch
    current = [r for r in shadow_trades if classify_record(r) == DataEpoch.CURRENT]

    # Extract analysable records
    analysable = []
    for r in current:
        ds = r.get("decision_snapshot", {})
        outcome = r.get("simulated_outcome", {})
        phase = ds.get("market_phase", "")
        pattern = ds.get("pattern", "")
        r_mult = outcome.get("pnl_r_multiple")
        mfe = outcome.get("mfe_r")
        mae = outcome.get("mae_r")

        if phase in _VALID_PHASES and pattern and r_mult is not None:
            family = classify_strategy_family(pattern)
            analysable.append({
                "phase": phase,
                "pattern": pattern,
                "family": family,
                "r": float(r_mult),
                "mfe": float(mfe) if mfe is not None else 0.0,
                "mae": float(mae) if mae is not None else 0.0,
            })

    n_total = len(analysable)

    # Readiness check
    if n_total < _MIN_TOTAL_SAMPLES:
        return build_report(
            question_id="M10",
            status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n_total} phase-labelled trades (need {_MIN_TOTAL_SAMPLES})",
                     "current_records": len(current), "phase_labelled": n_total},
            confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades_current_epoch", "sample_size": n_total},
            fingerprint=build_fingerprint(n_total, len(current) - n_total, "shadow_trades"),
            recommendation="WAIT",
            warnings=[f"Need {_MIN_TOTAL_SAMPLES - n_total} more phase-labelled trades"],
            provenance=_provenance(),
        )

    # ─── FAMILY DISTRIBUTION ──────────────────────────────────────────
    family_counts: dict[str, int] = defaultdict(int)
    for t in analysable:
        family_counts[t["family"]] += 1

    # ─── GROUP BY PHASE × FAMILY ─────────────────────────────────────
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trade in analysable:
        cells[(trade["phase"], trade["family"])].append(trade)

    # ─── COMPUTE PER-CELL METRICS ─────────────────────────────────────
    cell_results: list[dict[str, Any]] = []
    for (phase, family), trades in cells.items():
        n = len(trades)
        if n < _MIN_CELL_SAMPLES:
            continue

        r_values = [t["r"] for t in trades]
        mfe_values = [t["mfe"] for t in trades]
        mae_values = [t["mae"] for t in trades]

        ev = sum(r_values) / n
        wr = sum(1 for r in r_values if r > 0) / n
        avg_mfe = sum(mfe_values) / n
        avg_mae = sum(mae_values) / n
        directional = sum(1 for i in range(n) if mfe_values[i] > mae_values[i]) / n

        if n >= _HIGH_CONFIDENCE_N:
            confidence = "HIGH"
        elif n >= 30:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        cell_results.append({
            "phase": phase,
            "family": family,
            "n": n,
            "ev": round(ev, 4),
            "win_rate": round(wr, 4),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
            "directional_pct": round(directional, 4),
            "confidence": confidence,
        })

    cell_results.sort(key=lambda c: c["ev"], reverse=True)

    # ─── BEST FAMILY PER PHASE ────────────────────────────────────────
    best_family_per_phase: dict[str, dict] = {}
    for phase in _VALID_PHASES:
        phase_cells = [c for c in cell_results if c["phase"] == phase]
        if phase_cells:
            best_family_per_phase[phase] = phase_cells[0]

    # ─── FAMILY SUMMARIES (across all phases) ─────────────────────────
    family_summaries: dict[str, dict] = {}
    for family in FAMILY_ORDER:
        family_trades = [t for t in analysable if t["family"] == family]
        if family_trades:
            n_f = len(family_trades)
            ev_f = sum(t["r"] for t in family_trades) / n_f
            wr_f = sum(1 for t in family_trades if t["r"] > 0) / n_f
            family_summaries[family] = {
                "n": n_f,
                "ev": round(ev_f, 4),
                "win_rate": round(wr_f, 4),
                "phases_present": len(set(t["phase"] for t in family_trades)),
            }

    # ─── INTERACTION DETECTION ────────────────────────────────────────
    # Does family performance DEPEND on phase? (interaction effect)
    # Simple test: does the best family change across phases?
    dominant_families = set()
    for phase, best in best_family_per_phase.items():
        dominant_families.add(best["family"])

    interaction_detected = len(dominant_families) > 1
    interaction_description = ""
    if interaction_detected:
        phase_winners = {p: c["family"] for p, c in best_family_per_phase.items()}
        interaction_description = f"Different phases favour different families: {phase_winners}"
    else:
        single_family = list(dominant_families)[0] if dominant_families else "NONE"
        interaction_description = f"Same family ({single_family}) dominates all phases — no interaction effect"

    # ─── INSUFFICIENT FAMILIES ────────────────────────────────────────
    missing_families = []
    for family in ["REVERSAL", "CONTINUATION", "BREAKOUT"]:
        if family not in family_summaries or family_summaries[family]["n"] < _MIN_CELL_SAMPLES:
            missing_families.append(family)

    # ─── WARNINGS ─────────────────────────────────────────────────────
    warnings: list[str] = []
    if missing_families:
        warnings.append(f"Insufficient evidence for families: {missing_families}")
    if family_counts.get("UNKNOWN", 0) > 0:
        warnings.append(f"{family_counts['UNKNOWN']} trades with unclassified patterns")

    low_cells = [c for c in cell_results if c["confidence"] == "LOW"]
    if len(low_cells) > len(cell_results) * 0.5:
        warnings.append("Majority of cells have LOW confidence — interpret with caution")

    # ─── RECOMMENDATION ───────────────────────────────────────────────
    if interaction_detected and any(c["ev"] > 0 and c["confidence"] in ("HIGH", "MEDIUM") for c in cell_results):
        recommendation = "MONITOR"
        finding = (
            f"Interaction detected: {interaction_description}. "
            f"Best overall: {cell_results[0]['family']} in {cell_results[0]['phase']} "
            f"(EV={cell_results[0]['ev']:+.4f}R, n={cell_results[0]['n']})."
        )
    elif interaction_detected:
        recommendation = "WAIT"
        finding = (
            f"Interaction detected but no HIGH/MEDIUM confidence positive EV cell. "
            f"{interaction_description}. Need more data per cell."
        )
    else:
        recommendation = "WAIT"
        finding = (
            f"No interaction effect detected. {interaction_description}. "
            f"Strategy family gating would not add value based on current evidence."
        )

    # ─── BUILD REPORT ─────────────────────────────────────────────────
    report = build_report(
        question_id="M10",
        status=ReadinessStatus.COMPLETE,
        overall={
            "total_analysed": n_total,
            "cells_reported": len(cell_results),
            "family_distribution": dict(family_counts),
            "family_summaries": family_summaries,
            "best_family_per_phase": best_family_per_phase,
            "all_cells": cell_results,
            "interaction_detected": interaction_detected,
            "interaction_description": interaction_description,
            "missing_families": missing_families,
            "finding": finding,
        },
        confidence=compute_confidence(n_total, interaction_detected),
        dataset={"source": "shadow_trades_current_epoch", "sample_size": n_total,
                 "total_current": len(current),
                 "family_mapping_version": "1.0",
                 "families_classified": len(STRATEGY_FAMILIES)},
        fingerprint=build_fingerprint(n_total, len(current) - n_total, "shadow_trades_current"),
        recommendation=recommendation,
        assumptions=[
            "Pattern → family mapping is fixed (see STRATEGY_FAMILIES dict)",
            "Uses CURRENT-epoch data only (post-lineage migration)",
            f"Minimum {_MIN_CELL_SAMPLES} trades per phase×family cell",
            "Interaction = best family differs across phases",
            "BREAKOUT family has limited representation (only engulfing patterns)",
        ],
        warnings=warnings,
        provenance=_provenance(),
    )

    persist_report(report, "m10_strategy_family_per_phase.json")
    update_knowledge_map("M10", finding, recommendation)

    return report


def _provenance() -> dict[str, Any]:
    return {
        "experiment_module": "research_engine.experiments.m10_strategy_family_per_phase",
        "registry_id": "M10",
        "function": "run_m10_strategy_family_per_phase",
        "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    result = run_m10_strategy_family_per_phase()
    status = result.get("status", "?")
    overall = result.get("overall", {})

    print(f"\nM10: {status} | {overall.get('total_analysed', 0)} trades | {overall.get('cells_reported', 0)} cells")
    print(f"Finding: {overall.get('finding', '?')}")
    print(f"Interaction: {overall.get('interaction_detected', '?')}")

    if overall.get("family_summaries"):
        print("\nFamily Summaries (all phases combined):")
        for family, s in sorted(overall["family_summaries"].items()):
            print(f"  {family:15s} n={s['n']:>3d} EV={s['ev']:+.4f} WR={s['win_rate']:.0%}")

    if overall.get("best_family_per_phase"):
        print("\nBest Family Per Phase:")
        for phase, cell in sorted(overall["best_family_per_phase"].items()):
            print(f"  {phase:15s} -> {cell['family']:15s} EV={cell['ev']:+.4f} n={cell['n']} [{cell['confidence']}]")

    if overall.get("all_cells"):
        print("\nAll Phase × Family Cells:")
        for c in overall["all_cells"]:
            print(f"  {c['phase']:15s} × {c['family']:15s} EV={c['ev']:+.4f} WR={c['win_rate']:.0%} n={c['n']} [{c['confidence']}]")
