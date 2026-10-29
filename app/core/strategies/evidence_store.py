"""
Strategy Evidence Store — Persists observations and outcomes as evidence.

Creates StrategyEvidenceRecord combining observation context with outcome.
Supports queries for research validation.

This is RESEARCH ONLY. It does not:
    - Influence execution
    - Modify scoring
    - Activate strategies
    - Connect to the decision engine

Flow:
    StrategyObservation + StrategyOutcomeLink
        ↓
    StrategyEvidenceRecord
        ↓
    StrategyEvidenceStore (in-memory + JSONL persistence)
        ↓
    Research Queries
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.strategies.outcome_linker import OutcomeStatus, StrategyOutcomeLink
from core.strategies.strategy_observer import StrategyObservation

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE RECORD
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StrategyEvidenceRecord:
    """
    Complete evidence record combining observation context with outcome.

    This is the fundamental unit of strategy research:
        "These conditions existed → This outcome occurred"
    """
    # ─── IDENTITY ─────────────────────────────────────────────────────
    evidence_id: str
    observation_id: str
    strategy_id: str
    created_at: float

    # ─── OBSERVATION CONTEXT ──────────────────────────────────────────
    family: str = ""
    market_phase: str = ""
    regime: str = ""
    direction: str = ""
    conditions_met: int = 0
    conditions_failed: int = 0
    conditions_missing: int = 0
    confidence: float = 0.0
    overall_status: str = ""         # FULLY_MET | PARTIALLY_MET | NOT_MET
    pattern_detected: str = ""
    eligible_by_phase: bool = False

    # ─── OUTCOME ──────────────────────────────────────────────────────
    outcome_status: str = "PENDING"  # WIN | LOSS | BREAKEVEN | EXPIRED | NO_TRADE | PENDING
    realised_r: float = 0.0
    holding_time: float = 0.0
    exit_reason: str = ""

    # ─── METADATA ─────────────────────────────────────────────────────
    symbol: str = ""
    cycle_id: int = 0
    source: str = ""                 # "shadow_trade" | "real_trade" | "manual"
    version: str = "1.0"

    @property
    def has_outcome(self) -> bool:
        return self.outcome_status != "PENDING"

    @property
    def is_win(self) -> bool:
        return self.outcome_status == "WIN"

    @property
    def is_loss(self) -> bool:
        return self.outcome_status == "LOSS"

    @property
    def is_resolved(self) -> bool:
        return self.outcome_status not in ("PENDING",)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "evidence_id": self.evidence_id,
            "observation_id": self.observation_id,
            "strategy_id": self.strategy_id,
            "created_at": self.created_at,
            "family": self.family,
            "market_phase": self.market_phase,
            "regime": self.regime,
            "direction": self.direction,
            "conditions_met": self.conditions_met,
            "conditions_failed": self.conditions_failed,
            "conditions_missing": self.conditions_missing,
            "confidence": round(self.confidence, 4),
            "overall_status": self.overall_status,
            "pattern_detected": self.pattern_detected,
            "eligible_by_phase": self.eligible_by_phase,
            "outcome_status": self.outcome_status,
            "realised_r": round(self.realised_r, 4),
            "holding_time": round(self.holding_time, 2),
            "exit_reason": self.exit_reason,
            "symbol": self.symbol,
            "cycle_id": self.cycle_id,
            "source": self.source,
            "version": self.version,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE STORE
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyEvidenceStore:
    """
    Stores and queries strategy evidence records.

    Supports:
        - In-memory storage (primary)
        - JSONL file persistence (optional, fire-and-forget)
        - Research queries (by strategy, family, phase)

    Usage:
        store = StrategyEvidenceStore()

        # Save from observation
        record = store.save_observation(observation)

        # Link outcome later
        store.link_outcome(observation_id, outcome_link)

        # Query
        stats = store.get_strategy_statistics("range_reversal_v1")
    """

    def __init__(
        self,
        *,
        persistence_path: str | None = None,
        max_records: int = 50000,
    ) -> None:
        """
        Initialize evidence store.

        Args:
            persistence_path: Optional path for JSONL persistence.
                              If None, in-memory only.
            max_records: Maximum records in memory (FIFO eviction).
        """
        self._records: list[StrategyEvidenceRecord] = []
        self._by_observation_id: dict[str, StrategyEvidenceRecord] = {}
        self._max_records = max_records
        self._persistence_path = persistence_path

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def resolved_count(self) -> int:
        return sum(1 for r in self._records if r.is_resolved)

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self._records if not r.has_outcome)

    # ═══════════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════════

    def save_observation(
        self, observation: StrategyObservation
    ) -> StrategyEvidenceRecord:
        """
        Create an evidence record from a StrategyObservation.

        Initially created with PENDING outcome. Outcome is linked later.
        """
        record = StrategyEvidenceRecord(
            evidence_id=str(uuid.uuid4()),
            observation_id=observation.observation_id,
            strategy_id=observation.strategy_id,
            created_at=observation.timestamp_utc,
            family=observation.family,
            market_phase=observation.market_phase,
            regime=observation.regime,
            direction=observation.direction,
            conditions_met=observation.conditions_met,
            conditions_failed=observation.conditions_failed,
            conditions_missing=observation.conditions_missing,
            confidence=observation.confidence,
            overall_status=observation.overall_status,
            pattern_detected=observation.pattern_detected,
            eligible_by_phase=observation.eligible_by_phase,
            outcome_status="PENDING",
            symbol=observation.symbol,
            cycle_id=observation.cycle_id,
        )

        self._store_record(record)
        return record

    def link_outcome(
        self,
        observation_id: str,
        outcome_link: StrategyOutcomeLink,
    ) -> StrategyEvidenceRecord | None:
        """
        Link an outcome to an existing evidence record.

        Creates a new immutable record replacing the pending one.
        Returns the updated record, or None if observation not found.
        """
        existing = self._by_observation_id.get(observation_id)
        if existing is None:
            logger.debug(
                "[EVIDENCE_STORE] No record for observation '%s'",
                observation_id,
            )
            return None

        if existing.has_outcome:
            logger.debug(
                "[EVIDENCE_STORE] Observation '%s' already has outcome, skipping",
                observation_id,
            )
            return existing

        # Create updated record (frozen dataclass — must replace)
        updated = StrategyEvidenceRecord(
            evidence_id=existing.evidence_id,
            observation_id=existing.observation_id,
            strategy_id=existing.strategy_id,
            created_at=existing.created_at,
            family=existing.family,
            market_phase=existing.market_phase,
            regime=existing.regime,
            direction=existing.direction,
            conditions_met=existing.conditions_met,
            conditions_failed=existing.conditions_failed,
            conditions_missing=existing.conditions_missing,
            confidence=existing.confidence,
            overall_status=existing.overall_status,
            pattern_detected=existing.pattern_detected,
            eligible_by_phase=existing.eligible_by_phase,
            outcome_status=outcome_link.outcome_status.value,
            realised_r=outcome_link.realised_r,
            holding_time=outcome_link.holding_time,
            exit_reason=outcome_link.exit_reason,
            symbol=existing.symbol,
            cycle_id=existing.cycle_id,
            source=outcome_link.source,
            version=existing.version,
        )

        # Replace in storage
        idx = self._records.index(existing)
        self._records[idx] = updated
        self._by_observation_id[observation_id] = updated
        self._persist_record(updated)

        return updated


    # ═══════════════════════════════════════════════════════════════════
    # QUERY
    # ═══════════════════════════════════════════════════════════════════

    def get_all_records(self) -> list[StrategyEvidenceRecord]:
        """Return all evidence records."""
        return list(self._records)

    def get_record_by_observation(
        self, observation_id: str
    ) -> StrategyEvidenceRecord | None:
        """Get evidence record for an observation."""
        return self._by_observation_id.get(observation_id)

    def get_records_for_strategy(
        self, strategy_id: str
    ) -> list[StrategyEvidenceRecord]:
        """Get all records for a strategy."""
        return [r for r in self._records if r.strategy_id == strategy_id]

    def get_resolved_records(self) -> list[StrategyEvidenceRecord]:
        """Get all records with linked outcomes."""
        return [r for r in self._records if r.is_resolved]

    def get_records_for_family(self, family: str) -> list[StrategyEvidenceRecord]:
        """Get all records for a strategy family."""
        return [r for r in self._records if r.family == family]

    def get_records_for_phase(self, phase: str) -> list[StrategyEvidenceRecord]:
        """Get all records observed during a specific phase."""
        return [r for r in self._records if r.market_phase == phase]

    def get_records_for_context(
        self, *, strategy_id: str = "", family: str = "",
        phase: str = "", regime: str = "", resolved_only: bool = False,
    ) -> list[StrategyEvidenceRecord]:
        """Flexible query with multiple filters."""
        results = self._records
        if strategy_id:
            results = [r for r in results if r.strategy_id == strategy_id]
        if family:
            results = [r for r in results if r.family == family]
        if phase:
            results = [r for r in results if r.market_phase == phase]
        if regime:
            results = [r for r in results if r.regime == regime]
        if resolved_only:
            results = [r for r in results if r.is_resolved]
        return results

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_strategy_statistics(
        self, strategy_id: str
    ) -> dict[str, Any]:
        """
        Compute statistics for a single strategy.

        Returns sample size, win rate, average R, expectancy.
        """
        records = self.get_records_for_context(
            strategy_id=strategy_id, resolved_only=True
        )
        return self._compute_statistics(records, strategy_id)

    def get_family_statistics(self, family: str) -> dict[str, Any]:
        """Compute statistics for a strategy family."""
        records = self.get_records_for_context(
            family=family, resolved_only=True
        )
        return self._compute_statistics(records, f"family:{family}")

    def get_phase_strategy_performance(self) -> dict[str, dict[str, Any]]:
        """
        Get performance grouped by phase × strategy.

        Returns nested dict: {phase: {strategy_id: stats}}
        """
        resolved = self.get_resolved_records()
        phases: dict[str, dict[str, list[StrategyEvidenceRecord]]] = {}

        for r in resolved:
            if r.market_phase not in phases:
                phases[r.market_phase] = {}
            if r.strategy_id not in phases[r.market_phase]:
                phases[r.market_phase][r.strategy_id] = []
            phases[r.market_phase][r.strategy_id].append(r)

        result: dict[str, dict[str, Any]] = {}
        for phase, strategies in phases.items():
            result[phase] = {}
            for sid, records in strategies.items():
                result[phase][sid] = self._compute_statistics(records, sid)

        return result

    def _compute_statistics(
        self, records: list[StrategyEvidenceRecord], label: str
    ) -> dict[str, Any]:
        """Compute core statistics from a list of records."""
        n = len(records)
        if n == 0:
            return {
                "label": label,
                "sample_size": 0,
                "win_rate": 0.0,
                "loss_rate": 0.0,
                "average_r": 0.0,
                "expectancy": 0.0,
                "total_r": 0.0,
                "confidence": "INSUFFICIENT",
            }

        wins = sum(1 for r in records if r.is_win)
        losses = sum(1 for r in records if r.is_loss)
        total_r = sum(r.realised_r for r in records)
        avg_r = total_r / n

        win_rate = wins / n if n > 0 else 0.0
        loss_rate = losses / n if n > 0 else 0.0

        # Expectancy = average R per trade
        expectancy = avg_r

        # Confidence classification
        if n >= 100:
            confidence = "HIGH"
        elif n >= 50:
            confidence = "MEDIUM"
        elif n >= 20:
            confidence = "LOW"
        else:
            confidence = "INSUFFICIENT"

        return {
            "label": label,
            "sample_size": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "loss_rate": round(loss_rate, 4),
            "average_r": round(avg_r, 4),
            "total_r": round(total_r, 4),
            "expectancy": round(expectancy, 4),
            "confidence": confidence,
        }

    # ═══════════════════════════════════════════════════════════════════
    # PRIVATE
    # ═══════════════════════════════════════════════════════════════════

    def _store_record(self, record: StrategyEvidenceRecord) -> None:
        """Store record with capacity management."""
        if len(self._records) >= self._max_records:
            evicted = self._records.pop(0)
            self._by_observation_id.pop(evicted.observation_id, None)

        self._records.append(record)
        self._by_observation_id[record.observation_id] = record
        self._persist_record(record)

    def _persist_record(self, record: StrategyEvidenceRecord) -> None:
        """Persist record to JSONL file. Fire-and-forget."""
        if not self._persistence_path:
            return
        try:
            path = Path(self._persistence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception:
            pass  # Persistence failure must never affect operation

    def clear(self) -> None:
        """Clear all records. For testing only."""
        self._records.clear()
        self._by_observation_id.clear()
