"""
V3 Discovery Engine — First Research Pass.

Tests whether V3 market context features (location, liquidity) have predictive value.
Uses only linked V3 observations with outcomes.
"""

import json
import math
from pathlib import Path
from collections import Counter

# ═══════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════

v3_dir = Path("logs/v3_opportunities")
records = []
if v3_dir.exists():
    for f in v3_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        rec = json.loads(line)
                        if rec.get("outcome_linked") or rec.get("_linkage", {}).get("linked"):
                            records.append(rec)
                    except:
                        pass

print("=" * 70)
print("V3 DISCOVERY ENGINE — FIRST RESEARCH PASS")
print("=" * 70)
print(f"\nLinked records loaded: {len(records)}")


def get_r(rec):
    """Extract outcome R from record."""
    r = rec.get("outcome_raw_r")
    if r is not None:
        return float(r)
    linkage = rec.get("_linkage", {})
    r = linkage.get("result_r")
    if r is not None:
        return float(r)
    return None


def stats(subset, label=""):
    """Compute stats for a subset."""
    outcomes = [get_r(r) for r in subset if get_r(r) is not None]
    if not outcomes:
        return {"n": 0, "wr": 0, "ev": 0, "mfe": 0, "mae": 0, "std": 0}
    n = len(outcomes)
    wins = sum(1 for o in outcomes if o > 0)
    wr = wins / n
    ev = sum(outcomes) / n
    std = math.sqrt(sum((o - ev) ** 2 for o in outcomes) / max(n - 1, 1))
    se = std / math.sqrt(n) if n > 0 else 0
    ci_low = ev - 1.96 * se
    ci_high = ev + 1.96 * se

    mfe_vals = [r.get("outcome_mfe_r", 0) or 0 for r in subset if get_r(r) is not None]
    mae_vals = [r.get("outcome_mae_r", 0) or 0 for r in subset if get_r(r) is not None]
    avg_mfe = sum(mfe_vals) / len(mfe_vals) if mfe_vals else 0
    avg_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0

    return {
        "n": n, "wr": wr, "ev": ev, "std": std,
        "ci_low": ci_low, "ci_high": ci_high,
        "mfe": avg_mfe, "mae": avg_mae,
    }


def print_stats(s, label):
    """Print stats line."""
    if s["n"] == 0:
        print(f"  {label:30s} | n=0 (no data)")
        return
    sig = "***" if s["ci_low"] > 0 else "**" if s["ev"] > 0 else ""
    print(f"  {label:30s} | n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}R | MFE={s['mfe']:.3f} | MAE={s['mae']:.3f} | 95%CI=[{s['ci_low']:+.3f}, {s['ci_high']:+.3f}] {sig}")


# ═══════════════════════════════════════════════════════════════════
# BASELINE
# ═══════════════════════════════════════════════════════════════════

baseline = stats(records, "ALL")
print(f"\nBASELINE (all linked records):")
print_stats(baseline, "All trades")

# ═══════════════════════════════════════════════════════════════════
# RQ1: MARKET LOCATION
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("RQ1: DOES MARKET LOCATION IMPROVE EXPECTANCY?")
print("=" * 70)

# M15 Range Position
print("\n--- M15 Range Position ---")
m15_pos = [(r, r.get("m15_range_position", 0)) for r in records if r.get("m15_range_position", 0) > 0]
if m15_pos:
    discount = [r for r, p in m15_pos if p < 0.33]
    mid_range = [r for r, p in m15_pos if 0.33 <= p <= 0.67]
    premium = [r for r, p in m15_pos if p > 0.67]

    print_stats(stats(discount), "Discount (<0.33)")
    print_stats(stats(mid_range), "Mid-range (0.33-0.67)")
    print_stats(stats(premium), "Premium (>0.67)")
else:
    print("  No M15 range_position data available")

# H1 Range Position
print("\n--- H1 Range Position ---")
h1_pos = [(r, r.get("h1_range_position", 0)) for r in records if r.get("h1_range_position", 0) > 0]
if h1_pos:
    discount = [r for r, p in h1_pos if p < 0.33]
    mid_range = [r for r, p in h1_pos if 0.33 <= p <= 0.67]
    premium = [r for r, p in h1_pos if p > 0.67]

    print_stats(stats(discount), "Discount (<0.33)")
    print_stats(stats(mid_range), "Mid-range (0.33-0.67)")
    print_stats(stats(premium), "Premium (>0.67)")
else:
    print("  No H1 range_position data available")

# Distance from swing levels
print("\n--- Distance from H1 Swing Low (pips) ---")
h1_dist_low = [(r, r.get("h1_distance_from_low_pips", 0)) for r in records if r.get("h1_distance_from_low_pips", 0) > 0]
if len(h1_dist_low) >= 20:
    near_low = [r for r, d in h1_dist_low if d < 15]
    mid_low = [r for r, d in h1_dist_low if 15 <= d < 40]
    far_low = [r for r, d in h1_dist_low if d >= 40]

    print_stats(stats(near_low), "Near low (<15 pips)")
    print_stats(stats(mid_low), "Mid (15-40 pips)")
    print_stats(stats(far_low), "Far from low (>40 pips)")

