"""
Stability Gate — Pure policy evaluator for final trade admission.

Fully isolated. No engine/runtime/execution imports.
No logging, no broker access, no state mutation, no file I/O, no side effects.

This is the last gate before run_build_intent(). It determines whether a
finalized trade should still be allowed after all normal strategy/risk checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── DECISION TYPE ────────────────────────────────────────────────────────────

@dataclass
class StabilityDecision:
    """Result of stability gate evaluation."""

    allow_trade: bool
    mode: str       # "NORMAL" | "PROTECT" | "RUNNER"
    reason: str


# ─── PURE EVALUATOR ──────────────────────────────────────────────────────────

def evaluate_stability_policy(
    snapshot: Any,
    policy_registry: dict[str, Any],
) -> StabilityDecision:
    """
    Evaluate whether a trade should proceed based on system stability.

    Pure function. No side effects. No imports from engine/runtime.

    Args:
        snapshot: Object exposing system state attributes (safe access via getattr).
        policy_registry: Plain dict with policy thresholds and blocked states.

    Returns:
        StabilityDecision with allow_trade, mode, and reason.
    """
    # ─── Extract snapshot fields (safe access) ────────────────────────
    drawdown_state = getattr(snapshot, "drawdown_state", "NORMAL")
    recent_loss_streak = getattr(snapshot, "recent_loss_streak", 0)
    session_quality = getattr(snapshot, "session_quality", "NORMAL")
    volatility_state = getattr(snapshot, "volatility_state", "STABLE")
    spread_state = getattr(snapshot, "spread_state", "NORMAL")
    market_regime = getattr(snapshot, "market_regime", "UNKNOWN")
    confidence_score = getattr(snapshot, "confidence_score", 5.0)

    # ─── Extract registry thresholds (safe defaults) ──────────────────
    max_loss_streak = policy_registry.get("max_loss_streak", 3)
    blocked_drawdown_states = policy_registry.get("blocked_drawdown_states", ["LOCKED"])
    blocked_sessions = policy_registry.get("blocked_sessions", ["DEAD"])
    blocked_volatility = policy_registry.get("blocked_volatility", ["CHAOTIC"])
    blocked_spread = policy_registry.get("blocked_spread", ["WIDE"])
    runner_confidence_min = policy_registry.get("runner_confidence_min", 8.5)
    protect_confidence_max = policy_registry.get("protect_confidence_max", 6.0)

    # ─── A. HARD BLOCKS (deny trade) ─────────────────────────────────

    # A1. Drawdown locked
    if drawdown_state in blocked_drawdown_states:
        return StabilityDecision(
            allow_trade=False,
            mode="PROTECT",
            reason="drawdown_lock",
        )

    # A2. Loss streak exceeded
    if recent_loss_streak >= max_loss_streak:
        return StabilityDecision(
            allow_trade=False,
            mode="PROTECT",
            reason="loss_streak_limit",
        )

    # A3. Dead session
    if session_quality in blocked_sessions:
        return StabilityDecision(
            allow_trade=False,
            mode="PROTECT",
            reason="dead_session",
        )

    # A4. Volatility unstable
    if volatility_state in blocked_volatility:
        return StabilityDecision(
            allow_trade=False,
            mode="PROTECT",
            reason="volatility_block",
        )

    # A5. Spread unstable
    if spread_state in blocked_spread:
        return StabilityDecision(
            allow_trade=False,
            mode="PROTECT",
            reason="spread_block",
        )

    # ─── B. RUNNER CONDITIONS ─────────────────────────────────────────

    if (
        confidence_score >= runner_confidence_min
        and market_regime == "TRENDING"
        and session_quality == "HIGH"
        and volatility_state == "STABLE"
        and spread_state == "TIGHT"
    ):
        return StabilityDecision(
            allow_trade=True,
            mode="RUNNER",
            reason="high_stability",
        )

    # ─── C. PROTECTIVE MODE ───────────────────────────────────────────

    if confidence_score <= protect_confidence_max:
        return StabilityDecision(
            allow_trade=True,
            mode="PROTECT",
            reason="low_confidence",
        )

    # ─── D. DEFAULT ───────────────────────────────────────────────────

    return StabilityDecision(
        allow_trade=True,
        mode="NORMAL",
        reason="stable",
    )
