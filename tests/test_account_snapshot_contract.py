"""Account-safe prop-risk telemetry — account snapshot contract (Block 2A).

Covers the canonical account snapshot contract end to end: exact identity,
schema, value semantics, fail-closed quality, status contract, freshness,
deterministic identity, multi-account isolation, MT5 adapter boundary, durable
canonical persistence, consumer lookup, restart behaviour and observability.

All clocks are injected. There are no sleeps and no live broker access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.canonical_delivery import configure_delivery_outbox
from core.canonical_delivery_outbox import DeliveryState
from core.lifecycle_evidence_obligations import EXACT_IDENTITY_FIELDS
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    is_symbol_scoped,
)
from core.risk.account_snapshot import (
    DATASET,
    SCHEMA_VERSION,
    AccountIdentity,
    AccountIdentityError,
    AccountSnapshot,
    AccountSnapshotNotFound,
    AccountSnapshotProducer,
    AccountSnapshotStatus,
    AccountSnapshotStore,
    AccountSourceUnavailable,
    capture_account_snapshot,
    derive_snapshot_id,
    freshness_threshold_ms,
    persist_account_snapshot,
    snapshot_interval_ms,
    to_epoch_ms,
)


# ═══════════════════════════════════════════════════════════════════════════
# TEST DOUBLES — no live MT5, no sleeps, injected clocks only
# ═══════════════════════════════════════════════════════════════════════════


class FixedClock:
    """Injected monotonic-in-tests clock. No sleeping, no wall-clock reads."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


@dataclass
class FakeAccountInfoSource:
    """Injected stand-in for the MT5 account_info() boundary."""

    payload: dict | None = None
    error: Exception | None = None

    def read_account_info(self) -> dict | None:
        if self.error is not None:
            raise self.error
        return None if self.payload is None else dict(self.payload)


T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0_MS = to_epoch_ms(T0)

ACCOUNT_A = AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 5012345)
ACCOUNT_B = AccountIdentity("ADMIRALS", "Admirals", "Admirals-Server", 9988776)


def _info(account: AccountIdentity, **overrides) -> dict:
    """A complete, internally consistent MT5 account_info payload."""
    balance = overrides.pop("balance", 10_000.0)
    profit = overrides.pop("profit", -150.0)
    credit = overrides.pop("credit", 0.0)
    payload = {
        "login": account.login,
        "server": account.server,
        "currency": "GBP",
        "leverage": 100,
        "balance": balance,
        "equity": balance + credit + profit,
        "credit": credit,
        "profit": profit,
        "margin": 0.0,
        "margin_free": balance + credit + profit,
        "margin_level": 0.0,
        "trade_allowed": True,
        "trade_expert": True,
        "trade_mode": 0,
        "margin_mode": 0,
        "stopout_mode": 0,
        "stopout_call": 50.0,
        "stopout_so": 30.0,
    }
    payload.update(overrides)
    return payload


def _capture(account, source, clock=None, source_name="MT5_ACCOUNT_INFO"):
    return capture_account_snapshot(
        account, source, clock=clock or FixedClock(T0), source_name=source_name)


@pytest.fixture
def isolated_outbox(tmp_path):
    """Isolate the process-wide canonical outbox for each test."""
    configure_delivery_outbox(tmp_path / "process_outbox.sqlite3")
    try:
        yield
    finally:
        configure_delivery_outbox(None)


# ═══════════════════════════════════════════════════════════════════════════
# 1–2. VALID SNAPSHOT + EXACT ACCOUNT IDENTITY
# ═══════════════════════════════════════════════════════════════════════════


def test_valid_full_account_snapshot_is_complete_with_exact_values():
    source = FakeAccountInfoSource(_info(ACCOUNT_A))
    snapshot = _capture(ACCOUNT_A, source)

    assert snapshot.status is AccountSnapshotStatus.COMPLETE
    assert snapshot.invalid_fields == ()
    assert snapshot.source_error is None
    assert snapshot.balance == 10_000.0
    assert snapshot.equity == 9_850.0
    assert snapshot.floating_pnl == -150.0
    assert snapshot.floating_pnl_source == "MT5_ACCOUNT_INFO_PROFIT"
    assert snapshot.margin == 0.0
    assert snapshot.free_margin == 9_850.0
    assert snapshot.margin_level == 0.0
    assert snapshot.leverage == 100
    assert snapshot.currency == "GBP"
    assert snapshot.trade_allowed is True
    assert snapshot.trade_expert is True
    assert snapshot.stopout_call == 50.0
    assert snapshot.stopout_so == 30.0


def test_snapshot_identity_is_broker_server_login_namespaced():
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))

    assert snapshot.account_id == "METAQUOTES"
    assert snapshot.broker == "MetaQuotes"
    assert snapshot.server == "MetaQuotes-Demo"
    assert snapshot.login == 5_012_345
    assert snapshot.identity.identity == (
        "METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 5_012_345)


def test_identity_rejects_symbol_broker_or_login_only_identification():
    with pytest.raises(AccountIdentityError):
        AccountIdentity("METAQUOTES", "MetaQuotes", "", 5012345)   # no server
    with pytest.raises(AccountIdentityError):
        AccountIdentity("METAQUOTES", "", "MetaQuotes-Demo", 5012345)  # no broker
    with pytest.raises(AccountIdentityError):
        AccountIdentity("", "MetaQuotes", "MetaQuotes-Demo", 5012345)   # no id
    with pytest.raises(AccountIdentityError):
        AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 0)
    with pytest.raises(AccountIdentityError):
        AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", "5012345")