print("\n--- Distance from H1 Swing High (pips) ---")
h1_dist_high = [(r, r.get("h1_distance_from_high_pips", 0)) for r in records if r.get("h1_distance_from_high_pips", 0) > 0]
if len(h1_dist_high) >= 20:
    near_high = [r for r, d in h1_dist_high if d < 15]
    mid_high = [r for r, d in h1_dist_high if 15 <= d < 40]
    far_high = [r for r, d in h1_dist_high if d >= 40]

    print_stats(stats(near_high), "Near high (<15 pips)")
    print_stats(stats(mid_high), "Mid (15-40 pips)")
    print_stats(stats(far_high), "Far from high (>40 pips)")

# ═══════════════════════════════════════════════════════════════════
# RQ2: LIQUIDITY FEATURES
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("RQ2: DOES NEARBY LIQUIDITY IMPROVE OUTCOME PREDICTION?")
print("=" * 70)

# Equal Highs
print("\n--- Equal Highs Above ---")
with_eq_h = [r for r in records if r.get("equal_highs_above")]
without_eq_h = [r for r in records if not r.get("equal_highs_above")]
print_stats(stats(with_eq_h), "Equal highs PRESENT")
print_stats(stats(without_eq_h), "Equal highs ABSENT")

# Equal Lows
print("\n--- Equal Lows Below ---")
with_eq_l = [r for r in records if r.get("equal_lows_below")]
without_eq_l = [r for r in records if not r.get("equal_lows_below")]
print_stats(stats(with_eq_l), "Equal lows PRESENT")
print_stats(stats(without_eq_l), "Equal lows ABSENT")

# Previous Session High
print("\n--- Previous Session High ---")
with_psh = [r for r in records if r.get("prev_session_high", 0) > 0]
without_psh = [r for r in records if not r.get("prev_session_high", 0)]
print_stats(stats(with_psh), "Session high AVAILABLE")
print_stats(stats(without_psh), "Session high UNAVAILABLE")

# Session High Swept
print("\n--- Session High Swept ---")
swept_h = [r for r in records if r.get("prev_session_high_swept")]
not_swept_h = [r for r in records if r.get("prev_session_high", 0) > 0 and not r.get("prev_session_high_swept")]
print_stats(stats(swept_h), "Session high SWEPT")
print_stats(stats(not_swept_h), "Session high NOT swept")

# Session Low Swept
print("\n--- Session Low Swept ---")
swept_l = [r for r in records if r.get("prev_session_low_swept")]
not_swept_l = [r for r in records if r.get("prev_session_low", 0) > 0 and not r.get("prev_session_low_swept")]
print_stats(stats(swept_l), "Session low SWEPT")
print_stats(stats(not_swept_l), "Session low NOT swept")

# Liquidity Sweep
print("\n--- Liquidity Sweep Just Occurred ---")
with_sweep = [r for r in records if r.get("liquidity_sweep_just_occurred")]
without_sweep = [r for r in records if not r.get("liquidity_sweep_just_occurred")]
print_stats(stats(with_sweep), "Sweep DETECTED")
print_stats(stats(without_sweep), "No sweep")

# ═══════════════════════════════════════════════════════════════════
# RQ2B: FVG AND ORDER BLOCKS
# ═══════════════════════════════════════════════════════════════════

print("\n--- FVG Present (above or below) ---")
with_fvg = [r for r in records if r.get("total_unfilled_fvgs_above", 0) > 0 or r.get("total_unfilled_fvgs_below", 0) > 0]
without_fvg = [r for r in records if r.get("total_unfilled_fvgs_above", 0) == 0 and r.get("total_unfilled_fvgs_below", 0) == 0]
print_stats(stats(with_fvg), "FVG present")
print_stats(stats(without_fvg), "No FVG")

print("\n--- Demand Order Block Present ---")
with_demand = [r for r in records if r.get("nearest_demand_ob_price", 0) > 0]
without_demand = [r for r in records if not r.get("nearest_demand_ob_price", 0)]
print_stats(stats(with_demand), "Demand OB PRESENT")
print_stats(stats(without_demand), "No demand OB")

print("\n--- Supply Order Block Present ---")
with_supply = [r for r in records if r.get("nearest_supply_ob_price", 0) > 0]
without_supply = [r for r in records if not r.get("nearest_supply_ob_price", 0)]
print_stats(stats(with_supply), "Supply OB PRESENT")
print_stats(stats(without_supply), "No supply OB")

# Rejection candle
print("\n--- Rejection Candle ---")
with_rej = [r for r in records if r.get("rejection_candle_present")]
without_rej = [r for r in records if not r.get("rejection_candle_present")]
print_stats(stats(with_rej), "Rejection PRESENT")
print_stats(stats(without_rej), "No rejection")

