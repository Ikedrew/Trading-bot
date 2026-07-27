"""
Tests for Research Readiness Dashboard.

Validates:
    1. Dashboard generation from synthetic data
    2. Coverage calculations correct
    3. Question status aggregation
    4. Critical blocker detection
    5. Readiness score computation
    6. Experiment gate blocks when requirements fail
    7. Experiment gate allows when data is ready

No trading logic is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from research_engine.dashboard import (
    generate_dashboard,
    can_execute,
    get_execution_gate,
    _compute_readiness_score,
    _classify_readiness,
    _field_status,
)
from research_engine.validation import validate_dataset


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_complete_records(n=30):
    """Records with all fields populated — should score ~100."""
    return [
        {
            "schema_version": "shadow_trades_v2",
            "identity": {"entity_id": f"EURUSD_{i*100}", "trade_id": f"t{i}", "strategy_id": "REVERSAL", "correlation_id": f"COR-20260727-{i}-EURUSD-ABCD"},
            "decision_snapshot": {
                "pattern": "HAMMER",
                "score": 0.7,
                "strategy": "REVERSAL",
                "trade_horizon": "SCALP",
                "regime": "TRENDING",
                "h4_regime": "TRENDING",
                "h1_bias": "BULLISH",
                "market_phase": "IMPULSE",
                "market_phase_confidence": 0.8,
            },
            "simulation_environment": {
                "htf_snapshot": {
                    "timeframe_bias": {
                        "H4": {"regime": "TRENDING", "bias": "BULLISH"},
                        "H1": {"bias": "BULLISH", "regime": "TRENDING"},
                    }
                }
            },
            "simulated_outcome": {"pnl_r_multiple": 1.5, "exit_reason": "take_profit", "bars_held": 10},
        }
        for i in range(n)
    ]


def _make_minimal_records(n=30):
    """Records with only pattern + outcome — typical replay data."""
    return [
        {
            "schema_version": "shadow_trades_v2",
            "identity": {"trade_id": f"t{i}"},
            "decision_snapshot": {"pattern": "TWEEZER_TOP", "score": 0.5},
            "simulation_environment": {},
            "simulated_outcome": {"pnl_r_multiple": -0.5, "exit_reason": "stop_loss", "bars_held": 5},
        }
        for i in range(n)
    ]


def _make_empty_records():
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardGeneration:
    """Dashboard generates without errors from various datasets."""

    def test_generates_from_complete_data(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert dashboard.total_records == 30
        assert dashboard.readiness_score > 0

    def test_generates_from_minimal_data(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert dashboard.total_records == 30
        assert dashboard.readiness_score > 0

    def test_generates_from_empty_data(self):
        dashboard = generate_dashboard(shadow_records=[], trace_records=[])
        assert dashboard.total_records == 0
        assert dashboard.readiness_score == 0

    def test_to_dict_serializable(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        d = dashboard.to_dict()
        assert isinstance(d, dict)
        assert "readiness_score" in d
        assert "coverage" in d
        assert "questions" in d


# ═══════════════════════════════════════════════════════════════════════════════
# 2. COVERAGE CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoverageCalculations:
    """Coverage entries reflect actual data content."""

    def test_complete_data_high_coverage(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        cov = {c.field: c.coverage_pct for c in dashboard.coverage}
        assert cov["outcome"] == 1.0
        assert cov["pattern"] == 1.0
        assert cov["entity_id"] == 1.0
        assert cov["strategy"] == 1.0
        assert cov["h4_regime"] == 1.0

    def test_minimal_data_low_coverage(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        cov = {c.field: c.coverage_pct for c in dashboard.coverage}
        assert cov["outcome"] == 1.0  # Always has outcome
        assert cov["entity_id"] == 0.0  # No entity_id
        assert cov["h4_regime"] == 0.0  # No H4 regime
        assert cov["market_phase"] == 0.0  # No phase

    def test_field_status_classification(self):
        assert _field_status(0.90) == "ready"
        assert _field_status(0.60) == "usable"
        assert _field_status(0.10) == "collecting"
        assert _field_status(0.0) == "insufficient"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. QUESTION STATUS AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionAggregation:
    """Questions are correctly classified into READY/WAITING/BLOCKED."""

    def test_complete_data_has_ready_questions(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert len(dashboard.questions_ready) > 0

    def test_minimal_data_has_blocked_questions(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        non_ready = len(dashboard.questions_blocked) + len(dashboard.questions_waiting)
        assert non_ready > 0

    def test_question_counts_sum_to_total(self):
        records = _make_complete_records()
        from research_engine.registry.research_question_registry import REGISTRY
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        total = len(dashboard.questions_ready) + len(dashboard.questions_waiting) + len(dashboard.questions_blocked)
        assert total == len(REGISTRY)  # All registry questions accounted for


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CRITICAL BLOCKER DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlockerDetection:
    """Critical blockers identified when data is insufficient."""

    def test_minimal_data_has_blockers(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert len(dashboard.critical_blockers) > 0

    def test_complete_data_no_blockers(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert len(dashboard.critical_blockers) == 0

    def test_lineage_blocker_detected(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        lineage_blockers = [b for b in dashboard.critical_blockers if "Lineage" in b or "lineage" in b.lower()]
        assert len(lineage_blockers) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. READINESS SCORE
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadinessScore:
    """Readiness score reflects data quality."""

    def test_complete_data_high_score(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert dashboard.readiness_score >= 90

    def test_minimal_data_low_score(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert dashboard.readiness_score < 50

    def test_empty_data_zero_score(self):
        dashboard = generate_dashboard(shadow_records=[], trace_records=[])
        assert dashboard.readiness_score == 0

    def test_classify_readiness(self):
        assert _classify_readiness(85) == "READY"
        assert _classify_readiness(65) == "PARTIAL"
        assert _classify_readiness(30) == "NOT_READY"

    def test_score_bounded(self):
        records = _make_complete_records()
        sv = validate_dataset(records, dataset_name="test")
        score = _compute_readiness_score(sv)
        assert 0 <= score <= 100


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EXPERIMENT GATE — BLOCKS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentGateBlocks:
    """Gate blocks execution when data requirements not met."""

    def test_e1_blocked_without_lineage(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert can_execute("E1", dashboard) is False

    def test_m1_blocked_without_h4_regime(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert can_execute("M1", dashboard) is False

    def test_unknown_question_blocked(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert can_execute("Z99", dashboard) is False

    def test_gate_detail_includes_reason(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        gate = get_execution_gate("E1", dashboard)
        assert gate["allowed"] is False
        assert gate["status"] in ("BLOCKED", "WAITING_DATA")
        assert len(gate["reason"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EXPERIMENT GATE — ALLOWS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentGateAllows:
    """Gate allows execution when data requirements are met."""

    def test_e2_allowed_with_pattern_and_outcome(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert can_execute("E2", dashboard) is True

    def test_g1_allowed_with_outcomes(self):
        records = _make_minimal_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        assert can_execute("G1", dashboard) is True

    def test_gate_detail_shows_allowed(self):
        records = _make_complete_records()
        dashboard = generate_dashboard(shadow_records=records, trace_records=[])
        gate = get_execution_gate("E2", dashboard)
        assert gate["allowed"] is True
        assert gate["status"] == "READY"
