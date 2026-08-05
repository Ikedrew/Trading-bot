"""
Tests for trade protection verification.

Covers:
    1. Position exists with matching ticket → VERIFIED
    2. Position exists but ticket changed → symbol/volume fallback match
    3. Position genuinely closed → clear failure reason
    4. Broker delay → retry succeeds
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass

from core.protection_verification import (
    verify_protection,
    ProtectionStatus,
    _query_broker_position,
)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

@dataclass
class FakeMT5Position:
    ticket: int
    sl: float
    tp: float
    symbol: str
    volume: float
    magic: int


def _mock_positions_get_by_ticket(target_ticket, positions_list):
    """Create a mock that returns positions when queried by ticket."""
    def _get(*args, **kwargs):
        ticket = kwargs.get("ticket")
        if ticket is not None:
            matches = [p for p in positions_list if p.ticket == ticket]
            return matches if matches else None
        symbol = kwargs.get("symbol")
        if symbol is not None:
            return [p for p in positions_list if p.symbol == symbol]
        return None
    return _get


# ═══════════════════════════════════════════════════════════════
# TEST 1: Position exists with matching ticket
# ═══════════════════════════════════════════════════════════════

class TestPositionFoundByTicket:
    """Position exists on broker with correct ticket → VERIFIED."""

    def test_exact_ticket_match_verified(self):
        positions = [FakeMT5Position(
            ticket=54568066, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )]
        mock_get = _mock_positions_get_by_ticket(54568066, positions)

        with patch("core.protection_verification.mt5_call", side_effect=lambda func, *a, **kw: mock_get(**kw)):
            result = verify_protection(
                symbol="US500",
                position_ticket=54568066,
                requested_sl=7607.975,
                requested_tp=7704.7,
            )

        assert result.protection_status == ProtectionStatus.VERIFIED.value
        assert result.broker_confirmed_sl == 7607.975
        assert result.broker_confirmed_tp == 7704.7
        assert result.attempts == 1

    def test_fx_position_verified(self):
        positions = [FakeMT5Position(
            ticket=12345678, sl=1.0950, tp=1.1050,
            symbol="EURUSD", volume=0.10, magic=713001,
        )]
        mock_get = _mock_positions_get_by_ticket(12345678, positions)

        with patch("core.protection_verification.mt5_call", side_effect=lambda func, *a, **kw: mock_get(**kw)):
            result = verify_protection(
                symbol="EURUSD",
                position_ticket=12345678,
                requested_sl=1.0950,
                requested_tp=1.1050,
            )

        assert result.protection_status == ProtectionStatus.VERIFIED.value


# ═══════════════════════════════════════════════════════════════
# TEST 2: Position exists but ticket changed (symbol/volume fallback)
# ═══════════════════════════════════════════════════════════════

class TestFallbackMatch:
    """Position exists but under different ticket → symbol+volume match."""

    def test_symbol_volume_fallback_finds_position(self):
        """Ticket doesn't match but volume+symbol does → found."""
        # The position has a DIFFERENT ticket than expected
        actual_position = FakeMT5Position(
            ticket=99999999, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )

        call_count = [0]

        def mock_mt5_call(func, *args, **kwargs):
            call_count[0] += 1
            ticket = kwargs.get("ticket")
            symbol = kwargs.get("symbol")
            if ticket is not None:
                # Ticket lookup fails (wrong ticket)
                return None
            if symbol is not None:
                return [actual_position]
            return None

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call):
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066,
                symbol="US500",
                volume=56.5,
                magic=713001,
            )

        assert found is True
        assert method == "symbol_volume_match"
        assert sl == 7607.975
        assert tp == 7704.7


# ═══════════════════════════════════════════════════════════════
# TEST 3: Position genuinely closed → clear failure
# ═══════════════════════════════════════════════════════════════

class TestPositionGenuinelyClosed:
    """No position found anywhere → POSITION_NOT_FOUND with clear reason."""

    def test_no_positions_found(self):
        def mock_mt5_call(func, *args, **kwargs):
            return None  # Nothing found anywhere

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call):
            result = verify_protection(
                symbol="US500",
                position_ticket=54568066,
                requested_sl=7607.975,
                requested_tp=7704.7,
            )

        assert result.protection_status == ProtectionStatus.POSITION_NOT_FOUND.value
        assert "54568066" in result.protection_failure_reason
        assert "not found" in result.protection_failure_reason.lower()

    def test_empty_positions_list(self):
        def mock_mt5_call(func, *args, **kwargs):
            return []  # Empty list

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call):
            result = verify_protection(
                symbol="US500",
                position_ticket=54568066,
                requested_sl=7607.975,
                requested_tp=7704.7,
            )

        assert result.protection_status == ProtectionStatus.POSITION_NOT_FOUND.value


# ═══════════════════════════════════════════════════════════════
# TEST 4: Broker delay → retry succeeds
# ═══════════════════════════════════════════════════════════════

class TestBrokerDelay:
    """Position not visible immediately but appears on retry."""

    def test_found_on_second_attempt(self):
        """First query returns nothing, second finds the position."""
        position = FakeMT5Position(
            ticket=54568066, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )
        call_count = [0]

        def mock_mt5_call(func, *args, **kwargs):
            call_count[0] += 1
            ticket = kwargs.get("ticket")
            if ticket is not None:
                # Fail on first call, succeed on second
                if call_count[0] <= 1:
                    return None
                return [position]
            return None

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call), \
             patch("core.protection_verification.time.sleep"):  # Don't actually sleep
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066,
                symbol="US500",
            )

        assert found is True
        assert attempts == 2
        assert method == "ticket_match"
        assert sl == 7607.975

    def test_found_on_third_attempt(self):
        """Position appears on third retry."""
        position = FakeMT5Position(
            ticket=54568066, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )
        attempt_count = [0]

        def mock_mt5_call(func, *args, **kwargs):
            ticket = kwargs.get("ticket")
            symbol = kwargs.get("symbol")
            if ticket is not None:
                attempt_count[0] += 1
                # Only succeed on 3rd ticket query
                if attempt_count[0] >= 3:
                    return [position]
                return None
            if symbol is not None:
                return None  # symbol fallback also fails initially
            return None

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call), \
             patch("core.protection_verification.time.sleep"):
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066,
                symbol="US500",
            )

        assert found is True
        assert attempts == 3

    def test_progressive_backoff_timing(self):
        """Verify that retry delays are progressive (not fixed)."""
        sleep_calls = []

        def mock_mt5_call(func, *args, **kwargs):
            return None  # Never found

        def mock_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("core.protection_verification.mt5_call", side_effect=mock_mt5_call), \
             patch("core.protection_verification.time.sleep", side_effect=mock_sleep):
            _query_broker_position(position_ticket=99999, symbol="TEST")

        # Should have delays: 0.5, 1.5, 3.0 (first attempt has no delay)
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == 0.5
        assert sleep_calls[1] == 1.5
        assert sleep_calls[2] == 3.0
