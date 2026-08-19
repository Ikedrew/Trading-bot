"""
DISPLACEMENT vs EDGE ANALYSIS — MEAN_REVERSION

Question: Does MEAN_REVERSION lose its edge as structural displacement increases?
Is there a measurable displacement threshold beyond which the opportunity is no longer viable?

Method:
- For all V10_PRIMARY shadows that are MEAN_REVERSION (or pattern categories associated
  with mean-reversion strategies), compute the structural displacement at shadow creation
- Displacement = (shadow_entry - entry_reference_structural) in R-multiples
  Since shadow_entry = midpoint at decision time, and we have the risk_distance,
  displacement_R = (shadow_entry - some_reference) / risk_distance
  BUT: we don't have entry_reference in shadow data.
  ALTERNATIVE: use the shadow's own geometry. If shadow entry is far from SL relative to risk,
  the displacement is already captured in how far entry is from SL.
  
  Actually: displacement = how far price has moved from the structural zone.
  For BUY: displacement = current_price - structural_entry_zone
  In the shadow: entry_price = midpoint. stop_loss = structural level below zone.
  So: (entry_price - stop_loss) / risk_distance should show this.
  For a BUY at a demand zone: entry should be NEAR the SL (tight stop below zone).
  If entry >> SL, price has moved far above the demand zone.
  
  Better metric: For BUY shadows, ratio = (entry - SL) / risk_distance tells us
  how many "risks" price is above the stop. 1.0 means normal. >2 means displaced.
  But risk_distance = |entry - SL| by definition. So ratio = 1 always.
  
  NEW APPROACH: Use the execution_context data which has bid/ask at decision time,
  and combine with execution_results which has entry_reference (the structural level).
  Displacement_R = (ctx_ask - entry_reference) / risk_distance for BUY.
  
  This works for orders that have both an execution_result AND an execution_context.
  For shadows without execution data, we can use shadow fields:
  - shadow has entry_price (midpoint) and stop_loss and take_profit
  - shadow entry_price ≈ midpoint ≈ ask (within half-spread)
  - We can compute: how far is entry from TP? This shows whether geometry is stale.
  
  BEST APPROACH: Use ALL execution attempts (322) where we have both:
  - execution_result (entry_reference, SL, TP, pattern, result_ok)
  - execution_context (bid, ask)
  - shadow match (r_multiple = counterfactual outcome)
  
  Compute displacement_R = (ctx_ask - entry_reference) / |entry_reference - SL|
  Then plot shadow R against displacement_R to find the edge decay curve.

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


def load_shadow_primary():
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def main():
    out = []
    out.append("=" * 80)
    out.append("DISPLACEMENT vs EDGE ANALYSIS — MEAN_REVERSION")
    out.append("=" * 80)
    out.append("")

    exec_results = load_execution_results()
    ctx_map = load_execution_contexts()
    shadows = load_shadow_primary()

    # Build shadow lookup
    shadow_by_corr = {}
    for s in shadows:
        cid = s.get("correlation_id", "")
        if cid:
            shadow_by_corr[cid] = s

    out.append(f"Execution results: {len(exec_results)}")
    out.append(f"Execution contexts: {len(ctx_map)}")
    out.append(f"V10_PRIMARY shadows: {len(shadows)}")
    out.append("")

    # Build enriched dataset: exec_result + context + shadow
    enriched = []
    for r in exec_results:
        cid = r.get("correlation_id", "")
        ctx = ctx_map.get(cid, {})
        shadow = shadow_by_corr.get(cid, {})

        if not ctx or not shadow:
            continue

        market = ctx.get("market_access", {}) or {}
        ctx_bid = market.get("bid", 0)
        ctx_ask = market.get("ask", 0)

        symbol = r.get("symbol", "")
        side = r.get("side", "")
        sl = r.get("sl", 0)
        tp = r.get("tp", 0)
        entry_ref = r.get("entry_reference", 0)
        pattern = r.get("pattern", "")
        result_ok = r.get("result_ok", False)

        shadow_r = shadow.get("r_multiple")
        shadow_exit = shadow.get("exit_reason", "")

        if not (entry_ref and sl and tp and ctx_ask and ctx_bid):
            continue

        risk_distance = abs(entry_ref - sl)
        if risk_distance <= 0:
            continue

        # Displacement in R: how far has price moved from structural entry
        if side == "BUY":
            displacement = ctx_ask - entry_ref
        elif side == "SELL":
            displacement = entry_ref - ctx_bid
        else:
            continue

        displacement_r = displacement / risk_distance

        enriched.append({
            "symbol": symbol,
            "side": side,
            "pattern": pattern,
            "displacement_r": displacement_r,
            "risk_distance": risk_distance,
            "shadow_r": shadow_r,
            "shadow_exit": shadow_exit,
            "result_ok": result_ok,
            "entry_ref": entry_ref,
            "ctx_ask": ctx_ask,
            "ctx_bid": ctx_bid,
            "sl": sl,
            "tp": tp,
        })

    out.append(f"Enriched records (exec + ctx + shadow): {len(enriched)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: ALL PATTERNS — DISPLACEMENT vs SHADOW R
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 1: ALL PATTERNS — DISPLACEMENT vs SHADOW R")
    out.append("━" * 80)
    out.append("")

    # Bucket by displacement
    def bucket_analysis(records, label):
        out.append(f"  {label} (N={len(records)}):")
        if not records:
            out.append("    No data")
            out.append("")
            return

        buckets = [
            ("<0R", lambda d: d < 0),
            ("0–1R", lambda d: 0 <= d < 1),
            ("1–2R", lambda d: 1 <= d < 2),
            ("2–3R", lambda d: 2 <= d < 3),
            ("3–5R", lambda d: 3 <= d < 5),
            ("5–10R", lambda d: 5 <= d < 10),
            ("10–20R", lambda d: 10 <= d < 20),
            ("20R+", lambda d: d >= 20),
        ]

        out.append(f"    {'Displacement':<12} {'N':<5} {'Mean R':<9} {'Median R':<10} {'WR%':<7} {'TP%':<6} {'SL%':<6} {'Timeout%'}")
        out.append(f"    {'─'*12} {'─'*5} {'─'*9} {'─'*10} {'─'*7} {'─'*6} {'─'*6} {'─'*8}")

        for label_b, condition in buckets:
            bucket_recs = [r for r in records if condition(r["displacement_r"])]
            if not bucket_recs:
                continue
            r_vals = [r["shadow_r"] for r in bucket_recs if r["shadow_r"] is not None]
            if not r_vals:
                continue
            mean_r = statistics.mean(r_vals)
            median_r = statistics.median(r_vals)
            wr = sum(1 for r in r_vals if r > 0) * 100 / len(r_vals)
            exits = Counter(r["shadow_exit"] for r in bucket_recs)
            tp_pct = exits.get("take_profit", 0) * 100 // len(bucket_recs)
            sl_pct = exits.get("stop_loss", 0) * 100 // len(bucket_recs)
            to_pct = exits.get("max_bars_timeout", 0) * 100 // len(bucket_recs)
            out.append(f"    {label_b:<12} {len(r_vals):<5} {mean_r:+.4f}  {median_r:+.4f}   "
                       f"{wr:<7.1f} {tp_pct}%{'':<3} {sl_pct}%{'':<3} {to_pct}%")
        out.append("")

    bucket_analysis(enriched, "ALL PATTERNS")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: MEAN_REVERSION ONLY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 2: MEAN_REVERSION — DISPLACEMENT vs SHADOW R")
    out.append("━" * 80)
    out.append("")

    mr_records = [r for r in enriched if r["pattern"] == "MEAN_REVERSION"]
    bucket_analysis(mr_records, "MEAN_REVERSION")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: NON-MEAN-REVERSION (for comparison)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 3: NON-MEAN_REVERSION — DISPLACEMENT vs SHADOW R")
    out.append("━" * 80)
    out.append("")

    non_mr_records = [r for r in enriched if r["pattern"] != "MEAN_REVERSION"]
    bucket_analysis(non_mr_records, "NON-MEAN_REVERSION")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: THRESHOLD IDENTIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 4: EDGE THRESHOLD — WHERE DOES MEAN_REVERSION LOSE ITS EDGE?")
    out.append("━" * 80)
    out.append("")

    # Compute cumulative R by displacement threshold for MEAN_REVERSION
    mr_with_r = [(r["displacement_r"], r["shadow_r"]) for r in mr_records if r["shadow_r"] is not None]
    mr_with_r.sort(key=lambda x: x[0])

    if mr_with_r:
        # Sliding threshold: include all orders with displacement <= threshold
        thresholds = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0, 50.0]
        out.append(f"  MEAN_REVERSION: If we ONLY execute when displacement ≤ threshold:")
        out.append(f"    {'Threshold':<11} {'N':<5} {'Mean R':<9} {'WR%':<7} {'Improvement?'}")
        out.append(f"    {'─'*11} {'─'*5} {'─'*9} {'─'*7} {'─'*15}")

        full_r = [r for _, r in mr_with_r]
        full_mean = statistics.mean(full_r)

        for thresh in thresholds:
            below = [r for d, r in mr_with_r if d <= thresh]
            above = [r for d, r in mr_with_r if d > thresh]
            if not below:
                continue
            mean_below = statistics.mean(below)
            wr_below = sum(1 for r in below if r > 0) * 100 / len(below)
            improvement = "✓ BETTER" if mean_below > full_mean + 0.05 else ("≈ same" if abs(mean_below - full_mean) < 0.05 else "✗ worse")
            out.append(f"    ≤{thresh:<9} {len(below):<5} {mean_below:+.4f}  {wr_below:<7.1f} {improvement}")

        out.append("")
        out.append(f"  Full population (no filter): N={len(full_r)}, Mean R={full_mean:+.4f}, "
                   f"WR={sum(1 for r in full_r if r > 0)*100/len(full_r):.1f}%")
        out.append("")

        # Also show what's ABOVE each threshold
        out.append(f"  What MEAN_REVERSION produces ABOVE each displacement threshold:")
        out.append(f"    {'Threshold':<11} {'N':<5} {'Mean R':<9} {'WR%':<7}")
        out.append(f"    {'─'*11} {'─'*5} {'─'*9} {'─'*7}")
        for thresh in thresholds:
            above = [r for d, r in mr_with_r if d > thresh]
            if not above:
                continue
            mean_above = statistics.mean(above)
            wr_above = sum(1 for r in above if r > 0) * 100 / len(above)
            out.append(f"    >{thresh:<9} {len(above):<5} {mean_above:+.4f}  {wr_above:<7.1f}")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: TREND_CONTINUATION FOR COMPARISON
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 5: TREND_CONTINUATION — DISPLACEMENT vs SHADOW R")
    out.append("━" * 80)
    out.append("")

    tc_records = [r for r in enriched if r["pattern"] == "TREND_CONTINUATION"]
    bucket_analysis(tc_records, "TREND_CONTINUATION")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6: SYMBOL SEGMENTATION WITHIN MEAN_REVERSION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 6: MEAN_REVERSION BY SYMBOL")
    out.append("━" * 80)
    out.append("")

    mr_symbols = sorted(set(r["symbol"] for r in mr_records))
    out.append(f"  {'Symbol':<10} {'N':<5} {'Mean Disp(R)':<13} {'Mean R':<9} {'WR%':<7} {'Stale%'}")
    out.append(f"  {'─'*10} {'─'*5} {'─'*13} {'─'*9} {'─'*7} {'─'*7}")
    for sym in mr_symbols:
        sym_recs = [r for r in mr_records if r["symbol"] == sym]
        r_vals = [r["shadow_r"] for r in sym_recs if r["shadow_r"] is not None]
        disps = [r["displacement_r"] for r in sym_recs]
        stale_count = sum(1 for r in sym_recs if r["displacement_r"] > 2 and r["shadow_r"] is not None and
                         ((r["side"] == "BUY" and r["tp"] <= r["ctx_ask"]) or
                          (r["side"] == "SELL" and r["tp"] >= r["ctx_bid"])))
        if r_vals:
            out.append(f"  {sym:<10} {len(r_vals):<5} {statistics.mean(disps):+.2f}R{'':<6} "
                       f"{statistics.mean(r_vals):+.4f}  "
                       f"{sum(1 for r in r_vals if r > 0)*100/len(r_vals):<7.1f}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 7: CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CONCLUSIONS")
    out.append("=" * 80)
    out.append("")

    if mr_with_r:
        # Find the inflection point
        full_mean = statistics.mean([r for _, r in mr_with_r])
        
        # Best threshold = highest mean R for "below" group
        best_thresh = None
        best_mean = -999
        for thresh in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0]:
            below = [r for d, r in mr_with_r if d <= thresh]
            if len(below) >= 5:
                m = statistics.mean(below)
                if m > best_mean:
                    best_mean = m
                    best_thresh = thresh

        out.append(f"  1. DOES MEAN_REVERSION LOSE EDGE WITH DISPLACEMENT?")
        # Compare low vs high displacement
        low_disp = [r for d, r in mr_with_r if d <= 2.0]
        high_disp = [r for d, r in mr_with_r if d > 5.0]
        if low_disp and high_disp:
            low_mean = statistics.mean(low_disp)
            high_mean = statistics.mean(high_disp)
            out.append(f"     Displacement ≤ 2R: N={len(low_disp)}, Mean R={low_mean:+.4f}")
            out.append(f"     Displacement > 5R: N={len(high_disp)}, Mean R={high_mean:+.4f}")
            out.append(f"     Δ = {low_mean - high_mean:+.4f}")
            if low_mean > high_mean + 0.1:
                out.append(f"     → YES: MEAN_REVERSION loses edge with displacement (PROVEN)")
            elif abs(low_mean - high_mean) < 0.1:
                out.append(f"     → NO CLEAR DEGRADATION in this sample")
            else:
                out.append(f"     → COUNTERINTUITIVE: higher displacement has better R")
        out.append("")

        out.append(f"  2. OPTIMAL DISPLACEMENT THRESHOLD:")
        if best_thresh is not None:
            below_best = [r for d, r in mr_with_r if d <= best_thresh]
            out.append(f"     Best threshold: ≤{best_thresh}R")
            out.append(f"     Mean R at threshold: {best_mean:+.4f} (N={len(below_best)})")
            out.append(f"     Full population: {full_mean:+.4f} (N={len(mr_with_r)})")
            out.append(f"     Improvement: {best_mean - full_mean:+.4f}R per trade")
        out.append("")

        out.append(f"  3. CLASSIFICATION:")
        out.append(f"     Full MEAN_REVERSION mean R: {full_mean:+.4f}")
        if full_mean > 0:
            out.append(f"     Strategy has positive edge overall")
        else:
            out.append(f"     Strategy has NEGATIVE edge overall")
        out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/displacement_edge_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
