"""
Tests for Phase 2A Shadow Opportunity Layer.

Covers:
    - Opportunity creation from Signal
    - Evidence extraction from HTF context
    - Lifecycle state transitions
    - Persistence to JSONL
    - Batch persistence
    - Rejection reason capture
    - No execution fields present
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.opportunity.opportunity import Opportunity, OpportunityState
from core.opportunity.factory import create_opportunity
from core.opportunity.persistence import persist_opportunity, persist_opportunity_batch
from strategy.signals import Signal, Side


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

def _make_signal(pattern: str = "TWEEZER_TOP", side: Side = Side.SELL,
                 bar_index: int = 5, bar_time: int = 1784800000,
                 confidence: float = 0.85) -> Signal:
    return Signal(
        pattern=pattern,
        side=side,
        bar_index=bar_index,
        bar_time=bar_time,
        confidence=confidence,
    )


class _MockCandle:
    def __init__(self, o=1.33700, h=1.33750, l=1.33650, c=1.33720, t=1784800000):
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.time = t


class _MockHTFContext:
    """Mock HTF context with H4 regime and H1 bias."""
    def __init__(self):
        self.regime = MagicMock()
        self.regime.classification = MagicMock()
        self.regime.classification.value = "TRENDING_BULLISH"
        self.regime.confidence = 0.78

        self.bias = MagicMock()
        self.bias.direction = MagicMock()
        self.bias.direction.value = "BULLISH"
        self.bias.bos_confirmed = True
        self.bias.swing_structure = "HH_HL"


class _MockEngineState:
    """Mock engine state with bias FSM."""
    def __init__(self):
        self.current_bias = MagicMock()
        self.current_bias.name = "SELL"
        self.bias_phase = "CONFIRMED"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: OPPORTUNITY CREATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpportunityCreation:
    """Test that create_opportunity produces correct Opportunity objects."""

    def test_basic_creation(self):
        """Signal produces Opportunity with correct identity fields."""
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="GBPUSD", cycle_id=42)

        assert opp.opportunity_id == "GBPUSD*1784800000*TWEEZER_TOP"
        assert opp.symbol == "GBPUSD"
        assert opp.cycle_id == 42
        assert opp.direction == "SELL"
        assert opp.pattern == "TWEEZER_TOP"
        assert opp.detection_timeframe == "M5"
        assert opp.detected_at_bar_time == 1784800000
        assert opp.state == OpportunityState.DETECTED.value

    def test_pattern_confidence_captured(self):
        """Pattern confidence from Signal is preserved."""
        signal = _make_signal(confidence=0.72)
        opp = create_opportunity(signal=signal, symbol="NZDUSD", cycle_id=1)

        assert opp.pattern_confidence == 0.72

    def test_trigger_candle_extracted(self):
        """Trigger candle OHLC is captured when candles provided."""
        signal = _make_signal(bar_index=2)
        candles = [_MockCandle(), _MockCandle(), _MockCandle(o=1.10, h=1.11, l=1.09, c=1.105)]
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=5, candles=candles)

        assert opp.trigger_candle["open"] == 1.10
        assert opp.trigger_candle["high"] == 1.11
        assert opp.trigger_candle["low"] == 1.09
        assert opp.trigger_candle["close"] == 1.105

    def test_htf_evidence_extracted(self):
        """H4 regime and H1 bias are captured from HTF context."""
        signal = _make_signal()
        htf = _MockHTFContext()
        opp = create_opportunity(signal=signal, symbol="GBPUSD", cycle_id=10, htf_context=htf)

        assert opp.h4_regime == "TRENDING_BULLISH"
        assert opp.h4_regime_confidence == 0.78
        assert opp.h1_direction == "BULLISH"
        assert opp.h1_bos_confirmed is True
        assert opp.h1_swing_structure == "HH_HL"

    def test_engine_state_bias_captured(self):
        """Bias FSM state extracted from engine_state."""
        signal = _make_signal()
        state = _MockEngineState()
        opp = create_opportunity(signal=signal, symbol="USDCHF", cycle_id=7, engine_state=state)

        assert opp.bias_direction == "SELL"
        assert opp.bias_phase == "CONFIRMED"

    def test_sibling_patterns_recorded(self):
        """Other patterns detected on same bar are listed."""
        signal = _make_signal(pattern="TWEEZER_TOP")
        opp = create_opportunity(
            signal=signal, symbol="AUDUSD", cycle_id=3,
            sibling_patterns=["EVENING_STAR", "HANGING_MAN"],
        )

        assert opp.sibling_patterns == ["EVENING_STAR", "HANGING_MAN"]

    def test_no_execution_fields(self):
        """Opportunity NEVER contains SL, TP, volume, or order details."""
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        d = opp.to_dict()

        # These fields must NOT exist
        forbidden = ["sl", "tp", "volume", "lot_size", "order_type",
                     "entry_price", "stop_loss", "take_profit"]
        for field in forbidden:
            assert field not in d, f"Forbidden field '{field}' found on Opportunity"

    def test_missing_context_graceful(self):
        """Opportunity is created safely when HTF/engine_state/candles are None."""
        signal = _make_signal()
        opp = create_opportunity(
            signal=signal, symbol="USDJPY", cycle_id=99,
            candles=None, htf_context=None, engine_state=None,
        )

        assert opp.symbol == "USDJPY"
        assert opp.h4_regime == ""
        assert opp.h1_direction == ""
        assert opp.bias_direction == ""
        assert opp.trigger_candle == {}
        assert opp.state == OpportunityState.DETECTED.value


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: LIFECYCLE TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestLifecycleTransitions:
    """Test Opportunity state machine."""

    def test_initial_state_is_detected(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        assert opp.state == "DETECTED"

    def test_transition_to_assessed(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        opp.transition(OpportunityState.ASSESSED)
        assert opp.state == "ASSESSED"

    def test_transition_to_rejected_with_reason(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        opp.transition(
            OpportunityState.REJECTED,
            rejection_reason="ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
            rejection_stage="execution_policy",
        )

        assert opp.state == "REJECTED"
        assert opp.rejection_reason == "ev_policy_blocked: NEGATIVE_EXPECTED_VALUE"
        assert opp.rejection_stage == "execution_policy"

    def test_transition_to_executed_with_trade_link(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="NZDUSD", cycle_id=1)
        opp.transition(OpportunityState.ASSESSED)
        opp.transition(
            OpportunityState.EXECUTED,
            outcome_trade_id="pos_53388892",
            correlation_id="COR-20260723-4578-NZDUSD-F40A",
        )

        assert opp.state == "EXECUTED"
        assert opp.outcome_trade_id == "pos_53388892"
        assert opp.correlation_id == "COR-20260723-4578-NZDUSD-F40A"

    def test_transition_to_expired(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        opp.transition(OpportunityState.EXPIRED)
        assert opp.state == "EXPIRED"

    def test_rejection_pattern_not_selected(self):
        """Pattern not selected by _select_best_pattern → rejected with reason."""
        signal = _make_signal(pattern="HANGING_MAN")
        opp = create_opportunity(signal=signal, symbol="GBPUSD", cycle_id=5)
        opp.transition(
            OpportunityState.REJECTED,
            rejection_reason="pattern_not_selected",
            rejection_stage="pattern_selection",
        )

        assert opp.state == "REJECTED"
        assert opp.rejection_reason == "pattern_not_selected"

    def test_rejection_guard_chain(self):
        """Guard chain block → rejected with guard name."""
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="USDCAD", cycle_id=10)
        opp.transition(OpportunityState.ASSESSED)
        opp.transition(
            OpportunityState.REJECTED,
            rejection_reason="correlation_guard:MAX_GROUP_POSITIONS",
            rejection_stage="guard_chain:correlation_guard",
        )

        assert opp.state == "REJECTED"
        assert "correlation_guard" in opp.rejection_reason


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    """Test to_dict produces valid JSON-serializable output."""

    def test_to_dict_complete(self):
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="EURUSD", cycle_id=1)
        d = opp.to_dict()

        assert isinstance(d, dict)
        assert d["opportunity_id"] == "EURUSD*1784800000*TWEEZER_TOP"
        assert d["symbol"] == "EURUSD"
        assert d["direction"] == "SELL"
        assert d["pattern"] == "TWEEZER_TOP"
        assert d["state"] == "DETECTED"

    def test_json_serializable(self):
        signal = _make_signal()
        htf = _MockHTFContext()
        opp = create_opportunity(signal=signal, symbol="GBPUSD", cycle_id=5, htf_context=htf)
        opp.transition(OpportunityState.REJECTED, rejection_reason="test_reason")

        # Must not raise
        serialized = json.dumps(opp.to_dict(), default=str)
        parsed = json.loads(serialized)
        assert parsed["state"] == "REJECTED"
        assert parsed["rejection_reason"] == "test_reason"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """Test JSONL persistence."""

    def test_persist_writes_file(self, tmp_path):
        """Single opportunity persisted to JSONL."""
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="NZDUSD", cycle_id=1)

        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path / "opportunities")):
            persist_opportunity(opp)

        files = list((tmp_path / "opportunities" / "NZDUSD").glob("*.jsonl"))
        assert len(files) == 1

        with open(files[0]) as f:
            record = json.loads(f.read().strip())

        assert record["symbol"] == "NZDUSD"
        assert record["pattern"] == "TWEEZER_TOP"
        assert record["state"] == "DETECTED"
        assert "_persisted_at" in record

    def test_batch_persist(self, tmp_path):
        """Multiple opportunities persisted in one write."""
        opps = [
            create_opportunity(signal=_make_signal(pattern="TWEEZER_TOP"), symbol="GBPUSD", cycle_id=1),
            create_opportunity(signal=_make_signal(pattern="EVENING_STAR"), symbol="GBPUSD", cycle_id=1),
            create_opportunity(signal=_make_signal(pattern="HAMMER", side=Side.BUY), symbol="EURUSD", cycle_id=1),
        ]

        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path / "opportunities")):
            persist_opportunity_batch(opps)

        gbp_files = list((tmp_path / "opportunities" / "GBPUSD").glob("*.jsonl"))
        eur_files = list((tmp_path / "opportunities" / "EURUSD").glob("*.jsonl"))
        assert len(gbp_files) == 1
        assert len(eur_files) == 1

        with open(gbp_files[0]) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
        assert len(lines) == 2  # Two GBPUSD opportunities

    def test_persist_empty_batch(self, tmp_path):
        """Empty batch does nothing."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path / "opportunities")):
            persist_opportunity_batch([])

        assert not (tmp_path / "opportunities").exists()

    def test_lifecycle_update_persisted(self, tmp_path):
        """Lifecycle transition persisted with updated state."""
        signal = _make_signal()
        opp = create_opportunity(signal=signal, symbol="USDCHF", cycle_id=3)
        opp.transition(OpportunityState.REJECTED, rejection_reason="score_below_threshold")

        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path / "opportunities")):
            persist_opportunity(opp)

        files = list((tmp_path / "opportunities" / "USDCHF").glob("*.jsonl"))
        with open(files[0]) as f:
            record = json.loads(f.read().strip())

        assert record["state"] == "REJECTED"
        assert record["rejection_reason"] == "score_below_threshold"
        assert record["_state_at_persist"] == "REJECTED"
