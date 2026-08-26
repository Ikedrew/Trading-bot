"""V10 Scanner Adapter — Bridge between live_scanner and V10Pipeline.

Provides the same interface shape that live_scanner expects from the
existing engine, wrapping V10Pipeline.process() results into a format
the scanner can consume without restructuring its loop.

Usage in live_scanner:
    from core.v10.scanner_adapter import run_v10_cycle
    
    result = run_v10_cycle(
        symbol=sym_state.symbol,
        candles=candles,
        closed_i=closed_i,
        bid=bid,
        ask=ask,
        htf_context=htf_context,
        market_context=market_context,
        engine_state=sym_state.engine_state,
    )
    # result["action"] = "EXECUTE" or "NO_TRADE"
    # result["v10_pipeline_result"] = PipelineResult (for research/logging)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_v10_cycle(
    *,
    symbol: str,
    candles: Any,
    closed_i: int,
    bid: float,
    ask: float,
    htf_context: Any = None,
    market_context: Any = None,
    engine_state: Any = None,
    config: Any = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Run one V10 pipeline cycle for a symbol.

    Returns a dict compatible with the live_scanner's engine result format:
      - action: "EXECUTE" | "NO_TRADE"
      - reason: human-readable string
      - v10_pipeline_result: full PipelineResult for research
      - side: "BUY" | "SELL" (if EXECUTE)
      - entry_price, stop_loss, take_profit, volume (if EXECUTE)
    """
    # Compute entity_id BEFORE pipeline call — always available regardless of exceptions
    _bar_time = int(candles[closed_i].time) if candles and closed_i >= 0 else 0
    _fallback_entity_id = f"{symbol}_{_bar_time}"

    try:
        result = _do_v10_cycle(
            symbol=symbol, candles=candles, closed_i=closed_i,
            bid=bid, ask=ask, htf_context=htf_context,
            market_context=market_context, engine_state=engine_state,
        )
        # Ensure entity_id is always present (defensive)
        if "entity_id" not in result or not result["entity_id"]:
            result["entity_id"] = _fallback_entity_id
        # Persist decision and print report
        try:
            from core.v10.decision_report import format_v10_decision
            if result.get("v10_pipeline_result"):
                # Remediation: NO second ledger row here. The V10 research
                # payload is attached to this result and persisted by the
                # sole authoritative writer (DecisionRecorder) via live_scanner.
                from core.v10.persistence_adapter import build_v10_payload
                result["v10_payload"] = build_v10_payload(result["v10_pipeline_result"])
                # Print V10 reasoning chain for all decisions
                print(format_v10_decision(result["v10_pipeline_result"]))
        except Exception:
            pass  # Persistence/report failure must never block
        return result
    except Exception as exc:
        logger.warning("[V10_ADAPTER] cycle failed for %s: %s", symbol, exc)
        return {
            "action": "NO_TRADE",
            "reason": f"V10 pipeline error: {exc}",
            "score": 0.0,
            "v10_pipeline_result": None,
            "entity_id": _fallback_entity_id,
        }


