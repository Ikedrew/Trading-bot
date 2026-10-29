"""
Post-Execution Handler — Fire-and-forget effects after successful trade execution.

Handles all observational side effects that occur after a trade has been
successfully executed and registered. None of these affect trading decisions.

This module OWNS:
    - Discord trade executed notification
    - Slippage recording
    - Paper engine outcome tracking
    - Cohort policy assignment
    - Trade event emission

This module does NOT own:
    - Deciding whether to trade
    - Broker execution
    - Decision ledger writes
    - Trade manager registration
    - Runtime state mutations (engine_state, daily_trade_limit)
    - Runtime loop control

Design: fire-and-forget effects — never raises, never controls flow.
"""

from __future__ import annotations

import logging
from typing import Any

from core.event_bus import emit_trade_events
from core.slippage_monitor import record_slippage
from execution.mt5_execution import describe_retcode

logger = logging.getLogger(__name__)


def emit_post_trade_success(
    *,
    symbol: str,
    intent: Any,
    result: Any,
    score_value: int,
    closed_i: int,
    closed_time: int,
    bias_value: str,
    config: Any,
    new_result: dict | None,
    unified: Any,
    engine_state: Any,
) -> None:
    """
    Emit all fire-and-forget side effects after successful trade execution.

    Called AFTER:
        - decision ledger finalized
        - trade manager registered

    Never raises. All effects are independently try/except guarded.

    Args:
        symbol: Symbol traded.
        intent: OrderIntent used for execution.
        result: Broker execution result.
        score_value: Signal score (int).
        closed_i: Closed bar index.
        closed_time: Bar close timestamp.
        bias_value: Current bias value string.
        config: Configuration object.
        new_result: New engine result dict (may be None for old pipeline).
        unified: Old pipeline unified result (may be None for new engine).
        engine_state: Current engine state.
    """
    # ─── 1. DISCORD: Trade executed notification ──────────────────────
    try:
        _dl = getattr(config, "_discord_logger", None)
        if _dl is not None:
            _discord_payload = {
                "symbol": symbol,
                "decision": "ALLOW",
                "pattern": intent.pattern,
                "score": score_value,
                "side": intent.side.name,
            }
            # Attach reasoning summary for Discord
            _exec_r = new_result.get("reasoning") if new_result else None
            if _exec_r and hasattr(_exec_r, "primary_thesis"):
                _discord_payload["thesis"] = _exec_r.primary_thesis
                _discord_payload["supporting"] = list(_exec_r.supporting_evidence)[:5]
                _discord_payload["contradicting"] = list(_exec_r.contradicting_evidence)[:3]
                if _exec_r.alternative_thesis:
                    _discord_payload["alternative"] = _exec_r.alternative_thesis
            _dl.event("TRADE_DECISION", _discord_payload)
    except Exception:
        pass

    # ─── 2. SLIPPAGE RECORDING ────────────────────────────────────────
    try:
        _expected_px = intent.entry_reference
        _actual_fill = result.fill_price if result.fill_price else _expected_px
        record_slippage(
            symbol=symbol,
            expected_price=_expected_px,
            fill_price=_actual_fill,
        )
    except Exception:
        pass  # Slippage monitoring must never affect execution

    # ─── 3. PAPER ENGINE: Record executed trade ───────────────────────
    try:
        from core.pipeline.paper_outcome_engine import get_paper_engine
        get_paper_engine().record_signal(
            symbol=symbol,
            source="executed_trade",
            side=intent.side.name,
            entry_price=intent.entry_reference,
            stop_loss=intent.sl,
            take_profit=intent.tp,
            pattern=intent.pattern,
            score=float(score_value),
            bar_index=closed_i,
        )
    except Exception:
        pass  # Paper engine must never affect execution

    # ─── 4. COHORT POLICY ASSIGNMENT ──────────────────────────────────
    try:
        from tools.cohort_analysis.policy_adapter import assign_policy_to_trade
        _trade_meta: dict = {
            "symbol": symbol,
            "confirmation": {
                "strength": "STRONG",  # Default — legacy layer_confirmation not always available
                "body_pct": 0.0,
                "wick_ratio": 0.0,
                "close_location": 0.0,
            },
            "entry_timing": getattr(unified, "entry_timing", None) if unified else None,
            "engine_state": {"regime_state": engine_state.regime_state},
        }
        assign_policy_to_trade(_trade_meta, _trade_meta)
        logger.info(
            "[COHORT_POLICY] symbol=%s cohort=%s policy=%s",
            symbol,
            _trade_meta.get("cohort"),
            _trade_meta.get("management_policy").name if _trade_meta.get("management_policy") else "NONE",
        )
    except Exception:
        pass  # Cohort policy assignment must never affect execution

    # ─── 5. TRADE EVENTS ──────────────────────────────────────────────
    emit_trade_events(
        candle_i=closed_i,
        candle_time=closed_time,
        bias_value=bias_value,
        score_value=score_value,
        should_trade=True,
        execution_ok=True,
    )


def emit_post_trade_failure(
    *,
    result: Any,
    closed_i: int,
    closed_time: int,
    bias_value: str,
    score_value: int,
) -> None:
    """
    Emit trade events for broker rejection. Never raises.

    Called AFTER decision ledger has been finalized with failure outcome.
    """
    try:
        emit_trade_events(
            candle_i=closed_i,
            candle_time=closed_time,
            bias_value=bias_value,
            score_value=score_value,
            should_trade=True,
            execution_ok=False,
            reject_reason=describe_retcode(result.retcode),
        )
    except Exception:
        pass
