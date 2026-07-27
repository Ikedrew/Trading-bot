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

from research_engine.data_access.loaders import load_shadow_trades, load_trade_truth
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
    print("=" * 60)
    print("RESEARCH ENGINE — Phase 1: Shadow Validation (Q16)")
    print("=" * 60)
    print()

    # ─── 1. LOAD DATA ─────────────────────────────────────────────────
    print("[1/4] Loading research data...")
    shadow_trades = load_shadow_trades()
    trade_truths = load_trade_truth()

    if not shadow_trades:
        print("\n⚠️  No shadow trade data found in logs/shadow_trades/")
        print("    The Research Engine requires existing shadow trade records.")
        print("    Run the trading system with shadow trades enabled to generate data.")
        return

    print(f"      Shadow trades: {len(shadow_trades)}")
    print(f"      Trade truths:  {len(trade_truths)}")
    print()

    # ─── 2. CORRELATE ─────────────────────────────────────────────────
    print("[2/4] Building correlated research records...")
    research_records = build_research_records(shadow_trades, trade_truths)

    matched = [r for r in research_records if r.is_matched()]
    shadow_only = sum(1 for r in research_records if r.has_shadow and not r.has_live)
    live_only = sum(1 for r in research_records if r.has_live and not r.has_shadow)

    print(f"      Total records:  {len(research_records)}")
    print(f"      Matched:        {len(matched)}")
    print(f"      Shadow only:    {shadow_only}")
    print(f"      Live only:      {live_only}")
    print()

    # ─── 3. RUN EXPERIMENT ────────────────────────────────────────────
    print("[3/4] Running Q16: Shadow Validation Experiment...")
    result = run_shadow_validation(research_records)

    print()
    print("─" * 60)
    print("SHADOW VALIDATION REPORT")
    print("─" * 60)
    print(f"  Trades analysed:       {result.matched_trades}")
    print(f"  Average shadow R:      {result.avg_shadow_r:+.3f}")
    print(f"  Average live R:        {result.avg_live_r:+.3f}")
    print(f"  Prediction error:      {result.avg_prediction_error:+.3f}R")
    print(f"  Mean absolute error:   {result.mean_absolute_error:.3f}R")
    print(f"  Correlation:           {result.correlation:.3f}" if result.correlation is not None else "  Correlation:           N/A (insufficient data)")
    print(f"  Shadow win rate:       {result.shadow_win_rate:.1%}")
    print(f"  Live win rate:         {result.live_win_rate:.1%}")
    print(f"  Directional accuracy:  {result.directional_accuracy:.1%}")
    print(f"  Confidence:            {result.confidence}")
    print()
    print(f"  CONCLUSION: {result.conclusion}")
    print("─" * 60)
    print()

    # ─── 4. GENERATE REPORT ───────────────────────────────────────────
    print("[4/4] Generating research report...")
    report_path = generate_report(
        experiment_name="shadow_validation",
        question_id="Q16",
        question_text="How well do shadow R-multiples predict live R-multiples?",
        dataset_sources=["logs/shadow_trades/", "logs/trade_truth/"],
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
    print(f"      Report saved: {report_path}")
    print()
    print("✅ Research Engine Phase 1 complete.")


if __name__ == "__main__":
    main()
