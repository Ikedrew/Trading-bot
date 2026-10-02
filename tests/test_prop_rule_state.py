"""Prop rule STATE authority (Block 3B).

Covers the durable state foundation end to end: initial-anchor creation,
immutability and conflict; rule-day anchors and reset timezone semantics
including non-midnight resets and the America/New_York DST boundary; separate
monotonic balance/equity high-water state; the authoritative realised P&L ledger
with separated commission/swap/fees; trade-event deduplication, same-ticket
multi-account isolation, partial closes and late arrivals; trading-day history
and inactivity; historical rule-pack selection; durability and restart
reconstruction.

Nothing here reads a wall clock, touches a broker, or mutates trading runtime.
Every instant is explicit.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from core.risk.prop_rule_enums import RulePhase
from core.risk.prop_rule_state import (
    ANCHOR_BASES,
    STATE_FAMILIES,
    AccountKey,
    AmbiguousRulePackInForce,
    CloseEventKind,
    ClosedTradeEvent,
    ConflictingDuplicateEvent,
    DailyAccountAnchor,
    DailyPnlLedgerEntry,
    HighWaterGapKind,
    HighWaterState,
    InitialAccountAnchor,
    InitialAnchorConflict,
    NoRulePackInForce,
    PropRuleStateError,
    PropRuleStateStore,
    RuleDayDefinition,
    RuleDayUnavailable,
    TradingDayCriterion,
    TradingDayHistory,
    TradingDayRecord,
    TradingDayRecordConflict,
    build_trading_day_history,
    derive_inactivity_state,
    parse_inactive_days,
    project_daily_ledger,
    project_trading_day_record,
    resolve_criterion,
    rule_pack_timeline,
    rules_effective_at,
)
from core.risk.prop_rule_pack import RulePackStore

UTC = timezone.utc

ACCOUNT_A = AccountKey("ACC_A", "MT5", "DemoBroker-Server", 111111)
ACCOUNT_B = AccountKey("ACC_B", "MT5", "DemoBroker-Server", 222222)

#: A 17:00 America/New_York reset. March 2026 DST starts on the 8th.
NY_1700 = RuleDayDefinition("America/New_York", time(17, 0))
NY_MIDNIGHT = RuleDayDefinition("America/New_York", time(0, 0))
UTC_MIDNIGHT = RuleDayDefinition("UTC", time(0, 0))


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# BUILDERS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def initial_anchor(
    account: AccountKey = ACCOUNT_A,
    balance: float = 100_000.0,
    equity: float = 100_000.0,
    observed: datetime | None = None,
    snapshot_id: str = "asnap_initial_1",
) -> InitialAccountAnchor:
    return InitialAccountAnchor(
        account=account,
        account_currency="USD",
        initial_balance=balance,
        initial_equity=equity,
        observed_at_utc=observed or datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
        source_snapshot_id=snapshot_id,
        source_provenance="BLOCK_2A_ACCOUNT_SNAPSHOT",
    )


def daily_anchor(
    account: AccountKey = ACCOUNT_A,
    definition: RuleDayDefinition = NY_MIDNIGHT,
    rule_day: date = date(2026, 3, 10),
    balance: float = 100_000.0,
    equity: float = 100_000.0,
    basis: str = "EXACT_BOUNDARY",
) -> DailyAccountAnchor:
    return DailyAccountAnchor(
        account=account,
        account_currency="USD",
        rule_day=rule_day,
        timezone_name=definition.timezone_name,
        rule_day_definition=definition.key(),
        start_of_day_balance=balance,
        start_of_day_equity=equity,
        anchor_at_utc=definition.day_start_utc(rule_day),
        source_snapshot_id=f"asnap_sod_{rule_day.isoformat()}",
        source_provenance="BLOCK_2A_ACCOUNT_SNAPSHOT",
        start_of_day_floating_pnl=0.0,
        anchor_basis=basis,
    )


def close_event(
    *,
    account: AccountKey = ACCOUNT_A,
    source_trade_id: str = "deal-1",
    ticket: int | None = 555,
    closed_at: datetime | None = None,
    gross: float = -250.0,
    commission: float | None = -3.0,
    swap: float | None = -1.0,
    fees: float | None = 0.0,
    volume: float | None = 0.10,
    close_kind: CloseEventKind = CloseEventKind.FULL_CLOSE,
    duration: float | None = 900.0,
    symbol: str = "EURUSD",
    event_id: str = "",
) -> ClosedTradeEvent:
    return ClosedTradeEvent(
        account=account,
        account_currency="USD",
        source="MT5_HISTORY_DEAL",
        source_trade_id=source_trade_id,
        position_ticket=ticket,
        symbol=symbol,
        closed_at_utc=closed_at or datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
        gross_realised_pnl=gross,
        volume=volume,
        close_kind=close_kind,
        commission=commission,
        swap=swap,
        fees=fees,
        duration_seconds=duration,
        event_id=event_id,
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 1-3  INITIAL ANCHOR: creation, immutability, conflict
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_initial_anchor_creation_is_deterministic_and_typed():
    anchor = initial_anchor()
    assert anchor.anchor_id.startswith("pia_")
    assert anchor.initial_balance == 100_000.0
    assert anchor.account_currency == "USD"
    assert anchor.source_snapshot_id == "asnap_initial_1"
    # Identity is derived from the ACCOUNT + VALUES, so it is stable.
    assert initial_anchor().anchor_id == anchor.anchor_id


def test_initial_anchor_is_frozen():
    anchor = initial_anchor()
    with pytest.raises(Exception):
        anchor.initial_balance = 1.0  # type: ignore[misc]


def test_initial_anchor_rejects_non_positive_and_missing_values():
    with pytest.raises(PropRuleStateError):
        initial_anchor(balance=0.0)
    with pytest.raises(PropRuleStateError):
        InitialAccountAnchor(
            account=ACCOUNT_A, account_currency="USD", initial_balance=100.0,
            initial_equity=100.0, observed_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
            source_snapshot_id="s", source_provenance="",
        )


def test_initial_anchor_rejects_naive_observed_at():
    with pytest.raises(PropRuleStateError):
        InitialAccountAnchor(
            account=ACCOUNT_A, account_currency="USD", initial_balance=100.0,
            initial_equity=100.0, observed_at_utc=datetime(2026, 1, 1),
            source_snapshot_id="s", source_provenance="p",
        )


def test_initial_anchor_conflict_fails_closed_rather_than_choosing_by_time():
    """Two incompatible "initial" snapshots are a CONFLICT, not a timestamp race."""
    store = PropRuleStateStore()
    store.record_initial_anchor(initial_anchor(balance=100_000.0))
    # A LATER snapshot claiming a different "initial" value must be rejected even
    # though it is newer: the initial anchor can never drift.
    later = initial_anchor(
        balance=99_000.0, observed=datetime(2026, 3, 20, tzinfo=UTC), snapshot_id="asnap_later"
    )
    assert later.observed_at_utc > store.initial_anchor_for(ACCOUNT_A).observed_at_utc
    with pytest.raises(InitialAnchorConflict):
        store.record_initial_anchor(later)
    assert store.initial_anchor_for(ACCOUNT_A).initial_balance == 100_000.0


def test_initial_anchor_recording_is_idempotent_for_identical_values():
    store = PropRuleStateStore()
    first = store.record_initial_anchor(initial_anchor())
    second = store.record_initial_anchor(initial_anchor(snapshot_id="asnap_repeat"))
    assert first.anchor_id == second.anchor_id


def test_initial_anchor_never_recalculated_from_a_later_balance():
    store = PropRuleStateStore()
    store.record_initial_anchor(initial_anchor(balance=100_000.0))
    store.record_daily_anchor(daily_anchor(balance=87_500.0))
    assert store.initial_anchor_for(ACCOUNT_A).initial_balance == 100_000.0



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 4-6  DAILY ANCHOR AND RESET SEMANTICS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_daily_anchor_records_one_start_of_day_reference():
    anchor = daily_anchor(balance=101_250.0, equity=101_400.0)
    assert anchor.anchor_id.startswith("daa_")
    assert anchor.start_of_day_balance == 101_250.0
    assert anchor.rule_day == date(2026, 3, 10)
    assert anchor.anchor_basis == "EXACT_BOUNDARY"


def test_daily_anchor_requires_an_explicit_timezone_and_definition():
    with pytest.raises(PropRuleStateError):
        DailyAccountAnchor(
            account=ACCOUNT_A, account_currency="USD", rule_day=date(2026, 3, 10),
            timezone_name="", rule_day_definition="x", start_of_day_balance=1.0,
            start_of_day_equity=1.0, anchor_at_utc=datetime(2026, 3, 10, tzinfo=UTC),
            source_snapshot_id="s", source_provenance="p",
        )


def test_daily_anchor_rejects_an_unlabelled_basis():
    with pytest.raises(PropRuleStateError):
        daily_anchor(basis="SOME_GUESS")
    assert ANCHOR_BASES == {"EXACT_BOUNDARY", "FIRST_VALID_AFTER"}


def test_daily_anchor_conflict_fails_closed():
    store = PropRuleStateStore()
    store.record_daily_anchor(daily_anchor(balance=100_000.0))
    with pytest.raises(InitialAnchorConflict):
        store.record_daily_anchor(daily_anchor(balance=99_000.0))
    stored = store.daily_anchor_for(ACCOUNT_A, NY_MIDNIGHT.timezone_name, date(2026, 3, 10))
    assert stored.start_of_day_balance == 100_000.0


def test_daily_anchors_are_scoped_per_account_timezone_and_rule_day():
    store = PropRuleStateStore()
    store.record_daily_anchor(daily_anchor(account=ACCOUNT_A, rule_day=date(2026, 3, 10)))
    store.record_daily_anchor(daily_anchor(account=ACCOUNT_B, rule_day=date(2026, 3, 10)))
    store.record_daily_anchor(daily_anchor(account=ACCOUNT_A, rule_day=date(2026, 3, 11)))
    london = RuleDayDefinition("Europe/London", time(0, 0))
    store.record_daily_anchor(daily_anchor(account=ACCOUNT_A, definition=london, rule_day=date(2026, 3, 10)))
    assert store.daily_anchor_for(ACCOUNT_A, "America/New_York", date(2026, 3, 10)) is not None
    assert store.daily_anchor_for(ACCOUNT_B, "America/New_York", date(2026, 3, 10)) is not None
    assert store.daily_anchor_for(ACCOUNT_A, "Europe/London", date(2026, 3, 10)) is not None
    assert store.daily_anchor_for(ACCOUNT_A, "America/New_York", date(2026, 3, 12)) is None


def test_reset_definition_rejects_naive_reset_time_and_unknown_zone():
    with pytest.raises(RuleDayUnavailable):
        RuleDayDefinition("America/New_York", time(17, 0, tzinfo=UTC))
    with pytest.raises(RuleDayUnavailable):
        RuleDayDefinition("Not/AZone", time(0, 0))
    with pytest.raises(RuleDayUnavailable):
        RuleDayDefinition("", time(0, 0))


def test_midnight_reset_maps_utc_five_hours_ahead_of_new_york():
    """00:00 New York is 05:00Z in winter (EST)."""
    assert NY_MIDNIGHT.day_start_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    assert NY_MIDNIGHT.rule_day_for(datetime(2026, 1, 15, 4, 59, tzinfo=UTC)) == date(2026, 1, 14)
    assert NY_MIDNIGHT.rule_day_for(datetime(2026, 1, 15, 5, 0, tzinfo=UTC)) == date(2026, 1, 15)


def test_non_midnight_reset_uses_the_local_rule_clock():
    """A 17:00 New York reset is 22:00Z in winter, not 17:00Z."""
    assert NY_1700.day_start_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
    assert NY_1700.rule_day_for(datetime(2026, 1, 15, 21, 59, tzinfo=UTC)) == date(2026, 1, 14)
    assert NY_1700.rule_day_for(datetime(2026, 1, 15, 22, 0, tzinfo=UTC)) == date(2026, 1, 15)
    assert NY_1700.rule_day_for(datetime(2026, 1, 15, 21, 0, tzinfo=UTC)) == date(2026, 1, 14)


def test_rule_day_never_uses_host_machine_local_time():
    """The rule day comes from the rule pack's zone, not the process timezone."""
    definition = RuleDayDefinition("Pacific/Kiritimati", time(0, 0))  # UTC+14
    # Local midnight on the 5th is 10:00Z on the 4th.
    assert definition.day_start_utc(date(2026, 5, 5)) == datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    # 09:59Z on the 5th is still 23:59 local on the 4th.
    assert definition.rule_day_for(datetime(2026, 5, 5, 9, 59, tzinfo=UTC)) == date(2026, 5, 5)
    assert definition.rule_day_for(datetime(2026, 5, 4, 10, 0, tzinfo=UTC)) == date(2026, 5, 5)
    assert definition.rule_day_for(datetime(2026, 5, 4, 9, 59, tzinfo=UTC)) == date(2026, 5, 4)