# ═══════════════════════════════════════════════════════════════════
# RQ3: COMBINATIONS
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("RQ3: DOES COMBINING LOCATION + LIQUIDITY IMPROVE PREDICTION?")
print("=" * 70)

# Combo 1: Equal lows + discount zone
print("\n--- Equal Lows + Discount (M15 range < 0.33) ---")
combo1 = [r for r in records if r.get("equal_lows_below") and r.get("m15_range_position", 0) > 0 and r.get("m15_range_position", 0) < 0.33]
print_stats(stats(combo1), "Eq lows + discount")
print_stats(stats(records), "Baseline (all)")

# Combo 2: Equal highs + premium zone
print("\n--- Equal Highs + Premium (M15 range > 0.67) ---")
combo2 = [r for r in records if r.get("equal_highs_above") and r.get("m15_range_position", 0) > 0.67]
print_stats(stats(combo2), "Eq highs + premium")

# Combo 3: OB present + session extremes
print("\n--- Demand OB + Session Low Available ---")
combo3 = [r for r in records if r.get("nearest_demand_ob_price", 0) > 0 and r.get("prev_session_low", 0) > 0]
print_stats(stats(combo3), "Demand OB + session low")

# Combo 4: FVG + OB
print("\n--- FVG Present + OB Present ---")
combo4 = [r for r in records if (r.get("total_unfilled_fvgs_above", 0) > 0 or r.get("total_unfilled_fvgs_below", 0) > 0) and (r.get("nearest_demand_ob_price", 0) > 0 or r.get("nearest_supply_ob_price", 0) > 0)]
print_stats(stats(combo4), "FVG + OB")

# Combo 5: Rejection + near structure
print("\n--- Rejection Candle + Near Support (M15) ---")
combo5 = [r for r in records if r.get("rejection_candle_present") and r.get("nearest_support_distance_pips", 99) < 10]
print_stats(stats(combo5), "Rejection + near support")

# ═══════════════════════════════════════════════════════════════════
# FEATURE POPULATION SUMMARY
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("FEATURE POPULATION SUMMARY")
print("=" * 70)

key_features = [
    ("m15_range_position", "> 0"),
    ("h1_range_position", "> 0"),
    ("equal_highs_above", "True"),
    ("equal_lows_below", "True"),
    ("prev_session_high", "> 0"),
    ("prev_session_low", "> 0"),
    ("prev_session_high_swept", "True"),
    ("prev_session_low_swept", "True"),
    ("liquidity_sweep_just_occurred", "True"),
    ("nearest_fvg_above_price", "> 0"),
    ("nearest_fvg_below_price", "> 0"),
    ("nearest_demand_ob_price", "> 0"),
    ("nearest_supply_ob_price", "> 0"),
    ("rejection_candle_present", "True"),
    ("displacement_into_level", "True"),
    ("atr", "> 0"),
]

print(f"\n  {'Feature':<35s} | {'Events':>6s} | {'Linked':>6s} | {'%Pop':>5s} | {'Research?'}")
print(f"  {'-'*35}-+-{'-'*6}-+-{'-'*6}-+-{'-'*5}-+-{'-'*12}")
for feat, condition in key_features:
    if condition == "True":
        events = sum(1 for r in records if r.get(feat))
    else:
        events = sum(1 for r in records if (r.get(feat, 0) or 0) > 0)
    pct = events / len(records) * 100 if records else 0
    ready = "YES" if events >= 50 else "ALMOST" if events >= 30 else "NO"
    print(f"  {feat:<35s} | {events:>6d} | {events:>6d} | {pct:>4.1f}% | {ready}")

# ═══════════════════════════════════════════════════════════════════
# COST-ADJUSTED VIEW
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("COST-ADJUSTED COMPARISON (spread cost = 0.48R)")
print("=" * 70)
SPREAD_COST = 0.48

print(f"\n  {'Feature':<35s} | {'n':>4s} | {'Raw EV':>8s} | {'Adj EV':>8s} | {'Signal?'}")
print(f"  {'-'*35}-+-{'-'*4}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

comparisons = [
    ("Baseline (all)", records),
    ("Equal highs PRESENT", with_eq_h),
    ("Equal lows PRESENT", with_eq_l),
    ("Session high available", with_psh),
    ("Session high SWEPT", swept_h),
    ("FVG present", with_fvg),
    ("Demand OB present", with_demand),
    ("Supply OB present", with_supply),
    ("Rejection candle", with_rej),
]

for label, subset in comparisons:
    s = stats(subset)
    if s["n"] > 0:
        adj = s["ev"] - SPREAD_COST
        signal = "+" if adj > 0 else "-"
        print(f"  {label:<35s} | {s['n']:>4d} | {s['ev']:>+7.4f} | {adj:>+7.4f} | {signal}")

print("\n" + "=" * 70)
print("END OF V3 DISCOVERY PASS")
print("=" * 70)
