"""
Tests for the Cross-Universe Interface: trace, compare, classify, propose.

Covers:
- Tracer: complete/partial/empty traces, missing universes, identity preservation
- Comparison: dimension extraction, missing data handling, ownership preservation
- Classifier: deterministic rules, contradictions, missing data classification
- Proposal: governance, trigger-based generation, no unsupported claims
- Reproducibility: universe versions preserved in trace results
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.universes.models import Universe, Population
from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.cross_universe.tracer import (
    CrossUniverseTracer,
    LifecycleTrace,
    UniversePresence,
)
from research_engine.v10.cross_universe.comparison import (
    ComparisonBuilder,
    CrossUniverseComparison,
)
from research_engine.v10.cross_universe.classifier import (
    CrossUniverseClassifier,
    Classification,
)
from research_engine.v10.cross_universe.proposal import (
    ProposalGenerator,
    ResearchProposal,
)
from typing import Any
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# TEST FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


class MockBuilder(UniverseBuilder):
    """Minimal builder for testing."""

    def __init__(self, universe: Universe, records: list[dict[str, Any]]):
        super().__init__()
        self._universe = universe
        self._records = records
        self._built = True
        self._metadata = self._generate_metadata(
            records=records,
            source_files=("mock",),
            populations=("all",),
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


def make_full_builders():
    """Create 6 universe builders with one shared entity_id."""
    return {
        Universe.MARKET: MockBuilder(Universe.MARKET, [
            {"entity_id": "e1", "regime": "TRENDING", "session": "LONDON", "volatility_state": "NORMAL"},
        ]),
        Universe.DECISION: MockBuilder(Universe.DECISION, [
            {"entity_id": "e1", "action": "EXECUTE", "score": 78, "terminal_stage": "completed"},
        ]),
        Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [
            {"entity_id": "e1", "family": "TREND_CONTINUATION", "confidence": 0.82},
        ]),
        Universe.RISK: MockBuilder(Universe.RISK, [
            {"entity_id": "e1", "risk_control_result": "APPROVED", "risk_control_reason": "", "risk_percentage": 1.0},
        ]),
        Universe.EXECUTION: MockBuilder(Universe.EXECUTION, [
            {"entity_id": "e1", "trade_id": "pos_1", "r_multiple": 1.5, "exit_reason": "tp"},
        ]),
        Universe.OUTCOME: MockBuilder(Universe.OUTCOME, [
            {"entity_id": "e1", "r_multiple": 1.5, "exit_reason": "tp", "net_realised_pnl": 75.0},
        ]),
    }


def make_partial_builders():
    """Create builders where entity only exists in Decision + Risk (NO_TRADE)."""
    return {
        Universe.MARKET: MockBuilder(Universe.MARKET, [
            {"entity_id": "e2", "regime": "RANGING"},
        ]),
        Universe.DECISION: MockBuilder(Universe.DECISION, [
            {"entity_id": "e2", "action": "NO_TRADE", "score": 45, "terminal_stage": "risk"},
        ]),
        Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [
            {"entity_id": "e2", "family": "MEAN_REVERSION", "confidence": 0.55},
        ]),
        Universe.RISK: MockBuilder(Universe.RISK, [
            {"entity_id": "e2", "risk_control_result": "BLOCKED", "risk_control_reason": "exposure_limit"},
        ]),
        Universe.EXECUTION: MockBuilder(Universe.EXECUTION, []),  # No execution
        Universe.OUTCOME: MockBuilder(Universe.OUTCOME, []),  # No outcome
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRACER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossUniverseTracer:

    def test_complete_trace(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")

        assert trace.entity_id == "e1"
        assert trace.trace_status == "COMPLETE"
        assert trace.present_count == 6
        assert trace.missing_count == 0

    def test_partial_trace(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")

        assert trace.trace_status == "PARTIAL"
        assert trace.universes["execution"].presence == UniversePresence.MISSING
        assert trace.universes["outcome"].presence == UniversePresence.MISSING
        assert trace.universes["decision"].presence == UniversePresence.PRESENT

    def test_empty_trace(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("nonexistent")

        assert trace.trace_status == "EMPTY"
        assert trace.present_count == 0

    def test_empty_entity_id(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("")

        assert trace.trace_status == "EMPTY"

    def test_universe_versions_preserved(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")

        assert "MARKET" in trace.universe_versions
        assert "DECISION" in trace.universe_versions
        assert len(trace.universe_versions) == 6
        # Content hashes are 16 chars
        for h in trace.universe_versions.values():
            assert len(h) == 16

    def test_all_entity_ids(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        ids = tracer.all_entity_ids()
        assert "e1" in ids

    def test_trace_batch(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        traces = tracer.trace_batch(["e1", "nonexistent"])
        assert len(traces) == 2
        assert traces[0].trace_status == "COMPLETE"
        assert traces[1].trace_status == "EMPTY"

    def test_record_preserved_in_observation(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")

        decision_obs = trace.universes["decision"]
        assert decision_obs.record["action"] == "EXECUTE"
        assert decision_obs.record["score"] == 78

    def test_to_dict(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        d = trace.to_dict()

        assert d["entity_id"] == "e1"
        assert "universes" in d
        assert "universe_versions" in d


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossUniverseComparison:

    def test_complete_comparison(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        assert comparison.entity_id == "e1"
        assert comparison.trace_status == "COMPLETE"
        assert len(comparison.dimensions) > 0

    def test_comparison_has_decision_risk_dimension(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        dim_names = [d.name for d in comparison.dimensions]
        assert "decision_vs_risk" in dim_names

    def test_comparison_missing_data_not_comparable(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        # decision_vs_outcome should not be comparable (outcome missing)
        for dim in comparison.dimensions:
            if dim.name == "decision_vs_outcome":
                assert dim.comparable is False

    def test_comparison_preserves_ownership(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        # decision_vs_risk should have decision-owned and risk-owned values
        for dim in comparison.dimensions:
            if dim.name == "decision_vs_risk":
                assert "decision_action" in dim.values
                assert "risk_control_result" in dim.values

    def test_empty_trace_produces_empty_comparison(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("nonexistent")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        assert comparison.trace_status == "EMPTY"
        assert len(comparison.dimensions) == 0

    def test_summary_present_missing(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)

        assert "execution" in comparison.summary["missing_universes"]
        assert "decision" in comparison.summary["present_universes"]


# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFIER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossUniverseClassifier:

    def test_complete_lifecycle_classified(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        assert result.lifecycle_classification == Classification.COMPLETE_LIFECYCLE

    def test_no_execution_classified(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        assert result.lifecycle_classification == Classification.NO_EXECUTION

    def test_decision_execute_risk_approved(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        dec_risk = [d for d in result.dimension_classifications if d.dimension_name == "decision_vs_risk"]
        assert len(dec_risk) == 1
        assert dec_risk[0].classification == Classification.DECISION_EXECUTE_RISK_APPROVED

    def test_decision_no_trade_classified(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        dec_risk = [d for d in result.dimension_classifications if d.dimension_name == "decision_vs_risk"]
        assert len(dec_risk) == 1
        assert dec_risk[0].classification == Classification.DECISION_NO_TRADE

    def test_missing_data_does_not_produce_false_negative(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        # execution_vs_outcome with no execution should not claim "loss"
        exe_out = [d for d in result.dimension_classifications if d.dimension_name == "execution_vs_outcome"]
        if exe_out:
            assert exe_out[0].classification != Classification.OUTCOME_NEGATIVE

    def test_positive_outcome_classified(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        exe_out = [d for d in result.dimension_classifications if d.dimension_name == "execution_vs_outcome"]
        assert len(exe_out) == 1
        assert exe_out[0].classification == Classification.OUTCOME_POSITIVE

    def test_all_classifications_have_rules(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)

        for dc in result.dimension_classifications:
            assert dc.rule, f"Classification {dc.classification} has no rule"
            assert dc.confidence == "DETERMINISTIC"

    def test_to_dict(self):
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)
        d = result.to_dict()

        assert "entity_id" in d
        assert "lifecycle_classification" in d
        assert "dimension_classifications" in d


# ═══════════════════════════════════════════════════════════════════════════════
# PROPOSAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestProposalGenerator:

    def test_no_proposals_for_aligned_lifecycle(self):
        """A clean aligned lifecycle should not generate investigation proposals."""
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        # Clean lifecycle may produce 0 proposals or only informational ones
        for p in proposals:
            assert p.proposal_type != "INVESTIGATION"

    def test_contradictory_generates_investigation(self):
        """Risk blocked + execution present should generate investigation."""
        # Create a contradictory scenario
        builders = {
            Universe.MARKET: MockBuilder(Universe.MARKET, [{"entity_id": "e3"}]),
            Universe.DECISION: MockBuilder(Universe.DECISION, [
                {"entity_id": "e3", "action": "EXECUTE", "score": 60},
            ]),
            Universe.STRATEGY: MockBuilder(Universe.STRATEGY, [{"entity_id": "e3", "family": "X"}]),
            Universe.RISK: MockBuilder(Universe.RISK, [
                {"entity_id": "e3", "risk_control_result": "BLOCKED", "risk_control_reason": "limit"},
            ]),
            Universe.EXECUTION: MockBuilder(Universe.EXECUTION, [
                {"entity_id": "e3", "r_multiple": -1.0},
            ]),
            Universe.OUTCOME: MockBuilder(Universe.OUTCOME, [
                {"entity_id": "e3", "r_multiple": -1.0},
            ]),
        }
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e3")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        investigation_proposals = [p for p in proposals if p.proposal_type == "INVESTIGATION"]
        assert len(investigation_proposals) >= 1

    def test_proposal_references_supporting_universes(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        for p in proposals:
            assert len(p.supporting_universes) > 0

    def test_proposal_has_governance_note(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        for p in proposals:
            assert "research" in p.governance_note.lower()
            assert "recommendation" not in p.governance_note.lower() or "not" in p.governance_note.lower()

    def test_proposal_references_entity_id(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        for p in proposals:
            assert len(p.evidence_entity_ids) > 0

    def test_proposal_to_dict(self):
        builders = make_partial_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e2")
        comparison = ComparisonBuilder().compare(trace)
        classification = CrossUniverseClassifier().classify(comparison)
        proposals = ProposalGenerator().generate(classification)

        for p in proposals:
            d = p.to_dict()
            assert "proposal_type" in d
            assert "trigger" in d
            assert "governance_note" in d


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE TEST
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullPipeline:

    def test_trace_compare_classify_propose(self):
        """End-to-end pipeline works."""
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("e1")

        comparison = ComparisonBuilder().compare(trace)
        assert comparison.entity_id == "e1"

        classification = CrossUniverseClassifier().classify(comparison)
        assert classification.lifecycle_classification == Classification.COMPLETE_LIFECYCLE

        proposals = ProposalGenerator().generate(classification)
        # May be empty for clean lifecycle — that's correct
        assert isinstance(proposals, list)

    def test_reproducibility_same_data_same_result(self):
        """Same universe data produces same trace/comparison/classification."""
        builders = make_full_builders()
        tracer = CrossUniverseTracer(builders)

        trace1 = tracer.trace("e1")
        trace2 = tracer.trace("e1")

        assert trace1.universe_versions == trace2.universe_versions
        assert trace1.trace_status == trace2.trace_status
        assert trace1.present_count == trace2.present_count


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
