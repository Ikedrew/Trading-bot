"""Account-safe prop-risk telemetry -- portfolio / correlation exposure (2C).

Covers the canonical portfolio and correlation-cluster exposure contract built
strictly on top of Block 2B:

  * EXACT LINKAGE. Portfolio exposure binds to the exact 2B observation_id /
    open_risk_id and the exact 2A account_snapshot_id. Timestamp-nearest joins
    are forbidden and mismatched cycles are never combined.
  * NO HEDGE CREDIT. A correlation cluster is CONCENTRATION. Cluster risk is
    always the SUM of member monetary risk, even when members are held in
    opposing directions.
  * UNKNOWN RISK IS NOT ZERO RISK. The known floor is always published; the
    authoritative total and every dependent percentage are withheld.
  * EXACT ACCOUNT SCOPE. latest(A) never returns B. Cross-account aggregation is
    a separate, explicitly labelled grain.
  * NO CROSS-CURRENCY SUMMING. Mixed-currency accounts produce currency buckets,
    never one guessed monetary total.

All clocks are injected. There are no sleeps and no live broker access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import json

import pytest

from core.canonical_delivery import configure_delivery_outbox
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
from core.risk.portfolio_exposure import (
    CLUSTER_DATASET,
    CLUSTER_SCHEMA_VERSION,
    CROSS_ACCOUNT_DATASET,
    CROSS_ACCOUNT_SCHEMA_VERSION,
    DATASET,
    SCHEMA_VERSION,
    CorrelationClusterExposure,
    CrossAccountPortfolioExposure,
    Direction,
    PortfolioExposure,
    PortfolioExposureCycle,
    PortfolioExposureNotFound,
    PortfolioExposureProducer,
    PortfolioExposureStore,
    PortfolioFailureReason,
    PortfolioStatus,
    SymbolExposure,
    aggregate_cross_account_exposure,
    aggregate_portfolio_exposure,
    derive_cluster_exposure_id,
    derive_cross_account_exposure_id,
    derive_portfolio_exposure_id,
    observe_account_portfolio,
    persist_cross_account_exposure,
    persist_portfolio_exposure,
)
from core.risk.position_snapshot import (
    AccountOpenRiskSnapshot,
    PositionObservationCycle,
    PositionSetObservation,
    PositionSetUnavailable,
    PositionSide,
    PositionSnapshot,
    PositionStatus,
    observe_positions,
    to_epoch_ms,
)
from core.risk.symbol_correlation import (
    CorrelationCluster,
    CorrelationModelInvalid,
    SymbolCorrelationModel,
    build_symbol_correlation_model,
    default_symbol_correlation_model,
    unclassified_cluster_id,
)
from research_engine.dataset_disposition import (
    RESEARCH_DISPOSITIONS,
    ResearchDispositionStatus,
)


# ─── TEST DOUBLES ────────────────────────────────────────────────────────────


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
    rows: object = None
    error: Exception | None = None

    def read_positions(self):
        if self.error is not None:
            raise self.error
        return None if self.rows is None else list(self.rows)
T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0_MS = to_epoch_ms(T0)

ACCOUNT_A = AccountIdentity("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", 5012345)
ACCOUNT_B = AccountIdentity("ADMIRALS", "Admirals", "Admirals-Server", 9988776)

FX_SPEC = {
    "digits": 5, "point": 0.00001, "trade_tick_size": 0.00001,
    "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
    "trade_tick_value_loss": 1.0, "volume_min": 0.01, "volume_max": 100.0,
    "volume_step": 0.01, "pip_size": 0.0001,
}
GOLD_SPEC = {
    "digits": 2, "point": 0.01, "trade_tick_size": 0.01,
    "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
    "trade_tick_value_loss": 1.0, "volume_min": 0.01, "volume_max": 50.0,
    "volume_step": 0.01, "pip_size": 0.1,
}
INDEX_SPEC = {
    "digits": 2, "point": 0.1, "trade_tick_size": 0.1,
    "trade_tick_value": 0.5, "trade_tick_value_profit": 0.5,
    "trade_tick_value_loss": 0.5, "volume_min": 0.1, "volume_max": 100.0,
    "volume_step": 0.1, "pip_size": 1.0,
}


# ─── DETERMINISTIC TEST CORRELATION MODEL (§35 acceptance) ──────────────────


ACCEPTANCE_MODEL = build_symbol_correlation_model(
    [
        CorrelationCluster("FX_USD_LONG_CLUSTER", ("EURUSD", "GBPUSD")),
        CorrelationCluster("US_INDEX_CLUSTER", ("NAS100", "US500")),
    ],
    model_id="TEST_CORRELATION",
    model_version="v1",
    effective_from_utc="2026-10-02T00:00:00Z",
    provenance="block2c acceptance fixture",
)


def _geometry(symbol: str) -> tuple[float, float]:
    """Realistic (entry, stop_distance) per instrument class."""
    if symbol == "XAUUSD":
        return 2400.0, 10.0
    if symbol in {"NAS100", "US500"}:
        return 18000.0, 50.0
    return 1.0850, 0.0050


def _spec_for(symbol: str) -> dict:
    if symbol == "XAUUSD":
        return GOLD_SPEC
    if symbol in {"NAS100", "US500"}:
        return INDEX_SPEC
    return FX_SPEC


def _row(ticket: int = 1, *, symbol: str = "EURUSD", side: str = "BUY",
         risk: float | None = 100.0) -> dict:
    """A position row whose exact monetary risk to SL is ``risk``.

    ``risk=None`` builds a row with NO protective stop, so 2B reports UNKNOWN
    risk (never zero).
    """
    spec = _spec_for(symbol)
    entry, stop_distance = _geometry(symbol)
    sl = ((entry - stop_distance) if side == "BUY" else (entry + stop_distance))
    if risk is None:
        sl = 0.0
    # Volume is chosen so the exact tick-value identity reproduces ``risk``.
    ticks = stop_distance / spec["trade_tick_size"]
    per_lot = ticks * spec["trade_tick_value_loss"]
    volume = 0.01 if risk is None else (risk / per_lot)
    return {
        "ticket": ticket, "symbol": symbol,
        "type": 0 if side == "BUY" else 1,
        "volume": round(volume, 4), "price_open": entry,
        "price": entry, "sl": sl, "tp": 0.0, "magic": 777,
        "comment": "block2c", "time": 1_700_000_000,
        "time_msc": 1_700_000_000_000, "order": ticket, "profit": -10.0,
    }


class FakeSpec:
    """Injected broker symbol-spec boundary with exact tick-value math."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def read_symbol_spec(self, broker_symbol: str):
        self.requested.append(broker_symbol)
        return _spec_for(broker_symbol)


