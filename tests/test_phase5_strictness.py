"""Tests for Phase 5 StrictnessBase diagnostic engine."""

from __future__ import annotations

from phase5.event_reconstructor import TradeEvent, VoterSnapshot, WeightIntelligenceSnapshot
from phase5.strictness_base import compute_strictness_report, StrictnessReport


def _event(
    production="NO_TRADE", shadow="NO_TRADE", outcome="loss", pnl=0.0,
    agreement=0.5, conflict_severity="none", system_state="coherent",
    bias=0.0, structure=0.0, session=0.0, spread=0.0, volatility=0.0,
    ssi=0.5, conflict_types=None, dominant=None, conflicting=None,
    trade_id="t1", timestamp=1000.0, symbol="EURUSD",
) -> TradeEvent:
    return TradeEvent(
        trade_id=trade_id, timestamp=timestamp, symbol=symbol,
        production_decision=production, shadow_decision=shadow,
        pnl=pnl, outcome=outcome, ssi=ssi,
        agreement_score=agreement,
        conflict_types=conflict_types or [],
        conflict_severity=conflict_severity,
        system_state=system_state,
        voter_snapshot=VoterSnapshot(bias=bias, structure=structure, session=session, spread=spread, volatility=volatility),
        dominant_voters=dominant or [],
        conflicting_voters=conflicting or [],
        weight_intelligence=WeightIntelligenceSnapshot(),
    )


class TestStrictnessReport:
    def test_empty_events(self):
        report = compute_strictness_report([])
        assert report.total_attempts == 0
        assert report.regime == "BALANCED"

    def test_all_trades_executed(self):
        events = [_event(production="BUY", bias=1.0, structure=0.8, agreement=0.9) for _ in range(10)]
        report = compute_strictness_report(events)
        assert report.block_rate < 0.3
        assert report.regime in ("UNDER_FILTERING", "BALANCED")

    def test_over_filtering_detected(self):
        # All events are NO_TRADE with low voter signals (structural blocks)
        events = [_event(bias=0.0, structure=-0.5, system_state="unstable") for _ in range(20)]
        report = compute_strictness_report(events)
        assert report.block_rate > 0.7
        assert report.regime in ("OVER_FILTERING", "UNSTABLE")
        assert report.total_blocks > 15

    def test_mixed_produces_balanced(self):
        traded = [_event(production="BUY", bias=1.0, structure=0.8, agreement=0.8) for _ in range(5)]
        blocked = [_event(bias=0.0, structure=-0.3) for _ in range(5)]
        report = compute_strictness_report(traded + blocked)
        assert 0.3 <= report.block_rate <= 0.7

    def test_funnel_efficiency_computed(self):
        events = [_event(bias=0.5, structure=0.3, agreement=0.6) for _ in range(10)]
        report = compute_strictness_report(events)
        assert isinstance(report.funnel_efficiency, dict)
        assert len(report.funnel_efficiency) > 0

    def test_suggestion_tags_present(self):
        events = [_event(bias=0.0, structure=-0.5, system_state="unstable") for _ in range(20)]
        report = compute_strictness_report(events)
        assert isinstance(report.suggestion_tags, list)

    def test_consistency_score_bounded(self):
        events = [_event() for _ in range(10)]
        report = compute_strictness_report(events)
        assert 0.0 <= report.consistency_score <= 1.0

    def test_entropy_bounded(self):
        events = [_event() for _ in range(10)]
        report = compute_strictness_report(events)
        assert report.entropy_of_blocks >= 0.0

    def test_symbol_filter(self):
        eu = [_event(symbol="EURUSD") for _ in range(5)]
        gb = [_event(symbol="GBPUSD") for _ in range(5)]
        report = compute_strictness_report(eu + gb, symbol="EURUSD")
        assert report.total_attempts == 5
        assert report.symbol == "EURUSD"

    def test_report_is_frozen(self):
        import pytest
        events = [_event() for _ in range(5)]
        report = compute_strictness_report(events)
        with pytest.raises(Exception):
            report.block_rate = 0.99  # type: ignore
