"""
Tests for persist_new_engine_decision_audit — the authoritative decision audit
function for the new pipeline path.

Verifies:
- EXECUTE decisions produce audit records
- NO_TRADE (rejected) decisions produce audit records
- Audit format is compatible with existing schema
- Disabled config prevents writes
- Never raises
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.decision_audit import persist_new_engine_decision_audit
from strategy.signals import Side


# ─── HELPERS ──────────────────────────────────────────────────────────────────

@dataclass
class _FakeCandle:
    time: int = 1700000000
    open: float = 1.1000
    high: float = 1.1050
    low: float = 1.0950
    close: float = 1.1020


@dataclass
class _FakeEngineState:
    current_bias: object = None
    bias_phase: str = "CONFIRMED"
    bias_strength: float = 75.0
    regime_state: str = "TRENDING"


@dataclass(frozen=True)
class _FakeIntent:
    symbol: str = "EURUSD"
    side: Side = Side.BUY
    volume: float = 0.01
    sl: float = 1.0950
    tp: float = 1.1166
    pattern: str = "BULLISH_ENGULFING"


@dataclass(frozen=True)
class _FakeAssessment:
    symbol: str = "EURUSD"
    side: str = "BUY"
    pattern: str = "BULLISH_ENGULFING"
    regime: str = "TRENDING"
    score_strategy: float = 0.68
    score_neutral: float = 0.62


def _make_execute_result():
    """Simulate run_new_engine() returning EXECUTE."""
    return {
        "action": "EXECUTE",
        "intent": _FakeIntent(),
        "score": 0.68,
        "score_neutral": 0.62,
        "score_strategy": 0.68,
        "pattern": "BULLISH_ENGULFING",
        "side": "BUY",
        "strategy": "CONTINUATION",
        "strategy_confidence": 0.72,
        "activation_regime": "TRENDING",
        "market_state": "STRUCTURED",
        "market_state_confidence": 0.78,
        "policy_trade_allowed": True,
        "policy_reasoning": "EV positive, RR sufficient",
        "ev": 0.000045,
        "ev_positive": True,
        "p_success": 0.35,
        "rr_effective": 2.1,
        "confirmation_score": 0.85,
        "entity_id": "EURUSD_1700000000",
        "strategy_ts_utc_ms": 1700000000000,
        "assessment": _FakeAssessment(),
    }


def _make_no_trade_result():
    """Simulate run_new_engine() returning NO_TRADE."""
    return {
        "action": "NO_TRADE",
        "reason": "ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
        "score": 0.42,
        "score_neutral": 0.38,
        "score_strategy": 0.42,
        "pattern": "HAMMER",
        "side": "BUY",
        "strategy": None,
        "strategy_confidence": 0.0,
        "activation_regime": "TRANSITIONAL",
        "market_state": "TRANSITIONAL",
        "market_state_confidence": 0.45,
        "policy_trade_allowed": False,
        "policy_reasoning": "EV negative",
        "ev": -0.00003,
        "ev_positive": False,
        "p_success": 0.22,
        "rr_effective": 1.8,
        "confirmation_score": 0.55,
        "entity_id": "EURUSD_1700000000",
        "strategy_ts_utc_ms": 1700000000000,
        "assessment": _FakeAssessment(score_strategy=0.42, score_neutral=0.38, regime="TRANSITIONAL"),
    }


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_audit_dir(tmp_path):
    """Redirect audit output to temp dir."""
    with patch("core.decision_audit.config") as mock_cfg:
        mock_cfg.DECISION_AUDIT_ENABLED = True
        mock_cfg.DECISION_AUDIT_DIR = str(tmp_path)
        mock_cfg.DECISION_AUDIT_FLUSH_EVERY_WRITE = True
        mock_cfg.DECISION_AUDIT_INCLUDE_REJECTIONS = True
        mock_cfg.TIMEFRAME = 5
        yield tmp_path


# ─── TESTS ────────────────────────────────────────────────────────────────────

class TestExecuteDecisionAudit:
    def test_execute_produces_audit_record(self, tmp_audit_dir):
        """EXECUTE decisions are persisted to JSONL."""
        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        decision_id = persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=42,
            engine_result=_make_execute_result(),
            engine_state=_FakeEngineState(),
            candles=candles,
            closed_i=5,
            correlation_id="COR-20260713-42-EURUSD-ABCD",
            entity_id="EURUSD_1700000000",
            strategy_ts_utc_ms=1700000000000,
        )

        # Verify decision_id returned
        assert decision_id != ""
        assert len(decision_id) == 32  # UUID hex

        # Verify file created
        files = list(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1

        # Verify record content
        content = files[0].read_text(encoding="utf-8").strip()
        record = json.loads(content)

        assert record["should_trade"] is True
        assert record["symbol"] == "EURUSD"
        assert record["cycle_id"] == 42
        assert record["score"] == 0.68
        assert record["pattern"] == "BULLISH_ENGULFING"
        assert record["strategy"] == "CONTINUATION"
        assert record["market_state"] == "STRUCTURED"
        assert record["ev_positive"] is True
        assert record["decision_id"] == decision_id
        assert record["correlation_id"] == "COR-20260713-42-EURUSD-ABCD"
        assert record["engine_version"] == "new_engine"

        # Verify intent populated
        assert record["intent"] is not None
        assert record["intent"]["side"] == "BUY"
        assert record["intent"]["sl"] == 1.0950
        assert record["intent"]["tp"] == 1.1166

    def test_execute_has_trigger_candle(self, tmp_audit_dir):
        """EXECUTE record includes trigger candle OHLC."""
        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=1,
            engine_result=_make_execute_result(),
            engine_state=_FakeEngineState(),
            candles=candles,
            closed_i=5,
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        assert record["trigger_candle"]["time"] == 1700001500  # candle at index 5
        assert record["trigger_candle"]["close"] == 1.1020


class TestNoTradeDecisionAudit:
    def test_no_trade_produces_audit_record(self, tmp_audit_dir):
        """NO_TRADE (rejected) decisions are also persisted."""
        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        decision_id = persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=99,
            engine_result=_make_no_trade_result(),
            engine_state=_FakeEngineState(),
            candles=candles,
            closed_i=7,
        )

        assert decision_id != ""

        files = list(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1

        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        assert record["should_trade"] is False
        assert "NEGATIVE_EXPECTED_VALUE" in record["reason"]
        assert record["score"] == 0.42
        assert record["ev_positive"] is False
        assert record["intent"] is None
        assert record["engine_version"] == "new_engine"

    def test_no_trade_has_policy_reasoning(self, tmp_audit_dir):
        """NO_TRADE records include policy/EV information."""
        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        persist_new_engine_decision_audit(
            symbol="GBPUSD",
            cycle_id=55,
            engine_result=_make_no_trade_result(),
            engine_state=_FakeEngineState(),
            candles=candles,
            closed_i=5,
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        assert record["policy_trade_allowed"] is False
        assert record["p_success"] == 0.22
        assert record["rr_effective"] == 1.8


class TestAuditDisabled:
    def test_disabled_returns_uuid_but_no_file(self, tmp_path):
        """When disabled, returns UUID but writes nothing."""
        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.DECISION_AUDIT_ENABLED = False
            mock_cfg.TIMEFRAME = 5

            decision_id = persist_new_engine_decision_audit(
                symbol="EURUSD",
                cycle_id=1,
                engine_result=_make_execute_result(),
                engine_state=_FakeEngineState(),
                candles=[_FakeCandle()],
                closed_i=0,
            )

        assert decision_id != ""
        assert len(decision_id) == 32
        # No files written
        assert list(tmp_path.glob("*.jsonl")) == []


class TestAuditSafety:
    def test_never_raises_on_bad_input(self, tmp_audit_dir):
        """Even with broken input, never raises."""
        # Empty result
        decision_id = persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=1,
            engine_result={},
            engine_state=_FakeEngineState(),
            candles=[],
            closed_i=0,
        )
        assert decision_id != ""

    def test_never_raises_on_none_fields(self, tmp_audit_dir):
        """Handles None assessment gracefully."""
        result = _make_execute_result()
        result["assessment"] = None
        result["intent"] = None

        decision_id = persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=1,
            engine_result=result,
            engine_state=_FakeEngineState(),
            candles=[_FakeCandle()],
            closed_i=0,
        )
        assert decision_id != ""


class TestFormatCompatibility:
    def test_record_has_expected_schema_fields(self, tmp_audit_dir):
        """Output record contains all expected schema fields."""
        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=42,
            engine_result=_make_execute_result(),
            engine_state=_FakeEngineState(),
            candles=candles,
            closed_i=5,
            correlation_id="COR-TEST",
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        # Required fields from original schema
        assert "ts_utc_ms" in record
        assert "timestamp_utc" in record
        assert "symbol" in record
        assert "cycle_id" in record
        assert "should_trade" in record
        assert "reason" in record
        assert "score" in record
        assert "intent" in record
        assert "engine_state" in record
        assert "trigger_candle" in record
        assert "decision_id" in record
        assert "correlation_id" in record

        # New engine specific fields
        assert "engine_version" in record
        assert "ev" in record
        assert "policy_trade_allowed" in record
        assert "market_state" in record
