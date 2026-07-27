"""
Legacy Shadow Pipeline — Shadow divergence logging for new-engine-authority mode.

When the new engine is authority and ENABLE_LEGACY_SHADOW_PIPELINE is True,
runs the old pipeline in shadow mode to log divergences. Never affects live
trading decisions.

This module OWNS:
    - Legacy shadow pipeline execution (NO_TRADE path)
    - Shadow divergence comparison (EXECUTE path)
    - Paper outcome tracking for shadow signals
    - Divergence logging (console print)

This module does NOT own:
    - Live execution
    - Real trade placement
    - Risk management
    - Production decision engine
    - Runtime orchestration
    - Cycle control
    - MT5 management
    - Modifying live trade state

Design: fire-and-forget shadow — never raises, never affects live pipeline.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    """Result of legacy shadow pipeline execution."""
    old_action: str  # "EXECUTE" or "NO_TRADE"
    old_score: float = 0.0
    diverged: bool = False
    """True if old pipeline disagrees with new engine decision."""


# ─── SHADOW ON NO_TRADE PATH ─────────────────────────────────────────────────

def run_shadow_no_trade(
    *,
    candles: Any,
    closed_i: int,
    sym_state: Any,
    config: Any,
    bid: float,
    ask: float,
    closed_time: int,
    process_bar_fn: Any,
) -> ShadowResult | None:
    """
    Run legacy pipeline as shadow when new engine decided NO_TRADE.

    Only called when ENABLE_LEGACY_SHADOW_PIPELINE is True and new engine
    is authority with a NO_TRADE decision.

    Never raises. Returns ShadowResult or None on failure.
    """
    try:
        _shadow_es = copy.deepcopy(sym_state.engine_state)
        _shadow_old_unified = process_bar_fn(
            candles=candles, closed_i=closed_i,
            symbol=sym_state.symbol, config=config,
            risk=sym_state.risk, state=_shadow_es,
            bid=bid, ask=ask, now_s=float(closed_time),
            htf_context=None,
        )
        _old_shadow_action = "EXECUTE" if _shadow_old_unified.decision.should_trade else "NO_TRADE"
        if _old_shadow_action == "EXECUTE":
            print(f"[SHADOW DRIFT] {sym_state.symbol} | new=NO_TRADE old=EXECUTE | old would have traded (shadow only)")
        # Paper engine: track old system shadow signals
        if _old_shadow_action == "EXECUTE" and _shadow_old_unified.decision.intent is not None:
            try:
                from core.pipeline.paper_outcome_engine import get_paper_engine
                get_paper_engine().record_signal(
                    symbol=sym_state.symbol, source="old_system_shadow",
                    side=_shadow_old_unified.decision.intent.side.name,
                    entry_price=_shadow_old_unified.decision.intent.entry_reference,
                    stop_loss=_shadow_old_unified.decision.intent.sl,
                    take_profit=_shadow_old_unified.decision.intent.tp,
                    pattern=_shadow_old_unified.decision.intent.pattern,
                    score=float(_shadow_old_unified.decision.score),
                    bar_index=closed_i,
                )
            except Exception:
                pass

        return ShadowResult(
            old_action=_old_shadow_action,
            old_score=float(_shadow_old_unified.decision.score),
            diverged=(_old_shadow_action == "EXECUTE"),
        )
    except Exception:
        return None  # Shadow failure must never affect new pipeline


# ─── SHADOW ON EXECUTE PATH ──────────────────────────────────────────────────

def run_shadow_execute_comparison(
    *,
    sym_state: Any,
    unified: Any,
    new_engine_score: float,
    closed_i: int,
) -> ShadowResult | None:
    """
    Compare old pipeline result with new engine EXECUTE decision.

    Only called when new engine is authority, produced EXECUTE, and old
    pipeline result (unified) is available.

    Never raises. Returns ShadowResult or None on failure.
    """
    try:
        _old_would_trade = unified.decision.should_trade
        _old_score = float(unified.decision.score)
        if _old_would_trade:
            print(f"[SHADOW MATCH] {sym_state.symbol} | BOTH EXECUTE | old_score={_old_score:.0f} new_score={new_engine_score:.3f}")
        else:
            print(f"[SHADOW DRIFT] {sym_state.symbol} | new=EXECUTE old=NO_TRADE | old_reason={unified.decision.reason[:60]}")
        # Paper engine: record old system shadow signal
        if _old_would_trade and unified.decision.intent is not None:
            try:
                from core.pipeline.paper_outcome_engine import get_paper_engine
                get_paper_engine().record_signal(
                    symbol=sym_state.symbol, source="old_system_shadow",
                    side=unified.decision.intent.side.name,
                    entry_price=unified.decision.intent.entry_reference,
                    stop_loss=unified.decision.intent.sl,
                    take_profit=unified.decision.intent.tp,
                    pattern=unified.decision.intent.pattern,
                    score=_old_score, bar_index=closed_i,
                )
            except Exception:
                pass

        return ShadowResult(
            old_action="EXECUTE" if _old_would_trade else "NO_TRADE",
            old_score=_old_score,
            diverged=(not _old_would_trade),
        )
    except Exception:
        return None  # Shadow comparison must never block new engine
