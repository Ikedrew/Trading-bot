"""V8.3 — Trade Reconstruction Audit.

Re-simulates historical observations under the INTENDED production policy
with correct geometry (structural stop/target placement for the inverted
direction) instead of naive result_r * -1.

For TREND policy instruments: the original signal says direction X,
we take the OPPOSITE. But we must place stops/targets using the
ACTUAL structural levels available at that moment.
"""
import json, math
from pathlib import Path
from datetime import datetime

print("=" * 70)
print("V8.3 — TRADE RECONSTRUCTION AUDIT")
print("=" * 70)

TREND_SYMS = ["NAS100", "US500"]  # Focus on validated instruments

# ═══════════════════════════════════════════════════════════════
# LOAD DATA SOURCES
# ═══════════════════════════════════════════════════════════════

# 1. Shadow trades (original signals)
shadow_dir = Path("logs/shadow_trades")
shadow_trades = {}
for sym in TREND_SYMS:
    d = shadow_dir / sym
    if not d.exists():
        continue
    trades = []
    for f in d.glob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("schema_version") != "shadow_trades_v2":
                continue
            snap = r.get("decision_snapshot", {})
            outcome = r.get("simulated_outcome", {})
            if outcome.get("pnl_r_multiple") is None:
                continue
            trades.append({
                "direction": snap.get("direction", ""),
                "entry_price": snap.get("entry_intent_price", 0),
                "stop_price": snap.get("stop_loss_intent", 0),
                "target_price": snap.get("take_profit_intent", 0),
                "bid": snap.get("bid_at_entry", 0),
                "ask": snap.get("ask_at_entry", 0),
                "timestamp": snap.get("timestamp_decision_utc", 0),
                "result_r": outcome["pnl_r_multiple"],
                "mfe_r": outcome.get("mfe_r", 0),
                "mae_r": outcome.get("mae_r", 0),
                "exit_reason": outcome.get("exit_reason", ""),
                "bars_held": outcome.get("bars_held", 0),
            })
    shadow_trades[sym] = sorted(trades, key=lambda t: t["timestamp"])

# 2. Market understanding (structural levels at each timestamp)
mu_dir = Path("logs/v3_shadow/market_understanding")
mu_data = {}
for sym in TREND_SYMS:
    sd = mu_dir / sym
    if not sd.exists():
        continue
    records = {}
    for f in sd.glob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            ts = int(r.get("timestamp_utc", 0))
            records[ts] = r
    mu_data[sym] = records

# 3. Candle data (for outcome simulation)
candle_dir = Path("replay_data")
candle_data = {}
for sym in TREND_SYMS:
    cd = candle_dir / sym / "5"
    if not cd.exists():
        continue
    candles = []
    for f in sorted(cd.glob("*.jsonl")):
        for line in open(f):
            if not line.strip():
                continue
            c = json.loads(line)
            candles.append({
                "ts": c["ts"] / 1000.0,  # ms to seconds
                "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"],
            })
    candle_data[sym] = candles

print(f"\n  Data loaded:")
for sym in TREND_SYMS:
    n_trades = len(shadow_trades.get(sym, []))
    n_mu = len(mu_data.get(sym, {}))
    n_candles = len(candle_data.get(sym, []))
    print(f"    {sym}: {n_trades} trades | {n_mu} market_understanding | {n_candles} candles")

# ═══════════════════════════════════════════════════════════════
# RECONSTRUCTION LOGIC
# ═══════════════════════════════════════════════════════════════

