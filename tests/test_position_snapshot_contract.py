"""Account-safe prop-risk telemetry — position / open-risk contract (Block 2B).

Covers the canonical position and open-risk telemetry contract end to end:
exact position identity, per-position schema, stop geometry, monetary-risk
valuation, the position-SET completeness boundary, the account open-risk
aggregate, exact account-snapshot linkage, multi-account isolation, freshness,
durable canonical persistence, the consumer store boundary, cadence and
observability.

The load-bearing invariants are:

  * UNKNOWN RISK IS NOT ZERO RISK. An open position without a provable
    monetary risk reports ``monetary_risk_to_sl is None`` and forces the
    aggregate to withhold ``total_open_risk``. A position with no stop is
    explicitly UNPROTECTED, never silently cheap.
  * ZERO IS NOT UNAVAILABLE. ``positions_get() == ()`` is a COMPLETE
    zero-position set; a failure is UNAVAILABLE and is never an empty
    portfolio.
  * CLOSURE IS PROVEN, NOT INFERRED. Current-state reconstruction is governed
    by the exact complete position-set boundary, never by recency.
  * LINKAGE IS EXACT. Every observation joins to its account by exact
    account id and exact ``account_snapshot_id``; never by timestamp proximity.

All clocks are injected. There are no sleeps and no live broker access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import json
import math

import pytest

from core.canonical_delivery import configure_delivery_outbox
from core.canonical_delivery_outbox import DeliveryState
from core.lifecycle_evidence_obligations import (
    ACCOUNT_SCOPED_DATASETS,
    EXACT_IDENTITY_FIELDS,
)
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    is_symbol_scoped,
)
from core.risk.account_snapshot import AccountIdentity, AccountIdentityError
from core.risk.position_snapshot import (
    DATASET,
    OPEN_RISK_DATASET,
    OPEN_RISK_SCHEMA_VERSION,
    SCHEMA_VERSION,
    AccountOpenRiskSnapshot,
    PositionObservationCycle,
    PositionSetObservation,
    PositionSetUnavailable,
    PositionSide,
    PositionSnapshot,
    PositionSnapshotError,
    PositionSnapshotHeartbeat,
    PositionSnapshotNotFound,
    PositionSnapshotProducer,
    PositionSnapshotStore,
    PositionStatus,
    aggregate_open_risk,
    capture_position_snapshot,
    derive_observation_id,
    derive_open_risk_id,
    derive_position_set_id,
    derive_position_snapshot_id,
    observe_account_risk,
    observe_positions,
    persist_open_risk,
    persist_position_set,
    persist_position_snapshot,
    snapshot_interval_ms,
    to_epoch_ms,
)


# ─── TEST DOUBLES — no live MT5, no sleeps, injected clocks only
# ─────────────────────────────────────────────────────────────────────


class FixedClock:
    """Injected clock. No sleeping, no wall-clock reads."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


@dataclass
class FakePositionSource:
    """Injected stand-in for the MT5 ``positions_get()`` boundary.

    ``rows`` of ``None`` or an Exception models a SOURCE FAILURE and must
    never be observable as an empty portfolio.
    """

    rows: object = None
    error: Exception | None = None

    def read_positions(self):
        if self.error is not None:
            raise self.error
        if self.rows is None:
            return None
        # A Mapping/str/bytes payload is returned VERBATIM so the contract's
        # fail-closed malformed-payload path is reachable from tests.
        if isinstance(self.rows, (dict, str, bytes)):
            return self.rows
        return list(self.rows)


class FakeSpecSource:
    """Injected broker symbol-spec source (``symbol_info`` boundary)."""

    def __init__(self, spec: dict | None) -> None:
        self._spec = spec
        self.requested: list[str] = []

    def read_symbol_spec(self, broker_symbol: str):
        self.requested.append(broker_symbol)
        return None if self._spec is None else dict(self._spec)


class FakeCalculator:
    """Broker-native ``order_calc_profit`` double."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    def calc_profit(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._error is not None:
            raise self._error
        return self._result


T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0_MS = to_epoch_ms(T0)
T0_ISO = "2026-10-02T12:00:00.000000Z"

ACCOUNT_A = AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 5012345)
ACCOUNT_B = AccountIdentity("ADMIRALS", "Admirals", "Admirals-Server", 9988776)

# A realistic FX spec: 5-digit EURUSD, tick value in the ACCOUNT currency.
FX_SPEC = {
    "digits": 5, "point": 0.00001, "trade_tick_size": 0.00001,
    "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
    "trade_tick_value_loss": 1.0, "volume_min": 0.01, "volume_max": 100.0,
    "volume_step": 0.01, "pip_size": 0.0001,
}
# Metals: 2-digit gold, 1.00 USD per 0.01 move per lot.
GOLD_SPEC = {
    "digits": 2, "point": 0.01, "trade_tick_size": 0.01,
    "trade_tick_value": 1.0, "volume_min": 0.01, "volume_max": 50.0,
    "volume_step": 0.01, "pip_size": 0.1,
}
# CFD index: NAS100, 0.50 USD per 0.1 point per lot.
INDEX_SPEC = {
    "digits": 2, "point": 0.1, "trade_tick_size": 0.1,
    "trade_tick_value": 0.5, "volume_min": 0.1, "volume_max": 100.0,
    "volume_step": 0.1, "pip_size": 1.0,
}


def _row(ticket: int = 1, **overrides) -> dict:
    """A complete, internally consistent EURUSD BUY position row."""
    row = {
        "ticket": ticket, "symbol": "EURUSD", "type": 0,
        "volume": 0.10, "price_open": 1.0850, "price": 1.0860,
        "sl": 1.0800, "tp": 0.0, "magic": 777, "comment": "block2b",
        "time": 1_700_000_000, "time_msc": 1_700_000_000_000,
        "order": ticket, "profit": -10.0,
    }
    row.update(overrides)
    return row


def _observe(
    account: AccountIdentity = ACCOUNT_A,
    rows=(),
    *,
    clock=None,
    spec=...,
    calculator=None,
    canonical_resolver=None,
    currency="GBP",
    account_snapshot_id=None,
    source=None,
    source_name="MT5_POSITIONS_GET",
):
    """Observe one exact account with injected doubles."""
    spec_source = (
        _spec_source() if spec is ... else
        (None if spec is None else FakeSpecSource(spec))
    )
    return observe_positions(
        account,
        source if source is not None else FakePositionSource(list(rows)),
        clock=clock or (lambda: T0),
        source_name=source_name,
        spec_source=spec_source,
        calculator=calculator,
        canonical_resolver=canonical_resolver,
        currency=currency,
        account_snapshot_id=account_snapshot_id,
    )


def _spec_source(spec: dict | None = None) -> FakeSpecSource:
    return FakeSpecSource(FX_SPEC if spec is None else spec)


def _one(**overrides) -> PositionSnapshot:
    """The single position snapshot of a one-position account observation."""
    return _observe(rows=[_row(**overrides)]).positions[0]


@pytest.fixture
def isolated_outbox(tmp_path):
    """Isolate the process-wide canonical outbox for each test."""
    configure_delivery_outbox(tmp_path / "process_outbox.sqlite3")
    try:
        yield
    finally:
        configure_delivery_outbox(None)


def _persist(cycle: PositionObservationCycle, base_dir, outbox=None) -> None:
    """Persist one whole observation cycle through the canonical path."""
    persist_position_set(cycle.position_set, base_dir=base_dir, outbox=outbox)
    for snapshot in cycle.positions:
        persist_position_snapshot(snapshot, base_dir=base_dir, outbox=outbox)
    persist_open_risk(cycle.open_risk, base_dir=base_dir, outbox=outbox)


def _store(base_dir, **kwargs) -> PositionSnapshotStore:
    return PositionSnapshotStore(
        base_dir=base_dir, open_risk_dir=base_dir, threshold_ms=60_000, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# 1–2. ZERO OPEN POSITIONS IS A VALID OBSERVATION; A FAILURE IS NOT ZERO
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_open_positions_is_a_complete_valid_observation():
    """``positions_get() == ()`` is COMPLETE zero exposure, not UNAVAILABLE."""
    cycle = _observe(rows=[])
    observation, aggregate = cycle.position_set, cycle.open_risk

    assert observation.position_set_complete is True
    assert observation.open_position_count == 0
    assert observation.position_tickets == ()
    assert observation.status is PositionStatus.COMPLETE
    assert observation.source_error is None
    assert cycle.positions == ()
    # A provably empty complete set is a legitimate, complete ZERO-risk account.
    assert aggregate.position_set_complete is True
    assert aggregate.open_position_count == 0
    assert aggregate.total_open_risk == 0.0
    assert aggregate.risk_complete is True
    assert aggregate.status is PositionStatus.COMPLETE


@pytest.mark.parametrize("failure", [
    None,                                   # source returned None
    PositionSetUnavailable("POSITIONS_GET_FAILED"),
    RuntimeError("terminal not connected"),
    ConnectionError("socket closed"),
])
def test_positions_get_failure_is_never_zero_positions(failure):
    """A source failure is UNAVAILABLE, never an empty portfolio."""
    source = FakePositionSource(None) if failure is None else (
        FakePositionSource([], error=failure))
    cycle = _observe(source=source)
    observation, aggregate = cycle.position_set, cycle.open_risk

    assert observation.position_set_complete is False
    assert observation.status is PositionStatus.UNAVAILABLE
    assert observation.source_error                     # explicit reason
    # The distinguishing fact: a failure does NOT claim zero exposure.
    assert observation.open_position_count is None
    assert observation.status is not PositionStatus.COMPLETE
    assert cycle.positions == ()
    # And the aggregate never publishes an authoritative zero-risk total.
    assert aggregate.position_set_complete is False
    assert aggregate.status is PositionStatus.UNAVAILABLE
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.open_position_count is None


def test_malformed_position_set_payload_fails_closed():
    """A scalar/None-ish payload is not a position set."""
    cycle = _observe(source=FakePositionSource(rows={"not": "a set"}))
    assert cycle.position_set.position_set_complete is False
    assert cycle.position_set.source_error == "POSITION_SET_MALFORMED"
    assert cycle.open_risk.total_open_risk is None


def test_empty_set_survives_durable_round_trip(tmp_path):
    """The zero-position boundary is durable evidence, not an inference."""
    cycle = _observe(rows=[])
    _persist(cycle, tmp_path)
    store = _store(tmp_path)
    observation = store.latest_position_set("METAQUOTES")
    assert observation.position_set_complete is True
    assert observation.open_position_count == 0
    assert store.current_positions("METAQUOTES") == ()
    assert store.open_tickets("METAQUOTES") == ()
    assert store.latest_open_risk("METAQUOTES").total_open_risk == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 3–8. SIDE, STOP PRESENCE AND STOP DIRECTION
# ═══════════════════════════════════════════════════════════════════════════


def test_buy_with_valid_stop_is_complete_with_exact_risk():
    """BUY, SL below market: protected, and 50.00 GBP of money at risk."""
    cycle = _observe(rows=[_row()])
    snapshot = cycle.positions[0]

    assert snapshot.status is PositionStatus.COMPLETE
    assert snapshot.side is PositionSide.BUY
    assert snapshot.has_stop is True
    assert snapshot.stop_side_valid is True
    assert snapshot.is_protected is True
    # (1.0800 - 1.0850) / 0.00001 = -500 ticks; 500 * 1.00 * 0.10 = 50.00 loss.
    assert snapshot.expected_pnl_at_sl == pytest.approx(-50.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(50.0)
    assert snapshot.risk_calculation == "BROKER_TICK_VALUE_EXACT"
    assert snapshot.risk_currency == "GBP"
    # Stop geometry is measured from the CURRENT close-out price.
    assert snapshot.risk_price == 1.0860
    assert snapshot.stop_distance_price == pytest.approx(0.0060)
    assert snapshot.stop_distance_points == pytest.approx(600.0)
    assert snapshot.stop_distance_pips == pytest.approx(60.0)
    assert cycle.open_risk.total_open_risk == pytest.approx(50.0)
    assert cycle.open_risk.risk_complete is True


def test_sell_with_valid_stop_is_complete_with_exact_risk():
    """SELL, SL above market: protected, mirrored sign convention."""
    cycle = _observe(rows=[_row(type=1, price_open=1.0850, price=1.0840,
                               sl=1.0900)])
    snapshot = cycle.positions[0]

    assert snapshot.status is PositionStatus.COMPLETE
    assert snapshot.side is PositionSide.SELL
    assert snapshot.has_stop is True
    assert snapshot.stop_side_valid is True
    # (1.0900 - 1.0850) / 0.00001 = +500 ticks; a SELL loses on a rising close.
    assert snapshot.expected_pnl_at_sl == pytest.approx(-50.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(50.0)
    assert cycle.open_risk.total_open_risk == pytest.approx(50.0)
    assert cycle.open_risk.risk_complete is True


def test_buy_without_stop_is_unprotected_and_never_zero_risk():
    """No SL is UNBOUNDED risk. It must not be reported as a cheap position."""
    cycle = _observe(rows=[_row(sl=0.0)])
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    assert snapshot.has_stop is False
    assert snapshot.is_protected is False
    assert snapshot.status is PositionStatus.UNPROTECTED
    # The load-bearing assertion: unknown risk, NOT zero risk.
    assert snapshot.monetary_risk_to_sl is None
    assert snapshot.has_known_risk is False
    assert snapshot.risk_calculation == "UNPROTECTED_NO_STOP"
    # The aggregate must not claim a complete risk for an unbounded position.
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.unprotected_position_count == 1
    assert aggregate.unknown_risk_position_count == 1
    assert aggregate.status is PositionStatus.UNPROTECTED


def test_sell_without_stop_is_unprotected_and_never_zero_risk():
    cycle = _observe(rows=[_row(type=1, sl=0.0)])
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    assert snapshot.side is PositionSide.SELL
    assert snapshot.has_stop is False
    assert snapshot.status is PositionStatus.UNPROTECTED
    assert snapshot.monetary_risk_to_sl is None
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None


def test_invalid_buy_stop_direction_is_preserved_verbatim_and_invalid():
    """A BUY stop above the market is recorded, never clamped or swapped."""
    cycle = _observe(rows=[_row(sl=1.0900)])
    snapshot = cycle.positions[0]

    assert snapshot.sl == 1.0900                  # observed value preserved
    assert snapshot.stop_side_valid is False
    assert snapshot.status is PositionStatus.INVALID
    assert snapshot.risk_calculation == "INVALID_STOP_DIRECTION"
    assert "stop_side" in snapshot.invalid_fields
    assert snapshot.monetary_risk_to_sl is None
    assert snapshot.expected_pnl_at_sl is None
    assert snapshot.is_protected is False
    assert cycle.open_risk.total_open_risk is None


def test_invalid_sell_stop_direction_is_preserved_verbatim_and_invalid():
    cycle = _observe(rows=[_row(type=1, sl=1.0800)])
    snapshot = cycle.positions[0]

    assert snapshot.sl == 1.0800                  # observed value preserved
    assert snapshot.stop_side_valid is False
    assert snapshot.status is PositionStatus.INVALID
    assert snapshot.risk_calculation == "INVALID_STOP_DIRECTION"
    assert snapshot.monetary_risk_to_sl is None
    assert cycle.open_risk.total_open_risk is None


def test_stop_side_is_judged_against_the_current_close_out_price():
    """A stop judged only against the entry price would mis-classify trails."""
    # BUY opened 1.0850, now 1.0900, SL 1.0800: valid against the LIVE market.
    cycle = _observe(rows=[_row(price=1.0900, sl=1.0800)])
    assert cycle.positions[0].stop_side_valid is True
    assert cycle.positions[0].risk_price == 1.0900


# ═══════════════════════════════════════════════════════════════════════════
# 9–12. EXACT ACCOUNT / TICKET IDENTITY AND SYMBOL RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════


def test_exact_account_and_ticket_identity_is_carried_on_every_record():
    snapshot = _one(ticket=987_654_321)
    cycle = _observe(rows=[_row(ticket=987_654_321)])
    aggregate = cycle.open_risk

    for record in (snapshot, cycle.position_set):
        assert record.account_id == "METAQUOTES"
        assert record.broker == "MetaQuotes"
        assert record.server == "MetaQuotes-Demo"
        assert record.login == 5012345
    assert snapshot.position_ticket == 987_654_321
    # The account-safe ownership key is (account, ticket), never ticket alone.
    assert snapshot.ownership_key == ("METAQUOTES", 987_654_321)
    assert snapshot.position_identity.ownership_key() == (
        "METAQUOTES", 987_654_321)
    assert aggregate.account_id == "METAQUOTES"
    assert aggregate.login == 5012345


def test_same_ticket_on_two_accounts_is_two_distinct_positions():
    """The same numeric ticket on A and B is never the same position."""
    shared_ticket = 555_001
    a = _observe(ACCOUNT_A, rows=[_row(shared_ticket)])
    b = _observe(ACCOUNT_B, rows=[_row(shared_ticket)], currency="USD")

    assert a.positions[0].position_ticket == b.positions[0].position_ticket
    assert a.positions[0].ownership_key != b.positions[0].ownership_key
    assert a.positions[0].position_snapshot_id != b.positions[0].position_snapshot_id
    assert a.observation_id != b.observation_id
    assert a.open_risk.open_risk_id != b.open_risk.open_risk_id
    assert a.positions[0].account_id == "METAQUOTES"
    assert b.positions[0].account_id == "ADMIRALS"


def test_account_a_stays_valid_when_account_b_source_fails():
    """One account's failure can never contaminate the other account."""
    a = _observe(ACCOUNT_A, rows=[_row(1)])
    b = _observe(ACCOUNT_B,
                 source=FakePositionSource(error=RuntimeError("B_DISCONNECTED")),
                 currency="USD")

    assert a.open_risk.risk_complete is True
    assert a.open_risk.total_open_risk == pytest.approx(50.0)
    assert b.open_risk.status is PositionStatus.UNAVAILABLE
    assert b.open_risk.total_open_risk is None
    assert b.open_risk.position_set_complete is False
    # A's identity is untouched by B's fault.
    assert all(p.account_id == "METAQUOTES" for p in a.positions)
    assert b.positions == ()


