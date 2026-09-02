"""Tests for V10 Baseline Snapshot System."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import install_fake_s3, reset_fake_s3

from research_engine.v10.baselines import (
    BaselineSnapshot, SnapshotBuilder, SnapshotRegistry, compare_snapshots,
)


# ═══════════════════════════════════════════════════════════════
# MODEL (1-3)
# ═══════════════════════════════════════════════════════════════

class TestSnapshotModel:
    def test_creation(self):
        s = BaselineSnapshot(snapshot_id="TEST_001", bot_version="V10.0")
        assert s.snapshot_id == "TEST_001"
        assert s.created_at != ""
        assert s.bot_version == "V10.0"

    def test_required_fields(self):
        s = BaselineSnapshot(snapshot_id="X")
        d = s.to_dict()
        assert "snapshot_id" in d
        assert "created_at" in d
        assert "environment" in d
        assert "configuration" in d
        assert "performance_metrics" in d
        assert "dataset_metadata" in d

    def test_missing_fields_safe(self):
        s = BaselineSnapshot(snapshot_id="EMPTY")
        d = s.to_dict()
        # Should not crash — empty dicts are fine
        assert d["configuration"] == {}
        assert d["performance_metrics"] == {}

    def test_from_dict_roundtrip(self):
        original = BaselineSnapshot(
            snapshot_id="RT_001",
            bot_version="V10.1",
            performance_metrics={"expectancy_r": -0.14, "trade_count": 94},
        )
        d = original.to_dict()
        restored = BaselineSnapshot.from_dict(d)
        assert restored.snapshot_id == "RT_001"
        assert restored.performance_metrics["expectancy_r"] == -0.14


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION (4-5)
# ═══════════════════════════════════════════════════════════════

class TestConfiguration:
    def test_configuration_captured(self):
        install_fake_s3()  # empty S3 — universe absent is fine for this check
        try:
            builder = SnapshotBuilder()
            snapshot = builder.build()
            # Should have configuration block (may be MISSING if config not importable)
            assert "configuration" in snapshot.to_dict()
        finally:
            reset_fake_s3()

    def test_missing_config_reported(self):
        """Missing configuration should report status, not crash."""
        s = BaselineSnapshot(
            snapshot_id="MISS",
            configuration={"status": "MISSING", "reason": "not available"},
        )
        assert s.configuration["status"] == "MISSING"


# ═══════════════════════════════════════════════════════════════
# PERFORMANCE (6-8)
# ═══════════════════════════════════════════════════════════════

class TestPerformance:
    def test_metrics_from_universe(self):
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        builder = SnapshotBuilder()
        snapshot = builder.build()
        perf = snapshot.performance_metrics
        assert perf["trade_count"] == 94
        assert "expectancy_r" in perf
        assert "profit_factor" in perf
        assert "net_realised_pnl" in perf

    def test_canonical_pnl_used(self):
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        builder = SnapshotBuilder()
        snapshot = builder.build()
        # net_realised_pnl should match governance-validated canonical PnL
        assert snapshot.performance_metrics["net_realised_pnl"] != 0

    def test_governance_definitions_reused(self):
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        builder = SnapshotBuilder()
        snapshot = builder.build()
        # Uses compute_metrics from base.py
        assert "win_rate" in snapshot.performance_metrics


# ═══════════════════════════════════════════════════════════════
# DATASET IDENTITY (9-11)
# ═══════════════════════════════════════════════════════════════

class TestDatasetIdentity:
    def test_metadata_stored(self):
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        builder = SnapshotBuilder()
        snapshot = builder.build()
        ds = snapshot.dataset_metadata
        assert "dataset" in ds
        assert "records" in ds
        assert ds["records"] == 94

    def test_hash_generated(self):
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        builder = SnapshotBuilder()
        snapshot = builder.build()
        assert len(snapshot.dataset_metadata["hash"]) > 0

    def test_hash_changes_detected(self):
        # Two DIFFERENT research_universe artifact contents → different hashes.
        # dataset_metadata no longer carries file_size_bytes/last_modified (S3
        # artifact has no local file identity); only dataset/records/hash exist.
        fake1 = install_fake_s3()
        fake1.add_artifact("research_universe", [{"trade_id": "1"}])
        try:
            b1 = SnapshotBuilder().build()
        finally:
            reset_fake_s3()

        fake2 = install_fake_s3()
        fake2.add_artifact("research_universe", [{"trade_id": "2"}])
        try:
            b2 = SnapshotBuilder().build()
        finally:
            reset_fake_s3()

        assert b1.dataset_metadata["dataset"] == "research_universe"
        assert b2.dataset_metadata["dataset"] == "research_universe"
        assert b1.dataset_metadata["records"] == 1
        assert b2.dataset_metadata["records"] == 1
        assert b1.dataset_metadata["hash"] != b2.dataset_metadata["hash"]


# ═══════════════════════════════════════════════════════════════
# REGISTRY (12-14)
# ═══════════════════════════════════════════════════════════════

class TestSnapshotRegistry:
    def test_save(self, tmp_path):
        reg = SnapshotRegistry(baselines_dir=str(tmp_path))
        s = BaselineSnapshot(snapshot_id="SAVE_001", bot_version="V10")
        path = reg.save(s)
        assert Path(path).exists()

    def test_load(self, tmp_path):
        reg = SnapshotRegistry(baselines_dir=str(tmp_path))
        s = BaselineSnapshot(snapshot_id="LOAD_001", performance_metrics={"x": 42})
        reg.save(s)
        loaded = reg.load("LOAD_001")
        assert loaded is not None
        assert loaded.performance_metrics["x"] == 42

    def test_latest(self, tmp_path):
        reg = SnapshotRegistry(baselines_dir=str(tmp_path))
        reg.save(BaselineSnapshot(snapshot_id="A_FIRST"))
        reg.save(BaselineSnapshot(snapshot_id="B_SECOND"))
        latest = reg.latest()
        assert latest is not None
        assert latest.snapshot_id == "B_SECOND"

    def test_list_snapshots(self, tmp_path):
        reg = SnapshotRegistry(baselines_dir=str(tmp_path))
        reg.save(BaselineSnapshot(snapshot_id="S1"))
        reg.save(BaselineSnapshot(snapshot_id="S2"))
        ids = reg.list_snapshots()
        assert len(ids) == 2
        assert "S1" in ids
        assert "S2" in ids


# ═══════════════════════════════════════════════════════════════
# COMPARISON (15-16)
# ═══════════════════════════════════════════════════════════════

class TestSnapshotComparison:
    def test_two_snapshots_compare(self):
        b = BaselineSnapshot(
            snapshot_id="BASE",
            performance_metrics={"expectancy_r": -0.14, "win_rate": 0.36, "profit_factor": 1.2, "trade_count": 94},
        )
        c = BaselineSnapshot(
            snapshot_id="CAND",
            performance_metrics={"expectancy_r": 0.05, "win_rate": 0.42, "profit_factor": 1.6, "trade_count": 120},
        )
        result = compare_snapshots(b, c)
        assert result["baseline_id"] == "BASE"
        assert result["candidate_id"] == "CAND"
        assert result["performance_delta"]["expectancy_r"]["change"] == 0.19

    def test_differences_calculated(self):
        b = BaselineSnapshot(
            snapshot_id="B1",
            performance_metrics={"expectancy_r": -0.10, "trade_count": 50},
            configuration={"max_positions": 1},
        )
        c = BaselineSnapshot(
            snapshot_id="C1",
            performance_metrics={"expectancy_r": 0.10, "trade_count": 80},
            configuration={"max_positions": 2},
        )
        result = compare_snapshots(b, c)
        assert result["performance_delta"]["expectancy_r"]["change"] == 0.2
        assert len(result["configuration_changes"]) >= 1
        assert result["summary"].startswith("IMPROVED")


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN LINK (17-18)
# ═══════════════════════════════════════════════════════════════

class TestCampaignLink:
    def test_campaign_can_reference_baseline(self):
        """Campaigns should be able to store baseline_id."""
        from research_engine.v10.campaigns.models import CampaignResult
        result = CampaignResult(campaign_id="FX_OPT_V1")
        # CampaignResult can carry baseline in filters or as metadata
        result.filters_applied["baseline_id"] = "V10_BASELINE_20260807"
        assert result.filters_applied["baseline_id"] == "V10_BASELINE_20260807"

    def test_report_can_include_baseline(self, tmp_path):
        from research_engine.v10.campaigns.models import CampaignResult
        from research_engine.v10.campaigns.campaign_report import save_campaign_report
        result = CampaignResult(
            campaign_id="TEST_BL",
            filters_applied={"baseline_id": "V10_BASELINE_20260807"},
        )
        paths = save_campaign_report(result, reports_dir=str(tmp_path))
        data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert data["filters_applied"]["baseline_id"] == "V10_BASELINE_20260807"
