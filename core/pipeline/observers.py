"""
Observer Registry — Dispatches pipeline observers after engine evaluation.

Coordinates notification of all passive observers in correct order.
Each observer is isolated: failure in one never prevents subsequent observers.

This module OWNS:
    - Observer dispatch
    - Notification ordering
    - try/except isolation per observer
    - Invoking every registered observer

This module does NOT own:
    - Trading decisions
    - Execution
    - Risk logic
    - Pipeline state
    - Runtime loop
    - Retries
    - Heartbeat / MT5
    - Strategy logic
    - Observer implementations
    - Logging redesign

Design: pure dispatcher — no business logic, no return values consumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ─── CONTEXT (passed from runtime to observers) ──────────────────────────────

@dataclass
class ObserverContext:
    """All inputs needed by the observer dispatch.

    Flat structure — no nesting. Mirrors the exact values available
    at the call site in live_scanner.py.
    """
    symbol: str
    cycle_id: int
    bar_time: float
    engine_result: dict[str, Any]
    engine_state: Any
    candles: Any
    closed_i: int
    bid: float
    ask: float
    config: Any
    detected_patterns: list[Any]
    risk_manager: Any
    htf_context: Any
    runtime_session_id: str
    decision_funnel: Any
    market_context: Any = None  # MarketContext object (optional, preferred over htf_context)
    observation_id: str = ""
    decision_id: str = ""
    correlation_id: str = ""


# ─── OBSERVER REGISTRY ────────────────────────────────────────────────────────

class ObserverRegistry:
    """
    Dispatches pipeline observers after engine evaluation.

    Observer ordering (preserved from original):
        1. Event observer — observe_engine_output
        2. Forensic logger — log_full_cycle
        3. Entity tracker — track_opportunity
        4. Visibility layer — emit_visibility_trace
        5. Shadow rooms — run_shadow_rooms
        6. Decision trace — build + persist + funnel record
        7. Strategy observer — strategy intelligence observation (read-only)
        8. V2 opportunity observer — full market state capture (read-only)
        9. V3 opportunity observer — location + liquidity capture (read-only)
        10. V3 shadow — market understanding engine (read-only)

    Each observer is wrapped in try/except Exception: pass.
    Failure in one observer never blocks subsequent observers.
    No observer return values are consumed.

    Usage:
        registry = ObserverRegistry()
        registry.notify_all(context)
    """

    def notify_all(self, ctx: ObserverContext) -> None:
        """
        Dispatch all observers in order. Never raises.

        Args:
            ctx: ObserverContext with all required inputs.
        """
        # ─── 1. Event observer: emit on meaningful state change ───────
        try:
            from core.pipeline.event_observer import observe_engine_output
            observe_engine_output(ctx.engine_result)
        except Exception:
            pass

        # ─── 2. Forensic logger: full gate trace to pair channel ──────
        try:
            from core.pipeline.forensic_logger import log_full_cycle
            ctx.engine_result["_bias_phase"] = getattr(ctx.engine_state, "bias_phase", "?")
            log_full_cycle(
                symbol=ctx.symbol,
                cycle_id=ctx.cycle_id,
                engine_result=ctx.engine_result,
                mt5_time=ctx.bar_time,
            )
        except Exception:
            pass

        # ─── 3. Entity tracker: continuous state logging ──────────────
        try:
            from core.pipeline.entity_tracker import track_opportunity
            track_opportunity(
                symbol=ctx.symbol,
                bar_time=ctx.bar_time,
                engine_result=ctx.engine_result,
                cycle_id=ctx.cycle_id,
            )
        except Exception:
            pass

        # ─── 4. Visibility layer: design vs reality gap trace ─────────
        try:
            from core.pipeline.visibility_layer import emit_visibility_trace
            emit_visibility_trace(
                symbol=ctx.symbol,
                cycle_id=ctx.cycle_id,
                bar_time=ctx.bar_time,
                engine_result=ctx.engine_result,
                bias_phase=getattr(ctx.engine_state, "bias_phase", "?"),
            )
        except Exception:
            pass

        # ─── 5. Shadow rooms: full parallel compute ───────────────────
        try:
            from core.pipeline.shadow_rooms import run_shadow_rooms
            run_shadow_rooms(
                symbol=ctx.symbol,
                cycle_id=ctx.cycle_id,
                bar_time=ctx.bar_time,
                candles=ctx.candles,
                closed_i=ctx.closed_i,
                bid=ctx.bid,
                ask=ctx.ask,
                engine_state=ctx.engine_state,
                config=ctx.config,
                detected_patterns=ctx.detected_patterns,
                risk_manager=ctx.risk_manager,
                htf_context=ctx.htf_context,
                live_engine_result=ctx.engine_result,
            )
        except Exception:
            pass

        # ─── 6. Decision trace: build + persist + funnel record ───────
        try:
            from core.decision_trace import build_decision_trace, persist_decision_trace
            _trace = build_decision_trace(
                engine_result=ctx.engine_result,
                runtime_session_id=ctx.runtime_session_id,
                pattern_count=len(ctx.detected_patterns),
                v10_pipeline_result=ctx.engine_result.get("v10_pipeline_result"),
                observation_id=ctx.observation_id,
                decision_id=ctx.decision_id,
                correlation_id=ctx.correlation_id,
            )
            persist_decision_trace(_trace)
            ctx.decision_funnel.record_trace(_trace)
        except Exception:
            pass

        # ─── 7. Strategy observer: strategy intelligence observation ──
        # READ ONLY. Evaluates which strategies match current context.
        # Creates StrategyObservation records for research evidence.
        # Never influences decisions. Never modifies engine_result.
        # Failure here never affects trading pipeline.
        try:
            from core.strategies.strategy_intelligence_observer import (
                observe_strategy_intelligence,
            )
            observe_strategy_intelligence(ctx)
        except Exception:
            pass

        # ─── 8. V2 opportunity observer: full market state capture ────
        # READ ONLY. Captures complete market context for V2 research.
        # Builds V2Opportunity records for predictive analysis.
        # Never influences decisions. Never modifies engine_result.
        # Failure here never affects trading pipeline.
        try:
            from core.observers.v2_opportunity_observer import (
                observe_v2_opportunity,
            )
            observe_v2_opportunity(ctx)
        except Exception:
            pass

        # ─── 9. V3 opportunity observer: location + liquidity capture ─
        # READ ONLY. Captures market location and liquidity context
        # for V3 research (where is price relative to structure/levels).
        # Never influences decisions. Never modifies engine_result.
        # Failure here never affects trading pipeline.
        try:
            from core.observers.v3_opportunity_observer import (
                observe_v3_opportunity,
            )
            observe_v3_opportunity(ctx)
        except Exception:
            pass

        # ─── 10. V3 shadow: market understanding engine ───────────────
        # READ ONLY. Builds complete MarketUnderstanding for V3 research.
        # Describes market objectively. Never influences decisions.
        # Failure here never affects trading pipeline.
        try:
            from core.v3_shadow.observer import (
                observe_market_understanding,
            )
            observe_market_understanding(ctx)
        except Exception:
            pass
