"""
CLI runner for Candidate Walk-Forward Validation.

Usage:
    python -m research_engine.edge_candidates.run_candidate_validation
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
from research_engine.edge_candidates.validation import validate_candidates
from research_engine.console import configure_console, safe_print
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OFFLINE_REPLAY_FLAG = "--offline-replay"


def main() -> None:
    # Encoding safety: decorative output must never crash on a restrictive
    # Windows console. Research exceptions are NOT affected.
    configure_console(sys.stdout, sys.stderr)
    safe_print("=" * 70)
    safe_print("CANDIDATE WALK-FORWARD VALIDATION")
    safe_print("    Test which edge candidates survive unseen data")
    safe_print("=" * 70)
    safe_print()

    if OFFLINE_REPLAY_FLAG in sys.argv:
        # EXPLICIT offline fixture mode — local replay candles + counterfactual
        # simulator. Never selected by a production run; never a fallback.
        safe_print("[1/5] OFFLINE REPLAY MODE: loading decision traces + local fixtures...")
        evidence = load_edge_evidence_offline_replay()
    else:
        # PRODUCTION mode: canonical S3 evidence only.
        safe_print("[1/5] Loading canonical evidence (S3: decision_trace + shadow_runtime_v1 + trade_truth)...")
        evidence = load_edge_evidence()
    records = evidence.records
    acc = evidence.accounting

    # Sort chronologically
    records.sort(key=lambda r: r.timestamp_utc)
    safe_print(f"      Accounting: {acc}")
    safe_print(f"      Records: {len(records)}")
    safe_print()

    safe_print("[3/5] Generating candidates...")
    gen_result = generate_candidates(records)
    candidates = gen_result.accepted
    safe_print(f"      Candidates to validate: {len(candidates)}")
    safe_print()

    safe_print("[4/5] Running walk-forward validation...")
    report = validate_candidates(candidates, records, n_splits=5)
    safe_print()

    # Display
    safe_print("-" * 70)
    safe_print(f"VALIDATION SUMMARY")
    safe_print("-" * 70)
    safe_print(f"  Candidates validated: {report.candidates_validated}")
    safe_print(f"  Passed: {report.candidates_passed}")
    safe_print(f"  Failed: {report.candidates_failed}")
    safe_print()

    if report.survivors:
        safe_print("-" * 70)
        safe_print("SURVIVORS (passed all criteria)")
        safe_print("-" * 70)
        safe_print(f"  {'Rank':>4} {'Candidate':<40} {'Pos':>5} {'TotR':>7} {'Trades':>6} {'AvgEV':>7} {'WR':>5} {'Conf':>6}")
        safe_print(f"  {'-'*4} {'-'*40} {'-'*5} {'-'*7} {'-'*6} {'-'*7} {'-'*5} {'-'*6}")
        for i, s in enumerate(report.survivors[:20], 1):
            conds = ", ".join(f"{k}={v}" for k, v in s.conditions.items())[:38]
            safe_print(f"  {i:>4} {conds:<40} {s.splits_positive}/{s.splits_total:>2} {s.total_r:>+6.1f} {s.total_trades:>6} {s.avg_ev:>+6.3f} {s.avg_win_rate:>4.0%} {s.confidence:>6}")
        safe_print()

        # Detail for top 3
        for s in report.survivors[:3]:
            safe_print(f"  {s.candidate_id}:")
            safe_print(f"    Conditions: {s.conditions}")
            for sp in s.splits:
                status = "+" if sp.total_r > 0 else "-"
                safe_print(f"      Split {sp.split}: {status} trades={sp.trades_taken} WR={sp.win_rate:.0%} R={sp.total_r:+.1f}")
            safe_print()
    else:
        safe_print("  No candidates survived walk-forward validation.")
        safe_print()

    if report.failures:
        safe_print("-" * 70)
        safe_print(f"TOP FAILURES (closest to passing)")
        safe_print("-" * 70)
        top_fails = sorted(report.failures, key=lambda f: f.total_r, reverse=True)[:10]
        for f in top_fails:
            conds = ", ".join(f"{k}={v}" for k, v in f.conditions.items())[:35]
            safe_print(f"  {conds:<37} {f.splits_positive}/{f.splits_total} pos  R={f.total_r:+.1f}  Fail: {', '.join(f.fail_reasons[:2])}")
        safe_print()

    safe_print("-" * 70)
    safe_print(f"CONCLUSION: {report.conclusion}")
    safe_print(f"Confidence: {report.confidence}")
    safe_print("-" * 70)
    safe_print()

    safe_print("[5/5] Generating report...")
    report_path = generate_report(
        experiment_name="candidate_validation",
        question_id="EDGE_VALIDATION",
        question_text="Which edge candidates survive walk-forward validation?",
        dataset_sources=acc.get("datasets", ["s3:decision_trace", "s3:shadow_runtime_v1(ingested)", "s3:trade_truth"])
        if acc.get("mode") == "production_canonical_s3"
        else ["s3:decision_trace", f"{acc.get('replay_dir', 'replay_data')}/ (explicit offline fixtures)"],
        sample_count=len(records),
        metrics={**report.to_dict(), "evidence_accounting": acc},
        conclusion=report.conclusion,
        confidence=report.confidence,
    )
    safe_print(f"      Report: {report_path}")
    safe_print("\n[OK] Candidate Walk-Forward Validation complete.")


if __name__ == "__main__":
    main()
