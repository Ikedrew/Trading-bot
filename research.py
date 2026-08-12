#!/usr/bin/env python
"""
V10 Research Control Plane CLI.

Usage:
    python research.py status
    python research.py <QUESTION_ID>
    python research.py inspect <QUESTION_ID>
    python research.py angle <ANGLE>
    python research.py all
    python research.py cockpit
    python research.py feedback
    python research.py knowledge
    python research.py proposals
    python research.py continuous
    python research.py continuous status
    python research.py continuous plan
    python research.py bottleneck
    python research.py next
    python research.py optimisation list
    python research.py optimisation create
    python research.py optimisation validate <ID>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root importable
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question
from research_engine.v10.universes.models import Universe, Population, QuestionStatus
from research_engine.v10.runner.primitive_mapping import QUESTION_PARAMETERS, build_full_mapping
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext
from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment

_QUESTIONS_DIR = Path("reports/research/questions")
_RUNS_DIR = Path("reports/research/runs")


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE BUILD (shared, lazy)
# ═══════════════════════════════════════════════════════════════════════════════

_builders_cache: dict | None = None


def _get_builders():
    global _builders_cache
    if _builders_cache is not None:
        return _builders_cache
    from research_engine.v10.universes import (
        ExecutionUniverseBuilder, DecisionUniverseBuilder,
        MarketUniverseBuilder, StrategyUniverseBuilder,
        RiskUniverseBuilder, OutcomeUniverseBuilder,
    )
    builders = {}
    for UClass, utype in [
        (ExecutionUniverseBuilder, Universe.EXECUTION),
        (DecisionUniverseBuilder, Universe.DECISION),
        (MarketUniverseBuilder, Universe.MARKET),
        (StrategyUniverseBuilder, Universe.STRATEGY),
        (RiskUniverseBuilder, Universe.RISK),
    ]:
        b = UClass()
        b.build()
        builders[utype] = b
    # Outcome enrichment
    exe = builders[Universe.EXECUTION]
    enrichment = OutcomeEnrichment(exe)
    enrichment.enrich_all(builders)
    # Outcome universe (wraps completed executions)
    outcome = OutcomeUniverseBuilder(execution_builder=exe)
    outcome.build()
    builders[Universe.OUTCOME] = outcome
    _builders_cache = builders
    return builders


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_status():
    """Show current research status from latest results."""
    print("RESEARCH STATUS")
    print("=" * 50)

    complete, inconclusive, blocked, error, not_run = 0, 0, 0, 0, 0
    for q in QUESTION_BANK:
        latest = _load_latest(q.question_id)
        if latest is None:
            not_run += 1
        elif latest.get("confidence") == "INSUFFICIENT" or latest.get("outcome") == "INCONCLUSIVE":
            inconclusive += 1
        elif latest.get("outcome") == "ANALYSIS_FAILED":
            error += 1
        else:
            complete += 1

    # Check blocked from contracts
    for q in QUESTION_BANK:
        if q.status == QuestionStatus.BLOCKED:
            blocked += 1
            complete -= 1 if complete > 0 else 0

    print(f"  TOTAL:         {len(QUESTION_BANK)}")
    print(f"  COMPLETE:      {complete}")
    print(f"  INCONCLUSIVE:  {inconclusive}")
    print(f"  BLOCKED:       {blocked}")
    print(f"  ERROR:         {error}")
    print(f"  NOT RUN:       {not_run}")

    # Latest run
    runs = sorted(_RUNS_DIR.glob("*.json"), reverse=True) if _RUNS_DIR.exists() else []
    if runs:
        run = json.loads(runs[0].read_text(encoding="utf-8"))
        print(f"\n  Latest run:    {run.get('run_id', '')}")
        print(f"  Timestamp:     {run.get('timestamp', '')}")
        print(f"  Duration:      {run.get('duration_seconds', 0):.1f}s")


def cmd_run_question(qid: str):
    """Run a single question and display result."""
    q = get_question(qid)
    if q is None:
        print(f"ERROR: Question '{qid}' not found in canonical bank.")
        print(f"  Available: {[qq.question_id for qq in QUESTION_BANK[:10]]}...")
        return

    if q.status == QuestionStatus.BLOCKED:
        print(f"QUESTION: {qid} — {q.title}")
        print(f"STATUS: BLOCKED")
        print(f"REASON: Question contract status is BLOCKED")
        return

    print(f"Running {qid}...")
    builders = _get_builders()
    registry = build_default_registry()
    mapping = build_full_mapping(QUESTION_BANK)
    runner = QuestionRunner(registry, mapping)
    ctx = RunContext()

    # Resolve population
    primary_u = q.required_universes[0]
    builder = builders.get(primary_u)
    pop = builder.get_population(q.required_populations[0]) if q.required_populations else builder.records

    start = time.time()
    result = runner.run_question(q, pop, ctx)
    elapsed = time.time() - start

    # Save finding
    if result.success and result.finding:
        from research_engine.v10.control_plane.question_products import QuestionProductManager
        mgr = QuestionProductManager()
        mgr.save_finding(result.finding)

    _display_result(qid, q, result, pop, elapsed)


def cmd_run_angle(angle: str):
    """Run all questions for a given angle/group."""
    angle_upper = angle.upper()
    # Map angle names to universe filters
    angle_map = {
        "EXECUTION": [Universe.EXECUTION],
        "DECISION": [Universe.DECISION],
        "MARKET": [Universe.MARKET],
        "STRATEGY": [Universe.STRATEGY],
    }

    target_universes = angle_map.get(angle_upper)
    if target_universes is None:
        # Try cross-angle
        questions = [q for q in QUESTION_BANK if angle_upper in q.question_id]
        if not questions:
            print(f"Unknown angle: {angle}")
            print(f"  Available: execution, decision, market, strategy")
            return
    else:
        questions = [
            q for q in QUESTION_BANK
            if any(u in q.required_universes for u in target_universes)
            and q.status != QuestionStatus.BLOCKED
        ]

    print(f"RUNNING ANGLE: {angle_upper} ({len(questions)} questions)")
    print("=" * 50)

    builders = _get_builders()
    registry = build_default_registry()
    mapping = build_full_mapping(QUESTION_BANK)
    runner = QuestionRunner(registry, mapping)
    ctx = RunContext()
    mgr = None

    complete, inconclusive = 0, 0
    for q in questions:
        primary_u = q.required_universes[0]
        builder = builders.get(primary_u)
        if not builder:
            continue
        pop = builder.get_population(q.required_populations[0]) if q.required_populations else builder.records
        result = runner.run_question(q, pop, ctx)

        if result.success and result.finding:
            if mgr is None:
                from research_engine.v10.control_plane.question_products import QuestionProductManager
                mgr = QuestionProductManager()
            mgr.save_finding(result.finding)

            status = "COMPLETE" if result.finding.confidence != "INSUFFICIENT" else "INCONCLUSIVE"
            if status == "COMPLETE":
                complete += 1
            else:
                inconclusive += 1
            icon = "+" if status == "COMPLETE" else "~"
            print(f"  {icon} {q.question_id:<10} {status:<13} {result.finding.outcome}")
        else:
            print(f"  ! {q.question_id:<10} ERROR       {result.error[:40]}")

    print(f"\n  Complete: {complete}, Inconclusive: {inconclusive}")


def cmd_run_all():
    """Run the full 45-question bank."""
    print("RUNNING FULL 45-QUESTION BANK")
    print("=" * 50)
    from research_engine.v10.runner.orchestrator import ResearchExecutionOrchestrator
    orch = ResearchExecutionOrchestrator()
    manifest, outcomes = orch.execute_all()

    complete = sum(1 for o in outcomes if o.status == "COMPLETE")
    inconclusive = sum(1 for o in outcomes if o.status == "INCONCLUSIVE")
    blocked = sum(1 for o in outcomes if o.status == "BLOCKED")
    error = sum(1 for o in outcomes if o.status == "ERROR")

    print(f"\n  Run ID:        {manifest.run_id}")
    print(f"  Duration:      {manifest.duration_seconds:.1f}s")
    print(f"  COMPLETE:      {complete}")
    print(f"  INCONCLUSIVE:  {inconclusive}")
    print(f"  BLOCKED:       {blocked}")
    print(f"  ERROR:         {error}")

    # Refresh Cockpit automatically after successful run
    print()
    _refresh_cockpit_after_run()


def cmd_inspect(qid: str):
    """Inspect the latest result for a question."""
    q = get_question(qid)
    if q is None:
        print(f"ERROR: Question '{qid}' not found.")
        return

    latest = _load_latest(qid)
    if latest is None:
        print(f"QUESTION: {qid} — {q.title}")
        print(f"STATUS: NOT RUN (no latest.json found)")
        return

    params = QUESTION_PARAMETERS.get(qid, {})
    angles = [u.value for u in q.required_universes]

    print(f"QUESTION")
    print(f"  {qid} — {q.title}")
    print(f"\nRESEARCH INTENT")
    print(f"  {q.research_intent[:200]}")
    print(f"\nANGLES: {', '.join(angles)}")
    print(f"ANALYSIS TYPE: {q.analysis_type.value}")
    print(f"POPULATIONS: {[p.value for p in q.required_populations]}")

    if params:
        print(f"\nRESOLVED PARAMETERS")
        for k, v in params.items():
            print(f"  {k}: {v}")
    else:
        print(f"\nPARAMETERS: (primitive defaults)")

    print(f"\nSTATUS: {_classify_finding(latest)}")
    print(f"OUTCOME: {latest.get('outcome', '')}")
    print(f"CONFIDENCE: {latest.get('confidence', '')}")

    sizes = latest.get("sample_sizes", {})
    pop_size = sizes.get("population", sizes.get("total", "?"))
    analytical = sizes.get("analytical_sample", pop_size)
    minimum = sizes.get("minimum_required", "?")
    reduction = sizes.get("sample_reduction_reason", "")

    print(f"POPULATION: {pop_size}")
    print(f"ANALYTICAL SAMPLE: {analytical}")
    print(f"MINIMUM REQUIRED: {minimum}")
    if reduction and analytical != pop_size:
        print(f"REASON FOR REDUCTION: {reduction}")

    metrics = latest.get("primary_metrics", {})
    if metrics:
        print(f"\nMETRICS")
        for k, v in list(metrics.items())[:10]:
            print(f"  {k}: {v}")

    conclusion = latest.get("conclusion", "")
    if conclusion and conclusion != "No conclusion":
        print(f"\nINTERPRETATION")
        print(f"  {conclusion[:300]}")

    limitations = latest.get("limitations", [])
    if limitations:
        print(f"\nLIMITATIONS")
        for lim in limitations[:5]:
            print(f"  - {lim}")

    # Bot implication
    print(f"\nBOT IMPLICATION")
    print(f"  {q.decision_enabled}")

    print(f"\nRUN: {latest.get('run_id', '?')}")
    print(f"TIMESTAMP: {latest.get('run_timestamp', '?')}")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _load_latest(qid: str) -> dict | None:
    path = _QUESTIONS_DIR / qid / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_finding(finding: dict) -> str:
    if finding.get("confidence") == "INSUFFICIENT":
        return "INCONCLUSIVE"
    if finding.get("outcome") == "INCONCLUSIVE":
        return "INCONCLUSIVE"
    if finding.get("outcome") == "ANALYSIS_FAILED":
        return "ERROR"
    return "COMPLETE"


def _display_result(qid, q, result, pop, elapsed):
    params = QUESTION_PARAMETERS.get(qid, {})

    print(f"\nQUESTION")
    print(f"  {qid} — {q.title}")
    print(f"\nSTATUS: ", end="")
    if not result.success:
        print(f"ERROR — {result.error}")
        return

    f = result.finding
    status = "COMPLETE" if f.confidence != "INSUFFICIENT" else "INCONCLUSIVE"
    print(status)

    # Sample breakdown
    sizes = f.sample_sizes
    pop_size = sizes.get("population", len(pop))
    analytical = sizes.get("analytical_sample", pop_size)
    minimum = sizes.get("minimum_required", "?")
    reduction = sizes.get("sample_reduction_reason", "")

    print(f"\nPOPULATION: {pop_size}")
    print(f"ANALYTICAL SAMPLE: {analytical}")
    print(f"MINIMUM REQUIRED: {minimum}")
    if analytical != pop_size:
        print(f"REASON FOR REDUCTION: {reduction}")
    print(f"OUTCOME: {f.outcome}")
    print(f"CONFIDENCE: {f.confidence}")

    # Primitives + parameters
    prims = f.evidence.get("primitives_executed", [])
    print(f"\nPRIMITIVE: {', '.join(prims)}")
    if params:
        print(f"PARAMETERS:")
        for k, v in params.items():
            print(f"  {k} = {v}")

    # Metrics
    if f.primary_metrics:
        print(f"\nRESULT")
        for k, v in list(f.primary_metrics.items())[:8]:
            print(f"  {k}: {v}")

    # Interpretation
    if f.conclusion and f.conclusion != "No conclusion":
        print(f"\nINTERPRETATION")
        print(f"  {f.conclusion[:300]}")

    # Bot implication
    print(f"\nBOT IMPLICATION")
    print(f"  {q.decision_enabled}")

    # Next action
    print(f"\nNEXT ACTION")
    if status == "INCONCLUSIVE":
        print(f"  Gather more data or investigate field availability.")
    elif f.outcome in ("POSITIVE", "PREDICTIVE", "WELL_CALIBRATED"):
        print(f"  Evidence supports this signal. Consider leveraging in strategy.")
    elif f.outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
        print(f"  Evidence suggests this component is not helping. Investigate alternatives.")
    else:
        print(f"  Review evidence and determine if actionable.")

    print(f"\n  (executed in {elapsed:.1f}s)")


# ═══════════════════════════════════════════════════════════════════════════════
# COCKPIT
# ═══════════════════════════════════════════════════════════════════════════════


def _refresh_cockpit_after_run() -> None:
    """Refresh Cockpit after a research run. Failures here do not affect research results."""
    try:
        from research_engine.v10.cockpit.refresh import refresh_cockpit
        result = refresh_cockpit()
        if result.success:
            print(f"  Cockpit refreshed: {result.local_path}")
            if result.s3_published:
                print(f"  S3 published: {result.s3_path}")
            elif result.s3_error:
                print(f"  S3 publish: {result.s3_error}")
        else:
            print(f"  WARNING: Cockpit refresh failed — {result.error}")
    except Exception as e:
        print(f"  WARNING: Cockpit refresh failed ({e}). Research results are unaffected.")


def cmd_cockpit():
    """Regenerate the Research Cockpit HTML from persisted state."""
    print("Refreshing Research Cockpit...")

    from research_engine.v10.cockpit.refresh import refresh_cockpit
    result = refresh_cockpit()

    if not result.success:
        if "No persisted research run" in result.error:
            print("ERROR: No persisted research run found in reports/research/runs/")
            print("  Run 'python research.py all' first to generate research results.")
        else:
            print(f"ERROR: {result.error}")
        return

    print(f"  Latest persisted run: {result.latest_run_id}")
    print(f"  Timestamp: {result.latest_run_timestamp}")
    print(f"  Duration: {result.latest_run_duration:.1f}s")
    print(f"  Cockpit written to: {result.local_path}")

    if result.s3_published:
        print(f"  S3 published: {result.s3_path}")
    elif result.s3_error:
        print(f"  S3: {result.s3_error}")


def cmd_feedback():
    """Generate and display research feedback from latest findings."""
    print("RESEARCH FEEDBACK")
    print("=" * 50)

    from research_engine.v10.feedback.generator import FeedbackGenerator
    from research_engine.v10.feedback.persistence import FeedbackStore

    # Load all latest findings
    findings = []
    if _QUESTIONS_DIR.exists():
        for qdir in sorted(_QUESTIONS_DIR.iterdir()):
            latest = qdir / "latest.json"
            if latest.exists():
                try:
                    findings.append(json.loads(latest.read_text(encoding="utf-8")))
                except Exception:
                    continue

    if not findings:
        print("  No research findings available.")
        print("  Run 'python research.py all' first.")
        return

    # Generate feedback
    gen = FeedbackGenerator()
    feedbacks = gen.from_run(findings)

    # Persist
    store = FeedbackStore()
    store.save_batch(feedbacks)

    # Summary
    summary = gen.summary(feedbacks)
    print(f"\n  Total findings:      {summary['total']}")
    print(f"  Strengths:           {summary['strengths']}")
    print(f"  Weaknesses:          {summary['weaknesses']}")
    print(f"  Opportunities:       {summary['opportunities']}")
    print(f"  Uncertainties:       {summary['uncertainties']}")
    print(f"  Data gaps:           {summary['data_gaps']}")
    print(f"  Proposal-eligible:   {summary['proposal_eligible']}")

    # Show weaknesses
    weaknesses = [fb for fb in feedbacks if fb.feedback_type == "IDENTIFIED_WEAKNESS"]
    if weaknesses:
        print(f"\n  IDENTIFIED WEAKNESSES:")
        for fb in weaknesses:
            eligible = "  [PROPOSAL ELIGIBLE]" if fb.proposal_eligible else ""
            print(f"    {fb.question_id}: {fb.interpretation}{eligible}")

    # Show proposal-eligible
    eligible = [fb for fb in feedbacks if fb.proposal_eligible]
    if eligible:
        print(f"\n  PROPOSAL-ELIGIBLE ({len(eligible)}):")
        for fb in eligible:
            print(f"    {fb.question_id} ({fb.feedback_type}): {fb.finding_outcome} / {fb.finding_confidence}")


def cmd_knowledge(args: list[str]):
    """Display current research knowledge state."""
    from research_engine.v10.knowledge.engine import KnowledgeEngine
    from research_engine.v10.knowledge.store import KnowledgeStore

    # Load all latest findings
    findings = []
    if _QUESTIONS_DIR.exists():
        for qdir in sorted(_QUESTIONS_DIR.iterdir()):
            latest = qdir / "latest.json"
            if latest.exists():
                try:
                    findings.append(json.loads(latest.read_text(encoding="utf-8")))
                except Exception:
                    continue

    if not findings:
        print("No research findings available.")
        return

    # Synthesise knowledge
    engine = KnowledgeEngine()
    items = engine.synthesise_from_findings(findings)

    # Persist
    store = KnowledgeStore()
    store.save_batch(items)

    # Filter by args
    area_filter = None
    status_filter = None
    inspect_id = None
    for i, a in enumerate(args):
        if a == "--area" and i + 1 < len(args):
            area_filter = args[i + 1].upper()
        elif a == "--status" and i + 1 < len(args):
            status_filter = args[i + 1].upper()
        elif a == "inspect" and i + 1 < len(args):
            inspect_id = args[i + 1]

    if inspect_id:
        item = store.load(inspect_id)
        if item:
            print(f"KNOWLEDGE: {item.knowledge_id}")
            print(f"  Subject: {item.subject}")
            print(f"  Area: {item.system_area}")
            print(f"  Status: {item.status}")
            print(f"  Confidence: {item.confidence}")
            print(f"  Statement: {item.statement}")
            print(f"  Supporting: {len(item.supporting_evidence)}")
            print(f"  Contradicting: {len(item.contradicting_evidence)}")
            print(f"  Version: {item.knowledge_version}")
            print(f"  First observed: {item.first_observed_at}")
            print(f"  Last updated: {item.last_updated_at}")
        else:
            print(f"Knowledge item '{inspect_id}' not found.")
        return

    # Apply filters
    display = items
    if area_filter:
        display = [k for k in display if k.system_area == area_filter]
    if status_filter:
        display = [k for k in display if k.status == status_filter]

    # Summary
    print("CURRENT SYSTEM KNOWLEDGE")
    print("=" * 50)
    print(f"  Total items: {len(items)}")
    print(f"  Displaying: {len(display)}")

    by_status = {}
    for k in items:
        by_status[k.status] = by_status.get(k.status, 0) + 1
    print(f"\n  By status:")
    for s, c in sorted(by_status.items()):
        print(f"    {s}: {c}")

    # Show items
    if display:
        print(f"\n  {'ID':<15} {'Area':<12} {'Status':<18} {'Confidence':<12} Subject")
        print(f"  {'-'*80}")
        for k in display[:30]:
            print(f"  {k.knowledge_id:<15} {k.system_area:<12} {k.status:<18} {k.confidence:<12} {k.subject[:40]}")


def cmd_proposals():
    """Display current research proposals and their validation status."""
    from research_engine.v10.proposals.store import ProposalStore

    store = ProposalStore()
    proposal_ids = store.list_proposals()

    print("RESEARCH PROPOSALS")
    print("=" * 50)

    if not proposal_ids:
        print("  No proposals found.")
        return

    print(f"  Total: {len(proposal_ids)}")
    print()

    for pid in proposal_ids:
        p = store.load_proposal(pid)
        v = store.load_validation(pid)
        prom = store.load_promotion(pid)

        status = "PROPOSED"
        if prom:
            status = prom.get("status", "UNKNOWN")
        elif v:
            status = v.get("status", "PENDING")

        area = p.get("system_area", "?") if p else "?"
        problem = (p.get("problem_statement", "")[:60] if p else "")
        print(f"  {pid}")
        print(f"    Area: {area} | Status: {status}")
        if problem:
            print(f"    Problem: {problem}")
        print()


def cmd_proposals_rank(args: list[str]):
    """Rank proposals by research priority."""
    from research_engine.v10.proposals.ranking import ProposalRanker, RankingStore

    top_n = 10
    for i, a in enumerate(args):
        if a == "--top" and i + 1 < len(args):
            try:
                top_n = int(args[i + 1])
            except ValueError:
                pass

    ranker = ProposalRanker()
    ranking = ranker.rank()

    # Persist
    store = RankingStore()
    store.save(ranking)

    print("RESEARCH PROPOSAL PRIORITY")
    print("=" * 60)
    print(f"  Total proposals: {ranking.total_proposals}")
    print(f"  Ranking version: {ranking.ranking_version}")
    print()

    # Show top N
    display = ranking.priorities[:top_n]
    print(f"  {'RANK':<5} {'PRIORITY':<10} {'SCORE':<7} {'PROPOSAL':<25} {'AREA':<14} {'NEXT ACTION'}")
    print(f"  {'-'*90}")

    for p in display:
        print(f"  {p.rank:<5} {p.priority_band:<10} {p.priority_score:<7.1f} {p.proposal_id:<25} {p.system_area:<14} {p.next_action}")

    # Show top recommendation
    if ranking.priorities:
        top = ranking.priorities[0]
        print(f"\n  TOP INVESTIGATION:")
        print(f"    {top.proposal_id}")
        print(f"    Reason: {top.ranking_reason}")
        print(f"    Next: {top.next_action}")
        print(f"    Outcome: {top.finding_outcome} | Confidence: {top.finding_confidence}")
        print(f"    Sample: {top.sample_size}")

    print(f"\n  Governance: No trading-system modification or deployment performed.")


def cmd_continuous(args: list[str]):
    """Continuous research operation."""
    from research_engine.v10.continuous.orchestrator import ContinuousResearchOrchestrator

    orch = ContinuousResearchOrchestrator()
    subcmd = args[0] if args else "run"

    if subcmd == "status":
        state = orch.status()
        print("CONTINUOUS RESEARCH STATUS")
        print("=" * 50)
        if state.get("status") == "NO_PREVIOUS_CYCLE":
            print("  No previous cycle. Run 'python research.py continuous' to start.")
        else:
            print(f"  Cycle: {state.get('cycle_id', '?')}")
            print(f"  Status: {state.get('status', '?')}")
            print(f"  Trigger: {state.get('trigger_reason', '?')}")
            print(f"  Research run: {state.get('research_run_id', '—')}")
            print(f"  Findings: {state.get('finding_count', 0)}")
            print(f"  Feedback: {state.get('feedback_count', 0)}")
            print(f"  Knowledge: {state.get('knowledge_updates', 0)}")
            print(f"  Proposals: {state.get('proposal_count', 0)}")
            print(f"  Promotion eligible: {state.get('promotion_eligible_count', 0)}")
            print(f"  Completed: {state.get('completed_at', '—')}")

    elif subcmd == "plan":
        print("CONTINUOUS RESEARCH — PLAN (read-only)")
        print("=" * 50)
        state = orch.plan()
        print(f"  Trigger: {state.trigger_status}")
        print(f"  Reason: {state.trigger_reason}")
        print(f"  Would execute: {'YES' if state.status == 'DETECTED' else 'NO'}")
        if state.current_population_sizes:
            print(f"  Current populations: {state.current_population_sizes}")

    elif subcmd == "run" or subcmd == "":
        print("RUNNING CONTINUOUS RESEARCH CYCLE")
        print("=" * 50)
        force = "--force" in args
        state = orch.run_cycle(force=force)
        print(f"  Cycle: {state.cycle_id}")
        print(f"  Status: {state.status}")
        print(f"  Trigger: {state.trigger_reason}")
        if state.research_run_id:
            print(f"  Research run: {state.research_run_id}")
        print(f"  Findings: {state.finding_count}")
        print(f"  Feedback: {state.feedback_count}")
        print(f"  Knowledge updates: {state.knowledge_updates}")
        print(f"  Proposals: {state.proposal_count}")
        print(f"  Promotion eligible: {state.promotion_eligible_count}")
        if state.blocked_reason:
            print(f"  Blocked: {state.blocked_reason}")
        print(f"  Stages completed: {state.stages_completed}")

    else:
        print(f"Unknown continuous subcommand: {subcmd}")
        print("  Available: status, plan, run, run --force")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()

    if cmd == "status":
        cmd_status()
    elif cmd == "all":
        cmd_run_all()
    elif cmd == "cockpit":
        cmd_cockpit()
    elif cmd == "feedback":
        cmd_feedback()
    elif cmd == "knowledge":
        cmd_knowledge(args[1:])
    elif cmd == "proposals":
        if len(args) >= 2 and args[1] == "rank":
            cmd_proposals_rank(args[2:])
        else:
            cmd_proposals()
    elif cmd == "candidate" and len(args) >= 3 and args[1] == "experiment":
        _run_candidate_experiment(args[2])
    elif cmd == "continuous":
        cmd_continuous(args[1:])
    elif cmd == "angle" and len(args) >= 2:
        cmd_run_angle(args[1])
    elif cmd == "inspect" and len(args) >= 2:
        cmd_inspect(args[1].upper())
    elif cmd == "bottleneck":
        cmd_bottleneck()
    elif cmd == "next":
        cmd_next()
    elif cmd == "optimisation" or cmd == "optimization":
        _handle_optimisation(args[1:])
    elif cmd.upper().startswith(("E-", "D-", "M-", "S-", "ED", "EM", "ES", "DM", "DS", "MS", "EDM", "DMS", "EDMS")):
        cmd_run_question(cmd.upper())
    else:
        # Try as question ID
        qid = cmd.upper()
        if get_question(qid):
            cmd_run_question(qid)
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)


# Stubs for bottleneck/next/optimisation — implemented in next task
def _run_candidate_experiment(proposal_id: str):
    """Run a candidate experiment end-to-end."""
    from research_engine.v10.proposals.run_experiment import run_candidate_experiment, print_experiment_report
    result = run_candidate_experiment(proposal_id)
    print_experiment_report(result)


def cmd_bottleneck():
    print("Loading bottleneck analysis...")
    from research_engine.v10.cockpit.bottleneck import analyse_bottleneck
    analyse_bottleneck()


def cmd_next():
    print("Determining next investigation...")
    from research_engine.v10.cockpit.bottleneck import recommend_next
    recommend_next()


def _handle_optimisation(args):
    from research_engine.v10.cockpit.optimisation_register import handle_optimisation_command
    handle_optimisation_command(args)


if __name__ == "__main__":
    main()
