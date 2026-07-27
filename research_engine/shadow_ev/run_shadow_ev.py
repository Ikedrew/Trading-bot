"""
CLI runner for Shadow EV Model comparison.

Usage:
    python -m research_engine.shadow_ev.run_shadow_ev
"""
from __future__ import annotations
import logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.data_access.loaders import load_decision_trace
from research_engine.shadow_ev.replay import run_shadow_ev_replay
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    print("=" * 70)
    print("SHADOW EV MODEL — HISTORICAL REPLAY COMPARISON")
    print("=" * 70)
    print()
    print("[1/3] Loading decision traces...")
    traces = load_decision_trace()
    print(f"      Traces: {len(traces)}")
    print()
    print("[2/3] Running shadow EV replay (all models)...")
    result = run_shadow_ev_replay(traces)
    print()

    if result.confidence == "INSUFFICIENT_DATA":
        print(f"  ⚠ {result.conclusion}")
        return

    # Model comparison
    print("─" * 70)
    print("MODEL PERFORMANCE COMPARISON")
    print("─" * 70)
    print(f"  {'Model':<12} {'Approv':>6} {'Rate':>6} {'WR':>5} {'AvgR':>7} {'TotalR':>8} {'PF':>5} {'MaxDD':>7} {'BlkWR':>6}")
    print(f"  {'─'*12} {'─'*6} {'─'*6} {'─'*5} {'─'*7} {'─'*8} {'─'*5} {'─'*7} {'─'*6}")
    for m in result.models:
        star = "★" if m.name == result.best_model else " "
        print(
            f" {star}{m.name:<11} {m.approved:>6} {m.approval_rate:>5.1%} "
            f"{m.win_rate:>4.0%} {m.avg_r:>+6.3f} {m.total_r:>+7.1f} "
            f"{m.profit_factor:>4.1f} {m.max_drawdown_r:>6.1f} {m.blocked_wr:>5.0%}"
        )
    print()

    # Opportunity recovery
    print("─" * 70)
    print("OPPORTUNITY RECOVERY (trades approved that EXISTING rejected)")
    print("─" * 70)
    for model, count in result.opportunity_recovery.items():
        fp = result.false_positive_analysis.get(model, {})
        if count > 0 and fp:
            print(f"  {model:<12} recovered={count:>4}  good={fp.get('good_trades',0)}  bad={fp.get('bad_trades',0)}  net_R={fp.get('net_r',0):+.1f}  recovery_WR={fp.get('recovery_wr',0):.0%}")
        else:
            print(f"  {model:<12} recovered={count:>4}")
    print()

    # Verdict
    print("─" * 70)
    print("VERDICT")
    print("─" * 70)
    print(f"  Best model: {result.best_model}")
    print(f"  Confidence: {result.confidence}")
    print(f"  CONCLUSION: {result.conclusion}")
    print("─" * 70)
    print()

    print("[3/3] Generating report...")
    report_path = generate_report(
        experiment_name="shadow_ev_replay",
        question_id="SHADOW_EV",
        question_text="Which alternative EV model produces the best historical trade selection?",
        dataset_sources=["logs/decision_trace/", "replay_data/"],
        sample_count=result.decisions_with_outcome,
        metrics=result.to_dict(),
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    print(f"      Report: {report_path}")
    print("\n✅ Shadow EV Replay complete.")


if __name__ == "__main__":
    main()
