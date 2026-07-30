"""V10 Runtime Smoke Test — One complete cycle end-to-end.

Phase J.3 acceptance: Verifies the full pipeline chain produces
terminal report + decision record + event records from a single
MarketUnderstanding input with realistic broker fixtures.
"""

import pytest
from core.v10.pipeline import V10Pipeline, PipelineResult
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.decision_report import format_v10_decision
from core.v10.persistence_adapter import build_v10_decision_record
from core.v3_shadow.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.v3_shadow.context_models import (
    V3MarketContext, HTFStructureContext, LocationContext, BehaviourContext,
)


def _full_inputs():
    """Realistic inputs simulating a live MT5 evaluation."""
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
        h4=H4Understanding(trend="NEUTRAL", trend_strength=0.15, market_phase="CONSOLIDATION"),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
            active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
            active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
            session_high=1.0930, session_low=1.0840,
        ),
        m15=M15Understanding(pullback_active=True, pullback_depth_atr=1.3, range_position=0.75),
        m5=M5Understanding(
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9, at_institutional_zone=True,
            zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012,
        ),
    )
    ctx = V3MarketContext(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(
            macro_bias="NEUTRAL", structure_alignment=0.30,
            bos_active=True, bos_direction="BEARISH",
        ),
        location=LocationContext(
            location_type="SUPPLY_OB", inside_institutional_zone=True,
            premium_discount="PREMIUM", range_position=0.75,
            zone_quality=0.85, liquidity_below=True,
        ),
        behaviour=BehaviourContext(
            regime="RANGING", volatility_state="NEUTRAL",
            momentum_direction="NEUTRAL",
        ),
        overall_confidence=0.8,
    )
    account = AccountContext(
        balance=10000.0, equity=10000.0,
        current_open_risk_pct=0.0, open_positions=0,
        daily_loss_pct=0.0, symbols_with_positions=[],
    )
    broker = BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        symbol="EURUSD", spread=0.00012, available_margin=5000.0,
        tick_value=1.0, tick_size=0.00001,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        point=0.00001, digits=5, stops_level=0,
    )
    return mu, ctx, account, broker


class TestV10FullCycleSmoke:
    """One complete V10 cycle — all stages fire, all outputs produced."""

    def test_pipeline_produces_result(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        assert isinstance(result, PipelineResult)

    def test_all_stages_evaluated(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        assert result.market_state.symbol == "EURUSD"
        assert result.opportunity.opportunity_state in ("VALID", "INVALID", "WATCHING")
        assert result.strategy.strategy_family != ""
        assert result.horizon.horizon_type != ""
        assert result.entry.entry_status != ""

    def test_decision_context_exists(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        assert result.decision_context is not None
        assert result.decision_context.final_action in ("EXECUTE", "NO_TRADE")

    def test_terminal_report_has_all_headers(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        report = format_v10_decision(result)
        for header in [
            "[V10 MARKET UNDERSTANDING]", "[V10 OPPORTUNITY]",
            "[V10 STRATEGY]", "[V10 HORIZON]", "[V10 ENTRY]",
            "[V10 RISK]", "[V10 EXECUTION]", "[FINAL ACTION]",
        ]:
            assert header in report, f"Missing header: {header}"

    def test_terminal_report_no_legacy(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        report = format_v10_decision(result)
        for legacy in ["Composite Score", "Neutral Score", "Grade:",
                       "Threshold:", "Dual Scoring", "Execution Policy"]:
            assert legacy not in report, f"Legacy found: {legacy}"

    def test_decision_record_valid(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        record = build_v10_decision_record(result)
        assert record["schema_version"] == "v10_decision_v1"
        assert record["symbol"] == "EURUSD"
        assert record["observation_id"] != ""
        assert record["final_action"] in ("EXECUTE", "NO_TRADE")

    def test_events_collected(self):
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        assert result.events is not None

    def test_all_outputs_agree(self):
        """Terminal, context, and record all show same action."""
        mu, ctx, account, broker = _full_inputs()
        result = V10Pipeline().process(mu, ctx, account, broker)
        report = format_v10_decision(result)
        record = build_v10_decision_record(result)
        ctx_result = result.decision_context

        action = ctx_result.final_action
        assert record["final_action"] == action
        assert action in report
