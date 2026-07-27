"""
Tests for Research Decision Gates (Section 12).

Validates:
    - Historical results preserved (never overwritten)
    - Incomplete data blocks promotion
    - Complete lineage allows promotion
    - Q19 blocked until requirements met
    - Strategy changes rejected when evidence insufficient
    - READY questions can recommend action
    - Invalidated hypotheses cannot be promoted
    - Contamination blocks promotion
    - Gate requirements correctly assigned per question category
    - Promotion summary logic

Does NOT test trading logic — decision gates are reporting only.
"""

import sys
from pathlib import Path

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from research_engine.command_center.command_models import (
    CoverageField,
    DataHealth,
)
from research_engine.command_center.decision_gates import (
    DecisionGateReport,
    GateRequirement,
    PromotionReadinessSummary,
    ResearchDecision,
    ResearchDecisionStatus,
    evaluate_decision_gates,
    _build_promotion_summary,
    _classify_decision,
    _extract_coverage,
    _extract_historical_result,
    _get_requirements_for_question,
)

# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _data_health_incomplete() -> DataHealth:
    """Dataset with low coverage — typical early collection state."""
    return DataHealth(
        record_count=150,
        source="shadow_trades",
        outcome_coverage=CoverageField("outcome", 1.0, "OK"),
        lineage_coverage=CoverageField("entity_id", 0.25, "LOW"),
        context_fields=[
            CoverageField("H4 regime", 0.0, "MISSING"),
            CoverageField("H1 bias", 0.0, "MISSING"),
            CoverageField("market phase", 0.0, "MISSING"),
            CoverageField("strategy", 0.04, "LOW"),
            CoverageField("trade_horizon", 0.02, "LOW"),
        ],
        contamination_count=50,
        dataset_verdict="NOT READY FOR FULL RESEARCH",
    )


def _data_health_complete() -> DataHealth:
    """Dataset with full coverage — ready for promotion."""
    return DataHealth(
        record_count=500,
        source="shadow_trades",
        outcome_coverage=CoverageField("outcome", 0.99, "OK"),
        lineage_coverage=CoverageField("entity_id", 0.92, "OK"),
        context_fields=[
            CoverageField("H4 regime", 0.85, "OK"),
            CoverageField("H1 bias", 0.82, "OK"),
            CoverageField("market phase", 0.81, "OK"),
            CoverageField("strategy", 0.75, "OK"),
            CoverageField("trade_horizon", 0.68, "OK"),
        ],
        contamination_count=0,
        dataset_verdict="READY FOR FULL RESEARCH",
    )


def _dashboard_with_q19() -> dict:
    return {
        "questions": {
            "Q19": {
                "question": "What is the system's true edge?",
                "status": "COMPLETE",
                "runner": "experiments.expected_value",
                "last_run": "2026-07-21T22:40:40Z",
                "report_file": "analysis/reports/q19_expected_value.json",
                "recommendation": "POSITIVE_EDGE",
            },
            "Q20": {
                "question": "Is score calibrated?",
                "status": "COMPLETE",
                "runner": "experiments.score_calibration",
                "last_run": "2026-07-21T22:40:41Z",
                "report_file": "analysis/reports/q20_score_calibration.json",
                "recommendation": "PROMOTE_CALIBRATION",
            },
            "Q24": {
                "question": "Which strategies have expectancy?",
                "status": "COMPLETE",
                "runner": "experiments.research_runner",
                "last_run": "2026-07-21T22:40:55Z",
                "report_file": "analysis/reports/q24_strategy_edge.json",
                "recommendation": "COMPLETE",
            },
            "Q16": {
                "question": "Shadow vs live correlation?",
                "status": "COMPLETE",
                "runner": "experiments.shadow_validation",
                "last_run": "2026-07-21T22:40:40Z",
                "report_file": "analysis/reports/q16_shadow_validation.json",
                "recommendation": "BLOCKED",
            },
        },
    }


