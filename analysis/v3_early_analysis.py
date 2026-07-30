"""Early V3 observation pipeline analysis."""

import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

# Load V3 data
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

print("=" * 70)
print("V3 EARLY PROGRESS ANALYSIS")
print("=" * 70)
print()

# ═══════════════════════════════════════════════════════════════════
# 1. COLLECTION STATUS
# ═══════════════════════════════════════════════════════════════════
print("1. COLLECTION STATUS")
print("-" * 40)
print(f"Total V3 observations: {len(records)}")

# Symbols
symbols = Counter(r.get("symbol", "") for r in records)
print(f"Symbols: {dict(symbols)}")

# Sessions
sessions = Counter(r.get("session", "") for r in records)
print(f"Sessions: {dict(sessions)}")

# Timestamps
timestamps = [r.get("timestamp_utc", 0) for r in records if r.get("timestamp_utc", 0) > 1_000_000_000]
if timestamps:
    earliest = datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
    latest = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
    span_hours = (max(timestamps) - min(timestamps)) / 3600
    rate = len(records) / max(span_hours, 1)
    print(f"Date range: {earliest.strftime('%Y-%m-%d %H:%M')} to {latest.strftime('%Y-%m-%d %H:%M')}")
    print(f"Span: {span_hours:.1f} hours")
    print(f"Collection rate: {rate:.1f} records/hour")
print()

# ═══════════════════════════════════════════════════════════════════
# 2. DATA QUALITY AUDIT
# ═══════════════════════════════════════════════════════════════════
print("2. DATA QUALITY AUDIT")
print("-" * 40)

# Categorize fields
position_fields = ["price_at_observation", "h4_swing_high", "h4_swing_low", "h4_range_position",
                   "h4_distance_from_high_pips", "h4_distance_from_low_pips",
                   "h1_swing_high", "h1_swing_low", "h1_range_position",
                   "h1_distance_from_high_pips", "h1_distance_from_low_pips",
                   "h1_last_bos_price", "h1_distance_from_bos_pips",
                   "m15_swing_high", "m15_swing_low", "m15_range_position"]

sr_fields = ["nearest_support_price", "nearest_support_distance_pips",
             "nearest_support_touches", "nearest_support_age_bars", "nearest_support_timeframe",
             "nearest_resistance_price", "nearest_resistance_distance_pips",
             "nearest_resistance_touches", "nearest_resistance_age_bars", "nearest_resistance_timeframe",
             "support_quality_score", "resistance_quality_score"]

liquidity_fields = ["equal_highs_above", "equal_highs_distance_pips", "equal_highs_count",
                    "equal_lows_below", "equal_lows_distance_pips", "equal_lows_count",
                    "prev_session_high", "prev_session_low",
                    "distance_to_prev_session_high_pips", "distance_to_prev_session_low_pips",
                    "prev_session_high_swept", "prev_session_low_swept",
                    "prev_day_high", "prev_day_low",
                    "distance_to_prev_day_high_pips", "distance_to_prev_day_low_pips",
                    "prev_day_high_swept", "prev_day_low_swept",
                    "liquidity_sweep_just_occurred", "sweep_direction", "sweep_distance_pips", "bars_since_sweep"]

ob_fields = ["nearest_demand_ob_price", "nearest_demand_ob_distance_pips",
             "demand_ob_timeframe", "demand_ob_mitigated", "demand_ob_strength",
             "nearest_supply_ob_price", "nearest_supply_ob_distance_pips",
             "supply_ob_timeframe", "supply_ob_mitigated", "supply_ob_strength",
             "price_inside_ob", "ob_type_if_inside"]

fvg_fields = ["nearest_fvg_above_price", "nearest_fvg_above_distance_pips", "fvg_above_filled_pct",
              "nearest_fvg_below_price", "nearest_fvg_below_distance_pips", "fvg_below_filled_pct",
              "price_inside_fvg", "fvg_direction_if_inside",
              "total_unfilled_fvgs_above", "total_unfilled_fvgs_below"]

displacement_fields = ["displacement_into_level", "displacement_magnitude_atr",
                       "rejection_candle_present", "rejection_body_ratio", "rejection_wick_atr_ratio",
                       "bars_at_current_level", "consolidation_range_pips"]

exec_fields = ["bid", "ask", "spread", "spread_risk_ratio", "atr", "session"]

