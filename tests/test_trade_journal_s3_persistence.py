"""
Trade Journal S3 Persistence — Tests.

Validates:
    1. S3 path generation (Hive-compatible partitioning)
    2. Schema version injected into every record
    3. Partition correctness (different symbols → different prefixes)
    4. S3 failure does not break trade journal
    5. Local persistence remains unchanged
    6. Record content includes required fields
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.trade_journal import (
    _record_to_dict,
    _write_s3_trade_journal,
    _SCHEMA_VERSION,
    _S3_BUCKET,
    _S3_PREFIX,
    persist_trade,
    build_trade_record,
    TradeRecord,
)
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


# ═══════════════════════════════════════════════════════════════════════════════
# 1. S3 Path Generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestS3PathGeneration:
    def test_s3_key_hive_format(self):
        """S3 key uses Hive-compatible partition layout."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_trade_journal("EURUSD", "2026-07-25", '{"test": true}\n')

        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args[1]
        expected_key = "trade_journal/schema_version=trade_journal_v1/symbol=EURUSD/date=2026-07-25/part-000.jsonl"
        assert call_kwargs["Key"] == expected_key
        assert call_kwargs["Bucket"] == "trading-bot-data-mk1"

    def test_s3_bucket_correct(self):
        assert _S3_BUCKET == "trading-bot-data-mk1"

    def test_s3_prefix_correct(self):
        assert _S3_PREFIX == "trade_journal"

    def test_schema_version_value(self):
        assert _SCHEMA_VERSION == "trade_journal_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Schema Version in Record
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    def test_record_contains_schema_version(self):
        """Every serialized record includes schema_version field."""
        pos = Position(
            position_id="pos_sv_test",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.1,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=1.11,
        )
        record = build_trade_record(position=pos, exit_price=1.11, exit_time=2000.0, close_reason="tp_hit")
        d = _record_to_dict(record)
        assert "schema_version" in d
        assert d["schema_version"] == "trade_journal_v1"

    def test_schema_version_in_json_output(self):
        """Schema version appears in the serialized JSON line."""
        pos = Position(
            position_id="pos_json_test",
            symbol="GBPUSD",
            side=Side.SELL,
            magic=713001,
            entry_price=1.33,
            initial_sl=1.34,
            initial_tp=1.31,
            stop_loss=1.34,
            take_profit=1.31,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=1.31,
        )
        record = build_trade_record(position=pos, exit_price=1.31, exit_time=2000.0, close_reason="tp_hit")
        line = json.dumps(_record_to_dict(record))
        parsed = json.loads(line)
        assert parsed["schema_version"] == "trade_journal_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Partition Correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartitioning:
    def test_different_symbols_different_keys(self):
        """Different symbols produce different S3 partition keys."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_trade_journal("EURUSD", "2026-07-25", '{"a":1}\n')
            _write_s3_trade_journal("GBPUSD", "2026-07-25", '{"b":2}\n')

        calls = mock_s3.put_object.call_args_list
        keys = [c[1]["Key"] for c in calls]
        assert "symbol=EURUSD" in keys[0]
        assert "symbol=GBPUSD" in keys[1]
        assert keys[0] != keys[1]

    def test_different_dates_different_keys(self):
        """Different dates produce different partition keys."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_trade_journal("EURUSD", "2026-07-24", '{"a":1}\n')
            _write_s3_trade_journal("EURUSD", "2026-07-25", '{"b":2}\n')

        calls = mock_s3.put_object.call_args_list
        keys = [c[1]["Key"] for c in calls]
        assert "date=2026-07-24" in keys[0]
        assert "date=2026-07-25" in keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Failure Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailureHandling:
    def test_s3_failure_does_not_raise(self):
        """S3 failure is silently swallowed — never raises."""
        # Calling with no boto3 available / no config should not raise
        _write_s3_trade_journal("EURUSD", "2026-07-25", '{"test":true}\n')
        # If we get here without exception, the test passes

    def test_persist_trade_succeeds_when_s3_fails(self, tmp_path):
        """Local persist succeeds even when S3 mirror fails."""
        pos = Position(
            position_id="pos_fail_test",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.1,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=1.11,
        )
        record = build_trade_record(position=pos, exit_price=1.11, exit_time=2000.0, close_reason="tp_hit")

        with patch("core.trade_journal._get_journal_dir", return_value=tmp_path), \
             patch("core.trade_journal._write_s3_trade_journal", side_effect=RuntimeError("S3 down")):
            result = persist_trade(record)

        assert result is True
        # Local file should exist
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Local Persistence Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestLocalPersistence:
    def test_local_file_still_created(self, tmp_path):
        """Local JSONL file is still created with correct content."""
        pos = Position(
            position_id="pos_local_test",
            symbol="USDJPY",
            side=Side.BUY,
            magic=713001,
            entry_price=150.0,
            initial_sl=149.5,
            initial_tp=151.0,
            stop_loss=149.5,
            take_profit=151.0,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=150.5,
        )
        record = build_trade_record(position=pos, exit_price=150.5, exit_time=2000.0, close_reason="tp_hit")

        with patch("core.trade_journal._get_journal_dir", return_value=tmp_path), \
             patch("core.trade_journal._write_s3_trade_journal"):
            persist_trade(record)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["symbol"] == "USDJPY"
        assert parsed["schema_version"] == "trade_journal_v1"
        assert parsed["trade_horizon"] == "SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Record Content Verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordContent:
    def test_record_has_required_trade_fields(self):
        pos = Position(
            position_id="pos_fields",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.1,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=1.11,
            trade_horizon="INTRADAY",
        )
        record = build_trade_record(position=pos, exit_price=1.11, exit_time=5000.0, close_reason="tp_hit")
        d = _record_to_dict(record)

        # Trade reconstruction
        assert "trade_id" in d
        assert "symbol" in d
        assert "direction" in d
        assert "entry_price" in d
        assert "exit_price" in d
        assert "entry_time" in d
        assert "exit_time" in d
        assert "duration_seconds" in d

        # Risk
        assert "initial_sl" in d
        assert "initial_tp" in d

        # Outcome
        assert "realised_pnl" in d
        assert "net_pnl" in d
        assert "close_reason" in d

        # Research
        assert "trade_horizon" in d
        assert d["trade_horizon"] == "INTRADAY"

        # Schema
        assert "schema_version" in d
        assert d["schema_version"] == "trade_journal_v1"

        # Identity
        assert "correlation_id" in d
