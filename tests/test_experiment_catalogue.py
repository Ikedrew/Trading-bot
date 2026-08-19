"""
Tests for Experiment Catalogue — permanent governed registry of experiments.

Tests: identity, persistence, lifecycle, relationships, dataset fingerprint,
search, reports, audit, immutability, restart recovery.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.experiment_catalogue import (
    ExperimentCatalogue,
    ExperimentLifecycle,
    ExperimentRecord,
)


@pytest.fixture
def tmp_catalogue(tmp_path, monkeypatch):
    """Create a catalogue using temp storage."""
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "experiment_registry.json")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit_log.jsonl")
    return ExperimentCatalogue()


def _make_record(**overrides) -> ExperimentRecord:
    defaults = {
        "title": "Test Experiment",
        "experiment_type": "DIRECTION_INVERSION",
        "hypothesis_id": "H-test-001",
        "population": "TBC shadows",
        "observation_count": 100,
    }
    defaults.update(overrides)
    return ExperimentRecord(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# IDENTITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentIdentity:
    def test_unique_ids(self):
        r1 = ExperimentRecord(title="A")
        r2 = ExperimentRecord(title="B")
        assert r1.experiment_id != r2.experiment_id

    def test_stable_id_format(self):
        r = ExperimentRecord(title="Test")
        assert r.experiment_id.startswith("EXP-")
        assert len(r.experiment_id) > 10

    def test_no_accidental_reuse(self):
        ids = set()
        for _ in range(100):
            r = ExperimentRecord(title="bulk")
            assert r.experiment_id not in ids
            ids.add(r.experiment_id)

    def test_explicit_id_preserved(self):
        r = ExperimentRecord(experiment_id="EXP-CUSTOM-001", title="Custom")
        assert r.experiment_id == "EXP-CUSTOM-001"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_save_and_load(self, tmp_catalogue, tmp_path, monkeypatch):
        rec = _make_record()
        tmp_catalogue.register(rec)

        # Create new catalogue (simulates restart)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "experiment_registry.json")
        fresh = ExperimentCatalogue()
        loaded = fresh.get(rec.experiment_id)
        assert loaded is not None
        assert loaded.title == "Test Experiment"
        assert loaded.hypothesis_id == "H-test-001"

    def test_restart_recovery(self, tmp_catalogue, tmp_path, monkeypatch):
        r1 = _make_record(title="First")
        r2 = _make_record(title="Second")
        tmp_catalogue.register(r1)
        tmp_catalogue.register(r2)
        tmp_catalogue.start(r1.experiment_id)
        tmp_catalogue.complete(r1.experiment_id, conclusion="REJECTED")

        # Reload
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "experiment_registry.json")
        fresh = ExperimentCatalogue()
        assert len(fresh.all()) == 2
        assert fresh.get(r1.experiment_id).status == ExperimentLifecycle.COMPLETED

    def test_atomic_write(self, tmp_catalogue, tmp_path):
        rec = _make_record()
        tmp_catalogue.register(rec)
        # Verify no .tmp file remains
        assert not (tmp_path / "experiment_registry.tmp").exists()
        assert (tmp_path / "experiment_registry.json").exists()


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycle:
    def test_valid_transition_registered_to_running(self):
        r = _make_record()
        assert r.transition(ExperimentLifecycle.RUNNING)
        assert r.status == ExperimentLifecycle.RUNNING
        assert r.started_at != ""

    def test_valid_transition_running_to_completed(self):
        r = _make_record()
        r.transition(ExperimentLifecycle.RUNNING)
        assert r.transition(ExperimentLifecycle.COMPLETED)
        assert r.completed_at != ""

    def test_invalid_transition_rejected(self):
        r = _make_record()
        assert not r.transition(ExperimentLifecycle.COMPLETED)  # Can't skip RUNNING
        assert r.status == ExperimentLifecycle.REGISTERED

    def test_terminal_state_blocks_transitions(self):
        r = _make_record()
        r.transition(ExperimentLifecycle.RUNNING)
        r.transition(ExperimentLifecycle.COMPLETED)
        assert not r.transition(ExperimentLifecycle.RUNNING)  # Cannot go back

    def test_failed_experiment(self, tmp_catalogue):
        rec = _make_record()
        tmp_catalogue.register(rec)
        tmp_catalogue.start(rec.experiment_id)
        assert tmp_catalogue.fail(rec.experiment_id, reason="MT5 disconnected")
        assert tmp_catalogue.get(rec.experiment_id).status == ExperimentLifecycle.FAILED

    def test_cancelled_experiment(self, tmp_catalogue):
        rec = _make_record()
        tmp_catalogue.register(rec)
        assert tmp_catalogue.cancel(rec.experiment_id, reason="superseded")
        assert tmp_catalogue.get(rec.experiment_id).status == ExperimentLifecycle.CANCELLED


# ═══════════════════════════════════════════════════════════════════════════════
# IMMUTABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_completed_core_fields_immutable(self):
        r = _make_record()
        r.transition(ExperimentLifecycle.RUNNING)
        r.transition(ExperimentLifecycle.COMPLETED)
        assert r.is_immutable
        # Amend attempt on core field silently rejected
        r.amend(field_name="definition", old_value={}, new_value={"changed": True},
                reason="test", actor="tester")
        assert len(r.amendments) == 0  # Not recorded — blocked

    def test_non_terminal_allows_amendment(self):
        r = _make_record()
        r.amend(field_name="description", old_value="", new_value="updated",
                reason="clarification", actor="researcher")
        assert len(r.amendments) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# RELATIONSHIPS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRelationships:
    def test_hypothesis_to_experiments(self, tmp_catalogue):
        r1 = _make_record(hypothesis_id="H-001", title="Primary")
        r2 = _make_record(hypothesis_id="H-001", title="Placebo")
        r3 = _make_record(hypothesis_id="H-002", title="Other")
        tmp_catalogue.register(r1)
        tmp_catalogue.register(r2)
        tmp_catalogue.register(r3)
        found = tmp_catalogue.find_by_hypothesis("H-001")
        assert len(found) == 2

    def test_parent_child_relationship(self, tmp_catalogue):
        parent = _make_record(title="Original")
        tmp_catalogue.register(parent)
        child = _make_record(title="Follow-up", parent_experiment_id=parent.experiment_id)
        tmp_catalogue.register(child)
        history = tmp_catalogue.get_history(child.experiment_id)
        assert len(history) == 2
        assert history[0].experiment_id == parent.experiment_id

    def test_supersedes(self, tmp_catalogue):
        old = _make_record(title="V1")
        tmp_catalogue.register(old)
        new = _make_record(title="V2", supersedes_experiment_id=old.experiment_id)
        tmp_catalogue.register(new)
        assert new.supersedes_experiment_id == old.experiment_id


# ═══════════════════════════════════════════════════════════════════════════════
# DATASET FINGERPRINT
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatasetFingerprint:
    def test_fingerprint_stored(self, tmp_catalogue):
        rec = _make_record()
        rec.dataset_fingerprint = {"content_hash": "abc123", "observation_count": 50}
        tmp_catalogue.register(rec)
        loaded = tmp_catalogue.get(rec.experiment_id)
        assert loaded.dataset_fingerprint["content_hash"] == "abc123"

    def test_search_by_fingerprint(self, tmp_catalogue):
        r1 = _make_record(title="A")
        r1.dataset_fingerprint = {"content_hash": "hash_A"}
        r2 = _make_record(title="B")
        r2.dataset_fingerprint = {"content_hash": "hash_B"}
        tmp_catalogue.register(r1)
        tmp_catalogue.register(r2)
        found = tmp_catalogue.find_by_fingerprint("hash_A")
        assert len(found) == 1
        assert found[0].title == "A"


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearch:
    def test_find_by_status(self, tmp_catalogue):
        r1 = _make_record(title="Running")
        r2 = _make_record(title="Registered")
        tmp_catalogue.register(r1)
        tmp_catalogue.register(r2)
        tmp_catalogue.start(r1.experiment_id)
        found = tmp_catalogue.find_by_status(ExperimentLifecycle.RUNNING)
        assert len(found) == 1
        assert found[0].title == "Running"

    def test_find_by_type(self, tmp_catalogue):
        r1 = _make_record(experiment_type="DIRECTION_INVERSION")
        r2 = _make_record(experiment_type="PLACEBO_CONTROL")
        tmp_catalogue.register(r1)
        tmp_catalogue.register(r2)
        found = tmp_catalogue.find_by_type("PLACEBO_CONTROL")
        assert len(found) == 1

    def test_find_by_dataset(self, tmp_catalogue):
        r1 = _make_record()
        r1.dataset_id = "shadow_trades_v10"
        tmp_catalogue.register(r1)
        found = tmp_catalogue.find_by_dataset("shadow_trades_v10")
        assert len(found) == 1

    def test_get_latest(self, tmp_catalogue):
        for i in range(5):
            tmp_catalogue.register(_make_record(title=f"Exp-{i}"))
        latest = tmp_catalogue.get_latest(3)
        assert len(latest) == 3

    def test_find_by_date_range(self, tmp_catalogue):
        rec = _make_record()
        tmp_catalogue.register(rec)
        # All experiments created today should match a broad range
        found = tmp_catalogue.find_by_date_range("2020-01-01", "2030-12-31")
        assert len(found) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS & CATALOGUE
# ═══════════════════════════════════════════════════════════════════════════════


class TestReports:
    def test_report_path_stored(self, tmp_catalogue):
        rec = _make_record()
        tmp_catalogue.register(rec)
        tmp_catalogue.start(rec.experiment_id)
        tmp_catalogue.complete(rec.experiment_id, report_path="reports/test.md")
        loaded = tmp_catalogue.get(rec.experiment_id)
        assert loaded.report_path == "reports/test.md"

    def test_generate_catalogue_report(self, tmp_catalogue):
        tmp_catalogue.register(_make_record(title="Alpha"))
        tmp_catalogue.register(_make_record(title="Beta"))
        report = tmp_catalogue.generate_catalogue_report()
        assert "# Research Experiment Catalogue" in report
        assert "Total experiments" in report
        assert "DIRECTION_INVERSION" in report
        assert "H-test-001" in report

    def test_summary(self, tmp_catalogue):
        tmp_catalogue.register(_make_record())
        summary = tmp_catalogue.get_summary()
        assert summary["total_experiments"] == 1
        assert "REGISTERED" in summary["by_status"]


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ═══════════════════════════════════════════════════════════════════════════════


class TestAudit:
    def test_lifecycle_events_generated(self, tmp_catalogue, tmp_path):
        rec = _make_record()
        tmp_catalogue.register(rec)
        tmp_catalogue.start(rec.experiment_id)
        tmp_catalogue.complete(rec.experiment_id, conclusion="REJECTED")

        audit_file = tmp_path / "audit_log.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l)["event"] for l in lines]
        assert "EXPERIMENT_REGISTERED" in events
        assert "EXPERIMENT_STARTED" in events
        assert "EXPERIMENT_COMPLETED" in events

    def test_timestamps_preserved(self, tmp_catalogue, tmp_path):
        rec = _make_record()
        tmp_catalogue.register(rec)
        audit_file = tmp_path / "audit_log.jsonl"
        entry = json.loads(audit_file.read_text(encoding="utf-8").strip().splitlines()[0])
        assert "timestamp" in entry
        assert entry["experiment_id"] == rec.experiment_id


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALISATION ROUNDTRIP
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialisation:
    def test_full_roundtrip(self):
        rec = _make_record()
        rec.dataset_fingerprint = {"content_hash": "abc", "observation_count": 50}
        rec.result_summary = {"mean_r": 0.15, "n": 484}
        rec.related_experiment_ids = ["EXP-other-001"]
        data = rec.to_dict()
        restored = ExperimentRecord.from_dict(data)
        assert restored.experiment_id == rec.experiment_id
        assert restored.dataset_fingerprint["content_hash"] == "abc"
        assert restored.result_summary["mean_r"] == 0.15
        assert "EXP-other-001" in restored.related_experiment_ids


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TEST (deterministic, no MT5)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_lifecycle_deterministic(self, tmp_catalogue, tmp_path, monkeypatch):
        """Complete lifecycle without any external dependencies."""
        # 1. Register experiment
        rec = ExperimentRecord(
            title="TBC Direction Inversion (synthetic test)",
            experiment_type="DIRECTION_INVERSION",
            hypothesis_id="H-synthetic-001",
            research_question_id="RQ-pattern-direction",
            population="THREE_BLACK_CROWS shadows",
            observation_count=484,
            dataset_id="V10_PRIMARY_TBC",
            dataset_version="shadow_trades_v2",
            dataset_fingerprint={"content_hash": "a1b2c3d4e5f6", "observation_count": 484,
                                  "fingerprint_algorithm": "SHA-256"},
            definition={"type": "DIRECTION_INVERSION", "treatment": "BUY", "control": "SELL",
                        "horizon_bars": 60, "stop_multiplier": 1.0, "tp_multiplier": 3.0},
            parameters={"pattern_filter": ["THREE_BLACK_CROWS"], "min_sample_size": 30},
            control_description="Original direction (SELL) at 1R stop",
            treatment_description="Inverted direction (BUY) at 1R stop, 3R TP",
            null_hypothesis="Direction label has no systematic effect on R",
        )
        tmp_catalogue.register(rec)

        # 2. Start
        assert tmp_catalogue.start(rec.experiment_id)

        # 3. Complete with result
        assert tmp_catalogue.complete(
            rec.experiment_id,
            result_summary={"n": 484, "mean_r": 0.248, "win_rate": 0.318,
                            "total_r": 120.2, "ci_lower": 0.109, "ci_upper": 0.386},
            conclusion="REJECTED",
            classification="RED",
            report_path="reports/research/lifecycle/H-synthetic-001_report.md",
        )

        # 4. Verify terminal state
        loaded = tmp_catalogue.get(rec.experiment_id)
        assert loaded.status == ExperimentLifecycle.COMPLETED
        assert loaded.is_immutable
        assert loaded.conclusion == "REJECTED"
        assert loaded.dataset_fingerprint["content_hash"] == "a1b2c3d4e5f6"

        # 5. Verify cannot transition back
        assert not tmp_catalogue.start(rec.experiment_id)

        # 6. Reload (restart simulation)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE",
                            tmp_path / "experiment_registry.json")
        fresh = ExperimentCatalogue()
        reloaded = fresh.get(rec.experiment_id)
        assert reloaded is not None
        assert reloaded.status == ExperimentLifecycle.COMPLETED
        assert reloaded.hypothesis_id == "H-synthetic-001"
        assert reloaded.dataset_fingerprint["content_hash"] == "a1b2c3d4e5f6"
        assert reloaded.result_summary["mean_r"] == 0.248
        assert reloaded.report_path == "reports/research/lifecycle/H-synthetic-001_report.md"

        # 7. Verify lineage query
        by_hyp = fresh.find_by_hypothesis("H-synthetic-001")
        assert len(by_hyp) == 1

        # 8. Verify audit
        audit_file = tmp_path / "audit_log.jsonl"
        events = [json.loads(l)["event"] for l in audit_file.read_text(encoding="utf-8").strip().splitlines()]
        assert "EXPERIMENT_REGISTERED" in events
        assert "EXPERIMENT_STARTED" in events
        assert "EXPERIMENT_COMPLETED" in events

        # 9. Verify summary
        summary = fresh.get_summary()
        assert summary["total_experiments"] == 1
        assert summary["by_status"]["COMPLETED"] == 1

        # 10. Verify catalogue report
        report = fresh.generate_catalogue_report()
        assert "H-synthetic-001" in report
        assert "DIRECTION_INVERSION" in report
