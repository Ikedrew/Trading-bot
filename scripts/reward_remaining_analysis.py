"""
REWARD_REMAINING vs COUNTERFACTUAL R — FULL SHADOW POPULATION

The previous analysis was limited to 57 execution-attempt-matched MEAN_REVERSION orders.
This analysis uses the FULL V10_PRIMARY shadow population (989 records) to get
sufficient sample size for robust conclusions.

For each shadow, we can compute:
  reward_remaining_R = (TP - entry_price) / risk_distance  [for BUY-like geometry]
  
Since shadow entry = midpoint at decision time, and shadow uses the SAME SL/TP as OrderIntent:
  risk_distance = |entry_price - SL|
  reward_remaining_R = |TP - entry_price| / risk_distance  [reward available from midpoint]
  
This is effectively the shadow's RR ratio (reward:risk from actual entry point).

We then compare shadow_r (outcome) against reward_remaining to find:
1. Is there a minimum RR below which outcomes are systematically negative?
2. After deducting spread costs (spread_ratio = spread/risk_distance), what's the net R?
3. Is there a stable economically meaningful threshold?

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


def load_shadow_primary():
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def bootstrap_mean_ci(values, n_bootstrap=2000, ci=0.90):
    if len(values) < 3:
        return (None, None, statistics.mean(values) if values else 0)
    means = []
    for _ in range(n_bootstrap):
        sample = random.choices(values, k=len(values))
        means.append(statistics.mean(sample))
    means.sort()
    lo_idx = int((1 - ci) / 2 * n_bootstrap)
    hi_idx = int((1 + ci) / 2 * n_bootstrap)
    return (means[lo_idx], means[hi_idx], statistics.mean(values))


def main():
    random.seed(42)

    out = []
    out.append("=" * 80)
    out.append("REWARD_REMAINING vs COUNTERFACTUAL R — FULL SHADOW POPULATION")
    out.append("=" * 80)
    out.append("")

    shadows = load_shadow_primary()
    out.append(f"Total V10_PRIMARY shadows: {len(shadows)}")

    # Filter to shadows with valid geometry
    valid = []
    for s in shadows:
        entry = s.get("entry_price", 0)
        sl = s.get("stop_loss", 0)
        tp = s.get("take_profit", 0)
        r_mult = s.get("r_multiple")
        direction = s.get("direction", "")
        rr = s.get("reward_risk_ratio", 0)
        risk_dist = s.get("risk_distance", 0)
        spread = s.get("spread_at_entry") or 0
        
        if not (entry and sl and tp and r_mult is not None and direction):
            continue
        if risk_dist <= 0:
            risk_dist = abs(entry - sl)
        if risk_dist <= 0:
            continue

        # Compute reward_remaining from shadow's perspective
        # Shadow enters at midpoint, so this is the actual RR from entry
        if direction == "BUY":
            reward_remaining = (tp - entry) / risk_dist
        elif direction == "SELL":
            reward_remaining = (entry - tp) / risk_dist
        else:
            continue

        # Spread cost in R (if available)
        if spread and spread > 0:
            spread_cost_r = spread / risk_dist
        else:
            # Estimate from typical spreads
            symbol = s.get("symbol", "")
            # Use conservative estimates based on known forex spreads
            spread_cost_r = 0  # Will compute separately where spread data exists

        valid.append({
            "symbol": s.get("symbol", ""),
            "direction": direction,
            "pattern": s.get("pattern", ""),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "r_multiple": r_mult,
            "reward_remaining_r": reward_remaining,
            "risk_distance": risk_dist,
            "rr_ratio": rr,
            "spread_at_entry": spread,
            "spread_cost_r": spread_cost_r,
            "exit_reason": s.get("exit_reason", ""),
            "bars_held": s.get("bars_held", 0),
            "has_correlation_id": bool(s.get("correlation_id")),
        })

    out.append(f"Valid shadows with geometry: {len(valid)}")
    out.append("")

    # Separate synthetic test data (no correlation_id, all same characteristics)
    real_shadows = [v for v in valid if v["has_correlation_id"]]
    test_shadows = [v for v in valid if not v["has_correlation_id"]]
    out.append(f"Real (execution period, has corr_id): {len(real_shadows)}")
    out.append(f"Synthetic/test (no corr_id): {len(test_shadows)}")
    out.append(f"Using REAL shadows only for analysis.")
    out.append("")

    data = real_shadows  # Only use real execution-period data

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: REWARD_REMAINING DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 1: REWARD_REMAINING DISTRIBUTION (all strategies)")
    out.append("━" * 80)
    out.append("")

    rr_vals = [d["reward_remaining_r"] for d in data]
    out.append(f"  N={len(rr_vals)}")
    out.append(f"  Mean reward_remaining: {statistics.mean(rr_vals):.3f}R")
    out.append(f"  Median: {statistics.median(rr_vals):.3f}R")
    out.append(f"  Min: {min(rr_vals):.3f}R, Max: {max(rr_vals):.3f}R")
    out.append("")

    # Distribution
    buckets_rr = [(-99, 0), (0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0),
                  (2.0, 3.0), (3.0, 5.0), (5.0, 10.0), (10.0, 999)]
    out.append(f"  {'RR Range':<12} {'N':<6} {'%':<6}")
    out.append(f"  {'─'*12} {'─'*6} {'─'*6}")
    for lo, hi in buckets_rr:
        count = sum(1 for r in rr_vals if lo <= r < hi)
        pct = count * 100 / len(rr_vals)
        label = f"{lo:.1f}–{hi:.1f}" if hi < 100 else f"{lo:.1f}+"
        out.append(f"  {label:<12} {count:<6} {pct:.1f}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: REWARD_REMAINING vs SHADOW R (all strategies)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 2: REWARD_REMAINING vs SHADOW R (ALL strategies, N={})".format(len(data)))
    out.append("━" * 80)
    out.append("")

    buckets = [(-99, 0, "≤0 (stale)"), (0, 0.5, "0–0.5R"), (0.5, 1.0, "0.5–1.0R"),
               (1.0, 1.5, "1.0–1.5R"), (1.5, 2.0, "1.5–2.0R"), (2.0, 3.0, "2.0–3.0R"),
               (3.0, 5.0, "3.0–5.0R"), (5.0, 10.0, "5.0–10R"), (10.0, 999, "10R+")]

    out.append(f"  {'RR Bucket':<14} {'N':<6} {'Mean R':<9} {'Median R':<10} {'WR%':<7} {'90% CI':<20} {'TP%':<5} {'SL%':<5} {'TO%'}")
    out.append(f"  {'─'*14} {'─'*6} {'─'*9} {'─'*10} {'─'*7} {'─'*20} {'─'*5} {'─'*5} {'─'*5}")

    for lo, hi, label in buckets:
        bucket_data = [d for d in data if lo <= d["reward_remaining_r"] < hi]
        if not bucket_data:
            continue
        r_vals = [d["r_multiple"] for d in bucket_data]
        mean_r = statistics.mean(r_vals)
        median_r = statistics.median(r_vals)
        wr = sum(1 for r in r_vals if r > 0) * 100 / len(r_vals)
        exits = Counter(d["exit_reason"] for d in bucket_data)
        tp_pct = exits.get("take_profit", 0) * 100 // len(bucket_data)
        sl_pct = exits.get("stop_loss", 0) * 100 // len(bucket_data)
        to_pct = exits.get("max_bars_timeout", 0) * 100 // len(bucket_data)

        lo_ci, hi_ci, _ = bootstrap_mean_ci(r_vals)
        ci_str = f"[{lo_ci:+.3f}, {hi_ci:+.3f}]" if lo_ci is not None else "N/A"

        out.append(f"  {label:<14} {len(r_vals):<6} {mean_r:+.4f}  {median_r:+.4f}   "
                   f"{wr:<7.1f} {ci_str:<20} {tp_pct}%{'':<2} {sl_pct}%{'':<2} {to_pct}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: NET R AFTER SPREAD COST ESTIMATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 3: NET R AFTER ESTIMATED SPREAD COSTS")
    out.append("━" * 80)
    out.append("")

    # Typical spread costs by symbol (from execution context data we've seen):
    # FX majors: ~1-2 pips → depends on risk_distance
    # For this analysis: use the actual spread_at_entry where available,
    # otherwise estimate spread_cost_r from the execution-period context data.
    
    # From earlier analysis: accepted trades had mean spread of 0.067 price units (varies by symbol)
    # Better: compute spread_cost_r = spread / risk_distance for each shadow
    # Use the shadow's spread_at_entry if populated

    # How many have spread data?
    with_spread = [d for d in data if d["spread_at_entry"] and d["spread_at_entry"] > 0]
    without_spread = [d for d in data if not d["spread_at_entry"] or d["spread_at_entry"] <= 0]
    out.append(f"  Shadows with spread_at_entry: {len(with_spread)}")
    out.append(f"  Shadows without spread data: {len(without_spread)}")
    out.append("")

    # For those with spread, compute actual spread_cost_r
    if with_spread:
        spread_costs = [d["spread_at_entry"] / d["risk_distance"] for d in with_spread if d["risk_distance"] > 0]
        out.append(f"  Actual spread_cost_R (where available):")
        out.append(f"    N={len(spread_costs)}, Mean={statistics.mean(spread_costs):.4f}R, "
                   f"Median={statistics.median(spread_costs):.4f}R")
        out.append("")

    # Use conservative estimate: 0.15R spread cost for FX, 0.05R for indices
    # (based on typical 1-2 pip spread on 10-15 pip stops for FX)
    # Actually let's use the per-symbol median from execution_context data
    
    # Estimate per-symbol spread_cost_R from available data
    symbol_spread_costs = defaultdict(list)
    for d in with_spread:
        if d["risk_distance"] > 0:
            symbol_spread_costs[d["symbol"]].append(d["spread_at_entry"] / d["risk_distance"])
    
    symbol_median_cost = {}
    for sym, costs in symbol_spread_costs.items():
        symbol_median_cost[sym] = statistics.median(costs)
        
    out.append(f"  Estimated spread_cost_R by symbol:")
    for sym, cost in sorted(symbol_median_cost.items()):
        out.append(f"    {sym}: {cost:.4f}R")
    out.append("")

    # Apply spread cost: use actual if available, else symbol median, else global median
    global_median_cost = statistics.median(spread_costs) if spread_costs else 0.15
    
    net_data = []
    for d in data:
        if d["spread_at_entry"] and d["spread_at_entry"] > 0 and d["risk_distance"] > 0:
            cost = d["spread_at_entry"] / d["risk_distance"]
        elif d["symbol"] in symbol_median_cost:
            cost = symbol_median_cost[d["symbol"]]
        else:
            cost = global_median_cost
        
        net_r = d["r_multiple"] - cost  # Deduct spread from shadow R
        net_data.append({**d, "net_r": net_r, "spread_cost_applied": cost})

    # Redo the bucket analysis with NET R
    out.append(f"  REWARD_REMAINING vs NET R (after spread deduction):")
    out.append(f"  {'RR Bucket':<14} {'N':<6} {'Raw R':<9} {'Net R':<9} {'Net WR%':<8} {'Net 90% CI'}")
    out.append(f"  {'─'*14} {'─'*6} {'─'*9} {'─'*9} {'─'*8} {'─'*20}")

    for lo, hi, label in buckets:
        bucket_data = [d for d in net_data if lo <= d["reward_remaining_r"] < hi]
        if not bucket_data:
            continue
        raw_r = [d["r_multiple"] for d in bucket_data]
        net_r_vals = [d["net_r"] for d in bucket_data]
        mean_raw = statistics.mean(raw_r)
        mean_net = statistics.mean(net_r_vals)
        net_wr = sum(1 for r in net_r_vals if r > 0) * 100 / len(net_r_vals)
        lo_ci, hi_ci, _ = bootstrap_mean_ci(net_r_vals)
        ci_str = f"[{lo_ci:+.3f}, {hi_ci:+.3f}]" if lo_ci is not None else "N/A"
        out.append(f"  {label:<14} {len(net_r_vals):<6} {mean_raw:+.4f}  {mean_net:+.4f}  "
                   f"{net_wr:<8.1f} {ci_str}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: FIND THE BREAKEVEN THRESHOLD
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 4: MINIMUM REWARD REQUIREMENT (breakeven threshold)")
    out.append("━" * 80)
    out.append("")

    # For cumulative threshold: "if we only take trades with RR >= X, what's the net R?"
    thresholds = [0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0]
    
    out.append(f"  Cumulative: If minimum reward_remaining >= threshold:")
    out.append(f"  {'Min RR':<9} {'N':<6} {'Net Mean R':<11} {'Net WR%':<9} {'Net 90% CI':<22} {'Edge?'}")
    out.append(f"  {'─'*9} {'─'*6} {'─'*11} {'─'*9} {'─'*22} {'─'*12}")

    for thresh in thresholds:
        above = [d for d in net_data if d["reward_remaining_r"] >= thresh]
        if not above:
            continue
        net_r_vals = [d["net_r"] for d in above]
        mean_net = statistics.mean(net_r_vals)
        net_wr = sum(1 for r in net_r_vals if r > 0) * 100 / len(net_r_vals)
        lo_ci, hi_ci, _ = bootstrap_mean_ci(net_r_vals)
        ci_str = f"[{lo_ci:+.3f}, {hi_ci:+.3f}]" if lo_ci is not None else "N/A"
        
        if lo_ci is not None and lo_ci > 0:
            edge = "✓ POSITIVE"
        elif lo_ci is not None and hi_ci < 0:
            edge = "✗ NEGATIVE"
        else:
            edge = "~ uncertain"
        
        out.append(f"  ≥{thresh:<7} {len(net_r_vals):<6} {mean_net:+.4f}    {net_wr:<9.1f} {ci_str:<22} {edge}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: STRATEGY-SEGMENTED THRESHOLD
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 5: STRATEGY-SEGMENTED ANALYSIS")
    out.append("━" * 80)
    out.append("")

    patterns = sorted(set(d["pattern"] for d in net_data if d["pattern"]))
    for pat in patterns:
        pat_data = [d for d in net_data if d["pattern"] == pat]
        if len(pat_data) < 10:
            continue
        
        out.append(f"  {pat} (N={len(pat_data)}):")
        net_r_vals = [d["net_r"] for d in pat_data]
        out.append(f"    Overall net R: {statistics.mean(net_r_vals):+.4f}, "
                   f"WR={sum(1 for r in net_r_vals if r > 0)*100/len(net_r_vals):.1f}%")
        
        # Find breakeven threshold for this pattern
        for thresh in [0.5, 1.0, 1.5, 2.0, 3.0]:
            above = [d for d in pat_data if d["reward_remaining_r"] >= thresh]
            if len(above) >= 5:
                net_above = [d["net_r"] for d in above]
                out.append(f"    RR≥{thresh}: N={len(net_above)}, Net R={statistics.mean(net_above):+.4f}, "
                           f"WR={sum(1 for r in net_above if r > 0)*100/len(net_above):.1f}%")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6: SYMBOL-SEGMENTED (to check universality)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 6: DOES THE THRESHOLD HOLD ACROSS SYMBOLS?")
    out.append("━" * 80)
    out.append("")

    syms = sorted(set(d["symbol"] for d in net_data))
    out.append(f"  {'Symbol':<10} {'N':<5} {'Net R (all)':<12} {'Net R (RR≥1.5)':<15} {'N(RR≥1.5)':<10}")
    out.append(f"  {'─'*10} {'─'*5} {'─'*12} {'─'*15} {'─'*10}")
    for sym in syms:
        sym_data = [d for d in net_data if d["symbol"] == sym]
        if len(sym_data) < 5:
            continue
        all_net = [d["net_r"] for d in sym_data]
        above_15 = [d["net_r"] for d in sym_data if d["reward_remaining_r"] >= 1.5]
        all_str = f"{statistics.mean(all_net):+.4f}" if all_net else "N/A"
        above_str = f"{statistics.mean(above_15):+.4f}" if above_15 else "N/A"
        out.append(f"  {sym:<10} {len(sym_data):<5} {all_str:<12} {above_str:<15} {len(above_15)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 7: ECONOMIC SIGNIFICANCE
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 7: ECONOMIC SIGNIFICANCE OF THRESHOLD")
    out.append("━" * 80)
    out.append("")

    # What would the system produce at different minimum RR filters?
    out.append(f"  PORTFOLIO IMPACT: If minimum reward_remaining filter were applied:")
    out.append(f"  {'Filter':<12} {'Trades':<8} {'Net R/trade':<12} {'Total Net R':<12} {'vs Unfiltered'}")
    out.append(f"  {'─'*12} {'─'*8} {'─'*12} {'─'*12} {'─'*13}")
    
    unfiltered_net = [d["net_r"] for d in net_data]
    unfiltered_total = sum(unfiltered_net)
    unfiltered_mean = statistics.mean(unfiltered_net) if unfiltered_net else 0
    
    out.append(f"  {'None':<12} {len(unfiltered_net):<8} {unfiltered_mean:+.4f}     "
               f"{unfiltered_total:+.1f}R{'':<5} baseline")
    
    for thresh in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        filtered = [d["net_r"] for d in net_data if d["reward_remaining_r"] >= thresh]
        if not filtered:
            continue
        filt_mean = statistics.mean(filtered)
        filt_total = sum(filtered)
        improvement = filt_total - unfiltered_total
        out.append(f"  ≥{thresh}R{'':<7} {len(filtered):<8} {filt_mean:+.4f}     "
                   f"{filt_total:+.1f}R{'':<5} {improvement:+.1f}R")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CONCLUSIONS")
    out.append("=" * 80)
    out.append("")

    # Find the threshold where CI lower bound first crosses zero
    breakeven_thresh = None
    for thresh in thresholds:
        above = [d for d in net_data if d["reward_remaining_r"] >= thresh]
        if len(above) >= 5:
            net_r_vals = [d["net_r"] for d in above]
            lo_ci, hi_ci, mean = bootstrap_mean_ci(net_r_vals)
            if lo_ci is not None and lo_ci > 0:
                breakeven_thresh = thresh
                break

    out.append(f"  1. MINIMUM REWARD REQUIREMENT (CI lower bound > 0):")
    if breakeven_thresh is not None:
        above = [d for d in net_data if d["reward_remaining_r"] >= breakeven_thresh]
        net_r_vals = [d["net_r"] for d in above]
        lo_ci, hi_ci, mean = bootstrap_mean_ci(net_r_vals)
        out.append(f"     First positive-edge threshold: reward_remaining ≥ {breakeven_thresh}R")
        out.append(f"     Net R: {mean:+.4f}, 90% CI: [{lo_ci:+.4f}, {hi_ci:+.4f}]")
        out.append(f"     N={len(net_r_vals)}")
    else:
        out.append(f"     No threshold found where 90% CI is entirely positive")
        out.append(f"     This means no minimum RR guarantees positive edge in this sample")
    out.append("")

    # Find where mean crosses zero
    zero_thresh = None
    for thresh in thresholds:
        above = [d for d in net_data if d["reward_remaining_r"] >= thresh]
        if len(above) >= 5:
            net_r_vals = [d["net_r"] for d in above]
            if statistics.mean(net_r_vals) > 0:
                zero_thresh = thresh
                break

    out.append(f"  2. BREAKEVEN POINT (net mean R crosses zero):")
    if zero_thresh is not None:
        out.append(f"     reward_remaining ≥ {zero_thresh}R produces positive mean net R")
    else:
        out.append(f"     No threshold produces positive mean net R")
    out.append("")

    out.append(f"  3. STABILITY ASSESSMENT:")
    out.append(f"     Sample size (real execution-period shadows): {len(data)}")
    if len(data) >= 200:
        out.append(f"     → Adequate for threshold estimation")
    elif len(data) >= 100:
        out.append(f"     → Marginal — threshold indicative but needs validation")
    else:
        out.append(f"     → Insufficient for confident threshold — treat as hypothesis")
    out.append("")

    out.append(f"  4. ECONOMICALLY MEANINGFUL?")
    if breakeven_thresh is not None:
        # How many trades pass the filter?
        passes = sum(1 for d in net_data if d["reward_remaining_r"] >= breakeven_thresh)
        out.append(f"     At threshold ≥{breakeven_thresh}R: {passes}/{len(net_data)} trades pass ({passes*100//len(net_data)}%)")
        if passes < len(net_data) * 0.1:
            out.append(f"     ⚠️ Only {passes*100//len(net_data)}% of trades pass — severe volume reduction")
        elif passes < len(net_data) * 0.5:
            out.append(f"     Moderate filter — removes {100-passes*100//len(net_data)}% of trades")
        else:
            out.append(f"     Light filter — keeps majority of trades")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/reward_remaining_threshold.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
