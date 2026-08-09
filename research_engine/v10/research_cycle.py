"""
V10 Research Cycle Orchestrator (V2).

Executes a complete research cycle with one command:
    1. Load trade database
    2. Build segmentation views (with anomaly classification)
    3. Run all registered experiments against FULL + asset class + instruments
    4. Generate cycle report (with anomaly summary)
    5. Store baseline for future comparison

Usage:
    python -m research_engine.v10.research_cycle
    python -m research_engine.v10.research_cycle --compare

Produces:
    reports/research_cycles/YYYY-MM-cycle-NN/
        cycle_metadata.json
        dataset_summary.json
        segmentation_report.json
        anomaly_report.json
        instrument_rankings.json
        experiments/E1.json, E2.json, ...
        cycle_summary.md
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import compute_metrics, timestamp_now
from research_engine.v10.segmentation import build_segmentation, load_view
from research_engine.v10.runner import run_experiment, _EXPERIMENT_REGISTRY
from research_engine.v10.baseline import load_baseline, save_baseline, compare_baselines

logger = logging.getLogger(__name__)

_CYCLES_DIR = "reports/research_cycles"

# Views to run experiments against (runner accepts DatasetView enum strings)
_CORE_VIEWS = ["FULL", "FX_ONLY", "INDEX_ONLY"]

# Minimum trades to run an experiment against an instrument view
_MIN_INSTRUMENT_SAMPLE = 5


def run_research_cycle(compare: bool = False) -> dict[str, Any]:
    """
    Execute a complete V2 research cycle.

    Steps:
        1. Build segmentation (includes anomaly classification)
        2. Run all experiments against FULL + asset class views
        3. Run experiments against instrument views (where sample allows)
        4. Generate cycle report with anomaly summary
        5. Save baseline
        6. Optionally compare against previous cycle

    Returns:
        Cycle summary dict.
    """
    start = time.time()
    cycle_id = _generate_cycle_id()
    cycle_dir = Path(_CYCLES_DIR) / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    exp_dir = cycle_dir / "experiments"
    exp_dir.mkdir(exist_ok=True)

    logger.info(f"[RESEARCH_CYCLE] Starting V2 cycle: {cycle_id}")

    # ─── STEP 1: SEGMENTATION + ANOMALY ──────────────────────
    seg_result = build_segmentation()
    if "error" in seg_result:
        return {"error": seg_result["error"], "cycle_id": cycle_id}

    total_trades = seg_result["total_trades"]
    normal_trades = seg_result.get("normal_trades", total_trades)
    flagged_trades = seg_result.get("flagged_trades", 0)

    logger.info(
        f"[RESEARCH_CYCLE] Segmentation: {total_trades} trades "
        f"(normal={normal_trades}, flagged={flagged_trades}), "
        f"{len(seg_result['view_counts'])} views"
    )

    # Save segmentation report into cycle
    (cycle_dir / "segmentation_report.json").write_text(
        json.dumps(seg_result, indent=2, default=str), encoding="utf-8"
    )

    # Save anomaly report
    anomaly_summary = seg_result.get("anomaly_summary", {})
    (cycle_dir / "anomaly_report.json").write_text(
        json.dumps(anomaly_summary, indent=2, default=str), encoding="utf-8"
    )

    # Save instrument rankings
    rankings = seg_result.get("rankings", {})
    (cycle_dir / "instrument_rankings.json").write_text(
        json.dumps(rankings, indent=2, default=str), encoding="utf-8"
    )

    # Dataset summary
    dataset_summary = {
        "total_trades": total_trades,
        "normal_trades": normal_trades,
        "flagged_trades": flagged_trades,
        "date_range": seg_result.get("date_range", {}),
        "symbols": sorted(seg_result.get("instrument_summary", {}).keys()),
        "asset_classes": {
            name: seg_result["asset_class_summary"].get(name, {}).get("count", 0)
            for name in ["FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FX", "INDEX", "COMMODITY"]
        },
    }
    (cycle_dir / "dataset_summary.json").write_text(
        json.dumps(dataset_summary, indent=2, default=str), encoding="utf-8"
    )

    # ─── STEP 2: DETERMINE INSTRUMENT VIEWS ──────────────────
    # Find instruments with enough trades for meaningful analysis
    instrument_views = []
    for sym, metrics in seg_result.get("instrument_summary", {}).items():
        if metrics.get("count", 0) >= _MIN_INSTRUMENT_SAMPLE:
            instrument_views.append(sym)

    logger.info(f"[RESEARCH_CYCLE] Instrument views eligible: {instrument_views}")

    # ─── STEP 3: RUN ALL EXPERIMENTS ─────────────────────────
    experiment_ids = list(_EXPERIMENT_REGISTRY.keys())
    experiment_results = {}
    experiments_completed = 0
    experiments_failed = 0

    for exp_id in experiment_ids:
        exp_result = {}

        # Core views (FULL, FX_ONLY, INDEX_ONLY)
        for view in _CORE_VIEWS:
            try:
                result = run_experiment(exp_id, view=view)
                exp_result[view] = {
                    "conclusion": result.get("conclusion", "ERROR"),
                    "sample_size": result.get("sample_size", 0),
                    "metrics": result.get("metrics", {}),
                }
            except Exception as exc:
                exp_result[view] = {"conclusion": "ERROR", "error": str(exc)}
                experiments_failed += 1

        # Instrument views (load pre-built view, pass as trades)
        for sym in instrument_views:
            try:
                sym_trades = load_view(sym)
                if len(sym_trades) >= _MIN_INSTRUMENT_SAMPLE:
                    result = run_experiment(exp_id, view="FULL", trades=sym_trades)
                    exp_result[sym] = {
                        "conclusion": result.get("conclusion", "ERROR"),
                        "sample_size": result.get("sample_size", len(sym_trades)),
                        "metrics": result.get("metrics", {}),
                    }
            except Exception as exc:
                exp_result[sym] = {"conclusion": "ERROR", "error": str(exc)}

        experiment_results[exp_id] = exp_result
        experiments_completed += 1

        # Save individual experiment result
        (exp_dir / f"{exp_id}.json").write_text(
            json.dumps(exp_result, indent=2, default=str), encoding="utf-8"
        )

    logger.info(
        f"[RESEARCH_CYCLE] Experiments: {experiments_completed}/{len(experiment_ids)} complete, "
        f"{experiments_failed} view-level errors"
    )

    # ─── STEP 4: GENERATE CYCLE REPORT ───────────────────────
    full_raw_metrics = seg_result["asset_class_summary"].get("FULL_RAW", {})
    standard_metrics = seg_result["asset_class_summary"].get("STANDARD", {})

    cycle_summary = {
        "cycle_id": cycle_id,
        "version": "V2",
        "generated_utc": timestamp_now(),
        "duration_seconds": round(time.time() - start, 1),
        "dataset": dataset_summary,
        "performance": {
            view: seg_result["asset_class_summary"].get(view, {})
            for view in ["FULL_RAW", "STANDARD", "ANOMALY_ONLY", "FX", "INDEX", "COMMODITY"]
        },
        "anomaly_summary": {
            "flagged_count": flagged_trades,
            "reason_counts": anomaly_summary.get("reason_counts", {}),
            "impact": anomaly_summary.get("impact", {}),
        },
        "experiments": {
            exp_id: {view: r.get("conclusion", "?") for view, r in views.items()}
            for exp_id, views in experiment_results.items()
        },
        "instrument_views_run": instrument_views,
        "rankings": rankings.get("by_expectancy", [])[:5],
    }

    # ─── STEP 5: SAVE BASELINE ────────────────────────────────
    baseline_data = {
        "cycle_id": cycle_id,
        "timestamp": timestamp_now(),
        "trade_count": total_trades,
        "normal_count": normal_trades,
        "flagged_count": flagged_trades,
        "expectancy_r": standard_metrics.get("expectancy_r", 0),
        "win_rate": standard_metrics.get("win_rate", 0),
        "profit_factor": standard_metrics.get("profit_factor", 0),
        "total_pnl": standard_metrics.get("total_pnl", 0),
        "experiments": {
            exp_id: views.get("FULL", {}).get("conclusion", "?")
            for exp_id, views in experiment_results.items()
        },
    }
    save_baseline(baseline_data)

    # ─── STEP 6: COMPARISON ───────────────────────────────────
    comparison = None
    if compare:
        comparison = compare_baselines()
        cycle_summary["comparison"] = comparison

    # ─── WRITE REPORTS ────────────────────────────────────────
    (cycle_dir / "cycle_metadata.json").write_text(
        json.dumps(cycle_summary, indent=2, default=str), encoding="utf-8"
    )
    (cycle_dir / "cycle_summary.md").write_text(
        _build_cycle_markdown(cycle_summary, comparison, experiment_results, instrument_views),
        encoding="utf-8",
    )

    cycle_summary["cycle_dir"] = str(cycle_dir)
    return cycle_summary


def _generate_cycle_id() -> str:
    """Generate a unique cycle identifier."""
    now = datetime.now(timezone.utc)
    base = now.strftime("%Y-%m")
    cycles_path = Path(_CYCLES_DIR)
    existing = list(cycles_path.glob(f"{base}-cycle-*")) if cycles_path.exists() else []
    n = len(existing) + 1
    return f"{base}-cycle-{n:02d}"


def _build_cycle_markdown(
    summary: dict,
    comparison: dict | None,
    experiment_results: dict,
    instrument_views: list[str],
) -> str:
    md = []
    md.append(f"# V10 Research Cycle: {summary['cycle_id']} (V2)")
    md.append("")
    md.append(f"Generated: {summary['generated_utc']}")
    md.append(f"Duration: {summary['duration_seconds']:.1f}s")
    md.append("")

    # Dataset
    md.append("## Dataset")
    md.append("")
    ds = summary["dataset"]
    md.append(f"- Total trades: {ds['total_trades']}")
    md.append(f"- Normal trades: {ds['normal_trades']}")
    md.append(f"- Flagged trades: {ds['flagged_trades']}")
    md.append(f"- Symbols: {', '.join(ds.get('symbols', []))}")
    dr = ds.get("date_range", {})
    md.append(f"- Date range: {dr.get('oldest', '?')} -> {dr.get('newest', '?')}")
    md.append("")

    # Anomaly summary
    anom = summary.get("anomaly_summary", {})
    if anom.get("reason_counts"):
        md.append("## Anomaly Summary")
        md.append("")
        md.append(f"Flagged: {anom['flagged_count']} trades")
        md.append("")
        md.append("| Reason | Count |")
        md.append("|---|---|")
        for reason, count in sorted(anom["reason_counts"].items(), key=lambda x: -x[1]):
            md.append(f"| {reason} | {count} |")
        impact = anom.get("impact", {})
        if impact:
            md.append("")
            md.append(f"Impact: FULL_RAW exp={impact.get('full_raw_expectancy', 0):+.4f}R | "
                      f"STANDARD exp={impact.get('standard_expectancy', 0):+.4f}R")
        md.append("")

    # Performance
    md.append("## Performance")
    md.append("")
    md.append("| View | Trades | Win% | Expectancy | PF | PnL |")
    md.append("|---|---|---|---|---|---|")
    for view in ["FULL_RAW", "STANDARD", "FX", "INDEX", "COMMODITY"]:
        m = summary["performance"].get(view, {})
        if m.get("count", 0) == 0:
            continue
        pf = f"{m.get('profit_factor', 0):.1f}" if m.get("profit_factor", 0) < 900 else "inf"
        md.append(f"| {view} | {m['count']} | {m.get('win_rate',0):.0%} | "
                  f"{m.get('expectancy_r',0):+.2f} | {pf} | ${m.get('total_pnl',0):.2f} |")

    # Instrument rankings
    if summary.get("rankings"):
        md.append("")
        md.append("## Instrument Rankings")
        md.append("")
        for i, r in enumerate(summary["rankings"][:5], 1):
            md.append(f"{i}. **{r['symbol']}** -- {r['expectancy_r']:+.2f}R "
                      f"(n={r['count']}, {r['confidence']})")

    # Experiment conclusions
    md.append("")
    md.append("## Experiment Conclusions")
    md.append("")

    # Build header with instrument columns
    inst_cols = instrument_views[:3]  # Show top 3 instruments in table
    header = "| Experiment | FULL | FX | INDEX |"
    sep = "|---|---|---|---|"
    for sym in inst_cols:
        header += f" {sym} |"
        sep += "---|"
    md.append(header)
    md.append(sep)

    for exp_id, views in summary["experiments"].items():
        row = f"| {exp_id} | {views.get('FULL', '?')[:20]} | " \
              f"{views.get('FX_ONLY', '?')[:20]} | {views.get('INDEX_ONLY', '?')[:15]} |"
        for sym in inst_cols:
            row += f" {views.get(sym, '-')[:15]} |"
        md.append(row)

    # Comparison
    if comparison:
        md.append("")
        md.append("## Comparison vs Previous Cycle")
        md.append("")
        md.append(f"Status: **{comparison.get('status', 'N/A')}**")
        md.append("")
        for k, v in comparison.get("changes", {}).items():
            if isinstance(v, dict):
                md.append(f"- {k}:")
                for kk, vv in v.items():
                    md.append(f"  - {kk}: {vv}")
            else:
                md.append(f"- {k}: {v}")

    md.append("")
    md.append("---")
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    compare_flag = "--compare" in sys.argv

    print("=" * 56)
    print("  V10 RESEARCH CYCLE (V2)")
    print("=" * 56)

    result = run_research_cycle(compare=compare_flag)

    if "error" in result:
        print(f"\nERROR: {result['error']}")
        sys.exit(1)

    print(f"\n  Cycle: {result['cycle_id']}")
    print(f"  Duration: {result['duration_seconds']:.1f}s")

    ds = result["dataset"]
    print(f"\n  Dataset:")
    print(f"    Total: {ds['total_trades']} | Normal: {ds['normal_trades']} | Flagged: {ds['flagged_trades']}")
    print(f"    Symbols: {len(ds.get('symbols', []))}")

    print(f"\n  Performance:")
    for view in ["FULL_RAW", "STANDARD", "FX", "INDEX", "COMMODITY"]:
        m = result["performance"].get(view, {})
        if m.get("count", 0) > 0:
            print(f"    {view:14s}: n={m['count']:3d} win={m.get('win_rate',0):.0%} "
                  f"exp={m.get('expectancy_r',0):+.2f}R pnl=${m.get('total_pnl',0):.2f}")

    anom = result.get("anomaly_summary", {})
    if anom.get("flagged_count"):
        print(f"\n  Anomalies: {anom['flagged_count']} flagged")
        for reason, count in sorted(anom.get("reason_counts", {}).items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")

    print(f"\n  Experiments ({len(result['experiments'])}):")
    for exp_id, views in result["experiments"].items():
        conclusion = views.get("FULL", "?")[:30]
        print(f"    {exp_id:4s}: {conclusion}")

    if result.get("instrument_views_run"):
        print(f"\n  Instrument views: {', '.join(result['instrument_views_run'])}")

    if result.get("comparison"):
        print(f"\n  Comparison: {result['comparison'].get('status', 'N/A')}")

    print(f"\n  Reports: {result.get('cycle_dir', '')}")
    print("=" * 56)
