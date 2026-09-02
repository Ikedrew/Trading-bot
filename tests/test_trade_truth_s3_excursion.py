"""
Verify the V1 trade_truth S3 object contains the realised excursion fields.

The S3 writer uploads the EXACT bytes produced by the single json.dumps(record)
used for the local write — no projection/whitelist/recompute. These tests prove
the five excursion fields survive into the S3 payload under
``outcome.{max_favourable_price,max_adverse_price,mfe_r,mae_r,excursion_provenance}``
with correct null (unknown → null) vs measured-zero (0.0) semantics, and that
the local and S3 representations agree byte-for-byte.

Persistence verification only — no trading logic exercised, no live S3 call.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_truth import build_trade_truth, persist_trade_truth


def _record(*, mfe_price=1.1090, mae_price=1.0975, mfe_r=1.8, mae_r=0.5,
            provenance="full_lifecycle", commission=None, swap=0.0, net_profit=None):
    return build_trade_truth(
        trade_id="pos_4001",
        correlation_id="COR-20260101-1-EURUSD-ABCD",
        canonical_opportunity_id="EURUSD*1785205500*ENGULFING_BULLISH",
        symbol="EURUSD",
        entry_fill_price=1.1000,
        exit_fill_price=1.1050,
        volume_executed=0.10,
        entry_timestamp_broker=1785205500.0,
        exit_timestamp_broker=1785205500.0 + 3600,
        pnl_realised=50.0,
        r_multiple_realised=1.0,
        commission=commission,
        swap=swap,
        net_profit=net_profit,
        exit_reason="take_profit_hit",
        max_favourable_price=mfe_price,
        max_adverse_price=mae_price,
        mfe_r=mfe_r,
        mae_r=mae_r,
        excursion_provenance=provenance,
    )


def _run_persist_and_capture_s3(record, tmp_path):
    """Run the REAL persist_trade_truth path against the production local dir
    (patched to tmp) with the S3 client mocked. Returns (local_line, s3_body)."""
    captured = {}

    def _fake_client(*a, **k):
        client = MagicMock()

        def _get_object(**kw):
            raise Exception("NoSuchKey")  # new object → body = line

        def _put_object(**kw):
            captured["Bucket"] = kw["Bucket"]
            captured["Key"] = kw["Key"]
            captured["Body"] = kw["Body"]

        client.get_object.side_effect = _get_object
        client.put_object.side_effect = _put_object
        return client

    prod_dir = tmp_path / "logs" / "trade_truth"
    # The S3 mirror only fires when local_dir resolves to the production
    # logs/trade_truth path AND EVENT_STREAM_S3_MIRROR is enabled. We chdir into
    # tmp so the relative "logs/trade_truth" resolves under tmp and matches the
    # module's production-path comparison; boto3 + the config flag are patched.
    import os
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("boto3.client", _fake_client), \
             patch("core.config.EVENT_STREAM_S3_MIRROR", True):
            ok = persist_trade_truth(record, local_dir="logs/trade_truth")
    finally:
        os.chdir(old)

    assert ok is True
    # Local line
    sym = record["identity"]["symbol"]
    from datetime import datetime, timezone
    date_str = datetime.fromtimestamp(
        record["timestamps"]["exit_timestamp_broker"], tz=timezone.utc
    ).strftime("%Y-%m-%d")
    local_file = prod_dir / sym / f"{date_str}.jsonl"
    local_line = local_file.read_text(encoding="utf-8").strip()
    return local_line, captured


# ─── Full-lifecycle values reach S3 exactly ───────────────────────────────────

def test_excursion_fields_reach_s3_exactly(tmp_path):
    rec = _record()
    local_line, s3 = _run_persist_and_capture_s3(rec, tmp_path)

    # S3 received a body (put_object was called with the record bytes).
    assert "Body" in s3, "S3 put_object was not called"
    s3_text = s3["Body"].decode("utf-8") if isinstance(s3["Body"], bytes) else s3["Body"]
    s3_obj = json.loads(s3_text.strip())
    out = s3_obj["outcome"]

    assert out["max_favourable_price"] == 1.1090
    assert out["max_adverse_price"] == 1.0975
    assert out["mfe_r"] == 1.8
    assert out["mae_r"] == 0.5
    assert out["excursion_provenance"] == "full_lifecycle"

    # Local and S3 representations agree byte-for-byte (single serialization).
    assert local_line == s3_text.strip()

    # Canonical V1 dataset path — no v2 / no separate MAE-MFE prefix.
    assert s3["Bucket"] == "trading-bot-v10-data"
    assert s3["Key"].startswith("core/trade_truth/schema_version=trade_truth_v1/")
    assert "mae" not in s3["Key"].split("/")[0:2]
    assert s3_obj["schema_version"] == "trade_truth_v1"


# ─── Unknown → null (not 0.0) survives to S3 ──────────────────────────────────

def test_unknown_excursion_serializes_as_null_in_s3(tmp_path):
    rec = _record(mfe_price=None, mae_price=None, mfe_r=None, mae_r=None,
                  provenance="recovery_seeded", commission=None, swap=None, net_profit=None)
    _, s3 = _run_persist_and_capture_s3(rec, tmp_path)
    s3_text = s3["Body"].decode("utf-8") if isinstance(s3["Body"], bytes) else s3["Body"]

    # Raw JSON must contain null, never a coerced 0.0 for the unknown fields.
    obj = json.loads(s3_text.strip())
    out = obj["outcome"]
    assert out["mfe_r"] is None
    assert out["mae_r"] is None
    assert out["max_favourable_price"] is None
    assert out["max_adverse_price"] is None
    assert out["commission"] is None
    assert out["swap"] is None
    assert out["net_profit"] is None
    assert out["excursion_provenance"] == "recovery_seeded"
    # Prove the literal token is null (unknown), not 0.0.
    assert '"mae_r":null' in s3_text or '"mae_r": null' in s3_text


# ─── Measured zero → 0.0 preserved (distinct from unknown) ────────────────────

def test_measured_zero_mae_preserved_in_s3(tmp_path):
    rec = _record(mae_r=0.0, mae_price=1.1000, swap=0.0)  # observed, never adverse
    _, s3 = _run_persist_and_capture_s3(rec, tmp_path)
    s3_text = s3["Body"].decode("utf-8") if isinstance(s3["Body"], bytes) else s3["Body"]
    out = json.loads(s3_text.strip())["outcome"]

    assert out["mae_r"] == 0.0            # numeric zero, not null
    assert out["mae_r"] is not None
    assert out["swap"] == 0.0             # measured-zero cost preserved
    # unknown != measured zero, on the same record.
    assert out["commission"] is None      # commission left unknown by _record default