def count_populated(field_list, label):
    print(f"\n  {label}:")
    populated = 0
    empty = 0
    for field in field_list:
        non_zero = sum(1 for r in records if r.get(field) not in (0, 0.0, "", False, None))
        pct = non_zero / len(records) * 100 if records else 0
        status = "OK" if pct > 50 else "LOW" if pct > 5 else "EMPTY"
        if status != "OK" or pct < 90:
            print(f"    {field:40s} {non_zero:4d}/{len(records):4d} ({pct:5.1f}%) [{status}]")
        if non_zero > 0:
            populated += 1
        else:
            empty += 1
    return populated, empty

p1, e1 = count_populated(position_fields, "PRICE POSITION")
p2, e2 = count_populated(sr_fields, "SUPPORT/RESISTANCE")
p3, e3 = count_populated(liquidity_fields, "LIQUIDITY")
p4, e4 = count_populated(ob_fields, "ORDER BLOCKS")
p5, e5 = count_populated(fvg_fields, "FAIR VALUE GAPS")
p6, e6 = count_populated(displacement_fields, "DISPLACEMENT")
p7, e7 = count_populated(exec_fields, "EXECUTION")

total_pop = p1+p2+p3+p4+p5+p6+p7
total_empty = e1+e2+e3+e4+e5+e6+e7
print(f"\n  SUMMARY: {total_pop} fields with data, {total_empty} fields always empty")
print()

# ═══════════════════════════════════════════════════════════════════
# 3. MARKET LOCATION ANALYSIS
# ═══════════════════════════════════════════════════════════════════
print("3. MARKET LOCATION ANALYSIS")
print("-" * 40)

# H4 range position distribution
h4_pos = [r.get("h4_range_position", 0) for r in records if r.get("h4_range_position", 0) > 0]
if h4_pos:
    premium = sum(1 for p in h4_pos if p > 0.7)
    discount = sum(1 for p in h4_pos if p < 0.3)
    equilibrium = sum(1 for p in h4_pos if 0.3 <= p <= 0.7)
    print(f"H4 range position (n={len(h4_pos)}):")
    print(f"  Premium (>0.7):     {premium:3d} ({premium/len(h4_pos)*100:.1f}%)")
    print(f"  Equilibrium (0.3-0.7): {equilibrium:3d} ({equilibrium/len(h4_pos)*100:.1f}%)")
    print(f"  Discount (<0.3):    {discount:3d} ({discount/len(h4_pos)*100:.1f}%)")
    print(f"  Mean position: {sum(h4_pos)/len(h4_pos):.3f}")
else:
    print("H4 range position: NO DATA")

h1_pos = [r.get("h1_range_position", 0) for r in records if r.get("h1_range_position", 0) > 0]
if h1_pos:
    premium = sum(1 for p in h1_pos if p > 0.7)
    discount = sum(1 for p in h1_pos if p < 0.3)
    equilibrium = sum(1 for p in h1_pos if 0.3 <= p <= 0.7)
    print(f"\nH1 range position (n={len(h1_pos)}):")
    print(f"  Premium (>0.7):     {premium:3d} ({premium/len(h1_pos)*100:.1f}%)")
    print(f"  Equilibrium (0.3-0.7): {equilibrium:3d} ({equilibrium/len(h1_pos)*100:.1f}%)")
    print(f"  Discount (<0.3):    {discount:3d} ({discount/len(h1_pos)*100:.1f}%)")
else:
    print("\nH1 range position: NO DATA")

# Support/resistance distances
sup_dist = [r.get("nearest_support_distance_pips", 0) for r in records if r.get("nearest_support_distance_pips", 0) > 0]
res_dist = [r.get("nearest_resistance_distance_pips", 0) for r in records if r.get("nearest_resistance_distance_pips", 0) > 0]
if sup_dist:
    print(f"\nNearest support distance (n={len(sup_dist)}):")
    print(f"  Mean: {sum(sup_dist)/len(sup_dist):.1f} pips")
    print(f"  Within 5 pips: {sum(1 for d in sup_dist if d < 5)} records")
    print(f"  Within 10 pips: {sum(1 for d in sup_dist if d < 10)} records")
if res_dist:
    print(f"\nNearest resistance distance (n={len(res_dist)}):")
    print(f"  Mean: {sum(res_dist)/len(res_dist):.1f} pips")
    print(f"  Within 5 pips: {sum(1 for d in res_dist if d < 5)} records")
    print(f"  Within 10 pips: {sum(1 for d in res_dist if d < 10)} records")
print()

# ═══════════════════════════════════════════════════════════════════
# 4. LIQUIDITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════
print("4. LIQUIDITY ANALYSIS")
print("-" * 40)
eq_highs = sum(1 for r in records if r.get("equal_highs_above"))
eq_lows = sum(1 for r in records if r.get("equal_lows_below"))
sweeps = sum(1 for r in records if r.get("liquidity_sweep_just_occurred"))
sweep_dirs = Counter(r.get("sweep_direction", "") for r in records if r.get("liquidity_sweep_just_occurred"))
print(f"Equal highs above detected: {eq_highs}/{len(records)}")
print(f"Equal lows below detected: {eq_lows}/{len(records)}")
print(f"Liquidity sweeps detected: {sweeps}/{len(records)}")
if sweep_dirs:
    print(f"Sweep directions: {dict(sweep_dirs)}")

