"""
Tests for Finding Trigger Engine.

Tests: detection, eligibility, deduplication, knowledge map check,
experiment type selection, lifecycle transitions, persistence, audit.
"""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.finding_trigger import (
    FindingTrigger,
    FindingTriggerEngine,
    TriggerStatus,
    TriggerCategory,
    EligibilityConfig,
    ExecutionMode,
)
from research_engine.lifecycle.experiment_protocol import ExperimentType
from research_engine.lifecycle.hypothesis import HypothesisCategory


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Isolated trigger engine."""
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "triggers.json")
    return FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetection:
    def test_poor_pattern_triggers(self, engine):
        """Low WR + negative R triggers investigation."""
        trigger = engine.detect_from_pattern_performance(
            pattern="UNIQUE_POOR_PATTERN_XYZ", mean_r=-0.95, win_rate=0.04, sample_size=40)
        assert trigger is not None
        assert trigger.status == TriggerStatus.ELIGIBLE
        assert trigger.category == TriggerCategory.POOR_PATTERN_PERFORMANCE

    def test_strong_pattern_triggers(self, engine):
        """High WR + positive R triggers robustness check."""
        trigger = engine.detect_from_pattern_performance(
            pattern="TWEEZER_TOP", mean_r=0.42, win_rate=0.70, sample_size=50)
        assert trigger is not None
        assert trigger.category == TriggerCategory.STRONG_PATTERN_PERFORMANCE
        assert trigger.suggested_experiment_type == ExperimentType.ROBUSTNESS_CHECK

    def test_normal_performance_no_trigger(self, engine):
        """Average performance does not trigger."""
        trigger = engine.detect_from_pattern_performance(
            pattern="TREND_CONTINUATION", mean_r=-0.03, win_rate=0.49, sample_size=100)
        assert trigger is None

    def test_insufficient_sample_no_trigger(self, engine):
        """Below min sample size does not trigger."""
        trigger = engine.detect_from_pattern_performance(
            pattern="RARE_PATTERN", mean_r=-1.0, win_rate=0.0, sample_size=5)
        assert trigger is None

    def test_detect_from_finding_dict(self, engine):
        """Can detect from a generic research finding dict."""
        finding = {
            "question_id": "Q-test",
            "title": "Pattern anomaly detected",
            "outcome": "ANOMALOUS",
            "confidence": "HIGH",
            "primary_metrics": {"mean_r": -0.5},
            "sample_sizes": {"population": 50},
            "conclusion": "Pattern underperforming",
        }
        trigger = engine.detect_from_finding(finding)
        assert trigger is not None
        assert trigger.status == TriggerStatus.ELIGIBLE


# ═══════════════════════════════════════════════════════════════════════════════
# ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestEligibility:
    def test_eligible_trigger_has_experiment_type(self, engine):
        trigger = engine.detect_from_pattern_performance(
            "BAD_PATTERN", mean_r=-0.8, win_rate=0.05, sample_size=30)
        assert trigger.suggested_experiment_type == ExperimentType.DIRECTION_INVERSION

    def test_eligible_trigger_has_hypothesis_category(self, engine):
        trigger = engine.detect_from_pattern_performance(
            "BAD_PATTERN", mean_r=-0.8, win_rate=0.05, sample_size=30)
        assert trigger.suggested_hypothesis_category == HypothesisCategory.PATTERN_SIGNAL

    def test_eligible_trigger_has_claim(self, engine):
        trigger = engine.detect_from_pattern_performance(
            "MY_PATTERN", mean_r=-0.9, win_rate=0.03, sample_size=40)
        assert "MY_PATTERN" in trigger.suggested_claim
        assert trigger.suggested_null != ""


# ═══════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeduplication:
    def test_same_finding_not_triggered_twice(self, engine):
        """Duplicate detection prevents identical triggers."""
        t1 = engine.detect_from_pattern_performance("DUP", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert t1 is not None
        t2 = engine.detect_from_pattern_performance("DUP", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert t2 is None  # Deduplicated

    def test_different_patterns_not_deduplicated(self, engine):
        t1 = engine.detect_from_pattern_performance("PAT_A", mean_r=-1.0, win_rate=0.0, sample_size=30)
        t2 = engine.detect_from_pattern_performance("PAT_B", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert t1 is not None
        assert t2 is not None


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE MAP CHECK
# ═══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeMapCheck:
    def test_already_rejected_blocks_trigger(self, engine, tmp_path, monkeypatch):
        """If knowledge map shows REJECTED for this pattern, trigger is BLOCKED."""
        km_path = Path("analysis/summaries/research_knowledge.json")
        km_path.parent.mkdir(parents=True, exist_ok=True)
        km_data = {
            "lifecycle_findings": {
                "H-old": {"title": "BLOCKED_PATTERN investigation", "conclusion": "REJECTED"}
            }
        }
        km_path.write_text(json.dumps(km_data), encoding="utf-8")

        trigger = engine.detect_from_pattern_performance(
            "BLOCKED_PATTERN", mean_r=-1.0, win_rate=0.0, sample_size=50)
        assert trigger is None  # Blocked by knowledge map

        # Cleanup
        km_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAX ACTIVE TRIGGERS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMaxActiveTriggers:
    def test_max_active_blocks_new(self, tmp_path, monkeypatch):
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "t.json")
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20, max_active_triggers=2))

        t1 = engine.detect_from_pattern_performance("A", mean_r=-1.0, win_rate=0.0, sample_size=30)
        t2 = engine.detect_from_pattern_performance("B", mean_r=-1.0, win_rate=0.0, sample_size=30)
        t3 = engine.detect_from_pattern_performance("C", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert t1 is not None
        assert t2 is not None
        assert t3 is None  # Blocked by max_active


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_triggers_survive_restart(self, tmp_path, monkeypatch):
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "t.json")

        engine1 = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        engine1.detect_from_pattern_performance("PERSIST", mean_r=-1.0, win_rate=0.0, sample_size=40)
        assert len(engine1.all_triggers()) == 1

        # Restart
        engine2 = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        assert len(engine2.all_triggers()) == 1
        assert engine2.all_triggers()[0].title.startswith("PERSIST")

    def test_audit_events_recorded(self, engine, tmp_path):
        engine.detect_from_pattern_performance("AUDIT", mean_r=-1.0, win_rate=0.0, sample_size=30)
        audit_file = tmp_path / "audit_log.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l)["event"] for l in lines]
        assert "FINDING_ELIGIBLE" in events


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycleTransitions:
    def test_mark_registered(self, engine):
        trigger = engine.detect_from_pattern_performance("REG", mean_r=-1.0, win_rate=0.0, sample_size=30)
        engine.mark_registered(trigger.trigger_id, "H-test-123")
        loaded = engine.all_triggers()[0]
        assert loaded.status == TriggerStatus.REGISTERED
        assert loaded.hypothesis_id == "H-test-123"

    def test_mark_completed(self, engine):
        trigger = engine.detect_from_pattern_performance("COMP", mean_r=-1.0, win_rate=0.0, sample_size=30)
        engine.mark_registered(trigger.trigger_id, "H-1")
        engine.mark_investigating(trigger.trigger_id)
        engine.mark_completed(trigger.trigger_id)
        assert engine.all_triggers()[0].status == TriggerStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY / QUERIES
# ═══════════════════════════════════════════════════════════════════════════════


class TestQueries:
    def test_summary(self, engine):
        engine.detect_from_pattern_performance("A", mean_r=-1.0, win_rate=0.0, sample_size=30)
        engine.detect_from_pattern_performance("B", mean_r=-1.0, win_rate=0.0, sample_size=30)
        summary = engine.get_summary()
        assert summary["total_triggers"] == 2
        assert summary["eligible_count"] == 2
        assert len(summary["top_candidates"]) == 2

    def test_eligible_query(self, engine):
        engine.detect_from_pattern_performance("ELG", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert len(engine.eligible()) == 1

    def test_by_status(self, engine):
        engine.detect_from_pattern_performance("X", mean_r=-1.0, win_rate=0.0, sample_size=30)
        assert len(engine.by_status(TriggerStatus.ELIGIBLE)) == 1
        assert len(engine.by_status(TriggerStatus.DISMISSED)) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# MODE BEHAVIOUR
# ═══════════════════════════════════════════════════════════════════════════════


class TestMode:
    def test_detect_only_is_default(self, engine):
        assert engine.mode == ExecutionMode.DETECT_ONLY

    def test_detect_and_investigate_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "t.json")
        eng = FindingTriggerEngine(mode=ExecutionMode.DETECT_AND_INVESTIGATE)
        assert eng.mode == ExecutionMode.DETECT_AND_INVESTIGATE


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALISATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialisation:
    def test_trigger_roundtrip(self):
        t = FindingTrigger(
            title="Test", finding_id="F-1", category=TriggerCategory.DIRECTION_ASYMMETRY,
            sample_size=100, suggested_patterns=["TBC"],
            suggested_experiment_type=ExperimentType.DIRECTION_INVERSION,
        )
        data = t.to_dict()
        t2 = FindingTrigger.from_dict(data)
        assert t2.title == "Test"
        assert t2.category == TriggerCategory.DIRECTION_ASYMMETRY
        assert t2.suggested_patterns == ["TBC"]