def _reports_q19() -> dict:
    return {
        "Q19": {
            "question_id": "Q19",
            "timestamp": "2026-07-21T22:40:40Z",
            "dataset": {"source": "shadow_trades", "sample_size": 501},
            "metrics": {"expected_value": 0.5407},
            "finding": "+0.55R EV with HIGH confidence",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: HISTORICAL RESULT PRESERVED
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoricalPreserved:
    """Historical findings are never overwritten or deleted."""

    def test_historical_result_from_report(self):
        """Historical result extracted from report finding."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        q19 = next(d for d in report.decisions if d.question_id == "Q19")
        assert "+0.55R" in q19.historical_result

    def test_historical_result_from_recommendation(self):
        """When no report exists, historical comes from recommendation."""
        dh = _data_health_incomplete()
        dashboard = {"questions": {"Q24": {
            "question": "Strategy?", "status": "COMPLETE",
            "recommendation": "COMPLETE", "runner": "x", "last_run": "2026-07-21T00:00:00Z",
        }}}
        report = evaluate_decision_gates(dh, dashboard, {})
        q24 = report.decisions[0]
        assert q24.historical_result == "COMPLETE"

    def test_historical_never_blank_when_status_exists(self):
        """Every completed question has some historical result."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        for d in report.decisions:
            if d.historical_status in ("COMPLETE", "POSITIVE_EDGE", "PROMOTE_CALIBRATION"):
                assert d.historical_result != "", f"{d.question_id} has blank historical result"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: INCOMPLETE DATA BLOCKS PROMOTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestIncompleteDataBlocks:
    """Incomplete data prevents any promotion decision."""

    def test_low_lineage_blocks_q19(self):
        """Q19 cannot be promoted with 25% lineage."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        q19 = next(d for d in report.decisions if d.question_id == "Q19")
        assert q19.current_status == ResearchDecisionStatus.NEEDS_DATA
        assert q19.can_change_strategy_logic is False

    def test_low_strategy_blocks_q24(self):
        """Q24 blocked without strategy coverage."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), {})
        q24 = next(d for d in report.decisions if d.question_id == "Q24")
        assert q24.can_change_strategy_logic is False

    def test_all_questions_blocked_when_incomplete(self):
        """No question allows changes with incomplete data."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        for d in report.decisions:
            assert d.can_change_strategy_logic is False


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: COMPLETE LINEAGE ALLOWS PROMOTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompleteLineageAllows:
    """Full coverage allows promotion decisions."""

    def test_q19_promotable_with_full_data(self):
        """Q19 POSITIVE_EDGE can be promoted with full coverage."""
        dh = _data_health_complete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        q19 = next(d for d in report.decisions if d.question_id == "Q19")
        assert q19.current_status == ResearchDecisionStatus.PROMOTE
        assert q19.can_change_strategy_logic is True
        assert q19.confidence == "HIGH"

    def test_q20_promotable_with_full_data(self):
        """Q20 PROMOTE_CALIBRATION works with full coverage."""
        dh = _data_health_complete()
        dashboard = _dashboard_with_q19()
        reports = {"Q20": {
            "question_id": "Q20", "timestamp": "2026-07-21",
            "dataset": {"source": "shadow_trades", "sample_size": 500},
            "metrics": {}, "finding": "Calibration needed",
        }}
        report = evaluate_decision_gates(dh, dashboard, reports)
        q20 = next(d for d in report.decisions if d.question_id == "Q20")
        assert q20.current_status == ResearchDecisionStatus.PROMOTE
        assert q20.can_change_strategy_logic is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Q19 BLOCKED UNTIL REQUIREMENTS MET
# ═══════════════════════════════════════════════════════════════════════════════


class TestQ19Requirements:
    """Q19 has strict requirements before action is allowed."""

    def test_q19_requires_lineage(self):
        """Q19 gates include entity_id >= 80%."""
        dh = _data_health_incomplete()
        cov = _extract_coverage(dh)
        reqs = _get_requirements_for_question("Q19", cov, dh)
        lineage_req = next(r for r in reqs if r.field_name == "entity_id")
        assert lineage_req.required_pct == 0.80
        assert lineage_req.met is False

    def test_q19_requires_no_contamination(self):
        """Q19 gates include zero contamination."""
        dh = _data_health_incomplete()
        cov = _extract_coverage(dh)
        reqs = _get_requirements_for_question("Q19", cov, dh)
        contam_req = next(r for r in reqs if r.field_name == "no_contamination")
        assert contam_req.met is False

    def test_q19_requires_200_samples(self):
        """Q19 gates include minimum 200 samples."""
        dh = _data_health_incomplete()  # has 150
        cov = _extract_coverage(dh)
        reqs = _get_requirements_for_question("Q19", cov, dh)
        sample_req = next(r for r in reqs if "200" in r.field_name)
        assert sample_req.met is False

    def test_q19_passes_with_complete_data(self):
        """Q19 all requirements met with complete data."""
        dh = _data_health_complete()  # has 500, no contamination
        cov = _extract_coverage(dh)
        reqs = _get_requirements_for_question("Q19", cov, dh)
        assert all(r.met for r in reqs)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: STRATEGY CHANGES REJECTED WHEN INSUFFICIENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyChangesRejected:
    """Strategy modifications are blocked without sufficient evidence."""

    def test_promotion_summary_blocked(self):
        """Promotion summary says NO when data incomplete."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), {})
        ps = report.promotion_summary
        assert ps.strategy_changes_allowed is False
        assert "incomplete" in ps.reason.lower() or "post-migration" in ps.reason.lower()

    def test_unsafe_actions_listed(self):
        """Unsafe actions are listed when changes blocked."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), {})
        ps = report.promotion_summary
        assert "remove patterns" in ps.unsafe_actions
        assert "change scoring weights" in ps.unsafe_actions

    def test_safe_actions_listed(self):
        """Safe actions always include data collection."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), {})
        ps = report.promotion_summary
        assert "collect data" in ps.safe_actions


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: READY QUESTIONS CAN RECOMMEND ACTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadyRecommendation:
    """Questions that pass all gates get actionable recommendations."""

    def test_promotable_question_has_action(self):
        """Promotable question recommends reviewing the finding."""
        dh = _data_health_complete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        q19 = next(d for d in report.decisions if d.question_id == "Q19")
        assert "Evidence supports" in q19.recommended_action or "action" in q19.recommended_action.lower()

    def test_promotion_summary_allows_when_ready(self):
        """Full data plus answered safety questions allows strategy changes."""
        dh = _data_health_complete()
        # Create decisions that simulate R3/R4/E5/P1 as answered
        from research_engine.command_center.decision_gates import ResearchDecision, ResearchDecisionStatus
        mock_decisions = [
            ResearchDecision(question_id="R3", title="", can_change_strategy_logic=True, current_status=ResearchDecisionStatus.PROMOTE),
            ResearchDecision(question_id="R4", title="", can_change_strategy_logic=True, current_status=ResearchDecisionStatus.PROMOTE),
            ResearchDecision(question_id="E5", title="", can_change_strategy_logic=True, current_status=ResearchDecisionStatus.PROMOTE),
            ResearchDecision(question_id="P1", title="", can_change_strategy_logic=True, current_status=ResearchDecisionStatus.PROMOTE),
        ]
        cov = _extract_coverage(dh)
        ps = _build_promotion_summary(cov, dh, mock_decisions)
        assert ps.strategy_changes_allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: INVALIDATED HYPOTHESES CANNOT BE PROMOTED
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidatedBlocked:
    """Invalidated findings can never be promoted."""

    def test_blocked_status_prevents_promotion(self):
        """Q16 with BLOCKED recommendation stays blocked."""
        dh = _data_health_complete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), {})
        q16 = next(d for d in report.decisions if d.question_id == "Q16")
        assert q16.current_status == ResearchDecisionStatus.BLOCKED
        assert q16.can_change_strategy_logic is False

    def test_invalidated_cannot_change(self):
        """INVALIDATED status prevents any strategy change."""
        dh = _data_health_complete()
        dashboard = {"questions": {"QX": {
            "question": "Test?", "status": "COMPLETE",
            "recommendation": "INVALIDATED", "runner": "x", "last_run": "2026-07-27T00:00:00Z",
        }}}
        report = evaluate_decision_gates(dh, dashboard, {})
        qx = report.decisions[0]
        assert qx.current_status == ResearchDecisionStatus.INVALIDATED
        assert qx.can_change_strategy_logic is False


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: CONTAMINATION BLOCKS PROMOTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestContaminationBlocks:
    """Contaminated datasets prevent promotion even with sufficient samples."""

    def test_contamination_blocks_ev_promotion(self):
        """Q19 blocked by contamination even with 500 records."""
        dh = DataHealth(
            record_count=500, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.99, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.92, "OK"),
            context_fields=[
                CoverageField("H4 regime", 0.85, "OK"),
                CoverageField("strategy", 0.75, "OK"),
                CoverageField("trade_horizon", 0.68, "OK"),
            ],
            contamination_count=10,  # Contaminated!
            dataset_verdict="PARTIAL",
        )
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        q19 = next(d for d in report.decisions if d.question_id == "Q19")
        # Contamination gate should block
        contam_req = next((r for r in q19.blocking_requirements if "contamination" in r.field_name), None)
        assert contam_req is not None
        assert contam_req.met is False
        assert q19.can_change_strategy_logic is False

    def test_contamination_blocks_promotion_summary(self):
        """Overall promotion blocked with contamination."""
        dh = DataHealth(
            record_count=500, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.99, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.92, "OK"),
            context_fields=[
                CoverageField("strategy", 0.75, "OK"),
                CoverageField("trade_horizon", 0.68, "OK"),
            ],
            contamination_count=5,
            dataset_verdict="PARTIAL",
        )
        cov = _extract_coverage(dh)
        ps = _build_promotion_summary(cov, dh, [])
        assert ps.strategy_changes_allowed is False
        assert "contamination" in " ".join(ps.required_before_changes).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: GATE REQUIREMENTS BY CATEGORY