def test_rule_day_window_is_half_open_at_the_boundary():
    definition = NY_MIDNIGHT
    day = date(2026, 3, 10)
    start = definition.day_start_utc(day)
    end = definition.day_end_utc(day)
    # The window is [start, end): the closing instant is the NEXT day's opening.
    assert definition.contains(start)
    assert definition.contains(end - timedelta(microseconds=1))
    # At `end` the instant already belongs to the following rule day.
    assert definition.rule_day_for(end) == day + timedelta(days=1)
    assert end - start == timedelta(hours=24)


def test_iter_rule_days_rejects_an_inverted_range():
    with pytest.raises(RuleDayUnavailable):
        NY_MIDNIGHT.iter_rule_days(datetime(2026, 3, 10, tzinfo=UTC), datetime(2026, 3, 9, tzinfo=UTC))



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 7  RULE-DAY DST ACCEPTANCE (America/New_York, spring forward 2026-03-08)
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_dst_spring_forward_boundary_is_exactly_once_per_day():
    """00:00 New York is 05:00Z until 2026-03-08, then 04:00Z (EDT)."""
    before = NY_MIDNIGHT.day_start_utc(date(2026, 3, 7))
    transition = NY_MIDNIGHT.day_start_utc(date(2026, 3, 8))
    after = NY_MIDNIGHT.day_start_utc(date(2026, 3, 9))
    assert before == datetime(2026, 3, 7, 5, 0, tzinfo=UTC)
    # The 8th still OPENS in EST; the transition happens at 02:00 local that day.
    assert transition == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert after == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)
    # The spring-forward rule day is 23 hours long; every other day is 24.
    assert NY_MIDNIGHT.day_end_utc(date(2026, 3, 8)) - transition == timedelta(hours=23)
    assert NY_MIDNIGHT.day_end_utc(date(2026, 3, 7)) - before == timedelta(hours=24)


