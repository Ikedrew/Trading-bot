"""
Portfolio Shadow S3 Persistence — Tests.

Validates:
    1. S3 path generation (Hive-compatible partitioning)
    2. Schema version injected into records
    3. Partition correctness (date-based)
    4. S3 failure does not break portfolio processing
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

from core.portfolio_ranking.shadow_comparison import (
    ShadowComparison,
    persist_shadow_comparison,
    _write_s3_portfolio_shadow,
    _SCHEMA_VERSION,
    _S3_BUCKET,
    _S3_PREFIX,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_comparison(agreement: bool = False, candidates: int = 3) -> ShadowComparison:
    return ShadowComparison(
        cycle_id=100,
        runtime_session_id="session_test",
        compared_at_utc="2026-07-25T12:00:00.000Z",
        actual_executed_symbols=["EURUSD"],
        actual_execution_count=1,
        ranking_selected_symbol="GBPUSD",
        ranking_selected_rank_score=0.85,
        agreement=agreement,
        disagreement_type="WRONG_SYMBOL" if not agreement else "",
        disagreement_detail="Executed EURUSD but ranking recommends GBPUSD" if not agreement else "",
        total_candidates=candidates,
        eligible_candidates=2,
        outranked_symbols=["EURUSD"] if not agreement else [],
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
            _write_s3_portfolio_shadow("2026-07-25", '{"test": true}\n')

        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args[1]
        expected_key = "portfolio_shadow/schema_version=portfolio_shadow_v1/date=2026-07-25/part-000.jsonl"
        assert call_kwargs["Key"] == expected_key
        assert call_kwargs["Bucket"] == "trading-bot-data-mk1"

    def test_constants(self):
        assert _S3_BUCKET == "trading-bot-data-mk1"
        assert _S3_PREFIX == "portfolio_shadow"
        assert _SCHEMA_VERSION == "portfolio_shadow_v1"

    def test_no_symbol_partition(self):
        """Portfolio shadow is cross-symbol — no symbol partition key."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_portfolio_shadow("2026-07-25", '{"test": true}\n')

        key = mock_s3.put_object.call_args[1]["Key"]
        assert "symbol=" not in key


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Schema Version in Record
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    def test_persisted_record_has_schema_version(self, tmp_path):
        comparison = _make_comparison(agreement=False, candidates=3)

        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path)), \
             patch("core.portfolio_ranking.shadow_comparison._write_s3_portfolio_shadow"):
            persist_shadow_comparison(comparison)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["schema_version"] == "portfolio_shadow_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Partition Correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartitioning:
    def test_different_dates_different_keys(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_portfolio_shadow("2026-07-24", '{"a":1}\n')
            _write_s3_portfolio_shadow("2026-07-25", '{"b":2}\n')

        calls = mock_s3.put_object.call_args_list
        keys = [c[1]["Key"] for c in calls]
        assert "date=2026-07-24" in keys[0]
        assert "date=2026-07-25" in keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Failure Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailureHandling:
    def test_s3_failure_does_not_raise(self):
        _write_s3_portfolio_shadow("2026-07-25", '{"test":true}\n')

    def test_persist_succeeds_when_s3_fails(self, tmp_path):
        comparison = _make_comparison(agreement=False, candidates=3)

        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path)), \
             patch("core.portfolio_ranking.shadow_comparison._write_s3_portfolio_shadow", side_effect=RuntimeError("S3 down")):
            persist_shadow_comparison(comparison)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Local Persistence Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestLocalPersistence:
    def test_local_file_created_on_disagreement(self, tmp_path):
        comparison = _make_comparison(agreement=False, candidates=3)

        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path)), \
             patch("core.portfolio_ranking.shadow_comparison._write_s3_portfolio_shadow"):
            persist_shadow_comparison(comparison)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1

    def test_agreement_with_few_candidates_not_persisted(self, tmp_path):
        """Agreement with <=1 candidates is not persisted (noise reduction)."""
        comparison = _make_comparison(agreement=True, candidates=1)

        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path)), \
             patch("core.portfolio_ranking.shadow_comparison._write_s3_portfolio_shadow"):
            persist_shadow_comparison(comparison)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 0  # Not persisted


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Record Content
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordContent:
    def test_record_has_required_fields(self, tmp_path):
        comparison = _make_comparison(agreement=False, candidates=3)

        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path)), \
             patch("core.portfolio_ranking.shadow_comparison._write_s3_portfolio_shadow"):
            persist_shadow_comparison(comparison)

        files = list(tmp_path.glob("*.jsonl"))
        content = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)

        # Identity
        assert "cycle_id" in parsed
        assert "compared_at_utc" in parsed
        assert "schema_version" in parsed

        # Portfolio state
        assert "actual_executed_symbols" in parsed
        assert "actual_execution_count" in parsed

        # Decision context
        assert "ranking_selected_symbol" in parsed
        assert "agreement" in parsed
        assert "disagreement_type" in parsed
        assert "total_candidates" in parsed
        assert "eligible_candidates" in parsed
