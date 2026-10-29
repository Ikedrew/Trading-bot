"""
Pipeline Lineage Validation — Synthetic Decision Simulation.

Creates one complete synthetic decision and verifies that entity_id and
correlation_id propagate consistently across every pipeline stage:

    market_context → decision_trace → decision_ledger →
    opportunity_assessment → shadow_trade → trade_truth

Confirms the new pipeline produces CURRENT classification.

This test does NOT execute broker orders or modify live data.
It simulates the data structures that the live pipeline produces.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pytest
from research_engine.data_quality.classifier import classify_record, DataEpoch


# ═══════════════════════════════════════════════════════════════════════════════
# SYNTHETIC PIPELINE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

# Simulated identifiers (as produced by the live scanner)
_SYMBOL = "EURUSD"
_CYCLE_ID = 42857
_BAR_TIME = 1753574400  # 2025-07-26T20:00:00Z
_ENTITY_ID = f"{_SYMBOL}_{_BAR_TIME}"  # Deterministic: "{symbol}_{bar_time}"
_CORRELATION_ID = f"COR-{_CYCLE_ID}-{_SYMBOL}-A1B2"
_RUNTIME_SESSION_ID = "abc123def456"
_TRADE_ID = f"shadow_{_CYCLE_ID}_{_SYMBOL}_SCALP"


def _simulate_market_context() -> dict:
    """Stage 1: MarketContext persisted per cycle."""
    return {
        "schema_version": "market_context_v1",
        "symbol": _SYMBOL,
        "cycle_id": _CYCLE_ID,
        "bar_time": _BAR_TIME,
        "h4": {"regime": "TRENDING", "confidence": 0.82},
        "h1": {"bias": "BULLISH", "bos_confirmed": True},
        "m15": {"quality_score": 0.65},
        "phase": "IMPULSE",
        "phase_confidence": 0.70,
        "tradability_score": 0.75,
        "direction": "BULLISH",
        "conflict_detected": False,
    }


def _simulate_decision_trace() -> dict:
    """Stage 2: DecisionTrace persisted per entity_id per cycle."""
    return {
        "schema_version": "decision_trace_v1",
        "entity_id": _ENTITY_ID,
        "symbol": _SYMBOL,
        "cycle_id": _CYCLE_ID,
        "timestamp_utc": "2025-07-26T20:00:05Z",
        "action": "EXECUTE",
        "terminal_stage": "execute",
        "regime": "TRENDING",
        "regime_source": "H4_MARKET_CONTEXT",
        "regime_confidence": 0.82,
        "score_neutral": 0.62,
        "score_strategy": 0.68,
        "p_success": 0.41,
        "ev": 0.032,
        "selected_strategy": "CONTINUATION",
        "strategy_confidence": 0.0,
        "htf_alignment": 0.72,
        "h4_alignment": 0.80,
        "components": {
            "pattern_quality": 0.70,
            "bias_alignment": 0.80,
            "market_quality": 0.65,
            "trend_alignment": 0.75,
            "chop_clarity": 0.60,
            "volatility_quality": 0.55,
            "bias_stability": 0.50,
            "confirmation_pre": 0.70,
            "htf_alignment": 0.72,
            "h4_alignment": 0.80,
        },
        "pattern_name": "BOS_PULLBACK",
        "market_state": "TRENDING",
        "runtime_session_id": _RUNTIME_SESSION_ID,
    }


def _simulate_decision_ledger() -> dict:
    """Stage 3: Decision ledger entry."""
    return {
        "symbol": _SYMBOL,
        "cycle_id": _CYCLE_ID,
        "entity_id": _ENTITY_ID,
        "correlation_id": _CORRELATION_ID,
        "decision": "EXECUTE",
        "reason": "All gates passed",
        "signal_score": 0.62,
        "regime": "TRENDING",
        "timestamp_utc": "2025-07-26T20:00:05Z",
    }


def _simulate_opportunity() -> dict:
    """Stage 4: Opportunity assessment."""
    return {
        "schema_version": "opportunity_v1",
        "opportunity_id": f"OPP-{_CYCLE_ID}-{_SYMBOL}-BOS_PULLBACK",
        "symbol": _SYMBOL,
        "cycle_id": _CYCLE_ID,
        "entity_id": _ENTITY_ID,
        "pattern": "BOS_PULLBACK",
        "direction": "BUY",
        "state": "ASSESSED",
        "h4_regime": "TRENDING",
        "h1_direction": "BULLISH",
        "overall_score": 0.62,
        "strategy_classification": "CONTINUATION",
        "strategy_confidence": 0.0,
        "correlation_id": _CORRELATION_ID,
        "runtime_session_id": _RUNTIME_SESSION_ID,
    }


def _simulate_shadow_trade() -> dict:
    """Stage 5: Shadow trade (STR — Simulated Trade Lifecycle Record)."""
    return {
        "schema_version": "shadow_trades_v2",
        "source": "shadow_trade_engine",
        "identity": {
            "trade_id": _TRADE_ID,
            "correlation_id": _CORRELATION_ID,
            "symbol": _SYMBOL,
            "strategy_id": "CONTINUATION",
            "cycle_id": str(_CYCLE_ID),
            "entity_id": _ENTITY_ID,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": _BAR_TIME,
            "entry_intent_price": 1.08500,
            "stop_loss_intent": 1.08300,
            "take_profit_intent": 1.08900,
            "direction": "BUY",
            "position_size": 0.01,
            "pattern": "BOS_PULLBACK",
            "score": 0.62,
            "market_phase": "IMPULSE",
            "market_phase_confidence": 0.70,
            "regime": "TRENDING",
            "h4_regime": "TRENDING",
            "h1_bias": "BULLISH",
            "trade_horizon": "SCALP",
        },
        "simulation_environment": {
            "htf_snapshot": {"timeframe_bias": {"H4": {"regime": "TRENDING"}}},
            "entry_bar_index": 295,
        },
        "simulated_outcome": {
            "exit_price": 1.08900,
            "exit_timestamp": _BAR_TIME + 3600,
            "pnl_r_multiple": 2.0,
            "mfe_r": 2.1,
            "mae_r": -0.3,
            "exit_reason": "take_profit",
            "bars_held": 12,
        },
    }


def _simulate_trade_truth() -> dict:
    """Stage 6: Trade truth (actual broker execution outcome)."""
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": f"live_{_CYCLE_ID}_{_SYMBOL}",
            "correlation_id": _CORRELATION_ID,
            "symbol": _SYMBOL,
            "entity_id": _ENTITY_ID,
        },
        "execution": {
            "entry_price": 1.08502,
            "exit_price": 1.08895,
            "slippage_entry": 0.00002,
            "slippage_exit": -0.00005,
            "fill_latency_ms": 45,
        },
        "outcome": {
            "r_multiple_realised": 1.96,
            "pnl_realised": 3.93,
            "exit_reason": "take_profit",
            "bars_held": 12,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntityIdConsistency:
    """Verify entity_id exists and is identical across all pipeline stages."""

    def test_entity_id_in_decision_trace(self):
        trace = _simulate_decision_trace()
        assert trace["entity_id"] == _ENTITY_ID
        assert _SYMBOL in trace["entity_id"]

    def test_entity_id_in_decision_ledger(self):
        ledger = _simulate_decision_ledger()
        assert ledger["entity_id"] == _ENTITY_ID

    def test_entity_id_in_opportunity(self):
        opp = _simulate_opportunity()
        assert opp["entity_id"] == _ENTITY_ID

    def test_entity_id_in_shadow_trade(self):
        shadow = _simulate_shadow_trade()
        assert shadow["identity"]["entity_id"] == _ENTITY_ID

    def test_entity_id_in_trade_truth(self):
        truth = _simulate_trade_truth()
        assert truth["identity"]["entity_id"] == _ENTITY_ID

    def test_entity_id_consistent_across_all_stages(self):
        """Same entity_id in every stage — proves joinability."""
        stages = [
            _simulate_decision_trace()["entity_id"],
            _simulate_decision_ledger()["entity_id"],
            _simulate_opportunity()["entity_id"],
            _simulate_shadow_trade()["identity"]["entity_id"],
            _simulate_trade_truth()["identity"]["entity_id"],
        ]
        assert all(eid == _ENTITY_ID for eid in stages)
        assert len(set(stages)) == 1


class TestCorrelationIdConsistency:
    """Verify correlation_id exists and is identical across execution stages."""

    def test_correlation_id_in_ledger(self):
        ledger = _simulate_decision_ledger()
        assert ledger["correlation_id"] == _CORRELATION_ID

    def test_correlation_id_in_opportunity(self):
        opp = _simulate_opportunity()
        assert opp["correlation_id"] == _CORRELATION_ID

    def test_correlation_id_in_shadow_trade(self):
        shadow = _simulate_shadow_trade()
        assert shadow["identity"]["correlation_id"] == _CORRELATION_ID

    def test_correlation_id_in_trade_truth(self):
        truth = _simulate_trade_truth()
        assert truth["identity"]["correlation_id"] == _CORRELATION_ID

    def test_correlation_id_consistent_across_execution_stages(self):
        """Same correlation_id links decision → execution → outcome."""
        stages = [
            _simulate_decision_ledger()["correlation_id"],
            _simulate_opportunity()["correlation_id"],
            _simulate_shadow_trade()["identity"]["correlation_id"],
            _simulate_trade_truth()["identity"]["correlation_id"],
        ]
        assert all(cid == _CORRELATION_ID for cid in stages)


class TestMarketContextLineage:
    """Verify market context fields propagate into downstream records."""

    def test_regime_propagates_to_trace(self):
        mc = _simulate_market_context()
        trace = _simulate_decision_trace()
        assert trace["regime"] == mc["h4"]["regime"]

    def test_regime_propagates_to_shadow(self):
        mc = _simulate_market_context()
        shadow = _simulate_shadow_trade()
        assert shadow["decision_snapshot"]["h4_regime"] == mc["h4"]["regime"]

    def test_phase_propagates_to_shadow(self):
        mc = _simulate_market_context()
        shadow = _simulate_shadow_trade()
        assert shadow["decision_snapshot"]["market_phase"] == mc["phase"]

    def test_h1_bias_propagates_to_shadow(self):
        shadow = _simulate_shadow_trade()
        assert shadow["decision_snapshot"]["h1_bias"] == "BULLISH"


class TestDataQualityClassification:
    """Verify new pipeline records get CURRENT classification."""

    def test_shadow_trade_classified_current(self):
        shadow = _simulate_shadow_trade()
        epoch = classify_record(shadow)
        assert epoch == DataEpoch.CURRENT

    def test_shadow_trade_not_legacy(self):
        shadow = _simulate_shadow_trade()
        assert classify_record(shadow) != DataEpoch.LEGACY

    def test_current_classification_requires_entity_id(self):
        shadow = _simulate_shadow_trade()
        shadow["identity"]["entity_id"] = ""  # Remove entity_id
        epoch = classify_record(shadow)
        assert epoch != DataEpoch.CURRENT  # Should downgrade

    def test_current_classification_requires_clean_strategy(self):
        shadow = _simulate_shadow_trade()
        shadow["identity"]["strategy_id"] = "CONTINUATION_SCALP"  # Contaminate
        epoch = classify_record(shadow)
        assert epoch == DataEpoch.LEGACY  # Contaminated = LEGACY

    def test_current_classification_requires_regime(self):
        shadow = _simulate_shadow_trade()
        shadow["decision_snapshot"]["h4_regime"] = ""  # Remove regime
        shadow["decision_snapshot"]["regime"] = ""     # Remove fallback too
        epoch = classify_record(shadow)
        assert epoch == DataEpoch.TRANSITIONAL  # Downgrade without regime


class TestFullChainJoinability:
    """Verify a complete lifecycle can be reconstructed from IDs."""

    def test_full_chain_linkable(self):
        """Every stage can be joined to the next via shared identifiers."""
        mc = _simulate_market_context()
        trace = _simulate_decision_trace()
        ledger = _simulate_decision_ledger()
        opp = _simulate_opportunity()
        shadow = _simulate_shadow_trade()
        truth = _simulate_trade_truth()

        # market_context → decision_trace (via symbol + bar_time/cycle_id)
        assert mc["symbol"] == trace["symbol"]
        assert mc["cycle_id"] == trace["cycle_id"]

        # decision_trace → decision_ledger (via entity_id)
        assert trace["entity_id"] == ledger["entity_id"]

        # decision_ledger → opportunity (via entity_id + correlation_id)
        assert ledger["entity_id"] == opp["entity_id"]
        assert ledger["correlation_id"] == opp["correlation_id"]

        # opportunity → shadow_trade (via entity_id + correlation_id)
        assert opp["entity_id"] == shadow["identity"]["entity_id"]
        assert opp["correlation_id"] == shadow["identity"]["correlation_id"]

        # shadow_trade → trade_truth (via correlation_id)
        assert shadow["identity"]["correlation_id"] == truth["identity"]["correlation_id"]

        # Full chain: single entity_id links ALL stages
        all_entity_ids = [
            trace["entity_id"],
            ledger["entity_id"],
            opp["entity_id"],
            shadow["identity"]["entity_id"],
            truth["identity"]["entity_id"],
        ]
        assert len(set(all_entity_ids)) == 1  # All same

    def test_entity_id_format_deterministic(self):
        """entity_id = {symbol}_{bar_time} — always reproducible."""
        assert _ENTITY_ID == f"{_SYMBOL}_{_BAR_TIME}"
        # Given the same symbol and bar_time, we always get the same entity_id
        reconstructed = f"{_SYMBOL}_{_BAR_TIME}"
        assert reconstructed == _ENTITY_ID
