"""
Strategy Outcome Linker — Connects observations with trade outcomes.

Answers: "Did this strategy hypothesis succeed or fail?"

This is OBSERVATION AND EVIDENCE only. It does not:
    - Place trades
    - Modify scoring
    - Activate strategies
    - Connect to the decision engine

Flow:
    StrategyObservation (created each cycle)
        ↓
    Trade resolves (shadow or real)
        ↓
    StrategyOutcomeLinker.link_trade_result()
        ↓
    StrategyOutcomeLink (evidence pair)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class OutcomeStatus(str, Enum):
    """Outcome classification for a strategy observation."""
    PENDING = "PENDING"
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    EXPIRED = "EXPIRED"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class StrategyOutcomeLink:
    """
    Immutable record linking an observation to its outcome.

    This is the evidence pair:
        "Conditions X were present → Outcome was Y"
    """
    # ─── IDENTITY ─────────────────────────────────────────────────────
    link_id: str
    observation_id: str
    strategy_id: str
    timestamp_utc: float

    # ─── OUTCOME ──────────────────────────────────────────────────────
    outcome_status: OutcomeStatus
    realised_r: float = 0.0
    holding_time: float = 0.0        # seconds
    exit_reason: str = ""

    # ─── LINEAGE ──────────────────────────────────────────────────────
    trade_id: str = ""               # linked real trade (if any)
    linked_shadow_trade_id: str = "" # linked shadow trade (if any)
    entity_id: str = ""              # entity linkage key

    # ─── CONTEXT (frozen at link time) ────────────────────────────────
    family: str = ""
    market_phase: str = ""
    regime: str = ""
    conditions_met: int = 0
    confidence: float = 0.0

    # ─── METADATA ─────────────────────────────────────────────────────
    linked_at: float = 0.0
    source: str = ""                 # "shadow_trade" | "real_trade" | "manual"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "link_id": self.link_id,
            "observation_id": self.observation_id,
            "strategy_id": self.strategy_id,
            "timestamp_utc": self.timestamp_utc,
            "outcome_status": self.outcome_status.value,
            "realised_r": round(self.realised_r, 4),
            "holding_time": round(self.holding_time, 2),
            "exit_reason": self.exit_reason,
            "trade_id": self.trade_id,
            "linked_shadow_trade_id": self.linked_shadow_trade_id,
            "entity_id": self.entity_id,
            "family": self.family,
            "market_phase": self.market_phase,
            "regime": self.regime,
            "conditions_met": self.conditions_met,
            "confidence": round(self.confidence, 4),
            "linked_at": self.linked_at,
            "source": self.source,
        }

    @property
    def is_win(self) -> bool:
        return self.outcome_status == OutcomeStatus.WIN

    @property
    def is_loss(self) -> bool:
        return self.outcome_status == OutcomeStatus.LOSS


class StrategyOutcomeLinker:
    """
    Links strategy observations with trade outcomes.

    Creates StrategyOutcomeLink records that form the evidence base
    for research validation. Never overwrites existing evidence.

    Usage:
        linker = StrategyOutcomeLinker()

        link = linker.link_trade_result(
            observation_id="obs-123",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.5,
            holding_time=3600,
            exit_reason="take_profit",
            trade_id="trade-456",
            source="shadow_trade",
        )
    """

    def __init__(self) -> None:
        self._links: list[StrategyOutcomeLink] = []
        self._linked_observations: set[str] = set()

    @property
    def link_count(self) -> int:
        return len(self._links)

    def link_trade_result(
        self,
        *,
        observation_id: str,
        strategy_id: str,
        outcome_status: OutcomeStatus | str,
        realised_r: float = 0.0,
        holding_time: float = 0.0,
        exit_reason: str = "",
        trade_id: str = "",
        linked_shadow_trade_id: str = "",
        entity_id: str = "",
        family: str = "",
        market_phase: str = "",
        regime: str = "",
        conditions_met: int = 0,
        confidence: float = 0.0,
        source: str = "",
        timestamp_utc: float | None = None,
    ) -> StrategyOutcomeLink | None:
        """
        Create an outcome link for an observation.

        Args:
            observation_id: The observation being linked.
            strategy_id: Strategy that was observed.
            outcome_status: What happened (WIN/LOSS/etc.)
            realised_r: R-multiple achieved.
            holding_time: How long the trade was held (seconds).
            exit_reason: Why the trade closed.
            trade_id: Real trade ID if applicable.
            linked_shadow_trade_id: Shadow trade ID if applicable.
            entity_id: Entity linkage key.
            family: Strategy family at observation time.
            market_phase: Phase at observation time.
            regime: Regime at observation time.
            conditions_met: Number of conditions met.
            confidence: Confidence at observation time.
            source: "shadow_trade" | "real_trade" | "manual"
            timestamp_utc: When the observation occurred.

        Returns:
            StrategyOutcomeLink if created, None if duplicate or invalid.
        """
        if not observation_id:
            logger.warning("[OUTCOME_LINKER] Cannot link: empty observation_id")
            return None

        if not strategy_id:
            logger.warning("[OUTCOME_LINKER] Cannot link: empty strategy_id")
            return None

        # Prevent duplicate linking
        if observation_id in self._linked_observations:
            logger.debug(
                "[OUTCOME_LINKER] Observation '%s' already linked, skipping",
                observation_id,
            )
            return None

        # Normalize outcome_status
        if isinstance(outcome_status, str):
            try:
                outcome_status = OutcomeStatus(outcome_status)
            except ValueError:
                logger.warning(
                    "[OUTCOME_LINKER] Invalid outcome_status '%s'", outcome_status
                )
                return None

        ts = timestamp_utc if timestamp_utc is not None else time.time()

        link = StrategyOutcomeLink(
            link_id=str(uuid.uuid4()),
            observation_id=observation_id,
            strategy_id=strategy_id,
            timestamp_utc=ts,
            outcome_status=outcome_status,
            realised_r=realised_r,
            holding_time=holding_time,
            exit_reason=exit_reason,
            trade_id=trade_id,
            linked_shadow_trade_id=linked_shadow_trade_id,
            entity_id=entity_id,
            family=family,
            market_phase=market_phase,
            regime=regime,
            conditions_met=conditions_met,
            confidence=confidence,
            linked_at=time.time(),
            source=source,
        )

        self._links.append(link)
        self._linked_observations.add(observation_id)
        return link

    # ─── ACCESS ───────────────────────────────────────────────────────

    def get_all_links(self) -> list[StrategyOutcomeLink]:
        """Return all outcome links."""
        return list(self._links)

    def get_links_for_strategy(self, strategy_id: str) -> list[StrategyOutcomeLink]:
        """Return links for a specific strategy."""
        return [l for l in self._links if l.strategy_id == strategy_id]

    def get_links_for_observation(self, observation_id: str) -> StrategyOutcomeLink | None:
        """Return the link for a specific observation (if any)."""
        for l in self._links:
            if l.observation_id == observation_id:
                return l
        return None

    def is_linked(self, observation_id: str) -> bool:
        """Check if an observation has been linked."""
        return observation_id in self._linked_observations

    def get_wins(self) -> list[StrategyOutcomeLink]:
        """Return all WIN links."""
        return [l for l in self._links if l.is_win]

    def get_losses(self) -> list[StrategyOutcomeLink]:
        """Return all LOSS links."""
        return [l for l in self._links if l.is_loss]

    def clear(self) -> None:
        """Clear all links. For testing only."""
        self._links.clear()
        self._linked_observations.clear()
