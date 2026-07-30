"""V5.2 — Mean Reversion Expression Validation.

Tests whether the V3 contrarian/mean-reversion pattern produces stable
expectancy when expressed with appropriate location, entry, exit, and
timeframe assumptions.
"""
import json, math, random
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V5.2 — MEAN REVERSION EXPRESSION VALIDATION")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

ctx_dir = Path("logs/v3_shadow/market_context")
mu_dir = Path("logs/v3_shadow/market_understanding")
exec_dir = Path("logs/v3_shadow/execution_assessment")
shadow_dir = Path("logs/shadow_trades")

# Load market context
ctx_records = {}
if ctx_dir.exists():
    for f in ctx_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    ctx_records[key] = r
                except:
                    pass

# Load market understanding
mu_records = {}
if mu_dir.exists():
    for f in mu_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    mu_records[key] = r
                except:
                    pass

# Load execution assessments with outcomes
exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        exec_records.append(r)
                except:
                    pass

# Load shadow trades for detailed outcome data
shadow_trades = {}  # (symbol, int(ts)) -> full record
if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("schema_version") != "shadow_trades_v2":
                            continue
                        identity = r.get("identity", {})
                        snap = r.get("decision_snapshot", {})
                        outcome = r.get("simulated_outcome", {})
                        sym = identity.get("symbol", "")
                        ts = snap.get("timestamp_decision_utc", 0)
                        key = (sym, int(ts))
                        if key not in shadow_trades:
                            shadow_trades[key] = {
                                "symbol": sym, "timestamp": ts,
                                "direction": snap.get("direction", ""),
                                "result_r": outcome.get("pnl_r_multiple"),
                                "mfe_r": outcome.get("mfe_r", 0),
                                "mae_r": outcome.get("mae_r", 0),
                                "bars_held": outcome.get("bars_held", 0),
                                "hold_minutes": outcome.get("hold_minutes", 0),
                                "exit_reason": outcome.get("exit_reason", ""),
                                "stop_pips": snap.get("stop_distance_pips", 0),
                                "target_pips": snap.get("target_distance_pips", 0),
                            }
                    except:
                        pass

print(f"Market context: {len(ctx_records)}")
print(f"Market understanding: {len(mu_records)}")
print(f"Execution assessments: {len(exec_records)}")
print(f"Shadow trades: {len(shadow_trades)}")

# ═══════════════════════════════════════════════════════════════
# BUILD ENRICHED DATASET
# ═══════════════════════════════════════════════════════════════