def test_identity_matches_canonical_account_config_contract():
    from core.accounts.config import AccountConfig

    config = AccountConfig(
        account_id="METAQUOTES", broker="MetaQuotes",
        server="MetaQuotes-Demo", login=5012345,
    )
    assert AccountIdentity.from_account_config(config).identity == config.identity


def test_snapshot_from_a_different_account_is_never_attributed(tmp_path):
    """A read belonging to another login can never satisfy this account."""
    other = FakeAccountInfoSource(_info(ACCOUNT_B))
    snapshot = _capture(ACCOUNT_A, other)

    assert snapshot.status is AccountSnapshotStatus.UNAVAILABLE
    assert snapshot.source_error == "ACCOUNT_INFO_IDENTITY_MISMATCH"
    assert snapshot.balance is None
    assert snapshot.account_id == "METAQUOTES"


# ═══════════════════════════════════════════════════════════════════════════
# 5–16. FAIL-CLOSED DATA QUALITY, VALUE SEMANTICS, CURRENCY
# ═══════════════════════════════════════════════════════════════════════════


def test_missing_account_info_is_unavailable_not_zero():
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(None))

    assert snapshot.status is AccountSnapshotStatus.UNAVAILABLE
    assert snapshot.source_error == "ACCOUNT_INFO_UNAVAILABLE"
    assert snapshot.balance is None
    assert snapshot.equity is None
    assert "equity" in snapshot.unavailable_fields


def test_connection_failure_is_explicitly_unavailable():
    source = FakeAccountInfoSource(
        error=AccountSourceUnavailable("MT5_ACCOUNT_INFO_FAILED:TimeoutError"))
    snapshot = _capture(ACCOUNT_A, source)

    assert snapshot.status is AccountSnapshotStatus.UNAVAILABLE
    assert snapshot.source_error == "MT5_ACCOUNT_INFO_FAILED:TimeoutError"
    assert snapshot.balance is None


def test_legitimate_zero_balance_and_zero_equity_are_preserved():
    source = FakeAccountInfoSource(
        _info(ACCOUNT_A, balance=0.0, profit=0.0, equity=0.0, margin_free=0.0))
    snapshot = _capture(ACCOUNT_A, source)

    assert snapshot.status is AccountSnapshotStatus.COMPLETE
    assert snapshot.balance == 0.0
    assert snapshot.equity == 0.0
    assert snapshot.floating_pnl == 0.0
    assert "balance" not in snapshot.unavailable_fields


def test_unavailable_equity_is_never_substituted_with_zero():
    payload = _info(ACCOUNT_A)
    payload["equity"] = None
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.equity is None
    assert "equity" in snapshot.unavailable_fields
    assert snapshot.status is AccountSnapshotStatus.PARTIAL


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinite_equity_are_rejected_as_invalid(bad):
    payload = _info(ACCOUNT_A)
    payload["equity"] = bad
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.equity is None
    assert "equity" in snapshot.invalid_fields
    assert snapshot.status is AccountSnapshotStatus.INVALID
    assert snapshot.balance == 10_000.0  # unaffected fields survive


def test_non_numeric_balance_is_invalid_not_coerced():
    payload = _info(ACCOUNT_A)
    payload["balance"] = "10000"
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.balance is None
    assert "balance" in snapshot.invalid_fields
    assert snapshot.status is AccountSnapshotStatus.INVALID


def test_negative_and_positive_floating_pnl_are_preserved_exactly():
    negative = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, profit=-275.5)))
    positive = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, profit=412.25)))

    assert negative.floating_pnl == -275.5
    assert negative.equity == 10_000.0 - 275.5
    assert positive.floating_pnl == 412.25
    assert positive.equity == 10_000.0 + 412.25
    assert negative.floating_pnl_source == "MT5_ACCOUNT_INFO_PROFIT"
    assert positive.floating_pnl_source == "MT5_ACCOUNT_INFO_PROFIT"


def test_floating_pnl_uses_credit_aware_derivation_only_when_exact():
    payload = _info(ACCOUNT_A)
    del payload["profit"]
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.floating_pnl == -150.0
    assert snapshot.floating_pnl_source == (
        "DERIVED_EQUITY_MINUS_BALANCE_MINUS_CREDIT")


def test_floating_pnl_is_unavailable_when_derivation_would_be_inexact():
    payload = _info(ACCOUNT_A)
    del payload["profit"]
    payload["credit"] = None  # equity - balance is then NOT exact
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.floating_pnl is None
    assert "floating_pnl" in snapshot.unavailable_fields
    assert snapshot.floating_pnl_source == "UNAVAILABLE"


