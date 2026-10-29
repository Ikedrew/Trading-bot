"""
Calibration Applier — Optionally applies calibration results to config.

MUST BE MANUAL OR FEATURE-GATED.
NEVER automatic in live trading.

Only applies when config.CALIBRATION_MODE == "ENABLED".
Logs all before/after changes for audit trail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.calibration.calibration_aggregator import GlobalCalibrationPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplyResult:
    """Result of calibration apply attempt."""

    applied: bool
    changes: list[str]
    reason: str


def apply_calibration(plan: GlobalCalibrationPlan, config: Any) -> ApplyResult:
    """
    Apply calibration plan to config if CALIBRATION_MODE is ENABLED.

    Args:
        plan: GlobalCalibrationPlan from calibration_aggregator.
        config: The config module (core.config or equivalent).

    Returns:
        ApplyResult indicating whether changes were applied.
    """
    mode = getattr(config, "CALIBRATION_MODE", "DISABLED")

    if mode != "ENABLED":
        return ApplyResult(
            applied=False,
            changes=[],
            reason=f"CALIBRATION_MODE={mode} (must be ENABLED to apply)",
        )

    if not plan.applied_cohorts:
        return ApplyResult(
            applied=False,
            changes=[],
            reason="No cohorts in plan — nothing to apply",
        )

    changes: list[str] = []

    # Break-even trigger RR
    old_be = getattr(config, "TM_BREAK_EVEN_TRIGGER_RR", 0.0)
    new_be = plan.final_break_even_rr
    if old_be != new_be:
        _safe_set(config, "TM_BREAK_EVEN_TRIGGER_RR", new_be)
        changes.append(f"TM_BREAK_EVEN_TRIGGER_RR: {old_be} → {new_be}")

    # Trailing start RR
    old_trail = getattr(config, "TM_TRAILING_START_RR", 0.0)
    new_trail = plan.final_trailing_start_rr
    if old_trail != new_trail:
        _safe_set(config, "TM_TRAILING_START_RR", new_trail)
        changes.append(f"TM_TRAILING_START_RR: {old_trail} → {new_trail}")

    # Trailing step
    old_step = getattr(config, "TM_TRAILING_STEP", 0.0)
    new_step = plan.final_trailing_step
    if old_step != new_step:
        _safe_set(config, "TM_TRAILING_STEP", new_step)
        changes.append(f"TM_TRAILING_STEP: {old_step} → {new_step}")

    # Partial TP
    old_partial_val = getattr(config, "TM_PARTIAL_TP_FRACTION", 0.0)
    old_partial = float(old_partial_val) > 0 if isinstance(old_partial_val, (int, float)) else False
    new_partial = plan.final_partial_tp_state
    if old_partial != new_partial:
        fraction = 0.5 if new_partial else 0.0
        _safe_set(config, "TM_PARTIAL_TP_FRACTION", fraction)
        changes.append(f"TM_PARTIAL_TP_FRACTION: {0.5 if old_partial else 0.0} → {fraction}")

    if changes:
        logger.info(
            "[CALIBRATION_APPLIED] cohorts=%s changes=%d",
            plan.applied_cohorts, len(changes),
        )
        for c in changes:
            logger.info("[CALIBRATION_CHANGE] %s", c)
    else:
        logger.info("[CALIBRATION_APPLIED] No parameter changes needed")

    return ApplyResult(
        applied=len(changes) > 0,
        changes=changes,
        reason=f"Applied from {len(plan.applied_cohorts)} cohorts" if changes else "No changes needed",
    )


def _safe_set(config: Any, key: str, value: Any) -> None:
    """Safely set config attribute. Handles frozen config gracefully."""
    try:
        setattr(config, key, value)
    except (AttributeError, RuntimeError) as exc:
        logger.warning("[CALIBRATION_SET_FAILED] key=%s error=%s", key, exc)
