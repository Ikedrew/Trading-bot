"""
End-to-end: Finding Trigger → Command Center visibility.

Tests: DETECT_ONLY mode, DETECT_AND_INVESTIGATE mode, persistence,
audit trail, governance boundary, pipeline lineage.
"""
import sys
import json
from unittest.mock import patch
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.finding_trigger import (
    FindingTriggerEngine, TriggerStatus, EligibilityConfig, ExecutionMode,
)
from research_engine.lifecycle.orchestrator import ResearchOrchestrator
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition, ExperimentType, PopulationSpec, SimulationSpec,
)
from research_engine.lifecycle.hypothesis import HypothesisStatus, HypothesisCategory


def _mock_pop(pattern="CHAIN_PAT"):
    return [{"symbol": "EURUSD", "cid": f"C-{i}", "dir": "SELL", "entry": 1.085,
             "sl": 1.086, "tp": 1.083, "time": 1784739300 + i * 300,
             "pattern": pattern, "score": 0.6} for i in range(50)]


def _mock_candles():
    return [{"high": 1.086, "low": 1.083, "close": 1.084}] * 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fully isolated environment for trigger + lifecycle + CC."""
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "triggers.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "reg.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "cat.json")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")
    return tmp_path


class TestDetectOnlyMode:
    """DETECT_ONLY: trigger detected, visible in CC, no experiment runs."""

    def test_trigger_surfaces_without_investigation(self, env):
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        trigger = engine.detect_from_pattern_performance(
            "FAIL_PAT", mean_r=-0.95, win_rate=0.04, sample_size=50)
        assert trigger is not None
        assert trigger.status == TriggerStatus.ELIGIBLE

        # Verify persisted
        assert (env / "triggers.json").exists()

        # Verify summary shows it
        summary = engine.get_summary()
        assert summary["eligible_count"] == 1
        assert len(summary["top_candidates"]) == 1
        assert summary["top_candidates"][0]["title"].startswith("FAIL_PAT")

    def test_no_experiment_runs_in_detect_only(self, env):
        engine = FindingTriggerEngine(mode=ExecutionMode.DETECT_ONLY,
                                       config=EligibilityConfig(min_sample_size=20))
        trigger = engine.detect_from_pattern_performance(
            "NO_EXP_PAT", mean_r=-1.0, win_rate=0.0, sample_size=40)
        assert trigger.status == TriggerStatus.ELIGIBLE
        # No hypothesis or experiment created
        assert trigger.hypothesis_id == ""

    def test_audit_trail_stops_at_eligible(self, env):
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        engine.detect_from_pattern_performance("AUDIT_PAT", mean_r=-0.9, win_rate=0.05, sample_size=30)
        audit = (env / "audit_log.jsonl")
        events = [json.loads(l)["event"] for l in audit.read_text(encoding="utf-8").strip().splitlines()]
        assert "FINDING_ELIGIBLE" in events
        assert "INVESTIGATION_STARTED" not in events


class TestDetectAndInvestigateMode:
    """DETECT_AND_INVESTIGATE: full chain from finding to conclusion."""

    def test_full_chain_finding_to_conclusion(self, env):
        """Complete: detect → trigger → hypothesis → experiment → conclusion → CC."""
        # Create trigger
        engine = FindingTriggerEngine(mode=ExecutionMode.DETECT_AND_INVESTIGATE,
                                       config=EligibilityConfig(min_sample_size=20))
        trigger = engine.detect_from_pattern_performance(
            "CHAIN_PAT", mean_r=-0.95, win_rate=0.03, sample_size=50)
        assert trigger is not None
        assert trigger.status == TriggerStatus.ELIGIBLE

        # Now manually drive the investigation using the trigger's suggestions
        # (In production this would be automated; here we verify the chain works)
        orch = ResearchOrchestrator()
        orch._knowledge_path = env / "km.json"

        h = orch.detect_and_register(
            title=trigger.title,
            description=trigger.observation,
            claim=trigger.suggested_claim,
            null_hypothesis=trigger.suggested_null,
            category=trigger.suggested_hypothesis_category,
            source=f"trigger:{trigger.trigger_id}",
            source_finding_id=trigger.finding_id,
            multiple_testing_count=1,
        )
        engine.mark_registered(trigger.trigger_id, h.hypothesis_id)

        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=trigger.suggested_experiment_type,
            title=f"Auto-investigation: {trigger.title}",
            population=PopulationSpec(pattern_filter=trigger.suggested_patterns, min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        engine.mark_investigating(trigger.trigger_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_pop("CHAIN_PAT")):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv = orch.investigate(h, trigger.suggested_experiment_type, defn)

        engine.mark_completed(trigger.trigger_id)

        # Verify full chain
        assert inv.status == "complete"
        assert inv.conclusion in ("VALIDATED", "REJECTED", "INCONCLUSIVE")
        assert h.status == HypothesisStatus.CONCLUDED

        # Verify trigger lifecycle completed
        final_trigger = engine.all_triggers()[0]
        assert final_trigger.status == TriggerStatus.COMPLETED
        assert final_trigger.hypothesis_id == h.hypothesis_id

        # Verify knowledge map
        km = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        assert h.hypothesis_id in km.get("lifecycle_findings", {})


class TestRestartPersistence:
    """Trigger state survives process restart."""

    def test_trigger_visible_after_restart(self, env):
        engine1 = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        engine1.detect_from_pattern_performance("PERSIST_PAT", mean_r=-1.0, win_rate=0.0, sample_size=40)

        # Simulate restart
        engine2 = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        assert len(engine2.all_triggers()) == 1
        assert engine2.all_triggers()[0].status == TriggerStatus.ELIGIBLE
        assert engine2.get_summary()["eligible_count"] == 1


class TestGovernanceBoundary:
    """Trigger system cannot promote research into production."""

    def test_trigger_cannot_promote(self, env):
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        trigger = engine.detect_from_pattern_performance(
            "GOV_PAT", mean_r=-0.9, win_rate=0.05, sample_size=40)

        # Even after full investigation, no promotion
        orch = ResearchOrchestrator()
        orch._knowledge_path = env / "km.json"

        h = orch.detect_and_register(
            title="Gov test", description="d",
            claim=trigger.suggested_claim, null_hypothesis=trigger.suggested_null,
        )
        engine.mark_registered(trigger.trigger_id, h.hypothesis_id)

        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Gov exp",
            population=PopulationSpec(pattern_filter=["GOV_PAT"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_pop("GOV_PAT")):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Governance must block
        assert h.status != HypothesisStatus.PROMOTED
        assert not h.human_approval_granted


class TestPipelineLineage:
    """The complete pipeline is traceable: finding → trigger → hypothesis → experiment."""

    def test_lineage_reconstruction(self, env):
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=20))
        trigger = engine.detect_from_pattern_performance(
            "LINEAGE_PAT", mean_r=-0.85, win_rate=0.08, sample_size=35)

        orch = ResearchOrchestrator()
        orch._knowledge_path = env / "km.json"

        h = orch.detect_and_register(
            title=trigger.title,
            claim=trigger.suggested_claim,
            null_hypothesis=trigger.suggested_null,
            description=trigger.observation,
            source=f"trigger:{trigger.trigger_id}",
            source_finding_id=trigger.finding_id,
        )
        engine.mark_registered(trigger.trigger_id, h.hypothesis_id)

        # Verify lineage reconstruction
        # From trigger → hypothesis
        assert trigger.hypothesis_id == h.hypothesis_id
        # From hypothesis → trigger (via source field)
        assert trigger.trigger_id in h.source
        # From hypothesis → finding (via source_finding_id)
        assert h.source_finding_id == trigger.finding_id
