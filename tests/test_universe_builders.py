"""
Tests for the six universe builders (S3-backed).

Post-migration these builders read their datasets from S3 via the shared
S3ResearchDataSource (see UniverseBuilder._load_dataset). Tests drive them
through the in-memory fake S3 source (tests/_s3_fake.py) — the sanctioned test
mechanism. No test touches the network or local logs/ for source data.

Validates:
    - Each builder loads S3 data, builds normalised records, serves populations
    - Normalised records contain the semantic fields questions require
    - Population filters return correct subsets
    - Metadata is generated correctly
    - Trade_truth → execution universe contract (entity_id enrichment, exclusions)
"""

import pytest

from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
from research_engine.v10.universes.market_universe import MarketUniverseBuilder
from research_engine.v10.universes.strategy_universe import StrategyUniverseBuilder

from tests._s3_fake import FakeS3, install_fake_s3, reset_fake_s3


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def fake_s3():
    """Every test runs against an in-memory S3 source (never the network)."""
    fake = install_fake_s3()
    yield fake
    reset_fake_s3()


def _sample_trade_truth(trade_id="pos_1", r_mult=1.5, correlation_id="COR-1",
                        symbol="EURUSD"):
    """Canonical trade_truth_v1 nested record (identity/execution/timestamps/
    outcome/exit) — the shape the Execution universe normalises."""
    return {
        "identity": {
            "trade_id": trade_id,
            "correlation_id": correlation_id,
            "canonical_opportunity_id": "EURUSD*1784800000*ENGULFING",
            "symbol": symbol,
        },
        "execution": {
            "position_ticket": 12345,
            "order_type": "BUY",
            "entry_fill_price": 1.1,
            "exit_fill_price": 1.102,
            "volume_executed": 0.1,
        },
        "timestamps": {
            "entry_timestamp_broker": 1000.0,
            "exit_timestamp_broker": 2000.0,
            "duration_seconds": 1000.0,
        },
        "outcome": {
            "r_multiple_realised": r_mult,
            "pnl_realised": 20.0,
            "commission": -2.0,
            "swap": 0.0,
            "net_profit": 18.0,
        },
        "exit": {
            "exit_reason": "TAKE_PROFIT",
        },
    }


def _sample_execution_result(correlation_id="COR-1", entity_id="EURUSD_1000",
                             result_ok=True):
    return {
        "correlation_id": correlation_id,
        "entity_id": entity_id,
        "result_ok": result_ok,
        "comment": "",
    }


