"""
Research Engine — Main Entry Point

Runs the Phase 1 validation experiment (Q16: Shadow Validation).

Usage:
    python -m research_engine.main

This module:
    - Loads existing persisted trading data (read-only)
    - Builds correlated research records
    - Runs the ShadowAccuracyExperiment
    - Generates a validation report

It does NOT:
    - Modify any production data
    - Import any execution modules
    - Change trading decisions
    - Require the trading engine to be running
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.console import configure_console, safe_print
from research_engine.data_access.loaders import load_trade_truth
from research_engine.data_access.shadow_runtime_ingestion import (
    ingest_completed_shadow_trades,
)
from research_engine.correlation.linker import build_research_records
from research_engine.experiments.shadow_validation import run_shadow_validation
from research_engine.reports.generator import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run Phase 1 Research Engine: Q16 Shadow Validation."""
    # Encoding safety: decorative output must never crash the process on a
    # restrictive Windows console (e.g. cp1252). Real research exceptions
    # below are NOT affected - only encoding/rendering failures degrade.
    configure_console(sys.stdout, sys.stderr)

    safe_print("=" * 60)
    safe_print("RESEARCH ENGINE - Phase 1: Shadow Validation (Q16)")
    safe_print("=" * 60)
    safe_print()

    # ─── 1. LOAD DATA ─────────────────────────────────────────────────
    safe_print("[1/4] Loading research data...")
    # Canonical production shadow population: S3 shadow_runtime_v1 event
    # stream reconstructed into completed shadow outcomes (nshadow_*).
    shadow_trades = ingest_completed_shadow_trades()
    trade_truths = load_trade_truth()

    if not shadow_trades:
        safe_print("\n[WARN] No completed shadow outcomes available from the canonical")
        safe_print("    S3 shadow_runtime_v1 source.")
        safe_print("    Run the trading system with shadow trades enabled to generate data.")
        return

    safe_print(f"      Shadow trades: {len(shadow_trades)}")
    safe_print(f"      Trade truths:  {len(trade_truths)}")
    safe_print()

    # ─── 2. CORRELATE ─────────────────────────────────────────────────
    safe_print("[2/4] Building correlated research records...")
    research_records = build_research_records(shadow_trades, trade_truths)

    matched = [r for r in research_records if r.is_matched()]
    shadow_only = sum(1 for r in research_records if r.has_shadow and not r.has_live)
    live_only = sum(1 for r in research_records if r.has_live and not r.has_shadow)

    safe_print(f"      Total records:  {len(research_records)}")
    safe_print(f"      Matched:        {len(matched)}")
    safe_print(f"      Shadow only:    {shadow_only}")
    safe_print(f"      Live only:      {live_only}")
    safe_print()

    # ─── 3. RUN EXPERIMENT ────────────────────────────────────────────
    safe_print("[3/4] Running Q16: Shadow Validation Experiment...")
    result = run_shadow_validation(research_records)

    safe_print()
    safe_print("-" * 60)
    safe_print("SHADOW VALIDATION REPORT")
    safe_print("-" * 60)
    safe_print(f"  Trades analysed:       {result.matched_trades}")
    safe_print(f"  Average shadow R:      {result.avg_shadow_r:+.3f}")
    safe_print(f"  Average live R:        {result.avg_live_r:+.3f}")
    safe_print(f"  Prediction error:      {result.avg_prediction_error:+.3f}R")
    safe_print(f"  Mean absolute error:   {result.mean_absolute_error:.3f}R")
    safe_print(f"  Correlation:           {result.correlation:.3f}" if result.correlation is not None else "  Correlation:           N/A (insufficient data)")
    safe_print(f"  Shadow win rate:       {result.shadow_win_rate:.1%}")
    safe_print(f"  Live win rate:         {result.live_win_rate:.1%}")
    safe_print(f"  Directional accuracy:  {result.directional_accuracy:.1%}")
    safe_print(f"  Confidence:            {result.confidence}")
    safe_print()
    safe_print(f"  CONCLUSION: {result.conclusion}")
    safe_print("-" * 60)
    safe_print()

    # ─── 4. GENERATE REPORT ───────────────────────────────────────────
    safe_print("[4/4] Generating research report...")
    report_path = generate_report(
        experiment_name="shadow_validation",
        question_id="Q16",
        question_text="How well do shadow R-multiples predict live R-multiples?",
        dataset_sources=["s3:shadow_runtime_v1(ingested, nshadow_*)", "s3:trade_truth"],
        sample_count=result.matched_trades,
        metrics=result.to_dict(),
        conclusion=result.conclusion,
        confidence=result.confidence,
        metadata={
            "total_shadow_trades": result.total_shadow_trades,
            "total_live_trades": result.total_live_trades,
            "shadow_only_count": shadow_only,
            "live_only_count": live_only,
        },
    )
    safe_print(f"      Report saved: {report_path}")
    safe_print()
    safe_print("[OK] Research Engine Phase 1 complete.")


if __name__ == "__main__":
    main()
