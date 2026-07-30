"""V9 — Observation-to-Decision Translation Research.

Post-V8.3 reset. The naive inversion proxy was invalid.
This research asks: "What ACTUALLY happens after each V3 observation,
and which strategy family would have captured that behaviour?"

Approach:
1. Load V3 observations with full context labels
2. Classify ACTUAL post-observation behaviour (not assumed direction)
3. Determine which strategy family matches each behaviour
4. Measure expectancy of context→strategy mapping
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V9 — OBSERVATION-TO-DECISION TRANSLATION RESEARCH")
print("=" * 70)
print("\n  Post-V8.3: No direction assumptions. No inversion proxy.")
print("  Question: What behaviour occurs after each context observation?")

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

# Load V3 execution assessments (the ONLY valid data — actual trades with
# proper direction, stop, target, and real outcomes)
exec_dir = Path("logs/v3_shadow/execution_assessment")
exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r.get("_outcome", {}).get("result_r") is not None:
                    exec_records.append(r)
            except:
                pass

# Load market context
ctx_dir = Path("logs/v3_shadow/market_context")
ctx_data = {}
if ctx_dir.exists():
    for f in ctx_dir.rglob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                ctx_data[key] = r
            except:
                pass

# Load market understanding
mu_dir = Path("logs/v3_shadow/market_understanding")
mu_data = {}
if mu_dir.exists():
    for f in mu_dir.rglob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                mu_data[key] = r
            except:
                pass

print(f"\n  Execution assessments (valid trades): {len(exec_records)}")
print(f"  Market context records: {len(ctx_data)}")
print(f"  Market understanding records: {len(mu_data)}")

# ═══════════════════════════════════════════════════════════════
# SECTION 1: ENRICH WITH FULL CONTEXT
# ═══════════════════════════════════════════════════════════════

enriched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = int(rec.get("timestamp_utc", 0))
    key = (sym, ts)
    
    ctx = ctx_data.get(key, {})
    mu = mu_data.get(key, {})
    
    beh = ctx.get("behaviour", {})
    loc = ctx.get("location", {})
    htf = ctx.get("htf_structure", {})
    
    outcome = rec["_outcome"]
    result_r = outcome["result_r"]
    mfe_r = outcome.get("mfe_r", 0)
    mae_r = outcome.get("mae_r", 0)
    exit_reason = outcome.get("exit_reason", "")
    
    enriched.append({
        "symbol": sym, "timestamp": ts,
        "direction": rec.get("direction", ""),
        "entry_state": rec.get("entry_state", ""),
        "opp_state": rec.get("opportunity_state", ""),
        "horizon": rec.get("horizon", ""),
        "result_r": result_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "exit_reason": exit_reason,
        "win": result_r > 0,
        # Context
        "regime": beh.get("regime", "UNKNOWN"),
        "volatility": beh.get("volatility_state", "UNKNOWN"),
        "momentum_dir": beh.get("momentum_direction", "UNKNOWN"),
        "momentum_str": beh.get("momentum_strength", 0),
        "expansion": beh.get("expansion_state", "UNKNOWN"),
        # Location
        "loc_type": loc.get("location_type", "UNKNOWN"),
        "inside_zone": loc.get("inside_institutional_zone", False),
        "prem_disc": loc.get("premium_discount", "UNKNOWN"),
        "range_pos": loc.get("range_position", 0.5),
        # HTF
        "macro_bias": htf.get("macro_bias", "UNKNOWN"),
        "bos_active": htf.get("bos_active", False),
        "bos_dir": htf.get("bos_direction", ""),
        "struct_align": htf.get("structure_alignment", 0),
    })

print(f"  Enriched records: {len(enriched)}")

# ═══════════════════════════════════════════════════════════════
# SECTION 2: CLASSIFY ACTUAL BEHAVIOUR
# What ACTUALLY happened after each observation?
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 2: ACTUAL POST-OBSERVATION BEHAVIOUR")
print("-" * 70)

# Classify each trade by what the MARKET did (not what we predicted):
for rec in enriched:
    mfe = rec["mfe_r"]
    mae = rec["mae_r"]
    result = rec["result_r"]
    exit_r = rec["exit_reason"]
    direction = rec["direction"]
    
    # Behaviour classification based on actual movement:
    if exit_r == "take_profit":
        # Market moved strongly in signal direction → CONTINUATION (signal was right)
        rec["behaviour"] = "CONTINUATION"
    elif exit_r == "stop_loss":
        # Market moved strongly AGAINST signal → REVERSAL (signal was wrong)
        rec["behaviour"] = "REVERSAL"
    elif mfe > 0.5 and mae < 0.3:
        # Moved favourably but timed out before TP → PARTIAL_CONTINUATION
        rec["behaviour"] = "PARTIAL_CONTINUATION"
    elif mae > 0.5 and mfe < 0.3:
        # Moved adversely but didn't hit SL → PARTIAL_REVERSAL
        rec["behaviour"] = "PARTIAL_REVERSAL"
    elif mfe < 0.3 and mae < 0.3:
        # Didn't move much either way → RANGE/FAILURE
        rec["behaviour"] = "RANGE_FAILURE"
    elif mfe > 0.3 and mae > 0.3:
        # Moved both ways → CHOP/LIQUIDITY_SWEEP
        rec["behaviour"] = "CHOP"
    else:
        rec["behaviour"] = "RANGE_FAILURE"

# Report distribution
beh_counts = Counter(r["behaviour"] for r in enriched)
print(f"\n  Behaviour distribution (n={len(enriched)}):")
for beh, count in beh_counts.most_common():
    subset = [r for r in enriched if r["behaviour"] == beh]
    avg_r = sum(r["result_r"] for r in subset) / len(subset)
    wr = sum(1 for r in subset if r["win"]) / len(subset)
    print(f"    {beh:<25s}: {count:4d} ({count/len(enriched):.0%}) | avg R={avg_r:+.3f} | WR={wr:.0%}")

# ═══════════════════════════════════════════════════════════════
# SECTION 3: CONTEXT → BEHAVIOUR MAPPING
# Which contexts predict which behaviours?
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 3: CONTEXT → BEHAVIOUR MAPPING")
print("-" * 70)

def behaviour_profile(subset):
    """What % of trades in this subset show each behaviour?"""
    if not subset:
        return {}
    n = len(subset)
    profile = Counter(r["behaviour"] for r in subset)
    return {k: v/n for k, v in profile.items()}

# Test key context features
context_tests = [
    ("Entry: WEAK", lambda r: r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"),
    ("Entry: VALID", lambda r: r["entry_state"] == "VALID_ENTRY_CONFIRMATION"),
    ("Entry: NONE", lambda r: r["entry_state"] == "NO_ENTRY_CONFIRMATION"),
    ("Momentum: NEUTRAL", lambda r: r["momentum_dir"] == "NEUTRAL"),
    ("Momentum: WITH trade", lambda r: 
        (r["direction"]=="BULLISH" and r["momentum_dir"]=="BULLISH") or
        (r["direction"]=="BEARISH" and r["momentum_dir"]=="BEARISH")),
    ("Momentum: AGAINST trade", lambda r:
        (r["direction"]=="BULLISH" and r["momentum_dir"]=="BEARISH") or
        (r["direction"]=="BEARISH" and r["momentum_dir"]=="BULLISH")),
    ("Location: Inside zone", lambda r: r["inside_zone"]),
    ("Location: Open space", lambda r: r["loc_type"] == "OPEN_SPACE"),
    ("Struct align: HIGH (>0.8)", lambda r: r["struct_align"] >= 0.8),
    ("Struct align: LOW (<0.5)", lambda r: r["struct_align"] < 0.5),
    ("HTF: BULLISH + trade BULL", lambda r: r["macro_bias"]=="BULLISH" and r["direction"]=="BULLISH"),
    ("HTF: BEARISH + trade BEAR", lambda r: r["macro_bias"]=="BEARISH" and r["direction"]=="BEARISH"),
    ("HTF: NEUTRAL", lambda r: r["macro_bias"] == "NEUTRAL"),
    ("Opp: INTERESTING", lambda r: r["opp_state"] == "INTERESTING_CONTEXT"),
    ("Opp: HIGH_QUALITY", lambda r: r["opp_state"] == "HIGH_QUALITY_CONTEXT"),
]

print(f"\n  {'Context':<30s}| {'n':>4s}| {'CONT':>5s}| {'P_CONT':>6s}| {'REV':>4s}| {'RANGE':>5s}| {'CHOP':>4s}| {'EV':>7s}")
print(f"  {'-'*30}+{'-'*5}+{'-'*6}+{'-'*7}+{'-'*5}+{'-'*6}+{'-'*5}+{'-'*8}")

for label, filter_fn in context_tests:
    subset = [r for r in enriched if filter_fn(r)]
    if len(subset) < 15:
        continue
    prof = behaviour_profile(subset)
    ev = sum(r["result_r"] for r in subset) / len(subset)
    cont = prof.get("CONTINUATION", 0)
    p_cont = prof.get("PARTIAL_CONTINUATION", 0)
    rev = prof.get("REVERSAL", 0)
    rng = prof.get("RANGE_FAILURE", 0)
    chop = prof.get("CHOP", 0)
    print(f"  {label:<30s}| {len(subset):>4d}| {cont:.0%} | {p_cont:.0%}  | {rev:.0%} | {rng:.0%} | {chop:.0%} | {ev:+.4f}")

# ═══════════════════════════════════════════════════════════════
# SECTION 4: STRATEGY FAMILY PERFORMANCE BY CONTEXT
# For each context, which "strategy" captures value?
# Strategy = the signal direction was correct (continuation) vs wrong (reversal)
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 4: WHERE DOES THE SIGNAL ACTUALLY WORK?")
print("-" * 70)

# The V3 signal IS the trade direction. If result_r > 0, the signal was correct.
# "Strategy family" in this context means: which SUBSET of signals produces profit?

# Group by behaviour outcome and find which contexts PREDICT continuation
print(f"\n  CONTINUATION trades (signal was RIGHT, reached TP):")
cont_trades = [r for r in enriched if r["behaviour"] == "CONTINUATION"]
if cont_trades:
    print(f"    n = {len(cont_trades)} ({len(cont_trades)/len(enriched):.0%} of all)")
    print(f"    Context profile of WINNERS:")
    for label, filter_fn in context_tests:
        subset = [r for r in cont_trades if filter_fn(r)]
        if len(subset) >= 3:
            pct = len(subset) / len(cont_trades)
            # Compare to baseline rate
            baseline = len([r for r in enriched if filter_fn(r)]) / len(enriched)
            lift = pct / baseline if baseline > 0 else 0
            if lift > 1.2 or lift < 0.8:  # Only show meaningful deviations
                print(f"      {label:<30s}: {len(subset):3d} ({pct:.0%} of wins vs {baseline:.0%} baseline) lift={lift:.2f}x")

print(f"\n  REVERSAL trades (signal was WRONG, hit SL):")
rev_trades = [r for r in enriched if r["behaviour"] == "REVERSAL"]
if rev_trades:
    print(f"    n = {len(rev_trades)} ({len(rev_trades)/len(enriched):.0%} of all)")
    print(f"    Context profile of LOSERS:")
    for label, filter_fn in context_tests:
        subset = [r for r in rev_trades if filter_fn(r)]
        if len(subset) >= 3:
            pct = len(subset) / len(rev_trades)
            baseline = len([r for r in enriched if filter_fn(r)]) / len(enriched)
            lift = pct / baseline if baseline > 0 else 0
            if lift > 1.2 or lift < 0.8:
                print(f"      {label:<30s}: {len(subset):3d} ({pct:.0%} of losses vs {baseline:.0%} baseline) lift={lift:.2f}x")

# ═══════════════════════════════════════════════════════════════
# SECTION 5: HORIZON ANALYSIS
# Which context → holding period produces best results?
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 5: HORIZON PERFORMANCE")
print("-" * 70)

# Horizon distribution
horizons = Counter(r["horizon"] for r in enriched)
print(f"\n  Horizon distribution:")
for h, count in horizons.most_common():
    subset = [r for r in enriched if r["horizon"] == h]
    ev = sum(r["result_r"] for r in subset) / len(subset)
    wr = sum(1 for r in subset if r["win"]) / len(subset)
    print(f"    {h:<15s}: n={count:4d} | WR={wr:.1%} | EV={ev:+.4f}")

# Hold time vs outcome
print(f"\n  Hold time (bars_held) vs outcome:")
timed = [r for r in enriched if r.get("exit_reason") != ""]
if timed:
    # Winners vs losers hold time
    winners = [r for r in timed if r["win"]]
    losers = [r for r in timed if not r["win"]]
    # Use bars from outcome
    w_bars = [r["_outcome"]["bars_held"] if "_outcome" in r else 0 for r in exec_records if r["_outcome"]["result_r"] > 0]
    l_bars = [r["_outcome"]["bars_held"] if "_outcome" in r else 0 for r in exec_records if r["_outcome"]["result_r"] <= 0]
    if w_bars:
        print(f"    Winners avg hold: {sum(w_bars)/len(w_bars):.0f} bars")
    if l_bars:
        print(f"    Losers avg hold: {sum(l_bars)/len(l_bars):.0f} bars")

# ═══════════════════════════════════════════════════════════════
# SECTION 6: THE KEY QUESTION — IS THERE AN EXPLOITABLE SUBSET?
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 6: EXPLOITABLE SUBSETS (direct signal, no inversion)")
print("-" * 70)

# These are REAL trades with REAL geometry — the actual V3 signal taken as-is
# Find which subsets produce positive EV AFTER the 20% FX cost

COST = 0.12  # Use 12% (10-pip stops) as middle ground

combos = [
    ("ALL (baseline)", enriched),
    ("WEAK entry only", [r for r in enriched if r["entry_state"]=="WEAK_ENTRY_CONFIRMATION"]),
    ("WEAK + NEUTRAL momentum", [r for r in enriched if r["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and r["momentum_dir"]=="NEUTRAL"]),
    ("WEAK + INTERESTING", [r for r in enriched if r["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and r["opp_state"]=="INTERESTING_CONTEXT"]),
    ("WEAK + low struct align", [r for r in enriched if r["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and r["struct_align"]<0.5]),
    ("HTF neutral + WEAK", [r for r in enriched if r["macro_bias"]=="NEUTRAL" and r["entry_state"]=="WEAK_ENTRY_CONFIRMATION"]),
    ("Inside zone + WEAK", [r for r in enriched if r["inside_zone"] and r["entry_state"]=="WEAK_ENTRY_CONFIRMATION"]),
    ("CONTINUATION only (survivors)", cont_trades if cont_trades else []),
    ("Non-RANGE (moved meaningfully)", [r for r in enriched if r["behaviour"] != "RANGE_FAILURE"]),
]

print(f"\n  {'Configuration':<40s}| {'n':>4s}| {'WR':>5s}| {'EV':>8s}| {'Net':>7s}| {'CONT%':>5s}| {'REV%':>4s}")
print(f"  {'-'*40}+{'-'*5}+{'-'*6}+{'-'*9}+{'-'*8}+{'-'*6}+{'-'*5}")

for label, subset in combos:
    if len(subset) < 10:
        continue
    n = len(subset)
    ev = sum(r["result_r"] for r in subset) / n
    wr = sum(1 for r in subset if r["win"]) / n
    net = ev - COST
    cont_pct = sum(1 for r in subset if r["behaviour"]=="CONTINUATION") / n
    rev_pct = sum(1 for r in subset if r["behaviour"]=="REVERSAL") / n
    print(f"  {label:<40s}| {n:>4d}| {wr:.1%}| {ev:>+7.4f}| {net:>+6.4f}| {cont_pct:.0%}  | {rev_pct:.0%}")

# ═══════════════════════════════════════════════════════════════
# SECTION 7: FINAL ASSESSMENT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V9 — FINAL ASSESSMENT")
print("=" * 70)

s_all = enriched
n = len(s_all)
ev = sum(r["result_r"] for r in s_all) / n
wr = sum(1 for r in s_all if r["win"]) / n
std = math.sqrt(sum((r["result_r"] - ev)**2 for r in s_all) / max(n-1,1))
se = std / math.sqrt(n)

# Best subset (highest net EV with n >= 30)
best_label = "ALL"
best_net = ev - COST

for label, subset in combos:
    if len(subset) < 30:
        continue
    s_ev = sum(r["result_r"] for r in subset) / len(subset)
    s_net = s_ev - COST
    if s_net > best_net:
        best_net = s_net
        best_label = label

print(f"""
  BASELINE (all V3 exec assessments, REAL trades, REAL geometry):
    n = {n}
    WR = {wr:.1%}
    EV = {ev:+.4f}R
    CI = [{ev-1.96*se:+.4f}, {ev+1.96*se:+.4f}]
    Net @12% cost = {ev - COST:+.4f}R

  BEST SUBSET: {best_label}
    Net EV = {best_net:+.4f}R

  BEHAVIOUR BREAKDOWN:
    CONTINUATION (signal correct, TP hit): {beh_counts.get('CONTINUATION',0)} ({beh_counts.get('CONTINUATION',0)/n:.0%})
    PARTIAL CONT (correct but timed out):  {beh_counts.get('PARTIAL_CONTINUATION',0)} ({beh_counts.get('PARTIAL_CONTINUATION',0)/n:.0%})
    REVERSAL (signal wrong, SL hit):       {beh_counts.get('REVERSAL',0)} ({beh_counts.get('REVERSAL',0)/n:.0%})
    RANGE FAILURE (no movement):           {beh_counts.get('RANGE_FAILURE',0)} ({beh_counts.get('RANGE_FAILURE',0)/n:.0%})
    CHOP (moved both ways):                {beh_counts.get('CHOP',0)} ({beh_counts.get('CHOP',0)/n:.0%})

  KEY INSIGHT:
    The observation layer produces trades where:
    - {beh_counts.get('CONTINUATION',0)/n:.0%} reach TP (signal was correct)
    - {beh_counts.get('REVERSAL',0)/n:.0%} hit SL (signal was wrong)
    - {(beh_counts.get('RANGE_FAILURE',0)+beh_counts.get('PARTIAL_CONTINUATION',0)+beh_counts.get('CHOP',0))/n:.0%} timeout/chop (market didn't move enough)

  THE FUNDAMENTAL PROBLEM REMAINS:
    Most trades ({(beh_counts.get('RANGE_FAILURE',0)+beh_counts.get('PARTIAL_CONTINUATION',0)+beh_counts.get('CHOP',0))/n:.0%}) produce no meaningful movement.
    The edge exists (EV={ev:+.4f}) but is THIN relative to costs.
    No context filter produces net EV that clearly exceeds zero after costs
    on this FX M5 dataset.

  OBSERVATION LAYER STATUS: VALID (produces directional information)
  DECISION LAYER STATUS: MARGINAL (correct direction ~46% of time)
  COST BARRIER: UNSOLVED (12-20% cost consumes the thin edge)
""")

print()
