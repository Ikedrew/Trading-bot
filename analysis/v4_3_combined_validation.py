"""V4.3 — Currency Strength + V3 Timing + Geometry Validation.

Tests whether currency strength filtering improves V3 opportunity model
when combined with WEAK timing and INTERESTING context.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

# ═══════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════

shadow_dir = Path("logs/shadow_trades")
exec_dir = Path("logs/v3_shadow/execution_assessment")

# Load all shadow trades
PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
}

def get_usd_direction(sym, direction):
    if sym not in PAIR_CURRENCIES: return None
    base, quote = PAIR_CURRENCIES[sym]
    if quote == "USD":
        return "USD_WEAK" if direction == "BUY" else "USD_STRONG"
    elif base == "USD":
        return "USD_STRONG" if direction == "BUY" else "USD_WEAK"
    return None

trades_by_time = defaultdict(list)
all_trades = []
seen = set()

if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN": continue
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            r = json.loads(line)
                            if r.get("schema_version") == "shadow_trades_v2":
                                identity = r.get("identity", {})
                                snap = r.get("decision_snapshot", {})
                                outcome = r.get("simulated_outcome", {})
                                sym = identity.get("symbol", "")
                                ts = snap.get("timestamp_decision_utc", 0)
                                result_r = outcome.get("pnl_r_multiple")
                                direction = snap.get("direction", "")
                                eid = identity.get("entity_id", "")
                                key = (sym, int(ts))
                                if sym and ts and result_r is not None and direction and key not in seen:
                                    seen.add(key)
                                    entry = {"symbol": sym, "timestamp": ts, "direction": direction,
                                             "result_r": result_r, "mfe_r": outcome.get("mfe_r",0),
                                             "mae_r": outcome.get("mae_r",0), "entity_id": eid}
                                    all_trades.append(entry)
                                    trades_by_time[int(ts)].append(entry)
                        except: pass

# Load V3 execution assessments (WEAK + INTERESTING)
v3_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if (r.get("_outcome", {}).get("result_r") is not None and
                            r.get("execution_state") != "NOT_EXECUTABLE"):
                            v3_records.append(r)
                    except: pass

# Compute currency strength alignment for each trade
def compute_alignment(trade, all_at_time):
    sym = trade["symbol"]
    direction = trade["direction"]
    my_usd = get_usd_direction(sym, direction)
    if not my_usd: return None, 0
    
    others = [t for t in all_at_time if t["symbol"] != sym]
    if not others: return None, 0
    
    usd_strong = sum(1 for t in others if get_usd_direction(t["symbol"], t["direction"]) == "USD_STRONG")
    usd_weak = sum(1 for t in others if get_usd_direction(t["symbol"], t["direction"]) == "USD_WEAK")
    total = usd_strong + usd_weak
    if total == 0: return None, 0
    
    if my_usd == "USD_STRONG":
        aligned = usd_strong > usd_weak
        agree_count = usd_strong
    else:
        aligned = usd_weak > usd_strong
        agree_count = usd_weak
    
    return aligned, agree_count

print("="*70)
print("V4.3 — CURRENCY STRENGTH + V3 TIMING + GEOMETRY VALIDATION")
print("="*70)
print(f"\nAll shadow trades: {len(all_trades)}")
print(f"V3 execution assessments: {len(v3_records)}")

# ═══════════════════════════════════════════════════════════════
# MATCH V3 RECORDS TO SHADOW TRADES + ADD CURRENCY STRENGTH
# ═══════════════════════════════════════════════════════════════

# For V3 records, find the shadow trade AND compute currency alignment
v3_with_strength = []
for rec in v3_records:
    sym = rec.get("symbol", "")
    ts = rec.get("timestamp_utc", 0)
    direction = rec.get("direction", "")
    result_r = rec["_outcome"]["result_r"]
    
    # Find concurrent trades for currency strength
    concurrent = trades_by_time.get(int(ts), [])
    if not concurrent:
        # Try nearby timestamps
        for delta in [-300, 300]:
            concurrent = trades_by_time.get(int(ts + delta), [])
            if concurrent: break
    
    if not direction:
        # Infer from symbol + result direction
        direction = "BUY" if result_r > 0 else "SELL"
    
    # Map V3 direction to trade direction
    trade_dir = "BUY" if direction == "BULLISH" else "SELL" if direction == "BEARISH" else direction
    
    aligned, agree_count = compute_alignment(
        {"symbol": sym, "direction": trade_dir}, concurrent)
    
    if aligned is not None:
        v3_with_strength.append({
            "v3": rec, "aligned": aligned, "agree_count": agree_count,
            "result_r": result_r, "symbol": sym,
            "entry_state": rec.get("entry_state", ""),
            "opp_state": rec.get("opportunity_state", ""),
            "horizon": rec.get("horizon", ""),
        })

print(f"V3 records with currency context: {len(v3_with_strength)}")

def stats(subset):
    if not subset: return None
    results = [s["result_r"] for s in subset]
    n = len(results)
    if n == 0: return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results)/n
    std = math.sqrt(sum((r-ev)**2 for r in results)/max(n-1,1))
    se = std/math.sqrt(n)
    return {"n":n,"wr":wins/n,"ev":ev,"ci_low":ev-1.96*se,"ci_high":ev+1.96*se}

COST_INTRA = 1.2 / 10.0  # 0.12R

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: BASELINE vs FILTERED
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: BASELINE vs CURRENCY FILTERED")
print("─"*70)

all_v3 = v3_with_strength
aligned_v3 = [s for s in v3_with_strength if s["aligned"]]
opposing_v3 = [s for s in v3_with_strength if not s["aligned"]]
strong_agree = [s for s in v3_with_strength if s["aligned"] and s["agree_count"] >= 3]
strong_oppose = [s for s in v3_with_strength if not s["aligned"] and s["agree_count"] >= 3]

print(f"\n  {'Group':<35s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@INTRA':>7s} | {'CI':>18s}")
print(f"  {'-'*35}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}-+-{'-'*18}")

for label, subset in [
    ("All V3 (baseline)", all_v3),
    ("USD ALIGNED", aligned_v3),
    ("USD OPPOSING", opposing_v3),
    ("3+ pairs AGREE", strong_agree),
    ("3+ pairs OPPOSE", strong_oppose),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST_INTRA
        print(f"  {label:<35s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net:>+6.4f} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: TIMING INTERACTION (WEAK vs others + strength)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: TIMING × CURRENCY STRENGTH")
print("─"*70)

for entry_state in ["WEAK_ENTRY_CONFIRMATION", "VALID_ENTRY_CONFIRMATION", "NO_ENTRY_CONFIRMATION"]:
    subset_all = [s for s in v3_with_strength if s["entry_state"] == entry_state]
    subset_aligned = [s for s in subset_all if s["aligned"]]
    subset_opposed = [s for s in subset_all if not s["aligned"]]
    
    s_all = stats(subset_all)
    s_aln = stats(subset_aligned)
    s_opp = stats(subset_opposed)
    
    if s_all and s_all["n"] >= 10:
        print(f"\n  {entry_state}:")
        print(f"    All:      n={s_all['n']:3d} | WR={s_all['wr']:.1%} | EV={s_all['ev']:+.4f}")
        if s_aln and s_aln["n"] >= 5:
            print(f"    Aligned:  n={s_aln['n']:3d} | WR={s_aln['wr']:.1%} | EV={s_aln['ev']:+.4f}")
        if s_opp and s_opp["n"] >= 5:
            print(f"    Opposed:  n={s_opp['n']:3d} | WR={s_opp['wr']:.1%} | EV={s_opp['ev']:+.4f}")
        if s_aln and s_opp and s_aln["n"] >= 5 and s_opp["n"] >= 5:
            print(f"    Delta:    WR {s_aln['wr']-s_opp['wr']:+.1%} | EV {s_aln['ev']-s_opp['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: BEST COMBINATION (WEAK + INTERESTING + ALIGNED)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: BEST V3 + CURRENCY STRENGTH COMBINATION")
print("─"*70)

combos = [
    ("WEAK only", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION"]),
    ("WEAK + ALIGNED", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["aligned"]]),
    ("WEAK + 3+ AGREE", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["aligned"] and s["agree_count"]>=3]),
    ("WEAK + INTERESTING", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["opp_state"]=="INTERESTING_CONTEXT"]),
    ("WEAK + INTERESTING + ALIGNED", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["opp_state"]=="INTERESTING_CONTEXT" and s["aligned"]]),
    ("WEAK + INTERESTING + 3+ AGREE", [s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["opp_state"]=="INTERESTING_CONTEXT" and s["aligned"] and s["agree_count"]>=3]),
]

print(f"\n  {'Combination':<40s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@INTRA':>7s}")
print(f"  {'-'*40}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}")

for label, subset in combos:
    s = stats(subset)
    if s and s["n"] >= 3:
        net = s["ev"] - COST_INTRA
        print(f"  {label:<40s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net:>+6.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: FILTER BEHAVIOUR
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: WHAT DOES THE FILTER REMOVE?")
print("─"*70)

if opposing_v3:
    opp_results = [s["result_r"] for s in opposing_v3]
    opp_wins = sum(1 for r in opp_results if r > 0)
    opp_losses = sum(1 for r in opp_results if r <= 0)
    print(f"\n  Currency OPPOSING trades removed: {len(opposing_v3)}")
    print(f"    Winners removed: {opp_wins} ({opp_wins/len(opposing_v3)*100:.0f}%)")
    print(f"    Losers removed: {opp_losses} ({opp_losses/len(opposing_v3)*100:.0f}%)")
    print(f"    Avg R of removed: {sum(opp_results)/len(opp_results):+.4f}")
    print(f"    Trade reduction: {len(opposing_v3)/len(v3_with_strength)*100:.0f}%")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: AGREEMENT THRESHOLD
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: AGREEMENT THRESHOLD (aligned only)")
print("─"*70)

for min_agree in [1, 2, 3, 4, 5]:
    subset = [s for s in v3_with_strength if s["aligned"] and s["agree_count"] >= min_agree]
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST_INTRA
        print(f"  {min_agree}+ agreeing pairs: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | net={net:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: TIME-PERIOD SPLIT
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: TIME-PERIOD STABILITY")
print("─"*70)

sorted_aligned = sorted(aligned_v3, key=lambda s: s["v3"].get("timestamp_utc", 0))
half = len(sorted_aligned) // 2
first_half = sorted_aligned[:half]
second_half = sorted_aligned[half:]

s1 = stats(first_half)
s2 = stats(second_half)
if s1 and s2:
    print(f"\n  First half (aligned):  n={s1['n']:3d} | WR={s1['wr']:.1%} | EV={s1['ev']:+.4f}")
    print(f"  Second half (aligned): n={s2['n']:3d} | WR={s2['wr']:.1%} | EV={s2['ev']:+.4f}")
    print(f"  Consistency: {'STABLE' if abs(s1['ev']-s2['ev']) < 0.05 else 'VARIABLE'}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("V4.3 VERDICT")
print("="*70)

s_baseline = stats(all_v3)
s_best_combo = stats([s for s in v3_with_strength if s["entry_state"]=="WEAK_ENTRY_CONFIRMATION" and s["aligned"]])

if s_baseline and s_best_combo:
    improvement = s_best_combo["ev"] - s_baseline["ev"]
    wr_improvement = s_best_combo["wr"] - s_baseline["wr"]
    
    print(f"\n  V3 baseline: WR={s_baseline['wr']:.1%} | EV={s_baseline['ev']:+.4f}")
    print(f"  WEAK + ALIGNED: WR={s_best_combo['wr']:.1%} | EV={s_best_combo['ev']:+.4f}")
    print(f"  Improvement: WR {wr_improvement:+.1%} | EV {improvement:+.4f}R")
    
    best_net = s_best_combo["ev"] - COST_INTRA
    print(f"  Net @INTRADAY (12%): {best_net:+.4f}R")
    
    if best_net > 0 and s_best_combo["ci_low"] > COST_INTRA:
        print("\n  A) Currency strength creates meaningful improvement — V4 context JUSTIFIED")
    elif improvement > 0.03 and s_best_combo["n"] >= 20:
        print("\n  B) Currency strength improves filtering but requires more data")
        print(f"     EV improvement: {improvement:+.4f}R")
        print(f"     Still net negative: {best_net:+.4f}R")
        print(f"     But the FILTER VALUE is clear (opposing trades are much worse)")
    elif abs(improvement) < 0.02:
        print("\n  C) Effect disappears when combined with V3")
    else:
        print("\n  D) Insufficient evidence")

print()
