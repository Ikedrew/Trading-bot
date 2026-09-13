"""Phase 3 correction tests: Per-question report history scoping."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.control_plane import (
    ReportValidity,
    build_question_state,
)


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


class TestReportHistoryScoping:
    def test_1_e1_report_appears_only_in_e1_history(self, tmp_path):
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        d2 = build_question_state("D2", reports_dir=tmp_path, evidence_source={})
        e4 = build_question_state("E4", reports_dir=tmp_path, evidence_source={})
        s5 = build_question_state("S5", reports_dir=tmp_path, evidence_source={})
        assert e1.report_history_count == 1
        assert e1.authoritative_report.report_path == "q19_expected_value.json"
        assert d2.report_history_count == 0
        assert e4.report_history_count == 0
        assert s5.report_history_count == 0

    def test_2_e1_report_does_not_increase_other_counts(self, tmp_path):
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        _write_report(tmp_path, "q4_confidence_calibration.json", _current_report("Q20"))
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        d2 = build_question_state("D2", reports_dir=tmp_path, evidence_source={})
        e4 = build_question_state("E4", reports_dir=tmp_path, evidence_source={})
        assert e1.report_history_count == 1
        assert d2.report_history_count == 1
        assert e4.report_history_count == 0

    def test_3_no_reports_has_zero_history(self, tmp_path):
        s5 = build_question_state("S5", reports_dir=tmp_path, evidence_source={})
        assert s5.report_history_count == 0
        assert s5.report_validity == ReportValidity.MISSING
        assert s5.invalidated_report_count == 0

    def test_4_mixed_epoch_q19_stays_in_e1(self, tmp_path):
        mixed = _current_report("Q19", epoch="MIXED")
        _write_report(tmp_path, "q19_expected_value.json", mixed)
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert e1.report_history_count == 1
        assert e1.report_validity == ReportValidity.STALE

    def test_5_r3_r4_r5_historical_invalidated(self, tmp_path):
        for qid, fn in [("R3", "r3_probability_of_ruin.json"),
                        ("R4", "r4_drawdown_threshold.json"),
                        ("R5", "r5_position_sizing.json")]:
            r = _current_report(qid)
            r["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
            _write_report(tmp_path, fn, r)
        r3 = build_question_state("R3", reports_dir=tmp_path, evidence_source={})
        r4 = build_question_state("R4", reports_dir=tmp_path, evidence_source={})
        r5 = build_question_state("R5", reports_dir=tmp_path, evidence_source={})
        assert r3.report_history_count == 1
        assert r4.report_history_count == 1
        assert r5.report_history_count == 1
        assert r3.report_validity == ReportValidity.INVALIDATED
        assert r4.report_validity == ReportValidity.INVALIDATED
        assert r5.report_validity == ReportValidity.INVALIDATED
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert e1.report_history_count == 0

    def test_6_unrelated_identity_excluded_not_invalidated(self, tmp_path):
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        e4 = build_question_state("E4", reports_dir=tmp_path, evidence_source={})
        assert e4.report_history_count == 0
        assert e4.invalidated_report_count == 0
        s5 = build_question_state("S5", reports_dir=tmp_path, evidence_source={})
        assert s5.report_history_count == 0

    def test_7_legacy_alias_routes_correctly(self, tmp_path):
        _write_report(tmp_path, "q4_confidence_calibration.json", _current_report("Q20"))
        d2 = build_question_state("D2", reports_dir=tmp_path, evidence_source={})
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert d2.report_history_count == 1
        assert e1.report_history_count == 0

    def test_8_authoritative_selection_unchanged(self, tmp_path):
        _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert e1.authoritative_report is not None
        assert e1.authoritative_report.is_authoritative is True

    def test_9_supersession_unchanged(self, tmp_path):
        old = _current_report("Q19")
        old["generated"] = "2026-09-10T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value_old.json", old)
        new = _current_report("Q19")
        new["generated"] = "2026-09-13T00:00:00Z"
        _write_report(tmp_path, "q19_expected_value.json", new)
        e1 = build_question_state("E1", reports_dir=tmp_path, evidence_source={})
        assert e1.report_history_count == 2
        assert e1.authoritative_report.report_path == "q19_expected_value.json"
        assert e1.superseded_report_count == 1