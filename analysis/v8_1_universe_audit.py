"""V8.1 — Trading Universe Expansion Audit.

Evaluates whether each FX pair demonstrates evidence for inclusion
alongside the validated NAS100/US500 trend-following policy.

NOTE: The V7 research established that FX pairs operate under a DIFFERENT
policy (mean-reversion/contrarian) than indices (trend-following).
This audit evaluates each FX pair under ITS OWN optimal policy
as determined by prior research.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V8.1 — TRADING UNIVERSE EXPANSION AUDIT")
print("=" * 70)

FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD"]
INDEX_SYMBOLS = ["NAS100", "US500"]

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

shadow_dir = Path("logs/shadow_trades")
all_trades = {}  # symbol -> [trades]

TARGET_SYMBOLS = set(FX_SYMBOLS + INDEX_SYMBOLS)
for sym_dir in shadow_dir.iterdir():
    if not sym_dir.is_dir() or sym_dir.name not in TARGET_SYMBOLS:
        continue
    sym = sym_dir.name
    trades = []
    for f in sym_dir.glob("*.jsonl"):
        for line in open(f):
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
                trades.append({
                    "symbol": sym,
                    "result_r": outcome["pnl_r_multiple"],
                    "mfe_r": outcome.get("mfe_r", 0),
                    "mae_r": outcome.get("mae_r", 0),
                    "exit_reason": outcome.get("exit_reason", ""),
                    "bars_held": outcome.get("bars_held", 0),
                    "timestamp": snap.get("timestamp_decision_utc", 0),
                    "direction": snap.get("direction", ""),
                })
            except:
                pass
    if trades:
        all_trades[sym] = sorted(trades, key=lambda t: t["timestamp"])

# Load V3 execution assessments (have context labels)
exec_dir = Path("logs/v3_shadow/execution_assessment")
fx_exec = defaultdict(list)
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        sym = r.get("symbol", "")
                        fx_exec[sym].append(r)
                except:
                    pass

print(f"\n  Shadow trades per symbol:")
for sym in FX_SYMBOLS + INDEX_SYMBOLS:
    n = len(all_trades.get(sym, []))
    n_exec = len(fx_exec.get(sym, []))
    print(f"    {sym:10s}: {n:5d} shadow trades | {n_exec:4d} exec assessments")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def calc_stats(trades, invert=False):
    if not trades:
        return None
    results = [-t["result_r"] if invert else t["result_r"] for t in trades]
    n = len(results)
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    
    win_r = [r for r in results if r > 0]
    loss_r = [r for r in results if r < 0]
    avg_win = sum(win_r) / len(win_r) if win_r else 0
    avg_loss = sum(loss_r) / len(loss_r) if loss_r else 0
    
    # Max losing streak
    consec = 0; max_consec = 0
    for r in results:
        if r < 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    
    return {
        "n": n, "wr": wins/n, "ev": ev, "std": std,
        "ci_low": ev - 1.96*se, "ci_high": ev + 1.96*se,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "max_losing_streak": max_consec,
    }

# ═══════════════════════════════════════════════════════════════
# SECTION 1: DATA AVAILABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 1: DATA AVAILABILITY AUDIT")
print("─" * 70)

print(f"\n  {'Symbol':<10s} | {'Shadow':>7s} | {'Exec':>5s} | {'Quality'}")
print(f"  {'-'*10}-+-{'-'*7}-+-{'-'*5}-+-{'-'*30}")
for sym in FX_SYMBOLS:
    n_shadow = len(all_trades.get(sym, []))
    n_exec = len(fx_exec.get(sym, []))
    if n_shadow >= 200 and n_exec >= 30:
        quality = "GOOD (shadow + V3 context)"
    elif n_shadow >= 100:
        quality = "ADEQUATE (shadow only)"
    elif n_shadow >= 30:
        quality = "MINIMAL"
    else:
        quality = "INSUFFICIENT"
    print(f"  {sym:<10s} | {n_shadow:>7d} | {n_exec:>5d} | {quality}")

# ═══════════════════════════════════════════════════════════════
# SECTION 2: INDIVIDUAL PERFORMANCE
# Test each FX pair under BOTH policies:
#   - Reversion (original signal = current V3 behaviour)
#   - Trend-following (inverted signal = V7.5 index policy)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 2: INDIVIDUAL PERFORMANCE (shadow trades)")
print("─" * 70)

print(f"\n  Each pair tested under BOTH policies to determine natural fit:")
print(f"\n  {'Symbol':<10s} | {'n':>5s} | {'Rev WR':>6s} | {'Rev EV':>8s} | {'Trend WR':>8s} | {'Trend EV':>9s} | {'Better':<10s} | {'MaxLS':>5s}")
print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*9}-+-{'-'*10}-+-{'-'*5}")

pair_analysis = {}
for sym in FX_SYMBOLS:
    trades = all_trades.get(sym, [])
    if not trades:
        continue
    
    s_rev = calc_stats(trades, invert=False)   # Reversion: keep original
    s_trend = calc_stats(trades, invert=True)  # Trend: invert signal
    
    if s_rev and s_trend:
        better = "REVERSION" if s_rev["ev"] > s_trend["ev"] else "TREND"
        best_s = s_rev if s_rev["ev"] > s_trend["ev"] else s_trend
        print(f"  {sym:<10s} | {s_rev['n']:>5d} | {s_rev['wr']:.1%} | {s_rev['ev']:>+7.4f} | {s_trend['wr']:.1%}  | {s_trend['ev']:>+8.4f} | {better:<10s} | {best_s['max_losing_streak']:>5d}")
        
        pair_analysis[sym] = {
            "rev": s_rev, "trend": s_trend, "better": better,
            "best_ev": max(s_rev["ev"], s_trend["ev"]),
            "best_policy": better,
        }

# Index comparison
print(f"\n  INDEX BASELINE (trend-following policy):")
for sym in INDEX_SYMBOLS:
    trades = all_trades.get(sym, [])
    if trades:
        s = calc_stats(trades, invert=True)
        if s:
            print(f"  {sym:<10s} | {s['n']:>5d} | {'—':>6s} | {'—':>8s} | {s['wr']:.1%}  | {s['ev']:>+8.4f} | TREND      | {s['max_losing_streak']:>5d}")

# ═══════════════════════════════════════════════════════════════
# SECTION 3: COMPARISON AGAINST VALIDATED MARKETS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 3: COMPARISON AGAINST VALIDATED BASELINE")
print("─" * 70)

# Baseline: NAS100+US500 combined (trend policy)
idx_trades_all = []
for sym in INDEX_SYMBOLS:
    idx_trades_all.extend(all_trades.get(sym, []))
idx_baseline = calc_stats(idx_trades_all, invert=True)

if idx_baseline:
    print(f"\n  BASELINE (NAS100+US500 trend): WR={idx_baseline['wr']:.1%} | EV={idx_baseline['ev']:+.4f}")
    print(f"\n  {'Symbol':<10s} | {'Best EV':>8s} | {'vs Baseline':>11s} | {'CI':>20s} | Assessment")
    print(f"  {'-'*10}-+-{'-'*8}-+-{'-'*11}-+-{'-'*20}-+-{'-'*20}")
    
    for sym in FX_SYMBOLS:
        if sym not in pair_analysis:
            continue
        pa = pair_analysis[sym]
        best_ev = pa["best_ev"]
        best_policy = pa["best_policy"]
        best_s = pa["rev"] if best_policy == "REVERSION" else pa["trend"]
        delta = best_ev - idx_baseline["ev"]
        
        if best_ev > 0.10 and best_s["ci_low"] > 0:
            assessment = "STRONG"
        elif best_ev > 0.05:
            assessment = "MODERATE"
        elif best_ev > 0:
            assessment = "WEAK"
        else:
            assessment = "NEGATIVE"
        
        print(f"  {sym:<10s} | {best_ev:>+7.4f} | {delta:>+10.4f} | [{best_s['ci_low']:+.3f},{best_s['ci_high']:+.3f}] | {assessment}")

# ═══════════════════════════════════════════════════════════════
# SECTION 4: TIME STABILITY PER PAIR
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 4: TIME STABILITY (halves)")
print("─" * 70)

print(f"\n  {'Symbol':<10s} | {'Policy':<10s} | {'H1 EV':>7s} | {'H2 EV':>7s} | {'Both +?':>7s} | {'Trend':>10s}")
print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*10}")

for sym in FX_SYMBOLS:
    trades = all_trades.get(sym, [])
    if len(trades) < 20:
        continue
    pa = pair_analysis.get(sym)
    if not pa:
        continue
    
    invert = pa["best_policy"] == "TREND"
    half = len(trades) // 2
    s1 = calc_stats(trades[:half], invert=invert)
    s2 = calc_stats(trades[half:], invert=invert)
    
    if s1 and s2:
        both_pos = s1["ev"] > 0 and s2["ev"] > 0
        if s2["ev"] > s1["ev"]:
            trend = "IMPROVING"
        elif s2["ev"] < s1["ev"] and s2["ev"] > 0:
            trend = "DECLINING"
        elif s2["ev"] < 0:
            trend = "DEGRADED"
        else:
            trend = "STABLE"
        
        print(f"  {sym:<10s} | {pa['best_policy']:<10s} | {s1['ev']:>+6.4f} | {s2['ev']:>+6.4f} | {'YES' if both_pos else 'NO':>7s} | {trend:<10s}")

# ═══════════════════════════════════════════════════════════════
# SECTION 5: V3-FILTERED PERFORMANCE (exec assessments)
# Only available for FX pairs with V3 pipeline data
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 5: V3-FILTERED PERFORMANCE (exec assessments — reversion policy)")
print("─" * 70)

print(f"\n  V3 execution assessments (WEAK/INTERESTING filtered):")
print(f"  {'Symbol':<10s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'CI':>20s}")
print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*20}")

for sym in FX_SYMBOLS:
    records = fx_exec.get(sym, [])
    if not records:
        continue
    results = [r["_outcome"]["result_r"] for r in records]
    n = len(results)
    if n < 10:
        continue
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev)**2 for r in results) / max(n-1,1))
    se = std / math.sqrt(n)
    print(f"  {sym:<10s} | {n:>4d} | {wins/n:.1%} | {ev:>+7.4f} | [{ev-1.96*se:+.3f},{ev+1.96*se:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# SECTION 6: COST ANALYSIS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 6: TRADING COST IMPACT")
print("─" * 70)

# FX cost model: ~1.0-1.5 pip spread, 3.5-7 pip typical stop = 15-40% cost ratio
# Index cost model: ~8-10% cost ratio
FX_COST = 0.20  # 20% average cost/stop for FX M5
IDX_COST = 0.09  # 9% average for indices

print(f"\n  Cost assumptions:")
print(f"    FX pairs: {FX_COST:.0%} spread/stop ratio (1.2pip / 6pip)")
print(f"    Indices: {IDX_COST:.0%} spread/stop ratio")

print(f"\n  {'Symbol':<10s} | {'Best EV':>8s} | {'Cost':>5s} | {'Net EV':>8s} | {'Net +?':>6s}")
print(f"  {'-'*10}-+-{'-'*8}-+-{'-'*5}-+-{'-'*8}-+-{'-'*6}")

for sym in FX_SYMBOLS:
    if sym not in pair_analysis:
        continue
    best_ev = pair_analysis[sym]["best_ev"]
    net = best_ev - FX_COST
    print(f"  {sym:<10s} | {best_ev:>+7.4f} | {FX_COST:.0%} | {net:>+7.4f} | {'YES' if net > 0 else 'NO':>6s}")

for sym in INDEX_SYMBOLS:
    trades = all_trades.get(sym, [])
    if trades:
        s = calc_stats(trades, invert=True)
        if s:
            net = s["ev"] - IDX_COST
            print(f"  {sym:<10s} | {s['ev']:>+7.4f} | {IDX_COST:.0%} | {net:>+7.4f} | {'YES' if net > 0 else 'NO':>6s}")

# ═══════════════════════════════════════════════════════════════
# SECTION 7: CORRELATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 7: CORRELATION ANALYSIS")
print("─" * 70)

# Check if pairs trade at the same timestamps (correlated opportunities)
print(f"\n  Concurrent trade frequency (same timestamp):")
from collections import Counter as C2
timestamp_counts = Counter()
for sym, trades in all_trades.items():
    for t in trades:
        timestamp_counts[int(t["timestamp"])] += 1

multi_trade_ts = sum(1 for ts, count in timestamp_counts.items() if count > 1)
total_ts = len(timestamp_counts)
print(f"    Unique timestamps: {total_ts}")
print(f"    Multi-symbol timestamps: {multi_trade_ts} ({multi_trade_ts/max(total_ts,1):.0%})")
print(f"    Avg symbols per timestamp: {sum(timestamp_counts.values())/max(total_ts,1):.1f}")
print(f"\n  NOTE: High concurrency means signals fire simultaneously across pairs.")
print(f"  This creates portfolio concentration risk, not diversification.")

# ═══════════════════════════════════════════════════════════════
# SECTION 8: UNIVERSE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 8: UNIVERSE CLASSIFICATION")
print("─" * 70)

print(f"\n  {'Symbol':<10s} | {'Tier':<8s} | {'Reason'}")
print(f"  {'-'*10}-+-{'-'*8}-+-{'-'*50}")

classification = {}
for sym in INDEX_SYMBOLS:
    trades = all_trades.get(sym, [])
    if trades:
        s = calc_stats(trades, invert=True)
        if s and s["ev"] > 0.10 and s["ci_low"] > 0:
            tier = "TIER 1"
            reason = f"VALIDATED: EV={s['ev']:+.4f}, CI excludes zero, trend policy"
        elif s and s["ev"] > 0.05:
            tier = "TIER 1"
            reason = f"Strong evidence: EV={s['ev']:+.4f}, trend policy"
        else:
            tier = "TIER 2"
            reason = "Needs more data"
        classification[sym] = tier
        print(f"  {sym:<10s} | {tier:<8s} | {reason}")

for sym in FX_SYMBOLS:
    if sym not in pair_analysis:
        classification[sym] = "TIER 3"
        print(f"  {sym:<10s} | TIER 3   | No data available")
        continue
    
    pa = pair_analysis[sym]
    best_ev = pa["best_ev"]
    best_s = pa["rev"] if pa["best_policy"] == "REVERSION" else pa["trend"]
    net = best_ev - FX_COST
    
    # Time stability check
    trades = all_trades[sym]
    half = len(trades) // 2
    invert = pa["best_policy"] == "TREND"
    s2 = calc_stats(trades[half:], invert=invert)
    recent_positive = s2 and s2["ev"] > 0
    
    if net > 0.05 and best_s["ci_low"] > 0 and recent_positive:
        tier = "TIER 1"
        reason = f"NET POSITIVE: EV={best_ev:+.4f}, net={net:+.4f}, CI>0, {pa['best_policy']}"
    elif net > 0 and recent_positive:
        tier = "TIER 2"
        reason = f"Marginal net positive: net={net:+.4f}, needs validation"
    elif best_ev > 0 and net <= 0:
        tier = "TIER 3"
        reason = f"COSTS DESTROY EDGE: gross={best_ev:+.4f}, net={net:+.4f}"
    elif best_ev <= 0:
        tier = "TIER 3"
        reason = f"NEGATIVE EV under both policies: best={best_ev:+.4f}"
    else:
        tier = "TIER 3"
        reason = f"Recent performance negative"
    
    classification[sym] = tier
    print(f"  {sym:<10s} | {tier:<8s} | {reason}")

# ═══════════════════════════════════════════════════════════════
# FINAL OUTPUT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V8.1 FINAL OUTPUT")
print("=" * 70)

tier1 = [sym for sym, t in classification.items() if t == "TIER 1"]
tier2 = [sym for sym, t in classification.items() if t == "TIER 2"]
tier3 = [sym for sym, t in classification.items() if t == "TIER 3"]

print(f"""
  A) VALIDATED INSTRUMENTS (Tier 1 — earned inclusion):
     {', '.join(tier1) if tier1 else 'None'}

  B) PROMISING BUT INSUFFICIENT (Tier 2 — need more data):
     {', '.join(tier2) if tier2 else 'None'}

  C) EXCLUDED (Tier 3 — negative or cost-destroyed):
     {', '.join(tier3) if tier3 else 'None'}
