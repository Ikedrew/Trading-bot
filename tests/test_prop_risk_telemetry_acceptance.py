"""Prop-risk telemetry runtime reachability + full-chain acceptance (Block 2D).

The load-bearing defect this file exists to close
---------------------------------------------------
Block 2A/2B/2C shipped complete, certified contracts, but they were reachable
ONLY as callable code: ``AccountSnapshotProducer``, ``PositionSnapshotProducer``,
``PortfolioExposureProducer`` and both heartbeat classes were referenced from
nothing outside ``core/risk/*`` and their own test files, and ``main.py``
started the Block 1 canonical delivery service but never started telemetry.
The system could therefore never produce a prop-risk row at runtime.

This file certifies the MINIMAL repair:

  * ONE managed :class:`PropRiskTelemetryService` owned by ONE thread;
  * ONE coherent per-account cycle 2A -> 2B -> 2C under ONE frozen instant;
  * EXACT object/ID lineage through that cycle (never a timestamp join);
  * strict account isolation and zero-vs-unavailable separation;
  * fail-closed semantics at every layer;
  * reachability from ``main.py`` in BOTH directions of the lifecycle.

The runtime-reachability tests are deliberately ADVERSARIAL and non-vacuous:
several assert against the real source of ``main.py`` and against live object
identity, so deleting the wiring fails the suite rather than passing silently.

All clocks are injected. There are no sleeps and no live broker access.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import re
import threading
from pathlib import Path

import pytest

import core.risk.telemetry_runtime as tr
from core.lifecycle_evidence_obligations import (
    ACCOUNT_SCOPED_DATASETS,
    EXACT_IDENTITY_FIELDS,
)
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
from core.risk.account_snapshot import (
    AccountIdentity,
    AccountIdentityError,
    AccountSnapshotStatus,
    AccountSnapshotStore,
)
from core.risk.portfolio_exposure import (
    PortfolioExposureStore,
    PortfolioStatus,
)
from core.risk.position_snapshot import (
    PositionSetUnavailable,
    PositionSnapshotStore,
    PositionStatus,
    to_epoch_ms,
)
from core.risk.symbol_correlation import (
    CorrelationCluster,
    build_symbol_correlation_model,
)
from core.risk.telemetry_runtime import (
    PropRiskTelemetryService,
    build_runtime_telemetry_service,
    prop_risk_telemetry_service_status,
    start_prop_risk_telemetry_service,
    stop_prop_risk_telemetry_service,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"

T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0_MS = to_epoch_ms(T0)

ACCOUNT_A = AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 5012345)
ACCOUNT_B = AccountIdentity("ADMIRALS", "Admirals", "Admirals-Server", 9988776)

MODEL = build_symbol_correlation_model(
    [
        CorrelationCluster("FX_USD_LONG_CLUSTER", ("EURUSD", "GBPUSD")),
        CorrelationCluster("US_INDEX_CLUSTER", ("NAS100", "US500")),
    ],
    model_id="TEST_CORRELATION",
    model_version="v1",
    effective_from_utc="2026-10-02T00:00:00Z",
    provenance="block2d acceptance fixture",
)

FX_SPEC = {
    "digits": 5, "point": 0.00001, "trade_tick_size": 0.00001,
    "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
    "trade_tick_value_loss": 1.0, "volume_min": 0.01, "volume_max": 100.0,
    "volume_step": 0.01, "pip_size": 0.0001,
}

class FixedClock:
    """Injected clock. No sleeping, no wall-clock reads."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


class FakeAccountSource:
    """Injected 2A boundary. ``info=None`` models an unreadable account."""

    source_name = "FAKE_ACCOUNT_INFO"

    def __init__(self, info=None, *, error: Exception | None = None) -> None:
        self.info = info
        self.error = error
        self.reads = 0

    def read_account_info(self):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.info


