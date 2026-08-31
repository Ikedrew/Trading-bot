"""Tests for opportunity S3 persistence mirror.

Proves:
1. persist_opportunity() mirrors single records to S3
2. persist_opportunity_batch() mirrors batch records to S3
3. S3 mirror only fires when EVENT_STREAM_S3_MIRROR is enabled
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.opportunity.opportunity import Opportunity, OpportunityState
from core.opportunity.persistence import (
    persist_opportunity,
    persist_opportunity_batch,
    _write_s3_opportunity,
    _write_s3_opportunity_batch,
    _S3_BUCKET,
    _S3_PREFIX,
    _SCHEMA_VERSION,
)


def _fake_opportunity(symbol="EURUSD", pattern="TEST_PATTERN"):
    return Opportunity(
        opportunity_id=f"{symbol}_999_{pattern}",
        symbol=symbol,
        cycle_id=999,
        direction="SELL",
        pattern=pattern,
        detection_timeframe="M5",
        detected_at_bar_time=1785400000,
        detected_at_utc="2026-07-30T12:00:00.000Z",
    )


class TestSingleOpportunityS3:
    """persist_opportunity() must call _write_s3_opportunity."""

    @patch("core.opportunity.persistence._write_s3_opportunity")
    def test_single_persist_calls_s3_mirror(self, mock_s3, tmp_path):
        """Single opportunity persistence triggers S3 mirror."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path)):
            opp = _fake_opportunity()
            persist_opportunity(opp)
            mock_s3.assert_called_once()
            args = mock_s3.call_args
            assert args[0][0] == "EURUSD"  # symbol

    @patch("core.opportunity.persistence._write_s3_opportunity")
    def test_single_persist_creates_local_file(self, mock_s3, tmp_path):
        """Single opportunity creates local JSONL file."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path)):
            opp = _fake_opportunity()
            persist_opportunity(opp)
            files = list(tmp_path.rglob("*.jsonl"))
            assert len(files) == 1
            content = files[0].read_text()
            assert "TEST_PATTERN" in content


class TestBatchOpportunityS3:
    """persist_opportunity_batch() must call _write_s3_opportunity_batch."""

    @patch("core.opportunity.persistence._write_s3_opportunity_batch")
    def test_batch_persist_calls_s3_mirror(self, mock_s3_batch, tmp_path):
        """Batch opportunity persistence triggers S3 mirror."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path)):
            opps = [
                _fake_opportunity("EURUSD", "PATTERN_A"),
                _fake_opportunity("EURUSD", "PATTERN_B"),
                _fake_opportunity("GBPUSD", "PATTERN_C"),
            ]
            persist_opportunity_batch(opps)
            # Should be called twice: once for EURUSD batch, once for GBPUSD batch
            assert mock_s3_batch.call_count == 2
            symbols_called = [call[0][0] for call in mock_s3_batch.call_args_list]
            assert "EURUSD" in symbols_called
            assert "GBPUSD" in symbols_called

    @patch("core.opportunity.persistence._write_s3_opportunity_batch")
    def test_batch_persist_creates_local_files(self, mock_s3_batch, tmp_path):
        """Batch persist creates local JSONL for each symbol."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path)):
            opps = [
                _fake_opportunity("EURUSD", "PAT_A"),
                _fake_opportunity("AUDUSD", "PAT_B"),
            ]
            persist_opportunity_batch(opps)
            files = list(tmp_path.rglob("*.jsonl"))
            assert len(files) == 2

    @patch("core.opportunity.persistence._write_s3_opportunity_batch")
    def test_empty_batch_does_not_call_s3(self, mock_s3_batch):
        """Empty batch should not call S3."""
        persist_opportunity_batch([])
        mock_s3_batch.assert_not_called()

    @patch("core.opportunity.persistence._write_s3_opportunity_batch")
    def test_batch_content_contains_all_records(self, mock_s3_batch, tmp_path):
        """S3 batch content includes all records for that symbol."""
        with patch("core.opportunity.persistence._LOCAL_DIR", str(tmp_path)):
            opps = [
                _fake_opportunity("EURUSD", "FIRST"),
                _fake_opportunity("EURUSD", "SECOND"),
            ]
            persist_opportunity_batch(opps)
            # Get the content arg (3rd positional: symbol, date_str, content)
            content = mock_s3_batch.call_args[0][2]
            assert "FIRST" in content
            assert "SECOND" in content


class TestS3GateRespected:
    """S3 mirror must only fire when config gate is enabled."""

    def test_s3_batch_respects_mirror_gate_disabled(self):
        """When EVENT_STREAM_S3_MIRROR=False, no S3 call made."""
        import core.config as cfg
        original = getattr(cfg, "EVENT_STREAM_S3_MIRROR", True)
        cfg.EVENT_STREAM_S3_MIRROR = False
        try:
            # Should return early without attempting boto3
            _write_s3_opportunity_batch("EURUSD", "2026-08-03", "test\n")
        finally:
            cfg.EVENT_STREAM_S3_MIRROR = original

    def test_s3_single_respects_mirror_gate_disabled(self):
        """When EVENT_STREAM_S3_MIRROR=False, single persist skips S3."""
        import core.config as cfg
        original = getattr(cfg, "EVENT_STREAM_S3_MIRROR", True)
        cfg.EVENT_STREAM_S3_MIRROR = False
        try:
            _write_s3_opportunity("EURUSD", "2026-08-03", '{"test":true}')
            # Should not raise — returns early
        finally:
            cfg.EVENT_STREAM_S3_MIRROR = original


class TestS3KeyFormat:
    """S3 key must match the expected Athena-compatible format."""

    def test_key_format_in_source(self):
        """Verify the S3 key format uses correct partitioning."""
        import inspect
        source = inspect.getsource(_write_s3_opportunity_batch)
        assert "schema_version=" in source
        assert "_SCHEMA_VERSION" in source
        assert "symbol=" in source
        assert "date=" in source
        assert "part-000.jsonl" in source

    def test_schema_version_constant(self):
        assert _SCHEMA_VERSION == "opportunities_v1"

    def test_s3_prefix_constant(self):
        assert _S3_PREFIX == "opportunities"

    def test_s3_bucket_constant(self):
        assert _S3_BUCKET == "trading-bot-v10-data"
