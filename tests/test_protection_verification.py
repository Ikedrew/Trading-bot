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
from core.position_ownership import PositionOwnership
from core.lifecycle_evidence_obligations import LifecycleEvidenceLedger, ObligationStatus
from core.accounts.worker import AccountReadError


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


class FakeLifecycleRouter:
    """Explicit METAQUOTES test mapping with account-owned ticket reads."""
    _accounts = {"METAQUOTES": ("MetaQuotes", "TEST_SERVER")}

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def read(self, owner, operation, **kwargs):
        assert operation == "positions_get"
        assert (owner.account_id, owner.broker, owner.broker_server) == (
            "METAQUOTES", *self._accounts["METAQUOTES"])
        self.calls += 1
        return self.response(self.calls) if callable(self.response) else self.response


def _owner(ticket: int, symbol: str) -> PositionOwnership:
    return PositionOwnership(
        account_id="METAQUOTES", broker="MetaQuotes", broker_server="TEST_SERVER",
        position_ticket=ticket, canonical_symbol=symbol, broker_symbol=symbol,
        correlation_id=f"COR-{ticket}", decision_id=f"DEC-{ticket}",
    )


def test_verified_fill_records_local_protection_without_claiming_canonical_presence(tmp_path):
    ledger = LifecycleEvidenceLedger(tmp_path / "obligations.jsonl")
    owner = PositionOwnership(
        account_id="ACCOUNT-A", broker="BROKER", broker_server="SERVER",
        position_ticket=123, canonical_symbol="EURUSD",
        correlation_id="COR-1", decision_id="DEC-1",
    )

    with patch("core.lifecycle_evidence_obligations.obligation_ledger",
               return_value=ledger), \
            patch("core.protection_verification._query_broker_position",
                  return_value=(1.095, 1.105, True, 1, "exact_ticket")), \
            patch("core.protection_verification._persist_result", return_value=True):
        result = verify_protection(
            symbol="EURUSD", position_ticket=123,
            requested_sl=1.095, requested_tp=1.105,
            correlation_id="COR-1", ownership=owner,
        )

    by_dataset = {item.expected_dataset: item for item in ledger.obligations()}
    protection = by_dataset["protection_audit"]
    truth = by_dataset["trade_truth"]
    assert result.protection_status == ProtectionStatus.VERIFIED.value
    assert protection.current_status == ObligationStatus.NOT_YET_DUE.value
    assert protection.provenance["producer_write"] == "LOCAL_FSYNC_SUCCEEDED"
    assert protection.provenance["canonical_mirror_acknowledgement"] == "NOT_OBSERVED"
    assert truth.current_status == ObligationStatus.NOT_YET_DUE.value