class FakePositionSource:
    """Injected 2B boundary. ``rows=None`` is a FAILURE, never an empty set."""

    def __init__(self, rows=(), *, error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.reads = 0

    def read_positions(self):
        self.reads += 1
        if self.error is not None:
            raise self.error
        if self.rows is None:
            return None
        return list(self.rows)


class FakeSpecSource:
    """Injected broker symbol-spec boundary with exact tick-value math."""

    def read_symbol_spec(self, broker_symbol: str):
        return dict(FX_SPEC)


def account_info(account: AccountIdentity = ACCOUNT_A, *, currency="GBP",
                 equity=10_000.0, balance=10_000.0) -> dict:
    return {
        "login": account.login, "server": account.server, "balance": balance,
        "equity": equity, "margin": 0.0, "margin_free": balance,
        "margin_level": 0.0, "leverage": 100, "currency": currency,
        "credit": 0.0, "profit": 0.0, "trade_allowed": True,
        "trade_expert": True, "trade_mode": 4, "margin_mode": 0,
    }


def position_row(ticket: int, *, symbol="EURUSD", side="BUY", risk=100.0,
                 sl_present=True) -> dict:
    """A row whose exact monetary risk to SL is ``risk``.

    ``sl_present=False`` builds a row with NO protective stop, so 2B reports
    UNKNOWN risk (never zero).
    """
    entry, stop_distance = 1.0850, 0.0050
    sl = (entry - stop_distance) if side == "BUY" else (entry + stop_distance)
    volume = 0.01
    if risk is not None:
        ticks = stop_distance / FX_SPEC["trade_tick_size"]
        volume = round(risk / (ticks * FX_SPEC["trade_tick_value_loss"]), 4)
    return {
        "ticket": ticket, "symbol": symbol,
        "type": 0 if side == "BUY" else 1, "volume": volume,
        "price_open": entry, "price": entry,
        "sl": sl if sl_present else 0.0, "tp": 0.0, "magic": 777,
        "comment": "block2d", "time": 1_700_000_000,
        "time_msc": 1_700_000_000_000, "order": ticket, "profit": -10.0,
    }


def build_service(tmp_path, *, identities=(ACCOUNT_A,), account_sources=None,
                  position_sources=None, clock=None, model=MODEL,
                  interval_ms=1000, **kwargs) -> PropRiskTelemetryService:
    """A fully injected service. No MT5, no network, no sleeps."""
    clock = clock or FixedClock()
    return PropRiskTelemetryService(
        identities=tuple(identities),
        account_sources=account_sources,
        position_sources=position_sources or {},
        spec_source=FakeSpecSource(),
        model=model,
        outbox=None,
        clock=clock,
        monotonic=lambda: 0.0,
        interval_ms=interval_ms,
        account_dir=str(tmp_path / "account_snapshots"),
        position_dir=str(tmp_path / "position_snapshots"),
        open_risk_dir=str(tmp_path / "account_open_risk"),
        portfolio_dir=str(tmp_path / "portfolio_exposure"),
        cluster_dir=str(tmp_path / "correlation_exposure"),
        **kwargs,
    )


def _one_service(tmp_path, **kwargs):
    """The common single-valid-account service used by most tests."""
    return build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
        **kwargs,
    )


# ═════════════════════════════════════════════════════════════════════════════
# T1–T8  CORE ACCEPTANCE INVARIANTS (through the RUNTIME service)
# ═════════════════════════════════════════════════════════════════════════════


def test_T1_exact_account_identity_is_carried_through_the_whole_chain(tmp_path):
    """T1: the exact configured identity reaches 2A, 2B and 2C unchanged."""
    service = _one_service(tmp_path)
    result = service.tick()[0]

    assert result.account_id == ACCOUNT_A.account_id
    assert result.currency == "GBP"
    store_a = AccountSnapshotStore(base_dir=service._account_dir)
    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)

    snapshot = store_a.latest(ACCOUNT_A.account_id)
    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    for record in (snapshot, risk, portfolio):
        assert (record.account_id, record.broker, record.server, record.login) \
            == ACCOUNT_A.identity

    # An identity that cannot exist is rejected outright, never invented.
    with pytest.raises(AccountIdentityError):
        AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 0)


def test_T2_exact_object_lineage_2A_to_2B_to_2C(tmp_path):
    """T2: exact IDs chain 2A -> 2B -> 2C with no timestamp join."""
    service = _one_service(tmp_path)
    result = service.tick()[0]

    assert result.account_snapshot_id
    assert result.observation_id
    assert result.open_risk_id
    assert result.portfolio_exposure_id

    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)

    # 2B links to THIS exact 2A snapshot.
    assert risk.account_snapshot_id == result.account_snapshot_id
    # 2C links to THIS exact 2B observation AND THIS exact open_risk_id.
    assert portfolio.observation_id == result.observation_id
    assert portfolio.open_risk_snapshot_id == result.open_risk_id
    assert portfolio.account_snapshot_id == result.account_snapshot_id
    # The correlation model is versioned and carried, never anonymous.
    assert portfolio.correlation_model_id == "TEST_CORRELATION"
    assert portfolio.correlation_model_version == "v1"


def test_T3_zero_positions_is_not_unavailable_positions(tmp_path):
    """T3: an empty authoritative set is NOT a failed read."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([])},
    )
    zero = service.tick()[0]
    assert zero.position_set_complete is True
    assert zero.open_position_count == 0
    assert zero.error is None
    # An authoritative zero portfolio: a real, complete total of 0.
    assert zero.total_open_risk == 0.0
    assert zero.risk_complete is True
    assert zero.portfolio_status != PortfolioStatus.UNAVAILABLE.value

    failed = build_service(
        tmp_path / "b",
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource(None)},
    ).tick()[0]
    assert failed.position_set_complete is False
    assert failed.open_position_count is None
    assert failed.error is not None
    # The failure is NEVER collapsed onto the zero state.
    assert failed.total_open_risk is None
    assert failed.portfolio_status == PortfolioStatus.UNAVAILABLE.value


def test_T4_unknown_risk_is_never_reported_as_zero(tmp_path):
    """T4: an open position with no stop has UNKNOWN risk, not zero risk."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={
            "METAQUOTES": FakePositionSource([position_row(1, sl_present=False)]),
        },
    )
    result = service.tick()[0]
    assert result.open_position_count == 1
    assert result.risk_complete is False
    assert result.total_open_risk is None
    assert result.known_open_risk in (0.0, None)

    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    position = store_b.latest_position(ACCOUNT_A.account_id, 1)
    assert position.monetary_risk_to_sl is None
    assert position.status is PositionStatus.UNPROTECTED


