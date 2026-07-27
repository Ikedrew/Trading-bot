"""
Tests for the execution_context/ persistence layer.

Validates:
    - Schema correctness (all required fields)
    - Forbidden field rejection
    - Immutability (frozen dataclass)
    - Validation rules
    - Persistence (local JSONL)
    - No outcome/execution data leakage
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.execution_context import (
    ExecutionContext,
    build_execution_context,
    persist_execution_context,
    validate_execution_context,
    load_execution_contexts,
)


@pytest.fixture
def valid_ctx():
    """A fully valid execution context."""
    return build_execution_context(
        correlation_id="COR-20260704-100-EURUSD-A93F",
        symbol="EURUSD",
        timestamp_utc=1700000000.0,
        bid=1.14435,
        ask=1.14447,
        session_state="LONDON",
        spread_atr_ratio=0.12,
        latency_ms=45,
        feed_state="HEALTHY",
        tick_age_ms=1200,
        bars_since_last_gap=150,
        drawdown_pct=1.2,
        daily_loss_pct=0.4,
        open_positions=1,
        correlation_exposure=0.02,
        last_candle_ts=1783081200000,
        last_feature_ts=1783081195000,
    )


class TestBuildExecutionContext:
    def test_builds_correctly(self, valid_ctx):
        assert valid_ctx.correlation_id == "COR-20260704-100-EURUSD-A93F"
        assert valid_ctx.symbol == "EURUSD"
        assert valid_ctx.timestamp_utc == 1700000000.0
        assert valid_ctx.market_access.session_state == "LONDON"
        assert valid_ctx.market_access.spread == round(1.14447 - 1.14435, 8)
        assert valid_ctx.market_access.bid == 1.14435
        assert valid_ctx.market_access.ask == 1.14447
        assert valid_ctx.infrastructure.latency_ms == 45
        assert valid_ctx.infrastructure.feed_state == "HEALTHY"
        assert valid_ctx.risk_environment.drawdown_pct == 1.2
        assert valid_ctx.risk_environment.open_positions == 1
        assert valid_ctx.events_ref.last_candle_ts == 1783081200000

    def test_frozen_immutable(self, valid_ctx):
        with pytest.raises(AttributeError):
            valid_ctx.correlation_id = "HACKED"
        with pytest.raises(AttributeError):
            valid_ctx.market_access = None

    def test_to_dict(self, valid_ctx):
        d = valid_ctx.to_dict()
        assert d["correlation_id"] == "COR-20260704-100-EURUSD-A93F"
        assert d["market_access"]["session_state"] == "LONDON"
        assert d["infrastructure"]["latency_ms"] == 45
        assert d["risk_environment"]["drawdown_pct"] == 1.2
        assert d["events_ref"]["last_candle_ts"] == 1783081200000

    def test_spread_computed_from_bid_ask(self):
        ctx = build_execution_context(
            correlation_id="X", symbol="EURUSD",
            timestamp_utc=1700000000.0,
            bid=1.10000, ask=1.10020,
        )
        assert ctx.market_access.spread == 0.0002


class TestValidation:
    def test_valid_passes(self, valid_ctx):
        valid, reason = validate_execution_context(valid_ctx.to_dict())
        assert valid is True
        assert reason == "valid"

    def test_missing_correlation_id(self):
        record = {"symbol": "X", "timestamp_utc": 1, "market_access": {"bid": 1, "ask": 1.1},
                  "infrastructure": {}, "risk_environment": {}, "events_ref": {}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "correlation_id" in reason

    def test_missing_symbol(self):
        record = {"correlation_id": "X", "timestamp_utc": 1, "market_access": {"bid": 1, "ask": 1.1},
                  "infrastructure": {}, "risk_environment": {}, "events_ref": {}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "symbol" in reason

    def test_invalid_timestamp(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 0,
                  "market_access": {"bid": 1, "ask": 1.1},
                  "infrastructure": {}, "risk_environment": {}, "events_ref": {}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "timestamp" in reason

    def test_missing_section(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 1, "ask": 1.1},
                  "risk_environment": {}, "events_ref": {}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "infrastructure" in reason

    def test_invalid_bid_ask(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 0, "ask": 0, "session_state": "X", "spread": 0, "spread_atr_ratio": 0},
                  "infrastructure": {}, "risk_environment": {}, "events_ref": {}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "bid_ask" in reason


class TestForbiddenFieldRejection:
    def test_rejects_entry_price(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "LONDON", "spread": 0.1, "spread_atr_ratio": 0.1},
                  "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
                  "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
                  "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
                  "entry_price": 1.1}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "entry_price" in reason

    def test_rejects_pnl(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
                  "infrastructure": {"latency_ms": 0, "feed_state": "X", "tick_age_ms": 0, "bars_since_last_gap": 0},
                  "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
                  "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
                  "pnl": 50.0}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "pnl" in reason

    def test_rejects_trade_id(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
                  "infrastructure": {"latency_ms": 0, "feed_state": "X", "tick_age_ms": 0, "bars_since_last_gap": 0},
                  "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
                  "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
                  "trade_id": "T1"}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "trade_id" in reason

    def test_rejects_nested_forbidden(self):
        record = {"correlation_id": "X", "symbol": "Y", "timestamp_utc": 1,
                  "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0, "confluence_score": 5.0},
                  "infrastructure": {"latency_ms": 0, "feed_state": "X", "tick_age_ms": 0, "bars_since_last_gap": 0},
                  "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
                  "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0}}
        valid, reason = validate_execution_context(record)
        assert valid is False
        assert "confluence_score" in reason


class TestPersistence:
    def test_persist_creates_file(self, valid_ctx, tmp_path, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(tmp_path / "ec"))
        result = persist_execution_context(valid_ctx)
        assert result is True

        files = list((tmp_path / "ec").rglob("*.jsonl"))
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8").strip()
        rec = json.loads(content)
        assert rec["correlation_id"] == "COR-20260704-100-EURUSD-A93F"
        assert rec["market_access"]["session_state"] == "LONDON"

    def test_persist_rejects_invalid(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(tmp_path / "ec"))
        bad = {"correlation_id": "", "symbol": "X"}
        result = persist_execution_context(bad)
        assert result is False
        assert not list((tmp_path / "ec").rglob("*.jsonl"))

    def test_load_contexts(self, valid_ctx, tmp_path, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(tmp_path / "ec"))
        persist_execution_context(valid_ctx)

        records = load_execution_contexts(local_dir=str(tmp_path / "ec"))
        assert len(records) == 1
        assert records[0]["symbol"] == "EURUSD"


class TestNoOutcomeLeakage:
    def test_context_has_no_outcome_fields(self, valid_ctx):
        d = valid_ctx.to_dict()
        flat_str = json.dumps(d)
        forbidden_in_output = ["entry_price", "exit_price", "pnl", "r_multiple",
                               "trade_id", "slippage", "score", "pattern", "strategy"]
        for field in forbidden_in_output:
            assert f'"{field}"' not in flat_str, f"Forbidden field {field} found in output"
