"""Tests for Research Cycle Orchestrator and Baseline Tracking."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.baseline import save_baseline, load_baseline, compare_baselines


@pytest.fixture(scope="module")
def cycle_result(tmp_path_factory):
    """Run ONE cycle to shared temp dir — reused across all tests in module."""
    tmp = tmp_path_factory.mktemp("cycle")

    # Patch module-level constants before import
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


class TestCycleCreatesOutput:
    def test_no_error(self, cycle_result):
        assert "error" not in cycle_result

    def test_cycle_creates_directory(self, cycle_result):
        assert Path(cycle_result["cycle_dir"]).exists()

    def test_metadata_saved(self, cycle_result):
        cycle_dir = Path(cycle_result["cycle_dir"])
        assert (cycle_dir / "cycle_metadata.json").exists()
        meta = json.loads((cycle_dir / "cycle_metadata.json").read_text(encoding="utf-8"))
        assert "cycle_id" in meta
        assert "experiments" in meta

    def test_dataset_summary_saved(self, cycle_result):
        cycle_dir = Path(cycle_result["cycle_dir"])
        assert (cycle_dir / "dataset_summary.json").exists()

    def test_segmentation_report_saved(self, cycle_result):
        cycle_dir = Path(cycle_result["cycle_dir"])
        assert (cycle_dir / "segmentation_report.json").exists()

    def test_cycle_summary_md_created(self, cycle_result):
        cycle_dir = Path(cycle_result["cycle_dir"])
        assert (cycle_dir / "cycle_summary.md").exists()


class TestExperimentExecution:
    def test_experiments_run(self, cycle_result):
        assert "experiments" in cycle_result
        assert len(cycle_result["experiments"]) == 10

    def test_individual_experiment_files(self, cycle_result):
        exp_dir = Path(cycle_result["cycle_dir"]) / "experiments"
        assert exp_dir.exists()
        for eid in ["E1", "E2", "M1", "D1", "D2", "D3", "OQ1", "OQ2", "R1", "R2"]:
            assert (exp_dir / f"{eid}.json").exists()

    def test_experiments_have_conclusions(self, cycle_result):
        for exp_id, views in cycle_result["experiments"].items():
            assert "FULL" in views, f"{exp_id} missing FULL view"


class TestBaselineTracking:
    def test_baseline_saved_after_cycle(self, cycle_result):
        import research_engine.v10.baseline as bl
        bl._BASELINE_DIR = str(Path(cycle_result["_tmp"]) / "cycles")
        baseline = load_baseline()
        assert baseline is not None
        assert "cycle_id" in baseline
        assert "trade_count" in baseline

    def test_no_previous_baseline_comparison(self, cycle_result):
        import research_engine.v10.baseline as bl
        bl._BASELINE_DIR = str(Path(cycle_result["_tmp"]) / "cycles")
        comparison = compare_baselines()
        # Only one cycle so far
        assert comparison["status"] == "NO_PREVIOUS"


class TestBaselineComparison:
    def test_comparison_after_two_baselines(self, tmp_path):
        import research_engine.v10.baseline as bl
        bl._BASELINE_DIR = str(tmp_path)

        save_baseline({
            "cycle_id": "2026-07-cycle-01",
            "trade_count": 80,
            "expectancy_r": -0.20,
            "win_rate": 0.30,
            "profit_factor": 0.8,
            "experiments": {"E1": "INCONCLUSIVE", "R1": "STOPS_NEED_REVIEW"},
        })
        save_baseline({
            "cycle_id": "2026-08-cycle-01",
            "trade_count": 94,
            "expectancy_r": 0.05,
            "win_rate": 0.42,
            "profit_factor": 1.5,
            "experiments": {"E1": "POSITIVE_EXPECTANCY", "R1": "STOP_MODEL_IMPROVED"},
        })
        comparison = compare_baselines()
        assert comparison["status"] == "IMPROVED"
        assert "trade_count" in comparison["changes"]
        assert "experiment_changes" in comparison["changes"]


class TestFailureResilience:
    def test_failed_experiment_does_not_crash_cycle(self, tmp_path):
        """If one experiment throws, the cycle should still complete."""
        import research_engine.v10.research_cycle as rc
        import research_engine.v10.segmentation as seg
        import research_engine.v10.baseline as bl
        from research_engine.v10 import runner

        rc._CYCLES_DIR = str(tmp_path / "cycles")
        bl._BASELINE_DIR = str(tmp_path / "cycles")
        seg._VIEWS_DIR = str(tmp_path / "views")
        seg._REPORTS_DIR = str(tmp_path / "reports")

        original_registry = runner._EXPERIMENT_REGISTRY.copy()
        # Replace all real experiments with a broken one + one real one for speed
        runner._EXPERIMENT_REGISTRY.clear()
        runner._EXPERIMENT_REGISTRY["E1"] = original_registry["E1"]
        runner._EXPERIMENT_REGISTRY["BROKEN"] = "nonexistent.module.that.does.not.exist"

        try:
            result = rc.run_research_cycle()
            assert "error" not in result
            # BROKEN should have error conclusion
            assert "BROKEN" in result["experiments"]
            broken_full = result["experiments"]["BROKEN"].get("FULL", "")
            assert broken_full == "ERROR" or "error" in str(broken_full).lower() or broken_full == "?"
            # E1 should still work
            assert result["experiments"]["E1"]["FULL"] != "ERROR"
        finally:
            runner._EXPERIMENT_REGISTRY.clear()
            runner._EXPERIMENT_REGISTRY.update(original_registry)