def _do_v10_cycle(
    *,
    symbol: str,
    candles: Any,
    closed_i: int,
    bid: float,
    ask: float,
    htf_context: Any,
    market_context: Any,
    engine_state: Any,
) -> dict[str, Any]:
    """Internal implementation — may raise."""
    from core.v3_shadow.builders import build_market_understanding
    from core.v3_shadow.context_builders import build_v3_market_context
    from core.v10.pipeline import V10Pipeline
    from core.runtime.account_provider import get_account_context, get_broker_context

    # ─── Build MarketUnderstanding from available data ─────────
    understanding = build_market_understanding(
        symbol=symbol,
        timestamp_utc=candles[closed_i].time if candles and closed_i >= 0 else 0,
        candles=candles,
        htf_context=htf_context,
        market_context=market_context,
        bid=bid,
        ask=ask,
    )

    # ─── Build V3MarketContext ─────────────────────────────────
    v3_context = build_v3_market_context(understanding)

    # ─── Live account and broker context from MT5 ──────────────
    account = get_account_context()
    broker = get_broker_context(symbol=symbol, bid=bid, ask=ask)

    # ─── Run V10 Pipeline ──────────────────────────────────────
    pipeline = V10Pipeline()
    result = pipeline.process(understanding, v3_context, account, broker)

    # ─── Entity Identity (stable: symbol + bar_time) ──────────
    _bar_time = int(candles[closed_i].time) if candles and closed_i >= 0 else 0
    _entity_id = f"{symbol}_{_bar_time}"

    # ─── Convert to scanner-compatible format ──────────────────
    if result.approved:
        order = result.execution.order_details
        intent = _build_order_intent(result, symbol)
        logger.info(
            "[V10 EXECUTION BRIDGE] symbol=%s action=EXECUTE intent_created=true "
            "direction=%s volume=%.4f entry=%.5f sl=%.5f tp=%.5f",
            symbol, order.direction, order.volume,
            order.entry_price, order.stop_loss, order.take_profit,
        )
        return {
            "action": "EXECUTE",
            "intent": intent,
            "reason": f"V10: {result.strategy.strategy_family} → {result.horizon.horizon_type}",
            "score": result.opportunity.quality.overall_quality,
            "side": order.direction,
            "entry_price": order.entry_price,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "volume": order.volume,
            "pattern": result.strategy.strategy_family,
            "strategy": result.strategy.strategy_family,
            "strategy_confidence": result.strategy.strategy_confidence,
            "entity_id": _entity_id,
            "components": {
                "location_score": result.opportunity.quality.location_score,
                "structure_score": result.opportunity.quality.structure_score,
                "behaviour_score": result.opportunity.quality.behaviour_score,
                "formation_score": result.opportunity.quality.formation_score,
            },
            "v10_pipeline_result": result,
        }
    else:
        stage = result.rejection_stage
        reason = ""
        if stage == "opportunity":
            reason = f"No opportunity ({result.opportunity.opportunity_state})"
        elif stage == "strategy":
            reason = "No strategy matched"
        elif stage == "entry":
            reason = f"Entry {result.entry.entry_status}"
        elif stage == "risk":
            reason = f"Risk: {result.risk.rejection_reason}"
        elif stage == "execution":
            reason = f"Exec: {result.execution.rejection_reason}"
        else:
            reason = "Pipeline did not approve"

        logger.debug(
            "[V10 EXECUTION BRIDGE] symbol=%s action=NO_TRADE intent_created=false reason=%s",
            symbol, reason,
        )
        return {
            "action": "NO_TRADE",
            "reason": f"V10 [{stage}]: {reason}",
            "score": result.opportunity.quality.overall_quality if result.opportunity else 0.0,
            "pattern": result.strategy.strategy_family if result.strategy and result.strategy.strategy_family != "NONE" else None,
            "strategy": result.strategy.strategy_family if result.strategy and result.strategy.strategy_family != "NONE" else None,
            "strategy_confidence": result.strategy.strategy_confidence if result.strategy else 0.0,
            "entity_id": _entity_id,
            "components": {
                "location_score": result.opportunity.quality.location_score,
                "structure_score": result.opportunity.quality.structure_score,
                "behaviour_score": result.opportunity.quality.behaviour_score,
                "formation_score": result.opportunity.quality.formation_score,
            } if result.opportunity and result.opportunity.opportunity_state != "INVALID" else None,
            "v10_pipeline_result": result,
        }


def _build_order_intent(result, symbol: str):
    """Build an OrderIntent from V10 ExecutionDecision — pure translation, no logic."""
    from risk.models import OrderIntent
    from strategy.signals import Side

    order = result.execution.order_details
    direction = Side.BUY if order.direction == "BUY" else Side.SELL

    return OrderIntent(
        symbol=symbol,
        side=direction,
        volume=order.volume,
        entry_reference=order.entry_price,
        sl=order.stop_loss,
        tp=order.take_profit,
        entry_type=order.order_type,  # "MARKET" / "LIMIT" / "STOP"
        pattern=result.strategy.strategy_family,
        risk_id=result.opportunity.observation_id,
        metadata={
            "engine": "V10",
            "strategy_family": result.strategy.strategy_family,
            "horizon": result.horizon.horizon_type,
            "opportunity_type": result.opportunity.opportunity_type,
            "decision_id": result.opportunity.observation_id,
        },
    )


