"""V10 Decision Persistence — Saves full V10 pipeline decision chain.

Persists a complete linked decision record containing all V10 layers
before execution. One record per pipeline evaluation.

Output: logs/v10_decisions/{SYMBOL}/{DATE}.jsonl
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.v10.pipeline import PipelineResult

logger = logging.getLogger(__name__)

_OUTPUT_DIR = "logs/v10_decisions"


def persist_v10_decision(result: PipelineResult) -> None:
    """
    Persist the full V10 decision chain as a single linked record.

    Never raises — failures are logged and silently ignored.
    """
    try:
        _do_persist(result)
    except Exception as exc:
        logger.debug("[V10_PERSIST] failed: %s", exc)


def _do_persist(result: PipelineResult) -> None:
    """Internal persistence logic."""
    symbol = result.market_state.symbol
    ts = result.market_state.timestamp_utc

    # Build decision record
    record = build_decision_record(result)

    # Determine output path
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else datetime.now(timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    out_dir = Path(_OUTPUT_DIR) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date_str}.jsonl"

    # Append
    with open(out_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def build_decision_record(result: PipelineResult) -> dict[str, Any]:
    """Build a complete decision record from a PipelineResult."""
    state = result.market_state
    opp = result.opportunity
    strat = result.strategy
    hz = result.horizon
    entry = result.entry
    risk = result.risk
    exe = result.execution

    return {
        "schema_version": "v10_decision_v1",
        "decision_id": opp.observation_id,
        "symbol": state.symbol,
        "timestamp_utc": state.timestamp_utc,

        # Final outcome
        "final_action": "EXECUTE" if result.approved else "NO_TRADE",
        "rejection_stage": result.rejection_stage,
        "rejection_reason": _get_rejection_reason(result),

        # V10 layers
        "market_state": {
            "h4_trend": state.h4.trend,
            "h4_phase": state.h4.market_phase,
            "h1_bos": state.h1.bos_direction if state.h1.bos_confirmed else "",
            "h1_choch": state.h1.choch_direction if state.h1.choch_detected else "",
            "h1_clarity": state.h1.structural_clarity,
            "m15_pullback": state.m15.pullback_active,
            "m15_displacement": state.m15.displacement_present,
            "regime": state.regime.regime,
            "volatility": state.regime.volatility_state,
            "location_type": state.location.location_type,
            "inside_zone": state.location.inside_institutional_zone,
            "range_position": state.location.range_position,
        },
        "opportunity": {
            "state": opp.opportunity_state,
            "direction": opp.directional_bias,
            "type": opp.opportunity_type,
            "quality": opp.quality.overall_quality,
            "location_score": opp.quality.location_score,
            "structure_score": opp.quality.structure_score,
            "behaviour_score": opp.quality.behaviour_score,
            "formation_score": opp.quality.formation_score,
            "reasoning": opp.reasoning[:5],
        },
        "strategy": {
            "family": strat.strategy_family,
            "confidence": strat.strategy_confidence,
            "direction": strat.directional_context,
            "reasoning": strat.reasoning[:3],
        },
        "horizon": {
            "type": hz.horizon_type,
            "min_move": hz.movement_expectation.minimum_expected_move,
            "max_move": hz.movement_expectation.maximum_expected_move,
            "unit": hz.movement_expectation.measurement_unit,
            "duration_min": hz.trade_lifecycle.expected_duration_minutes,
        },
        "entry": {
            "method": entry.entry_method,
            "status": entry.entry_status,
            "direction": entry.trade_direction,
            "entry_price": entry.entry_price,
            "stop_price": entry.stop_reference.price,
            "target_price": entry.target_reference.price,
            "risk_distance": entry.risk_distance,
            "reward_distance": entry.reward_distance,
            "expected_rr": entry.expected_rr,
        },
        "risk": {
            "approved": risk.approved,
            "rejection_reason": risk.rejection_reason,
            "risk_pct": risk.risk_profile.risk_percentage,
            "position_size": risk.risk_profile.position_size,
            "max_loss": risk.risk_profile.max_loss_amount,
        },
        "execution": {
            "approved": exe.approved,
            "rejection_reason": exe.rejection_reason,
            "order_type": exe.order_details.order_type if exe.approved else "",
            "volume": exe.order_details.volume if exe.approved else 0,
        },
    }


def _get_rejection_reason(result: PipelineResult) -> str:
    """Get human-readable rejection reason from the appropriate layer."""
    stage = result.rejection_stage
    if not stage:
        return ""
    if stage == "opportunity":
        return f"Opportunity {result.opportunity.opportunity_state}"
    if stage == "strategy":
        return "No strategy family matched"
    if stage == "entry":
        return f"Entry {result.entry.entry_status}: {result.entry.reasoning[0] if result.entry.reasoning else ''}"
    if stage == "risk":
        return result.risk.rejection_reason
    if stage == "execution":
        return result.execution.rejection_reason
    return "Unknown"