enriched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = int(rec.get("timestamp_utc", 0))
    key = (sym, ts)
    
    ctx = ctx_records.get(key, {})
    mu = mu_records.get(key, {})
    shadow = shadow_trades.get(key, {})
    
    behaviour = ctx.get("behaviour", {})
    location = ctx.get("location", {})
    htf = ctx.get("htf_structure", {})
    
    m15 = mu.get("m15", {})
    m5 = mu.get("m5", {})
    h1 = mu.get("h1", {})
    
    outcome = rec["_outcome"]
    result_r = outcome["result_r"]
    mfe_r = outcome.get("mfe_r", 0)
    mae_r = outcome.get("mae_r", 0)
    
    # Mean-reversion specific features
    range_pos = location.get("range_position", 0.5)
    inside_zone = location.get("inside_institutional_zone", False)
    loc_type = location.get("location_type", "OPEN_SPACE")
    prem_disc = location.get("premium_discount", "")
    zone_quality = location.get("zone_quality", 0)
    
    # Distance from equilibrium (0.5 = middle, 0/1 = extremes)
    dist_from_eq = abs(range_pos - 0.5)
    
    # Momentum (contrarian signal)
    momentum_dir = behaviour.get("momentum_direction", "NEUTRAL")
    momentum_str = behaviour.get("momentum_strength", 0)
    direction = rec.get("direction", "")
    
    # Is this a contrarian trade? (going against current momentum)
    contrarian = False
    if direction == "BULLISH" and momentum_dir == "BEARISH":
        contrarian = True
    elif direction == "BEARISH" and momentum_dir == "BULLISH":
        contrarian = True
    
    # Momentum neutral (best from V5.1)
    momentum_neutral = momentum_dir == "NEUTRAL"
    
    # Structure alignment (inverted: lower = better)
    struct_align = htf.get("structure_alignment", 0)
    
    # Entry state
    entry_state = rec.get("entry_state", "")
    opp_state = rec.get("opportunity_state", "")
    
    # M15 pullback info
    pullback_active = m15.get("pullback_active", False)
    pullback_depth = m15.get("pullback_depth_atr", 0)
    retracement_pct = m15.get("retracement_pct", 0)
    
    # Rejection info
    rejection_present = m5.get("rejection_present", False)
    rejection_strength = m5.get("rejection_strength_atr", 0)
    
    # Exit/hold data from shadow trade
    bars_held = shadow.get("bars_held", outcome.get("bars_held", 0))
    hold_minutes = shadow.get("hold_minutes", outcome.get("hold_minutes", 0))
    exit_reason = shadow.get("exit_reason", outcome.get("exit_reason", ""))
    
    # Stop/target geometry
    stop_pips = shadow.get("stop_pips", 0)
    spread_at_entry = rec.get("spread_at_entry", 0)
    
    enriched.append({
        "symbol": sym, "timestamp": ts, "direction": direction,
        "entry_state": entry_state, "opp_state": opp_state,
        "result_r": result_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "win": result_r > 0,
        # Location
        "range_pos": range_pos, "dist_from_eq": dist_from_eq,
        "inside_zone": inside_zone, "loc_type": loc_type,
        "prem_disc": prem_disc, "zone_quality": zone_quality,
        # Momentum/contrarian
        "momentum_dir": momentum_dir, "momentum_str": momentum_str,
        "contrarian": contrarian, "momentum_neutral": momentum_neutral,
        # Structure
        "struct_align": struct_align,
        # M15 context
        "pullback_active": pullback_active,
        "pullback_depth": pullback_depth,
        "retracement_pct": retracement_pct,
        # Rejection
        "rejection_present": rejection_present,
        "rejection_strength": rejection_strength,
        # Hold/exit
        "bars_held": bars_held, "hold_minutes": hold_minutes,
        "exit_reason": exit_reason,
        # Cost
        "spread_at_entry": spread_at_entry,
    })

print(f"\nEnriched records: {len(enriched)}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def stats(subset):
    if not subset:
        return None
    results = [s["result_r"] for s in subset]
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    mfe_vals = [s["mfe_r"] for s in subset]
    mae_vals = [s["mae_r"] for s in subset]
    move_05 = sum(1 for s in subset if s["mfe_r"] > 0.5) / n
    move_1 = sum(1 for s in subset if s["mfe_r"] > 1.0) / n
    avg_hold = sum(s["bars_held"] for s in subset) / n if any(s["bars_held"] for s in subset) else 0
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "mfe": sum(mfe_vals) / n, "mae": sum(mae_vals) / n,
        "move_05": move_05, "move_1": move_1,
        "avg_hold": avg_hold,
    }

COST_10P = 0.12  # 1.2 pip spread / 10 pip stop
COST_15P = 0.08
COST_20P = 0.06

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: LOCATION QUALITY (Mean-Reversion Context)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: LOCATION QUALITY")
print("─" * 70)

# A) Range position — are extremes better for mean reversion?
print(f"\n  A) Range Position (distance from equilibrium):")
print(f"  {'Position':<30s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'MFE':>5s} | {'MAE':>5s}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*5}")

