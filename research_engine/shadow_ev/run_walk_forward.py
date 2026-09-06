"""
CLI runner for Walk-Forward Validation of Shadow EV Models.

Usage:
    python -m research_engine.shadow_ev.run_walk_forward
"""
from __future__ import annotations
import logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.data_access.loaders import load_decision_trace
from research_engine.shadow_ev.walk_forward import run_walk_forward
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
    safe_print("WALK-FORWARD VALIDATION — SHADOW EV MODELS")
    safe_print("=" * 70)
    safe_print()
    safe_print("[1/3] Loading decision traces...")
    traces = load_decision_trace()
    safe_print(f"      Traces: {len(traces)}")
    safe_print()
    safe_print("[2/3] Running walk-forward validation...")
    r = run_walk_forward(traces)
    safe_print()

    if r.confidence == "INSUFFICIENT_DATA":
        safe_print(f"  [WARN]  {r.conclusion}")
        return

    # Per-split results
    safe_print("-" * 70)
    safe_print(f"WALK-FORWARD SPLITS (n={r.n_splits})")
    safe_print("-" * 70)
    safe_print(f"  {'Model':<10} {'Split':>5} {'Train':>6} {'Test':>5} {'Appr':>5} {'WR':>5} {'AvgR':>7} {'TotR':>7} {'DD':>6} {'TopPat':>8}")
    safe_print(f"  {'-'*10} {'-'*5} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*7} {'-'*7} {'-'*6} {'-'*8}")
    for sp in r.splits:
        conc = f"{sp.top_pattern_contribution:.0%}" if sp.approved > 0 else "—"
        safe_print(
            f"  {sp.model:<10} {sp.split:>5} {sp.train_size:>6} {sp.test_size:>5} "
            f"{sp.approved:>5} {sp.win_rate:>4.0%} {sp.avg_r:>+6.3f} {sp.total_r:>+6.1f} "
            f"{sp.max_drawdown:>5.1f} {conc:>8}"
        )
    safe_print()

    # Model summary
    safe_print("-" * 70)
    safe_print("MODEL SUMMARY (across all splits)")
    safe_print("-" * 70)
    safe_print(f"  {'Model':<10} {'Pos/Tot':>8} {'Rate':>6} {'Total R':>8} {'Approved':>8} {'Avg WR':>7} {'Max DD':>7}")
    safe_print(f"  {'-'*10} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*7} {'-'*7}")
    for m, s in r.model_summary.items():
        star = "*" if r.acceptance.get(m, {}).get("PASSES_ALL") else " "
        safe_print(
            f" {star}{m:<9} {s['splits_positive']}/{s['splits_total']:>5} "
            f"{s['positive_rate']:>5.0%} {s['total_r']:>+7.1f} "
            f"{s['total_approved']:>8} {s['avg_win_rate']:>6.0%} {s['max_drawdown']:>6.1f}"
        )
    safe_print()

    # Pattern dependency
    safe_print("-" * 70)
    safe_print("PATTERN DEPENDENCY ANALYSIS")
    safe_print("-" * 70)
    safe_print(f"  {'Model':<10} {'Total R':>8} {'Without Top':>11} {'Top Pattern':<25} {'Fraction':>8} {'Survives':>8}")
    safe_print(f"  {'-'*10} {'-'*8} {'-'*11} {'-'*25} {'-'*8} {'-'*8}")
    for pd in r.pattern_dependency:
        surv = "YES" if pd.edge_survives_without_top else "NO"
        safe_print(
            f"  {pd.model:<10} {pd.total_r_all:>+7.1f} {pd.total_r_without_top:>+10.1f} "
            f"{pd.top_pattern:<25} {pd.top_pattern_fraction:>7.0%} {surv:>8}"
        )
    safe_print()

    # Acceptance criteria
    safe_print("-" * 70)
    safe_print("ACCEPTANCE CRITERIA")
    safe_print("-" * 70)
    for m, a in r.acceptance.items():
        status = "[OK]  PASS" if a["PASSES_ALL"] else "❌ FAIL"
        safe_print(f"  {m:<10} {status}")
        for crit, passed in a.items():
            if crit == "PASSES_ALL":
                continue
            mark = "✓" if passed else "✗"
            safe_print(f"    {mark} {crit}")
    safe_print()

    # Verdict
    safe_print("-" * 70)
    safe_print("RECOMMENDATION")
    safe_print("-" * 70)
    safe_print(f"  {r.recommendation}")
    safe_print(f"  Confidence: {r.confidence}")
    safe_print(f"  CONCLUSION: {r.conclusion}")
    safe_print("-" * 70)
    safe_print()

    safe_print("[3/3] Generating report...")
    report_path = generate_report(
        experiment_name="walk_forward_shadow_ev",
        question_id="SHADOW_EV_WF",
        question_text="Do shadow EV models maintain positive expectancy out-of-sample?",
        dataset_sources=["s3:decision_trace", "replay_data/ (offline replay fixtures)"],
        sample_count=r.decisions_with_outcome,
        metrics=r.to_dict(),
        conclusion=r.conclusion,
        confidence=r.confidence,
    )
    safe_print(f"      Report: {report_path}")
    safe_print("\n[OK]  Walk-Forward Validation complete.")


if __name__ == "__main__":
    main()
