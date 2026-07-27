"""
Tests for execution identity propagation on protection verification records.

Verifies that protection_verification persist_execution_result calls
include entity_id (not empty string).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.persistence.execution_result_writer import persist_execution_result


class TestProtectionVerificationEntityId:
    """Protection verification records must carry entity_id."""

    def test_entity_id_written_to_record(self, tmp_path):
        """persist_execution_result with entity_id produces record with that field."""
        with patch("core.persistence.execution_result_writer._LOCAL_DIR", str(tmp_path / "exec")):
            with patch("core.persistence.execution_result_writer._write_s3"):
                persist_execution_result(
                    symbol="GBPUSD",
                    cycle_id=100,
                    result_ok=True,
                    retcode=10009,
                    deal=12345,
                    order=67890,
                    comment="protection_verification",
                    fill_price=1.33700,
                    side="SELL",
                    volume=0.01,
                    sl=1.33775,
                    tp=1.33625,
                    pattern="TWEEZER_TOP",
                    decision_id="abc123hex",
                    correlation_id="COR-20260724-100-GBPUSD",
                    entity_id="GBPUSD_1784800000",  # The fix: entity_id provided
                    requested_sl=1.33775,
                    broker_confirmed_sl=1.33775,
                    requested_tp=1.33625,
                    broker_confirmed_tp=1.33625,
                    protection_status="VERIFIED",
                    protection_failure_reason="",
                )

        files = list((tmp_path / "exec" / "GBPUSD").glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text().strip())
        assert record["entity_id"] == "GBPUSD_1784800000"
        assert record["comment"] == "protection_verification"
        assert record["correlation_id"] == "COR-20260724-100-GBPUSD"

    def test_empty_entity_id_when_not_provided(self, tmp_path):
        """Without entity_id parameter, record gets empty string (the old bug)."""
        with patch("core.persistence.execution_result_writer._LOCAL_DIR", str(tmp_path / "exec")):
            with patch("core.persistence.execution_result_writer._write_s3"):
                persist_execution_result(
                    symbol="EURUSD",
                    cycle_id=200,
                    result_ok=True,
                    retcode=10009,
                    deal=99999,
                    order=88888,
                    comment="protection_verification",
                    # entity_id NOT passed → defaults to ""
                )

        files = list((tmp_path / "exec" / "EURUSD").glob("*.jsonl"))
        record = json.loads(files[0].read_text().strip())
        assert record["entity_id"] == ""  # Demonstrates the old behaviour

    def test_identity_continuity_between_execution_and_protection(self, tmp_path):
        """Same entity_id on both primary execution and protection verification records."""
        entity = "NZDUSD_1784900000"
        with patch("core.persistence.execution_result_writer._LOCAL_DIR", str(tmp_path / "exec")):
            with patch("core.persistence.execution_result_writer._write_s3"):
                # Primary execution record
                persist_execution_result(
                    symbol="NZDUSD", cycle_id=300, result_ok=True,
                    retcode=10009, deal=111, order=222,
                    comment="Request executed",
                    entity_id=entity,
                    correlation_id="COR-TEST",
                )
                # Protection verification record
                persist_execution_result(
                    symbol="NZDUSD", cycle_id=300, result_ok=True,
                    retcode=10009, deal=111, order=222,
                    comment="protection_verification",
                    entity_id=entity,  # Same entity_id
                    correlation_id="COR-TEST",
                    protection_status="VERIFIED",
                )

        files = list((tmp_path / "exec" / "NZDUSD").glob("*.jsonl"))
        lines = [l for l in files[0].read_text().strip().split("\n") if l]
        assert len(lines) == 2

        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        # Both share the same entity_id
        assert r1["entity_id"] == entity
        assert r2["entity_id"] == entity
        # Distinguishable by comment
        assert r1["comment"] == "Request executed"
        assert r2["comment"] == "protection_verification"
