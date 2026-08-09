"""Tests for V10 Lambda Deployment — handler, storage, isolation, package."""
import inspect
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.lambda_handler import handler
from research_engine.v10.operations.storage import ResearchStorage
from research_engine.v10.operations import ResearchRouter


# ═══════════════════════════════════════════════════════════════
# LAMBDA HANDLER ROUTING
# ═══════════════════════════════════════════════════════════════

class TestLambdaRouting:
    def test_run_question_routes(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        result = handler({"action": "run_question", "question_id": "E1"})
        assert result["_action"] == "run_question"
        assert "result" in result

    def test_run_campaign_routes(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        result = handler({"action": "run_campaign", "campaign_id": "RISK_INVESTIGATION_V1"})
        assert result["_action"] == "run_campaign"

    def test_get_state_routes(self):
        result = handler({"action": "get_state"})
        assert result["_action"] == "get_state"
        assert "last_research_run" in result

    def test_invalid_action(self):
        result = handler({"action": "destroy_everything"})
        assert "error" in result
        assert "Unknown" in result["error"]

    def test_missing_action(self):
        result = handler({})
        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# S3 / LOCAL STORAGE ABSTRACTION
# ═══════════════════════════════════════════════════════════════

class TestStorageAbstraction:
    def test_local_backend_default(self):
        storage = ResearchStorage(backend="local")
        assert not storage.is_s3

    def test_s3_backend_flag(self):
        storage = ResearchStorage(backend="s3")
        assert storage.is_s3

    def test_local_read_write(self, tmp_path):
        storage = ResearchStorage(backend="local")
        test_file = str(tmp_path / "test.json")
        storage._local_write(test_file, '{"hello": "world"}')
        content = storage._local_read(test_file)
        assert json.loads(content) == {"hello": "world"}

    def test_local_read_missing(self, tmp_path):
        storage = ResearchStorage(backend="local")
        content = storage._local_read(str(tmp_path / "nonexistent.json"))
        assert content == ""

    def test_load_state_default(self, tmp_path):
        storage = ResearchStorage(backend="local")
        state = storage.load_state(str(tmp_path / "missing_state.json"))
        assert state == {}


# ═══════════════════════════════════════════════════════════════
# ENVIRONMENT CONFIGURATION
# ═══════════════════════════════════════════════════════════════

class TestEnvironmentConfig:
    def test_env_vars_respected(self, monkeypatch):
        monkeypatch.setenv("RESEARCH_STORAGE", "s3")
        monkeypatch.setenv("RESEARCH_BUCKET", "my-custom-bucket")
        # Reimport to pick up env
        import importlib
        import research_engine.v10.operations.storage as mod
        importlib.reload(mod)
        assert mod._STORAGE_BACKEND == "s3"
        assert mod._BUCKET == "my-custom-bucket"
        # Restore
        monkeypatch.setenv("RESEARCH_STORAGE", "local")
        monkeypatch.setenv("RESEARCH_BUCKET", "v10-engine")
        importlib.reload(mod)


# ═══════════════════════════════════════════════════════════════
# DEPLOYMENT PACKAGE CONTENTS
# ═══════════════════════════════════════════════════════════════

class TestPackageContents:
    def test_zip_exists(self):
        zip_path = Path("build/v10-research-lambda.zip")
        if not zip_path.exists():
            pytest.skip("Lambda ZIP not built yet")
        assert zip_path.stat().st_size > 0

    def test_handler_in_zip(self):
        zip_path = Path("build/v10-research-lambda.zip")
        if not zip_path.exists():
            pytest.skip("Lambda ZIP not built")
        with zipfile.ZipFile(zip_path) as zf:
            assert "lambda_handler.py" in zf.namelist()

    def test_research_engine_in_zip(self):
        zip_path = Path("build/v10-research-lambda.zip")
        if not zip_path.exists():
            pytest.skip("Lambda ZIP not built")
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("research_engine/v10/" in n for n in names)


# ═══════════════════════════════════════════════════════════════
# CREDENTIAL EXCLUSION
# ═══════════════════════════════════════════════════════════════

class TestCredentialExclusion:
    def test_no_env_in_zip(self):
        zip_path = Path("build/v10-research-lambda.zip")
        if not zip_path.exists():
            pytest.skip("Lambda ZIP not built")
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert ".env" not in name
                assert "credentials" not in name.lower()
                assert "secret" not in name.lower()


# ═══════════════════════════════════════════════════════════════
# LIVE-TRADING ISOLATION
# ═══════════════════════════════════════════════════════════════

class TestLiveIsolation:
    def test_lambda_handler_no_broker(self):
        import research_engine.v10.lambda_handler as mod
        source = inspect.getsource(mod)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        for line in imports:
            assert "MetaTrader5" not in line
            assert "mt5" not in line
            assert "order_send" not in line

    def test_router_no_broker(self):
        import research_engine.v10.operations.router as mod
        source = inspect.getsource(mod)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        for line in imports:
            assert "MetaTrader5" not in line
            assert "from execution" not in line

    def test_no_mt5_in_zip(self):
        zip_path = Path("build/v10-research-lambda.zip")
        if not zip_path.exists():
            pytest.skip("Lambda ZIP not built")
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert "mt5_execution" not in name


# ═══════════════════════════════════════════════════════════════
# MISSING DATA HANDLING
# ═══════════════════════════════════════════════════════════════

class TestMissingData:
    def test_missing_universe_does_not_crash(self):
        router = ResearchRouter(universe_file="nonexistent_universe.jsonl")
        result = router.execute({"action": "run_question", "question_id": "E1"})
        assert "_action" in result  # Did not crash

    def test_missing_s3_data_returns_empty(self):
        storage = ResearchStorage(backend="local")
        content = storage.load_universe("nonexistent_path.jsonl")
        assert content == ""


# ═══════════════════════════════════════════════════════════════
# SUCCESSFUL EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestSuccessfulExecution:
    def test_question_produces_governance(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        result = handler({"action": "run_question", "question_id": "E1"})
        inner = result.get("result", {})
        assert "governance" in inner

    def test_report_generation(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        result = handler({"action": "generate_report"})
        assert "dataset" in result
        assert "campaigns" in result


# ═══════════════════════════════════════════════════════════════
# STATE PERSISTENCE
# ═══════════════════════════════════════════════════════════════

class TestStatePersistence:
    def test_save_and_load(self, tmp_path):
        from research_engine.v10.operations.state import save_research_state, get_research_state
        state_file = str(tmp_path / "state.json")
        save_research_state({"data_version": "test_v1"}, state_file)
        loaded = get_research_state(state_file)
        assert loaded["data_version"] == "test_v1"
        assert "last_updated" in loaded
