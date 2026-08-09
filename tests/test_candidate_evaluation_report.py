"""Tests for V10 Candidate Evaluation Dashboard."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.candidates import (
    CandidateRegistry, CandidateRecord, CandidateStatus, CandidateEvaluationReport,
)
from research_engine.v10.candidates.models import ValidationEntry


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def populated_registry(tmp_path):
    """Registry with several candidates in different states."""
    reg = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))

    # High-priority validated candidate
    c1 = CandidateRecord(
        candidate_id="V10.1_STOP_ATR_2.0",
        hypothesis_id="HYP_R2_001",
        baseline_id="V10_BASELINE",
        component="risk.stop_model",
        created_from_question="R2",
        created_from_campaign="FX_OPT_V1",
        change_definition={"atr_multiplier": {"before": 1.5, "after": 2.0}},
        risk_level="LOW",
    )
    reg.create(c1)
    reg.update_status("V10.1_STOP_ATR_2.0", "VALIDATING")
    reg.add_validation_result(
        "V10.1_STOP_ATR_2.0",
        validation_id="VAL_001", decision="IMPROVED",
        confidence="MEDIUM", sample_size=87, expectancy_delta=0.22,
    )
    reg.update_status("V10.1_STOP_ATR_2.0", "VALIDATED")

    # Proposed candidate
    reg.create(CandidateRecord(
        candidate_id="V10.1_SCORE_THRESHOLD",
        hypothesis_id="HYP_D3_001",
        baseline_id="V10_BASELINE",
        component="decision.threshold",
        created_from_question="D3",
        risk_level="LOW",
    ))

    # Rejected candidate
    c3 = CandidateRecord(
        candidate_id="V10.1_SESSION_FILTER",
        hypothesis_id="HYP_C1_001",
        baseline_id="V10_BASELINE",
        component="market.session_filter",
        created_from_question="C1",
        risk_level="MEDIUM",
    )
    reg.create(c3)
    reg.update_status("V10.1_SESSION_FILTER", "VALIDATING")
    reg.add_validation_result(
        "V10.1_SESSION_FILTER",
        validation_id="VAL_002", decision="REGRESSION",
        confidence="MEDIUM", sample_size=60, expectancy_delta=-0.15,
        regressions=["expectancy_r"],
    )
    reg.update_status("V10.1_SESSION_FILTER", "REJECTED")

    return reg


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestEmptyRegistry:
    def test_empty_produces_valid_report(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path / "empty"))
        report = CandidateEvaluationReport(registry=reg, reports_dir=str(tmp_path / "rep"))
        result = report.generate()
        assert result["total_candidates"] == 0
        assert result["candidates"] == []
        assert result["recommended_actions"] == []


class TestActiveCandidate:
    def test_active_candidate_appears(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        result = report.generate()
        validating = [c for c in result["candidates"] if c["status"] == "VALIDATED"]
        assert len(validating) == 1
        assert validating[0]["candidate_id"] == "V10.1_STOP_ATR_2.0"
        assert validating[0]["health"] == "HEALTHY"


class TestRejectedCandidate:
    def test_rejected_appears_correctly(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        result = report.generate()
        rejected = [c for c in result["candidates"] if c["status"] == "REJECTED"]
        assert len(rejected) == 1
        assert rejected[0]["health"] == "FAILED"


class TestPriorityRanking:
    def test_higher_evidence_ranks_first(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        result = report.generate()
        # V10.1_STOP_ATR_2.0 has validation evidence → should rank higher
        candidates = result["candidates"]
        stop_idx = next(i for i, c in enumerate(candidates) if c["candidate_id"] == "V10.1_STOP_ATR_2.0")
        score_idx = next(i for i, c in enumerate(candidates) if c["candidate_id"] == "V10.1_SCORE_THRESHOLD")
        assert stop_idx < score_idx  # Higher priority = earlier in list


class TestNextAction:
    def test_proposed_gets_run_validation(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        result = report.generate()
        proposed = next(c for c in result["candidates"] if c["status"] == "PROPOSED")
        assert "validation" in proposed["next_action"].lower() or "Run" in proposed["next_action"]

    def test_validated_gets_proceed(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        result = report.generate()
        validated = next(c for c in result["candidates"] if c["status"] == "VALIDATED")
        assert "shadow" in validated["next_action"].lower() or "review" in validated["next_action"].lower()


class TestReportPersistence:
    def test_json_and_md_generated(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        report.generate()
        assert (tmp_path / "candidate_evaluation_report.json").exists()
        assert (tmp_path / "candidate_evaluation_report.md").exists()

    def test_json_valid(self, populated_registry, tmp_path):
        report = CandidateEvaluationReport(
            registry=populated_registry, reports_dir=str(tmp_path)
        )
        report.generate()
        data = json.loads((tmp_path / "candidate_evaluation_report.json").read_text(encoding="utf-8"))
        assert "total_candidates" in data
        assert "candidates" in data
        assert "recommended_actions" in data
