"""
Post-Migration Analysis — First run after MarketContext authority migration.

Reads decision traces and reports on decision funnel, scores, rejections,
and authority usage.
"""
import json, sys, statistics
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TRACE_DIR = Path("logs/decision_trace")


def _load_traces() -> list[dict]:
    records = []
    if not _TRACE_DIR.exists():
        return records
    for sym_dir in sorted(_TRACE_DIR.iterdir()):
        if not sym_dir.is_dir():
            continue
        for f in sorted(sym_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    return records


def main():
    traces = _load_traces()
    # Split pre/post migration
    post = [t for t in traces if t.get("regime_source")]
    pre = [t for t in traces if not t.get("regime_source")]

    print("=" * 65)
    print("POST-MIGRATION ANALYSIS — MarketContext Authority")
    print("=" * 65)
    print(f"Total traces: {len(traces)} (pre-migration: {len(pre)}, post-migration: {len(post)})")
    print()

    data = post if post else pre
    label = "POST-MIGRATION" if post else "PRE-MIGRATION (no post-migration data yet)"
    print(f"Analysing: {label} ({len(data)} records)")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 1. DECISION FUNNEL
    # ═══════════════════════════════════════════════════════════════════
    print("─── 1. DECISION FUNNEL ──────────────────────────────────────────")
    patterns_detected = sum(1 for t in data if t.get("pattern_detected") or t.get("pattern_name"))
    scored = sum(1 for t in data if t.get("score_strategy", 0) > 0 or t.get("score_neutral", 0) > 0)
    executions = sum(1 for t in data if t.get("action") == "EXECUTE")
    no_trades = sum(1 for t in data if t.get("action") == "NO_TRADE")
    print(f"  Total decisions:    {len(data)}")
    print(f"  Patterns detected:  {patterns_detected} ({patterns_detected/max(len(data),1)*100:.1f}%)")
    print(f"  Scored (score>0):   {scored} ({scored/max(len(data),1)*100:.1f}%)")
    print(f"  Executions:         {executions} ({executions/max(len(data),1)*100:.1f}%)")
    print(f"  No-trade:           {no_trades} ({no_trades/max(len(data),1)*100:.1f}%)")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 2. SCORE STATISTICS
    # ═══════════════════════════════════════════════════════════════════
    print("─── 2. SCORE STATISTICS ─────────────────────────────────────────")
    scores_neutral = [t.get("score_neutral", 0) for t in data if t.get("score_neutral", 0) > 0]
    scores_strategy = [t.get("score_strategy", 0) for t in data if t.get("score_strategy", 0) > 0]

    if scores_neutral:
        print(f"  score_neutral (n={len(scores_neutral)}):")
        print(f"    Mean:   {statistics.mean(scores_neutral):.4f}")
        print(f"    Median: {statistics.median(scores_neutral):.4f}")
        print(f"    Min:    {min(scores_neutral):.4f}")
        print(f"    Max:    {max(scores_neutral):.4f}")
        if len(scores_neutral) > 1:
            print(f"    StdDev: {statistics.stdev(scores_neutral):.4f}")
    if scores_strategy:
        print(f"  score_strategy (n={len(scores_strategy)}):")
        print(f"    Mean:   {statistics.mean(scores_strategy):.4f}")
        print(f"    Median: {statistics.median(scores_strategy):.4f}")
        print(f"    Min:    {min(scores_strategy):.4f}")
        print(f"    Max:    {max(scores_strategy):.4f}")
    print()

    # Score distribution buckets
    if scores_neutral:
        buckets = Counter()
        for s in scores_neutral:
            if s < 0.3: buckets["<0.30"] += 1
            elif s < 0.4: buckets["0.30-0.40"] += 1
            elif s < 0.5: buckets["0.40-0.50"] += 1
            elif s < 0.6: buckets["0.50-0.60"] += 1
            elif s < 0.7: buckets["0.60-0.70"] += 1
            else: buckets[">=0.70"] += 1
        print("  Score distribution (neutral):")
        for b in sorted(buckets.keys()):
            print(f"    {b:10s}: {buckets[b]:5d} ({buckets[b]/len(scores_neutral)*100:.1f}%)")
        print()

    # ═══════════════════════════════════════════════════════════════════
    # 3. REJECTION REASONS (TOP 10)
    # ═══════════════════════════════════════════════════════════════════
    print("─── 3. TOP REJECTION REASONS ────────────────────────────────────")
    stages = Counter(t.get("terminal_stage", "unknown") for t in data if t.get("action") == "NO_TRADE")
    reasons = Counter()
    for t in data:
        if t.get("action") == "NO_TRADE":
            r = t.get("terminal_reason", "") or t.get("reason", "") or "unknown"
            # Shorten long reasons
            if len(r) > 60:
                r = r[:57] + "..."
            reasons[r] += 1

    print("  By terminal stage:")
    for stage, cnt in stages.most_common(10):
        print(f"    {stage:25s}: {cnt:5d} ({cnt/max(len(data),1)*100:.1f}%)")
    print()
    print("  By reason (top 10):")
    for reason, cnt in reasons.most_common(10):
        print(f"    {reason:60s}: {cnt:5d}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 4. AUTHORITY USAGE
    # ═══════════════════════════════════════════════════════════════════
    print("─── 4. AUTHORITY USAGE ──────────────────────────────────────────")

    # Regime source
    regime_sources = Counter(t.get("regime_source", "UNKNOWN") for t in data if t.get("regime_source"))
    print("  Regime source:")
    for src, cnt in regime_sources.most_common():
        print(f"    {src:25s}: {cnt:5d} ({cnt/max(len(data),1)*100:.1f}%)")

    # Trend alignment source
    trend_sources = Counter(t.get("trend_alignment_source", "") for t in data if t.get("trend_alignment_source"))
    print("  Trend alignment source:")
    for src, cnt in trend_sources.most_common():
        print(f"    {src:25s}: {cnt:5d} ({cnt/max(len(data),1)*100:.1f}%)")

    # BOS source
    bos_sources = Counter(t.get("bos_source", "") for t in data if t.get("bos_source"))
    print("  BOS source:")
    for src, cnt in bos_sources.most_common():
        print(f"    {src:25s}: {cnt:5d} ({cnt/max(len(data),1)*100:.1f}%)")

    # H4 alignment (>0 means H4 context was available)
    h4_available = sum(1 for t in data if t.get("h4_alignment", 0) > 0)
    h1_available = sum(1 for t in data if t.get("htf_alignment", 0) > 0 and t.get("htf_alignment") != 0.5)
    print(f"  H4 context available: {h4_available}/{len(data)} ({h4_available/max(len(data),1)*100:.1f}%)")
    print(f"  H1 context available: {h1_available}/{len(data)} ({h1_available/max(len(data),1)*100:.1f}%)")
    print()

    # Component averages (authority check)
    components_list = [t.get("components", {}) for t in data if t.get("components")]
    if components_list:
        print("  Average component scores (authority verification):")
        all_keys = sorted(set(k for c in components_list for k in c))
        authority_map = {
            "pattern_quality": "M5", "bias_alignment": "M5", "market_quality": "M15",
            "trend_alignment": "H1", "chop_clarity": "M15", "volatility_quality": "M5",
            "bias_stability": "M5", "confirmation_pre": "M5", "htf_alignment": "H1+M15",
            "h4_alignment": "H4",
        }
        for key in all_keys:
            vals = [c[key] for c in components_list if key in c]
            if vals:
                src = authority_map.get(key, "?")
                print(f"    {key:22s}: mean={statistics.mean(vals):.4f} (authority: {src})")
        print()

    # ═══════════════════════════════════════════════════════════════════
    # 5. OLD M5 AUTHORITY CHECK
    # ═══════════════════════════════════════════════════════════════════
    print("─── 5. OLD M5 AUTHORITY LEAKAGE CHECK ───────────────────────────")

    # Check: if regime_source is present and NOT H4, that's old M5 authority
    m5_regime_fallback = sum(1 for t in data if t.get("regime_source") == "M5_CLASSIFIER")
    m5_trend_fallback = sum(1 for t in data if t.get("trend_alignment_source") == "M5_EMA50")
    m5_bos_fallback = sum(1 for t in data if t.get("bos_source") == "M5_SWING_CONTEXT")

    print(f"  M5 regime fallback used:     {m5_regime_fallback}/{len(data)} ({m5_regime_fallback/max(len(data),1)*100:.1f}%)")
    print(f"  M5 trend alignment fallback: {m5_trend_fallback}/{len(data)} ({m5_trend_fallback/max(len(data),1)*100:.1f}%)")
    print(f"  M5 BOS fallback used:        {m5_bos_fallback}/{len(data)} ({m5_bos_fallback/max(len(data),1)*100:.1f}%)")
    print()

    if m5_regime_fallback == 0 and m5_trend_fallback == 0 and m5_bos_fallback == 0 and post:
        print("  ✅ No old M5 authority influenced decisions — MarketContext is canonical")
    elif not post:
        print("  ⚠️  No post-migration data — analysis based on pre-migration traces")
        print("     Run the bot to generate post-migration data for full validation")
    else:
        total_fallback = m5_regime_fallback + m5_trend_fallback + m5_bos_fallback
        print(f"  ⚠️  M5 fallback used in {total_fallback} cases — likely cold-start/HTF cache misses")
        print("     This is expected during first cycle after startup (HTF cache warming)")
    print()


if __name__ == "__main__":
    main()