def test_dst_produces_no_duplicate_and_no_missing_rule_day():
    start = datetime(2026, 3, 6, tzinfo=UTC)
    end = datetime(2026, 3, 11, tzinfo=UTC)
    days = NY_MIDNIGHT.iter_rule_days(start, end)
    assert days == (
        date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 7),
        date(2026, 3, 8), date(2026, 3, 9), date(2026, 3, 10),
    )
    # No duplicates and strictly increasing: one boundary per rule day.
    assert len(set(days)) == len(days)
    assert list(days) == sorted(days)


def test_every_instant_in_a_dst_window_maps_to_exactly_one_rule_day():
    """Sweep the whole window: each instant belongs to exactly one rule day."""
    cursor = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    end = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    seen: set[date] = set()
    while cursor < end:
        day = NY_MIDNIGHT.rule_day_for(cursor)
        assert NY_MIDNIGHT.contains(cursor)
        # The instant falls inside the computed day's own window.
        assert NY_MIDNIGHT.day_start_utc(day) <= cursor < NY_MIDNIGHT.day_end_utc(day)
        seen.add(day)
        cursor += timedelta(minutes=17)
    assert date(2026, 3, 8) in seen


def test_dst_non_midnight_reset_also_shifts_correctly():
    """17:00 New York is 22:00Z in winter and 21:00Z after the transition."""
    assert NY_1700.day_start_utc(date(2026, 3, 7)) == datetime(2026, 3, 7, 22, 0, tzinfo=UTC)
    # The 7th at 17:00 is still EST; the 8th at 17:00 is already EDT.
    assert NY_1700.day_start_utc(date(2026, 3, 8)) == datetime(2026, 3, 8, 21, 0, tzinfo=UTC)
    # The 7th's rule day is the 23-hour one, because it spans the transition.
    assert NY_1700.day_end_utc(date(2026, 3, 7)) - NY_1700.day_start_utc(date(2026, 3, 7)) == timedelta(hours=23)
    # 20:59Z on the 7th is 15:59 local, still the 6th's rule day.
    assert NY_1700.rule_day_for(datetime(2026, 3, 7, 20, 59, tzinfo=UTC)) == date(2026, 3, 6)
    assert NY_1700.rule_day_for(datetime(2026, 3, 7, 22, 0, tzinfo=UTC)) == date(2026, 3, 7)


def test_dst_non_midnight_window_has_no_duplicate_or_missing_day():
    days = NY_1700.iter_rule_days(datetime(2026, 3, 6, tzinfo=UTC), datetime(2026, 3, 11, tzinfo=UTC))
    assert days == (
        date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 7),
        date(2026, 3, 8), date(2026, 3, 9), date(2026, 3, 10),
    )
    assert len(set(days)) == len(days)


def test_dst_fall_back_boundary_is_handled_symmetrically():
    """Fall back 2026-11-01: 00:00 New York is 04:00Z, and that day is 25h."""
    assert NY_MIDNIGHT.day_start_utc(date(2026, 10, 31)) == datetime(2026, 10, 31, 4, 0, tzinfo=UTC)
    # The 1st still OPENS in EDT; the transition happens at 02:00 local that day.
    assert NY_MIDNIGHT.day_start_utc(date(2026, 11, 1)) == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert NY_MIDNIGHT.day_start_utc(date(2026, 11, 2)) == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    # The fall-back rule day is 25 hours long.
    assert NY_MIDNIGHT.day_end_utc(date(2026, 11, 1)) - NY_MIDNIGHT.day_start_utc(
        date(2026, 11, 1)
    ) == timedelta(hours=25)


def test_utc_definition_is_stable_across_dst():
    days = UTC_MIDNIGHT.iter_rule_days(datetime(2026, 3, 6, tzinfo=UTC), datetime(2026, 3, 11, tzinfo=UTC))
    assert days == tuple(date(2026, 3, d) for d in range(6, 11))



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 8-11  HIGH-WATER STATE
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_high_water_tracks_balance_and_equity_separately():
    state = HighWaterState.empty(ACCOUNT_A, "USD")
    assert state.high_water_balance is None
    assert state.high_water_equity is None
    assert state.gap is HighWaterGapKind.NO_OBSERVATIONS
    # Equity is far above balance here; they must NOT be blended.
    state = state.advance(
        balance=100_000.0, equity=103_500.0,
        observed_at_utc=datetime(2026, 3, 10, 14, 0, tzinfo=UTC), source_snapshot_id="asnap_1",
    )
    assert state.high_water_balance == 100_000.0
    assert state.high_water_equity == 103_500.0
    assert state.balance_mark.source_snapshot_id == "asnap_1"
    assert state.equity_mark.high_at_utc == datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
    assert state.is_complete


