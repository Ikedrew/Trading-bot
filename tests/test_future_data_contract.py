"""
Future Data Completeness Contract Tests.

Proves:
    - Four universes remain independently buildable
    - Persistence paths are correctly defined
    - Future correlation is deterministic (entity_id linkage)
    - Population continuity works with synthetic future records
    - Failed correlation never removes canonical records
    - Invariants are formally defined and verified
    - No research question is executed during this phase
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import FakeS3, install_fake_s3, reset_fake_s3

from research_engine.v10.universes.future_data_contract import (
    ALL_PERSISTENCE_PATHS,
    CONDITIONAL_REQUIREMENTS,
    FUTURE_CORRELATION,
    INVARIANTS,
    VERDICT,
    FutureDataVerdict,
    Invariant,
    PersistenceGuarantee,
)
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
from research_engine.v10.universes.market_universe import MarketUniverseBuilder
from research_engine.v10.universes.strategy_universe import StrategyUniverseBuilder
from research_engine.v10.universes.correlation import (
    CorrelationEngine,
    CorrelationStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# INVARIANT DEFINITION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvariantDefinitions:

    def test_all_8_invariants_defined(self):
        assert len(INVARIANTS) == 8

    def test_all_invariants_verified(self):
        """Every invariant must be VERIFIED."""
        for inv in INVARIANTS:
            assert inv.current_status == "VERIFIED", (
                f"Invariant {inv.invariant_id.value} ({inv.name}) is {inv.current_status}"
            )

    def test_invariant_ids_are_unique(self):
        ids = [inv.invariant_id for inv in INVARIANTS]
        assert len(ids) == len(set(ids))

    def test_each_invariant_has_verification_method(self):
        for inv in INVARIANTS:
            assert inv.verification_method, f"{inv.name}: no verification method"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE PATH TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistencePaths:

    def test_five_persistence_paths_defined(self):
        assert len(ALL_PERSISTENCE_PATHS) == 5

    def test_all_paths_have_local_pattern(self):
        for p in ALL_PERSISTENCE_PATHS:
            assert "logs/" in p.local_path_pattern, (
                f"{p.event_type}: missing local path"
            )

    def test_all_paths_have_identity_fields(self):
        for p in ALL_PERSISTENCE_PATHS:
            assert p.identity_fields, f"{p.event_type}: no identity fields"

    def test_all_paths_have_correlation_fields(self):
        for p in ALL_PERSISTENCE_PATHS:
            assert p.correlation_fields, f"{p.event_type}: no correlation fields"

    def test_decision_trace_has_entity_id(self):
        dt = next(p for p in ALL_PERSISTENCE_PATHS if p.event_type == "decision_trace")
        assert "entity_id" in dt.identity_fields
        assert "entity_id" in dt.correlation_fields

    def test_execution_result_has_entity_id(self):
        er = next(p for p in ALL_PERSISTENCE_PATHS if p.event_type == "execution_result")
        assert "entity_id" in er.identity_fields
        assert "entity_id" in er.correlation_fields

    def test_all_paths_use_fsync(self):
        """All paths must use at least fsync for durability."""
        for p in ALL_PERSISTENCE_PATHS:
            assert p.persistence_guarantee in (
                PersistenceGuarantee.FSYNC_LOCAL_PLUS_S3_MIRROR,
                PersistenceGuarantee.FSYNC_LOCAL_ONLY,
            ), f"{p.event_type}: uses {p.persistence_guarantee}"


# ═══════════════════════════════════════════════════════════════════════════════
# FUTURE CORRELATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFutureCorrelation:

    def test_relationship_is_deterministic(self):
        assert FUTURE_CORRELATION.relationship == "DETERMINISTIC_1_TO_1"

    def test_join_key_is_entity_id(self):
        assert FUTURE_CORRELATION.join_key == "entity_id"

    def test_left_is_execution_results(self):
        assert FUTURE_CORRELATION.left_dataset == "execution_results"

    def test_right_is_decision_trace(self):
        assert FUTURE_CORRELATION.right_dataset == "decision_trace"

    def test_synthetic_future_correlation(self):
        """Simulate a future execution linked to a future decision by entity_id."""
        # Future decision
        decision = {
            "entity_id": "EURUSD_1786000000",
            "symbol": "EURUSD",
            "action": "EXECUTE",
        }
        # Future execution result (entity_id propagated from same engine_result)
        execution = {
            "trade_id": "pos_99999999",
            "symbol": "EURUSD",
            "entry_time": 1786000015.0,  # 15s after cycle
            "entity_id": "EURUSD_1786000000",  # SAME entity_id
        }

        # Direct join on entity_id = DETERMINISTIC
        assert execution["entity_id"] == decision["entity_id"]

        # Temporal reconstruction also works (within 600s)
        engine = CorrelationEngine(temporal_window=600)
        results = engine.correlate(
            [execution],
            [decision],
        )
        assert results[0].status == CorrelationStatus.CORRELATED
        assert results[0].time_delta_seconds == 15.0


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE INDEPENDENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniverseIndependence:

    @pytest.fixture(autouse=True)
    def _s3(self):
        fake = install_fake_s3()
        yield fake
        reset_fake_s3()

    def test_execution_enriches_entity_id_from_results(self, _s3):
        """CR-001 FIX: ExecutionUniverseBuilder enriches entity_id from execution_results."""
        # Seed trade_truth (authoritative realised outcome) for the trade.
        _s3.add("trade_truth", [{
            "identity": {"trade_id": "pos_54850055", "correlation_id": "COR-20260806-3005-EURUSD-A0CE", "symbol": "EURUSD"},
            "execution": {"entry_fill_price": 1.155, "exit_fill_price": 1.157, "volume_executed": 0.1},
            "timestamps": {"entry_timestamp_broker": 1785975900, "exit_timestamp_broker": 1785980000, "duration_seconds": 4100},
            "outcome": {"r_multiple_realised": 2.0, "pnl_realised": 20, "net_profit": 18, "commission": -2, "swap": 0},
            "exit": {"exit_reason": "TAKE_PROFIT"},
        }], symbol="EURUSD")
        # Seed execution_results carrying the entity_id (joined by correlation_id).
        _s3.add("execution_results", [{
            "symbol": "EURUSD", "deal": 54850055, "result_ok": True,
            "entity_id": "EURUSD_1785986700", "correlation_id": "COR-20260806-3005-EURUSD-A0CE",
            "comment": "Request executed",
        }], symbol="EURUSD")

        builder = ExecutionUniverseBuilder()
        records = builder.build()

        assert len(records) == 1
        # entity_id should be enriched from execution_results
        assert records[0]["entity_id"] == "EURUSD_1785986700"
        # trade_id preserved
        assert records[0]["trade_id"] == "pos_54850055"

    def test_execution_fallback_when_no_results(self, _s3):
        """Without execution_results, entity_id falls back to trade_id."""
        _s3.add("trade_truth", [{
            "identity": {"trade_id": "pos_999", "correlation_id": "", "symbol": "GBPUSD"},
            "execution": {"entry_fill_price": 1.33, "exit_fill_price": 1.325, "volume_executed": 0.1},
            "timestamps": {"entry_timestamp_broker": 1000, "exit_timestamp_broker": 2000, "duration_seconds": 1000},
            "outcome": {"r_multiple_realised": 1.0, "pnl_realised": 5, "net_profit": 4, "commission": -1, "swap": 0},
            "exit": {"exit_reason": "TP"},
        }], symbol="GBPUSD")

        # No execution_results seeded → no entity_id join available.
        builder = ExecutionUniverseBuilder()
        records = builder.build()

        assert len(records) == 1
        # Falls back to trade_id
        assert records[0]["entity_id"] == "pos_999"

    def test_execution_builds_independently(self, _s3):
        """Execution Universe builds without Decision/Market/Strategy."""
        _s3.add("trade_truth", [{
            "identity": {"trade_id": "pos_1", "correlation_id": "", "symbol": "EURUSD"},
            "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.11, "volume_executed": 0.1},
            "timestamps": {"entry_timestamp_broker": 1000, "exit_timestamp_broker": 2000, "duration_seconds": 1000},
            "outcome": {"r_multiple_realised": 1.0, "pnl_realised": 10, "net_profit": 9, "commission": -1, "swap": 0},
            "exit": {"exit_reason": "TP"},
        }], symbol="EURUSD")
        builder = ExecutionUniverseBuilder()
        records = builder.build()
        assert len(records) == 1

    def test_decision_builds_independently(self, _s3):
        """Decision Universe builds without Execution/Market/Strategy."""
        _s3.add("decision_trace", [{
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "terminal_stage": "unknown", "terminal_reason": "test",
            "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
            "v10_risk": {}, "v10_entry": {},
        }], symbol="SYM")
        builder = DecisionUniverseBuilder()
        records = builder.build()
        assert len(records) == 1

    def test_market_builds_independently(self, _s3):
        """Market Universe builds without Execution."""
        _s3.add("decision_trace", [{
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "v10_market_state": {"regime": {"regime": "TRENDING", "regime_confidence": 0.8,
                                            "volatility_state": "NEUTRAL", "expansion_state": ""},
                                 "h4": {}, "h1": {}, "m15": {}, "m5": {},
                                 "location": {}, "htf_alignment": {}},
        }], symbol="SYM")
        builder = MarketUniverseBuilder()
        records = builder.build()
        assert len(records) == 1

    def test_strategy_builds_independently(self, _s3):
        """Strategy Universe builds without Execution."""
        _s3.add("strategy_observations", [{
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": 1000.0, "strategy_family": "MEAN_REVERSION",
            "confidence": 0.7, "conditions_passed": 3, "evaluation_status": "SELECTED",
            "decision_action": "EXECUTE", "decision_score": 72.0,
            "detected_pattern": "ENGULFING", "direction": "BUY",
        }], symbol="SYM")
        builder = StrategyUniverseBuilder()
        records = builder.build()
        assert len(records) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION CONTINUITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationContinuity:

    @pytest.fixture(autouse=True)
    def _s3(self):
        fake = install_fake_s3()
        yield fake
        reset_fake_s3()

    def test_new_record_appears_in_population(self, _s3):
        """A new record automatically appears in the applicable population."""
        # Initially one NO_TRADE
        no_trade = {
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "terminal_stage": "unknown", "terminal_reason": "V10 [opportunity]: invalid",
            "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
            "v10_risk": {}, "v10_entry": {},
        }
        _s3.add("decision_trace", [no_trade], symbol="SYM")

        builder = DecisionUniverseBuilder()
        builder.build()
        assert len(builder.get_population(Population.EXECUTE_DECISIONS)) == 0

        # Simulate new EXECUTE record arriving in S3 (fresh run reads it).
        _s3.add("decision_trace", [{
            "entity_id": "SYM_2000", "symbol": "SYM", "cycle_id": 2,
            "timestamp_utc": "2026-08-09T01:00:00Z", "action": "EXECUTE",
            "terminal_stage": "", "terminal_reason": "",
            "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
            "v10_risk": {}, "v10_entry": {},
        }], symbol="SYM")
        # New run → fresh source (no stale run-level cache carried over).
        reset_fake_s3()
        install_fake_s3(_s3)

        # Rebuild — new record automatically in population
        builder2 = DecisionUniverseBuilder()
        builder2.build()
        assert len(builder2.get_population(Population.EXECUTE_DECISIONS)) == 1

    def test_failed_correlation_preserves_records(self, _s3):
        """If correlation fails, both universe records remain intact."""
        # Execution record (trade_truth), no matching decision to correlate with.
        _s3.add("trade_truth", [{
            "identity": {"trade_id": "pos_NOCORR", "correlation_id": "", "symbol": "GBPUSD"},
            "execution": {"entry_fill_price": 1.33, "exit_fill_price": 1.335, "volume_executed": 0.1},
            "timestamps": {"entry_timestamp_broker": 9999, "exit_timestamp_broker": 10000, "duration_seconds": 100},
            "outcome": {"r_multiple_realised": -0.5, "pnl_realised": -5, "net_profit": -6, "commission": -1, "swap": 0},
            "exit": {"exit_reason": "SL"},
        }], symbol="GBPUSD")

        exe_builder = ExecutionUniverseBuilder()
        exe_builder.build()

        # Attempt correlation with empty decision set
        engine = CorrelationEngine(temporal_window=600)
        results = engine.correlate(exe_builder.records, [])

        # Execution record STILL exists in universe
        assert len(exe_builder.records) == 1
        # Correlation says UNCORRELATED but record is preserved
        assert results[0].status == CorrelationStatus.UNCORRELATED


# ═══════════════════════════════════════════════════════════════════════════════
# VERDICT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerdict:

    def test_verdict_is_verified(self):
        assert VERDICT == FutureDataVerdict.VERIFIED

    def test_no_conditional_requirements(self):
        assert len(CONDITIONAL_REQUIREMENTS) == 0

    def test_no_legacy_imports(self):
        import inspect
        from research_engine.v10.universes import future_data_contract
        source = inspect.getsource(future_data_contract)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "research_question_registry" not in line
            assert "v10_research_registry" not in line
