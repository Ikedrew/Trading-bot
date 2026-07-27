"""
Tests for core/strategies/strategy_observer.py — Strategy Observation Engine.

Verifies:
    - Strategies are evaluated each cycle
    - Observations are created with correct data
    - Missing data is handled safely
    - Execution behaviour is unchanged
    - Outcome linkage works correctly
    - Statistics are accurate
"""

import time

import pytest

from core.strategies.condition_evaluator import build_market_snapshot
from core.strategies.strategy_observer import (
    ObservationCycleResult,
    StrategyObservation,
    StrategyObserver,
)


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION CREATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestObservationCreation:
    """Tests that observations are created correctly each cycle."""

    def setup_method(self):
        self.observer = StrategyObserver()

    def test_observe_creates_observations_for_all_strategies(self):
        """One observation per registered strategy per cycle."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        result = self.observer.observe(snapshot=snapshot, symbol="EURUSD", cycle_id=1)

        assert result.observations_created == 5
        assert result.total_strategies_evaluated == 5
        assert self.observer.observation_count == 5

    def test_observation_has_correct_strategy_id(self):
        """Each observation must reference its strategy."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        observations = self.observer.get_observations()
        strategy_ids = [o.strategy_id for o in observations]
        assert "range_reversal_v1" in strategy_ids
        assert "momentum_expansion_v1" in strategy_ids

    def test_observation_has_market_context(self):
        """Observations must capture market context."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            direction="BEARISH", h1_direction="BEARISH",
            m15_at_key_level=True, tradability_score=0.7,
        )
        self.observer.observe(snapshot=snapshot, symbol="GBPUSD", cycle_id=5)

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        assert len(obs) == 1
        assert obs[0].regime == "RANGING"
        assert obs[0].market_phase == "REVERSAL"
        assert obs[0].direction == "BEARISH"
        assert obs[0].symbol == "GBPUSD"
        assert obs[0].cycle_id == 5
        assert obs[0].tradability_score == 0.7

    def test_observation_has_condition_counts(self):
        """Observations must have passed/failed/missing counts."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        assert obs[0].conditions_met > 0
        assert obs[0].confidence > 0.0

    def test_observation_has_family(self):
        """Observations must include strategy family."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        assert obs[0].family == "REVERSAL"

        obs_m = self.observer.get_observations_for_strategy("momentum_expansion_v1")
        assert obs_m[0].family == "MOMENTUM"

    def test_observation_has_pattern_info(self):
        """Observations must capture detected pattern."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True,
        )
        self.observer.observe(
            snapshot=snapshot, cycle_id=1, pattern_detected="HAMMER",
        )

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        assert obs[0].pattern_detected == "HAMMER"
        assert obs[0].pattern_in_strategy_triggers is True

    def test_pattern_not_in_triggers_marked(self):
        """Pattern outside strategy triggers is marked correctly."""
        snapshot = build_market_snapshot(
            regime="TRENDING", phase="IMPULSE",
            pattern_detected="HAMMER",
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        # HAMMER is not in momentum_expansion triggers
        obs = self.observer.get_observations_for_strategy("momentum_expansion_v1")
        assert obs[0].pattern_in_strategy_triggers is False

    def test_observation_has_unique_id(self):
        """Each observation must have a unique ID."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        observations = self.observer.get_observations()
        ids = [o.observation_id for o in observations]
        assert len(ids) == len(set(ids))  # All unique


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLE RESULT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycleResult:
    """Tests for ObservationCycleResult returned by observe()."""

    def setup_method(self):
        self.observer = StrategyObserver()

    def test_cycle_result_has_eligible_strategies(self):
        """Result must identify phase-eligible strategies."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        result = self.observer.observe(snapshot=snapshot, cycle_id=1)

        assert result.has_eligible_strategies
        assert "range_reversal_v1" in result.phase_eligible_strategies
        assert "momentum_expansion_v1" not in result.phase_eligible_strategies

    def test_cycle_result_has_fully_met(self):
        """Result must identify fully met strategies."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        result = self.observer.observe(snapshot=snapshot, cycle_id=1)

        assert result.has_fully_met
        assert "range_reversal_v1" in result.fully_met_strategies

    def test_cycle_result_counts(self):
        """Result must have correct count totals."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
        )
        result = self.observer.observe(snapshot=snapshot, cycle_id=1)

        assert result.total_strategies_evaluated == 5
        assert result.observations_created == 5
        total = (result.fully_met_count + result.partially_met_count +
                 result.not_met_count)
        # Some strategies might be INCOMPLETE or other status
        assert total <= result.total_strategies_evaluated

    def test_multiple_cycles_accumulate(self):
        """Multiple observe() calls accumulate observations."""
        snapshot1 = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        snapshot2 = build_market_snapshot(regime="TRENDING", phase="IMPULSE")

        self.observer.observe(snapshot=snapshot1, cycle_id=1)
        self.observer.observe(snapshot=snapshot2, cycle_id=2)

        assert self.observer.observation_count == 10  # 5 per cycle
        assert self.observer.total_cycles == 2


