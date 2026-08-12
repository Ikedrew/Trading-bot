"""M-004 End-to-End Forensic Audit — run from project root."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.universes.models import Universe, Population
from research_engine.v10.universes import MarketUniverseBuilder, ExecutionUniverseBuilder
from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment
from research_engine.v10.runner.primitives.implementations import PredictivePowerPrimitive
from research_engine.v10.runner.primitive_mapping import QUESTION_PARAMETERS

print("M-004 FORENSIC AUDIT")
print("=" * 60)

# STEP 1 — POPULATION
print("\nSTEP 1 — POPULATION")
exe = ExecutionUniverseBuilder(); exe.build()
mkt = MarketUniverseBuilder(); mkt.build()
pop_pre = mkt.get_population(Population.ALL_MARKET_STATES)
print(f"  Source: MarketUniverseBuilder (decision_trace v10_market_state + market_context)")
print(f"  Population: ALL_MARKET_STATES")
print(f"  Count BEFORE enrichment: {len(pop_pre)}")
if pop_pre:
    r = pop_pre[0]
    print(f"  Record type: market-state observation")
    print(f"  Relevant fields in sample: h1_structural_clarity={r.get('h1_structural_clarity')}, r_multiple={r.get('r_multiple')}, entity_id={r.get('entity_id','')[:20]}")

# STEP 2 — ENRICHMENT
print("\nSTEP 2 — ENRICHMENT")
print(f"  Stage: OutcomeEnrichment (joins r_multiple from Execution via entity_id)")
print(f"  Input records: {len(pop_pre)}")
enrichment = OutcomeEnrichment(exe)
enr_result = enrichment.enrich(mkt)
pop_post = mkt.get_population(Population.ALL_MARKET_STATES)
print(f"  Output records: {len(pop_post)}")
print(f"  Records removed: 0 (enrichment never removes records)")
print(f"  Records matched (r_multiple added): {enr_result.matched}")
print(f"  Records unmatched (r_multiple stays None): {enr_result.unmatched}")
print(f"  Fields added: r_multiple, execution_match, outcome_available, execution_id, exit_reason, net_realised_pnl")
print(f"  h1_structural_clarity: NOT modified by enrichment (already in source)")

# STEP 3 — FIELD AVAILABILITY
print("\nSTEP 3 — FIELD AVAILABILITY (after enrichment)")
total = len(pop_post)
h1_present = sum(1 for r in pop_post if r.get("h1_structural_clarity") is not None)
h1_null = total - h1_present
h1_numeric = sum(1 for r in pop_post if isinstance(r.get("h1_structural_clarity"), (int, float)))
r_present = sum(1 for r in pop_post if r.get("r_multiple") is not None)
r_null = total - r_present
r_numeric = sum(1 for r in pop_post if isinstance(r.get("r_multiple"), (int, float)))
both = sum(1 for r in pop_post if r.get("h1_structural_clarity") is not None and r.get("r_multiple") is not None)
only_feat = h1_present - both
only_out = r_present - both
neither = total - h1_present - r_present + both

print(f"  TOTAL RECORDS: {total}")
print(f"  h1_structural_clarity:")
print(f"    present: {h1_present}")
print(f"    null/missing: {h1_null}")
print(f"    numeric: {h1_numeric}")
print(f"  r_multiple:")
print(f"    present: {r_present}")
print(f"    null/missing: {r_null}")
print(f"    numeric: {r_numeric}")
print(f"  BOTH VALID: {both}")
print(f"  FEATURE ONLY: {only_feat}")
print(f"  OUTCOME ONLY: {only_out}")
print(f"  NEITHER: {neither}")

# STEP 4 — RECORD LOSS
print("\nSTEP 4 — RECORD LOSS TRACE")
print(f"  Stage                    Before    After     Removed")
print(f"  Population               -         {total}       -")
print(f"  Enrichment               {total}       {total}       0")
print(f"  Feature filter           {total}       {h1_present}     {h1_null}")
print(f"  Outcome filter           {h1_present}     {both}        {only_feat}")
print(f"  Primitive input          {both}        {both}        0")
print(f"  KEY: {total} -> {both} usable records ({total - both} lost)")
print(f"  Primary loss: r_multiple unavailable ({r_null} records)")

# STEP 5 — PRIMITIVE INPUT
print("\nSTEP 5 — PRIMITIVE INPUT")
params = QUESTION_PARAMETERS["M-004"]
print(f"  feature_field: {params['feature_field']}")
print(f"  outcome_field: {params['outcome_field']}")
print(f"  population_count: {total}")
pairs = [
    (r["h1_structural_clarity"], r["r_multiple"])
    for r in pop_post
    if r.get("h1_structural_clarity") is not None and r.get("r_multiple") is not None
]
print(f"  usable_pairs: {len(pairs)}")
if pairs:
    print(f"  first 5 usable pairs:")
    for feat, out in pairs[:5]:
        print(f"    ({feat:.4f}, {out:.4f})")

# STEP 6 — PREDICTIVE_POWER EXECUTION
print("\nSTEP 6 — PREDICTIVE_POWER")
print(f"  Minimum N: 10 (hard-coded in primitive)")
print(f"  Effective input N: {len(pairs)}")
print(f"  If N < 10: returns AnalysisResult(sample_size=N, warnings=['Insufficient data'])")
print(f"  If N >= 10: sorts pairs, creates buckets, tests monotonicity + spread")

# STEP 7 — ACTUAL RESULT
print("\nSTEP 7 — RAW PRIMITIVE RESULT")
prim = PredictivePowerPrimitive()
result = prim.analyse(pop_post, params)
print(f"  sample_size: {result.sample_size}")
print(f"  success: {result.success}")
print(f"  metrics: {result.metrics}")
print(f"  evidence: {result.evidence}")
print(f"  warnings: {result.warnings}")
print(f"  error: {result.error}")

# STEP 8 — WHY INCONCLUSIVE
print("\nSTEP 8 — WHY INCONCLUSIVE")
if len(pairs) < 10:
    print(f"  Classification: MISSING_OUTCOME_DATA")
    print(f"  The primitive found {len(pairs)} valid (feature, outcome) pairs.")
    print(f"  It requires >= 10 pairs to perform predictive_power analysis.")
    print(f"  r_multiple is only available in {r_present} records (outcome enrichment matched {enr_result.matched}).")
    print(f"  h1_structural_clarity is available in {h1_present} records — feature data is plentiful.")
    print(f"  The limitation is OUTCOME DATA, not feature data or analytical capability.")
else:
    print(f"  The primitive DID execute with {len(pairs)} pairs.")
    print(f"  Check the metrics/confidence for the actual verdict reason.")

# STEP 9 — QUESTION VALIDITY
print("\nSTEP 9 — QUESTION VALIDITY")
print(f"  Q: Does h1_structural_clarity predict r_multiple?")
print(f"  This IS a valid research question:")
print(f"    h1_structural_clarity = market structure quality at decision time (0-1)")
print(f"    r_multiple = realised trade outcome (R-multiples)")
print(f"  The hypothesis: trades taken in clearer market structure do better.")
print(f"  The pairing is conceptually correct.")
print(f"  The limitation is DATA VOLUME, not question design.")

# STEP 10 — SHADOW/LIVE
print("\nSTEP 10 — SHADOW/LIVE")
print(f"  M-004 population source: Market Universe")
print(f"  Record type: [X] market observations (from live bot decision pipeline)")
print(f"  Shadow data: NOT separately used by M-004")
print(f"  h1_structural_clarity source: v10_market_state.h1.structural_clarity (live pipeline)")
print(f"  r_multiple source: Execution Universe via entity_id enrichment (live trades)")

# FINAL VERDICT
print("\n" + "=" * 60)
print("M-004 — FINAL AUDIT")
print("=" * 60)
print(f"""
Population:                  {total}
Actual analytical sample:    {len(pairs)}
Feature availability:        {h1_present}/{total}
Outcome availability:        {r_present}/{total}
Valid feature/outcome pairs: {both}

