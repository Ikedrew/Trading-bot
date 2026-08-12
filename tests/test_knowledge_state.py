"""
Tests for Persistent Evidence / Knowledge State (Item 10).

Covers:
- Knowledge synthesis from findings
- Supporting/contradicting evidence tracking
- Status determination rules
- Confidence derivation
- Knowledge versioning
- Immutable history
- Persistence save/load/reconstruct
- System area mapping
- Update with new evidence
- Deterministic synthesis
- Governance boundary
- Query by area/status
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.knowledge.model import (
    KnowledgeStatus,
    KnowledgeItem,
    EvidenceRef,
)
from research_engine.v10.knowledge.engine import KnowledgeEngine
from research_engine.v10.knowledge.store import KnowledgeStore


def make_finding(qid="E-001", outcome="POSITIVE", confidence="MEDIUM", run_id="run_1", title="Test"):
    return {
        "question_id": qid,
        "title": title,
        "run_id": run_id,
        "run_timestamp": "2026-08-10T10:00:00Z",
        "outcome": outcome,
        "confidence": confidence,
        "conclusion": f"Finding: {outcome}",
        "universes_used": ["EXECUTION"],
        "universe_versions": {"EXECUTION": "abc"},
        "population_versions": {"all_trades": "def"},
    }


class TestKnowledgeSynthesis:

    def test_single_positive_finding(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="POSITIVE")])
        assert len(items) == 1
        assert items[0].status in (KnowledgeStatus.WEAKLY_SUPPORTED.value, KnowledgeStatus.SUPPORTED.value)

    def test_multiple_positive_findings_supported(self):
        engine = KnowledgeEngine()
        findings = [make_finding(run_id=f"run_{i}", outcome="POSITIVE") for i in range(4)]
        items = engine.synthesise_from_findings(findings)
        assert items[0].status == KnowledgeStatus.SUPPORTED.value

    def test_single_negative_finding(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="NEGATIVE")])
        assert items[0].status == KnowledgeStatus.CONTRADICTED.value

    def test_mixed_findings_inconclusive(self):
        engine = KnowledgeEngine()
        findings = [
            make_finding(run_id="r1", outcome="POSITIVE"),
            make_finding(run_id="r2", outcome="NEGATIVE"),
        ]
        items = engine.synthesise_from_findings(findings)
        assert items[0].status == KnowledgeStatus.INCONCLUSIVE.value

    def test_inconclusive_finding_no_count(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="INCONCLUSIVE")])
        # INCONCLUSIVE doesn't count as support or contradiction
        assert items[0].evidence_count == 0
        assert items[0].status == KnowledgeStatus.UNRESOLVED.value

    def test_deterministic(self):
        engine = KnowledgeEngine()
        findings = [make_finding(outcome="POSITIVE", run_id="r1")]
        i1 = engine.synthesise_from_findings(findings)
        i2 = engine.synthesise_from_findings(findings)
        assert i1[0].status == i2[0].status
        assert i1[0].evidence_count == i2[0].evidence_count


class TestEvidenceTracking:

    def test_supporting_evidence_tracked(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="POSITIVE")])
        assert len(items[0].supporting_evidence) == 1
        assert items[0].supporting_evidence[0].outcome == "POSITIVE"

    def test_contradicting_evidence_tracked(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="NEGATIVE")])
        assert len(items[0].contradicting_evidence) == 1

    def test_both_tracked_separately(self):
        engine = KnowledgeEngine()
        findings = [
            make_finding(run_id="r1", outcome="POSITIVE"),
            make_finding(run_id="r2", outcome="POSITIVE"),
            make_finding(run_id="r3", outcome="NEGATIVE"),
        ]
        items = engine.synthesise_from_findings(findings)
        assert len(items[0].supporting_evidence) == 2
        assert len(items[0].contradicting_evidence) == 1
        assert items[0].evidence_count == 3


class TestKnowledgeUpdate:

    def test_update_adds_evidence(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="POSITIVE")])
        original = items[0]

        updated = engine.update_item(original, make_finding(outcome="POSITIVE", run_id="r2"))
        assert updated.knowledge_version == 2
        assert len(updated.supporting_evidence) == 2

    def test_contradicting_update_changes_status(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding(outcome="POSITIVE")])
        original = items[0]

        updated = engine.update_item(original, make_finding(outcome="NEGATIVE", run_id="r2"))
        assert updated.status == KnowledgeStatus.INCONCLUSIVE.value


class TestKnowledgeVersioning:

    def test_version_starts_at_1(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        assert items[0].knowledge_version == 1

    def test_update_increments_version(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        updated = engine.update_item(items[0], make_finding(run_id="r2"))
        assert updated.knowledge_version == 2


class TestKnowledgePersistence:

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(base_dir=tmp)
            engine = KnowledgeEngine()
            items = engine.synthesise_from_findings([make_finding()])
            store.save(items[0])

            loaded = store.load(items[0].knowledge_id)
            assert loaded is not None
            assert loaded.knowledge_id == items[0].knowledge_id
            assert loaded.status == items[0].status

    def test_immutable_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(base_dir=tmp)
            engine = KnowledgeEngine()

            items = engine.synthesise_from_findings([make_finding()])
            store.save(items[0])

            # Update and save v2
            updated = engine.update_item(items[0], make_finding(run_id="r2"))
            store.save(updated)

            history = store.load_history(items[0].knowledge_id)
            assert len(history) == 2
            assert history[0].knowledge_version == 1
            assert history[1].knowledge_version == 2

    def test_load_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(base_dir=tmp)
            engine = KnowledgeEngine()
            findings = [
                make_finding(qid="E-001", outcome="POSITIVE"),
                make_finding(qid="D-001", outcome="NEGATIVE"),
            ]
            items = engine.synthesise_from_findings(findings)
            store.save_batch(items)

            all_items = store.load_all()
            assert len(all_items) == 2

    def test_query_by_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(base_dir=tmp)
            engine = KnowledgeEngine()
            findings = [
                make_finding(qid="E-001"),
                make_finding(qid="M-001"),
            ]
            items = engine.synthesise_from_findings(findings)
            store.save_batch(items)

            exe = store.query_by_area("EXECUTION")
            mkt = store.query_by_area("MARKET")
            assert len(exe) == 1
            assert len(mkt) == 1

    def test_query_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(base_dir=tmp)
            engine = KnowledgeEngine()
            findings = [
                make_finding(qid="E-001", outcome="POSITIVE"),
                make_finding(qid="D-001", outcome="NEGATIVE"),
            ]
            items = engine.synthesise_from_findings(findings)
            store.save_batch(items)

            contradicted = store.query_by_status(KnowledgeStatus.CONTRADICTED.value)
            assert len(contradicted) == 1


class TestLineagePreservation:

    def test_preserves_universe_versions(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        assert items[0].universe_versions == {"EXECUTION": "abc"}

    def test_preserves_source_universes(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        assert "EXECUTION" in items[0].source_universes


class TestGovernance:

    def test_governance_note(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        assert "cannot" in items[0].governance_note.lower()
        assert "trading" in items[0].governance_note.lower()

    def test_no_trading_methods(self):
        item = KnowledgeItem()
        methods = [m for m in dir(item) if not m.startswith("_")]
        trading = [m for m in methods if "trade" in m or "execute" in m or "deploy" in m]
        assert trading == []


class TestSerialization:

    def test_to_dict(self):
        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings([make_finding()])
        d = items[0].to_dict()
        assert "knowledge_id" in d
        assert "status" in d
        assert "supporting_evidence" in d
        serialized = json.dumps(d, default=str)
        assert len(serialized) > 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
