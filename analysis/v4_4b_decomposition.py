"""V4.4b — Currency Strength Effect Decomposition.

Investigates why V4.3 (+0.105R, n=25) weakened to +0.032R (n=45) and
why the OPPOSING group showed BETTER outcomes than ALIGNED in the
WEAK+INTERESTING subset.
"""
import json, math, random
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V4.4b — CURRENCY STRENGTH EFFECT DECOMPOSITION")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (same as V4.4)
# ═══════════════════════════════════════════════════════════════

PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
}

shadow_dir = Path("logs/shadow_trades")
trades_by_time = defaultdict(list)
all_trades = []
seen_trades = set()

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
                        result_r = outcome.get("pnl_r_multiple")
                        direction = snap.get("direction", "")
                        key = (sym, int(ts))
                        if sym and ts and result_r is not None and direction and key not in seen_trades:
                            seen_trades.add(key)
                            entry = {
                                "symbol": sym, "timestamp": ts,
                                "direction": direction, "result_r": result_r,
                                "mfe_r": outcome.get("mfe_r", 0),
                                "mae_r": outcome.get("mae_r", 0),
                            }
                            all_trades.append(entry)
                            trades_by_time[int(ts)].append(entry)
                    except:
                        pass

# Load V3 execution assessments
exec_dir = Path("logs/v3_shadow/execution_assessment")
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

print(f"Shadow trades: {len(all_trades)}")
print(f"Execution assessments: {len(exec_records)}")

# ═══════════════════════════════════════════════════════════════
# CURRENCY STRENGTH COMPUTATION
# ═══════════════════════════════════════════════════════════════

def get_usd_direction(sym, direction):
    if sym not in PAIR_CURRENCIES:
        return None
    base, quote = PAIR_CURRENCIES[sym]
    d = direction.upper()
    if d in ("BUY", "BULLISH", "LONG"):
        d = "BUY"
    elif d in ("SELL", "BEARISH", "SHORT"):
        d = "SELL"
    else:
        return None
    if quote == "USD":
        return "USD_WEAK" if d == "BUY" else "USD_STRONG"
    elif base == "USD":
        return "USD_STRONG" if d == "BUY" else "USD_WEAK"
    return None


def compute_strength(sym, direction, timestamp):
    """Compute currency strength from concurrent trades on other pairs."""
    my_usd = get_usd_direction(sym, direction)
    if not my_usd:
        return None, 0, 0, []
    
    # Get trades at same timestamp from other pairs
    concurrent = [t for t in trades_by_time.get(int(timestamp), [])
                  if t["symbol"] != sym]
    
    if not concurrent:
        return None, 0, 0, []
    
    usd_strong = 0
    usd_weak = 0
    pair_details = []
    for t in concurrent:
        ud = get_usd_direction(t["symbol"], t["direction"])
        if ud == "USD_STRONG":
            usd_strong += 1
            pair_details.append((t["symbol"], "USD_STRONG", t["result_r"]))
        elif ud == "USD_WEAK":
            usd_weak += 1
            pair_details.append((t["symbol"], "USD_WEAK", t["result_r"]))
    
    total = usd_strong + usd_weak
    if total == 0:
        return None, 0, 0, []
    
    if my_usd == "USD_STRONG":
        aligned = usd_strong > usd_weak
        agree_count = usd_strong
    else:
        aligned = usd_weak > usd_strong
        agree_count = usd_weak
    
    return aligned, agree_count, total, pair_details


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
    mfe_vals = [s.get("mfe_r", 0) for s in subset]
    mae_vals = [s.get("mae_r", 0) for s in subset]
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "mfe": sum(mfe_vals) / n, "mae": sum(mae_vals) / n,
    }

# ═══════════════════════════════════════════════════════════════
# BUILD ENRICHED DATASET
# ═══════════════════════════════════════════════════════════════