def test_broker_symbol_is_mapped_to_its_canonical_symbol():
    """Broker naming is preserved AND resolved; canonical naming is upstream."""
    resolver = {"EURUSD.pro": "EURUSD", "XAUUSDm": "XAUUSD",
                "NAS100.cash": "NAS100"}
    cycle = _observe(rows=[_row(symbol="EURUSD.pro")],
                     canonical_resolver=resolver.__getitem__)
    snapshot = cycle.positions[0]

    assert snapshot.broker_symbol == "EURUSD.pro"     # broker name preserved
    assert snapshot.canonical_symbol == "EURUSD"      # canonical name resolved


def test_unresolvable_broker_symbol_is_explicitly_marked_not_invented():
    cycle = _observe(rows=[_row(symbol="MYSTERY")],
                     canonical_resolver=lambda symbol: None)
    # No fabricated canonical name is invented.
    assert cycle.positions[0].canonical_symbol == "UNRESOLVED:MYSTERY"
    assert cycle.positions[0].broker_symbol == "MYSTERY"


def test_a_failing_symbol_resolver_never_fabricates_a_canonical_name():
    def _boom(symbol):
        raise RuntimeError("resolution service down")

    cycle = _observe(rows=[_row(symbol="EURUSD")], canonical_resolver=_boom)
    assert cycle.positions[0].canonical_symbol == "UNRESOLVED:EURUSD"


# ═══════════════════════════════════════════════════════════════════════════
# 13–21. MONETARY RISK ACROSS INSTRUMENTS, CURRENCY AND DATA QUALITY
# ═══════════════════════════════════════════════════════════════════════════


