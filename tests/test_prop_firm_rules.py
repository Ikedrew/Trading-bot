"""
Tests for H3: Prop Firm Rule Violation Detector.

Covers:
- Each rule individually (daily loss, drawdown, hours, trades, lot, weekend)
- Multiple rule conflict (first-hit priority)
- Disabled mode allows all
- Config validation
- Pipeline integration ordering
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.prop_firm_rules import (
    PropFirmRuleSet,
    TradeContext,
    ComplianceResult,
    check_prop_firm_rules,
    check_prop_firm_gate,
    validate_prop_firm_config,
    REJECT_DAILY_LOSS,
    REJECT_DRAWDOWN,
    REJECT_TRADING_HOURS,
    REJECT_MAX_TRADES,
    REJECT_LOT_SIZE,
    REJECT_WEEKEND,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture
def default_rules():
    return PropFirmRuleSet(
        max_daily_loss_percent=5.0,
        max_total_drawdown_percent=10.0,
        max_position_hold_minutes=None,
        blocked_trading_hours=((22, 24),),
        max_trades_per_day=20,
        max_lot_size=1.0,
        allow_weekend_holding=False,
        allow_news_trading=False,
    )


def _ctx(**kwargs) -> TradeContext:
    """Create a TradeContext with defaults overridden by kwargs."""
    defaults = dict(
        symbol="EURUSD", lot_size=0.01, current_equity=100000,
        daily_loss_percent=0.0, total_drawdown_percent=0.0,
        current_hour=10, current_weekday=2, trades_today=0,
    )
    defaults.update(kwargs)
    return TradeContext(**defaults)


# --- TEST: DAILY LOSS RULE -----------------------------------------------------

class TestDailyLossRule:
    def test_blocks_at_limit(self, default_rules):
        """Daily loss at limit ? blocked."""
        ctx = _ctx(daily_loss_percent=5.0)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_DAILY_LOSS

    def test_blocks_above_limit(self, default_rules):
        """Daily loss above limit ? blocked."""
        ctx = _ctx(daily_loss_percent=6.5)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_DAILY_LOSS

    def test_allows_below_limit(self, default_rules):
        """Daily loss below limit ? allowed."""
        ctx = _ctx(daily_loss_percent=3.0)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True


# --- TEST: DRAWDOWN RULE ------------------------------------------------------

class TestDrawdownRule:
    def test_blocks_at_limit(self, default_rules):
        """Total drawdown at limit ? blocked."""
        ctx = _ctx(total_drawdown_percent=10.0)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_DRAWDOWN

    def test_allows_below(self, default_rules):
        """Below drawdown limit ? allowed."""
        ctx = _ctx(total_drawdown_percent=7.0)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True


# --- TEST: TRADING HOURS RULE --------------------------------------------------

class TestTradingHoursRule:
    def test_blocks_in_restricted_hour(self, default_rules):
        """Trading during blocked hour ? blocked."""
        ctx = _ctx(current_hour=22)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_TRADING_HOURS

    def test_allows_outside_restricted(self, default_rules):
        """Trading outside blocked hours ? allowed."""
        ctx = _ctx(current_hour=14)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True

    def test_multiple_blocked_ranges(self):
        """Multiple blocked hour ranges all enforced."""
        rules = PropFirmRuleSet(blocked_trading_hours=((0, 2), (22, 24)))
        ctx1 = _ctx(current_hour=1)
        ctx2 = _ctx(current_hour=23)
        ctx3 = _ctx(current_hour=10)

        assert check_prop_firm_rules(ctx1, rules).allowed is False
        assert check_prop_firm_rules(ctx2, rules).allowed is False
        assert check_prop_firm_rules(ctx3, rules).allowed is True


# --- TEST: MAX TRADES PER DAY -------------------------------------------------

class TestMaxTradesRule:
    def test_blocks_at_limit(self, default_rules):
        """Trades today at limit ? blocked."""
        ctx = _ctx(trades_today=20)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_MAX_TRADES

    def test_allows_below(self, default_rules):
        """Below max trades ? allowed."""
        ctx = _ctx(trades_today=15)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True


# --- TEST: LOT SIZE RULE ------------------------------------------------------

class TestLotSizeRule:
    def test_blocks_oversized(self, default_rules):
        """Lot size above max ? blocked."""
        ctx = _ctx(lot_size=1.5)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_LOT_SIZE

    def test_allows_within_limit(self, default_rules):
        """Lot size at or below max ? allowed."""
        ctx = _ctx(lot_size=1.0)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True

    def test_allows_small_lot(self, default_rules):
        """Small lot ? allowed."""
        ctx = _ctx(lot_size=0.01)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True


# --- TEST: WEEKEND RULE -------------------------------------------------------

class TestWeekendRule:
    def test_blocks_saturday(self, default_rules):
        """Saturday trading blocked when weekend holding not allowed."""
        ctx = _ctx(current_weekday=5)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_WEEKEND

    def test_blocks_sunday(self, default_rules):
        """Sunday trading blocked."""
        ctx = _ctx(current_weekday=6)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is False
        assert result.reason == REJECT_WEEKEND

    def test_allows_weekday(self, default_rules):
        """Weekday ? allowed."""
        ctx = _ctx(current_weekday=3)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.allowed is True

    def test_weekend_allowed_when_configured(self):
        """Weekend trading allowed when allow_weekend_holding=True."""
        rules = PropFirmRuleSet(allow_weekend_holding=True)
        ctx = _ctx(current_weekday=6)
        result = check_prop_firm_rules(ctx, rules)
        assert result.allowed is True


# --- TEST: FIRST-HIT PRIORITY -------------------------------------------------

class TestFirstHitPriority:
    def test_daily_loss_takes_priority(self, default_rules):
        """Multiple violations ? daily loss reported first."""
        ctx = _ctx(daily_loss_percent=6.0, total_drawdown_percent=12.0, trades_today=25)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.reason == REJECT_DAILY_LOSS  # First rule checked

    def test_drawdown_before_trades(self, default_rules):
        """Drawdown checked before trade count."""
        ctx = _ctx(total_drawdown_percent=12.0, trades_today=25)
        result = check_prop_firm_rules(ctx, default_rules)
        assert result.reason == REJECT_DRAWDOWN


# --- TEST: DISABLED MODE ------------------------------------------------------

class TestDisabledMode:
    def test_disabled_allows_all(self):
        """When disabled, all rules pass."""
        with patch("core.prop_firm_rules._is_enabled", return_value=False):
            result = check_prop_firm_gate(
                symbol="EURUSD", lot_size=99.0,
                daily_loss_percent=99.0, trades_today=999,
            )
        assert result.allowed is True


# --- TEST: CONFIG VALIDATION --------------------------------------------------

class TestConfigValidation:
    def test_valid_config(self):
        """Valid default config passes."""
        with patch("core.prop_firm_rules._is_enabled", return_value=True), \
             patch("core.prop_firm_rules._get_rule_set", return_value=PropFirmRuleSet()):
            errors = validate_prop_firm_config()
        assert errors == []

    def test_zero_daily_loss_errors(self):
        """Zero daily loss limit generates error."""
        rules = PropFirmRuleSet(max_daily_loss_percent=0)
        with patch("core.prop_firm_rules._is_enabled", return_value=True), \
             patch("core.prop_firm_rules._get_rule_set", return_value=rules):
            errors = validate_prop_firm_config()
        assert any("daily_loss" in e for e in errors)


# --- TEST: PRODUCTION INTEGRATION ---------------------------------------------

class TestProductionIntegration:
    def test_gate_before_execution(self):
        """Prop firm gate appears in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        pfr_pos = source.find("check_prop_firm_gate")

        assert pfr_pos > 0, "Prop firm gate not found in runtime guard chain"

    def test_gate_after_consistency(self):
        """Prop firm gate after consistency gate."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        cons_pos = source.find("check_consistency_gate")
        pfr_pos = source.find("check_prop_firm_gate")

        assert cons_pos > 0
        assert pfr_pos > 0
        assert cons_pos < pfr_pos