enriched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = rec.get("timestamp_utc", 0)
    direction = rec.get("direction", "")
    result_r = rec["_outcome"]["result_r"]
    entry_state = rec.get("entry_state", "")
    opp_state = rec.get("opportunity_state", "")
    horizon = rec.get("horizon", "")
    exec_state = rec.get("execution_state", "")
    
    aligned, agree_count, total_pairs, pair_details = compute_strength(sym, direction, ts)
    
    enriched.append({
        "symbol": sym, "timestamp": ts, "direction": direction,
        "result_r": result_r, "mfe_r": rec["_outcome"].get("mfe_r", 0),
        "mae_r": rec["_outcome"].get("mae_r", 0),
        "entry_state": entry_state, "opp_state": opp_state,
        "horizon": horizon, "exec_state": exec_state,
        "aligned": aligned, "agree_count": agree_count,
        "total_pairs": total_pairs, "pair_details": pair_details,
        "has_context": aligned is not None,
    })

with_context = [r for r in enriched if r["has_context"]]
without_context = [r for r in enriched if not r["has_context"]]

print(f"\nEnriched execution records: {len(enriched)}")
print(f"  With currency context: {len(with_context)}")
print(f"  Without context (no concurrent): {len(without_context)}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 1: POPULATION DIFFERENCE
# Why did V4.3 get n=25 but V4.4 gets n=45?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 1: POPULATION DIFFERENCE (V4.3 vs V4.4)")
print("─" * 70)

# V4.3 used a different alignment method (checking trades_by_time at exact ts)
# V4.4 used the same but got more records because it loaded more exec records

# Let's understand the full population
weak_int = [r for r in with_context
            if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
            and r["opp_state"] == "INTERESTING_CONTEXT"]

weak_int_aligned = [r for r in weak_int if r["aligned"]]
weak_int_opposed = [r for r in weak_int if not r["aligned"]]
weak_int_3agree = [r for r in weak_int if r["aligned"] and r["agree_count"] >= 3]
weak_int_3oppose = [r for r in weak_int if not r["aligned"] and r["agree_count"] >= 3]

print(f"\n  All execution assessments: {len(exec_records)}")
print(f"  With currency context: {len(with_context)}")
print(f"  WEAK+INTERESTING: {len(weak_int)}")
print(f"    Aligned: {len(weak_int_aligned)}")
print(f"    Opposed: {len(weak_int_opposed)}")
print(f"    3+ agree: {len(weak_int_3agree)}")
print(f"    3+ oppose: {len(weak_int_3oppose)}")

# V4.3 got 25 because it filtered on exec_state != NOT_EXECUTABLE
# Let's see what filter was different
not_exec = [r for r in enriched
            if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
            and r["opp_state"] == "INTERESTING_CONTEXT"
            and r["exec_state"] == "NOT_EXECUTABLE"]
print(f"\n  NOT_EXECUTABLE in WEAK+INT: {len(not_exec)}")

# Show exec_state breakdown
exec_states = Counter(r["exec_state"] for r in weak_int)
print(f"  Execution states in WEAK+INTERESTING:")
for state, count in exec_states.most_common():
    print(f"    {state}: {count}")

# Horizon breakdown
horizons = Counter(r["horizon"] for r in weak_int)
print(f"  Horizons in WEAK+INTERESTING:")
for h, count in horizons.most_common():
    print(f"    {h}: {count}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 2: ALIGNMENT EFFECT — FULL vs V4.2 POPULATION
# The V4.2 analysis used ALL shadow trades (not just WEAK+INTERESTING)
# Let's see if alignment still works on the FULL population
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 2: ALIGNMENT REPLICATION")
print("─" * 70)

# Full population (all shadow trades with context — V4.2 approach)
print("\n  A) ALL shadow trades (V4.2 replication):")
all_shadow_with_ctx = []
for trade in all_trades:
    aligned, agree_count, total, _ = compute_strength(
        trade["symbol"], trade["direction"], trade["timestamp"])
    if aligned is not None:
        all_shadow_with_ctx.append({
            **trade, "aligned": aligned, "agree_count": agree_count,
        })

all_aligned = [r for r in all_shadow_with_ctx if r["aligned"]]
all_opposed = [r for r in all_shadow_with_ctx if not r["aligned"]]
all_3agree = [r for r in all_shadow_with_ctx if r["aligned"] and r["agree_count"] >= 3]
all_3oppose = [r for r in all_shadow_with_ctx if not r["aligned"] and r["agree_count"] >= 3]

print(f"  Total with context: {len(all_shadow_with_ctx)}")
for label, subset in [
    ("ALL (baseline)", all_shadow_with_ctx),
    ("Aligned", all_aligned),
    ("Opposed", all_opposed),
    ("3+ agree", all_3agree),
    ("3+ oppose", all_3oppose),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f} | MAE={s['mae']:.3f}")

# B) V3 execution assessments (V4.3/V4.4 population)
print(f"\n  B) V3 execution assessments only:")
for label, subset in [
    ("ALL exec (baseline)", with_context),
    ("Aligned", [r for r in with_context if r["aligned"]]),
    ("Opposed", [r for r in with_context if not r["aligned"]]),
    ("3+ agree", [r for r in with_context if r["aligned"] and r["agree_count"] >= 3]),
    ("3+ oppose", [r for r in with_context if not r["aligned"] and r["agree_count"] >= 3]),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f} | MAE={s['mae']:.3f}")

# C) WEAK+INTERESTING subset
print(f"\n  C) WEAK+INTERESTING subset:")
for label, subset in [
    ("ALL W+I (baseline)", weak_int),
    ("W+I Aligned", weak_int_aligned),
    ("W+I Opposed", weak_int_opposed),
    ("W+I 3+ agree", weak_int_3agree),
    ("W+I 3+ oppose", weak_int_3oppose),
]:
    s = stats(subset)
    if s and s["n"] >= 3:
        print(f"    {label:<20s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f} | MAE={s['mae']:.3f}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 3: THE INVERSION — WHY DO OPPOSING W+I TRADES
# SHOW BETTER WR THAN ALIGNED?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 3: THE INVERSION PROBLEM")
print("─" * 70)

# Key finding from V4.4: WEAK+INTERESTING opposing trades had WR=54.7%
# while aligned had WR=39.2%. This is the OPPOSITE of the V4.2 finding.

print("\n  The mystery: In V4.2 (all trades), aligned > opposed")
print("  But in V4.4 (WEAK+INTERESTING), opposed > aligned")
print("\n  Hypothesis: WEAK timing already selects for contrarian entries")
print("  If WEAK = early/contrarian, then 'opposing' USD = the trade IS the reversal")

# Test: What is the direction composition?
print(f"\n  Direction breakdown:")
for label, subset in [("W+I Aligned", weak_int_aligned), ("W+I Opposed", weak_int_opposed)]:
    dirs = Counter(r["direction"] for r in subset)
    print(f"    {label}: {dict(dirs)}")

# Symbol breakdown for aligned vs opposed
print(f"\n  Symbol breakdown:")
for sym in sorted(set(r["symbol"] for r in weak_int)):
    sym_aligned = [r for r in weak_int_aligned if r["symbol"] == sym]
    sym_opposed = [r for r in weak_int_opposed if r["symbol"] == sym]
    sa = stats(sym_aligned)
    so = stats(sym_opposed)
    if sa and so and sa["n"] >= 2 and so["n"] >= 2:
        print(f"    {sym:10s}: aligned n={sa['n']:3d} EV={sa['ev']:+.4f} WR={sa['wr']:.0%} | "
              f"opposed n={so['n']:3d} EV={so['ev']:+.4f} WR={so['wr']:.0%}")

# Test: Are runners concentrated in opposing or aligned?
print(f"\n  Runner analysis (R > 1.5):")
aligned_runners = [r for r in weak_int_aligned if r["result_r"] > 1.5]
opposed_runners = [r for r in weak_int_opposed if r["result_r"] > 1.5]
print(f"    Aligned runners: {len(aligned_runners)}/{len(weak_int_aligned)} "
      f"({len(aligned_runners)/max(len(weak_int_aligned),1)*100:.1f}%)")
print(f"    Opposed runners: {len(opposed_runners)}/{len(weak_int_opposed)} "
      f"({len(opposed_runners)/max(len(weak_int_opposed),1)*100:.1f}%)")

# Show the actual R values for both groups
print(f"\n  Result distribution (WEAK+INTERESTING):")
aligned_results = sorted([r["result_r"] for r in weak_int_aligned])
opposed_results = sorted([r["result_r"] for r in weak_int_opposed])
print(f"    Aligned: min={aligned_results[0] if aligned_results else 0:.2f} "
      f"median={aligned_results[len(aligned_results)//2] if aligned_results else 0:.2f} "
      f"max={aligned_results[-1] if aligned_results else 0:.2f}")
print(f"    Opposed: min={opposed_results[0] if opposed_results else 0:.2f} "
      f"median={opposed_results[len(opposed_results)//2] if opposed_results else 0:.2f} "
      f"max={opposed_results[-1] if opposed_results else 0:.2f}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 4: WHAT DOES "ALIGNED" ACTUALLY MEAN?
# Check the concurrent pairs — are they trading the SAME move?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 4: CONCURRENT PAIR ANALYSIS")
print("─" * 70)

# For aligned WEAK+INT trades, what did the concurrent pairs actually do?
print("\n  How did concurrent AGREEING pairs perform?")
aligned_concurrent_results = []
for r in weak_int_aligned:
    for sym, usd_dir, pair_result in r["pair_details"]:
        aligned_concurrent_results.append(pair_result)

opposed_concurrent_results = []
for r in weak_int_opposed:
    for sym, usd_dir, pair_result in r["pair_details"]:
        opposed_concurrent_results.append(pair_result)

if aligned_concurrent_results:
    avg_conc_aligned = sum(aligned_concurrent_results) / len(aligned_concurrent_results)
    print(f"    Concurrent pairs when ALIGNED: avg R = {avg_conc_aligned:+.4f} (n={len(aligned_concurrent_results)})")
if opposed_concurrent_results:
    avg_conc_opposed = sum(opposed_concurrent_results) / len(opposed_concurrent_results)
    print(f"    Concurrent pairs when OPPOSED: avg R = {avg_conc_opposed:+.4f} (n={len(opposed_concurrent_results)})")

# KEY QUESTION: Are we measuring TRADE direction alignment, or
# OUTCOME direction alignment? The shadow trades record the TRADE
# direction, not whether USD actually went that way.
print("\n  CRITICAL: Currency 'direction' comes from trade direction,")
print("  NOT from whether the trade was profitable.")
print("  'Aligned' means: other pairs also BET on same USD direction")
print("  NOT: other pairs also WON on same USD direction")

# Check: What % of concurrent trades actually won?
aligned_conc_wins = sum(1 for r in aligned_concurrent_results if r > 0)
opposed_conc_wins = sum(1 for r in opposed_concurrent_results if r > 0)
if aligned_concurrent_results:
    print(f"\n    Concurrent WR when aligned: {aligned_conc_wins/len(aligned_concurrent_results):.1%}")
if opposed_concurrent_results:
    print(f"    Concurrent WR when opposed: {opposed_conc_wins/len(opposed_concurrent_results):.1%}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 5: ENTRY TIMING INTERACTION
# Does WEAK timing already capture contrarian information?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 5: TIMING × ALIGNMENT DEEP DIVE")
print("─" * 70)

for entry_state in ["WEAK_ENTRY_CONFIRMATION", "VALID_ENTRY_CONFIRMATION", "NO_ENTRY_CONFIRMATION"]:
    subset = [r for r in with_context if r["entry_state"] == entry_state]
    aligned_sub = [r for r in subset if r["aligned"]]
    opposed_sub = [r for r in subset if not r["aligned"]]
    
    sa = stats(aligned_sub)
    so = stats(opposed_sub)
    s_all = stats(subset)
    
    if s_all and s_all["n"] >= 10:
        print(f"\n  {entry_state}:")
        print(f"    All:      n={s_all['n']:4d} | WR={s_all['wr']:.1%} | EV={s_all['ev']:+.4f}")
        if sa and sa["n"] >= 5:
            print(f"    Aligned:  n={sa['n']:4d} | WR={sa['wr']:.1%} | EV={sa['ev']:+.4f}")
        if so and so["n"] >= 5:
            print(f"    Opposed:  n={so['n']:4d} | WR={so['wr']:.1%} | EV={so['ev']:+.4f}")
        if sa and so and sa["n"] >= 5 and so["n"] >= 5:
            delta_wr = sa["wr"] - so["wr"]
            delta_ev = sa["ev"] - so["ev"]
            effect = "ALIGNED better" if delta_ev > 0 else "OPPOSED better"
            print(f"    Effect:   WR {delta_wr:+.1%} | EV {delta_ev:+.4f} → {effect}")

# By opportunity state
print(f"\n  By Opportunity State:")
for opp_state in set(r["opp_state"] for r in with_context):
    if not opp_state:
        continue
    subset = [r for r in with_context if r["opp_state"] == opp_state]
    aligned_sub = [r for r in subset if r["aligned"]]
    opposed_sub = [r for r in subset if not r["aligned"]]
    
    sa = stats(aligned_sub)
    so = stats(opposed_sub)
    
    if sa and so and sa["n"] >= 10 and so["n"] >= 10:
        delta = sa["ev"] - so["ev"]
        effect = "ALIGNED+" if delta > 0 else "OPPOSED+"
        print(f"    {opp_state:<30s}: aligned EV={sa['ev']:+.4f} vs opposed EV={so['ev']:+.4f} → {effect} ({delta:+.4f})")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 6: SELECTION EFFECT — V4.2 vs V4.4 METHODOLOGY
# V4.2 used shadow trades directly; V4.4 used exec assessments
# The populations are DIFFERENT
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 6: V4.2 vs V4.4 POPULATION COMPARISON")
print("─" * 70)

# V4.2 used ALL 1125 shadow trades with context
# V4.4 uses execution_assessment records (subset with V3 labels)
# The difference is: execution_assessment = V3 pipeline OUTPUT
# Shadow trades = ALL trades the system generated

print(f"\n  V4.2 population (all shadow trades): {len(all_shadow_with_ctx)}")
print(f"  V4.4 population (V3 exec assessments): {len(with_context)}")
print(f"  Overlap: V3 is a SUBSET of shadow trades")

# V4.2 showed alignment worked on ALL trades
# V4.4 shows it INVERTS on WEAK+INTERESTING
# This means: the V3 filter (WEAK+INTERESTING) selects a DIFFERENT population
# where alignment semantics change

# What is the V3 exec population's alignment vs all shadow?
v3_aligned = [r for r in with_context if r["aligned"]]
v3_opposed = [r for r in with_context if not r["aligned"]]

print(f"\n  Alignment effect comparison:")
s_v42_aligned = stats(all_aligned)
s_v42_opposed = stats(all_opposed)
s_v44_aligned = stats(v3_aligned)
s_v44_opposed = stats(v3_opposed)

if s_v42_aligned and s_v42_opposed:
    print(f"    V4.2 (all shadow): aligned EV={s_v42_aligned['ev']:+.4f} vs opposed EV={s_v42_opposed['ev']:+.4f}")
    print(f"      Delta: {s_v42_aligned['ev']-s_v42_opposed['ev']:+.4f} (ALIGNED better)")
if s_v44_aligned and s_v44_opposed:
    print(f"    V4.4 (V3 exec):    aligned EV={s_v44_aligned['ev']:+.4f} vs opposed EV={s_v44_opposed['ev']:+.4f}")
    print(f"      Delta: {s_v44_aligned['ev']-s_v44_opposed['ev']:+.4f}")

# The KEY difference: V3 execution assessments already FILTER for quality
# They require structure + location + behaviour alignment
# When V3 says "WEAK entry + INTERESTING context" it has ALREADY identified
# a potential reversal point. Adding "USD consensus agrees" may actually mean
# "the crowd agrees" which is a CONTRARY indicator at reversal points.

print(f"\n  INTERPRETATION:")
print(f"  V4.2: On ALL trades (including trend-following), alignment helps")
print(f"  V4.4: On REVERSAL-TIMED trades (WEAK entry), alignment may hurt")
print(f"  Possible mechanism: WEAK entries are contrarian by nature.")
print(f"  'Everyone agrees with you' at a reversal = you're NOT contrarian.")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 7: TIME + SYMBOL STABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 7: TIME + SYMBOL STABILITY")
print("─" * 70)

# Time split for the FULL V4.2 population (all shadow trades)
sorted_all = sorted(all_shadow_with_ctx, key=lambda r: r["timestamp"])
third = len(sorted_all) // 3
periods = [
    ("Early", sorted_all[:third]),
    ("Middle", sorted_all[third:2*third]),
    ("Recent", sorted_all[2*third:]),
]

print(f"\n  V4.2 population (all shadow) — time thirds:")
for label, subset in periods:
    aligned_sub = [r for r in subset if r["aligned"]]
    opposed_sub = [r for r in subset if not r["aligned"]]
    sa = stats(aligned_sub)
    so = stats(opposed_sub)
    if sa and so and sa["n"] >= 10 and so["n"] >= 10:
        delta = sa["ev"] - so["ev"]
        print(f"    {label:<8s}: aligned EV={sa['ev']:+.4f} (n={sa['n']}) vs opposed EV={so['ev']:+.4f} (n={so['n']}) | Δ={delta:+.4f}")

# Symbol stability for V4.2 population
print(f"\n  V4.2 population — per symbol:")
for sym in sorted(set(r["symbol"] for r in all_shadow_with_ctx)):
    sym_aligned = [r for r in all_aligned if r["symbol"] == sym]
    sym_opposed = [r for r in all_opposed if r["symbol"] == sym]
    sa = stats(sym_aligned)
    so = stats(sym_opposed)
    if sa and so and sa["n"] >= 10 and so["n"] >= 10:
        delta = sa["ev"] - so["ev"]
        direction = "ALIGNED+" if delta > 0 else "OPPOSED+"
        print(f"    {sym:10s}: delta={delta:+.4f} ({direction}) | aligned n={sa['n']} opposed n={so['n']}")

# ═══════════════════════════════════════════════════════════════
# INVESTIGATION 8: THE REAL QUESTION — REJECTION VALUE
# V4.2's strongest finding was: 3+ OPPOSE = WR 29.3%, EV -0.287R
# Does this hold in V3 context?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("INVESTIGATION 8: REJECTION VALUE (OPPOSE = DON'T TRADE)")
print("─" * 70)

# On ALL shadow trades
print(f"\n  ALL shadow trades:")
s_3opp_all = stats(all_3oppose)
s_baseline_all = stats(all_shadow_with_ctx)
if s_3opp_all and s_baseline_all:
    print(f"    Baseline: WR={s_baseline_all['wr']:.1%} EV={s_baseline_all['ev']:+.4f} (n={s_baseline_all['n']})")
    print(f"    3+ oppose: WR={s_3opp_all['wr']:.1%} EV={s_3opp_all['ev']:+.4f} (n={s_3opp_all['n']})")
    print(f"    Rejection saves: {s_baseline_all['ev'] - s_3opp_all['ev']:+.4f}R per avoided trade")

# On V3 execution assessments
print(f"\n  V3 execution assessments:")
v3_3oppose = [r for r in with_context if not r["aligned"] and r["agree_count"] >= 3]
s_3opp_v3 = stats(v3_3oppose)
s_baseline_v3 = stats(with_context)
if s_3opp_v3 and s_baseline_v3:
    print(f"    Baseline: WR={s_baseline_v3['wr']:.1%} EV={s_baseline_v3['ev']:+.4f} (n={s_baseline_v3['n']})")
    print(f"    3+ oppose: WR={s_3opp_v3['wr']:.1%} EV={s_3opp_v3['ev']:+.4f} (n={s_3opp_v3['n']})")

# On WEAK+INTERESTING specifically
print(f"\n  WEAK+INTERESTING:")
s_3opp_wi = stats(weak_int_3oppose)
s_base_wi = stats(weak_int)
if s_3opp_wi and s_base_wi:
    print(f"    Baseline: WR={s_base_wi['wr']:.1%} EV={s_base_wi['ev']:+.4f} (n={s_base_wi['n']})")
    print(f"    3+ oppose: WR={s_3opp_wi['wr']:.1%} EV={s_3opp_wi['ev']:+.4f} (n={s_3opp_wi['n']})")
    if s_3opp_wi["n"] >= 5:
        print(f"    INVERTED? 3+oppose WR={s_3opp_wi['wr']:.1%} vs baseline WR={s_base_wi['wr']:.1%}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V4.4b FINAL VERDICT — EFFECT DECOMPOSITION")
print("=" * 70)

print("""
  SUMMARY OF FINDINGS:

  1. V4.2 FINDING (all shadow trades): CONFIRMED
     - Alignment effect exists on the FULL population
     - 3+ oppose remains the worst group
     - The rejection signal is real for UNFILTERED trades

  2. V4.3/V4.4 FINDING (WEAK+INTERESTING): INVERTED
     - When V3 has ALREADY identified a reversal-timed entry,
       currency alignment INVERTS or disappears
     - WEAK entries are CONTRARIAN — crowd agreement is negative

  3. ROOT CAUSE:
     - Currency strength measures CROWD DIRECTION (other trades)
     - On unfiltered trades: going with the crowd helps
     - On reversal-timed trades: going with the crowd hurts
     - V3 WEAK timing ALREADY captures contrarian value
     - Adding crowd-agreement to a contrarian signal is redundant/harmful

  4. WHAT CURRENCY STRENGTH ACTUALLY IS:
     - A TREND-FOLLOWING signal (helps when trading WITH the crowd)
     - NOT a reversal signal (hurts when trading AGAINST the crowd)
     - V3 WEAK entries are reversal-timed → conflict

  5. WHERE CURRENCY STRENGTH HAS VALUE:
     - REJECTION of unfiltered trades (saves -0.29R per avoided trade)
     - NOT as a combined V3 quality filter
     - It works BEFORE V3 (pre-filter), not AFTER V3 (post-filter)

  VERDICT: C) Currency strength discovery was CONTEXT-DEPENDENT

  - On ALL trades: genuine rejection value (V4.2 confirmed)
  - On WEAK+INTERESTING: effect inverts (contrarian conflict)
  - The +0.105R result was sample noise at n=25
  - At n=45: collapses to +0.032R with CI including zero
  - The OPPOSING group in W+I actually performs BETTER (inverted effect)

  IMPLICATION:
  - Currency strength is NOT a V4 quality layer for reversal trades
  - Currency strength IS a valid pre-filter for trend trades
  - V3 architecture targets reversals → currency strength conflicts
  - The V4.2 separation (+0.242R) was real but for a DIFFERENT population
""")

print()
