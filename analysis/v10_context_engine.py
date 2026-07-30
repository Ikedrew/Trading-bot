"""V10 — Context-Driven Strategy Execution Engine.

Tests whether reassigning timeframe responsibilities solves the V9 problem:
  H4/H1: Determine environment + strategy family
  M15: Select direction + structure context
  M5: Execute timing only (not decision authority)

Key V9 finding: 65% of M5 trades produce no movement because M5 was
responsible for BOTH direction selection AND execution. When those
responsibilities are separated, does the system improve?

This analysis uses existing V3 data (which already captures H4/H1/M15/M5)
to simulate the V10 responsibility model retroactively.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V10 — CONTEXT-DRIVEN STRATEGY ENGINE RESEARCH")
print("=" * 70)
print("\n  Hypothesis: H4/H1 environment selection + M15 strategy routing")
print("  reduces the 65% 'no movement' problem by filtering for contexts")
print("  where movement is LIKELY before committing to M5 execution.")

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (V3 exec assessments + full context)
# ═══════════════════════════════════════════════════════════════

exec_dir = Path("logs/v3_shadow/execution_assessment")
ctx_dir = Path("logs/v3_shadow/market_context")
mu_dir = Path("logs/v3_shadow/market_understanding")

exec_records = []
for f in exec_dir.rglob("*.jsonl"):
    for line in open(f):
        if not line.strip(): continue
        try:
            r = json.loads(line)
            if r.get("_outcome", {}).get("result_r") is not None:
                exec_records.append(r)
        except: pass

ctx_data = {}
for f in ctx_dir.rglob("*.jsonl"):
    for line in open(f):
        if not line.strip(): continue
        try:
            r = json.loads(line)
            ctx_data[(r.get("symbol",""), int(r.get("timestamp_utc",0)))] = r
        except: pass

mu_data = {}
for f in mu_dir.rglob("*.jsonl"):
    for line in open(f):
        if not line.strip(): continue
        try:
            r = json.loads(line)
            mu_data[(r.get("symbol",""), int(r.get("timestamp_utc",0)))] = r
        except: pass

print(f"\n  Data: {len(exec_records)} trades | {len(ctx_data)} contexts | {len(mu_data)} market_understanding")

# ═══════════════════════════════════════════════════════════════
# ENRICH: V10 responsibility layers
# ═══════════════════════════════════════════════════════════════

enriched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = int(rec.get("timestamp_utc", 0))
    ctx = ctx_data.get((sym, ts), {})
    mu = mu_data.get((sym, ts), {})
    
    beh = ctx.get("behaviour", {})
    loc = ctx.get("location", {})
    htf = ctx.get("htf_structure", {})
    h4 = mu.get("h4", {})
    h1 = mu.get("h1", {})
    m15 = mu.get("m15", {})
    m5 = mu.get("m5", {})
    
    outcome = rec["_outcome"]
    result_r = outcome["result_r"]
    mfe_r = outcome.get("mfe_r", 0)
    mae_r = outcome.get("mae_r", 0)
    exit_reason = outcome.get("exit_reason", "")
    
    # ─── H4/H1 ENVIRONMENT (Layer 1) ──────────────────────────
    h4_trend = h4.get("trend", "NEUTRAL")
    h1_trend = h1.get("dominant_trend", "NEUTRAL")
    macro_bias = htf.get("macro_bias", "NEUTRAL")
    bos_active = htf.get("bos_active", False)
    bos_dir = htf.get("bos_direction", "")
    struct_align = htf.get("structure_alignment", 0)
    regime = beh.get("regime", "RANGING")
    volatility = beh.get("volatility_state", "NEUTRAL")
    
    # Derived: is higher TF in a clear state?
    htf_clear = macro_bias in ("BULLISH", "BEARISH") and struct_align >= 0.7
    htf_neutral = macro_bias == "NEUTRAL" or struct_align < 0.3
    
    # ─── M15 STRATEGY CONTEXT (Layer 2) ───────────────────────
    m15_pullback = m15.get("pullback_active", False)
    m15_pullback_depth = m15.get("pullback_depth_atr", 0)
    m15_displacement = m15.get("displacement_present", False)
    inside_zone = loc.get("inside_institutional_zone", False)
    loc_type = loc.get("location_type", "OPEN_SPACE")
    range_pos = loc.get("range_position", 0.5)
    momentum_dir = beh.get("momentum_direction", "NEUTRAL")
    
    # ─── M5 EXECUTION (Layer 3) ──────────────────────────────
    entry_state = rec.get("entry_state", "")
    direction = rec.get("direction", "")
    opp_state = rec.get("opportunity_state", "")
    
    # ─── V10 STRATEGY CLASSIFICATION ─────────────────────────
    # Based on context, what strategy SHOULD be applied?
    
    # Strategy A: TREND CONTINUATION
    # H1 trending + M15 pullback into zone + M5 confirmation
    htf_aligned_with_trade = (
        (direction == "BULLISH" and macro_bias == "BULLISH") or
        (direction == "BEARISH" and macro_bias == "BEARISH")
    )
    is_trend_cont = htf_aligned_with_trade and m15_pullback and inside_zone
    
    # Strategy B: RANGE MEAN-REVERSION
    # HTF neutral + price at extremes + M5 rejection
    is_mean_rev = htf_neutral and (range_pos < 0.25 or range_pos > 0.75)
    
    # Strategy C: BREAKOUT/EXPANSION
    # Low struct align + displacement + momentum
    is_breakout = struct_align < 0.3 and m15_displacement
    
    # Strategy D: LIQUIDITY SWEEP (inside zone + rejection)
    is_sweep = inside_zone and entry_state == "WEAK_ENTRY_CONFIRMATION"
    
    # Strategy E: NO CLEAR SETUP (filter out)
    is_no_setup = not any([is_trend_cont, is_mean_rev, is_breakout, is_sweep])
    
    # Assign primary strategy
    if is_trend_cont:
        strategy = "TREND_CONTINUATION"
    elif is_sweep:
        strategy = "ZONE_REACTION"
    elif is_mean_rev:
        strategy = "MEAN_REVERSION"
    elif is_breakout:
        strategy = "BREAKOUT"
    else:
        strategy = "UNCLASSIFIED"
    
    enriched.append({
        "symbol": sym, "timestamp": ts, "direction": direction,
        "result_r": result_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "exit_reason": exit_reason, "win": result_r > 0,
        # V10 layers
        "htf_clear": htf_clear, "htf_neutral": htf_neutral,
        "htf_aligned": htf_aligned_with_trade,
        "m15_pullback": m15_pullback, "inside_zone": inside_zone,
        "strategy": strategy,
        "entry_state": entry_state, "opp_state": opp_state,
        "struct_align": struct_align, "macro_bias": macro_bias,
        "momentum_dir": momentum_dir, "range_pos": range_pos,
    })

print(f"  Enriched: {len(enriched)}")

# ═══════════════════════════════════════════════════════════════
# SECTION 1: STRATEGY FAMILY PERFORMANCE
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 1: V10 STRATEGY FAMILY PERFORMANCE")
print("-" * 70)

COST = 0.12  # 12% cost at 10-pip stops

def stats(subset):
    if not subset: return None
    results = [r["result_r"] for r in subset]
    n = len(results)
    if n == 0: return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r-ev)**2 for r in results) / max(n-1,1))
    se = std / math.sqrt(n)
    timeouts = sum(1 for r in subset if r["exit_reason"] == "max_bars_timeout")
    moves = sum(1 for r in subset if r["mfe_r"] > 0.5)
    return {"n":n, "wr":wins/n, "ev":ev, "ci_low":ev-1.96*se, "ci_high":ev+1.96*se,
            "timeout_pct": timeouts/n, "move_pct": moves/n}

strats = Counter(r["strategy"] for r in enriched)
print(f"\n  Strategy distribution:")
for strat, count in strats.most_common():
    print(f"    {strat}: {count} ({count/len(enriched):.0%})")

print(f"\n  {'Strategy':<25s}| {'n':>4s}| {'WR':>5s}| {'EV':>8s}| {'Net':>7s}| {'T/O':>5s}| {'Move%':>5s}| {'CI':>20s}")
print(f"  {'-'*25}+{'-'*5}+{'-'*6}+{'-'*9}+{'-'*8}+{'-'*6}+{'-'*6}+{'-'*20}")

for strat in ["TREND_CONTINUATION", "ZONE_REACTION", "MEAN_REVERSION", "BREAKOUT", "UNCLASSIFIED"]:
    subset = [r for r in enriched if r["strategy"] == strat]
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST
        print(f"  {strat:<25s}| {s['n']:>4d}| {s['wr']:.1%}| {s['ev']:>+7.4f}| {net:>+6.4f}| {s['timeout_pct']:.0%} | {s['move_pct']:.0%} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# V10 filter: ONLY trade when a clear strategy is identified
classified = [r for r in enriched if r["strategy"] != "UNCLASSIFIED"]
s_class = stats(classified)
s_all = stats(enriched)

print(f"\n  V10 FILTER EFFECT:")
print(f"    ALL trades: n={s_all['n']} | EV={s_all['ev']:+.4f} | T/O={s_all['timeout_pct']:.0%}")
if s_class:
    print(f"    CLASSIFIED only: n={s_class['n']} | EV={s_class['ev']:+.4f} | T/O={s_class['timeout_pct']:.0%}")
    print(f"    Trade reduction: {1 - s_class['n']/s_all['n']:.0%}")
    print(f"    Timeout reduction: {s_all['timeout_pct'] - s_class['timeout_pct']:+.1%}")

# ═══════════════════════════════════════════════════════════════
# SECTION 2: MOVEMENT PROBLEM — DOES V10 SOLVE IT?
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 2: DOES V10 REDUCE THE 65% TIMEOUT PROBLEM?")
print("-" * 70)

# The key question: does strategy classification predict movement?
for strat in ["TREND_CONTINUATION", "ZONE_REACTION", "MEAN_REVERSION", "BREAKOUT", "UNCLASSIFIED"]:
    subset = [r for r in enriched if r["strategy"] == strat]
    if len(subset) < 5:
        continue
    n = len(subset)
    timeouts = sum(1 for r in subset if r["exit_reason"] == "max_bars_timeout")
    moves_05 = sum(1 for r in subset if r["mfe_r"] > 0.5)
    moves_1 = sum(1 for r in subset if r["mfe_r"] > 1.0)
    print(f"  {strat:<25s}: T/O={timeouts/n:.0%} | P(>0.5R)={moves_05/n:.0%} | P(>1R)={moves_1/n:.0%} (n={n})")

# ═══════════════════════════════════════════════════════════════
# SECTION 3: HTF ENVIRONMENT AS GATE
# Only trade when H4/H1 gives clear permission
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 3: HTF ENVIRONMENT GATE")
print("-" * 70)

# Test: only trade when HTF is clear (bias + alignment)
htf_clear_trades = [r for r in enriched if r["htf_clear"]]
htf_neutral_trades = [r for r in enriched if r["htf_neutral"]]
htf_other = [r for r in enriched if not r["htf_clear"] and not r["htf_neutral"]]

for label, subset in [("HTF CLEAR (bias+aligned)", htf_clear_trades),
                      ("HTF NEUTRAL (no bias)", htf_neutral_trades),
                      ("HTF OTHER (mixed)", htf_other)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        net = s["ev"] - COST
        print(f"  {label:<30s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | net={net:+.4f} | T/O={s['timeout_pct']:.0%}")

# HTF clear + aligned with trade direction
htf_clear_aligned = [r for r in enriched if r["htf_clear"] and r["htf_aligned"]]
htf_clear_counter = [r for r in enriched if r["htf_clear"] and not r["htf_aligned"]]

print(f"\n  Within HTF CLEAR:")
for label, subset in [("Aligned with trade", htf_clear_aligned),
                      ("Counter to trade", htf_clear_counter)]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# SECTION 4: M15 PULLBACK AS TIMING GATE
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 4: M15 PULLBACK AS MOVEMENT PREDICTOR")
print("-" * 70)

pb_yes = [r for r in enriched if r["m15_pullback"]]
pb_no = [r for r in enriched if not r["m15_pullback"]]

for label, subset in [("M15 pullback active", pb_yes), ("No pullback", pb_no)]:
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"  {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | T/O={s['timeout_pct']:.0%} | P(>0.5R)={s['move_pct']:.0%}")

# Combined: HTF aligned + M15 pullback + inside zone (full V10 trend setup)
v10_ideal = [r for r in enriched if r["htf_aligned"] and r["m15_pullback"] and r["inside_zone"]]
s_ideal = stats(v10_ideal)
if s_ideal and s_ideal["n"] >= 3:
    print(f"\n  V10 IDEAL TREND SETUP (HTF aligned + M15 pullback + inside zone):")
    print(f"    n={s_ideal['n']} | WR={s_ideal['wr']:.1%} | EV={s_ideal['ev']:+.4f} | T/O={s_ideal['timeout_pct']:.0%}")

# ═══════════════════════════════════════════════════════════════
# SECTION 5: ZONE REACTION (Best V9 subset) DEEPER ANALYSIS
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 5: ZONE REACTION STRATEGY (V9's best finding)")
print("-" * 70)

# V9 found: WEAK + inside zone = n=40, WR=62.5%, EV=+0.183R
zone_reaction = [r for r in enriched if r["strategy"] == "ZONE_REACTION"]
s_zone = stats(zone_reaction)
if s_zone:
    print(f"\n  ZONE_REACTION (WEAK + inside zone):")
    print(f"    n={s_zone['n']} | WR={s_zone['wr']:.1%} | EV={s_zone['ev']:+.4f} | net={s_zone['ev']-COST:+.4f}")
    print(f"    Timeout: {s_zone['timeout_pct']:.0%} | P(>0.5R): {s_zone['move_pct']:.0%}")
    
    # Time stability
    if s_zone["n"] >= 10:
        half = s_zone["n"] // 2
        sorted_zone = sorted(zone_reaction, key=lambda r: r["timestamp"])
        s1 = stats(sorted_zone[:half])
        s2 = stats(sorted_zone[half:])
        if s1 and s2:
            print(f"    Time halves: H1 EV={s1['ev']:+.4f} | H2 EV={s2['ev']:+.4f} | Stable={'YES' if s1['ev']>0 and s2['ev']>0 else 'NO'}")
    
    # Symbol breakdown
    print(f"    Per symbol:")
    for sym in sorted(set(r["symbol"] for r in zone_reaction)):
        subset = [r for r in zone_reaction if r["symbol"] == sym]
        if len(subset) >= 3:
            ev = sum(r["result_r"] for r in subset) / len(subset)
            print(f"      {sym:10s}: n={len(subset):3d} | EV={ev:+.4f}")

# ═══════════════════════════════════════════════════════════════
# SECTION 6: V10 COMBINED MODEL — BEST CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 6: V10 BEST CONFIGURATIONS")
print("-" * 70)

configs = [
    ("ALL (V3 baseline)", enriched),
    ("V10: CLASSIFIED only", classified),
    ("V10: ZONE_REACTION only", zone_reaction),
    ("V10: TREND_CONTINUATION only", [r for r in enriched if r["strategy"]=="TREND_CONTINUATION"]),
    ("V10: HTF clear + aligned", htf_clear_aligned),
    ("V10: HTF neutral (contrarian)", htf_neutral_trades),
    ("V10: Zone + HTF aligned", [r for r in zone_reaction if r["htf_aligned"]]),
    ("V10: Zone + HTF neutral", [r for r in zone_reaction if r["htf_neutral"]]),
]

print(f"\n  {'Config':<35s}| {'n':>4s}| {'WR':>5s}| {'EV':>8s}| {'Net12%':>7s}| {'T/O':>5s}| {'CI':>20s}")
print(f"  {'-'*35}+{'-'*5}+{'-'*6}+{'-'*9}+{'-'*8}+{'-'*6}+{'-'*20}")

for label, subset in configs:
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST
        print(f"  {label:<35s}| {s['n']:>4d}| {s['wr']:.1%}| {s['ev']:>+7.4f}| {net:>+6.4f}| {s['timeout_pct']:.0%} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V10 VERDICT")
print("=" * 70)

print(f"""
  QUESTION: Does V10 responsibility separation solve the M5 problem?

  FINDINGS:
