"""
Tests for C3: Slippage Monitoring.

Covers:
- Expected vs fill calculation
- Pip conversion accuracy
- Per-symbol stats
- Global rolling mean
- ATR-normalised metric
- Alert threshold breach
- Persistence (JSONL)
- Disabled mode
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.slippage_monitor import (
    SlippageMonitor,
    SlippageRecord,
    SlippageStats,
    record_slippage,
    get_slippage_stats,
    validate_slippage_config,
    _get_point_size,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config(tmp_path):
    """Set known config and redirect journal."""
    journal = tmp_path / "slippage.jsonl"
    with patch("core.slippage_monitor._is_enabled", return_value=True), \
         patch("core.slippage_monitor._get_alert_threshold_pips", return_value=0.5), \
         patch("core.slippage_monitor._get_journal_path", return_value=journal), \
         patch("core.slippage_monitor._get_max_history", return_value=100):
        # Reset singleton
        import core.slippage_monitor as mod
        mod._monitor = None
        yield journal
        mod._monitor = None


# --- TEST: SLIPPAGE CALCULATION ------------------------------------------------

class TestSlippageCalculation:
    def test_positive_slippage(self, default_config):
        """Fill above expected = positive slippage (worse for buyer)."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="EURUSD",
            expected_price=1.08500,
            fill_price=1.08512,
        )
        assert rec.slippage == pytest.approx(0.00012, abs=1e-7)
        assert rec.slippage_pips == pytest.approx(1.2, abs=0.1)

    def test_negative_slippage(self, default_config):
        """Fill below expected = negative slippage (better for buyer)."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="EURUSD",
            expected_price=1.08500,
            fill_price=1.08495,
        )
        assert rec.slippage == pytest.approx(-0.00005, abs=1e-7)
        assert rec.slippage_pips == pytest.approx(-0.5, abs=0.1)

    def test_zero_slippage(self, default_config):
        """Exact fill = zero slippage."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="GBPUSD",
            expected_price=1.30000,
            fill_price=1.30000,
        )
        assert rec.slippage == 0.0
        assert rec.slippage_pips == 0.0


# --- TEST: PIP CONVERSION -----------------------------------------------------

