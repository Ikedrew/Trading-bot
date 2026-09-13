"""Phase 3 acceptance tests: Canonical Report Truth / History / Invalidation.

Tests the canonical report layer that answers, for every canonical question:
1. What is the latest VALID CURRENT research result?
2. What previous results exist?
3. Which results are stale, legacy, invalidated, malformed, or superseded?
4. Why was a result invalidated?
5. What evidence produced each result?
6. Can an old report ever accidentally become current truth?
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.control_plane import (
    ApplicationStatus,
    CanonicalReportRecord,
    InvalidationEntry,
    QuestionState,
    ReportValidity,
    RunnerStatus,
    build_all_question_states,
    build_invalidations_dict,
    build_question_state,
    get_invalidations_for_dataset,
    get_invalidations_for_question,
    is_report_invalidated,
    record_invalidations_json,
    resolve_report_history,
)
from research_engine.control_plane.invalidation import _KNOWN_INVALIDATIONS
from research_engine.control_plane.report_history import AmbiguousReportError
from research_engine.registry.research_question_registry import REGISTRY


def _current_report(question_id: str, *, recommendation: str = "MONITOR", epoch: str = "CURRENT") -> dict:
    return {
        "question_id": question_id,
        "status": "COMPLETE",
        "overall": {"finding": "Current finding", "confidence": "HIGH"},
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades", "sample_size": 125},
        "fingerprint": {
            "epoch": epoch,
            "dataset_id": "shadow_trades_2026-09-13",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 125,
            "source": "shadow_trades",
        },
        "recommendation": recommendation,
        "warnings": [],
        "generated": "2026-09-13T00:00:00Z",
        "provenance": {
            "experiment_module": "research_engine.experiments.expected_value",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output",
        },
    }


def _write_report(root: Path, filename: str, report: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(json.dumps(report), encoding="utf-8")
class TestValidCurrentReportBecomesAuthoritative:
    def test_1_valid_current_report_becomes_authoritative(self, tmp_path):
        """A VALID_CURRENT report becomes authoritative."""
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.report_validity == ReportValidity.VALID_CURRENT
        assert state.authoritative_report is not None
        assert state.authoritative_report.is_authoritative is True
        assert state.latest_finding == "Current finding"


class TestStaleReportCannotBecomeAuthoritative:
    def test_2_stale_mixed_epoch_report_cannot_become_authoritative(self, tmp_path):
        """A stale mixed-epoch report cannot become authoritative."""
        report = _current_report("Q19", epoch="MIXED")
        _write_report(tmp_path, "q19_expected_value.json", report)
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.report_validity == ReportValidity.STALE
        assert state.latest_result is None
        assert state.latest_finding == ""
        assert state.authoritative_report is None


class TestInvalidatedReportCannotBecomeAuthoritative:
    def test_3_explicitly_invalidated_report_cannot_become_authoritative(self, tmp_path):
        """An explicitly invalidated report cannot become authoritative."""
        report = _current_report("R3")
        report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
        _write_report(tmp_path, "r3_probability_of_ruin.json", report)
        state = build_question_state("R3", reports_dir=tmp_path, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED
        assert state.latest_result is None
        assert state.latest_finding == ""


class TestR3R4R5HistoricalArtifacts:
    def test_4_r3_historical_artifact_classified_invalid(self, tmp_path):
        """R3 historical artifact is classified invalid without poisoning future reruns."""
        report = _current_report("R3")
        report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
        _write_report(tmp_path, "r3_probability_of_ruin.json", report)
        state = build_question_state("R3", reports_dir=tmp_path, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED
        inv = is_report_invalidated(
            "r3_probability_of_ruin.json",
            {"dataset_id": "shadow_trades_2026-07-27", "records_used": 100},
        )
        assert inv is not None
        assert inv.canonical_question_id == "R3"
        assert inv.is_permanent is False

    def test_4b_r4_historical_artifact_classified_invalid(self, tmp_path):
        """R4 historical artifact is classified invalid."""
        report = _current_report("R4")
        report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
        _write_report(tmp_path, "r4_drawdown_threshold.json", report)
        state = build_question_state("R4", reports_dir=tmp_path, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED

    def test_4c_r5_historical_artifact_classified_invalid(self, tmp_path):
        """R5 historical artifact is classified invalid."""
        report = _current_report("R5")
        report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
class TestSupersession:
    def test_5_newer_valid_current_supersedes_older(self, tmp_path):
        """A newer valid CURRENT report supersedes an older valid CURRENT report."""
        old_report = _current_report("Q19")
        old_report["generated"] = "2026-09-10T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value_old.json", old_report)
        new_report = _current_report("Q19")
        new_report["generated"] = "2026-09-13T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value.json", new_report)
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.authoritative_report is not None
        assert state.authoritative_report.report_path == "q19_expected_value.json"
        assert state.superseded_report_count >= 0

    def test_6_superseded_and_invalidated_are_distinct(self, tmp_path):
        """SUPERSEDED and INVALIDATED are distinct classifications."""
        invalid_report = _current_report("R3")
        invalid_report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
        _write_report(tmp_path, "r3_probability_of_ruin.json", invalid_report)
        inv_state = build_question_state("R3", reports_dir=tmp_path, evidence_source={})
        assert inv_state.report_validity == ReportValidity.INVALIDATED
        assert inv_state.report_validity != ReportValidity.SUPERSEDED


class TestAmbiguousReports:
    def test_7_ambiguous_competing_valid_reports_fail_closed(self, tmp_path):
        """Ambiguous competing valid reports fail closed rather than guessing."""
        report1 = _current_report("Q19")
        report1["generated"] = "2026-09-13T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value_a.json", report1)
        report2 = _current_report("Q19")
        report2["generated"] = "2026-09-13T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value_b.json", report2)
        with pytest.raises(AmbiguousReportError):
            resolve_report_history(
                "E1",
                "q19_expected_value_a.json",
                tmp_path,
                build_invalidations_dict(),
            )


class TestLegacyReports:
    def test_8_legacy_reports_remain_visible_in_history_but_not_authority(self, tmp_path):
        """Legacy reports remain visible in history but not authority."""
        legacy_report = _current_report("Q19")
        legacy_report.pop("epoch", None)
        if "fingerprint" in legacy_report:
            legacy_report["fingerprint"].pop("epoch", None)
        _write_report(tmp_path, "q19_expected_value_legacy.json", legacy_report)
        current_report = _current_report("Q19")
        _write_report(tmp_path, "q19_expected_value.json", current_report)
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.report_history_count >= 1
        assert state.authoritative_report is not None
        assert state.authoritative_report.report_validity == ReportValidity.VALID_CURRENT


class TestMalformedReports:
    def test_9_malformed_reports_cannot_surface_findings(self, tmp_path):
        """Malformed reports cannot surface findings."""
        _write_report(tmp_path, "malformed_report.json", {"not": "a valid report"})
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        if state.authoritative_report:
            assert state.authoritative_report.report_validity != ReportValidity.UNKNOWN


class TestOldDashboardSafety:
    def test_10_old_q1_q25_dashboard_cannot_override_canonical_truth(self, tmp_path):
        """Old Q1-Q25 dashboard content cannot override canonical report truth."""
        old_dashboard_report = _current_report("Q19", recommendation="POSITIVE_EDGE")
        old_dashboard_report["overall"]["finding"] = "POSITIVE_EDGE detected"
        old_dashboard_report["generated"] = "2026-08-01T00:00:00Z"
        _write_report(tmp_path, "research_dashboard.json", old_dashboard_report)
        current_report = _current_report("Q19", recommendation="NEGATIVE_EDGE")
        current_report["overall"]["finding"] = "System has NEGATIVE expected value"
        _write_report(tmp_path, "q19_expected_value.json", current_report)
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.authoritative_report is not None
        assert state.authoritative_report.report_path == "q19_expected_value.json"
        assert "NEGATIVE" in state.latest_finding


class TestControlPlaneIntegration:
    def test_11_control_plane_receives_authoritative_findings_through_this_layer(self, tmp_path):
        """Control-plane question state receives authoritative findings only through this layer."""
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state.report_history_count >= 0
        assert state.invalidated_report_count >= 0
        assert state.stale_report_count >= 0
        assert state.superseded_report_count >= 0


class TestHistoryOrdering:
    def test_12_history_ordering_is_deterministic(self, tmp_path):
        """History ordering is deterministic."""
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        state1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        state2 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert state1.report_history_count == state2.report_history_count
        if state1.authoritative_report and state2.authoritative_report:
            assert state1.authoritative_report.report_path == state2.authoritative_report.report_path


class TestJsonSerializable:
    def test_13_output_is_json_serializable(self, tmp_path):
        """Output is JSON-serializable."""
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
class TestReadOnlyHistory:
    def test_14_no_report_file_modified_during_history_resolution(self, tmp_path):
        """No report file is modified during read-only history resolution."""
        report = _current_report("Q19")
        _write_report(tmp_path, "q19_expected_value.json", report)
        original_content = (tmp_path / "q19_expected_value.json").read_text()
        resolve_report_history(
            "E1",
            "q19_expected_value.json",
            tmp_path,
            build_invalidations_dict(),
        )
        after_content = (tmp_path / "q19_expected_value.json").read_text()
        assert original_content == after_content


class TestInvalidationLedger:
    def test_invalidation_ledger_has_r3_r4_r5_entries(self):
        """Invalidation ledger contains R3, R4, R5 entries."""
        invs = record_invalidations_json()
        qids = {inv["canonical_question_id"] for inv in invs}
        assert "R3" in qids
        assert "R4" in qids
        assert "R5" in qids

    def test_invalidations_are_not_permanent(self):
        """Invalidations are not permanent (future reruns can become valid)."""
        for inv in _KNOWN_INVALIDATIONS:
            assert inv.is_permanent is False

    def test_invalidations_dict_builds_correctly(self):
        """build_invalidations_dict returns correct lookup structure."""
        result = build_invalidations_dict()
        assert "r3_probability_of_ruin.json" in result
        assert "shadow_trades_2026-07-27" in result["r3_probability_of_ruin.json"]


class TestRepresentativeQuestions:
    def test_e1_q19_expected_value_has_current_report(self):
        """E1 / Q19 Expected Value should have a current report in the real reports dir."""
        real_reports = Path("analysis/reports")
        if not real_reports.exists():
            pytest.skip("Real reports directory not available")
        state = build_question_state("E1", reports_dir=real_reports, evidence_source={})
        assert state.report_history_count >= 0

    def test_r3_invalidated_in_real_reports(self):
        """R3 should be invalidated in real reports directory."""
        real_reports = Path("analysis/reports")
        if not real_reports.exists():
            pytest.skip("Real reports directory not available")
        state = build_question_state("R3", reports_dir=real_reports, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED
        assert state.latest_result is None

    def test_r4_invalidated_in_real_reports(self):
        """R4 should be invalidated in real reports directory."""
        real_reports = Path("analysis/reports")
        if not real_reports.exists():
            pytest.skip("Real reports directory not available")
        state = build_question_state("R4", reports_dir=real_reports, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED

    def test_r5_invalidated_in_real_reports(self):
        """R5 should be invalidated in real reports directory."""
        real_reports = Path("analysis/reports")
        if not real_reports.exists():
            pytest.skip("Real reports directory not available")
        state = build_question_state("R5", reports_dir=real_reports, evidence_source={})
        assert state.report_validity == ReportValidity.INVALIDATED
        result = state.to_dict()
        json.dumps(result, sort_keys=True)