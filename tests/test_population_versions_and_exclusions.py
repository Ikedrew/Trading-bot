"""
Tests for Shared Platform Primitives: population_versions + exclusion tracking.

Covers:
- population_versions is populated in ResearchFinding (not empty {})
- population_versions uses SHA-256[:16] content hash
- population_versions is deterministic (same population → same hash)
- population_versions changes when population changes
- exclusion tracking in ExecutionUniverseBuilder
- exclusion tracking in DecisionUniverseBuilder
- exclusion metadata structure
- UniverseMetadata.exclusions field
"""

import sys
import os
import hashlib
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.runner.question_runner import (
    compose_evidence,
    RunContext,
    _compute_population_versions,
)
from research_engine.v10.runner.primitives.base import AnalysisResult
from research_engine.v10.universes.models import (
    NewEngineQuestion,
    Universe,
    Population,
    AnalysisType,
    ViewType,
    QuestionStatus,
)
from research_engine.v10.universes.base import UniverseMetadata


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION VERSIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationVersions:
    """Verify population_versions is populated correctly in findings."""

    def test_population_versions_not_empty(self):
        """compose_evidence produces non-empty population_versions."""
        question = NewEngineQuestion(
            question_id="TEST-001",
            title="Test",
            research_intent="Test intent",
            required_universes=(Universe.EXECUTION,),
            required_populations=(Population.ALL_TRADES,),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=5,
            status=QuestionStatus.READY,
        )
        population = [
            {"trade_id": "t1", "r_multiple": 1.5},
            {"trade_id": "t2", "r_multiple": -1.0},
        ]
        results = [AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=2,
            metrics={"mean_r": 0.25, "count": 2},
        )]
        ctx = RunContext(run_id="test_run", timestamp="2026-01-01T00:00:00Z")

        finding = compose_evidence(question, results, ctx, population)

        assert finding.population_versions != {}
        assert "all_trades" in finding.population_versions
        assert len(finding.population_versions["all_trades"]) == 16  # SHA-256[:16]

    def test_population_versions_deterministic(self):
        """Same population produces same hash."""
        question = NewEngineQuestion(
            question_id="TEST-001",
            title="Test",
            research_intent="Test",
            required_universes=(Universe.EXECUTION,),
            required_populations=(Population.ALL_TRADES,),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=5,
            status=QuestionStatus.READY,
        )
        population = [
            {"trade_id": "t1", "r_multiple": 1.5},
            {"trade_id": "t2", "r_multiple": -1.0},
        ]

        v1 = _compute_population_versions(question, population)
        v2 = _compute_population_versions(question, population)

        assert v1 == v2

    def test_population_versions_changes_with_data(self):
        """Different population produces different hash."""
        question = NewEngineQuestion(
            question_id="TEST-001",
            title="Test",
            research_intent="Test",
            required_universes=(Universe.EXECUTION,),
            required_populations=(Population.ALL_TRADES,),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=5,
            status=QuestionStatus.READY,
        )
        pop_a = [{"trade_id": "t1", "r_multiple": 1.5}]
        pop_b = [{"trade_id": "t1", "r_multiple": 1.5}, {"trade_id": "t2", "r_multiple": -1.0}]

        v_a = _compute_population_versions(question, pop_a)
        v_b = _compute_population_versions(question, pop_b)

        assert v_a["all_trades"] != v_b["all_trades"]

    def test_population_versions_empty_population(self):
        """Empty population produces 'empty' marker."""
        question = NewEngineQuestion(
            question_id="TEST-001",
            title="Test",
            research_intent="Test",
            required_universes=(Universe.EXECUTION,),
            required_populations=(Population.ALL_TRADES,),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=5,
            status=QuestionStatus.READY,
        )

        v = _compute_population_versions(question, [])

        assert v["all_trades"] == "empty"

    def test_population_versions_uses_first_population_name(self):
        """Key is derived from question's first required_population."""
        question = NewEngineQuestion(
            question_id="TEST-001",
            title="Test",
            research_intent="Test",
            required_universes=(Universe.DECISION,),
            required_populations=(Population.EXECUTE_DECISIONS, Population.ALL_DECISIONS),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=5,
            status=QuestionStatus.READY,
        )
        population = [{"entity_id": "e1", "action": "EXECUTE"}]

        v = _compute_population_versions(question, population)

        assert "execute_decisions" in v


# ═══════════════════════════════════════════════════════════════════════════════
# EXCLUSION TRACKING
# ═══════════════════════════════════════════════════════════════════════════════


