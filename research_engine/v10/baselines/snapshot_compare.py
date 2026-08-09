"""
Baseline Snapshot — Comparison engine.

Compares two snapshots to determine what changed.
Does NOT optimise. Only measures differences.
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.baselines.models import BaselineSnapshot


def compare_snapshots(
    baseline: BaselineSnapshot,
    candidate: BaselineSnapshot,
) -> dict[str, Any]:
    """
    Compare two baseline snapshots.

    Returns:
        {
            "baseline_id": str,
            "candidate_id": str,
            "performance_delta": {metric: {baseline, candidate, change}},
            "configuration_changes": [...],
            "dataset_changes": {...},
            "summary": str,
        }
    """
    # Performance comparison
    b_perf = baseline.performance_metrics or {}
    c_perf = candidate.performance_metrics or {}

    perf_metrics = [
        "trade_count", "win_rate", "expectancy_r",
        "profit_factor", "net_realised_pnl", "average_r",
    ]

    performance_delta = {}
    for metric in perf_metrics:
        b_val = b_perf.get(metric, 0) or 0
        c_val = c_perf.get(metric, 0) or 0
        change = c_val - b_val
        performance_delta[metric] = {
            "baseline": round(b_val, 4) if isinstance(b_val, float) else b_val,
            "candidate": round(c_val, 4) if isinstance(c_val, float) else c_val,
            "change": round(change, 4) if isinstance(change, float) else change,
        }

    # Configuration changes
    config_changes = _diff_dicts(baseline.configuration, candidate.configuration, "configuration")
    config_changes += _diff_dicts(baseline.risk_configuration, candidate.risk_configuration, "risk")
    config_changes += _diff_dicts(baseline.strategy_configuration, candidate.strategy_configuration, "strategy")

    # Dataset changes
    b_ds = baseline.dataset_metadata or {}
    c_ds = candidate.dataset_metadata or {}
    dataset_changes = {
        "records_baseline": b_ds.get("records", 0),
        "records_candidate": c_ds.get("records", 0),
        "hash_changed": b_ds.get("hash", "") != c_ds.get("hash", ""),
        "same_dataset": b_ds.get("hash", "") == c_ds.get("hash", "") and b_ds.get("hash", "") != "",
    }

    # Summary
    exp_change = performance_delta.get("expectancy_r", {}).get("change", 0)
    if exp_change > 0.05:
        summary = f"IMPROVED: expectancy +{exp_change:.4f}R"
    elif exp_change < -0.05:
        summary = f"DEGRADED: expectancy {exp_change:.4f}R"
    else:
        summary = f"STABLE: expectancy change {exp_change:.4f}R"

    return {
        "baseline_id": baseline.snapshot_id,
        "candidate_id": candidate.snapshot_id,
        "performance_delta": performance_delta,
        "configuration_changes": config_changes,
        "dataset_changes": dataset_changes,
        "summary": summary,
    }


def _diff_dicts(a: dict, b: dict, prefix: str) -> list[dict[str, Any]]:
    """Find differences between two config dicts."""
    changes = []
    all_keys = set(list(a.keys()) + list(b.keys()))
    for key in sorted(all_keys):
        a_val = a.get(key)
        b_val = b.get(key)
        if a_val != b_val:
            changes.append({
                "field": f"{prefix}.{key}",
                "baseline": a_val,
                "candidate": b_val,
            })
    return changes
