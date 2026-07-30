"""Phase J.1 — V10 Reporting Cleanup Tests.

Verifies:
  - No legacy scoring in V10 terminal output
  - Rejection stage is accurate (first failure wins)
  - V10 formatter is sole output authority
"""

import pytest
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.decision_report import format_v10_decision
from core.v10.decision_context import V10DecisionContext
from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.entry_model import EntryDecision, EntryStatus
from core.v10.risk_model import RiskDecision
from core.v10.execution_model import ExecutionDecision
from core.v3_shadow.models import MarketUnderstanding


def _run(symbol="EURUSD"):
    mu = MarketUnderstanding(symbol=symbol, timestamp_utc=1785400000.0)
    return V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())


class TestLegacyConceptsAbsent:
    """V10 terminal output must NOT contain legacy scoring concepts."""

    def test_no_composite_score(self):
        report = format_v10_decision(_run())
        assert "Composite Score" not in report
        assert "composite_score" not in report.lower()

    def test_no_neutral_score(self):
        report = format_v10_decision(_run())
        assert "Neutral Score" not in report

    def test_no_strategy_score(self):
        report = format_v10_decision(_run())
        assert "Strategy Score" not in report

    def test_no_final_score_summary(self):
        report = format_v10_decision(_run())
        assert "FINAL SCORE SUMMARY" not in report

    def test_no_grade(self):
        report = format_v10_decision(_run())
        # "Grade" as a standalone concept (not in words like "upgrade")
        assert "Grade:" not in report
        assert "Grade =" not in report

    def test_no_threshold(self):
        report = format_v10_decision(_run())
        assert "Threshold:" not in report
        assert "Threshold =" not in report

    def test_no_dual_scoring(self):
        report = format_v10_decision(_run())
        assert "Dual Scoring" not in report

    def test_no_execution_policy_legacy(self):
        report = format_v10_decision(_run())
        assert "Execution Policy" not in report

    def test_no_permission_legacy(self):
        report = format_v10_decision(_run())
        assert "Permission:" not in report


class TestV10ReportStructure:
    """V10 terminal output must contain V10 section headers."""

    def test_has_market_understanding(self):
        report = format_v10_decision(_run())
        assert "[V10 MARKET UNDERSTANDING]" in report

    def test_has_opportunity(self):
        report = format_v10_decision(_run())
        assert "[V10 OPPORTUNITY]" in report

    def test_has_strategy(self):
        report = format_v10_decision(_run())
        assert "[V10 STRATEGY]" in report

    def test_has_final_action(self):
        report = format_v10_decision(_run())
        assert "[FINAL ACTION]" in report


class TestRejectionStageAccuracy:
    """First failure stage must own the rejection — no downstream overwrite."""

    def test_opportunity_invalid_reports_opportunity(self):
        """When opportunity is INVALID, rejection_stage must be 'opportunity'."""
        result = _run()
        if result.opportunity.opportunity_state == "INVALID":
            assert result.rejection_stage == "opportunity"
            if result.decision_context:
                assert result.decision_context.terminal_stage == "opportunity"

    def test_first_failure_wins_in_context(self):
        """DecisionContext preserves the FIRST failed stage."""
        ctx = V10DecisionContext.empty("TEST", 1000.0)
        ctx = ctx.with_market_state(V10MarketState())
        # Opportunity INVALID
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="INVALID"))
        assert ctx.terminal_stage == "opportunity"
        # Strategy also NONE — but shouldn't overwrite
        ctx = ctx.with_strategy(StrategyDecision(strategy_family=StrategyFamily.NONE.value))
        assert ctx.terminal_stage == "opportunity"  # First failure preserved
        # Entry also INVALID — still shouldn't overwrite
        ctx = ctx.with_entry(EntryDecision(entry_status=EntryStatus.INVALID.value))
        assert ctx.terminal_stage == "opportunity"
        # Risk rejected — still shouldn't overwrite
        ctx = ctx.with_risk(RiskDecision(approved=False))
        assert ctx.terminal_stage == "opportunity"
        # Execution rejected — still shouldn't overwrite
        ctx = ctx.with_execution(ExecutionDecision(approved=False))
        assert ctx.terminal_stage == "opportunity"

    def test_strategy_failure_when_opportunity_valid(self):
        ctx = V10DecisionContext.empty("TEST", 1000.0)
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID"))
        ctx = ctx.with_strategy(StrategyDecision(strategy_family=StrategyFamily.NONE.value))
        assert ctx.terminal_stage == "strategy"

    def test_risk_failure_when_earlier_stages_pass(self):
        ctx = V10DecisionContext.empty("TEST", 1000.0)
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID"))
        ctx = ctx.with_strategy(StrategyDecision(strategy_family="MEAN_REVERSION"))
        ctx = ctx.with_entry(EntryDecision(entry_status="READY"))
        ctx = ctx.with_risk(RiskDecision(approved=False, rejection_reason="Daily loss"))
        assert ctx.terminal_stage == "risk"

    def test_execution_failure_when_all_earlier_pass(self):
        ctx = V10DecisionContext.empty("TEST", 1000.0)
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID"))
        ctx = ctx.with_strategy(StrategyDecision(strategy_family="MEAN_REVERSION"))
        ctx = ctx.with_entry(EntryDecision(entry_status="READY"))
        ctx = ctx.with_risk(RiskDecision(approved=True))
        ctx = ctx.with_execution(ExecutionDecision(approved=False, rejection_reason="Spread"))
        assert ctx.terminal_stage == "execution"


class TestReportMatchesPersistence:
    """Terminal report and persisted record must show same rejection."""

    def test_report_shows_correct_stopped_at(self):
        result = _run()
        report = format_v10_decision(result)
        if not result.approved:
            stage = result.rejection_stage
            assert f"Stopped at: {stage}" in report

    def test_persistence_matches_report(self):
        from core.v10.persistence_adapter import build_v10_decision_record
        result = _run()
        record = build_v10_decision_record(result)
        # Both should show same rejection stage
        assert record["rejection_stage"] == result.rejection_stage or (
            record["rejection_stage"] is None and result.rejection_stage == ""
        )
