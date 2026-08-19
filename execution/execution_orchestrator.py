"""
Execution Orchestrator — Trade execution and result persistence.

Handles broker execution of trade intents and persistence of execution results.
Returns structured outcome without controlling runtime flow.

AUTHORITY BOUNDARIES:
    CAN:
        - Submit market orders to MT5 broker via mt5_execution
        - Report execution success or failure
        - Persist execution results (execution_results dataset)
        - Compute slippage (fill_price vs entry_reference)

    CANNOT:
        - Make trading decisions (pipeline/new_engine owns that)
        - Override risk guard rejections
        - Modify SL/TP after fill (trade_management owns that)
        - Control runtime loop flow (no continue/break)
        - Write to decision ledger or trade journal

This module OWNS:
    - Broker execution call (execution.execute)
    - Execution error handling and logging
    - Execution result persistence
    - Returning structured ExecutionOutcome

This module does NOT own:
    - Trade entry decisions
    - Risk decisions
    - Guard logic
    - Decision ledger writes
    - Trade manager registration
    - Slippage monitoring
    - Event emission
    - Runtime loop control (no continue/break)
    - Discord trade notifications (beyond execution errors)

Design: execute and report — returns ExecutionOutcome, never controls flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.event_bus import log_runtime_exception

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass
class ExecutionOutcome:
    """Result of trade execution attempt."""

    executed: bool
    """True if broker call succeeded (regardless of fill success)."""

    ok: bool = False
    """True if broker accepted and filled the order."""

    result: Any = None
    """Raw execution result from broker (retcode, deal, order, etc.)."""

    decision_ts_utc_ms: int = 0
    """Timestamp of decision (UTC milliseconds)."""

    error: str = ""
    """Error description if executed=False."""


# ─── EXECUTION ORCHESTRATOR ───────────────────────────────────────────────────

class ExecutionOrchestrator:
    """
    Executes trade intents through the broker and persists results.

    Usage:
        orchestrator = ExecutionOrchestrator(execution, config)
        outcome = orchestrator.execute_trade(
            intent=decision.intent, symbol=..., cycle_id=..., ...
        )
        if not outcome.executed:
            continue  # Caller handles skip
        if outcome.ok:
            # Success path
        else:
            # Broker rejected path
    """

    def __init__(self, execution: Any, config: Any) -> None:
        self._execution = execution
        self._config = config

    def execute_trade(
        self,
        *,
        intent: Any,
        symbol: str,
        cycle_id: int,
        decision_id: str,
        correlation_id: str,
        entity_id: str,
        observation_id: str = "",
        mt5_state: str,
    ) -> ExecutionOutcome:
        """
        Execute a trade intent through the broker and persist the result.

        Args:
            intent: OrderIntent with trade parameters.
            symbol: Symbol being traded.
            cycle_id: Current cycle number.
            decision_id: Unique decision identifier.
            correlation_id: Correlation ID for tracing.
            entity_id: Entity ID for forensic linking.
            observation_id: Canonical V10 opportunity identity.
            mt5_state: Current MT5 connection state.

        Returns:
            ExecutionOutcome with executed=False if broker call failed.
            ExecutionOutcome with executed=True and ok/result on success.
        """
        # ─── 1. EXECUTE TRADE ─────────────────────────────────────────
        logger.info("[STATE] symbol=%s | ENTRY | pattern=%s | score=%s", symbol, intent.pattern, getattr(intent, "score", "?"))
        try:
            from core.clock import utc_ms as _utc_ms
            _decision_ts = _utc_ms()
            result = self._execution.execute(
                order_intent=intent,
                decision_ts_utc_ms=_decision_ts,
                decision_id=decision_id,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            log_runtime_exception(exc, "EXECUTION", mt5_state)
            try:
                _dl = getattr(self._config, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("ERROR", {
                        "location": "live_scanner:execution",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:200],
                        "details": {"symbol": symbol, "cycle": cycle_id},
                    })
            except Exception:
                pass
            return ExecutionOutcome(executed=False, error=str(exc))

        # ─── 2. PERSIST EXECUTION RESULT ──────────────────────────────
        try:
            from core.persistence.execution_result_writer import persist_execution_result
            _exec_slippage = abs(float(result.fill_price) - intent.entry_reference) if result.fill_price else 0.0
            persist_execution_result(
                symbol=symbol,
                cycle_id=cycle_id,
                result_ok=result.ok,
                retcode=result.retcode,
                deal=result.deal,
                order=result.order,
                comment=result.comment,
                fill_price=result.fill_price,
                side=intent.side.name if hasattr(intent.side, "name") else str(intent.side),
                volume=intent.volume,
                entry_reference=intent.entry_reference,
                sl=intent.sl,
                tp=intent.tp,
                pattern=intent.pattern,
                decision_id=decision_id,
                correlation_id=correlation_id,
                entity_id=entity_id,
                observation_id=observation_id,
                decision_ts_utc_ms=_decision_ts,
                slippage=_exec_slippage,
            )
        except Exception:
            pass  # Execution result persistence must never affect trading

        return ExecutionOutcome(
            executed=True,
            ok=result.ok,
            result=result,
            decision_ts_utc_ms=_decision_ts,
        )