def find_structural_stop(sym, ts, inverted_direction, entry_price, mu_records):
    """Find the correct structural stop for the INVERTED direction.
    
    If inverted_direction = BUY:
      Stop goes BELOW the nearest structural support (swing low, demand OB)
    If inverted_direction = SELL:
      Stop goes ABOVE the nearest structural resistance (swing high, supply OB)
    """
    mu = mu_records.get(int(ts))
    if not mu:
        # Try nearby timestamps (±300s)
        for delta in [0, 300, -300, 600, -600]:
            mu = mu_records.get(int(ts) + delta)
            if mu:
                break
    if not mu:
        return None, None
    
    h1 = mu.get("h1", {})
    m15 = mu.get("m15", {})
    
    h1_swing_high = h1.get("swing_high", 0)
    h1_swing_low = h1.get("swing_low", 0)
    m15_swing_high = m15.get("swing_high", 0)
    m15_swing_low = m15.get("swing_low", 0)
    
    # Order blocks
    demand_ob_high = h1.get("active_demand_ob_high", 0)
    demand_ob_low = h1.get("active_demand_ob_low", 0)
    supply_ob_high = h1.get("active_supply_ob_high", 0)
    supply_ob_low = h1.get("active_supply_ob_low", 0)
    
    if inverted_direction == "BUY":
        # Stop below support — use nearest structural floor
        candidates = []
        if m15_swing_low > 0 and m15_swing_low < entry_price:
            candidates.append(m15_swing_low)
        if h1_swing_low > 0 and h1_swing_low < entry_price:
            candidates.append(h1_swing_low)
        if demand_ob_low > 0 and demand_ob_low < entry_price:
            candidates.append(demand_ob_low)
        
        if candidates:
            # Use nearest support below price (tightest valid stop)
            stop = max(candidates)  # Highest floor = tightest stop
            # Add buffer (1 ATR-point for indices)
            buffer = 2.0 if sym in ("NAS100",) else 0.5
            stop -= buffer
        else:
            return None, None
        
        # Target: 2:1 R:R above entry
        risk = entry_price - stop
        target = entry_price + (risk * 2.0)
        
    else:  # SELL
        # Stop above resistance — use nearest structural ceiling
        candidates = []
        if m15_swing_high > 0 and m15_swing_high > entry_price:
            candidates.append(m15_swing_high)
        if h1_swing_high > 0 and h1_swing_high > entry_price:
            candidates.append(h1_swing_high)
        if supply_ob_high > 0 and supply_ob_high > entry_price:
            candidates.append(supply_ob_high)
        
        if candidates:
            stop = min(candidates)  # Lowest ceiling = tightest stop
            buffer = 2.0 if sym in ("NAS100",) else 0.5
            stop += buffer
        else:
            return None, None
        
        risk = stop - entry_price
        target = entry_price - (risk * 2.0)
    
    return stop, target


def simulate_outcome(candles, entry_ts, entry_price, stop, target, direction, max_bars=60):
    """Walk forward on candle data to determine actual outcome."""
    # Find entry candle index
    start_idx = None
    for i, c in enumerate(candles):
        if c["ts"] >= entry_ts:
            start_idx = i
            break
    
    if start_idx is None:
        return None
    
    risk = abs(entry_price - stop)
    if risk == 0:
        return None
    
    mfe = 0.0
    mae = 0.0
    
    for bar_idx in range(start_idx, min(start_idx + max_bars, len(candles))):
        c = candles[bar_idx]
        
        if direction == "BUY":
            # Favourable: price goes up
            bar_mfe = (c["h"] - entry_price) / risk
            bar_mae = (entry_price - c["l"]) / risk
            
            mfe = max(mfe, bar_mfe)
            mae = max(mae, bar_mae)
            
            # Check stop hit (low touches stop)
            if c["l"] <= stop:
                return {"result_r": -1.0, "mfe_r": mfe, "mae_r": 1.0,
                        "exit": "stop_loss", "bars": bar_idx - start_idx + 1}
            # Check target hit (high touches target)
            if c["h"] >= target:
                target_r = (target - entry_price) / risk
                return {"result_r": target_r, "mfe_r": target_r, "mae_r": mae,
                        "exit": "take_profit", "bars": bar_idx - start_idx + 1}
        else:  # SELL
            bar_mfe = (entry_price - c["l"]) / risk
            bar_mae = (c["h"] - entry_price) / risk
            
            mfe = max(mfe, bar_mfe)
            mae = max(mae, bar_mae)
            
            if c["h"] >= stop:
                return {"result_r": -1.0, "mfe_r": mfe, "mae_r": 1.0,
                        "exit": "stop_loss", "bars": bar_idx - start_idx + 1}
            if c["l"] <= target:
                target_r = (entry_price - target) / risk
                return {"result_r": target_r, "mfe_r": target_r, "mae_r": mae,
                        "exit": "take_profit", "bars": bar_idx - start_idx + 1}
    
    # Timeout — use last price
    if candles and start_idx + max_bars <= len(candles):
        last_c = candles[min(start_idx + max_bars - 1, len(candles) - 1)]
        if direction == "BUY":
            timeout_r = (last_c["c"] - entry_price) / risk
        else:
            timeout_r = (entry_price - last_c["c"]) / risk
        return {"result_r": timeout_r, "mfe_r": mfe, "mae_r": mae,
                "exit": "timeout", "bars": max_bars}
    
    return None