def _observe(
    account: AccountIdentity = ACCOUNT_A,
    rows=(),
    *,
    clock: FixedClock | None = None,
    currency: str | None = "GBP",
    account_snapshot_id: str | None = None,
    canonical_resolver=None,
    source: FakePositionSource | None = None,
    source_error: Exception | None = None,
) -> PositionObservationCycle:
    """ONE exact Block 2B observation cycle -- the ONLY 2C truth source."""
    the_clock = clock or FixedClock(T0)
    payload = (
        source if source is not None else
        FakePositionSource(rows=rows, error=source_error)
    )
    return observe_positions(
        account, payload, clock=the_clock,
        spec_source=FakeSpec(), currency=currency,
        account_snapshot_id=account_snapshot_id,
        canonical_resolver=canonical_resolver,
    )


class FakeSpec:
    """Injected broker symbol-spec boundary with exact tick-value math."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def read_symbol_spec(self, broker_symbol: str):
        self.requested.append(broker_symbol)
        return _spec_for(broker_symbol)


def _derive(account: AccountIdentity = ACCOUNT_A, rows=(), *,
            model=ACCEPTANCE_MODEL, clock: FixedClock | None = None,
            currency: str | None = "GBP",
            account_snapshot_id: str | None = None,
            account_snapshot=None, **kwargs) -> PortfolioExposureCycle:
    cycle = _observe(account, rows, clock=clock, currency=currency,
                     account_snapshot_id=account_snapshot_id, **kwargs)
    return aggregate_portfolio_exposure(
        account, cycle, model=model, account_snapshot=account_snapshot)


def _rows(*specs) -> list[dict]:
    return [_row(ticket=index + 1, **spec) for index, spec in enumerate(specs)]
# ═══════════════════════════════════════════════════════════════════════════
# 1–7. CORRELATION MODEL AUTHORITY, DETERMINISM, MISSING SYMBOL, INVALID MODEL
# ═══════════════════════════════════════════════════════════════════════════


def test_model_is_explicit_static_and_not_empirical():
    """Correlation semantics are STATIC and governed -- never pretended empirical."""
    model = ACCEPTANCE_MODEL
    assert model.is_empirical is False
    assert model.model_key == "TEST_CORRELATION@v1"
    assert model.semantics == "CONCENTRATION_NOT_HEDGING_CREDIT"
    assert model.unclassified_policy == "SINGLETON_UNCLASSIFIED"
    payload = model.to_dict()
    assert payload["is_empirical"] is False
    assert payload["provenance"]


def test_default_governed_model_lifts_the_projects_real_relationship_source():
    """The project's ACTUAL static source of truth is reused, not invented."""
    from core import config

    model = default_symbol_correlation_model()
    assert model.model_id == "SYMBOL_CORRELATION_CLUSTERS"
    assert model.is_empirical is False
    for group in config.CORRELATION_GROUPS:
        for symbol in group:
            assert model.is_classified(symbol), symbol


def test_cluster_membership_is_deterministic_under_a_fixed_model():
    """Same inputs + same model version -> same cluster membership."""
    rows = _rows({"symbol": "EURUSD", "risk": 100.0},
                 {"symbol": "GBPUSD", "risk": 120.0})
    first = _derive(rows=rows)
    second = _derive(rows=rows)
    assert [c.cluster_id for c in first.clusters] == \
           [c.cluster_id for c in second.clusters]
    assert [c.position_tickets for c in first.clusters] == \
           [c.position_tickets for c in second.clusters]


def test_symbol_missing_from_the_model_gets_a_singleton_unclassified_cluster():
    """A missing symbol is never dropped and never merged into a neighbour."""
    cycle = _derive(rows=_rows({"symbol": "USDJPY", "risk": 90.0}))
    assert len(cycle.clusters) == 1
    cluster = cycle.clusters[0]
    assert cluster.cluster_id == unclassified_cluster_id("USDJPY")
    assert cluster.classified is False
    assert cluster.member_symbols == ("USDJPY",)
    assert cycle.portfolio.unclassified_symbol_count == 1
    assert cycle.portfolio.unclassified_cluster_count == 1
    assert cycle.portfolio.max_cluster_id == cluster.cluster_id


@pytest.mark.parametrize(
    "clusters,code",
    [
        ([CorrelationCluster("CL_A", ("EURUSD", "GBPUSD")),
          CorrelationCluster("CL_A", ("NAS100",))], "DUPLICATE_CORRELATION_CLUSTER_ID"),
        ([CorrelationCluster("CL_A", ("EURUSD",)),
          CorrelationCluster("CL_B", ("EURUSD", "GBPUSD"))],
         "INCONSISTENT_SYMBOL_ASSIGNMENT"),
    ],
)
def test_invalid_models_fail_closed(clusters, code):
    """A conflicting model raises; it never silently picks one interpretation."""
    with pytest.raises(CorrelationModelInvalid) as exc:
        SymbolCorrelationModel(
            model_id="X", model_version="v1", effective_from_utc="T",
            clusters=tuple(clusters), provenance="p")
    assert code in str(exc.value)


def test_duplicate_symbol_inside_one_cluster_fails_closed():
    """A contradictory relation inside one cluster is invalid, not merged."""
    with pytest.raises(CorrelationModelInvalid) as exc:
        CorrelationCluster("CL_A", ("EURUSD", "EURUSD"))
    assert "DUPLICATE_SYMBOL_IN_CLUSTER" in str(exc.value)


def test_malformed_cluster_id_and_non_canonical_symbol_fail_closed():
    with pytest.raises(CorrelationModelInvalid) as exc:
        CorrelationCluster("bad id", ("EURUSD",))
    assert "MALFORMED_CORRELATION_CLUSTER_ID" in str(exc.value)
    with pytest.raises(CorrelationModelInvalid) as exc:
        CorrelationCluster("GOOD_ID", ("eurusd",))
    assert "NON_CANONICAL_CORRELATION_SYMBOL" in str(exc.value)
    with pytest.raises(CorrelationModelInvalid):
        CorrelationCluster("GOOD_ID", ())


def test_empirical_correlation_claims_are_rejected_by_contract():
    """This block does not pretend to compute live/empirical correlation."""
    with pytest.raises(CorrelationModelInvalid):
        SymbolCorrelationModel(
            model_id="X", model_version="v1", effective_from_utc="T",
            clusters=(CorrelationCluster("CL_X", ("EURUSD",)),), provenance="p",
            is_empirical=True)