def test_zero_margin_with_no_open_positions_is_preserved():
    snapshot = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, margin=0.0)))

    assert snapshot.margin == 0.0
    assert "margin" not in snapshot.unavailable_fields
    assert snapshot.status is AccountSnapshotStatus.COMPLETE


def test_margin_level_and_leverage_unavailable_are_explicit():
    payload = _info(ACCOUNT_A)
    payload["margin_level"] = None
    payload["leverage"] = None
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.margin_level is None
    assert snapshot.leverage is None
    assert "margin_level" in snapshot.unavailable_fields
    assert "leverage" in snapshot.unavailable_fields
    # Margin/leverage qualify independently; they are not required for COMPLETE.
    assert snapshot.status is AccountSnapshotStatus.COMPLETE


def test_missing_currency_prevents_complete_status():
    payload = _info(ACCOUNT_A)
    payload["currency"] = None
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.currency is None
    assert "currency" in snapshot.unavailable_fields
    assert snapshot.status is AccountSnapshotStatus.PARTIAL
    assert snapshot.missing_required_fields() == ("currency",)


def test_currency_is_preserved_verbatim_and_never_converted():
    payload = _info(ACCOUNT_A)
    payload["currency"] = "JPY"
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.currency == "JPY"
    assert snapshot.balance == 10_000.0  # value untouched, no FX applied


def test_trade_permissions_false_are_preserved_not_treated_as_unavailable():
    payload = _info(ACCOUNT_A, trade_allowed=False, trade_expert=False)
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.trade_allowed is False
    assert snapshot.trade_expert is False
    assert "trade_allowed" not in snapshot.unavailable_fields
    assert snapshot.status is AccountSnapshotStatus.COMPLETE


def test_numeric_precision_is_preserved_without_aggressive_rounding():
    payload = _info(ACCOUNT_A, balance=10_000.123456789, profit=-0.000123456)
    payload["equity"] = 10_000.123456789 - 0.000123456
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.balance == 10_000.123456789
    assert snapshot.floating_pnl == -0.000123456


# ═══════════════════════════════════════════════════════════════════════════
# 16. POSITION / FLOATING P&L CONSISTENCY CHECK
# ═══════════════════════════════════════════════════════════════════════════


def test_tiny_float_rounding_does_not_fail_the_consistency_check():
    payload = _info(ACCOUNT_A, balance=10_000.0, profit=-150.0)
    payload["equity"] = 9_850.0 + 1e-9  # sub-cent broker rounding
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.consistency_ok is True
    assert snapshot.status is AccountSnapshotStatus.COMPLETE


def test_obvious_equity_contradiction_degrades_to_invalid_without_correction():
    payload = _info(ACCOUNT_A, balance=10_000.0, profit=-150.0)
    payload["equity"] = 42.0  # impossible under equity = balance + credit + profit
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))

    assert snapshot.consistency_ok is False
    assert snapshot.status is AccountSnapshotStatus.INVALID
    assert "equity_identity_consistency" in snapshot.invalid_fields
    assert snapshot.equity == 42.0  # reported value preserved, never "fixed"
    assert "EQUITY_IDENTITY_CONTRADICTION" in snapshot.consistency_detail


# ═══════════════════════════════════════════════════════════════════════════
# 7–8. TIMESTAMP / FRESHNESS AND DETERMINISTIC IDENTITY
# ═══════════════════════════════════════════════════════════════════════════


def test_observation_timestamp_is_utc_and_never_persistence_time():
    clock = FixedClock(T0)
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)),
                        clock=clock)

    assert snapshot.observed_at_utc == "2026-10-02T12:00:00+00:00"
    assert snapshot.observed_at_utc_ms == T0_MS
    # A later persistence moment must not rewrite the observation instant.
    assert snapshot.observed_at_utc_ms != to_epoch_ms(clock.advance(hours=3))


def test_naive_observation_time_is_rejected():
    from core.risk.account_snapshot import to_epoch_ms as _ms

    with pytest.raises(ValueError):
        _ms(datetime(2026, 10, 2, 12, 0, 0))


def test_fresh_snapshot_stays_complete_and_aged_snapshot_becomes_stale():
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))

    fresh = snapshot.evaluate(now_ms=T0_MS + 5_000, threshold_ms=60_000)
    assert fresh.status is AccountSnapshotStatus.COMPLETE
    assert fresh.age_ms(now_ms=T0_MS + 5_000) == 5_000

    aged = snapshot.evaluate(now_ms=T0_MS + 120_000, threshold_ms=60_000)
    assert aged.status is AccountSnapshotStatus.STALE
    assert aged.age_ms(now_ms=T0_MS + 120_000) == 120_000


def test_freshness_threshold_is_configurable_and_never_hard_coded():
    assert freshness_threshold_ms({}) == 60_000
    assert freshness_threshold_ms({"ACCOUNT_SNAPSHOT_FRESHNESS_SECONDS": "5"}) == 5_000
    # Invalid configuration falls back to the documented default.
    assert freshness_threshold_ms({"ACCOUNT_SNAPSHOT_FRESHNESS_SECONDS": "abc"}) == 60_000
    assert freshness_threshold_ms({"ACCOUNT_SNAPSHOT_FRESHNESS_SECONDS": "-3"}) == 60_000

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    tight = snapshot.evaluate(now_ms=T0_MS + 30_000, threshold_ms=5_000)
    assert tight.status is AccountSnapshotStatus.STALE