def test_high_water_advances_only_on_a_new_maximum():
    state = HighWaterState.empty(ACCOUNT_A, "USD").advance(
        balance=100_000.0, equity=100_000.0,
        observed_at_utc=datetime(2026, 3, 10, 14, 0, tzinfo=UTC), source_snapshot_id="asnap_1",
    )
    higher = state.advance(
        balance=101_000.0, equity=102_000.0,
        observed_at_utc=datetime(2026, 3, 10, 15, 0, tzinfo=UTC), source_snapshot_id="asnap_2",
    )
    assert higher.high_water_balance == 101_000.0
    assert higher.high_water_equity == 102_000.0
    assert higher.balance_mark.source_snapshot_id == "asnap_2"


def test_high_water_never_decreases_on_a_lower_observation():
    state = HighWaterState.empty(ACCOUNT_A, "USD").advance(
        balance=100_000.0, equity=100_000.0,
        observed_at_utc=datetime(2026, 3, 10, 14, 0, tzinfo=UTC), source_snapshot_id="asnap_1",
    )
    lower = state.advance(
        balance=90_000.0, equity=88_000.0,
        observed_at_utc=datetime(2026, 3, 10, 16, 0, tzinfo=UTC), source_snapshot_id="asnap_2",
    )
    assert lower.high_water_balance == 100_000.0
    assert lower.high_water_equity == 100_000.0
    # The evidence of the LOW point is still counted, but the mark is unchanged.
    assert lower.observation_count == 2
    assert lower.balance_mark.source_snapshot_id == "asnap_1"


def test_high_water_missing_observation_does_not_reduce_and_declares_a_gap():
    state = HighWaterState.empty(ACCOUNT_A, "USD").advance(
        balance=100_000.0, equity=100_000.0,
        observed_at_utc=datetime(2026, 3, 10, 14, 0, tzinfo=UTC), source_snapshot_id="asnap_1",
    )
    gapped = state.note_coverage_gap("NO_OBSERVATION_2026-03-10T18:00Z_2026-03-11T09:00Z")
    # A missing observation can never reduce the mark...
    assert gapped.high_water_equity == 100_000.0
    # ...and the unseen period is NOT assumed to have been flat.
    assert gapped.gap is HighWaterGapKind.COVERAGE_GAP
    assert gapped.is_complete is False


def test_high_water_rejects_a_missing_source_snapshot_id():
    state = HighWaterState.empty(ACCOUNT_A, "USD")
    with pytest.raises(PropRuleStateError):
        state.advance(
            balance=100_000.0, equity=100_000.0,
            observed_at_utc=datetime(2026, 3, 10, tzinfo=UTC), source_snapshot_id="",
        )


def test_high_water_reconstructs_exactly_after_restart(tmp_path):
    store = PropRuleStateStore(tmp_path / "state")
    moments = [
        ("asnap_1", 100_000.0, 100_000.0, datetime(2026, 3, 10, 14, 0, tzinfo=UTC)),
        ("asnap_2", 101_500.0, 102_000.0, datetime(2026, 3, 10, 15, 0, tzinfo=UTC)),
        ("asnap_3", 99_000.0, 98_000.0, datetime(2026, 3, 10, 16, 0, tzinfo=UTC)),
    ]
    for snapshot_id, balance, equity, moment in moments:
        store.record_high_water_observation(
            account=ACCOUNT_A, account_currency="USD", balance=balance, equity=equity,
            observed_at_utc=moment, source_snapshot_id=snapshot_id,
        )
    before = store.high_water_for(ACCOUNT_A, "USD")
    # A brand-new store reading only the durable records.
    restarted = PropRuleStateStore(tmp_path / "state")
    after = restarted.high_water_for(ACCOUNT_A, "USD")
    assert (after.high_water_balance, after.high_water_equity) == (
        before.high_water_balance, before.high_water_equity,
    )
    assert after.high_water_balance == 101_500.0
    assert after.high_water_equity == 102_000.0
    assert after.balance_mark.source_snapshot_id == "asnap_2"
    assert after.observation_count == 3


def test_high_water_projection_is_order_independent(tmp_path):
    """Observations recorded out of order still reconstruct the same maximum."""
    base = tmp_path / "state"
    ascending = PropRuleStateStore(base)
    for snapshot_id, balance, equity, moment in (
        ("a1", 100_000.0, 100_000.0, datetime(2026, 3, 10, 14, 0, tzinfo=UTC)),
        ("a2", 105_000.0, 106_000.0, datetime(2026, 3, 10, 15, 0, tzinfo=UTC)),
        ("a3", 101_000.0, 101_000.0, datetime(2026, 3, 10, 16, 0, tzinfo=UTC)),
    ):
        ascending.record_high_water_observation(
            account=ACCOUNT_A, account_currency="USD", balance=balance, equity=equity,
            observed_at_utc=moment, source_snapshot_id=snapshot_id,
        )
    rebuilt = PropRuleStateStore(base).high_water_for(ACCOUNT_A, "USD")
    assert rebuilt.high_water_equity == 106_000.0
    assert rebuilt.high_water_balance == 105_000.0



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 12-16  REALISED P&L LEDGER, COSTS, DEDUPLICATION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def ledger_for(events, definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10), account=ACCOUNT_A):
    return project_daily_ledger(
        account=account, account_currency="USD", definition=definition,
        rule_day=rule_day, events=events,
    )


def test_ledger_projects_gross_realised_pnl_from_close_evidence():
    ledger = ledger_for([close_event(gross=-250.0)])
    assert ledger.trade_count == 1
    assert ledger.gross_realised_pnl == -250.0
    assert ledger.ledger_id.startswith("dpl_")
    assert ledger.event_ids and ledger.source_trade_ids == ("deal-1",)


def test_ledger_keeps_commission_swap_and_fees_separate():
    ledger = ledger_for([close_event(gross=100.0, commission=-3.5, swap=-1.25, fees=-0.25)])
    assert ledger.gross_realised_pnl == 100.0
    assert ledger.commission == -3.5
    assert ledger.swap == -1.25
    assert ledger.fees == -0.25
    assert ledger.net_realised_pnl == 95.0
    assert ledger.is_complete


def test_ledger_net_pnl_is_exact_gross_plus_each_cost():
    ledger = ledger_for([close_event(gross=500.0, commission=-10.0, swap=-4.0, fees=-1.0)])
    assert ledger.net_realised_pnl == 500.0 - 10.0 - 4.0 - 1.0