# ═══════════════════════════════════════════════════════════════════════════════


class TestGateCategories:
    """Different question categories have different requirements."""

    def test_ev_questions_need_strategy_and_horizon(self):
        """Q19, Q20, Q21, Q22 require strategy + horizon + no contamination."""
        dh = _data_health_complete()
        cov = _extract_coverage(dh)
        for qid in ("Q19", "Q20", "Q21", "Q22"):
            reqs = _get_requirements_for_question(qid, cov, dh)
            field_names = [r.field_name for r in reqs]
            assert "strategy" in field_names
            assert "trade_horizon" in field_names
            assert "no_contamination" in field_names

    def test_regime_questions_need_h4(self):
        """Q23, Q6 require H4 regime coverage."""
        dh = _data_health_complete()
        cov = _extract_coverage(dh)
        for qid in ("Q23", "Q6"):
            reqs = _get_requirements_for_question(qid, cov, dh)
            field_names = [r.field_name for r in reqs]
            assert "h4_regime" in field_names

    def test_strategy_questions_need_strategy(self):
        """Q24, Q2 require strategy coverage."""
        dh = _data_health_complete()
        cov = _extract_coverage(dh)
        for qid in ("Q24", "Q2"):
            reqs = _get_requirements_for_question(qid, cov, dh)
            field_names = [r.field_name for r in reqs]
            assert "strategy" in field_names

    def test_execution_questions_need_trade_truth(self):
        """Q11, Q12, Q16 require trade_truth."""
        dh = _data_health_complete()
        cov = _extract_coverage(dh)
        for qid in ("Q11", "Q12", "Q16"):
            reqs = _get_requirements_for_question(qid, cov, dh)
            field_names = [r.field_name for r in reqs]
            assert "trade_truth" in field_names

    def test_all_questions_need_lineage(self):
        """Every question needs entity_id coverage."""
        dh = _data_health_complete()
        cov = _extract_coverage(dh)
        for qid in ("Q1", "Q5", "Q19", "Q23"):
            reqs = _get_requirements_for_question(qid, cov, dh)
            field_names = [r.field_name for r in reqs]
            assert "entity_id" in field_names


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    """Decision gate report serializes correctly."""

    def test_to_dict(self):
        """DecisionGateReport.to_dict() produces valid structure."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, _dashboard_with_q19(), _reports_q19())
        d = report.to_dict()
        assert "decisions" in d
        assert "promotion_summary" in d
        assert len(d["decisions"]) == 4
        assert d["promotion_summary"]["strategy_changes_allowed"] is False

    def test_decision_to_dict(self):
        """Individual ResearchDecision serializes correctly."""
        decision = ResearchDecision(
            question_id="Q19", title="Expected value",
            historical_status="POSITIVE_EDGE",
            historical_result="+0.55R EV",
            current_status=ResearchDecisionStatus.NEEDS_DATA,
            confidence="INSUFFICIENT",
            can_change_strategy_logic=False,
            blocking_requirements=[GateRequirement("entity_id", 0.80, 0.25, False)],
            recommended_action="Collect data",
        )
        d = decision.to_dict()
        assert d["question_id"] == "Q19"
        assert d["can_change_strategy_logic"] is False
        assert d["blocking_requirements"][0]["met"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EMPTY STATE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyState:
    """Decision gates handle empty data gracefully."""

    def test_no_dashboard(self):
        """No dashboard produces empty decisions."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, None, {})
        assert report.decisions == []
        assert report.promotion_summary.strategy_changes_allowed is False

    def test_empty_dashboard(self):
        """Empty questions dict produces empty decisions."""
        dh = _data_health_incomplete()
        report = evaluate_decision_gates(dh, {"questions": {}}, {})
        assert report.decisions == []
