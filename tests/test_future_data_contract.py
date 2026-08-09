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
import tempfile
from pathlib import Path

import pytest

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

    def test_execution_enriches_entity_id_from_results(self, tmp_path):
        """CR-001 FIX: ExecutionUniverseBuilder enriches entity_id from execution_results."""
        # Create a minimal execution universe record
        uni_path = tmp_path / "universe.jsonl"
        uni_path.write_text(json.dumps({
            "trade_id": "pos_54850055",
            "execution": {
                "ticket": 54850055, "symbol": "EURUSD", "direction": "BUY",
                "entry_price": 1.155, "exit_price": 1.157, "entry_time": 1785975900,
                "exit_time": 1785980000, "stop_loss": 1.154, "take_profit": 1.160,
                "gross_profit": 20, "commission": -2, "swap": 0,
                "net_realised_pnl": 18, "r_multiple": 2.0,
                "volume": 0.1, "duration_seconds": 4100, "exit_reason": "TAKE_PROFIT",
            },
            "decision": {"strategy": "", "score": 0, "confidence": 0,
                         "decision_type": "", "decision_timestamp": 0,
                         "components": {}, "weakest_component": "", "ev": None, "p_success": None},
            "market": {"regime": "", "session": "", "volatility": "", "trend_state": "",
                       "higher_timeframe_bias": "", "h4_phase": "", "h1_clarity": 0},
            "strategy": {"family": "", "pattern": "", "conditions_met": 0,
                         "strategy_confidence": 0, "opportunity_quality": 0, "opportunity_type": ""},
            "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "VALID",
                        "data_completeness": "FULL", "missing": [], "join_method": "", "pnl_source": ""},
        }) + "\n")

        # Create matching execution_results with entity_id
        er_dir = tmp_path / "exec_results" / "EURUSD"
        er_dir.mkdir(parents=True)
        (er_dir / "2026-08-06.jsonl").write_text(json.dumps({
            "symbol": "EURUSD", "deal": 54850055, "result_ok": True,
            "entity_id": "EURUSD_1785986700", "correlation_id": "COR-20260806-3005-EURUSD-A0CE",
            "comment": "Request executed",
        }) + "\n")

        builder = ExecutionUniverseBuilder(
            source_path=uni_path,
            execution_results_dir=tmp_path / "exec_results",
        )
        records = builder.build()

        assert len(records) == 1
        # entity_id should be enriched from execution_results
        assert records[0]["entity_id"] == "EURUSD_1785986700"
        # trade_id preserved
        assert records[0]["trade_id"] == "pos_54850055"

    def test_execution_fallback_when_no_results(self, tmp_path):
        """Without execution_results, entity_id falls back to trade_id."""
        uni_path = tmp_path / "universe.jsonl"
        uni_path.write_text(json.dumps({
            "trade_id": "pos_999",
            "execution": {
                "ticket": 999, "symbol": "GBPUSD", "direction": "SELL",
                "entry_price": 1.33, "exit_price": 1.325, "entry_time": 1000,
                "exit_time": 2000, "stop_loss": 1.335, "take_profit": 1.325,
                "gross_profit": 5, "commission": -1, "swap": 0,
                "net_realised_pnl": 4, "r_multiple": 1.0,
                "volume": 0.1, "duration_seconds": 1000, "exit_reason": "TP",
            },
            "decision": {"strategy": "", "score": 0, "confidence": 0,
                         "decision_type": "", "decision_timestamp": 0,
                         "components": {}, "weakest_component": "", "ev": None, "p_success": None},
            "market": {"regime": "", "session": "", "volatility": "", "trend_state": "",
                       "higher_timeframe_bias": "", "h4_phase": "", "h1_clarity": 0},
            "strategy": {"family": "", "pattern": "", "conditions_met": 0,
                         "strategy_confidence": 0, "opportunity_quality": 0, "opportunity_type": ""},
            "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "VALID",
                        "data_completeness": "FULL", "missing": [], "join_method": "", "pnl_source": ""},
        }) + "\n")

        # No execution_results directory
        builder = ExecutionUniverseBuilder(
            source_path=uni_path,
            execution_results_dir=tmp_path / "nonexistent",
        )
        records = builder.build()

        assert len(records) == 1
        # Falls back to trade_id
        assert records[0]["entity_id"] == "pos_999"

    def test_execution_builds_independently(self, tmp_path):
        """Execution Universe builds without Decision/Market/Strategy."""
        p = tmp_path / "uni.jsonl"
        p.write_text(json.dumps({
            "trade_id": "pos_1",
            "execution": {"r_multiple": 1.0, "symbol": "EURUSD", "direction": "BUY",
                          "entry_price": 1.1, "exit_price": 1.11, "entry_time": 1000,
                          "exit_time": 2000, "stop_loss": 1.09, "take_profit": 1.12,
                          "gross_profit": 10, "commission": -1, "swap": 0,
                          "net_realised_pnl": 9, "volume": 0.1,
                          "duration_seconds": 1000, "exit_reason": "TP"},
            "decision": {"strategy": "", "score": 0, "confidence": 0,
                         "decision_type": "", "decision_timestamp": 0,
                         "components": {}, "weakest_component": "", "ev": None, "p_success": None},
            "market": {"regime": "", "session": "", "volatility": "", "trend_state": "",
                       "higher_timeframe_bias": "", "h4_phase": "", "h1_clarity": 0},
            "strategy": {"family": "", "pattern": "", "conditions_met": 0,
                         "strategy_confidence": 0, "opportunity_quality": 0, "opportunity_type": ""},
            "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "VALID",
                        "data_completeness": "FULL", "missing": [], "join_method": "", "pnl_source": ""},
        }) + "\n")
        builder = ExecutionUniverseBuilder(source_path=p)
        records = builder.build()
        assert len(records) == 1

    def test_decision_builds_independently(self, tmp_path):
        """Decision Universe builds without Execution/Market/Strategy."""
        d = tmp_path / "SYM"
        d.mkdir()
        (d / "f.jsonl").write_text(json.dumps({
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "terminal_stage": "unknown", "terminal_reason": "test",
            "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
            "v10_risk": {}, "v10_entry": {},
        }) + "\n")
        builder = DecisionUniverseBuilder(source_dir=tmp_path)
        records = builder.build()
        assert len(records) == 1

    def test_market_builds_independently(self, tmp_path):
        """Market Universe builds without Execution."""
        dt = tmp_path / "dt" / "SYM"
        dt.mkdir(parents=True)
        (dt / "f.jsonl").write_text(json.dumps({
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "v10_market_state": {"regime": {"regime": "TRENDING", "regime_confidence": 0.8,
                                            "volatility_state": "NEUTRAL", "expansion_state": ""},
                                 "h4": {}, "h1": {}, "m15": {}, "m5": {},
                                 "location": {}, "htf_alignment": {}},
        }) + "\n")
        builder = MarketUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            market_context_dir=tmp_path / "mc",
        )
        records = builder.build()
        assert len(records) == 1

    def test_strategy_builds_independently(self, tmp_path):
        """Strategy Universe builds without Execution."""
        so = tmp_path / "so" / "SYM"
        so.mkdir(parents=True)
        (so / "f.jsonl").write_text(json.dumps({
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": 1000.0, "strategy_family": "MEAN_REVERSION",
            "confidence": 0.7, "conditions_passed": 3, "evaluation_status": "SELECTED",
            "decision_action": "EXECUTE", "decision_score": 72.0,
            "detected_pattern": "ENGULFING", "direction": "BUY",
        }) + "\n")
        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            strategy_obs_dir=tmp_path / "so",
        )
        records = builder.build()
        assert len(records) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION CONTINUITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationContinuity:

    def test_new_record_appears_in_population(self, tmp_path):
        """A new record automatically appears in the applicable population."""
        d = tmp_path / "SYM"
        d.mkdir()
        # Initially one NO_TRADE
        (d / "f.jsonl").write_text(json.dumps({
            "entity_id": "SYM_1000", "symbol": "SYM", "cycle_id": 1,
            "timestamp_utc": "2026-08-09T00:00:00Z", "action": "NO_TRADE",
            "terminal_stage": "unknown", "terminal_reason": "V10 [opportunity]: invalid",
            "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
            "v10_risk": {}, "v10_entry": {},
        }) + "\n")

        builder = DecisionUniverseBuilder(source_dir=tmp_path)
        builder.build()
        assert len(builder.get_population(Population.EXECUTE_DECISIONS)) == 0

        # Simulate new EXECUTE record arriving
        with open(d / "f.jsonl", "a") as f:
            f.write(json.dumps({
                "entity_id": "SYM_2000", "symbol": "SYM", "cycle_id": 2,
                "timestamp_utc": "2026-08-09T01:00:00Z", "action": "EXECUTE",
                "terminal_stage": "", "terminal_reason": "",
                "v10_market_state": {}, "v10_opportunity": {}, "v10_strategy": {},
                "v10_risk": {}, "v10_entry": {},
            }) + "\n")

        # Rebuild — new record automatically in population
        builder2 = DecisionUniverseBuilder(source_dir=tmp_path)
        builder2.build()
        assert len(builder2.get_population(Population.EXECUTE_DECISIONS)) == 1

    def test_failed_correlation_preserves_records(self, tmp_path):
        """If correlation fails, both universe records remain intact."""
        # Execution record
        exe_path = tmp_path / "exe.jsonl"
        exe_path.write_text(json.dumps({
            "trade_id": "pos_NOCORR",
            "execution": {"r_multiple": -0.5, "symbol": "GBPUSD", "direction": "SELL",
                          "entry_price": 1.33, "exit_price": 1.335, "entry_time": 9999,
                          "exit_time": 10000, "stop_loss": 1.335, "take_profit": 1.325,
                          "gross_profit": -5, "commission": -1, "swap": 0,
                          "net_realised_pnl": -6, "volume": 0.1,
                          "duration_seconds": 100, "exit_reason": "SL"},
            "decision": {"strategy": "", "score": 0, "confidence": 0,
                         "decision_type": "", "decision_timestamp": 0,
                         "components": {}, "weakest_component": "", "ev": None, "p_success": None},
            "market": {"regime": "", "session": "", "volatility": "", "trend_state": "",
                       "higher_timeframe_bias": "", "h4_phase": "", "h1_clarity": 0},
            "strategy": {"family": "", "pattern": "", "conditions_met": 0,
                         "strategy_confidence": 0, "opportunity_quality": 0, "opportunity_type": ""},
            "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "VALID",
                        "data_completeness": "FULL", "missing": [], "join_method": "", "pnl_source": ""},
        }) + "\n")

        exe_builder = ExecutionUniverseBuilder(source_path=exe_path)
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