def test_snapshot_identity_is_deterministic_for_the_same_observation():
    first = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    replay = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))

    assert first.snapshot_id == replay.snapshot_id
    assert first.snapshot_id == derive_snapshot_id(
        ACCOUNT_A, T0_MS, "MT5_ACCOUNT_INFO")


def test_different_observation_timestamps_create_distinct_snapshots():
    early = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    later = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)),
        clock=FixedClock(T0 + timedelta(seconds=1)))

    assert early.snapshot_id != later.snapshot_id
    assert early.observed_at_utc_ms != later.observed_at_utc_ms


def test_snapshot_identity_never_collides_across_accounts():
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    b = _capture(ACCOUNT_B, FakeAccountInfoSource(_info(ACCOUNT_B, currency="USD")))

    assert a.snapshot_id != b.snapshot_id


def test_snapshot_identity_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        derive_snapshot_id(ACCOUNT_A, 0, "MT5_ACCOUNT_INFO")
    with pytest.raises(ValueError):
        derive_snapshot_id(ACCOUNT_A, T0_MS, "")
    with pytest.raises(ValueError):
        derive_snapshot_id(ACCOUNT_A, float(T0_MS), "MT5_ACCOUNT_INFO")


def test_snapshot_id_does_not_depend_on_monetary_values():
    """Balances must never be part of a deterministic identity."""
    first = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, balance=10_000.0)))
    same_money = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, balance=10_000.0)))
    other_money = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A, balance=99_999.0)))

    assert first.snapshot_id == same_money.snapshot_id
    assert first.snapshot_id == other_money.snapshot_id


# ═══════════════════════════════════════════════════════════════════════════
# 3–4, 9, 21–22. MULTI-ACCOUNT ISOLATION
# ═══════════════════════════════════════════════════════════════════════════

SHARED_CONTEXT = {
    "symbol": "EURUSD", "strategy": "M5_TREND", "correlation_id": "COR-1",
    "trade_id": "trade_METAQUOTES_shared",
}


def test_accounts_sharing_symbol_strategy_and_correlation_remain_isolated():
    """A and B share every non-identity key; only identity may separate them."""
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(
        _info(ACCOUNT_A, balance=10_000.0, profit=-150.0, currency="GBP")))
    b = _capture(ACCOUNT_B, FakeAccountInfoSource(
        _info(ACCOUNT_B, balance=25_000.0, profit=300.0, currency="USD")))

    # The two observations are produced from an identical trading context.
    assert SHARED_CONTEXT["symbol"] == "EURUSD"
    assert SHARED_CONTEXT["correlation_id"] == "COR-1"
    assert a.snapshot_id != b.snapshot_id
    assert a.balance != b.balance
    assert a.equity != b.equity
    assert a.currency == "GBP" and b.currency == "USD"
    assert a.account_id != b.account_id
    # Neither snapshot carries the other's identity in any field.
    for snapshot in (a, b):
        others = ACCOUNT_B if snapshot is a else ACCOUNT_A
        assert others.account_id not in snapshot.to_dict().values()
        assert others.login != snapshot.login


def test_account_a_remains_valid_when_account_b_fails():
    """A COMPLETE / B UNAVAILABLE — A is unaffected and independently valid."""
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    b = _capture(ACCOUNT_B, FakeAccountInfoSource(None))

    assert a.status is AccountSnapshotStatus.COMPLETE
    assert a.balance == 10_000.0
    assert b.status is AccountSnapshotStatus.UNAVAILABLE
    assert b.balance is None
    assert b.account_id == "ADMIRALS"
    assert a.account_id == "METAQUOTES"


def test_one_account_source_failure_does_not_affect_the_other_account():
    """observe_all keeps failures strictly account-local."""
    class SelectableSource:
        def read_account_info(self):
            raise AccountSourceUnavailable("TERMINAL_DISCONNECTED")

    clock = FixedClock(T0)
    producer = AccountSnapshotProducer(
        SelectableSource(), base_dir=None, clock=clock, persist=False)
    results = producer.observe_all([ACCOUNT_A, ACCOUNT_B])

    assert [s.account_id for s in results] == ["METAQUOTES", "ADMIRALS"]
    assert all(s.status is AccountSnapshotStatus.UNAVAILABLE for s in results)
    assert all(s.source_error == "TERMINAL_DISCONNECTED" for s in results)


def test_latest_lookup_for_account_a_can_never_return_account_b(tmp_path):
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    b = _capture(ACCOUNT_B, FakeAccountInfoSource(
        _info(ACCOUNT_B, balance=25_000.0, currency="USD")))
    for snapshot in (a, b):
        persist_account_snapshot(snapshot, base_dir=tmp_path)

    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    assert store.latest("METAQUOTES").balance == 10_000.0
    assert store.latest("ADMIRALS").balance == 25_000.0
    assert store.latest("METAQUOTES").currency == "GBP"
    assert store.latest("ADMIRALS").currency == "USD"
    with pytest.raises(AccountSnapshotNotFound):
        store.latest("VANTAGE")


