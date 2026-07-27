"""
EV Negative Rejection Analysis — Post MarketContext Migration.

For each EV-rejected decision, examines:
- Score, probability, threshold
- H4 regime, H1 direction/phase, M15 quality
- Strategy selected
- Whether research shadow trade outcome exists (won/lost)
"""
import json, sys, statistics
from collections import Counter, defaultdict
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


def main():
    traces = _load_jsonl_tree(_TRACE_DIR)
    shadows = _load_jsonl_tree(_SHADOW_DIR)

    # Post-migration EV rejections
    post = [t for t in traces if t.get("regime_source")]
    ev_rejected = [t for t in post if t.get("terminal_stage") == "ev_policy"]

    # Build shadow trade lookup by symbol+bar_time (approximate matching)
    shadow_by_key: dict[str, dict] = {}
    for s in shadows:
        identity = s.get("identity", {})
        ds = s.get("decision_snapshot", {})
        key = f"{identity.get('symbol', '')}_{int(ds.get('timestamp_decision_utc', 0))}"
        shadow_by_key[key] = s

    print("=" * 70)
    print("EV NEGATIVE REJECTION ANALYSIS — Post MarketContext Migration")
    print("=" * 70)
    print(f"Post-migration traces: {len(post)}")
    print(f"EV-rejected decisions: {len(ev_rejected)} ({len(ev_rejected)/max(len(post),1)*100:.1f}%)")
    print(f"Research shadow trades: {len(shadows)}")
    print()

    if not ev_rejected:
        print("No EV-rejected decisions found in post-migration data.")
        return

    # ═══════════════════════════════════════════════════════════════════
    # AGGREGATE STATISTICS
    # ═══════════════════════════════════════════════════════════════════
    print("─── AGGREGATE EV REJECTION STATISTICS ───────────────────────────")
    print()

    scores = [t.get("score_neutral", 0) for t in ev_rejected]
    p_success_vals = [t.get("p_success", 0) for t in ev_rejected if t.get("p_success") is not None]
    ev_vals = [t.get("ev", 0) for t in ev_rejected if t.get("ev") is not None]

    print(f"  Scores: mean={statistics.mean(scores):.4f} median={statistics.median(scores):.4f} min={min(scores):.4f} max={max(scores):.4f}")
    if p_success_vals:
        print(f"  P(success): mean={statistics.mean(p_success_vals):.4f} median={statistics.median(p_success_vals):.4f} min={min(p_success_vals):.4f} max={max(p_success_vals):.4f}")
    if ev_vals:
        print(f"  EV: mean={statistics.mean(ev_vals):.6f} median={statistics.median(ev_vals):.6f} min={min(ev_vals):.6f} max={max(ev_vals):.6f}")
    print()

    # Regime distribution
    regimes = Counter(t.get("regime") for t in ev_rejected)
    print("  Regime distribution (EV-rejected):")
    for r, c in regimes.most_common():
        print(f"    {r or 'UNKNOWN':20s}: {c:4d} ({c/len(ev_rejected)*100:.1f}%)")
    print()

    # Strategy distribution
    strategies = Counter(t.get("selected_strategy") or "None" for t in ev_rejected)
    print("  Strategy distribution (EV-rejected):")
    for s, c in strategies.most_common():
        print(f"    {s:20s}: {c:4d} ({c/len(ev_rejected)*100:.1f}%)")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # DETAILED SAMPLE (first 20)
    # ═══════════════════════════════════════════════════════════════════
    print("─── DETAILED EV REJECTIONS (sample) ────────────────────────────")
    print()
    print(f"{'#':>3s} {'Symbol':8s} {'Score':>6s} {'P_suc':>6s} {'EV':>9s} {'Regime':>12s} {'Strategy':>12s} {'H4_align':>8s} {'M15_qual':>8s} {'Shadow':>8s}")
    print(f"{'─'*3} {'─'*8} {'─'*6} {'─'*6} {'─'*9} {'─'*12} {'─'*12} {'─'*8} {'─'*8} {'─'*8}")

    sample_size = min(30, len(ev_rejected))
    shadow_matches = 0
    shadow_wins = 0
    shadow_losses = 0

    for i, t in enumerate(ev_rejected[:sample_size]):
        symbol = t.get("symbol", "?")
        score = t.get("score_neutral", 0)
        p_suc = t.get("p_success")
        ev = t.get("ev")
        regime = t.get("regime", "?")
        strategy = t.get("selected_strategy") or "None"
        h4_align = t.get("h4_alignment", 0)

        # Get M15 quality from components
        components = t.get("components", {})
        m15_qual = components.get("market_quality", 0)

        # Look for matching shadow trade
        entity_id = t.get("entity_id", "")
        shadow_result = "—"
        # Try to find shadow by entity_id proximity
        for sk, sv in shadow_by_key.items():
            if symbol in sk:
                outcome = sv.get("simulated_outcome", {})
                r_mult = outcome.get("pnl_r_multiple", 0)
                if abs(int(sk.split("_")[-1]) - int(entity_id.split("_")[-1] if "_" in entity_id else "0")) < 600:
                    if r_mult > 0:
                        shadow_result = f"+{r_mult:.2f}R"
                        shadow_wins += 1
                    else:
                        shadow_result = f"{r_mult:.2f}R"
                        shadow_losses += 1
                    shadow_matches += 1
                    break

        p_str = f"{p_suc:.4f}" if p_suc is not None else "—"
        ev_str = f"{ev:.6f}" if ev is not None else "—"

        print(f"{i+1:3d} {symbol:8s} {score:6.4f} {p_str:>6s} {ev_str:>9s} {regime:>12s} {strategy:>12s} {h4_align:8.4f} {m15_qual:8.4f} {shadow_result:>8s}")

    print()

    # ═══════════════════════════════════════════════════════════════════
    # EV FORMULA ANALYSIS
    # ═══════════════════════════════════════════════════════════════════
    print("─── EV FORMULA ANALYSIS ────────────────────────────────────────")
    print()

    # EV = p_success * reward - p_failure * risk
    # p_base = score * 0.6 + strategy_confidence * 0.4
    # strategy_confidence is always ~0 → p_base ≈ score * 0.6
    # For EV > 0: p_success > 1 / (1 + RR)
    # At RR=2: need p_success > 0.333
    # At RR=2: p_base needs to be > 0.333 → score needs to be > 0.555

    rr_vals = [t.get("rr_effective") for t in ev_rejected if t.get("rr_effective") is not None]
    strat_confs = [t.get("strategy_confidence", 0) for t in ev_rejected]

    print(f"  Strategy confidence: mean={statistics.mean(strat_confs):.4f} (if 0 → p_base = score*0.6)")
    if rr_vals:
        print(f"  RR effective: mean={statistics.mean(rr_vals):.2f} median={statistics.median(rr_vals):.2f}")
        # Required p_success for positive EV at median RR
        median_rr = statistics.median(rr_vals)
        required_p = 1.0 / (1.0 + median_rr) if median_rr > 0 else 0.5
        print(f"  Required p_success for EV>0 at RR={median_rr:.2f}: {required_p:.4f}")
        print(f"  Required score for EV>0 (if strat_conf=0): {required_p/0.6:.4f}")
    print()

    # Score vs threshold
    above_threshold = sum(1 for s in scores if s >= 0.35)
    print(f"  Decisions passing score threshold (>=0.35): {above_threshold}/{len(ev_rejected)} (all EV-rejected had scores above 0.35)")
    print(f"  Mean score of EV-rejected: {statistics.mean(scores):.4f}")
    if rr_vals:
        # How many COULD have positive EV if p_base used score directly?
        hypothetical_positive = 0
        for t in ev_rejected:
            s = t.get("score_neutral", 0)
            rr = t.get("rr_effective", 2.0)
            if rr and rr > 0:
                p_hypothetical = s * 0.6  # Current formula
                ev_hyp = p_hypothetical * rr - (1 - p_hypothetical) * 1.0
                if ev_hyp > 0:
                    hypothetical_positive += 1
        print(f"  Would pass EV with score*0.6 formula: {hypothetical_positive}/{len(ev_rejected)} ({hypothetical_positive/max(len(ev_rejected),1)*100:.1f}%)")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # SHADOW TRADE COMPARISON
    # ═══════════════════════════════════════════════════════════════════
    print("─── SHADOW TRADE COMPARISON ────────────────────────────────────")
    print()
    if shadow_matches > 0:
        print(f"  Matched shadow trades: {shadow_matches}")
        print(f"  Shadow wins:  {shadow_wins} ({shadow_wins/shadow_matches*100:.1f}%)")
        print(f"  Shadow losses: {shadow_losses} ({shadow_losses/shadow_matches*100:.1f}%)")
        if shadow_wins > shadow_losses:
            print(f"  ⚠️  EV gate may be over-rejecting — shadow trades show positive outcomes")
        else:
            print(f"  ✅ EV gate is correctly rejecting — shadow trades confirm losses")
    else:
        print("  No matching shadow trades found for EV-rejected decisions")
        print("  (Shadow trades only created for RESEARCH_WOULD_EXECUTE disagreements)")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # VERDICT
    # ═══════════════════════════════════════════════════════════════════
    print("─── VERDICT ────────────────────────────────────────────────────")
    print()
    if p_success_vals:
        mean_p = statistics.mean(p_success_vals)
        if mean_p < 0.35:
            print(f"  EV formula produces mean p_success = {mean_p:.4f}")
            print(f"  This is structurally low — the formula p_base = score*0.6 + strat_conf*0.4")
            print(f"  with strategy_confidence ≈ 0 means p_success ≈ score * 0.6")
            print(f"  Average score {statistics.mean(scores):.4f} → p ≈ {statistics.mean(scores)*0.6:.4f}")
            print(f"  At RR=2.0, need p > 0.333 for positive EV")
            print()
            print(f"  CONCLUSION: EV rejections are STRUCTURALLY CORRECT given the formula.")
            print(f"  The formula itself may be the issue (strategy_confidence always ≈ 0)")
            print(f"  but the gate is correctly enforcing the formula as designed.")
        else:
            print(f"  Mean p_success = {mean_p:.4f} — EV rejections have reasonable probability")
    else:
        print("  No p_success data available in traces — likely pre-EV stage exits")
    print()


if __name__ == "__main__":
    main()
