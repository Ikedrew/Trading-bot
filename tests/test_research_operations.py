"""Tests for V10 Research Operations & Lambda Execution Layer."""
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.operations import ResearchRouter
from research_engine.v10.operations.state import get_research_state, save_research_state
from research_engine.v10.lambda_handler import handler


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def router():
    universe = Path("data/research/research_universe.jsonl")
    if not universe.exists():
        pytest.skip("Research universe not available")
    return ResearchRouter()


# ═══════════════════════════════════════════════════════════════
# LAMBDA EVENT ROUTING
# ═══════════════════════════════════════════════════════════════

class TestLambdaRouting:
    def test_run_question(self, router):
        result = router.execute({"action": "run_question", "question_id": "E1"})
        assert "error" not in result or not result.get("error")
        assert result["_action"] == "run_question"

    def test_run_campaign(self, router):
        result = router.execute({"action": "run_campaign", "campaign_id": "RISK_INVESTIGATION_V1"})
        assert not result.get("error")
        assert result["_action"] == "run_campaign"
        assert result.get("questions_executed", 0) > 0

    def test_unknown_action(self, router):
        result = router.execute({"action": "nonexistent_action"})
        assert "error" in result
        assert "Unknown action" in result["error"]

    def test_lambda_handler(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        result = handler({"action": "get_state"})
        assert "_action" in result
        assert result["_action"] == "get_state"


# ═══════════════════════════════════════════════════════════════
# QUESTION EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestQuestionExecution:
    def test_run_question_returns_result(self, router):
        result = router.execute({"action": "run_question", "question_id": "E1"})
        assert "result" in result
        assert result["question_id"] == "E1"

    def test_invalid_question(self, router):
        result = router.execute({"action": "run_question", "question_id": "NONEXISTENT"})
        # Should still return without crashing
        assert "_action" in result


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestCampaignExecution:
    def test_campaign_produces_findings(self, router):
        result = router.execute({"action": "run_campaign", "campaign_id": "RISK_INVESTIGATION_V1"})
        assert result.get("questions_executed", 0) > 0
        assert "findings" in result

    def test_unknown_campaign(self, router):
        result = router.execute({"action": "run_campaign", "campaign_id": "FAKE_CAMPAIGN"})
        assert result.get("error")


# ═══════════════════════════════════════════════════════════════
# SEGMENTED EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestSegmentedExecution:
    def test_segmented_research(self, router):
        result = router.execute({
            "action": "run_segmented_research",
            "question_id": "E1",
            "filters": {"instrument": "FX"},
        })
        assert result["question_id"] == "E1"
        assert result["filters"] == {"instrument": "FX"}


# ═══════════════════════════════════════════════════════════════
# CANDIDATE VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestCandidateValidation:
    def test_validation_via_router(self, router):
        result = router.execute({
            "action": "run_candidate_validation",
            "candidate_id": "TEST_VAL",
            "changes": {"stop_multiplier": 1.5},
            "baseline_id": "BASELINE_TEST",
        })
        assert result.get("validation_id") or result.get("candidate_id")
        assert result.get("status") in ("COMPLETED", "FAILED", None) or "decision" in result


# ═══════════════════════════════════════════════════════════════
# SHADOW PROCESSING
# ═══════════════════════════════════════════════════════════════

class TestShadowProcessing:
    def test_shadow_with_no_trades(self, router):
        result = router.execute({"action": "run_shadow_processing", "trades": []})
        assert result["trades_processed"] == 0


# ═══════════════════════════════════════════════════════════════
# PERSISTENT STATE
# ═══════════════════════════════════════════════════════════════

class TestPersistentState:
    def test_state_save_load(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        save_research_state({"last_research_run": "2026-08-07", "data_version": "v1"}, state_file)
        loaded = get_research_state(state_file)
        assert loaded["last_research_run"] == "2026-08-07"
        assert "last_updated" in loaded

    def test_default_state(self, tmp_path):
        state = get_research_state(str(tmp_path / "nonexistent.json"))
        assert "last_research_run" in state
        assert "active_candidates" in state

    def test_get_state_via_router(self, router):
        result = router.execute({"action": "get_state"})
        assert "last_research_run" in result or "_action" in result


# ═══════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

class TestReportGeneration:
    def test_operational_report(self, router):
        result = router.execute({"action": "generate_report"})
        assert "dataset" in result
        assert "campaigns" in result
        assert "candidates" in result

    def test_dashboard(self, router):
        result = router.execute({"action": "generate_dashboard"})
        assert "total_candidates" in result


# ═══════════════════════════════════════════════════════════════
# MISSING DATASETS
# ═══════════════════════════════════════════════════════════════

class TestMissingData:
    def test_missing_universe_handled(self):
        router = ResearchRouter(universe_file="nonexistent_file.jsonl")
        result = router.execute({"action": "run_question", "question_id": "E1"})
        # Should not crash — returns result with error or empty data
        assert "_action" in result


# ═══════════════════════════════════════════════════════════════
# CANDIDATE ISOLATION
# ═══════════════════════════════════════════════════════════════

class TestCandidateIsolation:
    def test_validation_does_not_modify_universe(self, router):
        """Validation must not alter the research universe file."""
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        before = universe.read_text(encoding="utf-8")
        router.execute({
            "action": "run_candidate_validation",
            "candidate_id": "ISO_TEST",
            "changes": {"stop_multiplier": 2.0},
            "baseline_id": "B",
        })
        after = universe.read_text(encoding="utf-8")
        assert before == after


# ═══════════════════════════════════════════════════════════════
# LIVE TRADING ISOLATION
# ═══════════════════════════════════════════════════════════════

class TestLiveIsolation:
    def test_no_broker_imports_in_operations(self):
        """Operations modules must not import broker execution."""
        import research_engine.v10.operations.router as mod
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        banned = ["MetaTrader5", "mt5", "order_send", "from execution"]
        for line in import_lines:
            for term in banned:
                assert term not in line, f"SAFETY: '{term}' in operations/router.py"

    def test_no_broker_imports_in_lambda(self):
        """Lambda handler must not import broker execution."""
        import research_engine.v10.lambda_handler as mod
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        banned = ["MetaTrader5", "mt5", "order_send", "from execution"]
        for line in import_lines:
            for term in banned:
                assert term not in line, f"SAFETY: '{term}' in lambda_handler.py"
