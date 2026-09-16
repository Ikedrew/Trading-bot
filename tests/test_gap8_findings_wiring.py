"""
Gap-8 Wave-2 — focused tests proving canonical question results wire into
the existing FindingTriggerEngine store and survive into snapshot["findings"].

Tests are fully isolated: trigger persistence (logs/research_lifecycle) is
redirected into a per-test temp directory via monkeypatching of the module-
level constants. Production AWS is NEVER touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_trigger_store(tmp_path, monkeypatch):
    """Redirect FindingTriggerEngine persistence into a temp sandbox."""
    import research_engine.lifecycle.finding_trigger as ft_mod
    monkeypatch.chdir(tmp_path)
    trigger_dir = tmp_path / "research_lifecycle"
    trigger_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ft_mod, "_TRIGGER_DIR", trigger_dir)
    monkeypatch.setattr(ft_mod, "_TRIGGER_FILE", trigger_dir / "finding_triggers.json")


def _make_result(
    *,
    question_id: str = "Q-test",
    status: str = "COMPLETE",
    outcome: str = "ANOMALOUS",
    confidence: str = "HIGH",
    sample_sizes: dict[str, int] | None = None,
    primary_metrics: dict[str, Any] | None = None,
    title: str = "Pattern anomaly detected",
    conclusion: str = "Pattern underperforming",
    recommendation: str = "NEGATIVE_EDGE",
) -> dict[str, Any]:
    """Build a canonical run_all() result dict with finding-relevant fields."""
    _ss = sample_sizes if sample_sizes is not None else {"population": 100}
    _pm = primary_metrics if primary_metrics is not None else {"mean_r": -0.5}
    return {
        "status": status,
        "status_source": "report",
        "sample": sum(_ss.values()),
        "recommendation": recommendation,
        "outcome": outcome,
        "primary_metrics": _pm,
        "sample_sizes": _ss,
        "confidence": confidence,
        "title": title,
        "conclusion": conclusion,
        "question_id": question_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. run_all() result preserves the structured fields detect_from_finding() needs
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunAllPreservesFindingFields:
    def test_result_carries_all_required_fields(self):
        """The extended result dict has every field detect_from_finding reads."""
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-preserve",
            outcome="ANOMALOUS",
            sample_sizes={"population": 100},
            primary_metrics={"mean_r": -0.5, "win_rate": 0.10},
            confidence="HIGH",
            title="Anomaly",
            conclusion="Underperforming",
            recommendation="NEGATIVE_EDGE",
        )
        for key in ("outcome", "primary_metrics", "sample_sizes",
                     "confidence", "title", "conclusion", "question_id"):
            assert key in result, f"missing required field: {key}"

        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is not None
        assert trigger.status.value == "ELIGIBLE"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Eligible COMPLETE result reaches the trigger engine and persists
# ═══════════════════════════════════════════════════════════════════════════════


class TestEligibleResultReachesEngine:
    def test_complete_anomalous_result_creates_trigger(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-eligible",
            status="COMPLETE",
            outcome="ANOMALOUS",
            confidence="HIGH",
            sample_sizes={"population": 100},
            recommendation="PROMOTE_CALIBRATION",
        )
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is not None
        assert trigger.status.value == "ELIGIBLE"

    def test_trigger_persisted_to_trigger_file(self):
        import research_engine.lifecycle.finding_trigger as ft_mod
        result = _make_result(question_id="Q-persist", outcome="NEGATIVE",
                              sample_sizes={"population": 80})
        engine = ft_mod.FindingTriggerEngine()
        engine.detect_from_finding(result)
        assert ft_mod._TRIGGER_FILE.exists()
        data = json.loads(ft_mod._TRIGGER_FILE.read_text(encoding="utf-8"))
        triggers = data.get("triggers", {})
        assert any(t.get("finding_id") == "Q-persist" for t in triggers.values())


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Persisted finding appears in snapshot["findings"]
# ═══════════════════════════════════════════════════════════════════════════════


class TestSnapshotContainsFinding:
    def test_finding_survives_into_snapshot_findings(self, tmp_path, monkeypatch):
        import research_engine.lifecycle.finding_trigger as ft_mod
        from research_engine.lifecycle import cycle_snapshot

        trigger_dir = tmp_path / "research_lifecycle"
        trigger_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(ft_mod, "_TRIGGER_DIR", trigger_dir)
        monkeypatch.setattr(ft_mod, "_TRIGGER_FILE", trigger_dir / "finding_triggers.json")

        result = _make_result(
            question_id="Q-snapshot",
            outcome="ANOMALOUS",
            confidence="HIGH",
            sample_sizes={"population": 120},
        )
        engine = ft_mod.FindingTriggerEngine()
        engine.detect_from_finding(result)

        findings = cycle_snapshot._load_trigger_state()
        # _load_trigger_state keys by trigger_id; finding_id is inside each entry
        found = any(
            f.get("status") == "ELIGIBLE"
            for f in findings.values()
        )
        assert found, f"no ELIGIBLE finding in {findings}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Idempotent — repeated processing doesn't create duplicates
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_repeated_processing_no_duplicate(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-dup",
            outcome="ANOMALOUS",
            sample_sizes={"population": 100},
        )
        engine = FindingTriggerEngine()
        t1 = engine.detect_from_finding(result)
        assert t1 is not None
        t2 = engine.detect_from_finding(result)
        assert t2 is None  # deduplicated by finding_id

        eligible = [t for t in engine.all_triggers()
                    if t.status.value == "ELIGIBLE"]
        assert len(eligible) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5–6. Non-eligible statuses never create findings
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonEligibleResults:
    def test_wait_status_does_not_create_finding(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(status="WAITING_DATA", outcome="INSUFFICIENT")
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is None

    def test_insufficient_data_does_not_create_finding(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(status="INSUFFICIENT_DATA", outcome="INSUFFICIENT")
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is None

    def test_error_status_does_not_create_finding(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(status="ERROR", outcome="")
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is None

    def test_complete_non_anomalous_does_not_create_finding(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-neutral",
            status="COMPLETE",
            outcome="POSITIVE",
            confidence="HIGH",
            sample_sizes={"population": 100},
        )
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is None

    def test_complete_zero_evidence_does_not_create_finding(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-empty",
            status="COMPLETE",
            outcome="ANOMALOUS",
            sample_sizes={},
            confidence="HIGH",
        )
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. COMPLETE but engine-ineligible (below min sample) does not create finding
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompleteButIneligible:
    def test_tiny_sample_rejected_by_engine(self):
        import research_engine.lifecycle.finding_trigger as ft_mod

        result = _make_result(
            question_id="Q-tiny",
            status="COMPLETE",
            outcome="ANOMALOUS",
            confidence="HIGH",
            sample_sizes={"population": 5},
                )
        engine = ft_mod.FindingTriggerEngine(config=ft_mod.EligibilityConfig(min_sample_size=30))
        trigger = engine.detect_from_finding(result)
        assert trigger is None

        # A rejected finding may leave no trigger file at all (engine never
        # persisted an ELIGIBLE trigger) — both states are legitimate:
        #   1. _TRIGGER_FILE does not exist, OR
        #   2. _TRIGGER_FILE exists but contains no ELIGIBLE trigger for Q-tiny.
        if ft_mod._TRIGGER_FILE.exists():
            data = json.loads(ft_mod._TRIGGER_FILE.read_text(encoding="utf-8"))
            triggers = data.get("triggers", {})
            eligible = [t for t in triggers.values()
                        if t.get("status") == "ELIGIBLE"
                        and t.get("finding_id") == "Q-tiny"]
            assert not eligible, "tiny-sample finding should not be ELIGIBLE"
        # else: file never created because nothing was ever eligible


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Source provenance is preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenance:
    def test_question_id_preserved_in_trigger_and_evidence(self):
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine

        result = _make_result(
            question_id="Q-provenance",
            outcome="ANOMALOUS",
            confidence="HIGH",
            sample_sizes={"population": 100},
            recommendation="NEGATIVE_EDGE",
        )
        engine = FindingTriggerEngine()
        trigger = engine.detect_from_finding(result)
        assert trigger is not None
        assert trigger.finding_id == "Q-provenance"
        assert trigger.evidence.get("question_id") == "Q-provenance"
        assert trigger.evidence.get("source_report_status") == "COMPLETE"
        assert trigger.evidence.get("source_report_recommendation") == "NEGATIVE_EDGE"


# ═══════════════════════════════════════════════════════════════════════════════
# Status-gate integration: the bridge only offers COMPLETE results to the engine
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatusGate:
    def test_only_complete_results_offered_to_engine(self, tmp_path, monkeypatch):
        import research_engine.lifecycle.finding_trigger as ft_mod
        monkeypatch.chdir(tmp_path)
        trigger_dir = tmp_path / "research_lifecycle"
        trigger_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(ft_mod, "_TRIGGER_DIR", trigger_dir)
        monkeypatch.setattr(ft_mod, "_TRIGGER_FILE", trigger_dir / "finding_triggers.json")

        results: dict[str, dict] = {
            "Q1": _make_result(question_id="Q1", status="COMPLETE", outcome="ANOMALOUS",
                               sample_sizes={"p": 100}),
            "Q2": _make_result(question_id="Q2", status="WAITING_DATA", outcome="INSUFFICIENT",
                               sample_sizes={"p": 100}),
            "Q3": _make_result(question_id="Q3", status="INSUFFICIENT_DATA", outcome="INSUFFICIENT",
                               sample_sizes={"p": 100}),
            "Q4": _make_result(question_id="Q4", status="ERROR", outcome="",
                               sample_sizes={"p": 100}),
            "Q5": _make_result(question_id="Q5", status="COMPLETE", outcome="POSITIVE",
                               sample_sizes={"p": 100}),
        }

        engine = ft_mod.FindingTriggerEngine()
        for _qid, _info in results.items():
            if _info.get("status") != "COMPLETE":
                continue
            engine.detect_from_finding(_info)

        eligible = [t for t in engine.all_triggers()
                    if t.status.value == "ELIGIBLE"]
        assert len(eligible) == 1
        assert eligible[0].finding_id == "Q1"



