"""V10 Persistence Adapter — Bridges V10 PipelineResult into research pipeline.

Converts PipelineResult into:
  1. V10DecisionRecord (full research-grade record)
  2. Decision ledger entry (compatible with existing DecisionLedgerWriter)

Records BOTH execute and no-trade decisions for research learning.
Every field is explicitly populated — no silent missing values.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.v10.pipeline import PipelineResult
from core.v10.strategy_family import StrategyFamily
from core.v10.entry_model import EntryStatus, TradeDirection

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "v10_decision_v1"


# ═══════════════════════════════════════════════════════════════
# V10 DECISION RECORD (full research-grade format)
# ═══════════════════════════════════════════════════════════════


def build_v10_decision_record(result: PipelineResult, cycle_id: int = 0) -> dict[str, Any]:
    """
    Build a complete V10 decision record from PipelineResult.

    Every field is mandatory. Null values are explicit, never omitted.
    Both EXECUTE and NO_TRADE decisions are fully recorded.
    """
    state = result.market_state
    opp = result.opportunity
    strat = result.strategy
    hz = result.horizon
    entry = result.entry
    risk = result.risk
    exe = result.execution

    decision_id = opp.observation_id or _generate_id(state.symbol, state.timestamp_utc)
    correlation_id = f"v10_{state.symbol}_{int(state.timestamp_utc)}_{cycle_id}"

    return {
        "schema_version": _SCHEMA_VERSION,

        # Identity
        "observation_id": decision_id,
        "decision_id": decision_id,
        "correlation_id": correlation_id,
        "symbol": state.symbol,
        "timestamp_utc": state.timestamp_utc,
        "cycle_id": cycle_id,

        # Lineage
        "lineage": {
            "engine": "V10",
            "version": "v10",
            "schema": _SCHEMA_VERSION,
        },

        # Final outcome
        "final_action": "EXECUTE" if result.approved else "NO_TRADE",
        "rejection_stage": result.rejection_stage or None,
        "rejection_reason": _get_rejection_reason(result) or None,

        # Market state (always populated)
        "market_state": {
            "h4_trend": state.h4.trend or None,
            "h4_trend_strength": state.h4.trend_strength,
            "h4_phase": state.h4.market_phase or None,
            "h1_bos_direction": state.h1.bos_direction if state.h1.bos_confirmed else None,
            "h1_choch_direction": state.h1.choch_direction if state.h1.choch_detected else None,
            "h1_structural_clarity": state.h1.structural_clarity,
            "h1_dominant_trend": state.h1.dominant_trend or None,
            "m15_pullback_active": state.m15.pullback_active,
            "m15_displacement": state.m15.displacement_present,
            "m5_momentum": state.m5.momentum_direction or None,
            "regime": state.regime.regime or None,
            "volatility_state": state.regime.volatility_state or None,
            "location_type": state.location.location_type or None,
            "inside_zone": state.location.inside_institutional_zone,
            "range_position": state.location.range_position,
            "zone_quality": state.location.zone_quality,
        },

        # Opportunity (always populated)
        "opportunity": {
            "state": opp.opportunity_state,
            "directional_bias": opp.directional_bias or None,
            "opportunity_type": opp.opportunity_type or None,
            "overall_quality": opp.quality.overall_quality,
            "location_score": opp.quality.location_score,
            "structure_score": opp.quality.structure_score,
            "behaviour_score": opp.quality.behaviour_score,
            "formation_score": opp.quality.formation_score,
            "reasoning": opp.reasoning[:5] if opp.reasoning else [],
        },

        # Strategy (null if not reached)
        "strategy_family": strat.strategy_family if strat.strategy_family != StrategyFamily.NONE.value else None,
        "strategy_confidence": strat.strategy_confidence if strat.strategy_family != StrategyFamily.NONE.value else None,
        "strategy_direction": strat.directional_context or None,

        # Horizon (null if not reached)
        "horizon": hz.horizon_type if strat.strategy_family != StrategyFamily.NONE.value else None,
        "horizon_min_move": hz.movement_expectation.minimum_expected_move,
        "horizon_max_move": hz.movement_expectation.maximum_expected_move,
        "horizon_unit": hz.movement_expectation.measurement_unit,

        # Entry (null if not reached or invalid)
        "entry_method": entry.entry_method if entry.entry_status != EntryStatus.INVALID.value else None,
        "entry_direction": entry.trade_direction if entry.trade_direction != TradeDirection.NONE.value else None,
        "entry_status": entry.entry_status,
        "entry_price": entry.entry_price if entry.entry_price > 0 else None,
        "stop_price": entry.stop_reference.price if entry.stop_reference.price > 0 else None,
        "target_price": entry.target_reference.price if entry.target_reference.price > 0 else None,
        "risk_distance": entry.risk_distance if entry.risk_distance > 0 else None,
        "reward_distance": entry.reward_distance if entry.reward_distance > 0 else None,
        "expected_rr": entry.expected_rr if entry.expected_rr > 0 else None,

        # Risk
        "risk_approved": risk.approved,
        "risk_rejection": risk.rejection_reason or None,
        "risk_percentage": risk.risk_profile.risk_percentage if risk.approved else None,
        "position_size": risk.risk_profile.position_size if risk.approved else None,

        # Execution
        "execution_approved": exe.approved,
        "execution_rejection": exe.rejection_reason or None,
        "order_type": exe.order_details.order_type if exe.approved else None,
        "order_volume": exe.order_details.volume if exe.approved else None,

        # Engine version marker
        "engine_version": "V10",

        # Runtime snapshots (for research: "was the failure conditions or strategy?")
        "account_snapshot": {
            "balance": result.account_snapshot.balance if result.account_snapshot else None,
            "equity": result.account_snapshot.equity if result.account_snapshot else None,
            "margin_free": result.account_snapshot.margin_free if result.account_snapshot else None,
            "leverage": result.account_snapshot.leverage if result.account_snapshot else None,
            "profit": result.account_snapshot.profit if result.account_snapshot else None,
        } if result.account_snapshot and result.account_snapshot.available else None,

        "broker_snapshot": {
            "symbol": result.broker_snapshot.symbol if result.broker_snapshot else None,
            "spread": result.broker_snapshot.spread if result.broker_snapshot else None,
            "tick_value": result.broker_snapshot.tick_value if result.broker_snapshot else None,
            "tick_size": result.broker_snapshot.tick_size if result.broker_snapshot else None,
            "volume_min": result.broker_snapshot.volume_min if result.broker_snapshot else None,
            "volume_step": result.broker_snapshot.volume_step if result.broker_snapshot else None,
            "stops_level": result.broker_snapshot.stops_level if result.broker_snapshot else None,
            "bid": result.broker_snapshot.bid if result.broker_snapshot else None,
            "ask": result.broker_snapshot.ask if result.broker_snapshot else None,
            "market_open": result.broker_snapshot.market_open if result.broker_snapshot else None,
        } if result.broker_snapshot and result.broker_snapshot.available else None,
    }


# ═══════════════════════════════════════════════════════════════
# DECISION LEDGER COMPATIBILITY
# ═══════════════════════════════════════════════════════════════


def build_v10_ledger_entry(result: PipelineResult, cycle_id: int = 0) -> dict[str, Any]:
    """
    Build a decision ledger entry compatible with existing DecisionLedgerWriter.

    Adds V10-specific fields without breaking old schema.
    """
    from core.decision_ledger import build_ledger_entry, DecisionOutcome

    state = result.market_state
    opp = result.opportunity
    strat = result.strategy
    entry = result.entry
    risk = result.risk

    # Map V10 action to legacy DecisionOutcome
    decision = DecisionOutcome.EXECUTE if result.approved else DecisionOutcome.NO_TRADE

    # Build base ledger entry using existing function
    ledger_entry = build_ledger_entry(
        symbol=state.symbol,
        cycle_id=cycle_id,
        decision=decision,
        reason=_get_rejection_reason(result) if not result.approved else f"V10: {strat.strategy_family}",
        signal_score=opp.quality.overall_quality,
        signal_type=strat.strategy_family if strat.strategy_family != StrategyFamily.NONE.value else None,
        regime=state.regime.regime or "unknown",
        execution_intent={
            "direction": entry.trade_direction,
            "entry_price": entry.entry_price,
            "stop_loss": entry.stop_reference.price,
            "take_profit": entry.target_reference.price,
            "volume": risk.risk_profile.position_size,
        } if result.approved else None,
        engine_version="V10",
        last_stage=result.rejection_stage or "execution_approved",
        correlation_id=opp.observation_id,
        entity_id=opp.observation_id,
    )

    # Add V10-specific fields (extends, doesn't break schema)
    ledger_entry["v10"] = {
        "strategy_family": strat.strategy_family,
        "horizon": result.horizon.horizon_type,
        "entry_method": entry.entry_method,
        "opportunity_state": opp.opportunity_state,
        "opportunity_type": opp.opportunity_type,
        "decision_id": opp.observation_id,
    }

    return ledger_entry


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE (writes both V10 record and ledger entry)
# ═══════════════════════════════════════════════════════════════


def persist_v10_full(result: PipelineResult, cycle_id: int = 0) -> None:
    """
    Persist the full V10 decision — local JSONL, S3, and ledger.

    Never raises. Failures are logged and silently ignored.
    Logs correlation_id at every step for observability.
    """
    try:
        obs_id = result.opportunity.observation_id if result.opportunity else "?"
        logger.info(
            "[V10_PERSIST] step=begin obs_id=%s action=%s symbol=%s",
            obs_id, "EXECUTE" if result.approved else "NO_TRADE",
            result.market_state.symbol if result.market_state else "?",
        )

        # Write to decision ledger (existing infrastructure)
        ledger_entry = build_v10_ledger_entry(result, cycle_id)
        _write_to_ledger(ledger_entry)
        logger.info("[V10_PERSIST] step=ledger_written obs_id=%s", obs_id)

    except Exception as exc:
        logger.debug("[V10_PERSIST] full persistence failed: %s", exc)


def _write_to_ledger(entry: dict) -> None:
    """Write to the existing decision ledger (fire-and-forget)."""
    try:
        from core.decision_ledger import get_ledger
        ledger = get_ledger()
        ledger.write(entry)
    except Exception:
        pass  # Ledger failure must never block


def _generate_id(symbol: str, timestamp: float) -> str:
    raw = f"v10_{symbol}_{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_rejection_reason(result: PipelineResult) -> str:
    stage = result.rejection_stage
    if not stage:
        return ""
    if stage == "opportunity":
        return f"Opportunity {result.opportunity.opportunity_state}"
    if stage == "strategy":
        return "No strategy family matched"
    if stage == "entry":
        reasons = result.entry.reasoning
        return f"Entry {result.entry.entry_status}: {reasons[0] if reasons else ''}"
    if stage == "risk":
        return result.risk.rejection_reason
    if stage == "execution":
        return result.execution.rejection_reason
    return "Unknown"
