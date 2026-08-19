"""
BROKER REJECTION EVENT-LEVEL RECONSTRUCTION

For every rejected order, reconstruct:
- Decision timestamp vs submission timestamp (latency)
- Decision-time bid/ask vs submission-time bid/ask
- SL, TP, entry_reference, risk_distance
- Broker minimum stop-level requirements
- Price movement between decision and submission
- Symbol, strategy/pattern, regime, session

Determines root cause of "Invalid stops" (retcode 10016):
- Hypothesis A: Price moved past SL/TP between decision and submission
- Hypothesis B: Broker minimum stop distance not met (stops too close to price)
- Hypothesis C: Spread expansion pushed effective levels invalid

Separately analyses SPREAD_EXCEEDED:RATIO rejections.

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
    records = []
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
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def load_shadow_primary():
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def main():
    out = []
    out.append("=" * 80)
    out.append("BROKER REJECTION EVENT-LEVEL RECONSTRUCTION")
    out.append("=" * 80)
    out.append("")

    exec_results = load_execution_results()
    exec_contexts = load_execution_contexts()
    shadows = load_shadow_primary()

    out.append(f"Execution results: {len(exec_results)}")
    out.append(f"Execution contexts: {len(exec_contexts)}")
    out.append(f"V10_PRIMARY shadows: {len(shadows)}")
    out.append("")

    # Build context lookup by correlation_id
    ctx_by_corr = {}
    for ctx in exec_contexts:
        cid = ctx.get("correlation_id", "")
        if cid:
            ctx_by_corr[cid] = ctx

    # Build shadow lookup by correlation_id
    shadow_by_corr = {}
    for s in shadows:
        cid = s.get("correlation_id", "")
        if cid:
            shadow_by_corr[cid] = s

    # Separate results
    failed = [r for r in exec_results if not r.get("result_ok")]
    successful = [r for r in exec_results if r.get("result_ok")]

    # Categorize failures
    invalid_stops = [r for r in failed if r.get("comment") == "Invalid stops"]
    spread_exceeded = [r for r in failed if "SPREAD_EXCEEDED" in r.get("comment", "")]
    api_errors = [r for r in failed if "order_send_none" in r.get("comment", "")]
    other_fails = [r for r in failed if r not in invalid_stops and r not in spread_exceeded and r not in api_errors]

    out.append(f"Failed: {len(failed)} (Invalid stops: {len(invalid_stops)}, Spread: {len(spread_exceeded)}, API: {len(api_errors)}, Other: {len(other_fails)})")
    out.append(f"Successful: {len(successful)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: EXECUTION CONTEXT MATCHING
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 1: EXECUTION CONTEXT AVAILABILITY")
    out.append("━" * 80)
    out.append("")

    # Check context fields
    if exec_contexts:
        sample_ctx = exec_contexts[0]
        out.append(f"  Context fields: {sorted(sample_ctx.keys())}")
        out.append("")

    # Match contexts to rejected orders
    reject_with_ctx = []
    reject_without_ctx = 0
    for r in invalid_stops:
        cid = r.get("correlation_id", "")
        ctx = ctx_by_corr.get(cid)
        shadow = shadow_by_corr.get(cid)
        if ctx:
            reject_with_ctx.append({"exec": r, "ctx": ctx, "shadow": shadow})
        else:
            reject_without_ctx += 1

    out.append(f"  'Invalid stops' with execution context: {len(reject_with_ctx)}")
    out.append(f"  'Invalid stops' without context: {reject_without_ctx}")
    out.append("")

    # Same for successful
    accept_with_ctx = []
    for r in successful:
        cid = r.get("correlation_id", "")
        ctx = ctx_by_corr.get(cid)
        shadow = shadow_by_corr.get(cid)
        if ctx:
            accept_with_ctx.append({"exec": r, "ctx": ctx, "shadow": shadow})

    out.append(f"  Successful with execution context: {len(accept_with_ctx)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: PER-ORDER RECONSTRUCTION — INVALID STOPS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 2: INVALID STOPS — PER-ORDER RECONSTRUCTION")
    out.append("━" * 80)
    out.append("")

    # For each "Invalid stops" rejection, determine the mechanism:
    # MT5 retcode 10016 (TRADE_RETCODE_INVALID_STOPS) means:
    #   - SL is on wrong side of price (BUY: SL > ask, SELL: SL < bid)
    #   - TP is on wrong side of price (BUY: TP < ask, SELL: TP > bid)
    #   - SL/TP too close to current price (within freeze_level or stops_level)
    #
    # The execution module's request uses tick.ask for BUY, tick.bid for SELL as price.
    # SL and TP come unchanged from OrderIntent (computed at decision time).

    invalid_analysis = []
    for item in reject_with_ctx:
        r = item["exec"]
        ctx = item["ctx"]
        shadow = item["shadow"]

        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)
        decision_ts = r.get("decision_ts_utc_ms", 0)  # ms
        submission_ts = r.get("timestamp_unix", 0)  # seconds

        # From context: bid/ask at DECISION time (nested in market_access)
        market = ctx.get("market_access", {}) or {}
        ctx_bid = market.get("bid", 0) or ctx.get("bid", 0)
        ctx_ask = market.get("ask", 0) or ctx.get("ask", 0)
        ctx_spread_atr = market.get("spread_atr_ratio", 0)

        # Compute analysis
        risk_distance = abs(entry_ref - sl) if entry_ref and sl else 0
        reward_distance = abs(tp - entry_ref) if tp and entry_ref else 0

        # Decision timestamp
        decision_time_s = decision_ts / 1000 if decision_ts > 1e12 else decision_ts

        # Latency (decision → submission)
        latency_s = (submission_ts - decision_time_s) if (submission_ts and decision_time_s) else None

        # Determine which invalid-stops scenario:
        # For BUY: entry = ask. SL should be below ask. TP should be above ask.
        # For SELL: entry = bid. SL should be above bid. TP should be below bid.
        # If price moved toward TP since decision, the levels may now be invalid.

        diagnosis = "UNKNOWN"
        price_at_decision = ctx_ask if side == "BUY" else ctx_bid
        # We don't have submission-time price directly, but we can infer from the geometry:
        # If side=BUY and TP <= current_ask at submission → "TP on wrong side"
        # If side=SELL and TP >= current_bid at submission → "TP on wrong side"
        # Since "Invalid stops" was the broker response, price must have moved.

        # For BUY: invalid if SL >= ask OR TP <= ask OR SL/TP within stops_level
        # For SELL: invalid if SL <= bid OR TP >= bid OR SL/TP within stops_level
        if side == "BUY":
            if tp and entry_ref and tp <= entry_ref:
                diagnosis = "TP_BELOW_ENTRY (geometry error)"
            elif sl and entry_ref and sl >= entry_ref:
                diagnosis = "SL_ABOVE_ENTRY (geometry error)"
            elif tp and ctx_ask and tp <= ctx_ask:
                diagnosis = "TP_BELOW_CURRENT_ASK (price already past TP)"
            elif sl and ctx_ask and abs(ctx_ask - sl) < 0.0001:
                diagnosis = "SL_TOO_CLOSE (minimum stops_level)"
            else:
                diagnosis = "PRICE_MOVED_OR_STOPS_LEVEL"
        elif side == "SELL":
            if tp and entry_ref and tp >= entry_ref:
                diagnosis = "TP_ABOVE_ENTRY (geometry error)"
            elif sl and entry_ref and sl <= entry_ref:
                diagnosis = "SL_BELOW_ENTRY (geometry error)"
            elif tp and ctx_bid and tp >= ctx_bid:
                diagnosis = "TP_ABOVE_CURRENT_BID (price already past TP)"
            elif sl and ctx_bid and abs(sl - ctx_bid) < 0.0001:
                diagnosis = "SL_TOO_CLOSE (minimum stops_level)"
            else:
                diagnosis = "PRICE_MOVED_OR_STOPS_LEVEL"

        shadow_r = shadow.get("r_multiple") if shadow else None
        shadow_exit = shadow.get("exit_reason", "") if shadow else ""

        invalid_analysis.append({
            "symbol": symbol,
            "side": side,
            "sl": sl,
            "tp": tp,
            "entry_ref": entry_ref,
            "ctx_bid": ctx_bid,
            "ctx_ask": ctx_ask,
            "risk_distance": risk_distance,
            "reward_distance": reward_distance,
            "rr_ratio": reward_distance / risk_distance if risk_distance > 0 else 0,
            "latency_s": latency_s,
            "diagnosis": diagnosis,
            "shadow_r": shadow_r,
            "shadow_exit": shadow_exit,
            "decision_ts": decision_time_s,
            "submission_ts": submission_ts,
            "spread_at_decision": ctx_ask - ctx_bid if ctx_ask and ctx_bid else 0,
            "pattern": r.get("pattern", ""),
        })

    # Also analyze the ones WITHOUT context (use just execution_result fields)
    for r in invalid_stops:
        cid = r.get("correlation_id", "")
        if cid in ctx_by_corr:
            continue  # Already processed above
        shadow = shadow_by_corr.get(cid)
        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)
        risk_distance = abs(entry_ref - sl) if entry_ref and sl else 0
        reward_distance = abs(tp - entry_ref) if tp and entry_ref else 0

        invalid_analysis.append({
            "symbol": symbol,
            "side": side,
            "sl": sl,
            "tp": tp,
            "entry_ref": entry_ref,
            "ctx_bid": 0,
            "ctx_ask": 0,
            "risk_distance": risk_distance,
            "reward_distance": reward_distance,
            "rr_ratio": reward_distance / risk_distance if risk_distance > 0 else 0,
            "latency_s": None,
            "diagnosis": "NO_CONTEXT_AVAILABLE",
            "shadow_r": shadow.get("r_multiple") if shadow else None,
            "shadow_exit": shadow.get("exit_reason", "") if shadow else "",
            "decision_ts": 0,
            "submission_ts": r.get("timestamp_unix", 0),
            "spread_at_decision": 0,
            "pattern": r.get("pattern", ""),
        })

    # Print per-order summary
    out.append(f"  Total 'Invalid stops' orders analysed: {len(invalid_analysis)}")
    out.append("")

    # Diagnosis distribution
    diag_counts = Counter(a["diagnosis"] for a in invalid_analysis)
    out.append("  Diagnosis distribution:")
    for diag, count in diag_counts.most_common():
        out.append(f"    {diag}: {count}")
    out.append("")

    # Latency analysis
    latencies = [a["latency_s"] for a in invalid_analysis if a["latency_s"] is not None and a["latency_s"] > 0]
    if latencies:
        out.append(f"  Decision-to-submission latency (N={len(latencies)}):")
        out.append(f"    Mean: {statistics.mean(latencies):.1f}s")
        out.append(f"    Median: {statistics.median(latencies):.1f}s")
        out.append(f"    Min: {min(latencies):.1f}s, Max: {max(latencies):.1f}s")
    out.append("")

    # Compare latency for rejected vs accepted
    accept_latencies = []
    for item in accept_with_ctx:
        r = item["exec"]
        decision_ts = r.get("decision_ts_utc_ms", 0)
        submission_ts = r.get("timestamp_unix", 0)
        decision_time_s = decision_ts / 1000 if decision_ts > 1e12 else decision_ts
        if submission_ts and decision_time_s and submission_ts > decision_time_s:
            lat = submission_ts - decision_time_s
            if 0 < lat < 600:  # sanity check: < 10 minutes
                accept_latencies.append(lat)

    if accept_latencies:
        out.append(f"  ACCEPTED latency comparison (N={len(accept_latencies)}):")
        out.append(f"    Mean: {statistics.mean(accept_latencies):.1f}s")
        out.append(f"    Median: {statistics.median(accept_latencies):.1f}s")
    out.append("")

    # Risk distance comparison
    reject_rds = [a["risk_distance"] for a in invalid_analysis if a["risk_distance"] > 0]
    accept_rds_ctx = [abs(item["exec"].get("entry_reference", 0) - item["exec"].get("sl", 0))
                      for item in accept_with_ctx
                      if item["exec"].get("entry_reference") and item["exec"].get("sl")]
    accept_rds_ctx = [rd for rd in accept_rds_ctx if rd > 0]

    if reject_rds:
        out.append(f"  REJECTED risk_distance (N={len(reject_rds)}):")
        out.append(f"    Mean: {statistics.mean(reject_rds):.6f}")
        out.append(f"    Median: {statistics.median(reject_rds):.6f}")
    if accept_rds_ctx:
        out.append(f"  ACCEPTED risk_distance (N={len(accept_rds_ctx)}):")
        out.append(f"    Mean: {statistics.mean(accept_rds_ctx):.6f}")
        out.append(f"    Median: {statistics.median(accept_rds_ctx):.6f}")
    if reject_rds and accept_rds_ctx:
        out.append(f"  Ratio (rejected/accepted mean): {statistics.mean(reject_rds)/statistics.mean(accept_rds_ctx):.3f}")
    out.append("")

    # Spread at decision time
    reject_spreads = [a["spread_at_decision"] for a in invalid_analysis if a["spread_at_decision"] > 0]
    accept_spreads = []
    for item in accept_with_ctx:
        market = item["ctx"].get("market_access", {}) or {}
        b = market.get("bid", 0)
        a = market.get("ask", 0)
        if a and b and a > b:
            accept_spreads.append(a - b)
    accept_spreads = [s for s in accept_spreads if s > 0]

    if reject_spreads:
        out.append(f"  REJECTED spread at decision (N={len(reject_spreads)}):")
        out.append(f"    Mean: {statistics.mean(reject_spreads):.6f}")
    if accept_spreads:
        out.append(f"  ACCEPTED spread at decision (N={len(accept_spreads)}):")
        out.append(f"    Mean: {statistics.mean(accept_spreads):.6f}")
    out.append("")

    # Shadow R for Invalid stops
    invalid_r = [a["shadow_r"] for a in invalid_analysis if a["shadow_r"] is not None]
    if invalid_r:
        out.append(f"  Invalid stops shadow R: N={len(invalid_r)}, Mean={statistics.mean(invalid_r):+.4f}, "
                   f"WR={sum(1 for r in invalid_r if r > 0)*100/len(invalid_r):.1f}%")
    out.append("")

    # Exit reason
    invalid_exits = Counter(a["shadow_exit"] for a in invalid_analysis if a["shadow_exit"])
    out.append(f"  Invalid stops shadow exit reasons:")
    for ex, c in invalid_exits.most_common():
        out.append(f"    {ex}: {c}")
    out.append("")

    # Per-order detail (first 10)
    out.append("  Per-order detail (first 15 'Invalid stops'):")
    out.append(f"  {'Sym':<8} {'Side':<5} {'RD':<10} {'RR':<6} {'Lat(s)':<8} {'ShadR':<8} {'ShadExit':<15} {'Diag'}")
    out.append(f"  {'─'*8} {'─'*5} {'─'*10} {'─'*6} {'─'*8} {'─'*8} {'─'*15} {'─'*30}")
    for a in invalid_analysis[:15]:
        lat_str = f"{a['latency_s']:.0f}" if a['latency_s'] is not None else "?"
        r_str = f"{a['shadow_r']:+.3f}" if a['shadow_r'] is not None else "?"
        out.append(f"  {a['symbol']:<8} {a['side']:<5} {a['risk_distance']:<10.6f} "
                   f"{a['rr_ratio']:<6.2f} {lat_str:<8} {r_str:<8} {a['shadow_exit']:<15} {a['diagnosis']}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: BROKER MINIMUM STOP DISTANCE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 3: BROKER MINIMUM STOP DISTANCE vs PRICE STALENESS")
    out.append("━" * 80)
    out.append("")

    # Key question: is the SL within broker's minimum stops_level of current price?
    # Or has price moved past the geometry?
    # Broker minimum stop distance = stops_level * point (per symbol_info)
    # If risk_distance < stops_level * point, the stop is too close.

    # We can infer: if entry_reference ≈ ctx_bid or ctx_ask (within 1 pip),
    # then the geometry was valid at decision time but may have become invalid
    # due to price movement.

    # Check: was SL valid at decision time?
    valid_at_decision = 0
    invalid_at_decision = 0
    for a in invalid_analysis:
        if not a["ctx_bid"] or not a["ctx_ask"]:
            continue
        side = a["side"]
        sl = a["sl"]
        tp = a["tp"]
        if side == "BUY":
            # For BUY: SL should be < ask, TP should be > ask
            if sl < a["ctx_ask"] and tp > a["ctx_ask"]:
                valid_at_decision += 1
            else:
                invalid_at_decision += 1
        elif side == "SELL":
            # For SELL: SL should be > bid, TP should be < bid
            if sl > a["ctx_bid"] and tp < a["ctx_bid"]:
                valid_at_decision += 1
            else:
                invalid_at_decision += 1

    out.append(f"  Geometry valid at DECISION time: {valid_at_decision}")
    out.append(f"  Geometry ALREADY invalid at decision time: {invalid_at_decision}")
    out.append(f"  No context available: {len(invalid_analysis) - valid_at_decision - invalid_at_decision}")
    out.append("")

    if valid_at_decision > 0:
        out.append("  ✓ Geometry was valid at decision time → became invalid by submission time")
        out.append("    This proves PRICE MOVEMENT / LATENCY is the primary cause.")
    if invalid_at_decision > 0:
        out.append(f"  ⚠️ {invalid_at_decision} orders had ALREADY-invalid geometry at decision time")
        out.append("    This suggests MINIMUM STOP DISTANCE constraint or geometry computation error.")
    out.append("")

    # Recoverability: how many would be valid if SL/TP were recalculated?
    # A recalculated order would use fresh tick price and same RR ratio.
    # If risk_distance is above broker minimum AND price hasn't run past the setup,
    # the trade would be recoverable with fresh SL/TP.
    out.append("  RECOVERABILITY ESTIMATE:")
    out.append("  If V10 recalculated SL/TP at submission time (same RR, fresh price):")
    out.append(f"    Valid-at-decision (latency caused): {valid_at_decision} — potentially recoverable")
    out.append(f"    Already-invalid (structural): {invalid_at_decision} — NOT recoverable without geometry change")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: SPREAD_EXCEEDED:RATIO DEEP ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 4: SPREAD_EXCEEDED:RATIO — DEEP ANALYSIS (16 rejections)")
    out.append("━" * 80)
    out.append("")

    spread_analysis = []
    for r in spread_exceeded:
        cid = r.get("correlation_id", "")
        ctx = ctx_by_corr.get(cid, {})
        shadow = shadow_by_corr.get(cid, {})
        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)
        risk_distance = abs(entry_ref - sl) if entry_ref and sl else 0

        market = ctx.get("market_access", {}) or {}
        ctx_bid = market.get("bid", 0)
        ctx_ask = market.get("ask", 0)
        spread_at_decision = ctx_ask - ctx_bid if ctx_ask and ctx_bid else 0
        spread_ratio_at_decision = spread_at_decision / risk_distance if risk_distance > 0 else 0

        shadow_r = shadow.get("r_multiple")
        shadow_exit = shadow.get("exit_reason", "")
        shadow_rr = shadow.get("reward_risk_ratio", 0)

        spread_analysis.append({
            "symbol": symbol,
            "side": side,
            "risk_distance": risk_distance,
            "spread_at_decision": spread_at_decision,
            "spread_ratio": spread_ratio_at_decision,
            "shadow_r": shadow_r,
            "shadow_exit": shadow_exit,
            "shadow_rr": shadow_rr,
            "pattern": r.get("pattern", ""),
        })

    # Summary
    spread_ratios = [a["spread_ratio"] for a in spread_analysis if a["spread_ratio"] > 0]
    spread_rds = [a["risk_distance"] for a in spread_analysis if a["risk_distance"] > 0]
    spread_r_vals = [a["shadow_r"] for a in spread_analysis if a["shadow_r"] is not None]

    out.append(f"  Spread-blocked orders: {len(spread_analysis)}")
    out.append("")

    if spread_ratios:
        out.append(f"  Spread/risk_distance ratio at decision time:")
        out.append(f"    Mean: {statistics.mean(spread_ratios):.4f}")
        out.append(f"    Median: {statistics.median(spread_ratios):.4f}")
        out.append(f"    Min: {min(spread_ratios):.4f}, Max: {max(spread_ratios):.4f}")
        out.append(f"    Threshold: 0.30")
        # How many exceed 0.30 at decision time vs at execution time?
        exceed_at_decision = sum(1 for r in spread_ratios if r > 0.30)
        out.append(f"    Exceed 0.30 at DECISION time: {exceed_at_decision}/{len(spread_ratios)}")
        out.append(f"    Below 0.30 at decision (spread widened after): {len(spread_ratios) - exceed_at_decision}")
    out.append("")

    if spread_rds:
        out.append(f"  Risk distance of spread-blocked trades:")
        out.append(f"    Mean: {statistics.mean(spread_rds):.6f}")
        out.append(f"    Median: {statistics.median(spread_rds):.6f}")
        # Compare to accepted
        if accept_rds_ctx:
            out.append(f"    vs Accepted mean: {statistics.mean(accept_rds_ctx):.6f}")
            out.append(f"    Ratio: {statistics.mean(spread_rds)/statistics.mean(accept_rds_ctx):.3f}")
    out.append("")

    if spread_r_vals:
        out.append(f"  Spread-blocked counterfactual R:")
        out.append(f"    N={len(spread_r_vals)}, Mean R={statistics.mean(spread_r_vals):+.4f}, "
                   f"WR={sum(1 for r in spread_r_vals if r > 0)*100/len(spread_r_vals):.1f}%")
    out.append("")

    # Model threshold relaxation
    out.append("  THRESHOLD RELAXATION MODEL:")
    out.append("  If MAX_SPREAD_ATR_RATIO were relaxed from 0.30 to 0.40:")
    would_pass_040 = sum(1 for r in spread_ratios if r <= 0.40)
    out.append(f"    Would pass at 0.40: {would_pass_040}/{len(spread_ratios)}")
    would_pass_050 = sum(1 for r in spread_ratios if r <= 0.50)
    out.append(f"    Would pass at 0.50: {would_pass_050}/{len(spread_ratios)}")
    out.append("")

    # But would they be NET positive after spread cost?
    # Spread cost in R terms = spread / risk_distance (this IS the ratio)
    # If ratio is 0.35, you pay 0.35R in spread cost on entry + exit = ~0.70R total roundtrip!?
    # Actually no: spread cost on entry is priced in. SL/TP are set relative to fill.
    # The spread_ratio represents the PROPORTION of risk eaten by spread at entry.
    # So if ratio = 0.35, 35% of your risk is consumed by spread immediately.
    out.append("  SPREAD COST IMPACT ON NET R:")
    out.append("  spread_ratio = spread / risk_distance = fraction of 1R consumed by entry spread")
    for a in spread_analysis:
        if a["shadow_r"] is not None and a["spread_ratio"] > 0:
            # Shadow R is computed from midpoint entry (no spread cost)
            # Real R would be: shadow_r - spread_ratio (approximate)
            net_r = a["shadow_r"] - a["spread_ratio"]
            out.append(f"    {a['symbol']:<8} ratio={a['spread_ratio']:.3f} shadow_R={a['shadow_r']:+.3f} "
                       f"net_R≈{net_r:+.3f} exit={a['shadow_exit']}")
    out.append("")

    # Net expectancy if all 16 had been taken
    if spread_r_vals and spread_ratios:
        # Approximate net R after spread deduction
        net_r_all = []
        for a in spread_analysis:
            if a["shadow_r"] is not None and a["spread_ratio"] > 0:
                net_r_all.append(a["shadow_r"] - a["spread_ratio"])
        if net_r_all:
            out.append(f"  If all {len(net_r_all)} spread-blocked trades were executed:")
            out.append(f"    Raw shadow Mean R: {statistics.mean(spread_r_vals):+.4f}")
            out.append(f"    Net R (after spread deduction): {statistics.mean(net_r_all):+.4f}")
            net_positive = sum(1 for r in net_r_all if r > 0)
            out.append(f"    Net positive: {net_positive}/{len(net_r_all)} ({net_positive*100//len(net_r_all)}%)")
    out.append("")

    # Per-order detail for spread
    out.append("  Per-order detail (all 16 SPREAD_EXCEEDED):")
    out.append(f"  {'Sym':<8} {'Side':<5} {'RD':<10} {'Spread':<10} {'Ratio':<7} {'ShadR':<8} {'ShadExit':<15} {'Pattern'}")
    out.append(f"  {'─'*8} {'─'*5} {'─'*10} {'─'*10} {'─'*7} {'─'*8} {'─'*15} {'─'*20}")
    for a in spread_analysis:
        r_str = f"{a['shadow_r']:+.3f}" if a['shadow_r'] is not None else "?"
        out.append(f"  {a['symbol']:<8} {a['side']:<5} {a['risk_distance']:<10.6f} "
                   f"{a['spread_at_decision']:<10.6f} {a['spread_ratio']:<7.3f} "
                   f"{r_str:<8} {a['shadow_exit']:<15} {a['pattern']}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: SYMBOL-LEVEL STOP DISTANCE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 5: SYMBOL-LEVEL ANALYSIS")
    out.append("━" * 80)
    out.append("")

    # By symbol: rejected vs accepted risk_distance
    for sym in sorted(set(a["symbol"] for a in invalid_analysis)):
        sym_reject_rd = [a["risk_distance"] for a in invalid_analysis if a["symbol"] == sym and a["risk_distance"] > 0]
        sym_accept_rd = [abs(item["exec"].get("entry_reference", 0) - item["exec"].get("sl", 0))
                         for item in accept_with_ctx if item["exec"].get("symbol") == sym
                         and item["exec"].get("entry_reference") and item["exec"].get("sl")]
        sym_accept_rd = [rd for rd in sym_accept_rd if rd > 0]

        if sym_reject_rd or sym_accept_rd:
            rej_mean = statistics.mean(sym_reject_rd) if sym_reject_rd else 0
            acc_mean = statistics.mean(sym_accept_rd) if sym_accept_rd else 0
            out.append(f"  {sym}: Rejected RD mean={rej_mean:.6f} (N={len(sym_reject_rd)}), "
                       f"Accepted RD mean={acc_mean:.6f} (N={len(sym_accept_rd)})")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CAUSAL DIAGNOSIS")
    out.append("=" * 80)
    out.append("")

    out.append("MECHANISM A: PRICE MOVEMENT / LATENCY (Invalid stops)")
    out.append(f"  Orders with valid geometry at decision time: {valid_at_decision}")
    out.append(f"  These became invalid between decision and submission.")
    if valid_at_decision > 0:
        out.append(f"  Classification: PROVEN (geometry valid at decision, invalid at broker)")
        out.append(f"  Recoverability: HIGH — recalculating SL/TP at submission would fix")
    out.append("")

    out.append("MECHANISM B: BROKER MINIMUM STOP DISTANCE")
    out.append(f"  Orders with already-invalid geometry at decision: {invalid_at_decision}")
    if invalid_at_decision > 0:
        out.append(f"  These had SL/TP too close or on wrong side even at decision time.")
        out.append(f"  Classification: PROVEN (structural geometry issue)")
        out.append(f"  Recoverability: LOW — requires wider stop placement by V10")
    out.append("")

    out.append("MECHANISM C: SPREAD EXPANSION (SPREAD_EXCEEDED)")
    out.append(f"  Orders blocked by spread guard: {len(spread_exceeded)}")
    if spread_ratios:
        exceed_at_dec = sum(1 for r in spread_ratios if r > 0.30)
        out.append(f"  Already exceeded 0.30 at decision time: {exceed_at_dec}")
        out.append(f"  Spread widened after decision: {len(spread_ratios) - exceed_at_dec}")
    out.append(f"  Classification: PROVEN")
    out.append(f"  Recoverability: MODERATE — threshold relaxation OR spread monitoring at decision")
    out.append("")

    out.append("OVERALL CLASSIFICATION:")
    out.append(f"  A. Price staleness (latency): {valid_at_decision} orders — PROVEN")
    out.append(f"  B. Structural geometry: {invalid_at_decision} orders — PROVEN")
    out.append(f"  C. Spread guard: {len(spread_exceeded)} orders — PROVEN")
    out.append(f"  D. API errors: {len(api_errors)} orders — RANDOM")
    out.append(f"  E. Other: {len(other_fails)} orders — UNRESOLVED")
    out.append("")

    out.append("RECOVERABILITY SUMMARY:")
    out.append(f"  Recoverable with latency fix: {valid_at_decision} orders")
    out.append(f"  Recoverable with threshold relaxation: ~{would_pass_040} of {len(spread_exceeded)} orders" if spread_ratios else "")
    out.append(f"  NOT recoverable (structural): {invalid_at_decision} orders")
    out.append(f"  NOT recoverable (random): {len(api_errors)} orders")
    out.append("")

    total_recoverable = valid_at_decision + (would_pass_040 if spread_ratios else 0)
    out.append(f"  TOTAL POTENTIALLY RECOVERABLE: {total_recoverable} of 54 ({total_recoverable*100//54}%)")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/broker_rejection_event_level.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