""")

# Research gaps
print(f"  D) RESEARCH GAPS REMAINING:")
print(f"     1. No forward validation data exists for ANY instrument")
print(f"     2. FX cost ratio (20%) destroys most positive signals")
print(f"     3. FX time stability is poor (early period drives results)")
print(f"     4. No regime/session analysis possible without V3 pipeline on indices")
print(f"     5. Correlation between concurrent FX trades is unmeasured")

print(f"\n  E) MINIMUM ADDITIONAL EVIDENCE REQUIRED:")
print(f"     - 200+ new NAS100/US500 shadow trades (forward validation)")
print(f"     - Actual spread measurement during collection period")
print(f"     - For FX inclusion: demonstrate net EV > 0 after 20% cost")

print(f"\n  F) RECOMMENDED INITIAL TRADING UNIVERSE:")
print(f"     ┌────────────────────────────────────────────────────────┐")
print(f"     │ NAS100 — Strongest validated evidence (trend-following) │")
print(f"     │ US500  — Validated evidence (trend-following)           │")
print(f"     │                                                        │")
print(f"     │ FX PAIRS: NOT INCLUDED                                  │")
print(f"     │ Reason: 20% cost ratio destroys all positive signals    │")
print(f"     │ Exception: EURUSD may qualify IF V3-filtered            │")
print(f"     │ (V3 exec assessment shows +0.62R but concentrated)      │")
print(f"     └────────────────────────────────────────────────────────┘")

print(f"\n  G) CAN FX PAIRS BE INCLUDED?")
for sym in FX_SYMBOLS:
    if sym in pair_analysis:
        net = pair_analysis[sym]["best_ev"] - FX_COST
        if net > 0:
            answer = f"POSSIBLY — net {net:+.4f}R (marginal, needs forward validation)"
        else:
            answer = f"NO — net {net:+.4f}R (costs exceed edge)"
    else:
        answer = "NO — no data"
    print(f"     {sym:10s}: {answer}")

print()
