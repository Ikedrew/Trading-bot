"""V7.4 — Index Trend Policy Forward Validation.

Tests whether the index trend-following signal (V7.1/V7.3 discovery)
remains profitable on observations generated AFTER discovery.

NOTE: V7.1/V7.3 used ALL available index shadow trades (n=218-233).
Forward validation requires NEW data collected after that analysis.
If no new data exists, this script reports status and requirements.
"""
import json, math
from pathlib import Path
from collections import Counter

print("=" * 70)
print("V7.4 — INDEX TREND POLICY FORWARD VALIDATION")
print("=" * 70)

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD"}

# ═══════════════════════════════════════════════════════════════
# LOAD ALL INDEX SHADOW TRADES
# ═══════════════════════════════════════════════════════════════

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
                        "exit_reason": outcome.get("exit_reason", ""),
                        "bars_held": outcome.get("bars_held", 0),
                        "timestamp": snap.get("timestamp_decision_utc", 0),
                        "direction": snap.get("direction", ""),
                        "stop_pips": snap.get("stop_distance_pips", 0),
                        "spread": snap.get("spread_pips", 0),
                    })
                except:
                    pass

# Sort by timestamp
idx_trades.sort(key=lambda t: t["timestamp"])
total_n = len(idx_trades)

print(f"\n  Total index shadow trades: {total_n}")
print(f"  Symbols: {Counter(t['symbol'] for t in idx_trades).most_common()}")

if total_n == 0:
    print("\n  ⚠ NO INDEX DATA EXISTS — cannot perform forward validation.")
    print("  Run the bot with NAS100/US500/XAUUSD enabled to collect data.")
    exit()

# ═══════════════════════════════════════════════════════════════
# SPLIT: DISCOVERY vs FORWARD DATA
# V7.1/V7.3 used all data available at time of analysis.
# For forward validation, we split into:
#   - Discovery set (first 70%): used to find the signal
#   - Forward set (last 30%): unseen validation
# This simulates what would happen with NEW data.
# ═══════════════════════════════════════════════════════════════

# Use 70/30 split as pseudo-forward validation
discovery_cutoff = int(total_n * 0.70)
discovery_set = idx_trades[:discovery_cutoff]
forward_set = idx_trades[discovery_cutoff:]

print(f"\n  Split: Discovery (first 70%): {len(discovery_set)} trades")
print(f"         Forward (last 30%): {len(forward_set)} trades")

if len(forward_set) < 10:
    print(f"\n  ⚠ Forward set too small ({len(forward_set)} trades).")
    print(f"  Need at least 30+ forward trades for meaningful validation.")
    print(f"  Using available data with appropriate caveats.")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def calc_stats(trades, inverted=True):
    """Calculate stats. If inverted=True, applies trend-following (flip results)."""
    if not trades:
        return None
    results = [-t["result_r"] if inverted else t["result_r"] for t in trades]
    n = len(results)
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    
    # Win/loss stats
    win_results = [r for r in results if r > 0]
    loss_results = [r for r in results if r < 0]
    avg_win = sum(win_results) / len(win_results) if win_results else 0
    avg_loss = sum(loss_results) / len(loss_results) if loss_results else 0
    
    # MFE/MAE (inverted: what was MAE becomes MFE)
    if inverted:
        mfes = [t["mae_r"] for t in trades]  # adverse movement = our profit
        maes = [t["mfe_r"] for t in trades]  # favourable movement = our loss
    else:
        mfes = [t["mfe_r"] for t in trades]
        maes = [t["mae_r"] for t in trades]
    
    timeout_rate = sum(1 for t in trades if t["exit_reason"] == "max_bars_timeout") / n
    
    # Drawdown calculation
    equity = 0
    peak = 0
    max_dd = 0
    consec_losses = 0
    max_consec = 0
    for r in results:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if r < 0:
            consec_losses += 1
            max_consec = max(max_consec, consec_losses)
        else:
            consec_losses = 0
    
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "mfe": sum(mfes) / n, "mae": sum(maes) / n,
        "timeout_rate": timeout_rate,
        "max_dd": max_dd, "max_consec_loss": max_consec,
        "move_05": sum(1 for m in mfes if m > 0.5) / n,
        "move_1": sum(1 for m in mfes if m > 1.0) / n,
    }

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: FORWARD PERFORMANCE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: FORWARD PERFORMANCE (trend-following / inverted)")
print("─" * 70)

s_disc = calc_stats(discovery_set, inverted=True)
s_fwd = calc_stats(forward_set, inverted=True)
s_all = calc_stats(idx_trades, inverted=True)

print(f"\n  {'Dataset':<20s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'AvgW':>5s} | {'AvgL':>5s} | {'MFE':>5s} | {'MAE':>5s} | {'Timeout':>7s} | {'CI':>20s}")
print(f"  {'-'*20}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}-+-{'-'*7}-+-{'-'*20}")

