"""READ-ONLY live capability proof — runs the actual Research Engine surfaces.

Run: $env:RESEARCH_AWS_PROFILE="trading-bot-new"; python tools\run_capability_proof.py
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def section(name):
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", flush=True)

def guarded(name, fn):
    section(name)
    try:
        fn()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)

# ── 1. Q16 shadow validation end-to-end (main entry point) ────────────────
def q16():
    from research_engine.main import main
    main()

# ── 2. Research cycle DETECT_ONLY (finding triggers + idempotency state) ──
def cycle():
    from research_engine.lifecycle.research_cycle_runner import (
        ResearchCycleConfig, ResearchCycleRunner, CycleState,
    )
    from research_engine.lifecycle.finding_trigger import ExecutionMode
    cfg = ResearchCycleConfig(mode=ExecutionMode.DETECT_ONLY,
                              min_cycle_interval_seconds=0.0)
    r = ResearchCycleRunner(cfg).run_cycle()
    print(r.to_dict())

# ── 3. Representative registry experiments ────────────────────────────────
def experiments():
    from research_engine.experiments.research_runner import run_all
    results = run_all()
    for qid, info in sorted(results.items()):
        print(f"  {qid}: {info['status']} (n={info.get('sample', '?')})")

# ── 4. Candidate surfaces ─────────────────────────────────────────────────
def candidates():
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry
    reg = CandidateRegistry()
    all_c = reg.list_all() if hasattr(reg, "list_all") else []
    print(f"candidates on disk: {len(all_c)}")
    from collections import Counter
    st = Counter(c.status for c in all_c)
    print("status distribution:", dict(st))
    # Auto-evaluator: prove it reports insufficient rather than promoting
    from research_engine.lifecycle.candidate_auto_evaluator import auto_evaluate_candidates
    res = auto_evaluate_candidates()
    print("auto-evaluation:", res.to_dict())

# ── 5. Disposition/leakage guards live ────────────────────────────────────
def guards():
    from research_engine.dataset_disposition import coverage_report, assert_full_coverage
    assert_full_coverage()
    cov = coverage_report()
    print(f"coverage: {cov['covered']}/23, uncovered={cov['uncovered']}")
    from research_engine.dataset_disposition import assert_not_outcome_as_decision_feature
    try:
        assert_not_outcome_as_decision_feature(feature="r_multiple_realised",
                                               source_dataset="trade_truth")
        print("LEAKAGE GUARD FAILED TO FIRE")
    except ValueError as e:
        print(f"leakage guard fires correctly: {e}")

guarded("1. Q16 SHADOW VALIDATION (main entry)", q16)
guarded("2. RESEARCH CYCLE (DETECT_ONLY)", cycle)
guarded("3. REGISTRY EXPERIMENTS (run_all)", experiments)
guarded("4. CANDIDATE SURFACES", candidates)
guarded("5. GUARDS", guards)
print("\nCAPABILITY PROOF COMPLETE")