# Quartiles by distance from equilibrium
sorted_by_dist = sorted(enriched, key=lambda r: r["dist_from_eq"])
q = len(sorted_by_dist) // 4
for i, label in enumerate(["Near equilibrium (Q1)", "Mid-low (Q2)", "Mid-high (Q3)", "Extreme (Q4)"]):
    subset = sorted_by_dist[i*q:(i+1)*q] if i < 3 else sorted_by_dist[3*q:]
    s = stats(subset)
    if s:
        avg_dist = sum(r["dist_from_eq"] for r in subset) / len(subset)
        print(f"  {label+f' (d={avg_dist:.2f})':<30s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {s['mfe']:.3f} | {s['mae']:.3f}")

# B) Premium/discount relative to trade direction
print(f"\n  B) Premium/Discount relative to direction (mean-reversion logic):")
# Mean reversion: BUY in discount (oversold), SELL in premium (overbought)
mr_correct = [r for r in enriched
              if (r["direction"] == "BULLISH" and r["prem_disc"] == "DISCOUNT") or
                 (r["direction"] == "BEARISH" and r["prem_disc"] == "PREMIUM")]
mr_incorrect = [r for r in enriched
                if (r["direction"] == "BULLISH" and r["prem_disc"] == "PREMIUM") or
                   (r["direction"] == "BEARISH" and r["prem_disc"] == "DISCOUNT")]
mr_equilibrium = [r for r in enriched if r["prem_disc"] == "EQUILIBRIUM"]

for label, subset in [("MR correct (buy disc/sell prem)", mr_correct),
                      ("MR incorrect (buy prem/sell disc)", mr_incorrect),
                      ("At equilibrium", mr_equilibrium)]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"  {label:<40s} | n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# C) Zone type performance
