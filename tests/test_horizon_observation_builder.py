"""
Horizon Observation Builder — Tests.

Validates:
    1. Completed SCALP trade creates observation
    2. Zero trades produce sample_size=0 (not failure)
    3. Hold duration calculation (seconds → minutes)
    4. R-multiple calculation (directional profit / initial risk)
    5. MAE/MFE handling
    6. Contract comparison integration
    7. Inactive horizons remain supported
    8. Execution behaviour unchanged
    9. Multiple trades aggregate correctly
    10. BUY and SELL directions handled
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.horizon.observation_builder import (
    build_horizon_observation,
    build_all_horizon_observations,
    _compute_realised_r,
    _compute_mfe_pips,
    _compute_mae_pips,
    _compute_hold_minutes,
    _compute_realised_move_pips,
    _compute_initial_risk_pips,
)
from core.horizon.research_contract import (
    HorizonObservation,
    ValidationStatus,
    compare_contract_to_observation,
    SCALP_RESEARCH_V1,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS — Minimal TradeRecord-like object
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FakeTradeRecord:
    """Minimal TradeRecord stand-in for testing (matches real field names)."""
    trade_id: str = "pos_test"
    position_ticket: int | None = 100
    symbol: str = "EURUSD"
    magic: int = 713001
    pattern_name: str = "HAMMER"
    direction: str = "BUY"
    entry_time: float = 1000.0
    exit_time: float = 4600.0       # 3600s = 60min after entry
    duration_seconds: float = 3600.0
    entry_price: float = 1.1000
    exit_price: float = 1.1020      # +20 pips profit
    initial_volume: float = 0.01
    final_volume: float = 0.01
    realised_pnl: float = 2.0
    commission: float = 0.0
    swap: float = 0.0
    net_pnl: float = 2.0
    close_reason: str = "tp_hit"
    initial_sl: float = 1.0980      # 20 pips risk
    initial_tp: float = 1.1040
    max_favourable_price: float = 1.1025  # 25 pips MFE
    recorded_at_utc: str = "2026-07-23T00:00:00Z"
    correlation_id: str = "COR-123"
    trade_horizon: str = "SCALP"


def _make_trade(**overrides) -> FakeTradeRecord:
    return FakeTradeRecord(**overrides)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Completed SCALP Trade Creates Observation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleTradeObservation:
    def test_single_scalp_trade_produces_observation(self):
        trades = [_make_trade()]
        obs = build_horizon_observation(trades)
        assert obs.horizon == "SCALP"
        assert obs.sample_size == 1
        assert obs.observed_hold_average_minutes == 60.0

    def test_observation_has_profile_version(self):
        trades = [_make_trade()]
        obs = build_horizon_observation(trades)
        assert obs.profile_version == "SCALP_RESEARCH_V1"

    def test_r_multiple_calculated(self):
        # entry=1.1, exit=1.102, sl=1.098 → risk=0.002, profit=0.002 → R=1.0
        trades = [_make_trade(entry_price=1.1, exit_price=1.102, initial_sl=1.098)]
        obs = build_horizon_observation(trades)
        assert obs.observed_rr == pytest.approx(1.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Zero Trades
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroTrades:
    def test_empty_list_returns_zero_sample(self):
        obs = build_horizon_observation([], horizon="SCALP")
        assert obs.sample_size == 0
        assert obs.horizon == "SCALP"

    def test_no_matching_horizon_returns_zero(self):
        trades = [_make_trade(trade_horizon="SCALP")]
        obs = build_horizon_observation(trades, horizon="INTRADAY")
        assert obs.sample_size == 0
        assert obs.horizon == "INTRADAY"

    def test_all_horizons_returns_zero_for_missing(self):
        trades = [_make_trade(trade_horizon="SCALP")]
        all_obs = build_all_horizon_observations(trades)
        assert all_obs["SCALP"].sample_size == 1
        assert all_obs["INTRADAY"].sample_size == 0
        assert all_obs["EXTENDED"].sample_size == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Hold Duration Calculation
# ═══════════════════════════════════════════════════════════════════════════════

class TestHoldDuration:
    def test_duration_seconds_to_minutes(self):
        trade = _make_trade(duration_seconds=2700.0)  # 45 min
        assert _compute_hold_minutes(trade) == 45.0

    def test_observation_hold_average(self):
        trades = [
            _make_trade(duration_seconds=1800.0),  # 30 min
            _make_trade(duration_seconds=3600.0),  # 60 min
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_hold_average_minutes == 45.0

    def test_observation_hold_median(self):
        trades = [
            _make_trade(duration_seconds=600.0),   # 10 min
            _make_trade(duration_seconds=1800.0),  # 30 min
            _make_trade(duration_seconds=5400.0),  # 90 min
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_hold_median_minutes == 30.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. R-Multiple Calculation
# ═══════════════════════════════════════════════════════════════════════════════

class TestRCalculation:
    def test_buy_winner_r(self):
        # BUY: entry=1.1, exit=1.104, sl=1.098 → risk=0.002, profit=0.004 → R=2.0
        trade = _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098)
        assert _compute_realised_r(trade) == pytest.approx(2.0)

    def test_buy_loser_r(self):
        # BUY: entry=1.1, exit=1.098, sl=1.098 → risk=0.002, loss=-0.002 → R=-1.0
        trade = _make_trade(direction="BUY", entry_price=1.1, exit_price=1.098, initial_sl=1.098)
        assert _compute_realised_r(trade) == pytest.approx(-1.0)

    def test_sell_winner_r(self):
        # SELL: entry=1.1, exit=1.096, sl=1.102 → risk=0.002, profit=0.004 → R=2.0
        trade = _make_trade(direction="SELL", entry_price=1.1, exit_price=1.096, initial_sl=1.102)
        assert _compute_realised_r(trade) == pytest.approx(2.0)

    def test_sell_loser_r(self):
        # SELL: entry=1.1, exit=1.102, sl=1.102 → risk=0.002, loss=-0.002 → R=-1.0
        trade = _make_trade(direction="SELL", entry_price=1.1, exit_price=1.102, initial_sl=1.102)
        assert _compute_realised_r(trade) == pytest.approx(-1.0)

    def test_zero_risk_returns_none(self):
        trade = _make_trade(entry_price=1.1, initial_sl=1.1)
        assert _compute_realised_r(trade) is None

    def test_observation_expectancy(self):
        # 2 wins at 2R, 2 losses at -1R → expectancy = (0.5*2) - (0.5*1) = 0.5
        trades = [
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.098, initial_sl=1.098),
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.098, initial_sl=1.098),
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_win_rate == pytest.approx(0.5)
        assert obs.observed_expectancy == pytest.approx(0.5, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MAE/MFE Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaeMfe:
    def test_mfe_buy(self):
        # BUY: entry=1.1, max_favourable=1.1025 → MFE = 25 pips
        trade = _make_trade(direction="BUY", entry_price=1.1, max_favourable_price=1.1025)
        assert _compute_mfe_pips(trade) == pytest.approx(25.0)

    def test_mfe_sell(self):
        # SELL: entry=1.1, max_favourable=1.0975 → MFE = 25 pips
        trade = _make_trade(direction="SELL", entry_price=1.1, max_favourable_price=1.0975)
        assert _compute_mfe_pips(trade) == pytest.approx(25.0)

    def test_mfe_zero_returns_none(self):
        trade = _make_trade(max_favourable_price=0)
        assert _compute_mfe_pips(trade) is None

    def test_mae_sl_hit(self):
        # SL hit → MAE = full initial risk (20 pips)
        trade = _make_trade(entry_price=1.1, initial_sl=1.098, close_reason="sl_hit")
        assert _compute_mae_pips(trade) == pytest.approx(20.0)

    def test_mae_tp_winner_returns_none(self):
        # Winner via TP → MAE unknown without tick data
        trade = _make_trade(entry_price=1.1, exit_price=1.102, initial_sl=1.098, close_reason="tp_hit")
        assert _compute_mae_pips(trade) is None

    def test_observation_mfe_aggregated(self):
        trades = [
            _make_trade(direction="BUY", entry_price=1.1, max_favourable_price=1.103),  # 30 pips
            _make_trade(direction="BUY", entry_price=1.1, max_favourable_price=1.101),  # 10 pips
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_mfe_pips == pytest.approx(20.0)  # average of 30, 10


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Contract Comparison Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestContractComparison:
    def test_observation_compared_to_contract(self):
        """Build observation then compare to SCALP contract."""
        trades = [
            _make_trade(
                duration_seconds=2400.0,  # 40 min (within 2-90)
                entry_price=1.1, exit_price=1.104, initial_sl=1.098,  # R=2.0
                max_favourable_price=1.105,
            )
            for _ in range(25)
        ]
        obs = build_horizon_observation(trades)
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs)
        statuses = {r.field: r.status for r in results}
        assert statuses["hold_average_minutes"] == ValidationStatus.VALIDATED
        assert statuses["rr"] == ValidationStatus.VALIDATED

    def test_insufficient_data_with_few_trades(self):
        trades = [_make_trade() for _ in range(5)]
        obs = build_horizon_observation(trades)
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs, min_sample_size=20)
        assert results[0].status == ValidationStatus.INSUFFICIENT_DATA


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Inactive Horizons Remain Supported
# ═══════════════════════════════════════════════════════════════════════════════

class TestInactiveHorizons:
    def test_intraday_observation_builds_empty(self):
        obs = build_horizon_observation([], horizon="INTRADAY")
        assert obs.horizon == "INTRADAY"
        assert obs.sample_size == 0
        assert obs.profile_version == "INTRADAY_RESEARCH_V1"

    def test_extended_observation_builds_empty(self):
        obs = build_horizon_observation([], horizon="EXTENDED")
        assert obs.horizon == "EXTENDED"
        assert obs.sample_size == 0
        assert obs.profile_version == "EXTENDED_RESEARCH_V1"

    def test_build_all_includes_inactive(self):
        all_obs = build_all_horizon_observations([])
        assert "INTRADAY" in all_obs
        assert "EXTENDED" in all_obs
        assert all_obs["INTRADAY"].sample_size == 0
        assert all_obs["EXTENDED"].sample_size == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Execution Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionUnchanged:
    def test_permitted_horizons_still_scalp(self):
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP"]

    def test_authority_blocks_intraday(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is False


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Multiple Trades Aggregate
# ═══════════════════════════════════════════════════════════════════════════════

class TestAggregation:
    def test_win_rate_calculation(self):
        trades = [
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),  # win
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),  # win
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.098, initial_sl=1.098),  # loss
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_win_rate == pytest.approx(2/3, abs=0.01)

    def test_profit_factor(self):
        # 2 wins at 2R each (gross=4R), 1 loss at 1R (gross=1R) → PF=4.0
        trades = [
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.104, initial_sl=1.098),
            _make_trade(direction="BUY", entry_price=1.1, exit_price=1.098, initial_sl=1.098),
        ]
        obs = build_horizon_observation(trades)
        assert obs.observed_profit_factor == pytest.approx(4.0, abs=0.01)

    def test_exit_reasons_counted(self):
        trades = [
            _make_trade(close_reason="tp_hit"),
            _make_trade(close_reason="tp_hit"),
            _make_trade(close_reason="sl_hit"),
        ]
        obs = build_horizon_observation(trades)
        assert obs.exit_reasons == {"tp_hit": 2, "sl_hit": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# 10. BUY and SELL Directions
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirections:
    def test_sell_trade_r_positive_on_profit(self):
        # SELL: entry=1.1, exit=1.096, sl=1.102 → profit → R=2.0
        trade = _make_trade(direction="SELL", entry_price=1.1, exit_price=1.096, initial_sl=1.102)
        assert _compute_realised_r(trade) == pytest.approx(2.0)

    def test_sell_trade_move_pips(self):
        # SELL: entry=1.1, exit=1.096 → +40 pips profit
        trade = _make_trade(direction="SELL", entry_price=1.1, exit_price=1.096)
        assert _compute_realised_move_pips(trade) == pytest.approx(40.0)

    def test_jpy_pair_pips(self):
        # USDJPY: entry=150.00, exit=150.50, sl=149.50 → risk=50 pips, profit=50 pips → R=1.0
        trade = _make_trade(
            symbol="USDJPY", direction="BUY",
            entry_price=150.00, exit_price=150.50, initial_sl=149.50,
        )
        assert _compute_initial_risk_pips(trade) == pytest.approx(50.0)
        assert _compute_realised_r(trade) == pytest.approx(1.0)
