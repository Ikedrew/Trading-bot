"""
Validation Lab — Regression Checker.

Detects whether a candidate improvement comes at the cost of degrading
other system properties.
"""

from __future__ import annotations

from typing import Any


# Thresholds for regression detection
_EXPECTANCY_REGRESSION = -0.1    # R deterioration
_PF_REGRESSION = -0.3            # Profit factor drop
_WIN_RATE_REGRESSION = -0.05     # Win rate drop (5 percentage points)
_TRADE_COUNT_MIN_RATIO = 0.5     # Candidate must retain 50% of baseline trades


def check_regressions(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """
    Check for regressions between baseline and candidate.

    Returns:
        {
            "regressions_detected": bool,
            "regressions": [{metric, baseline, candidate, delta, severity}],
            "trade_count_concern": bool,
            "status": "PASS" | "REGRESSION_DETECTED" | "SEVERE_REGRESSION",
        }
    """
    regressions = []

    # Expectancy regression
    b_exp = baseline.get("expectancy_r", 0) or 0
    c_exp = candidate.get("expectancy_r", 0) or 0
    exp_delta = c_exp - b_exp
    if exp_delta < _EXPECTANCY_REGRESSION:
        severity = "SEVERE" if exp_delta < -0.25 else "MODERATE"
        regressions.append({
            "metric": "expectancy_r",
            "baseline": b_exp,
            "candidate": c_exp,
            "delta": round(exp_delta, 4),
            "severity": severity,
        })

    # Profit factor regression
    b_pf = baseline.get("profit_factor", 0) or 0
    c_pf = candidate.get("profit_factor", 0) or 0
    pf_delta = c_pf - b_pf
    if pf_delta < _PF_REGRESSION and b_pf > 0:
        severity = "SEVERE" if pf_delta < -0.5 else "MODERATE"
        regressions.append({
            "metric": "profit_factor",
            "baseline": b_pf,
            "candidate": c_pf,
            "delta": round(pf_delta, 2),
            "severity": severity,
        })

    # Win rate regression
    b_wr = baseline.get("win_rate", 0) or 0
    c_wr = candidate.get("win_rate", 0) or 0
    wr_delta = c_wr - b_wr
    if wr_delta < _WIN_RATE_REGRESSION:
        regressions.append({
            "metric": "win_rate",
            "baseline": b_wr,
            "candidate": c_wr,
            "delta": round(wr_delta, 4),
            "severity": "MODERATE",
        })

    # Trade count concern (candidate filtered out too many trades)
    b_count = baseline.get("count", 0) or baseline.get("sample_size", 0) or 0
    c_count = candidate.get("count", 0) or candidate.get("sample_size", 0) or 0
    trade_count_concern = False
    if b_count > 0 and c_count < b_count * _TRADE_COUNT_MIN_RATIO:
        trade_count_concern = True
        regressions.append({
            "metric": "trade_count",
            "baseline": b_count,
            "candidate": c_count,
            "delta": c_count - b_count,
            "severity": "WARNING",
        })

    # Overall status
    has_severe = any(r["severity"] == "SEVERE" for r in regressions)
    if has_severe:
        status = "SEVERE_REGRESSION"
    elif regressions:
        status = "REGRESSION_DETECTED"
    else:
        status = "PASS"

    return {
        "regressions_detected": len(regressions) > 0,
        "regressions": regressions,
        "trade_count_concern": trade_count_concern,
        "status": status,
    }