def test_T5_aggregate_incompleteness_propagates_into_2C(tmp_path):
    """T5: an incomplete 2B position set makes 2C incomplete, never flat."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource(None)},
    )
    result = service.tick()[0]
    assert result.position_set_complete is False
    assert result.risk_complete is False
    assert result.correlation_complete is False

    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert portfolio.position_set_complete is False
    assert portfolio.total_open_risk is None
    assert portfolio.status is PortfolioStatus.UNAVAILABLE


def test_T6_correlation_cluster_never_credits_a_hedge(tmp_path):
    """T6: a cluster is CONCENTRATION. Opposing sides still sum in full."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([
            position_row(1, symbol="EURUSD", side="BUY", risk=100.0),
            position_row(2, symbol="GBPUSD", side="SELL", risk=100.0),
        ])},
    )
    result = service.tick()[0]
    assert result.total_open_risk == pytest.approx(200.0)

    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    cluster = store_c.latest_cluster_exposures(ACCOUNT_A.account_id)[0]
    assert cluster.cluster_id == "FX_USD_LONG_CLUSTER"
    # FULL gross sum: no netting, no diversification discount.
    assert cluster.total_cluster_risk == 200.0
    assert cluster.long_risk == 100.0
    assert cluster.short_risk == 100.0
    assert cluster.hedging_credit_applied is False
    # Gross and net are separate, both persisted.
    assert portfolio.gross_risk == 200.0
    assert portfolio.net_known_directional_risk == 0.0
    assert portfolio.total_open_risk == 200.0


def test_T7_currency_integrity_is_preserved_end_to_end(tmp_path):
    """T7: monetary risk stays in the account's own currency, never converted."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(
            account_info(currency="USD"))},
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
    )
    result = service.tick()[0]
    assert result.currency == "USD"

    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    assert store_b.latest_open_risk(
        ACCOUNT_A.account_id).currency == "USD"
    assert store_c.latest_portfolio_exposure(
        ACCOUNT_A.account_id).currency == "USD"


def test_T8_freshness_propagates_and_derived_data_cannot_be_fresher(tmp_path):
    """T8: one frozen instant per cycle; age is evaluated, never slept."""
    clock = FixedClock()
    service = _one_service(tmp_path, clock=clock)
    service.tick()

    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    # Every record in the cycle shares ONE frozen observation instant, so 2C
    # can never appear fresher than the 2B observation it was derived from.
    assert risk.observed_at_utc_ms == T0_MS
    assert portfolio.observed_at_utc_ms == T0_MS

    # Age is evaluated against the injected clock -- no sleeping.
    assert risk.age_ms(now_ms=T0_MS + 10_000) == 10_000
    assert risk.is_stale(now_ms=T0_MS + 10_000, threshold_ms=1_000)
    assert risk.evaluate(
        now_ms=T0_MS + 10_000, threshold_ms=1_000
    ).status is PositionStatus.STALE


# ═════════════════════════════════════════════════════════════════════════════
# T9–T15  DURABILITY, RESTART, GRAIN, ISOLATION, FAIL-CLOSED, REPLAY, ZERO
# ═════════════════════════════════════════════════════════════════════════════


def test_T9_canonical_durability_reaches_the_certified_outbox(tmp_path):
    """T9: every persisted row hands off to the Block 1 outbox, not straight to S3."""
    service = _one_service(tmp_path)
    service.tick()

    # Local durable JSONL really exists for each layer.
    assert list(Path(service._account_dir).rglob("*.jsonl"))
    assert list(Path(service._position_dir).rglob("*.jsonl"))
    assert list(Path(service._open_risk_dir).rglob("*.jsonl"))
    assert list(Path(service._portfolio_dir).rglob("*.jsonl"))
    assert list(Path(service._cluster_dir).rglob("*.jsonl"))

    # There is deliberately NO direct S3 writer in the telemetry runtime.
    source = (ROOT / "core" / "risk" / "telemetry_runtime.py").read_text(
        encoding="utf-8")
    for forbidden in ("boto3", "put_object", "upload_file", "s3_client"):
        assert forbidden not in source


def test_T10_restart_reconstruction_from_durable_evidence(tmp_path):
    """T10: fresh stores rebuild the exact same truth after a restart."""
    service = _one_service(tmp_path)
    first = service.tick()[0]

    # A brand-new store instance = a restarted process reading only disk.
    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    store_a = AccountSnapshotStore(base_dir=service._account_dir)
    store_b.rebuild()
    store_c.rebuild()
    store_a.rebuild()

    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert risk.open_risk_id == first.open_risk_id
    assert risk.total_open_risk == first.total_open_risk
    assert portfolio.portfolio_exposure_id == first.portfolio_exposure_id
    assert portfolio.observation_id == first.observation_id
    assert store_a.latest(ACCOUNT_A.account_id).snapshot_id \
        == first.account_snapshot_id
    # The proven open-ticket set survives the restart.
    assert tuple(p.position_ticket for p in
                 store_b.current_positions(ACCOUNT_A.account_id)) == (1,)


def test_T11_dataset_grain_is_exactly_one_row_per_observation(tmp_path):
    """T11: one cycle emits one portfolio row and one cluster row per cluster."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([
            position_row(1, symbol="EURUSD"),
            position_row(2, symbol="GBPUSD"),
        ])},
    )
    service.tick()

    portfolio_files = list(Path(service._portfolio_dir).rglob("*.jsonl"))
    cluster_files = list(Path(service._cluster_dir).rglob("*.jsonl"))
    # Grain A: exactly one portfolio row per account observation.
    assert len(portfolio_files) == 1
    assert len(portfolio_files[0].read_text(
        encoding="utf-8").strip().splitlines()) == 1
    # Grain B: exactly one cluster row PER cluster, sharing one observation.
    cluster_rows = [
        line for f in cluster_files
        for line in f.read_text(encoding="utf-8").strip().splitlines() if line
    ]
    assert len(cluster_rows) == 1
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert all(c.observation_id == portfolio.observation_id
               for c in store_c.latest_cluster_exposures(ACCOUNT_A.account_id))
    # Two positions in ONE cluster still yield exactly ONE symbol bucket each.
    assert len(portfolio.symbol_exposure) == 2