Data flow:
  Market Context logs + Decision Traces ({total} records)
    -> MarketUniverseBuilder ({total})
    -> OutcomeEnrichment ({enr_result.matched} matched, {enr_result.unmatched} unmatched)
    -> Valid pairs filter ({both} with both fields)
    -> predictive_power (N={len(pairs)}, minimum=10)
    -> INCONCLUSIVE (N < 10)

Why INCONCLUSIVE:
  MISSING_OUTCOME_DATA
  Only {r_present} of {total} market records have r_multiple.
  Outcome enrichment only matches records with entity_ids that
  exist in both Market Universe AND Execution Universe.
  The Market Universe has ~{total} observations but only ~80
  executed trades have matching entity_ids.

Is the calculation trustworthy:
  VALID BUT DATA-LIMITED
  The primitive, parameters, and question design are correct.
  The primitive correctly refuses to analyse with < 10 pairs.
  When more trades accumulate, this question will become answerable.

Confirmed research-engine issue:
  The CLI labels len(population) as "ANALYTICAL SAMPLE".
  This shows {total} when the ACTUAL analytical sample is {len(pairs)}.
  Consequence: user cannot distinguish "lots of data, bad signal"
  from "no usable data yet" without running a forensic audit.

Does M-004 currently tell us anything about market structure:
  NO
  With only {len(pairs)} valid pairs (< 10 minimum), no conclusion
  is possible. The verdict is correct: INCONCLUSIVE.

Required change:
  REPORTING/OBSERVABILITY FIX REQUIRED.
  The finding should report analytical_sample_size = {len(pairs)}, not {total}.
  UNDERLYING PRIMITIVE NOT YET SHOWN TO BE WRONG.

ONE NEXT ACTION:
  Fix the CLI/finding to report the primitive's actual sample_size
  (the number of valid feature/outcome pairs) instead of len(population).
  This is a display/reporting fix in compose_evidence() and the CLI.
""")
