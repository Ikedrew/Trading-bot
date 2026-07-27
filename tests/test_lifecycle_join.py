"""
Tests for Phase 3B Lifecycle Join Framework.

Covers:
    - Complete lifecycle join (opportunity → outcome)
    - Partial lifecycle (rejected opportunity)
    - Missing records handling
    - Duplicate detection
    - Quality assessment
    - Final state derivation
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_engine.joins.lifecycle_join import (
    join_lifecycle,
    LifecycleRecord,
    LifecycleQuality,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _opp_executed():
    return {
        "opportunity_id": "GBPUSD_1784809820_TWEEZER_TOP",
        "symbol": "GBPUSD",
        "cycle_id": 4578,
        "entity_id": "GBPUSD_1784809820",
        "pattern": "TWEEZER_TOP",
        "direction": "SELL",
        "state": "EXECUTED",
    }


def _opp_rejected():
    return {
        "opportunity_id": "NZDUSD_1784809820_EVENING_STAR",
        "symbol": "NZDUSD",
        "cycle_id": 4578,
        "entity_id": "NZDUSD_1784809820",
        "pattern": "EVENING_STAR",
        "direction": "SELL",
        "state": "REJECTED",
        "rejection_reason": "pattern_not_selected",
    }


def _assessment():
    return {
        "assessment_id": "GBPUSD_1784809820_TWEEZER_TOP_assessment",
        "opportunity_id": "GBPUSD_1784809820_TWEEZER_TOP",
        "symbol": "GBPUSD",
        "cycle_id": 4578,
        "score_strategy": 0.62,
        "ev": 0.000142,
    }


def _ranking():
    return {
        "ranking_id": "ranking_4578_123",
        "cycle_id": 4578,
        "selected_symbol": "GBPUSD",
        "candidates": [
            {
                "symbol": "GBPUSD",
                "opportunity_id": "GBPUSD_4578_TWEEZER_TOP",
                "rank_position": 1,
                "selection_status": "SELECTED",
                "rank_score": 0.000142,
            },
            {
                "symbol": "NZDUSD",
                "opportunity_id": "NZDUSD_4578_EVENING_STAR",
                "rank_position": 2,
                "selection_status": "OUTRANKED",
                "rank_score": 0.000098,
            },
        ],
    }


def _shadow():
    return {
        "cycle_id": 4578,
        "agreement": True,
        "ranking_selected_symbol": "GBPUSD",
        "actual_executed_symbols": ["GBPUSD"],
    }


def _decision_execute():
    return {
        "symbol": "GBPUSD",
        "cycle_id": 4578,
        "entity_id": "GBPUSD_1784809820",
        "decision": "EXECUTE",
        "reason": "all_guards_passed",
        "correlation_id": "COR-20260723-4578-GBPUSD-0DCA",
        "signal_score": 6.0,
    }


def _decision_reject():
    return {
        "symbol": "NZDUSD",
        "cycle_id": 4578,
        "entity_id": "NZDUSD_1784809820",
        "decision": "NO_TRADE",
        "reason": "ev_policy_blocked",
        "correlation_id": "",
    }


def _execution():
    return {
        "symbol": "GBPUSD",
        "correlation_id": "COR-20260723-4578-GBPUSD-0DCA",
        "result_ok": True,
        "fill_price": 1.33424,
        "slippage": 0.00002,
    }


def _trade_truth():
    return {
        "identity": {
            "trade_id": "pos_53388774",
            "correlation_id": "COR-20260723-4578-GBPUSD-0DCA",
            "symbol": "GBPUSD",
        },
        "outcome": {
            "r_multiple_realised": 2.11,
            "pnl_realised": 1.37,
        },
        "exit": {
            "exit_reason": "take_profit_hit",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: COMPLETE LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompleteLifecycle:
    """Full lifecycle: opportunity through to trade truth."""

    def test_complete_join(self):
        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=[_assessment()],
            rankings=[_ranking()],
            shadow_comparisons=[_shadow()],
            decisions=[_decision_execute()],
            executions=[_execution()],
            trade_truths=[_trade_truth()],
        )

        assert len(results) == 1
        r = results[0]

        assert r.opportunity_id == "GBPUSD_1784809820_TWEEZER_TOP"
        assert r.symbol == "GBPUSD"
        assert r.opportunity is not None
        assert r.assessment is not None
        assert r.ranking is not None
        assert r.decision is not None
        assert r.execution is not None
        assert r.outcome is not None
        assert r.final_state == "EXECUTED"
        assert r.r_multiple == 2.11
        assert r.pnl == 1.37

    def test_complete_quality(self):
        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=[_assessment()],
            rankings=[_ranking()],
            shadow_comparisons=[_shadow()],
            decisions=[_decision_execute()],
            executions=[_execution()],
            trade_truths=[_trade_truth()],
        )

        q = results[0].quality
        assert q.is_complete
        assert q.completeness >= 0.99
        assert not q.is_orphan
        assert q.duplicate_count == 0
        assert "opportunity" in q.stages_present
        assert "outcome" in q.stages_present


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PARTIAL LIFECYCLE (REJECTED)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartialLifecycle:
    """Rejected opportunity: no execution, no outcome."""

    def test_rejected_opportunity(self):
        results = join_lifecycle(
            opportunities=[_opp_rejected()],
            assessments=[],
            rankings=[_ranking()],
            decisions=[_decision_reject()],
            executions=[],
            trade_truths=[],
        )

        assert len(results) == 1
        r = results[0]
        assert r.final_state == "REJECTED"
        assert r.rejection_reason == "pattern_not_selected"
        assert r.outcome is None
        assert r.execution is None
        assert r.r_multiple is None

    def test_rejected_has_ranking(self):
        """Rejected opportunity still shows in ranking candidates."""
        results = join_lifecycle(
            opportunities=[_opp_rejected()],
            rankings=[_ranking()],
            decisions=[_decision_reject()],
        )

        r = results[0]
        assert r.ranking is not None
        assert r.ranking["selection_status"] == "OUTRANKED"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MISSING RECORDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingRecords:
    """Handles missing data gracefully."""

    def test_opportunity_only(self):
        """Opportunity with no downstream data."""
        results = join_lifecycle(opportunities=[_opp_executed()])
        assert len(results) == 1
        r = results[0]
        assert r.opportunity is not None
        assert r.assessment is None
        assert r.decision is None
        assert r.quality.completeness < 0.5

    def test_no_assessment_available(self):
        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=[],  # Empty
            decisions=[_decision_execute()],
        )
        r = results[0]
        assert "assessment" in r.quality.stages_missing
        assert r.decision is not None

    def test_empty_opportunities_returns_empty(self):
        results = join_lifecycle(opportunities=[])
        assert results == []

    def test_none_datasets_handled(self):
        """None passed for optional datasets."""
        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=None,
            rankings=None,
            shadow_comparisons=None,
            decisions=None,
            executions=None,
            trade_truths=None,
        )
        assert len(results) == 1
        assert results[0].quality.completeness < 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicates:
    """Detects duplicate records at join points."""

    def test_duplicate_assessments_counted(self):
        # Two assessments with same opportunity_id
        a1 = _assessment()
        a2 = _assessment()
        a2["ev"] = 0.0002  # Different value but same ID

        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=[a1, a2],
        )

        r = results[0]
        assert r.quality.duplicate_count >= 1
        assert r.assessment is not None  # Takes first match


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MULTIPLE OPPORTUNITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleOpportunities:
    """Multiple opportunities produce multiple lifecycle records."""

    def test_two_opportunities_same_cycle(self):
        results = join_lifecycle(
            opportunities=[_opp_executed(), _opp_rejected()],
            assessments=[_assessment()],
            rankings=[_ranking()],
            shadow_comparisons=[_shadow()],
            decisions=[_decision_execute(), _decision_reject()],
            executions=[_execution()],
            trade_truths=[_trade_truth()],
        )

        assert len(results) == 2

        executed = [r for r in results if r.final_state == "EXECUTED"]
        rejected = [r for r in results if r.final_state == "REJECTED"]

        assert len(executed) == 1
        assert len(rejected) == 1
        assert executed[0].r_multiple == 2.11
        assert rejected[0].r_multiple is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: TO_DICT SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_to_dict_produces_flat_structure(self):
        results = join_lifecycle(
            opportunities=[_opp_executed()],
            assessments=[_assessment()],
            decisions=[_decision_execute()],
            executions=[_execution()],
            trade_truths=[_trade_truth()],
        )

        d = results[0].to_dict()
        assert "opportunity" in d
        assert "assessment" in d
        assert "outcome" in d
        assert "quality" in d
        assert d["final_state"] == "EXECUTED"
        assert d["r_multiple"] == 2.11