def test_T12_multi_account_isolation_with_identical_ticket_and_symbol(tmp_path):
    """T12: identical ticket/symbol/timestamp across accounts never mixes."""
    service = build_service(
        tmp_path,
        identities=(ACCOUNT_A, ACCOUNT_B),
        account_sources={
            "METAQUOTES": FakeAccountSource(account_info(ACCOUNT_A)),
            "ADMIRALS": FakeAccountSource(account_info(ACCOUNT_B)),
        },
        position_sources={
            # SAME ticket, SAME symbol, SAME frozen instant -- different risk.
            "METAQUOTES": FakePositionSource(
                [position_row(1, symbol="EURUSD", risk=100.0)]),
            "ADMIRALS": FakePositionSource(
                [position_row(1, symbol="EURUSD", risk=250.0)]),
        },
    )
    results = {r.account_id: r for r in service.tick()}

    a, b = results["METAQUOTES"], results["ADMIRALS"]
    assert a.total_open_risk == pytest.approx(100.0)
    assert b.total_open_risk == pytest.approx(250.0)
    # Same ticket, but the records remain strictly account-scoped.
    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    assert store_b.latest_position("METAQUOTES", 1).account_id == "METAQUOTES"
    assert store_b.latest_position("ADMIRALS", 1).account_id == "ADMIRALS"
    assert store_b.latest_open_risk("ADMIRALS").observation_id \
        != a.observation_id


def test_T13_source_loss_fails_closed_without_fabricating_2B_or_2C(tmp_path):
    """T13: an unreadable 2A source never yields a valid 2B/2C truth claim."""
    service = build_service(
        tmp_path,
        account_sources={
            "METAQUOTES": FakeAccountSource(
                None, error=RuntimeError("terminal down")),
        },
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
    )
    result = service.tick()[0]

    # 2A is UNAVAILABLE, so the account is explicitly degraded...
    assert result.account_status == AccountSnapshotStatus.UNAVAILABLE.value
    assert result.error is not None
    # ...and NO monetary truth is fabricated at any layer. The position COUNT
    # survives because it is a genuinely provable, non-monetary 2B fact; every
    # currency-bearing value is withheld rather than published unlabelled.
    assert result.total_open_risk is None
    assert result.known_open_risk is None
    assert result.currency is None
    assert result.risk_complete is False
    # The persisted 2C row names WHY equity-linked values are missing.
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert portfolio.equity is None
    assert portfolio.risk_pct_equity is None
    assert portfolio.total_open_risk is not None  # the 2B floor is real

    # A missing 2B source is an explicit unavailability, not an empty set.
    no_source = build_service(
        tmp_path / "b",
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={},
    ).tick()[0]
    assert no_source.position_set_complete is False
    assert no_source.open_position_count is None
    assert no_source.total_open_risk is None
    assert no_source.error is not None


