"""V3 Research Readiness Audit — complete data lifecycle and quality assessment."""

import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════════
# PART 1: DATA STORAGE AUDIT
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("V3 RESEARCH READINESS AUDIT")
print("=" * 70)

print("\n## PART 1: DATA STORAGE & LINEAGE")
print("-" * 40)
print("""
V3 Observation Lifecycle:
    1. Created: core/observers/v3_opportunity_observer.py → _do_observe()
    2. Built: core/v3_opportunity_builder.py → build_v3_opportunity()
    3. Persisted: core/v3_opportunity_builder.py → persist_v3_opportunity()
    4. Storage: logs/v3_opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl
    5. Schema: v3_opportunity_v1

Lineage to outcomes:
    V3Opportunity.correlation_id
        → shadow_trades.identity.entity_id
        → shadow_trades.identity.correlation_id
        → shadow_trades.simulated_outcome.pnl_r_multiple

Join keys available:
    - correlation_id (primary)
    - symbol + timestamp_utc (fallback, ±300s tolerance)

Other persistence layers:
    - V2 opportunities: logs/v2_opportunities/{SYMBOL}/{DATE}.jsonl
    - Shadow trades: logs/shadow_trades/{SYMBOL}/{DATE}.jsonl
    - S3 mirror: s3://trading-bot-data-mk1/shadow_trades/ (if enabled)
    - No Athena DDL for V3 yet (V2 has DDL defined)
""")

# ═══════════════════════════════════════════════════════════════════
# PART 2: CURRENT DATASET
# ═══════════════════════════════════════════════════════════════════
print("\n## PART 2: CURRENT V3 DATASET")
print("-" * 40)

v3_dir = Path("logs/v3_opportunities")
records = []
if v3_dir.exists():
    for f in v3_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except:
                        pass

print(f"Total V3 observations: {len(records)}")

if records:
    symbols = Counter(r.get("symbol", "") for r in records)
    print(f"Symbols: {dict(symbols)}")

    timestamps = [r.get("timestamp_utc", 0) for r in records if r.get("timestamp_utc", 0) > 1e9]
    if timestamps:
        earliest = datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
        latest = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
        print(f"Date range: {earliest.strftime('%Y-%m-%d %H:%M')} to {latest.strftime('%Y-%m-%d %H:%M')}")

    sessions = Counter(r.get("session", "") for r in records)
    print(f"Sessions: {dict(sessions)}")

    # Outcomes
    with_outcome = sum(1 for r in records if r.get("outcome_linked"))
    print(f"\nWith linked outcomes: {with_outcome}")
    print(f"Pending (no outcome): {len(records) - with_outcome}")
    print(f"\nResearch-ready (with outcomes): {with_outcome}")
else:
    print("NO V3 DATA COLLECTED YET")

# Shadow trades available for linkage
shadow_dir = Path("logs/shadow_trades")
shadow_count = 0
if shadow_dir.exists():
    for d in shadow_dir.iterdir():
        if d.is_dir() and d.name != "UNKNOWN":
            for f in d.glob("*.jsonl"):
                with open(f) as fh:
                    shadow_count += sum(1 for line in fh if line.strip())
print(f"\nShadow trades available for linkage: {shadow_count}")

# ═══════════════════════════════════════════════════════════════════
# PART 3: FEATURE POPULATION AUDIT
# ═══════════════════════════════════════════════════════════════════
print("\n\n## PART 3: FEATURE POPULATION AUDIT")
print("-" * 40)