def test_missing_protection_account_is_explicit_producer_failure(tmp_path):
    ledger = LifecycleEvidenceLedger(tmp_path / "obligations.jsonl")
    with patch("core.lifecycle_evidence_obligations.obligation_ledger",
               return_value=ledger), \
            patch("core.protection_verification._query_broker_position",
                  return_value=(0.0, 0.0, False, 1, "not_found")), \
            patch("core.protection_verification._persist_result", return_value=True):
        verify_protection(
            symbol="EURUSD", position_ticket=123,
            requested_sl=1.095, requested_tp=1.105,
            correlation_id="COR-MISSING-ACCOUNT",
        )

    by_dataset = {item.expected_dataset: item for item in ledger.obligations()}
    assert by_dataset["protection_audit"].current_status == ObligationStatus.PRODUCER_FAILED.value
    assert by_dataset["trade_truth"].current_status == ObligationStatus.PRODUCER_FAILED.value
    assert not by_dataset["protection_audit"].account_id


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
        result = verify_protection(
            symbol="US500", position_ticket=54568066,
            requested_sl=7607.975, requested_tp=7704.7,
            ownership=_owner(54568066, "US500"),
            lifecycle_router=FakeLifecycleRouter(positions),
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
        result = verify_protection(
            symbol="EURUSD", position_ticket=12345678,
            requested_sl=1.0950, requested_tp=1.1050,
            ownership=_owner(12345678, "EURUSD"),
            lifecycle_router=FakeLifecycleRouter(positions),
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value


# ═══════════════════════════════════════════════════════════════
# TEST 2: Position exists but ticket changed (symbol/volume fallback)
# ═══════════════════════════════════════════════════════════════

class TestFallbackMatch:
    """Account-owned protection never falls back from a mismatched ticket."""

    def test_different_ticket_is_rejected_without_symbol_volume_fallback(self):
        """A similar symbol/volume record cannot satisfy the pinned ticket."""
        actual_position = FakeMT5Position(
            ticket=99999999, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )
        router = FakeLifecycleRouter([actual_position])
        with pytest.raises(AccountReadError, match="POSITION_IDENTITY_MISMATCH"):
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066,
                symbol="US500",
                volume=56.5,
                magic=713001,
                ownership=_owner(54568066, "US500"),
                lifecycle_router=router,
            )


# ═══════════════════════════════════════════════════════════════
# TEST 3: Position genuinely closed → clear failure
# ═══════════════════════════════════════════════════════════════

class TestPositionGenuinelyClosed:
    """No position found anywhere → POSITION_NOT_FOUND with clear reason."""

    def test_no_positions_found(self):
        def mock_mt5_call(func, *args, **kwargs):
            return None  # Nothing found anywhere

        result = verify_protection(
            symbol="US500", position_ticket=54568066,
            requested_sl=7607.975, requested_tp=7704.7,
            ownership=_owner(54568066, "US500"),
            lifecycle_router=FakeLifecycleRouter([]),
        )

        assert result.protection_status == ProtectionStatus.POSITION_NOT_FOUND.value
        assert "54568066" in result.protection_failure_reason
        assert "not found" in result.protection_failure_reason.lower()

    def test_empty_positions_list(self):
        def mock_mt5_call(func, *args, **kwargs):
            return []  # Empty list

        result = verify_protection(
            symbol="US500", position_ticket=54568066,
            requested_sl=7607.975, requested_tp=7704.7,
            ownership=_owner(54568066, "US500"),
            lifecycle_router=FakeLifecycleRouter([]),
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
        router = FakeLifecycleRouter(lambda call: [] if call == 1 else [position])
        with patch("core.protection_verification.time.sleep"):
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066, symbol="US500",
                ownership=_owner(54568066, "US500"), lifecycle_router=router,
            )

        assert found is True
        assert attempts == 2
        assert method == "account_ticket_match"
        assert sl == 7607.975

    def test_found_on_third_attempt(self):
        """Position appears on third retry."""
        position = FakeMT5Position(
            ticket=54568066, sl=7607.975, tp=7704.7,
            symbol="US500", volume=56.5, magic=713001,
        )
        router = FakeLifecycleRouter(lambda call: [position] if call >= 3 else [])
        with patch("core.protection_verification.time.sleep"):
            sl, tp, found, attempts, method = _query_broker_position(
                position_ticket=54568066,
                symbol="US500",
                ownership=_owner(54568066, "US500"),
                lifecycle_router=router,
            )

        assert found is True
        assert attempts == 3

    def test_progressive_backoff_timing(self):
        """Verify that retry delays are progressive (not fixed)."""
        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("core.protection_verification.time.sleep", side_effect=mock_sleep):
            _query_broker_position(
                position_ticket=99999, symbol="TEST",
                ownership=_owner(99999, "TEST"), lifecycle_router=FakeLifecycleRouter([]),
            )

        # Should have delays: 0.5, 1.5, 3.0 (first attempt has no delay)
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == 0.5
        assert sleep_calls[1] == 1.5
        assert sleep_calls[2] == 3.0
