"""
Break-Even Buffer RR Migration — Verification Tests.

Validates that break_even_buffer_rr is correctly interpreted as a fraction
of initial risk distance (R), not as an absolute price offset.

BUY example:
    entry = 1.1000, initial_sl = 1.0980, risk = 0.0020
    buffer_rr = 0.1 → buffer_price = 0.1 * 0.0020 = 0.0002
    Expected BE stop = 1.1000 + 0.0002 = 1.1002

SELL example:
    entry = 1.1000, initial_sl = 1.1020, risk = 0.0020
    buffer_rr = 0.1 → buffer_price = 0.1 * 0.0020 = 0.0002
    Expected BE stop = 1.1000 - 0.0002 = 1.0998
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.trade_management.sl_tp_rules import maybe_break_even_sl
from strategy.signals import Side


class TestBuyBreakEvenBufferRR:
    def test_buy_basic_be_calculation(self):
        """BUY: buffer_rr=0.1, risk=0.0020 → BE stop at entry + 0.0002."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1025,   # Well above trigger
            ask=1.1026,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(1.1002, abs=1e-8)

    def test_buy_zero_buffer(self):
        """BUY: buffer_rr=0.0 → BE stop at exactly entry."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1025,
            ask=1.1026,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=1.0,
            buffer_rr=0.0,
        )
        assert result is not None
        assert result == pytest.approx(1.1000, abs=1e-8)

    def test_buy_larger_buffer(self):
        """BUY: buffer_rr=0.5 → BE stop at entry + 0.5 * risk."""
        # risk = 1.1000 - 1.0980 = 0.0020
        # buffer_price = 0.5 * 0.0020 = 0.0010
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1025,
            ask=1.1026,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=1.0,
            buffer_rr=0.5,
        )
        assert result is not None
        assert result == pytest.approx(1.1010, abs=1e-8)

    def test_buy_not_triggered_below_threshold(self):
        """BUY: price below trigger threshold → None."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1015,   # favourable = 0.0015, trigger requires 1.0 * 0.002 = 0.002
            ask=1.1016,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is None

    def test_buy_triggered_at_exact_threshold(self):
        """BUY: favourable == trigger*R → triggers."""
        # trigger=1.0, risk=0.002, so need bid >= entry + 0.002 = 1.1020
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1020,
            ask=1.1021,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(1.1002, abs=1e-8)

    def test_buy_does_not_lower_existing_sl(self):
        """BUY: if current_sl is already above BE, return current_sl (max)."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1025,
            ask=1.1026,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.1005,  # Already above BE (1.1002)
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(1.1005, abs=1e-8)


class TestSellBreakEvenBufferRR:
    def test_sell_basic_be_calculation(self):
        """SELL: buffer_rr=0.1, risk=0.0020 → BE stop at entry - 0.0002."""
        result = maybe_break_even_sl(
            Side.SELL,
            bid=1.0974,
            ask=1.0975,   # Well below entry (favourable for SELL)
            entry=1.1000,
            initial_sl=1.1020,
            current_sl=1.1020,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(1.0998, abs=1e-8)

    def test_sell_zero_buffer(self):
        """SELL: buffer_rr=0.0 → BE stop at exactly entry."""
        result = maybe_break_even_sl(
            Side.SELL,
            bid=1.0974,
            ask=1.0975,
            entry=1.1000,
            initial_sl=1.1020,
            current_sl=1.1020,
            trigger_rr=1.0,
            buffer_rr=0.0,
        )
        assert result is not None
        assert result == pytest.approx(1.1000, abs=1e-8)

    def test_sell_larger_buffer(self):
        """SELL: buffer_rr=0.5 → BE stop at entry - 0.5 * risk."""
        # risk = 1.1020 - 1.1000 = 0.0020
        # buffer_price = 0.5 * 0.0020 = 0.0010
        result = maybe_break_even_sl(
            Side.SELL,
            bid=1.0974,
            ask=1.0975,
            entry=1.1000,
            initial_sl=1.1020,
            current_sl=1.1020,
            trigger_rr=1.0,
            buffer_rr=0.5,
        )
        assert result is not None
        assert result == pytest.approx(1.0990, abs=1e-8)

    def test_sell_not_triggered_below_threshold(self):
        """SELL: price not far enough below entry → None."""
        result = maybe_break_even_sl(
            Side.SELL,
            bid=1.0984,
            ask=1.0985,   # favourable = entry - ask = 0.0015, need 0.002
            entry=1.1000,
            initial_sl=1.1020,
            current_sl=1.1020,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is None

    def test_sell_does_not_raise_existing_sl(self):
        """SELL: if current_sl already below BE, keep it (min)."""
        result = maybe_break_even_sl(
            Side.SELL,
            bid=1.0974,
            ask=1.0975,
            entry=1.1000,
            initial_sl=1.1020,
            current_sl=1.0995,  # Already below BE (1.0998)
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(1.0995, abs=1e-8)


class TestEdgeCases:
    def test_trigger_rr_zero_disables(self):
        """trigger_rr=0 disables break-even entirely."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.2000,
            ask=1.2001,
            entry=1.1000,
            initial_sl=1.0980,
            current_sl=1.0980,
            trigger_rr=0.0,
            buffer_rr=0.1,
        )
        assert result is None

    def test_zero_risk_returns_none(self):
        """If entry == initial_sl (zero risk), return None safely."""
        result = maybe_break_even_sl(
            Side.BUY,
            bid=1.1025,
            ask=1.1026,
            entry=1.1000,
            initial_sl=1.1000,  # Zero risk
            current_sl=1.1000,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is None

    def test_jpy_pair_buy(self):
        """JPY pair (2-decimal): entry=150.00, sl=149.80, risk=0.20."""
        # buffer = 0.1 * 0.20 = 0.02
        result = maybe_break_even_sl(
            Side.BUY,
            bid=150.25,
            ask=150.26,
            entry=150.00,
            initial_sl=149.80,
            current_sl=149.80,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(150.02, abs=1e-6)

    def test_jpy_pair_sell(self):
        """JPY pair SELL: entry=150.00, sl=150.20, risk=0.20."""
        # buffer = 0.1 * 0.20 = 0.02
        result = maybe_break_even_sl(
            Side.SELL,
            bid=149.74,
            ask=149.75,
            entry=150.00,
            initial_sl=150.20,
            current_sl=150.20,
            trigger_rr=1.0,
            buffer_rr=0.1,
        )
        assert result is not None
        assert result == pytest.approx(149.98, abs=1e-6)
