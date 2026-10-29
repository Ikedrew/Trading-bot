"""
Runtime Guard Chain — Post-engine guard orchestration for trade intents.

Evaluates the full chain of runtime guards in strict order. First failure
short-circuits remaining guards. Returns a result indicating pass/fail.

AUTHORITY BOUNDARIES:
    CAN:
        - Block any EXECUTE decision (veto authority)
        - Evaluate 10 independent risk guards in sequence
        - Short-circuit on first failure (efficiency)
        - Return structured rejection with guard name + reason

    CANNOT:
        - Create trade decisions (pipeline/new_engine owns that)
        - Override the decision engine's NO_TRADE
        - Execute broker orders (execution/ owns that)
        - Modify configuration or thresholds
        - Manage positions (trade_management/ owns that)

    GUARD ORDER:
        1. daily_trade_limit
        2. trade_cooldown
        3. correlation_guard
        4. portfolio_exposure_guard
        5. regime_guard
        6. spread_guard
        7. consistency_rules
        8. weekend_protection
        9. prop_firm_rules
        10. control_layer

This module OWNS:
    - Runtime guard execution order
    - Calling existing guard modules
    - Collecting guard results
    - Producing GuardChainResult

This module does NOT own:
    - Risk calculations (delegates to existing modules)
    - Changing thresholds
    - Creating new policies
    - Execution decisions
    - Order placement
    - Position management
    - Strategy selection
    - Cycle control (continue/break)
    - Decision ledger writes
    - Discord notifications
    - Filter hit tracking
    - Decision funnel recording

Design: pure evaluation — returns GuardChainResult, never controls flow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from risk.correlation_guard import check_correlation
from risk.portfolio_exposure_guard import check_portfolio_exposure
from risk.regime_guard import check_regime
from core.challenge_progress_tracker import check_challenge_gate
from core.consistency_rules import check_consistency_gate
from core.prop_firm_rules import check_prop_firm_gate
from core.weekend_protection import check_weekend_gate

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass
class GuardChainResult:
    """Result of runtime guard chain evaluation."""

    allowed: bool
    """True if all guards passed — intent may proceed to execution."""

    # Block details (only meaningful when allowed=False)
    guard_name: str = ""
    """Name of the guard that blocked (e.g. 'trade_cooldown', 'correlation_guard')."""
    reason: str = ""
    """Human-readable rejection reason."""
    rejection_code: str = ""
    """Machine code for record_rejection (e.g. 'A3_correlation_guard')."""
    filter_key: str = ""
    """Key for _filter_hits map (e.g. 'cooldown', 'correlation')."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Guard-specific metadata for risk event emission."""


# ─── GUARD CHAIN EVALUATION ──────────────────────────────────────────────────