def test_ledger_marks_an_unknown_cost_unknown_never_zero():
    ledger = ledger_for([close_event(gross=100.0, commission=None, swap=-1.0, fees=0.0)])
    assert ledger.commission is None
    assert "commission" in ledger.unknown_components
    assert "net_realised_pnl" in ledger.unknown_components
    assert ledger.net_realised_pnl is None
    assert ledger.is_complete is False


def test_ledger_one_unknown_event_poisons_the_day_component():
    """A single event with unknown swap makes the day's swap UNKNOWN, not zero."""
    ledger = ledger_for([
        close_event(source_trade_id="d1", gross=100.0, swap=-1.0),
        close_event(source_trade_id="d2", gross=50.0, swap=None),
    ])
    assert ledger.swap is None
    assert "swap" in ledger.unknown_components
    assert ledger.gross_realised_pnl == 150.0


def test_ledger_is_idempotent_for_a_duplicated_event():
    event = close_event()
    once = ledger_for([event])
    twice = ledger_for([event, event])
    assert once.trade_count == 1
    assert twice.trade_count == 1
    assert once.net_realised_pnl == twice.net_realised_pnl
    assert once.ledger_id == twice.ledger_id


def test_ledger_is_idempotent_across_two_independent_ingestion_passes():
    """Re-projecting the day twice from the same evidence is byte-identical."""
    events = [close_event(source_trade_id="d1", gross=10.0), close_event(source_trade_id="d2", gross=-4.0)]
    assert ledger_for(events).to_dict() == ledger_for(events).to_dict()


def test_same_ticket_on_two_accounts_stays_distinct():
    """The same ticket reported by two accounts must never be deduplicated."""
    event_a = close_event(account=ACCOUNT_A, source_trade_id="deal-1", ticket=777, gross=-100.0)
    event_b = close_event(account=ACCOUNT_B, source_trade_id="deal-1", ticket=777, gross=-100.0)
    assert event_a.event_id != event_b.event_id
    assert ledger_for([event_a, event_b], account=ACCOUNT_A).trade_count == 1
    assert ledger_for([event_a, event_b], account=ACCOUNT_B).trade_count == 1



def test_two_partial_closes_are_two_events_and_are_never_collapsed():
    first = close_event(
        source_trade_id="deal-p1", ticket=888, gross=50.0,
        close_kind=CloseEventKind.PARTIAL_CLOSE, volume=0.05,
    )
    second = close_event(
        source_trade_id="deal-p2", ticket=888, gross=30.0,
        close_kind=CloseEventKind.PARTIAL_CLOSE, volume=0.05,
    )
    assert first.event_id != second.event_id
    ledger = ledger_for([first, second])
    assert ledger.trade_count == 2
    assert ledger.partial_close_count == 2
    assert ledger.full_close_count == 0
    assert ledger.gross_realised_pnl == 80.0
    # Each partial close keeps its own source identity.
    assert len(set(ledger.source_trade_ids)) == 2


def test_mixed_full_and_partial_closes_are_counted_distinctly():
    ledger = ledger_for([
        close_event(source_trade_id="d1", gross=10.0, close_kind=CloseEventKind.FULL_CLOSE),
        close_event(source_trade_id="d2", gross=5.0, close_kind=CloseEventKind.PARTIAL_CLOSE),
    ])
    assert ledger.trade_count == 2
    assert ledger.full_close_count == 1
    assert ledger.partial_close_count == 1


def test_unspecified_close_kind_is_preserved_rather_than_assumed_full():
    ledger = ledger_for([close_event(close_kind=CloseEventKind.UNSPECIFIED)])
    assert ledger.trade_count == 1
    assert ledger.full_close_count == 0
    assert ledger.partial_close_count == 0


def test_late_arriving_trade_lands_on_its_own_rule_day_not_the_arrival_day():
    """A trade ingested two days late still belongs to the day it CLOSED."""
    late = close_event(source_trade_id="late-1", closed_at=datetime(2026, 3, 10, 14, tzinfo=UTC), gross=-75.0)
    early = close_event(source_trade_id="early-1", closed_at=datetime(2026, 3, 10, 13, tzinfo=UTC), gross=-25.0)
    assert ledger_for([early, late], rule_day=date(2026, 3, 10)).trade_count == 2
    assert ledger_for([early, late], rule_day=date(2026, 3, 11)).trade_count == 0


def test_late_arrival_recomputes_the_projection_deterministically():
    early = close_event(source_trade_id="early-1", gross=-25.0)
    before = ledger_for([early])
    late = close_event(source_trade_id="late-1", gross=-75.0)
    after = ledger_for([early, late])
    assert after.to_dict() == ledger_for([early, late]).to_dict()
    assert after.trade_count == before.trade_count + 1
    assert after.gross_realised_pnl == -100.0


def test_ledger_events_belong_to_the_rule_day_they_closed_in():
    """A 23:30 New York close is that day; a 00:30 close is the next."""
    late_evening = close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 10, 23, 30, tzinfo=UTC), gross=10.0)
    small_hours = close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 11, 4, 30, tzinfo=UTC), gross=20.0)
    # 23:30Z on the 10th is 19:30 New York; 04:30Z on the 11th is 00:30 New York.
    assert late_evening.closed_at_utc.astimezone(NY_MIDNIGHT.zone).date() == date(2026, 3, 10)
    assert small_hours.closed_at_utc.astimezone(NY_MIDNIGHT.zone).date() == date(2026, 3, 11)
    assert ledger_for([late_evening, small_hours], rule_day=date(2026, 3, 10)).trade_count == 1
    assert ledger_for([late_evening, small_hours], rule_day=date(2026, 3, 11)).trade_count == 1


def test_ledger_uses_closed_trade_evidence_not_a_balance_delta():
    """The ledger is built from close events; no balance snapshot is consulted."""
    events = [close_event(source_trade_id="d1", gross=-120.0), close_event(source_trade_id="d2", gross=45.0)]
    ledger = ledger_for(events)
    assert ledger.gross_realised_pnl == -75.0
    assert ledger.trade_count == 2
    assert set(ledger.symbols) == {"EURUSD"}


def test_conflicting_duplicate_event_fails_closed():
    store = PropRuleStateStore()
    store.record_close_event(close_event(source_trade_id="d1", gross=100.0))
    with pytest.raises(ConflictingDuplicateEvent):
        store.record_close_event(close_event(source_trade_id="d1", gross=999.0))