# ═══════════════════════════════════════════════════════════════════════════════
# MISSING DATA HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingDataHandling:
    """Tests that missing data is handled safely."""

    def setup_method(self):
        self.observer = StrategyObserver()

    def test_empty_snapshot_does_not_crash(self):
        """Empty snapshot creates observations without crashing."""
        result = self.observer.observe(snapshot={}, cycle_id=1)

        assert result.observations_created == 5
        assert self.observer.observation_count == 5

    def test_no_arguments_does_not_crash(self):
        """Calling observe with no context creates observations."""
        result = self.observer.observe(cycle_id=1)

        assert result.observations_created == 5

    def test_missing_data_recorded_in_observation(self):
        """Observations record what data was missing."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        # Pattern not provided — should be missing
        assert obs[0].conditions_missing > 0 or obs[0].pattern_detected == ""

    def test_partial_data_still_evaluates(self):
        """Partial market data still produces meaningful evaluations."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            # No phase, no pattern, no levels
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        obs = self.observer.get_observations_for_strategy("range_reversal_v1")
        assert obs[0].overall_status in ("NOT_MET", "PARTIALLY_MET", "INCOMPLETE")


# ═══════════════════════════════════════════════════════════════════════════════
# OUTCOME LINKAGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutcomeLinkage:
    """Tests for linking trade outcomes to observations."""

    def setup_method(self):
        self.observer = StrategyObserver()
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

    def test_link_outcome_updates_observation(self):
        """Linking outcome sets status and r_multiple."""
        obs = self.observer.get_observations_for_strategy("range_reversal_v1")[0]
        obs_id = obs.observation_id

        success = self.observer.link_outcome(obs_id, "WIN", r_multiple=1.5)

        assert success is True
        updated = self.observer.get_observations_for_strategy("range_reversal_v1")[0]
        assert updated.outcome_status == "WIN"
        assert updated.outcome_r_multiple == 1.5
        assert updated.outcome_linked is True
        assert updated.outcome_linked_at > 0

    def test_link_outcome_unknown_id_returns_false(self):
        """Unknown observation ID returns False."""
        success = self.observer.link_outcome("fake-id-123", "WIN", 1.0)
        assert success is False

    def test_pending_observations_decrease_after_link(self):
        """Pending count decreases after linking."""
        pending_before = len(self.observer.get_pending_observations())
        obs = self.observer.get_observations()[0]
        self.observer.link_outcome(obs.observation_id, "LOSS", -1.0)

        pending_after = len(self.observer.get_pending_observations())
        assert pending_after == pending_before - 1

    def test_default_outcome_is_pending(self):
        """New observations default to PENDING outcome."""
        for obs in self.observer.get_observations():
            assert obs.outcome_status == "PENDING"
            assert obs.outcome_linked is False


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistics:
    """Tests for observer statistics."""

    def setup_method(self):
        self.observer = StrategyObserver()

    def test_empty_statistics(self):
        """Empty observer returns zero stats."""
        stats = self.observer.get_statistics()
        assert stats["total_observations"] == 0
        assert stats["total_cycles"] == 0

    def test_statistics_after_observe(self):
        """Stats reflect observations after cycle."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        stats = self.observer.get_statistics()
        assert stats["total_observations"] == 5
        assert stats["total_cycles"] == 1
        assert "range_reversal_v1" in stats["by_strategy"]
        assert stats["outcome_pending"] == 5
        assert stats["outcome_linked"] == 0

    def test_statistics_by_status(self):
        """Stats include status distribution."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        stats = self.observer.get_statistics()
        assert "by_status" in stats
        # At least one strategy should be FULLY_MET
        assert stats["fully_met_total"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# CAPACITY AND EVICTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCapacity:
    """Tests for observation capacity management."""

    def test_max_observations_enforced(self):
        """Observer evicts oldest when max reached."""
        observer = StrategyObserver(max_observations=10)

        # 5 strategies × 3 cycles = 15 observations, but max is 10
        for i in range(3):
            snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
            observer.observe(snapshot=snapshot, cycle_id=i)

        assert observer.observation_count == 10

    def test_oldest_evicted_first(self):
        """Oldest observations are evicted first (FIFO)."""
        observer = StrategyObserver(max_observations=10)

        snapshot1 = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        observer.observe(snapshot=snapshot1, cycle_id=1, timestamp_utc=1000.0)

        snapshot2 = build_market_snapshot(regime="TRENDING", phase="IMPULSE")
        observer.observe(snapshot=snapshot2, cycle_id=2, timestamp_utc=2000.0)

        snapshot3 = build_market_snapshot(regime="RANGING", phase="CONSOLIDATION")
        observer.observe(snapshot=snapshot3, cycle_id=3, timestamp_utc=3000.0)

        # Only last 10 should remain (cycles 2 and 3)
        remaining = observer.get_observations()
        assert all(o.timestamp_utc >= 2000.0 for o in remaining)


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS — NO EXECUTION IMPACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoExecutionImpact:
    """Verify the observer has zero impact on trading execution."""

    def setup_method(self):
        self.observer = StrategyObserver()

    def test_observe_does_not_mutate_strategy_registry(self):
        """Observing must not change strategy registry state."""
        from core.strategies.registry import get_all_strategies
        before = [(s.strategy_id, s.status.value) for s in get_all_strategies()]

        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        after = [(s.strategy_id, s.status.value) for s in get_all_strategies()]
        assert before == after

    def test_observe_does_not_activate_strategies(self):
        """No strategies should become ACTIVE from observation."""
        from core.strategies.registry import get_active_strategies
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        for i in range(10):
            self.observer.observe(snapshot=snapshot, cycle_id=i)

        assert get_active_strategies() == []

    def test_observe_does_not_modify_snapshot(self):
        """The input snapshot must not be mutated."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
        )
        snapshot_copy = dict(snapshot)
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        # Only pattern_detected might be added (empty string)
        for key in snapshot_copy:
            assert snapshot.get(key) == snapshot_copy[key]

    def test_observation_serializes_cleanly(self):
        """Observations must serialize to dict without error."""
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        self.observer.observe(snapshot=snapshot, cycle_id=1)

        for obs in self.observer.get_observations():
            d = obs.to_dict()
            assert isinstance(d, dict)
            assert "observation_id" in d
            assert "strategy_id" in d
            assert "outcome_status" in d

    def test_cycle_result_is_frozen(self):
        """ObservationCycleResult must be immutable."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        result = self.observer.observe(snapshot=snapshot, cycle_id=1)

        with pytest.raises(Exception):
            result.cycle_id = 999  # type: ignore

    def test_clear_resets_state(self):
        """clear() resets all internal state."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        self.observer.observe(snapshot=snapshot, cycle_id=1)
        assert self.observer.observation_count > 0

        self.observer.clear()
        assert self.observer.observation_count == 0
        assert self.observer.total_cycles == 0
