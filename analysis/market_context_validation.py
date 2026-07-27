"""
Market Context Phase 1 — Validation Report Generator.

Reads existing persisted data (decision_trace, decision_ledger, market_context)
to assess whether MarketContext produces meaningful separation.

Does NOT modify any code or data.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_jsonl(directory: Path) -> list[dict]:
    """Load all JSONL records from a directory tree."""
    records = []
    if not directory.exists():
        return records
    for sym_dir in sorted(directory.iterdir()):
        if not sym_dir.is_dir():
            continue
        for f in sorted(sym_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        pass
    return records


def load_flat_jsonl(directory: Path) -> list[dict]:
    """Load JSONL from flat directory (no symbol subdirs)."""
    records = []
    if not directory.exists():
        return records
    for f in sorted(directory.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
    return records


def main():
    root = Path(".")

    print("=" * 70)
    print("MARKET CONTEXT PHASE 1 — VALIDATION REPORT")
    print("=" * 70)
    print()

    # ─── 1. LOAD DATA ────────────────────────────────────────────────────────
    traces = load_jsonl(root / "logs" / "decision_trace")
    mc_records = load_jsonl(root / "logs" / "market_context")

    print(f"Data sources loaded:")
    print(f"  Decision traces: {len(traces)} records")
    print(f"  Market context:  {len(mc_records)} records")
    print(f"  Symbols (traces): {sorted(set(t.get('symbol', '?') for t in traces))}")
    print()

    # ─── 2. MARKET CONTEXT OUTPUT DISTRIBUTION ────────────────────────────────
    print("=" * 70)
    print("SECTION 1: MARKET CONTEXT OUTPUT DISTRIBUTION")
    print("=" * 70)
    print()

    if mc_records:
        # H4 regime frequency
        h4_regimes = Counter(r.get("h4", {}).get("regime", "?") for r in mc_records)
        print("--- H4 Regime Frequency (from MarketContext) ---")
        total = sum(h4_regimes.values())
        for regime, count in h4_regimes.most_common():
            print(f"  {regime:25s}: {count:4d} ({count/total*100:.1f}%)")
        print()

        # Direction frequency
        directions = Counter(r.get("direction", "?") for r in mc_records)
        print("--- Unified Direction (from MarketContext) ---")
        total = sum(directions.values())
        for d, c in directions.most_common():
            print(f"  {d:15s}: {c:4d} ({c/total*100:.1f}%)")
        print()

        # Phase frequency
        phases = Counter(r.get("phase", "?") for r in mc_records)
        print("--- Market Phase (from MarketContext) ---")
        total = sum(phases.values())
        for p, c in phases.most_common():
            print(f"  {p:20s}: {c:4d} ({c/total*100:.1f}%)")
        print()

        # M15 setup quality distribution
        m15_quals = [r.get("m15", {}).get("quality_score", 0.0) for r in mc_records]
        if m15_quals:
            print("--- M15 Setup Quality Distribution ---")
            print(f"  Count: {len(m15_quals)}")
            print(f"  Mean:  {statistics.mean(m15_quals):.4f}")
            print(f"  Median:{statistics.median(m15_quals):.4f}")
            print(f"  Min:   {min(m15_quals):.4f}")
            print(f"  Max:   {max(m15_quals):.4f}")
            # Bucket distribution
            buckets = Counter()
            for q in m15_quals:
                if q < 0.3:
                    buckets["LOW (0-0.3)"] += 1
                elif q < 0.6:
                    buckets["MED (0.3-0.6)"] += 1
                else:
                    buckets["HIGH (0.6-1.0)"] += 1
            for b, c in sorted(buckets.items()):
                print(f"    {b}: {c} ({c/len(m15_quals)*100:.0f}%)")
            print()

        # Conflict detection
        conflicts = sum(1 for r in mc_records if r.get("conflict_detected"))
        print(f"--- Direction Conflicts ---")
        print(f"  Total records: {len(mc_records)}")
        print(f"  Conflicts detected: {conflicts} ({conflicts/len(mc_records)*100:.1f}%)")
        conflict_descs = [r.get("conflict_description", "") for r in mc_records if r.get("conflict_detected")]
        if conflict_descs:
            desc_counts = Counter(conflict_descs)
            print(f"  Conflict types:")
            for desc, c in desc_counts.most_common(5):
                print(f"    {desc}: {c}")
        print()

        # Tradability distribution
        tradability = [r.get("tradability_score", 0.0) for r in mc_records]
        if tradability:
            print("--- Tradability Score Distribution ---")
            print(f"  Mean:  {statistics.mean(tradability):.4f}")
            print(f"  Median:{statistics.median(tradability):.4f}")
            buckets = Counter()
            for t in tradability:
                if t < 0.3:
                    buckets["LOW (0-0.3)"] += 1
                elif t < 0.6:
                    buckets["MED (0.3-0.6)"] += 1
                else:
                    buckets["HIGH (0.6-1.0)"] += 1
            for b, c in sorted(buckets.items()):
                print(f"    {b}: {c} ({c/len(tradability)*100:.0f}%)")
            print()
    else:
        print("  ⚠ NO MARKET CONTEXT DATA — system has not run since Phase 1 deployment")
        print("  Using decision_trace data for equivalent analysis...")
        print()

    # ─── 3. EXISTING ENGINE INTERPRETATION (from decision traces) ─────────────
    print("=" * 70)
    print("SECTION 2: EXISTING ENGINE INTERPRETATION (decision_trace)")
    print("=" * 70)
    print()

    if traces:
        # Regime distribution (strategy_activation)
        regimes = Counter(t.get("regime") for t in traces if t.get("regime"))
        print("--- M5 Regime (strategy_activation._detect_regime) ---")
        total = sum(regimes.values())
        for r, c in regimes.most_common():
            print(f"  {r:20s}: {c:5d} ({c/total*100:.1f}%)")
        print()

        # Market state
        mstates = Counter(t.get("market_state") for t in traces if t.get("market_state"))
        print("--- Market State (MarketStateEngine) ---")
        total = sum(mstates.values())
        for ms, c in mstates.most_common():
            print(f"  {ms:20s}: {c:5d} ({c/total*100:.1f}%)")
        print()

        # HTF alignment scores
        htf_vals = [t.get("htf_alignment") for t in traces if t.get("htf_alignment") is not None]
        h4_vals = [t.get("h4_alignment") for t in traces if t.get("h4_alignment") is not None]
        print("--- HTF Alignment Score Distribution ---")
        if htf_vals:
            print(f"  htf_alignment (n={len(htf_vals)}): mean={statistics.mean(htf_vals):.4f} median={statistics.median(htf_vals):.4f} std={statistics.stdev(htf_vals):.4f}")
        if h4_vals:
            print(f"  h4_alignment  (n={len(h4_vals)}): mean={statistics.mean(h4_vals):.4f} median={statistics.median(h4_vals):.4f} std={statistics.stdev(h4_vals):.4f}")
        print()

        # Terminal stage distribution
        stages = Counter(t.get("terminal_stage") for t in traces if t.get("terminal_stage"))
        print("--- Terminal Stage (where decisions stop) ---")
        total = sum(stages.values())
        for s, c in stages.most_common():
            print(f"  {s:25s}: {c:5d} ({c/total*100:.1f}%)")
        print()

        # Score distributions
        neutrals = [t.get("score_neutral") for t in traces if t.get("score_neutral") is not None and t.get("score_neutral") > 0]
        strategies = [t.get("score_strategy") for t in traces if t.get("score_strategy") is not None and t.get("score_strategy") > 0]
        print("--- Score Distribution (decisions with patterns) ---")
        if neutrals:
            print(f"  score_neutral  (n={len(neutrals)}): mean={statistics.mean(neutrals):.4f} median={statistics.median(neutrals):.4f}")
        if strategies:
            print(f"  score_strategy (n={len(strategies)}): mean={statistics.mean(strategies):.4f} median={statistics.median(strategies):.4f}")
        print()

        # Component breakdown (where available)
        components_list = [t.get("components", {}) for t in traces if t.get("components")]
        if components_list:
            print("--- Average Component Scores (where patterns detected) ---")
            all_keys = set()
            for c in components_list:
                all_keys.update(c.keys())
            for key in sorted(all_keys):
                vals = [c.get(key, 0.0) for c in components_list if key in c]
                if vals:
                    print(f"  {key:20s}: mean={statistics.mean(vals):.4f} (n={len(vals)})")
            print()

    # ─── 4. ANALYSIS: MEANINGFUL SEPARATION ───────────────────────────────────
    print("=" * 70)
    print("SECTION 3: ANALYSIS — MEANINGFUL SEPARATION & DUPLICATION")
    print("=" * 70)
    print()

    if traces:
        # Check if regime adds information beyond what HTF already provides
        # Compare: decisions where H4 aligned vs not aligned → score difference
        high_h4 = [t for t in traces if (t.get("h4_alignment") or 0) >= 0.6]
        low_h4 = [t for t in traces if (t.get("h4_alignment") or 0) <= 0.3 and (t.get("h4_alignment") or 0) > 0]

        print("--- H4 Alignment Impact on Scores ---")
        if high_h4:
            high_scores = [t.get("score_neutral", 0) for t in high_h4 if t.get("score_neutral")]
            print(f"  High H4 alignment (>=0.6): n={len(high_h4)}, avg_score={statistics.mean(high_scores):.4f}" if high_scores else f"  High H4 alignment (>=0.6): n={len(high_h4)}")
        if low_h4:
            low_scores = [t.get("score_neutral", 0) for t in low_h4 if t.get("score_neutral")]
            print(f"  Low H4 alignment  (<=0.3): n={len(low_h4)}, avg_score={statistics.mean(low_scores):.4f}" if low_scores else f"  Low H4 alignment  (<=0.3): n={len(low_h4)}")
        print()

        # Regime vs terminal stage — does regime predict where decisions stop?
        print("--- Regime × Terminal Stage (does regime predict pipeline exit?) ---")
        regime_stage = defaultdict(Counter)
        for t in traces:
            r = t.get("regime", "UNKNOWN")
            s = t.get("terminal_stage", "unknown")
            if r and s:
                regime_stage[r][s] += 1
        for regime in sorted(regime_stage.keys()):
            total = sum(regime_stage[regime].values())
            top3 = regime_stage[regime].most_common(3)
            top_str = ", ".join(f"{s}={c}/{total}" for s, c in top3)
            print(f"  {regime:15s} (n={total:4d}): {top_str}")
        print()

        # M5 regime overlap with H4
        # Look for cases where strategy_activation regime disagrees with H4-based regime
        print("--- M5 Regime vs H4 Regime (duplication analysis) ---")
        regime_pairs = []
        for t in traces:
            m5_regime = t.get("regime", "")
            # H4 regime is not directly stored in trace, but h4_alignment indicates it
            # h4_alignment > 0.5 → H4 aligned (likely trending in same direction)
            # h4_alignment < 0.3 → H4 contra or ranging
            h4_align = t.get("h4_alignment", 0.5)
            if m5_regime and h4_align is not None:
                regime_pairs.append((m5_regime, h4_align))

        if regime_pairs:
            # Group by M5 regime, show H4 alignment distribution
            by_m5 = defaultdict(list)
            for m5r, h4a in regime_pairs:
                by_m5[m5r].append(h4a)
            for m5r in sorted(by_m5.keys()):
                vals = by_m5[m5r]
                print(f"  M5={m5r:15s}: n={len(vals):4d}, h4_alignment mean={statistics.mean(vals):.4f}")
        print()

    # ─── 5. MIGRATION EXPECTANCY IMPACT ───────────────────────────────────────
    print("=" * 70)
    print("SECTION 4: MIGRATION EXPECTANCY IMPACT ASSESSMENT")
    print("=" * 70)
    print()

    if traces:
        # Identify which components have highest weight × variance → most impact if migrated
        components_list = [t.get("components", {}) for t in traces if t.get("components")]
        if components_list:
            weights = {
                "pattern_quality": 0.14,
                "bias_alignment": 0.18,
                "market_quality": 0.08,
                "trend_alignment": 0.10,
                "chop_clarity": 0.06,
                "volatility_quality": 0.07,
                "bias_stability": 0.07,
                "confirmation_pre": 0.06,
                "htf_alignment": 0.14,
                "h4_alignment": 0.10,
            }
            print("--- Component Variance × Weight (migration impact potential) ---")
            impacts = []
            for key in sorted(weights.keys()):
                vals = [c.get(key, 0.0) for c in components_list if key in c]
                if len(vals) > 2:
                    var = statistics.variance(vals)
                    w = weights.get(key, 0.0)
                    impact = var * w
                    impacts.append((key, impact, var, w, statistics.mean(vals)))

            impacts.sort(key=lambda x: -x[1])
            print(f"  {'Component':<22s} {'Impact':>8s} {'Variance':>10s} {'Weight':>8s} {'Mean':>8s}")
            print(f"  {'-'*22} {'-'*8} {'-'*10} {'-'*8} {'-'*8}")
            for key, impact, var, w, mean in impacts:
                print(f"  {key:<22s} {impact:8.5f} {var:10.5f} {w:8.2f} {mean:8.4f}")
            print()
            print("  INTERPRETATION:")
            print(f"  Highest impact migration: {impacts[0][0]} (migrating this component")
            print(f"  from M5 to its proper timeframe would change the most decisions)")
            print()

    # ─── 6. SUMMARY ──────────────────────────────────────────────────────────
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    print("1. MarketContext data availability:")
    if mc_records:
        print(f"   ✅ {len(mc_records)} records persisted — system is producing output")
    else:
        print("   ⚠  No live MarketContext data yet — system has not run since Phase 1 deploy")
        print("   → Run the trading bot to generate MarketContext persistence")
    print()
    print(f"2. Decision trace data: {len(traces)} records across {len(set(t.get('symbol','?') for t in traces))} symbols")
    print()
    if traces:
        print("3. Key findings:")
        # Regime concentration
        regime_top = regimes.most_common(1)[0] if regimes else ("?", 0)
        print(f"   - Regime is {regime_top[1]/sum(regimes.values())*100:.0f}% {regime_top[0]} (low variance → limited separation)")
        # H4 alignment neutral
        if h4_vals:
            h4_at_05 = sum(1 for v in h4_vals if 0.4 <= v <= 0.6) / len(h4_vals) * 100
            print(f"   - H4 alignment is neutral (0.4-0.6) in {h4_at_05:.0f}% of decisions")
        # Suggested migration
        if impacts:
            print(f"   - Highest-impact migration target: {impacts[0][0]} (variance×weight = {impacts[0][1]:.5f})")
    print()


if __name__ == "__main__":
    main()
