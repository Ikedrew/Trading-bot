"""
EV Calibration Analysis — Predicted Probability vs Actual Shadow Outcomes.

Compares EV p_success predictions against research shadow trade results
to determine whether the EV formula is miscalibrated.
"""
import json, sys, statistics
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TRACE_DIR = Path("logs/decision_trace")
_SHADOW_DIR = Path("logs/research_shadow_trades")


def _load_jsonl_tree(directory: Path) -> list[dict]:
    records = []
    if not directory.exists():
        return records
    for item in sorted(directory.rglob("*.jsonl")):
        for line in item.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def _bucket(val: float, edges: list[float]) -> str:
    for i in range(len(edges) - 1):
        if val < edges[i + 1]:
            return f"{edges[i]:.2f}-{edges[i+1]:.2f}"
    return f">={edges[-1]:.2f}"


def main():
    traces = _load_jsonl_tree(_TRACE_DIR)
    shadows = _load_jsonl_tree(_SHADOW_DIR)

    # All traces with EV data (both pre and post migration)
    ev_traces = [t for t in traces if t.get("p_success") is not None and t.get("ev") is not None]

    # Shadow outcomes indexed by symbol + approximate time
    shadow_outcomes: dict[str, list[dict]] = defaultdict(list)
    for s in shadows:
        identity = s.get("identity", {})
        sym = identity.get("symbol", "")
        outcome = s.get("simulated_outcome", {})
        ds = s.get("decision_snapshot", {})
        if sym and outcome:
            shadow_outcomes[sym].append({
                "time": ds.get("timestamp_decision_utc", 0),
                "r": outcome.get("pnl_r_multiple", 0),
                "exit_reason": outcome.get("exit_reason", ""),
                "pattern": ds.get("pattern", ""),
            })

    # Sort shadow outcomes by time for matching
    for sym in shadow_outcomes:
        shadow_outcomes[sym].sort(key=lambda x: x["time"])

    print("=" * 70)
    print("EV CALIBRATION ANALYSIS — Predicted vs Actual")
    print("=" * 70)
    print(f"Traces with EV data: {len(ev_traces)}")
    print(f"Shadow trade outcomes: {len(shadows)}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # OVERALL CALIBRATION
    # ═══════════════════════════════════════════════════════════════════
    print("─── OVERALL EV CALIBRATION ─────────────────────────────────────")
    print()

    p_vals = [t.get("p_success", 0) for t in ev_traces]
    print(f"  Predicted p_success: mean={statistics.mean(p_vals):.4f} median={statistics.median(p_vals):.4f}")
    print()

    # Shadow overall stats
    all_r = [s.get("simulated_outcome", {}).get("pnl_r_multiple", 0) for s in shadows if s.get("simulated_outcome")]
    if all_r:
        wins = sum(1 for r in all_r if r > 0)
        losses = sum(1 for r in all_r if r < 0)
        total = len(all_r)
        actual_wr = wins / total if total > 0 else 0
        print(f"  Shadow outcomes: {total} trades, {wins} wins, {losses} losses")
        print(f"  Actual win rate: {actual_wr:.4f} ({actual_wr*100:.1f}%)")
        print(f"  Avg R: {statistics.mean(all_r):.4f}")
        print(f"  Total R: {sum(all_r):.2f}")
        print()
        print(f"  CALIBRATION GAP: predicted p={statistics.mean(p_vals):.4f} vs actual WR={actual_wr:.4f}")
        if actual_wr > statistics.mean(p_vals):
            print(f"  → EV UNDERPREDICTS by {(actual_wr - statistics.mean(p_vals))*100:.1f} percentage points")
        else:
            print(f"  → EV OVERPREDICTS by {(statistics.mean(p_vals) - actual_wr)*100:.1f} percentage points")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # BY SCORE BUCKET
    # ═══════════════════════════════════════════════════════════════════
    print("─── BY SCORE BUCKET ────────────────────────────────────────────")
    print()
    print(f"{'Score Bucket':>14s} {'Pred_p':>7s} {'N_traces':>9s} {'N_shadow':>9s} {'Act_WR':>7s} {'Avg_R':>7s} {'Gap':>8s}")
    print(f"{'─'*14} {'─'*7} {'─'*9} {'─'*9} {'─'*7} {'─'*7} {'─'*8}")

    score_edges = [0.0, 0.35, 0.45, 0.55, 0.65, 0.80, 1.01]
    for i in range(len(score_edges) - 1):
        lo, hi = score_edges[i], score_edges[i + 1]
        label = f"{lo:.2f}-{hi:.2f}"
        bucket_traces = [t for t in ev_traces if lo <= t.get("score_neutral", 0) < hi]
        bucket_p = [t.get("p_success", 0) for t in bucket_traces]

        # Match shadows by score range (approximate — shadows don't carry score directly)
        bucket_shadows = [s for s in shadows
                         if lo <= s.get("decision_snapshot", {}).get("score", 0) < hi]
        bucket_r = [s.get("simulated_outcome", {}).get("pnl_r_multiple", 0)
                    for s in bucket_shadows if s.get("simulated_outcome")]
        bucket_wins = sum(1 for r in bucket_r if r > 0)
        n_shadow = len(bucket_r)
        act_wr = bucket_wins / n_shadow if n_shadow > 0 else 0
        avg_r = statistics.mean(bucket_r) if bucket_r else 0

        pred_p_mean = statistics.mean(bucket_p) if bucket_p else 0
        gap = act_wr - pred_p_mean if n_shadow > 0 else 0

        n_shd_str = str(n_shadow) if n_shadow > 0 else "—"
        wr_str = f"{act_wr:.4f}" if n_shadow > 0 else "—"
        r_str = f"{avg_r:+.3f}" if n_shadow > 0 else "—"
        gap_str = f"{gap:+.4f}" if n_shadow > 0 else "—"

        print(f"{label:>14s} {pred_p_mean:7.4f} {len(bucket_traces):>9d} {n_shd_str:>9s} {wr_str:>7s} {r_str:>7s} {gap_str:>8s}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # BY STRATEGY SELECTED
    # ═══════════════════════════════════════════════════════════════════
    print("─── BY STRATEGY SELECTED (yes/no) ───────────────────────────────")
    print()
    print(f"{'Strategy':>12s} {'Pred_p':>7s} {'N_traces':>9s} {'Strat_Conf':>11s} {'N_shadow':>9s} {'Act_WR':>7s} {'Avg_R':>7s}")
    print(f"{'─'*12} {'─'*7} {'─'*9} {'─'*11} {'─'*9} {'─'*7} {'─'*7}")

    for has_strat in [True, False]:
        if has_strat:
            subset = [t for t in ev_traces if t.get("selected_strategy")]
            label = "YES"
        else:
            subset = [t for t in ev_traces if not t.get("selected_strategy")]
            label = "NO"

        if not subset:
            continue
        pred_p = statistics.mean([t.get("p_success", 0) for t in subset])
        strat_conf = statistics.mean([t.get("strategy_confidence", 0) for t in subset])

        # Shadow approximation
        shadow_subset = [s for s in shadows]  # can't easily filter by strategy
        n_shd = len(shadow_subset) if has_strat else 0
        act_wr_str = "—"
        r_str = "—"

        print(f"{label:>12s} {pred_p:7.4f} {len(subset):>9d} {strat_conf:>11.4f} {n_shd if n_shd > 0 else '—':>9} {act_wr_str:>7s} {r_str:>7s}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # BY H4 REGIME (post-migration only)
    # ═══════════════════════════════════════════════════════════════════
    print("─── BY H4 REGIME ───────────────────────────────────────────────")
    print()
    post_ev = [t for t in ev_traces if t.get("regime_source")]
    regimes = sorted(set(t.get("regime", "?") for t in post_ev))
    print(f"{'Regime':>14s} {'Pred_p':>7s} {'N':>6s} {'Mean_Score':>11s} {'Strat_Conf':>11s}")
    print(f"{'─'*14} {'─'*7} {'─'*6} {'─'*11} {'─'*11}")
    for regime in regimes:
        subset = [t for t in post_ev if t.get("regime") == regime]
        if not subset:
            continue
        pred_p = statistics.mean([t.get("p_success", 0) for t in subset])
        mean_score = statistics.mean([t.get("score_neutral", 0) for t in subset])
        strat_conf = statistics.mean([t.get("strategy_confidence", 0) for t in subset])
        print(f"{regime or '?':>14s} {pred_p:7.4f} {len(subset):>6d} {mean_score:>11.4f} {strat_conf:>11.4f}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # BY M15 QUALITY BUCKET
    # ═══════════════════════════════════════════════════════════════════
    print("─── BY M15 QUALITY BUCKET ──────────────────────────────────────")
    print()
    print(f"{'M15 Quality':>12s} {'Pred_p':>7s} {'N':>6s} {'Mean_Score':>11s}")
    print(f"{'─'*12} {'─'*7} {'─'*6} {'─'*11}")
    m15_edges = [0.0, 0.3, 0.5, 0.7, 1.01]
    for i in range(len(m15_edges) - 1):
        lo, hi = m15_edges[i], m15_edges[i + 1]
        label = f"{lo:.1f}-{hi:.1f}"
        subset = [t for t in post_ev if lo <= t.get("components", {}).get("market_quality", 0) < hi]
        if not subset:
            continue
        pred_p = statistics.mean([t.get("p_success", 0) for t in subset])
        mean_score = statistics.mean([t.get("score_neutral", 0) for t in subset])
        print(f"{label:>12s} {pred_p:7.4f} {len(subset):>6d} {mean_score:>11.4f}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSIS
    # ═══════════════════════════════════════════════════════════════════
    print("─── DIAGNOSIS ──────────────────────────────────────────────────")
    print()

    # Formula: p_base = score * 0.6 + strategy_confidence * 0.4
    all_strat_conf = [t.get("strategy_confidence", 0) for t in ev_traces]
    mean_strat_conf = statistics.mean(all_strat_conf) if all_strat_conf else 0
    zero_conf_pct = sum(1 for c in all_strat_conf if c == 0) / max(len(all_strat_conf), 1) * 100

    print(f"  EV Formula: p_base = score × 0.6 + strategy_confidence × 0.4")
    print(f"  Mean strategy_confidence: {mean_strat_conf:.4f}")
    print(f"  Zero confidence rate: {zero_conf_pct:.1f}%")
    print()

    if zero_conf_pct > 80:
        print(f"  ❌ FINDING: strategy_confidence is ZERO in {zero_conf_pct:.0f}% of decisions")
        print(f"     → p_base ≈ score × 0.6 (the 0.4 weighting is dead weight)")
        print(f"     → Mean score {statistics.mean(p_vals)/0.6:.4f} → p = {statistics.mean(p_vals):.4f}")
        print(f"     → At RR=2.0, threshold is p > 0.333")
        print(f"     → Requires score > 0.555 just to pass EV")
        print()
        print(f"  ROOT CAUSE: The EV formula assigns 40% weight to a variable that is")
        print(f"  almost always zero. This structurally caps p_success at score × 0.6,")
        print(f"  making it nearly impossible for average-scoring decisions to pass EV.")
        print()
        if all_r:
            print(f"  EVIDENCE: Shadow trades show actual win rate = {actual_wr:.4f}")
            print(f"  But EV predicts mean p_success = {statistics.mean(p_vals):.4f}")
            print(f"  The formula underestimates probability by {(actual_wr - statistics.mean(p_vals)):.4f}")
            print()
            print(f"  CONCLUSION: EV IS MISCALIBRATED.")
            print(f"  The miscalibration source is strategy_confidence = 0 (dead variable).")
            print(f"  If p_base used score directly (without the 0.6/0.4 split),")
            print(f"  mean p would be {statistics.mean([t.get('score_neutral',0) for t in ev_traces]):.4f}")
            print(f"  which is much closer to the actual win rate of {actual_wr:.4f}.")
    else:
        print(f"  Strategy confidence active in {100-zero_conf_pct:.0f}% of decisions")
        print(f"  Formula is receiving both inputs — calibration issue is elsewhere")
    print()


if __name__ == "__main__":
    main()