# ═══════════════════════════════════════════════════════════════
# RUN RECONSTRUCTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("RECONSTRUCTION: Simulating inverted trades with structural geometry")
print("-" * 70)

reconstructed = {}
for sym in TREND_SYMS:
    trades = shadow_trades.get(sym, [])
    mu = mu_data.get(sym, {})
    candles = candle_data.get(sym, [])
    
    if not trades or not candles:
        print(f"\n  {sym}: insufficient data (trades={len(trades)}, candles={len(candles)})")
        continue
    
    results = []
    skipped = 0
    no_structure = 0
    no_candle_match = 0
    
    for trade in trades:
        orig_dir = trade["direction"]
        ts = trade["timestamp"]
        
        # Determine entry price
        entry = trade["entry_price"]
        if not entry or entry == 0:
            # Use bid/ask as proxy
            entry = trade["bid"] if orig_dir == "SELL" else trade["ask"]
        if not entry or entry == 0:
            skipped += 1
            continue
        
        # Invert direction (TREND policy)
        inverted_dir = "SELL" if orig_dir == "BUY" else "BUY"
        
        # Find structural stop/target for inverted direction
        stop, target = find_structural_stop(sym, ts, inverted_dir, entry, mu)
        if stop is None:
            no_structure += 1
            continue
        
        # Validate geometry (stop must be reasonable distance)
        risk = abs(entry - stop)
        if risk == 0 or risk > entry * 0.02:  # Max 2% of price
            skipped += 1
            continue
        
        # Simulate on candle data
        outcome = simulate_outcome(candles, ts, entry, stop, target, inverted_dir, max_bars=60)
        if outcome is None:
            no_candle_match += 1
            continue
        
        results.append({
            "orig_dir": orig_dir, "inv_dir": inverted_dir,
            "entry": entry, "stop": stop, "target": target,
            "risk": risk, "timestamp": ts,
            **outcome,
        })
    
    reconstructed[sym] = results
    print(f"\n  {sym}:")
    print(f"    Input trades: {len(trades)}")
    print(f"    Reconstructed: {len(results)}")
    print(f"    Skipped (no entry): {skipped}")
    print(f"    No structure available: {no_structure}")
    print(f"    No candle match: {no_candle_match}")

# ═══════════════════════════════════════════════════════════════
# RESULTS COMPARISON: PROXY vs RECONSTRUCTED
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("COMPARISON: Naive Proxy vs Structural Reconstruction")
print("-" * 70)