def test_missing_correlation_model_is_explicit_not_zero_exposure():
    """A model failure is never collapsed into an empty cluster set."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}), model=None)
    portfolio = cycle.portfolio
    assert portfolio.status is PortfolioStatus.PARTIAL
    assert portfolio.failure_reason is \
        PortfolioFailureReason.CORRELATION_MODEL_UNAVAILABLE
    assert portfolio.correlation_model_id is None
    assert portfolio.cluster_count == 0
    # The MONETARY truth is still fully preserved.
    assert portfolio.known_open_risk == 100.0
    assert portfolio.total_open_risk == 100.0
    assert portfolio.correlation_complete is False
# ═══════════════════════════════════════════════════════════════════════════
# 8–11, 34, 41. PORTFOLIO SHAPE: zero / one / same-symbol / multi-symbol
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_position_account_is_complete_not_unavailable():
    """An empty COMPLETE set is a valid zero-risk account, not missing data."""
    portfolio = _derive(rows=()).portfolio
    assert portfolio.status is PortfolioStatus.COMPLETE
    assert portfolio.failure_reason is PortfolioFailureReason.NO_POSITIONS
    assert portfolio.open_position_count == 0
    assert portfolio.known_open_risk == 0.0
    assert portfolio.total_open_risk == 0.0
    assert portfolio.risk_complete is True
    assert portfolio.cluster_count == 0
    assert portfolio.largest_symbol_risk == 0.0
    assert portfolio.largest_symbol is None
    assert portfolio.position_set_complete is True


def test_empty_complete_set_is_distinguishable_from_unavailable_source():
    """§41: an empty complete set != an unreadable position source."""
    empty = _derive(rows=()).portfolio
    unavailable = _derive(rows=None).portfolio
    assert empty.status is PortfolioStatus.COMPLETE
    assert unavailable.status is PortfolioStatus.UNAVAILABLE
    assert unavailable.failure_reason is \
        PortfolioFailureReason.POSITION_SOURCE_UNAVAILABLE
    assert unavailable.total_open_risk is None
    assert unavailable.correlation_complete is False


def test_one_position_account():
    portfolio = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0})).portfolio
    assert portfolio.status is PortfolioStatus.COMPLETE
    assert portfolio.open_position_count == 1
    assert portfolio.symbols_open == 1
    assert portfolio.long_position_count == 1
    assert portfolio.short_position_count == 0
    assert portfolio.total_open_risk == 100.0
    assert portfolio.largest_symbol == "EURUSD"
    assert portfolio.largest_symbol_risk == 100.0
    assert portfolio.largest_symbol_risk_pct == 1.0


def test_multiple_positions_on_the_same_canonical_symbol_share_one_bucket():
    """Multiple tickets on one symbol -> ONE bucket, each risk counted once."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "EURUSD", "risk": 60.0},
                               {"symbol": "EURUSD", "risk": 40.0}))
    portfolio, symbols = cycle.portfolio, cycle.symbols
    assert portfolio.symbols_open == 1
    assert portfolio.open_position_count == 3
    assert portfolio.total_open_risk == 200.0
    assert len(symbols) == 1
    bucket = symbols[0]
    assert bucket.position_count == 3
    assert bucket.position_tickets == (1, 2, 3)
    assert bucket.known_risk == 200.0
    assert bucket.total_risk == 200.0


def test_multiple_symbols_across_several_clusters():
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "NAS100", "risk": 250.0},
                               {"symbol": "XAUUSD", "risk": 80.0}))
    portfolio = cycle.portfolio
    assert portfolio.symbols_open == 3
    assert portfolio.cluster_count == 3
    assert portfolio.max_cluster_risk == 250.0
    assert portfolio.max_cluster_id == "US_INDEX_CLUSTER"
    assert portfolio.largest_symbol == "NAS100"
    assert portfolio.largest_symbol_risk == 250.0
    assert portfolio.largest_symbol_risk_pct == pytest.approx(250 / 430)


def test_long_only_short_only_and_mixed_direction_accounts():
    """Gross long and gross short are persisted separately, never netted."""
    long_only = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                                   {"symbol": "GBPUSD", "risk": 50.0})).portfolio
    assert long_only.largest_direction is Direction.LONG
    assert long_only.long_risk == 150.0
    assert long_only.short_risk == 0.0
    assert long_only.gross_risk == 150.0

    short_only = _derive(rows=_rows({"symbol": "EURUSD", "side": "SELL",
                                     "risk": 100.0})).portfolio
    assert short_only.largest_direction is Direction.SHORT
    assert short_only.long_risk == 0.0
    assert short_only.short_risk == 100.0
    assert short_only.gross_risk == 100.0

    mixed = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "GBPUSD", "side": "SELL",
                                "risk": 120.0})).portfolio
    assert mixed.long_risk == 100.0
    assert mixed.short_risk == 120.0
    # Gross is NOT the net: netting would understate exposure.
    assert mixed.gross_risk == 220.0
    assert mixed.net_known_directional_risk == -20.0
    assert mixed.total_open_risk == 220.0
# ═══════════════════════════════════════════════════════════════════════════
# 12–13, 17–20, 24–25, 35–40. CLUSTERS, NO HEDGE CREDIT, INCOMPLETE RISK
# ═══════════════════════════════════════════════════════════════════════════


def test_representative_cluster_acceptance_scenario():
    """§35: EURUSD 100 + GBPUSD 120 + XAUUSD SELL 80 under the test model."""
    cycle = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "GBPUSD", "risk": 120.0},
        {"symbol": "XAUUSD", "side": "SELL", "risk": 80.0}))
    portfolio = cycle.portfolio
    assert portfolio.gross_risk == 300.0
    assert portfolio.total_open_risk == 300.0
    fx = next(c for c in cycle.clusters if c.cluster_id == "FX_USD_LONG_CLUSTER")
    assert fx.known_cluster_risk == 220.0
    assert fx.total_cluster_risk == 220.0
    assert fx.member_symbols == ("EURUSD", "GBPUSD")
    # XAUUSD is outside the governed map -> deterministic singleton.
    gold = next(c for c in cycle.clusters
                if c.cluster_id == unclassified_cluster_id("XAUUSD"))
    assert gold.member_symbols == ("XAUUSD",)
    assert gold.known_cluster_risk == 80.0
    assert portfolio.max_cluster_risk == 220.0
    assert portfolio.max_cluster_id == "FX_USD_LONG_CLUSTER"
    assert portfolio.correlated_cluster_risk == 220.0


def test_acceptance_scenario_with_gbpusd_risk_unknown():
    """§35: unknown member risk floors the cluster WITHOUT under-reporting."""
    cycle = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "GBPUSD", "risk": None},
        {"symbol": "XAUUSD", "side": "SELL", "risk": 80.0}))
    portfolio = cycle.portfolio
    fx = next(c for c in cycle.clusters if c.cluster_id == "FX_USD_LONG_CLUSTER")
    assert fx.known_cluster_risk == 100.0
    assert fx.cluster_risk_complete is False
    assert fx.total_cluster_risk is None
    assert fx.unknown_risk_position_count == 1
    # Portfolio total stays withheld; the floor is still visible.
    assert portfolio.total_open_risk is None
    assert portfolio.risk_complete is False
    assert portfolio.known_open_risk == 180.0
    assert portfolio.status is PortfolioStatus.PARTIAL
    assert portfolio.failure_reason is PortfolioFailureReason.RISK_INCOMPLETE


def test_opposing_directions_preserve_gross_cluster_risk_with_no_hedge_credit():
    """§36: EURUSD BUY 100 + GBPUSD SELL 120 in one cluster == 220, never 20."""
    cycle = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "GBPUSD", "side": "SELL", "risk": 120.0}))
    fx = cycle.clusters[0]
    assert fx.cluster_id == "FX_USD_LONG_CLUSTER"
    assert fx.known_cluster_risk == 220.0
    assert fx.total_cluster_risk == 220.0
    assert fx.hedging_credit_applied is False
    assert fx.long_risk == 100.0
    assert fx.short_risk == 120.0
    assert fx.direction is Direction.MIXED
    # The net is a LABELLED indicator and is never substituted for the total.
    assert fx.net_known_directional_risk == -20.0
    assert cycle.portfolio.total_open_risk == 220.0