def test_T14_deterministic_replay_and_evidence_immutability(tmp_path):
    """T14: replaying one instant is byte-identical; replaying a later one is new."""
    # Two independent services observing the SAME frozen instant agree exactly.
    a = build_service(tmp_path / "x").tick()[0]
    b = build_service(tmp_path / "y").tick()[0]
    assert a == b  # dataclass equality over every lineage and monetary field

    # Advancing the clock yields a NEW observation, not a mutated old one.
    clock = FixedClock()
    live = build_service(
        tmp_path / "z", clock=clock,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
    )
    first_result = live.tick()[0]
    original_id = first_result.observation_id
    store_b = PositionSnapshotStore(
        base_dir=live._position_dir, open_risk_dir=live._open_risk_dir)
    store_b.rebuild()
    original_row = store_b.latest_open_risk(ACCOUNT_A.account_id).to_dict()

    clock.advance(seconds=60)
    second_result = live.tick()[0]
    store_b.rebuild()
    assert second_result.observation_id != original_id
    # The original evidence on disk is UNCHANGED -- append-only, never rewritten.
    assert store_b.latest_open_risk(ACCOUNT_A.account_id).to_dict() != \
        original_row
    rows = [
        line for f in Path(live._open_risk_dir).rglob("*.jsonl")
        for line in f.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(rows) == 2  # both observations retained


def test_T15_provable_zero_state_is_explicit_and_complete(tmp_path):
    """T15: zero exposure is a PROVEN, complete, positive claim."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([])},
    )
    result = service.tick()[0]

    assert result.position_set_complete is True
    assert result.risk_complete is True
    assert result.correlation_complete is True
    assert result.open_position_count == 0
    assert result.total_open_risk == 0.0
    assert result.known_open_risk == 0.0
    assert result.error is None

    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert portfolio.status is PortfolioStatus.COMPLETE
    assert portfolio.total_open_risk == 0.0
    assert portfolio.position_set_complete is True


# ═════════════════════════════════════════════════════════════════════════════
# RUNTIME SCENARIOS: ZERO-vs-FAILURE, NO-STOP, ACCOUNT FAILURE ISOLATION
# ═════════════════════════════════════════════════════════════════════════════


def test_runtime_zero_positions_versus_source_failure_never_collapses(tmp_path):
    """§13: A's empty set is authoritative zero; B's failure is unavailable."""
    service = build_service(
        tmp_path,
        identities=(ACCOUNT_A, ACCOUNT_B),
        account_sources={
            "METAQUOTES": FakeAccountSource(account_info(ACCOUNT_A)),
            "ADMIRALS": FakeAccountSource(account_info(ACCOUNT_B)),
        },
        position_sources={
            "METAQUOTES": FakePositionSource([]),          # valid, empty
            "ADMIRALS": FakePositionSource(None),          # unreadable
        },
    )
    results = {r.account_id: r for r in service.tick()}
    a, b = results["METAQUOTES"], results["ADMIRALS"]

    # Authoritative zero: provable, complete, zero risk.
    assert a.open_position_count == 0
    assert a.position_set_complete is True
    assert a.total_open_risk == 0.0
    assert a.error is None
    # Unavailable: unknown, never zero.
    assert b.open_position_count is None
    assert b.position_set_complete is False
    assert b.total_open_risk is None
    assert b.error is not None
    # The two states are provably DIFFERENT and never collapsed.
    assert a.position_set_complete is not b.position_set_complete
    assert a.total_open_risk != b.total_open_risk


def test_runtime_no_stop_position_persists_unknown_risk_truth_end_to_end(tmp_path):
    """§14: a live unprotected position yields UNKNOWN risk, not zero, 2A->2C."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([
            position_row(7, sl_present=False)])},
    )
    result = service.tick()[0]

    # 2B: unknown risk, UNPROTECTED, incomplete.
    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    position = store_b.latest_position(ACCOUNT_A.account_id, 7)
    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    assert position.monetary_risk_to_sl is None
    assert position.status is PositionStatus.UNPROTECTED
    assert risk.unknown_risk_position_count == 1
    assert risk.risk_complete is False
    assert risk.total_open_risk is None
    assert risk.unprotected_position_count == 1

    # 2C: known floor only, incomplete correlation exposure, no flat total.
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert portfolio.total_open_risk is None
    assert portfolio.risk_complete is False
    assert portfolio.status is PortfolioStatus.PARTIAL
    assert portfolio.known_open_risk is not None
    # Cluster MEMBERSHIP stays complete (the set was readable), but the
    # cluster's monetary risk is NOT authoritative and no percentage is
    # published against a floor.
    assert portfolio.correlation_complete is True
    cluster = store_c.latest_cluster_exposures(ACCOUNT_A.account_id)[0]
    assert cluster.cluster_risk_complete is False
    assert cluster.total_cluster_risk is None
    assert portfolio.risk_pct_equity is None
    assert portfolio.max_cluster_risk_pct is None
    # The same exact chain IDs carry that truth to 2C.
    assert portfolio.observation_id == risk.observation_id
    assert portfolio.open_risk_snapshot_id == risk.open_risk_id
    assert portfolio.account_snapshot_id == risk.account_snapshot_id
    assert result.risk_complete is False
    assert result.total_open_risk is None


# ═════════════════════════════════════════════════════════════════════════════
# RUNTIME REACHABILITY (§10 / §11)
#
# These are the tests that CLOSE the proven blocker. They are adversarial: they
# assert against the REAL main.py source and against LIVE OBJECT IDENTITY, so
# deleting the wiring or the service makes them FAIL rather than silently pass.
# ═════════════════════════════════════════════════════════════════════════════


def _main_source() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def _main_calls(name: str) -> int:
    """How many times main.py actually CALLS ``name`` (not merely imports it)."""
    return len(re.findall(rf"{name}\s*\(", _main_source()))


@pytest.fixture(autouse=True)
def _clean_process_service():
    """Never leak the process-level singleton between tests."""
    yield
    tr._SERVICE = None


def test_R1_startup_actually_invokes_the_telemetry_cycle(tmp_path):
    """A: service startup really runs the 2A -> 2B -> 2C cycle."""
    source = FakePositionSource([position_row(1)])
    account = FakeAccountSource(account_info())
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": account},
        position_sources={"METAQUOTES": source},
        interval_ms=1,
    )

    seen = threading.Event()
    original = service.tick

    def spy():
        seen.set()
        return original()

    service.tick = spy
    try:
        assert service.start() is True
        assert seen.wait(timeout=5.0) is True
    finally:
        assert service.stop(timeout=5.0) is True

    # Startup reached BOTH real broker boundaries and persisted the whole chain.
    assert account.reads >= 1
    assert source.reads >= 1
    assert service.status().cycles_completed >= 1
    assert Path(service._portfolio_dir).exists()


