"""
Strategy Observation Engine — Collects evidence of strategy condition
occurrence without influencing execution.

Each market cycle, this observer:
    1. Receives MarketContext + detected patterns
    2. Evaluates all registered strategies via StrategyConditionEvaluator
    3. Creates StrategyObservation records for persistence
    4. Accumulates evidence: "When conditions were present, what happened?"

This component:
    - NEVER influences execution
    - NEVER blocks trades
    - NEVER modifies scoring
    - ONLY collects evidence for future research validation

The observations created here become the training data for validating
whether "conditions met" correlates with positive outcome (future M9/M10).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.strategies.condition_evaluator import (
    ConditionEvaluationResult,
    StrategyConditionEvaluator,
    build_market_snapshot,
    snapshot_from_market_context,
)
from core.strategies.conditions import (
    STRATEGY_CONDITIONS,
    ConditionResult,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class StrategyObservation:
    """
    A single observation record capturing strategy condition state at one moment.

    Frozen at creation time. Outcome fields are updated later when trade
    results become available (forward-only mutation for outcome linkage).
    """
    # ─── IDENTITY ─────────────────────────────────────────────────────
    observation_id: str
    timestamp_utc: float
    cycle_id: int = 0
    symbol: str = ""

    # ─── STRATEGY CLASSIFICATION ──────────────────────────────────────
    strategy_id: str = ""
    family: str = ""                    # REVERSAL | MOMENTUM | CONTINUATION | BREAKOUT

    # ─── MARKET CONTEXT ───────────────────────────────────────────────
    regime: str = ""                    # TRENDING | RANGING | TRANSITIONAL
    market_phase: str = ""              # IMPULSE | PULLBACK | CONSOLIDATION | EXHAUSTION | REVERSAL
    direction: str = ""                 # BULLISH | BEARISH | NEUTRAL
    h4_trend_bias: str = ""
    h1_direction: str = ""
    m15_at_key_level: bool = False
    tradability_score: float = 0.0

    # ─── CONDITION EVALUATION ─────────────────────────────────────────
    eligible_by_phase: bool = False
    conditions_met: int = 0
    conditions_failed: int = 0
    conditions_missing: int = 0
    conditions_unavailable: int = 0
    confidence: float = 0.0
    overall_status: str = ""            # FULLY_MET | PARTIALLY_MET | NOT_MET | INCOMPLETE

    # ─── PATTERN / TRIGGER ────────────────────────────────────────────
    pattern_detected: str = ""
    pattern_in_strategy_triggers: bool = False

    # ─── OUTCOME (populated later by outcome linker) ──────────────────
    outcome_status: str = "PENDING"     # PENDING | WIN | LOSS | TIMEOUT | NO_TRADE
    outcome_r_multiple: float = 0.0
    outcome_linked: bool = False
    outcome_linked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "observation_id": self.observation_id,
            "timestamp_utc": self.timestamp_utc,
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "family": self.family,
            "regime": self.regime,
            "market_phase": self.market_phase,
            "direction": self.direction,
            "h4_trend_bias": self.h4_trend_bias,
            "h1_direction": self.h1_direction,
            "m15_at_key_level": self.m15_at_key_level,
            "tradability_score": round(self.tradability_score, 4),
            "eligible_by_phase": self.eligible_by_phase,
            "conditions_met": self.conditions_met,
            "conditions_failed": self.conditions_failed,
            "conditions_missing": self.conditions_missing,
            "conditions_unavailable": self.conditions_unavailable,
            "confidence": round(self.confidence, 4),
            "overall_status": self.overall_status,
            "pattern_detected": self.pattern_detected,
            "pattern_in_strategy_triggers": self.pattern_in_strategy_triggers,
            "outcome_status": self.outcome_status,
            "outcome_r_multiple": round(self.outcome_r_multiple, 4),
            "outcome_linked": self.outcome_linked,
            "outcome_linked_at": self.outcome_linked_at,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION CYCLE RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ObservationCycleResult:
    """
    Summary of one observation cycle across all strategies.

    Returned by StrategyObserver.observe() for diagnostics and logging.
    """
    cycle_id: int
    symbol: str
    timestamp_utc: float
    total_strategies_evaluated: int
    phase_eligible_count: int
    fully_met_count: int
    partially_met_count: int
    not_met_count: int
    observations_created: int
    phase_eligible_strategies: tuple[str, ...] = ()
    fully_met_strategies: tuple[str, ...] = ()

    @property
    def has_eligible_strategies(self) -> bool:
        return self.phase_eligible_count > 0

    @property
    def has_fully_met(self) -> bool:
        return self.fully_met_count > 0


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY OBSERVER
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyObserver:
    """
    Observes strategy condition state each cycle and creates observation records.

    OBSERVATION ONLY. Does not:
        - Influence execution
        - Block trades
        - Modify scoring
        - Connect to the decision engine

    Usage:
        observer = StrategyObserver()

        # Each cycle:
        result = observer.observe(
            market_context=ctx,
            pattern_detected="HAMMER",
            symbol="EURUSD",
            cycle_id=42,
        )

        # Access observations:
        observations = observer.get_observations()
        pending = observer.get_pending_observations()

        # Link outcome later:
        observer.link_outcome(observation_id, "WIN", r_multiple=1.5)
    """

    def __init__(self, *, max_observations: int = 10000) -> None:
        """
        Initialize observer.

        Args:
            max_observations: Maximum observations to hold in memory.
                              Oldest are evicted when limit reached.
        """
        self._evaluator = StrategyConditionEvaluator()
        self._observations: list[StrategyObservation] = []
        self._max_observations = max_observations
        self._total_cycles = 0

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    @property
    def total_cycles(self) -> int:
        return self._total_cycles

    def observe(
        self,
        *,
        market_context: Any = None,
        snapshot: dict[str, Any] | None = None,
        pattern_detected: str = "",
        symbol: str = "",
        cycle_id: int = 0,
        timestamp_utc: float | None = None,
    ) -> ObservationCycleResult:
        """
        Run one observation cycle: evaluate all strategies and create records.

        Accepts either a MarketContext object or a pre-built snapshot dict.
        At least one must be provided.

        Args:
            market_context: MarketContext object (preferred)
            snapshot: Pre-built flat dict (alternative)
            pattern_detected: Current detected pattern name
            symbol: Trading symbol
            cycle_id: Current processing cycle
            timestamp_utc: Observation timestamp (defaults to now)

        Returns:
            ObservationCycleResult summarising what was observed.
        """
        self._total_cycles += 1
        ts = timestamp_utc if timestamp_utc is not None else time.time()

        # Build snapshot from MarketContext or use provided
        if snapshot is not None:
            snap = dict(snapshot)
        elif market_context is not None:
            snap = snapshot_from_market_context(market_context)
        else:
            snap = build_market_snapshot()

        # Inject pattern into snapshot
        if pattern_detected:
            snap["pattern_detected"] = pattern_detected

        # Evaluate all strategies
        eval_results = self._evaluator.evaluate_all(snap)

        # Create observations
        observations_created = 0
        phase_eligible: list[str] = []
        fully_met: list[str] = []

        for eval_result in eval_results:
            obs = self._create_observation(
                eval_result=eval_result,
                snapshot=snap,
                pattern_detected=pattern_detected,
                symbol=symbol,
                cycle_id=cycle_id,
                timestamp_utc=ts,
            )
            self._store_observation(obs)
            observations_created += 1

            if eval_result.eligible_by_phase:
                phase_eligible.append(eval_result.strategy_id)
            if eval_result.overall_status == "FULLY_MET":
                fully_met.append(eval_result.strategy_id)

        # Summary counts
        not_met = sum(1 for r in eval_results if r.overall_status == "NOT_MET")
        partially = sum(1 for r in eval_results if r.overall_status == "PARTIALLY_MET")

        return ObservationCycleResult(
            cycle_id=cycle_id,
            symbol=symbol,
            timestamp_utc=ts,
            total_strategies_evaluated=len(eval_results),
            phase_eligible_count=len(phase_eligible),
            fully_met_count=len(fully_met),
            partially_met_count=partially,
            not_met_count=not_met,
            observations_created=observations_created,
            phase_eligible_strategies=tuple(phase_eligible),
            fully_met_strategies=tuple(fully_met),
        )


    # ═══════════════════════════════════════════════════════════════════
    # OBSERVATION ACCESS
    # ═══════════════════════════════════════════════════════════════════

    def get_observations(self) -> list[StrategyObservation]:
        """Return all stored observations."""
        return list(self._observations)

    def get_observations_for_strategy(self, strategy_id: str) -> list[StrategyObservation]:
        """Return observations for a specific strategy."""
        return [o for o in self._observations if o.strategy_id == strategy_id]

    def get_pending_observations(self) -> list[StrategyObservation]:
        """Return observations awaiting outcome linkage."""
        return [o for o in self._observations if o.outcome_status == "PENDING"]

    def get_eligible_observations(self) -> list[StrategyObservation]:
        """Return observations where strategy was phase-eligible."""
        return [o for o in self._observations if o.eligible_by_phase]

    def get_fully_met_observations(self) -> list[StrategyObservation]:
        """Return observations where all required conditions were met."""
        return [o for o in self._observations if o.overall_status == "FULLY_MET"]

    # ═══════════════════════════════════════════════════════════════════
    # OUTCOME LINKAGE
    # ═══════════════════════════════════════════════════════════════════

    def link_outcome(
        self,
        observation_id: str,
        outcome_status: str,
        r_multiple: float = 0.0,
    ) -> bool:
        """
        Link a trade outcome to an observation record.

        Called by the outcome tracker when a trade result is available.
        This is what creates the evidence: "conditions were met, outcome was X."

        Args:
            observation_id: The observation to update
            outcome_status: WIN | LOSS | TIMEOUT | NO_TRADE
            r_multiple: Outcome in R-multiples

        Returns:
            True if observation was found and updated, False otherwise.
        """
        for obs in self._observations:
            if obs.observation_id == observation_id:
                obs.outcome_status = outcome_status
                obs.outcome_r_multiple = r_multiple
                obs.outcome_linked = True
                obs.outcome_linked_at = time.time()
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_statistics(self) -> dict[str, Any]:
        """Return summary statistics of all observations."""
        total = len(self._observations)
        if total == 0:
            return {
                "total_observations": 0,
                "total_cycles": self._total_cycles,
                "by_strategy": {},
                "by_status": {},
                "outcome_linked": 0,
                "outcome_pending": 0,
            }

        by_strategy: dict[str, int] = {}
        by_status: dict[str, int] = {}
        linked = 0
        pending = 0

        for obs in self._observations:
            by_strategy[obs.strategy_id] = by_strategy.get(obs.strategy_id, 0) + 1
            by_status[obs.overall_status] = by_status.get(obs.overall_status, 0) + 1
            if obs.outcome_linked:
                linked += 1
            if obs.outcome_status == "PENDING":
                pending += 1

        return {
            "total_observations": total,
            "total_cycles": self._total_cycles,
            "by_strategy": by_strategy,
            "by_status": by_status,
            "outcome_linked": linked,
            "outcome_pending": pending,
            "phase_eligible_total": sum(1 for o in self._observations if o.eligible_by_phase),
            "fully_met_total": sum(1 for o in self._observations if o.overall_status == "FULLY_MET"),
        }

    def clear(self) -> None:
        """Clear all observations. For testing only."""
        self._observations.clear()
        self._total_cycles = 0


    # ═══════════════════════════════════════════════════════════════════
    # PRIVATE: Observation creation
    # ═══════════════════════════════════════════════════════════════════

    def _create_observation(
        self,
        *,
        eval_result: ConditionEvaluationResult,
        snapshot: dict[str, Any],
        pattern_detected: str,
        symbol: str,
        cycle_id: int,
        timestamp_utc: float,
    ) -> StrategyObservation:
        """Create a StrategyObservation from evaluation result + context."""
        # Determine family from strategy registry
        family = self._get_strategy_family(eval_result.strategy_id)

        # Check if pattern is in this strategy's trigger set
        pattern_in_triggers = self._pattern_in_triggers(
            eval_result.strategy_id, pattern_detected
        )

        # Count missing vs unavailable
        missing_count = len(eval_result.missing_data)
        unavailable_count = len(eval_result.unavailable_conditions)

        return StrategyObservation(
            observation_id=str(uuid.uuid4()),
            timestamp_utc=timestamp_utc,
            cycle_id=cycle_id,
            symbol=symbol,
            strategy_id=eval_result.strategy_id,
            family=family,
            regime=snapshot.get("regime", ""),
            market_phase=snapshot.get("phase", ""),
            direction=snapshot.get("direction", ""),
            h4_trend_bias=snapshot.get("h4.trend_bias", ""),
            h1_direction=snapshot.get("h1.direction", ""),
            m15_at_key_level=bool(snapshot.get("m15.at_key_level", False)),
            tradability_score=float(snapshot.get("tradability_score", 0.0)),
            eligible_by_phase=eval_result.eligible_by_phase,
            conditions_met=eval_result.conditions_passed,
            conditions_failed=eval_result.conditions_failed,
            conditions_missing=missing_count,
            conditions_unavailable=unavailable_count,
            confidence=eval_result.confidence,
            overall_status=eval_result.overall_status,
            pattern_detected=pattern_detected,
            pattern_in_strategy_triggers=pattern_in_triggers,
        )

    def _store_observation(self, obs: StrategyObservation) -> None:
        """Store observation, evicting oldest if at capacity."""
        if len(self._observations) >= self._max_observations:
            self._observations.pop(0)
        self._observations.append(obs)

    def _get_strategy_family(self, strategy_id: str) -> str:
        """Look up strategy family from registry."""
        try:
            from core.strategies.registry import get_strategy
            strategy = get_strategy(strategy_id)
            if strategy:
                return strategy.family_name
        except Exception:
            pass
        return ""

    def _pattern_in_triggers(self, strategy_id: str, pattern: str) -> bool:
        """Check if the detected pattern is in a strategy's trigger set."""
        if not pattern:
            return False
        try:
            from core.strategies.registry import get_strategy
            strategy = get_strategy(strategy_id)
            if strategy and strategy.trigger_patterns:
                return pattern.upper() in strategy.trigger_patterns
        except Exception:
            pass
        return False