class TestPipConversion:
    def test_eurusd_pip_size(self):
        """EURUSD pip = 0.0001."""
        assert _get_point_size("EURUSD") == 0.0001

    def test_usdjpy_pip_size(self):
        """USDJPY pip = 0.01."""
        assert _get_point_size("USDJPY") == 0.01

    def test_usdjpy_slippage_pips(self, default_config):
        """JPY pair slippage correctly converted to pips."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="USDJPY",
            expected_price=150.000,
            fill_price=150.015,
        )
        # 0.015 / 0.01 = 1.5 pips
        assert rec.slippage_pips == pytest.approx(1.5, abs=0.1)


# --- TEST: PER-SYMBOL STATS ---------------------------------------------------

class TestPerSymbolStats:
    def test_separate_symbol_stats(self, default_config):
        """Each symbol has independent statistics."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.08500, fill_price=1.08510)  # +1.0 pip
        mon.record(symbol="EURUSD", expected_price=1.08500, fill_price=1.08520)  # +2.0 pips
        mon.record(symbol="GBPUSD", expected_price=1.30000, fill_price=1.30005)  # +0.5 pip

        eur_stats = mon.get_stats("EURUSD")
        gbp_stats = mon.get_stats("GBPUSD")

        assert eur_stats.trade_count == 2
        assert eur_stats.mean_slippage_pips == pytest.approx(1.5, abs=0.1)
        assert gbp_stats.trade_count == 1
        assert gbp_stats.mean_slippage_pips == pytest.approx(0.5, abs=0.1)

    def test_global_stats(self, default_config):
        """Global stats aggregate all symbols."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.08500, fill_price=1.08510)  # +1.0
        mon.record(symbol="GBPUSD", expected_price=1.30000, fill_price=1.30020)  # +2.0

        stats = mon.get_stats()  # Global
        assert stats.trade_count == 2
        assert stats.mean_slippage_pips == pytest.approx(1.5, abs=0.1)


# --- TEST: ROLLING MEAN -------------------------------------------------------

class TestRollingMean:
    def test_mean_updates(self, default_config):
        """Mean updates with each new trade."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0001)  # +1 pip
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0003)  # +3 pips

        stats = mon.get_stats("EURUSD")
        assert stats.mean_slippage_pips == pytest.approx(2.0, abs=0.1)  # (1+3)/2

    def test_max_tracked(self, default_config):
        """Max slippage tracked correctly."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0001)
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0005)  # +5 pips

        stats = mon.get_stats("EURUSD")
        assert stats.max_slippage_pips == pytest.approx(5.0, abs=0.1)

    def test_empty_stats(self, default_config):
        """No records ? zero stats."""
        mon = SlippageMonitor()
        stats = mon.get_stats()
        assert stats.trade_count == 0
        assert stats.mean_slippage == 0.0


# --- TEST: ATR-NORMALISED METRIC ----------------------------------------------

class TestATRMetric:
    def test_atr_ratio_calculated(self, default_config):
        """ATR ratio = |slippage| / ATR."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="EURUSD",
            expected_price=1.08500,
            fill_price=1.08510,  # +0.0001 = 1 pip
            atr=0.00100,  # ATR = 10 pips
        )
        # |0.0001| / 0.001 = 0.1
        assert rec.slippage_atr_ratio == pytest.approx(0.1, abs=0.01)

    def test_atr_none_no_ratio(self, default_config):
        """No ATR ? no ratio calculated."""
        mon = SlippageMonitor()
        rec = mon.record(
            symbol="EURUSD",
            expected_price=1.08500,
            fill_price=1.08510,
            atr=None,
        )
        assert rec.slippage_atr_ratio is None

    def test_mean_atr_ratio_in_stats(self, default_config):
        """Stats include mean ATR ratio."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0001, atr=0.001)  # 0.1
        mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.0002, atr=0.001)  # 0.2

        stats = mon.get_stats("EURUSD")
        assert stats.mean_atr_ratio == pytest.approx(0.15, abs=0.01)


# --- TEST: ALERT THRESHOLD ----------------------------------------------------

class TestAlert:
    def test_alert_on_threshold_breach(self, default_config, caplog):
        """Alert emitted when mean slippage exceeds threshold."""
        import logging
        mon = SlippageMonitor()

        with caplog.at_level(logging.WARNING):
            # 3 trades with high slippage (mean > 0.5 pip threshold)
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.00010)  # 1 pip
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.00008)  # 0.8 pip
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.00012)  # 1.2 pip

        assert "SLIPPAGE_ALERT" in caplog.text

    def test_no_alert_below_threshold(self, default_config, caplog):
        """No alert when below threshold."""
        import logging
        mon = SlippageMonitor()

        with caplog.at_level(logging.WARNING):
            # Small slippage
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.000002)
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.000003)
            mon.record(symbol="EURUSD", expected_price=1.0, fill_price=1.000001)

        assert "SLIPPAGE_ALERT" not in caplog.text


# --- TEST: PERSISTENCE --------------------------------------------------------

class TestPersistence:
    def test_jsonl_written(self, default_config):
        """Each record appended to JSONL file."""
        mon = SlippageMonitor()
        mon.record(symbol="EURUSD", expected_price=1.08500, fill_price=1.08510)
        mon.record(symbol="GBPUSD", expected_price=1.30000, fill_price=1.30015)

        lines = default_config.read_text().strip().split("\n")
        assert len(lines) == 2

        entry = json.loads(lines[0])
        assert entry["symbol"] == "EURUSD"
        assert entry["slippage_pips"] == pytest.approx(1.0, abs=0.1)


# --- TEST: DISABLED MODE ------------------------------------------------------

class TestDisabledMode:
    def test_disabled_returns_zero(self, default_config):
        """When disabled, returns record with zero slippage."""
        with patch("core.slippage_monitor._is_enabled", return_value=False):
            mon = SlippageMonitor()
            rec = mon.record(
                symbol="EURUSD",
                expected_price=1.08500,
                fill_price=1.08600,
            )
        assert rec.slippage == 0.0


# --- TEST: CONFIG VALIDATION --------------------------------------------------

class TestConfigValidation:
    def test_valid_config(self, default_config):
        """Valid config passes."""
        errors = validate_slippage_config()
        assert errors == []

    def test_zero_threshold_errors(self, default_config):
        """Zero threshold generates error."""
        with patch("core.slippage_monitor._get_alert_threshold_pips", return_value=0):
            errors = validate_slippage_config()
        assert any("THRESHOLD" in e for e in errors)
