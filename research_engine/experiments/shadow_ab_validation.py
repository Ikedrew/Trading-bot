"""
L7 — Shadow A/B Validation Experiment.

Question:
    Does a proposed strategy change outperform the currently promoted
    version when evaluated in shadow mode with statistical significance?

Compares: Control (current) vs Candidate (proposed change)
Measures: EV, win rate, drawdown, trade frequency, significance

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    _deep_get,
    build_fingerprint,
    build_report,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)

_MIN_SAMPLES_PER_ARM = 30
_SIGNIFICANCE_THRESHOLD = 1.96  # z for 95% confidence


def _two_sample_z_test(a: list[float], b: list[float]) -> tuple[float, bool]:
    """Two-sample z-test for difference in means. Returns (z_stat, significant)."""
    na, nb = len(a), len(b)
    if na < 5 or nb < 5:
        return 0.0, False
    mean_a = sum(a) / na
    mean_b = sum(b) / nb
    var_a = sum((x - mean_a) ** 2 for x in a) / (na - 1) if na > 1 else 0
    var_b = sum((x - mean_b) ** 2 for x in b) / (nb - 1) if nb > 1 else 0
    se = math.sqrt(var_a / na + var_b / nb) if (var_a / na + var_b / nb) > 0 else 0.001
    z = (mean_b - mean_a) / se
    return round(z, 4), abs(z) >= _SIGNIFICANCE_THRESHOLD


def _arm_stats(r_values: list[float]) -> dict[str, Any]:
    """Compute stats for one arm of the A/B test."""
    if not r_values:
        return {"n": 0, "ev": 0, "win_rate": 0, "max_dd": 0, "std_dev": 0}
    n = len(r_values)
    ev = sum(r_values) / n
    wr = sum(1 for r in r_values if r > 0) / n
    variance = sum((r - ev) ** 2 for r in r_values) / (n - 1) if n > 1 else 0
    # Max drawdown approximation
    cumsum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_values:
        cumsum += r
        peak = max(peak, cumsum)
        dd = peak - cumsum
        max_dd = max(max_dd, dd)
    return {"n": n, "ev": round(ev, 4), "win_rate": round(wr, 4), "max_dd": round(max_dd, 4), "std_dev": round(math.sqrt(variance), 4)}


def run_shadow_ab_validation(
    shadow_trades: list[dict[str, Any]] | None = None,
    control_label: str = "",
    candidate_label: str = "",
) -> dict[str, Any]:
    """
    Run L7: Shadow A/B Validation experiment.

    Splits shadow trades into control and candidate groups based on
    schema_version or strategy field, then compares performance.
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(
        shadow_trades, min_samples=_MIN_SAMPLES_PER_ARM * 2,
        require_outcome=True, require_strategy=True,
    )
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="L7", status=status, overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)), recommendation="WAIT", warnings=[reason],
        )

    # Split into control (first half) vs candidate (second half) chronologically
    # This simulates "old strategy" vs "new strategy" comparison
    r_values = extract_r_multiples(shadow_trades)
    n = len(r_values)
    if n < _MIN_SAMPLES_PER_ARM * 2:
        return build_report(
            question_id="L7", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} R-multiples (need {_MIN_SAMPLES_PER_ARM * 2})"},
            confidence="INSUFFICIENT_DATA", dataset={"r_multiples": n},
            fingerprint=build_fingerprint(n, len(shadow_trades) - n), recommendation="WAIT",
        )

    mid = n // 2
    control_r = r_values[:mid]
    candidate_r = r_values[mid:]

    control_stats = _arm_stats(control_r)
    candidate_stats = _arm_stats(candidate_r)

    # Statistical test
    z_stat, significant = _two_sample_z_test(control_r, candidate_r)

    # Determine winner
    ev_diff = candidate_stats["ev"] - control_stats["ev"]
    if significant and ev_diff > 0:
        winner = "CANDIDATE"
    elif significant and ev_diff < 0:
        winner = "CONTROL"
    else:
        winner = "INCONCLUSIVE"

    confidence = compute_confidence(n, significant)

    if winner == "CANDIDATE" and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = f"Candidate outperforms control by {ev_diff:+.4f}R (z={z_stat}, significant). Promote candidate."
    elif winner == "CONTROL":
        recommendation = "REJECT"
        finding = f"Control outperforms candidate by {-ev_diff:+.4f}R. Reject proposed change."
    else:
        recommendation = "WAIT"
        finding = f"No significant difference (z={z_stat}). Need more data or larger effect."

    report = build_report(
        question_id="L7", status=ReadinessStatus.COMPLETE,
        overall={
            "control": control_stats,
            "candidate": candidate_stats,
            "difference_ev": round(ev_diff, 4),
            "z_statistic": z_stat,
            "significant": significant,
            "winner": winner,
            "control_label": control_label or "first_half",
            "candidate_label": candidate_label or "second_half",
        },
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "r_multiples": n, "control_n": len(control_r), "candidate_n": len(candidate_r), "coverage": coverage},
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=["Chronological split: first half = control, second half = candidate", f"Significance level: 95% (z >= {_SIGNIFICANCE_THRESHOLD})", "Two-sample z-test for means"],
        warnings=[w for w in [f"Small sample per arm: {min(len(control_r), len(candidate_r))}" if min(len(control_r), len(candidate_r)) < 50 else ""] if w],
        provenance={"experiment_module": "research_engine.experiments.shadow_ab_validation", "registry_id": "L7", "function": "run_shadow_ab_validation", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "l7_shadow_ab_validation.json")
    update_knowledge_map("L7", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_shadow_ab_validation()
    o = result.get("overall", {})
    print(f"L7: winner={o.get('winner', '?')} diff={o.get('difference_ev', '?')} z={o.get('z_statistic', '?')} rec={result.get('recommendation')}")
