"""Tests for V10 Research Governance & Statistical Confidence."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.research_governance.sample_validator import SampleValidator
from research_engine.v10.research_governance.confidence_engine import ConfidenceEngine
from research_engine.v10.research_governance.finding_ranker import (
    FindingRanker, MultipleComparisonTracker, generate_governance_report,
)
from research_engine.v10.research_governance.models import ResearchFinding, validate_finding
from research_engine.v10.research_intelligence.models import ExperimentResult


# ═══════════════════════════════════════════════════════════════
# 1. LARGE SAMPLE → VALID CONFIDENCE
# ═══════════════════════════════════════════════════════════════

class TestLargeSampleValid:
    def test_50_trades_is_valid(self):
        sv = SampleValidator()
        result = sv.validate(50)
        assert result["status"] == "VALID"
        assert result["confidence"] == "HIGH"

    def test_large_sample_high_confidence(self):
        ce = ConfidenceEngine()
        result = ce.assess(sample_size=50, effect_size=0.3, recommendation="SUPPORTED")
        assert result["confidence"] == "HIGH"
        assert result["score"] >= 0.7


# ═══════════════════════════════════════════════════════════════
# 2. SMALL SAMPLE → INSUFFICIENT
# ═══════════════════════════════════════════════════════════════

class TestSmallSampleInsufficient:
    def test_5_trades_insufficient(self):
        sv = SampleValidator()
        result = sv.validate(5)
        assert result["status"] == "INSUFFICIENT"
        assert result["confidence"] == "LOW"

    def test_0_trades_insufficient(self):
        sv = SampleValidator()
        result = sv.validate(0)
        assert result["status"] == "INSUFFICIENT"


# ═══════════════════════════════════════════════════════════════
# 3. NEGATIVE RESULT → REJECTED
# ═══════════════════════════════════════════════════════════════

class TestNegativeRejected:
    def test_rejected_finding(self):
        exp = ExperimentResult(
            question_id="R2",
            question_name="Stop Effectiveness",
            sample_size=40,
            result={"expectancy_r": -0.35, "win_rate": 0.30},
            confidence="HIGH",
            recommendation="REJECTED",
            limitations=[],
        )
        finding = validate_finding(exp)
        # Progressive governance: large negative with decent sample → REJECTED or EARLY_FAILURE
        assert finding.status in ("REJECTED", "EARLY_FAILURE")
        assert finding.result_value < 0


# ═══════════════════════════════════════════════════════════════
# 4. POSITIVE RESULT → SUPPORTED
# ═══════════════════════════════════════════════════════════════

class TestPositiveSupported:
    def test_supported_finding(self):
        exp = ExperimentResult(
            question_id="E1",
            question_name="System Expectancy",
            sample_size=50,
            result={"expectancy_r": 0.25, "win_rate": 0.55, "profit_factor": 1.5},
            confidence="HIGH",
            recommendation="SUPPORTED",
            limitations=[],
        )
        finding = validate_finding(exp)
        # Progressive governance: 50 trades + positive effect → SUPPORTED or CONTINUE_TESTING
        assert finding.status in ("SUPPORTED", "CONTINUE_TESTING")
        assert finding.result_value > 0


# ═══════════════════════════════════════════════════════════════
# 5. MIXED EVIDENCE → INCONCLUSIVE
# ═══════════════════════════════════════════════════════════════

class TestMixedInconclusive:
    def test_small_sample_not_supported(self):
        exp = ExperimentResult(
            question_id="M1",
            question_name="Regime",
            sample_size=5,
            result={"expectancy_r": 0.1},
            confidence="LOW",
            recommendation="SUPPORTED",
            limitations=["Small sample"],
        )
        finding = validate_finding(exp)
        # Progressive: 5 trades = EXPLORATORY, should NOT be SUPPORTED
        assert finding.status != "SUPPORTED"
        assert finding.decision_status in ("INVESTIGATE", "PROMISING")

    def test_low_effect_with_medium_sample(self):
        exp = ExperimentResult(
            question_id="D3",
            question_name="Threshold",
            sample_size=30,
            result={"expectancy_r": 0.02},
            confidence="MEDIUM",
            recommendation="INCONCLUSIVE",
            limitations=[],
        )
        finding = validate_finding(exp)
        # Negligible effect → likely CONTINUE_TESTING or INCONCLUSIVE
        assert finding.decision_status in ("CONTINUE_TESTING", "INCONCLUSIVE", "INVESTIGATE")


# ═══════════════════════════════════════════════════════════════
# 6. CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════

class TestConfidenceScoring:
    def test_score_range(self):
        ce = ConfidenceEngine()
        result = ce.assess(sample_size=25, effect_size=0.15)
        assert 0.0 <= result["score"] <= 1.0

    def test_more_evidence_higher_score(self):
        ce = ConfidenceEngine()
        r1 = ce.assess(sample_size=10, effect_size=0.1)
        r2 = ce.assess(sample_size=50, effect_size=0.5, recommendation="SUPPORTED")
        assert r2["score"] > r1["score"]

    def test_limitations_reduce_score(self):
        ce = ConfidenceEngine()
        r1 = ce.assess(sample_size=30, effect_size=0.3, limitations=[])
        r2 = ce.assess(sample_size=30, effect_size=0.3, limitations=["a", "b", "c", "d"])
        assert r1["score"] > r2["score"]

    def test_factors_populated(self):
        ce = ConfidenceEngine()
        result = ce.assess(sample_size=20, effect_size=0.2, recommendation="SUPPORTED")
        assert len(result["factors"]) > 0


# ═══════════════════════════════════════════════════════════════
# 7. FINDING RANKING
# ═══════════════════════════════════════════════════════════════

class TestFindingRanking:
    def test_ranking_order(self):
        f1 = ResearchFinding(
            finding_id="f1", question_id="E1", sample_size=50,
            confidence_level="HIGH", confidence_score=0.8,
            status="SUPPORTED", result_value=0.3,
        )
        f2 = ResearchFinding(
            finding_id="f2", question_id="M1", sample_size=8,
            confidence_level="LOW", confidence_score=0.2,
            status="INCONCLUSIVE", result_value=0.05,
            sample_status="INSUFFICIENT",
        )
        f3 = ResearchFinding(
            finding_id="f3", question_id="R2", sample_size=35,
            confidence_level="MEDIUM", confidence_score=0.55,
            status="REJECTED", result_value=-0.4,
        )

        ranker = FindingRanker()
        ranked = ranker.rank([f1, f2, f3])
        # f1 should be first (highest confidence + supported + large effect)
        assert ranked[0].finding_id == "f1"
        assert ranked[0].priority == "HIGH"
        # f2 should be last (insufficient + inconclusive)
        assert ranked[-1].finding_id == "f2"
        assert ranked[-1].priority == "LOW"


# ═══════════════════════════════════════════════════════════════
# 8. MULTIPLE COMPARISON WARNING
# ═══════════════════════════════════════════════════════════════

class TestMultipleComparison:
    def test_low_exposure_no_warning(self):
        tracker = MultipleComparisonTracker()
        for i in range(5):
            tracker.record(f"Q{i}", {"instrument": "EURUSD"})
        assert tracker.risk_level() == "LOW"
        assert tracker.generate_warning() is None

    def test_high_exposure_generates_warning(self):
        tracker = MultipleComparisonTracker()
        for i in range(60):
            tracker.record(f"Q{i % 10}", {"instrument": f"SYM{i}", "regime": "TRENDING"})
        assert tracker.risk_level() in ("MEDIUM", "HIGH")
        warning = tracker.generate_warning()
        assert warning is not None
        assert "60" in warning

    def test_exposure_tracking(self):
        tracker = MultipleComparisonTracker()
        tracker.record("E1", {"instrument": "EURUSD"})
        tracker.record("E1", {"instrument": "GBPUSD"})
        tracker.record("R2", {"instrument": "EURUSD"})
        exp = tracker.exposure
        assert exp["questions_tested"] == 2
        assert exp["segments_tested"] == 2  # instrument=EURUSD, instrument=GBPUSD
        assert exp["total_comparisons"] == 3


# ═══════════════════════════════════════════════════════════════
# 9. REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

class TestReportGeneration:
    def test_report_creates_files(self, tmp_path):
        findings = [
            ResearchFinding(
                finding_id="f1", question_id="E1", question_name="Expectancy",
                sample_size=50, confidence_level="HIGH", confidence_score=0.8,
                status="SUPPORTED", result_value=0.2, priority="HIGH",
            ),
        ]
        report = generate_governance_report(findings, reports_dir=str(tmp_path))
        assert (tmp_path / "research_confidence_report.json").exists()
        assert (tmp_path / "research_confidence_report.md").exists()
        assert report["total_findings"] == 1
        assert report["supported"] == 1


# ═══════════════════════════════════════════════════════════════
# 10. EXPERIMENT RUNNER INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestRunnerIntegration:
    def test_run_with_governance(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")

        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner()
        result = runner.run_with_governance("E1")

        assert "result" in result
        assert "governance" in result
        gov = result["governance"]
        assert "confidence" in gov
        assert gov["confidence"]["level"] in ("HIGH", "MEDIUM", "LOW")
        assert gov["status"] in ("SUPPORTED", "REJECTED", "INCONCLUSIVE")
        assert "sample" in gov
        assert gov["sample"]["size"] > 0
