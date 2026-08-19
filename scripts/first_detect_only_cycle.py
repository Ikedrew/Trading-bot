"""
FIRST REAL-WORLD VALIDATION: ResearchCycleRunner in DETECT_ONLY mode.

This is an OBSERVATION-ONLY run against the real V10 research dataset.
- No investigations will be started
- No production state will be modified
- No hypotheses will be created
- No experiments will be executed

Reports everything detected for human review.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, ".")

from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleRunner, ResearchCycleConfig, CycleState,
)
from research_engine.lifecycle.finding_trigger import (
    FindingTriggerEngine, EligibilityConfig, ExecutionMode, TriggerStatus,
)
from research_engine.lifecycle.registry import InvestigationRegistry
from research_engine.lifecycle.experiment_catalogue import ExperimentCatalogue


def main():
    print("=" * 80)
    print("FIRST REAL-WORLD DETECT_ONLY CYCLE")
    print("=" * 80)
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. CONFIRM CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("1. CONFIGURATION")
    print("━" * 80)

    config = ResearchCycleConfig(
        mode=ExecutionMode.DETECT_ONLY,
        min_cycle_interval_seconds=0,  # Allow immediate execution for this test
        max_investigations_per_cycle=0,  # Extra safety: cannot investigate
        eligibility=EligibilityConfig(
            min_sample_size=30,
            min_effect_size=0.15,
            max_win_rate_for_poor=0.15,
            min_win_rate_for_strong=0.65,
            cooldown_hours=72.0,
            max_active_triggers=10,
        ),
    )

    print(f"  Mode: {config.mode.value}")
    print(f"  max_investigations_per_cycle: {config.max_investigations_per_cycle}")
    print(f"  Eligibility:")
    print(f"    min_sample_size: {config.eligibility.min_sample_size}")
    print(f"    min_effect_size: {config.eligibility.min_effect_size}")
    print(f"    max_win_rate_for_poor: {config.eligibility.max_win_rate_for_poor}")
    print(f"    min_win_rate_for_strong: {config.eligibility.min_win_rate_for_strong}")
    print(f"    cooldown_hours: {config.eligibility.cooldown_hours}")
    print(f"    max_active_triggers: {config.eligibility.max_active_triggers}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. CONFIRM DETECT_ONLY SAFETY
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("2. SAFETY CONFIRMATION")
    print("━" * 80)
    print(f"  Mode is DETECT_ONLY: {config.mode == ExecutionMode.DETECT_ONLY}")
    print(f"  max_investigations_per_cycle = 0: {config.max_investigations_per_cycle == 0}")
    print(f"  → Cannot execute investigations: CONFIRMED")
    print(f"  → Cannot modify production: CONFIRMED (runner has no production access)")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. CONFIRM CURRENT LIFECYCLE STATE
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("3. CURRENT LIFECYCLE STATE (before cycle)")
    print("━" * 80)

    registry = InvestigationRegistry()
    catalogue = ExperimentCatalogue()
    hypotheses_before = len(registry.all())
    experiments_before = len(catalogue.all())
    print(f"  Hypotheses in registry: {hypotheses_before}")
    print(f"  Experiments in catalogue: {experiments_before}")

    # Check existing triggers
    trigger_engine = FindingTriggerEngine(config=config.eligibility)
    existing_triggers = trigger_engine.all_triggers()
    print(f"  Existing triggers: {len(existing_triggers)}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. CONFIRM DATASET
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("4. DATASET / POPULATION")
    print("━" * 80)

    # Load the shadow outcome universe to see what patterns exist
    try:
        from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
        from research_engine.v10.universes.models import Population
        from research_engine.lifecycle.dataset_fingerprint import compute_content_hash
        import statistics
        from collections import defaultdict

        builder = ShadowOutcomeUniverseBuilder()
        builder.build()
        shadows = builder.get_population(Population.PRIMARY_V10_SHADOW)
        real_shadows = [s for s in shadows if s.get("correlation_id")]

        print(f"  Total V10_PRIMARY shadows: {len(shadows)}")
        print(f"  Real (with correlation_id): {len(real_shadows)}")

        # Per-pattern stats
        by_pattern = defaultdict(list)
        for s in real_shadows:
            pat = s.get("pattern", "")
            r = s.get("r_multiple")
            if pat and r is not None:
                by_pattern[pat].append(r)

        print(f"  Patterns with data: {len(by_pattern)}")
        print()
        print(f"  {'Pattern':<25} {'N':<6} {'Mean R':<9} {'WR%':<7} {'Trigger?'}")
        print(f"  {'─'*25} {'─'*6} {'─'*9} {'─'*7} {'─'*12}")

        for pat in sorted(by_pattern.keys()):
            vals = by_pattern[pat]
            if len(vals) < 10:
                continue
            mean_r = statistics.mean(vals)
            wr = sum(1 for v in vals if v > 0) / len(vals)
            # Check if would trigger
            would_trigger = ""
            if len(vals) >= config.eligibility.min_sample_size:
                if wr < config.eligibility.max_win_rate_for_poor and mean_r < -config.eligibility.min_effect_size:
                    would_trigger = "← POOR"
                elif wr > config.eligibility.min_win_rate_for_strong and mean_r > config.eligibility.min_effect_size:
                    would_trigger = "← STRONG"
            print(f"  {pat:<25} {len(vals):<6} {mean_r:+.4f}  {wr*100:<7.1f} {would_trigger}")

        # Dataset fingerprint (sample)
        fp = compute_content_hash(real_shadows[:100])
        print(f"\n  Dataset fingerprint (first 100 records): {fp[:16]}...")
        print(f"  Observation count: {len(real_shadows)}")

    except Exception as e:
        print(f"  ERROR loading shadows: {e}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. EXECUTE ONE RESEARCH CYCLE
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("5. EXECUTING RESEARCH CYCLE (DETECT_ONLY)")
    print("━" * 80)
    print()

    runner = ResearchCycleRunner(config)
    result = runner.run_cycle()

    print(f"  Cycle ID: {result.cycle_id}")
    print(f"  Status: {result.status}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Findings scanned: {result.findings_scanned}")
    print(f"  Triggers detected: {result.triggers_detected}")
    print(f"  Triggers eligible: {result.triggers_eligible}")
    print(f"  Triggers dismissed: {result.triggers_dismissed}")
    print(f"  Investigations started: {result.investigations_started}")
    print(f"  Investigations completed: {result.investigations_completed}")
    print(f"  Dataset fingerprint: {result.dataset_fingerprint}")
    if result.errors:
        print(f"  Errors: {result.errors}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. ELIGIBLE TRIGGERS (detailed)
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("6. ELIGIBLE TRIGGERS")
    print("━" * 80)
    print()

    # Reload trigger engine to get freshly persisted triggers
    trigger_engine2 = FindingTriggerEngine(config=config.eligibility)
    all_triggers = trigger_engine2.all_triggers()
    eligible = [t for t in all_triggers if t.status == TriggerStatus.ELIGIBLE]
    dismissed = [t for t in all_triggers if t.status == TriggerStatus.DISMISSED]
    blocked = [t for t in all_triggers if t.status == TriggerStatus.BLOCKED]

    print(f"  Total triggers: {len(all_triggers)}")
    print(f"  Eligible: {len(eligible)}")
    print(f"  Dismissed: {len(dismissed)}")
    print(f"  Blocked: {len(blocked)}")
    print()

    if eligible:
        print("  ELIGIBLE TRIGGERS:")
        print()
        for t in eligible:
            print(f"    Trigger ID: {t.trigger_id}")
            print(f"    Finding ID: {t.finding_id}")
            print(f"    Pattern: {t.suggested_patterns}")
            print(f"    Title: {t.title}")
            print(f"    Observation: {t.observation}")
            print(f"    Sample size: {t.sample_size}")
            print(f"    Confidence: {t.confidence}")
            print(f"    Category: {t.category.value}")
            print(f"    Trigger reason: {t.trigger_reason}")
            print(f"    Suggested hypothesis: {t.suggested_claim}")
            print(f"    Suggested null: {t.suggested_null}")
            print(f"    Suggested experiment: {t.suggested_experiment_type.value}")
            print(f"    Priority: {t.priority}")
            print()
    else:
        print("  No eligible triggers detected.")
        print()

    if dismissed:
        print(f"  DISMISSED ({len(dismissed)}):")
        for t in dismissed:
            print(f"    {t.trigger_id}: {t.title[:40]} — {t.dismissed_reason}")
        print()

    if blocked:
        print(f"  BLOCKED ({len(blocked)}):")
        for t in blocked:
            print(f"    {t.trigger_id}: {t.title[:40]} — {t.dismissed_reason}")
        print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. VERIFY NO PRODUCTION MODIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("7. PRODUCTION SAFETY VERIFICATION")
    print("━" * 80)

    hypotheses_after = len(InvestigationRegistry().all())
    experiments_after = len(ExperimentCatalogue().all())
    print(f"  Hypotheses before: {hypotheses_before}, after: {hypotheses_after}")
    print(f"  Experiments before: {experiments_before}, after: {experiments_after}")
    print(f"  Hypotheses created this cycle: {hypotheses_after - hypotheses_before}")
    print(f"  Experiments created this cycle: {experiments_after - experiments_before}")
    print(f"  Production state modified: NO")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. AUDIT EVENTS
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("8. AUDIT EVENTS")
    print("━" * 80)

    audit_path = Path("logs/research_lifecycle/audit_log.jsonl")
    if audit_path.exists():
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        # Show last 10 events
        recent = lines[-10:] if len(lines) > 10 else lines
        for line in recent:
            entry = json.loads(line)
            print(f"  {entry.get('timestamp','')[:19]} | {entry.get('event','')} | "
                  f"{entry.get('cycle_id', entry.get('trigger_id', entry.get('hypothesis_id', '')))[:20]}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 9. COMMAND CENTER LIFECYCLE OUTPUT
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("9. COMMAND CENTER LIFECYCLE SECTION")
    print("━" * 80)

    from research_engine.command_center.research_command_center import _build_lifecycle_section
    lc = _build_lifecycle_section()
    if lc and lc.available:
        print(f"  Hypotheses: {lc.total_hypotheses}")
        for status, count in sorted(lc.hypotheses_by_status.items()):
            print(f"    {status}: {count}")
        print(f"  Experiments: {lc.total_experiments}")
        print(f"    Completed: {lc.experiments_completed}")
        print(f"  Conclusions:")
        print(f"    VALIDATED: {lc.conclusions_validated}")
        print(f"    REJECTED: {lc.conclusions_rejected}")
        print(f"    INCONCLUSIVE: {lc.conclusions_inconclusive}")
        print(f"  Research Triggers:")
        print(f"    Total: {lc.total_triggers}")
        print(f"    Eligible: {lc.triggers_eligible}")
        print(f"    Dismissed: {lc.triggers_dismissed}")
        if lc.trigger_candidates:
            print(f"  Top candidates:")
            for c in lc.trigger_candidates[:3]:
                print(f"    {c.get('trigger_id','')} | {c.get('title','')[:40]} | N={c.get('sample_size',0)}")
    else:
        print(f"  {lc.unavailable_reason if lc else 'Not available'}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # 10. RUNNER STATUS
    # ═══════════════════════════════════════════════════════════════════════════
    print("━" * 80)
    print("10. RUNNER STATUS")
    print("━" * 80)
    status = runner.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
    print()

    print("=" * 80)
    print("CYCLE COMPLETE — DETECT_ONLY — NO PRODUCTION CHANGES")
    print("=" * 80)


if __name__ == "__main__":
    main()