def test_representative_multi_account_acceptance_scenario(tmp_path):
    """Account A GBP 10,000/9,850 and Account B USD 25,000/25,300."""
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(
        _info(ACCOUNT_A, balance=10_000.0, profit=-150.0, currency="GBP")))
    b = _capture(ACCOUNT_B, FakeAccountInfoSource(
        _info(ACCOUNT_B, balance=25_000.0, profit=300.0, currency="USD")))
    assert b.equity == 25_300.0

    for snapshot in (a, b):
        persist_account_snapshot(snapshot, base_dir=tmp_path)
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    assert store.latest("METAQUOTES").equity == 9_850.0
    assert store.latest("ADMIRALS").equity == 25_300.0

    # Account B's next observation fails. A must remain untouched.
    failed_b = _capture(
        ACCOUNT_B, FakeAccountInfoSource(None),
        clock=FixedClock(T0 + timedelta(minutes=1)))
    persist_account_snapshot(failed_b, base_dir=tmp_path)
    refreshed = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    assert refreshed.latest("ADMIRALS").status is AccountSnapshotStatus.UNAVAILABLE
    assert refreshed.latest("ADMIRALS").equity is None
    assert refreshed.latest("METAQUOTES").status is AccountSnapshotStatus.COMPLETE
    assert refreshed.latest("METAQUOTES").balance == 10_000.0
    assert refreshed.latest("METAQUOTES").currency == "GBP"
    assert refreshed.latest("ADMIRALS").account_id == "ADMIRALS"


# ═══════════════════════════════════════════════════════════════════════════
# 12, 20, 22–26, 29. DURABLE PERSISTENCE + CANONICAL DELIVERY
# ═══════════════════════════════════════════════════════════════════════════


def test_account_snapshots_dataset_is_registered_and_account_scoped():
    from core.lifecycle_evidence_obligations import ACCOUNT_SCOPED_DATASETS

    assert DATASET in PRODUCTION_SCHEMA_REGISTRY
    assert current_schema(DATASET) == SCHEMA_VERSION
    assert not is_symbol_scoped(DATASET)  # account-scoped, never symbol-scoped
    assert DATASET in ACCOUNT_SCOPED_DATASETS
    assert EXACT_IDENTITY_FIELDS[DATASET] == ("account_id", "snapshot_id")


def test_snapshot_participates_in_the_certified_canonical_outbox_path(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    assert persist_account_snapshot(snapshot, base_dir=tmp_path) is True

    records = get_delivery_outbox().records()
    assert len(records) == 1
    record = records[0]
    assert record.dataset == DATASET
    assert record.delivery_state is DeliveryState.PENDING
    assert record.canonical_ack is None
    assert record.record_identity["account_id"] == "METAQUOTES"
    assert record.record_identity["snapshot_id"] == snapshot.snapshot_id
    # Account snapshots are date-partitioned, so no symbol segment is required.
    assert "symbol=" not in record.canonical_key
    assert f"date={snapshot.observed_at_utc[:10]}" in record.canonical_key


def test_canonical_delivery_handoff_is_prepared_before_the_local_append():
    """The certified pre-write handoff must precede the durable local write."""
    import inspect

    from core.risk import account_snapshot as module

    source = inspect.getsource(module.persist_account_snapshot)
    assert source.index("try_prepare_local_jsonl_handoffs(") < source.index("os.write(fd")


def test_repeated_identical_observation_is_idempotent_in_the_outbox(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)
    persist_account_snapshot(snapshot, base_dir=tmp_path)

    records = get_delivery_outbox().records()
    assert len(records) == 1  # one logical delivery obligation
    lines = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2  # append-only local evidence keeps both writes


def test_different_observation_timestamps_create_distinct_delivery_records(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    first = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    second = _capture(
        ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)),
        clock=FixedClock(T0 + timedelta(seconds=5)))
    persist_account_snapshot(first, base_dir=tmp_path)
    persist_account_snapshot(second, base_dir=tmp_path)

    records = get_delivery_outbox().records()
    assert len(records) == 2
    assert len({r.idempotency_key for r in records}) == 2


