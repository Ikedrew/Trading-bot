"""AR8 — Edge Stability and Regime Robustness Analysis."""
import json, math, random
from pathlib import Path
from collections import Counter

# Load WEAK+INTERESTING matched records with progressions
exec_dir = Path("logs/v3_shadow/execution_assessment")
shadow_dir = Path("logs/shadow_trades")

exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if (r.get("_outcome", {}).get("result_r") is not None and
                            r.get("execution_state") != "NOT_EXECUTABLE" and
                            r.get("entry_state") == "WEAK_ENTRY_CONFIRMATION" and
                            r.get("opportunity_state") == "INTERESTING_CONTEXT"):
                            exec_records.append(r)
                    except: pass

shadow_by_key = {}
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
                                prog = r.get("simulated_outcome", {}).get("trade_state_progression", [])
                                if prog:
                                    eid = r.get("identity", {}).get("entity_id", "")
                                    sym = r.get("identity", {}).get("symbol", "")
                                    ts = r.get("decision_snapshot", {}).get("timestamp_decision_utc", 0)
                                    if eid: shadow_by_key[eid] = r
                                    if sym and ts: shadow_by_key[f"{sym}_{int(ts)}"] = r
                        except: pass

matched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = rec.get("timestamp_utc", 0)
    key = f"{sym}_{int(ts)}"
    trade = shadow_by_key.get(key)
    if not trade:
        for delta in [-300, 300]:
            trade = shadow_by_key.get(f"{sym}_{int(ts+delta)}")
            if trade: break
    if trade:
        prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
        if prog:
            matched.append({"v3": rec, "progression": prog, "trade": trade,
                           "symbol": sym, "timestamp": ts,
                           "result_r": rec["_outcome"]["result_r"]})

# Sort by timestamp for time-based analysis
matched.sort(key=lambda m: m["timestamp"])

# Simulate with best config: SL=0.5R, no TP, 60 bars
def sim(item):
    for b in item["progression"][:60]:
        r = float(b.get("r", 0))
        if r <= -0.5: return -0.5
    if item["progression"][:60]:
        return float(item["progression"][min(59, len(item["progression"])-1)].get("r", 0))
    return 0.0

results = [(sim(m), m) for m in matched]
COST_20P = 1.2 / 20.0  # 0.06R

print("="*70)
print("AR8 — EDGE STABILITY AND REGIME ROBUSTNESS ANALYSIS")
print("="*70)
print(f"\nDataset: {len(matched)} WEAK+INTERESTING records (sorted chronologically)")
print(f"Simulation: SL=0.5R, no TP, 60 bars")
print(f"Cost assumption: 1.2 pips / 20 pip stop = {COST_20P:.4f}R")

raw_evs = [r for r, _ in results]
net_evs = [r - COST_20P for r, _ in results]
n = len(raw_evs)
raw_ev = sum(raw_evs)/n
net_ev = sum(net_evs)/n
print(f"Raw EV: {raw_ev:+.4f}R | Net EV: {net_ev:+.4f}R | n={n}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: TIME STABILITY (thirds)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: TIME STABILITY (chronological thirds)")
print("─"*70)

third = n // 3
periods = [
    ("Period 1 (earliest)", results[:third]),
    ("Period 2 (middle)", results[third:2*third]),
    ("Period 3 (latest)", results[2*third:]),
]

print(f"\n  {'Period':<25s} | {'n':>4s} | {'WR':>5s} | {'Raw EV':>7s} | {'Net EV':>7s} | {'MaxDD':>6s}")
print(f"  {'-'*25}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*7}-+-{'-'*6}")

for label, period_results in periods:
    rs = [r for r, _ in period_results]
    if rs:
        wins = sum(1 for r in rs if r > 0)
        ev = sum(rs)/len(rs)
        net = ev - COST_20P
        # Max drawdown
        cumsum = 0
        max_dd = 0
        for r in rs:
            cumsum += r
            if cumsum < max_dd:
                max_dd = cumsum
        print(f"  {label:<25s} | {len(rs):>4d} | {wins/len(rs):.1%} | {ev:>+6.4f} | {net:>+6.4f} | {max_dd:>+5.2f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: ROLLING WINDOW (20-trade windows)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: ROLLING WINDOW (20-trade windows)")
print("─"*70)

