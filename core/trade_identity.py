"""
Trade Identity — Immutable decision-origin identity for a live trade.

This module defines the canonical identity that links a Position to the
decision that created it. Once created at execution time, the identity
is FROZEN and carried by the Position through its entire lifecycle.

INVARIANT:
    Every Position owns the immutable identity of the decision that created it.
    Identity is never reconstructed, recovered from thread-local context,
    regenerated, or derived from transient runtime state.

OWNERSHIP MODEL:
    Decision → ExecutionPrep → TradeIdentity → Position → Trade Journal → Trade Truth

FIELDS (all sourced from the execution pipeline — none invented):
    correlation_id   — COR-{YYYYMMDD}-{cycle}-{SYMBOL}-{hash} (from generate_correlation_id)
    decision_id      — Persisted decision audit ID (from persist_new_engine_decision_audit)
    cycle_id         — Decision cycle number (from live_scanner)
    strategy         — Strategy identifier (from engine result)
    pattern          — Pattern that triggered execution (from OrderIntent)
    decision_ts_utc  — Unix timestamp of the decision bar close (from closed_time)

RULES:
    - TradeIdentity is frozen (immutable after creation)
    - Created ONCE at execution preparation time
    - Passed into Position at registration
    - Read by all downstream persistence layers
    - Never regenerated or recovered from context
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeIdentity:
    """
    Immutable identity payload linking a Position to its originating decision.

    All fields are set at execution time and NEVER modified.
    This is the canonical evidence chain for auditing, analytics,
    and Research Engine experiments.
    """

    #: Decision Spine Correlation ID — globally unique per decision cycle.
    #: Format: COR-{YYYYMMDD}-{cycle_id}-{SYMBOL}-{hash4}
    correlation_id: str

    #: Decision audit record ID (empty string if audit persistence failed).
    decision_id: str = ""

    #: Canonical opportunity lineage root (remediation) — THE authoritative
    #: lineage ID linking this trade to its originating opportunity.
    canonical_opportunity_id: str = ""

    #: Retired lineage role — schema-compatibility only. No longer a root.
    observation_id: str = ""

    #: Cycle number from the live scanner at decision time.
    cycle_id: int = 0

    #: Strategy identifier from the engine result.
    strategy: str = ""

    #: Pattern that triggered the execution (from OrderIntent).
    pattern: str = ""

    #: Unix timestamp of the decision (bar close time).
    decision_ts_utc: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Serialise to plain dict for persistence layers."""
        return {
            "correlation_id": self.correlation_id,
            "decision_id": self.decision_id,
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "observation_id": self.observation_id,
            "cycle_id": self.cycle_id,
            "strategy": self.strategy,
            "pattern": self.pattern,
            "decision_ts_utc": self.decision_ts_utc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TradeIdentity":
        """Reconstruct from persisted dict (e.g. startup recovery)."""
        return cls(
            correlation_id=data.get("correlation_id", ""),
            decision_id=data.get("decision_id", ""),
            canonical_opportunity_id=data.get("canonical_opportunity_id", ""),
            observation_id=data.get("observation_id", ""),
            cycle_id=int(data.get("cycle_id", 0)),
            strategy=str(data.get("strategy", "")),
            pattern=str(data.get("pattern", "")),
            decision_ts_utc=float(data.get("decision_ts_utc", 0.0)),
        )

    @classmethod
    def empty(cls) -> "TradeIdentity":
        """Create an empty identity (for recovered positions without identity)."""
        return cls(correlation_id="")


# ═══════════════════════════════════════════════════════════════════════════════
# SENTINEL: Indicates a position has no identity (e.g. recovered from broker)
# ═══════════════════════════════════════════════════════════════════════════════

EMPTY_IDENTITY = TradeIdentity(correlation_id="")