class _AckingS3:
    """S3 double that stores the canonical body/metadata and echoes them back.

    The Block 1 worker verifies the remote object by body hash, exact metadata
    and governed identity, so the double must round-trip all three faithfully.
    """

    def __init__(self):
        import hashlib

        self._md5 = hashlib.md5
        self.objects: dict[tuple[str, str], dict] = {}

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        self.objects[key] = {
            "Body": kwargs["Body"],
            "Metadata": dict(kwargs.get("Metadata") or {}),
        }
        return {"ETag": '"' + self._md5(kwargs["Body"]).hexdigest() + '"',
                "VersionId": "v1",
                "ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            raise _NoSuchKey()
        stored = self.objects[key]

        class _Body:
            def read(inner_self):
                return stored["Body"]

        return {"Body": _Body(), "Metadata": stored["Metadata"],
                "ETag": '"' + self._md5(stored["Body"]).hexdigest() + '"',
                "VersionId": "v1"}


class _NoSuchKey(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {
            "Error": {"Code": "NoSuchKey", "Message": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }


def test_canonical_ack_does_not_change_the_numeric_payload(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox
    from core.canonical_delivery_worker import CanonicalDeliveryWorker

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)
    outbox = get_delivery_outbox()
    before = outbox.records()[0].payload

    CanonicalDeliveryWorker(
        outbox, s3_client=_AckingS3()).drain(max_items=1)

    after = outbox.records()[0]
    assert after.delivery_state is DeliveryState.ACKNOWLEDGED
    assert after.lifecycle_obligation_id
    assert after.reconciliation_state == "RECONCILED"
    from core.lifecycle_evidence_obligations import (
        ObligationStatus,
        obligation_ledger,
    )
    obligation = obligation_ledger().get(after.lifecycle_obligation_id)
    assert obligation is not None
    assert dict(obligation.expected_identity) == dict(after.record_identity)
    assert obligation.current_status == ObligationStatus.PRESENT.value
    for key in ("balance", "equity", "floating_pnl", "margin", "free_margin",
                "margin_level", "leverage", "currency"):
        assert after.payload[key] == before[key]
    assert after.payload == before


def test_missing_s3_does_not_lose_the_local_snapshot(tmp_path, isolated_outbox):
    """Local durable evidence survives even when canonical delivery cannot."""
    from core.canonical_delivery import get_delivery_outbox

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)

    local = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(encoding="utf-8")
    assert json.loads(local.strip())["balance"] == 10_000.0
    assert get_delivery_outbox().records()[0].delivery_state is DeliveryState.PENDING


def test_expired_credentials_leave_delivery_pending_and_retryable(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox
    from core.canonical_delivery_worker import (
        CanonicalDeliveryWorker, classify_delivery_failure,
    )

    class _ExpiredCredentials(Exception):
        def __init__(self):
            super().__init__("expired token")
            self.response = {
                "Error": {"Code": "ExpiredToken", "Message": "expired token"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            }

    class _ExpiredS3:
        def put_object(self, **kwargs):
            raise _ExpiredCredentials()

        def get_object(self, **kwargs):
            raise _ExpiredCredentials()

    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)
    outbox = get_delivery_outbox()

    result = CanonicalDeliveryWorker(
        outbox, s3_client=_ExpiredS3()).drain(max_items=1)[0]

    assert result.state_after is DeliveryState.RETRYABLE_FAILURE
    assert classify_delivery_failure(
        _ExpiredCredentials()).category == "RETRYABLE"
    # The obligation remains durable and retryable; nothing was lost.
    assert outbox.records()[0].delivery_state is DeliveryState.RETRYABLE_FAILURE
    assert (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").exists()


# ═══════════════════════════════════════════════════════════════════════════
# 15, 19, 27, 28. RESTART, INDEX RECONSTRUCTION, STARTUP, OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════════════


def test_restart_preserves_durable_history_and_reads_latest_per_account(
    tmp_path, isolated_outbox,
):
    for offset, balance in ((0, 10_000.0), (60, 9_800.0), (120, 9_900.0)):
        snapshot = _capture(
            ACCOUNT_A,
            FakeAccountInfoSource(_info(ACCOUNT_A, balance=balance, profit=0.0)),
            clock=FixedClock(T0 + timedelta(seconds=offset)))
        persist_account_snapshot(snapshot, base_dir=tmp_path)

    # A brand-new store instance == process restart.
    restarted = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    latest = restarted.latest("METAQUOTES")

    assert latest.balance == 9_900.0
    assert latest.observed_at_utc_ms == T0_MS + 120_000
    # Append-only history is intact, not overwritten.
    lines = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["balance"] for line in lines] == [
        10_000.0, 9_800.0, 9_900.0]


def test_stale_durable_snapshot_is_clearly_stale_not_current(tmp_path):
    old = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(old, base_dir=tmp_path)

    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    evaluated = store.latest("METAQUOTES", now_ms=T0_MS + 600_000)

    assert evaluated.status is AccountSnapshotStatus.STALE
    assert evaluated.is_complete is False


def test_account_b_never_reuses_account_a_snapshot_after_restart(tmp_path):
    a = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(a, base_dir=tmp_path)

    restarted = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    with pytest.raises(AccountSnapshotNotFound):
        restarted.latest("ADMIRALS")


def test_startup_creates_a_fresh_snapshot_when_the_account_is_available(
    tmp_path, isolated_outbox,
):
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    with pytest.raises(AccountSnapshotNotFound):
        store.latest("METAQUOTES")

    producer = AccountSnapshotProducer(
        FakeAccountInfoSource(_info(ACCOUNT_A)),
        base_dir=tmp_path, clock=FixedClock(T0),
    )
    startup_snapshot = producer.observe(ACCOUNT_A)
    store.record(startup_snapshot)

    assert startup_snapshot.status is AccountSnapshotStatus.COMPLETE
    assert store.latest(
        "METAQUOTES", now_ms=T0_MS).status is AccountSnapshotStatus.COMPLETE


def test_source_failure_is_observable_in_snapshot_and_health(tmp_path):
    producer = AccountSnapshotProducer(
        FakeAccountInfoSource(
            error=AccountSourceUnavailable("TERMINAL_DISCONNECTED")),
        base_dir=tmp_path, clock=FixedClock(T0),
    )
    failed = producer.observe(ACCOUNT_A)
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    store.record(failed)

    health = store.health(now_ms=T0_MS)
    assert health["accounts_observed"] == 1
    assert health["counts"]["unavailable"] == 1
    assert health["source_error_count"] == 1
    assert health["accounts"]["METAQUOTES"]["source_error"] == (
        "TERMINAL_DISCONNECTED")
    assert health["last_success_observed_at_utc_ms"] is None


def test_health_reports_per_account_age_status_and_counts(tmp_path):
    good = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    bad_payload = _info(ACCOUNT_B)
    bad_payload["equity"] = float("nan")
    bad = _capture(
        ACCOUNT_B, FakeAccountInfoSource(bad_payload),
        clock=FixedClock(T0 + timedelta(seconds=10)))
    for snapshot in (good, bad):
        persist_account_snapshot(snapshot, base_dir=tmp_path)

    health = AccountSnapshotStore(
        base_dir=tmp_path, threshold_ms=60_000).health(now_ms=T0_MS + 20_000)

    assert health["accounts_observed"] == 2
    assert health["accounts"]["METAQUOTES"]["status"] == "COMPLETE"
    assert health["accounts"]["METAQUOTES"]["age_ms"] == 20_000
    assert health["accounts"]["ADMIRALS"]["status"] == "INVALID"
    assert health["accounts"]["ADMIRALS"]["invalid_field_count"] == 1
    assert health["counts"] == {
        "complete": 1, "partial": 0, "unavailable": 0,
        "invalid": 1, "stale": 0,
    }
    assert health["last_success_observed_at_utc_ms"] == T0_MS + 10_000


def test_latest_index_is_reconstructible_from_canonical_local_snapshots(tmp_path):
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)

    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    # Wipe the in-memory index entirely; canonical evidence rebuilds it.
    store._index = {}
    store.rebuild()

    assert store.latest("METAQUOTES").balance == 10_000.0


def test_corrupt_local_lines_are_skipped_without_breaking_the_index(tmp_path):
    good = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(good, base_dir=tmp_path)
    path = tmp_path / "2026-10-02" / "METAQUOTES.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    assert store.latest("METAQUOTES").balance == 10_000.0


def test_store_exposes_timestamp_status_and_staleness_for_consumers(tmp_path):
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    persist_account_snapshot(snapshot, base_dir=tmp_path)
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    fresh = store.latest("METAQUOTES", now_ms=T0_MS + 1_000)
    assert fresh.observed_at_utc == snapshot.observed_at_utc
    assert fresh.status is AccountSnapshotStatus.COMPLETE
    assert fresh.age_ms(now_ms=T0_MS + 1_000) == 1_000
    assert store.threshold_ms == 60_000
    assert store.account_ids() == ("METAQUOTES",)


def test_snapshot_round_trips_through_its_persisted_payload():
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A)))
    restored = AccountSnapshot.from_dict(snapshot.to_dict())

    assert restored == snapshot
    assert restored.status is AccountSnapshotStatus.COMPLETE
    assert restored.balance == 10_000.0


