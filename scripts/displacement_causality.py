"""
DISPLACEMENT CAUSALITY ANALYSIS

Tests whether the displacement → R relationship is:
A. Genuinely causal/robust (structural reason why closer = better)
B. A confounded artefact (displacement correlates with symbol/time/regime)
C. A small-sample artefact (N=56 with heavy tails)

Tests applied:
1. MONOTONICITY: Is the R decay strictly monotonic with displacement?
2. CONFOUNDING: Does displacement just proxy for USDJPY/trending regime?
   - Control: same analysis EXCLUDING USDJPY
   - Control: same analysis within single symbols
3. STRUCTURAL REASONING: Does the shadow model mechanically produce worse R
   when entry is far from TP (compressed geometry)?
4. STATISTICAL ROBUSTNESS: Bootstrap confidence intervals, permutation test
5. ALTERNATIVE HYPOTHESIS: Is it actually "absolute TP distance from entry"
   that matters, not "displacement from structural zone"?
6. TIME CONFOUND: Are high-displacement orders clustered in time (same regime)?

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


def bootstrap_mean_ci(values, n_bootstrap=2000, ci=0.90):
    """Compute bootstrap confidence interval for mean."""
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
    random.seed(42)  # Reproducibility

    out = []
    out.append("=" * 80)
    out.append("DISPLACEMENT CAUSALITY ANALYSIS")
    out.append("=" * 80)
    out.append("")

    exec_results = load_execution_results()
    ctx_map = load_execution_contexts()
    shadows = load_shadow_primary()

    shadow_by_corr = {}
    for s in shadows:
        cid = s.get("correlation_id", "")
        if cid:
            shadow_by_corr[cid] = s

    # Build enriched MEAN_REVERSION dataset
    mr_records = []
    for r in exec_results:
        if r.get("pattern") != "MEAN_REVERSION":
            continue
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
        ts = r.get("timestamp_unix", 0)

        shadow_r = shadow.get("r_multiple")
        if shadow_r is None:
            continue
        if not (entry_ref and sl and tp and (ctx_ask or ctx_bid)):
            continue

        risk_distance = abs(entry_ref - sl)
        if risk_distance <= 0:
            continue

        if side == "BUY":
            displacement = (ctx_ask - entry_ref) / risk_distance
        elif side == "SELL":
            displacement = (entry_ref - ctx_bid) / risk_distance
        else:
            continue

        # Geometry compression: how much reward is left?
        # For BUY: reward_left = TP - ctx_ask (if positive, TP is above current price)
        if side == "BUY":
            reward_remaining = (tp - ctx_ask) / risk_distance
        else:
            reward_remaining = (ctx_bid - tp) / risk_distance

        mr_records.append({
            "symbol": symbol,
            "side": side,
            "displacement_r": displacement,
            "shadow_r": shadow_r,
            "reward_remaining_r": reward_remaining,
            "risk_distance": risk_distance,
            "ts": ts,
            "entry_ref": entry_ref,
            "ctx_ask": ctx_ask,
            "ctx_bid": ctx_bid,
            "sl": sl,
            "tp": tp,
        })

    out.append(f"MEAN_REVERSION enriched records: {len(mr_records)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 1: MONOTONICITY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 1: MONOTONICITY — Is R decay strictly monotonic?")
    out.append("━" * 80)
    out.append("")

    # Sort by displacement and compute rolling mean
    sorted_recs = sorted(mr_records, key=lambda r: r["displacement_r"])
    window = 10
    rolling = []
    for i in range(len(sorted_recs) - window + 1):
        chunk = sorted_recs[i:i+window]
        mean_disp = statistics.mean([r["displacement_r"] for r in chunk])
        mean_r = statistics.mean([r["shadow_r"] for r in chunk])
        rolling.append((mean_disp, mean_r))

    out.append(f"  Rolling mean (window={window}):")
    out.append(f"    {'Displacement':<13} {'Mean R'}")
    out.append(f"    {'─'*13} {'─'*8}")
    for disp, r_val in rolling[::max(1, len(rolling)//10)]:
        out.append(f"    {disp:+.2f}R{'':<7} {r_val:+.4f}")
    out.append("")

    # Check monotonicity: count inversions
    inversions = 0
    for i in range(1, len(rolling)):
        if rolling[i][1] > rolling[i-1][1] and rolling[i][0] > rolling[i-1][0]:
            inversions += 1
    monotonic_pct = (1 - inversions / max(len(rolling)-1, 1)) * 100
    out.append(f"  Monotonicity score: {monotonic_pct:.0f}% (100% = perfectly monotonic decay)")
    out.append(f"  Inversions: {inversions}/{len(rolling)-1}")
    if monotonic_pct > 70:
        out.append(f"  → SUPPORTS causality (consistent decay pattern)")
    else:
        out.append(f"  → WEAKENS causality (non-monotonic — relationship may be noisy)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 2: CONFOUNDING — SYMBOL CONTROL
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 2: CONFOUNDING — Does displacement just proxy for USDJPY?")
    out.append("━" * 80)
    out.append("")

    # USDJPY has highest displacement AND worst R. Remove it.
    no_jpy = [r for r in mr_records if r["symbol"] != "USDJPY"]
    jpy_only = [r for r in mr_records if r["symbol"] == "USDJPY"]

    out.append(f"  USDJPY: N={len(jpy_only)}, Mean displacement={statistics.mean([r['displacement_r'] for r in jpy_only]):+.2f}R, "
               f"Mean R={statistics.mean([r['shadow_r'] for r in jpy_only]):+.4f}")
    out.append(f"  Non-USDJPY: N={len(no_jpy)}, Mean displacement={statistics.mean([r['displacement_r'] for r in no_jpy]):+.2f}R, "
               f"Mean R={statistics.mean([r['shadow_r'] for r in no_jpy]):+.4f}")
    out.append("")

    # Does the displacement relationship still hold WITHOUT USDJPY?
    if no_jpy:
        low_disp_no_jpy = [r["shadow_r"] for r in no_jpy if r["displacement_r"] <= 2.0]
        high_disp_no_jpy = [r["shadow_r"] for r in no_jpy if r["displacement_r"] > 3.0]
        
        out.append(f"  Without USDJPY:")
        if low_disp_no_jpy:
            out.append(f"    Displacement ≤ 2R: N={len(low_disp_no_jpy)}, Mean R={statistics.mean(low_disp_no_jpy):+.4f}")
        if high_disp_no_jpy:
            out.append(f"    Displacement > 3R: N={len(high_disp_no_jpy)}, Mean R={statistics.mean(high_disp_no_jpy):+.4f}")
        if low_disp_no_jpy and high_disp_no_jpy:
            delta = statistics.mean(low_disp_no_jpy) - statistics.mean(high_disp_no_jpy)
            out.append(f"    Δ = {delta:+.4f}")
            if delta > 0.1:
                out.append(f"    → Relationship HOLDS without USDJPY (not purely a symbol confound)")
            else:
                out.append(f"    → Relationship WEAKENS without USDJPY (partially a symbol confound)")
    out.append("")

    # Within-symbol check (symbols with enough data)
    out.append("  Within-symbol displacement effect:")
    for sym in sorted(set(r["symbol"] for r in mr_records)):
        sym_recs = [r for r in mr_records if r["symbol"] == sym]
        if len(sym_recs) < 5:
            continue
        sym_low = [r["shadow_r"] for r in sym_recs if r["displacement_r"] <= statistics.median([r["displacement_r"] for r in sym_recs])]
        sym_high = [r["shadow_r"] for r in sym_recs if r["displacement_r"] > statistics.median([r["displacement_r"] for r in sym_recs])]
        if sym_low and sym_high:
            out.append(f"    {sym}: Low-disp R={statistics.mean(sym_low):+.4f} (N={len(sym_low)}), "
                       f"High-disp R={statistics.mean(sym_high):+.4f} (N={len(sym_high)}), "
                       f"Δ={statistics.mean(sym_low)-statistics.mean(sym_high):+.4f}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 3: MECHANICAL / STRUCTURAL EXPLANATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 3: MECHANICAL — Is displacement just 'reward remaining' by another name?")
    out.append("━" * 80)
    out.append("")

    # If displacement is high, reward_remaining is low (TP closer to or below entry)
    # This would make the relationship MECHANICAL not informational
    disps = [r["displacement_r"] for r in mr_records]
    rewards = [r["reward_remaining_r"] for r in mr_records]

    # Correlation between displacement and reward_remaining
    if len(disps) > 5:
        mean_d = statistics.mean(disps)
        mean_r = statistics.mean(rewards)
        cov = sum((d - mean_d) * (r - mean_r) for d, r in zip(disps, rewards)) / (len(disps) - 1)
        std_d = statistics.stdev(disps)
        std_r = statistics.stdev(rewards) if statistics.stdev(rewards) > 0 else 1
        corr = cov / (std_d * std_r) if (std_d > 0 and std_r > 0) else 0
        out.append(f"  Correlation(displacement, reward_remaining): {corr:.3f}")
        out.append("")

    # If reward_remaining < 0, TP is already below current price (stale)
    # If reward_remaining > 0 but small, there's little reward left
    stale_orders = [r for r in mr_records if r["reward_remaining_r"] <= 0]
    low_reward = [r for r in mr_records if 0 < r["reward_remaining_r"] <= 1.0]
    good_reward = [r for r in mr_records if r["reward_remaining_r"] > 1.0]

    out.append(f"  Reward remaining categories:")
    out.append(f"    STALE (reward ≤ 0): N={len(stale_orders)}, "
               f"Mean R={statistics.mean([r['shadow_r'] for r in stale_orders]):+.4f}" if stale_orders else "    STALE: N=0")
    out.append(f"    LOW (0 < reward ≤ 1R): N={len(low_reward)}, "
               f"Mean R={statistics.mean([r['shadow_r'] for r in low_reward]):+.4f}" if low_reward else "    LOW: N=0")
    out.append(f"    GOOD (reward > 1R): N={len(good_reward)}, "
               f"Mean R={statistics.mean([r['shadow_r'] for r in good_reward]):+.4f}" if good_reward else "    GOOD: N=0")
    out.append("")

    if corr < -0.7:
        out.append(f"  → HIGH negative correlation: displacement IS reward_remaining by another name")
        out.append(f"     The 'edge decay' is MECHANICAL: higher displacement = less reward potential")
        out.append(f"     This is a NECESSARY consequence of geometry, not a market insight")
    elif corr < -0.3:
        out.append(f"  → MODERATE correlation: displacement partially explains reward compression")
        out.append(f"     But the relationship contains SOME independent information")
    else:
        out.append(f"  → LOW correlation: displacement is independent of reward remaining")
        out.append(f"     The edge decay is NOT purely mechanical — likely genuinely informational")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 4: STATISTICAL ROBUSTNESS — Bootstrap CIs
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 4: STATISTICAL ROBUSTNESS — Bootstrap confidence intervals")
    out.append("━" * 80)
    out.append("")

    # Bootstrap CI for low vs high displacement
    low_r = [r["shadow_r"] for r in mr_records if r["displacement_r"] <= 2.0]
    high_r = [r["shadow_r"] for r in mr_records if r["displacement_r"] > 3.0]

    if low_r:
        lo_ci, hi_ci, mean = bootstrap_mean_ci(low_r)
        out.append(f"  Displacement ≤ 2R: N={len(low_r)}, Mean={mean:+.4f}, "
                   f"90% CI=[{lo_ci:+.4f}, {hi_ci:+.4f}]" if lo_ci is not None else
                   f"  Displacement ≤ 2R: N={len(low_r)}, Mean={mean:+.4f}, CI=insufficient data")
    if high_r:
        lo_ci, hi_ci, mean = bootstrap_mean_ci(high_r)
        out.append(f"  Displacement > 3R: N={len(high_r)}, Mean={mean:+.4f}, "
                   f"90% CI=[{lo_ci:+.4f}, {hi_ci:+.4f}]" if lo_ci is not None else
                   f"  Displacement > 3R: N={len(high_r)}, Mean={mean:+.4f}, CI=insufficient data")
    out.append("")

    # Do the CIs overlap?
    if low_r and high_r and len(low_r) >= 3 and len(high_r) >= 3:
        lo_ci_low, hi_ci_low, _ = bootstrap_mean_ci(low_r)
        lo_ci_high, hi_ci_high, _ = bootstrap_mean_ci(high_r)
        if hi_ci_low is not None and lo_ci_high is not None:
            overlap = hi_ci_low > lo_ci_high  # True if CIs overlap
            out.append(f"  CI overlap: {'YES' if overlap else 'NO'}")
            if not overlap:
                out.append(f"  → CIs do NOT overlap: difference is statistically robust at 90% level")
            else:
                out.append(f"  → CIs overlap: difference may not be statistically significant")
                out.append(f"     Low-disp upper bound: {hi_ci_low:+.4f}")
                out.append(f"     High-disp lower bound: {lo_ci_high:+.4f}")
    out.append("")

    # Permutation test: is the observed Δ unlikely under random assignment?
    if low_r and high_r:
        observed_delta = statistics.mean(low_r) - statistics.mean(high_r)
        combined = low_r + high_r
        n_low = len(low_r)
        n_perms = 5000
        count_extreme = 0
        for _ in range(n_perms):
            random.shuffle(combined)
            perm_low = combined[:n_low]
            perm_high = combined[n_low:]
            perm_delta = statistics.mean(perm_low) - statistics.mean(perm_high)
            if perm_delta >= observed_delta:
                count_extreme += 1
        p_value = count_extreme / n_perms
        out.append(f"  Permutation test (5000 permutations):")
        out.append(f"    Observed Δ (low - high): {observed_delta:+.4f}")
        out.append(f"    p-value: {p_value:.4f}")
        if p_value < 0.05:
            out.append(f"    → SIGNIFICANT at p<0.05: displacement effect is unlikely to be random")
        elif p_value < 0.10:
            out.append(f"    → MARGINAL (p<0.10): suggestive but not conclusive")
        else:
            out.append(f"    → NOT SIGNIFICANT: observed difference could be random")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 5: TIME CLUSTERING — Are high-displacement orders from one period?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 5: TEMPORAL CLUSTERING — Same regime period?")
    out.append("━" * 80)
    out.append("")

    # Check if high-displacement orders cluster in time
    low_ts = sorted([r["ts"] for r in mr_records if r["displacement_r"] <= 2.0 and r["ts"]])
    high_ts = sorted([r["ts"] for r in mr_records if r["displacement_r"] > 3.0 and r["ts"]])

    if low_ts:
        out.append(f"  Low-displacement period: "
                   f"{_dt.fromtimestamp(low_ts[0], tz=_tz.utc).strftime('%m-%d %H:%M')} → "
                   f"{_dt.fromtimestamp(low_ts[-1], tz=_tz.utc).strftime('%m-%d %H:%M')}")
    if high_ts:
        out.append(f"  High-displacement period: "
                   f"{_dt.fromtimestamp(high_ts[0], tz=_tz.utc).strftime('%m-%d %H:%M')} → "
                   f"{_dt.fromtimestamp(high_ts[-1], tz=_tz.utc).strftime('%m-%d %H:%M')}")

    # Do they overlap in time?
    if low_ts and high_ts:
        overlap_start = max(low_ts[0], high_ts[0])
        overlap_end = min(low_ts[-1], high_ts[-1])
        if overlap_start < overlap_end:
            out.append(f"  Temporal overlap: YES — both occur in same period")
            out.append(f"  → Displacement is NOT purely a temporal/regime artefact")
        else:
            out.append(f"  Temporal overlap: NO — different time periods")
            out.append(f"  → ⚠️ Displacement may be confounded with regime/time period")
    out.append("")

    # Day-by-day distribution
    low_dates = Counter(_dt.fromtimestamp(t, tz=_tz.utc).strftime('%m-%d') for t in low_ts)
    high_dates = Counter(_dt.fromtimestamp(t, tz=_tz.utc).strftime('%m-%d') for t in high_ts)
    all_dates = sorted(set(list(low_dates.keys()) + list(high_dates.keys())))
    if all_dates:
        out.append(f"  Day-by-day distribution:")
        out.append(f"    {'Date':<8} {'Low-disp':<10} {'High-disp'}")
        out.append(f"    {'─'*8} {'─'*10} {'─'*10}")
        for d in all_dates:
            out.append(f"    {d:<8} {low_dates.get(d,0):<10} {high_dates.get(d,0)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST 6: ALTERNATIVE HYPOTHESIS — Is it just RR compression?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("TEST 6: ALTERNATIVE — Does 'reward_remaining' predict R better than displacement?")
    out.append("━" * 80)
    out.append("")

    # Compare predictive power: displacement_r vs reward_remaining_r
    # Use rank correlation (Spearman)
    def rank_corr(x_vals, y_vals):
        n = len(x_vals)
        if n < 5:
            return 0
        x_ranks = [sorted(x_vals).index(v) for v in x_vals]
        y_ranks = [sorted(y_vals).index(v) for v in y_vals]
        mean_xr = statistics.mean(x_ranks)
        mean_yr = statistics.mean(y_ranks)
        cov = sum((xr - mean_xr) * (yr - mean_yr) for xr, yr in zip(x_ranks, y_ranks)) / (n - 1)
        std_xr = statistics.stdev(x_ranks)
        std_yr = statistics.stdev(y_ranks)
        return cov / (std_xr * std_yr) if (std_xr > 0 and std_yr > 0) else 0

    disp_vals = [r["displacement_r"] for r in mr_records]
    reward_vals = [r["reward_remaining_r"] for r in mr_records]
    r_vals = [r["shadow_r"] for r in mr_records]

    corr_disp_r = rank_corr(disp_vals, r_vals)
    corr_reward_r = rank_corr(reward_vals, r_vals)

    out.append(f"  Rank correlation (Spearman-like):")
    out.append(f"    displacement_r → shadow_r: {corr_disp_r:.3f}")
    out.append(f"    reward_remaining_r → shadow_r: {corr_reward_r:.3f}")
    out.append("")

    if abs(corr_reward_r) > abs(corr_disp_r) + 0.05:
        out.append(f"  → reward_remaining is a BETTER predictor than displacement")
        out.append(f"     Displacement's predictive power may be mediated through geometry compression")
    elif abs(corr_disp_r) > abs(corr_reward_r) + 0.05:
        out.append(f"  → displacement is a BETTER predictor than reward_remaining")
        out.append(f"     This suggests displacement captures STRUCTURAL information beyond geometry")
    else:
        out.append(f"  → Both predictors have similar strength")
        out.append(f"     Displacement and reward_remaining are partially redundant")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("FINAL VERDICT: CAUSAL vs ARTEFACT")
    out.append("=" * 80)
    out.append("")

    # Collect evidence
    evidence_for_causal = []
    evidence_for_artefact = []

    # Monotonicity
    if monotonic_pct > 70:
        evidence_for_causal.append(f"Monotonic decay ({monotonic_pct:.0f}%)")
    else:
        evidence_for_artefact.append(f"Non-monotonic ({monotonic_pct:.0f}%)")

    # Confounding
    if no_jpy:
        low_no_jpy = [r["shadow_r"] for r in no_jpy if r["displacement_r"] <= 2.0]
        high_no_jpy = [r["shadow_r"] for r in no_jpy if r["displacement_r"] > 3.0]
        if low_no_jpy and high_no_jpy:
            d = statistics.mean(low_no_jpy) - statistics.mean(high_no_jpy)
            if d > 0.1:
                evidence_for_causal.append(f"Holds without USDJPY (Δ={d:+.4f})")
            else:
                evidence_for_artefact.append(f"Weakens without USDJPY (Δ={d:+.4f})")

    # Statistical significance
    if low_r and high_r:
        if p_value < 0.05:
            evidence_for_causal.append(f"Permutation test significant (p={p_value:.4f})")
        else:
            evidence_for_artefact.append(f"Permutation test not significant (p={p_value:.4f})")

    # Mechanical explanation
    if corr < -0.7:
        evidence_for_artefact.append(f"Highly mechanical (corr={corr:.3f} with reward_remaining)")
    elif corr < -0.3:
        evidence_for_artefact.append(f"Partially mechanical (corr={corr:.3f})")
    else:
        evidence_for_causal.append(f"Not purely mechanical (corr={corr:.3f})")

    # Sample size
    if len(mr_records) < 30:
        evidence_for_artefact.append(f"Very small sample (N={len(mr_records)})")
    elif len(mr_records) < 50:
        evidence_for_artefact.append(f"Small sample (N={len(mr_records)})")

    out.append("  EVIDENCE FOR CAUSAL/ROBUST:")
    for e in evidence_for_causal:
        out.append(f"    ✓ {e}")
    out.append("")
    out.append("  EVIDENCE FOR ARTEFACT/CONFOUND:")
    for e in evidence_for_artefact:
        out.append(f"    ✗ {e}")
    out.append("")

    # Overall verdict
    causal_score = len(evidence_for_causal)
    artefact_score = len(evidence_for_artefact)
    
    out.append(f"  SCORE: Causal={causal_score}, Artefact={artefact_score}")
    out.append("")

    if causal_score > artefact_score + 1:
        out.append("  VERDICT: PROBABLY CAUSAL")
        out.append("  The displacement effect appears to reflect genuine structural degradation.")
        out.append("  However, sample size limits certainty.")
    elif artefact_score > causal_score + 1:
        out.append("  VERDICT: PROBABLY ARTEFACT")
        out.append("  The observed threshold is likely driven by confounds or sample specifics.")
    else:
        out.append("  VERDICT: MIXED / INCONCLUSIVE")
        out.append("  The relationship is real but partially mechanical and potentially confounded.")
        out.append("  Larger sample needed to confirm threshold robustness.")
    out.append("")

    out.append("  STRUCTURAL REASONING (independent of statistics):")
    out.append("  Mean-reversion SHOULD degrade with displacement because:")
    out.append("  1. The structural zone was identified as support (demand)")
    out.append("  2. When price moves far above, the zone may have been 'consumed' (filled)")
    out.append("  3. Higher displacement = price has trended = mean-reversion thesis weakens")
    out.append("  4. Geometry compression (less reward remaining) mechanically limits upside")
    out.append("  5. The broker physically cannot fill stale geometry (TP < ask)")
    out.append("")
    out.append("  This structural reasoning suggests the relationship IS real,")
    out.append("  even if the exact threshold (2R vs 3R) is sample-dependent.")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/displacement_causality_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