window = 20
rolling_evs = []
for i in range(0, n - window + 1, window // 2):
    chunk = [r for r, _ in results[i:i+window]]
    if chunk:
        rolling_evs.append(sum(chunk)/len(chunk))

if rolling_evs:
    positive_windows = sum(1 for e in rolling_evs if e > 0)
    negative_windows = sum(1 for e in rolling_evs if e <= 0)
    max_ev = max(rolling_evs)
    min_ev = min(rolling_evs)
    print(f"\n  Windows: {len(rolling_evs)}")
    print(f"  Positive EV windows: {positive_windows} ({positive_windows/len(rolling_evs)*100:.0f}%)")
    print(f"  Negative EV windows: {negative_windows} ({negative_windows/len(rolling_evs)*100:.0f}%)")
    print(f"  Best window EV: {max_ev:+.4f}R")
    print(f"  Worst window EV: {min_ev:+.4f}R")
    print(f"  EV range: {max_ev - min_ev:.4f}R")

    # Longest negative streak
    max_neg_streak = 0
    current_streak = 0
    for e in rolling_evs:
        if e <= 0:
            current_streak += 1
            max_neg_streak = max(max_neg_streak, current_streak)
        else:
            current_streak = 0
    print(f"  Longest negative streak: {max_neg_streak} consecutive windows")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: SYMBOL STABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: SYMBOL STABILITY")
print("─"*70)

print(f"\n  {'Symbol':<10s} | {'n':>4s} | {'WR':>5s} | {'Raw EV':>7s} | {'Net EV':>7s} | {'Positive?':>9s}")
print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*7}-+-{'-'*9}")

sym_positive = 0
sym_total = 0
for sym in sorted(set(m["symbol"] for m in matched)):
    sym_results = [r for r, m in results if m["symbol"] == sym]
    if len(sym_results) >= 5:
        sym_total += 1
        ev = sum(sym_results)/len(sym_results)
        net = ev - COST_20P
        wins = sum(1 for r in sym_results if r > 0)
        pos = "YES" if net > 0 else "no"
        if net > 0: sym_positive += 1
        print(f"  {sym:<10s} | {len(sym_results):>4d} | {wins/len(sym_results):.1%} | {ev:>+6.4f} | {net:>+6.4f} | {pos:>9s}")

print(f"\n  Symbols with positive net EV: {sym_positive}/{sym_total}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: DIRECTION STABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: DIRECTION AND HORIZON STABILITY")
print("─"*70)

for field, values in [("direction", ["BULLISH","BEARISH"]), ("horizon", ["SCALP","INTRADAY"])]:
    print(f"\n  By {field}:")
    for val in values:
        subset = [(r, m) for r, m in results if m["v3"].get(field) == val]
        if len(subset) >= 10:
            rs = [r for r, _ in subset]
            ev = sum(rs)/len(rs)
            net = ev - COST_20P
            wins = sum(1 for r in rs if r > 0)
            print(f"    {val:15s}: n={len(rs):3d} | WR={wins/len(rs):.1%} | Raw={ev:+.4f} | Net={net:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: COST SENSITIVITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: COST SENSITIVITY STRESS TEST")
print("─"*70)

print(f"\n  {'Scenario':<30s} | {'Cost/trade':>10s} | {'Net EV':>7s} | {'Profitable?':>11s}")
print(f"  {'-'*30}-+-{'-'*10}-+-{'-'*7}-+-{'-'*11}")

scenarios = [
    ("Current (1.2p/20p stop)", COST_20P),
    ("+25% cost (1.5p/20p)", 1.5/20.0),
    ("+50% cost (1.8p/20p)", 1.8/20.0),
    ("High spread (2.0p/20p)", 2.0/20.0),
    ("ECN tight (0.8p/20p)", 0.8/20.0),
    ("Institutional (0.5p/20p)", 0.5/20.0),
    ("15p stop (1.2p/15p)", 1.2/15.0),
    ("30p stop (1.2p/30p)", 1.2/30.0),
]

for label, cost in scenarios:
    net = raw_ev - cost
    prof = "YES" if net > 0 else "NO"
    print(f"  {label:<30s} | {cost:>9.4f}R | {net:>+6.4f} | {prof:>11s}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: MONTE CARLO (sequence risk)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: MONTE CARLO SEQUENCE RISK (10,000 simulations)")
print("─"*70)

random.seed(42)
NUM_SIMS = 10000
TRADE_COUNT = n  # Same as dataset

max_drawdowns = []
final_pnls = []
losing_streaks = []

for _ in range(NUM_SIMS):
    shuffled = random.choices(raw_evs, k=TRADE_COUNT)
    cumsum = 0
    peak = 0
    max_dd = 0
    streak = 0
    max_streak = 0
    for r in shuffled:
        net_r = r - COST_20P
        cumsum += net_r
        if cumsum > peak:
            peak = cumsum
        dd = peak - cumsum
        if dd > max_dd:
            max_dd = dd
        if net_r <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    max_drawdowns.append(max_dd)
    final_pnls.append(cumsum)
    losing_streaks.append(max_streak)

