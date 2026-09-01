"""
Opportunities S3 Persistence — Tests.

Validates:
    1. S3 path generation (Hive-compatible partitioning)
    2. Schema version injected into every record
    3. Partition correctness (different symbols → different prefixes)
    4. S3 failure does not break opportunity processing
    5. Local persistence remains unchanged
    6. Record serialization
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.opportunity.persistence import (
    persist_opportunity,
    _write_s3_opportunity,
    _SCHEMA_VERSION,
    _S3_BUCKET,
    _S3_PREFIX,
)
from core.opportunity.opportunity import Opportunity


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_opportunity(symbol: str = "EURUSD", pattern: str = "HAMMER") -> Opportunity:
    return Opportunity(
        opportunity_id=f"opp_test_{symbol}",
        symbol=symbol,
        cycle_id=100,
        direction="BUY",
        pattern=pattern,
        detection_timeframe="M5",
        detected_at_bar_time=1719000000,
        detected_at_utc="2026-07-25T12:00:00Z",
        overall_score=0.75,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. S3 Path Generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestS3PathGeneration:
    def test_s3_key_hive_format(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_opportunity("EURUSD", "2026-07-25", '{"test": true}\n')

        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args[1]
        expected_key = "core/opportunities/schema_version=opportunities_v1/symbol=EURUSD/date=2026-07-25/part-000.jsonl"
        assert call_kwargs["Key"] == expected_key
        assert call_kwargs["Bucket"] == "trading-bot-v10-data"

    def test_s3_bucket_correct(self):
        assert _S3_BUCKET == "trading-bot-v10-data"

    def test_s3_prefix_correct(self):
        assert _S3_PREFIX == "core/opportunities"

    def test_schema_version_value(self):
        assert _SCHEMA_VERSION == "opportunities_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Schema Version in Record
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    def test_record_contains_schema_version(self, tmp_path):
        opp = _make_opportunity("EURUSD")
        local_dir = tmp_path / "opps"

        with patch("core.opportunity.persistence._LOCAL_DIR", str(local_dir)), \
             patch("core.opportunity.persistence._write_s3_opportunity"):
            persist_opportunity(opp)

        files = list(local_dir.rglob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert "schema_version" in parsed
        assert parsed["schema_version"] == "opportunities_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Partition Correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartitioning:
    def test_different_symbols_different_keys(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_opportunity("EURUSD", "2026-07-25", '{"a":1}\n')
            _write_s3_opportunity("GBPUSD", "2026-07-25", '{"b":2}\n')

        calls = mock_s3.put_object.call_args_list
        keys = [c[1]["Key"] for c in calls]
        assert "symbol=EURUSD" in keys[0]
        assert "symbol=GBPUSD" in keys[1]
        assert keys[0] != keys[1]

    def test_different_dates_different_keys(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_opportunity("EURUSD", "2026-07-24", '{"a":1}\n')
            _write_s3_opportunity("EURUSD", "2026-07-25", '{"b":2}\n')

        calls = mock_s3.put_object.call_args_list
        keys = [c[1]["Key"] for c in calls]
        assert "date=2026-07-24" in keys[0]
        assert "date=2026-07-25" in keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Failure Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailureHandling:
    def test_s3_failure_does_not_raise(self):
        _write_s3_opportunity("EURUSD", "2026-07-25", '{"test":true}\n')

    def test_persist_succeeds_when_s3_fails(self, tmp_path):
        opp = _make_opportunity("EURUSD")
        local_dir = tmp_path / "opps"

        with patch("core.opportunity.persistence._LOCAL_DIR", str(local_dir)), \
             patch("core.opportunity.persistence._write_s3_opportunity", side_effect=RuntimeError("S3 down")):
            persist_opportunity(opp)

        files = list(local_dir.rglob("*.jsonl"))
        assert len(files) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Local Persistence Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestLocalPersistence:
    def test_local_file_created(self, tmp_path):
        opp = _make_opportunity("USDJPY")
        local_dir = tmp_path / "opps"

        with patch("core.opportunity.persistence._LOCAL_DIR", str(local_dir)), \
             patch("core.opportunity.persistence._write_s3_opportunity"):
            persist_opportunity(opp)

        files = list(local_dir.rglob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["symbol"] == "USDJPY"
        assert parsed["pattern"] == "HAMMER"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Record Serialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordSerialization:
    def test_record_has_opportunity_fields(self, tmp_path):
        opp = _make_opportunity("EURUSD", "ENGULFING")
        local_dir = tmp_path / "opps"

        with patch("core.opportunity.persistence._LOCAL_DIR", str(local_dir)), \
             patch("core.opportunity.persistence._write_s3_opportunity"):
            persist_opportunity(opp)

        files = list(local_dir.rglob("*.jsonl"))
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)

        # Identity
        assert "opportunity_id" in parsed
        assert "symbol" in parsed

        # Classification
        assert "pattern" in parsed
        assert parsed["pattern"] == "ENGULFING"
        assert "direction" in parsed
        assert "overall_score" in parsed

        # Metadata
        assert "schema_version" in parsed
        assert "_persisted_at" in parsed
        assert "_state_at_persist" in parsed
