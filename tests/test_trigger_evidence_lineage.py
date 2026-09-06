"""
Gap 7 — finding/trigger evidence payload + lineage tests.

Proves:
  - qualifying triggers carry the statistical summary PLUS provenance
    (question/experiment identity, source datasets, fingerprint, as-of)
  - optional values stay honestly absent (never "unknown", never fabricated)
  - no raw dataset is embedded in trigger evidence
  - trigger -> hypothesis -> candidate lineage is reconstructable
  - week-on-week: stronger evidence reconfirms the SAME trigger identity;
    identical reruns are idempotent; stale evidence cannot overwrite newer
  - eligibility thresholds are untouched (tiny-N / ineligible never trigger)

All tests are synthetic - production AWS is NEVER touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.lifecycle.finding_trigger import (
    EligibilityConfig,
    FindingTriggerEngine,
    TriggerStatus,
    build_evidence_provenance,
    stamp_provenance,
)
from research_engine.v10.candidates.models import CandidateRecord


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """Trigger persistence (logs/research_lifecycle) lands in a sandbox."""
    monkeypatch.chdir(tmp_path)


def _engine() -> FindingTriggerEngine:
    return FindingTriggerEngine(config=EligibilityConfig(min_sample_size=30))


def _provenance() -> dict[str, Any]:
    return {
        "source_datasets": ["shadow_runtime_v1(ingested)"],
        "dataset_fingerprint": "abc123def4567890",
        "evidence_as_of": "2026-09-06T12:00:00+00:00",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestTriggerConstruction:
    def test_qualifying_finding_gets_stats_plus_provenance(self):
        engine = _engine()
        trigger = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.35, win_rate=0.10, sample_size=50,
            source="research_cycle_runner",
        )
        assert trigger is not None and trigger.status == TriggerStatus.ELIGIBLE
        # statistical summary — values exactly match the originating result
        assert trigger.evidence["mean_r"] == -0.35
        assert trigger.evidence["win_rate"] == 0.10
        assert trigger.evidence["n"] == 50
        assert trigger.evidence["pattern"] == "HAMMER"
        # provenance stamped by the weekly cycle
        stamp_provenance([trigger], **_provenance())
        assert trigger.evidence["source_datasets"] == ["shadow_runtime_v1(ingested)"]
        assert trigger.evidence["dataset_fingerprint"] == "abc123def4567890"
        assert trigger.evidence["evidence_as_of"] == "2026-09-06T12:00:00+00:00"
        # stats untouched by stamping
        assert trigger.evidence["mean_r"] == -0.35

    def test_question_finding_carries_question_provenance(self):
        engine = _engine()
        finding = {
            "question_id": "Q16",
            "status": "COMPLETE",
            "recommendation": "SHADOW_UNRELIABLE",
            "confidence": "MEDIUM",
            "outcome": "NEGATIVE",
            "title": "Shadow model unreliable",
            "conclusion": "Correlation below threshold",
            "primary_metrics": {"correlation": 0.31},
            "sample_sizes": {"matched": 40},
        }
        trigger = engine.detect_from_finding(finding)
        assert trigger is not None
        assert trigger.evidence["question_id"] == "Q16"
        assert trigger.evidence["source_report_status"] == "COMPLETE"
        assert trigger.evidence["source_report_recommendation"] == "SHADOW_UNRELIABLE"
        assert trigger.evidence["correlation"] == 0.31   # summary preserved
        assert "evidence_as_of" in trigger.evidence

    def test_missing_optional_values_stay_absent_not_fabricated(self):
        prov = build_evidence_provenance(question_id="Q1")
        assert prov == {"question_id": "Q1"}
        assert "experiment_id" not in prov
        assert "source_report_status" not in prov
        assert "dataset_fingerprint" not in prov
        assert "unknown" not in json.dumps(prov).lower()

        # finding without status/recommendation -> those keys absent
        engine = _engine()
        trigger = engine.detect_from_finding({
            "question_id": "Q9", "outcome": "ANOMALOUS", "confidence": "HIGH",
            "primary_metrics": {"spread": 0.4}, "sample_sizes": {"ctx": 40},
        })
        assert trigger is not None
        assert "source_report_status" not in trigger.evidence
        assert "question_id" in trigger.evidence

    def test_empty_metrics_still_carries_provenance_not_fake_stats(self):
        engine = _engine()
        trigger = engine.detect_from_finding({
            "question_id": "Q4", "outcome": "ANOMALOUS", "confidence": "MEDIUM",
            "primary_metrics": {}, "sample_sizes": {"calibration": 45},
        })
        assert trigger is not None
        assert trigger.evidence["question_id"] == "Q4"
        assert "mean_r" not in trigger.evidence          # no fabricated statistic
        assert "correlation" not in trigger.evidence

    def test_no_raw_dataset_embedded_in_evidence(self):
        engine = _engine()
        triggers = engine.detect_direction_asymmetry(
            [{"pattern": "HAMMER", "direction": d, "r_multiple": r}
             for d, rs in (("BUY", [1.5] * 25), ("SELL", [-1.0] * 25))
             for r in rs],
            source="research_cycle_runner",
        )
        assert triggers, "asymmetric pattern should trigger"
        trigger = triggers[0]
        stamp_provenance([trigger], **_provenance())
        for key, value in trigger.evidence.items():
            if isinstance(value, list):
                # only small lists of scalars (quartile means / dataset names)
                assert len(value) <= 10, f"{key} embeds a large array"
                assert all(isinstance(v, (str, int, float)) for v in value)
            else:
                assert isinstance(value, (str, int, float, bool, type(None)))


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE DETERMINISM
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenanceDeterminism:
    def test_provenance_keys_are_deterministic(self):
        a = build_evidence_provenance(
            source_datasets=["shadow_runtime_v1(ingested)"],
            dataset_fingerprint="fp1", evidence_as_of="T1",
        )
        b = build_evidence_provenance(
            source_datasets=["shadow_runtime_v1(ingested)"],
            dataset_fingerprint="fp1", evidence_as_of="T1",
        )
        assert a == b

    def test_stamp_never_overwrites_explicit_provenance(self):
        engine = _engine()
        trigger = engine.detect_from_finding({
            "question_id": "Q16", "outcome": "NEGATIVE", "confidence": "MEDIUM",
            "primary_metrics": {}, "sample_sizes": {"m": 40},
        })
        stamp_provenance([trigger], question_id="SHOULD_NOT_OVERWRITE",
                         dataset_fingerprint="fp")
        assert trigger.evidence["question_id"] == "Q16"   # explicit wins
        assert trigger.evidence["dataset_fingerprint"] == "fp"


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE TRACE (trigger -> hypothesis -> candidate)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycleTrace:
    def test_trigger_to_hypothesis_lineage_survives_persistence(self):
        engine = _engine()
        trigger = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.35, win_rate=0.10, sample_size=50)
        stamp_provenance([trigger], **_provenance())
        engine.mark_registered(trigger.trigger_id, "HYP-1")

        # reload from disk (persistence round-trip)
        engine2 = _engine()
        stored = engine2.get(trigger_id=trigger.trigger_id)
        assert stored is not None
        assert stored.hypothesis_id == "HYP-1"
        assert stored.evidence["dataset_fingerprint"] == "abc123def4567890"
        assert stored.evidence["mean_r"] == -0.35

    def test_candidate_to_canonical_evidence_trace_reconstructable(self):
        """candidate_id -> hypothesis_id -> trigger -> provenance -> datasets."""
        engine = _engine()
        trigger = engine.detect_from_pattern_performance(
            pattern="TWS", mean_r=-0.4, win_rate=0.05, sample_size=60)
        stamp_provenance([trigger], **_provenance())
        engine.mark_registered(trigger.trigger_id, "HYP-77")
        stored_trigger = engine.get(trigger_id=trigger.trigger_id).to_dict()

        # hypothesis record (orchestrator stores source_finding_id = finding_id)
        hypothesis = {"hypothesis_id": "HYP-77",
                      "source_finding_id": stored_trigger["finding_id"],
                      "status": "CONCLUDED", "conclusion": "VALIDATED"}
        # candidate record (registry stores hypothesis_id)
        candidate = CandidateRecord(candidate_id="OPT-1", hypothesis_id="HYP-77",
                                    status="PROPOSED")

        # full chain walk
        assert candidate.hypothesis_id == hypothesis["hypothesis_id"]
        assert hypothesis["source_finding_id"] == stored_trigger["finding_id"]
        assert stored_trigger["hypothesis_id"] == candidate.hypothesis_id
        assert stored_trigger["evidence"]["source_datasets"] == ["shadow_runtime_v1(ingested)"]
        assert stored_trigger["evidence"]["mean_r"] == -0.4   # the justifying statistic


# ═══════════════════════════════════════════════════════════════════════════════
# WEEK-ON-WEEK
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeekOnWeek:
    def test_stronger_evidence_reconfirms_same_trigger_identity(self):
        engine = _engine()
        first = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.35, win_rate=0.10, sample_size=40)
        stamp_provenance([first], **_provenance())
        original_id = first.trigger_id

        # week 4: same finding, larger sample -> reconfirm, no new trigger
        again = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.38, win_rate=0.08, sample_size=130)
        assert again is None                       # no duplicate trigger minted
        existing = engine.get(trigger_id=original_id)
        assert existing.status == TriggerStatus.ELIGIBLE
        assert existing.sample_size == 130
        assert existing.evidence["n"] == 130
        assert existing.evidence["mean_r"] == -0.38
        # exactly one live trigger for this finding
        live = [t for t in engine.all_triggers()
                if t.status == TriggerStatus.ELIGIBLE]
        assert len(live) == 1

    def test_identical_rerun_is_idempotent(self):
        engine = _engine()
        first = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.35, win_rate=0.10, sample_size=40)
        snapshot = json.dumps(first.to_dict(), sort_keys=True)

        again = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.35, win_rate=0.10, sample_size=40)
        assert again is None
        # existing trigger untouched by identical evidence
        assert json.dumps(engine.get(trigger_id=first.trigger_id).to_dict(),
                          sort_keys=True) == snapshot

    def test_stale_evidence_cannot_overwrite_newer(self):
        engine = _engine()
        first = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.38, win_rate=0.08, sample_size=130)
        older = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.30, win_rate=0.12, sample_size=40)
        assert older is None
        existing = engine.get(trigger_id=first.trigger_id)
        assert existing.sample_size == 130          # newer evidence preserved
        assert existing.evidence["mean_r"] == -0.38


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY — thresholds untouched
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    def test_tiny_n_cannot_trigger_even_with_rich_provenance(self):
        engine = _engine()
        trigger = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.90, win_rate=0.0, sample_size=5)
        assert trigger is None  # below min_sample_size — evidence metadata is irrelevant

    def test_insufficient_report_outcome_cannot_become_eligible(self):
        engine = _engine()
        # INSUFFICIENT outcome is not ANOMALOUS/NEGATIVE -> never triggers
        assert engine.detect_from_finding({
            "question_id": "Q2", "status": "INSUFFICIENT_DATA", "outcome": "INSUFFICIENT",
            "confidence": "LOW", "primary_metrics": {"ev": 0.9},
            "sample_sizes": {"x": 500},
        }) is None

    def test_blocked_report_outcome_cannot_become_eligible(self):
        engine = _engine()
        assert engine.detect_from_finding({
            "question_id": "Q9", "status": "BLOCKED", "outcome": "POSITIVE",
            "confidence": "HIGH", "primary_metrics": {"ev": 0.9},
            "sample_sizes": {"x": 500},
        }) is None

    def test_eligibility_config_unchanged(self):
        cfg = EligibilityConfig()
        assert cfg.min_sample_size == 30
        assert cfg.min_effect_size == 0.15
        assert cfg.max_win_rate_for_poor == 0.15
        assert cfg.min_win_rate_for_strong == 0.65
        assert cfg.cooldown_hours == 72.0

    def test_no_trading_mutation_path(self):
        src = (ROOT / "research_engine" / "lifecycle" / "finding_trigger.py").read_text(encoding="utf-8")
        for f in ("MT5Execution", "RiskManager", "order_send", "from core",
                  "persist_trade_truth", "ShadowRuntime("):
            assert f not in src, f"forbidden trading path: {f}"
