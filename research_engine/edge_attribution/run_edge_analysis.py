"""
CLI runner for Edge Attribution Analysis.

Usage:
    python -m research_engine.edge_attribution.run_edge_analysis
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
from research_engine.edge_attribution.analyser import run_edge_analysis
from research_engine.console import configure_console, safe_print
from research_engine.reports.generator import generate_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OFFLINE_REPLAY_FLAG = "--offline-replay"


def main() -> None:
    # Encoding safety: decorative output must never crash on a restrictive
    # Windows console. Research exceptions are NOT affected.
    configure_console(sys.stdout, sys.stderr)
    safe_print("=" * 70)
    safe_print("EDGE ATTRIBUTION ENGINE")
    safe_print("    Discover conditions that produce positive expectancy")
    safe_print("=" * 70)
    safe_print()

    if OFFLINE_REPLAY_FLAG in sys.argv:
        # EXPLICIT offline fixture mode — local replay candles + counterfactual
        # simulator. Never selected by a production run; never a fallback.
        safe_print("[1/4] OFFLINE REPLAY MODE: loading decision traces + local fixtures...")
        evidence = load_edge_evidence_offline_replay()
    else:
        # PRODUCTION mode: canonical S3 evidence only (decision_trace +
        # shadow_runtime_v1 counterfactuals + trade_truth realised outcomes).
        safe_print("[1/4] Loading canonical evidence (S3: decision_trace + shadow_runtime_v1 + trade_truth)...")
        evidence = load_edge_evidence()
    records = evidence.records
    acc = evidence.accounting
    safe_print(f"      Accounting: {acc}")
    safe_print(f"      Attribution records: {len(records)}")
    safe_print()

    safe_print("[3/4] Running edge analysis...")
    result = run_edge_analysis(records)
    safe_print()

    # Display
    safe_print("-" * 70)
    safe_print("FEATURE IMPORTANCE RANKING")
    safe_print("-" * 70)
    safe_print(f"  {'Feature':<22} {'Impact':<8} {'Spread':>7} {'Best':>20} {'Worst':>20} {'Reliable':>8}")
    safe_print(f"  {'-'*22} {'-'*8} {'-'*7} {'-'*20} {'-'*20} {'-'*8}")
    for fi in result.importance[:10]:
        safe_print(f"  {fi.feature:<22} {fi.impact:<8} {fi.ev_spread:>+6.3f} {fi.best_value+'='+f'{fi.best_ev:+.3f}':>20} {fi.worst_value+'='+f'{fi.worst_ev:+.3f}':>20} {'YES' if fi.reliable else 'no':>8}")
    safe_print()

    # Top single features
    for feature in ["pattern", "regime", "session", "htf_alignment_bin"]:
        conditions = result.single_features.get(feature, [])
        if conditions:
            safe_print(f"  {feature.upper()}:")
            for c in conditions[:5]:
                s = c.stats
                marker = "*" if s.get("ev", 0) > 0.05 and s.get("n", 0) >= 20 else " "
                safe_print(f"   {marker} {c.value:<20} n={s['n']:>4} WR={s['wr']:.0%} EV={s['ev']:+.3f}R PF={s['pf']:.1f} [{s['confidence']}]")
            safe_print()

    # Edge candidates
    safe_print("-" * 70)
    safe_print(f"EDGE CANDIDATES ({len(result.edge_candidates)} found)")
    safe_print("-" * 70)
    for ec in result.edge_candidates[:10]:
        feat = ec.get("feature", ec.get("features", "?"))
        val = ec.get("value", "?")
        safe_print(f"  {feat}={val}  EV={ec['ev']:+.3f}R  WR={ec['wr']:.0%}  n={ec['n']}  [{ec['confidence']}]")
    safe_print()

    # Pattern dependency
    safe_print("-" * 70)
    safe_print("PATTERN DEPENDENCY")
    safe_print("-" * 70)
    for pd in result.pattern_dependency[:7]:
        o = pd["overall"]
        surv = "[OK] YES" if pd["edge_survives_removal"] else "[X] NO"
        safe_print(f"  {pd['pattern']:<25} EV={o['ev']:+.3f}R n={o['n']} [{o['confidence']}]")
        if pd["best_condition"]:
            safe_print(f"    Best: {pd['best_condition']} (EV={pd['best_condition_ev']:+.3f})")
        if pd["worst_condition"]:
            safe_print(f"    Worst: {pd['worst_condition']} (EV={pd['worst_condition_ev']:+.3f})")
        safe_print(f"    Edge survives removal of best condition: {surv}")
    safe_print()

    # Warnings
    if result.warnings:
        safe_print("-" * 70)
        safe_print("WARNINGS")
        safe_print("-" * 70)
        for w in result.warnings:
            safe_print(f"  [WARN] {w}")
        safe_print()

    safe_print("-" * 70)
    safe_print(f"CONCLUSION: {result.conclusion}")
    safe_print(f"Confidence: {result.confidence}")
    safe_print("-" * 70)
    safe_print()

    safe_print("[4/4] Generating report...")
    report_path = generate_report(
        experiment_name="edge_attribution",
        question_id="EDGE",
        question_text="Under what conditions does an opportunity have positive expectancy?",
        dataset_sources=acc.get("datasets", ["s3:decision_trace", "s3:shadow_runtime_v1(ingested)", "s3:trade_truth"])
        if acc.get("mode") == "production_canonical_s3"
        else ["s3:decision_trace", f"{acc.get('replay_dir', 'replay_data')}/ (explicit offline fixtures)"],
        sample_count=len(records),
        metrics={**result.to_dict(), "evidence_accounting": acc},
        conclusion=result.conclusion,
        confidence=result.confidence,
    )
    safe_print(f"      Report: {report_path}")
    safe_print("\n[OK] Edge Attribution Analysis complete.")


if __name__ == "__main__":
    main()