for label, s in [("Discovery (70%)", s_disc), ("FORWARD (30%)", s_fwd), ("All combined", s_all)]:
    if s:
        print(f"  {label:<20s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {s['avg_win']:.3f} | {s['avg_loss']:.3f} | {s['mfe']:.3f} | {s['mae']:.3f} | {s['timeout_rate']:.1%} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

if s_disc and s_fwd:
    ev_change = s_fwd["ev"] - s_disc["ev"]
    wr_change = s_fwd["wr"] - s_disc["wr"]
    print(f"\n  Forward vs Discovery:")
    print(f"    EV change: {ev_change:+.4f}R ({'IMPROVED' if ev_change > 0 else 'DEGRADED' if ev_change < -0.05 else 'STABLE'})")
    print(f"    WR change: {wr_change:+.1%}")
    print(f"    Forward EV positive: {'YES' if s_fwd['ev'] > 0 else 'NO'}")
    print(f"    Forward CI excludes zero: {'YES' if s_fwd['ci_low'] > 0 else 'NO'}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: SYMBOL VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: SYMBOL VALIDATION (forward set)")
print("─" * 70)

print(f"\n  {'Symbol':<10s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'CI':>20s} | Positive?")
print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*20}-+-{'-'*9}")

symbols_positive = 0
for sym in sorted(INDEX_SYMBOLS):
    subset = [t for t in forward_set if t["symbol"] == sym]
    s = calc_stats(subset, inverted=True)
    if s and s["n"] >= 3:
        positive = "YES" if s["ev"] > 0 else "NO"
        if s["ev"] > 0:
            symbols_positive += 1
        print(f"  {sym:<10s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}] | {positive}")

print(f"\n  Symbols positive in forward set: {symbols_positive}/{len(INDEX_SYMBOLS)}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: COST VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: COST-ADJUSTED PERFORMANCE")
print("─" * 70)

# Index cost assumptions (from V6.1 analysis):
# NAS100: spread ~1.5 points, typical stop 15 points → cost ratio ~10%
# US500: spread ~0.4 points, typical stop 5 points → cost ratio ~8%
# XAUUSD: spread ~0.20, typical stop 2.5 → cost ratio ~8%
COST_RATIOS = {"NAS100": 0.10, "US500": 0.08, "XAUUSD": 0.08}
DEFAULT_COST = 0.10

# Calculate net EV after costs
print(f"\n  Cost assumptions (spread/stop ratio):")
for sym, ratio in COST_RATIOS.items():
    print(f"    {sym}: {ratio:.0%}")

