"""
CLI runner for Candidate Walk-Forward Validation.

Usage:
    python -m research_engine.edge_candidates.run_candidate_validation
"""
from __future__ import annotations
import json, logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.data_access.loaders import load_decision_trace
from research_engine.edge_attribution.models import build_attribution_record, EdgeAttributionRecord
from research_engine.edge_candidates.generator import generate_candidates
from research_engine.edge_candidates.validation import validate_candidates
from research_engine.counterfactual.simulator import simulate_blocked_decision
from research_engine.counterfactual.schema import SimulationConfidence
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    print("=" * 70)
    print("CANDIDATE WALK-FORWARD VALIDATION")
    print("    Test which edge candidates survive unseen data")
    print("=" * 70)
    print()

    print("[1/5] Loading data...")
    traces = load_decision_trace()
    decisions = [t for t in traces if t.get("pattern_detected") and t.get("components") and t.get("timestamp_utc")]
    decisions.sort(key=lambda t: t.get("timestamp_utc", ""))
    print(f"      Decisions: {len(decisions)}")
    print()

    print("[2/5] Building outcomes...")
    candle_cache: dict[str, list[dict]] = {}
    base = Path("replay_data")
    if base.exists():
        for sym_dir in base.iterdir():
            if not sym_dir.is_dir():
                continue
            tf_dir = sym_dir / "5"
            if not tf_dir.exists():
                continue
            candles: list[dict] = []
            for f in sorted(tf_dir.glob("*.jsonl")):
                with open(f) as fh:
                    for line in fh:
                        if line.strip():
                            try:
                                candles.append(json.loads(line.strip()))
                            except json.JSONDecodeError:
                                pass
            if candles:
                candle_cache[sym_dir.name] = candles

    records: list[EdgeAttributionRecord] = []
    for t in decisions:
        symbol = t.get("symbol", "")
        candles = candle_cache.get(symbol) or candle_cache.get(symbol + "_SB") or candle_cache.get(symbol.replace("_SB", "")) or []
        if not candles:
            continue
        cf = simulate_blocked_decision(t, candles)
        if cf.simulation_confidence in (SimulationConfidence.HIGH, SimulationConfidence.MEDIUM):
            records.append(build_attribution_record(t, cf.hypothetical_r))

    # Sort chronologically
    records.sort(key=lambda r: r.timestamp_utc)
    print(f"      Records: {len(records)}")
    print()

    print("[3/5] Generating candidates...")
    gen_result = generate_candidates(records)
    candidates = gen_result.accepted
    print(f"      Candidates to validate: {len(candidates)}")
    print()

    print("[4/5] Running walk-forward validation...")
    report = validate_candidates(candidates, records, n_splits=5)
    print()

    # Display
    print("─" * 70)
    print(f"VALIDATION SUMMARY")
    print("─" * 70)
    print(f"  Candidates validated: {report.candidates_validated}")
    print(f"  Passed: {report.candidates_passed}")
    print(f"  Failed: {report.candidates_failed}")
    print()

    if report.survivors:
        print("─" * 70)
        print("SURVIVORS (passed all criteria)")
        print("─" * 70)
        print(f"  {'Rank':>4} {'Candidate':<40} {'Pos':>5} {'TotR':>7} {'Trades':>6} {'AvgEV':>7} {'WR':>5} {'Conf':>6}")
        print(f"  {'─'*4} {'─'*40} {'─'*5} {'─'*7} {'─'*6} {'─'*7} {'─'*5} {'─'*6}")
        for i, s in enumerate(report.survivors[:20], 1):
            conds = ", ".join(f"{k}={v}" for k, v in s.conditions.items())[:38]
            print(f"  {i:>4} {conds:<40} {s.splits_positive}/{s.splits_total:>2} {s.total_r:>+6.1f} {s.total_trades:>6} {s.avg_ev:>+6.3f} {s.avg_win_rate:>4.0%} {s.confidence:>6}")
        print()

        # Detail for top 3
        for s in report.survivors[:3]:
            print(f"  {s.candidate_id}:")
            print(f"    Conditions: {s.conditions}")
            for sp in s.splits:
                status = "+" if sp.total_r > 0 else "-"
                print(f"      Split {sp.split}: {status} trades={sp.trades_taken} WR={sp.win_rate:.0%} R={sp.total_r:+.1f}")
            print()
    else:
        print("  No candidates survived walk-forward validation.")
        print()

    if report.failures:
        print("─" * 70)
        print(f"TOP FAILURES (closest to passing)")
        print("─" * 70)
        top_fails = sorted(report.failures, key=lambda f: f.total_r, reverse=True)[:10]
        for f in top_fails:
            conds = ", ".join(f"{k}={v}" for k, v in f.conditions.items())[:35]
            print(f"  {conds:<37} {f.splits_positive}/{f.splits_total} pos  R={f.total_r:+.1f}  Fail: {', '.join(f.fail_reasons[:2])}")
        print()

    print("─" * 70)
    print(f"CONCLUSION: {report.conclusion}")
    print(f"Confidence: {report.confidence}")
    print("─" * 70)
    print()

    print("[5/5] Generating report...")
    report_path = generate_report(
        experiment_name="candidate_validation",
        question_id="EDGE_VALIDATION",
        question_text="Which edge candidates survive walk-forward validation?",
        dataset_sources=["logs/decision_trace/", "replay_data/"],
        sample_count=len(records),
        metrics=report.to_dict(),
        conclusion=report.conclusion,
        confidence=report.confidence,
    )
    print(f"      Report: {report_path}")
    print("\n✅ Candidate Walk-Forward Validation complete.")


if __name__ == "__main__":
    main()