def test_close_event_rejects_a_tampered_event_id():
    with pytest.raises(PropRuleStateError):
        close_event(event_id="cte_forged")



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 17-20  TRADING DAY HISTORY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def history_from(events, definition=NY_MIDNIGHT, account=ACCOUNT_A):
    return build_trading_day_history(
        account=account, account_currency="USD", definition=definition, events=events
    )


def test_trading_day_record_qualifies_on_any_closed_trade():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event()],
    )
    assert record.has_closed_trade
    assert record.qualifies(TradingDayCriterion.ANY_CLOSED_TRADE) is True
    assert record.qualifies(TradingDayCriterion.UNSPECIFIED) is None


def test_non_qualifying_trade_day_is_recorded_as_no_trade():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 12), events=[],
    )
    assert record.has_closed_trade is False
    assert record.qualifies(TradingDayCriterion.ANY_CLOSED_TRADE) is False


def test_minimum_closed_trades_criterion():
    events = [
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 10, 13, tzinfo=UTC)),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 10, 14, tzinfo=UTC)),
    ]
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=events,
    )
    assert record.qualifies(TradingDayCriterion.MIN_CLOSED_TRADES, minimum_closed_trades=2) is True
    assert record.qualifies(TradingDayCriterion.MIN_CLOSED_TRADES, minimum_closed_trades=3) is False
    # A threshold the rule never stated stays indeterminate.
    assert record.qualifies(TradingDayCriterion.MIN_CLOSED_TRADES) is None


def test_minimum_volume_criterion_uses_closed_volume():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event(volume=0.5)],
    )
    assert record.total_volume == 0.5
    assert record.qualifies(TradingDayCriterion.MIN_TOTAL_VOLUME, minimum_volume=0.3) is True
    assert record.qualifies(TradingDayCriterion.MIN_TOTAL_VOLUME, minimum_volume=1.0) is False


def test_minimum_duration_criterion_uses_closed_duration():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event(duration=1200.0)],
    )
    assert record.qualifies(TradingDayCriterion.MIN_TOTAL_DURATION, minimum_duration_seconds=600.0) is True
    assert record.qualifies(TradingDayCriterion.MIN_TOTAL_DURATION, minimum_duration_seconds=3600.0) is False


def test_minimum_profit_criterion_requires_known_net_pnl():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event(gross=100.0, commission=-2.0, swap=-1.0, fees=0.0)],
    )
    assert record.net_realised_pnl == 97.0
    assert record.qualifies(TradingDayCriterion.MIN_REALISED_PNL, minimum_pnl=50.0) is True
    assert record.qualifies(TradingDayCriterion.MIN_REALISED_PNL, minimum_pnl=200.0) is False


def test_unspecified_criterion_never_counts_as_qualifying():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event()],
    )
    assert record.qualifies(TradingDayCriterion.UNSPECIFIED) is None
    assert resolve_criterion(None) is TradingDayCriterion.UNSPECIFIED
    assert resolve_criterion("SOMETHING_UNRECOGNISED") is TradingDayCriterion.UNSPECIFIED


def test_criterion_aliases_map_onto_the_explicit_vocabulary():
    assert resolve_criterion("ANY_CLOSED_TRADE") is TradingDayCriterion.ANY_CLOSED_TRADE
    assert resolve_criterion("any closed trade") is TradingDayCriterion.ANY_CLOSED_TRADE
    assert resolve_criterion("MIN_LOT") is TradingDayCriterion.MIN_TOTAL_VOLUME
    assert resolve_criterion("MIN_PROFIT") is TradingDayCriterion.MIN_REALISED_PNL



def test_trading_day_history_counts_qualifying_days_only():
    events = [
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC)),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 10, 14, tzinfo=UTC)),
        close_event(source_trade_id="d3", closed_at=datetime(2026, 3, 11, 14, tzinfo=UTC)),
    ]
    history = history_from(events)
    assert history.observed_days == (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11))
    assert history.count_qualifying(TradingDayCriterion.ANY_CLOSED_TRADE) == 3
    # "Up to" a rule day excludes later evidence: no lookahead.
    assert history.count_qualifying(
        TradingDayCriterion.ANY_CLOSED_TRADE, up_to_rule_day=date(2026, 3, 10)
    ) == 2
    assert history.last_qualifying_day(TradingDayCriterion.ANY_CLOSED_TRADE) == date(2026, 3, 11)


def test_trading_day_history_is_account_isolated():
    events_a = [close_event(account=ACCOUNT_A, source_trade_id="d1")]
    events_b = [close_event(account=ACCOUNT_B, source_trade_id="d1", ticket=999)]
    assert history_from(events_a, account=ACCOUNT_A).observed_days == (date(2026, 3, 10),)
    assert history_from(events_b, account=ACCOUNT_B).observed_days == (date(2026, 3, 10),)
    assert history_from(events_a, account=ACCOUNT_B).observed_days == ()


def test_trading_day_history_rejects_a_conflicting_duplicate_day():
    record = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event()],
    )
    history = history_from([close_event()])
    # Re-adding the IDENTICAL record is idempotent.
    assert history.with_record(record) is history
    other = project_trading_day_record(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT,
        rule_day=date(2026, 3, 10), events=[close_event(gross=-999.0)],
    )
    with pytest.raises(TradingDayRecordConflict):
        history.with_record(other)


def test_daily_profit_series_omits_days_with_unknown_net_pnl():
    events = [
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC), gross=100.0, swap=None),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 10, 14, tzinfo=UTC), gross=50.0),
    ]
    series = history_from(events).daily_profit_series()
    # The 9th is omitted (unknown net), not assumed to be zero.
    assert series == ((date(2026, 3, 10), 50.0 - 4.0),)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# INACTIVITY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_inactivity_on_a_calendar_basis_counts_rule_days():
    events = [close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC))]
    history = history_from(events)
    state = derive_inactivity_state(
        account=ACCOUNT_A, account_currency="USD", history=history, definition=NY_MIDNIGHT,
        current_rule_day=date(2026, 3, 12), criterion=TradingDayCriterion.ANY_CLOSED_TRADE,
        basis="CALENDAR_DAYS",
    )
    assert state.is_evaluable
    assert state.last_qualifying_day == date(2026, 3, 9)
    assert state.inactivity_days == 3


def test_inactivity_on_a_trading_day_basis_walks_recorded_days_only():
    events = [close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC))]
    history = history_from(events)
    state = derive_inactivity_state(
        account=ACCOUNT_A, account_currency="USD", history=history, definition=NY_MIDNIGHT,
        current_rule_day=date(2026, 3, 12), criterion=TradingDayCriterion.ANY_CLOSED_TRADE,
        basis="TRADING_DAYS",
    )
    # Only RECORDED days are walked, so no calendar gap is invented.
    assert state.basis == "TRADING_DAYS"
    assert state.inactivity_days == 0


