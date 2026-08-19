"""
Tests for Research Cycle Runner — scheduled/continuous research execution.

Tests: cycle execution, idempotency, concurrency, persistence, budget,
governance, Command Center visibility, restart recovery.
"""
import sys
import json
import os
from unittest.mock import patch
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleRunner, ResearchCycleConfig, CycleState, CycleResult,
    _acquire_research_lock, _release_research_lock,
)
from research_engine.lifecycle.finding_trigger import ExecutionMode, EligibilityConfig


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fully isolated environment."""
    monkeypatch.setattr("research_engine.lifecycle.research_cycle_runner._STATE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.research_cycle_runner._STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("research_engine.lifecycle.research_cycle_runner._LOCK_FILE", tmp_path / "cycle.lock")
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "triggers.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "reg.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "cat.json")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")
    return tmp_path


def _mock_shadows():
    """Shadows with pattern stats — one catastrophic, one normal."""
    shadows = []
    # Catastrophic pattern (will trigger)
    for i in range(40):
        shadows.append({"pattern": "BAD_PAT", "r_multiple": -1.0, "correlation_id": f"C-{i}",
                        "symbol": "EURUSD", "entry_price": 1.0, "stop_loss": 1.001})
    # Normal pattern (won't trigger)
    for i in range(40):
        shadows.append({"pattern": "OK_PAT", "r_multiple": 0.05, "correlation_id": f"D-{i}",
                        "symbol": "EURUSD", "entry_price": 1.0, "stop_loss": 0.999})
    return shadows


# ═══════════════════════════════════════════════════════════════════════════════
# BASIC CYCLE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycleExecution:
    def test_cycle_completes(self, env):
        """A cycle runs and returns a result."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)
        runner = ResearchCycleRunner(config)

        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 2, "patterns": {"A": {"n": 50}}, "fingerprint": "abc"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                result = runner.run_cycle()

        assert result.status == "complete"
        assert result.cycle_id.startswith("RC-")

    def test_cycle_detects_triggers(self, env):
        """Cycle detects anomalous patterns."""
        from research_engine.lifecycle.finding_trigger import FindingTrigger, TriggerStatus
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)
        runner = ResearchCycleRunner(config)

        mock_trigger = FindingTrigger(title="Test", status=TriggerStatus.ELIGIBLE, sample_size=50)

        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 1, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[mock_trigger]):
                result = runner.run_cycle()

        assert result.triggers_detected == 1
        assert result.triggers_eligible == 1


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_duplicate_cycle_no_new_triggers(self, env):
        """Running the same cycle twice does not create duplicate triggers."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)

        # First cycle
        runner1 = ResearchCycleRunner(config)
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "same"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                r1 = runner1.run_cycle()

        # Second cycle (same data)
        runner2 = ResearchCycleRunner(config)
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "same"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                r2 = runner2.run_cycle()

        assert r1.status == "complete"
        assert r2.status == "complete"
        # Both completed without duplicate triggers
        assert r2.triggers_detected == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    def test_lock_prevents_simultaneous_cycles(self, env):
        """Second cycle is blocked while first holds lock."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)

        # Manually acquire lock
        assert _acquire_research_lock()

        # Try to run cycle — should be blocked
        runner = ResearchCycleRunner(config)
        result = runner.run_cycle()
        assert result.status == "locked"

        # Release and retry
        _release_research_lock()
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                result2 = runner.run_cycle()
        assert result2.status == "complete"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE / RESTART
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_state_survives_restart(self, env):
        """Cycle state is persisted and recovered."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)
        runner1 = ResearchCycleRunner(config)
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "abc"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                runner1.run_cycle()

        # Simulate restart — new runner loads state
        runner2 = ResearchCycleRunner(config)
        assert runner2._state.total_cycles == 1
        assert runner2._state.last_dataset_fingerprint == "abc"


# ═══════════════════════════════════════════════════════════════════════════════
# COOLDOWN / BUDGET
# ═══════════════════════════════════════════════════════════════════════════════


class TestBudget:
    def test_cooldown_prevents_rapid_cycles(self, env):
        """Cycle respects minimum interval between executions."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=3600)  # 1 hour
        runner = ResearchCycleRunner(config)

        # First cycle
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                r1 = runner.run_cycle()
        assert r1.status == "complete"

        # Immediate second cycle — should be skipped
        runner2 = ResearchCycleRunner(config)
        r2 = runner2.run_cycle()
        assert r2.status == "skipped"
        assert "Cooldown" in r2.errors[0]


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_cycle_never_promotes(self, env):
        """Research cycle cannot promote findings to production."""
        config = ResearchCycleConfig(mode=ExecutionMode.DETECT_ONLY, min_cycle_interval_seconds=0)
        runner = ResearchCycleRunner(config)

        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                result = runner.run_cycle()

        # No investigations started in DETECT_ONLY
        assert result.investigations_started == 0
        assert result.status == "complete"


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ═══════════════════════════════════════════════════════════════════════════════


class TestAudit:
    def test_cycle_events_recorded(self, env):
        """Audit log contains cycle lifecycle events."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)
        runner = ResearchCycleRunner(config)
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                runner.run_cycle()

        audit = env / "audit.jsonl"
        # Check the lifecycle audit (shared file)
        audit_main = env / "audit_log.jsonl"
        # At least one of the audit paths should have events
        found_events = []
        for f in [audit, audit_main]:
            if f.exists():
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    found_events.append(json.loads(line).get("event", ""))
        assert "RESEARCH_CYCLE_STARTED" in found_events or "RESEARCH_CYCLE_COMPLETED" in found_events


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND CENTER STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommandCenter:
    def test_runner_status_available(self, env):
        """Runner provides status for Command Center."""
        config = ResearchCycleConfig(min_cycle_interval_seconds=0)
        runner = ResearchCycleRunner(config)
        with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._scan_population",
                   return_value={"total_patterns": 0, "patterns": {}, "fingerprint": "x"}):
            with patch("research_engine.lifecycle.research_cycle_runner.ResearchCycleRunner._detect_findings",
                       return_value=[]):
                runner.run_cycle()

        status = runner.get_status()
        assert status["total_cycles"] == 1
        assert status["mode"] == "DETECT_ONLY"
        assert status["last_cycle"] != ""