def _sample_decision_trace(entity_id="EURUSD_1000", action="EXECUTE",
                            terminal_reason="", regime="TRENDING",
                            opp_quality=0.8, family="TREND_CONTINUATION"):
    return {
        "entity_id": entity_id,
        "symbol": "EURUSD",
        "cycle_id": 100,
        "timestamp_utc": "2026-08-07T10:00:00Z",
        "runtime_session_id": "sess1",
        "action": action,
        "terminal_stage": "unknown" if action == "NO_TRADE" else "",
        "terminal_reason": terminal_reason,
        "stages_reached": ["opportunity", "strategy"],
        "stages_passed": ["opportunity"],
        "pattern_detected": True,
        "pattern_name": "ENGULFING",
        "pattern_quality": 0.7,
        "pattern_count": 1,
        "regime": None,
        "regime_confidence": 0.8,
        "score_neutral": 65.0,
        "score_strategy": 72.0,
        "score_delta": 7.0,
        "components": {"location": 80, "structure": 70},
        "weakest_component": "structure",
        "weakest_value": 70.0,
        "threshold_gap": 2.0,
        "ev": 0.25,
        "ev_positive": True,
        "p_success": 0.6,
        "rr_effective": 2.0,
        "correlation_id": "COR-1",
        "decision_id": "DEC-1",
        "observation_id": "OBS-1",
        "v10_market_state": {
            "h4": {"trend": "BULLISH", "trend_strength": 0.7,
                   "market_phase": "IMPULSE", "structure_type": "",
                   "swing_high": 1.12, "swing_low": 1.08,
                   "last_bos_direction": "UP", "atr": 0.001,
                   "volatility_state": "NEUTRAL"},
            "h1": {"dominant_trend": "BULLISH", "structural_clarity": 0.75,
                   "bos_confirmed": True, "bos_direction": "UP",
                   "choch_detected": False, "choch_direction": "",
                   "swing_high": 1.11, "swing_low": 1.09},
            "m15": {"pullback_active": False, "displacement_present": True,
                    "displacement_direction": "UP", "range_position": 0.7,
                    "internal_bos": True, "internal_bos_direction": "UP"},
            "m5": {"momentum_direction": "BULLISH", "momentum_strength": 0.6,
                   "rejection_present": False, "confirmation_candle": True,
                   "atr": 0.0003, "spread_atr_ratio": 0.1},
            "regime": {"regime": regime, "regime_confidence": 0.8,
                       "volatility_state": "NEUTRAL", "expansion_state": "NEUTRAL"},
            "location": {"location_type": "DEMAND_ZONE",
                         "inside_institutional_zone": True,
                         "zone_quality": 0.8, "range_position": 0.3,
                         "premium_discount": "DISCOUNT"},
            "htf_alignment": {"macro_bias": "BULLISH",
                              "macro_bias_strength": 0.7,
                              "structure_alignment": 0.8},
        },
        "v10_opportunity": {
            "state": "VALID", "directional_bias": "BULLISH",
            "opportunity_type": "TREND", "overall_quality": opp_quality,
            "location_score": 0.8, "structure_score": 0.7,
            "behaviour_score": 0.6, "formation_score": 0.9,
            "reasoning": [],
        },
        "v10_strategy": {
            "family": family, "confidence": 0.75,
            "direction": "BULLISH", "reasoning": [],
        },
        "v10_risk": {
            "approved": True, "rejection_reason": "",
            "risk_percentage": 1.0, "position_size": 0.1,
            "max_loss_amount": 10.0,
        },
        "v10_entry": {
            "method": "MARKET", "status": "READY", "direction": "BUY",
            "entry_price": 1.1, "stop_price": 1.099, "target_price": 1.103,
            "risk_distance": 0.001, "reward_distance": 0.003,
            "expected_rr": 3.0,
        },
        "v10_execution": {"approved": True, "rejection_reason": "",
                          "order_type": "MARKET", "volume": 0.1},
        "v10_account_snapshot": {"balance": 10000, "equity": 10000,
                                 "margin_free": 9000, "leverage": 100,
                                 "open_positions": 0, "daily_loss_pct": 0.0},
        "v10_broker_snapshot": {"symbol": "EURUSD", "spread": 1.2,
                                "tick_value": 1.0, "volume_min": 0.01,
                                "volume_step": 0.01, "stops_level": 0,
                                "bid": 1.1, "ask": 1.10012, "market_open": True},
        "schema_version": "2.0",
    }


