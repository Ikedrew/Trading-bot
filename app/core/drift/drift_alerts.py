"""
Drift Alerts — Emits structured alerts when drift is detected.

Output destinations:
  - Logging (immediate)
  - Optional Discord webhook (future Phase F1 expansion)

NO execution changes. NO parameter modification. PURE alerting layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.drift.drift_classifier import DriftStatus
from core.drift.drift_scoring import DriftMetrics
from core.drift.regime_drift_detector import RegimeDriftReport

logger = logging.getLogger(__name__)


# ─── SEVERITY LEVELS ──────────────────────────────────────────────────────────

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


# ─── ALERT TYPE ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DriftAlert:
    """Structured drift alert for logging and notification."""

    timestamp: str
    severity: str             # "info" | "warning" | "critical"
    cohort_key: str
    drift_score: float
    explanation: str
    recommended_action: str   # Human-readable — NOT auto-applied


# ─── ALERT GENERATION ─────────────────────────────────────────────────────────

def generate_drift_alerts(
    drift_results: list[tuple[DriftMetrics, DriftStatus]],
    regime_report: RegimeDriftReport | None = None,
) -> list[DriftAlert]:
    """
    Generate structured alerts from drift classification results.

    Rules:
        BROKEN_EDGE → critical alert
        DEGRADED    → warning alert
        WARNING     → info alert
        STABLE      → no alert

    Optionally incorporates regime drift report for richer explanations.

    Args:
        drift_results: List of (DriftMetrics, DriftStatus) tuples per cohort.
        regime_report: Optional RegimeDriftReport for cause enrichment.

    Returns:
        List of DriftAlert objects (only for non-STABLE cohorts).
    """
    alerts: list[DriftAlert] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Build regime context lookup
    regime_causes: dict[str, str] = {}
    if regime_report and regime_report.affected_cohorts:
        for affected in regime_report.affected_cohorts:
            regime_causes[affected.cohort_key] = affected.pattern

    for metrics, status in drift_results:
        if status == DriftStatus.STABLE:
            continue

        severity = _status_to_severity(status)
        explanation = _build_explanation(metrics, status, regime_causes)
        action = _recommend_action(metrics, status)

        alert = DriftAlert(
            timestamp=now,
            severity=severity,
            cohort_key=metrics.cohort_key,
            drift_score=metrics.composite_drift_score,
            explanation=explanation,
            recommended_action=action,
        )
        alerts.append(alert)

    # Emit to logs
    _emit_to_logs(alerts)

    return alerts


# ─── SEVERITY MAPPING ─────────────────────────────────────────────────────────

def _status_to_severity(status: DriftStatus) -> str:
    """Map DriftStatus to alert severity."""
    if status == DriftStatus.BROKEN_EDGE:
        return SEVERITY_CRITICAL
    if status == DriftStatus.DEGRADED:
        return SEVERITY_WARNING
    if status == DriftStatus.WARNING:
        return SEVERITY_INFO
    return SEVERITY_INFO


# ─── EXPLANATION BUILDER ──────────────────────────────────────────────────────

def _build_explanation(
    metrics: DriftMetrics,
    status: DriftStatus,
    regime_causes: dict[str, str],
) -> str:
    """Build human-readable explanation of the drift."""
    parts: list[str] = []

    parts.append(f"Cohort {metrics.cohort_key} classified as {status.value}.")
    parts.append(f"Composite drift score: {metrics.composite_drift_score:.3f}.")
    parts.append(f"Direction: {metrics.direction}.")

    if metrics.expectancy_drift != 0:
        sign = "+" if metrics.expectancy_drift > 0 else ""
        parts.append(f"Expectancy shift: {sign}{metrics.expectancy_drift:.3f}R.")

    if metrics.winrate_drift != 0:
        sign = "+" if metrics.winrate_drift > 0 else ""
        parts.append(f"Win rate shift: {sign}{metrics.winrate_drift:.3f}.")

    if metrics.variance_drift > 0.5:
        parts.append(f"Variance increased by {metrics.variance_drift:.3f} (instability signal).")

    # Regime cause enrichment
    regime_pattern = regime_causes.get(metrics.cohort_key)
    if regime_pattern:
        parts.append(f"Regime pattern: {regime_pattern}.")

    return " ".join(parts)


# ─── ACTION RECOMMENDATIONS ──────────────────────────────────────────────────

def _recommend_action(metrics: DriftMetrics, status: DriftStatus) -> str:
    """Generate recommended action (NOT auto-applied)."""
    if status == DriftStatus.BROKEN_EDGE:
        return (
            "REVIEW IMMEDIATELY: Consider disabling this cohort from active trading. "
            "Run calibration engine to assess parameter adjustments. "
            "Verify regime filter accuracy for this cohort."
        )

    if status == DriftStatus.DEGRADED:
        return (
            "MONITOR CLOSELY: Reduce position sizing for this cohort if manual override available. "
            "Schedule calibration review within next session. "
            "Check if regime conditions have structurally changed."
        )

    if status == DriftStatus.WARNING:
        return (
            "OBSERVE: Performance slightly below baseline. "
            "No immediate action required. "
            "Flag for next calibration cycle review."
        )

    return "No action required."


# ─── LOG EMISSION ─────────────────────────────────────────────────────────────

def _emit_to_logs(alerts: list[DriftAlert]) -> None:
    """Emit all alerts to structured logging."""
    for alert in alerts:
        log_msg = (
            f"[DRIFT_ALERT] severity={alert.severity} "
            f"cohort={alert.cohort_key} "
            f"score={alert.drift_score:.3f} "
            f"action={alert.recommended_action[:80]}"
        )

        if alert.severity == SEVERITY_CRITICAL:
            logger.critical(log_msg)
        elif alert.severity == SEVERITY_WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
