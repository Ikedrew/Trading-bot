"""
Targeted verification of M-002, M-004, S-003 against real enriched universes.
Executes ONLY these three questions through the canonical runner path.
Does NOT modify anything. Does NOT publish to S3.
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
os.chdir(str(_PROJECT_ROOT))

from research_engine.v10.universes.question_bank import get_question
from research_engine.v10.universes.models import Universe
from research_engine.v10.universes import (
    ExecutionUniverseBuilder, DecisionUniverseBuilder,
    MarketUniverseBuilder, StrategyUniverseBuilder,
)
from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.runner.primitive_mapping import QUESTION_PARAMETERS, build_full_mapping
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext


def main():
    print("=" * 60)
    print("TARGETED VERIFICATION: M-002, M-004, S-003")
    print("=" * 60)

    # 1. Build universes (same as orchestrator)
    print("\nBuilding universes...")
    builders = {}
    for UClass, utype in [
        (ExecutionUniverseBuilder, Universe.EXECUTION),
        (DecisionUniverseBuilder, Universe.DECISION),
        (MarketUniverseBuilder, Universe.MARKET),
        (StrategyUniverseBuilder, Universe.STRATEGY),
    ]:
        b = UClass()
        b.build()
        builders[utype] = b
        print(f"  {utype.value}: {b.metadata.record_count} records")

    # 2. Apply outcome enrichment
    print("\nApplying outcome enrichment...")
    exe_builder = builders[Universe.EXECUTION]
    enrichment = OutcomeEnrichment(exe_builder)
    results = enrichment.enrich_all(builders)
    for u, r in results.items():
        print(f"  {u}: {r.matched} enriched / {r.total_records} total")

    # 3. Setup runner
    registry = build_default_registry()
    mapping = build_full_mapping(
        __import__("research_engine.v10.universes.question_bank", fromlist=["QUESTION_BANK"]).QUESTION_BANK
    )
    runner = QuestionRunner(registry, mapping)
    ctx = RunContext(run_id="verify_three_params")

    # 4. Verify each question
    all_pass = True
    for qid in ["M-002", "M-004", "S-003"]:
        passed = verify_one(qid, builders, runner, ctx)
        if not passed:
            all_pass = False

    # 5. Final verdict
    print("\n" + "=" * 60)
    if all_pass:
        print("TARGETED VERIFICATION: PASS")
        print("  M-002: PASS")
        print("  M-004: PASS")
        print("  S-003: PASS")
    else:
        print("TARGETED VERIFICATION: FAIL")
        print("  See details above for which questions failed.")
    print("=" * 60)


def verify_one(qid, builders, runner, ctx):
    print(f"\n{'─'*60}")
    print(f"QUESTION: {qid}")
    print(f"{'─'*60}")

    q = get_question(qid)
    params = QUESTION_PARAMETERS.get(qid, {})

    # Resolve population (same as orchestrator)
    primary_u = q.required_universes[0]
    builder = builders[primary_u]
    if q.required_populations:
        pop = q.required_populations[0]
        population = builder.get_population(pop)
        print(f"  POPULATION: {pop.value}")
    else:
        population = builder.records
        print(f"  POPULATION: ALL")

    print(f"  RAW POPULATION SIZE: {len(population)}")
    print(f"  PARAMETERS: {params}")

    # Determine fields
    feature_field = params.get("feature_field", params.get("predicted_field", ""))
    outcome_field = params.get("outcome_field", "r_multiple")
    print(f"  FEATURE FIELD: {feature_field}")
    print(f"  OUTCOME FIELD: {outcome_field}")

    # Check field availability
    feature_available = sum(1 for r in population if r.get(feature_field) is not None)
    outcome_available = sum(1 for r in population if r.get(outcome_field) is not None)
    both_available = sum(
        1 for r in population
        if r.get(feature_field) is not None and r.get(outcome_field) is not None
    )
    print(f"  FEATURE VALUES AVAILABLE: {feature_available}/{len(population)}")
    print(f"  OUTCOME VALUES AVAILABLE: {outcome_available}/{len(population)}")
    print(f"  BOTH AVAILABLE (analytical pairs): {both_available}")

    # Execute through runner (uses QUESTION_PARAMETERS automatically)
    result = runner.run_question(q, population, ctx)

    if result.success and result.finding:
        f = result.finding
        print(f"  ANALYTICAL SAMPLE SIZE: {f.sample_sizes.get('total', 0)}")
        print(f"  PRIMITIVE RESULT: metrics={list(f.primary_metrics.keys())[:8]}")
        print(f"  FINDING OUTCOME: {f.outcome}")
        print(f"  FINDING CONFIDENCE: {f.confidence}")

        # Check if the analytical sample size from the primary primitive is > 0
        # The metrics should contain actual values
        has_evidence = bool(f.primary_metrics) and f.confidence != "INSUFFICIENT"
        if has_evidence:
            print(f"  STATUS: PASS (analytical evidence produced)")
            return True
        else:
            print(f"  STATUS: FAIL (no analytical evidence despite {both_available} available pairs)")
            return False
    else:
        print(f"  RUNNER ERROR: {result.error}")
        print(f"  STATUS: FAIL")
        return False


if __name__ == "__main__":
    main()
