"""
Final S3 Mirrors — Tests for protection_audit, risk_deviation, quarantine.

Validates S3 path generation, schema versioning, failure handling for each.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# PROTECTION AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectionAuditS3:
    def test_s3_key_format(self):
        from core.protection_verification import _write_s3_protection_audit
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_protection_audit("EURUSD", "2026-07-25", '{"test":1}\n')
        key = mock_s3.put_object.call_args[1]["Key"]
        assert key == "supporting/protection_audit/schema_version=protection_audit_v1/symbol=EURUSD/date=2026-07-25/part-000.jsonl"

    def test_schema_version_constant(self):
        from core.protection_verification import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "protection_audit_v1"

    def test_s3_failure_does_not_raise(self):
        from core.protection_verification import _write_s3_protection_audit
        _write_s3_protection_audit("EURUSD", "2026-07-25", '{"test":1}\n')

    def test_different_symbols_different_keys(self):
        from core.protection_verification import _write_s3_protection_audit
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_protection_audit("EURUSD", "2026-07-25", '{"a":1}\n')
            _write_s3_protection_audit("GBPUSD", "2026-07-25", '{"b":2}\n')
        keys = [c[1]["Key"] for c in mock_s3.put_object.call_args_list]
        assert "symbol=EURUSD" in keys[0]
        assert "symbol=GBPUSD" in keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# RISK DEVIATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskDeviationS3:
    def test_s3_key_format(self):
        from core.risk_deviation import _write_s3_risk_deviation
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_risk_deviation("USDJPY", "2026-07-25", '{"test":1}\n')
        key = mock_s3.put_object.call_args[1]["Key"]
        assert key == "supporting/risk_deviation/schema_version=risk_deviation_v1/symbol=USDJPY/date=2026-07-25/part-000.jsonl"

    def test_schema_version_constant(self):
        from core.risk_deviation import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "risk_deviation_v1"

    def test_s3_failure_does_not_raise(self):
        from core.risk_deviation import _write_s3_risk_deviation
        _write_s3_risk_deviation("EURUSD", "2026-07-25", '{"test":1}\n')

    def test_different_symbols(self):
        from core.risk_deviation import _write_s3_risk_deviation
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_risk_deviation("EURUSD", "2026-07-25", '{"a":1}\n')
            _write_s3_risk_deviation("AUDUSD", "2026-07-25", '{"b":2}\n')
        keys = [c[1]["Key"] for c in mock_s3.put_object.call_args_list]
        assert "symbol=EURUSD" in keys[0]
        assert "symbol=AUDUSD" in keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# QUARANTINE
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuarantineS3:
    def test_s3_key_format(self):
        from core.contracts.quarantine import _write_s3_quarantine
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_quarantine("trade_truth", "2026-07-25", '{"test":1}\n')
        key = mock_s3.put_object.call_args[1]["Key"]
        assert key == "projections/quarantine/schema_version=quarantine_v1/layer=trade_truth/date=2026-07-25/part-000.jsonl"

    def test_schema_version_constant(self):
        from core.contracts.quarantine import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "quarantine_v1"

    def test_s3_failure_does_not_raise(self):
        from core.contracts.quarantine import _write_s3_quarantine
        _write_s3_quarantine("trade_truth", "2026-07-25", '{"test":1}\n')

    def test_different_layers_different_keys(self):
        from core.contracts.quarantine import _write_s3_quarantine
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            _write_s3_quarantine("trade_truth", "2026-07-25", '{"a":1}\n')
            _write_s3_quarantine("shadow_trades", "2026-07-25", '{"b":2}\n')
        keys = [c[1]["Key"] for c in mock_s3.put_object.call_args_list]
        assert "layer=trade_truth" in keys[0]
        assert "layer=shadow_trades" in keys[1]
