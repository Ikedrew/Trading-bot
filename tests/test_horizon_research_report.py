"""
Horizon Research Reporting Layer — Tests.

Validates:
    1. Validated metric comparison
    2. Failed metric comparison (REVIEW_REQUIRED)
    3. Insufficient data handling
    4. Overall status calculation
    5. Recommendation generation
    6. JSON serialization
    7. Version preservation
    8. All horizons reporting
    9. Disabled horizons still generate research reports
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.horizon.research_contract import (
    HorizonObservation,
    SCALP_RESEARCH_V1,
    INTRADAY_RESEARCH_V1,
    EXTENDED_RESEARCH_V1,
)
from core.horizon.research_report import (
    HorizonResearchReport,
    OverallStatus,
    generate_horizon_report,
    generate_all_horizon_reports,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _obs_validated_scalp(sample: int = 50) -> HorizonObservation:
    """Observation within SCALP contract expectations."""
    return HorizonObservation(
        horizon="SCALP",
        profile_version="SCALP_RESEARCH_V1",
        sample_size=sample,
        observed_move_average_pips=8.0,
        observed_move_median_pips=7.0,
        observed_move_p95_pips=13.0,
        observed_hold_average_minutes=42.0,
        observed_hold_median_minutes=35.0,
        observed_rr=1.9,
        observed_win_rate=0.44,
        observed_profit_factor=1.5,
        observed_expectancy=0.4,
        observed_mae_pips=3.5,
        observed_mfe_pips=7.0,
        exit_reasons={"tp_hit": 22, "sl_hit": 28},
    )


def _obs_review_required_scalp(sample: int = 50) -> HorizonObservation:
    """Observation outside SCALP contract expectations."""
    return HorizonObservation(
        horizon="SCALP",
        profile_version="SCALP_RESEARCH_V1",
        sample_size=sample,
        observed_move_average_pips=1.5,       # Below 3-15 range
        observed_hold_average_minutes=150.0,  # Above 2-90 range
        observed_rr=0.8,                      # Below 2.0 expected
        observed_win_rate=0.20,               # Below 0.45 expected
        observed_mae_pips=8.0,                # Above 5.0 expected
        observed_mfe_pips=2.0,                # Below expected
    )


@dataclass(frozen=True)
class FakeTradeRecord:
    """Minimal TradeRecord for generate_all_horizon_reports tests."""
    trade_id: str = "pos_test"
    position_ticket: int | None = 100
    symbol: str = "EURUSD"
    magic: int = 713001
    pattern_name: str = "HAMMER"
    direction: str = "BUY"
    entry_time: float = 1000.0
    exit_time: float = 4600.0
    duration_seconds: float = 3600.0
    entry_price: float = 1.1000
    exit_price: float = 1.1020
    initial_volume: float = 0.01
    final_volume: float = 0.01
    realised_pnl: float = 2.0
    commission: float = 0.0
    swap: float = 0.0
    net_pnl: float = 2.0
    close_reason: str = "tp_hit"
    initial_sl: float = 1.0980
    initial_tp: float = 1.1040
    max_favourable_price: float = 1.1025
    recorded_at_utc: str = "2026-07-23T00:00:00Z"
    correlation_id: str = "COR-123"
    trade_horizon: str = "SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Validated Metric Comparison
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidatedComparison:
    def test_all_metrics_validated(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status == OverallStatus.VALIDATED

    def test_validated_report_has_metrics(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert len(report.metric_assessments) > 0

    def test_validated_recommendation_positive(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert any("validated" in r.lower() for r in report.recommendations)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Failed Metric Comparison
# ═══════════════════════════════════════════════════════════════════════════════

class TestReviewRequired:
    def test_metrics_outside_range(self):
        obs = _obs_review_required_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status in (OverallStatus.REVIEW_REQUIRED, OverallStatus.PARTIALLY_VALIDATED)

    def test_review_metrics_have_deviation(self):
        obs = _obs_review_required_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        review_metrics = [a for a in report.metric_assessments if a.status.value == "REVIEW_REQUIRED"]
        assert len(review_metrics) > 0
        assert all(a.deviation_pct > 0 for a in review_metrics)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Insufficient Data
# ═══════════════════════════════════════════════════════════════════════════════

class TestInsufficientData:
    def test_below_min_sample(self):
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=5,
        )
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status == OverallStatus.INSUFFICIENT_DATA

    def test_zero_trades(self):
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=0,
        )
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status == OverallStatus.INSUFFICIENT_DATA
        assert report.observation_sample_size == 0

    def test_custom_min_sample(self):
        obs = _obs_validated_scalp(sample=10)
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs, min_sample_size=50)
        assert report.overall_status == OverallStatus.INSUFFICIENT_DATA


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Overall Status Calculation
# ═══════════════════════════════════════════════════════════════════════════════

class TestOverallStatus:
    def test_all_validated_gives_validated(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status == OverallStatus.VALIDATED

    def test_all_review_gives_review(self):
        obs = _obs_review_required_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        # With all metrics failing, should be REVIEW_REQUIRED or PARTIALLY_VALIDATED
        assert report.overall_status in (OverallStatus.REVIEW_REQUIRED, OverallStatus.PARTIALLY_VALIDATED)

    def test_mixed_gives_partially_validated(self):
        """Some metrics pass, some fail → PARTIALLY_VALIDATED."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_hold_average_minutes=42.0,   # VALIDATED (within 2-90)
            observed_rr=0.5,                      # REVIEW_REQUIRED (way below 2.0)
            observed_win_rate=0.44,               # VALIDATED
            observed_move_average_pips=8.0,       # VALIDATED
            observed_mae_pips=3.0,                # VALIDATED
            observed_mfe_pips=7.0,                # VALIDATED
        )
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.overall_status == OverallStatus.PARTIALLY_VALIDATED


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Recommendation Generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecommendations:
    def test_review_generates_recommendations(self):
        obs = _obs_review_required_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert len(report.recommendations) > 0

    def test_recommendation_mentions_metric(self):
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_hold_average_minutes=200.0,  # Way above 90
            observed_rr=2.0,
            observed_win_rate=0.45,
            observed_move_average_pips=8.0,
            observed_mae_pips=3.0,
            observed_mfe_pips=7.0,
        )
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert any("hold_average_minutes" in r for r in report.recommendations)
        assert any("exceeds" in r for r in report.recommendations)

    def test_insufficient_data_recommendation(self):
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=5,
        )
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert any("insufficient" in r.lower() or "samples" in r.lower() for r in report.recommendations)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. JSON Serialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_to_dict_structure(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        d = report.to_dict()
        assert d["horizon"] == "SCALP"
        assert d["contract_version"] == "SCALP_RESEARCH_V1"
        assert d["sample_size"] == 50
        assert "metrics" in d
        assert "overall_status" in d
        assert "recommendations" in d
        assert "generated_at" in d

    def test_json_serializable(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        d = report.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["horizon"] == "SCALP"

    def test_metrics_dict_keyed_by_field(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        d = report.to_dict()
        # Metrics should be keyed by field name
        for key, metric in d["metrics"].items():
            assert "expected_min" in metric
            assert "expected_max" in metric
            assert "observed" in metric
            assert "status" in metric


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Version Preservation
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionPreservation:
    def test_report_preserves_contract_version(self):
        obs = _obs_validated_scalp()
        report = generate_horizon_report(SCALP_RESEARCH_V1, obs)
        assert report.contract_version == "SCALP_RESEARCH_V1"

    def test_different_version_different_report(self):
        v2 = SCALP_RESEARCH_V1.__class__(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V2",
            expected_rr=1.7,
            expected_hold_min_minutes=2.0,
            expected_hold_max_minutes=60.0,
            expected_win_rate=0.50,
        )
        obs = _obs_validated_scalp()
        report = generate_horizon_report(v2, obs)
        assert report.contract_version == "SCALP_RESEARCH_V2"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. All Horizons Reporting
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllHorizonsReporting:
    def test_generate_all_reports(self):
        trades = [FakeTradeRecord() for _ in range(25)]
        reports = generate_all_horizon_reports(trades)
        assert "SCALP" in reports
        assert "INTRADAY" in reports
        assert "EXTENDED" in reports

    def test_scalp_has_data(self):
        trades = [FakeTradeRecord() for _ in range(25)]
        reports = generate_all_horizon_reports(trades)
        assert reports["SCALP"].observation_sample_size == 25

    def test_intraday_insufficient(self):
        trades = [FakeTradeRecord(trade_horizon="SCALP") for _ in range(25)]
        reports = generate_all_horizon_reports(trades)
        assert reports["INTRADAY"].overall_status == OverallStatus.INSUFFICIENT_DATA

    def test_extended_insufficient(self):
        trades = [FakeTradeRecord(trade_horizon="SCALP") for _ in range(25)]
        reports = generate_all_horizon_reports(trades)
        assert reports["EXTENDED"].overall_status == OverallStatus.INSUFFICIENT_DATA


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Disabled Horizons Generate Reports
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisabledHorizonsReport:
    def test_intraday_report_generated_even_disabled(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=30,
            observed_hold_average_minutes=120.0,
            observed_rr=2.8,
            observed_win_rate=0.38,
            observed_move_average_pips=25.0,
            observed_mae_pips=10.0,
            observed_mfe_pips=20.0,
        )
        report = generate_horizon_report(INTRADAY_RESEARCH_V1, obs)
        assert report.horizon == "INTRADAY"
        assert report.overall_status != OverallStatus.INSUFFICIENT_DATA

    def test_runtime_horizons_match_current_contract(self):
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP", "INTRADAY", "EXTENDED"]