for sym in TREND_SYMS:
    recon = reconstructed.get(sym, [])
    trades = shadow_trades.get(sym, [])
    
    if not recon:
        print(f"\n  {sym}: No reconstructed trades available")
        continue
    
    # Proxy results (result_r * -1 on same trades)
    # Match by timestamp to compare equivalent trades
    recon_ts = set(r["timestamp"] for r in recon)
    matched_proxy = [t for t in trades if t["timestamp"] in recon_ts]
    proxy_results = [-t["result_r"] for t in matched_proxy]
    recon_results = [r["result_r"] for r in recon]
    
    n = len(recon_results)
    if n == 0:
        continue
    
    # Stats
    proxy_ev = sum(proxy_results) / len(proxy_results) if proxy_results else 0
    proxy_wr = sum(1 for r in proxy_results if r > 0) / len(proxy_results) if proxy_results else 0
    
    recon_ev = sum(recon_results) / n
    recon_wr = sum(1 for r in recon_results if r > 0) / n
    
    # Exit distribution
    recon_tp = sum(1 for r in recon if r["exit"] == "take_profit")
    recon_sl = sum(1 for r in recon if r["exit"] == "stop_loss")
    recon_to = sum(1 for r in recon if r["exit"] == "timeout")
    
    print(f"\n  {sym} (n={n} matched trades):")
    print(f"    {'Method':<25s}| {'WR':>5s} | {'EV':>8s} | {'Avg R':>6s}")
    print(f"    {'-'*25}+{'-'*6}-+{'-'*9}-+{'-'*7}")
    print(f"    {'Naive proxy (r*-1)':<25s}| {proxy_wr:.1%} | {proxy_ev:+.4f} | {'—':>6s}")
    print(f"    {'RECONSTRUCTED (struct)':<25s}| {recon_wr:.1%} | {recon_ev:+.4f} | {recon_ev:+.4f}")
    
    delta_ev = recon_ev - proxy_ev
    delta_wr = recon_wr - proxy_wr
    print(f"\n    Proxy vs Reality gap:")
    print(f"      EV: {delta_ev:+.4f}R ({'proxy OVERSTATES' if delta_ev < 0 else 'proxy UNDERSTATES'})")
    print(f"      WR: {delta_wr:+.1%}")
    
    print(f"\n    Exit distribution (reconstructed):")
    print(f"      Take profit: {recon_tp} ({recon_tp/n:.0%})")
    print(f"      Stop loss: {recon_sl} ({recon_sl/n:.0%})")
    print(f"      Timeout: {recon_to} ({recon_to/n:.0%})")
    
    # Risk geometry comparison
    risks = [r["risk"] for r in recon]
    avg_risk = sum(risks) / len(risks)
    print(f"\n    Avg structural stop distance: {avg_risk:.1f} points")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V8.3 VERDICT")
print("=" * 70)

total_recon = sum(len(v) for v in reconstructed.values())
if total_recon == 0:
    print(f"""
  VERDICT: CANNOT COMPLETE — Insufficient data for reconstruction

  The reconstruction requires:
  1. Shadow trades with entry_intent_price populated
  2. Market understanding at the same timestamp (structural levels)
  3. Candle data covering the outcome period
  
  Available data is limited to 2 days of candles per symbol.
  Most shadow trades occurred on dates WITHOUT candle data.
  
  RESOLUTION: Run replay on historical dates to generate candle cache,
  OR wait for live collection to accumulate matched data.
""")
else:
    all_recon_results = []
    all_proxy_results = []
    for sym in TREND_SYMS:
        recon = reconstructed.get(sym, [])
        trades = shadow_trades.get(sym, [])
        recon_ts = set(r["timestamp"] for r in recon)
        matched = [t for t in trades if t["timestamp"] in recon_ts]
        all_recon_results.extend([r["result_r"] for r in recon])
        all_proxy_results.extend([-t["result_r"] for t in matched])
    
    if all_recon_results:
        proxy_ev = sum(all_proxy_results) / len(all_proxy_results)
        recon_ev = sum(all_recon_results) / len(all_recon_results)
        gap = recon_ev - proxy_ev
        gap_pct = abs(gap / proxy_ev) * 100 if proxy_ev != 0 else 0
        
        print(f"\n  COMBINED RESULTS ({total_recon} reconstructed trades):")
        print(f"    Proxy EV (r*-1): {proxy_ev:+.4f}R")
        print(f"    Reconstructed EV: {recon_ev:+.4f}R")
        print(f"    Gap: {gap:+.4f}R ({gap_pct:.0f}% {'overstatement' if gap<0 else 'understatement'})")
        
        if abs(gap) < 0.03:
            print(f"\n  VERDICT: Proxy is ACCURATE (gap < 0.03R)")
            print(f"  The naive inversion is a valid approximation of true trade outcomes.")
        elif gap < -0.05:
            print(f"\n  VERDICT: Proxy OVERSTATES edge by {abs(gap):.4f}R")
            print(f"  True structural trades produce less EV than simple inversion suggests.")
            print(f"  Adjusted EV estimate: {recon_ev:+.4f}R")
        elif gap > 0.05:
            print(f"\n  VERDICT: Proxy UNDERSTATES edge by {gap:.4f}R") 
            print(f"  Structural trades actually perform BETTER than inversion proxy.")
        else:
            print(f"\n  VERDICT: Moderate gap ({gap:+.4f}R) — proxy is approximate but directionally correct")

print()
