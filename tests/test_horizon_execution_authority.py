"""
Phase 2: HorizonExecutionAuthority — Comprehensive tests.

Validates:
    1. Portfolio full → all blocked
    2. Symbol full → blocked
    3. Duplicate horizon (slot occupied) → blocked
    4. Different horizons coexist → allowed
    5. Three horizons coexist → allowed
    6. No positions → allowed
    7. Invalid/unknown horizon → blocked (not in PERMITTED)
    8. Configuration changes respected
    9. Structured decision output
    10. No regression: PERMITTED_HORIZONS=["SCALP"] preserves current behaviour
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.horizon.execution_authority import HorizonExecutionAuthority, HorizonPermission
from strategy.signals import Side


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_position(symbol: str, horizon: str = "SCALP") -> MagicMock:
    """Create a mock Position with symbol and trade_horizon."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.trade_horizon = horizon
    return pos


def _make_authority(
    *,
    max_total: int = 21,
    max_per_symbol: int = 3,
    permitted: list[str] | None = None,
    enabled: bool = True,
) -> HorizonExecutionAuthority:
    """Create authority with specific config (bypasses config module)."""
    auth = HorizonExecutionAuthority.__new__(HorizonExecutionAuthority)
    auth._max_total = max_total
    auth._max_per_symbol = max_per_symbol
    auth._permitted = permitted if permitted is not None else ["SCALP"]
    auth._enabled = enabled
    return auth


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Portfolio Full
# ═══════════════════════════════════════════════════════════════════════════════

