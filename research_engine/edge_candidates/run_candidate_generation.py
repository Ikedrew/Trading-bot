"""
CLI runner for Edge Candidate Generation.

Usage:
    python -m research_engine.edge_candidates.run_candidate_generation
"""
from __future__ import annotations
import logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.edge_attribution.evidence import (
    load_edge_evidence,
    load_edge_evidence_offline_replay,
)
from research_engine.edge_candidates.generator import generate_candidates
from research_engine.console import configure_console, safe_print
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OFFLINE_REPLAY_FLAG = "--offline-replay"


def main() -> None:
    # Encoding safety: decorative output must never crash on a restrictive
    # Windows console. Research exceptions are NOT affected.
    configure_console(sys.stdout, sys.stderr)
    safe_print("=" * 70)
    safe_print("EDGE CANDIDATE GENERATOR")
    safe_print("    Convert attribution findings into validation-ready hypotheses")
    safe_print("=" * 70)
    safe_print()

    if OFFLINE_REPLAY_FLAG in sys.argv:
        # EXPLICIT offline fixture mode — local replay candles + counterfactual
        # simulator. Never selected by a production run; never a fallback.
        safe_print("[1/4] OFFLINE REPLAY MODE: loading decision traces + local fixtures...")
        evidence = load_edge_evidence_offline_replay()
    else:
        # PRODUCTION mode: canonical S3 evidence only.
        safe_print("[1/4] Loading canonical evidence (S3: decision_trace + shadow_runtime_v1 + trade_truth)...")
        evidence = load_edge_evidence()
    records = evidence.records
    acc = evidence.accounting
    safe_print(f"      Accounting: {acc}")
    safe_print(f"      Records with outcomes: {len(records)}")
    safe_print()

    safe_print("[3/4] Generating candidates...")
    result = generate_candidates(records)
    safe_print()

    # Display
    safe_print("-" * 70)
    safe_print(f"GENERATION SUMMARY")
    safe_print("-" * 70)
    safe_print(f"  Combinations tested: {result.combinations_tested}")
    safe_print(f"  Candidates generated: {result.candidates_generated}")
    safe_print(f"  Accepted: {result.candidates_accepted}")
    safe_print(f"  Rejected: {result.candidates_rejected}")
    safe_print()

    if result.accepted:
        safe_print("-" * 70)
        safe_print("ACCEPTED CANDIDATES (ranked by confidence)")
        safe_print("-" * 70)
        safe_print(f"  {'Rank':>4} {'Score':>5} {'Overfit':>7} {'EV':>7} {'WR':>5} {'n':>4} {'PF':>5} {'Conditions'}")
        safe_print(f"  {'-'*4} {'-'*5} {'-'*7} {'-'*7} {'-'*5} {'-'*4} {'-'*5} {'-'*40}")
        for i, c in enumerate(result.accepted[:15], 1):
            conds = ", ".join(f"{k}={v}" for k, v in c.conditions.items())
            flags = ""
            if c.single_pattern_dependent:
                flags += " [PAT]"
            if c.low_sample:
                flags += " [LOW_N]"
            safe_print(f"  {i:>4} {c.confidence_score:>4.0f} {c.overfit_risk:>7} {c.expectancy:>+6.3f} {c.win_rate:>4.0%} {c.sample_size:>4} {c.profit_factor:>4.1f} {conds}{flags}")

        safe_print()
        safe_print("-" * 70)
        safe_print("TOP 5 VALIDATION SPECS")
        safe_print("-" * 70)
        for c in result.accepted[:5]:
            spec = c.to_validation_spec()
            safe_print(f"  {spec['candidate_id']}:")
            safe_print(f"    conditions: {spec['conditions']}")
            safe_print(f"    min_training_samples: {spec['training_requirements']['min_samples']}")
            safe_print()

    safe_print("-" * 70)
    safe_print(f"CONCLUSION: {result.conclusion}")
    safe_print(f"Confidence: {result.confidence}")
    safe_print("-" * 70)
    safe_print()

    safe_print("[4/4] Generating report...")
    report_path = generate_report(
        experiment_name="edge_candidate_generation",
        question_id="EDGE_CANDIDATES",
        question_text="Which condition combinations form viable edge hypotheses?",
        dataset_sources=acc.get("datasets", ["s3:decision_trace", "s3:shadow_runtime_v1(ingested)", "s3:trade_truth"])
        if acc.get("mode") == "production_canonical_s3"
        else ["s3:decision_trace", f"{acc.get('replay_dir', 'replay_data')}/ (explicit offline fixtures)"],
        sample_count=len(records),
        metrics={**result.to_dict(), "evidence_accounting": acc},
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    safe_print(f"      Report: {report_path}")
    safe_print("\n[OK] Edge Candidate Generation complete.")


if __name__ == "__main__":
    main()
