"""
V3 Discovery Pass 2 — Uniform Post-Phase-2 Dataset Only.

Filters to ONLY observations with populated V3 Phase-2 features
(liquidity, FVG, OB detectors active) to avoid composition artefact.
"""

import json
import math
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# LOAD AND FILTER DATA
# ═══════════════════════════════════════════════════════════════════

v3_dir = Path("logs/v3_opportunities")
all_records = []
if v3_dir.exists():
    for f in v3_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        all_records.append(json.loads(line))
                    except:
                        pass

# Filter: only post-Phase-2 records (have at least one detector field populated)
def is_post_phase2(r):
    """Record has Phase-2 detector data (liquidity/FVG/OB active)."""
    # Must have ATR (Phase 1 fix)
    if not r.get("atr", 0):
        return False
    # Must have at least ONE of: liquidity detection ran, FVG detection ran, OB detection ran
    # These fire even if they find nothing — check for session data (always available post-P2)
    if r.get("prev_session_high", 0) > 0 or r.get("prev_session_low", 0) > 0:
        return True
    if r.get("equal_highs_above") or r.get("equal_lows_below"):
        return True
    if r.get("nearest_fvg_above_price", 0) > 0 or r.get("nearest_fvg_below_price", 0) > 0:
        return True
    if r.get("nearest_demand_ob_price", 0) > 0 or r.get("nearest_supply_ob_price", 0) > 0:
        return True
    if r.get("consolidation_range_pips", 0) > 0:
        return True
    return False

post_phase2 = [r for r in all_records if is_post_phase2(r)]
linked = [r for r in post_phase2 if r.get("outcome_linked") or r.get("_linkage", {}).get("linked")]
excluded = len(all_records) - len(post_phase2)

print("=" * 70)
print("V3 DISCOVERY PASS 2 — POST-PHASE-2 UNIFORM DATASET")
print("=" * 70)
print(f"\nTotal V3 observations:      {len(all_records)}")
print(f"Post-Phase-2 (filtered):    {len(post_phase2)}")
print(f"Excluded (legacy/pre-P2):   {excluded}")
print(f"With linked outcomes:       {len(linked)}")
print(f"Analysis dataset:           {len(linked)} records")


def get_r(rec):
    r = rec.get("outcome_raw_r")
    if r is not None:
        return float(r)
    linkage = rec.get("_linkage", {})
    r = linkage.get("result_r")
    if r is not None:
        return float(r)
    return None


def compute_stats(subset):
    outcomes = [get_r(r) for r in subset if get_r(r) is not None]
    if not outcomes:
        return None
    n = len(outcomes)
    wins = sum(1 for o in outcomes if o > 0)
    wr = wins / n
    ev = sum(outcomes) / n
    std = math.sqrt(sum((o - ev) ** 2 for o in outcomes) / max(n - 1, 1))
    se = std / math.sqrt(n) if n > 0 else 0
    ci_low = ev - 1.96 * se
    ci_high = ev + 1.96 * se
    mfe_vals = [r.get("outcome_mfe_r") or 0 for r in subset if get_r(r) is not None]
    mae_vals = [r.get("outcome_mae_r") or 0 for r in subset if get_r(r) is not None]
    return {
        "n": n, "wr": wr, "ev": ev, "std": std,
        "ci_low": ci_low, "ci_high": ci_high,
        "mfe": sum(mfe_vals)/len(mfe_vals) if mfe_vals else 0,
        "mae": sum(mae_vals)/len(mae_vals) if mae_vals else 0,
    }


def fmt(s, label):
    if s is None or s["n"] == 0:
        print(f"  {label:35s} | n=0 — NO DATA")
        return
    sig = ""
    if s["n"] >= 10:
        if s["ci_low"] > 0:
            sig = " *** POSITIVE"
        elif s["ci_high"] < 0:
            sig = " *** NEGATIVE"
    print(f"  {label:35s} | n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}R | std={s['std']:.3f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}] | MFE={s['mfe']:.3f} MAE={s['mae']:.3f}{sig}")


if not linked:
    print("\n  *** NO LINKED POST-PHASE-2 RECORDS — CANNOT ANALYSE ***")
    print("  Action: Run bot during market hours, then re-link outcomes.")
    exit()