def test_persisted_payload_is_strict_json_without_non_finite_values(tmp_path):
    payload = _info(ACCOUNT_A)
    payload["equity"] = float("inf")
    snapshot = _capture(ACCOUNT_A, FakeAccountInfoSource(payload))
    persist_account_snapshot(snapshot, base_dir=tmp_path)

    line = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(
        encoding="utf-8").strip()
    record = json.loads(line)  # strict: NaN/Infinity are not valid JSON here
    assert record["equity"] is None
    assert record["status"] == "INVALID"
    assert "Infinity" not in line and "NaN" not in line


# ═══════════════════════════════════════════════════════════════════════════
# 13. SNAPSHOT CADENCE
# ═══════════════════════════════════════════════════════════════════════════


def test_producer_exposes_reliable_availability_hooks_without_spam():
    """Cadence is explicit and caller-driven: one observation per call."""
    clock = FixedClock(T0)
    producer = AccountSnapshotProducer(
        FakeAccountInfoSource(_info(ACCOUNT_A)),
        base_dir=None, clock=clock, persist=False)

    first = producer.observe(ACCOUNT_A)
    clock.advance(seconds=1)
    second = producer.observe(ACCOUNT_A)

    assert first.snapshot_id != second.snapshot_id
    assert first.observed_at_utc_ms < second.observed_at_utc_ms


def test_snapshot_interval_is_configurable_and_defaults_are_bounded():
    assert snapshot_interval_ms({}) == 60_000
    assert snapshot_interval_ms(
        {"ACCOUNT_SNAPSHOT_INTERVAL_SECONDS": "30"}) == 30_000
    # Non-positive / malformed configuration falls back to the documented default.
    assert snapshot_interval_ms({"ACCOUNT_SNAPSHOT_INTERVAL_SECONDS": "0"}) == 60_000
    assert snapshot_interval_ms({"ACCOUNT_SNAPSHOT_INTERVAL_SECONDS": "x"}) == 60_000