class TestPortfolioFull:
    def test_portfolio_at_max_blocks(self):
        auth = _make_authority(max_total=3)
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("GBPUSD", "SCALP"),
            _make_position("USDJPY", "SCALP"),
        ]
        result = auth.can_open(symbol="AUDUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "portfolio_full"
        assert result.portfolio_position_count == 3

    def test_portfolio_below_max_allows(self):
        auth = _make_authority(max_total=21)
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="GBPUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is True

    def test_portfolio_at_21_blocks(self):
        auth = _make_authority(max_total=21, permitted=["SCALP", "INTRADAY", "EXTENDED"])
        positions = [_make_position(f"SYM{i}", "SCALP") for i in range(21)]
        result = auth.can_open(symbol="NEWPAIR", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "portfolio_full"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Symbol Full
# ═══════════════════════════════════════════════════════════════════════════════

class TestSymbolFull:
    def test_symbol_at_max_blocks(self):
        auth = _make_authority(max_per_symbol=3, permitted=["SCALP", "INTRADAY", "EXTENDED"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
            _make_position("EURUSD", "EXTENDED"),
        ]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        # Slot is also occupied — but symbol_limit would apply too
        assert result.allowed is False
        # The first failing gate wins (slot_occupied comes before symbol_limit)
        assert result.reason == "slot_occupied"

    def test_symbol_at_max_new_horizon_still_blocked(self):
        """Even if a 4th horizon existed, symbol cap blocks it."""
        auth = _make_authority(
            max_per_symbol=3,
            permitted=["SCALP", "INTRADAY", "EXTENDED", "SWING"],
        )
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
            _make_position("EURUSD", "EXTENDED"),
        ]
        result = auth.can_open(symbol="EURUSD", horizon="SWING", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "symbol_limit_reached"
        assert result.symbol_position_count == 3

    def test_symbol_below_max_allows(self):
        auth = _make_authority(max_per_symbol=3, permitted=["SCALP", "INTRADAY"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert result.allowed is True
        assert result.symbol_position_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Duplicate Horizon (Slot Occupied)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateHorizon:
    def test_same_horizon_same_symbol_blocked(self):
        auth = _make_authority()
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "slot_occupied"
        assert result.slot_available is False

    def test_same_horizon_different_symbol_allowed(self):
        auth = _make_authority()
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="GBPUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is True
        assert result.slot_available is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Different Horizons Coexist
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonCoexistence:
    def test_scalp_exists_intraday_allowed(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert result.allowed is True
        assert result.existing_horizons == ["SCALP"]

    def test_scalp_exists_extended_allowed(self):
        auth = _make_authority(permitted=["SCALP", "EXTENDED"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=positions)
        assert result.allowed is True

    def test_extended_exists_scalp_allowed(self):
        auth = _make_authority(permitted=["SCALP", "EXTENDED"])
        positions = [_make_position("EURUSD", "EXTENDED")]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is True

    def test_intraday_exists_extended_allowed(self):
        auth = _make_authority(permitted=["INTRADAY", "EXTENDED"])
        positions = [_make_position("EURUSD", "INTRADAY")]
        result = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=positions)
        assert result.allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Three Horizons Coexist
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreeHorizons:
    def test_build_to_three_horizons(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])

        # First: empty → SCALP allowed
        r1 = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        assert r1.allowed is True

        # Second: SCALP exists → INTRADAY allowed
        positions = [_make_position("EURUSD", "SCALP")]
        r2 = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert r2.allowed is True

        # Third: SCALP + INTRADAY → EXTENDED allowed
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
        ]
        r3 = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=positions)
        assert r3.allowed is True

    def test_all_three_exist_fourth_blocked(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
            _make_position("EURUSD", "EXTENDED"),
        ]
        # All slots full, any request blocked
        r = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        assert r.allowed is False
        assert r.reason == "slot_occupied"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. No Positions (Empty Portfolio)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyPortfolio:
    def test_empty_portfolio_allows_scalp(self):
        auth = _make_authority()
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        assert result.allowed is True
        assert result.reason == "all_checks_passed"
        assert result.portfolio_position_count == 0
        assert result.symbol_position_count == 0
        assert result.slot_available is True

    def test_empty_portfolio_allows_any_permitted(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])
        for h in ["SCALP", "INTRADAY", "EXTENDED"]:
            result = auth.can_open(symbol="GBPUSD", horizon=h, current_positions=[])
            assert result.allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Invalid/Unknown Horizon
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvalidHorizon:
    def test_horizon_not_in_permitted_blocked(self):
        auth = _make_authority(permitted=["SCALP"])
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"

    def test_unknown_horizon_blocked(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])
        result = auth.can_open(symbol="EURUSD", horizon="UNKNOWN_HORIZON", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"

    def test_empty_string_horizon_blocked(self):
        auth = _make_authority(permitted=["SCALP"])
        result = auth.can_open(symbol="EURUSD", horizon="", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Configuration Changes
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigurationChanges:
    def test_max_total_respected(self):
        auth = _make_authority(max_total=2)
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("GBPUSD", "SCALP"),
        ]
        result = auth.can_open(symbol="USDJPY", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "portfolio_full"

    def test_max_per_symbol_respected(self):
        auth = _make_authority(max_per_symbol=1, permitted=["SCALP", "INTRADAY"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "symbol_limit_reached"

    def test_permitted_expansion_enables_intraday(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY"])
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is True

    def test_permitted_removal_blocks_previously_allowed(self):
        auth = _make_authority(permitted=["INTRADAY"])  # SCALP removed
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Structured Decision Output
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecisionOutput:
    def test_permission_has_all_fields(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)

        assert result.allowed is True
        assert result.reason == "all_checks_passed"
        assert result.symbol == "EURUSD"
        assert result.requested_horizon == "INTRADAY"
        assert result.existing_horizons == ["SCALP"]
        assert result.symbol_position_count == 1
        assert result.portfolio_position_count == 1
        assert result.slot_available is True

    def test_to_dict_serializable(self):
        auth = _make_authority()
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "allowed" in d
        assert "reason" in d
        assert "symbol" in d
        assert "requested_horizon" in d
        assert "existing_horizons" in d
        assert "symbol_position_count" in d
        assert "portfolio_position_count" in d
        assert "slot_available" in d

    def test_blocked_result_contains_metadata(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
        ]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.existing_horizons == ["SCALP", "INTRADAY"]
        assert result.symbol_position_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 10. No Regression: Current SCALP-Only Behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoRegression:
    def test_scalp_only_config_allows_first_trade(self):
        """Current production config: PERMITTED_HORIZONS=["SCALP"], empty portfolio."""
        auth = _make_authority(permitted=["SCALP"], max_total=21, max_per_symbol=3)
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        assert result.allowed is True

    def test_scalp_only_blocks_intraday(self):
        """Current config does not permit INTRADAY even if slot is free."""
        auth = _make_authority(permitted=["SCALP"])
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"

    def test_scalp_only_blocks_extended(self):
        """Current config does not permit EXTENDED."""
        auth = _make_authority(permitted=["SCALP"])
        result = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=[])
        assert result.allowed is False
        assert result.reason == "horizon_not_permitted"

    def test_scalp_duplicate_blocked(self):
        """Cannot open two SCALPs on same symbol."""
        auth = _make_authority(permitted=["SCALP"])
        positions = [_make_position("EURUSD", "SCALP")]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "slot_occupied"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Gate Priority (First Failing Gate Wins)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGatePriority:
    def test_not_permitted_before_slot_check(self):
        """Gate 1 (permitted) fires before Gate 2 (slot)."""
        auth = _make_authority(permitted=["SCALP"])
        positions = [_make_position("EURUSD", "INTRADAY")]  # slot would be free
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert result.reason == "horizon_not_permitted"

    def test_slot_occupied_before_symbol_limit(self):
        """Gate 2 (slot) fires before Gate 3 (symbol limit)."""
        auth = _make_authority(max_per_symbol=2, permitted=["SCALP", "INTRADAY"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
        ]
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=positions)
        # SCALP slot is occupied (Gate 2) AND symbol at 2/2 (Gate 3)
        # Gate 2 should fire first
        assert result.reason == "slot_occupied"

    def test_symbol_limit_before_portfolio_full(self):
        """Gate 3 (symbol) fires before Gate 4 (portfolio) when both apply."""
        auth = _make_authority(
            max_per_symbol=1, max_total=1,
            permitted=["SCALP", "INTRADAY"],
        )
        positions = [_make_position("EURUSD", "SCALP")]
        # EURUSD at 1/1 (Gate 3) AND portfolio at 1/1 (Gate 4)
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=positions)
        assert result.reason == "symbol_limit_reached"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Multi-Symbol Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiSymbol:
    def test_different_symbols_independent(self):
        """Positions on EURUSD don't affect GBPUSD slots."""
        auth = _make_authority(permitted=["SCALP", "INTRADAY", "EXTENDED"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("EURUSD", "INTRADAY"),
            _make_position("EURUSD", "EXTENDED"),
        ]
        result = auth.can_open(symbol="GBPUSD", horizon="SCALP", current_positions=positions)
        assert result.allowed is True
        assert result.symbol_position_count == 0  # GBPUSD has 0

    def test_portfolio_count_spans_all_symbols(self):
        """Portfolio cap considers all symbols together."""
        auth = _make_authority(max_total=5, permitted=["SCALP", "INTRADAY"])
        positions = [
            _make_position("EURUSD", "SCALP"),
            _make_position("GBPUSD", "SCALP"),
            _make_position("USDJPY", "SCALP"),
            _make_position("AUDUSD", "SCALP"),
            _make_position("NZDUSD", "SCALP"),
        ]
        result = auth.can_open(symbol="USDCAD", horizon="SCALP", current_positions=positions)
        assert result.allowed is False
        assert result.reason == "portfolio_full"
        assert result.portfolio_position_count == 5


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Properties & Observability
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperties:
    def test_enabled_property(self):
        auth = _make_authority(enabled=True)
        assert auth.enabled is True

    def test_disabled_property(self):
        auth = _make_authority(enabled=False)
        assert auth.enabled is False

    def test_max_total_property(self):
        auth = _make_authority(max_total=21)
        assert auth.max_total_positions == 21

    def test_max_per_symbol_property(self):
        auth = _make_authority(max_per_symbol=3)
        assert auth.max_positions_per_symbol == 3

    def test_permitted_horizons_property(self):
        auth = _make_authority(permitted=["SCALP", "INTRADAY"])
        assert auth.permitted_horizons == ["SCALP", "INTRADAY"]
