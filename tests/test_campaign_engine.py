"""Tests for V10 Research Campaign Engine."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.campaigns import CampaignRunner, CampaignRegistry, ResearchCampaign, CampaignResult
from research_engine.v10.campaigns.campaign_memory import CampaignMemory
from research_engine.v10.campaigns.campaign_report import save_campaign_report
from research_engine.v10.campaigns.models import CampaignFinding


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN MODEL (1-4)
# ═══════════════════════════════════════════════════════════════

class TestCampaignModel:
    def test_campaign_created(self):
        c = ResearchCampaign(
            campaign_id="TEST_1", name="Test", objective="Testing",
            questions=["E1", "R2"],
        )
        assert c.campaign_id == "TEST_1"
        assert c.created_at != ""

    def test_required_fields(self):
        c = ResearchCampaign(campaign_id="X", name="X", objective="X")
        assert c.campaign_id
        assert c.name
        assert c.objective

    def test_questions_stored(self):
        c = ResearchCampaign(
            campaign_id="Q", name="Q", objective="Q",
            questions=["E1", "R1", "D1"],
        )
        assert c.questions == ["E1", "R1", "D1"]

    def test_filters_stored(self):
        c = ResearchCampaign(
            campaign_id="F", name="F", objective="F",
            filters={"instrument": "FX"},
        )
        assert c.filters == {"instrument": "FX"}


# ═══════════════════════════════════════════════════════════════
# REGISTRY (5-7)
# ═══════════════════════════════════════════════════════════════

class TestCampaignRegistry:
    def test_registry_loads(self):
        reg = CampaignRegistry()
        assert len(reg.list_campaigns()) >= 4

    def test_campaign_ids_resolve(self):
        reg = CampaignRegistry()
        for cid in ["FX_OPT_V1", "RISK_INVESTIGATION_V1", "DECISION_QUALITY_V1", "STRATEGY_REVIEW_V1"]:
            assert reg.get(cid) is not None, f"{cid} not found"

    def test_duplicate_rejected(self):
        reg = CampaignRegistry()
        dup = ResearchCampaign(campaign_id="FX_OPT_V1", name="Dup", objective="Dup")
        with pytest.raises(ValueError):
            reg.register(dup)


# ═══════════════════════════════════════════════════════════════
# RUNNER (8-11)
# ═══════════════════════════════════════════════════════════════

class TestCampaignRunner:
    @pytest.fixture
    def runner(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        return CampaignRunner()

    def test_campaign_executes_questions(self, runner):
        result = runner.run_campaign("RISK_INVESTIGATION_V1")
        assert result.questions_executed > 0
        assert not result.error

    def test_questions_resolve_through_registry(self, runner):
        result = runner.run_campaign("RISK_INVESTIGATION_V1")
        # Should have findings for E1, R1, R2
        qids = [f.question_id for f in result.findings]
        assert "E1" in qids

    def test_domains_resolved(self, runner):
        result = runner.run_campaign("FX_OPT_V1")
        domains = {f.domain for f in result.findings}
        # FX_OPT covers trade + decision + market
        assert len(domains) >= 1

    def test_governance_applied(self, runner):
        result = runner.run_campaign("RISK_INVESTIGATION_V1")
        for f in result.findings:
            assert f.confidence in ("HIGH", "MEDIUM", "LOW")
            assert f.evidence_maturity != ""
            assert f.decision_status != ""


# ═══════════════════════════════════════════════════════════════
# REPORTING (12-15)
# ═══════════════════════════════════════════════════════════════

class TestCampaignReporting:
    def test_reports_generated(self, tmp_path):
        result = CampaignResult(
            campaign_id="TEST_RPT",
            campaign_name="Test Report",
            objective="Testing",
            questions_executed=3,
            findings=[
                CampaignFinding(
                    question_id="E1", question_name="Expectancy", domain="trade",
                    sample_size=50, confidence="HIGH", evidence_maturity="STRONG",
                    decision_status="SUPPORTED", priority="HIGH", result_value=0.2,
                ),
            ],
            recommendations=["Investigate stop model"],
            data_gaps=["C1 not active"],
        )
        paths = save_campaign_report(result, reports_dir=str(tmp_path))
        assert Path(paths["json"]).exists()
        assert Path(paths["md"]).exists()

    def test_findings_included(self, tmp_path):
        result = CampaignResult(
            campaign_id="TEST_F",
            findings=[
                CampaignFinding(question_id="R2", question_name="Stops", domain="trade",
                                sample_size=30, priority="HIGH", result_value=-0.2),
            ],
        )
        paths = save_campaign_report(result, reports_dir=str(tmp_path))
        data = json.loads(Path(paths["findings"]).read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["question_id"] == "R2"

    def test_recommendations_included(self, tmp_path):
        result = CampaignResult(
            campaign_id="TEST_R",
            recommendations=["Fix stops", "Review regime filter"],
        )
        paths = save_campaign_report(result, reports_dir=str(tmp_path))
        md = Path(paths["md"]).read_text(encoding="utf-8")
        assert "Fix stops" in md

    def test_data_gaps_included(self, tmp_path):
        result = CampaignResult(
            campaign_id="TEST_G",
            data_gaps=["C1: Status is 'draft'"],
        )
        paths = save_campaign_report(result, reports_dir=str(tmp_path))
        data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert "C1" in data["data_gaps"][0]


# ═══════════════════════════════════════════════════════════════
# PRIORITISATION (16-17)
# ═══════════════════════════════════════════════════════════════

class TestPrioritisation:
    def test_findings_ranked(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        runner = CampaignRunner()
        result = runner.run_campaign("RISK_INVESTIGATION_V1")
        if len(result.findings) >= 2:
            # Should be sorted by priority_score descending
            scores = [f.priority_score for f in result.findings]
            assert scores == sorted(scores, reverse=True)

    def test_high_impact_first(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        runner = CampaignRunner()
        result = runner.run_campaign("RISK_INVESTIGATION_V1")
        if result.findings:
            # First finding should have highest priority score
            assert result.findings[0].priority_score >= result.findings[-1].priority_score


# ═══════════════════════════════════════════════════════════════
# MEMORY (18-19)
# ═══════════════════════════════════════════════════════════════

class TestCampaignMemory:
    def test_previous_campaigns_stored(self, tmp_path):
        mem = CampaignMemory(memory_dir=str(tmp_path))
        mem.record("FX_OPT_V1", {"questions_executed": 8, "findings": 5})
        assert mem.previous_run_count("FX_OPT_V1") == 1

    def test_history_retrieved(self, tmp_path):
        mem = CampaignMemory(memory_dir=str(tmp_path))
        mem.record("TEST_1", {"run": 1})
        mem.record("TEST_1", {"run": 2})
        history = mem.get_history("TEST_1")
        assert len(history) == 2
        assert history[0]["run"] == 1
        assert history[1]["run"] == 2

    def test_latest(self, tmp_path):
        mem = CampaignMemory(memory_dir=str(tmp_path))
        mem.record("X", {"v": "first"})
        mem.record("X", {"v": "second"})
        assert mem.latest("X")["v"] == "second"


# ═══════════════════════════════════════════════════════════════
# LAMBDA COMPAT (20)
# ═══════════════════════════════════════════════════════════════

class TestLambdaExecution:
    def test_campaign_from_payload(self):
        """Campaign can execute from a simple payload (Lambda-style)."""
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")

        # Simulate Lambda event
        event = {"campaign_id": "RISK_INVESTIGATION_V1"}
        runner = CampaignRunner()
        result = runner.run_campaign(event["campaign_id"])
        assert not result.error
        assert result.questions_executed > 0


# ═══════════════════════════════════════════════════════════════
# UNKNOWN CAMPAIGN (extra)
# ═══════════════════════════════════════════════════════════════

class TestUnknownCampaign:
    def test_unknown_returns_error(self):
        runner = CampaignRunner()
        result = runner.run_campaign("NONEXISTENT_CAMPAIGN")
        assert result.error
        assert "not found" in result.error
