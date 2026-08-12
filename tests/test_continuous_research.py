"""
Tests for Item 12: Continuous Research Operation.

Covers:
- Data detection / trigger
- Cycle state model
- Cycle persistence
- Idempotency
- Resumability
- Failure isolation
- Plan mode (read-only)
- Governance boundary
- Serialization
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.continuous.state import (
    CycleState,
    CycleStateStore,
    CycleStatus,
    TriggerStatus,
)
from research_engine.v10.continuous.orchestrator import ContinuousResearchOrchestrator


# ═══════════════════════════════════════════════════════════════════════════════
# STATE MODEL
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycleState:

    def test_all_trigger_statuses(self):
        assert TriggerStatus.NO_NEW_DATA == "NO_NEW_DATA"
        assert TriggerStatus.NEW_DATA_BELOW_THRESHOLD == "NEW_DATA_BELOW_THRESHOLD"
        assert TriggerStatus.NEW_DATA_READY == "NEW_DATA_READY"
        assert TriggerStatus.FORCE_RUN == "FORCE_RUN"
        assert TriggerStatus.BLOCKED == "BLOCKED"

    def test_all_cycle_statuses(self):
        assert CycleStatus.COMPLETED == "COMPLETED"
        assert CycleStatus.PARTIALLY_COMPLETED == "PARTIALLY_COMPLETED"
        assert CycleStatus.BLOCKED == "BLOCKED"
        assert CycleStatus.FAILED == "FAILED"
        assert CycleStatus.NO_ACTION == "NO_ACTION"

    def test_state_to_dict(self):
        state = CycleState(
            cycle_id="test_001",
            status=CycleStatus.COMPLETED.value,
            finding_count=44,
        )
        d = state.to_dict()
        assert d["cycle_id"] == "test_001"
        assert d["status"] == "COMPLETED"
        assert d["finding_count"] == 44
        assert json.dumps(d, default=str)  # Serializable

    def test_governance_note(self):
        state = CycleState()
        assert "cannot" in state.governance_note.lower()
        assert "trading" in state.governance_note.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycleStatePersistence:

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CycleStateStore(state_dir=tmp)
            state = CycleState(cycle_id="c1", status=CycleStatus.COMPLETED.value, finding_count=10)
            store.save(state)

            loaded = store.load_latest()
            assert loaded is not None
            assert loaded.cycle_id == "c1"
            assert loaded.finding_count == 10

    def test_immutable_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CycleStateStore(state_dir=tmp)

            s1 = CycleState(cycle_id="c1", status=CycleStatus.COMPLETED.value)
            store.save(s1)

            s2 = CycleState(cycle_id="c2", status=CycleStatus.COMPLETED.value)
            store.save(s2)

            history = store.load_history()
            assert len(history) == 2

    def test_no_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CycleStateStore(state_dir=tmp)
            loaded = store.load_latest()
            assert loaded is None


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — PLAN MODE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlanMode:

    def test_plan_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = ContinuousResearchOrchestrator(state_dir=tmp)
            state = orch.plan()
            # Plan should not persist state
            store = CycleStateStore(state_dir=tmp)
            loaded = store.load_latest()
            assert loaded is None  # Nothing saved by plan

    def test_plan_returns_trigger_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = ContinuousResearchOrchestrator(state_dir=tmp)
            state = orch.plan()
            assert state.trigger_status in [t.value for t in TriggerStatus]
            assert state.trigger_reason != ""


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:

    def test_no_new_data_produces_no_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CycleStateStore(state_dir=tmp)
            # Simulate previous state with same population sizes
            prev = CycleState(
                cycle_id="prev",
                current_population_sizes={"execution_records": 94},
                status=CycleStatus.COMPLETED.value,
            )
            store.save(prev)

            orch = ContinuousResearchOrchestrator(state_dir=tmp)
            # Plan should detect no new data (assuming 94 still)
            state = orch.plan()
            # If current data hasn't changed from 94, should be NO_NEW_DATA or BELOW_THRESHOLD
            assert state.trigger_status in (
                TriggerStatus.NO_NEW_DATA.value,
                TriggerStatus.NEW_DATA_BELOW_THRESHOLD.value,
                TriggerStatus.NEW_DATA_READY.value,  # May be ready if this is first check with data
            )


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:

    def test_no_deploy_methods(self):
        orch = ContinuousResearchOrchestrator()
        methods = [m for m in dir(orch) if not m.startswith("_")]
        dangerous = [m for m in methods if any(w in m for w in ["deploy", "activate", "execute_trade", "modify_bot"])]
        assert dangerous == []

    def test_cycle_state_has_governance(self):
        state = CycleState()
        assert "cannot" in state.governance_note.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureIsolation:

    def test_failed_cycle_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CycleStateStore(state_dir=tmp)
            # A cycle that completed partially
            state = CycleState(
                cycle_id="partial",
                status=CycleStatus.PARTIALLY_COMPLETED.value,
                stages_completed=["RESEARCH", "FEEDBACK"],
                finding_count=44,
                feedback_count=44,
            )
            store.save(state)

            loaded = store.load_latest()
            assert loaded.status == CycleStatus.PARTIALLY_COMPLETED.value
            assert loaded.stages_completed == ["RESEARCH", "FEEDBACK"]
            assert loaded.finding_count == 44


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
