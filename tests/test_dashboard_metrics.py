"""
Tests for F4: Performance Metrics Dashboard Emission.

Covers:
- Correct win rate calculation
- Correct P&L aggregation
- Restart consistency (journal-based recompute)
- Empty journal handling
- R-multiple calculation
- Daily filtering correctness
- Dashboard payload includes performance block
- Daily summary emission
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dashboard_metrics import (
    PerformanceDashboard,
    compute_daily_performance,
    build_performance_payload,
    emit_daily_performance_summary,
    emit_dashboard_performance,
    reset_daily_summary_flag,
    _compute_avg_r_multiple,
)


# --- HELPERS ------------------------------------------------------------------

@dataclass
class _FakeTrade:
    """Minimal trade record for testing."""
    trade_id: str = "t1"
    symbol: str = "EURUSD"
    net_pnl: float = 0.0
    entry_price: float = 1.1
    initial_sl: float = 1.09
    initial_volume: float = 0.01
    exit_time: float = 0.0


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config():
    """Set known config defaults."""
    import core.dashboard_metrics as mod
    mod._daily_summary_emitted = False
    with patch("core.dashboard_metrics._include_pnl", return_value=True), \
         patch("core.dashboard_metrics._emit_daily_summary", return_value=True):
        yield


# --- TEST: WIN RATE CALCULATION ------------------------------------------------

class TestWinRate:
    def test_correct_win_rate(self, default_config):
        """Win rate computed from journal summary."""
        mock_summary = {
            "trades": 10, "wins": 6, "losses": 4,
            "net_pnl": 50.0, "avg_pnl": 5.0, "win_rate": 0.6,
        }
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.5):
            perf = compute_daily_performance()

        assert perf.win_rate == 0.6
        assert perf.wins == 6
        assert perf.losses == 4

    def test_zero_trades(self, default_config):
        """No trades ? 0% win rate."""
        mock_summary = {
            "trades": 0, "wins": 0, "losses": 0,
            "net_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0,
        }
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=None):
            perf = compute_daily_performance()

        assert perf.trades_today == 0
        assert perf.win_rate == 0.0

    def test_all_wins(self, default_config):
        """100% win rate."""
        mock_summary = {
            "trades": 5, "wins": 5, "losses": 0,
            "net_pnl": 100.0, "avg_pnl": 20.0, "win_rate": 1.0,
        }
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=1.2):
            perf = compute_daily_performance()

        assert perf.win_rate == 1.0


# --- TEST: P&L AGGREGATION ----------------------------------------------------

class TestPnLAggregation:
    def test_correct_pnl(self, default_config):
        """Net P&L from journal summary."""
        mock_summary = {
            "trades": 8, "wins": 5, "losses": 3,
            "net_pnl": 42.5, "avg_pnl": 5.31, "win_rate": 0.625,
        }
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.3):
            perf = compute_daily_performance()

        assert perf.net_pnl == 42.5
        assert perf.avg_pnl == 5.31

    def test_negative_pnl(self, default_config):
        """Negative day reflected correctly."""
        mock_summary = {
            "trades": 4, "wins": 1, "losses": 3,
            "net_pnl": -25.0, "avg_pnl": -6.25, "win_rate": 0.25,
        }
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=-0.5):
            perf = compute_daily_performance()

        assert perf.net_pnl == -25.0
        assert perf.avg_pnl == -6.25


# --- TEST: R-MULTIPLE ---------------------------------------------------------

class TestRMultiple:
    def test_r_multiple_calculation(self, default_config):
        """R-multiple computed from risk distance × volume."""
        trades = [
            _FakeTrade(net_pnl=10.0, entry_price=1.10, initial_sl=1.09, initial_volume=0.1),
            # Risk = |1.10 - 1.09| * 0.1 = 0.001, R = 10/0.001 = 10000 (price terms)
        ]
        with patch("core.dashboard_metrics.get_trades_by_date", return_value=trades):
            r = _compute_avg_r_multiple()

        assert r is not None
        # R = net_pnl / (risk_distance * volume) = 10 / (0.01 * 0.1) = 10000
        assert r == pytest.approx(10000.0, rel=0.01)

    def test_no_trades_returns_none(self, default_config):
        """No trades ? None."""
        with patch("core.dashboard_metrics.get_trades_by_date", return_value=[]):
            r = _compute_avg_r_multiple()
        assert r is None

    def test_zero_risk_skipped(self, default_config):
        """Trade with zero SL distance skipped in R calculation."""
        trades = [
            _FakeTrade(net_pnl=10.0, entry_price=1.10, initial_sl=1.10, initial_volume=0.1),
        ]
        with patch("core.dashboard_metrics.get_trades_by_date", return_value=trades):
            r = _compute_avg_r_multiple()
        assert r is None  # Skipped because risk_distance = 0


# --- TEST: RESTART CONSISTENCY -------------------------------------------------

class TestRestartConsistency:
    def test_metrics_from_journal_not_memory(self, default_config):
        """Metrics always computed from journal — restart safe."""
        # First computation
        mock_summary = {"trades": 5, "wins": 3, "losses": 2,
                        "net_pnl": 30.0, "avg_pnl": 6.0, "win_rate": 0.6}
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.4):
            perf1 = compute_daily_performance()

        # "Restart" — compute again, same journal, same result
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.4):
            perf2 = compute_daily_performance()

        assert perf1.trades_today == perf2.trades_today
        assert perf1.net_pnl == perf2.net_pnl


# --- TEST: DASHBOARD PAYLOAD --------------------------------------------------

class TestDashboardPayload:
    def test_payload_includes_performance(self, default_config):
        """build_performance_payload returns performance block."""
        mock_summary = {"trades": 3, "wins": 2, "losses": 1,
                        "net_pnl": 15.0, "avg_pnl": 5.0, "win_rate": 0.67}
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.3):
            payload = build_performance_payload()

        assert "performance" in payload
        perf = payload["performance"]
        assert perf["trades_today"] == 3
        assert perf["wins"] == 2
        assert perf["net_pnl"] == 15.0
        assert perf["win_rate"] == 0.67

    def test_disabled_returns_empty(self, default_config):
        """When disabled, returns empty dict."""
        with patch("core.dashboard_metrics._include_pnl", return_value=False):
            payload = build_performance_payload()
        assert payload == {}


# --- TEST: DAILY SUMMARY EMISSION ---------------------------------------------

class TestDailySummary:
    def test_emits_once_per_day(self, default_config):
        """Daily summary only emits once."""
        mock_summary = {"trades": 5, "wins": 3, "losses": 2,
                        "net_pnl": 20.0, "avg_pnl": 4.0, "win_rate": 0.6}
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.2):
            result1 = emit_daily_performance_summary()
            result2 = emit_daily_performance_summary()

        assert result1 is not None
        assert result2 is None  # Already emitted

    def test_reset_allows_re_emission(self, default_config):
        """After reset, summary can emit again."""
        mock_summary = {"trades": 3, "wins": 2, "losses": 1,
                        "net_pnl": 10.0, "avg_pnl": 3.33, "win_rate": 0.67}
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=0.1):
            emit_daily_performance_summary()
            reset_daily_summary_flag()
            result = emit_daily_performance_summary()

        assert result is not None

    def test_no_trades_no_emission(self, default_config):
        """No trades today ? no summary emitted."""
        mock_summary = {"trades": 0, "wins": 0, "losses": 0,
                        "net_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}
        with patch("core.dashboard_metrics.get_daily_summary", return_value=mock_summary), \
             patch("core.dashboard_metrics._compute_avg_r_multiple", return_value=None):
            result = emit_daily_performance_summary()
        assert result is None