max_drawdowns.sort()
final_pnls.sort()
losing_streaks.sort()

print(f"\n  Over {TRADE_COUNT} trades ({NUM_SIMS} simulations):")
print(f"\n  Final P&L distribution:")
print(f"    Median: {final_pnls[NUM_SIMS//2]:+.2f}R")
print(f"    5th percentile: {final_pnls[int(NUM_SIMS*0.05)]:+.2f}R")
print(f"    95th percentile: {final_pnls[int(NUM_SIMS*0.95)]:+.2f}R")
print(f"    Probability of profit: {sum(1 for p in final_pnls if p > 0)/NUM_SIMS*100:.1f}%")

print(f"\n  Maximum drawdown distribution:")
print(f"    Median DD: {max_drawdowns[NUM_SIMS//2]:.2f}R")
print(f"    95th percentile DD: {max_drawdowns[int(NUM_SIMS*0.95)]:.2f}R")
print(f"    Worst DD (99th): {max_drawdowns[int(NUM_SIMS*0.99)]:.2f}R")

print(f"\n  Losing streak distribution:")
print(f"    Median: {losing_streaks[NUM_SIMS//2]} trades")
print(f"    95th percentile: {losing_streaks[int(NUM_SIMS*0.95)]} trades")
print(f"    Worst (99th): {losing_streaks[int(NUM_SIMS*0.99)]} trades")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 7: MINIMUM EVIDENCE ASSESSMENT
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 7: STATISTICAL POWER ASSESSMENT")
print("─"*70)

std = math.sqrt(sum((r - raw_ev)**2 for r in raw_evs) / max(n-1, 1))
se = std / math.sqrt(n)
ci_low = raw_ev - 1.96 * se
ci_high = raw_ev + 1.96 * se
t_stat = raw_ev / se if se > 0 else 0

# Required n for significance at current effect size
required_n_95 = int((1.96 * std / max(raw_ev, 0.001))**2) if raw_ev > 0 else 99999

print(f"\n  Current sample: n={n}")
print(f"  Raw EV: {raw_ev:+.4f}R")
print(f"  Std dev: {std:.4f}R")
print(f"  Standard error: {se:.4f}R")
print(f"  95% CI: [{ci_low:+.4f}, {ci_high:+.4f}]")
print(f"  t-statistic: {t_stat:.3f}")
print(f"  CI includes zero: {'YES' if ci_low <= 0 <= ci_high else 'NO'}")
print(f"  Required n for significance (95%): ~{required_n_95}")

if ci_low > 0:
    classification = "STATISTICALLY SIGNIFICANT positive EV"
elif ci_high < 0:
    classification = "STATISTICALLY SIGNIFICANT negative EV"
elif raw_ev > 0:
    classification = "POSITIVE but NOT statistically significant (underpowered)"
else:
    classification = "INDISTINGUISHABLE FROM ZERO"

print(f"\n  Classification: {classification}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR8 VERDICT")
print("="*70)

prob_profit = sum(1 for p in final_pnls if p > 0)/NUM_SIMS
periods_positive = sum(1 for _, period_results in periods if sum(r for r, _ in period_results)/len(period_results) > COST_20P)

if ci_low > 0 and sym_positive >= 4 and periods_positive >= 2:
    print("\n  A) Stable edge — suitable for controlled shadow validation")
elif raw_ev > 0 and prob_profit > 0.55 and ci_high > COST_20P:
    print("\n  B) Weak but promising — requires more data collection")
    print(f"     Raw EV positive ({raw_ev:+.4f}R)")
    print(f"     {prob_profit:.0%} probability of profit over {TRADE_COUNT} trades")
    print(f"     CI upper bound ({ci_high:+.4f}) exceeds cost threshold")
    print(f"     But CI includes zero — NOT statistically proven")
elif raw_ev > 0 and prob_profit > 0.45:
    print("\n  C) Edge unstable — not reliable")
    print(f"     Raw EV barely positive ({raw_ev:+.4f}R)")
    print(f"     Only {prob_profit:.0%} chance of profitability")
else:
    print("\n  D) No evidence of persistent edge")

print(f"\n  Key numbers:")
print(f"    Raw EV: {raw_ev:+.4f}R")
print(f"    Net EV @20p: {net_ev:+.4f}R")
print(f"    95% CI: [{ci_low:+.4f}, {ci_high:+.4f}]")
print(f"    P(profit over {n} trades): {prob_profit:.0%}")
print(f"    Median drawdown: {max_drawdowns[NUM_SIMS//2]:.2f}R")
print(f"    Symbols positive: {sym_positive}/{sym_total}")
print(f"    Periods positive (raw): {periods_positive}/3")
print()