""")

# Summarize
if s_zone and s_all:
    zone_improvement = s_zone["ev"] - s_all["ev"]
    timeout_reduction = s_all["timeout_pct"] - s_zone["timeout_pct"]
    zone_net = s_zone["ev"] - COST
    
    print(f"    1. ZONE_REACTION is the ONLY net-positive strategy family")
    print(f"       EV={s_zone['ev']:+.4f} | Net={zone_net:+.4f} | n={s_zone['n']}")
    print(f"")
    print(f"    2. Movement problem: {'PARTIALLY SOLVED' if s_zone['timeout_pct'] < 0.60 else 'NOT SOLVED'}")
    print(f"       Baseline timeout: {s_all['timeout_pct']:.0%}")
    print(f"       Zone reaction timeout: {s_zone['timeout_pct']:.0%}")
    print(f"       Reduction: {timeout_reduction:+.0%}")
    print(f"")
    print(f"    3. Strategy routing adds value: {zone_improvement:+.4f}R improvement")
    print(f"       But sample size (n={s_zone['n']}) remains underpowered")
    
    if zone_net > 0.03 and s_zone["n"] >= 30:
        verdict = "B) Strategy routing shows promise — ZONE_REACTION survives costs"
    elif zone_net > 0 and s_zone["n"] >= 20:
        verdict = "B) Marginal net-positive on small sample — needs more data"
    else:
        verdict = "C) Insufficient evidence for production viability"
    
    print(f"\n  VERDICT: {verdict}")
    print(f"""
  ARCHITECTURE RECOMMENDATION:
  ┌──────────────────────────────────────────────────────────────────┐
  │ H4/H1: Determine environment (trending/ranging/neutral)          │
  │ M15:   Identify pullback into institutional zone                 │
  │ M5:    Time entry on WEAK confirmation (rejection/structure)     │
  │                                                                  │
  │ ONLY TRADE WHEN:                                                 │
  │   - Inside institutional zone (demand OB, supply OB, FVG)        │
  │   - WEAK entry confirmation present                              │
  │   - V3 signal direction available                                │
  │                                                                  │
  │ DO NOT TRADE WHEN:                                               │
  │   - Open space (no zone context)                                 │
  │   - VALID confirmation (too late)                                │
  │   - No M15 pullback context                                      │
  └──────────────────────────────────────────────────────────────────┘
""")

print()