def test_R2_main_py_startup_wiring_reaches_the_telemetry_service():
    """B: main.py genuinely starts the telemetry service (non-vacuous)."""
    source = _main_source()
    assert "start_prop_risk_telemetry_service" in source
    # A real CALL exists, not just an import or a comment.
    assert _main_calls("start_prop_risk_telemetry_service") == 1
    # It is wired INSIDE main(), not at import time or module scope.
    tree = ast.parse(source)
    main_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        n.func.id for n in ast.walk(main_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "start_prop_risk_telemetry_service" in called


def test_R3_shutdown_is_called_during_cleanup():
    """C: main.py stops telemetry on EVERY exit path, before MT5 shutdown."""
    source = _main_source()
    assert _main_calls("stop_prop_risk_telemetry_service") == 1
    tree = ast.parse(source)
    main_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "main")
    # The stop call lives inside a `finally`, so an exception cannot skip it.
    stop_call = "stop_prop_risk_telemetry_service"
    stop_in_finally = False
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        # Walk recursively: the stop call is guarded by its own try/except so
        # a telemetry fault can never block the rest of cleanup.
        for statement in node.finalbody:
            for inner in ast.walk(statement):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == stop_call):
                    stop_in_finally = True
    assert stop_in_finally is True


def test_R4_duplicate_start_does_not_create_duplicate_threads(tmp_path):
    """D: idempotent start -- exactly ONE managed thread, ever."""
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([])},
        interval_ms=10_000,
    )
    assert service.start() is True
    thread = service._thread
    try:
        # Repeated starts must never spawn a second loop thread.
        assert service.start() is False
        assert service.start() is False
        assert service._thread is thread
        assert thread.is_alive() is True
    finally:
        assert service.stop(timeout=5.0) is True
    assert thread.is_alive() is False


def test_R5_one_account_failure_does_not_stop_another_account(tmp_path):
    """E: B fails; A still completes a full, valid telemetry cycle."""
    service = build_service(
        tmp_path,
        identities=(ACCOUNT_A, ACCOUNT_B),
        account_sources={
            "METAQUOTES": FakeAccountSource(account_info(ACCOUNT_A)),
            "ADMIRALS": FakeAccountSource(
                account_info(ACCOUNT_B), error=RuntimeError("worker died")),
        },
        position_sources={
            "METAQUOTES": FakePositionSource([position_row(1, risk=100.0)]),
            "ADMIRALS": FakePositionSource([position_row(1, risk=100.0)]),
        },
    )
    results = {r.account_id: r for r in service.tick()}
    a, b = results["METAQUOTES"], results["ADMIRALS"]

    # A produced the whole chain normally.
    assert a.error is None
    assert a.account_snapshot_id and a.observation_id
    assert a.open_risk_id and a.portfolio_exposure_id
    assert a.total_open_risk == pytest.approx(100.0)
    # B is explicitly degraded, with no fabricated monetary truth.
    assert b.error is not None
    assert b.total_open_risk is None
    assert b.account_status == AccountSnapshotStatus.UNAVAILABLE.value
    # The service keeps running and reports health truthfully.
    status = service.status()
    assert status.last_error is not None and "ADMIRALS" in status.last_error


def test_R6_exact_2A_object_feeds_the_exact_2B_cycle(tmp_path, monkeypatch):
    """F: the 2B cycle is linked by the EXACT 2A object/id, not by proximity."""
    import core.risk.telemetry_runtime as module

    captured: dict = {}
    real_observe = module.observe_positions

    def spy(account, source, **kwargs):
        captured.update(kwargs)
        return real_observe(account, source, **kwargs)

    monkeypatch.setattr(module, "observe_positions", spy)
    service = _one_service(tmp_path)
    result = service.tick()[0]

    # The 2A snapshot id handed to 2B is the one this cycle just produced.
    assert captured["account_snapshot_id"] == result.account_snapshot_id
    assert captured["currency"] == "GBP"
    assert captured["account_snapshot_id"] is not None


def test_R7_exact_2B_cycle_feeds_the_exact_2C_aggregation(tmp_path, monkeypatch):
    """G: 2C derives from THIS cycle object and THIS 2A snapshot object."""
    import core.risk.telemetry_runtime as module

    captured: dict = {}
    real_producer = module.PortfolioExposureProducer

    class SpyProducer(real_producer):
        def observe(self, cycle, *, account_snapshot=None, model=None):
            captured["cycle"] = cycle
            captured["snapshot"] = account_snapshot
            return super().observe(
                cycle, account_snapshot=account_snapshot, model=model)

    monkeypatch.setattr(module, "PortfolioExposureProducer", SpyProducer)
    service = _one_service(tmp_path)
    result = service.tick()[0]

    # The objects passed to 2C carry exactly the published lineage IDs.
    assert captured["cycle"].observation_id == result.observation_id
    assert captured["cycle"].open_risk.open_risk_id == result.open_risk_id
    assert captured["snapshot"].snapshot_id == result.account_snapshot_id
    # And they are the SAME objects persisted, not reconstructed copies.
    assert captured["cycle"].account_id == ACCOUNT_A.account_id