prev_session_swept_h = sum(1 for r in records if r.get("prev_session_high_swept"))
prev_session_swept_l = sum(1 for r in records if r.get("prev_session_low_swept"))
prev_day_swept_h = sum(1 for r in records if r.get("prev_day_high_swept"))
prev_day_swept_l = sum(1 for r in records if r.get("prev_day_low_swept"))
print(f"Prev session high swept: {prev_session_swept_h}")
print(f"Prev session low swept: {prev_session_swept_l}")
print(f"Prev day high swept: {prev_day_swept_h}")
print(f"Prev day low swept: {prev_day_swept_l}")
print()

# ═══════════════════════════════════════════════════════════════════
# 5. STRUCTURE ANALYSIS
# ═══════════════════════════════════════════════════════════════════
print("5. STRUCTURE ANALYSIS")
print("-" * 40)
bos_detected = sum(1 for r in records if r.get("h1_last_bos_price", 0) > 0)
inside_ob = sum(1 for r in records if r.get("price_inside_ob"))
inside_fvg = sum(1 for r in records if r.get("price_inside_fvg"))
displacement = sum(1 for r in records if r.get("displacement_into_level"))
rejection = sum(1 for r in records if r.get("rejection_candle_present"))

print(f"H1 BOS price available: {bos_detected}/{len(records)}")
print(f"Price inside order block: {inside_ob}/{len(records)}")
print(f"Price inside FVG: {inside_fvg}/{len(records)}")
print(f"Displacement into level: {displacement}/{len(records)}")
print(f"Rejection candle present: {rejection}/{len(records)}")

# Displacement magnitude
disp_mag = [r.get("displacement_magnitude_atr", 0) for r in records if r.get("displacement_into_level")]
if disp_mag:
    print(f"\nDisplacement magnitude (n={len(disp_mag)}):")
    print(f"  Mean: {sum(disp_mag)/len(disp_mag):.2f} ATR")
    print(f"  Max: {max(disp_mag):.2f} ATR")

# Rejection stats
rej_wick = [r.get("rejection_wick_atr_ratio", 0) for r in records if r.get("rejection_candle_present")]
if rej_wick:
    print(f"\nRejection wick/ATR (n={len(rej_wick)}):")
    print(f"  Mean: {sum(rej_wick)/len(rej_wick):.3f}")
print()

# ═══════════════════════════════════════════════════════════════════
# 6. SPREAD AND EXECUTION
# ═══════════════════════════════════════════════════════════════════
print("6. EXECUTION CONTEXT")
print("-" * 40)
spreads = [r.get("spread", 0) for r in records if r.get("spread", 0) > 0]
spread_risk = [r.get("spread_risk_ratio", 0) for r in records if r.get("spread_risk_ratio", 0) > 0]
atrs = [r.get("atr", 0) for r in records if r.get("atr", 0) > 0]

if spreads:
    print(f"Spread (n={len(spreads)}): mean={sum(spreads)/len(spreads):.6f}")
if spread_risk:
    print(f"Spread/risk ratio (n={len(spread_risk)}): mean={sum(spread_risk)/len(spread_risk):.4f}")
if atrs:
    print(f"ATR (n={len(atrs)}): mean={sum(atrs)/len(atrs):.6f}")
print()

# ═══════════════════════════════════════════════════════════════════
# 7. FIELD POPULATION SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("7. FIELD POPULATION SUMMARY")
print("-" * 40)
all_fields = position_fields + sr_fields + liquidity_fields + ob_fields + fvg_fields + displacement_fields + exec_fields
populated_fields = []
empty_fields = []
for field in all_fields:
    non_zero = sum(1 for r in records if r.get(field) not in (0, 0.0, "", False, None))
    if non_zero > 0:
        populated_fields.append((field, non_zero))
    else:
        empty_fields.append(field)

print(f"Fields with data: {len(populated_fields)}/{len(all_fields)}")
print(f"Fields always empty: {len(empty_fields)}/{len(all_fields)}")
if empty_fields:
    print(f"\nAlways-empty fields ({len(empty_fields)}):")
    for f in empty_fields:
        print(f"  - {f}")
print()
print("=" * 70)
print("END OF V3 EARLY ANALYSIS")
print("=" * 70)
