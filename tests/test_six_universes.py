"""
Tests for the six-universe architecture: Risk + Outcome builders.

Covers:
- Universe enum has all 6 values
- Population enum has Risk and Outcome populations
- RiskUniverseBuilder constructs from decision trace data
- RiskUniverseBuilder exclusion tracking
- RiskUniverseBuilder population filtering
- OutcomeUniverseBuilder wraps Execution records
- OutcomeUniverseBuilder population filtering
- OutcomeUniverseBuilder exclusion tracking
- Backward compatibility (existing 4 universes unchanged)
- Identity preservation (entity_id propagates)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from unittest.mock import patch

from research_engine.v10.universes.models import ACTIVE_UNIVERSES, RETIRED_UNIVERSES, Universe, Population
from research_engine.v10.universes.risk_universe import RiskUniverseBuilder
from research_engine.v10.universes.outcome_universe import OutcomeUniverseBuilder
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE ENUM
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniverseEnum:
    """Verify all six universe values exist."""

    def test_six_universes_defined(self):
        assert Universe.EXECUTION == "EXECUTION"
        assert Universe.DECISION == "DECISION"
        assert Universe.MARKET == "MARKET"
        assert Universe.STRATEGY == "STRATEGY"
        assert Universe.RISK == "RISK"
        assert Universe.OUTCOME == "OUTCOME"

    def test_universe_count(self):
        assert len(ACTIVE_UNIVERSES) == 7
        assert Universe.SHADOW_OUTCOME in ACTIVE_UNIVERSES
        assert RETIRED_UNIVERSES == (Universe.SHADOW_REALITY,)

    def test_backward_compatible_values(self):
        """Existing 4 values unchanged."""
        assert Universe("EXECUTION") == Universe.EXECUTION
        assert Universe("DECISION") == Universe.DECISION
        assert Universe("MARKET") == Universe.MARKET
        assert Universe("STRATEGY") == Universe.STRATEGY


class TestPopulationEnum:
    """Verify Risk and Outcome populations exist."""

    def test_risk_populations(self):
        assert Population.ALL_RISK_EVALUATIONS == "all_risk_evaluations"
        assert Population.RISK_APPROVED == "risk_approved"
        assert Population.RISK_BLOCKED == "risk_blocked"

    def test_outcome_populations(self):
        assert Population.ALL_OUTCOMES == "all_outcomes"
        assert Population.OUTCOME_WINS == "outcome_wins"
        assert Population.OUTCOME_LOSSES == "outcome_losses"

    def test_existing_populations_unchanged(self):
        assert Population.ALL_TRADES == "all_trades"
        assert Population.ALL_DECISIONS == "all_decisions"
        assert Population.ALL_MARKET_STATES == "all_market_states"
        assert Population.ALL_STRATEGIES == "all_strategies"


# ═══════════════════════════════════════════════════════════════════════════════
# RISK UNIVERSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskUniverseBuilder:
    """Verify RiskUniverseBuilder constructs correctly from decision trace data."""

    def _make_builder_with_data(self, raw_records):
        builder = RiskUniverseBuilder(source_dir=Path("nonexistent"))
        builder._raw = raw_records
        builder.build()
        return builder

    def test_builds_from_risk_evidence(self):
        raw = [
            {
                "entity_id": "e1",
                "symbol": "EURUSD",
                "timestamp_utc": "2026-01-01T10:00:00Z",
                "v10_risk": {"approved": True, "risk_percentage": 1.0, "position_size": 0.1},
            },
            {
                "entity_id": "e2",
                "symbol": "GBPUSD",
                "timestamp_utc": "2026-01-01T11:00:00Z",
                "v10_risk": {"approved": False, "rejection_reason": "exposure_limit", "risk_percentage": 2.0},
            },
        ]
        builder = self._make_builder_with_data(raw)
        assert builder.is_built
        assert len(builder.records) == 2

    def test_universe_type(self):
        builder = RiskUniverseBuilder(source_dir=Path("nonexistent"))
        assert builder.universe_type == Universe.RISK

    def test_exclusion_no_entity_id(self):
        raw = [
            {"entity_id": "", "v10_risk": {"approved": True}},
            {"entity_id": "e1", "v10_risk": {"approved": True}},
        ]
        builder = self._make_builder_with_data(raw)
        assert len(builder.records) == 1
        assert builder.metadata.exclusions["reasons"]["missing_entity_id"] == 1

    def test_exclusion_no_risk_data(self):
        raw = [
            {"entity_id": "e1"},  # no v10_risk
            {"entity_id": "e2", "v10_risk": {}},  # empty v10_risk
            {"entity_id": "e3", "v10_risk": {"approved": True}},
        ]
        builder = self._make_builder_with_data(raw)
        assert len(builder.records) == 1
        assert builder.metadata.exclusions["reasons"]["no_v10_risk_data"] == 2

    def test_exclusion_risk_not_reached(self):
        raw = [
            {"entity_id": "e1", "v10_risk": {"some_field": "value"}},  # no approved field
            {"entity_id": "e2", "v10_risk": {"approved": True}},
        ]
        builder = self._make_builder_with_data(raw)
        assert len(builder.records) == 1
        assert builder.metadata.exclusions["reasons"]["risk_not_reached"] == 1

    def test_population_approved(self):
        raw = [
            {"entity_id": "e1", "v10_risk": {"approved": True}},
            {"entity_id": "e2", "v10_risk": {"approved": False, "rejection_reason": "limit"}},
            {"entity_id": "e3", "v10_risk": {"approved": True}},
        ]
        builder = self._make_builder_with_data(raw)
        approved = builder.get_population(Population.RISK_APPROVED)
        blocked = builder.get_population(Population.RISK_BLOCKED)
        assert len(approved) == 2
        assert len(blocked) == 1

    def test_population_all(self):
        raw = [
            {"entity_id": "e1", "v10_risk": {"approved": True}},
            {"entity_id": "e2", "v10_risk": {"approved": False}},
        ]
        builder = self._make_builder_with_data(raw)
        all_evals = builder.get_population(Population.ALL_RISK_EVALUATIONS)
        assert len(all_evals) == 2

    def test_normalised_fields(self):
        raw = [
            {
                "entity_id": "e1",
                "correlation_id": "c1",
                "symbol": "EURUSD",
                "cycle_id": "cyc_1",
                "timestamp_utc": "2026-01-01T10:00:00Z",
                "v10_risk": {
                    "approved": False,
                    "rejection_reason": "exposure_limit",
                    "risk_percentage": 1.5,
                    "position_size": 0.2,
                },
            }
        ]
        builder = self._make_builder_with_data(raw)
        rec = builder.records[0]
        assert rec["entity_id"] == "e1"
        assert rec["risk_control_result"] == "BLOCKED"
        assert rec["risk_control_reason"] == "exposure_limit"
        assert rec["risk_percentage"] == 1.5
        assert rec["position_size"] == 0.2
        assert rec["r_multiple"] is None  # placeholder for enrichment

    def test_content_hash_deterministic(self):
        raw = [
            {"entity_id": "e1", "v10_risk": {"approved": True, "risk_percentage": 1.0}},
        ]
        b1 = self._make_builder_with_data(raw)
        b2 = self._make_builder_with_data(raw)
        assert b1.metadata.content_hash == b2.metadata.content_hash

    def test_exclusion_accounting(self):
        raw = [
            {"entity_id": "e1", "v10_risk": {"approved": True}},
            {"entity_id": "", "v10_risk": {"approved": True}},
            {"entity_id": "e2"},
            {"entity_id": "e3", "v10_risk": {"approved": None}},
        ]
        builder = self._make_builder_with_data(raw)
        exc = builder.metadata.exclusions
        assert exc["source_records"] == 4
        assert exc["included_records"] == 1
        assert exc["total"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# OUTCOME UNIVERSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutcomeUniverseBuilder:
    """Verify OutcomeUniverseBuilder wraps Execution correctly."""

    def _make_exe_builder(self, records):
        """Create a mock-built ExecutionUniverseBuilder with given records."""
        builder = ExecutionUniverseBuilder(
            source_path=Path("nonexistent.jsonl"),
            execution_results_dir=Path("nonexistent_dir"),
        )
        builder._records = records
        builder._built = True
        builder._metadata = builder._generate_metadata(
            records=records,
            source_files=("test_source.jsonl",),
            populations=("all_trades",),
        )
        return builder

    def test_builds_from_execution_records(self):
        exe_records = [
            {"trade_id": "t1", "entity_id": "e1", "r_multiple": 1.5, "symbol": "EURUSD"},
            {"trade_id": "t2", "entity_id": "e2", "r_multiple": -1.0, "symbol": "GBPUSD"},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        assert outcome.is_built
        assert len(outcome.records) == 2

    def test_universe_type(self):
        outcome = OutcomeUniverseBuilder()
        assert outcome.universe_type == Universe.OUTCOME

    def test_population_wins(self):
        exe_records = [
            {"trade_id": "t1", "r_multiple": 2.0},
            {"trade_id": "t2", "r_multiple": -1.0},
            {"trade_id": "t3", "r_multiple": 0.5},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        wins = outcome.get_population(Population.OUTCOME_WINS)
        losses = outcome.get_population(Population.OUTCOME_LOSSES)
        assert len(wins) == 2  # r > 0
        assert len(losses) == 1  # r <= 0

    def test_population_all_outcomes(self):
        exe_records = [
            {"trade_id": "t1", "r_multiple": 1.0},
            {"trade_id": "t2", "r_multiple": -0.5},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        all_out = outcome.get_population(Population.ALL_OUTCOMES)
        assert len(all_out) == 2

    def test_backward_compatible_populations(self):
        """ALL_TRADES/WINNING_TRADES/LOSING_TRADES still work."""
        exe_records = [
            {"trade_id": "t1", "r_multiple": 1.0},
            {"trade_id": "t2", "r_multiple": -1.0},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        assert len(outcome.get_population(Population.ALL_TRADES)) == 2
        assert len(outcome.get_population(Population.WINNING_TRADES)) == 1
        assert len(outcome.get_population(Population.LOSING_TRADES)) == 1

    def test_no_data_duplication(self):
        """Outcome records are the same objects as Execution records (no copy)."""
        exe_records = [
            {"trade_id": "t1", "r_multiple": 1.0, "entity_id": "e1"},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        # Same object — no duplication
        assert outcome.records[0] is exe_records[0]

    def test_content_hash_matches_population(self):
        exe_records = [
            {"trade_id": "t1", "r_multiple": 1.0},
            {"trade_id": "t2", "r_multiple": -1.0},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        assert len(outcome.metadata.content_hash) == 16
        assert outcome.metadata.record_count == 2

    def test_exclusion_tracking(self):
        exe_records = [
            {"trade_id": "t1", "r_multiple": 1.0},
            {"trade_id": "t2", "r_multiple": None},  # should be excluded by exe builder, but test edge case
            {"trade_id": "t3", "r_multiple": -0.5},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        # One excluded (r_multiple=None)
        assert outcome.metadata.exclusions["reasons"]["missing_r_multiple"] == 1
        assert outcome.metadata.exclusions["included_records"] == 2

    def test_builds_empty_without_execution_builder(self):
        outcome = OutcomeUniverseBuilder(execution_builder=None)
        outcome.build()
        assert outcome.is_built
        assert len(outcome.records) == 0

    def test_identity_preserved(self):
        exe_records = [
            {"trade_id": "t1", "entity_id": "ent_123", "r_multiple": 1.5},
        ]
        exe = self._make_exe_builder(exe_records)
        outcome = OutcomeUniverseBuilder(execution_builder=exe)
        outcome.build()

        assert outcome.records[0]["entity_id"] == "ent_123"


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSixUniverseIntegration:
    """Verify all six universes can coexist."""

    def test_all_universe_types_distinct(self):
        values = [u.value for u in Universe]
        assert len(values) == len(set(values))

    def test_imports_work(self):
        from research_engine.v10.universes import (
            ExecutionUniverseBuilder,
            DecisionUniverseBuilder,
            MarketUniverseBuilder,
            StrategyUniverseBuilder,
            RiskUniverseBuilder,
            OutcomeUniverseBuilder,
        )
        assert RiskUniverseBuilder is not None
        assert OutcomeUniverseBuilder is not None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