def test_heartbeat_is_monotonic_scheduled_and_spawns_no_extra_threads(tmp_path):
    """A single reusable thread; drift never accumulates and no sleep is used."""
    import threading

    from core.risk.account_snapshot import AccountSnapshotHeartbeat

    before = threading.active_count()
    clock = FixedClock(T0)
    producer = AccountSnapshotProducer(
        FakeAccountInfoSource(_info(ACCOUNT_A)),
        base_dir=tmp_path, clock=clock, persist=False)
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    heartbeat = AccountSnapshotHeartbeat(
        producer, store, identities=[ACCOUNT_A], interval_ms=60_000)
    assert heartbeat.interval_ms == 60_000
    # Constructing it must not start a thread; start() is explicit and idempotent.
    assert threading.active_count() == before

    heartbeat.start()
    try:
        assert threading.active_count() == before + 1
        heartbeat.start()  # idempotent: no uncontrolled thread proliferation
        assert threading.active_count() == before + 1
    finally:
        heartbeat.stop()
    assert heartbeat.next_due_monotonic() > 0


def test_heartbeat_next_due_is_derived_from_the_previous_due_time(tmp_path):
    from core.risk.account_snapshot import AccountSnapshotHeartbeat

    heartbeat = AccountSnapshotHeartbeat(
        AccountSnapshotProducer(
            FakeAccountInfoSource(_info(ACCOUNT_A)),
            base_dir=tmp_path, persist=False),
        AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000),
        identities=[ACCOUNT_A], interval_ms=30_000,
    )
    # Fixed 10.0 and 20.0 inputs: the due time advances by exactly one interval,
    # so a late cycle never accumulates drift.
    assert heartbeat.next_due_from(10.0) == 40.0
    assert heartbeat.next_due_from(20.0) == 50.0


def test_heartbeat_tick_records_one_snapshot_per_account(tmp_path):
    from core.risk.account_snapshot import AccountSnapshotHeartbeat

    class _PerAccountSource:
        def read_account_info(self):
            raise AccountSourceUnavailable("ACCOUNT_LOCAL_READ_FAILED")

    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)
    heartbeat = AccountSnapshotHeartbeat(
        AccountSnapshotProducer(
            _PerAccountSource(), base_dir=tmp_path, persist=False),
        store, identities=[ACCOUNT_A, ACCOUNT_B], interval_ms=60_000,
    )
    results = heartbeat.tick()

    assert [s.account_id for s in results] == ["METAQUOTES", "ADMIRALS"]
    assert store.account_ids() == ("ADMIRALS", "METAQUOTES")


def test_heartbeat_rejects_a_non_positive_interval(tmp_path):
    from core.risk.account_snapshot import AccountSnapshotHeartbeat

    with pytest.raises(ValueError):
        AccountSnapshotHeartbeat(
            AccountSnapshotProducer(
                FakeAccountInfoSource(_info(ACCOUNT_A)),
                base_dir=tmp_path, persist=False),
            AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000),
            identities=[ACCOUNT_A], interval_ms=0,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 10, 14. CONSUMER BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════


def test_only_the_injected_mt5_adapter_calls_raw_account_info():
    """Account state flows through this contract, not ad-hoc account_info calls."""
    import ast
    import inspect

    from core.risk import account_snapshot as module

    tree = ast.parse(inspect.getsource(module))
    adapter = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Mt5AccountInfoSource")
    in_adapter = {id(n) for n in ast.walk(adapter) if isinstance(n, ast.Attribute)}

    # Locate every `mt5.account_info` attribute reference in the module.
    references = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "account_info"
        and isinstance(node.value, ast.Name) and node.value.id == "mt5"
    ]
    assert references, "the MT5 adapter must be the single account_info() caller"
    for node in references:
        # Every raw call must be confined to the adapter boundary.
        assert id(node) in in_adapter, (
            "raw mt5.account_info() referenced outside Mt5AccountInfoSource")


def test_snapshot_contract_module_has_no_direct_s3_put():
    """Telemetry must never bypass the certified canonical delivery path."""
    import inspect

    from core.risk import account_snapshot as module

    assert ".put_object(" not in inspect.getsource(module)
    assert "enqueue_canonical_delivery" in inspect.getsource(module)


def test_consumer_lookup_fails_explicitly_instead_of_returning_zeros(tmp_path):
    store = AccountSnapshotStore(base_dir=tmp_path, threshold_ms=60_000)

    with pytest.raises(AccountSnapshotNotFound):
        store.latest("METAQUOTES")
    with pytest.raises(AccountSnapshotNotFound):
        store.latest("")
    with pytest.raises(AccountSnapshotNotFound):
        store.latest("   ")


def test_store_rejects_a_non_positive_freshness_threshold(tmp_path):
    with pytest.raises(ValueError):
        AccountSnapshotStore(base_dir=tmp_path, threshold_ms=0)
    with pytest.raises(ValueError):
        AccountSnapshotStore(base_dir=tmp_path, threshold_ms=-1)
    with pytest.raises(ValueError):
        _capture(ACCOUNT_A, FakeAccountInfoSource(_info(ACCOUNT_A))).is_stale(
            now_ms=T0_MS, threshold_ms=0)
