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
from research_engine.console import configure_console, safe_print
from research_engine.reports.generator import generate_report

# Local replay candles are an EXPLICIT offline fixture mode (Gap 3/9):
# normal Research Engine execution never consumes replay_data/.
OFFLINE_REPLAY_FLAG = "--offline-replay"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    configure_console(sys.stdout, sys.stderr)
    if OFFLINE_REPLAY_FLAG not in sys.argv:
        safe_print("[REFUSED] " + __name__ + " requires explicit --offline-replay.")
        safe_print("       Local replay_data/ candles are an offline fixture mode,")
        safe_print("       never a production-evidence source. Re-run with --offline-replay.")
        raise SystemExit(2)
    safe_print("=" * 70)
    safe_print("SHADOW EV MODEL — HISTORICAL REPLAY COMPARISON")
    safe_print("=" * 70)
    safe_print()
    safe_print("[1/3] Loading decision traces...")
    traces = load_decision_trace()
    safe_print(f"      Traces: {len(traces)}")
    safe_print()
    safe_print("[2/3] Running shadow EV replay (all models)...")
    result = run_shadow_ev_replay(traces)
    safe_print()

    if result.confidence == "INSUFFICIENT_DATA":
        safe_print(f"  [WARN]  {result.conclusion}")
        return

    # Model comparison
    safe_print("-" * 70)
    safe_print("MODEL PERFORMANCE COMPARISON")
    safe_print("-" * 70)
    safe_print(f"  {'Model':<12} {'Approv':>6} {'Rate':>6} {'WR':>5} {'AvgR':>7} {'TotalR':>8} {'PF':>5} {'MaxDD':>7} {'BlkWR':>6}")
    safe_print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*5} {'-'*7} {'-'*8} {'-'*5} {'-'*7} {'-'*6}")
    for m in result.models:
        star = "*" if m.name == result.best_model else " "
        safe_print(
            f" {star}{m.name:<11} {m.approved:>6} {m.approval_rate:>5.1%} "
            f"{m.win_rate:>4.0%} {m.avg_r:>+6.3f} {m.total_r:>+7.1f} "
            f"{m.profit_factor:>4.1f} {m.max_drawdown_r:>6.1f} {m.blocked_wr:>5.0%}"
        )
    safe_print()

    # Opportunity recovery
    safe_print("-" * 70)
    safe_print("OPPORTUNITY RECOVERY (trades approved that EXISTING rejected)")
    safe_print("-" * 70)
    for model, count in result.opportunity_recovery.items():
        fp = result.false_positive_analysis.get(model, {})
        if count > 0 and fp:
            safe_print(f"  {model:<12} recovered={count:>4}  good={fp.get('good_trades',0)}  bad={fp.get('bad_trades',0)}  net_R={fp.get('net_r',0):+.1f}  recovery_WR={fp.get('recovery_wr',0):.0%}")
        else:
            safe_print(f"  {model:<12} recovered={count:>4}")
    safe_print()

    # Verdict
    safe_print("-" * 70)
    safe_print("VERDICT")
    safe_print("-" * 70)
    safe_print(f"  Best model: {result.best_model}")
    safe_print(f"  Confidence: {result.confidence}")
    safe_print(f"  CONCLUSION: {result.conclusion}")
    safe_print("-" * 70)
    safe_print()

    safe_print("[3/3] Generating report...")
    report_path = generate_report(
        experiment_name="shadow_ev_replay",
        question_id="SHADOW_EV",
        question_text="Which alternative EV model produces the best historical trade selection?",
        dataset_sources=["s3:decision_trace", "replay_data/ (offline replay fixtures)"],
        sample_count=result.decisions_with_outcome,
        metrics=result.to_dict(),
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    safe_print(f"      Report: {report_path}")
    safe_print("\n[OK]  Shadow EV Replay complete.")


if __name__ == "__main__":
    main()
