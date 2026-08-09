"""Tests for V10 Progressive Research Governance."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.research_governance.evidence_maturity import (
    assess_maturity, assess_decision, next_validation_step, estimate_consistency,
)
from research_engine.v10.research_governance.progressive_validator import (
    FindingHistory, compare_baseline_candidate, evaluate_optimisation,
)
from research_engine.v10.research_governance.models import validate_finding
from research_engine.v10.research_intelligence.models import ExperimentResult


# ═══════════════════════════════════════════════════════════════
# 1. SMALL POSITIVE → PROMISING (not INCONCLUSIVE)
# ═══════════════════════════════════════════════════════════════

class TestSmallPositivePromising:
    def test_12_trades_large_effect_is_promising(self):
        decision = assess_decision(
            sample_size=12, effect_size=0.25,
            confidence_score=0.3, maturity="EARLY",
        )
        assert decision["status"] == "PROMISING"

    def test_not_automatically_inconclusive(self):
        """Small positive sample must NOT default to INCONCLUSIVE."""
        exp = ExperimentResult(
            question_id="R2", question_name="Stop Effectiveness",
            sample_size=15, result={"expectancy_r": 0.22, "win_rate": 0.45},
            confidence="LOW", recommendation="SUPPORTED",
        )
        finding = validate_finding(exp)
        assert finding.decision_status != "INCONCLUSIVE"
        assert finding.decision_status in ("PROMISING", "CONTINUE_TESTING")


# ═══════════════════════════════════════════════════════════════
# 2. SMALL NEGATIVE → EARLY_FAILURE
# ═══════════════════════════════════════════════════════════════

class TestSmallNegativeEarlyFailure:
    def test_large_negative_detected_early(self):
        decision = assess_decision(
            sample_size=15, effect_size=-0.35,
            confidence_score=0.3, maturity="EARLY",
            is_deterioration=True,
        )
        assert decision["status"] == "EARLY_FAILURE"

    def test_baseline_deterioration_triggers_failure(self):
        decision = assess_decision(
            sample_size=12, effect_size=-0.4,
            confidence_score=0.3, maturity="EARLY",
            is_deterioration=True, baseline_delta=-0.4,
        )
        assert decision["status"] == "EARLY_FAILURE"


# ═══════════════════════════════════════════════════════════════
# 3. MEDIUM SAMPLE → CONTINUE_TESTING
# ═══════════════════════════════════════════════════════════════

class TestMediumContinueTesting:
    def test_25_trades_positive_continues(self):
        decision = assess_decision(
            sample_size=25, effect_size=0.12,
            confidence_score=0.5, maturity="DEVELOPING",
        )
        assert decision["status"] == "CONTINUE_TESTING"


# ═══════════════════════════════════════════════════════════════
# 4. STRONG EVIDENCE → SUPPORTED
# ═══════════════════════════════════════════════════════════════

class TestStrongSupported:
    def test_50_trades_positive_supported(self):
        decision = assess_decision(
            sample_size=50, effect_size=0.15,
            confidence_score=0.7, maturity="STRONG",
        )
        assert decision["status"] == "SUPPORTED"

    def test_validated_finding_with_large_sample(self):
        exp = ExperimentResult(
            question_id="E1", question_name="Expectancy",
            sample_size=60,
            result={"expectancy_r": 0.20, "win_rate": 0.55, "profit_factor": 1.5},
            confidence="HIGH", recommendation="SUPPORTED",
        )
        finding = validate_finding(exp)
        assert finding.decision_status == "SUPPORTED"
        assert finding.evidence_maturity in ("STRONG", "LONG_RUN")


# ═══════════════════════════════════════════════════════════════
# 5. EVIDENCE MATURITY PROGRESSION
# ═══════════════════════════════════════════════════════════════

class TestMaturityProgression:
    def test_3_trades_exploratory(self):
        assert assess_maturity(3) == "EXPLORATORY"

    def test_12_trades_large_effect_early(self):
        assert assess_maturity(12, effect_size=0.4) == "EARLY"

    def test_25_trades_consistent_developing(self):
        assert assess_maturity(25, effect_size=0.2, consistency=0.6) == "DEVELOPING"

    def test_45_trades_consistent_strong(self):
        assert assess_maturity(45, consistency=0.7) == "STRONG"

    def test_80_trades_long_run(self):
        assert assess_maturity(80, consistency=0.7) == "LONG_RUN"

    def test_progression_order(self):
        """Maturity must progress with increasing evidence."""
        levels = ["EXPLORATORY", "EARLY", "DEVELOPING", "STRONG", "LONG_RUN"]
        m1 = assess_maturity(3)
        m2 = assess_maturity(12, effect_size=0.3)
        m3 = assess_maturity(25, effect_size=0.2, consistency=0.6)
        m4 = assess_maturity(45, consistency=0.7)
        m5 = assess_maturity(80, consistency=0.7)
        assert levels.index(m1) <= levels.index(m2) <= levels.index(m3)
        assert levels.index(m3) <= levels.index(m4) <= levels.index(m5)


# ═══════════════════════════════════════════════════════════════
# 6. FINDING HISTORY RETAINED
# ═══════════════════════════════════════════════════════════════

class TestFindingHistory:
    def test_append_and_load(self, tmp_path):
        fh = FindingHistory(history_dir=str(tmp_path))
        fh.append("OPT-001", {"sample_size": 10, "decision": "PROMISING"})
        fh.append("OPT-001", {"sample_size": 25, "decision": "CONTINUE_TESTING"})
        history = fh.load("OPT-001")
        assert len(history) == 2
        assert history[0]["decision"] == "PROMISING"
        assert history[1]["decision"] == "CONTINUE_TESTING"

    def test_latest(self, tmp_path):
        fh = FindingHistory(history_dir=str(tmp_path))
        fh.append("OPT-002", {"sample_size": 10, "status": "EARLY"})
        fh.append("OPT-002", {"sample_size": 50, "status": "STRONG"})
        latest = fh.latest("OPT-002")
        assert latest["status"] == "STRONG"

    def test_empty_history(self, tmp_path):
        fh = FindingHistory(history_dir=str(tmp_path))
        assert fh.load("NONEXISTENT") == []
        assert fh.latest("NONEXISTENT") is None


# ═══════════════════════════════════════════════════════════════
# 7. BASELINE / CANDIDATE COMPARISON
# ═══════════════════════════════════════════════════════════════

class TestBaselineComparison:
    def test_improved_candidate(self):
        result = compare_baseline_candidate(
            baseline={"expectancy_r": -0.30, "win_rate": 0.33, "profit_factor": 0.8},
            candidate={"expectancy_r": -0.05, "win_rate": 0.42, "profit_factor": 1.2, "count": 30},
        )
        assert result["delta"]["expectancy_r"] == 0.25
        assert result["decision"] in ("PROMISING", "CONTINUE_TESTING", "KEEP")

    def test_deteriorated_candidate(self):
        result = compare_baseline_candidate(
            baseline={"expectancy_r": -0.10, "win_rate": 0.40},
            candidate={"expectancy_r": -0.65, "win_rate": 0.25, "count": 15},
        )
        assert result["decision"] == "EARLY_FAILURE"
        assert result["delta"]["expectancy_r"] < 0


# ═══════════════════════════════════════════════════════════════
# 8. TARGET QUESTION INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestTargetQuestions:
    def test_target_improved_no_regression(self):
        result = evaluate_optimisation(
            target_results=[{"question_id": "R2", "effect": 0.2, "sample_size": 35}],
            regression_results=[
                {"question_id": "E1", "effect": 0.01, "sample_size": 40},
                {"question_id": "R1", "effect": -0.02, "sample_size": 40},
            ],
        )
        assert result["decision"] in ("KEEP", "CONTINUE_TESTING")
        assert result["target_improved"] is True
        assert result["regression_detected"] is False


# ═══════════════════════════════════════════════════════════════
# 9. REGRESSION QUESTION DETECTION
# ═══════════════════════════════════════════════════════════════

class TestRegressionQuestions:
    def test_regression_detected(self):
        result = evaluate_optimisation(
            target_results=[{"question_id": "R2", "effect": 0.15, "sample_size": 20}],
            regression_results=[
                {"question_id": "E1", "effect": -0.15, "sample_size": 30},
            ],
        )
        assert result["regression_detected"] is True
        assert result["decision"] == "INVESTIGATE"


# ═══════════════════════════════════════════════════════════════
# 10. HARMFUL OPTIMISATION → ROLLBACK
# ═══════════════════════════════════════════════════════════════

class TestRollback:
    def test_major_regression_triggers_rollback(self):
        result = evaluate_optimisation(
            target_results=[{"question_id": "R2", "effect": 0.1, "sample_size": 20}],
            regression_results=[
                {"question_id": "E1", "effect": -0.35, "sample_size": 30},
            ],
        )
        assert result["major_regression"] is True
        assert result["decision"] == "ROLLBACK"


# ═══════════════════════════════════════════════════════════════
# 11. NO UNIVERSAL 2000-TRADE REQUIREMENT
# ═══════════════════════════════════════════════════════════════

class TestNoArbitraryThreshold:
    def test_15_trades_can_be_promising(self):
        """System must NOT require 2000 trades for a useful decision."""
        decision = assess_decision(
            sample_size=15, effect_size=0.3,
            confidence_score=0.35, maturity="EARLY",
        )
        assert decision["status"] != "INCONCLUSIVE"
        assert decision["status"] in ("PROMISING", "EARLY_FAILURE", "INVESTIGATE")

    def test_small_sample_with_clear_signal_not_blocked(self):
        exp = ExperimentResult(
            question_id="R2", question_name="Stop Test",
            sample_size=18, result={"expectancy_r": 0.25},
            confidence="LOW", recommendation="SUPPORTED",
        )
        finding = validate_finding(exp)
        # Must provide useful direction, not just "wait for more data"
        assert finding.decision_status in ("PROMISING", "CONTINUE_TESTING")
        assert finding.next_step != ""


# ═══════════════════════════════════════════════════════════════
# 12. NEXT VALIDATION STEP GENERATED
# ═══════════════════════════════════════════════════════════════

class TestNextStep:
    def test_promising_has_next_step(self):
        step = next_validation_step("PROMISING", "EARLY", 12)
        assert len(step) > 0
        assert "12" in step  # Should reference current sample

    def test_early_failure_says_stop(self):
        step = next_validation_step("EARLY_FAILURE", "EARLY", 15)
        assert "stop" in step.lower() or "Stop" in step

    def test_inconclusive_explains_why(self):
        step = next_validation_step("INCONCLUSIVE", "EXPLORATORY", 7)
        assert "missing" in step.lower() or "identify" in step.lower() or "7" in step


# ═══════════════════════════════════════════════════════════════
# 13. LAMBDA-COMPATIBLE EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestLambdaCompatible:
    def test_no_global_state(self, tmp_path):
        """All components work with explicit paths — no global dependency."""
        fh = FindingHistory(history_dir=str(tmp_path / "history"))
        fh.append("TEST-1", {"sample": 10, "decision": "PROMISING"})
        assert fh.latest("TEST-1")["decision"] == "PROMISING"

    def test_pure_function_calls(self):
        """Core functions are stateless and deterministic."""
        m1 = assess_maturity(20, 0.2, 0.5)
        m2 = assess_maturity(20, 0.2, 0.5)
        assert m1 == m2

        d1 = assess_decision(20, 0.15, 0.5, "DEVELOPING")
        d2 = assess_decision(20, 0.15, 0.5, "DEVELOPING")
        assert d1 == d2


# ═══════════════════════════════════════════════════════════════
# 14. EXISTING PHASE 5 COMPATIBILITY
# ═══════════════════════════════════════════════════════════════

class TestBackwardCompat:
    def test_validate_finding_still_returns_finding(self):
        exp = ExperimentResult(
            question_id="E1", question_name="Expectancy",
            sample_size=40, result={"expectancy_r": -0.18, "win_rate": 0.36},
            confidence="MEDIUM", recommendation="REJECTED",
        )
        finding = validate_finding(exp)
        # All original fields still present
        assert finding.question_id == "E1"
        assert finding.sample_size == 40
        assert finding.confidence_level in ("HIGH", "MEDIUM", "LOW")
        assert finding.confidence_score > 0
        # New fields also present
        assert finding.evidence_maturity != ""
        assert finding.decision_status != ""
        assert finding.next_step != ""

    def test_to_dict_has_new_blocks(self):
        exp = ExperimentResult(
            question_id="D1", question_name="Score",
            sample_size=30, result={"expectancy_r": 0.1},
            confidence="MEDIUM", recommendation="SUPPORTED",
        )
        finding = validate_finding(exp)
        d = finding.to_dict()
        assert "evidence" in d
        assert "maturity" in d["evidence"]
        assert "decision" in d
        assert "status" in d["decision"]
        assert "validation" in d
        assert "next_step" in d["validation"]