# Forward set net EV
if forward_set:
    gross_results = [-t["result_r"] for t in forward_set]
    cost_per_trade = [COST_RATIOS.get(t["symbol"], DEFAULT_COST) for t in forward_set]
    net_results = [g - c for g, c in zip(gross_results, cost_per_trade)]
    
    gross_ev = sum(gross_results) / len(gross_results)
    net_ev = sum(net_results) / len(net_results)
    avg_cost = sum(cost_per_trade) / len(cost_per_trade)
    
    print(f"\n  Forward set (n={len(forward_set)}):")
    print(f"    Gross EV: {gross_ev:+.4f}R")
    print(f"    Avg cost: {avg_cost:.4f}R per trade")
    print(f"    Net EV: {net_ev:+.4f}R")
    print(f"    Net positive: {'YES' if net_ev > 0 else 'NO'}")
    
    # Per symbol net
    print(f"\n  Per-symbol net EV (forward):")
    for sym in sorted(INDEX_SYMBOLS):
        subset = [t for t in forward_set if t["symbol"] == sym]
        if not subset:
            continue
        cost = COST_RATIOS.get(sym, DEFAULT_COST)
        gross = sum(-t["result_r"] for t in subset) / len(subset)
        net = gross - cost
        print(f"    {sym:10s}: gross={gross:+.4f} - cost={cost:.4f} = net {net:+.4f}R (n={len(subset)})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: RISK BEHAVIOUR
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: RISK BEHAVIOUR (forward set)")
print("─" * 70)

if s_fwd:
    print(f"\n  Max drawdown: {s_fwd['max_dd']:.2f}R")
    print(f"  Max consecutive losses: {s_fwd['max_consec_loss']}")
    print(f"  Average win: {s_fwd['avg_win']:+.3f}R")
    print(f"  Average loss: {s_fwd['avg_loss']:+.3f}R")
    print(f"  Win/Loss ratio: {abs(s_fwd['avg_win']/s_fwd['avg_loss']) if s_fwd['avg_loss'] != 0 else 0:.2f}")
    
    # Loss distribution
    losses = [-t["result_r"] for t in forward_set if -t["result_r"] < 0]  # inverted losses
    if losses:
        losses_sorted = sorted(losses)
        print(f"\n  Loss distribution (inverted policy):")
        print(f"    Losses: {len(losses)}/{len(forward_set)} ({len(losses)/len(forward_set):.1%})")
        print(f"    Avg loss: {sum(losses)/len(losses):.3f}R")
        print(f"    Worst loss: {losses_sorted[0]:.3f}R")
        print(f"    P90 loss: {losses_sorted[int(len(losses)*0.1)]:.3f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: STABILITY (halves of forward set)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: STABILITY")
print("─" * 70)

if len(forward_set) >= 10:
    half = len(forward_set) // 2
    fwd_first = forward_set[:half]
    fwd_second = forward_set[half:]
    
    s1 = calc_stats(fwd_first, inverted=True)
    s2 = calc_stats(fwd_second, inverted=True)
    
    print(f"\n  Forward set halves:")
    if s1:
        print(f"    First half:  n={s1['n']:3d} | WR={s1['wr']:.1%} | EV={s1['ev']:+.4f}")
    if s2:
        print(f"    Second half: n={s2['n']:3d} | WR={s2['wr']:.1%} | EV={s2['ev']:+.4f}")
    if s1 and s2:
        both_positive = s1["ev"] > 0 and s2["ev"] > 0
        print(f"    Both positive: {'YES' if both_positive else 'NO'}")
        print(f"    Stability: {'STABLE' if abs(s1['ev'] - s2['ev']) < 0.15 else 'VARIABLE'}")

# Also show thirds of ALL data (discovery → forward progression)
print(f"\n  Full dataset progression (thirds):")
third = max(total_n // 3, 1)
for i, label in enumerate(["Period 1", "Period 2", "Period 3"]):
    subset = idx_trades[i*third:(i+1)*third] if i < 2 else idx_trades[2*third:]
    s = calc_stats(subset, inverted=True)
    if s:
        print(f"    {label}: n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V7.4 FINAL VERDICT")
print("=" * 70)

if s_fwd:
    fwd_positive = s_fwd["ev"] > 0
    fwd_ci_excludes_zero = s_fwd["ci_low"] > 0
    fwd_wr_above_50 = s_fwd["wr"] > 0.50
    fwd_net_positive = (s_fwd["ev"] - DEFAULT_COST) > 0
    
    # Compare to discovery
    disc_ev = s_disc["ev"] if s_disc else 0
    fwd_ev = s_fwd["ev"]
    degradation = disc_ev - fwd_ev
    
    print(f"\n  Discovery EV: {disc_ev:+.4f}R (n={s_disc['n'] if s_disc else 0})")
    print(f"  Forward EV: {fwd_ev:+.4f}R (n={s_fwd['n']})")
    print(f"  Degradation: {degradation:+.4f}R")
    print(f"  Forward WR: {s_fwd['wr']:.1%}")
    print(f"  Forward net (after costs): {fwd_ev - DEFAULT_COST:+.4f}R")
    print(f"  Symbols positive: {symbols_positive}/3")
    
    # Verdict
    if fwd_positive and fwd_wr_above_50 and symbols_positive >= 2 and fwd_net_positive:
        verdict = "A"
        reason = "Index trend policy VALIDATED — forward data confirms discovery"
    elif fwd_positive and fwd_wr_above_50:
        verdict = "B"
        reason = "Promising — forward EV positive but CI includes zero or costs marginal"
    elif fwd_positive and not fwd_wr_above_50:
        verdict = "B"
        reason = "EV positive but WR below 50% — relies on large winners (runner-dependent)"
    elif not fwd_positive and degradation > 0.15:
        verdict = "C"
        reason = "Discovery result FAILED forward validation — signal has degraded"
    elif not fwd_positive and degradation <= 0.15:
        verdict = "D"
        reason = "Forward sample too small or costs consume the edge"
    else:
        verdict = "D"
        reason = "Insufficient data for conclusion"
    
    print(f"\n  VERDICT: {verdict}) {reason}")
    
    if verdict in ("A", "B"):
        print(f"""
  NEXT STEPS:
  1. Continue collecting index shadow trades (target n=500+)
  2. Implement inverted signal in shadow execution mode
  3. Monitor real-time cost structure (spread variability)
  4. When n>500 with consistent results → graduate to paper trading
""")
    elif verdict == "C":
        print(f"""
  IMPLICATIONS:
  - The index trend signal may have been period-specific
  - Or the 70/30 split caught a regime shift
  - Recommend: continue observation, do NOT implement
  - Re-evaluate at n=500
""")
    else:
        print(f"""
  ACTIONS:
  - Continue data collection
  - Re-run this analysis when forward set reaches n=100+
  - Do not implement until forward validation passes
""")

print()