def test_cross_symbol_correlated_bets_share_one_directional_theme():
    """§11: separate tickets, one concentrated theme -- shown, not inferred."""
    cycle = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "GBPUSD", "risk": 120.0}))
    portfolio = cycle.portfolio
    assert portfolio.open_position_count == 2
    assert portfolio.symbols_open == 2
    assert portfolio.cluster_count == 1
    assert portfolio.correlated_cluster_risk == 220.0
    assert portfolio.total_open_risk == 220.0


def test_index_and_gold_exposure_use_their_governed_clusters():
    """§45/§46: index and commodity exposure are classified by the model."""
    cycle = _derive(rows=_rows({"symbol": "NAS100", "risk": 300.0},
                               {"symbol": "US500", "risk": 200.0},
                               {"symbol": "XAUUSD", "risk": 90.0}))
    by_id = {c.cluster_id: c for c in cycle.clusters}
    assert by_id["US_INDEX_CLUSTER"].known_cluster_risk == 500.0
    assert by_id["US_INDEX_CLUSTER"].member_symbols == ("NAS100", "US500")
    assert by_id[unclassified_cluster_id("XAUUSD")].known_cluster_risk == 90.0


def test_same_cluster_never_double_counts_a_position():
    """§10/§37: each ticket contributes to its cluster sum exactly once."""
    cycle = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "EURUSD", "risk": 60.0},
        {"symbol": "GBPUSD", "risk": 40.0}))
    fx = cycle.clusters[0]
    assert fx.position_tickets == (1, 2, 3)
    assert len(set(fx.position_tickets)) == 3
    assert fx.member_position_count == 3
    assert fx.known_cluster_risk == 200.0
    assert fx.known_cluster_risk == cycle.portfolio.total_open_risk
def test_unknown_position_risk_propagates_incompleteness_to_every_level():
    """§13/§40: unknown risk is never zero and never silently absorbed."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": None}))
    portfolio, symbols, clusters = cycle.portfolio, cycle.symbols, cycle.clusters
    assert portfolio.total_open_risk is None
    assert portfolio.risk_complete is False
    assert symbols[0].total_risk is None
    assert symbols[0].risk_complete is False
    assert clusters[0].total_cluster_risk is None
    assert clusters[0].cluster_risk_complete is False
    # Cluster MEMBERSHIP is still complete and useful.
    assert portfolio.correlation_complete is True


def test_no_stop_underlying_position_propagates_incomplete_portfolio():
    """§40: an UNPROTECTED position makes the whole portfolio risk-incomplete."""
    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": None}))
    position = cycle.positions[0]
    assert position.has_stop is False
    assert position.monetary_risk_to_sl is None
    portfolio = aggregate_portfolio_exposure(
        ACCOUNT_A, cycle, model=ACCEPTANCE_MODEL).portfolio
    assert portfolio.status is PortfolioStatus.PARTIAL
    assert portfolio.failure_reason is PortfolioFailureReason.RISK_INCOMPLETE
    assert portfolio.total_open_risk is None
    assert portfolio.known_open_risk == 0.0


def test_incomplete_position_set_makes_portfolio_unavailable_not_a_subset():
    """§18: the visible subset is never treated as the whole account."""
    portfolio = _derive(rows=None).portfolio
    assert portfolio.status is PortfolioStatus.UNAVAILABLE
    assert portfolio.position_set_complete is False
    assert portfolio.total_open_risk is None
    assert portfolio.correlation_complete is False
    assert portfolio.cluster_count == 0
    assert portfolio.max_cluster_risk is None


# ═══════════════════════════════════════════════════════════════════════════
# 21–25. PERCENTAGES AND EQUITY LINKAGE
# ═══════════════════════════════════════════════════════════════════════════


def _account_snapshot(account: AccountIdentity, *, equity=1000.0,
                      snapshot_id: str = "asnap_test", balance=800.0):
    from core.risk.account_snapshot import AccountSnapshot

    return AccountSnapshot(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login, snapshot_id=snapshot_id,
        observed_at_utc="2026-10-02T12:00:00.000000Z",
        observed_at_utc_ms=T0_MS, source="MT5_ACCOUNT_INFO",
        balance=balance, equity=equity, currency="GBP",
    )


def test_risk_pct_equity_is_derived_only_from_the_exact_linked_snapshot():
    """§21/§22: monetary risk / the exact linked equity, never balance."""
    snapshot = _account_snapshot(ACCOUNT_A, equity=1000.0, balance=250.0)
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                    account_snapshot_id=snapshot.snapshot_id,
                    account_snapshot=snapshot)
    portfolio = cycle.portfolio
    assert portfolio.equity == 1000.0
    assert portfolio.risk_pct_equity == pytest.approx(0.1)
    # The balance fallback must never be used.
    assert portfolio.risk_pct_equity != pytest.approx(100.0 / 250.0)
    assert cycle.symbols[0].risk_pct_equity == pytest.approx(0.1)
    assert cycle.clusters[0].cluster_risk_pct_equity == pytest.approx(0.1)


def test_risk_pct_equity_is_none_when_equity_is_unavailable():
    """§22: unavailable equity -> None, never a guessed denominator."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    assert cycle.portfolio.equity is None
    assert cycle.portfolio.risk_pct_equity is None
    assert cycle.symbols[0].risk_pct_equity is None


def test_no_balance_fallback_when_equity_is_missing():
    """§23: balance is never silently substituted for equity."""
    snapshot = _account_snapshot(ACCOUNT_A, equity=None, balance=5000.0)
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                    account_snapshot_id=snapshot.snapshot_id,
                    account_snapshot=snapshot)
    assert cycle.portfolio.equity is None
    assert cycle.portfolio.risk_pct_equity is None
    assert "equity" in cycle.portfolio.unavailable_fields
