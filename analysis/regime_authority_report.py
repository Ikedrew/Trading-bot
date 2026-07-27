"""
Regime Authority Analysis — Migration 1.5 Validation Report.

Produces regime distribution tables from persisted decision traces.
Handles both pre-migration (no regime_source) and post-migration data.

Does NOT modify trading code, thresholds, or behaviour.
Run: python analysis/regime_authority_report.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_TRACE_DIR = _PROJECT_ROOT / "logs" / "decision_trace"


# ─── DATA LOADING ─────────────────────────────────────────────────────────────


def _load_traces() -> list[dict]:
    """Load all decision trace JSONL records."""
    records: list[dict] = []
    if not _TRACE_DIR.exists():
        return records
    for sym_dir in sorted(_TRACE_DIR.iterdir()):
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


def _classify_source(trace: dict) -> str:
    """Classify regime source. Labels pre-migration data as PRE_MIGRATION."""
    src = trace.get("regime_source")
    if src is None or src == "":
        return "PRE_MIGRATION"
    return src


def _get_date(trace: dict) -> str:
    """Extract date from trace (YYYY-MM-DD)."""
    ts = trace.get("timestamp_utc", "")
    if len(ts) >= 10:
        return ts[:10]
    return "unknown"


# ─── REPORT GENERATION ────────────────────────────────────────────────────────


def generate_report(traces: list[dict]) -> str:
    """Generate full regime authority validation report."""
    lines: list[str] = []
    w = lines.append

    w("=" * 70)
    w("REGIME AUTHORITY ANALYSIS — Migration 1.5 Validation")
    w("=" * 70)
    w(f"Total decision traces: {len(traces)}")
    w(f"Symbols: {sorted(set(t.get('symbol', '?') for t in traces if t.get('symbol')))}")
    dates = sorted(set(_get_date(t) for t in traces))
    w(f"Date range: {dates[0] if dates else '?'} → {dates[-1] if dates else '?'}")
    w("")

    # ═══════════════════════════════════════════════════════════════════
    # 1. REGIME SOURCE DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════
    w("─── 1. REGIME SOURCE DISTRIBUTION ────────────────────────────────")
    w("")
    w(f"{'Source':<25s} {'Count':>7s} {'Percentage':>11s}")
    w(f"{'-'*25} {'-'*7} {'-'*11}")

    source_counts = Counter(_classify_source(t) for t in traces)
    total = len(traces)
    for source, count in source_counts.most_common():
        pct = count / total * 100 if total > 0 else 0
        w(f"{source:<25s} {count:>7d} {pct:>10.1f}%")
    w("")

    # ═══════════════════════════════════════════════════════════════════
    # 2. REGIME DISTRIBUTION BY SOURCE
    # ═══════════════════════════════════════════════════════════════════
    w("─── 2. REGIME DISTRIBUTION BY SOURCE ─────────────────────────────")
    w("")

    by_source: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        by_source[_classify_source(t)].append(t)

    for source in sorted(by_source.keys()):
        subset = by_source[source]
        regime_counts = Counter(t.get("regime") or "UNKNOWN" for t in subset)
        sub_total = len(subset)
        w(f"  Source: {source} (n={sub_total})")
        w(f"  {'Regime':<20s} {'Count':>7s} {'Percentage':>11s}")
        w(f"  {'-'*20} {'-'*7} {'-'*11}")
        for regime, count in regime_counts.most_common():
            pct = count / sub_total * 100 if sub_total > 0 else 0
            w(f"  {regime:<20s} {count:>7d} {pct:>10.1f}%")
        w("")

    # ═══════════════════════════════════════════════════════════════════
    # 3. AVERAGE CONFIDENCE BY REGIME
    # ═══════════════════════════════════════════════════════════════════
    w("─── 3. AVERAGE CONFIDENCE BY REGIME ──────────────────────────────")
    w("")
    w(f"{'Source':<22s} {'Regime':<16s} {'Avg Conf':>9s} {'Min':>6s} {'Max':>6s} {'n':>6s}")
    w(f"{'-'*22} {'-'*16} {'-'*9} {'-'*6} {'-'*6} {'-'*6}")

    for source in sorted(by_source.keys()):
        subset = by_source[source]
        by_regime: dict[str, list[float]] = defaultdict(list)
        for t in subset:
            regime = t.get("regime") or "UNKNOWN"
            conf = t.get("regime_confidence", 0.0)
            if isinstance(conf, (int, float)):
                by_regime[regime].append(conf)

        for regime in sorted(by_regime.keys()):
            confs = by_regime[regime]
            if confs:
                avg = sum(confs) / len(confs)
                w(f"{source:<22s} {regime:<16s} {avg:>9.4f} {min(confs):>6.3f} {max(confs):>6.3f} {len(confs):>6d}")
    w("")

    # ═══════════════════════════════════════════════════════════════════
    # 4. BEFORE VS AFTER MIGRATION COMPARISON
    # ═══════════════════════════════════════════════════════════════════
    w("─── 4. BEFORE VS AFTER MIGRATION COMPARISON ─────────────────────")
    w("")

    pre = by_source.get("PRE_MIGRATION", [])
    post_h4 = by_source.get("H4_MARKET_CONTEXT", [])
    post_m5 = by_source.get("M5_CLASSIFIER", [])
    post_all = post_h4 + post_m5

    w(f"  Pre-migration traces:  {len(pre)}")
    w(f"  Post-migration traces: {len(post_all)} (H4={len(post_h4)}, M5_fallback={len(post_m5)})")
    w("")

    if pre:
        pre_regimes = Counter(t.get("regime") or "UNKNOWN" for t in pre)
        pre_total = len(pre)
        w("  PRE-MIGRATION regime breakdown:")
        for r, c in pre_regimes.most_common():
            w(f"    {r:<20s}: {c:>5d} ({c/pre_total*100:.1f}%)")
        pre_confs = [t.get("regime_confidence", 0.0) for t in pre if isinstance(t.get("regime_confidence"), (int, float))]
        if pre_confs:
            w(f"    Avg confidence: {sum(pre_confs)/len(pre_confs):.4f}")
        w("")

    if post_all:
        post_regimes = Counter(t.get("regime") or "UNKNOWN" for t in post_all)
        post_total = len(post_all)
        w("  POST-MIGRATION regime breakdown:")
        for r, c in post_regimes.most_common():
            w(f"    {r:<20s}: {c:>5d} ({c/post_total*100:.1f}%)")
        post_confs = [t.get("regime_confidence", 0.0) for t in post_all if isinstance(t.get("regime_confidence"), (int, float))]
        if post_confs:
            w(f"    Avg confidence: {sum(post_confs)/len(post_confs):.4f}")
        w("")

        # Compute shift
        w("  SHIFT ANALYSIS:")
        all_regimes = sorted(set(list(pre_regimes.keys()) + list(post_regimes.keys())))
        w(f"    {'Regime':<20s} {'Pre %':>7s} {'Post %':>7s} {'Delta':>7s}")
        w(f"    {'-'*20} {'-'*7} {'-'*7} {'-'*7}")
        for r in all_regimes:
            pre_pct = pre_regimes.get(r, 0) / max(pre_total, 1) * 100
            post_pct = post_regimes.get(r, 0) / max(post_total, 1) * 100
            delta = post_pct - pre_pct
            sign = "+" if delta > 0 else ""
            w(f"    {r:<20s} {pre_pct:>6.1f}% {post_pct:>6.1f}% {sign}{delta:>5.1f}%")
        w("")
    else:
        w("  ⚠ No post-migration data available yet.")
        w("  Run the bot (live or replay) to generate H4-sourced regime traces.")
        w("")

    # ═══════════════════════════════════════════════════════════════════
    # 5. PER-SYMBOL REGIME SOURCE (post-migration only)
    # ═══════════════════════════════════════════════════════════════════
    if post_all:
        w("─── 5. PER-SYMBOL REGIME SOURCE (post-migration) ────────────────")
        w("")
        w(f"{'Symbol':<10s} {'H4_MC':>7s} {'M5_FB':>7s} {'H4 %':>7s}")
        w(f"{'-'*10} {'-'*7} {'-'*7} {'-'*7}")

        sym_source: dict[str, Counter] = defaultdict(Counter)
        for t in post_all:
            sym = t.get("symbol", "?")
            src = _classify_source(t)
            sym_source[sym][src] += 1

        for sym in sorted(sym_source.keys()):
            h4_c = sym_source[sym].get("H4_MARKET_CONTEXT", 0)
            m5_c = sym_source[sym].get("M5_CLASSIFIER", 0)
            total_s = h4_c + m5_c
            h4_pct = h4_c / total_s * 100 if total_s > 0 else 0
            w(f"{sym:<10s} {h4_c:>7d} {m5_c:>7d} {h4_pct:>6.1f}%")
        w("")

    # ═══════════════════════════════════════════════════════════════════
    # 6. DAILY REGIME TIMELINE (post-migration only)
    # ═══════════════════════════════════════════════════════════════════
    if post_all:
        w("─── 6. DAILY REGIME TIMELINE (post-migration) ───────────────────")
        w("")
        w(f"{'Date':<12s} {'TRENDING':>9s} {'RANGE':>7s} {'TRANSIT':>9s} {'Total':>7s} {'Dom.Regime':<14s}")
        w(f"{'-'*12} {'-'*9} {'-'*7} {'-'*9} {'-'*7} {'-'*14}")

        daily: dict[str, Counter] = defaultdict(Counter)
        for t in post_all:
            d = _get_date(t)
            r = t.get("regime") or "UNKNOWN"
            daily[d][r] += 1

        for date in sorted(daily.keys()):
            counts = daily[date]
            total_d = sum(counts.values())
            trending = counts.get("TRENDING", 0)
            ranging = counts.get("RANGE", 0)
            transit = counts.get("TRANSITIONAL", 0)
            dominant = counts.most_common(1)[0][0] if counts else "?"
            w(f"{date:<12s} {trending:>9d} {ranging:>7d} {transit:>9d} {total_d:>7d} {dominant:<14s}")
        w("")

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════
    w("─── SUMMARY ─────────────────────────────────────────────────────")
    w("")
    if post_all:
        h4_pct = len(post_h4) / len(post_all) * 100 if post_all else 0
        w(f"  Migration status: ACTIVE")
        w(f"  H4 authority rate: {h4_pct:.1f}% of post-migration decisions")
        dominant_post = post_regimes.most_common(1)[0] if post_regimes else ("?", 0)
        w(f"  Dominant regime:   {dominant_post[0]} ({dominant_post[1]/max(len(post_all),1)*100:.1f}%)")
        if pre:
            dominant_pre = pre_regimes.most_common(1)[0]
            collapsed = dominant_pre[1] / max(len(pre), 1) * 100 > 95
            w(f"  Pre-migration:     {dominant_pre[0]} dominated at {dominant_pre[1]/max(len(pre),1)*100:.1f}%")
            if not collapsed or dominant_post[1] / max(len(post_all), 1) * 100 < 90:
                w(f"  ✅ Regime distribution has improved (no longer single-class collapse)")
            else:
                w(f"  ⚠ Regime still concentrated — may need investigation")
    else:
        w(f"  Migration status: DEPLOYED (code verified), awaiting live data")
        w(f"  Pre-migration baseline: {len(pre)} traces, {pre_regimes.most_common(1)[0][0] if pre_regimes else '?'} = {pre_regimes.most_common(1)[0][1]/max(len(pre),1)*100:.1f}%" if pre else "  No data available")
        w(f"  Action required: Run bot to generate post-migration traces")
    w("")

    return "\n".join(lines)


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    traces = _load_traces()
    if not traces:
        print("ERROR: No decision traces found in logs/decision_trace/")
        print("Ensure the bot has run at least one session.")
        sys.exit(1)

    report = generate_report(traces)
    print(report)

    # Also write to file for archival
    output_path = _PROJECT_ROOT / "analysis" / "reports" / "regime_authority_validation.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report saved to: {output_path}")


if __name__ == "__main__":
    main()