def test_inactivity_without_a_declared_basis_is_indeterminate():
    history = history_from([close_event()])
    state = derive_inactivity_state(
        account=ACCOUNT_A, account_currency="USD", history=history, definition=NY_MIDNIGHT,
        current_rule_day=date(2026, 3, 12), criterion=TradingDayCriterion.ANY_CLOSED_TRADE,
        basis=None,
    )
    assert state.is_evaluable is False
    assert state.inactivity_days is None
    assert state.detail == "INACTIVITY_BASIS_NOT_DECLARED"


def test_inactivity_with_an_unspecified_criterion_is_indeterminate():
    history = history_from([close_event()])
    state = derive_inactivity_state(
        account=ACCOUNT_A, account_currency="USD", history=history, definition=NY_MIDNIGHT,
        current_rule_day=date(2026, 3, 12), criterion=TradingDayCriterion.UNSPECIFIED,
        basis="CALENDAR_DAYS",
    )
    assert state.is_evaluable is False
    assert state.inactivity_days is None


def test_inactive_window_parsing_is_exact_or_unknown():
    assert parse_inactive_days("3d") == 3
    assert parse_inactive_days("2 DAYS") == 2
    assert parse_inactive_days("1W") == 7
    assert parse_inactive_days(None) is None
    assert parse_inactive_days("") is None
    assert parse_inactive_days("soon") is None
    assert parse_inactive_days("3 fortnights") is None



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 21  HISTORICAL RULE PACK SELECTION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def versioned_packs():
    """Two versions of one identity: v1 through March, v2 from April."""
    from core.risk.prop_rule_examples import build_pack_a, pack_a_identity

    store = RulePackStore()
    v1 = build_pack_a(
        pack_a_identity(
            version="1.0.0",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            effective_to=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )
    v2 = build_pack_a(
        pack_a_identity(version="2.0.0", effective_from=datetime(2026, 4, 1, tzinfo=UTC))
    )
    store.register_rule_pack(v1)
    store.register_rule_pack(v2)
    return store, v1, v2


SELECT = ("SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1, 100_000)


def test_historical_pack_selection_binds_the_pack_in_force_at_the_time():
    store, v1, v2 = versioned_packs()
    before = rules_effective_at(store, *SELECT, datetime(2026, 3, 15, tzinfo=UTC))
    after = rules_effective_at(store, *SELECT, datetime(2026, 5, 15, tzinfo=UTC))
    assert before.rule_pack_id == v1.rule_pack_id
    assert after.rule_pack_id == v2.rule_pack_id


def test_historical_pack_selection_fails_closed_when_no_pack_is_in_force():
    store, _v1, _v2 = versioned_packs()
    with pytest.raises(NoRulePackInForce):
        rules_effective_at(store, *SELECT, datetime(2025, 6, 1, tzinfo=UTC))


def test_historical_pack_selection_fails_closed_on_an_ambiguous_window():
    """Two overlapping packs in force at once is a governance defect."""
    from core.risk.prop_rule_examples import build_pack_a, pack_a_identity

    store = RulePackStore()
    store.register_rule_pack(
        build_pack_a(pack_a_identity(version="1.0.0", effective_from=datetime(2026, 1, 1, tzinfo=UTC)))
    )
    store.register_rule_pack(
        build_pack_a(
            pack_a_identity(
                version="1.1.0",
                effective_from=datetime(2026, 2, 1, tzinfo=UTC),
                effective_to=datetime(2026, 3, 1, tzinfo=UTC),
            )
        )
    )
    with pytest.raises(AmbiguousRulePackInForce):
        rules_effective_at(store, *SELECT, datetime(2026, 2, 15, tzinfo=UTC))


def test_historical_pack_selection_fails_closed_on_an_unrelated_identity():
    store, _v1, _v2 = versioned_packs()
    with pytest.raises(NoRulePackInForce):
        rules_effective_at(
            store, "SOME_OTHER_FIRM", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
            100_000, datetime(2026, 3, 15, tzinfo=UTC),
        )


def test_historical_pack_selection_requires_a_store_and_an_aware_instant():
    store, _v1, _v2 = versioned_packs()
    with pytest.raises(PropRuleStateError):
        rules_effective_at(object(), *SELECT, datetime(2026, 3, 15, tzinfo=UTC))
    with pytest.raises(PropRuleStateError):
        rules_effective_at(store, *SELECT, datetime(2026, 3, 15))  # naive


def test_rule_pack_timeline_orders_versions_oldest_first():
    store, v1, v2 = versioned_packs()
    timeline = rule_pack_timeline(store, *SELECT)
    assert [pack.rule_pack_id for _f, _t, pack in timeline] == [v1.rule_pack_id, v2.rule_pack_id]



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 22  DURABILITY AND RESTART RECONSTRUCTION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_state_store_declares_its_five_operational_families():
    assert STATE_FAMILIES == (
        "initial_anchor", "daily_anchor", "trade_event", "trading_day", "high_water",
    )


def test_state_restart_reconstructs_every_family_exactly(tmp_path):
    base = tmp_path / "prop_rule_state"
    store = PropRuleStateStore(base)
    store.record_initial_anchor(initial_anchor(balance=100_000.0))
    store.record_daily_anchor(daily_anchor(balance=100_000.0))
    store.record_close_event(close_event(source_trade_id="d1", gross=-120.0))
    store.record_close_event(
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 11, 14, tzinfo=UTC), gross=80.0)
    )
    store.record_high_water_observation(
        account=ACCOUNT_A, account_currency="USD", balance=100_000.0, equity=101_000.0,
        observed_at_utc=datetime(2026, 3, 10, 14, tzinfo=UTC), source_snapshot_id="asnap_1",
    )
    store.record_high_water_observation(
        account=ACCOUNT_A, account_currency="USD", balance=100_200.0, equity=102_000.0,
        observed_at_utc=datetime(2026, 3, 10, 15, tzinfo=UTC), source_snapshot_id="asnap_2",
    )

    # A brand-new store reading ONLY the durable records.
    restarted = PropRuleStateStore(base)

    # 1. Initial anchor unchanged.
    assert restarted.initial_anchor_for(ACCOUNT_A).initial_balance == 100_000.0
    assert restarted.initial_anchor_for(ACCOUNT_A).anchor_id == initial_anchor().anchor_id
    # 2. Latest daily anchor restored.
    assert restarted.daily_anchor_for(ACCOUNT_A, "America/New_York", date(2026, 3, 10)) is not None
    # 3. High-water restored.
    high_water = restarted.high_water_for(ACCOUNT_A, "USD")
    assert high_water.high_water_balance == 100_200.0
    assert high_water.high_water_equity == 102_000.0
    # 4. Daily ledger deduplicated and identical.
    ledger = restarted.daily_ledger_for(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10)
    )
    assert ledger.trade_count == 1
    assert ledger.gross_realised_pnl == -120.0
    # 5. Trading days restored.
    history = restarted.trading_day_history_for(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT
    )
    assert history.observed_days == (date(2026, 3, 10), date(2026, 3, 11))