# ═══════════════════════════════════════════════════════════════════
# BASELINE
# ═══════════════════════════════════════════════════════════════════
baseline = compute_stats(linked)
print(f"\nBASELINE (post-Phase-2, linked):")
fmt(baseline, "All post-P2 trades")

# ═══════════════════════════════════════════════════════════════════
# RQ1: MARKET LOCATION
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RQ1: MARKET LOCATION")
print("=" * 70)

# M15 Range Position
print("\n--- M15 Range Position ---")
m15_data = [(r, r.get("m15_range_position", 0)) for r in linked if r.get("m15_range_position", 0) > 0]
if m15_data:
    discount = [r for r, p in m15_data if p < 0.33]
    mid = [r for r, p in m15_data if 0.33 <= p <= 0.67]
    premium = [r for r, p in m15_data if p > 0.67]
    fmt(compute_stats(discount), "Discount (<0.33)")
    fmt(compute_stats(mid), "Mid-range (0.33-0.67)")
    fmt(compute_stats(premium), "Premium (>0.67)")
    print(f"  [Total with M15 position: {len(m15_data)}]")
else:
    print("  No M15 range_position data in post-P2 set")

# H1 Range Position
print("\n--- H1 Range Position ---")
h1_data = [(r, r.get("h1_range_position", 0)) for r in linked if r.get("h1_range_position", 0) > 0]
if h1_data:
    discount = [r for r, p in h1_data if p < 0.33]
    mid = [r for r, p in h1_data if 0.33 <= p <= 0.67]
    premium = [r for r, p in h1_data if p > 0.67]
    fmt(compute_stats(discount), "Discount (<0.33)")
    fmt(compute_stats(mid), "Mid-range (0.33-0.67)")
    fmt(compute_stats(premium), "Premium (>0.67)")
    print(f"  [Total with H1 position: {len(h1_data)}]")
else:
    print("  No H1 range_position data in post-P2 set")

# Support/Resistance distance
print("\n--- Distance to Nearest Support ---")
sup_data = [(r, r.get("nearest_support_distance_pips", 0)) for r in linked if r.get("nearest_support_distance_pips", 0) > 0]
if len(sup_data) >= 10:
    near = [r for r, d in sup_data if d < 5]
    mid_d = [r for r, d in sup_data if 5 <= d < 15]
    far = [r for r, d in sup_data if d >= 15]
    fmt(compute_stats(near), "Near support (<5 pips)")
    fmt(compute_stats(mid_d), "Mid (5-15 pips)")
    fmt(compute_stats(far), "Far from support (>15 pips)")

# ═══════════════════════════════════════════════════════════════════
# RQ2: LIQUIDITY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RQ2: LIQUIDITY CONTEXT")
print("=" * 70)

print("\n--- Equal Highs Above ---")
eq_h_yes = [r for r in linked if r.get("equal_highs_above")]
eq_h_no = [r for r in linked if not r.get("equal_highs_above")]
fmt(compute_stats(eq_h_yes), "Equal highs PRESENT")
fmt(compute_stats(eq_h_no), "Equal highs ABSENT")

print("\n--- Equal Lows Below ---")
eq_l_yes = [r for r in linked if r.get("equal_lows_below")]
eq_l_no = [r for r in linked if not r.get("equal_lows_below")]
fmt(compute_stats(eq_l_yes), "Equal lows PRESENT")
fmt(compute_stats(eq_l_no), "Equal lows ABSENT")

print("\n--- Previous Session High ---")
psh_yes = [r for r in linked if r.get("prev_session_high", 0) > 0]
psh_no = [r for r in linked if not r.get("prev_session_high", 0)]
fmt(compute_stats(psh_yes), "Session high available")
fmt(compute_stats(psh_no), "Session high unavailable")

print("\n--- Session High Swept ---")
swept_h = [r for r in linked if r.get("prev_session_high_swept")]
not_swept_h = [r for r in linked if r.get("prev_session_high", 0) > 0 and not r.get("prev_session_high_swept")]
fmt(compute_stats(swept_h), "High SWEPT")
fmt(compute_stats(not_swept_h), "High NOT swept")

