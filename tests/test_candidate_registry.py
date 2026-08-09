"""Tests for V10 Candidate Registry."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.candidates import CandidateRegistry, CandidateRecord, CandidateStatus
from research_engine.v10.candidates.candidate_lifecycle import (
    is_valid_transition, validate_transition, is_active,
)
from research_engine.v10.candidates.candidate_report import generate_candidate_dashboard


# ═══════════════════════════════════════════════════════════════
# MODEL (1-2)
# ═══════════════════════════════════════════════════════════════

class TestModel:
    def test_record_creation(self):
        r = CandidateRecord(
            candidate_id="V10.1_TEST",
            hypothesis_id="HYP_R2",
            baseline_id="BASE_001",
            component="risk.stop_model",
        )
        assert r.candidate_id == "V10.1_TEST"
        assert r.created_at != ""
        assert r.status == CandidateStatus.PROPOSED

    def test_required_fields(self):
        r = CandidateRecord(candidate_id="X")
        d = r.to_dict()
        assert "candidate_id" in d
        assert "status" in d
        assert "validation_history" in d
        assert "status_history" in d


# ═══════════════════════════════════════════════════════════════
# LIFECYCLE (3-4)
# ═══════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_valid_transitions(self):
        assert is_valid_transition("PROPOSED", "VALIDATING")
        assert is_valid_transition("VALIDATING", "VALIDATED")
        assert is_valid_transition("VALIDATED", "SHADOW_TESTING")
        assert is_valid_transition("SHADOW_TESTING", "READY_FOR_REVIEW")
        assert is_valid_transition("READY_FOR_REVIEW", "ACCEPTED")
        assert is_valid_transition("READY_FOR_REVIEW", "REJECTED")

    def test_invalid_transitions_rejected(self):
        # Cannot jump from PROPOSED to ACCEPTED
        assert not is_valid_transition("PROPOSED", "ACCEPTED")
        # Cannot jump from PROPOSED to SHADOW_TESTING
        assert not is_valid_transition("PROPOSED", "SHADOW_TESTING")
        # Cannot go backward from ACCEPTED
        assert not is_valid_transition("ACCEPTED", "PROPOSED")

        with pytest.raises(ValueError):
            validate_transition("PROPOSED", "ACCEPTED")


# ═══════════════════════════════════════════════════════════════
# REGISTRY CRUD (5-8)
# ═══════════════════════════════════════════════════════════════

class TestRegistryCRUD:
    def test_create(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        r = CandidateRecord(candidate_id="C1", baseline_id="B1")
        reg.create(r)
        assert reg.get("C1") is not None

    def test_save_persists(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="C2", component="Risk"))
        # Load fresh registry
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert reg2.get("C2") is not None
        assert reg2.get("C2").component == "Risk"

    def test_load(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="L1", description="Load test"))
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        loaded = reg2.get("L1")
        assert loaded is not None
        assert loaded.description == "Load test"

    def test_list_all(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="A"))
        reg.create(CandidateRecord(candidate_id="B"))
        reg.create(CandidateRecord(candidate_id="C"))
        assert len(reg.list_all()) == 3

    def test_duplicate_rejected(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="DUP"))
        with pytest.raises(ValueError):
            reg.create(CandidateRecord(candidate_id="DUP"))


# ═══════════════════════════════════════════════════════════════
# FILTERING (9-10)
# ═══════════════════════════════════════════════════════════════

class TestFiltering:
    def test_list_by_status(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="F1"))
        reg.create(CandidateRecord(candidate_id="F2"))
        reg.update_status("F2", "VALIDATING")
        proposed = reg.list_by_status("PROPOSED")
        validating = reg.list_by_status("VALIDATING")
        assert len(proposed) == 1
        assert len(validating) == 1

    def test_list_active(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="A1"))
        reg.create(CandidateRecord(candidate_id="A2"))
        reg.update_status("A2", "VALIDATING")
        reg.update_status("A2", "REJECTED")
        active = reg.list_active()
        assert len(active) == 1
        assert active[0].candidate_id == "A1"


# ═══════════════════════════════════════════════════════════════
# VALIDATION LINK (11-12)
# ═══════════════════════════════════════════════════════════════

class TestValidationLink:
    def test_attach_validation_result(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="V1"))
        reg.add_validation_result(
            "V1",
            validation_id="VAL_001",
            decision="IMPROVED",
            confidence="MEDIUM",
            sample_size=30,
            expectancy_delta=0.15,
        )
        c = reg.get("V1")
        assert len(c.validation_history) == 1
        assert c.validation_history[0].decision == "IMPROVED"

    def test_preserve_history(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(candidate_id="V2"))
        reg.add_validation_result("V2", validation_id="VAL_A", decision="INCONCLUSIVE")
        reg.add_validation_result("V2", validation_id="VAL_B", decision="IMPROVED")
        c = reg.get("V2")
        assert len(c.validation_history) == 2
        assert c.validation_history[0].validation_id == "VAL_A"
        assert c.validation_history[1].validation_id == "VAL_B"


# ═══════════════════════════════════════════════════════════════
# RESEARCH LINK (13-14)
# ═══════════════════════════════════════════════════════════════

class TestResearchLink:
    def test_originating_question_preserved(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(
            candidate_id="RQ1",
            created_from_question="R2",
            created_from_campaign="FX_OPT_V1",
        ))
        c = reg.get("RQ1")
        assert c.created_from_question == "R2"
        assert c.created_from_campaign == "FX_OPT_V1"

    def test_hypothesis_reference_preserved(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(CandidateRecord(
            candidate_id="RH1",
            hypothesis_id="HYP_R2_STOP_WIDTH",
        ))
        c = reg.get("RH1")
        assert c.hypothesis_id == "HYP_R2_STOP_WIDTH"


# ═══════════════════════════════════════════════════════════════
# REPORTING (15-16)
# ═══════════════════════════════════════════════════════════════

class TestReporting:
    def test_generate_dashboard(self, tmp_path):
        candidates = [
            CandidateRecord(candidate_id="D1", status="PROPOSED", component="Risk"),
            CandidateRecord(candidate_id="D2", status="VALIDATING", component="Stop"),
            CandidateRecord(candidate_id="D3", status="REJECTED", component="Entry"),
        ]
        report = generate_candidate_dashboard(candidates, reports_dir=str(tmp_path))
        assert report["total_candidates"] == 3
        assert report["active"] == 2
        assert report["rejected"] == 1

    def test_report_files_created(self, tmp_path):
        candidates = [CandidateRecord(candidate_id="RF1")]
        generate_candidate_dashboard(candidates, reports_dir=str(tmp_path))
        assert (tmp_path / "candidate_registry_report.json").exists()
        assert (tmp_path / "candidate_registry_report.md").exists()


# ═══════════════════════════════════════════════════════════════
# INTEGRATION (17)
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_flow(self, tmp_path):
        """Finding → hypothesis → candidate → registry → validation result."""
        reg = CandidateRegistry(storage_dir=str(tmp_path))

        # Create from research finding
        reg.create(CandidateRecord(
            candidate_id="V10.1_STOP_ATR_2.0",
            hypothesis_id="HYP_R2_001",
            baseline_id="V10_BASELINE_20260807",
            component="risk.stop_model",
            created_from_question="R2",
            created_from_campaign="FX_OPT_V1",
            change_definition={"atr_multiplier": {"before": 1.5, "after": 2.0}},
            expected_outcome="Wider stops improve survival",
            risk_level="LOW",
        ))

        # Progress through lifecycle
        reg.update_status("V10.1_STOP_ATR_2.0", "VALIDATING")
        reg.add_validation_result(
            "V10.1_STOP_ATR_2.0",
            validation_id="VAL_001",
            decision="IMPROVED",
            confidence="MEDIUM",
            sample_size=94,
            expectancy_delta=0.22,
        )
        reg.update_status("V10.1_STOP_ATR_2.0", "VALIDATED")

        # Verify full state
        c = reg.get("V10.1_STOP_ATR_2.0")
        assert c.status == "VALIDATED"
        assert c.hypothesis_id == "HYP_R2_001"
        assert c.baseline_id == "V10_BASELINE_20260807"
        assert c.created_from_question == "R2"
        assert len(c.validation_history) == 1
        assert len(c.status_history) >= 3  # PROPOSED, VALIDATING, VALIDATED
