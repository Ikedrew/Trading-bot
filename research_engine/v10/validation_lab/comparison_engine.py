"""
Validation Lab — Comparison Engine.

Compares baseline metrics against candidate metrics.
"""

from __future__ import annotations

from typing import Any


_COMPARISON_METRICS = [
    "expectancy_r", "profit_factor", "win_rate", "average_r",
    "total_pnl", "count",
]


def compare_metrics(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare baseline and candidate metric sets.

    Returns:
        {
            "changes": {metric: {"before": x, "after": y, "delta": z}},
            "improved_metrics": [str],
            "degraded_metrics": [str],
            "summary": str,
        }
    """
    changes = {}
    improved = []
    degraded = []

    for metric in _COMPARISON_METRICS:
        before = baseline.get(metric, 0) or 0
        after = candidate.get(metric, 0) or 0
        delta = after - before

        changes[metric] = {
            "before": round(before, 4) if isinstance(before, float) else before,
            "after": round(after, 4) if isinstance(after, float) else after,
            "delta": round(delta, 4) if isinstance(delta, float) else delta,
        }

        # Determine direction (higher is better for these metrics)
        if metric == "count":
            continue  # Count change is informational, not directional
        if delta > 0.01:
            improved.append(metric)
        elif delta < -0.01:
            degraded.append(metric)

    # Summary
    if improved and not degraded:
        summary = f"Candidate improved {len(improved)} metrics: {', '.join(improved)}"
    elif degraded and not improved:
        summary = f"Candidate degraded {len(degraded)} metrics: {', '.join(degraded)}"
    elif improved and degraded:
        summary = f"Mixed: improved {', '.join(improved)}; degraded {', '.join(degraded)}"
    else:
        summary = "No significant change detected"

    return {
        "changes": changes,
        "improved_metrics": improved,
        "degraded_metrics": degraded,
        "summary": summary,
    }
