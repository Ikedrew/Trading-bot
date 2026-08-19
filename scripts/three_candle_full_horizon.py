"""
FULL 60-BAR HORIZON RESCUE EXPERIMENT — THREE_BLACK_CROWS / THREE_WHITE_SOLDIERS

Methodology:
- Load the raw shadow trade records for TBC/TWS from logs/shadow_trades/
- For each trade, extract: entry_price, direction, take_profit, entry_time, entry_bar_index, symbol
- Load the corresponding M5 candle data from the same period
- Re-simulate with alternative stop constructions: 1.25R, 1.5R, 2.0R, 3.0R, 5.0R
- Also simulate inverted variants (TBC→BUY, TWS→SELL) 
- Use EXACTLY the same methodology as ShadowTradeEngine.evaluate_bar()

Controls:
- Same entry timing, same direction (for normal), same TP, same candle data
- Only stop_loss changes
- Same 60-bar timeout, same SL-checked-before-TP rule

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, ".")

# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SHADOW SIMULATION (replicates ShadowTradeEngine.evaluate_bar exactly)
# ═══════════════════════════════════════════════════════════════════════════════

MAX_BARS = 60

def simulate_trade(*, direction, entry_price, stop_loss, take_profit, candles):
    """
    Simulate a single trade over up to 60 bars of M5 candle data.
    
    Exactly replicates ShadowTradeEngine.evaluate_bar() logic:
    - SL checked before TP on same bar
    - BUY: SL hit if bar_low <= stop_loss, TP hit if bar_high >= take_profit
    - SELL: SL hit if bar_high >= stop_loss, TP hit if bar_low <= take_profit
    - Timeout at MAX_BARS with exit at bar_close
    - MFE/MAE tracked each bar before exit check
    
    Returns dict with: r_multiple, exit_reason, bars_held, mfe_r, mae_r, exit_price
    """
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return {"r_multiple": 0, "exit_reason": "zero_risk", "bars_held": 0,
                "mfe_r": 0, "mae_r": 0, "exit_price": entry_price}

    if direction == "BUY":
        max_fav = entry_price
        max_adv = entry_price
    else:
        max_fav = entry_price
        max_adv = entry_price

    bars_held = 0
    exit_price = None
    exit_reason = ""

    for i, candle in enumerate(candles[:MAX_BARS]):
        bars_held += 1
        bar_high = candle["high"]
        bar_low = candle["low"]
        bar_close = candle["close"]

        # Update MFE/MAE (before exit check, same as production)
        if direction == "BUY":
            max_fav = max(max_fav, bar_high)
            max_adv = min(max_adv, bar_low)
        else:
            max_fav = min(max_fav, bar_low)
            max_adv = max(max_adv, bar_high)

        # Exit check: SL first, then TP (conservative, same as production)
        if direction == "BUY":
            if bar_low <= stop_loss:
                exit_price = stop_loss
                exit_reason = "stop_loss"
                break
            elif bar_high >= take_profit:
                exit_price = take_profit
                exit_reason = "take_profit"
                break
        else:  # SELL
            if bar_high >= stop_loss:
                exit_price = stop_loss
                exit_reason = "stop_loss"
                break
            elif bar_low <= take_profit:
                exit_price = take_profit
                exit_reason = "take_profit"
                break

    # Timeout
    if exit_price is None:
        if candles:
            exit_price = candles[min(MAX_BARS - 1, len(candles) - 1)]["close"]
        else:
            exit_price = entry_price
        exit_reason = "max_bars_timeout"
        bars_held = min(MAX_BARS, len(candles))

    # Compute R
    if direction == "BUY":
        pnl = exit_price - entry_price
    else:
        pnl = entry_price - exit_price
    r_multiple = round(pnl / risk, 4)

    # MFE/MAE in R
    if direction == "BUY":
        mfe = max(0, max_fav - entry_price) / risk
        mae = max(0, entry_price - max_adv) / risk
    else:
        mfe = max(0, entry_price - max_fav) / risk
        mae = max(0, max_adv - entry_price) / risk

    return {
        "r_multiple": r_multiple,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "mfe_r": round(mfe, 4),
        "mae_r": round(mae, 4),
        "exit_price": exit_price,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_shadow_trades_raw():
    """Load raw shadow trade JSONL files."""
    records = []
    base = Path("logs/shadow_trades")
    for sym_dir in base.iterdir():
        if not sym_dir.is_dir():
            continue
        for f in sym_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def load_candle_data(symbol, start_time, n_bars=65):
    """
    Load M5 candle data for a symbol starting AFTER a specific time.
    Uses MT5 copy_rates_range to get bars in the period after entry.
    """
    try:
        import MetaTrader5 as mt5
        from datetime import datetime, timezone
        
        # Get bars starting 1 second after entry, covering enough for 60+ bars
        dt_start = datetime.fromtimestamp(start_time + 1, tz=timezone.utc)
        dt_end = datetime.fromtimestamp(start_time + 20000, tz=timezone.utc)  # ~5.5 hours
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_start, dt_end)
        
        if rates is None or len(rates) == 0:
            return []
        
        candles = []
        for i in range(min(n_bars, len(rates))):
            candles.append({
                "time": int(rates[i][0]),
                "open": float(rates[i][1]),
                "high": float(rates[i][2]),
                "low": float(rates[i][3]),
                "close": float(rates[i][4]),
            })
        return candles
    except Exception as e:
        return []


def extract_trade_params(record):
    """Extract trade parameters from raw shadow JSONL (v2 schema)."""
    # Handle both v2 (nested) and legacy (flat) schemas
    if "identity" in record:
        # v2 schema
        identity = record.get("identity", {})
        snapshot = record.get("decision_snapshot", {})
        outcome = record.get("simulated_outcome", {})
        
        return {
            "trade_id": identity.get("trade_id", ""),
            "symbol": identity.get("symbol", ""),
            "correlation_id": identity.get("correlation_id", ""),
            "shadow_type": identity.get("shadow_type", ""),
            "direction": snapshot.get("direction", ""),
            "entry_price": snapshot.get("entry_intent_price", 0),
            "stop_loss": snapshot.get("stop_loss_intent", 0),
            "take_profit": snapshot.get("take_profit_intent", 0),
            "entry_time": snapshot.get("timestamp_decision_utc", 0),
            "pattern": snapshot.get("pattern", ""),
            "score": snapshot.get("score", 0),
            "risk_distance": snapshot.get("risk_config_snapshot", {}).get("risk_price_distance", 0),
            "original_r": outcome.get("pnl_r_multiple", 0),
            "original_exit": outcome.get("exit_reason", ""),
            "original_bars": outcome.get("bars_held", 0),
            "original_mfe": outcome.get("mfe_r", 0),
            "original_mae": outcome.get("mae_r", 0),
        }
    else:
        # Legacy schema (flat)
        return {
            "trade_id": record.get("trade_id", ""),
            "symbol": record.get("symbol", ""),
            "correlation_id": record.get("correlation_id", ""),
            "shadow_type": record.get("shadow_type", ""),
            "direction": record.get("direction", ""),
            "entry_price": record.get("entry_price", 0),
            "stop_loss": record.get("stop_loss", 0),
            "take_profit": record.get("take_profit", 0),
            "entry_time": record.get("entry_time", 0),
            "pattern": record.get("pattern", ""),
            "score": record.get("score", 0),
            "risk_distance": abs(record.get("entry_price", 0) - record.get("stop_loss", 0)),
            "original_r": record.get("r_multiple", 0),
            "original_exit": record.get("exit_reason", ""),
            "original_bars": record.get("bars_held", 0),
            "original_mfe": record.get("mfe_r", 0),
            "original_mae": record.get("mae_r", 0),
        }


def main():
    out = []
    out.append("=" * 80)
    out.append("FULL 60-BAR HORIZON RESCUE EXPERIMENT")
    out.append("THREE_BLACK_CROWS / THREE_WHITE_SOLDIERS")
    out.append("=" * 80)
    out.append("")

    # Load raw shadow trades
    raw_shadows = load_shadow_trades_raw()
    out.append(f"Total raw shadow trades loaded: {len(raw_shadows)}")

    # Extract params and filter to TBC/TWS with correlation_id (real execution period)
    all_params = [extract_trade_params(r) for r in raw_shadows]
    tbc_params = [p for p in all_params if p["pattern"] == "THREE_BLACK_CROWS" and p["correlation_id"]]
    tws_params = [p for p in all_params if p["pattern"] == "THREE_WHITE_SOLDIERS" and p["correlation_id"]]

    out.append(f"THREE_BLACK_CROWS (real, with corr_id): {len(tbc_params)}")
    out.append(f"THREE_WHITE_SOLDIERS (real, with corr_id): {len(tws_params)}")
    out.append("")

    # Attempt to load candle data for simulation
    out.append("Loading candle data from MT5...")
    
    # Test if MT5 is available
    mt5_available = False
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            mt5_available = True
            out.append("  MT5 initialized successfully")
        else:
            out.append("  MT5 initialization failed — using persisted shadow data only")
    except Exception as e:
        out.append(f"  MT5 not available ({e}) — using persisted shadow data only")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STRATEGY: If MT5 available, re-simulate with fresh candle data.
    # If MT5 NOT available, use the MAE/MFE from bar-1 data and extrapolate
    # using a more rigorous approach based on the original shadow's internal state.
    # ═══════════════════════════════════════════════════════════════════════════

    if mt5_available:
        out.append("━" * 80)
        out.append("FULL SIMULATION WITH MT5 CANDLE DATA")
        out.append("━" * 80)
        out.append("")

        # Run experiment for each pattern and stop variant
        stop_multipliers = [1.0, 1.25, 1.5, 2.0, 3.0, 5.0]
        
        for pattern_name, params_list in [("THREE_BLACK_CROWS", tbc_params), ("THREE_WHITE_SOLDIERS", tws_params)]:
            out.append(f"{'═' * 40}")
            out.append(f"  {pattern_name} (N={len(params_list)})")
            out.append(f"{'═' * 40}")
            out.append("")

            # Normal direction variants
            results_by_sl = defaultdict(list)
            
            for p in params_list:
                if not p["entry_price"] or not p["stop_loss"] or not p["entry_time"]:
                    continue

                risk_dist = abs(p["entry_price"] - p["stop_loss"])
                if risk_dist <= 0:
                    continue

                # Load candles for this trade
                candles = load_candle_data(p["symbol"], p["entry_time"], n_bars=65)
                if len(candles) < 5:
                    continue

                # Simulate with each stop multiplier
                for sl_mult in stop_multipliers:
                    if p["direction"] == "BUY":
                        new_sl = p["entry_price"] - risk_dist * sl_mult
                    else:
                        new_sl = p["entry_price"] + risk_dist * sl_mult

                    result = simulate_trade(
                        direction=p["direction"],
                        entry_price=p["entry_price"],
                        stop_loss=new_sl,
                        take_profit=p["take_profit"],
                        candles=candles,
                    )
                    result["symbol"] = p["symbol"]
                    result["sl_mult"] = sl_mult
                    results_by_sl[sl_mult].append(result)

            # Report results
            out.append(f"  {'SL Width':<9} {'N':<5} {'Mean R':<9} {'Med R':<9} {'WR%':<7} {'SL%':<7} {'TP%':<7} {'TO%':<7} {'MFE':<7} {'MAE':<7} {'TotalR'}")
            out.append(f"  {'─'*9} {'─'*5} {'─'*9} {'─'*9} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*7}")

            for sl_mult in stop_multipliers:
                res = results_by_sl.get(sl_mult, [])
                if not res:
                    continue
                r_vals = [r["r_multiple"] for r in res]
                exits = Counter(r["exit_reason"] for r in res)
                mfes = [r["mfe_r"] for r in res]
                maes = [r["mae_r"] for r in res]
                
                mean_r = statistics.mean(r_vals)
                med_r = statistics.median(r_vals)
                wr = sum(1 for r in r_vals if r > 0) * 100 / len(r_vals)
                sl_pct = exits.get("stop_loss", 0) * 100 // len(res)
                tp_pct = exits.get("take_profit", 0) * 100 // len(res)
                to_pct = exits.get("max_bars_timeout", 0) * 100 // len(res)
                total_r = sum(r_vals)

                out.append(f"  {sl_mult}R{'':<6} {len(res):<5} {mean_r:+.4f}  {med_r:+.4f}  "
                           f"{wr:<7.1f} {sl_pct}%{'':<4} {tp_pct}%{'':<4} {to_pct}%{'':<4} "
                           f"{statistics.mean(mfes):.3f}  {statistics.mean(maes):.3f}  {total_r:+.1f}")
            out.append("")

            # R distribution for best candidate (1.5R)
            best_res = results_by_sl.get(1.5, [])
            if best_res:
                r_vals = sorted([r["r_multiple"] for r in best_res])
                out.append(f"  1.5R stop R distribution:")
                buckets = [(-2, -0.9, "full SL"), (-0.9, -0.5, "partial loss"), 
                           (-0.5, 0, "small loss"), (0, 0.5, "small win"),
                           (0.5, 1.5, "good win"), (1.5, 5, "large win"), (5, 999, "exceptional")]
                for lo, hi, label in buckets:
                    count = sum(1 for r in r_vals if lo <= r < hi)
                    if count:
                        out.append(f"    {label}: {count} ({count*100//len(r_vals)}%)")
                out.append("")

            # ─── INVERTED VARIANT ─────────────────────────────────────
            out.append(f"  INVERTED {pattern_name} ({'BUY' if 'BLACK' in pattern_name else 'SELL'}):")
            inv_results_by_sl = defaultdict(list)

            for p in params_list:
                if not p["entry_price"] or not p["stop_loss"] or not p["entry_time"]:
                    continue
                risk_dist = abs(p["entry_price"] - p["stop_loss"])
                if risk_dist <= 0:
                    continue

                candles = load_candle_data(p["symbol"], p["entry_time"], n_bars=65)
                if len(candles) < 5:
                    continue

                inv_direction = "BUY" if p["direction"] == "SELL" else "SELL"

                for sl_mult in stop_multipliers:
                    if inv_direction == "BUY":
                        new_sl = p["entry_price"] - risk_dist * sl_mult
                        new_tp = p["entry_price"] + risk_dist * 3.0  # 3R TP for inverted
                    else:
                        new_sl = p["entry_price"] + risk_dist * sl_mult
                        new_tp = p["entry_price"] - risk_dist * 3.0

                    result = simulate_trade(
                        direction=inv_direction,
                        entry_price=p["entry_price"],
                        stop_loss=new_sl,
                        take_profit=new_tp,
                        candles=candles,
                    )
                    result["symbol"] = p["symbol"]
                    inv_results_by_sl[sl_mult].append(result)

            out.append(f"  {'SL Width':<9} {'N':<5} {'Mean R':<9} {'Med R':<9} {'WR%':<7} {'SL%':<7} {'TP%':<7} {'TO%':<7} {'TotalR'}")
            out.append(f"  {'─'*9} {'─'*5} {'─'*9} {'─'*9} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*7}")
            for sl_mult in stop_multipliers:
                res = inv_results_by_sl.get(sl_mult, [])
                if not res:
                    continue
                r_vals = [r["r_multiple"] for r in res]
                exits = Counter(r["exit_reason"] for r in res)
                mean_r = statistics.mean(r_vals)
                med_r = statistics.median(r_vals)
                wr = sum(1 for r in r_vals if r > 0) * 100 / len(r_vals)
                sl_pct = exits.get("stop_loss", 0) * 100 // len(res)
                tp_pct = exits.get("take_profit", 0) * 100 // len(res)
                to_pct = exits.get("max_bars_timeout", 0) * 100 // len(res)
                total_r = sum(r_vals)
                out.append(f"  {sl_mult}R{'':<6} {len(res):<5} {mean_r:+.4f}  {med_r:+.4f}  "
                           f"{wr:<7.1f} {sl_pct}%{'':<4} {tp_pct}%{'':<4} {to_pct}%{'':<4} {total_r:+.1f}")
            out.append("")

    else:
        out.append("━" * 80)
        out.append("MT5 NOT AVAILABLE — CANNOT PERFORM FULL 60-BAR SIMULATION")
        out.append("━" * 80)
        out.append("")
        out.append("  The full-horizon experiment requires loading 60 bars of M5 candle data")
        out.append("  AFTER the entry time for each of the 38 trades. MT5 must be running")
        out.append("  and connected to provide historical candle data.")
        out.append("")
        out.append("  WITHOUT MT5, we can only report the bar-1 analysis (already completed).")
        out.append("")
        out.append("  STATUS: EXPERIMENT BLOCKED — requires MT5 connection")
        out.append("")
        out.append("  CLASSIFICATION: AMBER")
        out.append("  The bar-1 evidence is promising (1.5R stop → +0.38R EV) but the full")
        out.append("  60-bar validation cannot be performed without historical candle access.")
        out.append("")
        out.append("  NEXT STEP: Re-run this script when MT5 is connected and market data")
        out.append("  is available for the period 2026-07-22 to 2026-08-13.")

    # ═══════════════════════════════════════════════════════════════════════════
    # FINAL CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("FINAL CLASSIFICATION")
    out.append("=" * 80)
    out.append("")

    if mt5_available and results_by_sl:
        # Determine classification based on full results
        best_15 = results_by_sl.get(1.5, [])
        if best_15:
            mean_15 = statistics.mean([r["r_multiple"] for r in best_15])
            wr_15 = sum(1 for r in best_15 if r["r_multiple"] > 0) * 100 / len(best_15)
            
            if mean_15 > 0.1 and wr_15 > 40:
                out.append("  CLASSIFICATION: GREEN — Rescue survives full-horizon validation")
                out.append(f"  1.5R stop: Mean R = {mean_15:+.4f}, WR = {wr_15:.1f}%")
            elif mean_15 > 0:
                out.append("  CLASSIFICATION: AMBER — Promising but marginal")
                out.append(f"  1.5R stop: Mean R = {mean_15:+.4f}, WR = {wr_15:.1f}%")
            else:
                out.append("  CLASSIFICATION: RED — Rescue does not survive full horizon")
                out.append(f"  1.5R stop: Mean R = {mean_15:+.4f}, WR = {wr_15:.1f}%")
        out.append("")
    else:
        out.append("  CLASSIFICATION: AMBER — Cannot validate without MT5 historical data")
        out.append("  Bar-1 evidence suggests rescue is viable but 60-bar confirmation pending.")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline").mkdir(parents=True, exist_ok=True)
    Path("reports/research/baseline/three_candle_full_horizon_rescue_experiment.md").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
