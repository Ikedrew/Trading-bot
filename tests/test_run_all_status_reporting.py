"""
Gap 4 — run_all() status reporting tests.

Proves the summary reports the AUTHORITATIVE research-run status
(report["status"]) rather than the recommendation/action label
(report["recommendation"]), for every known runner result shape,
with n taken from the same authoritative result context.

All tests are synthetic - production AWS is NEVER touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.experiments.research_runner import (
    _extract_run_status,
    _extract_sample,
    run_all,
)


def _report(status: str, recommendation: Any = "COMPLETE", sample: int = 0,
            qid: str = "QTEST") -> dict[str, Any]:
    """Canonical build_report-shaped runner result (report_contract.py)."""
    return {
        "question_id": qid,
        "status": status,
        "overall": {"finding": "test"},
        "confidence": "LOW",
        "dataset": {"source": "decision_trace", "sample_size": sample},
        "fingerprint": {"dataset_id": "x", "records_used": sample,
                        "records_excluded": 0, "validation_score": "LOW"},
        "recommendation": recommendation,
        "assumptions": [],
        "warnings": [],
        "generated": "2026-09-06T00:00:00Z",
        "provenance": {"experiment_module": "test", "registry_id": qid},
    }


@pytest.fixture()
def offline_run_all(monkeypatch):
    """run_all() with dataset validation routed to synthetic offline data."""
    import research_engine.experiments.research_runner as rr
    import research_engine.runner_discovery as rd

    def _install(runners: dict[str, Any]):
        monkeypatch.setattr(rr, "ingest_completed_shadow_trades", lambda **k: [])
        monkeypatch.setattr(rr, "_load_jsonl", lambda dataset: [])
        monkeypatch.setattr(rd, "get_all_runners", lambda: runners)

    return _install


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS MAPPING
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatusMapping:
    def test_insufficient_data_stays_insufficient_data(self):
        """The exact E3/S1 defect: report INSUFFICIENT_DATA with a COMPLETE-like
        recommendation must summarize as INSUFFICIENT_DATA."""
        report = _report("INSUFFICIENT_DATA", recommendation="COMPLETE", sample=0)
        status, source = _extract_run_status(report)
        assert status == "INSUFFICIENT_DATA"
        assert source == "report"

    def test_wait_stays_wait(self):
        report = _report("WAIT", recommendation="WAIT")
        status, source = _extract_run_status(report)
        assert status == "WAIT" and source == "report"

    def test_blocked_stays_blocked(self):
        report = _report("BLOCKED", recommendation="BLOCKED")
        status, source = _extract_run_status(report)
        assert status == "BLOCKED" and source == "report"

    def test_complete_stays_complete(self):
        report = _report("COMPLETE", recommendation="COMPLETE", sample=2718)
        status, source = _extract_run_status(report)
        assert status == "COMPLETE" and source == "report"

    def test_waiting_data_stays_waiting_data(self):
        report = _report("WAITING_DATA", recommendation="MONITOR")
        status, source = _extract_run_status(report)
        assert status == "WAITING_DATA"

    def test_negative_edge_is_a_recommendation_not_a_status(self):
        """E1 shape: the research run COMPLETED with a NEGATIVE_EDGE finding."""
        report = _report("COMPLETE", recommendation="NEGATIVE_EDGE", sample=350)
        status, source = _extract_run_status(report)
        assert status == "COMPLETE"       # run status, accurately COMPLETE
        assert report["recommendation"] == "NEGATIVE_EDGE"  # finding preserved

    def test_status_source_recorded_for_recommendation_status(self):
        """Legacy family without top-level status: rec.status is surfaced
        explicitly as such — never silently presented as a report status."""
        legacy = {"question_id": "OLD", "recommendation": {"status": "COMPLETE", "target": "x"}}
        status, source = _extract_run_status(legacy)
        assert status == "COMPLETE" and source == "recommendation"


# ═══════════════════════════════════════════════════════════════════════════════
# MALFORMED / UNKNOWN RESULTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMalformedResults:
    def test_missing_status_and_recommendation_never_becomes_complete(self):
        report = {"question_id": "QX", "dataset": {"sample_size": 5}}
        status, source = _extract_run_status(report)
        assert status == "UNKNOWN_STATUS"
        assert source == "missing"
        assert status != "COMPLETE"

    def test_non_dict_result_is_malformed_not_complete(self):
        status, source = _extract_run_status("just a string")
        assert status == "MALFORMED_REPORT" and source == "error"

    def test_none_result_is_malformed(self):
        status, source = _extract_run_status(None)
        assert status == "MALFORMED_REPORT"

    def test_empty_recommendation_dict_without_status_is_unknown(self):
        status, _ = _extract_run_status({"recommendation": {"target": "x"}})
        assert status == "UNKNOWN_STATUS"

    def test_non_string_status_ignored(self):
        status, _ = _extract_run_status({"status": 123, "recommendation": {"status": "COMPLETE"}})
        # non-string top-level status is malformed data -> falls to legacy path
        assert status == "COMPLETE" and _extract_run_status({"status": 123})[0] == "UNKNOWN_STATUS"


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE SIZE CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSampleConsistency:
    def test_sample_from_same_authoritative_result(self):
        report = _report("COMPLETE", sample=2718)
        assert _extract_sample(report) == 2718

    def test_sample_zero_is_not_fabricated(self):
        report = _report("INSUFFICIENT_DATA", sample=0)
        assert _extract_sample(report) == 0

    def test_sample_missing_dataset_is_zero_not_fabricated(self):
        assert _extract_sample({"question_id": "QX"}) == 0
        assert _extract_sample(None) == 0

    def test_r_multiples_used_fallback(self):
        report = {"dataset": {"r_multiples_used": 142}}
        assert _extract_sample(report) == 142


# ═══════════════════════════════════════════════════════════════════════════════
# END-TO-END run_all()
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunAllEndToEnd:
    def test_run_all_summary_matches_report_status(self, offline_run_all):
        runners = {
            "E3": lambda: _report("INSUFFICIENT_DATA", recommendation="COMPLETE", sample=0, qid="E3"),
            "S1": lambda: _report("INSUFFICIENT_DATA", recommendation="COMPLETE", sample=0, qid="S1"),
            "X2": lambda: _report("COMPLETE", recommendation="COMPLETE", sample=12, qid="X2"),
            "M9": lambda: _report("WAIT", recommendation="WAIT", sample=0, qid="M9"),
            "E1": lambda: _report("COMPLETE", recommendation="NEGATIVE_EDGE", sample=350, qid="E1"),
        }
        offline_run_all(runners)
        results = run_all()

        # The bug case: COMPLETE (n=0) must be gone
        assert results["E3"]["status"] == "INSUFFICIENT_DATA"
        assert results["E3"]["sample"] == 0
        assert results["E3"]["status_source"] == "report"
        assert results["E3"]["recommendation"] == "COMPLETE"  # kept, separately
        assert results["S1"]["status"] == "INSUFFICIENT_DATA"

        assert results["X2"]["status"] == "COMPLETE"
        assert results["X2"]["sample"] == 12
        assert results["M9"]["status"] == "WAIT"
        # NEGATIVE_EDGE remains visible as the recommendation, status COMPLETE
        assert results["E1"]["status"] == "COMPLETE"
        assert results["E1"]["recommendation"] == "NEGATIVE_EDGE"

    def test_run_all_malformed_runner_is_explicit(self, offline_run_all):
        runners = {
            "QBROKEN": lambda: {"question_id": "QBROKEN"},       # no status at all
            "QCRASH": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        }
        offline_run_all(runners)
        results = run_all()
        assert results["QBROKEN"]["status"] == "UNKNOWN_STATUS"
        assert results["QBROKEN"]["status_source"] == "missing"
        assert results["QCRASH"]["status"] == "ERROR"
        assert "boom" in results["QCRASH"]["error"]

    def test_run_all_legacy_nested_recommendation_shape(self, offline_run_all):
        runners = {
            "QLEGACY": lambda: {"question_id": "QLEGACY",
                                "recommendation": {"status": "PROMOTE_CALIBRATION",
                                                   "target": "probability_model"},
                                "dataset": {"sample_size": 40}},
        }
        offline_run_all(runners)
        results = run_all()
        assert results["QLEGACY"]["status"] == "PROMOTE_CALIBRATION"
        assert results["QLEGACY"]["status_source"] == "recommendation"  # explicit, not silent
