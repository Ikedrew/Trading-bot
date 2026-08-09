"""Tests for Research Cycle V2 — anomaly layer, extended segmentation, dual views."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.anomaly_layer import classify_anomalies
from research_engine.v10.segmentation import build_segmentation, load_view


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def seg_result(tmp_path_factory):
    """Build segmentation V2 once for all tests in module."""
    tmp = tmp_path_factory.mktemp("seg_v2")
    src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
    if not src.exists():
        pytest.skip("Research-ready dataset not available")
    return build_segmentation(
        source_file=str(src),
        views_dir=str(tmp / "views"),
        reports_dir=str(tmp / "reports"),
    )


@pytest.fixture(scope="module")
def views_dir(tmp_path_factory, seg_result):
    """Return the views dir used by seg_result."""
    # Reconstruct from seg_result metadata path
    # seg_result writes to tmp/views — find it from tmp_path_factory
    # Actually just rebuild from the fixture chain
    return str(tmp_path_factory.getbasetemp() / "seg_v20" / "views")


@pytest.fixture(scope="module")
def cycle_result(tmp_path_factory):
    """Run full V2 cycle once for cycle-specific tests."""
    tmp = tmp_path_factory.mktemp("cycle_v2")
    import research_engine.v10.research_cycle as rc
    import research_engine.v10.segmentation as seg
    import research_engine.v10.baseline as bl

    rc._CYCLES_DIR = str(tmp / "cycles")
    bl._BASELINE_DIR = str(tmp / "cycles")
    seg._VIEWS_DIR = str(tmp / "views")
    seg._REPORTS_DIR = str(tmp / "reports")

    result = rc.run_research_cycle(compare=False)
    result["_tmp"] = str(tmp)
    return result


# ═══════════════════════════════════════════════════════════════
# SEGMENTATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestSegmentationCreatesExpectedViews:
    def test_full_raw_exists(self, seg_result):
        assert "FULL_RAW" in seg_result["view_counts"]

    def test_standard_exists(self, seg_result):
        assert "STANDARD" in seg_result["view_counts"]

    def test_anomaly_only_exists(self, seg_result):
        assert "ANOMALY_ONLY" in seg_result["view_counts"]

    def test_asset_class_views(self, seg_result):
        for view in ["FX", "INDEX", "COMMODITY"]:
            assert view in seg_result["view_counts"]

    def test_instrument_views_generated(self, seg_result):
        # Every symbol with standard trades should have a view
        for sym in seg_result.get("instrument_summary", {}).keys():
            assert sym in seg_result["view_counts"]

    def test_instrument_rankings_in_report(self, seg_result):
        assert "rankings" in seg_result
        assert "by_expectancy" in seg_result["rankings"]
        assert len(seg_result["rankings"]["by_expectancy"]) > 0


class TestNoTradesDisappear:
    def test_full_raw_equals_total(self, seg_result):
        """FULL_RAW must contain every trade from the source."""
        assert seg_result["view_counts"]["FULL_RAW"] == seg_result["total_trades"]

    def test_standard_plus_anomaly_equals_full_raw(self, seg_result):
        """STANDARD + ANOMALY_ONLY must equal FULL_RAW."""
        full_raw = seg_result["view_counts"]["FULL_RAW"]
        standard = seg_result["view_counts"]["STANDARD"]
        anomaly = seg_result["view_counts"]["ANOMALY_ONLY"]
        assert standard + anomaly == full_raw

    def test_standard_lte_full_raw(self, seg_result):
        assert seg_result["view_counts"]["STANDARD"] <= seg_result["view_counts"]["FULL_RAW"]


# ═══════════════════════════════════════════════════════════════
# ANOMALY LAYER TESTS
# ═══════════════════════════════════════════════════════════════

class TestAnomalyLayerFlagsButDoesNotDelete:
    def test_anomaly_summary_present(self, seg_result):
        assert "anomaly_summary" in seg_result

    def test_flagged_count_matches(self, seg_result):
        assert seg_result["flagged_trades"] == seg_result["view_counts"]["ANOMALY_ONLY"]

    def test_normal_count_matches(self, seg_result):
        assert seg_result["normal_trades"] == seg_result["view_counts"]["STANDARD"]

    def test_flagged_trades_have_reasons(self, seg_result):
        """Every flagged trade must have at least one anomaly reason."""
        anom_summary = seg_result.get("anomaly_summary", {})
        reason_counts = anom_summary.get("reason_counts", {})
        if seg_result["flagged_trades"] > 0:
            assert sum(reason_counts.values()) >= seg_result["flagged_trades"]

    def test_classify_preserves_all_trades(self):
        """classify_anomalies never removes a trade."""
        fake_trades = [
            {"trade_id": "t1", "realised_r": 0.5, "final_pnl": 10, "rr_ratio": 2,
             "direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09, "duration_seconds": 60, "symbol": "EURUSD"},
            {"trade_id": "t2", "realised_r": 8.0, "final_pnl": 500, "rr_ratio": 3,
             "direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09, "duration_seconds": 120, "symbol": "EURUSD"},
        ]
        result = classify_anomalies(fake_trades, output_file="NUL")
        assert result["total"] == 2
        assert result["normal_count"] + result["flagged_count"] == 2
        # Every trade still in the list
        assert len(result["trades"]) == 2


class TestAnomalyClassificationRules:
    def test_extreme_r_flagged(self):
        trades = [
            {"trade_id": "x", "realised_r": 6.0, "final_pnl": 10, "rr_ratio": 2,
             "direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09, "duration_seconds": 60, "symbol": "X"},
        ]
        result = classify_anomalies(trades, output_file="NUL")
        assert result["flagged_count"] == 1
        assert "EXTREME_R_MULTIPLE" in result["flagged"][0]["anomaly_reasons"]

    def test_normal_trade_not_flagged(self):
        trades = [
            {"trade_id": "n", "realised_r": -1.0, "final_pnl": -50, "rr_ratio": 2,
             "direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09, "duration_seconds": 300, "symbol": "N"},
        ]
        result = classify_anomalies(trades, output_file="NUL")
        assert result["normal_count"] == 1
        assert result["flagged_count"] == 0


# ═══════════════════════════════════════════════════════════════
# INSTRUMENT RANKINGS
# ═══════════════════════════════════════════════════════════════

class TestInstrumentRankings:
    def test_rankings_sorted_by_expectancy(self, seg_result):
        by_exp = seg_result["rankings"]["by_expectancy"]
        if len(by_exp) >= 2:
            assert by_exp[0]["expectancy_r"] >= by_exp[-1]["expectancy_r"]

    def test_ranking_fields_complete(self, seg_result):
        for r in seg_result["rankings"]["by_expectancy"]:
            assert "symbol" in r
            assert "count" in r
            assert "win_rate" in r
            assert "expectancy_r" in r
            assert "profit_factor" in r
            assert "total_pnl" in r
            assert "confidence" in r


# ═══════════════════════════════════════════════════════════════
# RESEARCH CYCLE V2 TESTS
# ═══════════════════════════════════════════════════════════════

class TestCycleV2Completion:
    def test_no_error(self, cycle_result):
        assert "error" not in cycle_result

    def test_version_v2(self, cycle_result):
        assert cycle_result.get("version") == "V2"

    def test_cycle_dir_exists(self, cycle_result):
        assert Path(cycle_result["cycle_dir"]).exists()

    def test_cycle_summary_md(self, cycle_result):
        assert (Path(cycle_result["cycle_dir"]) / "cycle_summary.md").exists()

    def test_anomaly_report_saved(self, cycle_result):
        assert (Path(cycle_result["cycle_dir"]) / "anomaly_report.json").exists()

    def test_instrument_rankings_saved(self, cycle_result):
        assert (Path(cycle_result["cycle_dir"]) / "instrument_rankings.json").exists()

    def test_experiments_run(self, cycle_result):
        assert len(cycle_result["experiments"]) == 10

    def test_instrument_views_in_experiments(self, cycle_result):
        """At least one experiment should have instrument-level results."""
        for exp_id, views in cycle_result["experiments"].items():
            # Should have more than just FULL/FX_ONLY/INDEX_ONLY
            if len(views) > 3:
                return  # pass
        pytest.fail("No experiment ran against instrument views")

    def test_dataset_has_anomaly_counts(self, cycle_result):
        ds = cycle_result["dataset"]
        assert "normal_trades" in ds
        assert "flagged_trades" in ds
        assert ds["normal_trades"] + ds["flagged_trades"] == ds["total_trades"]


class TestCycleV2FailureResilience:
    def test_broken_experiment_does_not_crash(self, tmp_path):
        """A broken experiment should not crash the full cycle."""
        import research_engine.v10.research_cycle as rc
        import research_engine.v10.segmentation as seg
        import research_engine.v10.baseline as bl
        from research_engine.v10 import runner

        rc._CYCLES_DIR = str(tmp_path / "cycles")
        bl._BASELINE_DIR = str(tmp_path / "cycles")
        seg._VIEWS_DIR = str(tmp_path / "views")
        seg._REPORTS_DIR = str(tmp_path / "reports")

        original = runner._EXPERIMENT_REGISTRY.copy()
        runner._EXPERIMENT_REGISTRY.clear()
        runner._EXPERIMENT_REGISTRY["E1"] = original["E1"]
        runner._EXPERIMENT_REGISTRY["BROKEN"] = "nonexistent.broken.module"

        try:
            result = rc.run_research_cycle()
            assert "error" not in result
            assert "BROKEN" in result["experiments"]
            # E1 should still succeed
            assert result["experiments"]["E1"]["FULL"] != "ERROR"
        finally:
            runner._EXPERIMENT_REGISTRY.clear()
            runner._EXPERIMENT_REGISTRY.update(original)