print("\n--- Session Low Swept ---")
swept_l = [r for r in linked if r.get("prev_session_low_swept")]
not_swept_l = [r for r in linked if r.get("prev_session_low", 0) > 0 and not r.get("prev_session_low_swept")]
fmt(compute_stats(swept_l), "Low SWEPT")
fmt(compute_stats(not_swept_l), "Low NOT swept")

print("\n--- Liquidity Sweep Occurred ---")
sweep_yes = [r for r in linked if r.get("liquidity_sweep_just_occurred")]
sweep_no = [r for r in linked if not r.get("liquidity_sweep_just_occurred")]
fmt(compute_stats(sweep_yes), "Sweep YES")
fmt(compute_stats(sweep_no), "No sweep")

# ═══════════════════════════════════════════════════════════════════
# RQ3: FAIR VALUE GAPS
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RQ3: FAIR VALUE GAPS")
print("=" * 70)

fvg_above = [r for r in linked if r.get("total_unfilled_fvgs_above", 0) > 0]
fvg_below = [r for r in linked if r.get("total_unfilled_fvgs_below", 0) > 0]
fvg_any = [r for r in linked if r.get("total_unfilled_fvgs_above", 0) > 0 or r.get("total_unfilled_fvgs_below", 0) > 0]
fvg_none = [r for r in linked if r.get("total_unfilled_fvgs_above", 0) == 0 and r.get("total_unfilled_fvgs_below", 0) == 0]
inside_fvg = [r for r in linked if r.get("price_inside_fvg")]

print("\n--- FVG Presence ---")
fmt(compute_stats(fvg_any), "Any FVG present")
fmt(compute_stats(fvg_none), "No FVG")
fmt(compute_stats(fvg_above), "FVG above price")
fmt(compute_stats(fvg_below), "FVG below price")
fmt(compute_stats(inside_fvg), "Price INSIDE FVG")

# ═══════════════════════════════════════════════════════════════════
# RQ4: ORDER BLOCKS
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RQ4: ORDER BLOCKS")
print("=" * 70)

demand_yes = [r for r in linked if r.get("nearest_demand_ob_price", 0) > 0]
demand_no = [r for r in linked if not r.get("nearest_demand_ob_price", 0)]
supply_yes = [r for r in linked if r.get("nearest_supply_ob_price", 0) > 0]
supply_no = [r for r in linked if not r.get("nearest_supply_ob_price", 0)]
ob_any = [r for r in linked if r.get("nearest_demand_ob_price", 0) > 0 or r.get("nearest_supply_ob_price", 0) > 0]
inside_ob = [r for r in linked if r.get("price_inside_ob")]

print("\n--- Order Block Presence ---")
fmt(compute_stats(demand_yes), "Demand OB present")
fmt(compute_stats(demand_no), "No demand OB")
fmt(compute_stats(supply_yes), "Supply OB present")
fmt(compute_stats(supply_no), "No supply OB")
fmt(compute_stats(ob_any), "Any OB present")
fmt(compute_stats(inside_ob), "Price INSIDE OB")

# OB Strength
print("\n--- OB Strength (demand) ---")
strong_ob = [r for r in demand_yes if r.get("demand_ob_strength", 0) > 0.6]
weak_ob = [r for r in demand_yes if r.get("demand_ob_strength", 0) <= 0.6]
fmt(compute_stats(strong_ob), "Strong demand OB (>0.6)")
fmt(compute_stats(weak_ob), "Weak demand OB (<=0.6)")

# Mitigated vs fresh
mitigated = [r for r in ob_any if r.get("demand_ob_mitigated") or r.get("supply_ob_mitigated")]
fresh = [r for r in ob_any if not r.get("demand_ob_mitigated") and not r.get("supply_ob_mitigated")]
fmt(compute_stats(mitigated), "Mitigated OB")
fmt(compute_stats(fresh), "Fresh (unmitigated) OB")

# ═══════════════════════════════════════════════════════════════════
# RQ5: FEATURE RANKING
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RQ5: FEATURE RANKING BY EFFECT SIZE")
print("=" * 70)

features_to_rank = []

