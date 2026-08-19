"""
TBC/TWS INVERSION ROBUSTNESS VALIDATION

Comprehensive robustness analysis of the inverted-direction hypothesis:
- TBC (THREE_BLACK_CROWS) → BUY instead of SELL
- TWS (THREE_WHITE_SOLDIERS) → SELL instead of BUY

Tests: time stability, symbol concentration, regime, session, score,
outlier influence, out-of-sample split, discovery bias assessment.

Uses the same canonical 60-bar shadow simulation methodology.
DOES NOT modify V10.
"""
import sys
import json
import statistics
import random
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, ".")

MAX_BARS = 60

def simulate_trade(*, direction, entry_price, stop_loss, take_profit, candles):
    """Canonical shadow simulation (same as ShadowTradeEngine.evaluate_bar)."""
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return {"r_multiple": 0, "exit_reason": "zero_risk", "bars_held": 0, "mfe_r": 0, "mae_r": 0}

    max_fav = entry_price
    max_adv = entry_price
    bars_held = 0
    exit_price = None
    exit_reason = ""

    for candle in candles[:MAX_BARS]:
        bars_held += 1
        bh, bl, bc = candle["high"], candle["low"], candle["close"]

        if direction == "BUY":
            max_fav = max(max_fav, bh)
            max_adv = min(max_adv, bl)
            if bl <= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"
                break
            elif bh >= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"
                break
        else:
            max_fav = min(max_fav, bl)
            max_adv = max(max_adv, bh)
            if bh >= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"
                break
            elif bl <= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"
                break

    if exit_price is None:
        exit_price = candles[min(MAX_BARS-1, len(candles)-1)]["close"] if candles else entry_price
        exit_reason = "max_bars_timeout"
        bars_held = min(MAX_BARS, len(candles))

    pnl = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    r_multiple = round(pnl / risk, 4)
    mfe = max(0, (max_fav - entry_price) if direction == "BUY" else (entry_price - max_fav)) / risk
    mae = max(0, (entry_price - max_adv) if direction == "BUY" else (max_adv - entry_price)) / risk

    return {"r_multiple": r_multiple, "exit_reason": exit_reason, "bars_held": bars_held,
            "mfe_r": round(mfe, 4), "mae_r": round(mae, 4)}


def load_candle_data(symbol, start_time):
    import MetaTrader5 as mt5
    from datetime import datetime, timezone
    dt_start = datetime.fromtimestamp(start_time + 1, tz=timezone.utc)
    dt_end = datetime.fromtimestamp(start_time + 20000, tz=timezone.utc)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_start, dt_end)
    if rates is None or len(rates) == 0:
        return []
    return [{"time": int(rates[i][0]), "open": float(rates[i][1]), "high": float(rates[i][2]),
             "low": float(rates[i][3]), "close": float(rates[i][4])} for i in range(min(65, len(rates)))]


def bootstrap_ci(values, n=2000, ci=0.90):
    if len(values) < 3:
        return None, None
    means = sorted([statistics.mean(random.choices(values, k=len(values))) for _ in range(n)])
    return means[int((1-ci)/2*n)], means[int((1+ci)/2*n)]


def get_session(ts):
    h = _dt.fromtimestamp(ts, tz=_tz.utc).hour
    if 7 <= h < 12: return "LONDON"
    elif 12 <= h < 17: return "NY"
    elif 0 <= h < 7: return "ASIA"
    return "OFF_SESSION"


