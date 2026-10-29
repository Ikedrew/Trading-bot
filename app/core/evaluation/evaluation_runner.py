"""
Evaluation Runner — Consolidated evaluation boundary for engine comparison.

Handles all evaluation/comparison logic for the live scanner:
- Legacy shadow pipeline execution
- Engine comparison (shadow execute comparison)
- NO_TRADE shadow divergence logging
- Paper outcome tracking

This module is the SINGLE evaluation entry point for live_scanner.py.
It encapsulates all evaluation details so the production orchestrator
only needs to call evaluate().

This module OWNS:
    - Legacy shadow execution dispatch
    - Shadow comparison dispatch
    - Evaluation result aggregation

This module does NOT own:
    - Production decisions
    - Trade execution
    - Risk management
    - Runtime orchestration
    - Engine A logic

Design: fire-and-forget evaluation — never raises, never affects production.
Future: Will support ML policy engine evaluation alongside legacy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── CONTEXT / RESULT TYPES ──────────────────────────────────────────────────

@dataclass
class EvaluationContext:
    """Input context for evaluation. Passed from production orchestrator."""
    cycle_id: int
    symbol: str
    closed_time: int
    candles: Any
    closed_i: int
    bid: float
    ask: float
    config: Any
    risk: Any
    engine_state: Any
    htf_context: Any
    new_engine_result: dict | None = None
    new_engine_score: float = 0.0
    new_engine_action: str = ""  # "EXECUTE" or "NO_TRADE"
    detected_patterns: list = field(default_factory=list)


@dataclass
class EvaluationResult:
    """Output from evaluation. Consumed by diagnostics/reporting."""
    legacy_unified: Any = None
    """Legacy pipeline unified result (if shadow enabled)."""
    ran: bool = False
    """Whether any evaluation actually executed."""


# ─── EVALUATION RUNNER ────────────────────────────────────────────────────────

def evaluate(ctx: EvaluationContext) -> EvaluationResult:
    """
    Run all configured evaluations for this cycle/symbol.

    Currently supports:
        - Legacy shadow pipeline (ENABLE_LEGACY_SHADOW_PIPELINE)
        - Shadow execute comparison (when legacy produces result)
        - NO_TRADE shadow (when Engine A rejects and legacy shadow enabled)

    Future:
        - ML policy engine evaluation
        - Multi-strategy comparison
        - Outcome tracking

    Never raises. Never affects production decisions.
    """
    result = EvaluationResult()

    if not getattr(ctx.config, "ENABLE_LEGACY_SHADOW_PIPELINE", False):
        return result

    try:
        result.ran = True

        if ctx.new_engine_action == "EXECUTE":
            # Run legacy in shadow mode + compare with Engine A EXECUTE
            from core.evaluation.legacy_shadow_runner import run_legacy_shadow
            result.legacy_unified = run_legacy_shadow(
                candles=ctx.candles,
                closed_i=ctx.closed_i,
                symbol=ctx.symbol,
                config=ctx.config,
                risk=ctx.risk,
                engine_state=ctx.engine_state,
                bid=ctx.bid,
                ask=ctx.ask,
                closed_time=ctx.closed_time,
                htf_context=ctx.htf_context,
                new_engine_score=ctx.new_engine_score,
            )
            # Shadow comparison with Engine A execute decision
            if result.legacy_unified is not None:
                from core.pipeline.shadow_pipeline import run_shadow_execute_comparison
                run_shadow_execute_comparison(
                    sym_state=_make_sym_state_facade(ctx),
                    unified=result.legacy_unified,
                    new_engine_score=ctx.new_engine_score,
                    closed_i=ctx.closed_i,
                )

        elif ctx.new_engine_action == "NO_TRADE":
            # Run legacy shadow for NO_TRADE divergence logging
            from core.pipeline.shadow_pipeline import run_shadow_no_trade
            from core.engine import process_bar
            run_shadow_no_trade(
                candles=ctx.candles,
                closed_i=ctx.closed_i,
                sym_state=_make_sym_state_facade(ctx),
                config=ctx.config,
                bid=ctx.bid,
                ask=ctx.ask,
                closed_time=ctx.closed_time,
                process_bar_fn=process_bar,
            )

    except Exception:
        pass  # Evaluation failure must never affect production

    return result


def _make_sym_state_facade(ctx: EvaluationContext) -> Any:
    """Create a minimal facade matching what shadow functions expect from sym_state."""
    class _Facade:
        symbol = ctx.symbol
        engine_state = ctx.engine_state
        risk = ctx.risk
    return _Facade()


# ─── SHUTDOWN ─────────────────────────────────────────────────────────────────

def shutdown_evaluation(config: Any) -> None:
    """
    Emit evaluation shutdown summaries. Called from live_scanner finally block.

    Never raises.
    """
    try:
        if getattr(config, "MTF_SHADOW_MODE", False):
            from core.timeframes.calibration import mtf_calibration
            mtf_calibration.emit_summary()
    except Exception:
        pass
