"""
PipelineAuthority — Centralised decision ledger for the trading pipeline.

"The system may think in many places, but it is only allowed to decide in one."

This module is the SINGLE unified mechanism for recording trade allow/reject
decisions. All pipeline stages route their decisions through this interface.

Responsibilities:
  - Central decision ledger
  - Standardised rejection reasons
  - Stage-level traceability
  - Structured logging for every decision

NOT responsible for:
  - Trading logic
  - Signal computation
  - Voter evaluation
  - Risk calculation

Ownership: core/pipeline/pipeline_authority.py
Mutability: Per-cycle decision log (reset each bar)
Dependencies: NONE (pure coordination layer)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


# ─── DECISION RECORD SCHEMA ──────────────────────────────────────────────────

@dataclass(frozen=True)
class DecisionRecord:
    """
    Immutable record of a pipeline decision.

    action: ALLOW | REJECT | OBSERVE
    stage: Which pipeline stage produced this decision
    reason: Why (None for ALLOW, descriptive for REJECT)
    metadata: Stage-specific context (scores, thresholds, etc.)
    timestamp: When the decision was recorded (monotonic)
    """

    action: str  # "ALLOW" | "REJECT" | "OBSERVE"
    stage: str
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)


# ─── VALID STAGES ─────────────────────────────────────────────────────────────

VALID_STAGES = frozenset({
    "market_context",
    "strategy_detection",
    "structure_analysis",
    "structure_scoring",
    "confirmations",
    "trade_quality_pre",
    "scoring_engine",
    "trade_quality_post",
    "htf_constraint",
    "execution_gate",
    "risk_engine",
    "intent_builder",
    "complete",
})


# ─── PIPELINE AUTHORITY ───────────────────────────────────────────────────────

class PipelineAuthority:
    """
    Centralised decision ledger for a single bar evaluation cycle.

    Usage:
        authority = PipelineAuthority(symbol="EURUSD")
        ...
        authority.reject("market_context", "session_closed", {"hour": 22})
        ...
        authority.allow("complete", {"score": 7, "signal": "BUY"})
        ...
        # At end of cycle:
        final = authority.final_decision
        trace = authority.decision_trace
    """

    def __init__(self, symbol: str = "") -> None:
        self._symbol = symbol
        self._decisions: list[DecisionRecord] = []
        self._final: DecisionRecord | None = None

    # ─── PUBLIC INTERFACE ─────────────────────────────────────────────

    def reject(
        self,
        stage: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        """
        Record a REJECT decision from a pipeline stage.

        This is the ONLY way a stage should express "do not trade".
        The rejection is logged, recorded, and returned for the caller
        to use as its early-exit value.

        Args:
            stage: Pipeline stage name (must be in VALID_STAGES)
            reason: Human-readable rejection reason
            metadata: Optional context (scores, thresholds, values)

        Returns:
            Frozen DecisionRecord for audit trail.
        """
        record = DecisionRecord(
            action="REJECT",
            stage=stage,
            reason=reason,
            metadata=metadata or {},
        )
        self._decisions.append(record)
        self._final = record

        _logger.info(
            "[PIPELINE_AUTHORITY] symbol=%s action=REJECT stage=%s reason=%s metadata=%s",
            self._symbol,
            stage,
            reason,
            metadata or {},
        )

        return record

    def allow(
        self,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        """
        Record an ALLOW decision (trade approved through final stage).

        Only called when the pipeline reaches successful completion.

        Args:
            stage: Pipeline stage name (typically "complete")
            metadata: Trade details (signal, score, intent, etc.)

        Returns:
            Frozen DecisionRecord for audit trail.
        """
        record = DecisionRecord(
            action="ALLOW",
            stage=stage,
            reason=None,
            metadata=metadata or {},
        )
        self._decisions.append(record)
        self._final = record

        _logger.info(
            "[PIPELINE_AUTHORITY] symbol=%s action=ALLOW stage=%s metadata=%s",
            self._symbol,
            stage,
            metadata or {},
        )

        return record

    def record(
        self,
        stage: str,
        event: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        """
        Record a non-blocking observation (diagnostics, scoring, voter output).

        Does NOT affect the final decision. Used for:
          - Structure scoring output
          - Voter traces
          - Diagnostic events
          - StrictnessBase observations

        Args:
            stage: Pipeline stage name
            event: Description of what was observed
            metadata: Context data

        Returns:
            Frozen DecisionRecord (action="OBSERVE").
        """
        record = DecisionRecord(
            action="OBSERVE",
            stage=stage,
            reason=event,
            metadata=metadata or {},
        )
        self._decisions.append(record)

        _logger.debug(
            "[PIPELINE_AUTHORITY] symbol=%s action=OBSERVE stage=%s event=%s",
            self._symbol,
            stage,
            event,
        )

        return record

    # ─── QUERY INTERFACE ──────────────────────────────────────────────

    @property
    def final_decision(self) -> DecisionRecord | None:
        """The last decision recorded (ALLOW or REJECT). None if no decision yet."""
        return self._final

    @property
    def decision_trace(self) -> list[DecisionRecord]:
        """Full ordered list of all decisions/observations this cycle."""
        return list(self._decisions)

    @property
    def rejection_count(self) -> int:
        """Number of REJECT decisions recorded this cycle."""
        return sum(1 for d in self._decisions if d.action == "REJECT")

    @property
    def is_rejected(self) -> bool:
        """True if any REJECT decision was recorded."""
        return any(d.action == "REJECT" for d in self._decisions)

    @property
    def rejection_stages(self) -> list[str]:
        """List of stages that issued REJECT decisions."""
        return [d.stage for d in self._decisions if d.action == "REJECT"]

    @property
    def symbol(self) -> str:
        return self._symbol

    def reset(self) -> None:
        """Reset for next cycle (called at start of each bar evaluation)."""
        self._decisions.clear()
        self._final = None

    # ─── SUMMARY ──────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Structured summary for logging/audit."""
        return {
            "symbol": self._symbol,
            "total_decisions": len(self._decisions),
            "rejections": self.rejection_count,
            "observations": sum(1 for d in self._decisions if d.action == "OBSERVE"),
            "final_action": self._final.action if self._final else None,
            "final_stage": self._final.stage if self._final else None,
            "final_reason": self._final.reason if self._final else None,
            "rejection_stages": self.rejection_stages,
        }
