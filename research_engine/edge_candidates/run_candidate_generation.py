"""
CLI runner for Edge Candidate Generation.

Usage:
    python -m research_engine.edge_candidates.run_candidate_generation
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
from research_engine.counterfactual.simulator import simulate_blocked_decision
from research_engine.counterfactual.schema import SimulationConfidence
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    print("=" * 70)
    print("EDGE CANDIDATE GENERATOR")
    print("    Convert attribution findings into validation-ready hypotheses")
    print("=" * 70)
    print()

    print("[1/4] Loading data...")
    traces = load_decision_trace()
    decisions = [t for t in traces if t.get("pattern_detected") and t.get("components")]
    print(f"      Decisions: {len(decisions)}")
    print()

    print("[2/4] Building outcomes...")
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

    print(f"      Records with outcomes: {len(records)}")
    print()

    print("[3/4] Generating candidates...")
    result = generate_candidates(records)
    print()

    # Display
    print("─" * 70)
    print(f"GENERATION SUMMARY")
    print("─" * 70)
    print(f"  Combinations tested: {result.combinations_tested}")
    print(f"  Candidates generated: {result.candidates_generated}")
    print(f"  Accepted: {result.candidates_accepted}")
    print(f"  Rejected: {result.candidates_rejected}")
    print()

    if result.accepted:
        print("─" * 70)
        print("ACCEPTED CANDIDATES (ranked by confidence)")
        print("─" * 70)
        print(f"  {'Rank':>4} {'Score':>5} {'Overfit':>7} {'EV':>7} {'WR':>5} {'n':>4} {'PF':>5} {'Conditions'}")
        print(f"  {'─'*4} {'─'*5} {'─'*7} {'─'*7} {'─'*5} {'─'*4} {'─'*5} {'─'*40}")
        for i, c in enumerate(result.accepted[:15], 1):
            conds = ", ".join(f"{k}={v}" for k, v in c.conditions.items())
            flags = ""
            if c.single_pattern_dependent:
                flags += " [PAT]"
            if c.low_sample:
                flags += " [LOW_N]"
            print(f"  {i:>4} {c.confidence_score:>4.0f} {c.overfit_risk:>7} {c.expectancy:>+6.3f} {c.win_rate:>4.0%} {c.sample_size:>4} {c.profit_factor:>4.1f} {conds}{flags}")

        print()
        print("─" * 70)
        print("TOP 5 VALIDATION SPECS")
        print("─" * 70)
        for c in result.accepted[:5]:
            spec = c.to_validation_spec()
            print(f"  {spec['candidate_id']}:")
            print(f"    conditions: {spec['conditions']}")
            print(f"    min_training_samples: {spec['training_requirements']['min_samples']}")
            print()

    print("─" * 70)
    print(f"CONCLUSION: {result.conclusion}")
    print(f"Confidence: {result.confidence}")
    print("─" * 70)
    print()

    print("[4/4] Generating report...")
    report_path = generate_report(
        experiment_name="edge_candidate_generation",
        question_id="EDGE_CANDIDATES",
        question_text="Which condition combinations form viable edge hypotheses?",
        dataset_sources=["logs/decision_trace/", "replay_data/"],
        sample_count=len(records),
        metrics=result.to_dict(),
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    print(f"      Report: {report_path}")
    print("\n✅ Edge Candidate Generation complete.")


if __name__ == "__main__":
    main()
