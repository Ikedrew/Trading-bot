"""
Tests for the four universe builders.

Validates:
    - Each builder loads data, builds normalised records, and serves populations
    - Normalised records contain the semantic fields questions require
    - Population filters return correct subsets
    - Metadata is generated correctly
    - Builders work with synthetic data (no file system dependency)
"""

import json
import tempfile
from pathlib import Path

import pytest

from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
from research_engine.v10.universes.market_universe import MarketUniverseBuilder
from research_engine.v10.universes.strategy_universe import StrategyUniverseBuilder


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _write_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _sample_execution_record(trade_id="pos_1", r_mult=1.5, regime="TRENDING",
                              session="LONDON", pattern="ENGULFING",
                              family="TREND_CONTINUATION", anomaly=False):
    return {
        "trade_id": trade_id,
        "execution": {
            "ticket": 1, "symbol": "EURUSD", "direction": "BUY",
            "entry_price": 1.1, "exit_price": 1.102, "entry_time": 1000.0,
            "exit_time": 2000.0, "stop_loss": 1.099, "take_profit": 1.103,
            "gross_profit": 20.0, "commission": -2.0, "swap": 0.0,
            "net_realised_pnl": 18.0, "r_multiple": r_mult,
            "volume": 0.1, "duration_seconds": 1000.0, "exit_reason": "TAKE_PROFIT",
        },
        "decision": {
            "strategy": "trend", "score": 75.0, "confidence": 0.8,
            "decision_type": "EXECUTE", "decision_timestamp": 999.0,
            "components": {"location": 80, "structure": 70},
            "weakest_component": "structure", "ev": 0.3, "p_success": 0.6,
        },
        "market": {
            "regime": regime, "session": session, "volatility": "NORMAL",
            "trend_state": "UP", "higher_timeframe_bias": "BULLISH",
            "h4_phase": "IMPULSE", "h1_clarity": 0.8,
        },
        "strategy": {
            "family": family, "pattern": pattern, "conditions_met": 4,
            "strategy_confidence": 0.75, "opportunity_quality": 0.8,
            "opportunity_type": "REVERSAL",
        },
        "quality": {
            "anomaly": anomaly, "anomaly_reasons": [],
            "governance_status": "VALID", "data_completeness": "FULL",
            "missing": [], "join_method": "entity_id", "pnl_source": "BROKER",
        },
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

    def test_build_from_synthetic_data(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        records = [
            _sample_execution_record("t1", r_mult=1.5, regime="TRENDING"),
            _sample_execution_record("t2", r_mult=-1.0, regime="RANGING"),
            _sample_execution_record("t3", r_mult=0.5, regime="TRENDING", anomaly=True),
        ]
        _write_jsonl(path, records)

        builder = ExecutionUniverseBuilder(source_path=path)
        result = builder.build()

        assert len(result) == 3
        assert builder.is_built
        assert builder.metadata.record_count == 3

    def test_populations(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        records = [
            _sample_execution_record("t1", r_mult=2.0),
            _sample_execution_record("t2", r_mult=-1.0),
            _sample_execution_record("t3", r_mult=0.5, anomaly=True),
        ]
        _write_jsonl(path, records)

        builder = ExecutionUniverseBuilder(source_path=path)
        builder.build()

        assert len(builder.get_population(Population.ALL_TRADES)) == 3
        assert len(builder.get_population(Population.WINNING_TRADES)) == 2
        assert len(builder.get_population(Population.LOSING_TRADES)) == 1
        assert len(builder.get_population(Population.ANOMALOUS_TRADES)) == 1

    def test_normalised_fields_present(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        _write_jsonl(path, [_sample_execution_record()])

        builder = ExecutionUniverseBuilder(source_path=path)
        builder.build()
        r = builder.records[0]

        # Core execution fields
        assert "r_multiple" in r
        assert "net_realised_pnl" in r
        assert "entry_price" in r
        assert "exit_reason" in r
        assert "duration_seconds" in r
        assert "volume" in r
        # Decision fields
        assert "score" in r
        assert "ev" in r
        # Market fields
        assert "regime" in r
        assert "session" in r
        # Strategy fields
        assert "family" in r
        assert "pattern" in r
        # Join key
        assert "entity_id" in r

    def test_session_population(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        records = [
            _sample_execution_record("t1", session="LONDON"),
            _sample_execution_record("t2", session="NEW_YORK"),
            _sample_execution_record("t3", session="ASIA"),
        ]
        _write_jsonl(path, records)

        builder = ExecutionUniverseBuilder(source_path=path)
        builder.build()

        assert len(builder.get_population(Population.SESSION_LONDON)) == 1
        assert len(builder.get_population(Population.SESSION_NY)) == 1
        assert len(builder.get_population(Population.SESSION_ASIA)) == 1

    def test_not_built_raises(self, tmp_path):
        builder = ExecutionUniverseBuilder(source_path=tmp_path / "nope.jsonl")
        with pytest.raises(RuntimeError):
            _ = builder.records

    def test_universe_type(self):
        builder = ExecutionUniverseBuilder()
        assert builder.universe_type == Universe.EXECUTION


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION UNIVERSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionUniverse:

    def test_build_from_synthetic_data(self, tmp_path):
        path = tmp_path / "EURUSD" / "2026-08-07.jsonl"
        records = [
            _sample_decision_trace("EU_1", action="EXECUTE"),
            _sample_decision_trace("EU_2", action="NO_TRADE",
                                   terminal_reason="V10 [opportunity]: No opportunity"),
            _sample_decision_trace("EU_3", action="NO_TRADE",
                                   terminal_reason="V10 [risk]: Risk: R:R too low"),
        ]
        _write_jsonl(path, records)

        builder = DecisionUniverseBuilder(source_dir=tmp_path)
        result = builder.build()

        assert len(result) == 3
        assert builder.is_built

    def test_populations(self, tmp_path):
        path = tmp_path / "EURUSD" / "2026-08-07.jsonl"
        records = [
            _sample_decision_trace("EU_1", action="EXECUTE"),
            _sample_decision_trace("EU_2", action="NO_TRADE",
                                   terminal_reason="V10 [opportunity]: invalid"),
            _sample_decision_trace("EU_3", action="NO_TRADE",
                                   terminal_reason="V10 [strategy]: No strategy"),
            _sample_decision_trace("EU_4", action="NO_TRADE",
                                   terminal_reason="V10 [entry]: Entry not ready"),
            _sample_decision_trace("EU_5", action="NO_TRADE",
                                   terminal_reason="V10 [risk]: R:R below min"),
        ]
        _write_jsonl(path, records)

        builder = DecisionUniverseBuilder(source_dir=tmp_path)
        builder.build()

        assert len(builder.get_population(Population.ALL_DECISIONS)) == 5
        assert len(builder.get_population(Population.EXECUTE_DECISIONS)) == 1
        assert len(builder.get_population(Population.NO_TRADE_DECISIONS)) == 4
        assert len(builder.get_population(Population.REJECTED_AT_OPPORTUNITY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_STRATEGY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_ENTRY)) == 1
        assert len(builder.get_population(Population.REJECTED_AT_RISK)) == 1

    def test_normalised_fields(self, tmp_path):
        path = tmp_path / "SYM" / "file.jsonl"
        _write_jsonl(path, [_sample_decision_trace()])

        builder = DecisionUniverseBuilder(source_dir=tmp_path)
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

    def test_build_from_decision_traces(self, tmp_path):
        dt_dir = tmp_path / "dt" / "EURUSD"
        _write_jsonl(dt_dir / "2026-08-07.jsonl", [
            _sample_decision_trace("EU_1", regime="TRENDING"),
            _sample_decision_trace("EU_2", regime="RANGING"),
        ])

        builder = MarketUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            market_context_dir=tmp_path / "mc",  # empty
        )
        result = builder.build()

        assert len(result) == 2
        assert builder.is_built

    def test_regime_populations(self, tmp_path):
        dt_dir = tmp_path / "dt" / "EURUSD"
        _write_jsonl(dt_dir / "file.jsonl", [
            _sample_decision_trace("EU_1", regime="TRENDING"),
            _sample_decision_trace("EU_2", regime="RANGING"),
            _sample_decision_trace("EU_3", regime="RANGING"),
            _sample_decision_trace("EU_4", regime="TRANSITIONAL"),
        ])

        builder = MarketUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            market_context_dir=tmp_path / "mc",
        )
        builder.build()

        assert len(builder.get_population(Population.ALL_MARKET_STATES)) == 4
        assert len(builder.get_population(Population.TRENDING_REGIME)) == 1
        assert len(builder.get_population(Population.RANGING_REGIME)) == 2
        assert len(builder.get_population(Population.TRANSITIONAL_REGIME)) == 1

    def test_normalised_fields(self, tmp_path):
        dt_dir = tmp_path / "dt" / "SYM"
        _write_jsonl(dt_dir / "f.jsonl", [_sample_decision_trace()])

        builder = MarketUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            market_context_dir=tmp_path / "mc",
        )
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

    def test_build_from_strategy_observations(self, tmp_path):
        so_dir = tmp_path / "so" / "EURUSD"
        _write_jsonl(so_dir / "file.jsonl", [
            _sample_strategy_obs("EU_1", family="TREND_CONTINUATION"),
            _sample_strategy_obs("EU_2", family="MEAN_REVERSION", status="REJECTED"),
        ])

        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",  # empty
            strategy_obs_dir=tmp_path / "so",
        )
        result = builder.build()

        assert len(result) == 2
        assert builder.is_built

    def test_family_populations(self, tmp_path):
        so_dir = tmp_path / "so" / "SYM"
        _write_jsonl(so_dir / "f.jsonl", [
            _sample_strategy_obs("E1", family="TREND_CONTINUATION"),
            _sample_strategy_obs("E2", family="MEAN_REVERSION"),
            _sample_strategy_obs("E3", family="MEAN_REVERSION"),
            _sample_strategy_obs("E4", family="BREAKOUT"),
        ])

        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            strategy_obs_dir=tmp_path / "so",
        )
        builder.build()

        assert len(builder.get_population(Population.ALL_STRATEGIES)) == 4
        assert len(builder.get_population(Population.TREND_CONTINUATION)) == 1
        assert len(builder.get_population(Population.MEAN_REVERSION)) == 2
        assert len(builder.get_population(Population.BREAKOUT)) == 1

    def test_selected_vs_rejected(self, tmp_path):
        so_dir = tmp_path / "so" / "SYM"
        _write_jsonl(so_dir / "f.jsonl", [
            _sample_strategy_obs("E1", status="SELECTED", action="EXECUTE"),
            _sample_strategy_obs("E2", status="REJECTED", action="NO_TRADE"),
            _sample_strategy_obs("E3", status="NOT_MET", action="NO_TRADE"),
        ])

        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            strategy_obs_dir=tmp_path / "so",
        )
        builder.build()

        assert len(builder.get_population(Population.STRATEGY_SELECTED)) == 1
        assert len(builder.get_population(Population.STRATEGY_REJECTED)) == 2

    def test_normalised_fields(self, tmp_path):
        so_dir = tmp_path / "so" / "SYM"
        _write_jsonl(so_dir / "f.jsonl", [_sample_strategy_obs()])

        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            strategy_obs_dir=tmp_path / "so",
        )
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

    def test_universe_type(self):
        builder = StrategyUniverseBuilder()
        assert builder.universe_type == Universe.STRATEGY

    def test_deduplication_prefers_strategy_obs(self, tmp_path):
        """When same entity_id exists in both sources, prefer strategy_obs."""
        dt_dir = tmp_path / "dt" / "EURUSD"
        so_dir = tmp_path / "so" / "EURUSD"

        # Same entity_id in both
        _write_jsonl(dt_dir / "f.jsonl", [
            _sample_decision_trace("EU_SHARED", action="EXECUTE"),
        ])
        _write_jsonl(so_dir / "f.jsonl", [
            _sample_strategy_obs("EU_SHARED", family="MEAN_REVERSION"),
        ])

        builder = StrategyUniverseBuilder(
            decision_trace_dir=tmp_path / "dt",
            strategy_obs_dir=tmp_path / "so",
        )
        builder.build()

        # Should only have 1 record (deduped), from strategy_observations
        shared = [r for r in builder.records if r["entity_id"] == "EU_SHARED"]
        assert len(shared) == 1
        assert shared[0]["source"] == "strategy_observations"


# ═══════════════════════════════════════════════════════════════════════════════
# METADATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadata:

    def test_metadata_after_build(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        _write_jsonl(path, [_sample_execution_record()])

        builder = ExecutionUniverseBuilder(source_path=path)
        builder.build()

        meta = builder.metadata
        assert isinstance(meta, UniverseMetadata)
        assert meta.universe == "EXECUTION"
        assert meta.record_count == 1
        assert meta.content_hash
        assert meta.generation_timestamp

    def test_metadata_to_dict(self, tmp_path):
        path = tmp_path / "universe.jsonl"
        _write_jsonl(path, [_sample_execution_record()])

        builder = ExecutionUniverseBuilder(source_path=path)
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