def test_fx_monetary_risk_uses_the_account_currency_tick_value():
    """EURUSD: risk is priced by the account's own tick value, not a pip guess."""
    cycle = _observe(
        rows=[_row(symbol="EURUSD", volume=0.10, price_open=1.0850,
                   price=1.0860, sl=1.0800)],
        spec=FX_SPEC, currency="GBP")
    snapshot = cycle.positions[0]

    assert snapshot.risk_calculation == "BROKER_TICK_VALUE_EXACT"
    # -500 ticks * 1.00 GBP/tick/lot * 0.10 lot = -50.00 GBP
    assert snapshot.expected_pnl_at_sl == pytest.approx(-50.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(50.0)
    assert snapshot.risk_currency == "GBP"
    assert cycle.open_risk.currency == "GBP"


def test_gold_monetary_risk_is_derived_from_the_metals_spec():
    """XAUUSD: 10.00 move on 1.00 lot at 1.00 USD per 0.01 = 1000.00 USD."""
    cycle = _observe(
        rows=[_row(symbol="XAUUSD", volume=1.0, price_open=2350.00,
                   price=2352.00, sl=2340.00)],
        spec=GOLD_SPEC, currency="USD")
    snapshot = cycle.positions[0]

    assert snapshot.status is PositionStatus.COMPLETE
    assert snapshot.risk_calculation == "BROKER_TICK_VALUE_EXACT"
    # -1000 ticks (0.01 each) * 1.00 USD * 1.0 lot = -1000.00 USD
    assert snapshot.expected_pnl_at_sl == pytest.approx(-1000.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(1000.0)
    assert snapshot.risk_currency == "USD"
    assert cycle.open_risk.total_open_risk == pytest.approx(1000.0)


def test_index_monetary_risk_is_derived_from_the_cfd_spec():
    """NAS100: 100-point move on 1.0 lot at 0.50 USD per 0.1 = 500.00 USD."""
    cycle = _observe(
        rows=[_row(symbol="NAS100", type=1, volume=1.0, price_open=18000.0,
                   price=18010.0, sl=18100.0)],
        spec=INDEX_SPEC, currency="USD")
    snapshot = cycle.positions[0]

    assert snapshot.risk_calculation == "BROKER_TICK_VALUE_EXACT"
    # +1000 ticks (0.1 each) * 0.50 USD * 1.0 lot; a SELL loses on a rise.
    assert snapshot.expected_pnl_at_sl == pytest.approx(-500.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(500.0)
    assert cycle.open_risk.total_open_risk == pytest.approx(500.0)


def test_broker_native_calculation_is_preferred_over_the_local_formula():
    """order_calc_profit wins; it already prices FX, metals and CFDs natively."""
    calculator = FakeCalculator(result=-77.0)
    cycle = _observe(rows=[_row()], calculator=calculator, currency="GBP")
    snapshot = cycle.positions[0]

    assert snapshot.risk_calculation == "BROKER_ORDER_CALC_PROFIT"
    assert snapshot.monetary_risk_to_sl == pytest.approx(77.0)
    assert cycle.open_risk.total_open_risk == pytest.approx(77.0)
    # It is evaluated AT THE STOP with the position's own side and volume.
    call = calculator.calls[0]
    assert call["side"] == "BUY"
    assert call["broker_symbol"] == "EURUSD"
    assert call["volume"] == 0.10
    assert call["open_price"] == 1.0850
    assert call["close_price"] == 1.0800


def test_account_currency_is_preserved_verbatim_and_never_converted():
    cycle = _observe(rows=[_row()], currency="GBP")
    assert cycle.open_risk.currency == "GBP"
    assert cycle.positions[0].risk_currency == "GBP"
    # A risk number is never silently re-denominated into another currency.
    assert cycle.open_risk.to_dict()["currency"] == "GBP"


def test_risk_currency_follows_each_accounts_own_currency():
    a = _observe(ACCOUNT_A, rows=[_row()], currency="GBP")
    b = _observe(ACCOUNT_B, rows=[_row()], currency="USD")
    assert a.positions[0].risk_currency == "GBP"
    assert b.positions[0].risk_currency == "USD"
    assert a.open_risk.currency == "GBP"
    assert b.open_risk.currency == "USD"


def test_negative_and_positive_floating_pnl_are_preserved_exactly():
    cycle = _observe(rows=[_row(1, profit=-55.25), _row(2, profit=30.0)])
    by_ticket = {p.position_ticket: p for p in cycle.positions}
    assert by_ticket[1].floating_pnl == -55.25
    assert by_ticket[2].floating_pnl == 30.0
    assert cycle.open_risk.floating_pnl_total == pytest.approx(-25.25)


def test_legitimate_zero_floating_pnl_is_preserved_not_treated_as_missing():
    cycle = _observe(rows=[_row(profit=0.0)])
    assert cycle.positions[0].floating_pnl == 0.0
    assert cycle.open_risk.floating_pnl_total == 0.0


def test_unavailable_floating_pnl_is_never_substituted_with_zero():
    row = _row()
    row.pop("profit")
    cycle = _observe(rows=[row])
    snapshot = cycle.positions[0]

    assert snapshot.floating_pnl is None            # unknown, not 0.0
    assert "floating_pnl" in snapshot.unavailable_fields
    # A partially-known portfolio total is withheld rather than understated.
    assert cycle.open_risk.floating_pnl_total is None
    # Crucially, the monetary RISK is still exactly provable and is unaffected.
    assert cycle.open_risk.total_open_risk == pytest.approx(50.0)
    assert cycle.open_risk.risk_complete is True


def test_missing_broker_spec_leaves_risk_unknown_not_zero():
    """Without tick data there is no exact monetary path: risk is UNKNOWN."""
    cycle = _observe(rows=[_row()], spec=None, currency="GBP")
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    assert snapshot.status is PositionStatus.PARTIAL
    assert snapshot.monetary_risk_to_sl is None
    assert snapshot.risk_calculation == "UNAVAILABLE_NO_EXACT_MONETARY_PATH"
    assert "broker_spec" in snapshot.unavailable_fields
    # The aggregate refuses to claim a complete total it cannot prove.
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.unknown_risk_position_count == 1
    assert aggregate.status is PositionStatus.PARTIAL


def test_a_raising_broker_calculator_falls_back_and_never_escapes():
    calculator = FakeCalculator(error=RuntimeError("calc failed"))
    cycle = _observe(rows=[_row()], calculator=calculator, currency="GBP")
    snapshot = cycle.positions[0]

    assert calculator.calls                       # the broker path was tried
    # The exact tick identity still yields a provable value.
    assert snapshot.risk_calculation == "BROKER_TICK_VALUE_EXACT"
    assert snapshot.monetary_risk_to_sl == pytest.approx(50.0)


def test_a_raising_broker_calculator_with_no_spec_yields_unknown_risk():
    cycle = _observe(rows=[_row()], spec=None,
                     calculator=FakeCalculator(error=RuntimeError("boom")))
    snapshot = cycle.positions[0]
    assert snapshot.monetary_risk_to_sl is None
    assert cycle.open_risk.total_open_risk is None
    assert cycle.open_risk.risk_complete is False


def test_a_non_finite_broker_result_is_rejected_not_trusted():
    cycle = _observe(rows=[_row()],
                     calculator=FakeCalculator(result=float("nan")))
    snapshot = cycle.positions[0]
    # A NaN broker answer is discarded, not published as money.
    assert snapshot.risk_calculation != "BROKER_ORDER_CALC_PROFIT"
    assert math.isfinite(snapshot.monetary_risk_to_sl)


# ═══════════════════════════════════════════════════════════════════════════
# 22–26. LIVE POSITION MUTATIONS ARE OBSERVED, NEVER ASSUMED
# ═══════════════════════════════════════════════════════════════════════════


def _two_cycles(tmp_path, first_rows, second_rows, *, currency="GBP"):
    """Persist two successive observations of account A and return the store."""
    _persist(_observe(ACCOUNT_A, rows=first_rows, currency=currency), tmp_path)
    clock = FixedClock(T0)
    clock.advance(minutes=1)
    _persist(
        _observe(ACCOUNT_A, rows=second_rows, clock=clock, currency=currency),
        tmp_path)
    return _store(tmp_path)


def test_partial_close_recalculates_risk_at_the_reduced_volume():
    """Volume 0.10 -> 0.05 halves the money at risk."""
    cycle = _observe(rows=[_row(1, volume=0.05, price_open=1.0850,
                              price=1.0860, sl=1.0800)])
    snapshot = cycle.positions[0]
    assert snapshot.volume == 0.05                # observed, never normalised
    assert snapshot.monetary_risk_to_sl == pytest.approx(25.0)
    assert cycle.open_risk.total_open_risk == pytest.approx(25.0)


def test_stop_modification_recalculates_risk_at_the_new_distance():
    """Tightening the stop reduces risk; widening it increases risk."""
    # Risk is the loss at the stop measured from the OPEN price (1.0850).
    wide = _observe(rows=[_row(sl=1.0700)])       # 1500 ticks * 0.10 = 150.00
    normal = _observe(rows=[_row(sl=1.0800)])     #  500 ticks * 0.10 =  50.00
    tight = _observe(rows=[_row(sl=1.0830)])      #  200 ticks * 0.10 =  20.00

    assert wide.positions[0].monetary_risk_to_sl == pytest.approx(150.0)
    assert normal.positions[0].monetary_risk_to_sl == pytest.approx(50.0)
    assert tight.positions[0].monetary_risk_to_sl == pytest.approx(20.0)
    assert (tight.positions[0].monetary_risk_to_sl <
            normal.positions[0].monetary_risk_to_sl <
            wide.positions[0].monetary_risk_to_sl)


def test_stop_removal_makes_a_previously_protected_position_unprotected():
    """The dangerous direction: a stop DISAPPEARS between observations."""
    before = _observe(rows=[_row()])
    assert before.positions[0].is_protected is True
    assert before.open_risk.risk_complete is True
    assert before.open_risk.total_open_risk == pytest.approx(50.0)

    later = _observe(rows=[_row(sl=0.0)])
    assert later.positions[0].is_protected is False
    assert later.positions[0].status is PositionStatus.UNPROTECTED
    assert later.positions[0].monetary_risk_to_sl is None
    # The account's authoritative risk is WITHHELD, not reported as zero.
    assert later.open_risk.risk_complete is False
    assert later.open_risk.total_open_risk is None
    assert later.open_risk.unprotected_position_count == 1


def test_breakeven_stop_is_a_valid_non_negative_zero_risk():
    """SL exactly at the open price: real, provable ZERO risk (not unknown)."""
    cycle = _observe(rows=[_row(price_open=1.0850, price=1.0900, sl=1.0850)])
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    assert snapshot.stop_side_valid is True
    assert snapshot.is_protected is True
    assert snapshot.status is PositionStatus.COMPLETE
    # Distinct from the UNPROTECTED case: this zero is PROVEN, not missing.
    assert snapshot.has_known_risk is True
    assert snapshot.monetary_risk_to_sl == pytest.approx(0.0)
    assert snapshot.expected_pnl_at_sl == pytest.approx(0.0)
    assert aggregate.unknown_risk_position_count == 0
    assert aggregate.risk_complete is True
    assert aggregate.total_open_risk == pytest.approx(0.0)


def test_profit_locking_stop_reports_zero_risk_but_preserves_locked_profit():
    """SL above entry on a winning BUY: risk 0, and the locked gain is kept."""
    cycle = _observe(rows=[_row(price_open=1.0850, price=1.0900, sl=1.0870)])
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    assert snapshot.stop_side_valid is True
    assert snapshot.is_protected is True
    assert snapshot.status is PositionStatus.COMPLETE
    # The stop now GUARANTEES profit: +20.00 GBP locked in.
    assert snapshot.expected_pnl_at_sl == pytest.approx(20.0)
    assert snapshot.monetary_risk_to_sl == pytest.approx(0.0)
    # The locked geometry is never hidden behind the clamped zero risk.
    assert snapshot.expected_pnl_at_sl > 0.0
    assert aggregate.risk_complete is True
    assert aggregate.total_open_risk == pytest.approx(0.0)


def test_locked_profit_stop_keeps_risk_distinct_from_unknown_risk():
    """0.0 (provable) and None (unknown) must never be conflated."""
    locked = _observe(rows=[_row(price_open=1.0850, price=1.0900, sl=1.0870)])
    unknown = _observe(rows=[_row(sl=0.0)])

    assert locked.positions[0].monetary_risk_to_sl == 0.0
    assert locked.positions[0].has_known_risk is True
    assert unknown.positions[0].monetary_risk_to_sl is None
    assert unknown.positions[0].has_known_risk is False
    assert locked.open_risk.has_authoritative_total is True
    assert unknown.open_risk.has_authoritative_total is False


# ═══════════════════════════════════════════════════════════════════════════
# 27–33. POSITION-SET BOUNDARY AND AGGREGATE OPEN-RISK CONTRACT
# ═══════════════════════════════════════════════════════════════════════════


def test_a_complete_position_set_lists_every_observed_ticket_exactly():
    observation = _observe(rows=[_row(11), _row(22), _row(33)]).position_set

    assert observation.position_set_complete is True
    assert observation.open_position_count == 3
    assert observation.position_tickets == (11, 22, 33)   # sorted, exact set
    assert observation.consistency_ok is True
    for ticket in (11, 22, 33):
        assert observation.contains(ticket) is True
    assert observation.contains(44) is False


def test_position_set_boundary_carries_its_own_governed_identity():
    """The set row shares a dataset with per-position rows, so it needs an id."""
    cycle = _observe(rows=[_row(1)])
    observation = cycle.position_set

    assert observation.position_snapshot_id.startswith("pset_")
    assert observation.position_snapshot_id == derive_position_set_id(
        ACCOUNT_A, T0_MS, "MT5_POSITIONS_GET")
    assert observation.to_dict()["record_kind"] == "POSITION_SET"
    # Every record of one cycle shares the observation-cycle identity.
    assert cycle.observation_id == observation.observation_id
    assert cycle.observation_id == cycle.positions[0].observation_id
    assert cycle.observation_id == cycle.open_risk.observation_id


def test_an_incomplete_set_never_claims_a_ticket_is_open():
    cycle = _observe(source=FakePositionSource(error=RuntimeError("read failed")))
    observation = cycle.position_set
    # Nothing may be inferred as open from a set that could not be read.
    assert observation.contains(1) is False
    assert observation.position_tickets == ()
    assert observation.position_set_complete is False


def test_aggregate_counts_are_internally_consistent():
    rows = [_row(1), _row(2), _row(3, sl=0.0)]      # 2 protected, 1 unprotected
    aggregate = _observe(rows=rows).open_risk

    assert aggregate.open_position_count == 3
    assert aggregate.protected_position_count == 2
    assert aggregate.unprotected_position_count == 1
    assert aggregate.protected_position_count + \
        aggregate.unprotected_position_count == aggregate.open_position_count
    assert aggregate.unknown_risk_position_count == 1
    assert aggregate.consistency_ok is True
    assert aggregate.invalid_fields == ()


def test_aggregate_risk_is_complete_only_when_every_position_is_provable():
    aggregate = _observe(rows=[_row(1), _row(2)]).open_risk
    assert aggregate.unknown_risk_position_count == 0
    assert aggregate.risk_complete is True
    assert aggregate.total_open_risk == pytest.approx(100.0)
    assert aggregate.has_authoritative_total is True


def test_aggregate_is_incomplete_when_any_single_position_risk_is_unknown():
    """One unknown position destroys the authority of the account total."""
    aggregate = _observe(rows=[_row(1), _row(2, sl=0.0), _row(3)]).open_risk

    assert aggregate.open_position_count == 3
    assert aggregate.unknown_risk_position_count == 1
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.status is PositionStatus.UNPROTECTED


def test_total_open_risk_is_none_whenever_risk_is_incomplete():
    for rows in ([_row(1, sl=0.0)],                 # unprotected
                 [_row(1, volume=float("nan"))],   # invalid volume
                 [_row(1, price_open=float("inf"))]):
        aggregate = _observe(rows=rows).open_risk
        assert aggregate.risk_complete is False
        assert aggregate.total_open_risk is None


def test_known_open_risk_remains_exposed_while_the_total_is_withheld():
    """An operator can still see the provable floor, clearly labelled."""
    aggregate = _observe(rows=[_row(1), _row(2, sl=0.0), _row(3)]).open_risk

    # 100.00 GBP provable from the two protected positions...
    assert aggregate.known_open_risk == pytest.approx(100.0)
    # ...but the account total is NOT claimed, because one position is unbounded.
    assert aggregate.total_open_risk is None
    assert aggregate.risk_complete is False
    assert aggregate.has_authoritative_total is False


def test_known_open_risk_is_withheld_when_the_aggregate_is_self_inconsistent():
    """A self-inconsistent aggregate publishes no money at all."""
    observation = PositionSetObservation(
        account_id="METAQUOTES", broker="MetaQuotes",
        server="MetaQuotes-Demo", login=5012345,
        position_snapshot_id="pset_mismatch", observation_id="obs_mismatch",
        observed_at_utc=T0_ISO, observed_at_utc_ms=T0_MS,
        source="MT5_POSITIONS_GET", position_set_complete=True,
        open_position_count=2, position_tickets=(1, 2),
        status=PositionStatus.COMPLETE)
    cycle = _observe(rows=[_row(1)])
    aggregate = aggregate_open_risk(
        ACCOUNT_A, observation, cycle.positions, currency="GBP")

    assert aggregate.status is PositionStatus.INVALID
    assert aggregate.consistency_ok is False
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.known_open_risk is None
    assert aggregate.invalid_fields


def test_an_account_snapshot_from_another_account_is_never_attributed():
    """The set observation must belong to the account being aggregated."""
    foreign = PositionSetObservation(
        account_id="ADMIRALS", broker="Admirals", server="Admirals-Server",
        login=9988776, position_snapshot_id="pset_b", observation_id="obs_b",
        observed_at_utc=T0_ISO, observed_at_utc_ms=T0_MS,
        source="MT5_POSITIONS_GET", position_set_complete=True,
        open_position_count=0, position_tickets=(), status=PositionStatus.COMPLETE)
    with pytest.raises(AccountIdentityError):
        aggregate_open_risk(ACCOUNT_A, foreign, [])


# ═══════════════════════════════════════════════════════════════════════════
# 34–40. ACCOUNT LINKAGE, FRESHNESS, RESTART, CLOSURE AND IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════


class _FakeAccountProducer:
    """Minimal Block 2A stand-in that returns a real 2A-shaped snapshot id."""

    def __init__(self, currency="GBP", account_id="METAQUOTES") -> None:
        self.currency = currency
        self.account_id = account_id
        self.calls: list[str] = []

    def observe(self, account):
        from core.risk.account_snapshot import capture_account_snapshot

        self.calls.append(account.account_id)
        payload = {
            "login": account.login, "server": account.server,
            "currency": self.currency, "leverage": 100, "balance": 10_000.0,
            "equity": 10_000.0, "credit": 0.0, "profit": 0.0, "margin": 0.0,
            "margin_free": 10_000.0, "margin_level": 0.0, "trade_allowed": True,
            "trade_expert": True, "trade_mode": 0, "margin_mode": 0,
            "stopout_mode": 0, "stopout_call": 50.0, "stopout_so": 30.0,
        }

        class _Source:
            def read_account_info(self):
                return payload

        return capture_account_snapshot(
            account, _Source(), clock=lambda: T0, source_name="MT5_ACCOUNT_INFO")


def test_observation_links_to_its_account_snapshot_by_exact_id():
    """The link is an exact governed id, never a timestamp-nearest join."""
    producer = _FakeAccountProducer(currency="GBP")
    snapshot, cycle = observe_account_risk(
        ACCOUNT_A, FakePositionSource([_row()]),
        account_producer=producer, clock=lambda: T0,
        spec_source=_spec_source(), persist=False)

    assert producer.calls == ["METAQUOTES"]
    # The 2B aggregate carries the EXACT 2A snapshot identity...
    assert cycle.open_risk.account_snapshot_id == snapshot.snapshot_id
    assert snapshot.snapshot_id.startswith("asnap_")
    # ...and the account's own currency, taken from that same snapshot.
    assert cycle.open_risk.currency == "GBP"
    assert cycle.positions[0].risk_currency == "GBP"
    # One clock reading -> one shared observation-cycle identity.
    assert cycle.observation_id == cycle.open_risk.observation_id


def test_each_account_links_to_its_own_account_snapshot_only():
    a_producer = _FakeAccountProducer("GBP")
    b_producer = _FakeAccountProducer("USD")
    a_snapshot, a = observe_account_risk(
        ACCOUNT_A, FakePositionSource([_row(1)]), account_producer=a_producer,
        clock=lambda: T0, spec_source=_spec_source(), persist=False)
    b_snapshot, b = observe_account_risk(
        ACCOUNT_B, FakePositionSource([_row(2)]), account_producer=b_producer,
        clock=lambda: T0, spec_source=_spec_source(), persist=False)

    assert a.open_risk.account_snapshot_id == a_snapshot.snapshot_id
    assert b.open_risk.account_snapshot_id == b_snapshot.snapshot_id
    assert a.open_risk.account_snapshot_id != b.open_risk.account_snapshot_id
    assert a.open_risk.account_id == "METAQUOTES"
    assert b.open_risk.account_id == "ADMIRALS"
    assert a.open_risk.currency == "GBP"
    assert b.open_risk.currency == "USD"


def test_account_linkage_is_never_a_timestamp_nearest_join():
    """Two accounts observed at the SAME instant keep separate links."""
    _, a = observe_account_risk(
        ACCOUNT_A, FakePositionSource([_row(1)]),
        account_producer=_FakeAccountProducer("GBP"), clock=lambda: T0,
        spec_source=_spec_source(), persist=False)
    _, b = observe_account_risk(
        ACCOUNT_B, FakePositionSource([_row(1)]),
        account_producer=_FakeAccountProducer("USD"), clock=lambda: T0,
        spec_source=_spec_source(), persist=False)

    # Identical observation instants, yet the links are provably distinct.
    assert a.observed_at_utc_ms == b.observed_at_utc_ms
    assert a.open_risk.account_snapshot_id != b.open_risk.account_snapshot_id
    assert a.open_risk.observation_id != b.open_risk.observation_id


def test_an_account_snapshot_for_a_different_account_is_rejected():
    foreign = _FakeAccountProducer(currency="GBP", account_id="ADMIRALS")
    snapshot = foreign.observe(ACCOUNT_B)          # a valid B snapshot
    with pytest.raises(AccountIdentityError):
        observe_account_risk(
            ACCOUNT_A, FakePositionSource([_row()]),
            account_snapshot=snapshot, clock=lambda: T0, persist=False)


def test_a_stale_observation_is_evaluated_as_stale_not_as_current():
    """Freshness is a read-time fact; it never rewrites the stored observation."""
    cycle = _observe(rows=[_row()])
    snapshot, aggregate = cycle.positions[0], cycle.open_risk

    fresh_now = T0_MS + 5_000
    assert snapshot.evaluate(now_ms=fresh_now, threshold_ms=30_000).status is \
        PositionStatus.COMPLETE
    assert aggregate.evaluate(now_ms=fresh_now, threshold_ms=30_000).status is \
        PositionStatus.COMPLETE

    stale_now = T0_MS + 120_000
    assert snapshot.evaluate(now_ms=stale_now, threshold_ms=30_000).status is \
        PositionStatus.STALE
    assert aggregate.evaluate(now_ms=stale_now, threshold_ms=30_000).status is \
        PositionStatus.STALE
    # STALE is never persisted: the stored status is untouched.
    assert snapshot.status is PositionStatus.COMPLETE
    assert snapshot.to_dict()["status"] == "COMPLETE"
    assert "stale" not in snapshot.to_dict()
    assert "age_ms" not in snapshot.to_dict()


def test_fresh_observation_stays_complete_and_age_is_reported():
    snapshot = _one()
    assert snapshot.age_ms(now_ms=T0_MS + 4_000) == 4_000
    assert snapshot.is_stale(now_ms=T0_MS + 4_000, threshold_ms=30_000) is False


def test_freshness_threshold_rejects_a_non_positive_value():
    snapshot = _one()
    with pytest.raises(ValueError):
        snapshot.is_stale(now_ms=T0_MS, threshold_ms=0)
    with pytest.raises(ValueError):
        snapshot.evaluate(now_ms=T0_MS, threshold_ms=-1)


def test_restart_preserves_durable_history_and_reads_the_latest_per_account(
    tmp_path,
):
    """A new store instance rebuilds identical state from local evidence."""
    _persist(_observe(ACCOUNT_A, rows=[_row(11)], currency="GBP"), tmp_path)
    clock = FixedClock(T0)
    clock.advance(minutes=1)
    _persist(
        _observe(ACCOUNT_B, rows=[_row(22), _row(33)], clock=clock,
                 currency="USD"),
        tmp_path)

    store = _store(tmp_path)                       # simulates a restart
    assert store.account_ids() == ("ADMIRALS", "METAQUOTES")
    assert store.open_tickets("METAQUOTES") == (11,)
    assert store.open_tickets("ADMIRALS") == (22, 33)
    assert store.latest_open_risk("ADMIRALS").total_open_risk == \
        pytest.approx(100.0)
    assert store.latest_position("ADMIRALS", 22).position_ticket == 22
    # The append-only history is preserved, not truncated by the restart.
    assert len(list((tmp_path / "2026-10-02").glob("*.jsonl"))) == 2


def test_current_positions_excludes_a_position_closed_on_the_broker(tmp_path):
    """Closure is PROVEN by the next complete set, never inferred from recency."""
    store = _two_cycles(tmp_path, [_row(11), _row(22)], [])
    assert store.open_tickets("METAQUOTES") == ()
    assert store.current_positions("METAQUOTES") == ()
    observation = store.latest_position_set("METAQUOTES")
    assert observation.open_position_count == 0
    assert observation.position_set_complete is True
    # The historical rows are still queryable by exact ticket, just not "open".
    assert store.latest_position("METAQUOTES", 11).position_ticket == 11


def test_a_ticket_absent_from_unrelated_observations_is_not_closure(tmp_path):
    """B's observations can never close or open anything on account A."""
    _persist(_observe(ACCOUNT_A, rows=[_row(11)], currency="GBP"), tmp_path)
    clock = FixedClock(T0)
    clock.advance(minutes=1)
    _persist(_observe(ACCOUNT_B, rows=[], clock=clock, currency="USD"), tmp_path)

    store = _store(tmp_path)
    # A's state is completely unaffected by B's empty observation.
    assert store.open_tickets("METAQUOTES") == (11,)
    assert store.current_positions("METAQUOTES")[0].position_ticket == 11
    assert store.open_tickets("ADMIRALS") == ()


def test_an_incomplete_set_refuses_to_serve_current_positions(tmp_path):
    """After a read failure the consumer is told, not fed stale 'open' rows."""
    _two_cycles(tmp_path, [_row(11)], [_row(11)])
    clock = FixedClock(T0)
    clock.advance(minutes=5)
    _persist(
        _observe(ACCOUNT_A, source=FakePositionSource(error=RuntimeError("x")),
                 clock=clock),
        tmp_path)

    store = _store(tmp_path)
    with pytest.raises(PositionSnapshotNotFound):
        store.current_positions("METAQUOTES")
    with pytest.raises(PositionSnapshotNotFound):
        store.open_tickets("METAQUOTES")
    # The last known position row is still available as history.
    assert store.latest_position("METAQUOTES", 11).position_ticket == 11


def test_repeated_identical_observation_is_idempotent():
    first = _observe(rows=[_row(11)])
    second = _observe(rows=[_row(11)])
    # An exact replay at the same instant is the SAME logical observation.
    assert first.observation_id == second.observation_id
    assert first.positions[0].position_snapshot_id == \
        second.positions[0].position_snapshot_id
    assert first.open_risk.open_risk_id == second.open_risk.open_risk_id
    assert first.position_set.position_snapshot_id == \
        second.position_set.position_snapshot_id


def test_distinct_observation_timestamps_produce_distinct_snapshots():
    clock = FixedClock(T0)
    first = _observe(rows=[_row(11)], clock=clock)
    clock.advance(seconds=5)
    second = _observe(rows=[_row(11)], clock=clock)

    assert first.observation_id != second.observation_id
    assert first.positions[0].position_snapshot_id != \
        second.positions[0].position_snapshot_id
    assert first.open_risk.open_risk_id != second.open_risk.open_risk_id
    assert first.positions[0].observed_at_utc_ms < \
        second.positions[0].observed_at_utc_ms


def test_identity_never_collides_across_accounts_tickets_or_sources():
    shared = (ACCOUNT_A, 11, T0_MS, "MT5_POSITIONS_GET")
    assert derive_position_snapshot_id(*shared) != \
        derive_position_snapshot_id(ACCOUNT_B, 11, T0_MS, "MT5_POSITIONS_GET")
    assert derive_position_snapshot_id(*shared) != \
        derive_position_snapshot_id(ACCOUNT_A, 12, T0_MS, "MT5_POSITIONS_GET")
    assert derive_position_snapshot_id(*shared) != \
        derive_position_snapshot_id(ACCOUNT_A, 11, T0_MS + 1,
                                    "MT5_POSITIONS_GET")
    assert derive_observation_id(ACCOUNT_A, T0_MS, "A") != \
        derive_observation_id(ACCOUNT_A, T0_MS, "B")


def test_identity_never_depends_on_monetary_values():
    """Money is never part of identity: a repriced stop is still one position."""
    cheap = _observe(rows=[_row(11, sl=1.0840)])
    dear = _observe(rows=[_row(11, sl=1.0700)])
    assert cheap.positions[0].position_snapshot_id == \
        dear.positions[0].position_snapshot_id
    assert cheap.positions[0].monetary_risk_to_sl != \
        dear.positions[0].monetary_risk_to_sl


def test_identity_rejects_invalid_tickets_and_missing_source():
    with pytest.raises(PositionSnapshotError):
        derive_position_snapshot_id(ACCOUNT_A, 0, T0_MS, "S")
    with pytest.raises(PositionSnapshotError):
        derive_position_snapshot_id(ACCOUNT_A, -5, T0_MS, "S")
    with pytest.raises(PositionSnapshotError):
        derive_position_snapshot_id(ACCOUNT_A, "11", T0_MS, "S")
    with pytest.raises(PositionSnapshotError):
        derive_position_snapshot_id(ACCOUNT_A, True, T0_MS, "S")
    with pytest.raises(ValueError):
        derive_observation_id(ACCOUNT_A, T0_MS, "   ")


# ═══════════════════════════════════════════════════════════════════════════
# 41–44. DURABLE CANONICAL PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════


def test_both_2b_datasets_are_registered_account_scoped_and_governed():
    for dataset in (DATASET, OPEN_RISK_DATASET):
        assert dataset in PRODUCTION_SCHEMA_REGISTRY
        assert not is_symbol_scoped(dataset)   # account-scoped, never symbol
        assert dataset in ACCOUNT_SCOPED_DATASETS
    assert current_schema(DATASET) == SCHEMA_VERSION
    assert current_schema(OPEN_RISK_DATASET) == OPEN_RISK_SCHEMA_VERSION
    # Each dataset has exactly ONE governed exact identity.
    assert EXACT_IDENTITY_FIELDS[DATASET] == ("account_id", "position_snapshot_id")
    assert EXACT_IDENTITY_FIELDS[OPEN_RISK_DATASET] == ("account_id",
                                                         "open_risk_id")


def test_every_record_kind_enters_the_certified_canonical_outbox(
    tmp_path, isolated_outbox,
):
    """Position rows, the SET boundary and the aggregate all reach the outbox."""
    from core.canonical_delivery import get_delivery_outbox

    _persist(_observe(rows=[_row(11), _row(22)]), tmp_path)
    records = get_delivery_outbox().records()
    # 1 position-set boundary + 2 position rows + 1 aggregate.
    assert len(records) == 4
    by_dataset = {}
    for record in records:
        assert record.delivery_state is DeliveryState.PENDING
        assert record.canonical_ack is None
        by_dataset.setdefault(record.dataset, []).append(record)
    assert len(by_dataset[DATASET]) == 3
    assert len(by_dataset[OPEN_RISK_DATASET]) == 1

    # The set boundary and the position rows carry DIFFERENT governed ids.
    set_ids = {r.record_identity["position_snapshot_id"]
               for r in by_dataset[DATASET]}
    assert len(set_ids) == 3
    for record in by_dataset[DATASET]:
        assert record.record_identity["account_id"] == "METAQUOTES"
        # Account-scoped, date-partitioned: no symbol segment is required.
        assert "symbol=" not in record.canonical_key
        assert "date=2026-10-02" in record.canonical_key
    assert by_dataset[OPEN_RISK_DATASET][0].record_identity["account_id"] == \
        "METAQUOTES"


def test_no_position_row_is_silently_excluded_from_canonical_delivery(
    tmp_path, isolated_outbox,
):
    """An INVALID/UNPROTECTED position row is still delivered, not dropped."""
    from core.canonical_delivery import get_delivery_outbox

    cycle = _observe(rows=[_row(11), _row(22, sl=0.0),
                          _row(33, volume=float("nan"))])
    _persist(cycle, tmp_path)
    payloads = [r.payload for r in get_delivery_outbox().records()
                if r.dataset == DATASET]
    kinds = sorted(p["record_kind"] for p in payloads)
    assert kinds == ["POSITION", "POSITION", "POSITION", "POSITION_SET"]
    # The defective rows are present with their explicit quality status.
    statuses = {p["position_ticket"]: p["status"]
                for p in payloads if p["record_kind"] == "POSITION"}
    assert statuses[22] == "UNPROTECTED"
    assert statuses[33] == "INVALID"


def test_canonical_delivery_handoff_is_prepared_before_the_local_append():
    """The certified pre-write handoff must precede the durable local write."""
    source = inspect.getsource(persist_position_snapshot)
    assert source.index("_append_durable_jsonl(") < source.index("return True")
    helper = inspect.getsource(
        __import__("core.risk.position_snapshot", fromlist=["x"]).__dict__[
            "_append_durable_jsonl"])
    assert helper.index("try_prepare_local_jsonl_handoffs(") < helper.index(
        "os.write(fd")


def test_repeated_identical_observation_is_idempotent_in_the_outbox(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    cycle = _observe(rows=[_row(11)])
    _persist(cycle, tmp_path)
    _persist(cycle, tmp_path)

    records = get_delivery_outbox().records()
    assert len(records) == 3                     # one logical obligation each
    assert len({r.idempotency_key for r in records}) == 3
    # The append-only local evidence keeps BOTH writes.
    lines = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 6


def test_distinct_timestamps_create_distinct_delivery_records(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    clock = FixedClock(T0)
    _persist(_observe(rows=[_row(11)], clock=clock), tmp_path)
    clock.advance(seconds=5)
    _persist(_observe(rows=[_row(11)], clock=clock), tmp_path)

    records = get_delivery_outbox().records()
    assert len(records) == 6
    assert len({r.idempotency_key for r in records}) == 6


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
        md5 = self._md5(stored["Body"]).hexdigest()

        class _Body:
            def read(inner_self):
                return stored["Body"]

        return {"Body": _Body(), "Metadata": stored["Metadata"],
                "ETag": '"' + md5 + '"', "VersionId": "v1"}


class _NoSuchKey(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {
            "Error": {"Code": "NoSuchKey", "Message": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }


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


def test_canonical_ack_does_not_change_the_payload(tmp_path, isolated_outbox):
    """A verified canonical ACK leaves every risk number byte-identical."""
    from core.canonical_delivery import get_delivery_outbox
    from core.canonical_delivery_worker import CanonicalDeliveryWorker

    cycle = _observe(rows=[_row(11, sl=0.0), _row(22)])
    _persist(cycle, tmp_path)
    outbox = get_delivery_outbox()
    before = {r.idempotency_key: r.payload for r in outbox.records()}

    results = CanonicalDeliveryWorker(
        outbox, s3_client=_AckingS3()).drain(max_items=10)

    assert len(results) == 4        # 1 set + 2 positions + 1 aggregate
    for record in outbox.records():
        assert record.delivery_state is DeliveryState.ACKNOWLEDGED
        assert record.canonical_ack is not None
        assert record.lifecycle_obligation_id
        assert record.reconciliation_state == "RECONCILED"
        from core.lifecycle_evidence_obligations import obligation_ledger
        obligation = obligation_ledger().get(record.lifecycle_obligation_id)
        assert obligation is not None
        assert dict(obligation.expected_identity) == dict(record.record_identity)
        # The numeric payload is untouched by the acknowledgement.
        assert record.payload == before[record.idempotency_key]
    aggregate = next(r for r in outbox.records()
                     if r.dataset == OPEN_RISK_DATASET)
    assert aggregate.payload["total_open_risk"] is None   # still withheld
    assert aggregate.payload["known_open_risk"] == pytest.approx(50.0)
    assert aggregate.payload["currency"] == "GBP"


def test_missing_s3_does_not_lose_the_local_telemetry(tmp_path, isolated_outbox):
    """Local durable evidence survives even when canonical delivery cannot."""
    from core.canonical_delivery import get_delivery_outbox

    cycle = _observe(rows=[_row(11)])
    _persist(cycle, tmp_path)

    path = tmp_path / "2026-10-02" / "METAQUOTES.jsonl"
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    kinds = sorted(r["record_kind"] for r in records)
    assert kinds == ["ACCOUNT_OPEN_RISK", "POSITION", "POSITION_SET"]
    position = next(r for r in records if r["record_kind"] == "POSITION")
    assert position["monetary_risk_to_sl"] == pytest.approx(50.0)
    # Nothing was delivered, but nothing was lost either.
    assert all(r.delivery_state is DeliveryState.PENDING
               for r in get_delivery_outbox().records())
    store = _store(tmp_path)
    assert store.latest_open_risk("METAQUOTES").total_open_risk == \
        pytest.approx(50.0)


def test_expired_credentials_leave_delivery_pending_and_retryable(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox
    from core.canonical_delivery_worker import (
        CanonicalDeliveryWorker, classify_delivery_failure,
    )

    _persist(_observe(rows=[_row(11)]), tmp_path)
    outbox = get_delivery_outbox()

    results = CanonicalDeliveryWorker(
        outbox, s3_client=_ExpiredS3()).drain(max_items=10)

    assert results
    assert all(r.state_after is DeliveryState.RETRYABLE_FAILURE for r in results)
    assert classify_delivery_failure(
        _ExpiredCredentials()).category == "RETRYABLE"
    # The obligations remain durable and retryable.
    assert all(r.delivery_state is DeliveryState.RETRYABLE_FAILURE
               for r in outbox.records())
    assert (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").exists()
    assert _store(tmp_path).open_tickets("METAQUOTES") == (11,)


# ═══════════════════════════════════════════════════════════════════════════
# 45–50. MULTI-ACCOUNT ISOLATION, VALUE DEFECTS AND RUNTIME HOOKS
# ═══════════════════════════════════════════════════════════════════════════


def test_multi_account_fan_out_is_isolated_per_account(tmp_path):
    """Two accounts, shared tickets and symbols, fully separate state."""
    _persist(_observe(ACCOUNT_A, rows=[_row(700), _row(701)], currency="GBP"),
              tmp_path)
    _persist(_observe(ACCOUNT_B, rows=[_row(700)], currency="USD"), tmp_path)

    store = _store(tmp_path)
    assert store.account_ids() == ("ADMIRALS", "METAQUOTES")
    assert store.open_tickets("METAQUOTES") == (700, 701)
    assert store.open_tickets("ADMIRALS") == (700,)
    # The SHARED ticket resolves to each account's own row, never the other's.
    assert store.latest_position("METAQUOTES", 700).account_id == "METAQUOTES"
    assert store.latest_position("ADMIRALS", 700).account_id == "ADMIRALS"
    assert store.latest_open_risk("METAQUOTES").currency == "GBP"
    assert store.latest_open_risk("ADMIRALS").currency == "USD"
    assert store.latest_open_risk("METAQUOTES").total_open_risk == \
        pytest.approx(100.0)
    assert store.latest_open_risk("ADMIRALS").total_open_risk == \
        pytest.approx(50.0)


def test_no_cross_account_fallback_for_an_unknown_account(tmp_path):
    """An unknown account raises; it never borrows another account's state."""
    _persist(_observe(ACCOUNT_A, rows=[_row(700)], currency="GBP"), tmp_path)
    store = _store(tmp_path)

    for accessor in (store.latest_position_set, store.latest_open_risk):
        with pytest.raises(PositionSnapshotNotFound):
            accessor("ADMIRALS")
    with pytest.raises(PositionSnapshotNotFound):
        store.current_positions("ADMIRALS")
    with pytest.raises(PositionSnapshotNotFound):
        store.latest_position("METAQUOTES", 999_999)
    with pytest.raises(PositionSnapshotNotFound):
        store.latest_position("ADMIRALS", 700)     # A's ticket on B


def test_no_cross_account_fallback_after_a_restart(tmp_path):
    _persist(_observe(ACCOUNT_A, rows=[_row(700)], currency="GBP"), tmp_path)
    restarted = _store(tmp_path)
    assert restarted.open_tickets("METAQUOTES") == (700,)
    with pytest.raises(PositionSnapshotNotFound):
        restarted.open_tickets("ADMIRALS")


@pytest.mark.parametrize("field,contract_field", [
    ("price", "current_price"),
    ("price_open", "open_price"),
    ("sl", "sl"),
    ("tp", "tp"),
    ("profit", "floating_pnl"),
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_position_values_are_rejected_as_invalid(
    field, contract_field, bad,
):
    """NaN/inf never become money, geometry or a status of "fine"."""
    snapshot = _one(**{field: bad})
    assert snapshot.status is PositionStatus.INVALID
    assert contract_field in snapshot.invalid_fields
    assert getattr(snapshot, contract_field) is None
    # An invalid geometry can never yield a risk number.
    assert snapshot.monetary_risk_to_sl is None
    assert snapshot.has_known_risk is False


@pytest.mark.parametrize("bad_volume", [
    0.0, -0.10, 0.001, 500.0, 0.10005,
])
def test_impossible_volume_is_rejected_against_broker_specs(bad_volume):
    """The observed volume is validated against the broker, never normalised."""
    cycle = _observe(rows=[_row(volume=bad_volume)])
    snapshot = cycle.positions[0]

    assert snapshot.status is PositionStatus.INVALID
    assert "volume" in snapshot.invalid_fields
    # The observed value is preserved verbatim, not repaired to the grid.
    assert snapshot.volume == bad_volume
    assert snapshot.monetary_risk_to_sl is None
    assert cycle.open_risk.total_open_risk is None
    assert cycle.open_risk.risk_complete is False


def test_valid_broker_grid_volumes_are_accepted_verbatim():
    for volume in (0.01, 0.10, 0.25, 1.00, 5.50):
        snapshot = _one(volume=volume)
        assert snapshot.status is PositionStatus.COMPLETE
        assert snapshot.volume == volume


def test_position_set_mismatch_makes_the_aggregate_invalid():
    """A set that disagrees with its rows yields INVALID and no money at all."""
    cycle = _observe(rows=[_row(11)])
    mismatched = PositionSetObservation(
        account_id="METAQUOTES", broker="MetaQuotes",
        server="MetaQuotes-Demo", login=5012345,
        position_snapshot_id="pset_bad", observation_id=cycle.observation_id,
        observed_at_utc=T0_ISO, observed_at_utc_ms=T0_MS,
        source="MT5_POSITIONS_GET", position_set_complete=True,
        open_position_count=2, position_tickets=(11, 12),
        status=PositionStatus.COMPLETE)
    aggregate = aggregate_open_risk(
        ACCOUNT_A, mismatched, cycle.positions, currency="GBP")

    assert aggregate.status is PositionStatus.INVALID
    assert aggregate.consistency_ok is False
    assert aggregate.risk_complete is False
    assert aggregate.total_open_risk is None
    assert aggregate.known_open_risk is None
    assert any("POSITION_SET_TICKET_MISMATCH" in f
               for f in aggregate.invalid_fields)


def test_a_position_row_from_another_account_invalidates_the_aggregate():
    """Cross-account contamination is detected, never silently aggregated."""
    foreign = _observe(ACCOUNT_B, rows=[_row(11)], currency="USD")
    cycle = _observe(ACCOUNT_A, rows=[_row(11)], currency="GBP")
    aggregate = aggregate_open_risk(
        ACCOUNT_A, cycle.position_set, [*cycle.positions, *foreign.positions],
        currency="GBP")

    assert aggregate.status is PositionStatus.INVALID
    assert aggregate.total_open_risk is None
    assert "ACCOUNT_ID_MISMATCH_IN_POSITION_ROWS" in aggregate.invalid_fields


def test_snapshot_interval_is_configurable_and_defaults_are_bounded():
    assert snapshot_interval_ms({}) == 15_000
    assert snapshot_interval_ms(
        {"POSITION_SNAPSHOT_INTERVAL_SECONDS": "45"}) == 45_000
    # Non-positive / malformed configuration falls back to the documented default.
    assert snapshot_interval_ms(
        {"POSITION_SNAPSHOT_INTERVAL_SECONDS": "0"}) == 15_000
    assert snapshot_interval_ms(
        {"POSITION_SNAPSHOT_INTERVAL_SECONDS": "x"}) == 15_000


# ═══════════════════════════════════════════════════════════════════════════
# 50. RUNTIME CADENCE HOOKS
# ═══════════════════════════════════════════════════════════════════════════


def _producer(rows, tmp_path, *, persist=False, clock=None):
    return PositionSnapshotProducer(
        FakePositionSource(rows), base_dir=tmp_path, open_risk_dir=tmp_path,
        clock=clock or (lambda: T0), spec_source=_spec_source(),
        persist=persist)


def _heartbeat(tmp_path, sources, identities, *, currencies=None,
               interval_ms=60_000, rows=None):
    return PositionSnapshotHeartbeat(
        _producer(rows if rows is not None else [_row(11)], tmp_path),
        _store(tmp_path), sources=sources, identities=identities,
        interval_ms=interval_ms, currencies=currencies)


def test_producer_owns_the_read_persist_path_and_is_strictly_read_only(tmp_path):
    """Downstream consumers never call raw positions_get themselves."""
    clock = FixedClock(T0)
    producer = _producer([_row(11)], tmp_path, clock=clock)

    first = producer.observe(ACCOUNT_A, currency="GBP")
    clock.advance(seconds=1)
    second = producer.observe(ACCOUNT_A, currency="GBP")

    assert first.observation_id != second.observation_id
    assert first.observed_at_utc_ms < second.observed_at_utc_ms
    # One observation per call: explicit and caller-driven, never self-triggered.
    assert len(first.positions) == 1
    # The producer exposes no order-placing, modifying or closing capability.
    for forbidden in ("order_send", "close", "modify", "place", "execute"):
        assert not hasattr(producer, forbidden)


def test_producer_persists_a_whole_cycle_through_the_canonical_path(
    tmp_path, isolated_outbox,
):
    from core.canonical_delivery import get_delivery_outbox

    _producer([_row(11), _row(22)], tmp_path, persist=True,
              clock=lambda: T0).observe(ACCOUNT_A, currency="GBP")

    datasets = sorted(r.dataset for r in get_delivery_outbox().records())
    assert datasets == sorted([DATASET, DATASET, DATASET, OPEN_RISK_DATASET])
    assert _store(tmp_path).open_tickets("METAQUOTES") == (11, 22)


def test_heartbeat_is_monotonic_and_spawns_no_extra_thread(tmp_path):
    import threading

    before = threading.active_count()
    heartbeat = _heartbeat(
        tmp_path, {"METAQUOTES": FakePositionSource([_row(11)])},
        [ACCOUNT_A])
    assert heartbeat.interval_ms == 60_000
    # Constructing it must not start a thread; start() is explicit/idempotent.
    assert threading.active_count() == before

    heartbeat.start()
    try:
        assert threading.active_count() == before + 1
        heartbeat.start()      # idempotent: no uncontrolled thread proliferation
        assert threading.active_count() == before + 1
    finally:
        heartbeat.stop()
    assert heartbeat.next_due_monotonic() > 0


def test_heartbeat_next_due_is_derived_from_the_previous_due_time(tmp_path):
    """A late cycle never accumulates drift."""
    heartbeat = _heartbeat(
        tmp_path, {"METAQUOTES": FakePositionSource([_row(11)])},
        [ACCOUNT_A], interval_ms=30_000)
    assert heartbeat.next_due_from(10.0) == 40.0
    assert heartbeat.next_due_from(20.0) == 50.0


def test_heartbeat_tick_observes_every_account_and_isolates_failures(tmp_path):
    healthy = FakePositionSource([_row(11)])
    broken = FakePositionSource(error=RuntimeError("B_DISCONNECTED"))
    store = _store(tmp_path)
    heartbeat = PositionSnapshotHeartbeat(
        _producer([_row(11)], tmp_path), store,
        sources={"METAQUOTES": healthy, "ADMIRALS": broken},
        identities=[ACCOUNT_A, ACCOUNT_B],
        currencies={"METAQUOTES": "GBP", "ADMIRALS": "USD"})

    results = heartbeat.tick()

    assert [c.account_id for c in results] == ["METAQUOTES", "ADMIRALS"]
    # A's cycle is valid and complete; B's failure is isolated and explicit.
    assert results[0].open_risk.risk_complete is True
    assert results[0].open_risk.total_open_risk == pytest.approx(50.0)
    assert results[1].open_risk.status is PositionStatus.UNAVAILABLE
    assert results[1].open_risk.total_open_risk is None
    # Each account was read through its OWN source boundary, never the other's.
    assert store.account_ids() == ("ADMIRALS", "METAQUOTES")
    assert store.latest_open_risk("METAQUOTES").currency == "GBP"
    assert store.latest_open_risk("METAQUOTES").total_open_risk == \
        pytest.approx(50.0)
    assert store.latest_open_risk("ADMIRALS").status is PositionStatus.UNAVAILABLE


def test_heartbeat_rejects_a_non_positive_interval(tmp_path):
    with pytest.raises(ValueError):
        _heartbeat(tmp_path, {"METAQUOTES": FakePositionSource([_row(11)])},
                   [ACCOUNT_A], interval_ms=0)


def test_heartbeat_requires_a_source_for_every_configured_account(tmp_path):
    with pytest.raises(PositionSnapshotError):
        _heartbeat(tmp_path, {}, [ACCOUNT_A]).tick()


def test_store_health_reports_per_account_age_status_and_counts(tmp_path):
    _persist(_observe(rows=[_row(11), _row(22, sl=0.0)]), tmp_path)
    health = _store(tmp_path).health(now_ms=T0_MS + 1_000)

    assert health["accounts_observed"] == 1
    assert health["freshness_threshold_ms"] == 60_000
    row = health["accounts"]["METAQUOTES"]
    assert row["status"] == "UNPROTECTED"
    assert row["open_position_count"] == 2
    assert row["unprotected_position_count"] == 1
    assert row["unknown_risk_position_count"] == 1
    assert row["known_open_risk"] == pytest.approx(50.0)
    assert row["total_open_risk"] is None
    assert row["risk_complete"] is False
    assert row["currency"] == "GBP"
    assert health["accounts_with_unprotected_positions"] == 1
    assert health["accounts_with_unknown_risk"] == 1
    assert health["accounts_with_authoritative_total"] == 0
    assert health["source_error_count"] == 0


def test_store_health_surfaces_a_source_failure(tmp_path):
    _persist(
        _observe(ACCOUNT_A, source=FakePositionSource(error=RuntimeError("x"))),
        tmp_path)
    health = _store(tmp_path).health(now_ms=T0_MS)
    row = health["accounts"]["METAQUOTES"]
    assert row["status"] == "UNAVAILABLE"
    assert row["source_error"]
    assert row["total_open_risk"] is None
    assert health["source_error_count"] == 1


def test_store_rejects_a_non_positive_freshness_threshold(tmp_path):
    with pytest.raises(ValueError):
        PositionSnapshotStore(base_dir=tmp_path, threshold_ms=0)


def test_records_round_trip_through_their_persisted_payloads(tmp_path):
    cycle = _observe(rows=[_row(11), _row(22, sl=0.0)])
    _persist(cycle, tmp_path)

    for snapshot in cycle.positions:
        restored = PositionSnapshot.from_dict(snapshot.to_dict())
        assert restored == snapshot
    assert PositionSetObservation.from_dict(
        cycle.position_set.to_dict()) == cycle.position_set
    assert AccountOpenRiskSnapshot.from_dict(
        cycle.open_risk.to_dict()) == cycle.open_risk


def test_persisted_payload_is_strict_json_without_non_finite_values(
    tmp_path, isolated_outbox,
):
    """The durable line is always strict JSON, even for a defective row."""
    cycle = _observe(rows=[_row(11, volume=float("nan"))])
    _persist(cycle, tmp_path)
    line = (tmp_path / "2026-10-02" / "METAQUOTES.jsonl").read_text(
        encoding="utf-8").strip()
    assert "NaN" not in line and "Infinity" not in line
    for payload in (json.loads(l) for l in line.splitlines() if l.strip()):
        assert "NaN" not in json.dumps(payload, allow_nan=False)


def test_corrupt_local_lines_are_skipped_without_breaking_the_index(tmp_path):
    """One unusable line must never destroy the rest of the durable evidence."""
    _persist(_observe(rows=[_row(11)]), tmp_path)
    path = tmp_path / "2026-10-02" / "METAQUOTES.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
        handle.write(json.dumps({"record_kind": "POSITION"}) + "\n")

    store = _store(tmp_path)
    assert store.open_tickets("METAQUOTES") == (11,)
    assert store.latest_position("METAQUOTES", 11).position_ticket == 11
    assert store.latest_open_risk("METAQUOTES").total_open_risk == \
        pytest.approx(50.0)


def test_store_rebuilds_from_canonical_local_evidence_on_demand(tmp_path):
    """The index is a rebuildable convenience, never a second source of truth."""
    _persist(_observe(rows=[_row(11)]), tmp_path)
    store = _store(tmp_path)
    store.rebuild()
    assert store.open_tickets("METAQUOTES") == (11,)
    assert store.current_positions("METAQUOTES")[0].position_ticket == 11


def test_position_contract_module_has_no_direct_s3_put():
    """Telemetry reaches S3 only through the certified Block 1 outbox."""
    source = inspect.getsource(
        __import__("core.risk.position_snapshot", fromlist=["x"]))
    for forbidden in ("put_object", "boto3.client", "boto3.resource",
                      "import boto3"):
        assert forbidden not in source


# ═══════════════════════════════════════════════════════════════════════════
# REPRESENTATIVE ACCEPTANCE — TWO ACCOUNTS, THEN A LOST STOP, THEN A CLOSE
# ═══════════════════════════════════════════════════════════════════════════


def _nas100_sell(ticket: int) -> dict:
    return {
        "ticket": ticket, "symbol": "NAS100", "type": 1, "volume": 1.0,
        "price_open": 18000.0, "price": 18010.0, "sl": 18100.0,
        "tp": 0.0, "magic": 999, "comment": "block2b",
        "time": 1_700_000_100, "time_msc": 1_700_000_100_000,
        "order": ticket, "profit": -90.0,
    }


def test_representative_two_account_acceptance_then_lost_stop_then_close(
    tmp_path,
):
    """A: GBP EURUSD BUY 0.10 with SL. B: USD NAS100 SELL with SL.

    Step 1 proves exact ownership and complete risk in each account currency.
    Step 2 removes A's stop: A becomes UNPROTECTED and withholds its total,
    while B is untouched.
    Step 3 closes B: the next complete set proves zero open positions and no
    stale position remains current.
    """
    clock = FixedClock(T0)
    a_rows = [_row(101, symbol="EURUSD", volume=0.10, price_open=1.0850,
                   price=1.0860, sl=1.0800, profit=-50.0)]
    b_rows = [_nas100_sell(202)]

    # ── STEP 1: both accounts valid, protected, provably priced.
    _persist(_observe(ACCOUNT_A, a_rows, clock=clock, spec=FX_SPEC,
                      currency="GBP"), tmp_path)
    _persist(_observe(ACCOUNT_B, b_rows, clock=clock, spec=INDEX_SPEC,
                      currency="USD"), tmp_path)

    store = _store(tmp_path)
    a_snapshot = store.latest_position("METAQUOTES", 101)
    b_snapshot = store.latest_position("ADMIRALS", 202)

    # Exact account ownership.
    assert a_snapshot.account_id == "METAQUOTES"
    assert a_snapshot.ownership_key == ("METAQUOTES", 101)
    assert b_snapshot.account_id == "ADMIRALS"
    assert b_snapshot.ownership_key == ("ADMIRALS", 202)
    assert store.open_tickets("METAQUOTES") == (101,)
    assert store.open_tickets("ADMIRALS") == (202,)

    # Exact monetary risk, each in its OWN account currency.
    assert a_snapshot.side is PositionSide.BUY
    assert a_snapshot.risk_currency == "GBP"
    assert a_snapshot.monetary_risk_to_sl == pytest.approx(50.0)     # GBP
    assert b_snapshot.side is PositionSide.SELL
    assert b_snapshot.risk_currency == "USD"
    assert b_snapshot.monetary_risk_to_sl == pytest.approx(500.0)    # USD
    assert store.latest_open_risk("METAQUOTES").currency == "GBP"
    assert store.latest_open_risk("ADMIRALS").currency == "USD"

    # Aggregate risk COMPLETE for both accounts.
    for account in ("METAQUOTES", "ADMIRALS"):
        aggregate = store.latest_open_risk(account)
        assert aggregate.risk_complete is True
        assert aggregate.status is PositionStatus.COMPLETE
        assert aggregate.protected_position_count == 1
        assert aggregate.unprotected_position_count == 0
        assert aggregate.unknown_risk_position_count == 0
        assert aggregate.position_set_complete is True
        assert aggregate.has_authoritative_total is True

    # ── STEP 2: A's stop disappears. A must NOT understate its risk.
    clock.advance(minutes=1)
    _persist(_observe(ACCOUNT_A, [_row(101, volume=0.10, price_open=1.0850,
                                       price=1.0860, sl=0.0, profit=-50.0)],
                      clock=clock, spec=FX_SPEC, currency="GBP"), tmp_path)

    store = _store(tmp_path)
    a_after = store.latest_open_risk("METAQUOTES")
    a_position = store.latest_position("METAQUOTES", 101)

    assert a_position.status is PositionStatus.UNPROTECTED
    assert a_position.has_stop is False
    assert a_position.monetary_risk_to_sl is None
    assert a_after.status is PositionStatus.UNPROTECTED
    assert a_after.risk_complete is False
    assert a_after.total_open_risk is None            # withheld, NOT zero
    assert a_after.unprotected_position_count == 1
    assert a_after.unknown_risk_position_count == 1
    assert a_after.has_authoritative_total is False

    # B is COMPLETELY unchanged by A's fault.
    b_after = store.latest_open_risk("ADMIRALS")
    assert b_after.risk_complete is True
    assert b_after.total_open_risk == pytest.approx(500.0)
    assert b_after.currency == "USD"
    assert store.latest_position("ADMIRALS", 202).monetary_risk_to_sl == \
        pytest.approx(500.0)

    # ── STEP 3: B's position is closed. The next complete set proves it.
    clock.advance(minutes=1)
    _persist(_observe(ACCOUNT_B, [], clock=clock, spec=INDEX_SPEC,
                      currency="USD"), tmp_path)

    store = _store(tmp_path)
    b_set = store.latest_position_set("ADMIRALS")
    assert b_set.open_position_count == 0
    assert b_set.position_set_complete is True
    assert b_set.position_tickets == ()
    assert store.current_positions("ADMIRALS") == ()
    assert store.open_tickets("ADMIRALS") == ()

    # ...and its aggregate is a complete, provable ZERO-risk account.
    b_final = store.latest_open_risk("ADMIRALS")
    assert b_final.open_position_count == 0
    assert b_final.risk_complete is True
    assert b_final.total_open_risk == 0.0

    # A's unprotected state survives B's close and is still not understated.
    assert store.latest_open_risk("METAQUOTES").total_open_risk is None
    assert store.open_tickets("METAQUOTES") == (101,)
    # The closed position remains queryable as HISTORY, never as open.
    assert store.latest_position("ADMIRALS", 202).position_ticket == 202
    # No stale position remains current for B, at any read surface.
    assert [p.position_ticket for p in store.current_positions("ADMIRALS")] == []
    assert store.open_tickets("ADMIRALS") == ()


def test_a_closed_position_never_lingers_as_current_across_a_restart(tmp_path):
    """Closure survives a restart because it is proven by the SET boundary."""
    clock = FixedClock(T0)
    _persist(_observe(ACCOUNT_B, [_nas100_sell(202)], clock=clock,
                      spec=INDEX_SPEC, currency="USD"), tmp_path)
    assert _store(tmp_path).open_tickets("ADMIRALS") == (202,)

    clock.advance(minutes=1)
    _persist(_observe(ACCOUNT_B, [], clock=clock, spec=INDEX_SPEC,
                      currency="USD"), tmp_path)

    restarted = _store(tmp_path)                    # simulates a restart
    assert restarted.open_tickets("ADMIRALS") == ()
    assert restarted.current_positions("ADMIRALS") == ()
    assert restarted.latest_position_set(
        "ADMIRALS").position_set_complete is True
    assert restarted.latest_open_risk("ADMIRALS").total_open_risk == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# DATASET GRAIN, CANONICAL WRITER AND RESEARCH DISPOSITION AUDIT
# ═══════════════════════════════════════════════════════════════════════════


def test_position_set_and_open_risk_ids_are_independent_governed_identities():
    """Each aggregate kind has its own id namespace over the same observation."""
    cycle = _observe(rows=[_row(11)])

    assert cycle.open_risk.open_risk_id == derive_open_risk_id(
        ACCOUNT_A, T0_MS, "MT5_POSITIONS_GET")
    assert cycle.open_risk.open_risk_id.startswith("orsk_")
    # The set, position and aggregate identities are all distinct namespaces.
    assert len({
        cycle.position_set.position_snapshot_id,
        cycle.positions[0].position_snapshot_id,
        cycle.open_risk.open_risk_id,
    }) == 3


def test_capture_position_snapshot_builds_one_row_without_a_full_cycle():
    """The per-position capture boundary is usable on its own."""
    snapshot = capture_position_snapshot(
        ACCOUNT_A, _row(11),
        observed_at_utc=T0_ISO, observed_at_utc_ms=T0_MS,
        observation_id=derive_observation_id(ACCOUNT_A, T0_MS,
                                             "MT5_POSITIONS_GET"),
        source_name="MT5_POSITIONS_GET", canonical_symbol="EURUSD",
        spec_source=_spec_source(), risk_currency="GBP")

    assert snapshot.position_ticket == 11
    assert snapshot.account_id == "METAQUOTES"
    assert snapshot.monetary_risk_to_sl == pytest.approx(50.0)
    assert snapshot.status is PositionStatus.COMPLETE


def test_capture_rejects_a_non_mapping_row_and_a_missing_canonical_symbol():
    with pytest.raises(PositionSnapshotError):
        capture_position_snapshot(
            ACCOUNT_A, ["not", "a", "mapping"],
            observed_at_utc=T0_ISO, observed_at_utc_ms=T0_MS,
            observation_id="obs_x", source_name="S", canonical_symbol="EURUSD")
    with pytest.raises(PositionSnapshotError):
        capture_position_snapshot(
            ACCOUNT_A, _row(11), observed_at_utc=T0_ISO,
            observed_at_utc_ms=T0_MS, observation_id="obs_x",
            source_name="S", canonical_symbol="  ")


def test_each_new_dataset_has_exactly_one_governed_identity_and_writer():
    from core.canonical_delivery import CANONICAL_WRITER_MODULES

    assert EXACT_IDENTITY_FIELDS[DATASET] == ("account_id", "position_snapshot_id")
    assert EXACT_IDENTITY_FIELDS[OPEN_RISK_DATASET] == ("account_id",
                                                         "open_risk_id")
    # One owning writer module per dataset: no split-brain persistence.
    assert CANONICAL_WRITER_MODULES[DATASET] == ("core.risk.position_snapshot",)
    assert CANONICAL_WRITER_MODULES[OPEN_RISK_DATASET] == (
        "core.risk.position_snapshot",)


def test_position_snapshots_carries_both_grains_with_distinct_identities(
    tmp_path, isolated_outbox,
):
    """One dataset, two declared grains: a POSITION row and a POSITION_SET row.

    The shared dataset is intentional, but the two grains must never collide
    on identity, so canonical delivery can accept and reconcile both.
    """
    from core.canonical_delivery import get_delivery_outbox

    cycle = _observe(rows=[_row(11), _row(22)])
    _persist(cycle, tmp_path)

    payloads = [r.payload for r in get_delivery_outbox().records()
                if r.dataset == DATASET]
    kinds = {p["record_kind"] for p in payloads}
    assert kinds == {"POSITION", "POSITION_SET"}

    # The set boundary and the per-position rows carry distinct governed ids.
    ids = [p["position_snapshot_id"] for p in payloads]
    assert len(set(ids)) == len(ids)
    # A position row is grain-specific; the set row is the boundary.
    set_rows = [p for p in payloads if p["record_kind"] == "POSITION_SET"]
    assert len(set_rows) == 1
    assert set_rows[0]["position_tickets"] == [11, 22]
    assert "position_ticket" not in set_rows[0]


def test_the_position_set_boundary_is_intentional_in_account_open_risk():
    """The aggregate also carries the boundary, which is what reconstruction uses."""
    cycle = _observe(rows=[_row(11), _row(22, sl=0.0)])
    payload = cycle.open_risk.to_dict()

    assert payload["record_kind"] == "ACCOUNT_OPEN_RISK"
    assert payload["position_set_complete"] is True
    assert payload["position_tickets"] == [11, 22]
    # Sufficient on its own for current-state reconstruction, and it also
    # carries the exact account link the reconstruction needs.
    assert payload["account_snapshot_id"] == cycle.open_risk.account_snapshot_id
    assert payload["observation_id"] == cycle.observation_id
    assert payload["open_risk_id"].startswith("orsk_")


def test_both_new_datasets_have_explicit_research_disposition():
    from research_engine.dataset_disposition import (
        RESEARCH_DISPOSITIONS,
        ResearchDispositionStatus,
    )

    for dataset in (DATASET, OPEN_RISK_DATASET):
        disposition = RESEARCH_DISPOSITIONS[dataset]
        assert disposition.dataset == dataset
        # Explicitly operational: no silent research population.
        assert disposition.status is \
            ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL
        assert disposition.consumers == ()
        assert disposition.reason and disposition.research_purpose
        assert disposition.lineage_guard_notes
        # Join keys name the exact governed identities.
        assert "account_id" in disposition.join_keys


def test_both_new_datasets_are_in_the_lifecycle_disposition_table():
    from core.lifecycle_evidence_obligations import DATASET_DISPOSITIONS

    for dataset in (DATASET, OPEN_RISK_DATASET):
        assert dataset in DATASET_DISPOSITIONS
        assert DATASET_DISPOSITIONS[dataset]["class"] == "B_LIVE_CONDITIONAL"


def test_the_registry_retains_exactly_the_expected_active_dataset_count():
    """Block 2B moved 24 -> 26; Block 2C portfolio/correlation exposure added 3."""
    # Block 2C's own datasets are asserted by test_portfolio_exposure_contract.
    # This guard proves 2B's datasets were NOT weakened or removed by 2C.
    assert len(PRODUCTION_SCHEMA_REGISTRY) == 29
    assert DATASET in PRODUCTION_SCHEMA_REGISTRY
    assert OPEN_RISK_DATASET in PRODUCTION_SCHEMA_REGISTRY
    # Identity, writer and migration tables stay exactly in step with it.
    assert set(EXACT_IDENTITY_FIELDS) == set(PRODUCTION_SCHEMA_REGISTRY)
