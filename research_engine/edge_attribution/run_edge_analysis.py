"""
CLI runner for Edge Attribution Analysis.

Usage:
    python -m research_engine.edge_attribution.run_edge_analysis
"""
from __future__ import annotations
import json, logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.data_access.loaders import load_decision_trace
from research_engine.edge_attribution.models import build_attribution_record, EdgeAttributionRecord
from research_engine.edge_attribution.analyser import run_edge_analysis
from research_engine.counterfactual.simulator import simulate_blocked_decision
from research_engine.counterfactual.schema import SimulationConfidence
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    print("=" * 70)
    print("EDGE ATTRIBUTION ENGINE")
    print("    Discover conditions that produce positive expectancy")
    print("=" * 70)
    print()

    print("[1/4] Loading decision traces...")
    traces = load_decision_trace()
    decisions = [t for t in traces if t.get("pattern_detected") and t.get("components")]
    print(f"      Decisions with patterns: {len(decisions)}")
    print()

    print("[2/4] Building outcomes via counterfactual simulation...")
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
        eid = t.get("entity_id", "")
        symbol = t.get("symbol", "")
        candles = candle_cache.get(symbol) or candle_cache.get(symbol + "_SB") or candle_cache.get(symbol.replace("_SB", "")) or []
        if not candles:
            continue
        cf = simulate_blocked_decision(t, candles)
        if cf.simulation_confidence in (SimulationConfidence.HIGH, SimulationConfidence.MEDIUM):
            records.append(build_attribution_record(t, cf.hypothetical_r))

    print(f"      Attribution records: {len(records)}")
    print()

    print("[3/4] Running edge analysis...")
    result = run_edge_analysis(records)
    print()

    # Display
    print("─" * 70)
    print("FEATURE IMPORTANCE RANKING")
    print("─" * 70)
    print(f"  {'Feature':<22} {'Impact':<8} {'Spread':>7} {'Best':>20} {'Worst':>20} {'Reliable':>8}")
    print(f"  {'─'*22} {'─'*8} {'─'*7} {'─'*20} {'─'*20} {'─'*8}")
    for fi in result.importance[:10]:
        print(f"  {fi.feature:<22} {fi.impact:<8} {fi.ev_spread:>+6.3f} {fi.best_value+'='+f'{fi.best_ev:+.3f}':>20} {fi.worst_value+'='+f'{fi.worst_ev:+.3f}':>20} {'YES' if fi.reliable else 'no':>8}")
    print()

    # Top single features
    for feature in ["pattern", "regime", "session", "htf_alignment_bin"]:
        conditions = result.single_features.get(feature, [])
        if conditions:
            print(f"  {feature.upper()}:")
            for c in conditions[:5]:
                s = c.stats
                marker = "★" if s.get("ev", 0) > 0.05 and s.get("n", 0) >= 20 else " "
                print(f"   {marker} {c.value:<20} n={s['n']:>4} WR={s['wr']:.0%} EV={s['ev']:+.3f}R PF={s['pf']:.1f} [{s['confidence']}]")
            print()

    # Edge candidates
    print("─" * 70)
    print(f"EDGE CANDIDATES ({len(result.edge_candidates)} found)")
    print("─" * 70)
    for ec in result.edge_candidates[:10]:
        feat = ec.get("feature", ec.get("features", "?"))
        val = ec.get("value", "?")
        print(f"  {feat}={val}  EV={ec['ev']:+.3f}R  WR={ec['wr']:.0%}  n={ec['n']}  [{ec['confidence']}]")
    print()

    # Pattern dependency
    print("─" * 70)
    print("PATTERN DEPENDENCY")
    print("─" * 70)
    for pd in result.pattern_dependency[:7]:
        o = pd["overall"]
        surv = "✅ YES" if pd["edge_survives_removal"] else "❌ NO"
        print(f"  {pd['pattern']:<25} EV={o['ev']:+.3f}R n={o['n']} [{o['confidence']}]")
        if pd["best_condition"]:
            print(f"    Best: {pd['best_condition']} (EV={pd['best_condition_ev']:+.3f})")
        if pd["worst_condition"]:
            print(f"    Worst: {pd['worst_condition']} (EV={pd['worst_condition_ev']:+.3f})")
        print(f"    Edge survives removal of best condition: {surv}")
    print()

    # Warnings
    if result.warnings:
        print("─" * 70)
        print("WARNINGS")
        print("─" * 70)
        for w in result.warnings:
            print(f"  ⚠ {w}")
        print()

    print("─" * 70)
    print(f"CONCLUSION: {result.conclusion}")
    print(f"Confidence: {result.confidence}")
    print("─" * 70)
    print()

    print("[4/4] Generating report...")
    report_path = generate_report(
        experiment_name="edge_attribution",
        question_id="EDGE",
        question_text="Under what conditions does an opportunity have positive expectancy?",
        dataset_sources=["logs/decision_trace/", "replay_data/"],
        sample_count=len(records),
        metrics=result.to_dict(),
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    print(f"      Report: {report_path}")
    print("\n✅ Edge Attribution Analysis complete.")


if __name__ == "__main__":
    main()