feature_groups = {
    "MARKET LOCATION": [
        "h4_swing_high", "h4_swing_low", "h4_range_position",
        "h1_swing_high", "h1_swing_low", "h1_range_position",
        "h1_distance_from_high_pips", "h1_distance_from_low_pips",
        "m15_swing_high", "m15_swing_low", "m15_range_position",
        "nearest_support_price", "nearest_support_distance_pips",
        "nearest_resistance_price", "nearest_resistance_distance_pips",
    ],
    "VOLATILITY / DISPLACEMENT": [
        "atr", "displacement_into_level", "displacement_magnitude_atr",
        "rejection_candle_present", "rejection_body_ratio", "rejection_wick_atr_ratio",
        "bars_at_current_level", "consolidation_range_pips",
    ],
    "LIQUIDITY": [
        "equal_highs_above", "equal_highs_distance_pips", "equal_highs_count",
        "equal_lows_below", "equal_lows_distance_pips", "equal_lows_count",
        "prev_day_high", "prev_day_low",
        "distance_to_prev_day_high_pips", "distance_to_prev_day_low_pips",
        "prev_day_high_swept", "prev_day_low_swept",
        "prev_session_high", "prev_session_low",
        "distance_to_prev_session_high_pips", "distance_to_prev_session_low_pips",
        "prev_session_high_swept", "prev_session_low_swept",
        "liquidity_sweep_just_occurred", "sweep_direction", "sweep_distance_pips",
        "bars_since_sweep",
    ],
    "FAIR VALUE GAPS": [
        "nearest_fvg_above_price", "nearest_fvg_above_distance_pips", "fvg_above_filled_pct",
        "nearest_fvg_below_price", "nearest_fvg_below_distance_pips", "fvg_below_filled_pct",
        "price_inside_fvg", "fvg_direction_if_inside",
        "total_unfilled_fvgs_above", "total_unfilled_fvgs_below",
    ],
    "ORDER BLOCKS": [
        "nearest_demand_ob_price", "nearest_demand_ob_distance_pips",
        "demand_ob_strength", "demand_ob_mitigated",
        "nearest_supply_ob_price", "nearest_supply_ob_distance_pips",
        "supply_ob_strength", "supply_ob_mitigated",
        "price_inside_ob", "ob_type_if_inside",
    ],
    "EXECUTION": [
        "bid", "ask", "spread", "spread_risk_ratio", "atr", "session",
    ],
}

total_fields = 0
total_populated = 0
never_populated = []

for group_name, fields in feature_groups.items():
    print(f"\n  {group_name}:")
    group_pop = 0
    group_total = 0
    for field in fields:
        non_zero = sum(1 for r in records if r.get(field) not in (0, 0.0, "", False, None))
        pct = non_zero / len(records) * 100 if records else 0
        total_fields += 1
        if non_zero > 0:
            total_populated += 1
            group_pop += 1
        else:
            never_populated.append(field)
        group_total += 1
        status = "OK" if pct > 50 else "LOW" if pct > 5 else "EMPTY"
        if status != "OK":
            print(f"    {field:40s} {non_zero:4d}/{len(records):4d} ({pct:5.1f}%) [{status}]")
    if group_pop == group_total:
        print(f"    All {group_total} fields populated (>50%)")
    else:
        print(f"    Populated: {group_pop}/{group_total}")

print(f"\n  TOTAL: {total_populated}/{total_fields} fields have data")
print(f"  Never populated: {len(never_populated)} fields")
if never_populated:
    print(f"  Empty fields: {', '.join(never_populated[:20])}")

# ═══════════════════════════════════════════════════════════════════
# PART 4: SAMPLE SIZE REQUIREMENTS
# ═══════════════════════════════════════════════════════════════════
print("\n\n## PART 4: SAMPLE SIZE REQUIREMENTS")
print("-" * 40)

# Event frequencies from current data
if records:
    n = len(records)
    eq_highs_rate = sum(1 for r in records if r.get("equal_highs_above")) / n
    eq_lows_rate = sum(1 for r in records if r.get("equal_lows_below")) / n
    sweep_rate = sum(1 for r in records if r.get("liquidity_sweep_just_occurred")) / n
    fvg_rate = sum(1 for r in records if r.get("price_inside_fvg")) / n
    ob_demand_rate = sum(1 for r in records if r.get("nearest_demand_ob_price", 0) > 0) / n
    ob_supply_rate = sum(1 for r in records if r.get("nearest_supply_ob_price", 0) > 0) / n
    displacement_rate = sum(1 for r in records if r.get("displacement_into_level")) / n
    rejection_rate = sum(1 for r in records if r.get("rejection_candle_present")) / n

    print(f"\n  Event frequencies (current sample n={n}):")
    print(f"    Equal highs above:        {eq_highs_rate:.1%}")
    print(f"    Equal lows below:         {eq_lows_rate:.1%}")
    print(f"    Liquidity sweep:          {sweep_rate:.1%}")
    print(f"    Price inside FVG:         {fvg_rate:.1%}")
    print(f"    Demand OB present:        {ob_demand_rate:.1%}")
    print(f"    Supply OB present:        {ob_supply_rate:.1%}")
    print(f"    Displacement into level:  {displacement_rate:.1%}")
    print(f"    Rejection candle:         {rejection_rate:.1%}")

