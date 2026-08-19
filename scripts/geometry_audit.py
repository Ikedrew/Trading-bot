"""
ORDERINTENT GEOMETRY LINEAGE AUDIT

Reconstructs the 29 "Invalid stops" BUY orders and determines:
1. Is SL truly above entry_reference? (what we claimed)
2. Is SL above the ACTUAL execution price (tick.ask for BUY)?
3. Or is entry_reference a structural midpoint that is BELOW the actual ask?

The key hypothesis to test:
- V10 sets entry_reference = structural midpoint (e.g., (swing_high+swing_low)/2 or bos_level)
- Execution uses tick.ask as actual fill price
- For BUY: ask > midpoint > SL is POSSIBLE (SL below midpoint but above nothing)
  BUT: ask > SL would mean the trade IS valid at the broker
  WHILE: SL > entry_reference means our LABEL says "SL above entry" but broker sees SL < ask

So the question is: is SL above or below the ACTUAL ask at execution time?
If SL < ask but SL > entry_reference: it's a REFERENCE PRICE MISINTERPRETATION, not a bug.
If SL > ask: it's a genuine geometry bug that broker correctly rejects.

Also checks ACCEPTED BUY orders for the same condition (SL > entry_reference).

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import Counter, defaultdict

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


def main():
    out = []
    out.append("=" * 80)
    out.append("ORDERINTENT GEOMETRY LINEAGE AUDIT")
    out.append("=" * 80)
    out.append("")

    exec_results = load_execution_results()
    ctx_map = load_execution_contexts()

    out.append(f"Execution results: {len(exec_results)}")
    out.append(f"Execution contexts: {len(ctx_map)}")
    out.append("")

    # Separate by outcome
    invalid_stops = [r for r in exec_results if r.get("comment") == "Invalid stops"]
    successful = [r for r in exec_results if r.get("result_ok")]

    out.append(f"Invalid stops: {len(invalid_stops)}")
    out.append(f"Successful: {len(successful)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: RECONSTRUCT ALL 30 INVALID STOPS ORDERS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 1: FULL RECONSTRUCTION OF INVALID STOPS ORDERS")
    out.append("━" * 80)
    out.append("")

    out.append(f"{'#':<3} {'Sym':<8} {'Side':<5} {'entry_ref':<11} {'SL':<11} {'TP':<11} "
               f"{'ctx_bid':<11} {'ctx_ask':<11} {'SL>ref?':<7} {'SL>ask?':<7} {'Valid@broker?'}")
    out.append(f"{'─'*3} {'─'*8} {'─'*5} {'─'*11} {'─'*11} {'─'*11} {'─'*11} {'─'*11} {'─'*7} {'─'*7} {'─'*13}")

    sl_above_ref_count = 0
    sl_above_ask_count = 0
    sl_above_bid_count = 0
    geometry_valid_at_broker = 0
    geometry_invalid_at_broker = 0
    no_context = 0

    invalid_details = []

    for i, r in enumerate(invalid_stops):
        cid = r.get("correlation_id", "")
        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)

        ctx = ctx_map.get(cid, {})
        market = ctx.get("market_access", {}) or {}
        ctx_bid = market.get("bid", 0)
        ctx_ask = market.get("ask", 0)

        sl_above_ref = False
        sl_above_ask = False
        sl_below_bid = False
        valid_at_broker = "?"

        if side == "BUY":
            # BUY invariant: SL < entry < TP
            # At broker: SL < ask (execution price) and TP > ask
            sl_above_ref = (sl >= entry_ref) if (sl and entry_ref) else False
            sl_above_ask = (sl >= ctx_ask) if (sl and ctx_ask) else False
            if ctx_ask:
                # Broker uses ask as price for BUY
                valid_at_broker = "NO" if (sl >= ctx_ask or tp <= ctx_ask) else "YES"
            else:
                valid_at_broker = "?"
        elif side == "SELL":
            # SELL invariant: TP < entry < SL
            # At broker: SL > bid (execution price) and TP < bid
            sl_below_bid = (sl <= ctx_bid) if (sl and ctx_bid) else False
            sl_above_ref = (sl <= entry_ref) if (sl and entry_ref) else False  # inverted for SELL
            if ctx_bid:
                valid_at_broker = "NO" if (sl <= ctx_bid or tp >= ctx_bid) else "YES"
            else:
                valid_at_broker = "?"

        if sl_above_ref:
            sl_above_ref_count += 1
        if side == "BUY" and sl_above_ask:
            sl_above_ask_count += 1
        if side == "SELL" and sl_below_bid:
            sl_above_bid_count += 1
        if valid_at_broker == "YES":
            geometry_valid_at_broker += 1
        elif valid_at_broker == "NO":
            geometry_invalid_at_broker += 1
        else:
            no_context += 1

        detail = {
            "symbol": symbol, "side": side, "sl": sl, "tp": tp,
            "entry_ref": entry_ref, "ctx_bid": ctx_bid, "ctx_ask": ctx_ask,
            "sl_above_ref": sl_above_ref, "sl_above_ask": sl_above_ask,
            "valid_at_broker": valid_at_broker,
            "correlation_id": cid,
        }
        invalid_details.append(detail)

        ref_str = f"{entry_ref:.5f}" if entry_ref else "0"
        sl_str = f"{sl:.5f}" if sl else "0"
        tp_str = f"{tp:.5f}" if tp else "0"
        bid_str = f"{ctx_bid:.5f}" if ctx_bid else "?"
        ask_str = f"{ctx_ask:.5f}" if ctx_ask else "?"

        out.append(f"{i+1:<3} {symbol:<8} {side:<5} {ref_str:<11} {sl_str:<11} {tp_str:<11} "
                   f"{bid_str:<11} {ask_str:<11} {'YES' if sl_above_ref else 'no':<7} "
                   f"{'YES' if (side=='BUY' and sl_above_ask) else 'no':<7} {valid_at_broker}")

    out.append("")
    out.append(f"  SUMMARY:")
    out.append(f"    SL > entry_reference (our diagnostic): {sl_above_ref_count}/{len(invalid_stops)}")
    out.append(f"    SL > ctx_ask (actually invalid for BUY at broker): {sl_above_ask_count}/{len(invalid_stops)}")
    out.append(f"    Geometry valid at broker price: {geometry_valid_at_broker}")
    out.append(f"    Geometry INVALID at broker price: {geometry_invalid_at_broker}")
    out.append(f"    No context (cannot determine): {no_context}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: CHECK ACCEPTED BUY ORDERS FOR SAME CONDITION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 2: DO ACCEPTED BUY ORDERS ALSO HAVE SL > entry_reference?")
    out.append("━" * 80)
    out.append("")

    accepted_buy = [r for r in successful if r.get("side") == "BUY"]
    accepted_sell = [r for r in successful if r.get("side") == "SELL"]

    buy_sl_above_ref = 0
    buy_sl_below_ref = 0
    buy_total_with_data = 0

    for r in accepted_buy:
        sl = r.get("sl", 0)
        entry_ref = r.get("entry_reference", 0)
        if sl and entry_ref:
            buy_total_with_data += 1
            if sl >= entry_ref:
                buy_sl_above_ref += 1
            else:
                buy_sl_below_ref += 1

    out.append(f"  Accepted BUY orders: {len(accepted_buy)}")
    out.append(f"  With SL & entry_reference data: {buy_total_with_data}")
    out.append(f"  SL >= entry_reference: {buy_sl_above_ref}")
    out.append(f"  SL < entry_reference: {buy_sl_below_ref}")
    out.append("")

    if buy_sl_above_ref > 0:
        out.append(f"  ⚠️  {buy_sl_above_ref} ACCEPTED BUY orders also have SL >= entry_reference!")
        out.append(f"  This means SL > entry_reference is NOT the cause of broker rejection.")
        out.append(f"  The entry_reference is NOT the broker's execution price.")
        out.append("")
        # Show examples
        out.append("  Examples of ACCEPTED BUY with SL > entry_reference:")
        count = 0
        for r in accepted_buy:
            sl = r.get("sl", 0)
            entry_ref = r.get("entry_reference", 0)
            if sl and entry_ref and sl >= entry_ref:
                cid = r.get("correlation_id", "")
                ctx = ctx_map.get(cid, {})
                market = ctx.get("market_access", {}) or {}
                ctx_ask = market.get("ask", 0)
                fill = r.get("fill_price", 0)
                out.append(f"    {r.get('symbol')}: entry_ref={entry_ref:.5f}, SL={sl:.5f}, "
                           f"ctx_ask={ctx_ask:.5f}, fill={fill:.5f}")
                count += 1
                if count >= 5:
                    break
        out.append("")

    # Same for SELL
    sell_sl_below_ref = 0
    sell_total_with_data = 0
    for r in accepted_sell:
        sl = r.get("sl", 0)
        entry_ref = r.get("entry_reference", 0)
        if sl and entry_ref:
            sell_total_with_data += 1
            if sl <= entry_ref:
                sell_sl_below_ref += 1

    out.append(f"  Accepted SELL orders: {len(accepted_sell)}")
    out.append(f"  SL <= entry_reference (would be 'invalid' by our earlier test): {sell_sl_below_ref}/{sell_total_with_data}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: THE REAL QUESTION — WHAT PRICE DOES THE BROKER USE?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 3: REFERENCE PRICE MODEL — WHAT IS entry_reference?")
    out.append("━" * 80)
    out.append("")

    # Compare entry_reference to fill_price for accepted orders
    buy_ref_vs_fill = []
    for r in accepted_buy:
        ref = r.get("entry_reference", 0)
        fill = r.get("fill_price", 0)
        if ref and fill:
            buy_ref_vs_fill.append({"ref": ref, "fill": fill, "diff": fill - ref, "sym": r.get("symbol", "")})

    if buy_ref_vs_fill:
        diffs = [x["diff"] for x in buy_ref_vs_fill]
        out.append(f"  BUY: fill_price - entry_reference (N={len(diffs)}):")
        out.append(f"    Mean: {statistics.mean(diffs):.6f}")
        out.append(f"    Median: {statistics.median(diffs):.6f}")
        out.append(f"    Min: {min(diffs):.6f}, Max: {max(diffs):.6f}")
        out.append(f"    fill > ref (fill higher than reference): {sum(1 for d in diffs if d > 0)}/{len(diffs)}")
        out.append(f"    fill < ref (fill lower than reference): {sum(1 for d in diffs if d < 0)}/{len(diffs)}")
        out.append(f"    fill = ref (exact match): {sum(1 for d in diffs if d == 0)}/{len(diffs)}")
        out.append("")

        # This tells us whether entry_reference is the live ask or a structural estimate
        if abs(statistics.mean(diffs)) < 0.00005:
            out.append("  → entry_reference ≈ fill_price (reference IS the live price)")
        else:
            out.append(f"  → entry_reference ≠ fill_price (structural estimate, {statistics.mean(diffs):+.6f} average difference)")
            out.append("  → V10 entry_reference is a structural/midpoint estimate, NOT the live ask")
    out.append("")

    # For rejected orders: compare entry_reference to ctx_ask
    buy_ref_vs_ask = []
    for d in invalid_details:
        if d["side"] == "BUY" and d["entry_ref"] and d["ctx_ask"]:
            buy_ref_vs_ask.append({"ref": d["entry_ref"], "ask": d["ctx_ask"],
                                   "diff": d["ctx_ask"] - d["entry_ref"], "sym": d["symbol"]})

    if buy_ref_vs_ask:
        diffs = [x["diff"] for x in buy_ref_vs_ask]
        out.append(f"  REJECTED BUY: ctx_ask - entry_reference (N={len(diffs)}):")
        out.append(f"    Mean: {statistics.mean(diffs):.6f}")
        out.append(f"    Median: {statistics.median(diffs):.6f}")
        out.append(f"    ask > ref: {sum(1 for d in diffs if d > 0)}/{len(diffs)}")
        out.append(f"    ask < ref: {sum(1 for d in diffs if d < 0)}/{len(diffs)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: DETERMINE THE ACTUAL BROKER INVARIANT VIOLATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 4: ACTUAL BROKER INVARIANT — SL vs EXECUTION PRICE")
    out.append("━" * 80)
    out.append("")

    # The broker (MT5) checks:
    # BUY: SL must be < current_ask - stops_level*point, TP must be > current_ask + stops_level*point
    # OR at minimum: SL < current_ask and TP > current_ask
    # BUT: the execution happens at submission time, not decision time.
    # ctx_ask is the DECISION-TIME ask. The submission-time ask could be different.

    # For rejected BUYs: check SL vs ctx_ask
    out.append("  For rejected BUY orders — checking SL vs decision-time ASK:")
    buy_invalid = [d for d in invalid_details if d["side"] == "BUY" and d["ctx_ask"]]
    
    sl_above_ctx_ask = 0
    sl_below_ctx_ask = 0
    tp_below_ctx_ask = 0
    for d in buy_invalid:
        if d["sl"] >= d["ctx_ask"]:
            sl_above_ctx_ask += 1
        else:
            sl_below_ctx_ask += 1
        if d["tp"] <= d["ctx_ask"]:
            tp_below_ctx_ask += 1

    out.append(f"    SL >= ctx_ask (genuinely invalid): {sl_above_ctx_ask}")
    out.append(f"    SL < ctx_ask (would be valid at decision-time ask): {sl_below_ctx_ask}")
    out.append(f"    TP <= ctx_ask (TP already passed): {tp_below_ctx_ask}")
    out.append("")

    if sl_below_ctx_ask > 0:
        out.append(f"  ⚠️  {sl_below_ctx_ask} orders have SL < ctx_ask (valid geometry at decision time!)")
        out.append(f"  This means the BROKER-TIME ask must have moved BELOW the SL between decision and submission.")
        out.append(f"  OR: the broker's stops_level minimum distance is larger than (ask - SL).")
        out.append("")
        # Show these
        out.append("  Orders valid at decision but rejected by broker:")
        for d in buy_invalid:
            if d["sl"] < d["ctx_ask"] and d["sl"] >= d["entry_ref"]:
                out.append(f"    {d['symbol']}: ref={d['entry_ref']:.5f}, SL={d['sl']:.5f}, "
                           f"ask={d['ctx_ask']:.5f}, TP={d['tp']:.5f}")
                out.append(f"      → SL is {(d['ctx_ask']-d['sl'])*10000:.1f} pips below ask")
                out.append(f"      → SL is {(d['sl']-d['entry_ref'])*10000:.1f} pips ABOVE entry_ref")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: FINAL CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 5: FINAL CLASSIFICATION")
    out.append("━" * 80)
    out.append("")

    out.append("QUESTION: Is 'SL_ABOVE_ENTRY' a REAL BUG or a REFERENCE-PRICE MISINTERPRETATION?")
    out.append("")

    if buy_sl_above_ref > 0:
        out.append("  ANSWER: REFERENCE-PRICE MISINTERPRETATION")
        out.append(f"  PROOF: {buy_sl_above_ref} ACCEPTED BUY orders ALSO have SL > entry_reference")
        out.append("  Therefore SL > entry_reference is NOT the condition that causes rejection.")
        out.append("")
        out.append("  The V10 entry_reference is a STRUCTURAL ESTIMATE (midpoint/BOS level)")
        out.append("  that can be BELOW the actual ask (execution price).")
        out.append("  When entry_ref < SL < ask, the geometry is VALID for the broker")
        out.append("  because the broker evaluates SL against ask, not against entry_reference.")
        out.append("")
        out.append("  The ACTUAL rejection cause for the 30 'Invalid stops' orders must be:")
        out.append("  1. SL too close to current price (broker stops_level minimum)")
        out.append("  2. Price moved between decision and order submission")
        out.append("  3. TP already passed (price already at/beyond target)")
        out.append("  4. Broker-specific minimum stop distance constraint")
    else:
        out.append("  ANSWER: PROBABLE BUG")
        out.append("  No accepted BUY orders have the same condition.")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6: SHADOW GEOMETRY COMPARISON
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 6: SHADOW GEOMETRY — DOES SHADOW USE SAME entry_reference?")
    out.append("━" * 80)
    out.append("")

    # Load shadows and compare
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    shadows = builder.get_population(Population.PRIMARY_V10_SHADOW)

    shadow_by_corr = {}
    for s in shadows:
        cid = s.get("correlation_id", "")
        if cid:
            shadow_by_corr[cid] = s

    out.append("  Shadow entry model: entry_price = (bid + ask) / 2 at decision time")
    out.append("  OrderIntent entry_reference = structural estimate (midpoint/BOS level)")
    out.append("")

    # For rejected orders, compare shadow entry to OrderIntent entry_ref
    shadow_vs_intent = []
    for d in invalid_details:
        cid = d.get("correlation_id", "")
        shadow = shadow_by_corr.get(cid, {})
        if shadow:
            shadow_entry = shadow.get("entry_price", 0)
            shadow_sl = shadow.get("stop_loss", 0)
            shadow_tp = shadow.get("take_profit", 0)
            shadow_vs_intent.append({
                "symbol": d["symbol"],
                "side": d["side"],
                "intent_ref": d["entry_ref"],
                "intent_sl": d["sl"],
                "intent_tp": d["tp"],
                "shadow_entry": shadow_entry,
                "shadow_sl": shadow_sl,
                "shadow_tp": shadow_tp,
                "ctx_ask": d["ctx_ask"],
                "ctx_bid": d["ctx_bid"],
            })

    if shadow_vs_intent:
        out.append(f"  Matched {len(shadow_vs_intent)} rejected orders to their shadows:")
        out.append("")
        out.append(f"  {'Sym':<8} {'Side':<5} {'IntentRef':<11} {'IntentSL':<11} {'ShadEntry':<11} {'ShadSL':<11} {'CtxAsk':<11} {'SameGeom?'}")
        out.append(f"  {'─'*8} {'─'*5} {'─'*11} {'─'*11} {'─'*11} {'─'*11} {'─'*11} {'─'*9}")
        for sv in shadow_vs_intent[:15]:
            same_sl = "YES" if abs(sv["intent_sl"] - sv["shadow_sl"]) < 0.00001 else "no"
            out.append(f"  {sv['symbol']:<8} {sv['side']:<5} "
                       f"{sv['intent_ref']:<11.5f} {sv['intent_sl']:<11.5f} "
                       f"{sv['shadow_entry']:<11.5f} {sv['shadow_sl']:<11.5f} "
                       f"{sv['ctx_ask']:<11.5f} {same_sl}")
        out.append("")

        # Does shadow use the same SL as OrderIntent?
        same_sl_count = sum(1 for sv in shadow_vs_intent if abs(sv["intent_sl"] - sv["shadow_sl"]) < 0.00001)
        diff_sl_count = len(shadow_vs_intent) - same_sl_count
        out.append(f"  Shadow uses SAME SL as OrderIntent: {same_sl_count}/{len(shadow_vs_intent)}")
        out.append(f"  Shadow uses DIFFERENT SL: {diff_sl_count}/{len(shadow_vs_intent)}")
        out.append("")

        # Does shadow entry = midpoint or entry_reference?
        mid_matches = 0
        ref_matches = 0
        for sv in shadow_vs_intent:
            mid = (sv["ctx_bid"] + sv["ctx_ask"]) / 2 if sv["ctx_bid"] and sv["ctx_ask"] else 0
            if mid and abs(sv["shadow_entry"] - mid) < 0.00005:
                mid_matches += 1
            if abs(sv["shadow_entry"] - sv["intent_ref"]) < 0.00005:
                ref_matches += 1

        out.append(f"  Shadow entry ≈ (bid+ask)/2 midpoint: {mid_matches}/{len(shadow_vs_intent)}")
        out.append(f"  Shadow entry ≈ intent entry_reference: {ref_matches}/{len(shadow_vs_intent)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # FINAL DIAGNOSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("FINAL DIAGNOSIS")
    out.append("=" * 80)
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/geometry_audit.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
