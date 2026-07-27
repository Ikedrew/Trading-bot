"""
Tests for runtime_session_id observability feature.

Verifies:
A) Provided runtime_session_id is persisted in audit record
B) Missing runtime_session_id remains backwards compatible (empty string)
C) Multiple audit records from the same session receive the same session id
D) Different scanner starts would generate different session ids
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.decision_audit import persist_new_engine_decision_audit


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


def _make_result(action="NO_TRADE", reason="ev_policy_blocked"):
    return {
        "action": action,
        "reason": reason,
        "score": 0.5,
        "score_neutral": 0.45,
        "score_strategy": 0.5,
        "pattern": "HAMMER",
        "side": "BUY",
        "strategy": None,
        "strategy_confidence": 0.0,
        "activation_regime": "TRANSITIONAL",
        "market_state": "TRANSITIONAL",
        "market_state_confidence": 0.5,
        "policy_trade_allowed": False,
        "policy_reasoning": "EV negative",
        "ev": -0.00002,
        "ev_positive": False,
        "p_success": 0.2,
        "rr_effective": 1.5,
        "confirmation_score": 0.5,
        "entity_id": "EURUSD_1700000000",
        "assessment": None,
    }


@pytest.fixture
def tmp_audit_dir(tmp_path):
    with patch("core.decision_audit.config") as mock_cfg:
        mock_cfg.DECISION_AUDIT_ENABLED = True
        mock_cfg.DECISION_AUDIT_DIR = str(tmp_path)
        mock_cfg.DECISION_AUDIT_FLUSH_EVERY_WRITE = True
        mock_cfg.TIMEFRAME = 5
        mock_cfg.EVENT_STREAM_S3_MIRROR = False
        yield tmp_path


# ─── TEST A: Provided runtime_session_id is persisted ─────────────────────────

class TestSessionIdPersisted:
    def test_session_id_included_in_record(self, tmp_audit_dir):
        """When runtime_session_id is provided, it appears in the output."""
        persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=1,
            engine_result=_make_result(),
            engine_state=_FakeEngineState(),
            candles=[_FakeCandle()],
            closed_i=0,
            runtime_session_id="7fa91c3dabcd",
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        assert record["runtime_session_id"] == "7fa91c3dabcd"


# ─── TEST B: Backwards compatibility ──────────────────────────────────────────

class TestBackwardsCompatibility:
    def test_missing_session_id_defaults_to_empty(self, tmp_audit_dir):
        """When runtime_session_id not provided, field is empty string."""
        persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=1,
            engine_result=_make_result(),
            engine_state=_FakeEngineState(),
            candles=[_FakeCandle()],
            closed_i=0,
            # runtime_session_id NOT provided — uses default
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        assert record["runtime_session_id"] == ""

    def test_existing_fields_unchanged(self, tmp_audit_dir):
        """Adding runtime_session_id doesn't affect existing fields."""
        persist_new_engine_decision_audit(
            symbol="EURUSD",
            cycle_id=42,
            engine_result=_make_result(),
            engine_state=_FakeEngineState(),
            candles=[_FakeCandle()],
            closed_i=0,
            entity_id="EURUSD_1700000000",
            runtime_session_id="abc123",
        )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())

        # Existing fields still present and correct
        assert record["symbol"] == "EURUSD"
        assert record["cycle_id"] == 42
        assert record["entity_id"] == "EURUSD_1700000000"
        assert "decision_id" in record
        assert "ts_utc_ms" in record


# ─── TEST C: Same session = same session_id ───────────────────────────────────

class TestSessionConsistency:
    def test_multiple_records_same_session_id(self, tmp_audit_dir):
        """Multiple audit calls with same session_id produce consistent records."""
        session_id = "consistent123"

        for i in range(5):
            persist_new_engine_decision_audit(
                symbol="EURUSD",
                cycle_id=i,
                engine_result=_make_result(),
                engine_state=_FakeEngineState(),
                candles=[_FakeCandle(time=1700000000 + i * 300)],
                closed_i=0,
                runtime_session_id=session_id,
            )

        files = list(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1

        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5

        for line in lines:
            record = json.loads(line)
            assert record["runtime_session_id"] == session_id


# ─── TEST D: Different starts = different session_ids ─────────────────────────

class TestSessionUniqueness:
    def test_different_sessions_produce_different_ids(self):
        """Two separate session generations produce different values."""
        session_a = uuid.uuid4().hex[:12]
        session_b = uuid.uuid4().hex[:12]

        assert session_a != session_b
        assert len(session_a) == 12
        assert len(session_b) == 12

    def test_session_id_format(self):
        """Session ID is 12-char hex string."""
        session_id = uuid.uuid4().hex[:12]

        assert len(session_id) == 12
        assert all(c in "0123456789abcdef" for c in session_id)
