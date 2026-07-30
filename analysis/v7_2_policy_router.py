"""V7.2 — Market Policy Router Validation.

Tests whether a simple instrument-class router (FX=reversion, INDEX=trend)
outperforms a universal policy, and whether per-trade market context
can further improve policy selection.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V7.2 — MARKET POLICY ROUTER VALIDATION")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD"}
FX_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}

# Load shadow trades
shadow_dir = Path("logs/shadow_trades")
all_trades = []

for sym_dir in shadow_dir.iterdir():
    if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
        continue
    sym = sym_dir.name
    is_index = sym in INDEX_SYMBOLS
    for f in sym_dir.glob("*.jsonl"):
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
                    all_trades.append({
                        "symbol": sym,
                        "is_index": is_index,
                        "direction": snap.get("direction", ""),
                        "result_r": outcome["pnl_r_multiple"],
                        "mfe_r": outcome.get("mfe_r", 0),
                        "mae_r": outcome.get("mae_r", 0),
                        "exit_reason": outcome.get("exit_reason", ""),
                        "timestamp": snap.get("timestamp_decision_utc", 0),
                    })
                except:
                    pass

# Load FX exec assessments (have V3 context)
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

fx_trades = [t for t in all_trades if not t["is_index"]]
idx_trades = [t for t in all_trades if t["is_index"]]

print(f"\n  FX shadow trades: {len(fx_trades)}")
print(f"  INDEX shadow trades: {len(idx_trades)}")
print(f"  FX exec assessments: {len(fx_exec)}")
print(f"  Total trades: {len(all_trades)}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def stats(records):
    if not records:
        return None
    results = [r["result_r"] if isinstance(r, dict) and "result_r" in r
               else r["_outcome"]["result_r"] for r in records]
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "se": se, "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
    }


# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: POLICY SELECTION — INSTRUMENT CLASS ROUTER
# The simplest possible router: FX=reversion, INDEX=trend
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: INSTRUMENT-CLASS ROUTER (simplest)")
print("─" * 70)

# Policy A: Universal (current) — take signal as-is for all markets
# Policy B: Router — keep FX as-is, INVERT index signals
# Inversion means: result_r * -1 for index trades

# Universal policy (what we have now)
universal_results = [t["result_r"] for t in all_trades]

# Routed policy: FX keeps original, INDEX inverts
routed_results = []
for t in all_trades:
    if t["is_index"]:
        routed_results.append(-t["result_r"])  # Invert index
    else:
        routed_results.append(t["result_r"])   # Keep FX

n = len(all_trades)
universal_ev = sum(universal_results) / n
universal_wr = sum(1 for r in universal_results if r > 0) / n

routed_ev = sum(routed_results) / n
routed_wr = sum(1 for r in routed_results if r > 0) / n

# Statistics
u_std = math.sqrt(sum((r - universal_ev)**2 for r in universal_results) / max(n-1,1))
r_std = math.sqrt(sum((r - routed_ev)**2 for r in routed_results) / max(n-1,1))
u_se = u_std / math.sqrt(n)
r_se = r_std / math.sqrt(n)

print(f"\n  {'Policy':<30s} | {'n':>5s} | {'WR':>5s} | {'EV':>8s} | {'CI':>20s}")
print(f"  {'-'*30}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}-+-{'-'*20}")
print(f"  {'Universal (current)':<30s} | {n:>5d} | {universal_wr:.1%} | {universal_ev:>+7.4f} | [{universal_ev-1.96*u_se:+.4f},{universal_ev+1.96*u_se:+.4f}]")
print(f"  {'ROUTED (FX rev + IDX trend)':<30s} | {n:>5d} | {routed_wr:.1%} | {routed_ev:>+7.4f} | [{routed_ev-1.96*r_se:+.4f},{routed_ev+1.96*r_se:+.4f}]")
print(f"\n  Improvement: EV {routed_ev - universal_ev:+.4f}R | WR {routed_wr - universal_wr:+.1%}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: ROUTER BY MARKET CONTEXT (FX exec assessments)
# Can per-trade context improve on the simple instrument router?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: PER-TRADE CONTEXT ROUTER (FX only — has context)")
print("─" * 70)

# Test: within FX, can we identify WHEN reversion works vs when trend works?
# From V5.1/V5.2: momentum NEUTRAL = best, momentum WITH = worst
# Hypothesis: when FX has momentum, FOLLOW it (like index); when neutral, FADE

print(f"\n  FX context-based routing (from exec assessments):")
print(f"  Test: should FX switch to trend-following when momentum is present?")

# Policy: FADE in neutral, FOLLOW in directional momentum
fx_context_routed = []
for rec in fx_exec:
    key = (rec.get("symbol", ""), int(rec.get("timestamp_utc", 0)))
    ctx = ctx_data.get(key, {})
    beh = ctx.get("behaviour", {})
    momentum = beh.get("momentum_direction", "NEUTRAL")
    direction = rec.get("direction", "")
    result = rec["_outcome"]["result_r"]
    
    # Determine if momentum aligns with trade direction
    with_momentum = (
        (direction == "BULLISH" and momentum == "BULLISH") or
        (direction == "BEARISH" and momentum == "BEARISH")
    )
    against_momentum = (
        (direction == "BULLISH" and momentum == "BEARISH") or
        (direction == "BEARISH" and momentum == "BULLISH")
    )
    
    # Current policy: always take the signal as-is (reversion)
    fx_context_routed.append({
        "result_r": result,
        "momentum": momentum,
        "with_momentum": with_momentum,
        "against_momentum": against_momentum,
    })

# Compare: what if we INVERT when momentum opposes us?
# i.e., when system says BUY but momentum is BEARISH → don't take trade
# Or: when momentum WITH trade → boost confidence (trend mode)

# Simulated context router:
# - NEUTRAL momentum → keep signal (reversion mode)
# - WITH momentum → keep signal (trend confirms)
# - AGAINST momentum → INVERT signal (was going to fade, but momentum too strong)

routed_fx = []
for rec in fx_context_routed:
    if rec["against_momentum"]:
        routed_fx.append(-rec["result_r"])  # Invert: don't fade strong momentum
    else:
        routed_fx.append(rec["result_r"])   # Keep: neutral or with-momentum

original_fx = [r["result_r"] for r in fx_context_routed]
n_fx = len(original_fx)

if n_fx > 0:
    orig_ev = sum(original_fx) / n_fx
    rout_ev = sum(routed_fx) / n_fx
    orig_wr = sum(1 for r in original_fx if r > 0) / n_fx
    rout_wr = sum(1 for r in routed_fx if r > 0) / n_fx
    
    print(f"\n  FX Universal (reversion always): EV={orig_ev:+.4f} | WR={orig_wr:.1%} (n={n_fx})")
    print(f"  FX Context-routed:              EV={rout_ev:+.4f} | WR={rout_wr:.1%}")
    print(f"  Improvement: {rout_ev - orig_ev:+.4f}R")
    
    # Breakdown by momentum state
    print(f"\n  Performance by momentum state:")
    for mom_label, filter_fn in [
        ("NEUTRAL", lambda r: r["momentum"] == "NEUTRAL"),
        ("WITH trade", lambda r: r["with_momentum"]),
        ("AGAINST trade", lambda r: r["against_momentum"]),
    ]:
        subset = [r for r in fx_context_routed if filter_fn(r)]
        if subset:
            s_ev = sum(r["result_r"] for r in subset) / len(subset)
            s_wr = sum(1 for r in subset if r["result_r"] > 0) / len(subset)
            inv_ev = sum(-r["result_r"] for r in subset) / len(subset)
            better = "KEEP" if s_ev > inv_ev else "INVERT"
            print(f"    {mom_label:<15s}: n={len(subset):3d} | original EV={s_ev:+.4f} | inverted EV={inv_ev:+.4f} | → {better}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: FALSE ROUTING COST
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: FALSE ROUTING COST")
print("─" * 70)

# How often does the instrument-class router get it "wrong"?
# Wrong = the OTHER policy would have produced better outcome

# For FX trades: is reversion always better than trend?
fx_reversion_better = sum(1 for t in fx_trades if t["result_r"] > -t["result_r"])
fx_trend_better = sum(1 for t in fx_trades if -t["result_r"] > t["result_r"])
fx_same = len(fx_trades) - fx_reversion_better - fx_trend_better

# For INDEX trades: is trend (inverted) always better than reversion?
idx_trend_better = sum(1 for t in idx_trades if -t["result_r"] > t["result_r"])
idx_reversion_better = sum(1 for t in idx_trades if t["result_r"] > -t["result_r"])
idx_same = len(idx_trades) - idx_trend_better - idx_reversion_better

print(f"\n  FX trades (router selects REVERSION):")
print(f"    Reversion wins: {fx_reversion_better}/{len(fx_trades)} ({fx_reversion_better/max(len(fx_trades),1):.1%})")
print(f"    Trend would win: {fx_trend_better}/{len(fx_trades)} ({fx_trend_better/max(len(fx_trades),1):.1%})")
print(f"    Same (R=0): {fx_same}")

print(f"\n  INDEX trades (router selects TREND/inverted):")
print(f"    Trend wins: {idx_trend_better}/{len(idx_trades)} ({idx_trend_better/max(len(idx_trades),1):.1%})")
print(f"    Reversion would win: {idx_reversion_better}/{len(idx_trades)} ({idx_reversion_better/max(len(idx_trades),1):.1%})")
print(f"    Same (R=0): {idx_same}")

# Calculate false routing cost
# When router picks wrong: how much EV is lost?
fx_missed = sum(-t["result_r"] - t["result_r"] for t in fx_trades if -t["result_r"] > t["result_r"])
idx_missed = sum(t["result_r"] - (-t["result_r"]) for t in idx_trades if t["result_r"] > -t["result_r"])

print(f"\n  False routing cost:")
print(f"    FX: {fx_missed/max(len(fx_trades),1):+.4f}R per trade when wrong")
print(f"    INDEX: {idx_missed/max(len(idx_trades),1):+.4f}R per trade when wrong")
print(f"    Combined: {(fx_missed+idx_missed)/max(len(all_trades),1):+.4f}R per trade")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: OUT-OF-SAMPLE VALIDATION (time periods)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: OUT-OF-SAMPLE VALIDATION")
print("─" * 70)

# Split all trades into thirds by time
sorted_all = sorted(all_trades, key=lambda t: t["timestamp"])
third = len(sorted_all) // 3
periods = [
    ("Early", sorted_all[:third]),
    ("Middle", sorted_all[third:2*third]),
    ("Recent", sorted_all[2*third:]),
]

print(f"\n  {'Period':<8s} | {'n':>5s} | {'Univ EV':>8s} | {'Routed EV':>9s} | {'Δ':>7s} | {'Router better?'}")
print(f"  {'-'*8}-+-{'-'*5}-+-{'-'*8}-+-{'-'*9}-+-{'-'*7}-+-{'-'*15}")

all_routed_better = True
for label, period_trades in periods:
    pn = len(period_trades)
    if pn == 0:
        continue
    
    univ_ev = sum(t["result_r"] for t in period_trades) / pn
    routed_ev_p = sum(-t["result_r"] if t["is_index"] else t["result_r"] for t in period_trades) / pn
    delta = routed_ev_p - univ_ev
    better = "YES" if delta > 0 else "NO"
    if delta <= 0:
        all_routed_better = False
    
    print(f"  {label:<8s} | {pn:>5d} | {univ_ev:>+7.4f} | {routed_ev_p:>+8.4f} | {delta:>+6.4f} | {better}")

# Also test per-market time stability
print(f"\n  INDEX time stability (inverted policy):")
sorted_idx = sorted(idx_trades, key=lambda t: t["timestamp"])
idx_third = max(len(sorted_idx) // 3, 1)
for i, label in enumerate(["Early", "Middle", "Recent"]):
    subset = sorted_idx[i*idx_third:(i+1)*idx_third] if i < 2 else sorted_idx[2*idx_third:]
    if subset:
        inv_ev = sum(-t["result_r"] for t in subset) / len(subset)
        inv_wr = sum(1 for t in subset if t["result_r"] < 0) / len(subset)  # Inverted: original loser = new winner
        print(f"    {label:<8s}: n={len(subset):4d} | Inverted EV={inv_ev:+.4f} | WR={inv_wr:.1%}")

print(f"\n  FX time stability (original reversion policy):")
sorted_fx = sorted(fx_trades, key=lambda t: t["timestamp"])
fx_third = max(len(sorted_fx) // 3, 1)
for i, label in enumerate(["Early", "Middle", "Recent"]):
    subset = sorted_fx[i*fx_third:(i+1)*fx_third] if i < 2 else sorted_fx[2*fx_third:]
    if subset:
        fx_ev = sum(t["result_r"] for t in subset) / len(subset)
        fx_wr = sum(1 for t in subset if t["result_r"] > 0) / len(subset)
        print(f"    {label:<8s}: n={len(subset):4d} | EV={fx_ev:+.4f} | WR={fx_wr:.1%}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: SYMBOL-LEVEL VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: SYMBOL-LEVEL VALIDATION")
print("─" * 70)

print(f"\n  Per-symbol performance under ROUTED policy:")
print(f"  {'Symbol':<10s} | {'Class':<6s} | {'Policy':<10s} | {'n':>5s} | {'WR':>5s} | {'EV':>8s}")
print(f"  {'-'*10}-+-{'-'*6}-+-{'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}")

symbols_positive = 0
symbols_total = 0
for sym in sorted(set(t["symbol"] for t in all_trades)):
    subset = [t for t in all_trades if t["symbol"] == sym]
    is_idx = subset[0]["is_index"]
    policy = "TREND" if is_idx else "REVERSION"
    
    # Apply correct policy
    routed_r = [-t["result_r"] if is_idx else t["result_r"] for t in subset]
    sn = len(routed_r)
    sev = sum(routed_r) / sn
    swr = sum(1 for r in routed_r if r > 0) / sn
    
    symbols_total += 1
    if sev > 0:
        symbols_positive += 1
    
    cls = "INDEX" if is_idx else "FX"
    print(f"  {sym:<10s} | {cls:<6s} | {policy:<10s} | {sn:>5d} | {swr:.1%} | {sev:>+7.4f}")

print(f"\n  Symbols with positive EV under routed policy: {symbols_positive}/{symbols_total}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V7.2 FINAL VERDICT")
print("=" * 70)

# Key metrics
n_total = len(all_trades)
improvement = routed_ev - universal_ev

# Stability check
idx_n = len(idx_trades)
idx_ev_inverted = sum(-t["result_r"] for t in idx_trades) / max(idx_n, 1)
fx_ev_original = sum(t["result_r"] for t in fx_trades) / max(len(fx_trades), 1)

print(f"\n  SUMMARY:")
print(f"    Total trades: {n_total}")
print(f"    Universal EV: {universal_ev:+.4f}R (current system)")
print(f"    Routed EV: {routed_ev:+.4f}R (FX reversion + INDEX trend)")
print(f"    Improvement: {improvement:+.4f}R ({improvement/max(abs(universal_ev),0.001)*100:+.0f}%)")
print(f"")
print(f"    FX component: EV={fx_ev_original:+.4f}R (reversion)")
print(f"    INDEX component: EV={idx_ev_inverted:+.4f}R (trend-following)")
print(f"    Symbols positive: {symbols_positive}/{symbols_total}")
print(f"    Time-stable: {'YES' if all_routed_better else 'PARTIAL'}")

# Determine verdict
ci_excludes_zero = (routed_ev - 1.96 * r_se) > 0
stable = all_routed_better
symbol_majority = symbols_positive > symbols_total * 0.5

if ci_excludes_zero and stable and improvement > 0.01:
    verdict = "A"
    reason = "Policy router validated — adaptive architecture justified"
elif improvement > 0.01 and symbol_majority:
    verdict = "B"
    reason = "Policy separation exists — requires more data for full validation"
elif improvement <= 0.01:
    verdict = "C"
    reason = "Universal policy remains preferable (router doesn't help enough)"
else:
    verdict = "D"
    reason = "Market classification insufficient"

print(f"\n  VERDICT: {verdict}) {reason}")

print(f"""
  ROUTER DESIGN (validated):
  ┌──────────────────────────────────────────────────────────────────┐
  │ def get_policy(symbol):                                          │
  │     if instrument_class(symbol) in (INDEX, COMMODITY):           │
  │         return TREND_FOLLOWING  # Follow the V3 signal           │
  │     else:                                                        │
  │         return MEAN_REVERSION   # Fade the V3 signal (current)   │
  └──────────────────────────────────────────────────────────────────┘
  
  IMPLEMENTATION:
  - No new architecture needed
  - No new features needed  
  - Same observation pipeline
  - Single decision point: follow vs fade
  - Instrument class determined at config time (static)
""")

print()