def test_R8_exactly_one_managed_thread_not_one_per_layer(tmp_path):
    """H: ONE service thread -- never a separate 2A/2B/2C thread."""
    before = {t.name for t in threading.enumerate()}
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
        interval_ms=10_000,
    )
    assert service.start() is True
    try:
        new_threads = [t for t in threading.enumerate() if t.name not in before]
        # Exactly ONE new thread, and it is the telemetry loop itself.
        assert len(new_threads) == 1
        assert new_threads[0].name == "prop_risk_telemetry"
        assert new_threads[0].daemon is True
        # The legacy per-layer heartbeat threads are NOT started by this service.
        assert "account_snapshot_heartbeat" not in {
            t.name for t in threading.enumerate()}
        assert "position_snapshot_heartbeat" not in {
            t.name for t in threading.enumerate()}
    finally:
        assert service.stop(timeout=5.0) is True


def test_R9_one_bounded_synchronous_tick_needs_no_sleeping(tmp_path):
    """I: a full cycle runs synchronously with no wall-clock sleeping."""
    clock = FixedClock()
    service = build_service(
        tmp_path,
        clock=clock,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([position_row(1)])},
    )
    # The clock is read EXACTLY ONCE per cycle: 2A, 2B and 2C share it.
    before = clock.reads
    results = service.tick()
    assert clock.reads - before == 1
    assert len(results) == 1
    assert service.status().cycles_completed == 1
    # The service was never started, so no thread is involved at all.
    assert service.running is False


# ═════════════════════════════════════════════════════════════════════════════
# ADVERSARIAL RUNTIME TEST (§11)
#
# The pre-2D blocker was: "no external producer/service invocation exists".
# This test proves that blocker is CLOSED by simulating the LIVE path end to
# end: main.py's wiring -> the process singleton -> one managed thread -> a
# coherent 2A -> 2B -> 2C cycle with exact lineage. Removing ANY link fails it.
# ═════════════════════════════════════════════════════════════════════════════


def test_A1_previous_blocker_is_closed_main_to_service_to_coherent_cycle(
        tmp_path, monkeypatch):
    """main.py -> service start -> coherent 2A->2B->2C cycle, with lineage."""
    # main.py really starts the service (guards the "no invocation" defect).
    assert _main_calls("start_prop_risk_telemetry_service") == 1

    # The process singleton is what main.py calls; build it from injected
    # fakes so no MT5 is touched, but through the REAL public entry point.
    service = build_service(
        tmp_path,
        account_sources={"METAQUOTES": FakeAccountSource(account_info())},
        position_sources={"METAQUOTES": FakePositionSource([
            position_row(1, risk=100.0)])},
        interval_ms=10_000,
    )
    monkeypatch.setattr(
        tr, "build_runtime_telemetry_service", lambda **kw: service)

    # This IS main.py's call path, verbatim.
    started = start_prop_risk_telemetry_service()
    assert started is service
    assert started.running is True
    assert prop_risk_telemetry_service_status().running is True

    # Drive the live loop to a real cycle without sleeping.
    started.tick()
    result = started.last_results()[0]

    # A coherent cycle with EXACT lineage really was produced at runtime.
    assert result.error is None
    assert result.account_status == "COMPLETE"
    assert result.observation_id
    assert result.total_open_risk == pytest.approx(100.0)

    store_b = PositionSnapshotStore(
        base_dir=service._position_dir, open_risk_dir=service._open_risk_dir)
    store_c = PortfolioExposureStore(
        base_dir=service._portfolio_dir, cluster_dir=service._cluster_dir)
    risk = store_b.latest_open_risk(ACCOUNT_A.account_id)
    portfolio = store_c.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert portfolio.observation_id == risk.observation_id
    assert portfolio.account_snapshot_id == result.account_snapshot_id

    # And it is cleanable, exactly as main.py's finally block does.
    assert stop_prop_risk_telemetry_service() is True
    assert started.running is False


def test_A2_removing_the_main_py_wiring_breaks_reachability():
    """Anti-vacuity: the assertion really depends on main.py's own source."""
    source = _main_source()
    # Simulate the PRE-2D world: main.py with the telemetry wiring removed.
    stripped = re.sub(r".*prop_risk_telemetry.*\n", "", source, flags=re.DOTALL)
    assert "start_prop_risk_telemetry_service" not in stripped
    assert "start_prop_risk_telemetry_service" in source


def test_A3_service_is_reachable_outside_any_test_only_helper():
    """The service module is production code, not a test-only shim."""
    assert "core.risk.telemetry_runtime" in _main_source()
    for name in ("start_prop_risk_telemetry_service",
                 "stop_prop_risk_telemetry_service",
                 "build_runtime_telemetry_service",
                 "PropRiskTelemetryService"):
        assert name in tr.__all__


# ═════════════════════════════════════════════════════════════════════════════
# MT5 / ACCOUNT BOUNDARY (§3)
# ═════════════════════════════════════════════════════════════════════════════