def test_restart_does_not_depend_on_process_memory(tmp_path):
    """A fresh store with no prior in-memory state matches the projection."""
    base = tmp_path / "prop_rule_state"
    first = PropRuleStateStore(base)
    first.record_initial_anchor(initial_anchor())
    for index, gross in enumerate((-10.0, -20.0, 30.0), start=1):
        first.record_close_event(
            close_event(source_trade_id=f"d{index}", gross=gross,
                        closed_at=datetime(2026, 3, 10, 12 + index, tzinfo=UTC))
        )
    before = first.daily_ledger_for(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10)
    )
    second = PropRuleStateStore(base)
    after = second.daily_ledger_for(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10)
    )
    assert after.to_dict() == before.to_dict()
    assert after.ledger_id == before.ledger_id


def test_store_rebuild_detects_a_persisted_anchor_conflict(tmp_path):
    """A conflict written straight to the log is caught on reconstruction."""
    import json as _json

    base = tmp_path / "prop_rule_state"
    store = PropRuleStateStore(base)
    store.record_initial_anchor(initial_anchor(balance=100_000.0))
    conflicting = initial_anchor(balance=50_000.0, snapshot_id="asnap_bad")
    with store._path("initial_anchor", ACCOUNT_A).open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps(conflicting.to_dict(), sort_keys=True) + "\n")
    with pytest.raises(InitialAnchorConflict):
        PropRuleStateStore(base)



def test_multi_account_state_is_isolated_in_one_store(tmp_path):
    base = tmp_path / "prop_rule_state"
    store = PropRuleStateStore(base)
    store.record_initial_anchor(initial_anchor(account=ACCOUNT_A, balance=100_000.0))
    store.record_initial_anchor(initial_anchor(account=ACCOUNT_B, balance=50_000.0))
    store.record_close_event(close_event(account=ACCOUNT_A, source_trade_id="d1", gross=-500.0))
    store.record_close_event(close_event(account=ACCOUNT_B, source_trade_id="d1", gross=-100.0))
    restarted = PropRuleStateStore(base)
    assert restarted.initial_anchor_for(ACCOUNT_A).initial_balance == 100_000.0
    assert restarted.initial_anchor_for(ACCOUNT_B).initial_balance == 50_000.0
    ledger_a = restarted.daily_ledger_for(
        account=ACCOUNT_A, account_currency="USD", definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10)
    )
    ledger_b = restarted.daily_ledger_for(
        account=ACCOUNT_B, account_currency="USD", definition=NY_MIDNIGHT, rule_day=date(2026, 3, 10)
    )
    assert ledger_a.gross_realised_pnl == -500.0
    assert ledger_b.gross_realised_pnl == -100.0


def test_cross_currency_accounts_never_share_state(tmp_path):
    usd = AccountKey("ACC_USD", "MT5", "DemoBroker-Server", 333333)
    eur = AccountKey("ACC_EUR", "MT5", "DemoBroker-Server", 444444)
    base = tmp_path / "prop_rule_state"
    store = PropRuleStateStore(base)
    store.record_initial_anchor(initial_anchor(account=usd, balance=100_000.0))
    store.record_close_event(close_event(account=usd, source_trade_id="d1", gross=-42.0))
    store.record_initial_anchor(
        InitialAccountAnchor(
            account=eur, account_currency="EUR", initial_balance=90_000.0, initial_equity=90_000.0,
            observed_at_utc=datetime(2026, 3, 2, tzinfo=UTC),
            source_snapshot_id="asnap_eur", source_provenance="BLOCK_2A_ACCOUNT_SNAPSHOT",
        )
    )
    restarted = PropRuleStateStore(base)
    assert restarted.initial_anchor_for(usd).account_currency == "USD"
    assert restarted.initial_anchor_for(eur).account_currency == "EUR"
    assert restarted.initial_anchor_for(eur).initial_balance == 90_000.0
    assert store.close_events_for(eur) == ()


def test_state_lineage_is_stable_and_content_sensitive(tmp_path):
    store = PropRuleStateStore(tmp_path / "s")
    before = store.state_lineage(ACCOUNT_A)
    assert before["initial_anchor_id"] is None and before["event_count"] == 0
    store.record_initial_anchor(initial_anchor())
    store.record_close_event(close_event(source_trade_id="d1", gross=-10.0))
    after = store.state_lineage(ACCOUNT_A)
    assert after != before
    assert after["event_count"] == 1
    assert store.state_lineage(ACCOUNT_A) == after


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# NO WALL CLOCK, NO BROKER, NO RUNTIME DEPENDENCY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _code_lines(module) -> list[str]:
    """Source lines with comments and docstrings removed.

    The purity assertions below are about CODE. Scanning prose would make a
    module fail merely for EXPLAINING that it reads no wall clock.
    """
    import io
    import tokenize

    source = Path(module.__file__).read_text(encoding="utf-8")
    out: list[str] = []
    prev_type = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            # A docstring is an expression statement right after an indent/newline.
            is_docstring = token.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, tokenize.DEDENT,
            )
            if token.type == tokenize.COMMENT or is_docstring:
                out.append("")
                prev_type = token.type
                continue
        if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(token.string)
            prev_type = token.type
    return out


def test_state_module_reads_no_wall_clock_and_imports_no_broker():
    import core.risk.prop_rule_state as module

    code = "\n".join(_code_lines(module))
    for forbidden in ("datetime.now(", "time.time(", "date.today(", "MetaTrader5", "mt5_call"):
        assert forbidden not in code, f"state module must not use {forbidden}"


def test_state_module_imports_no_guard_or_execution_surface():
    import core.risk.prop_rule_state as module

    code = "\n".join(_code_lines(module))
    for forbidden in (
        "import kill_switch", "runtime_guard", "order_send", "positions_get",
        "daily_loss_guard", "drawdown_guard", "core.config", "config.",
    ):
        assert forbidden not in code, f"state module must not reference {forbidden}"