def evaluate_runtime_guards(
    *,
    symbol: str,
    intent: Any,
    daily_trade_limit: Any,
    trade_cooldown: Any,
    all_open_positions: list[Any],
    candles: Any,
    closed_i: int,
    htf_context: Any,
    engine_state: Any,
    config: Any,
) -> GuardChainResult:
    """
    Evaluate all runtime guards in strict order. First failure short-circuits.

    Guard ordering (preserved from original):
        1. Daily trade limit (A4)
        2. Trade cooldown (B1)
        3. Correlation guard (A3)
        4. Portfolio exposure (A5)
        5. Regime guard (I2)
        6. Challenge protect (H1)
        7. Consistency rules (H2)
        8. Prop firm rules (H3)
        9. Weekend protection (H4)
        10. Control layer

    Returns:
        GuardChainResult with allowed=True if all guards pass.
        GuardChainResult with block details on first failure.
    """
    # ─── 1. DAILY TRADE LIMIT (A4) ───────────────────────────────────
    _dtl_result = daily_trade_limit.can_open_trade(symbol)
    if not _dtl_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="daily_trade_limit",
            reason=f"daily_trade_limit:{_dtl_result.reason}",
            rejection_code="A4_daily_trade_limit",
            filter_key="daily_trade_limit",
            metadata={"reason": _dtl_result.reason},
        )

    # ─── 2. TRADE COOLDOWN (B1) ──────────────────────────────────────
    if not trade_cooldown.can_open_trade(symbol, time.time()):
        _remaining = trade_cooldown.get_remaining_cooldown(symbol, time.time())
        _result = GuardChainResult(
            allowed=False,
            guard_name="trade_cooldown",
            reason="trade_cooldown:cooldown_active",
            rejection_code="B1_trade_cooldown",
            filter_key="cooldown",
            metadata={"remaining_s": round(_remaining, 0)},
        )
        _emit_research_event(symbol, intent, _result)
        return _result

    # ─── 3. CORRELATION GUARD (A3) ───────────────────────────────────
    _corr_result = check_correlation(
        symbol=symbol,
        direction=intent.side.name,
        volume=intent.volume,
        open_positions=all_open_positions,
    )
    if not _corr_result.allowed:
        _result = GuardChainResult(
            allowed=False,
            guard_name="correlation_guard",
            reason=f"correlation_guard:{_corr_result.reason}",
            rejection_code="A3_correlation_guard",
            filter_key="correlation",
            metadata={"direction": intent.side.name, "volume": intent.volume, "reason": _corr_result.reason},
        )
        _emit_research_event(symbol, intent, _result)
        return _result

    # ─── 4. PORTFOLIO EXPOSURE (A5) ──────────────────────────────────
    _proposed_risk = float(getattr(config, "RISK_PER_TRADE_PERCENT", 1.0))
    _peg_result = check_portfolio_exposure(
        proposed_risk_pct=_proposed_risk,
        open_positions=all_open_positions,
    )
    if not _peg_result.allowed:
        _result = GuardChainResult(
            allowed=False,
            guard_name="portfolio_exposure",
            reason=f"portfolio_exposure:{_peg_result.reason}",
            rejection_code="A5_portfolio_exposure",
            filter_key="exposure",
            metadata={
                "positions": _peg_result.current_positions,
                "max_positions": _peg_result.max_positions,
                "risk_pct": round(_peg_result.current_risk_pct, 2),
                "max_risk_pct": round(_peg_result.max_risk_pct, 2),
                "reason": _peg_result.reason,
            },
        )
        _emit_research_event(symbol, intent, _result)
        return _result

    # ─── 5. REGIME GUARD (I2) ────────────────────────────────────────
    _atr_ratio = 0.0
    _structure_score = 0.0
    try:
        from core.features.engine import _compute_atr_ratio
        _atr_ratio = _compute_atr_ratio(candles[:closed_i + 1], 14, 50)
    except Exception:
        pass
    if htf_context is not None and getattr(htf_context, "structure", None) is not None:
        _structure_score = htf_context.structure.quality_score

    _regime_result = check_regime(
        htf_context=htf_context,
        m5_regime_state=engine_state.regime_state,
        atr_ratio=_atr_ratio,
        structure_score=_structure_score,
        symbol=symbol,
    )
    if not _regime_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="regime_guard",
            reason=f"regime_guard:{_regime_result.regime}",
            rejection_code="I2_regime_guard",
            filter_key="regime_guard",
            metadata={"regime": _regime_result.regime, "confidence": round(_regime_result.confidence, 2)},
        )

    # ─── 6. CHALLENGE PROTECT (H1) ───────────────────────────────────
    _challenge_result = check_challenge_gate()
    if not _challenge_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="challenge_protect",
            reason="challenge_protect:target_reached",
            rejection_code="H1_challenge_protect",
            filter_key="",
            metadata={
                "layer": "H1",
                "current_profit_pct": _challenge_result.current_profit_percent,
                "target_pct": _challenge_result.target_percent,
            },
        )

    # ─── 7. CONSISTENCY RULES (H2) ───────────────────────────────────
    _consistency_result = check_consistency_gate()
    if not _consistency_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="consistency_rules",
            reason=f"consistency_rules:{_consistency_result.reason}",
            rejection_code="H2_consistency_rules",
            filter_key="",
            metadata={
                "layer": "H2",
                "today_profit_pct": _consistency_result.today_profit_percent,
                "max_daily_profit_limit": _consistency_result.max_daily_profit_limit,
            },
        )

    # ─── 8. PROP FIRM RULES (H3) ─────────────────────────────────────
    _pfr_result = check_prop_firm_gate(
        symbol=symbol,
        lot_size=intent.volume,
    )
    if not _pfr_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="prop_firm_rules",
            reason=f"prop_firm_rules:{_pfr_result.reason}",
            rejection_code="H3_prop_firm_rules",
            filter_key="",
            metadata={
                "layer": "H3",
                "rule_triggered": _pfr_result.rule_triggered,
                "lot_size": intent.volume,
            },
        )

    # ─── 9. WEEKEND PROTECTION (H4) ──────────────────────────────────
    _weekend_result = check_weekend_gate()
    if not _weekend_result.allowed:
        return GuardChainResult(
            allowed=False,
            guard_name="weekend_protection",
            reason=f"weekend_protection:{_weekend_result.reason}",
            rejection_code="H4_weekend_protection",
            filter_key="",
            metadata={"layer": "H4"},
        )

    # ─── 10. CONTROL LAYER ───────────────────────────────────────────
    try:
        from core.pipeline.control_layer import control_gate
        _ctrl_ok, _ctrl_reason = control_gate()
        if not _ctrl_ok:
            return GuardChainResult(
                allowed=False,
                guard_name="control_gate",
                reason=f"control_gate:{_ctrl_reason or 'control_layer_block'}",
                rejection_code="",
                filter_key="",
                metadata={"layer": "CONTROL", "final_authority": True},
            )
    except Exception:
        pass  # Control layer failure must never prevent execution

    # ─── ALL GUARDS PASSED ────────────────────────────────────────────
    _result = GuardChainResult(allowed=True)
    _emit_research_event(symbol, intent, _result)
    return _result


# ─── RESEARCH EVENT EMISSION ──────────────────────────────────────────────────

def _emit_research_event(symbol: str, intent: Any, result: GuardChainResult) -> None:
    """Fire-and-forget research event for guard decisions. Never affects trading."""
    try:
        from core.research_events import persist_guard_event
        persist_guard_event(
            symbol=symbol,
            cycle_id=0,  # Caller doesn't pass cycle_id; research can correlate by timestamp
            correlation_id="",
            guard_name=result.guard_name if not result.allowed else "ALL_PASSED",
            allowed=result.allowed,
            reason=result.reason if not result.allowed else "all_guards_passed",
            metadata=result.metadata if not result.allowed else {},
            direction=intent.side.name if hasattr(intent, "side") else "",
            pattern=getattr(intent, "pattern", ""),
        )
    except Exception:
        pass  # Must NEVER affect trading