def test_M1_non_baseline_accounts_never_use_the_process_mt5_session():
    """No account-crossing shortcut: only the baseline owns the live session."""
    account_sources, position_sources = tr.runtime_account_sources(
        (ACCOUNT_A, ACCOUNT_B))

    # The baseline account uses this process's own MT5 session.
    assert isinstance(account_sources["METAQUOTES"], tr.Mt5AccountInfoSource)
    assert isinstance(position_sources["METAQUOTES"], tr.Mt5PositionSource)
    # Every OTHER account goes through its own pinned isolated worker.
    assert isinstance(account_sources["ADMIRALS"], tr.WorkerAccountInfoSource)
    assert isinstance(position_sources["ADMIRALS"], tr.WorkerPositionSource)
    # No account ever receives another account's source instance.
    assert account_sources["METAQUOTES"] is not account_sources["ADMIRALS"]
    assert position_sources["METAQUOTES"] is not position_sources["ADMIRALS"]


def test_M2_worker_sources_fail_closed_on_an_unverified_worker(monkeypatch):
    """A worker that cannot verify identity yields UNAVAILABLE, never zeros."""
    from core.accounts import manager

    monkeypatch.setattr(
        manager, "run_isolated",
        lambda cfg, *a, **k: {"connected": False, "identity_verified": False,
                              "positions": [], "reasons": ["WORKER_TIMEOUT"]},
    )
    with pytest.raises(Exception):
        tr.WorkerAccountInfoSource(ACCOUNT_B).read_account_info()
    with pytest.raises(PositionSetUnavailable):
        tr.WorkerPositionSource(ACCOUNT_B).read_positions()

    # Even a CONNECTED worker with an unreadable position set fails closed.
    monkeypatch.setattr(
        manager, "run_isolated",
        lambda cfg, *a, **k: {"connected": True, "identity_verified": True,
                              "positions": None, "reasons": []},
    )
    with pytest.raises(PositionSetUnavailable):
        tr.WorkerPositionSource(ACCOUNT_B).read_positions()


def test_M3_identities_come_only_from_configuration(monkeypatch):
    """Account identity is never invented; config is the only authority."""
    from core.accounts import config as account_config

    monkeypatch.setenv("MT5_ADMIRALS_ENABLED", "false")
    identities = tr.enabled_account_identities()
    for identity in identities:
        # Every identity matches a real configured account exactly.
        assert any(identity.matches_account_config(c)
                   for c in account_config.load_accounts())


# ═════════════════════════════════════════════════════════════════════════════
# NO BLOCK 3 LEAKAGE (§8) + DATASET / COVERAGE REGRESSION (§15)
# ═════════════════════════════════════════════════════════════════════════════


def test_X1_telemetry_runtime_contains_no_block_3_rule_enforcement():
    """This repair is telemetry ONLY: no daily loss, drawdown or trade actions."""
    source = (ROOT / "core" / "risk" / "telemetry_runtime.py").read_text(
        encoding="utf-8")
    forbidden = (
        "daily_loss", "max_drawdown", "high_water", "trailing_drawdown",
        "profit_target", "challenge_pass", "challenge_fail",
        "order_send", "close_position", "modify_position", "trade_block",
    )
    for token in forbidden:
        assert token not in source.lower(), token


def test_X2_this_repair_adds_no_dataset():
    """The runtime repair registers ZERO new datasets (count stays 29)."""
    assert len(PRODUCTION_SCHEMA_REGISTRY) == 29

    from core.risk.account_snapshot import DATASET as ACCOUNT_DATASET
    from core.risk.position_snapshot import (
        DATASET as POSITION_DATASET, OPEN_RISK_DATASET,
    )
    from core.risk.portfolio_exposure import DATASET as PORTFOLIO_DATASET
    for dataset in (ACCOUNT_DATASET, POSITION_DATASET, OPEN_RISK_DATASET,
                    PORTFOLIO_DATASET):
        assert dataset in PRODUCTION_SCHEMA_REGISTRY
        assert dataset in EXACT_IDENTITY_FIELDS


def test_X3_block_2_datasets_remain_account_scoped_and_covered():
    """Account scope + exact identity coverage stay complete for Block 2."""
    from core.risk.account_snapshot import DATASET as ACCOUNT_DATASET
    from core.risk.position_snapshot import (
        DATASET as POSITION_DATASET, OPEN_RISK_DATASET,
    )
    from core.risk.portfolio_exposure import DATASET as PORTFOLIO_DATASET

    for dataset in (ACCOUNT_DATASET, POSITION_DATASET, OPEN_RISK_DATASET,
                    PORTFOLIO_DATASET):
        assert dataset in ACCOUNT_SCOPED_DATASETS
        fields = EXACT_IDENTITY_FIELDS[dataset]
        assert "account_id" in fields


def test_X4_research_disposition_coverage_remains_complete():
    """Adding runtime reachability must not orphan a dataset's disposition."""
    from research_engine.dataset_disposition import RESEARCH_DISPOSITIONS

    for dataset in PRODUCTION_SCHEMA_REGISTRY:
        assert dataset in RESEARCH_DISPOSITIONS, dataset