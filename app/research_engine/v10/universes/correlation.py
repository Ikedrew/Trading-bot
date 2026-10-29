"""
Execution ↔ Decision Correlation Layer.

This module provides an OPTIONAL correlation between the Execution and Decision
universes. It does NOT modify or filter the canonical populations.

Key design rules:
    - Four universes remain independently buildable
    - Failed correlation NEVER removes a canonical record
    - Uncorrelated records remain visible in their universe
    - Correlation is a research capability, not a data dependency
    - Questions not requiring cross-universe joins remain unaffected

Historical coverage: ~9.6% (9/94 execution records reliably correlatable)
Reason: Execution entity_id = pos_TICKET, Decision entity_id = SYMBOL_CYCLE_TS
        — different identity schemes with no shared deterministic key.

Correlation method: TEMPORAL_RECONSTRUCTION
    Reconstructs decision entity_id from execution (symbol + floor(entry_time/300)*300)
    with +/- 2 cycle tolerance (600s window).

Future improvement: Store decision_entity_id at order placement time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class CorrelationStatus(str, Enum):
    """Explicit correlation state for each record."""
    NOT_EVALUATED = "NOT_EVALUATED"
    CORRELATED = "CORRELATED"
    UNCORRELATED = "UNCORRELATED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_CORRELATION = "INVALID_CORRELATION"


class CorrelationMethod(str, Enum):
    """How a correlation was established."""
    TEMPORAL_RECONSTRUCTION = "temporal_reconstruction"
    CORRELATION_ID_MATCH = "correlation_id_match"
    MANUAL = "manual"
    NONE = "none"


class CorrelationTrust(str, Enum):
    """Overall trustworthiness classification."""
    TRUSTWORTHY = "TRUSTWORTHY"
    PARTIAL_BUT_USABLE = "PARTIAL_BUT_USABLE"
    INSUFFICIENT = "INSUFFICIENT"
    UNAVAILABLE = "UNAVAILABLE"


class RelationshipType(str, Enum):
    """Classification of the correlation relationship."""
    DETERMINISTIC_1_TO_1 = "DETERMINISTIC_1_TO_1"
    TEMPORAL_CORRELATION = "TEMPORAL_CORRELATION"
    PARTIAL_CORRELATION = "PARTIAL_CORRELATION"
    NO_RELIABLE_CORRELATION = "NO_RELIABLE_CORRELATION"


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CorrelationContract:
    """Formal contract for a cross-universe correlation."""
    join_id: str
    left_universe: str
    right_universe: str
    relationship_type: RelationshipType
    correlation_method: CorrelationMethod
    left_key: str
    right_key: str
    temporal_window_seconds: int  # Max time delta for temporal correlation
    symbol_constrained: bool  # Must match on symbol
    cardinality: str  # "1:1", "1:N", etc.
    historical_coverage: float  # 0.0-1.0
    historical_match_count: int
    historical_total: int
    trust_classification: CorrelationTrust
    ambiguity_policy: str
    unmatched_policy: str
    version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "join_id": self.join_id,
            "left_universe": self.left_universe,
            "right_universe": self.right_universe,
            "relationship_type": self.relationship_type.value,
            "correlation_method": self.correlation_method.value,
            "left_key": self.left_key,
            "right_key": self.right_key,
            "temporal_window_seconds": self.temporal_window_seconds,
            "symbol_constrained": self.symbol_constrained,
            "cardinality": self.cardinality,
            "historical_coverage": self.historical_coverage,
            "historical_match_count": self.historical_match_count,
            "historical_total": self.historical_total,
            "trust_classification": self.trust_classification.value,
            "ambiguity_policy": self.ambiguity_policy,
            "unmatched_policy": self.unmatched_policy,
            "version": self.version,
        }


# The canonical correlation contract for Execution ↔ Decision
EXECUTION_DECISION_CORRELATION = CorrelationContract(
    join_id="execution_decision_correlation",
    left_universe="EXECUTION",
    right_universe="DECISION",
    relationship_type=RelationshipType.PARTIAL_CORRELATION,
    correlation_method=CorrelationMethod.TEMPORAL_RECONSTRUCTION,
    left_key="symbol + entry_time",
    right_key="entity_id (SYMBOL_CYCLE_TIMESTAMP)",
    temporal_window_seconds=600,
    symbol_constrained=True,
    cardinality="1:1",
    historical_coverage=0.096,
    historical_match_count=9,
    historical_total=94,
    trust_classification=CorrelationTrust.PARTIAL_BUT_USABLE,
    ambiguity_policy="If multiple decisions within window, select closest by time",
    unmatched_policy="Record remains in canonical universe with status=UNCORRELATED",
    version="1.0.0",
)


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION RECORD
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CorrelationRecord:
    """A single correlation result between two universe records."""
    execution_id: str  # trade_id from Execution Universe
    decision_id: str  # entity_id from Decision Universe (or empty)
    status: CorrelationStatus
    method: CorrelationMethod
    confidence: float  # 0.0-1.0
    time_delta_seconds: float | None = None
    symbol: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "decision_id": self.decision_id,
            "status": self.status.value,
            "method": self.method.value,
            "confidence": self.confidence,
            "time_delta_seconds": self.time_delta_seconds,
            "symbol": self.symbol,
            "notes": self.notes,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


class CorrelationEngine:
    """
    Correlates Execution records with Decision records.

    Does NOT modify canonical universe populations.
    Produces a separate correlation layer that questions can optionally use.
    """

    def __init__(self, temporal_window: int = 600):
        self._window = temporal_window
        self._results: list[CorrelationRecord] = []

    def correlate(
        self,
        execution_records: list[dict[str, Any]],
        decision_records: list[dict[str, Any]],
    ) -> list[CorrelationRecord]:
        """
        Attempt to correlate execution records with decision records.

        Uses temporal reconstruction: SYMBOL_floor(entry_time/300)*300
        with +/- window tolerance.

        Args:
            execution_records: Normalised execution universe records.
            decision_records: Normalised decision universe records (EXECUTE only).

        Returns:
            List of CorrelationRecords (one per execution record).
        """
        # Build decision lookup: entity_id → record
        dec_by_entity = {}
        dec_entities_by_symbol: dict[str, list[tuple[int, str]]] = {}

        for d in decision_records:
            eid = d.get("entity_id", "")
            if not eid:
                continue
            dec_by_entity[eid] = d
            sym = d.get("symbol", "")
            parts = eid.split("_")
            if len(parts) >= 2 and sym:
                try:
                    ts = int(parts[-1])
                    dec_entities_by_symbol.setdefault(sym, []).append((ts, eid))
                except ValueError:
                    pass

        # Sort for efficient searching
        for sym in dec_entities_by_symbol:
            dec_entities_by_symbol[sym].sort()

        results = []
        for exe in execution_records:
            record = self._correlate_one(exe, dec_entities_by_symbol)
            results.append(record)

        self._results = results
        return results

    def _correlate_one(
        self,
        exe: dict[str, Any],
        dec_lookup: dict[str, list[tuple[int, str]]],
    ) -> CorrelationRecord:
        """Correlate a single execution record."""
        trade_id = exe.get("trade_id", exe.get("entity_id", ""))
        symbol = exe.get("symbol", "")
        entry_time = exe.get("entry_time")

        if not symbol or not entry_time:
            return CorrelationRecord(
                execution_id=trade_id,
                decision_id="",
                status=CorrelationStatus.UNCORRELATED,
                method=CorrelationMethod.NONE,
                confidence=0.0,
                symbol=symbol,
                notes="Missing symbol or entry_time",
            )

        # Temporal reconstruction
        candidates = dec_lookup.get(symbol, [])
        if not candidates:
            return CorrelationRecord(
                execution_id=trade_id,
                decision_id="",
                status=CorrelationStatus.UNCORRELATED,
                method=CorrelationMethod.NONE,
                confidence=0.0,
                symbol=symbol,
                notes=f"No EXECUTE decisions for {symbol}",
            )

        # Find closest within window
        best_delta = float("inf")
        best_eid = ""
        matches_in_window = 0

        for dec_ts, eid in candidates:
            delta = abs(entry_time - dec_ts)
            if delta <= self._window:
                matches_in_window += 1
            if delta < best_delta:
                best_delta = delta
                best_eid = eid

        ambiguous = matches_in_window > 1

        if best_delta <= self._window:
            if ambiguous:
                return CorrelationRecord(
                    execution_id=trade_id,
                    decision_id=best_eid,
                    status=CorrelationStatus.AMBIGUOUS,
                    method=CorrelationMethod.TEMPORAL_RECONSTRUCTION,
                    confidence=0.5,
                    time_delta_seconds=best_delta,
                    symbol=symbol,
                    notes="Multiple decisions within window; closest selected",
                )
            else:
                # Confidence inversely proportional to time delta
                confidence = max(0.3, 1.0 - (best_delta / self._window))
                return CorrelationRecord(
                    execution_id=trade_id,
                    decision_id=best_eid,
                    status=CorrelationStatus.CORRELATED,
                    method=CorrelationMethod.TEMPORAL_RECONSTRUCTION,
                    confidence=round(confidence, 3),
                    time_delta_seconds=best_delta,
                    symbol=symbol,
                    notes=f"Matched within {best_delta:.0f}s",
                )
        else:
            return CorrelationRecord(
                execution_id=trade_id,
                decision_id="",
                status=CorrelationStatus.UNCORRELATED,
                method=CorrelationMethod.TEMPORAL_RECONSTRUCTION,
                confidence=0.0,
                time_delta_seconds=best_delta,
                symbol=symbol,
                notes=f"Closest decision is {best_delta:.0f}s away (>{self._window}s window)",
            )

    @property
    def results(self) -> list[CorrelationRecord]:
        return self._results

    @property
    def coverage(self) -> dict[str, Any]:
        """Compute coverage statistics."""
        if not self._results:
            return {"total": 0}
        total = len(self._results)
        correlated = sum(1 for r in self._results if r.status == CorrelationStatus.CORRELATED)
        uncorrelated = sum(1 for r in self._results if r.status == CorrelationStatus.UNCORRELATED)
        ambiguous = sum(1 for r in self._results if r.status == CorrelationStatus.AMBIGUOUS)
        return {
            "total": total,
            "correlated": correlated,
            "uncorrelated": uncorrelated,
            "ambiguous": ambiguous,
            "coverage_rate": round(correlated / total, 4) if total else 0,
            "ambiguity_rate": round(ambiguous / total, 4) if total else 0,
        }
