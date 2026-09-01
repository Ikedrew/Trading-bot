"""
Decision Recorder — Decision lifecycle management for the live scanner.

Manages the creation, mutation, and finalization of per-symbol cycle decisions.
Writes to the decision ledger exactly once per cycle (idempotent finalization).

This module OWNS:
    - Decision state management (initialization, mutation, finalization)
    - Decision invariant enforcement
    - Ledger write mechanics
    - Idempotent finalization guard

This module does NOT own:
    - Trading decisions (what decision to make)
    - Risk decisions
    - Guard logic
    - Execution
    - Strategy logic
    - Runtime loop control

Design: stateful recorder — one instance per scanner, reused across cycles.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.decision_ledger import DecisionOutcome

logger = logging.getLogger(__name__)


class DecisionRecorder:
    """
    Manages decision lifecycle: init → mutate → finalize.

    Each per-symbol iteration:
        1. init_cycle() — creates fresh decision state
        2. Caller mutates via self.decision dict or mutate()
        3. finalize() — validates invariants, writes to ledger (idempotent)

    Usage:
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(symbol=..., cycle_id=..., ...)
        recorder.decision["decision"] = DecisionOutcome.NO_TRADE
        recorder.decision["reason"] = "no_signal"
        recorder.finalize(cycle_start=cycle_start)
    """

    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger
        self._decision: dict = {}
        self._written: bool = True  # Start as True so finalize() is no-op before init

    @property
    def decision(self) -> dict:
        """Direct access to the current cycle decision dict."""
        return self._decision

    def init_cycle(
        self,
        *,
        symbol: str,
        cycle_id: int,
        regime: str,
        context_snapshot_id: str,
        drawdown_pct: float,
        daily_loss_pct: float,
        observation_id: str = "",
        canonical_opportunity_id: str = "",
        decision_id: str = "",
    ) -> dict:
        """
        Initialize fresh decision state for a new symbol cycle.

        Returns the decision dict for direct mutation by caller.
        """
        self._decision = {
            "symbol": symbol,
            "cycle_id": cycle_id,
            "decision": None,
            "reason": "",
            "signal_score": 0.0,
            "signal_type": None,
            "pattern_state": "none",
            "regime": regime,
            "session_state": "open",
            "last_stage": "",
            "risk_flag": "",
            "execution_intent": None,
            "context_snapshot_id": context_snapshot_id,
            "correlation_id": "",
            "decision_id": decision_id,
            "entity_id": "",
            "observation_id": observation_id,
            "canonical_opportunity_id": canonical_opportunity_id,
            "v10": None,
            "drawdown_pct": drawdown_pct,
            "daily_loss_pct": daily_loss_pct,
        }
        self._written = False
        return self._decision

    def mutate(self, **fields: Any) -> None:
        """
        Set one or more fields on the current decision.

        Convenience method — equivalent to self.decision[key] = value.
        """
        for key, value in fields.items():
            self._decision[key] = value

    def finalize(self, *, cycle_start: float) -> None:
        """
        Write the decision to the ledger. Idempotent — only writes once.

        Performs invariant enforcement:
            - decision must not be None
            - reason must not be empty

        Args:
            cycle_start: Cycle start timestamp for latency calculation.
        """
        if self._written:
            return

        # ─── INVARIANT ENFORCEMENT ────────────────────────────────────
        if self._decision.get("decision") is None:
            logger.error(
                "[DECISION_LEDGER_INVARIANT] decision=None at finalization "
                "symbol=%s cycle=%d — forcing UNKNOWN",
                self._decision.get("symbol"), self._decision.get("cycle_id"),
            )
            self._decision["decision"] = DecisionOutcome.NO_TRADE
            self._decision["reason"] = "INVARIANT_VIOLATION:decision_not_set"
        if not self._decision.get("reason"):
            logger.warning(
                "[DECISION_LEDGER_INVARIANT] reason empty at finalization "
                "symbol=%s cycle=%d decision=%s",
                self._decision.get("symbol"), self._decision.get("cycle_id"),
                self._decision.get("decision"),
            )
            self._decision["reason"] = "reason_not_set"
        # ─── END INVARIANT ENFORCEMENT ────────────────────────────────

        # ─── IDENTITY PROPAGATION ─────────────────────────────────────
        # Ensure correlation_id is always populated from context_snapshot_id
        # (the same cycle correlation ID, already persisted in execution_context).
        if not self._decision.get("correlation_id") and self._decision.get("context_snapshot_id"):
            self._decision["correlation_id"] = self._decision["context_snapshot_id"]
        if not self._decision.get("decision_id"):
            try:
                import uuid as _uuid_mod
                self._decision["decision_id"] = _uuid_mod.uuid4().hex
            except Exception:
                self._decision["decision_id"] = ""
        # ─── END IDENTITY PROPAGATION ─────────────────────────────────

        self._written = True
        try:
            self._ledger.record(
                symbol=self._decision["symbol"],
                cycle_id=self._decision["cycle_id"],
                decision=self._decision["decision"] or DecisionOutcome.NO_TRADE,
                reason=self._decision["reason"],
                signal_score=self._decision["signal_score"],
                signal_score_semantic=self._decision.get(
                    "signal_score_semantic", "unknown_legacy_projection"
                ),
                assessment_strategy_weighted_score=self._decision.get(
                    "assessment_strategy_weighted_score"
                ),
                opportunity_overall_quality_score=self._decision.get(
                    "opportunity_overall_quality_score"
                ),
                signal_type=self._decision["signal_type"],
                pattern_state=self._decision["pattern_state"],
                regime=self._decision["regime"],
                session_state=self._decision["session_state"],
                last_stage=self._decision["last_stage"],
                risk_flag=self._decision["risk_flag"],
                execution_intent=self._decision["execution_intent"],
                reasoning=self._decision.get("reasoning"),
                uncertainty=self._decision.get("uncertainty"),
                score_attribution=self._decision.get("score_attribution"),
                dual_ev=self._decision.get("dual_ev"),
                context_snapshot_id=self._decision["context_snapshot_id"],
                correlation_id=self._decision["correlation_id"],
                decision_id=self._decision.get("decision_id", ""),
                entity_id=self._decision.get("entity_id", ""),
                observation_id=self._decision.get("observation_id", ""),
                canonical_opportunity_id=self._decision.get("canonical_opportunity_id", ""),
                v10=self._decision.get("v10"),
                drawdown_pct=self._decision["drawdown_pct"],
                daily_loss_pct=self._decision["daily_loss_pct"],
                decision_latency_ms=int((time.time() - cycle_start) * 1000),
            )
        except Exception as e:
            print(f"[LEDGER WRITE ERROR] {type(e).__name__}: {e}")

    @property
    def is_written(self) -> bool:
        """Whether the current cycle decision has been finalized."""
        return self._written