def test_symbol_and_cluster_percentages_are_exposed_when_complete():
    """§23/§24: percentages exist only with an authoritative denominator."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "NAS100", "risk": 300.0}))
    eur = next(s for s in cycle.symbols if s.canonical_symbol == "EURUSD")
    assert eur.risk_pct_total_open_risk == pytest.approx(0.25)
    assert eur.risk_pct_equity is None  # no snapshot supplied
    index = next(c for c in cycle.clusters if c.cluster_id == "US_INDEX_CLUSTER")
    assert index.cluster_risk_pct_total_open_risk == pytest.approx(0.75)
    assert index.concentration_pct == pytest.approx(0.75)
    assert cycle.portfolio.largest_symbol_risk_pct == pytest.approx(0.75)


def test_percentages_are_withheld_when_the_denominator_is_only_a_floor():
    """§7/§23/§24: a known-risk floor is NOT an authoritative denominator."""
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "NAS100", "risk": 300.0},
                               {"symbol": "GBPUSD", "risk": None}))
    portfolio = cycle.portfolio
    assert portfolio.known_open_risk == 400.0
    assert portfolio.total_open_risk is None
    assert portfolio.largest_symbol_risk_pct is None
    assert portfolio.largest_direction_risk_pct is None
    for row in cycle.symbols:
        if row.known_risk is not None:
            assert row.risk_pct_total_open_risk is None
    for row in cycle.clusters:
        assert row.concentration_pct is None
        assert row.cluster_risk_pct_total_open_risk is None


def test_direction_and_symbol_concentration_metrics_are_exposed():
    """§25: the finite, prop-rule-useful concentration metric set."""
    # 275 == 11 index lots x 25.00/lot at the exact tick-value identity.
    portfolio = _derive(rows=_rows(
        {"symbol": "EURUSD", "risk": 100.0},
        {"symbol": "GBPUSD", "risk": 120.0},
        {"symbol": "NAS100", "side": "SELL", "risk": 275.0})).portfolio
    assert portfolio.total_open_risk == 495.0
    assert portfolio.largest_symbol == "NAS100"
    assert portfolio.largest_symbol_risk == 275.0
    assert portfolio.largest_direction is Direction.SHORT
    assert portfolio.largest_direction_risk == 275.0
    assert portfolio.largest_symbol_risk_pct == pytest.approx(275 / 495)
    assert portfolio.max_cluster_risk_pct == pytest.approx(275 / 495)
    assert portfolio.top_n_concentration[0][0] == "NAS100"
    assert portfolio.unclassified_symbol_count == 0
# ═══════════════════════════════════════════════════════════════════════════
# 8–9, 26–30, 43–44. IDENTITY, VERSIONING, LINKAGE, STALENESS, ALIASES
# ═══════════════════════════════════════════════════════════════════════════


def test_exact_observation_linkage_to_2a_and_2b():
    """§17/§27: portfolio binds to the EXACT 2A/2B lineage, never by time."""
    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                     account_snapshot_id="asnap_exact")
    derived = aggregate_portfolio_exposure(
        ACCOUNT_A, cycle, model=ACCEPTANCE_MODEL).portfolio
    assert derived.observation_id == cycle.observation_id
    assert derived.observation_id == cycle.open_risk.observation_id
    assert derived.open_risk_snapshot_id == cycle.open_risk.open_risk_id
    assert derived.account_snapshot_id == "asnap_exact"
    assert derived.observed_at_utc_ms == cycle.open_risk.observed_at_utc_ms


def test_timestamp_nearest_join_is_forbidden_for_account_snapshots():
    """§28: a mismatched observation lineage is rejected, never nearest-joined."""
    from core.risk.portfolio_exposure import PortfolioExposureError

    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                     account_snapshot_id="asnap_current")
    other = _account_snapshot(ACCOUNT_A, snapshot_id="asnap_stale")
    with pytest.raises(PortfolioExposureError):
        aggregate_portfolio_exposure(
            ACCOUNT_A, cycle, model=ACCEPTANCE_MODEL, account_snapshot=other)


def test_cross_account_snapshot_is_rejected():
    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    other = _account_snapshot(ACCOUNT_B, snapshot_id="asnap_b")
    with pytest.raises(AccountIdentityError):
        aggregate_portfolio_exposure(
            ACCOUNT_A, cycle, model=ACCEPTANCE_MODEL, account_snapshot=other)


def test_identity_is_deterministic_and_excludes_money():
    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    first = derive_portfolio_exposure_id(ACCOUNT_A, cycle.open_risk, ACCEPTANCE_MODEL)
    second = derive_portfolio_exposure_id(ACCOUNT_A, cycle.open_risk, ACCEPTANCE_MODEL)
    assert first == second
    assert first.startswith("pexp_")
    assert derive_cluster_exposure_id(
        ACCOUNT_A, cycle.open_risk, ACCEPTANCE_MODEL,
        "FX_USD_LONG_CLUSTER").startswith("cexp_")


def test_model_version_is_persisted_and_changes_identity():
    """§26: a new model version starts NEW rows; history is never rewritten."""
    v2 = build_symbol_correlation_model(
        [CorrelationCluster("FX_USD_LONG_CLUSTER", ("EURUSD", "GBPUSD", "AUDUSD")),
         CorrelationCluster("US_INDEX_CLUSTER", ("NAS100", "US500"))],
        model_id="TEST_CORRELATION", model_version="v2",
        effective_from_utc="2026-11-01T00:00:00Z", provenance="v2")
    cycle = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                                {"symbol": "GBPUSD", "risk": 120.0}))
    first = aggregate_portfolio_exposure(ACCOUNT_A, cycle, model=ACCEPTANCE_MODEL)
    second = aggregate_portfolio_exposure(ACCOUNT_A, cycle, model=v2)
    assert first.portfolio.correlation_model_version == "v1"
    assert second.portfolio.correlation_model_version == "v2"
    assert (first.portfolio.portfolio_exposure_id
            != second.portfolio.portfolio_exposure_id)
    assert first.portfolio.to_dict()["correlation_model_version"] == "v1"


def test_model_version_is_carried_on_every_cluster_row():
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    for cluster in cycle.clusters:
        assert cluster.cluster_model_id == "TEST_CORRELATION"
        assert cluster.cluster_model_version == "v1"
    for row in cycle.symbols:
        assert row.cluster_model_id == "TEST_CORRELATION"
        assert row.cluster_model_version == "v1"


def test_stale_source_makes_the_portfolio_stale():
    """§30: a derived snapshot can never appear fresher than its source."""
    portfolio = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0})).portfolio
    assert portfolio.is_stale(now_ms=T0_MS + 10_000, threshold_ms=30_000) is False
    assert portfolio.is_stale(now_ms=T0_MS + 60_000, threshold_ms=30_000) is True
    stale = portfolio.evaluate(now_ms=T0_MS + 60_000, threshold_ms=30_000)
    assert stale.status is PortfolioStatus.STALE
    assert stale.failure_reason is PortfolioFailureReason.STALE_SOURCE
    # STALE is a read-time evaluation, never a persisted observation fact.
    assert portfolio.status is PortfolioStatus.COMPLETE


def test_canonical_symbol_aliases_resolve_upstream_not_in_correlation():
    """§5/§44: broker aliases are canonicalised BEFORE correlation sees them."""
    aliases = {"NAS100_SB": "NAS100", "USTECH100M": "NAS100",
               "EURUSD_SB": "EURUSD", "GBPUSD_SB": "GBPUSD"}
    resolved = _derive(
        rows=[dict(_row(1, symbol="NAS100_SB", risk=100.0))],
        canonical_resolver=lambda broker: aliases.get(broker, broker))
    assert len(resolved.symbols) == 1
    assert resolved.symbols[0].canonical_symbol == "NAS100"
    assert resolved.clusters[0].cluster_id == "US_INDEX_CLUSTER"
    # Two broker aliases of ONE canonical symbol collapse to ONE exposure.
    both = _derive(
        rows=[dict(_row(1, symbol="NAS100_SB", risk=100.0)),
              dict(_row(2, symbol="USTECH100M", risk=50.0))],
        canonical_resolver=lambda broker: "NAS100")
    assert len(both.symbols) == 1
    assert both.symbols[0].position_count == 2
    assert both.symbols[0].known_risk == 150.0
    assert both.clusters[0].known_cluster_risk == 150.0
# ═══════════════════════════════════════════════════════════════════════════
# 27, 31–33, 35–37, 39, 50. PERSISTENCE, STORE, CADENCE, OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def isolated_outbox(tmp_path, monkeypatch):
    configure_delivery_outbox(tmp_path / "outbox.sqlite3")
    yield tmp_path
    configure_delivery_outbox(None)


def test_datasets_are_registered_with_exact_identity_and_grain():
    """§27/§28: three grains, three datasets, three identity namespaces."""
    for dataset in (DATASET, CLUSTER_DATASET, CROSS_ACCOUNT_DATASET):
        assert dataset in PRODUCTION_SCHEMA_REGISTRY
        assert is_symbol_scoped(dataset) is False  # account/date scoped
        assert EXACT_IDENTITY_FIELDS[dataset]
    assert EXACT_IDENTITY_FIELDS[DATASET] == ("account_id", "portfolio_exposure_id")
    assert EXACT_IDENTITY_FIELDS[CLUSTER_DATASET] == ("account_id", "cluster_exposure_id")
    assert EXACT_IDENTITY_FIELDS[CROSS_ACCOUNT_DATASET] == \
        ("cross_account_exposure_id",)
    # Only the per-account grains are account-scoped obligations.
    assert DATASET in ACCOUNT_SCOPED_DATASETS
    assert CLUSTER_DATASET in ACCOUNT_SCOPED_DATASETS
    assert CROSS_ACCOUNT_DATASET not in ACCOUNT_SCOPED_DATASETS


def test_schemas_come_from_the_production_contract():
    from core.risk import portfolio_exposure as module

    assert SCHEMA_VERSION == current_schema(DATASET)
    assert CLUSTER_SCHEMA_VERSION == current_schema(CLUSTER_DATASET)
    assert CROSS_ACCOUNT_SCHEMA_VERSION == current_schema(CROSS_ACCOUNT_DATASET)
    assert module.DATASET == "portfolio_exposure"
    assert module.CLUSTER_DATASET == "correlation_exposure"


def test_producer_persists_both_grains_through_canonical_delivery(
        tmp_path, isolated_outbox):
    """§27/§31: local fsync + the certified Block 1 handoff, never a direct S3."""
    from core.canonical_delivery import get_delivery_outbox

    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "GBPUSD", "risk": 120.0}))
    persist_portfolio_exposure(
        cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    portfolio_files = list((tmp_path / "pf").rglob("*.jsonl"))
    cluster_files = list((tmp_path / "cl").rglob("*.jsonl"))
    assert len(portfolio_files) == 1
    assert len(cluster_files) == 1
    datasets = {r.dataset for r in get_delivery_outbox().records()}
    assert datasets == {DATASET, CLUSTER_DATASET}
    from core.lifecycle_evidence_obligations import obligation_ledger
    for record in get_delivery_outbox().records():
        assert record.lifecycle_obligation_id
        obligation = obligation_ledger().get(record.lifecycle_obligation_id)
        assert obligation is not None
        assert obligation.expected_dataset == record.dataset
        assert dict(obligation.expected_identity) == dict(record.record_identity)
    # Both grains provably share ONE observation cycle.
    rows = [json.loads(line) for line in cluster_files[0].read_text().splitlines()]
    assert {r["portfolio_exposure_id"] for r in rows} == \
           {cycle.portfolio.portfolio_exposure_id}
    assert {r["observation_id"] for r in rows} == {cycle.observation_id}


def test_persisted_records_round_trip_without_reinterpretation():
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "XAUUSD", "side": "SELL",
                                "risk": 80.0}))
    assert PortfolioExposure.from_dict(cycle.portfolio.to_dict()) == cycle.portfolio
    for row in cycle.symbols:
        assert SymbolExposure.from_dict(row.to_dict()) == row
    for row in cycle.clusters:
        assert CorrelationClusterExposure.from_dict(row.to_dict()) == row


def test_restart_preserves_history_and_latest_is_the_newest_observation(tmp_path):
    """§30/§43: append-only history survives restart; latest wins."""
    clock = FixedClock(T0)
    first = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}), clock=clock)
    persist_portfolio_exposure(
        first, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    clock.advance(minutes=5)
    second = _derive(rows=_rows({"symbol": "GBPUSD", "risk": 250.0}), clock=clock)
    persist_portfolio_exposure(
        second, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")

    store = PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    latest = store.latest_portfolio_exposure(ACCOUNT_A.account_id)
    assert latest.portfolio_exposure_id == second.portfolio.portfolio_exposure_id
    assert latest.largest_symbol == "GBPUSD"
    # The first observation is still on disk as immutable history.
    history = list((tmp_path / "pf").rglob("*.jsonl"))
    assert sum(1 for path in history for _ in path.read_text().splitlines()) == 2
def test_store_exposes_exact_account_latest_symbol_and_clusters(tmp_path):
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "GBPUSD", "risk": 120.0}))
    persist_portfolio_exposure(
        cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    store = PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    account = ACCOUNT_A.account_id
    assert store.latest_portfolio_exposure(account).total_open_risk == 220.0
    assert store.latest_symbol_exposure(
        account, "EURUSD").known_risk == 100.0
    assert [c.cluster_id for c in store.latest_cluster_exposures(account)] == \
           ["FX_USD_LONG_CLUSTER"]
    assert store.latest_cluster_exposure(
        account, "FX_USD_LONG_CLUSTER").known_cluster_risk == 220.0


def test_latest_account_a_never_returns_account_b(tmp_path):
    """§29/§20: exact account lookup, no cross-account fallback."""
    cycle_a = _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    cycle_b = _derive(ACCOUNT_B, rows=_rows({"symbol": "EURUSD", "risk": 900.0}))
    for cycle in (cycle_a, cycle_b):
        persist_portfolio_exposure(
            cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    store = PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    assert store.latest_portfolio_exposure(
        ACCOUNT_A.account_id).total_open_risk == 100.0
    assert store.latest_portfolio_exposure(
        ACCOUNT_B.account_id).total_open_risk == 900.0
    with pytest.raises(PortfolioExposureNotFound):
        store.latest_portfolio_exposure("NEVER_OBSERVED")
    with pytest.raises(PortfolioExposureNotFound):
        store.latest_symbol_exposure(ACCOUNT_A.account_id, "NAS100")


def test_same_symbol_across_accounts_stays_independent(tmp_path):
    """§20: the same symbol on two accounts never satisfies the other account."""
    cycle_a = _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    cycle_b = _derive(ACCOUNT_B, rows=_rows({"symbol": "EURUSD", "risk": 150.0}))
    for cycle in (cycle_a, cycle_b):
        persist_portfolio_exposure(
            cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    store = PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    assert store.latest_symbol_exposure(
        ACCOUNT_A.account_id, "EURUSD").known_risk == 100.0
    assert store.latest_symbol_exposure(
        ACCOUNT_B.account_id, "EURUSD").known_risk == 150.0


def test_observability_health_exposes_the_required_per_account_fields(tmp_path):
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                               {"symbol": "XAUUSD", "risk": 80.0}))
    persist_portfolio_exposure(
        cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    store = PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    health = store.health(now_ms=T0_MS + 1_000)
    row = health["accounts"][ACCOUNT_A.account_id]
    assert row["status"] == "COMPLETE"
    assert row["failure_reason"] == "NONE"
    assert row["source_observation_age_ms"] == 1_000
    assert row["open_position_count"] == 2
    assert row["known_open_risk"] == 180.0
    assert row["total_open_risk"] == 180.0
    assert row["largest_symbol"] == "EURUSD"
    assert row["max_cluster_id"] == "FX_USD_LONG_CLUSTER"
    assert row["unclassified_symbol_count"] == 1
    assert row["correlation_model_key"] == "TEST_CORRELATION@v1"
    assert health["last_success_observed_at_utc_ms"] == T0_MS


def test_runtime_cycle_derives_2c_in_the_same_observation_as_2b(tmp_path):
    """§31/§50: 2A -> 2B -> 2C under one observation lineage, no new thread."""
    snapshot = _account_snapshot(ACCOUNT_A, equity=2000.0)
    cycle_2b = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                        clock=FixedClock(T0), currency="GBP",
                        account_snapshot_id=snapshot.snapshot_id)
    derived = observe_account_portfolio(
        ACCOUNT_A, cycle_2b, model=ACCEPTANCE_MODEL,
        account_snapshot=snapshot, base_dir=tmp_path / "pf",
        cluster_dir=tmp_path / "cl")
    assert derived.observation_id == cycle_2b.observation_id
    assert derived.portfolio.account_snapshot_id == snapshot.snapshot_id
    assert derived.portfolio.open_risk_snapshot_id == cycle_2b.open_risk.open_risk_id
    assert derived.portfolio.risk_pct_equity == pytest.approx(0.05)
    # No heartbeat/thread machinery was introduced for Block 2C.
    from core.risk import portfolio_exposure as module

    assert not hasattr(module, "PortfolioExposureHeartbeat")


def test_producer_derives_without_persisting_when_asked():
    cycle_2b = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    producer = PortfolioExposureProducer(persist=False, model=ACCEPTANCE_MODEL)
    derived = producer.derive(cycle_2b)
    assert derived.portfolio.total_open_risk == 100.0
# ═══════════════════════════════════════════════════════════════════════════
# 19–21, 37, 48–49. CROSS-ACCOUNT AGGREGATION AND CURRENCY SAFETY
# ═══════════════════════════════════════════════════════════════════════════


def _store_with(tmp_path, *cycles):
    for cycle in cycles:
        persist_portfolio_exposure(
            cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    return PortfolioExposureStore(
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")


def test_cross_account_same_currency_combines_explicitly(tmp_path):
    """§48: same-currency accounts combine, and the record stays labelled."""
    store = _store_with(
        tmp_path,
        _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0})),
        _derive(ACCOUNT_B, rows=_rows({"symbol": "EURUSD", "risk": 250.0})))
    aggregate = aggregate_cross_account_exposure(
        store, [ACCOUNT_A.account_id, ACCOUNT_B.account_id],
        model=ACCEPTANCE_MODEL)
    assert isinstance(aggregate, CrossAccountPortfolioExposure)
    assert aggregate.account_count == 2
    assert aggregate.account_ids == tuple(sorted(
        [ACCOUNT_A.account_id, ACCOUNT_B.account_id]))
    assert aggregate.combined_known_risk == 350.0
    assert aggregate.combined_total_risk == 350.0
    assert aggregate.combined_risk_complete is True
    assert [b.currency for b in aggregate.currency_buckets] == ["GBP"]
    assert aggregate.cross_account_exposure_id.startswith("xacc_")


def test_cross_account_mixed_currencies_never_produce_one_monetary_total(tmp_path):
    """§37/§49: GBP + USD stays in buckets; no FX conversion is guessed."""
    store = _store_with(
        tmp_path,
        _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0}),
                currency="GBP"),
        _derive(ACCOUNT_B, rows=_rows({"symbol": "EURUSD", "risk": 150.0}),
                currency="USD"))
    aggregate = aggregate_cross_account_exposure(
        store, [ACCOUNT_A.account_id, ACCOUNT_B.account_id],
        model=ACCEPTANCE_MODEL)
    assert aggregate.combined_known_risk is None
    assert aggregate.combined_total_risk is None
    assert aggregate.combined_risk_complete is False
    assert aggregate.status is PortfolioStatus.PARTIAL
    assert aggregate.failure_reason is \
        PortfolioFailureReason.CROSS_ACCOUNT_CURRENCY_DIVERGENCE
    buckets = {b.currency: b for b in aggregate.currency_buckets}
    assert buckets["GBP"].known_risk == 100.0
    assert buckets["USD"].known_risk == 150.0
    assert buckets["GBP"].account_ids == (ACCOUNT_A.account_id,)
    assert buckets["USD"].account_ids == (ACCOUNT_B.account_id,)


def test_cross_account_never_substitutes_one_account_for_another(tmp_path):
    """§19/§20: a missing account is named, never silently filled."""
    store = _store_with(
        tmp_path, _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0})))
    aggregate = aggregate_cross_account_exposure(
        store, [ACCOUNT_A.account_id, "NEVER_OBSERVED"], model=ACCEPTANCE_MODEL)
    assert aggregate.account_count == 1
    assert aggregate.missing_account_ids == ("NEVER_OBSERVED",)
    assert aggregate.combined_known_risk is None  # currency is UNKNOWN for B
    assert aggregate.status is PortfolioStatus.PARTIAL
    with pytest.raises(PortfolioExposureNotFound):
        aggregate_cross_account_exposure(
            store, ["NEVER_OBSERVED"], model=ACCEPTANCE_MODEL)


def test_cross_account_aggregation_is_opt_in_and_explicit():
    """§19: it is a separate, explicitly requested grain -- never implicit."""
    from core.risk.portfolio_exposure import PortfolioExposureError

    with pytest.raises(PortfolioExposureError):
        aggregate_cross_account_exposure(
            None, [ACCOUNT_A.account_id], requested=False)


def test_cross_account_record_round_trips_and_never_mixes_with_grain_a(tmp_path):
    """§28: a different grain, a different identity namespace, same dataset family."""
    store = _store_with(
        tmp_path,
        _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0})))
    aggregate = aggregate_cross_account_exposure(
        store, [ACCOUNT_A.account_id], model=ACCEPTANCE_MODEL)
    payload = aggregate.to_dict()
    assert payload["record_kind"] == "CROSS_ACCOUNT_PORTFOLIO_EXPOSURE"
    assert payload["cross_account_exposure_id"] != aggregate.account_ids[0]
    assert CROSS_ACCOUNT_DATASET != DATASET
    assert derive_cross_account_exposure_id(
        [ACCOUNT_A.account_id], [aggregate.observation_ids[0]],
        ACCEPTANCE_MODEL).startswith("xacc_")


def test_cross_account_exposure_persists_through_canonical_delivery(
        tmp_path, isolated_outbox):
    from core.canonical_delivery import get_delivery_outbox

    store = _store_with(
        tmp_path,
        _derive(ACCOUNT_A, rows=_rows({"symbol": "EURUSD", "risk": 100.0})))
    aggregate = aggregate_cross_account_exposure(
        store, [ACCOUNT_A.account_id], model=ACCEPTANCE_MODEL)
    persist_cross_account_exposure(
        aggregate, base_dir=tmp_path / "xacc")
    record = next(r for r in get_delivery_outbox().records()
                  if r.dataset == CROSS_ACCOUNT_DATASET)
    assert record.lifecycle_obligation_id
    from core.lifecycle_evidence_obligations import obligation_ledger
    obligation = obligation_ledger().get(record.lifecycle_obligation_id)
    assert obligation is not None
    assert dict(obligation.expected_identity) == dict(record.record_identity)
    rows = [json.loads(line)
            for path in (tmp_path / "xacc").rglob("*.jsonl")
            for line in path.read_text().splitlines()]
    assert rows[0]["cross_account_exposure_id"] == \
           aggregate.cross_account_exposure_id
# ═══════════════════════════════════════════════════════════════════════════
# 27, 31–33, 38, 41. DISPOSITION, DELIVERY RESILIENCE, END-TO-END ACCEPTANCE
# ═══════════════════════════════════════════════════════════════════════════


def test_research_disposition_is_truthfully_intentionally_operational():
    """§38: INTENTIONALLY_OPERATIONAL is valid; SUPPORTING_CONSUMED is not."""
    for dataset in (DATASET, CLUSTER_DATASET, CROSS_ACCOUNT_DATASET):
        disposition = RESEARCH_DISPOSITIONS[dataset]
        assert disposition.dataset == dataset
        assert disposition.status is \
            ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL
        assert disposition.consumers == ()
        assert disposition.reason and disposition.research_purpose
        assert disposition.lineage_guard_notes
        assert "account_id" in disposition.join_keys or \
               "cross_account_exposure_id" in disposition.join_keys


def test_canonical_delivery_handoff_retains_local_evidence_on_outbox_failure(
        tmp_path, monkeypatch):
    """§27/§33: a durable handoff failure never destroys local truth."""
    import core.canonical_delivery as delivery
    from core.risk.portfolio_exposure import persist_portfolio_exposure as persist

    def _explode(**kwargs):
        raise RuntimeError("S3_UNAVAILABLE")

    monkeypatch.setattr(delivery, "enqueue_canonical_delivery", _explode)
    cycle = _derive(rows=_rows({"symbol": "EURUSD", "risk": 100.0}))
    # The local fsync happens BEFORE the handoff enqueue, so evidence survives.
    try:
        persist(cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    except RuntimeError:
        pass
    files = list((tmp_path / "pf").rglob("*.jsonl"))
    assert files, "local durable evidence must survive a handoff failure"
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert rows[0]["total_open_risk"] == 100.0


def test_module_performs_no_direct_s3_writes():
    """§27: no direct S3 PUT; only the certified Block 1 handoff."""
    import core.risk.portfolio_exposure as module

    source = inspect.getsource(module)
    assert "enqueue_canonical_delivery" in source
    assert "try_prepare_local_jsonl_handoffs" in source
    assert ".put_object(" not in source


def test_end_to_end_observation_cycle_links_2a_2b_and_2c_exactly(tmp_path):
    """§41: 2A -> 2B -> 2C, exact ids through all three stages."""
    from core.risk.account_snapshot import AccountSnapshot

    snapshot = AccountSnapshot(
        account_id=ACCOUNT_A.account_id, broker=ACCOUNT_A.broker,
        server=ACCOUNT_A.server, login=ACCOUNT_A.login,
        snapshot_id="asnap_e2e", observed_at_utc="2026-10-02T12:00:00.000000Z",
        observed_at_utc_ms=T0_MS, source="MT5_ACCOUNT_INFO",
        balance=1000.0, equity=2000.0, currency="GBP")
    cycle_2b = _observe(rows=_rows({"symbol": "EURUSD", "risk": 100.0},
                                   {"symbol": "GBPUSD", "risk": 120.0}),
                        clock=FixedClock(T0), currency="GBP",
                        account_snapshot_id=snapshot.snapshot_id)
    derived = observe_account_portfolio(
        ACCOUNT_A, cycle_2b, model=ACCEPTANCE_MODEL, account_snapshot=snapshot,
        base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
    portfolio = derived.portfolio
    assert portfolio.account_snapshot_id == snapshot.snapshot_id
    assert portfolio.observation_id == cycle_2b.open_risk.observation_id
    assert portfolio.open_risk_snapshot_id == cycle_2b.open_risk.open_risk_id
    assert portfolio.risk_pct_equity == pytest.approx(0.11)


def test_every_mutation_produces_a_new_immutable_portfolio_state(tmp_path):
    """§41: add / remove / unknown-risk / model change -> new state, old kept."""
    clock = FixedClock(T0)
    history = []

    def _emit(rows, model=ACCEPTANCE_MODEL):
        clock.advance(seconds=15)
        cycle = _derive(rows=rows, clock=clock, model=model)
        persist_portfolio_exposure(
            cycle, base_dir=tmp_path / "pf", cluster_dir=tmp_path / "cl")
        history.append(cycle.portfolio.to_dict())
        return cycle.portfolio

    base = _emit(_rows({"symbol": "EURUSD", "risk": 100.0}))
    added = _emit(_rows({"symbol": "EURUSD", "risk": 100.0},
                        {"symbol": "GBPUSD", "risk": 120.0}))
    removed = _emit(_rows({"symbol": "EURUSD", "risk": 100.0}))
    unknown = _emit(_rows({"symbol": "EURUSD", "risk": None}))
    v2 = build_symbol_correlation_model(
        [CorrelationCluster("FX_USD_LONG_CLUSTER", ("EURUSD", "GBPUSD"))],
        model_id="TEST_CORRELATION", model_version="v2",
        effective_from_utc="2026-11-01T00:00:00Z", provenance="v2")
    changed_model = _emit(_rows({"symbol": "EURUSD", "risk": 100.0}), model=v2)

    assert added.cluster_count == 1 and added.correlated_cluster_risk == 220.0
    assert removed.cluster_count == 1
    assert removed.total_open_risk == 100.0
    assert unknown.total_open_risk is None
    assert changed_model.correlation_model_version == "v2"

    ids = [row["portfolio_exposure_id"] for row in history]
    assert len(set(ids)) == 5, "every mutation is a distinct immutable state"
    # Historical rows are byte-identical to what was first written.
    lines = [line for path in (tmp_path / "pf").rglob("*.jsonl")
             for line in path.read_text().splitlines()]
    assert [json.loads(line) for line in lines] == history
    assert json.loads(lines[0])["total_open_risk"] == 100.0
    assert base.portfolio_exposure_id == ids[0]
