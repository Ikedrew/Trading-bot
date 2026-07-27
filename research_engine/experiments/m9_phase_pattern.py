"""
M9 — Phase-Appropriate Pattern Classification.

Question:
    For each market phase (IMPULSE, PULLBACK, CONSOLIDATION, EXHAUSTION, REVERSAL),
    which patterns actually belong there and produce positive expectancy?

Uses CURRENT-epoch shadow trades only. Reports per-phase pattern performance
with confidence warnings for low sample groups.

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
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_TOTAL_SAMPLES = 100
_MIN_CELL_SAMPLES = 10      # Minimum per phase×pattern cell to report
_HIGH_CONFIDENCE_N = 50     # n >= 50 for HIGH confidence per cell
_VALID_PHASES = {"IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXPERIMENT
# ═══════════════════════════════════════════════════════════════════════════════


def run_m9_phase_pattern(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Run M9: Phase-Appropriate Pattern Classification.

    Analyses CURRENT-epoch shadow trades grouped by market_phase × pattern.
    Reports best/worst combinations with confidence levels.
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    # Filter to CURRENT epoch only
    current = [r for r in shadow_trades if classify_record(r) == DataEpoch.CURRENT]

    # Filter to records with valid phase and outcome
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
            analysable.append({
                "phase": phase,
                "pattern": pattern,
                "r": float(r_mult),
                "mfe": float(mfe) if mfe is not None else 0.0,
                "mae": float(mae) if mae is not None else 0.0,
            })

    n_total = len(analysable)

    # Readiness check
    if n_total < _MIN_TOTAL_SAMPLES:
        return build_report(
            question_id="M9",
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

    # ─── GROUP BY PHASE × PATTERN ────────────────────────────────────
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trade in analysable:
        cells[(trade["phase"], trade["pattern"])].append(trade)

    # ─── COMPUTE PER-CELL METRICS ─────────────────────────────────────
    cell_results: list[dict[str, Any]] = []
    for (phase, pattern), trades in cells.items():
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
        total_r = sum(r_values)

        # Confidence
        if n >= _HIGH_CONFIDENCE_N:
            confidence = "HIGH"
        elif n >= 30:
            confidence = "MEDIUM"
        elif n >= _MIN_CELL_SAMPLES:
            confidence = "LOW"
        else:
            confidence = "INSUFFICIENT"

        cell_results.append({
            "phase": phase,
            "pattern": pattern,
            "n": n,
            "ev": round(ev, 4),
            "win_rate": round(wr, 4),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
            "directional_pct": round(directional, 4),
            "total_r": round(total_r, 2),
            "confidence": confidence,
        })

    # Sort by EV descending
    cell_results.sort(key=lambda c: c["ev"], reverse=True)

    # ─── IDENTIFY BEST/WORST PER PHASE ────────────────────────────────
    best_per_phase: dict[str, dict] = {}
    worst_per_phase: dict[str, dict] = {}

    for phase in _VALID_PHASES:
        phase_cells = [c for c in cell_results if c["phase"] == phase]
        if phase_cells:
            best_per_phase[phase] = phase_cells[0]  # Already sorted by EV desc
            worst_per_phase[phase] = phase_cells[-1]

    # ─── PHASE SUMMARIES ──────────────────────────────────────────────
    phase_summaries: dict[str, dict] = {}
    for phase in _VALID_PHASES:
        phase_trades = [t for t in analysable if t["phase"] == phase]
        if phase_trades:
            n_p = len(phase_trades)
            ev_p = sum(t["r"] for t in phase_trades) / n_p
            wr_p = sum(1 for t in phase_trades if t["r"] > 0) / n_p
            phase_summaries[phase] = {
                "n": n_p,
                "ev": round(ev_p, 4),
                "win_rate": round(wr_p, 4),
                "patterns_analysed": len([c for c in cell_results if c["phase"] == phase]),
            }

    # ─── WARNINGS ─────────────────────────────────────────────────────
    warnings: list[str] = []
    low_sample_phases = [p for p, s in phase_summaries.items() if s["n"] < 50]
    if low_sample_phases:
        warnings.append(f"Low sample phases (<50 trades): {low_sample_phases}")

    positive_ev_cells = [c for c in cell_results if c["ev"] > 0 and c["confidence"] in ("HIGH", "MEDIUM")]
    if not positive_ev_cells:
        warnings.append("No phase×pattern combination shows positive EV with MEDIUM+ confidence")

    # ─── RECOMMENDATION ───────────────────────────────────────────────
    if positive_ev_cells:
        best = positive_ev_cells[0]
        recommendation = "MONITOR"
        finding = (
            f"Best combination: {best['pattern']} in {best['phase']} "
            f"(EV={best['ev']:+.4f}R, n={best['n']}, {best['confidence']}). "
            f"Analysed {len(cell_results)} phase×pattern cells from {n_total} trades."
        )
    else:
        recommendation = "WAIT"
        finding = (
            f"No phase×pattern combination shows validated positive EV. "
            f"Analysed {len(cell_results)} cells from {n_total} trades across {len(phase_summaries)} phases."
        )

    # ─── BUILD REPORT ─────────────────────────────────────────────────
    report = build_report(
        question_id="M9",
        status=ReadinessStatus.COMPLETE,
        overall={
            "total_analysed": n_total,
            "cells_reported": len(cell_results),
            "phases_with_data": len(phase_summaries),
            "phase_summaries": phase_summaries,
            "best_per_phase": best_per_phase,
            "worst_per_phase": worst_per_phase,
            "all_cells": cell_results,
            "positive_ev_cells": [c for c in cell_results if c["ev"] > 0],
            "finding": finding,
        },
        confidence=compute_confidence(n_total, bool(positive_ev_cells)),
        dataset={"source": "shadow_trades_current_epoch", "sample_size": n_total,
                 "total_current": len(current), "phase_coverage": f"{n_total*100//len(current)}%"},
        fingerprint=build_fingerprint(n_total, len(current) - n_total, "shadow_trades_current"),
        recommendation=recommendation,
        assumptions=[
            "Uses CURRENT-epoch data only (post-lineage migration)",
            f"Minimum {_MIN_CELL_SAMPLES} trades per phase×pattern cell",
            "MFE/MAE measured over full trade life (look-ahead present in MFE)",
            "Phase classified by MarketContextBuilder from H1 structure",
        ],
        warnings=warnings,
        provenance=_provenance(),
    )

    # Persist
    persist_report(report, "m9_phase_pattern.json")

    # Update knowledge map
    update_knowledge_map("M9", finding, recommendation)

    return report


def _provenance() -> dict[str, Any]:
    return {
        "experiment_module": "research_engine.experiments.m9_phase_pattern",
        "registry_id": "M9",
        "function": "run_m9_phase_pattern",
        "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    result = run_m9_phase_pattern()
    status = result.get("status", "?")
    overall = result.get("overall", {})

    print(f"\nM9: {status} | {overall.get('total_analysed', 0)} trades | {overall.get('cells_reported', 0)} cells")
    print(f"Finding: {overall.get('finding', '?')}")

    if overall.get("phase_summaries"):
        print("\nPhase Summaries:")
        for phase, s in sorted(overall["phase_summaries"].items()):
            print(f"  {phase:15s} n={s['n']:>3d} EV={s['ev']:+.4f} WR={s['win_rate']:.0%}")

    if overall.get("best_per_phase"):
        print("\nBest Pattern Per Phase:")
        for phase, cell in sorted(overall["best_per_phase"].items()):
            print(f"  {phase:15s} → {cell['pattern']:20s} EV={cell['ev']:+.4f} n={cell['n']} [{cell['confidence']}]")

    positive = overall.get("positive_ev_cells", [])
    if positive:
        print(f"\nPositive EV Cells ({len(positive)}):")
        for c in positive[:10]:
            print(f"  {c['phase']:15s} × {c['pattern']:20s} EV={c['ev']:+.4f} WR={c['win_rate']:.0%} n={c['n']} [{c['confidence']}]")