print("""
  Minimum sample size requirements (p<0.05, 80% power):
  
  Research Area             | Min n  | Preferred n | Notes
  ─────────────────────────┼────────┼─────────────┼──────────────
  Individual feature test   |    100 |         200 | Per feature category
  Feature combination       |    150 |         300 | Per combination tested
  Rare event (sweep/OB)     |     50 |         100 | Per event type with outcome
  Probability calibration   |    200 |         500 | For reliable calibration
  Walk-forward validation   |    300 |         500 | Train + test split
""")

# Estimate collection time needed
if records and n > 0:
    timestamps_valid = [r.get("timestamp_utc", 0) for r in records if r.get("timestamp_utc", 0) > 1e9]
    if len(timestamps_valid) >= 2:
        span_hours = (max(timestamps_valid) - min(timestamps_valid)) / 3600
        rate_per_hour = n / max(span_hours, 1)
        hours_for_200 = (200 - n) / max(rate_per_hour, 0.01)
        hours_for_500 = (500 - n) / max(rate_per_hour, 0.01)
        print(f"  Current collection rate: {rate_per_hour:.2f} records/hour")
        print(f"  Time to 200 records: {hours_for_200:.0f} hours ({hours_for_200/24:.1f} days)")
        print(f"  Time to 500 records: {hours_for_500:.0f} hours ({hours_for_500/24:.1f} days)")
        print(f"  (Assumes continuous bot operation)")

# ═══════════════════════════════════════════════════════════════════
# PART 5: RESEARCH OUTPUT PIPELINE
# ═══════════════════════════════════════════════════════════════════
print("\n\n## PART 5: RESEARCH PUBLICATION PIPELINE")
print("-" * 40)
print("""
  Existing research output locations:
    - analysis/reports/*.json         (V2 Discovery Engine reports)
    - analysis/artifacts/             (datasets, intermediate results)
    - architecture/*.md               (human-readable research conclusions)
    - research_engine/v2_discovery/   (statistical analysis framework)
  
  V3 research output should follow same conventions:
    Input:  logs/v3_opportunities/{SYMBOL}/{DATE}.jsonl
    Link:   core/research/v2_outcome_linker.py (reusable for V3)
    Engine: research_engine/v2_discovery/ (CQ1-CQ4 can run on V3 fields)
    Output: analysis/reports/v3_discovery_*.json
    Docs:   architecture/V3_DISCOVERY_RESULTS.md
  
  Pipeline:
    1. link_outcomes() — attach shadow trade results to V3 records
    2. run_full_discovery(linked_v3_records) — CQ1-CQ4 analysis
    3. save_report() — persist JSON
    4. Manual: create architecture/*.md summary
""")

# ═══════════════════════════════════════════════════════════════════
# PART 6: COLLECTION TARGETS
# ═══════════════════════════════════════════════════════════════════
print("\n## PART 6: RECOMMENDED COLLECTION TARGETS")
print("-" * 40)
print("""
  Research Area        | Minimum Target      | Preferred Target
  ─────────────────────┼─────────────────────┼──────────────────
  Location analysis    | 200 with outcomes   | 500 with outcomes
  Liquidity analysis   | 100 with sweeps     | 200 with sweeps
  FVG analysis         | 100 with FVG events | 200 with FVG events
  Order block analysis | 50 near OB          | 100 near OB
  Combined context     | 300 with outcomes   | 500 with outcomes
  
  CRITICAL: "with outcomes" means V3 observation LINKED to shadow trade result.
  Unlinked observations cannot determine EV.
""")

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("READINESS VERDICT")
print("=" * 70)

if not records:
    print("\n  STATUS: NOT READY")
    print("  Reason: Zero V3 observations collected with Phase 2 detectors.")
    print("  Action: Run bot to collect data. Phase 2 detectors are now live.")
elif len(records) < 50:
    print(f"\n  STATUS: COLLECTING (n={len(records)})")
    print("  Reason: Insufficient sample for any statistical analysis.")
    print("  Action: Continue collecting. Need minimum 200 linked records.")
elif with_outcome < 50:
    print(f"\n  STATUS: NEEDS LINKAGE (n={len(records)}, linked={with_outcome})")
    print("  Reason: Observations exist but outcomes not linked.")
    print("  Action: Run outcome linker against shadow trades.")
else:
    print(f"\n  STATUS: READY FOR PRELIMINARY ANALYSIS (n={len(records)}, linked={with_outcome})")
    print("  Action: Run V2 Discovery Engine against V3 fields.")

print("\n" + "=" * 70)
