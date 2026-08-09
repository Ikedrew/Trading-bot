"""Tests for V10 Optimisation Bridge."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.optimisation import (
    ResearchHypothesis, OptimisationCandidate, ValidationPlan, ChangeRisk,
    HypothesisEngine, OptimisationRegistry,
)
from research_engine.v10.optimisation.models import classify_change_risk
from research_engine.v10.optimisation.candidate_builder import build_candidate, build_validation_plan
from research_engine.v10.optimisation.optimisation_report import save_optimisation_report
from research_engine.v10.research_governance.models import ResearchFinding


# ═══════════════════════════════════════════════════════════════
# MODELS (1-3)
# ═══════════════════════════════════════════════════════════════

class TestHypothesisModel:
    def test_creation(self):
        h = ResearchHypothesis(
            hypothesis_id="HYP_R2_001",
            source_question="R2",
            statement="Stops are too tight",
        )
        assert h.hypothesis_id == "HYP_R2_001"
        assert h.created_at != ""
        assert h.status == "PROPOSED"

    def test_to_dict(self):
        h = ResearchHypothesis(hypothesis_id="H1", statement="Test")
        d = h.to_dict()
        assert "hypothesis_id" in d
        assert "statement" in d
        assert "status" in d


class TestCandidateModel:
    def test_creation(self):
        c = OptimisationCandidate(
            candidate_id="V10.1_TEST",
            hypothesis_id="HYP_001",
            baseline_id="V10_BASELINE_20260807",
            component="RiskManager",
            changes={"atr_multiplier": {"before": 1.5, "after": 2.0}},
        )
        assert c.candidate_id == "V10.1_TEST"
        assert c.baseline_id == "V10_BASELINE_20260807"

    def test_requires_baseline(self):
        with pytest.raises(ValueError):
            OptimisationCandidate(
                candidate_id="NO_BASE",
                hypothesis_id="H1",
                baseline_id="",  # Empty = error
            )


class TestValidationPlanModel:
    def test_creation(self):
        p = ValidationPlan(
            candidate_id="C1",
            baseline_id="B1",
            metrics=["expectancy_r", "profit_factor"],
            target_questions=["R2"],
        )
        assert p.candidate_id == "C1"
        assert "expectancy_r" in p.metrics
        assert p.created_at != ""


# ═══════════════════════════════════════════════════════════════
# HYPOTHESIS ENGINE (4-6)
# ═══════════════════════════════════════════════════════════════

class TestHypothesisEngine:
    def test_finding_converts_to_hypothesis(self):
        finding = ResearchFinding(
            finding_id="R2_FULL",
            question_id="R2",
            question_name="Stop Effectiveness",
            sample_size=40,
            result_value=-0.25,
            confidence_level="MEDIUM",
            evidence_maturity="DEVELOPING",
            decision_status="REJECTED",
        )
        engine = HypothesisEngine()
        hyp = engine.from_finding(finding)
        assert hyp is not None
        assert hyp.source_question == "R2"
        assert hyp.target_component == "StopPlacement"
        assert "PROPOSED" == hyp.status

    def test_unsupported_not_invented(self):
        """Weak findings should not produce hypotheses."""
        finding = ResearchFinding(
            finding_id="X_WEAK",
            question_id="X1",
            question_name="Unknown",
            sample_size=3,
            result_value=0.01,
            confidence_level="LOW",
            evidence_maturity="EXPLORATORY",
            decision_status="INVESTIGATE",
        )
        engine = HypothesisEngine()
        hyp = engine.from_finding(finding)
        assert hyp is None  # Not enough signal

    def test_source_question_preserved(self):
        finding = ResearchFinding(
            finding_id="D1_FX",
            question_id="D1",
            question_name="Score Predictive Power",
            sample_size=50,
            result_value=0.15,
            confidence_level="HIGH",
            evidence_maturity="STRONG",
            decision_status="SUPPORTED",
        )
        engine = HypothesisEngine()
        hyp = engine.from_finding(finding)
        assert hyp.source_question == "D1"
        assert hyp.source_finding == "D1_FX"


# ═══════════════════════════════════════════════════════════════
# CANDIDATES (7-9)
# ═══════════════════════════════════════════════════════════════

class TestCandidates:
    def test_requires_baseline(self):
        with pytest.raises(ValueError):
            build_candidate(
                candidate_id="X",
                hypothesis_id="H1",
                baseline_id="",
                component="Risk",
                changes={"x": 1},
            )

    def test_stores_changes(self):
        c = build_candidate(
            candidate_id="C1",
            hypothesis_id="H1",
            baseline_id="BASE_001",
            component="RiskManager",
            changes={"atr_multiplier": {"before": 1.5, "after": 2.0}},
        )
        assert c.changes["atr_multiplier"]["after"] == 2.0
        assert c.baseline_id == "BASE_001"

    def test_risk_classification(self):
        assert classify_change_risk({"atr_multiplier": 2.0}) == ChangeRisk.LOW
        assert classify_change_risk({"regime_filter": "add TRENDING only"}) == ChangeRisk.MEDIUM
        assert classify_change_risk({"new_strategy_engine": "v2"}) == ChangeRisk.HIGH


# ═══════════════════════════════════════════════════════════════
# REGISTRY (10-12)
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_hypothesis_stored(self):
        reg = OptimisationRegistry(registry_dir="NUL")
        h = ResearchHypothesis(hypothesis_id="H1", statement="Test")
        reg.add_hypothesis(h)
        assert reg.get_hypothesis("H1") is not None

    def test_candidate_stored(self):
        reg = OptimisationRegistry(registry_dir="NUL")
        c = OptimisationCandidate(
            candidate_id="C1", hypothesis_id="H1",
            baseline_id="B1", component="Risk",
        )
        reg.add_candidate(c)
        assert reg.get_candidate("C1") is not None

    def test_status_updates(self):
        reg = OptimisationRegistry(registry_dir="NUL")
        h = ResearchHypothesis(hypothesis_id="H2", status="PROPOSED")
        reg.add_hypothesis(h)
        reg.update_hypothesis_status("H2", "TESTING")
        assert reg.get_hypothesis("H2").status == "TESTING"

    def test_persistence(self, tmp_path):
        reg = OptimisationRegistry(registry_dir=str(tmp_path))
        reg.add_hypothesis(ResearchHypothesis(hypothesis_id="HP1", statement="Persist"))
        reg.add_candidate(OptimisationCandidate(
            candidate_id="CP1", hypothesis_id="HP1", baseline_id="B1",
        ))
        reg.save()

        # Load into fresh registry
        reg2 = OptimisationRegistry(registry_dir=str(tmp_path))
        reg2.load()
        assert reg2.get_hypothesis("HP1") is not None
        assert reg2.get_candidate("CP1") is not None


# ═══════════════════════════════════════════════════════════════
# VALIDATION (13-14)
# ═══════════════════════════════════════════════════════════════

class TestValidation:
    def test_plan_generated(self):
        c = OptimisationCandidate(
            candidate_id="C1", hypothesis_id="H1",
            baseline_id="B1", component="StopPlacement",
        )
        plan = build_validation_plan(c, target_questions=["R2"])
        assert plan.candidate_id == "C1"
        assert plan.baseline_id == "B1"
        assert "R2" in plan.target_questions
        assert "expectancy_r" in plan.metrics

    def test_metrics_stored(self):
        c = OptimisationCandidate(
            candidate_id="C2", hypothesis_id="H2",
            baseline_id="B2", component="Risk",
        )
        plan = build_validation_plan(c, target_questions=["R1", "R2"])
        assert len(plan.metrics) >= 3
        assert plan.success_conditions
        assert plan.failure_conditions


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN LINK (15)
# ═══════════════════════════════════════════════════════════════

class TestCampaignLink:
    def test_finding_creates_hypothesis(self):
        from research_engine.v10.campaigns.models import CampaignFinding
        cf = CampaignFinding(
            question_id="R2",
            question_name="Stop Effectiveness",
            domain="trade",
            sample_size=40,
            result_value=-0.3,
            confidence="MEDIUM",
            evidence_maturity="DEVELOPING",
            decision_status="REJECTED",
            priority="HIGH",
        )
        engine = HypothesisEngine()
        hyps = engine.from_campaign_findings([cf])
        assert len(hyps) >= 1
        assert hyps[0].source_question == "R2"


# ═══════════════════════════════════════════════════════════════
# REPORTING (16)
# ═══════════════════════════════════════════════════════════════

class TestReporting:
    def test_reports_generated(self, tmp_path):
        h = ResearchHypothesis(hypothesis_id="H1", source_question="R2", statement="Test")
        c = OptimisationCandidate(
            candidate_id="C1", hypothesis_id="H1",
            baseline_id="B1", component="Risk",
        )
        paths = save_optimisation_report([h], [c], reports_dir=str(tmp_path))
        assert Path(paths["hypotheses"]).exists()
        assert Path(paths["candidates"]).exists()
        assert Path(paths["summary"]).exists()


# ═══════════════════════════════════════════════════════════════
# SERIALISATION (17)
# ═══════════════════════════════════════════════════════════════

class TestSerialisation:
    def test_all_objects_serialise(self):
        h = ResearchHypothesis(hypothesis_id="S1", statement="Ser")
        c = OptimisationCandidate(candidate_id="S2", hypothesis_id="S1", baseline_id="B")
        p = ValidationPlan(candidate_id="S2", baseline_id="B", metrics=["x"])

        # Should not raise
        json.dumps(h.to_dict(), default=str)
        json.dumps(c.to_dict(), default=str)
        json.dumps(p.to_dict(), default=str)
