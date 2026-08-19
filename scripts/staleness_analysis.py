"""
STALENESS FREQUENCY ANALYSIS

For every execution attempt (322 total), determine:
- Is the geometry "stale" at decision time? (TP <= ctx_ask for BUY, TP >= ctx_bid for SELL)
- Segment by: strategy/pattern, symbol, session, score, risk_distance (proxy for volatility)

Answers: how often does V10 produce a structurally valid opportunity that becomes
stale before execution, and is it disproportionate?

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, ".")


def load_execution_results():
    records = []
    base = Path("logs/execution_results")
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


def load_execution_contexts():
    records = {}
    base = Path("logs/execution_context")
    for sym_dir in base.iterdir():
        if not sym_dir.is_dir():
            continue
        for f in sym_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    cid = rec.get("correlation_id", "")
                    if cid:
                        records[cid] = rec
                except Exception:
                    pass
    return records


def get_session(ts_unix):
    if not ts_unix:
        return "UNKNOWN"
    hour = _dt.fromtimestamp(ts_unix, tz=_tz.utc).hour
    if 7 <= hour < 12:
        return "LONDON"
    elif 12 <= hour < 17:
        return "NY"
    elif 0 <= hour < 7:
        return "ASIA"
    else:
        return "OFF_SESSION"


def main():
    out = []
    out.append("=" * 80)
    out.append("STALENESS FREQUENCY ANALYSIS")
    out.append("=" * 80)
    out.append("")

    exec_results = load_execution_results()
    ctx_map = load_execution_contexts()

    out.append(f"Total execution attempts: {len(exec_results)}")
    out.append("")

    # Classify every execution attempt
    records = []
    for r in exec_results:
        cid = r.get("correlation_id", "")
        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)
        pattern = r.get("pattern", "")
        volume = r.get("volume", 0)
        comment = r.get("comment", "")
        result_ok = r.get("result_ok", False)
        ts = r.get("timestamp_unix", 0)

        ctx = ctx_map.get(cid, {})
        market = ctx.get("market_access", {}) or {}
        ctx_bid = market.get("bid", 0)
        ctx_ask = market.get("ask", 0)
        infra = ctx.get("infrastructure", {}) or {}
        latency_ms = infra.get("latency_ms", 0)
        spread = market.get("spread", 0)

        # Determine staleness
        is_stale = False
        stale_reason = ""
        if side == "BUY" and ctx_ask and tp:
            if tp <= ctx_ask:
                is_stale = True
                stale_reason = "TP_BELOW_ASK"
            elif sl and sl >= ctx_ask:
                is_stale = True
                stale_reason = "SL_ABOVE_ASK"
        elif side == "SELL" and ctx_bid and tp:
            if tp >= ctx_bid:
                is_stale = True
                stale_reason = "TP_ABOVE_BID"
            elif sl and sl <= ctx_bid:
                is_stale = True
                stale_reason = "SL_BELOW_BID"

        # Price displacement: how far has price moved from structural entry?
        displacement = 0
        if side == "BUY" and ctx_ask and entry_ref:
            displacement = ctx_ask - entry_ref  # positive = price above entry zone
        elif side == "SELL" and ctx_bid and entry_ref:
            displacement = entry_ref - ctx_bid  # positive = price below entry zone

        # Risk distance
        risk_distance = abs(entry_ref - sl) if entry_ref and sl else 0

        # Displacement in R terms
        displacement_r = displacement / risk_distance if risk_distance > 0 else 0

        session = get_session(ts)

        records.append({
            "symbol": symbol,
            "side": side,
            "pattern": pattern,
            "session": session,
            "is_stale": is_stale,
            "stale_reason": stale_reason,
            "result_ok": result_ok,
            "comment": comment,
            "entry_ref": entry_ref,
            "sl": sl,
            "tp": tp,
            "ctx_ask": ctx_ask,
            "ctx_bid": ctx_bid,
            "risk_distance": risk_distance,
            "displacement": displacement,
            "displacement_r": displacement_r,
            "latency_ms": latency_ms,
            "spread": spread,
            "ts": ts,
        })

    # ═══════════════════════════════════════════════════════════════════════════
    # OVERALL STALENESS RATE
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("OVERALL STALENESS RATE")
    out.append("━" * 80)
    out.append("")

    total = len(records)
    stale = [r for r in records if r["is_stale"]]
    fresh = [r for r in records if not r["is_stale"]]

    out.append(f"  Total execution attempts: {total}")
    out.append(f"  STALE at decision time: {len(stale)} ({len(stale)*100//total}%)")
    out.append(f"  FRESH at decision time: {len(fresh)} ({len(fresh)*100//total}%)")
    out.append("")

    stale_reasons = Counter(r["stale_reason"] for r in stale)
    out.append(f"  Staleness reasons:")
    for reason, count in stale_reasons.most_common():
        out.append(f"    {reason}: {count}")
    out.append("")

    # How many stale ended up filled anyway?
    stale_filled = sum(1 for r in stale if r["result_ok"])
    stale_rejected = sum(1 for r in stale if not r["result_ok"])
    out.append(f"  Stale → filled: {stale_filled}")
    out.append(f"  Stale → rejected: {stale_rejected}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # BY SYMBOL
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STALENESS BY SYMBOL")
    out.append("━" * 80)
    out.append("")

    symbols = sorted(set(r["symbol"] for r in records))
    out.append(f"  {'Symbol':<10} {'Total':<7} {'Stale':<7} {'Fresh':<7} {'Stale%':<8} {'MeanDisp(R)'}")
    out.append(f"  {'─'*10} {'─'*7} {'─'*7} {'─'*7} {'─'*8} {'─'*12}")
    for sym in symbols:
        sym_all = [r for r in records if r["symbol"] == sym]
        sym_stale = [r for r in sym_all if r["is_stale"]]
        sym_fresh = [r for r in sym_all if not r["is_stale"]]
        stale_pct = len(sym_stale) * 100 // len(sym_all) if sym_all else 0
        disps = [r["displacement_r"] for r in sym_all if r["displacement_r"] != 0]
        mean_disp = statistics.mean(disps) if disps else 0
        out.append(f"  {sym:<10} {len(sym_all):<7} {len(sym_stale):<7} {len(sym_fresh):<7} "
                   f"{stale_pct}%{'':<5} {mean_disp:+.2f}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # BY PATTERN/STRATEGY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STALENESS BY PATTERN/STRATEGY")
    out.append("━" * 80)
    out.append("")

    patterns = sorted(set(r["pattern"] for r in records if r["pattern"]))
    out.append(f"  {'Pattern':<25} {'Total':<7} {'Stale':<7} {'Fresh':<7} {'Stale%'}")
    out.append(f"  {'─'*25} {'─'*7} {'─'*7} {'─'*7} {'─'*7}")
    for pat in patterns:
        pat_all = [r for r in records if r["pattern"] == pat]
        pat_stale = [r for r in pat_all if r["is_stale"]]
        stale_pct = len(pat_stale) * 100 // len(pat_all) if pat_all else 0
        out.append(f"  {pat:<25} {len(pat_all):<7} {len(pat_stale):<7} {len(pat_all)-len(pat_stale):<7} {stale_pct}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # BY SESSION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STALENESS BY SESSION")
    out.append("━" * 80)
    out.append("")

    sessions = ["ASIA", "LONDON", "NY", "OFF_SESSION", "UNKNOWN"]
    out.append(f"  {'Session':<13} {'Total':<7} {'Stale':<7} {'Fresh':<7} {'Stale%'}")
    out.append(f"  {'─'*13} {'─'*7} {'─'*7} {'─'*7} {'─'*7}")
    for sess in sessions:
        sess_all = [r for r in records if r["session"] == sess]
        if not sess_all:
            continue
        sess_stale = [r for r in sess_all if r["is_stale"]]
        stale_pct = len(sess_stale) * 100 // len(sess_all) if sess_all else 0
        out.append(f"  {sess:<13} {len(sess_all):<7} {len(sess_stale):<7} {len(sess_all)-len(sess_stale):<7} {stale_pct}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # BY SIDE
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STALENESS BY SIDE")
    out.append("━" * 80)
    out.append("")

    for side in ["BUY", "SELL"]:
        side_all = [r for r in records if r["side"] == side]
        side_stale = [r for r in side_all if r["is_stale"]]
        stale_pct = len(side_stale) * 100 // len(side_all) if side_all else 0
        out.append(f"  {side}: {len(side_stale)}/{len(side_all)} stale ({stale_pct}%)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # DISPLACEMENT ANALYSIS — HOW FAR HAS PRICE MOVED?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("PRICE DISPLACEMENT (structural entry → current price)")
    out.append("━" * 80)
    out.append("")

    stale_disps = [r["displacement_r"] for r in stale if r["displacement_r"] > 0]
    fresh_disps = [r["displacement_r"] for r in fresh if r["displacement_r"] > 0]

    if stale_disps:
        out.append(f"  STALE orders — displacement in R-multiples:")
        out.append(f"    N={len(stale_disps)}, Mean={statistics.mean(stale_disps):+.2f}R, "
                   f"Median={statistics.median(stale_disps):+.2f}R, "
                   f"Min={min(stale_disps):+.2f}R, Max={max(stale_disps):+.2f}R")
    if fresh_disps:
        out.append(f"  FRESH orders — displacement in R-multiples:")
        out.append(f"    N={len(fresh_disps)}, Mean={statistics.mean(fresh_disps):+.2f}R, "
                   f"Median={statistics.median(fresh_disps):+.2f}R, "
                   f"Min={min(fresh_disps):+.2f}R, Max={max(fresh_disps):+.2f}R")
    out.append("")

    # Distribution: what displacement threshold separates stale from fresh?
    all_disps_sorted = sorted([(r["displacement_r"], r["is_stale"]) for r in records if r["displacement_r"] > 0])
    if all_disps_sorted:
        out.append(f"  Displacement distribution (price moved in R-multiples from structural zone):")
        buckets = [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 10.0), (10.0, 100.0)]
        out.append(f"    {'Displacement':<15} {'Total':<7} {'Stale':<7} {'Stale%'}")
        out.append(f"    {'─'*15} {'─'*7} {'─'*7} {'─'*7}")
        for lo, hi in buckets:
            bucket_all = [(d, s) for d, s in all_disps_sorted if lo <= d < hi]
            bucket_stale = sum(1 for _, s in bucket_all if s)
            if bucket_all:
                out.append(f"    {lo:.1f}–{hi:.1f}R{'':<8} {len(bucket_all):<7} {bucket_stale:<7} "
                           f"{bucket_stale*100//len(bucket_all)}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # RISK DISTANCE (proxy for volatility state)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STALENESS BY RISK DISTANCE (tight vs wide stops)")
    out.append("━" * 80)
    out.append("")

    # Only use FX pairs for comparable risk_distance
    fx_records = [r for r in records if r["symbol"] in 
                  ("AUDUSD", "EURUSD", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")]
    fx_with_rd = [(r["risk_distance"], r["is_stale"]) for r in fx_records if r["risk_distance"] > 0]

    if fx_with_rd:
        fx_sorted = sorted(fx_with_rd, key=lambda x: x[0])
        n = len(fx_sorted)
        quartiles = [fx_sorted[:n//4], fx_sorted[n//4:n//2], fx_sorted[n//2:3*n//4], fx_sorted[3*n//4:]]
        labels = ["Q1 (tightest)", "Q2", "Q3", "Q4 (widest)"]
        out.append(f"  FX pairs only (N={n}):")
        out.append(f"    {'Quartile':<15} {'N':<5} {'Stale':<7} {'Stale%':<8} {'MeanRD'}")
        out.append(f"    {'─'*15} {'─'*5} {'─'*7} {'─'*8} {'─'*10}")
        for label, q in zip(labels, quartiles):
            q_stale = sum(1 for _, s in q if s)
            q_rds = [rd for rd, _ in q]
            out.append(f"    {label:<15} {len(q):<5} {q_stale:<7} "
                       f"{q_stale*100//len(q) if q else 0}%{'':<5} "
                       f"{statistics.mean(q_rds):.5f}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # INTERACTION: SYMBOL × SESSION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SYMBOL × SESSION INTERACTION")
    out.append("━" * 80)
    out.append("")

    # Find the highest-staleness combinations
    combos = defaultdict(lambda: {"total": 0, "stale": 0})
    for r in records:
        key = f"{r['symbol']}_{r['session']}"
        combos[key]["total"] += 1
        if r["is_stale"]:
            combos[key]["stale"] += 1

    out.append(f"  {'Symbol_Session':<20} {'Total':<7} {'Stale':<7} {'Stale%'}")
    out.append(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*7}")
    for key, vals in sorted(combos.items(), key=lambda x: -x[1]["stale"]):
        if vals["stale"] > 0:
            pct = vals["stale"] * 100 // vals["total"]
            out.append(f"  {key:<20} {vals['total']:<7} {vals['stale']:<7} {pct}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CONCLUSIONS")
    out.append("=" * 80)
    out.append("")

    stale_rate = len(stale) * 100 / total if total else 0
    out.append(f"1. OVERALL STALENESS RATE: {len(stale)}/{total} = {stale_rate:.1f}%")
    out.append("")

    # Find most affected
    if patterns:
        worst_pattern = max(patterns, key=lambda p: sum(1 for r in records if r["pattern"]==p and r["is_stale"]) / max(sum(1 for r in records if r["pattern"]==p), 1))
        wp_all = sum(1 for r in records if r["pattern"] == worst_pattern)
        wp_stale = sum(1 for r in records if r["pattern"] == worst_pattern and r["is_stale"])
        out.append(f"2. MOST AFFECTED PATTERN: {worst_pattern} ({wp_stale}/{wp_all} = {wp_stale*100//wp_all}% stale)")
    out.append("")

    if symbols:
        worst_symbol = max(symbols, key=lambda s: sum(1 for r in records if r["symbol"]==s and r["is_stale"]) / max(sum(1 for r in records if r["symbol"]==s), 1))
        ws_all = sum(1 for r in records if r["symbol"] == worst_symbol)
        ws_stale = sum(1 for r in records if r["symbol"] == worst_symbol and r["is_stale"])
        out.append(f"3. MOST AFFECTED SYMBOL: {worst_symbol} ({ws_stale}/{ws_all} = {ws_stale*100//ws_all}% stale)")
    out.append("")

    worst_session = max(sessions, key=lambda s: sum(1 for r in records if r["session"]==s and r["is_stale"]) / max(sum(1 for r in records if r["session"]==s), 1))
    wss_all = sum(1 for r in records if r["session"] == worst_session)
    wss_stale = sum(1 for r in records if r["session"] == worst_session and r["is_stale"])
    if wss_all:
        out.append(f"4. MOST AFFECTED SESSION: {worst_session} ({wss_stale}/{wss_all} = {wss_stale*100//wss_all}% stale)")
    out.append("")

    buy_all = sum(1 for r in records if r["side"] == "BUY")
    buy_stale = sum(1 for r in records if r["side"] == "BUY" and r["is_stale"])
    sell_all = sum(1 for r in records if r["side"] == "SELL")
    sell_stale = sum(1 for r in records if r["side"] == "SELL" and r["is_stale"])
    out.append(f"5. DIRECTIONAL BIAS: BUY={buy_stale}/{buy_all} ({buy_stale*100//buy_all if buy_all else 0}%), "
               f"SELL={sell_stale}/{sell_all} ({sell_stale*100//sell_all if sell_all else 0}%)")
    out.append("")

    out.append("6. MECHANISM:")
    out.append("   V10 entry engine produces geometry from structural zones (demand/supply OBs,")
    out.append("   swing midpoints, BOS levels). These zones may be 1-50R below current price.")
    out.append("   When price has moved far from the zone, TP is already below current ask (BUY)")
    out.append("   or above current bid (SELL). The order is inherently unexecutable as MARKET.")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/staleness_frequency_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
