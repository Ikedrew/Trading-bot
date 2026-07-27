"""
Tests for Strategy Intelligence Loop v1.

Covers:
    - Outcome Linker: creates links, prevents duplicates, handles missing IDs
    - Evidence Store: saves records, retrieves, calculates statistics
    - Research Queries: history, statistics, phase performance
    - Integration: observer → evidence → outcome → research
    - Safety: no execution imports, no decision pipeline changes
"""

import pytest

from core.strategies.outcome_linker import (
    OutcomeStatus,
    StrategyOutcomeLink,
    StrategyOutcomeLinker,
)
from core.strategies.evidence_store import (
    StrategyEvidenceRecord,
    StrategyEvidenceStore,
)
from core.strategies.strategy_observer import (
    StrategyObservation,
    StrategyObserver,
)
from core.strategies.condition_evaluator import build_market_snapshot
from core.strategies.research_queries import (
    get_condition_effectiveness,
    get_family_statistics,
    get_phase_strategy_performance,
    get_strategy_evidence_summary,
    get_strategy_history,
    get_strategy_statistics,
)
from core.strategies.evidence_diagnostics import (
    strategy_detail_report,
    strategy_evidence_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_observation(
    *,
    observation_id: str = "obs-001",
    strategy_id: str = "range_reversal_v1",
    family: str = "REVERSAL",
    market_phase: str = "REVERSAL",
    regime: str = "RANGING",
    conditions_met: int = 4,
    confidence: float = 0.8,
    overall_status: str = "FULLY_MET",
    eligible_by_phase: bool = True,
    pattern_detected: str = "HAMMER",
    symbol: str = "EURUSD",
    cycle_id: int = 1,
) -> StrategyObservation:
    return StrategyObservation(
        observation_id=observation_id,
        timestamp_utc=1000000.0,
        cycle_id=cycle_id,
        symbol=symbol,
        strategy_id=strategy_id,
        family=family,
        regime=regime,
        market_phase=market_phase,
        conditions_met=conditions_met,
        conditions_failed=0,
        conditions_missing=0,
        confidence=confidence,
        overall_status=overall_status,
        pattern_detected=pattern_detected,
        eligible_by_phase=eligible_by_phase,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# OUTCOME LINKER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutcomeLinker:
    """Tests for StrategyOutcomeLinker."""

    def setup_method(self):
        self.linker = StrategyOutcomeLinker()

    def test_creates_link(self):
        """link_trade_result creates a StrategyOutcomeLink."""
        link = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.5,
            holding_time=3600,
            exit_reason="take_profit",
            source="shadow_trade",
        )
        assert link is not None
        assert link.observation_id == "obs-001"
        assert link.strategy_id == "range_reversal_v1"
        assert link.outcome_status == OutcomeStatus.WIN
        assert link.realised_r == 1.5
        assert link.is_win is True
        assert self.linker.link_count == 1

    def test_prevents_duplicate_link(self):
        """Same observation_id cannot be linked twice."""
        self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.5,
        )
        duplicate = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.LOSS,
            realised_r=-1.0,
        )
        assert duplicate is None
        assert self.linker.link_count == 1

    def test_empty_observation_id_returns_none(self):
        """Empty observation_id is rejected."""
        link = self.linker.link_trade_result(
            observation_id="",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
        )
        assert link is None

    def test_empty_strategy_id_returns_none(self):
        """Empty strategy_id is rejected."""
        link = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="",
            outcome_status=OutcomeStatus.WIN,
        )
        assert link is None

    def test_invalid_outcome_status_string_returns_none(self):
        """Invalid outcome status string is rejected."""
        link = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status="INVALID_STATUS",
        )
        assert link is None

    def test_valid_outcome_status_string_accepted(self):
        """Valid outcome status string is converted to enum."""
        link = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status="WIN",
        )
        assert link is not None
        assert link.outcome_status == OutcomeStatus.WIN

    def test_link_preserves_lineage(self):
        """Link preserves all context fields."""
        link = self.linker.link_trade_result(
            observation_id="obs-001",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.8,
            family="REVERSAL",
            market_phase="REVERSAL",
            regime="RANGING",
            conditions_met=4,
            confidence=0.85,
            trade_id="trade-123",
            entity_id="EURUSD_1000",
        )
        assert link.family == "REVERSAL"
        assert link.market_phase == "REVERSAL"
        assert link.regime == "RANGING"
        assert link.conditions_met == 4
        assert link.trade_id == "trade-123"

    def test_get_links_for_strategy(self):
        """Filter links by strategy_id."""
        self.linker.link_trade_result(
            observation_id="obs-001", strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN, realised_r=1.0,
        )
        self.linker.link_trade_result(
            observation_id="obs-002", strategy_id="momentum_expansion_v1",
            outcome_status=OutcomeStatus.LOSS, realised_r=-1.0,
        )
        reversal = self.linker.get_links_for_strategy("range_reversal_v1")
        assert len(reversal) == 1
        assert reversal[0].strategy_id == "range_reversal_v1"

    def test_is_linked(self):
        """is_linked returns correct boolean."""
        self.linker.link_trade_result(
            observation_id="obs-001", strategy_id="x",
            outcome_status=OutcomeStatus.WIN,
        )
        assert self.linker.is_linked("obs-001") is True
        assert self.linker.is_linked("obs-999") is False

    def test_link_is_frozen(self):
        """StrategyOutcomeLink must be immutable."""
        link = self.linker.link_trade_result(
            observation_id="obs-001", strategy_id="x",
            outcome_status=OutcomeStatus.WIN,
        )
        with pytest.raises(Exception):
            link.realised_r = 99.0  # type: ignore

    def test_to_dict_serializes(self):
        """to_dict produces valid dict."""
        link = self.linker.link_trade_result(
            observation_id="obs-001", strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN, realised_r=1.5,
        )
        d = link.to_dict()
        assert d["observation_id"] == "obs-001"
        assert d["outcome_status"] == "WIN"
        assert d["realised_r"] == 1.5


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE STORE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceStore:
    """Tests for StrategyEvidenceStore."""

    def setup_method(self):
        self.store = StrategyEvidenceStore()

    def test_save_observation_creates_record(self):
        """Saving an observation creates an evidence record."""
        obs = _make_observation()
        record = self.store.save_observation(obs)

        assert record is not None
        assert isinstance(record, StrategyEvidenceRecord)
        assert record.strategy_id == "range_reversal_v1"
        assert record.outcome_status == "PENDING"
        assert self.store.record_count == 1

    def test_record_captures_context(self):
        """Evidence record captures full observation context."""
        obs = _make_observation(
            family="REVERSAL", market_phase="REVERSAL", regime="RANGING",
            conditions_met=4, confidence=0.85,
        )
        record = self.store.save_observation(obs)

        assert record.family == "REVERSAL"
        assert record.market_phase == "REVERSAL"
        assert record.regime == "RANGING"
        assert record.conditions_met == 4
        assert record.confidence == 0.85

    def test_link_outcome_updates_record(self):
        """Linking outcome updates the evidence record."""
        obs = _make_observation(observation_id="obs-link-test")
        self.store.save_observation(obs)

        linker = StrategyOutcomeLinker()
        link = linker.link_trade_result(
            observation_id="obs-link-test",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.5,
            holding_time=3600,
            exit_reason="take_profit",
            source="shadow_trade",
        )

        updated = self.store.link_outcome("obs-link-test", link)
        assert updated is not None
        assert updated.outcome_status == "WIN"
        assert updated.realised_r == 1.5
        assert updated.has_outcome is True

    def test_link_outcome_unknown_observation(self):
        """Linking to unknown observation returns None."""
        linker = StrategyOutcomeLinker()
        link = linker.link_trade_result(
            observation_id="obs-unknown",
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
        )
        result = self.store.link_outcome("obs-unknown", link)
        assert result is None

    def test_link_outcome_prevents_overwrite(self):
        """Already-linked record is not overwritten."""
        obs = _make_observation(observation_id="obs-dup")
        self.store.save_observation(obs)

        linker = StrategyOutcomeLinker()
        link1 = linker.link_trade_result(
            observation_id="obs-dup", strategy_id="x",
            outcome_status=OutcomeStatus.WIN, realised_r=1.0,
        )
        self.store.link_outcome("obs-dup", link1)

        linker2 = StrategyOutcomeLinker()
        link2 = linker2.link_trade_result(
            observation_id="obs-dup-2", strategy_id="x",
            outcome_status=OutcomeStatus.LOSS, realised_r=-1.0,
        )
        # Try to overwrite with different link
        result = self.store.link_outcome("obs-dup", link2)
        # Should return existing (already linked)
        assert result.outcome_status == "WIN"

    def test_get_records_for_strategy(self):
        """Query by strategy_id works."""
        self.store.save_observation(_make_observation(
            observation_id="a", strategy_id="range_reversal_v1"))
        self.store.save_observation(_make_observation(
            observation_id="b", strategy_id="momentum_expansion_v1"))

        results = self.store.get_records_for_strategy("range_reversal_v1")
        assert len(results) == 1
        assert results[0].strategy_id == "range_reversal_v1"

    def test_get_records_for_family(self):
        """Query by family works."""
        self.store.save_observation(_make_observation(
            observation_id="a", family="REVERSAL"))
        self.store.save_observation(_make_observation(
            observation_id="b", family="MOMENTUM"))

        results = self.store.get_records_for_family("REVERSAL")
        assert len(results) == 1

    def test_get_records_for_phase(self):
        """Query by phase works."""
        self.store.save_observation(_make_observation(
            observation_id="a", market_phase="REVERSAL"))
        self.store.save_observation(_make_observation(
            observation_id="b", market_phase="IMPULSE"))

        results = self.store.get_records_for_phase("REVERSAL")
        assert len(results) == 1

    def test_capacity_eviction(self):
        """Records evicted when max reached."""
        store = StrategyEvidenceStore(max_records=5)
        for i in range(10):
            store.save_observation(_make_observation(observation_id=f"obs-{i}"))
        assert store.record_count == 5

    def test_statistics_empty(self):
        """Empty store returns zero statistics."""
        stats = self.store.get_strategy_statistics("range_reversal_v1")
        assert stats["sample_size"] == 0
        assert stats["confidence"] == "INSUFFICIENT"

    def test_record_is_frozen(self):
        """StrategyEvidenceRecord must be immutable."""
        obs = _make_observation()
        record = self.store.save_observation(obs)
        with pytest.raises(Exception):
            record.outcome_status = "WIN"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistics:
    """Tests for evidence statistics calculation."""

    def setup_method(self):
        self.store = StrategyEvidenceStore()
        self.linker = StrategyOutcomeLinker()
        # Create 10 observations and link outcomes
        for i in range(10):
            obs = _make_observation(
                observation_id=f"obs-{i}",
                strategy_id="range_reversal_v1",
                family="REVERSAL",
                market_phase="REVERSAL",
            )
            self.store.save_observation(obs)
            outcome = OutcomeStatus.WIN if i < 6 else OutcomeStatus.LOSS
            r = 1.5 if i < 6 else -1.0
            link = self.linker.link_trade_result(
                observation_id=f"obs-{i}",
                strategy_id="range_reversal_v1",
                outcome_status=outcome,
                realised_r=r,
            )
            self.store.link_outcome(f"obs-{i}", link)

    def test_strategy_statistics(self):
        """get_strategy_statistics computes correctly."""
        stats = self.store.get_strategy_statistics("range_reversal_v1")
        assert stats["sample_size"] == 10
        assert stats["wins"] == 6
        assert stats["losses"] == 4
        assert stats["win_rate"] == 0.6
        # avg_r = (6*1.5 + 4*(-1.0)) / 10 = (9 - 4) / 10 = 0.5
        assert abs(stats["average_r"] - 0.5) < 0.001
        assert stats["confidence"] == "INSUFFICIENT"  # n=10 < 20

    def test_family_statistics(self):
        """get_family_statistics aggregates correctly."""
        stats = self.store.get_family_statistics("REVERSAL")
        assert stats["sample_size"] == 10
        assert stats["win_rate"] == 0.6

    def test_phase_strategy_performance(self):
        """get_phase_strategy_performance groups correctly."""
        perf = self.store.get_phase_strategy_performance()
        assert "REVERSAL" in perf
        assert "range_reversal_v1" in perf["REVERSAL"]
        assert perf["REVERSAL"]["range_reversal_v1"]["sample_size"] == 10


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH QUERIES TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestResearchQueries:
    """Tests for research query functions."""

    def setup_method(self):
        self.store = StrategyEvidenceStore()
        self.linker = StrategyOutcomeLinker()
        for i in range(5):
            obs = _make_observation(
                observation_id=f"rq-{i}",
                strategy_id="range_reversal_v1",
                confidence=0.9 if i < 3 else 0.4,
            )
            self.store.save_observation(obs)
            outcome = OutcomeStatus.WIN if i < 3 else OutcomeStatus.LOSS
            r = 1.2 if i < 3 else -1.0
            link = self.linker.link_trade_result(
                observation_id=f"rq-{i}",
                strategy_id="range_reversal_v1",
                outcome_status=outcome,
                realised_r=r,
            )
            self.store.link_outcome(f"rq-{i}", link)

    def test_get_strategy_history(self):
        """History returns timeline with observations."""
        history = get_strategy_history(self.store, "range_reversal_v1")
        assert history["total_observations"] == 5
        assert history["resolved"] == 5
        assert len(history["timeline"]) == 5

    def test_get_strategy_statistics(self):
        """Statistics delegation works."""
        stats = get_strategy_statistics(self.store, "range_reversal_v1")
        assert stats["sample_size"] == 5
        assert stats["wins"] == 3

    def test_get_family_statistics(self):
        """Family statistics delegation works."""
        stats = get_family_statistics(self.store, "REVERSAL")
        assert stats["sample_size"] == 5

    def test_get_phase_strategy_performance(self):
        """Phase performance delegation works."""
        perf = get_phase_strategy_performance(self.store)
        assert "REVERSAL" in perf

    def test_get_strategy_evidence_summary(self):
        """Summary includes all expected keys."""
        summary = get_strategy_evidence_summary(self.store)
        assert summary["total_observations"] == 5
        assert summary["total_resolved"] == 5
        assert "range_reversal_v1" in summary["per_strategy"]
        assert summary["per_strategy"]["range_reversal_v1"]["wins"] == 3

    def test_get_condition_effectiveness_insufficient(self):
        """Condition effectiveness with small sample."""
        result = get_condition_effectiveness(self.store, "range_reversal_v1")
        assert result["strategy_id"] == "range_reversal_v1"
        assert result["sample_size"] == 5
        assert result["conclusion"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Tests for the full observation → evidence → outcome loop."""

    def test_observer_to_evidence_store(self):
        """StrategyObserver output can feed into evidence store."""
        observer = StrategyObserver()
        store = StrategyEvidenceStore()

        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        observer.observe(snapshot=snapshot, cycle_id=1, pattern_detected="HAMMER")

        # Feed observations into evidence store
        for obs in observer.get_observations():
            store.save_observation(obs)

        assert store.record_count == 5  # 5 strategies observed

    def test_full_loop_observation_to_outcome(self):
        """Complete loop: observe → store → link outcome → query."""
        observer = StrategyObserver()
        store = StrategyEvidenceStore()
        linker = StrategyOutcomeLinker()

        # 1. Observe
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True,
        )
        observer.observe(
            snapshot=snapshot, cycle_id=1,
            pattern_detected="HAMMER", symbol="EURUSD",
        )

        # 2. Store observations
        for obs in observer.get_observations():
            store.save_observation(obs)

        # 3. Link outcome for one observation
        obs_list = observer.get_observations_for_strategy("range_reversal_v1")
        assert len(obs_list) == 1
        obs_id = obs_list[0].observation_id

        link = linker.link_trade_result(
            observation_id=obs_id,
            strategy_id="range_reversal_v1",
            outcome_status=OutcomeStatus.WIN,
            realised_r=1.5,
            source="shadow_trade",
        )
        store.link_outcome(obs_id, link)

        # 4. Query evidence
        stats = store.get_strategy_statistics("range_reversal_v1")
        assert stats["sample_size"] == 1
        assert stats["wins"] == 1
        assert stats["win_rate"] == 1.0

    def test_multiple_cycles_accumulate(self):
        """Multiple observation cycles accumulate evidence."""
        observer = StrategyObserver()
        store = StrategyEvidenceStore()

        for i in range(3):
            snapshot = build_market_snapshot(
                regime="RANGING", phase="REVERSAL",
            )
            observer.observe(snapshot=snapshot, cycle_id=i)
            for obs in observer.get_observations_for_strategy("range_reversal_v1"):
                if not store.get_record_by_observation(obs.observation_id):
                    store.save_observation(obs)

        # 3 cycles × 1 observation per strategy for range_reversal
        records = store.get_records_for_strategy("range_reversal_v1")
        assert len(records) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiagnostics:
    """Tests for evidence diagnostic reports."""

    def setup_method(self):
        self.store = StrategyEvidenceStore()
        linker = StrategyOutcomeLinker()
        for i in range(5):
            obs = _make_observation(
                observation_id=f"diag-{i}",
                strategy_id="range_reversal_v1",
            )
            self.store.save_observation(obs)
            if i < 3:
                link = linker.link_trade_result(
                    observation_id=f"diag-{i}",
                    strategy_id="range_reversal_v1",
                    outcome_status=OutcomeStatus.WIN,
                    realised_r=1.0,
                )
                self.store.link_outcome(f"diag-{i}", link)

    def test_evidence_report_contains_totals(self):
        """Report shows total observations and linked count."""
        report = strategy_evidence_report(self.store)
        assert "Total Observations" in report
        assert "Outcomes Linked" in report

    def test_evidence_report_contains_strategy(self):
        """Report shows strategy ID."""
        report = strategy_evidence_report(self.store)
        assert "range_reversal_v1" in report

    def test_detail_report_shows_stats(self):
        """Detail report shows statistics."""
        report = strategy_detail_report(self.store, "range_reversal_v1")
        assert "range_reversal_v1" in report
        assert "Sample Size" in report
        assert "Win Rate" in report

    def test_empty_store_report(self):
        """Empty store produces valid report."""
        empty = StrategyEvidenceStore()
        report = strategy_evidence_report(empty)
        assert "Total Observations:   0" in report


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """Verify no execution or decision pipeline imports."""

    def test_no_execution_imports_outcome_linker(self):
        """outcome_linker.py must not import execution code."""
        import inspect
        import core.strategies.outcome_linker as m
        source = inspect.getsource(m)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"outcome_linker.py: forbidden import '{f}'"

    def test_no_execution_imports_evidence_store(self):
        """evidence_store.py must not import execution code."""
        import inspect
        import core.strategies.evidence_store as m
        source = inspect.getsource(m)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"evidence_store.py: forbidden import '{f}'"

    def test_no_execution_imports_research_queries(self):
        """research_queries.py must not import execution code."""
        import inspect
        import core.strategies.research_queries as m
        source = inspect.getsource(m)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"research_queries.py: forbidden import '{f}'"

    def test_no_strategy_activation(self):
        """Intelligence loop must not activate any strategy."""
        from core.strategies.registry import get_active_strategies
        # Run full loop
        observer = StrategyObserver()
        store = StrategyEvidenceStore()
        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        observer.observe(snapshot=snapshot, cycle_id=1)
        for obs in observer.get_observations():
            store.save_observation(obs)

        # No strategies should be active
        assert get_active_strategies() == []
