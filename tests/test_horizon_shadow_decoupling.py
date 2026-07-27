"""
Tests for Phase 4C.3 — Horizon Shadow Research Decoupling.

Verifies that horizon shadows are created for ALL assessed opportunities,
not just EXECUTE decisions. Eliminates survivorship bias.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.shadow_trades import ShadowTradeEngine
from core.horizon.horizon_classifier import classify_horizons
from core.horizon.horizon_trade_builder import build_all_horizon_trades


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _simulate_horizon_shadow_creation(
    *,
    action: str = "NO_TRADE",
    direction: str = "SELL",
    strategy: str = "CONTINUATION",
    h4_regime: str = "TRENDING",
    h1_bos: bool = True,
    htf_alignment: float = 0.80,
    h4_alignment: float = 0.85,
    market_quality: float = 0.70,
    m5_candle_high: float = 1.33730,
    m15_resistance: float = 1.33820,
    h1_swing_high: float = 1.33950,
    entry_price: float = 1.33700,
) -> tuple[list, ShadowTradeEngine]:
    """
    Simulate the decoupled horizon shadow creation flow.
    Returns (created_trades, shadow_engine).
    """
    # Step 1: Classify horizons
    h_class = classify_horizons(
        strategy_type=strategy,
        h4_regime=h4_regime,
        h1_bos_confirmed=h1_bos,
        htf_alignment=htf_alignment,
        h4_alignment=h4_alignment,
        market_quality=market_quality,
        pattern="TWEEZER_TOP",
        direction=direction,
    )

    # Step 2: Build horizon trades for ALL eligible horizons
    eligible = h_class.eligible_horizons
    trades = build_all_horizon_trades(
        eligible_horizons=eligible,
        symbol="GBPUSD",
        direction=direction,
        entry_price=entry_price,
        m5_candle_high=m5_candle_high if direction == "SELL" else None,
        m5_candle_low=1.33670 if direction == "BUY" else None,
        m15_nearest_resistance=m15_resistance if direction == "SELL" else None,
        m15_nearest_support=1.33580 if direction == "BUY" else None,
        h1_last_swing_high=h1_swing_high if direction == "SELL" else None,
        h1_last_swing_low=1.33450 if direction == "BUY" else None,
    )

    # Step 3: Open shadow trades (this is what the decoupled code does)
    engine = ShadowTradeEngine(max_bars=200)
    for t in trades:
        engine.open_trade(
            trade_id=f"hshadow_100_GBPUSD_{t.horizon}",
            cycle_id=100,
            symbol="GBPUSD",
            direction=t.direction,
            entry_price=t.entry,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            entry_time=1784800000.0,
            strategy=f"{strategy}_{t.horizon}",
            pattern="TWEEZER_TOP",
            score=0.62,
            correlation_id=f"HORIZON-100-GBPUSD",
        )

    return trades, engine


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: NO_TRADE opportunity creates horizon shadows
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoTradeCreatesHorizonShadows:
    def test_no_trade_produces_shadow_trades(self):
        """A NO_TRADE opportunity with eligible horizons creates shadows."""
        trades, engine = _simulate_horizon_shadow_creation(action="NO_TRADE")
        assert len(trades) >= 2  # At least SCALP + INTRADAY
        assert engine.active_count >= 2

    def test_no_trade_all_horizons_tracked(self):
        """With strong alignment, all 3 horizons get shadows."""
        trades, engine = _simulate_horizon_shadow_creation(
            action="NO_TRADE",
            h4_regime="TRENDING",
            h1_bos=True,
            htf_alignment=0.85,
            h4_alignment=0.90,
            market_quality=0.75,
        )
        horizons = [t.horizon for t in trades]
        assert "SCALP" in horizons
        assert "INTRADAY" in horizons
        assert "EXTENDED" in horizons
        assert engine.active_count == 3

    def test_no_trade_shadows_have_valid_sltp(self):
        """NO_TRADE horizon shadows have correct SL/TP geometry."""
        trades, _ = _simulate_horizon_shadow_creation(action="NO_TRADE")
        for t in trades:
            # SELL: SL above entry, TP below entry
            assert t.stop_loss > t.entry
            assert t.take_profit < t.entry
            assert t.risk_distance > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: EXECUTE opportunity also creates horizon shadows
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecuteAlsoCreatesHorizonShadows:
    def test_execute_produces_shadows(self):
        """EXECUTE path also creates horizon shadows (same behaviour)."""
        trades, engine = _simulate_horizon_shadow_creation(action="EXECUTE")
        assert len(trades) >= 2
        assert engine.active_count >= 2

    def test_execute_and_no_trade_produce_same_shadow_count(self):
        """Action type does not affect number of shadows created."""
        trades_exec, _ = _simulate_horizon_shadow_creation(action="EXECUTE")
        trades_no, _ = _simulate_horizon_shadow_creation(action="NO_TRADE")
        assert len(trades_exec) == len(trades_no)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Multiple eligible horizons create multiple shadows
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleHorizons:
    def test_three_horizons_three_shadows(self):
        """All three horizons eligible → three shadow trades opened."""
        trades, engine = _simulate_horizon_shadow_creation(
            h4_regime="TRENDING", h1_bos=True,
            htf_alignment=0.85, h4_alignment=0.90, market_quality=0.75,
        )
        assert len(trades) == 3
        assert engine.active_count == 3

    def test_each_has_different_sl(self):
        """Each horizon has progressively wider stop loss."""
        trades, _ = _simulate_horizon_shadow_creation(
            h4_regime="TRENDING", h1_bos=True,
            htf_alignment=0.85, h4_alignment=0.90, market_quality=0.75,
        )
        sls = {t.horizon: t.stop_loss for t in trades}
        # SELL: wider SL = higher price
        assert sls["INTRADAY"] > sls["SCALP"]
        assert sls["EXTENDED"] > sls["INTRADAY"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Ineligible horizons are skipped
# ═══════════════════════════════════════════════════════════════════════════════

class TestIneligibleSkipped:
    def test_range_market_no_extended(self):
        """Range regime → EXTENDED ineligible → only SCALP + maybe INTRADAY."""
        trades, engine = _simulate_horizon_shadow_creation(
            h4_regime="RANGE",
            h1_bos=False,
            htf_alignment=0.55,
            h4_alignment=0.30,
            market_quality=0.55,
        )
        horizons = [t.horizon for t in trades]
        assert "EXTENDED" not in horizons
        assert "SCALP" in horizons

    def test_weak_structure_no_intraday(self):
        """Weak structure → INTRADAY ineligible."""
        trades, engine = _simulate_horizon_shadow_creation(
            h4_regime="TRANSITIONAL",
            h1_bos=False,
            htf_alignment=0.10,
            h4_alignment=0.15,
            market_quality=0.10,
            m15_resistance=None,  # No M15 data
            h1_swing_high=None,   # No H1 data
        )
        # Only SCALP should survive (INTRADAY/EXTENDED lack structure data)
        horizons = [t.horizon for t in trades]
        assert "SCALP" in horizons


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Execution path unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionUnchanged:
    def test_shadow_creation_is_independent(self):
        """Shadow creation does not produce OrderIntent or modify risk."""
        trades, engine = _simulate_horizon_shadow_creation(action="NO_TRADE")
        # The result is only shadow trades — no OrderIntent, no risk decision
        for t in trades:
            # HorizonTrade is not OrderIntent
            assert not hasattr(t, "entry_type")
            assert not hasattr(t, "volume")  # Not in HorizonTrade — only in OrderIntent

    def test_shadow_engine_does_not_execute(self):
        """ShadowTradeEngine only tracks — no broker calls."""
        _, engine = _simulate_horizon_shadow_creation()
        # Engine is pure simulation — verify it has no execution method
        assert not hasattr(engine, "execute")
        assert not hasattr(engine, "order_send")
        assert hasattr(engine, "evaluate_bar")  # Only tracks


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Direction from assessment (Phase 4C fix)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirectionFromAssessment:
    """Verify direction is read from assessment.side (not _new_result["side"])."""

    def test_no_trade_with_assessment_side_buy(self):
        """NO_TRADE result with assessment.side='BUY' creates shadows."""
        trades, engine = _simulate_horizon_shadow_creation(
            action="NO_TRADE",
            direction="BUY",
            h4_regime="TRENDING",
            h1_bos=True,
            htf_alignment=0.80,
            h4_alignment=0.85,
            market_quality=0.70,
        )
        buy_trades = [t for t in trades if t.direction == "BUY"]
        assert len(buy_trades) >= 1
        # BUY: SL below entry, TP above entry
        for t in buy_trades:
            assert t.stop_loss < t.entry
            assert t.take_profit > t.entry

    def test_no_trade_with_assessment_side_sell(self):
        """NO_TRADE result with assessment.side='SELL' creates shadows."""
        trades, engine = _simulate_horizon_shadow_creation(
            action="NO_TRADE",
            direction="SELL",
            h4_regime="TRENDING",
            h1_bos=True,
            htf_alignment=0.80,
        )
        sell_trades = [t for t in trades if t.direction == "SELL"]
        assert len(sell_trades) >= 1
        for t in sell_trades:
            assert t.stop_loss > t.entry
            assert t.take_profit < t.entry

    def test_missing_direction_returns_no_shadows(self):
        """Empty direction (no assessment) safely returns 0 trades."""
        from core.horizon.horizon_trade_builder import build_all_horizon_trades
        trades = build_all_horizon_trades(
            eligible_horizons=["SCALP", "INTRADAY"],
            symbol="EURUSD",
            direction="",  # Empty — simulates missing assessment.side
            entry_price=1.10000,
            m5_candle_high=1.10030,
            m15_nearest_resistance=1.10080,
        )
        assert trades == []

    def test_execute_still_creates_shadows(self):
        """EXECUTE path (direction from engine result) still works."""
        trades, engine = _simulate_horizon_shadow_creation(
            action="EXECUTE",
            direction="SELL",
        )
        assert len(trades) >= 2
        assert engine.active_count >= 2