def main():
    random.seed(42)
    out = []
    out.append("=" * 80)
    out.append("TBC/TWS INVERSION ROBUSTNESS VALIDATION")
    out.append("=" * 80)
    out.append("")

    import MetaTrader5 as mt5
    mt5.initialize()

    # Load shadow trades
    raw = []
    base = Path("logs/shadow_trades")
    for f in base.rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try: raw.append(json.loads(line))
            except: pass

    def extract(rec):
        if "identity" in rec:
            i, s, o = rec["identity"], rec["decision_snapshot"], rec["simulated_outcome"]
            return {"symbol": i.get("symbol",""), "correlation_id": i.get("correlation_id",""),
                    "direction": s.get("direction",""), "entry_price": s.get("entry_intent_price",0),
                    "stop_loss": s.get("stop_loss_intent",0), "take_profit": s.get("take_profit_intent",0),
                    "entry_time": s.get("timestamp_decision_utc",0), "pattern": s.get("pattern",""),
                    "score": s.get("score",0), "regime": s.get("regime","") or "",
                    "h4_regime": s.get("h4_regime","") or "", "original_r": o.get("pnl_r_multiple",0)}
        return {"symbol": rec.get("symbol",""), "correlation_id": rec.get("correlation_id",""),
                "direction": rec.get("direction",""), "entry_price": rec.get("entry_price",0),
                "stop_loss": rec.get("stop_loss",0), "take_profit": rec.get("take_profit",0),
                "entry_time": rec.get("entry_time",0), "pattern": rec.get("pattern",""),
                "score": rec.get("score",0), "regime": "", "h4_regime": "",
                "original_r": rec.get("r_multiple",0) or 0}

    all_params = [extract(r) for r in raw]
    tbc = [p for p in all_params if p["pattern"]=="THREE_BLACK_CROWS" and p["correlation_id"] and p["entry_price"]]
    tws = [p for p in all_params if p["pattern"]=="THREE_WHITE_SOLDIERS" and p["correlation_id"] and p["entry_price"]]

    out.append(f"TBC candidates: {len(tbc)}, TWS candidates: {len(tws)}")
    out.append("")

    # Run simulations
    def run_sims(params_list, inv_direction, label):
        results = []
        for p in params_list:
            risk = abs(p["entry_price"] - p["stop_loss"])
            if risk <= 0: continue
            candles = load_candle_data(p["symbol"], p["entry_time"])
            if len(candles) < 5: continue

            # Inverted: flip direction, use same entry, compute new SL/TP at 1R stop, 3R TP
            if inv_direction == "BUY":
                new_sl = p["entry_price"] - risk
                new_tp = p["entry_price"] + risk * 3.0
            else:
                new_sl = p["entry_price"] + risk
                new_tp = p["entry_price"] - risk * 3.0

            # ALSO run normal direction for comparison
            norm_res = simulate_trade(direction=p["direction"], entry_price=p["entry_price"],
                                      stop_loss=p["stop_loss"], take_profit=p["take_profit"], candles=candles)
            inv_res = simulate_trade(direction=inv_direction, entry_price=p["entry_price"],
                                     stop_loss=new_sl, take_profit=new_tp, candles=candles)

            results.append({**p, "normal_r": norm_res["r_multiple"], "inverted_r": inv_res["r_multiple"],
                           "inv_exit": inv_res["exit_reason"], "inv_bars": inv_res["bars_held"],
                           "inv_mfe": inv_res["mfe_r"], "inv_mae": inv_res["mae_r"],
                           "session": get_session(p["entry_time"])})
        return results

    out.append("Simulating TBC → BUY...")
    tbc_results = run_sims(tbc, "BUY", "TBC→BUY")
    out.append(f"  Completed: {len(tbc_results)} trades simulated")

    out.append("Simulating TWS → SELL...")
    tws_results = run_sims(tws, "SELL", "TWS→SELL")
    out.append(f"  Completed: {len(tws_results)} trades simulated")
    out.append("")

    mt5.shutdown()

    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER: Report metrics for a result set
    # ═══════════════════════════════════════════════════════════════════════════
    def report_metrics(results, field="inverted_r", label=""):
        vals = [r[field] for r in results]
        if not vals: return f"    N=0"
        lo, hi = bootstrap_ci(vals)
        ci_str = f"[{lo:+.3f}, {hi:+.3f}]" if lo is not None else "N/A"
        wr = sum(1 for v in vals if v > 0)*100/len(vals)
        return (f"    N={len(vals)}, Mean={statistics.mean(vals):+.4f}, Median={statistics.median(vals):+.4f}, "
                f"WR={wr:.1f}%, Total={sum(vals):+.1f}R, 90% CI={ci_str}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: AGGREGATE — Current vs Inverted
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("1. CURRENT vs INVERTED — AGGREGATE")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC", tbc_results), ("TWS", tws_results)]:
        out.append(f"  {name} NORMAL (current):"); out.append(report_metrics(res, "normal_r"))
        out.append(f"  {name} INVERTED:"); out.append(report_metrics(res, "inverted_r"))
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: TIME STABILITY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("2. TIME STABILITY (chronological thirds)")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        sorted_res = sorted(res, key=lambda r: r["entry_time"])
        n = len(sorted_res)
        thirds = [sorted_res[:n//3], sorted_res[n//3:2*n//3], sorted_res[2*n//3:]]
        labels = ["Early", "Middle", "Late"]
        out.append(f"  {name}:")
        for lbl, third in zip(labels, thirds):
            if third:
                t0 = _dt.fromtimestamp(third[0]["entry_time"], tz=_tz.utc).strftime("%m-%d")
                t1 = _dt.fromtimestamp(third[-1]["entry_time"], tz=_tz.utc).strftime("%m-%d")
                out.append(f"    {lbl} ({t0}→{t1}):")
                out.append(f"  {report_metrics(third)}")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: SYMBOL STABILITY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("3. SYMBOL STABILITY")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        out.append(f"  {name}:")
        syms = sorted(set(r["symbol"] for r in res))
        out.append(f"    {'Symbol':<10} {'N':<5} {'Mean R':<9} {'WR%':<7} {'Total R'}")
        out.append(f"    {'─'*10} {'─'*5} {'─'*9} {'─'*7} {'─'*8}")
        for sym in syms:
            s_res = [r for r in res if r["symbol"] == sym]
            vals = [r["inverted_r"] for r in s_res]
            if vals:
                out.append(f"    {sym:<10} {len(vals):<5} {statistics.mean(vals):+.4f}  "
                           f"{sum(1 for v in vals if v > 0)*100/len(vals):<7.1f} {sum(vals):+.1f}")
        # Excluding best symbol
        best_sym = max(syms, key=lambda s: sum(r["inverted_r"] for r in res if r["symbol"]==s))
        excl = [r for r in res if r["symbol"] != best_sym]
        out.append(f"    Excluding {best_sym}:")
        out.append(f"  {report_metrics(excl)}")
        # Excluding USDJPY if present
        no_jpy = [r for r in res if r["symbol"] != "USDJPY"]
        if len(no_jpy) < len(res):
            out.append(f"    Excluding USDJPY:")
            out.append(f"  {report_metrics(no_jpy)}")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: SESSION STABILITY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("4. SESSION STABILITY")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        out.append(f"  {name}:")
        for sess in ["ASIA", "LONDON", "NY", "OFF_SESSION"]:
            s_res = [r for r in res if r["session"] == sess]
            if s_res:
                vals = [r["inverted_r"] for r in s_res]
                out.append(f"    {sess}: N={len(vals)}, Mean={statistics.mean(vals):+.4f}, "
                           f"WR={sum(1 for v in vals if v > 0)*100/len(vals):.1f}%")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: SCORE CONDITIONING
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("5. SCORE CONDITIONING")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        out.append(f"  {name}:")
        scores = sorted([r["score"] for r in res if r["score"] > 0])
        if scores:
            med_score = statistics.median(scores)
            low_score = [r for r in res if r["score"] <= med_score and r["score"] > 0]
            high_score = [r for r in res if r["score"] > med_score]
            out.append(f"    Low score (≤{med_score:.3f}):")
            out.append(f"  {report_metrics(low_score)}")
            out.append(f"    High score (>{med_score:.3f}):")
            out.append(f"  {report_metrics(high_score)}")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6: OUTLIER / INFLUENCE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("6. OUTLIER / INFLUENCE ANALYSIS")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        vals = sorted([r["inverted_r"] for r in res], reverse=True)
        n = len(vals)
        out.append(f"  {name} (N={n}):")
        out.append(f"    Mean: {statistics.mean(vals):+.4f}, Median: {statistics.median(vals):+.4f}")
        out.append(f"    Top 5 winners: {[f'{v:+.2f}' for v in vals[:5]]}")
        out.append(f"    Top 5 contribution: {sum(vals[:5]):+.2f}R ({sum(vals[:5])*100/sum(vals):.0f}% of total)" if sum(vals) != 0 else "")
        out.append(f"    Bottom 5: {[f'{v:+.2f}' for v in vals[-5:]]}")
        # Remove top N and check
        for rm in [1, 5, 10, 20]:
            trimmed = vals[rm:]
            if trimmed:
                out.append(f"    Remove top {rm}: N={len(trimmed)}, Mean={statistics.mean(trimmed):+.4f}, "
                           f"Still positive: {'YES' if statistics.mean(trimmed) > 0 else 'NO'}")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 7: OUT-OF-SAMPLE / CHRONOLOGICAL SPLIT
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("7. OUT-OF-SAMPLE VALIDATION (first 60% discovery / last 40% validation)")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        sorted_res = sorted(res, key=lambda r: r["entry_time"])
        n = len(sorted_res)
        split = int(n * 0.6)
        train = sorted_res[:split]
        test = sorted_res[split:]
        out.append(f"  {name}:")
        out.append(f"    DISCOVERY (first 60%, N={len(train)}):")
        out.append(f"  {report_metrics(train)}")
        out.append(f"    VALIDATION (last 40%, N={len(test)}):")
        out.append(f"  {report_metrics(test)}")
        # Is validation positive?
        test_vals = [r["inverted_r"] for r in test]
        if test_vals:
            test_mean = statistics.mean(test_vals)
            lo, hi = bootstrap_ci(test_vals)
            if lo is not None and lo > 0:
                out.append(f"    → OUT-OF-SAMPLE VALIDATED (CI above zero)")
            elif test_mean > 0:
                out.append(f"    → PROMISING (mean positive, CI includes zero)")
            else:
                out.append(f"    → NOT VALIDATED (mean negative in validation)")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 8: PERMUTATION TEST
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("8. PERMUTATION TEST (direction label shuffle)")
    out.append("━" * 80)
    out.append("")
    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        inv_vals = [r["inverted_r"] for r in res]
        norm_vals = [r["normal_r"] for r in res]
        observed_delta = statistics.mean(inv_vals) - statistics.mean(norm_vals)
        combined = inv_vals + norm_vals
        n_inv = len(inv_vals)
        n_perms = 5000
        count = 0
        for _ in range(n_perms):
            random.shuffle(combined)
            perm_delta = statistics.mean(combined[:n_inv]) - statistics.mean(combined[n_inv:])
            if perm_delta >= observed_delta:
                count += 1
        p_val = count / n_perms
        out.append(f"  {name}: Observed Δ(inv-norm)={observed_delta:+.4f}, p={p_val:.4f}")
        if p_val < 0.01:
            out.append(f"    → HIGHLY SIGNIFICANT (p<0.01)")
        elif p_val < 0.05:
            out.append(f"    → SIGNIFICANT (p<0.05)")
        else:
            out.append(f"    → NOT SIGNIFICANT")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 9: DISCOVERY BIAS ASSESSMENT
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("9. DISCOVERY BIAS ASSESSMENT")
    out.append("━" * 80)
    out.append("")
    out.append("  Path to discovery:")
    out.append("    1. Observed TBC/TWS had -1R outcomes (all hit SL)")
    out.append("    2. Investigated mechanism → found SL hit on bar 1")
    out.append("    3. Tested wider stops (bar-1 only) → appeared to rescue")
    out.append("    4. Full 60-bar test → wider stops did NOT rescue")
    out.append("    5. Tested inversion → found positive R")
    out.append("    6. This validation tests whether that inversion is robust")
    out.append("")
    out.append("  Degrees of freedom explored before finding inversion:")
    out.append("    - 6 stop widths × 2 patterns × 2 directions = 24 variants")
    out.append("    - Of which only 2 (inverted at 1R) showed strong positive")
    out.append("")
    out.append("  Bonferroni-corrected threshold (24 tests, α=0.05):")
    out.append("    Required p < 0.002 for family-wise significance")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 10: FINAL CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("10. FINAL CLASSIFICATION")
    out.append("=" * 80)
    out.append("")

    for name, res in [("TBC→BUY", tbc_results), ("TWS→SELL", tws_results)]:
        inv_vals = [r["inverted_r"] for r in res]
        if not inv_vals:
            out.append(f"  {name}: NO DATA")
            continue
        mean_r = statistics.mean(inv_vals)
        lo, hi = bootstrap_ci(inv_vals)

        # Time stability check
        sorted_res = sorted(res, key=lambda r: r["entry_time"])
        n = len(sorted_res)
        thirds = [sorted_res[:n//3], sorted_res[n//3:2*n//3], sorted_res[2*n//3:]]
        positive_thirds = sum(1 for t in thirds if statistics.mean([r["inverted_r"] for r in t]) > 0)

        # Symbol concentration
        syms = set(r["symbol"] for r in res)
        sym_means = {s: statistics.mean([r["inverted_r"] for r in res if r["symbol"]==s])
                     for s in syms if sum(1 for r in res if r["symbol"]==s) >= 5}
        positive_syms = sum(1 for m in sym_means.values() if m > 0)

        # OOS check
        test = sorted_res[int(n*0.6):]
        test_mean = statistics.mean([r["inverted_r"] for r in test]) if test else 0

        out.append(f"  {name}:")
        out.append(f"    Aggregate: Mean={mean_r:+.4f}, CI=[{lo:+.4f}, {hi:+.4f}]" if lo else f"    Mean={mean_r:+.4f}")
        out.append(f"    Time stable: {positive_thirds}/3 periods positive")
        out.append(f"    Symbol distributed: {positive_syms}/{len(sym_means)} symbols positive")
        out.append(f"    OOS validation: {'positive' if test_mean > 0 else 'negative'} ({test_mean:+.4f})")
        out.append("")

        # Classification
        if lo is not None and lo > 0 and positive_thirds >= 2 and positive_syms >= 3 and test_mean > 0:
            out.append(f"    CLASSIFICATION: 🟢 GREEN — ROBUST CANDIDATE")
        elif mean_r > 0 and (positive_thirds >= 2 or test_mean > 0):
            out.append(f"    CLASSIFICATION: 🟠 AMBER — PROMISING BUT UNCONFIRMED")
        else:
            out.append(f"    CLASSIFICATION: 🔴 RED — NOT ROBUST")
        out.append("")

    # Most important question
    out.append("━" * 80)
    out.append("MOST IMPORTANT QUESTION")
    out.append("━" * 80)
    out.append("")
    out.append("  'Does TBC/TWS genuinely contain reversal information that V10 is")
    out.append("  currently interpreting in the wrong direction?'")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/tbc_tws_inversion_robustness_validation.md").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