def _sample_strategy_obs(entity_id="EURUSD_1000", family="TREND_CONTINUATION",
                          status="SELECTED", action="EXECUTE"):
    return {
        "schema_version": "1.0",
        "observation_id": "OBS-1",
        "timestamp_utc": 1000.0,
        "symbol": "EURUSD",
        "cycle_id": 100,
        "market_phase": "IMPULSE",
        "h4_regime": "TRENDING",
        "h1_bias": "BULLISH",
        "direction": "BUY",
        "detected_pattern": "ENGULFING",
        "pattern_in_triggers": True,
        "strategy_family": family,
        "candidate_strategies": [],
        "strategy_conditions": {
            "phase_eligible_count": 3, "fully_met_count": 2,
            "partially_met_count": 1, "not_met_count": 0,
            "phase_eligible": [], "fully_met": [],
        },
        "conditions_passed": 4,
        "conditions_failed": 1,
        "conditions_missing": 0,
        "missing_data": [],
        "evaluation_status": status,
        "confidence": 0.75,
        "tradability_score": 0.8,
        "eligible_by_phase": True,
        "decision_action": action,
        "decision_score": 72.0,
        "decision_side": "BUY",
        "decision_reason": "conditions met",
        "entity_id": entity_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION UNIVERSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecutionUniverse:

    def test_build_from_synthetic_data(self, fake_s3):
        fake_s3.add("trade_truth", [
            _sample_trade_truth("t1", r_mult=1.5),
            _sample_trade_truth("t2", r_mult=-1.0, correlation_id="COR-2"),
            _sample_trade_truth("t3", r_mult=0.5, correlation_id="COR-3"),
        ], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        result = builder.build()

        assert len(result) == 3
        assert builder.is_built
        assert builder.metadata.record_count == 3

    def test_populations(self, fake_s3):
        fake_s3.add("trade_truth", [
            _sample_trade_truth("t1", r_mult=2.0),
            _sample_trade_truth("t2", r_mult=-1.0, correlation_id="COR-2"),
            _sample_trade_truth("t3", r_mult=0.5, correlation_id="COR-3"),
        ], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.get_population(Population.ALL_TRADES)) == 3
        assert len(builder.get_population(Population.WINNING_TRADES)) == 2
        assert len(builder.get_population(Population.LOSING_TRADES)) == 1

    def test_normalised_fields_present(self, fake_s3):
        fake_s3.add("trade_truth", [_sample_trade_truth()], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]

        # Core realised-execution fields (from trade_truth outcome/execution)
        assert "r_multiple" in r
        assert "net_realised_pnl" in r
        assert "entry_price" in r
        assert "exit_reason" in r
        assert "duration_seconds" in r
        assert "volume" in r
        # Decision/market/strategy context joined downstream by entity_id
        assert "score" in r
        assert "ev" in r
        assert "regime" in r
        assert "session" in r
        assert "family" in r
        assert "pattern" in r
        # Join key
        assert "entity_id" in r

    def test_entity_id_enriched_from_execution_results(self, fake_s3):
        """correlation_id joins execution_results → entity_id (cross-universe spine)."""
        fake_s3.add("trade_truth", [_sample_trade_truth("t1", correlation_id="COR-1")],
                    symbol="EURUSD")
        fake_s3.add("execution_results",
                    [_sample_execution_result("COR-1", entity_id="EU_ENRICHED")],
                    symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]
        assert r["entity_id"] == "EU_ENRICHED"

    def test_entity_id_falls_back_to_trade_id(self, fake_s3):
        """Without an execution_results match, entity_id falls back to trade_id."""
        fake_s3.add("trade_truth", [_sample_trade_truth("t1", correlation_id="COR-X")],
                    symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]
        assert r["entity_id"] == "t1"

    def test_exclusion_tracking(self, fake_s3):
        fake_s3.add("trade_truth", [
            _sample_trade_truth("t1", r_mult=1.0),
            {"identity": {"trade_id": "", "correlation_id": "COR-2", "symbol": "EURUSD"},
             "execution": {}, "timestamps": {}, "outcome": {"r_multiple_realised": 1.0},
             "exit": {}},
            _sample_trade_truth("t3", r_mult=None, correlation_id="COR-3"),
        ], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.records) == 1
        reasons = builder.metadata.exclusions["reasons"]
        assert reasons["missing_trade_id"] == 1
        assert reasons["missing_r_multiple"] == 1

    def test_not_built_raises(self):
        builder = ExecutionUniverseBuilder()
        with pytest.raises(RuntimeError):
            _ = builder.records

    def test_universe_type(self):
        builder = ExecutionUniverseBuilder()
        assert builder.universe_type == Universe.EXECUTION


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION UNIVERSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionUniverse:

    def test_build_from_synthetic_data(self, fake_s3):
        fake_s3.add("decision_trace", [
            _sample_decision_trace("EU_1", action="EXECUTE"),
            _sample_decision_trace("EU_2", action="NO_TRADE",
                                   terminal_reason="V10 [opportunity]: No opportunity"),
            _sample_decision_trace("EU_3", action="NO_TRADE",
                                   terminal_reason="V10 [risk]: Risk: R:R too low"),
        ], symbol="EURUSD")

        builder = DecisionUniverseBuilder(symbol="EURUSD")
        result = builder.build()

        assert len(result) == 3
        assert builder.is_built

    def test_populations(self, fake_s3):
        fake_s3.add("decision_trace", [
            _sample_decision_trace("EU_1", action="EXECUTE"),
            _sample_decision_trace("EU_2", action="NO_TRADE",
                                   terminal_reason="V10 [opportunity]: invalid"),
            _sample_decision_trace("EU_3", action="NO_TRADE",
                                   terminal_reason="V10 [strategy]: No strategy"),
            _sample_decision_trace("EU_4", action="NO_TRADE",
                                   terminal_reason="V10 [entry]: Entry not ready"),
            _sample_decision_trace("EU_5", action="NO_TRADE",
                                   terminal_reason="V10 [risk]: R:R below min"),
        ], symbol="EURUSD")

        builder = DecisionUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.get_population(Population.ALL_DECISIONS)) == 5
        assert len(builder.get_population(Population.EXECUTE_DECISIONS)) == 1
        assert len(builder.get_population(Population.NO_TRADE_DECISIONS)) == 4
        assert len(builder.get_population(Population.REJECTED_AT_OPPORTUNITY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_STRATEGY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_ENTRY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_RISK)) == 1

    def test_normalised_fields(self, fake_s3):
        fake_s3.add("decision_trace", [_sample_decision_trace()], symbol="EURUSD")

        builder = DecisionUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]

        assert "entity_id" in r
        assert "action" in r
        assert "score" in r
        assert "ev" in r
        assert "opportunity_quality" in r
        assert "location_score" in r
        assert "strategy_family" in r
        assert "regime" in r
        assert "terminal_reason" in r

    def test_universe_type(self):
        builder = DecisionUniverseBuilder()
        assert builder.universe_type == Universe.DECISION


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET UNIVERSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarketUniverse:

    def test_build_from_decision_traces(self, fake_s3):
        fake_s3.add("decision_trace", [
            _sample_decision_trace("EU_1", regime="TRENDING"),
            _sample_decision_trace("EU_2", regime="RANGING"),
        ], symbol="EURUSD")

        builder = MarketUniverseBuilder(symbol="EURUSD")
        result = builder.build()

        assert len(result) == 2
        assert builder.is_built

    def test_regime_populations(self, fake_s3):
        fake_s3.add("decision_trace", [
            _sample_decision_trace("EU_1", regime="TRENDING"),
            _sample_decision_trace("EU_2", regime="RANGING"),
            _sample_decision_trace("EU_3", regime="RANGING"),
            _sample_decision_trace("EU_4", regime="TRANSITIONAL"),
        ], symbol="EURUSD")

        builder = MarketUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.get_population(Population.ALL_MARKET_STATES)) == 4
        assert len(builder.get_population(Population.TRENDING_REGIME)) == 1
        assert len(builder.get_population(Population.RANGING_REGIME)) == 2
        assert len(builder.get_population(Population.TRANSITIONAL_REGIME)) == 1

    def test_normalised_fields(self, fake_s3):
        fake_s3.add("decision_trace", [_sample_decision_trace()], symbol="EURUSD")

        builder = MarketUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]

        assert "entity_id" in r
        assert "regime" in r
        assert "volatility_state" in r
        assert "h4_trend" in r
        assert "h1_structural_clarity" in r
        assert "h1_bos_confirmed" in r
        assert "location_type" in r
        assert "htf_alignment_macro_bias" in r
        assert "structure_alignment" in r

    def test_universe_type(self):
        builder = MarketUniverseBuilder()
        assert builder.universe_type == Universe.MARKET


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY UNIVERSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyUniverse:

    def test_build_from_strategy_observations(self, fake_s3):
        fake_s3.add("strategy_observations", [
            _sample_strategy_obs("EU_1", family="TREND_CONTINUATION"),
            _sample_strategy_obs("EU_2", family="MEAN_REVERSION", status="REJECTED"),
        ], symbol="EURUSD")

        builder = StrategyUniverseBuilder(symbol="EURUSD")
        result = builder.build()

        assert len(result) == 2
        assert builder.is_built

    def test_family_populations(self, fake_s3):
        fake_s3.add("strategy_observations", [
            _sample_strategy_obs("E1", family="TREND_CONTINUATION"),
            _sample_strategy_obs("E2", family="MEAN_REVERSION"),
            _sample_strategy_obs("E3", family="MEAN_REVERSION"),
            _sample_strategy_obs("E4", family="BREAKOUT"),
        ], symbol="EURUSD")

        builder = StrategyUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.get_population(Population.ALL_STRATEGIES)) == 4
        assert len(builder.get_population(Population.TREND_CONTINUATION)) == 1
        assert len(builder.get_population(Population.MEAN_REVERSION)) == 2
        assert len(builder.get_population(Population.BREAKOUT)) == 1

    def test_selected_vs_rejected(self, fake_s3):
        fake_s3.add("strategy_observations", [
            _sample_strategy_obs("E1", status="SELECTED", action="EXECUTE"),
            _sample_strategy_obs("E2", status="REJECTED", action="NO_TRADE"),
            _sample_strategy_obs("E3", status="NOT_MET", action="NO_TRADE"),
        ], symbol="EURUSD")

        builder = StrategyUniverseBuilder(symbol="EURUSD")
        builder.build()

        assert len(builder.get_population(Population.STRATEGY_SELECTED)) == 1
        assert len(builder.get_population(Population.STRATEGY_REJECTED)) == 2

    def test_normalised_fields(self, fake_s3):
        fake_s3.add("strategy_observations", [_sample_strategy_obs()], symbol="EURUSD")

        builder = StrategyUniverseBuilder(symbol="EURUSD")
        builder.build()
        r = builder.records[0]

        assert "entity_id" in r
        assert "family" in r
        assert "confidence" in r
        assert "pattern" in r
        assert "conditions_met" in r
        assert "conditions_passed" in r
        assert "evaluation_status" in r
        assert "regime" in r
        assert "action" in r

    def test_deduplication_prefers_strategy_obs(self, fake_s3):
        """When same entity_id exists in both sources, prefer strategy_obs."""
        fake_s3.add("decision_trace", [
            _sample_decision_trace("EU_SHARED", action="EXECUTE"),
        ], symbol="EURUSD")
        fake_s3.add("strategy_observations", [
            _sample_strategy_obs("EU_SHARED", family="MEAN_REVERSION"),
        ], symbol="EURUSD")

        builder = StrategyUniverseBuilder(symbol="EURUSD")
        builder.build()

        # Should only have 1 record (deduped), from strategy_observations
        shared = [r for r in builder.records if r["entity_id"] == "EU_SHARED"]
        assert len(shared) == 1
        assert shared[0]["source"] == "strategy_observations"

    def test_universe_type(self):
        builder = StrategyUniverseBuilder()
        assert builder.universe_type == Universe.STRATEGY


# ═══════════════════════════════════════════════════════════════════════════════
# METADATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadata:

    def test_metadata_after_build(self, fake_s3):
        fake_s3.add("trade_truth", [_sample_trade_truth()], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()

        meta = builder.metadata
        assert isinstance(meta, UniverseMetadata)
        assert meta.universe == "EXECUTION"
        assert meta.record_count == 1
        assert meta.content_hash
        assert meta.generation_timestamp

    def test_metadata_to_dict(self, fake_s3):
        fake_s3.add("trade_truth", [_sample_trade_truth()], symbol="EURUSD")

        builder = ExecutionUniverseBuilder(symbol="EURUSD")
        builder.build()

        d = builder.metadata.to_dict()
        assert "universe" in d
        assert "record_count" in d
        assert "content_hash" in d
        assert "populations_available" in d

    def test_metadata_not_available_before_build(self):
        builder = ExecutionUniverseBuilder()
        with pytest.raises(RuntimeError):
            _ = builder.metadata