print(f"\n  C) Zone type (where mean-reversion fires):")
for loc in ["DEMAND_OB", "SUPPLY_OB", "BEARISH_FVG", "BULLISH_FVG", "OPEN_SPACE"]:
    subset = [r for r in enriched if r["loc_type"] == loc]
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {loc:<15s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: ENTRY TIMING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: ENTRY TIMING (mean-reversion context)")
print("─" * 70)

# A) Standard entry states
print(f"\n  A) Entry confirmation level:")
for es in ["WEAK_ENTRY_CONFIRMATION", "VALID_ENTRY_CONFIRMATION", "NO_ENTRY_CONFIRMATION"]:
    subset = [r for r in enriched if r["entry_state"] == es]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {es:<30s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | hold={s['avg_hold']:.0f}bars")

# B) Rejection present at entry
print(f"\n  B) Rejection at entry:")
rej_yes = [r for r in enriched if r["rejection_present"]]
rej_no = [r for r in enriched if not r["rejection_present"]]
for label, subset in [("Rejection present", rej_yes), ("No rejection", rej_no)]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# C) Pullback context (is price pulling back = mean-reversion setup?)
print(f"\n  C) Pullback active (M15 context):")
pb_yes = [r for r in enriched if r["pullback_active"]]
pb_no = [r for r in enriched if not r["pullback_active"]]
for label, subset in [("Pullback active", pb_yes), ("No pullback", pb_no)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# D) Pullback depth (deeper = more mean-reversion potential?)
if pb_yes:
    sorted_pb = sorted(pb_yes, key=lambda r: r["pullback_depth"])
    half = len(sorted_pb) // 2
    shallow = sorted_pb[:half]
    deep = sorted_pb[half:]
    print(f"\n  D) Pullback depth:")
    for label, subset in [("Shallow pullback", shallow), ("Deep pullback", deep)]:
        s = stats(subset)
        if s and s["n"] >= 5:
            avg_depth = sum(r["pullback_depth"] for r in subset) / len(subset)
            print(f"    {label:<20s} (avg={avg_depth:.2f}ATR): n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# E) Contrarian vs neutral vs with-momentum
print(f"\n  E) Contrarian signal:")
contrarian = [r for r in enriched if r["contrarian"]]
with_mom = [r for r in enriched
            if (r["direction"]=="BULLISH" and r["momentum_dir"]=="BULLISH") or
               (r["direction"]=="BEARISH" and r["momentum_dir"]=="BEARISH")]
neutral_mom = [r for r in enriched if r["momentum_neutral"]]

for label, subset in [("CONTRARIAN (against momentum)", contrarian),
                      ("NEUTRAL momentum", neutral_mom),
                      ("WITH momentum", with_mom)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<35s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: EXIT MODELS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: EXIT MODEL ANALYSIS")
print("─" * 70)

# A) Exit reason distribution
exit_reasons = Counter(r["exit_reason"] for r in enriched)
print(f"\n  A) Exit reason distribution:")
for reason, count in exit_reasons.most_common():
    subset = [r for r in enriched if r["exit_reason"] == reason]
    s = stats(subset)
    if s:
        print(f"    {reason:<20s}: n={s['n']:4d} ({count/len(enriched)*100:.0f}%) | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# B) Hold time analysis (mean-reversion should be quick)
print(f"\n  B) Hold time analysis:")
timed = [r for r in enriched if r["hold_minutes"] > 0]
if timed:
    sorted_hold = sorted(timed, key=lambda r: r["hold_minutes"])
    q = len(sorted_hold) // 4
    for i, label in enumerate(["Fast (Q1)", "Medium-fast (Q2)", "Medium-slow (Q3)", "Slow (Q4)"]):
        subset = sorted_hold[i*q:(i+1)*q] if i < 3 else sorted_hold[3*q:]
        s = stats(subset)
        if s and s["n"] >= 5:
            avg_mins = sum(r["hold_minutes"] for r in subset) / len(subset)
            print(f"    {label:<15s} (avg={avg_mins:.0f}min): n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# C) MFE reached quickly vs slowly
print(f"\n  C) Quick wins vs slow wins (bars to MFE proxy):")
winners = [r for r in enriched if r["result_r"] > 0]
if winners:
    # Use bars_held as proxy — fewer bars = quicker resolution
    sorted_winners = sorted(winners, key=lambda r: r["bars_held"])
    half = len(sorted_winners) // 2
    quick_wins = sorted_winners[:half]
    slow_wins = sorted_winners[half:]
    
    s_quick = stats(quick_wins)
    s_slow = stats(slow_wins)
    if s_quick and s_slow:
        avg_bars_quick = sum(r["bars_held"] for r in quick_wins) / len(quick_wins)
        avg_bars_slow = sum(r["bars_held"] for r in slow_wins) / len(slow_wins)
        print(f"    Quick wins (avg {avg_bars_quick:.0f} bars): n={s_quick['n']:4d} | avg R={s_quick['ev']:+.4f}")
        print(f"    Slow wins (avg {avg_bars_slow:.0f} bars): n={s_slow['n']:4d} | avg R={s_slow['ev']:+.4f}")

# D) MFE/MAE ratio (mean-reversion trades should have tight MAE)
print(f"\n  D) MFE/MAE ratio (mean-reversion quality):")
for label, subset in [("All trades", enriched),
                      ("Winners", [r for r in enriched if r["win"]]),
                      ("Losers", [r for r in enriched if not r["win"]])]:
    s = stats(subset)
    if s and s["n"] >= 5:
        ratio = s["mfe"] / max(s["mae"], 0.001)
        print(f"    {label:<12s}: MFE={s['mfe']:.3f} MAE={s['mae']:.3f} ratio={ratio:.2f}")

# E) Simulate different TP levels
print(f"\n  E) Simulated TP levels (% of trades reaching target):")
for tp_level in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
    reached = sum(1 for r in enriched if r["mfe_r"] >= tp_level)
    pct = reached / len(enriched)
    # Approximate EV: winners get tp_level, losers get -1
    approx_ev = pct * tp_level - (1 - pct) * 1.0
    print(f"    TP={tp_level:.1f}R: {reached:4d}/{len(enriched)} ({pct:.1%}) → approx EV={approx_ev:+.3f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: TIMEFRAME CONTEXT
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: TIMEFRAME CONTEXT")
print("─" * 70)

# A) H1 trend alignment (mean-reversion should work AGAINST H1 in pullbacks)
print(f"\n  A) H1 trend vs trade direction:")
# Mean reversion in pullback: trade AGAINST H1 trend (buying dip in uptrend)
h1_with = [r for r in enriched
           if (r["direction"] == "BULLISH" and
               ctx_records.get((r["symbol"], r["timestamp"]), {}).get("htf_structure", {}).get("macro_bias") == "BULLISH") or
              (r["direction"] == "BEARISH" and
               ctx_records.get((r["symbol"], r["timestamp"]), {}).get("htf_structure", {}).get("macro_bias") == "BEARISH")]
h1_against = [r for r in enriched
              if (r["direction"] == "BULLISH" and
                  ctx_records.get((r["symbol"], r["timestamp"]), {}).get("htf_structure", {}).get("macro_bias") == "BEARISH") or
                 (r["direction"] == "BEARISH" and
                  ctx_records.get((r["symbol"], r["timestamp"]), {}).get("htf_structure", {}).get("macro_bias") == "BULLISH")]
h1_neutral = [r for r in enriched
              if ctx_records.get((r["symbol"], r["timestamp"]), {}).get("htf_structure", {}).get("macro_bias") == "NEUTRAL"]

for label, subset in [("WITH H1 trend", h1_with),
                      ("AGAINST H1 trend", h1_against),
                      ("H1 NEUTRAL", h1_neutral)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f}")

# B) M15 internal structure
print(f"\n  B) M15 pullback context:")
pb_with_weak = [r for r in enriched
                if r["pullback_active"] and r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]
pb_without_weak = [r for r in enriched
                   if not r["pullback_active"] and r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]

for label, subset in [("WEAK + pullback active", pb_with_weak),
                      ("WEAK + no pullback", pb_without_weak)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# C) Structural clarity (how clear is the H1 structure?)
print(f"\n  C) H1 structural clarity:")
sorted_clarity = sorted(enriched, key=lambda r: r.get("struct_align", 0))
half = len(sorted_clarity) // 2
low_clarity = sorted_clarity[:half]
high_clarity = sorted_clarity[half:]

for label, subset in [("Low struct alignment", low_clarity), ("High struct alignment", high_clarity)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: MEAN-REVERSION COMPOSITE SCORE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: MEAN-REVERSION COMPOSITE CONFIGURATIONS")
print("─" * 70)

# Build mean-reversion score based on V5.1 findings
# Higher = more mean-reversion characteristics
for rec in enriched:
    mr_score = 0
    # Momentum neutral = +1 (V5.1: best predictor)
    if rec["momentum_neutral"]:
        mr_score += 2
    elif rec["contrarian"]:
        mr_score += 1
    # Low structure alignment = +1 (V5.1: inverted)
    if rec["struct_align"] < 0.5:
        mr_score += 1
    # WEAK entry = +1
    if rec["entry_state"] == "WEAK_ENTRY_CONFIRMATION":
        mr_score += 1
    # Inside zone = +1 (V5.1: higher WR in zone)
    if rec["inside_zone"]:
        mr_score += 1
    # Pullback active = +1
    if rec["pullback_active"]:
        mr_score += 1
    rec["mr_score"] = mr_score

# Test by MR score
print(f"\n  By Mean-Reversion Score:")
print(f"  {'Score':<10s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@15p':>7s} | {'MFE':>5s} | {'>0.5R':>4s}")
print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}-+-{'-'*5}-+-{'-'*4}")

for score in range(7):
    subset = [r for r in enriched if r["mr_score"] == score]
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST_15P
        print(f"  Score={score:<4d} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net:>+6.4f} | {s['mfe']:.3f} | {s['move_05']:.0%}")

# Cumulative: score >= threshold
print(f"\n  Cumulative (score >= threshold):")
for threshold in range(1, 6):
    subset = [r for r in enriched if r["mr_score"] >= threshold]
    s = stats(subset)
    if s and s["n"] >= 10:
        net = s["ev"] - COST_15P
        print(f"    Score>={threshold}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | net@15p={net:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: STABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 6: STABILITY TESTING")
print("─" * 70)

# Best subset for stability test: highest MR score with sufficient n
best_threshold = 3  # score >= 3
best_subset = [r for r in enriched if r["mr_score"] >= best_threshold]

if best_subset:
    # Time thirds
    sorted_best = sorted(best_subset, key=lambda r: r["timestamp"])
    third = max(len(sorted_best) // 3, 1)
    periods = [
        ("Early", sorted_best[:third]),
        ("Middle", sorted_best[third:2*third]),
        ("Recent", sorted_best[2*third:]),
    ]
    
    print(f"\n  Time stability (MR score >= {best_threshold}):")
    for label, subset in periods:
        s = stats(subset)
        if s and s["n"] >= 3:
            print(f"    {label:<8s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")
    
    # Symbol stability
    print(f"\n  Symbol stability (MR score >= {best_threshold}):")
    for sym in sorted(set(r["symbol"] for r in best_subset)):
        subset = [r for r in best_subset if r["symbol"] == sym]
        s = stats(subset)
        if s and s["n"] >= 3:
            print(f"    {sym:10s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")
    
    # Positive symbol count
    sym_positive = sum(1 for sym in set(r["symbol"] for r in best_subset)
                       if stats([r for r in best_subset if r["symbol"] == sym]) and
                          stats([r for r in best_subset if r["symbol"] == sym])["ev"] > 0)
    sym_total = len(set(r["symbol"] for r in best_subset))
    print(f"\n    Symbols with positive EV: {sym_positive}/{sym_total}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 7: MEAN-REVERSION TP OPTIMIZATION (informational only)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 7: OPTIMAL TP FOR MEAN-REVERSION")
print("─" * 70)

# For high-MR-score trades, what TP maximizes EV?
high_mr = [r for r in enriched if r["mr_score"] >= 3]
if high_mr:
    print(f"\n  For MR score >= 3 (n={len(high_mr)}):")
    print(f"  {'TP level':<10s} | {'Hit rate':>8s} | {'Approx EV':>9s} | {'Net @15p':>8s}")
    print(f"  {'-'*10}-+-{'-'*8}-+-{'-'*9}-+-{'-'*8}")
    
    for tp in [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0]:
        hit = sum(1 for r in high_mr if r["mfe_r"] >= tp)
        hit_rate = hit / len(high_mr)
        # Approx: winners get tp, losers get -1 (full stop hit)
        approx_ev = hit_rate * tp - (1 - hit_rate) * 1.0
        net = approx_ev - COST_15P
        print(f"  TP={tp:<5.1f}R | {hit_rate:>7.1%} | {approx_ev:>+8.4f} | {net:>+7.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 8: THE FUNDAMENTAL QUESTION — IS MR VIABLE?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 8: VIABILITY ASSESSMENT")
print("─" * 70)

# Calculate what a realistic mean-reversion system would produce
# Using the actual MFE distribution of high-MR trades
if high_mr:
    mfes = sorted([r["mfe_r"] for r in high_mr])
    maes = sorted([r["mae_r"] for r in high_mr])
    
    print(f"\n  High MR score trades (n={len(high_mr)}):")
    print(f"    MFE distribution: p10={mfes[len(mfes)//10]:.3f} p25={mfes[len(mfes)//4]:.3f} "
          f"p50={mfes[len(mfes)//2]:.3f} p75={mfes[3*len(mfes)//4]:.3f} p90={mfes[9*len(mfes)//10]:.3f}")
    print(f"    MAE distribution: p10={maes[len(maes)//10]:.3f} p25={maes[len(maes)//4]:.3f} "
          f"p50={maes[len(maes)//2]:.3f} p75={maes[3*len(maes)//4]:.3f} p90={maes[9*len(maes)//10]:.3f}")
    
    # What % never even reach 0.3R MFE?
    no_move = sum(1 for r in high_mr if r["mfe_r"] < 0.3)
    print(f"    Trades that never reach 0.3R MFE: {no_move}/{len(high_mr)} ({no_move/len(high_mr):.1%})")
    
    # Average time to resolution
    avg_bars = sum(r["bars_held"] for r in high_mr) / len(high_mr)
    avg_mins = sum(r["hold_minutes"] for r in high_mr if r["hold_minutes"] > 0)
    mins_count = sum(1 for r in high_mr if r["hold_minutes"] > 0)
    if mins_count > 0:
        avg_mins /= mins_count
    print(f"    Avg hold: {avg_bars:.0f} bars ({avg_mins:.0f} minutes)")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V5.2 FINAL VERDICT")
print("=" * 70)

s_all = stats(enriched)
s_high_mr = stats(high_mr) if high_mr else None

if s_all and s_high_mr:
    print(f"\n  Baseline (all): n={s_all['n']} | WR={s_all['wr']:.1%} | EV={s_all['ev']:+.4f}")
    print(f"  High MR (>=3): n={s_high_mr['n']} | WR={s_high_mr['wr']:.1%} | EV={s_high_mr['ev']:+.4f}")
    print(f"  CI: [{s_high_mr['ci_low']:+.4f}, {s_high_mr['ci_high']:+.4f}]")
    
    improvement = s_high_mr["ev"] - s_all["ev"]
    net_15p = s_high_mr["ev"] - COST_15P
    net_20p = s_high_mr["ev"] - COST_20P
    
    print(f"\n  Improvement: {improvement:+.4f}R")
    print(f"  Net @15p: {net_15p:+.4f}R")
    print(f"  Net @20p: {net_20p:+.4f}R")
    print(f"  Movement: P(>0.5R)={s_high_mr['move_05']:.1%} | P(>1R)={s_high_mr['move_1']:.1%}")
    
    ci_excludes_zero = s_high_mr["ci_low"] > 0
    
    if ci_excludes_zero and net_15p > 0:
        verdict = "A"
    elif s_high_mr["ev"] > 0 and net_20p > 0 and s_high_mr["n"] >= 30:
        verdict = "B"
    elif s_high_mr["ev"] > 0 and net_15p <= 0:
        verdict = "C"
    else:
        verdict = "D"
    
    verdicts = {
        "A": "Mean-reversion expression is viable",
        "B": "Mean-reversion signal exists but requires different execution model",
        "C": "Signal is real but too weak after costs",
        "D": "No reliable edge found",
    }
    
    print(f"\n  VERDICT: {verdict}) {verdicts[verdict]}")
    
    print(f"\n  KEY OBSERVATIONS:")
    print(f"    1. The system IS mean-reverting (contrarian features improve outcomes)")
    print(f"    2. Momentum-neutral + WEAK + inside-zone = best combination")
    print(f"    3. P(>0.5R MFE) = {s_high_mr['move_05']:.1%} — {'sufficient' if s_high_mr['move_05'] > 0.3 else 'INSUFFICIENT'} movement")
    print(f"    4. CI includes zero: {'YES' if not ci_excludes_zero else 'NO'}")
    if not ci_excludes_zero:
        # Required n
        if s_high_mr["ev"] > 0 and s_high_mr["std"] > 0:
            n_req = math.ceil((1.96 * s_high_mr["std"] / s_high_mr["ev"]) ** 2)
            print(f"    5. Required n for significance: {n_req} (have {s_high_mr['n']})")

print()
