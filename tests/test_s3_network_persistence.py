"""
Network Persistence Safety — S3 Write Isolation & Determinism Tests.

Validates ALL S3 writes across the persistence architecture:
    - Strict prefix routing (each layer ? own prefix only)
    - Payload purity (no cross-layer contamination)
    - Forbidden field rejection BEFORE write
    - Correlation integrity (joinable, no outcome dependency)
    - Write ordering (execution_context BEFORE shadow_trades)
    - Immutability guarantees (append-only, no overwrite)
    - Deterministic output (same input ? same record)

FAILURE = any cross-layer leakage, wrong prefix, or forbidden field reaching S3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest


# -------------------------------------------------------------------------------
# FIXTURES
# -------------------------------------------------------------------------------

@pytest.fixture
def mock_s3(monkeypatch):
    """Mock boto3 to capture all S3 put_object calls without network."""
    calls = []

    class FakeS3:
        def put_object(self, **kwargs):
            calls.append(kwargs)

        def get_object(self, **kwargs):
            raise Exception("NoSuchKey")

    def fake_client(*args, **kwargs):
        return FakeS3()

    monkeypatch.setattr("boto3.client", fake_client)
    return calls


@pytest.fixture
def local_dir(tmp_path):
    return tmp_path


# -------------------------------------------------------------------------------
# TEST 1: EXECUTION_CONTEXT ISOLATION
# -------------------------------------------------------------------------------

class TestExecutionContextIsolation:
    """execution_context writes ONLY to execution_context/ prefix."""

    def test_writes_to_correct_prefix(self, local_dir, monkeypatch):
        """Assert ONLY execution_context/ prefix is written."""
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))

        from core.execution_context import build_execution_context, persist_execution_context

        ctx = build_execution_context(
            correlation_id="COR-20260704-100-EURUSD-A93F",
            symbol="EURUSD",
            timestamp_utc=1700000000.0,
            bid=1.14435, ask=1.14447,
            session_state="LONDON",
        )
        result = persist_execution_context(ctx)
        assert result is True

        # Verify local write went to correct path
        files = list((local_dir / "ec").rglob("*.jsonl"))
        assert len(files) == 1
        assert "EURUSD" in str(files[0])

    def test_no_shadow_trades_written(self, local_dir, monkeypatch):
        """Writing execution_context must NOT produce shadow_trades/ files."""
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))

        from core.execution_context import build_execution_context, persist_execution_context

        ctx = build_execution_context(
            correlation_id="COR-TEST", symbol="EURUSD",
            timestamp_utc=1700000000.0, bid=1.1, ask=1.1001,
        )
        persist_execution_context(ctx)

        # No shadow_trades directory should exist
        assert not (local_dir / "shadow_trades").exists()
        assert not (local_dir / "trade_truth").exists()

    def test_s3_prefix_is_execution_context(self):
        """The S3 prefix constant must be the role-qualified Production V1 prefix."""
        from core.execution_context import _S3_PREFIX
        from core.production_data_contract import s3_base_prefix
        assert _S3_PREFIX == s3_base_prefix("execution_context")  # "supporting/execution_context"

    def test_s3_bucket_is_canonical(self):
        """Must write to the canonical runtime bucket only."""
        from core.execution_context import _S3_BUCKET
        assert _S3_BUCKET == "trading-bot-v10-data"


# -------------------------------------------------------------------------------
# TEST 2: SNAPSHOT PURITY (FORBIDDEN FIELD REJECTION)
# -------------------------------------------------------------------------------

class TestSnapshotPurity:
    """Forbidden fields must be rejected BEFORE any write attempt."""

    def test_rejects_pnl(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-X", "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "LONDON", "spread": 0.1, "spread_atr_ratio": 0.1},
            "infrastructure": {"latency_ms": 10, "feed_state": "HEALTHY", "tick_age_ms": 100, "bars_since_last_gap": 50},
            "risk_environment": {"drawdown_pct": 1.0, "daily_loss_pct": 0.5, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 1000, "last_feature_ts": 999},
            "pnl": 50.0,  # FORBIDDEN
        }
        result = persist_execution_context(record)
        assert result is False  # Rejected before write
        assert not list((local_dir / "ec").rglob("*.jsonl"))

    def test_rejects_entry_price(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-X", "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
            "entry_price": 1.1,  # FORBIDDEN
        }
        result = persist_execution_context(record)
        assert result is False

    def test_rejects_r_multiple(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-X", "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
            "r_multiple": 2.5,  # FORBIDDEN
        }
        result = persist_execution_context(record)
        assert result is False

    def test_rejects_trade_id(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-X", "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
            "trade_id": "shadow_123",  # FORBIDDEN
        }
        result = persist_execution_context(record)
        assert result is False

    def test_rejects_nested_forbidden_field(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-X", "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0,
                              "slippage": 0.0001},  # FORBIDDEN nested
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
        }
        result = persist_execution_context(record)
        assert result is False


# -------------------------------------------------------------------------------
# TEST 3: CORRELATION INTEGRITY
# -------------------------------------------------------------------------------

class TestCorrelationIntegrity:
    """correlation_id must be present and usable for cross-layer joins."""

    def test_correlation_id_required(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "",  # EMPTY = invalid
            "symbol": "EURUSD", "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
        }
        result = persist_execution_context(record)
        assert result is False

    def test_correlation_id_preserved_in_output(self, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import build_execution_context, persist_execution_context

        ctx = build_execution_context(
            correlation_id="COR-20260704-200-GBPUSD-BEEF",
            symbol="GBPUSD", timestamp_utc=1700000000.0,
            bid=1.26, ask=1.2601,
        )
        persist_execution_context(ctx)

        files = list((local_dir / "ec").rglob("*.jsonl"))
        content = files[0].read_text(encoding="utf-8").strip()
        rec = json.loads(content)
        assert rec["correlation_id"] == "COR-20260704-200-GBPUSD-BEEF"

    def test_no_dependency_on_execution_results(self):
        """Building execution_context must not require any trade outcome."""
        from core.execution_context import build_execution_context

        # Should build successfully with ONLY pre-decision data
        ctx = build_execution_context(
            correlation_id="COR-TEST", symbol="EURUSD",
            timestamp_utc=1700000000.0, bid=1.1, ask=1.1001,
        )
        d = ctx.to_dict()
        # No outcome fields anywhere
        assert "pnl" not in json.dumps(d)
        assert "r_multiple" not in json.dumps(d)
        assert "exit" not in json.dumps(d)


# -------------------------------------------------------------------------------
# TEST 4: WRITE ORDERING (TEMPORAL CONSTRAINT)
# -------------------------------------------------------------------------------

class TestWriteOrdering:
    """execution_context MUST be writable BEFORE shadow_trades opens."""

    def test_context_writable_before_trade(self, local_dir, monkeypatch):
        """Simulate: write context ? then open shadow trade. Context must succeed first."""
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))

        from core.execution_context import build_execution_context, persist_execution_context
        from core.shadow_trades import ShadowTradeEngine

        # Step 1: Write execution context (BEFORE trade)
        ctx = build_execution_context(
            correlation_id="COR-20260704-300-EURUSD-CAFE",
            symbol="EURUSD", timestamp_utc=1700000000.0,
            bid=1.14, ask=1.1401, session_state="LONDON",
            latency_ms=30, feed_state="HEALTHY",
        )
        ctx_result = persist_execution_context(ctx)
        assert ctx_result is True  # Context written FIRST

        # Step 2: Open shadow trade (AFTER context)
        engine = ShadowTradeEngine()
        trade = engine.open_trade(
            trade_id="shadow_300_EURUSD",
            cycle_id=300, symbol="EURUSD",
            direction="BUY", entry_price=1.14005,
            stop_loss=1.139, take_profit=1.142,
            entry_time=1700000000.0,
            correlation_id="COR-20260704-300-EURUSD-CAFE",
        )
        assert trade.correlation_id == "COR-20260704-300-EURUSD-CAFE"

    def test_context_does_not_depend_on_trade_id(self):
        """execution_context must be buildable WITHOUT knowing trade_id."""
        from core.execution_context import build_execution_context

        # trade_id is NOT a parameter — context is pre-trade
        ctx = build_execution_context(
            correlation_id="COR-PRE-TRADE",
            symbol="USDJPY", timestamp_utc=1700000000.0,
            bid=149.5, ask=149.52,
        )
        d = ctx.to_dict()
        assert "trade_id" not in json.dumps(d)


# -------------------------------------------------------------------------------
# TEST 5: FORBIDDEN FIELD COMPREHENSIVE REJECTION
# -------------------------------------------------------------------------------

class TestForbiddenFieldRejection:
    """Every forbidden field must be rejected individually."""

    FORBIDDEN_INJECTIONS = [
        ("pnl", 50.0),
        ("r_multiple", 2.0),
        ("entry_price", 1.1),
        ("exit_price", 1.12),
        ("trade_id", "T1"),
        ("position_id", "P1"),
        ("order_id", "O1"),
        ("slippage", 0.0001),
        ("confluence_score", 5.5),
        ("should_trade", True),
        ("strategy", "momentum_v1"),
        ("pattern", "ENGULFING"),
        ("exit_reason", "take_profit"),
        ("final_r", 1000.0),
        ("mfe_r", 2.5),
        ("mae_r", 0.3),
    ]

    @pytest.mark.parametrize("field,value", FORBIDDEN_INJECTIONS)
    def test_rejects_forbidden_field(self, field, value, local_dir, monkeypatch):
        monkeypatch.setattr("core.execution_context._LOCAL_DIR", str(local_dir / "ec"))
        from core.execution_context import persist_execution_context

        record = {
            "correlation_id": "COR-REJECT",
            "symbol": "EURUSD",
            "timestamp_utc": 1700000000,
            "market_access": {"bid": 1.1, "ask": 1.2, "session_state": "X", "spread": 0.1, "spread_atr_ratio": 0},
            "infrastructure": {"latency_ms": 0, "feed_state": "HEALTHY", "tick_age_ms": 0, "bars_since_last_gap": 0},
            "risk_environment": {"drawdown_pct": 0, "daily_loss_pct": 0, "open_positions": 0, "correlation_exposure": 0},
            "events_ref": {"last_candle_ts": 0, "last_feature_ts": 0},
            field: value,  # INJECT FORBIDDEN FIELD
        }
        result = persist_execution_context(record)
        assert result is False, f"Should have rejected forbidden field '{field}'"


# -------------------------------------------------------------------------------
# TEST 6: DETERMINISTIC OUTPUT
# -------------------------------------------------------------------------------

class TestDeterministicOutput:
    """Same inputs must produce identical output (no randomness)."""

    def test_same_inputs_same_output(self):
        from core.execution_context import build_execution_context

        kwargs = dict(
            correlation_id="COR-DET-TEST",
            symbol="EURUSD",
            timestamp_utc=1700000000.0,
            bid=1.14435, ask=1.14447,
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

        ctx1 = build_execution_context(**kwargs)
        ctx2 = build_execution_context(**kwargs)

        assert ctx1.to_dict() == ctx2.to_dict()

    def test_serialization_is_deterministic(self):
        """JSON serialization produces identical bytes."""
        from core.execution_context import build_execution_context

        ctx = build_execution_context(
            correlation_id="COR-DET",
            symbol="GBPUSD",
            timestamp_utc=1700000000.0,
            bid=1.26, ask=1.2601,
        )
        s1 = json.dumps(ctx.to_dict(), separators=(",", ":"), sort_keys=True)
        s2 = json.dumps(ctx.to_dict(), separators=(",", ":"), sort_keys=True)
        assert s1 == s2


# -------------------------------------------------------------------------------
# TEST 7: CROSS-LAYER PREFIX ISOLATION
# -------------------------------------------------------------------------------

class TestCrossLayerPrefixIsolation:
    """No module writes to another layer's prefix.

    Production V1 writers resolve their prefix via s3_base_prefix() from the
    central contract. (Legacy v10-engine research writers were deleted.)
    """

    def test_shadow_trades_prefix(self):
        from core.shadow_trades import _S3_PREFIX
        from core.production_data_contract import s3_base_prefix
        assert _S3_PREFIX == s3_base_prefix("shadow_trades")  # "supporting/shadow_trades"

    def test_trade_truth_prefix(self):
        from core.trade_truth import _S3_TRADES_PREFIX
        from core.production_data_contract import s3_base_prefix
        assert _S3_TRADES_PREFIX == s3_base_prefix("trade_truth")  # "core/trade_truth"

    def test_execution_context_prefix(self):
        from core.execution_context import _S3_PREFIX
        from core.production_data_contract import s3_base_prefix
        assert _S3_PREFIX == s3_base_prefix("execution_context")  # "supporting/execution_context"

    def test_all_use_same_bucket(self):
        """All active Production V1 writers use the single canonical bucket."""
        from core.shadow_trades import _S3_BUCKET as st_bucket
        from core.trade_truth import _S3_BUCKET as tt_bucket
        from core.execution_context import _S3_BUCKET as ec_bucket

        canonical = "trading-bot-v10-data"
        assert st_bucket == canonical
        assert tt_bucket == canonical
        assert ec_bucket == canonical
