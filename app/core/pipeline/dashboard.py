"""
Pipeline flow dashboard — tracks where decisions are rejected.

Purely observational. Never affects trading logic.
Provides real-time strictness measurement and bottleneck identification.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─── METRICS STATE ────────────────────────────────────────────────────────────

_metrics: dict[str, int] = {
    "cycles": 0,
    "rejected_market_filter": 0,
    "rejected_bias": 0,
    "rejected_pattern": 0,
    "rejected_confirmation": 0,
    "rejected_chop": 0,
    "rejected_trend": 0,
    "rejected_direction_cooldown": 0,
    "rejected_score": 0,
    "rejected_max_positions": 0,
    "rejected_cooldown": 0,
    "rejected_bias_expired": 0,
    "rejected_risk": 0,
    "rejected_execution": 0,
    "trades_executed": 0,
    "no_setup": 0,
}

_DASHBOARD_INTERVAL = 25  # Emit summary every N cycles


# ─── RECORDING API ────────────────────────────────────────────────────────────

def record_cycle() -> None:
    """Increment cycle counter. Call once per bar evaluation."""
    _metrics["cycles"] += 1
    if _metrics["cycles"] % _DASHBOARD_INTERVAL == 0:
        _emit_dashboard()


def record_rejection(gate: str) -> None:
    """Record a rejection at a specific gate."""
    key = f"rejected_{gate}"
    if key in _metrics:
        _metrics[key] += 1
    else:
        _metrics[key] = 1


def record_trade_executed() -> None:
    """Record a successful trade execution."""
    _metrics["trades_executed"] += 1


def record_no_setup() -> None:
    """Record a cycle with no valid setup."""
    _metrics["no_setup"] += 1


# ─── QUERY API ────────────────────────────────────────────────────────────────

def get_dashboard_metrics() -> dict[str, int]:
    """Return full metrics snapshot."""
    return dict(_metrics)


def get_strictness_ratio() -> float:
    """Compute total rejections / cycles."""
    cycles = _metrics["cycles"]
    if cycles == 0:
        return 0.0
    total_rejections = sum(v for k, v in _metrics.items() if k.startswith("rejected_"))
    return total_rejections / cycles


# ─── LOGGING ──────────────────────────────────────────────────────────────────

def _emit_dashboard() -> None:
    """Emit periodic dashboard summary."""
    try:
        logger.info(
            "[DASHBOARD] cycles=%d | bias=%d | pattern=%d | confirm=%d | chop=%d | trend=%d | "
            "score=%d | positions=%d | cooldown=%d | risk=%d | exec=%d | trades=%d | strictness=%.2f",
            _metrics["cycles"],
            _metrics["rejected_bias"],
            _metrics["rejected_pattern"],
            _metrics["rejected_confirmation"],
            _metrics["rejected_chop"],
            _metrics["rejected_trend"],
            _metrics["rejected_score"],
            _metrics["rejected_max_positions"],
            _metrics["rejected_cooldown"],
            _metrics["rejected_risk"],
            _metrics["rejected_execution"],
            _metrics["trades_executed"],
            get_strictness_ratio(),
        )
    except Exception:
        pass


def emit_dashboard_now() -> None:
    """Force emit dashboard (e.g. on shutdown)."""
    _emit_dashboard()


def reset() -> None:
    """Reset all counters."""
    for k in _metrics:
        _metrics[k] = 0
