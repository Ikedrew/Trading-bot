"""READ-ONLY live proof: repaired edge-candidate path on canonical S3 evidence.

Runs the production edge evidence loader, edge analysis, and candidate
generation against real canonical S3 data (RESEARCH_AWS_PROFILE). The
lifecycle bridge is exercised inside a SANDBOXED cwd so no real research
state (logs/research_lifecycle) is mutated. Writes nothing to production.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")


def main() -> None:
    from research_engine.edge_attribution.evidence import load_edge_evidence
    from research_engine.edge_attribution.analyser import run_edge_analysis
    from research_engine.edge_candidates.generator import generate_candidates

    print("=" * 72)
    print("EDGE INTEGRATION LIVE PROOF (read-only canonical S3)")
    print("=" * 72)

    evidence = load_edge_evidence()
    acc = evidence.accounting
    print("\n[canonical evidence]")
    for k in ("mode", "datasets", "join_key", "traces_total", "shadow_outcomes_total",
              "trade_truth_total", "decisions_eligible", "canonicals_with_outcome",
              "attribution_records", "decisions_without_outcome",
              "outcome_source_shadow_counterfactual", "outcome_source_trade_truth_realised",
              "shadow_horizon_alternative_skipped", "shadow_ambiguous_excluded"):
        if k in acc:
            print(f"  {k}: {acc[k]}")

    result = run_edge_analysis(evidence.records)
    print("\n[edge analysis]")
    print("  conclusion:", result.conclusion)
    print("  confidence:", result.confidence)
    print("  edge candidates found (discovery-level):", len(result.edge_candidates))
    print("  warnings:", result.warnings)

    gen = generate_candidates(evidence.records)
    print("\n[edge candidate generation]")
    print("  combinations tested:", gen.combinations_tested)
    print("  candidates generated:", gen.candidates_generated)
    print("  rejected as weak:", gen.candidates_rejected)
    print("  accepted (discovery gates: n>=30, EV>0, total_r>0):", gen.candidates_accepted)
    print("  conclusion:", gen.conclusion)

    if gen.accepted:
        print("\n  accepted edges (findings proposed for investigation):")
        for c in gen.accepted[:5]:
            print(f"    {c.candidate_id}  n={c.sample_size} EV={c.expectancy:+.3f} "
                  f"PF={c.profit_factor:.2f} score={c.confidence_score:.0f} "
                  f"overfit={c.overfit_risk}")

    # ── lifecycle bridge proof in a SANDBOX (no real state mutation) ─────
    real_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as sandbox:
        os.chdir(sandbox)
        try:
            from research_engine.edge_candidates.lifecycle_bridge import (
                submit_edge_candidates_to_lifecycle,
            )
            from research_engine.lifecycle.orchestrator import ResearchOrchestrator

            orch = ResearchOrchestrator()
            sub = submit_edge_candidates_to_lifecycle(gen, orchestrator=orch)
            print("\n[lifecycle bridge - SANDBOXED cwd, real research state untouched]")
            print("  ", sub.to_dict()["totals"])
            for r in sub.registered:
                print(f"    registered: {r['edge_id']} -> {r['hypothesis_id']} ({r['category']})")

            # idempotency on real evidence: rerun in the same sandbox
            sub2 = submit_edge_candidates_to_lifecycle(gen, orchestrator=orch)
            print("  rerun totals:", sub2.to_dict()["totals"])
            print("  hypotheses after rerun:", len(orch.registry.all()))
        finally:
            os.chdir(real_cwd)

    print("\nPROOF COMPLETE (read-only)")


if __name__ == "__main__":
    main()
