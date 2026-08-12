"""
Tests for Lifecycle Traceability.

Covers:
- Deterministic trace hash
- Mutation sensitivity (changed data → different hash)
- Persistence (save and reload)
- Immutable history (historical traces preserved)
- Complete/partial/empty lifecycle status
- Missing stage representation
- Contradictory evidence preservation
- Universe ownership preservation
- Version traceability
- Reproducibility
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.universes.models import Universe, Population
from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.cross_universe.tracer import (
    CrossUniverseTracer,
    LifecycleTrace,
    UniverseObservation,
    UniversePresence,
)
from research_engine.v10.cross_universe.persistence import LifecycleTraceStore
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


class MockBuilder(UniverseBuilder):
    def __init__(self, universe: Universe, records: list[dict[str, Any]]):
        super().__init__()
        self._universe = universe
        self._records = records
        self._built = True
        self._metadata = self._generate_metadata(
            records=records, source_files=("mock",), populations=("all",),
        )

    @property
    def universe_type(self) -> Universe:
        return self._universe

    def load(self) -> int:
        return len(self._records)

    def build(self) -> list[dict[str, Any]]:
        return self._records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        return self._records


def make_complete_builders():
    return {
        Universe.MARKET: MockBuilder(Universe.MARKET, [
            {"entity_id": "e1", "regime": "TRENDING", "session": "LONDON"},
        ]),
        Universe.DECISION: MockBuilder(Universe.DECISION, [
            {"entity_id": "e1", "action": "EXECUTE", "score": 80},
        ]),
        Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [
            {"entity_id": "e1", "family": "TREND_CONTINUATION", "confidence": 0.85},
        ]),
        Universe.RISK: MockBuilder(Universe.RISK, [
            {"entity_id": "e1", "risk_control_result": "APPROVED", "risk_percentage": 1.0},
        ]),
        Universe.EXECUTION: MockBuilder(Universe.EXECUTION, [
            {"entity_id": "e1", "trade_id": "pos_1", "r_multiple": 2.0},
        ]),
        Universe.OUTCOME: MockBuilder(Universe.OUTCOME, [
            {"entity_id": "e1", "r_multiple": 2.0, "net_realised_pnl": 100.0},
        ]),
    }


def make_partial_builders():
    return {
        Universe.MARKET: MockBuilder(Universe.MARKET, [{"entity_id": "e2", "regime": "RANGING"}]),
        Universe.DECISION: MockBuilder(Universe.DECISION, [{"entity_id": "e2", "action": "NO_TRADE"}]),
        Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [{"entity_id": "e2", "family": ""}]),
        Universe.RISK: MockBuilder(Universe.RISK, [{"entity_id": "e2", "risk_control_result": "BLOCKED"}]),
        Universe.EXECUTION: MockBuilder(Universe.EXECUTION, []),
        Universe.OUTCOME: MockBuilder(Universe.OUTCOME, []),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DETERMINISTIC HASH
# ═══════════════════════════════════════════════════════════════════════════════


class TestTraceHash:

    def test_hash_is_deterministic(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        t1 = tracer.trace("e1")
        t2 = tracer.trace("e1")
        assert t1.trace_hash == t2.trace_hash

    def test_hash_changes_with_different_data(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace_a = tracer.trace("e1")

        # Modify underlying data
        builders2 = make_complete_builders()
        builders2[Universe.DECISION] = MockBuilder(Universe.DECISION, [
            {"entity_id": "e1", "action": "EXECUTE", "score": 99},  # different score
        ])
        tracer2 = CrossUniverseTracer(builders2)
        trace_b = tracer2.trace("e1")

        assert trace_a.trace_hash != trace_b.trace_hash

    def test_hash_is_16_chars(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        assert len(trace.trace_hash) == 16

    def test_hash_in_to_dict(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        d = trace.to_dict()
        assert "trace_hash" in d
        assert d["trace_hash"] == trace.trace_hash

    def test_different_entity_different_hash(self):
        builders = make_complete_builders()
        builders[Universe.MARKET]._records.append({"entity_id": "e_other", "regime": "X"})
        builders[Universe.DECISION]._records.append({"entity_id": "e_other", "action": "NO_TRADE"})
        tracer = CrossUniverseTracer(builders)
        t1 = tracer.trace("e1")
        t2 = tracer.trace("e_other")
        assert t1.trace_hash != t2.trace_hash


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycleStatus:

    def test_complete(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        assert trace.trace_status == "COMPLETE"
        assert trace.present_count == 6

    def test_partial(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        assert trace.trace_status == "PARTIAL"
        assert trace.present_count == 4
        assert trace.missing_count == 2

    def test_empty(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("nonexistent")
        assert trace.trace_status == "EMPTY"
        assert trace.present_count == 0

    def test_missing_explicitly_marked(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        assert trace.universes["execution"].presence == UniversePresence.MISSING
        assert trace.universes["execution"].record is None
        assert trace.universes["outcome"].presence == UniversePresence.MISSING


# ═══════════════════════════════════════════════════════════════════════════════
# OWNERSHIP PRESERVATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestOwnershipPreservation:

    def test_market_fields_in_market_observation(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        market_rec = trace.universes["market"].record
        assert "regime" in market_rec
        assert "session" in market_rec

    def test_decision_fields_in_decision_observation(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        dec_rec = trace.universes["decision"].record
        assert "action" in dec_rec
        assert "score" in dec_rec

    def test_risk_fields_in_risk_observation(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        risk_rec = trace.universes["risk"].record
        assert "risk_control_result" in risk_rec
        assert "risk_percentage" in risk_rec


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRADICTORY EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestContradictoryEvidence:

    def test_contradictory_preserved(self):
        """Decision=EXECUTE, Risk=BLOCKED, Execution=PRESENT all preserved."""
        builders = {
            Universe.MARKET: MockBuilder(Universe.MARKET, [{"entity_id": "c1"}]),
            Universe.DECISION: MockBuilder(Universe.DECISION, [
                {"entity_id": "c1", "action": "EXECUTE"},
            ]),
            Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [{"entity_id": "c1"}]),
            Universe.RISK: MockBuilder(Universe.RISK, [
                {"entity_id": "c1", "risk_control_result": "BLOCKED"},
            ]),
            Universe.EXECUTION: MockBuilder(Universe.EXECUTION, [
                {"entity_id": "c1", "r_multiple": -1.0},
            ]),
            Universe.OUTCOME: MockBuilder(Universe.OUTCOME, [
                {"entity_id": "c1", "r_multiple": -1.0},
            ]),
        }
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("c1")

        # All present — contradiction preserved, not resolved
        assert trace.trace_status == "COMPLETE"
        assert trace.universes["decision"].record["action"] == "EXECUTE"
        assert trace.universes["risk"].record["risk_control_result"] == "BLOCKED"
        assert trace.universes["execution"].record["r_multiple"] == -1.0


# ═══════════════════════════════════════════════════════════════════════════════
# VERSION TRACEABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestVersionTraceability:

    def test_universe_versions_captured(self):
        builders = make_complete_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        assert len(trace.universe_versions) == 6
        for u in Universe:
            assert u.value in trace.universe_versions

    def test_version_changes_with_data(self):
        builders1 = make_complete_builders()
        tracer1 = CrossUniverseTracer(builders1)
        trace1 = tracer1.trace("e1")

        builders2 = make_complete_builders()
        builders2[Universe.EXECUTION] = MockBuilder(Universe.EXECUTION, [
            {"entity_id": "e1", "trade_id": "pos_1", "r_multiple": 3.0},
        ])
        tracer2 = CrossUniverseTracer(builders2)
        trace2 = tracer2.trace("e1")

        assert trace1.universe_versions["EXECUTION"] != trace2.universe_versions["EXECUTION"]


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LifecycleTraceStore(base_dir=tmp)
            builders = make_complete_builders()
            tracer = CrossUniverseTracer(builders)
            trace = tracer.trace("e1")

            store.save(trace)
            loaded = store.load_latest("e1")

            assert loaded is not None
            assert loaded.entity_id == "e1"
            assert loaded.trace_status == "COMPLETE"
            assert loaded.present_count == 6
            assert loaded.universes["decision"].presence == UniversePresence.PRESENT
            assert loaded.universes["decision"].record["action"] == "EXECUTE"

    def test_history_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LifecycleTraceStore(base_dir=tmp)

            # Save trace A
            builders_a = make_complete_builders()
            tracer_a = CrossUniverseTracer(builders_a)
            trace_a = tracer_a.trace("e1")
            hash_a = trace_a.trace_hash
            store.save(trace_a)

            # Save trace B (different data for same entity)
            builders_b = make_complete_builders()
            builders_b[Universe.DECISION] = MockBuilder(Universe.DECISION, [
                {"entity_id": "e1", "action": "EXECUTE", "score": 99},
            ])
            tracer_b = CrossUniverseTracer(builders_b)
            trace_b = tracer_b.trace("e1")
            hash_b = trace_b.trace_hash
            store.save(trace_b)

            # Both hashes should exist in history
            history = store.load_history("e1")
            history_hashes = {h.trace_hash for h in history}
            assert hash_a in history_hashes
            assert hash_b in history_hashes
            assert hash_a != hash_b

    def test_has_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LifecycleTraceStore(base_dir=tmp)
            assert not store.has_trace("e1")

            builders = make_complete_builders()
            tracer = CrossUniverseTracer(builders)
            trace = tracer.trace("e1")
            store.save(trace)

            assert store.has_trace("e1")

    def test_list_entities(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LifecycleTraceStore(base_dir=tmp)
            builders = make_complete_builders()
            tracer = CrossUniverseTracer(builders)

            store.save(tracer.trace("e1"))

            entities = store.list_entities()
            assert "e1" in entities

    def test_reconstructed_hash_matches(self):
        """Persisted and reconstructed trace produces same hash."""
        with tempfile.TemporaryDirectory() as tmp:
            store = LifecycleTraceStore(base_dir=tmp)
            builders = make_complete_builders()
            tracer = CrossUniverseTracer(builders)
            trace = tracer.trace("e1")
            original_hash = trace.trace_hash

            store.save(trace)
            loaded = store.load_latest("e1")

            assert loaded.trace_hash == original_hash


# ═══════════════════════════════════════════════════════════════════════════════
# REPRODUCIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestReproducibility:

    def test_same_data_same_trace(self):
        builders = make_complete_builders()
        t1 = CrossUniverseTracer(builders).trace("e1")
        t2 = CrossUniverseTracer(builders).trace("e1")
        assert t1.trace_hash == t2.trace_hash
        assert t1.trace_status == t2.trace_status
        assert t1.present_count == t2.present_count

    def test_same_versions_same_trace(self):
        builders = make_complete_builders()
        t1 = CrossUniverseTracer(builders).trace("e1")
        t2 = CrossUniverseTracer(builders).trace("e1")
        assert t1.universe_versions == t2.universe_versions


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
