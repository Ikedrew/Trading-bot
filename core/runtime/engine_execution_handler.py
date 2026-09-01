"""
Engine Execution Handler — Pre-execution preparation for Engine A EXECUTE path.

Handles all bookkeeping that occurs AFTER Engine A decides to EXECUTE
but BEFORE the runtime guard chain and broker execution:
- Correlation ID generation
- Decision audit persistence
- Execution context construction and persistence
- Shadow trade lifecycle opening

This module OWNS:
    - Correlation ID generation
    - Decision audit (EXECUTE path)
    - Execution context build + persist
    - Shadow trade open

This module does NOT own:
    - Broker execution
    - Risk guard chain
    - Post-execution handling
    - Decision authority
    - Flow control

Design: preparation function — returns required metadata, never controls flow.
"""

from __future__ import annotations

import logging
import time as _time_mod
from dataclasses import dataclass
from typing import Any

from core.correlation import generate_correlation_id

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPrep:
    """Result of execution preparation — consumed by downstream execution."""
    intent: Any
    """OrderIntent from new engine."""
    correlation_id: str
    """Decision spine correlation ID."""
    decision_id: str
    """Persisted decision audit ID."""
    canonical_opportunity_id: str = ""
    """Canonical opportunity lineage root (remediation)."""
    observation_id: str = ""
    """Canonical bar observation ID."""


def prepare_execution(
    *,
    new_result: dict,
    new_engine_score: float,
    new_engine_htf: Any,
    sym_state: Any,
    cycle_id: int,
    closed_time: int,
    canonical_opportunity_id: str = "",
    observation_id: str = "",
    decision_id: str = "",
    candles: Any = None,
    closed_i: int = 0,
    bid: float = 0.0,
    ask: float = 0.0,
    tick_time: float = 0.0,
    feed_state: str = "",
    cycle_start: float = 0.0,
    dd_result: Any = None,
    dl_result: Any = None,
    runtime_session_id: str = "",
    config: Any = None,
) -> ExecutionPrep:
    """
    Prepare execution: generate IDs, persist audit + context, open shadow trade.

    Args:
        new_result: Engine A result dict (contains "intent", "entity_id", etc.)
        new_engine_score: Engine A score.
        new_engine_htf: HTF context used by engine (for shadow trade snapshot).
        sym_state: Per-symbol state object.
        cycle_id: Current cycle number.
        closed_time: Bar close timestamp.
        candles: Candle array.
        closed_i: Closed bar index.
        bid: Current bid price.
        ask: Current ask price.
        tick_time: Last tick timestamp.
        feed_state: Feed health classification.
        cycle_start: Cycle start timestamp.
        dd_result: Drawdown guard result.
        dl_result: Daily loss guard result.
        runtime_session_id: Runtime session ID.
        config: Configuration object.

    Returns:
        ExecutionPrep with intent, correlation_id, decision_id.
    """
    _intent = new_result["intent"]

    # ─── 1. CORRELATION ID ────────────────────────────────────────────
    _cor_id = generate_correlation_id(
        cycle_id=cycle_id,
        symbol=sym_state.symbol,
        timestamp=float(closed_time),
    )

    # ─── 2. DECISION AUDIT ────────────────────────────────────────────
    _decision_id = str(decision_id or new_result.get("decision_id", "") or "")
    try:
        import uuid as _uuid_mod
        if not _decision_id:
            _decision_id = _uuid_mod.uuid4().hex
    except Exception:
        pass
    try:
        from core.decision_audit import persist_new_engine_decision_audit
        _audit_id = persist_new_engine_decision_audit(
            symbol=sym_state.symbol,
            cycle_id=cycle_id,
            engine_result=new_result,
            engine_state=sym_state.engine_state,
            candles=candles,
            closed_i=closed_i,
            correlation_id=_cor_id,
            entity_id=new_result.get("entity_id", ""),
            observation_id=observation_id or new_result.get("observation_id", ""),
            canonical_opportunity_id=canonical_opportunity_id or new_result.get("canonical_opportunity_id", ""),
            strategy_ts_utc_ms=new_result.get("strategy_ts_utc_ms", 0),
            runtime_session_id=runtime_session_id,
        )
        if _audit_id and not decision_id and not new_result.get("decision_id", ""):
            _decision_id = _audit_id  # Use audit-generated ID if available
    except Exception:
        pass  # decision_id already set from uuid above

    # ─── 3. EXECUTION CONTEXT — REMOVED (remediation Stage 5) ──────
    # The authoritative per-cycle execution-context writer is
    # build_cycle_context() in core/runtime/execution_context_builder.py,
    # which runs for EVERY bar with the identical deterministic COR id.
    # This EXECUTE-path duplicate write produced two rows per EXECUTE
    # cycle and has been removed. Execution semantics unchanged.

    # ─── 3b. CONFIG SNAPSHOT (research attribution) ───────────────────
    try:
        from core.research_events import persist_config_snapshot
        persist_config_snapshot(correlation_id=_cor_id, cycle_id=cycle_id)
    except Exception:
        pass  # Config snapshot must never block trading

    # ─── 4b. CANDIDATE SHADOW OBSERVATIONS (Stage-2 validation) ───────
    # Opens additional shadows for any candidates in SHADOW_TESTING.
    # Observation-only: never modifies production, never blocks execution.
    try:
        from research_engine.lifecycle.candidate_shadow_hook import open_candidate_shadows
        open_candidate_shadows(
            symbol=sym_state.symbol,
            cycle_id=cycle_id,
            direction=_intent.side.name,
            entry_price=(bid + ask) / 2,
            stop_loss=_intent.sl,
            take_profit=_intent.tp,
            entry_time=float(closed_time),
            entry_bar_index=closed_i,
            correlation_id=_cor_id,
            entity_id=new_result.get("entity_id", ""),
            pattern=_intent.pattern,
            score=new_engine_score,
            bid=bid,
            ask=ask,
            strategy=new_result.get("strategy", ""),
        )
    except Exception:
        pass  # Candidate shadow must NEVER block production execution

    return ExecutionPrep(
        intent=_intent,
        correlation_id=_cor_id,
        decision_id=_decision_id,
        canonical_opportunity_id=canonical_opportunity_id or new_result.get("canonical_opportunity_id", ""),
        observation_id=observation_id or new_result.get("observation_id", ""),
    )
