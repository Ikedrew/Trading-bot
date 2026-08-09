"""Tests for Phase 11.3 — S3 Research Data Handoff."""
import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# Sample universe content for mocking
_SAMPLE_UNIVERSE = "\n".join([
    json.dumps({
        "trade_id": f"pos_{i}",
        "execution": {"ticket": i, "symbol": "EURUSD", "direction": "BUY",
                      "entry_price": 1.1, "exit_price": 1.099, "entry_time": 1784808000.0,
                      "exit_time": 1784809000.0, "stop_loss": 1.098, "take_profit": 1.103,
                      "gross_profit": -0.5, "commission": -0.04, "swap": 0.0,
                      "net_realised_pnl": -0.54, "r_multiple": -1.0,
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
# TEST 1 — S3 mode downloads universe
# ═══════════════════════════════════════════════════════════════

class TestS3ModeDownloads:
    def test_s3_universe_obtained(self, monkeypatch, tmp_path):
        """When RESEARCH_STORAGE=s3, universe is fetched from S3 via ResearchStorage."""
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("RESEARCH_BUCKET", "test-bucket")
        monkeypatch.setenv("RESEARCH_UNIVERSE_KEY", "data/research/universe.jsonl")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = _SAMPLE_UNIVERSE
            MockStorage.return_value = mock_instance

            # Reimport to pick up env changes
            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter()

            assert router._universe_file is not None
            assert Path(router._universe_file).exists()
            # Verify content was written
            content = Path(router._universe_file).read_text(encoding="utf-8")
            assert len(content.splitlines()) == 25

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 2 — ExperimentRunner receives valid path
# ═══════════════════════════════════════════════════════════════

class TestExperimentRunnerReceivesPath:
    def test_runner_gets_temp_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = _SAMPLE_UNIVERSE
            MockStorage.return_value = mock_instance

            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter()

            # The universe_file should be a real path, not None
            assert router._universe_file is not None
            assert router._universe_file != "data/research/research_universe.jsonl"
            assert str(tmp_path) in router._universe_file

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 3 — Local mode remains unchanged
# ═══════════════════════════════════════════════════════════════

class TestLocalModeUnchanged:
    def test_local_uses_default(self, monkeypatch):
        monkeypatch.setenv("RESEARCH_STORAGE", "local")
        from research_engine.v10.operations.router import ResearchRouter
        router = ResearchRouter()
        # Should be None (let downstream use their defaults)
        assert router._universe_file is None

    def test_no_s3_call_in_local_mode(self, monkeypatch):
        monkeypatch.setenv("RESEARCH_STORAGE", "local")
        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter()
            MockStorage.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# TEST 4 — S3 failure is surfaced
# ═══════════════════════════════════════════════════════════════

class TestS3FailureSurfaced:
    def test_empty_s3_response_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = ""  # S3 failure returns empty
            MockStorage.return_value = mock_instance

            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter.create()

            # Should have captured the error
            assert router._init_error is not None
            assert "could not be loaded" in router._init_error

            # Execute should return the error, NOT "No trades match"
            result = router.execute({"action": "run_question", "question_id": "E1"})
            assert "error" in result
            assert "infrastructure" in result["error"].lower() or "could not" in result["error"].lower()

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 5 — Empty/invalid dataset handling
# ═══════════════════════════════════════════════════════════════

class TestInvalidDataset:
    def test_empty_jsonl_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = "\n\n\n"  # Whitespace only
            MockStorage.return_value = mock_instance

            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter.create()
            assert router._init_error is not None
            assert "empty" in router._init_error.lower()

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 6 — Dataset immutability
# ═══════════════════════════════════════════════════════════════

class TestDatasetImmutability:
    def test_s3_not_written_to(self, monkeypatch, tmp_path):
        """Research execution must never write to the source S3 universe."""
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("LAMBDA_TASK_ROOT_TMP", str(tmp_path))

        with patch("research_engine.v10.operations.storage.ResearchStorage") as MockStorage:
            mock_instance = MagicMock()
            mock_instance.load_universe.return_value = _SAMPLE_UNIVERSE
            MockStorage.return_value = mock_instance

            from research_engine.v10.operations.router import ResearchRouter
            router = ResearchRouter()
            router.execute({"action": "run_question", "question_id": "E1"})

            # The S3 storage's save methods should NOT have been called for universe
            # (save_report might be called, but load_universe should be read-only)
            # Check that no put_object style call happened for the universe key
            for call in mock_instance.method_calls:
                name = call[0]
                assert "save_universe" not in name
                assert "write_universe" not in name

        monkeypatch.setenv("RESEARCH_STORAGE", "local")


# ═══════════════════════════════════════════════════════════════
# TEST 7 — Lambda isolation
# ═══════════════════════════════════════════════════════════════

class TestLambdaIsolation:
    def test_router_no_broker_imports(self):
        import research_engine.v10.operations.router as mod
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        banned = ["MetaTrader5", "mt5", "order_send", "from execution"]
        for line in import_lines:
            for term in banned:
                assert term not in line, f"SAFETY: '{term}' in router.py imports"


# ═══════════════════════════════════════════════════════════════
# TEST 8 — Existing local research behaviour
# ═══════════════════════════════════════════════════════════════

class TestExistingBehaviour:
    def test_local_e1_still_works(self):
        """E1 with local filesystem should produce the same result as before."""
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Local universe not available")

        from research_engine.v10.operations.router import ResearchRouter
        router = ResearchRouter()  # Local mode (default)
        result = router.execute({"action": "run_question", "question_id": "E1"})
        assert "error" not in result or not result.get("error")
        inner = result.get("result", {}).get("result", {})
        # E1 should have found trades
        gov = result.get("result", {}).get("governance", {})
        assert gov.get("sample", {}).get("size", 0) > 0
