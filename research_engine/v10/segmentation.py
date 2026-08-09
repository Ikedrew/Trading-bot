"""
V10 Research Dataset Segmentation Engine (V2).

Shared preprocessing layer that generates standardised dataset views
for all research experiments. Runs once per research cycle.

Views generated:
    FULL_RAW      - all trades (NORMAL + FLAGGED anomalies)
    STANDARD      - only NORMAL trades (anomalies excluded from metrics)
    ANOMALY_ONLY  - only FLAGGED trades (for impact analysis)
    FULL          - alias for FULL_RAW (backward compat)
    FX            - FX_MAJOR + FX_JPY (standard only)
    INDEX         - INDEX instruments (standard only)
    COMMODITY     - COMMODITY instruments (standard only)
    {SYMBOL}      - per-instrument view (standard only)

Also generates:
    instrument_rankings.json  - cross-sectional ranking output
    anomalies.jsonl           - anomaly audit trail

Usage:
    from research_engine.v10.segmentation import build_segmentation, load_view

    # Build all views (run once per cycle)
    result = build_segmentation()

    # Load specific views
    trades = load_view("FULL_RAW")      # all trades
    trades = load_view("STANDARD")      # normal only
    trades = load_view("ANOMALY_ONLY")  # flagged only
    trades = load_view("FX")            # FX standard trades
    trades = load_view("EURUSD")        # per-instrument

CLI:
    python -m research_engine.v10.segmentation
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, _classify_instrument, _compute_r
from research_engine.v10.anomaly_layer import classify_anomalies

logger = logging.getLogger(__name__)

_RESEARCH_READY = "logs/research_ready_trade_dataset/research_ready_trades.jsonl"
_VIEWS_DIR = "logs/research_views"
_REPORTS_DIR = "reports/research"

_FX_CLASSES = frozenset({"FX_MAJOR", "FX_JPY"})


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def build_segmentation(
    source_file: str | None = None,
    views_dir: str | None = None,
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """
    Build all segmentation views from the research-ready dataset.

    V2 additions:
        - Anomaly classification (NORMAL/FLAGGED)
        - Dual views: FULL_RAW, STANDARD, ANOMALY_ONLY
        - instrument_rankings.json output
        - All instrument views auto-generated

    Returns:
        Segmentation summary dict.
    """
    src = Path(source_file or _RESEARCH_READY)
    out = Path(views_dir or _VIEWS_DIR)
    rep = Path(reports_dir or _REPORTS_DIR)

    # Load and ensure fields
    trades = _load_source(src)
    if not trades:
        return {"error": "No trades loaded"}

    n_total = len(trades)
    logger.info(f"[SEGMENTATION] Loaded {n_total} trades from {src}")

    # ─── CLASSIFY INSTRUMENTS ─────────────────────────────────
    for t in trades:
        if not t.get("instrument_class"):
            t["instrument_class"] = _classify_instrument(t.get("symbol", ""))
        if "realised_r" not in t or t["realised_r"] == 0:
            _compute_r(t)

    # ─── ANOMALY CLASSIFICATION ───────────────────────────────
    anomaly_result = classify_anomalies(
        trades,
        output_file=str(out / "anomalies.jsonl"),
    )
    normal_trades = anomaly_result["normal"]
    flagged_trades = anomaly_result["flagged"]

    logger.info(
        f"[SEGMENTATION] Anomaly layer: {anomaly_result['normal_count']} NORMAL, "
        f"{anomaly_result['flagged_count']} FLAGGED"
    )

    # ─── GENERATE VIEWS ──────────────────────────────────────
    views = {}

    # Dual dataset views
    views["FULL_RAW"] = trades
    views["FULL"] = trades  # backward compat alias
    views["STANDARD"] = normal_trades
    views["ANOMALY_ONLY"] = flagged_trades

    # Asset class views (from STANDARD trades — anomalies excluded)
    asset_groups = {}
    for t in normal_trades:
        cls = t["instrument_class"]
        asset_groups.setdefault(cls, []).append(t)

    fx_trades = [t for t in normal_trades if t["instrument_class"] in _FX_CLASSES]
    views["FX"] = fx_trades
    views["INDEX"] = asset_groups.get("INDEX", [])
    views["COMMODITY"] = asset_groups.get("COMMODITY", [])

    # Instrument-level views (from STANDARD trades)
    symbol_groups = {}
    for t in normal_trades:
        symbol_groups.setdefault(t["symbol"], []).append(t)

    for sym, group in symbol_groups.items():
        views[sym] = group

    # Also track all symbols (including those only in flagged)
    all_symbol_groups = {}
    for t in trades:
        all_symbol_groups.setdefault(t["symbol"], []).append(t)

    # ─── COMPUTE METRICS ──────────────────────────────────────
    view_metrics = {}
    for name, group in views.items():
        if name == "FULL":
            continue  # Skip alias, use FULL_RAW
        if group:
            view_metrics[name] = compute_metrics(group)
        else:
            view_metrics[name] = {"count": 0}

    # ─── CROSS-SECTIONAL RANKING ─────────────────────────────
    # Rank ALL instruments (no minimum) — include count for confidence
    instrument_rankings = []
    for sym, group in sorted(symbol_groups.items()):
        m = compute_metrics(group)
        instrument_rankings.append({
            "symbol": sym,
            "count": m["count"],
            "win_rate": m["win_rate"],
            "expectancy_r": m["expectancy_r"],
            "profit_factor": m["profit_factor"],
            "average_r": m["average_r"],
            "total_pnl": m["total_pnl"],
            "confidence": m["confidence"],
        })

    ranked_by_expectancy = sorted(instrument_rankings, key=lambda x: x["expectancy_r"], reverse=True)
    ranked_by_win_rate = sorted(instrument_rankings, key=lambda x: x["win_rate"], reverse=True)
    ranked_by_pf = sorted(
        instrument_rankings,
        key=lambda x: x["profit_factor"] if x["profit_factor"] < 900 else 0,
        reverse=True,
    )

    # ─── WRITE VIEWS ─────────────────────────────────────────
    out.mkdir(parents=True, exist_ok=True)

    # Dual views
    _write_view(out / "FULL_RAW.jsonl", views["FULL_RAW"])
    _write_view(out / "FULL.jsonl", views["FULL_RAW"])  # backward compat
    _write_view(out / "STANDARD.jsonl", views["STANDARD"])
    _write_view(out / "ANOMALY_ONLY.jsonl", views["ANOMALY_ONLY"])

    # Asset class
    ac_dir = out / "ASSET_CLASS"
    ac_dir.mkdir(parents=True, exist_ok=True)
    _write_view(ac_dir / "FX.jsonl", views["FX"])
    _write_view(ac_dir / "INDEX.jsonl", views["INDEX"])
    _write_view(ac_dir / "COMMODITY.jsonl", views["COMMODITY"])

    # Instrument
    inst_dir = out / "INSTRUMENT"
    inst_dir.mkdir(parents=True, exist_ok=True)
    for sym, group in symbol_groups.items():
        _write_view(inst_dir / f"{sym}.jsonl", group)

    # ─── INSTRUMENT RANKINGS FILE ─────────────────────────────
    rankings_output = {
        "generated_utc": timestamp_now(),
        "total_instruments": len(instrument_rankings),
        "by_expectancy": ranked_by_expectancy,
        "by_win_rate": ranked_by_win_rate,
        "by_profit_factor": ranked_by_pf,
    }
    (out / "instrument_rankings.json").write_text(
        json.dumps(rankings_output, indent=2, default=str), encoding="utf-8"
    )

    # ─── METADATA ─────────────────────────────────────────────
    metadata = {
        "generated_utc": timestamp_now(),
        "source": str(src),
        "total_trades": n_total,
        "normal_trades": anomaly_result["normal_count"],
        "flagged_trades": anomaly_result["flagged_count"],
        "views_generated": [k for k in views.keys() if k != "FULL"],
        "symbols": sorted(all_symbol_groups.keys()),
        "asset_classes": sorted(asset_groups.keys()),
        "view_counts": {k: len(v) for k, v in views.items() if k != "FULL"},
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

    # ─── BUILD REPORT ─────────────────────────────────────────
    report = {
        "generated_utc": timestamp_now(),
        "source_file": str(src),
        "total_trades": n_total,
        "normal_trades": anomaly_result["normal_count"],
        "flagged_trades": anomaly_result["flagged_count"],
        "date_range": _get_date_range(trades),
        "anomaly_summary": anomaly_result["anomaly_summary"],
        "asset_class_summary": {
            name: view_metrics.get(name, {"count": 0})
            for name in ["FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FX", "INDEX", "COMMODITY"]
        },
        "instrument_summary": {
            sym: view_metrics[sym]
            for sym in sorted(symbol_groups.keys())
            if view_metrics.get(sym, {}).get("count", 0) > 0
        },
        "rankings": {
            "by_expectancy": ranked_by_expectancy,
            "by_win_rate": ranked_by_win_rate,
            "by_profit_factor": ranked_by_pf,
        },
        "view_counts": {k: len(v) for k, v in views.items() if k != "FULL"},
    }

    rep.mkdir(parents=True, exist_ok=True)
    (rep / "segmentation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (rep / "segmentation_report.md").write_text(_build_markdown(report), encoding="utf-8")

    logger.info(f"[SEGMENTATION] Complete: {n_total} trades -> {len(views)-1} views")
    return report


def load_view(view_name: str, views_dir: str | None = None) -> list[dict[str, Any]]:
    """
    Load a pre-built segmentation view.

    Args:
        view_name: "FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FULL",
                   "FX", "INDEX", "COMMODITY", or a symbol like "EURUSD"
        views_dir: Override views directory

    Returns:
        List of trade dicts from the view file.
    """
    out = Path(views_dir or _VIEWS_DIR)

    # Try direct file first
    candidates = [
        out / f"{view_name}.jsonl",
        out / "ASSET_CLASS" / f"{view_name}.jsonl",
        out / "INSTRUMENT" / f"{view_name}.jsonl",
    ]

    for path in candidates:
        if path.exists():
            trades = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return trades

    # Fallback: load from source and filter dynamically
    logger.info(f"[SEGMENTATION] View '{view_name}' not found on disk, loading dynamically")
    all_trades = load_trades(DatasetView.FULL)

    name = view_name.upper()
    if name in ("FULL", "FULL_RAW"):
        return all_trades
    elif name == "STANDARD":
        return [t for t in all_trades if t.get("anomaly_status", "NORMAL") == "NORMAL"]
    elif name == "ANOMALY_ONLY":
        return [t for t in all_trades if t.get("anomaly_status") == "FLAGGED"]
    elif name == "FX":
        return [t for t in all_trades if t.get("instrument_class", "") in _FX_CLASSES]
    elif name == "INDEX":
        return [t for t in all_trades if t.get("instrument_class", "") == "INDEX"]
    elif name == "COMMODITY":
        return [t for t in all_trades if t.get("instrument_class", "") == "COMMODITY"]
    else:
        # Assume it's a symbol name
        return [t for t in all_trades if t.get("symbol", "") == name]


# ═══════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════

def _load_source(path: Path) -> list[dict]:
    if not path.exists():
        return []
    trades = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return trades


def _write_view(path: Path, trades: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t, default=str) for t in trades]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _get_date_range(trades: list[dict]) -> dict[str, str]:
    times = [t.get("entry_time", 0) for t in trades if t.get("entry_time")]
    if not times:
        return {"oldest": "", "newest": ""}
    return {
        "oldest": datetime.fromtimestamp(min(times), tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "newest": datetime.fromtimestamp(max(times), tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append("# V10 Research Dataset Segmentation Report (V2)")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Source: {report['source_file']}")
    md.append(f"Total trades: {report['total_trades']} (Normal: {report['normal_trades']}, Flagged: {report['flagged_trades']})")
    dr = report.get("date_range", {})
    md.append(f"Date range: {dr.get('oldest', '?')} -> {dr.get('newest', '?')}")
    md.append("")

    # Anomaly summary
    anom = report.get("anomaly_summary", {})
    if anom.get("reason_counts"):
        md.append("## Anomaly Classification")
        md.append("")
        md.append("| Reason | Count |")
        md.append("|---|---|")
        for reason, count in sorted(anom["reason_counts"].items(), key=lambda x: -x[1]):
            md.append(f"| {reason} | {count} |")
        md.append("")
        impact = anom.get("impact", {})
        if impact:
            md.append(f"Impact: FULL_RAW expectancy={impact.get('full_raw_expectancy', 0):+.4f}R | "
                      f"STANDARD expectancy={impact.get('standard_expectancy', 0):+.4f}R | "
                      f"diff={impact.get('expectancy_diff', 0):+.4f}R")
            md.append("")

    md.append("## Dataset Views")
    md.append("")
    md.append("| View | Trades | Win% | Avg R | Expectancy | PF | Total PnL |")
    md.append("|---|---|---|---|---|---|---|")
    for name in ["FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FX", "INDEX", "COMMODITY"]:
        m = report["asset_class_summary"].get(name, {})
        if m.get("count", 0) == 0:
            continue
        pf = f"{m.get('profit_factor', 0):.1f}" if m.get("profit_factor", 0) < 900 else "inf"
        md.append(f"| {name} | {m['count']} | {m.get('win_rate',0):.0%} | "
                  f"{m.get('average_r',0):+.2f} | {m.get('expectancy_r',0):+.2f} | "
                  f"{pf} | ${m.get('total_pnl',0):.2f} |")

    md.append("")
    md.append("## Instrument Summary")
    md.append("")
    md.append("| Symbol | N | Win% | Avg R | Expectancy | PF | PnL | Confidence |")
    md.append("|---|---|---|---|---|---|---|---|")
    for sym, m in sorted(report["instrument_summary"].items(), key=lambda x: -x[1].get("count", 0)):
        pf = f"{m.get('profit_factor',0):.1f}" if m.get("profit_factor", 0) < 900 else "inf"
        md.append(f"| {sym} | {m['count']} | {m.get('win_rate',0):.0%} | "
                  f"{m.get('average_r',0):+.2f} | {m.get('expectancy_r',0):+.2f} | "
                  f"{pf} | ${m.get('total_pnl',0):.2f} | {m.get('confidence','')} |")

    md.append("")
    md.append("## Instrument Rankings")
    md.append("")
    md.append("### By Expectancy")
    md.append("")
    for i, r in enumerate(report["rankings"]["by_expectancy"][:5], 1):
        md.append(f"{i}. **{r['symbol']}** -- {r['expectancy_r']:+.2f}R (n={r['count']}, {r['confidence']})")
    md.append("")
    md.append("### By Win Rate")
    md.append("")
    for i, r in enumerate(report["rankings"]["by_win_rate"][:5], 1):
        md.append(f"{i}. **{r['symbol']}** -- {r['win_rate']:.0%} (n={r['count']})")

    md.append("")
    md.append("---")
    md.append("*Views stored in logs/research_views/ for experiment consumption*")
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    print("Building research segmentation (V2)...")
    result = build_segmentation()

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    print(f"\nSegmentation complete:")
    print(f"  Total trades: {result['total_trades']}")
    print(f"  Normal: {result['normal_trades']} | Flagged: {result['flagged_trades']}")
    print(f"  Views: {len(result['view_counts'])}")
    print(f"\n  Dataset views:")
    for name in ["FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FX", "INDEX", "COMMODITY"]:
        m = result["asset_class_summary"].get(name, {})
        if m.get("count", 0) > 0:
            print(f"    {name:14s}: n={m['count']:3d} win={m.get('win_rate',0):.0%} exp={m.get('expectancy_r',0):+.2f}R pnl=${m.get('total_pnl',0):.2f}")
    print(f"\n  Top instruments (by expectancy):")
    for r in result["rankings"]["by_expectancy"][:5]:
        print(f"    {r['symbol']:8s}: exp={r['expectancy_r']:+.2f}R win={r['win_rate']:.0%} n={r['count']} ({r['confidence']})")
    anom = result.get("anomaly_summary", {})
    if anom.get("reason_counts"):
        print(f"\n  Anomaly reasons:")
        for reason, count in sorted(anom["reason_counts"].items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
    print(f"\n  Reports: reports/research/segmentation_report.*")
    print(f"  Views: logs/research_views/")
    print(f"  Rankings: logs/research_views/instrument_rankings.json")