def add_rank(name, present_subset, absent_subset):
    s_p = compute_stats(present_subset)
    s_a = compute_stats(absent_subset)
    if s_p and s_a and s_p["n"] >= 3 and s_a["n"] >= 3:
        effect = s_p["ev"] - s_a["ev"]
        features_to_rank.append({
            "name": name,
            "n_present": s_p["n"],
            "ev_present": s_p["ev"],
            "n_absent": s_a["n"],
            "ev_absent": s_a["ev"],
            "effect": effect,
            "wr_present": s_p["wr"],
            "sufficient": s_p["n"] >= 20,
        })

add_rank("equal_highs_above", eq_h_yes, eq_h_no)
add_rank("equal_lows_below", eq_l_yes, eq_l_no)
add_rank("prev_session_high", psh_yes, psh_no)
add_rank("fvg_any", fvg_any, fvg_none)
add_rank("demand_ob", demand_yes, demand_no)
add_rank("supply_ob", supply_yes, supply_no)
add_rank("session_high_swept", swept_h, not_swept_h)
add_rank("session_low_swept", swept_l, not_swept_l)

# Rejection
rej_yes = [r for r in linked if r.get("rejection_candle_present")]
rej_no = [r for r in linked if not r.get("rejection_candle_present")]
add_rank("rejection_candle", rej_yes, rej_no)

# Sort by absolute effect size
features_to_rank.sort(key=lambda f: abs(f["effect"]), reverse=True)

print(f"\n  {'Feature':<25s} | {'n_pres':>6s} | {'EV_pres':>8s} | {'n_abs':>5s} | {'EV_abs':>8s} | {'Effect':>8s} | {'WR_pres':>7s} | {'Status'}")
print(f"  {'-'*25}-+-{'-'*6}-+-{'-'*8}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*7}-+-{'-'*12}")
for f in features_to_rank:
    status = "GREEN" if f["effect"] > 0.2 and f["sufficient"] else \
             "YELLOW" if f["effect"] > 0.1 or f["n_present"] >= 10 else \
             "GRAY" if f["n_present"] < 10 else "RED"
    print(f"  {f['name']:<25s} | {f['n_present']:>6d} | {f['ev_present']:>+7.4f} | {f['n_absent']:>5d} | {f['ev_absent']:>+7.4f} | {f['effect']:>+7.4f} | {f['wr_present']:>6.1%} | {status}")

# ═══════════════════════════════════════════════════════════════════
# FEATURE POPULATION TABLE
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("FEATURE POPULATION (post-Phase-2 linked set)")
print("=" * 70)

pop_fields = [
    "m15_range_position", "h1_range_position", "atr",
    "equal_highs_above", "equal_lows_below",
    "prev_session_high", "prev_session_low",
    "prev_session_high_swept", "prev_session_low_swept",
    "liquidity_sweep_just_occurred",
    "nearest_fvg_above_price", "nearest_fvg_below_price",
    "price_inside_fvg",
    "nearest_demand_ob_price", "nearest_supply_ob_price",
    "price_inside_ob",
    "rejection_candle_present", "displacement_into_level",
    "consolidation_range_pips",
]

print(f"\n  {'Field':<35s} | {'Pop':>4s} | {'Total':>5s} | {'%':>5s} | {'Ready?'}")
print(f"  {'-'*35}-+-{'-'*4}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}")
for field in pop_fields:
    if field in ("equal_highs_above", "equal_lows_below", "prev_session_high_swept",
                 "prev_session_low_swept", "liquidity_sweep_just_occurred",
                 "price_inside_fvg", "price_inside_ob",
                 "rejection_candle_present", "displacement_into_level"):
        pop = sum(1 for r in linked if r.get(field))
    else:
        pop = sum(1 for r in linked if (r.get(field, 0) or 0) > 0)
    pct = pop / len(linked) * 100 if linked else 0
    ready = "YES" if pop >= 50 else "ALMOST" if pop >= 20 else "NO"
    print(f"  {field:<35s} | {pop:>4d} | {len(linked):>5d} | {pct:>4.1f}% | {ready}")

print("\n" + "=" * 70)
print("END OF V3 DISCOVERY PASS 2")
print("=" * 70)
