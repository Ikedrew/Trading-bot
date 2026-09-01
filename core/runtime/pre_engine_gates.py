"""
Pre-Engine Gates — Permission checks before engine evaluation.

Evaluates whether a symbol may proceed into the strategy engine for this bar.
Gates run in strict order; first failure short-circuits remaining checks.

This module OWNS:
    - Pre-engine permission evaluation
    - Gate ordering (kill switch → daily loss → session → pattern)
    - Gate pass/fail decisions
    - Early rejection reasons
    - GateResult creation

This module does NOT own:
    - Engine decisions
    - Strategy selection
    - Execution logic
    - Risk management after engine evaluation
    - Trade placement
    - Observer dispatch
    - Cycle control (continue/break)
    - Decision ledger writes
    - Paper outcome tracking on reject

Design: pure evaluation — returns GateResult, never controls flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass
class GateResult:
    """Result of pre-engine gate evaluation."""

    allowed: bool
    """True if all gates passed and engine may evaluate."""

    # Block details (only set when allowed=False)
    block_outcome: str = ""
    """DecisionOutcome value string when blocked."""
    block_reason: str = ""
    """Reason string for the block."""
    block_risk_flag: str = ""
    """Risk flag for the block (if applicable)."""
    block_session_state: str = ""
    """Session state for the block (if applicable)."""

    # Pattern gate output (only set when allowed=True)
    raw_patterns: list[Any] = field(default_factory=list)
    """Detected patterns from the pattern gate (non-empty when allowed)."""


# ─── PRE-ENGINE GATES ─────────────────────────────────────────────────────────

def evaluate_pre_engine_gates(
    *,
    kill_active: bool,
    daily_loss_blocked: bool,
    candles: Any,
    closed_i: int,
    symbol: str,
    cycle_id: int,
    closed_time: int,
) -> GateResult:
    """
    Evaluate all pre-engine gates in order. First failure short-circuits.

    Gate ordering (preserved from original):
        1. Kill switch check
        2. Daily loss limit block
        3. Session guard
        4. Pattern gate

    Args:
        kill_active: Whether kill switch is active.
        daily_loss_blocked: Whether daily loss limit is breached.
        candles: Candle array for pattern detection.
        closed_i: Index of closed bar.
        symbol: Current symbol being evaluated.
        cycle_id: Current cycle number.
        closed_time: Bar close timestamp (for logging).

    Returns:
        GateResult with allowed=True and raw_patterns if all gates pass.
        GateResult with allowed=False and block details if any gate fails.
    """
    # ─── 1. KILL SWITCH CHECK ─────────────────────────────────────────
    if kill_active:
        return GateResult(
            allowed=False,
            block_outcome="KILL_SWITCH",
            block_reason="kill_switch_active",
            block_session_state="blocked",
        )

    # ─── 2. DAILY LOSS LIMIT BLOCK ───────────────────────────────────
    if daily_loss_blocked:
        return GateResult(
            allowed=False,
            block_outcome="DAILY_LOSS_BLOCK",
            block_reason="daily_loss_limit_reached",
            block_risk_flag="daily_loss",
        )

    # ─── 3. SESSION GUARD ─────────────────────────────────────────────
    print(f"[PIPELINE ENTRY] symbol={symbol} cycle={cycle_id} bar_time={closed_time} entering evaluation")
    from risk.session_guard import check_session
    _session_result = check_session()
    print(f"[SESSION CHECK] {symbol} allowed={_session_result.allowed} reason={getattr(_session_result, 'reason', '')}")
    if not _session_result.allowed:
        return GateResult(
            allowed=False,
            block_outcome="SESSION_BLOCK",
            block_reason=getattr(_session_result, "reason", "outside_trading_hours"),
            block_session_state="blocked",
        )

    # ─── 4. PATTERN GATE ──────────────────────────────────────────────
    from strategy.signal_orchestrator import evaluate_closed_bar as _detect_patterns
    _raw_patterns = _detect_patterns(candles, closed_i)
    print(f"[PATTERN RESULT] {symbol} count={len(_raw_patterns)}")
    if not _raw_patterns:
        print(f"[PATTERN GATE] symbol={symbol} cycle={cycle_id} — no patterns detected, skipping pipeline")
        return GateResult(
            allowed=False,
            block_outcome="PATTERN_REJECT",
            block_reason="no_patterns_detected",
        )
    print(f"[PATTERN GATE] symbol={symbol} cycle={cycle_id} — {len(_raw_patterns)} pattern(s): {[s.pattern for s in _raw_patterns]}")

    # ─── ALL GATES PASSED ─────────────────────────────────────────────
    return GateResult(
        allowed=True,
        raw_patterns=_raw_patterns,
    )
