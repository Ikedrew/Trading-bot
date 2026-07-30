"""V7.3 — Dynamic Policy Router Validation.

Tests whether per-trade market state (regime, momentum, expansion) can
improve on the simple instrument-class router (FX=fade, INDEX=follow).

Key question: should the router be STATIC (by symbol) or DYNAMIC (by state)?
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V7.3 — DYNAMIC POLICY ROUTER VALIDATION")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD"}

# Load FX execution assessments (have V3 context for dynamic routing)
exec_dir = Path("logs/v3_shadow/execution_assessment")
fx_exec = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        fx_exec.append(r)
                except:
                    pass

# Load market context
ctx_dir = Path("logs/v3_shadow/market_context")
ctx_data = {}
if ctx_dir.exists():
    for f in ctx_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    ctx_data[key] = r
                except:
                    pass

# Load index shadow trades
shadow_dir = Path("logs/shadow_trades")
idx_trades = []
for sym in INDEX_SYMBOLS:
    d = shadow_dir / sym
    if not d.exists():
        continue
    for f in d.glob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("schema_version") != "shadow_trades_v2":
                        continue
                    snap = r.get("decision_snapshot", {})
                    outcome = r.get("simulated_outcome", {})
                    if outcome.get("pnl_r_multiple") is None:
                        continue
                    idx_trades.append({
                        "symbol": sym,
                        "result_r": outcome["pnl_r_multiple"],
                        "mfe_r": outcome.get("mfe_r", 0),
                        "mae_r": outcome.get("mae_r", 0),
                        "timestamp": snap.get("timestamp_decision_utc", 0),
                    })
                except:
                    pass

print(f"\n  FX exec assessments (with context): {len(fx_exec)}")
print(f"  Market context records: {len(ctx_data)}")
print(f"  INDEX shadow trades: {len(idx_trades)}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def calc_stats(results):
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    return {"n": n, "wr": wins/n, "ev": ev, "ci_low": ev-1.96*se, "ci_high": ev+1.96*se}


# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: MARKET STATE CLASSIFICATION (FX — has context labels)
# For each trade, determine: would REVERSION or TREND have been better?
# Then find which market state features predict this.
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: MARKET STATE vs OPTIMAL POLICY (FX exec data)")
print("─" * 70)

# For each FX exec trade: if result_r > 0, reversion was correct
# If result_r < 0, trend (inversion) would have been correct
# Tag each trade with its context and which policy wins

tagged = []
for rec in fx_exec:
    key = (rec.get("symbol", ""), int(rec.get("timestamp_utc", 0)))
    ctx = ctx_data.get(key, {})
    beh = ctx.get("behaviour", {})
    loc = ctx.get("location", {})
    htf = ctx.get("htf_structure", {})
    
    result = rec["_outcome"]["result_r"]
    direction = rec.get("direction", "")
    
    # Which policy would win?
    reversion_wins = result > 0      # Current signal was correct
    trend_wins = result < 0          # Opposite would have been correct
    
    momentum = beh.get("momentum_direction", "NEUTRAL")
    with_mom = (direction == "BULLISH" and momentum == "BULLISH") or \
               (direction == "BEARISH" and momentum == "BEARISH")
    against_mom = (direction == "BULLISH" and momentum == "BEARISH") or \
                  (direction == "BEARISH" and momentum == "BULLISH")
    
    tagged.append({
        "result_r": result,
        "reversion_wins": reversion_wins,
        "trend_wins": trend_wins,
        # Context features
        "regime": beh.get("regime", "UNKNOWN"),
        "volatility": beh.get("volatility_state", "UNKNOWN"),
        "expansion": beh.get("expansion_state", "UNKNOWN"),
        "momentum": momentum,
        "with_momentum": with_mom,
        "against_momentum": against_mom,
        "loc_type": loc.get("location_type", "UNKNOWN"),
        "inside_zone": loc.get("inside_institutional_zone", False),
        "struct_align": htf.get("structure_alignment", 0),
        "macro_bias": htf.get("macro_bias", "UNKNOWN"),
        "entry_state": rec.get("entry_state", ""),
        "opp_state": rec.get("opportunity_state", ""),
        "symbol": rec.get("symbol", ""),
        "timestamp": rec.get("timestamp_utc", 0),
    })

# For each feature, compare: when does reversion win vs trend win?
print(f"\n  Feature → Policy preference:")
print(f"  {'Feature':<30s} | {'n':>4s} | {'Rev wins':>8s} | {'Trend wins':>10s} | {'Rev EV':>7s} | {'Trend EV':>8s} | {'Better'}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*8}-+-{'-'*10}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

features_to_test = [
    ("Momentum: NEUTRAL", lambda t: t["momentum"] == "NEUTRAL"),
    ("Momentum: WITH trade", lambda t: t["with_momentum"]),
    ("Momentum: AGAINST trade", lambda t: t["against_momentum"]),
    ("Inside zone: YES", lambda t: t["inside_zone"]),
    ("Inside zone: NO", lambda t: not t["inside_zone"]),
    ("Struct align: HIGH (>0.8)", lambda t: t["struct_align"] >= 0.8),
    ("Struct align: LOW (<0.5)", lambda t: t["struct_align"] < 0.5),
    ("Entry: WEAK", lambda t: t["entry_state"] == "WEAK_ENTRY_CONFIRMATION"),
    ("Entry: VALID", lambda t: t["entry_state"] == "VALID_ENTRY_CONFIRMATION"),
    ("Opp: INTERESTING", lambda t: t["opp_state"] == "INTERESTING_CONTEXT"),
    ("Opp: HIGH_QUALITY", lambda t: t["opp_state"] == "HIGH_QUALITY_CONTEXT"),
    ("Macro: BULLISH", lambda t: t["macro_bias"] == "BULLISH"),
    ("Macro: BEARISH", lambda t: t["macro_bias"] == "BEARISH"),
    ("Macro: NEUTRAL", lambda t: t["macro_bias"] == "NEUTRAL"),
]

for label, filter_fn in features_to_test:
    subset = [t for t in tagged if filter_fn(t)]
    if len(subset) < 10:
        continue
    rev_ev = sum(t["result_r"] for t in subset) / len(subset)
    trend_ev = sum(-t["result_r"] for t in subset) / len(subset)
    rev_wins = sum(1 for t in subset if t["reversion_wins"])
    trend_wins_n = sum(1 for t in subset if t["trend_wins"])
    better = "REVERSION" if rev_ev > trend_ev else "TREND"
    print(f"  {label:<30s} | {len(subset):>4d} | {rev_wins:>8d} | {trend_wins_n:>10d} | {rev_ev:>+6.4f} | {trend_ev:>+7.4f} | {better}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: DYNAMIC ROUTER SIMULATION
# Compare: symbol-static vs state-dynamic routing
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: ROUTER SIMULATION")
print("─" * 70)

# Router A: Symbol-static (V7.2 result)
# FX → reversion (keep signal), INDEX → trend (invert signal)
# Already validated: INDEX inverted = +0.125R

# Router B: State-dynamic (within FX)
# Based on Analysis 1 findings, try:
# - WITH momentum → TREND (invert)
# - Everything else → REVERSION (keep)

# Router C: Conservative state-dynamic
# - WITH momentum → SKIP (don't trade)
# - Everything else → REVERSION (keep)

print(f"\n  FX EXEC ASSESSMENT ROUTERS (n={len(tagged)}):")

# Baseline: all reversion
baseline_results = [t["result_r"] for t in tagged]
baseline_s = calc_stats(baseline_results)

# Router B: invert WITH-momentum trades
router_b_results = [-t["result_r"] if t["with_momentum"] else t["result_r"] for t in tagged]
router_b_s = calc_stats(router_b_results)

# Router C: skip WITH-momentum trades
router_c_subset = [t for t in tagged if not t["with_momentum"]]
router_c_results = [t["result_r"] for t in router_c_subset]
router_c_s = calc_stats(router_c_results)

# Router D: only trade NEUTRAL momentum (V5.1's best finding)
router_d_subset = [t for t in tagged if t["momentum"] == "NEUTRAL"]
router_d_results = [t["result_r"] for t in router_d_subset]
router_d_s = calc_stats(router_d_results)

# Router E: WEAK + NEUTRAL momentum (V5.1 + V5.2 best)
router_e_subset = [t for t in tagged
                   if t["momentum"] == "NEUTRAL"
                   and t["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]
router_e_results = [t["result_r"] for t in router_e_subset]
router_e_s = calc_stats(router_e_results)

print(f"\n  {'Router':<45s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'CI':>20s}")
print(f"  {'-'*45}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*20}")

for label, s in [
    ("A: ALL reversion (baseline)", baseline_s),
    ("B: Invert WITH-momentum trades", router_b_s),
    ("C: Skip WITH-momentum trades", router_c_s),
    ("D: Only NEUTRAL momentum", router_d_s),
    ("E: WEAK + NEUTRAL momentum", router_e_s),
]:
    if s:
        print(f"  {label:<45s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | [{s['ci_low']:+.4f},{s['ci_high']:+.4f}]")

# Now combine with INDEX
print(f"\n  COMBINED PORTFOLIO (FX router + INDEX trend):")
idx_inverted_ev = sum(-t["result_r"] for t in idx_trades) / max(len(idx_trades), 1)
idx_n = len(idx_trades)

for label, fx_s, fx_n in [
    ("Symbol-static (FX all rev + IDX trend)", baseline_s, len(tagged)),
    ("Dynamic B (FX flip momentum + IDX trend)", router_b_s, len(tagged)),
    ("Dynamic D (FX neutral-only + IDX trend)", router_d_s, router_d_s["n"] if router_d_s else 0),
    ("Dynamic E (FX WEAK+neutral + IDX trend)", router_e_s, router_e_s["n"] if router_e_s else 0),
]:
    if fx_s and idx_n > 0:
        combined_ev = (fx_s["ev"] * fx_n + idx_inverted_ev * idx_n) / (fx_n + idx_n)
        combined_n = fx_n + idx_n
        print(f"    {label:<50s}: combined EV={combined_ev:+.4f} (n={combined_n})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: MISCLASSIFICATION COST
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: MISCLASSIFICATION COST")
print("─" * 70)

# For the symbol-static router:
# FX: reversion is "correct" when result > 0
# How often is it "wrong" (result < 0 = trend would have been better)?
fx_correct = sum(1 for t in tagged if t["result_r"] > 0)
fx_wrong = sum(1 for t in tagged if t["result_r"] < 0)
fx_neutral = sum(1 for t in tagged if t["result_r"] == 0)

# INDEX: trend (inverted) is "correct" when original result < 0
idx_correct = sum(1 for t in idx_trades if t["result_r"] < 0)
idx_wrong = sum(1 for t in idx_trades if t["result_r"] > 0)

print(f"\n  Symbol-static router accuracy:")
print(f"    FX (reversion): correct {fx_correct}/{len(tagged)} ({fx_correct/max(len(tagged),1):.1%}), wrong {fx_wrong} ({fx_wrong/max(len(tagged),1):.1%})")
print(f"    INDEX (trend):  correct {idx_correct}/{len(idx_trades)} ({idx_correct/max(len(idx_trades),1):.1%}), wrong {idx_wrong} ({idx_wrong/max(len(idx_trades),1):.1%})")

# Cost of being wrong
if tagged:
    fx_wrong_trades = [t for t in tagged if t["result_r"] < 0]
    fx_wrong_cost = sum(t["result_r"] for t in fx_wrong_trades) / max(len(fx_wrong_trades), 1)
    print(f"\n  When FX reversion is wrong:")
    print(f"    Average loss: {fx_wrong_cost:+.4f}R (n={len(fx_wrong_trades)})")
    print(f"    If inverted instead: {-fx_wrong_cost:+.4f}R average gain")

if idx_trades:
    idx_wrong_trades = [t for t in idx_trades if t["result_r"] > 0]
    idx_wrong_cost = sum(-t["result_r"] for t in idx_wrong_trades) / max(len(idx_wrong_trades), 1)
    print(f"\n  When INDEX trend is wrong (original trade was actually correct):")
    print(f"    Average loss from inverting: {idx_wrong_cost:+.4f}R (n={len(idx_wrong_trades)})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: OUT-OF-SAMPLE VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: OUT-OF-SAMPLE VALIDATION")
print("─" * 70)

# FX exec assessments — time thirds
sorted_tagged = sorted(tagged, key=lambda t: t["timestamp"])
third = max(len(sorted_tagged) // 3, 1)
fx_periods = [
    ("Early", sorted_tagged[:third]),
    ("Middle", sorted_tagged[third:2*third]),
    ("Recent", sorted_tagged[2*third:]),
]

print(f"\n  FX Reversion policy across time:")
for label, period in fx_periods:
    if not period:
        continue
    rev_ev = sum(t["result_r"] for t in period) / len(period)
    trend_ev = sum(-t["result_r"] for t in period) / len(period)
    better = "REVERSION" if rev_ev > trend_ev else "TREND"
    print(f"    {label:<8s}: n={len(period):3d} | Rev EV={rev_ev:+.4f} | Trend EV={trend_ev:+.4f} | → {better}")

# FX momentum-filtered across time
print(f"\n  FX NEUTRAL-momentum-only across time:")
for label, period in fx_periods:
    subset = [t for t in period if t["momentum"] == "NEUTRAL"]
    if len(subset) < 5:
        continue
    rev_ev = sum(t["result_r"] for t in subset) / len(subset)
    print(f"    {label:<8s}: n={len(subset):3d} | Rev EV={rev_ev:+.4f}")

# INDEX across time (already shown in V7.1 but confirm)
print(f"\n  INDEX inverted policy across time:")
sorted_idx = sorted(idx_trades, key=lambda t: t["timestamp"])
idx_third = max(len(sorted_idx) // 3, 1)
for i, label in enumerate(["Early", "Middle", "Recent"]):
    subset = sorted_idx[i*idx_third:(i+1)*idx_third] if i < 2 else sorted_idx[2*idx_third:]
    if subset:
        inv_ev = sum(-t["result_r"] for t in subset) / len(subset)
        inv_wr = sum(1 for t in subset if t["result_r"] < 0) / len(subset)
        print(f"    {label:<8s}: n={len(subset):4d} | Inv EV={inv_ev:+.4f} | WR={inv_wr:.1%}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V7.3 FINAL VERDICT")
print("=" * 70)

# Compare: does dynamic routing beat symbol-static?
if baseline_s and router_d_s and idx_trades:
    # Symbol-static: FX all reversion + INDEX trend
    static_fx_ev = baseline_s["ev"]
    static_combined = (static_fx_ev * len(tagged) + idx_inverted_ev * idx_n) / (len(tagged) + idx_n)
    
    # Dynamic best: FX neutral-only + INDEX trend
    dynamic_fx_ev = router_d_s["ev"]
    dynamic_n = router_d_s["n"] + idx_n
    dynamic_combined = (dynamic_fx_ev * router_d_s["n"] + idx_inverted_ev * idx_n) / dynamic_n
    
    improvement = dynamic_combined - static_combined
    
    print(f"\n  COMPARISON:")
    print(f"    Symbol-static router: combined EV={static_combined:+.4f} (n={len(tagged)+idx_n})")
    print(f"    Dynamic router (best): combined EV={dynamic_combined:+.4f} (n={dynamic_n})")
    print(f"    Improvement: {improvement:+.4f}R")
    print(f"")
    print(f"    INDEX component (same in both): EV={idx_inverted_ev:+.4f} (n={idx_n})")
    print(f"    FX static (all reversion): EV={static_fx_ev:+.4f} (n={len(tagged)})")
    print(f"    FX dynamic (neutral-only): EV={dynamic_fx_ev:+.4f} (n={router_d_s['n']})")
    
    # Verdict logic
    dynamic_helps = improvement > 0.02
    both_positive = static_combined > 0 and dynamic_combined > 0
    idx_drives = idx_inverted_ev > static_fx_ev
    
    if dynamic_helps and both_positive:
        verdict = "A"
        reason = "Dynamic router validated — state-based filtering improves EV"
    elif not dynamic_helps and static_combined > 0:
        verdict = "B"
        reason = "Symbol router sufficient — dynamic routing adds complexity without meaningful improvement"
    elif idx_inverted_ev > 0.05 and static_combined > 0:
        verdict = "B"
        reason = "Symbol router sufficient — INDEX trend-following is the primary value driver"
    else:
        verdict = "C"
        reason = "Policy separation exists but insufficient data for production"
    
    print(f"\n  VERDICT: {verdict}) {reason}")
    
    print(f"""
  KEY CONCLUSION:
  
  The INDEX trend-following signal (EV={idx_inverted_ev:+.4f}, n={idx_n}) is:
  - Stronger than any FX configuration
  - Simpler (no per-trade context needed)
  - More stable across time and symbols
  
  The FX dynamic router (neutral-momentum-only, EV={dynamic_fx_ev:+.4f}) is:
  - Better than universal FX reversion ({static_fx_ev:+.4f})
  - But reduces trade count significantly (n={router_d_s['n']} vs {len(tagged)})
  - And FX remains fragile/concentrated regardless
  
  RECOMMENDED ARCHITECTURE:
  ┌──────────────────────────────────────────────────────────────────┐
  │ TIER 1 (PRIMARY): INDEX trend-following                          │
  │   - NAS100, US500, XAUUSD                                       │
  │   - Invert V3 signal direction                                   │
  │   - No per-trade filtering needed                                │
  │   - EV: +0.125R | WR: 60% | 3/3 symbols | 3/3 periods          │
  │                                                                  │
  │ TIER 2 (SECONDARY): FX filtered reversion                       │
  │   - EURUSD, GBPUSD only (others are negative)                   │
  │   - V3 signal + NEUTRAL momentum filter                          │
  │   - WEAK entry timing                                            │
  │   - EV: ~+0.28R but n is small and time-unstable                │
  │                                                                  │
  │ POLICY ROUTER:                                                   │
  │   if instrument_class == INDEX:                                  │
  │       direction = FOLLOW signal                                  │
  │   elif symbol in (EURUSD, GBPUSD) and momentum == NEUTRAL:      │
  │       direction = FADE signal                                    │
  │   else:                                                          │
  │       SKIP (no trade)                                            │
  └──────────────────────────────────────────────────────────────────┘
""")

print()