class TestExclusionTracking:
    """Verify structured exclusion metadata in universe builders."""

    def test_universe_metadata_has_exclusions_field(self):
        """UniverseMetadata dataclass includes exclusions."""
        meta = UniverseMetadata(
            universe="TEST",
            record_count=10,
            generation_timestamp="2026-01-01T00:00:00Z",
            content_hash="abc123",
            exclusions={"total": 2, "reasons": {"missing_field": 2}},
        )
        assert meta.exclusions["total"] == 2
        assert meta.exclusions["reasons"]["missing_field"] == 2

    def test_universe_metadata_exclusions_in_to_dict(self):
        """Exclusions appear in to_dict() output."""
        meta = UniverseMetadata(
            universe="TEST",
            record_count=10,
            generation_timestamp="2026-01-01T00:00:00Z",
            content_hash="abc123",
            exclusions={"total": 3, "reasons": {"x": 3}},
        )
        d = meta.to_dict()
        assert "exclusions" in d
        assert d["exclusions"]["total"] == 3

    def test_universe_metadata_default_empty_exclusions(self):
        """Default exclusions is empty dict."""
        meta = UniverseMetadata(
            universe="TEST",
            record_count=0,
            generation_timestamp="2026-01-01T00:00:00Z",
            content_hash="abc",
        )
        assert meta.exclusions == {}

    def test_execution_builder_exclusion_tracking(self):
        """ExecutionUniverseBuilder tracks exclusions for missing fields."""
        from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
        from unittest.mock import patch
        from pathlib import Path

        builder = ExecutionUniverseBuilder()

        # Simulate raw trade_truth data (nested shape) with valid + invalid records
        builder._raw = [
            # Valid
            {"identity": {"trade_id": "pos_1", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1.5}},
            {"identity": {"trade_id": "pos_2", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": -1.0}},
            # Missing trade_id
            {"identity": {"trade_id": ""}, "outcome": {"r_multiple_realised": 0.5}},
            # Missing r_multiple
            {"identity": {"trade_id": "pos_3", "symbol": "GBPUSD"}, "outcome": {}},
            {"identity": {"trade_id": "pos_4"}, "outcome": {}},
        ]

        with patch.object(builder, '_build_entity_id_lookup', return_value={}):
            builder._entity_id_lookup = {}
            builder.build()

        meta = builder.metadata
        assert meta.exclusions["total"] == 3
        assert meta.exclusions["reasons"]["missing_trade_id"] == 1
        assert meta.exclusions["reasons"]["missing_r_multiple"] == 2
        assert meta.exclusions["source_records"] == 5
        assert meta.exclusions["included_records"] == 2
        assert meta.record_count == 2

    def test_decision_builder_exclusion_tracking(self):
        """DecisionUniverseBuilder tracks exclusions for missing fields."""
        from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
        from pathlib import Path

        builder = DecisionUniverseBuilder()

        # Simulate raw data
        builder._raw = [
            # Valid
            {"entity_id": "e1", "action": "EXECUTE", "symbol": "EURUSD"},
            {"entity_id": "e2", "action": "NO_TRADE", "symbol": "EURUSD", "terminal_stage": "risk"},
            # Missing entity_id
            {"entity_id": "", "action": "EXECUTE"},
            # Missing action
            {"entity_id": "e3", "action": ""},
        ]

        builder.build()

        meta = builder.metadata
        assert meta.exclusions["total"] == 2
        assert meta.exclusions["reasons"]["missing_entity_id"] == 1
        assert meta.exclusions["reasons"]["missing_action"] == 1
        assert meta.exclusions["source_records"] == 4
        assert meta.exclusions["included_records"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Verify both changes work together in the finding pipeline."""

    def test_finding_has_population_versions_and_exclusions_available(self):
        """A complete finding pipeline produces non-empty population_versions."""
        question = NewEngineQuestion(
            question_id="INT-001",
            title="Integration Test",
            research_intent="Test both features together",
            required_universes=(Universe.EXECUTION,),
            required_populations=(Population.ALL_TRADES,),
            analysis_type=AnalysisType.EXPECTANCY,
            minimum_sample_size=2,
            status=QuestionStatus.READY,
        )
        population = [
            {"trade_id": "t1", "r_multiple": 2.0, "entity_id": "e1"},
            {"trade_id": "t2", "r_multiple": -1.0, "entity_id": "e2"},
            {"trade_id": "t3", "r_multiple": 0.5, "entity_id": "e3"},
        ]
        results = [AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=3,
            metrics={"mean_r": 0.5, "count": 3, "wins": 2, "losses": 1},
        )]
        ctx = RunContext(run_id="int_test", timestamp="2026-01-01T00:00:00Z")

        finding = compose_evidence(question, results, ctx, population)

        # population_versions populated
        assert finding.population_versions != {}
        assert "all_trades" in finding.population_versions
        hash_val = finding.population_versions["all_trades"]
        assert len(hash_val) == 16
        assert hash_val != "empty"

        # Verify hash is correct
        expected = hashlib.sha256(
            json.dumps(population, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        assert hash_val == expected


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
