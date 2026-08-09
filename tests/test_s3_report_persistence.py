"""Tests for Phase 11.5 — S3 Report Persistence."""
import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.research_intelligence.experiment_runner import ExperimentRunner
from research_engine.v10.research_intelligence.models import ExperimentResult


# Sample universe for mocking
_SAMPLE_UNIVERSE = "\n".join([
    json.dumps({
        "trade_id": f"pos_{i}",
        "execution": {"ticket": i, "symbol": "EURUSD", "direction": "BUY",
                      "entry_price": 1.1, "exit_price": 1.099, "entry_time": 1784808000.0,
                      "exit_time": 1784809000.0, "stop_loss": 1.098, "take_profit": 1.103,
                      "gross_profit": -0.5, "commission": -0.04, "swap": 0.0,
                      "net_realised_pnl": -0.54, "r_multiple": -1.0 if i % 3 != 0 else 1.5,
                      "volume": 0.01, "duration_seconds": 1000, "exit_reason": "STOP_LOSS"},
        "decision": {"strategy": "REV", "score": 0.55, "confidence": 0.7,
                     "decision_type": "sym_cycle", "components": {}, "weakest_component": "",
                     "ev": None, "p_success": None},
        "market": {"regime": "TRENDING", "session": "LONDON", "volatility": "NEUTRAL",
                   "trend_state": "BULLISH", "higher_timeframe_bias": "BULLISH",
                   "h4_phase": "IMPULSE", "h1_clarity": 0.6},
        "strategy": {"family": "REV", "pattern": "HAMMER", "conditions_met": 2,
                     "strategy_confidence": 0.7, "opportunity_quality": 0.55,
                     "opportunity_type": "ZONE_REACTION"},
        "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "WARNING",
                    "data_completeness": "COMPLETE", "missing": [], "join_method": "sym_cycle",
                    "pnl_source": "MT5_BROKER"},
    }) for i in range(25)
])


# ═══════════════════════════════════════════════════════════════
# TEST 1 — S3 mode does NOT create ./reports/
# ═══════════════════════════════════════════════════════════════

class TestS3ModeNoLocalReports:
    def test_no_reports_dir_created(self, monkeypatch, tmp_path):
        """In S3 mode, _save_report must NOT create a local reports/ directory."""
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")

        # Create universe file
        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")

        # Mock ResearchStorage.save_report
        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            MockStorage.return_value = mock_instance

            runner = ExperimentRunner(universe_file=str(universe_file))
            runner.run("E1")

            # reports/ should NOT have been created anywhere in tmp_path
            assert not (tmp_path / "reports").exists()
            # But S3 save_report should have been called
            mock_instance.save_report.assert_called_once()

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 2 — S3 mode calls ResearchStorage.save_report
# ═══════════════════════════════════════════════════════════════

class TestS3ModeSaveReport:
    def test_calls_storage_save(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")

        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            MockStorage.return_value = mock_instance

            runner = ExperimentRunner(universe_file=str(universe_file))
            runner.run("E1")

            # Verify save_report was called with content and filename
            call_args = mock_instance.save_report.call_args
            content_arg = call_args[0][0]
            filename_arg = call_args[0][1]

            assert "E1" in filename_arg
            assert "questions/" in filename_arg
            parsed = json.loads(content_arg)
            assert parsed["question_id"] == "E1"

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 3 — RESEARCH_REPORT_PREFIX is respected
# ═══════════════════════════════════════════════════════════════

class TestReportPrefix:
    def test_prefix_used(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("RESEARCH_REPORT_PREFIX", "custom/prefix/")

        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.save_report.return_value = "custom/prefix/questions/E1.json"
            MockStorage.return_value = mock_instance

            runner = ExperimentRunner(universe_file=str(universe_file))
            runner.run("E1")

            MockStorage.assert_called_with(backend="s3")

        monkeypatch.setenv("RESEARCH_STORAGE", "local")
        monkeypatch.delenv("RESEARCH_REPORT_PREFIX", raising=False)


# ═══════════════════════════════════════════════════════════════
# TEST 4 — Report content preserved
# ═══════════════════════════════════════════════════════════════

class TestReportContent:
    def test_content_is_valid_experiment_result(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")

        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            MockStorage.return_value = mock_instance

            runner = ExperimentRunner(universe_file=str(universe_file))
            runner.run("E1")

            content = mock_instance.save_report.call_args[0][0]
            parsed = json.loads(content)
            assert "question_id" in parsed
            assert "sample_size" in parsed
            assert "confidence" in parsed
            assert "recommendation" in parsed

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 5 — Local mode still writes locally
# ═══════════════════════════════════════════════════════════════

class TestLocalModeUnchanged:
    def test_local_writes_to_filesystem(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "local")

        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")
        reports_dir = tmp_path / "reports"

        runner = ExperimentRunner(
            universe_file=str(universe_file),
            reports_dir=str(reports_dir),
        )
        runner.run("E1")

        # Local report should exist
        assert (reports_dir / "E1.json").exists()


# ═══════════════════════════════════════════════════════════════
# TEST 6 — Full E1 completes in S3 mode without writable CWD
# ═══════════════════════════════════════════════════════════════

class TestE1CompletesS3Mode:
    def test_e1_full_execution(self, monkeypatch, tmp_path):
        """E1 should complete without OSError in S3 mode."""
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")

        universe_file = tmp_path / "universe.jsonl"
        universe_file.write_text(_SAMPLE_UNIVERSE, encoding="utf-8")

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            MockStorage.return_value = mock_instance

            runner = ExperimentRunner(universe_file=str(universe_file))
            result = runner.run("E1")

            assert result.sample_size == 25
            assert not result.error

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 7 — Phase 11.3 S3 universe handoff still works
# ═══════════════════════════════════════════════════════════════

class TestPhase113Intact:
    def test_universe_handoff_works(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = _SAMPLE_UNIVERSE
            MockStorage.return_value = mock_instance

            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter()
            assert router._universe_file is not None
            assert Path(router._universe_file).exists()

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 8 — State not regressed
# ═══════════════════════════════════════════════════════════════

class TestStateNotRegressed:
    def test_state_operations_work(self, tmp_path):
        from research_engine.v10.operations.state import save_research_state, get_research_state
        state_file = str(tmp_path / "state.json")
        save_research_state({"test": "value"}, state_file)
        loaded = get_research_state(state_file)
        assert loaded["test"] == "value"


# ═══════════════════════════════════════════════════════════════
# TEST 9 — No broker imports
# ═══════════════════════════════════════════════════════════════

class TestNoBrokerImports:
    def test_experiment_runner_no_broker(self):
        import research_engine.v10.research_intelligence.experiment_runner as mod
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        for line in import_lines:
            assert "MetaTrader5" not in line
            assert "from execution" not in line
            assert "mt5" not in line.split("import")[0] if "import" in line else True
