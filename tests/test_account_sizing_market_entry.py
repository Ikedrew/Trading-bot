"""Regression tests for MARKET-order position sizing (entry-type-aware).

Proven defect (COR-20260916-25561 / USDCAD*1789584900*TREND_CONTINUATION):
MARKET orders were sized using a stale strategy ``entry_reference`` instead of
the current executable broker price. The stale reference sat ~1.2 pips from the
SL, collapsing ``stop_distance`` and producing positions ~32x oversized
(~8% risk at entry vs the configured 0.25%).

These tests lock in the repaired behaviour in
``core.accounts.account_sizing.volume_for_account``:

* MARKET BUY  sizes off the current ASK.
* MARKET SELL sizes off the current BID.
* the stale ``target.entry`` never controls MARKET sizing.
* invalid directional geometry / missing quote / too-close stop fail closed.
* LIMIT / STOP orders still size off the intended order entry.
* the resulting volume never exceeds the configured monetary risk budget.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.accounts.account_sizing import volume_for_account
from core.accounts.fanout import AccountTarget
from core.v10.risk_engine import calculate_position_size_exact


# ─── FIXTURE HELPERS ──────────────────────────────────────────────────────────

# Deterministic USDCAD-like broker spec (5-digit FX). tick_value is the money
# per 1 lot per tick (tick_size); using 10.0 keeps the arithmetic exact and
# broker-independent for the assertions below.
def _usdcad_row(*, bid: float, ask: float, status: str = "available",
                tick_value: float = 0.7) -> dict:
    return {
        "canonical_symbol": "USDCAD",
        "broker_symbol": "USDCAD",
        "status": status,
        "trade_tick_value": tick_value,
        "trade_tick_size": 0.00001,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "point": 0.00001,
        "trade_stops_level": 10,   # min stop = 10 * 0.00001 = 0.0001 (1 pip)
        "bid": bid,
        "ask": ask,
    }


def _target(*, side: str, entry: float, sl: float, tp: float,
            entry_type: str = "MARKET") -> AccountTarget:
    return AccountTarget(
        account_id="METAQUOTES", broker="MetaQuotes", broker_server="MetaQuotes-Demo",
        login=1, canonical_symbol="USDCAD", broker_symbol="USDCAD",
        canonical_opportunity_id="USDCAD*1789584900*TREND_CONTINUATION",
        correlation_id="COR-20260916-25561-USDCAD-1835", decision_id="dec",
        account_execution_id="exec", trade_id="trade",
        requested_volume=0.0, entry=entry, sl=sl, tp=tp, side=side,
        pattern="TREND_CONTINUATION", observation_id="obs",
        entry_type=entry_type,
    )


def _snapshot(balance: float = 9704.0) -> dict:
    return {"balance": balance}


# ─── 1. THE EXACT USDCAD FAILURE GEOMETRY ─────────────────────────────────────

# Reproduces the proven failure inputs:
STALE_ENTRY = 1.393765          # stale strategy entry_reference (old sizing basis)
SL = 1.3938892857142857         # SL as submitted
BID = 1.39935
ASK = 1.39936                   # true executable BUY price at decision


def test_market_buy_does_not_use_stale_entry_reference():
    """AFTER repair: MARKET BUY sizes off ASK, not the stale entry_reference."""
    row = _usdcad_row(bid=BID, ask=ASK)
    target = _target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.4304414286)
    result = volume_for_account(
        target=target, snapshot=_snapshot(), symbol_row=row,
        strategy_family="TREND_CONTINUATION", horizon_type="EXTENDED")

    assert result.blocked_reason == ""
    # Sizing entry is the ASK, not the stale reference.
    assert result.sizing_entry == pytest.approx(ASK)
    assert result.sizing_entry != pytest.approx(STALE_ENTRY)
    # Stop distance is the real ~53 pip distance, not the collapsed ~1.2 pips.
    assert result.stop_distance == pytest.approx(abs(ASK - SL))
    assert result.stop_distance > 0.005      # ~53 pips
    assert result.volume > 0


def test_repaired_volume_far_smaller_than_stale_reference_would_produce():
    """Quantify the fix: repaired volume << the oversized (stale) volume."""
    row = _usdcad_row(bid=BID, ask=ASK)
    inputs = dict(tick_value=0.7, tick_size=0.00001,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01)

    # Independent recomputation using the same production formula.
    risk_amount = 9704.0 * 0.0025 * 0.75  # EXTENDED horizon modifier (0.75)

    stale_distance = abs(STALE_ENTRY - SL)          # ~0.000124 (defect)
    real_distance = abs(ASK - SL)                   # ~0.00547  (correct)
    oversized = calculate_position_size_exact(risk_amount=risk_amount,
                                              stop_distance=stale_distance, **inputs)
    corrected = calculate_position_size_exact(risk_amount=risk_amount,
                                              stop_distance=real_distance, **inputs)

    result = volume_for_account(
        target=_target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.43),
        snapshot=_snapshot(), symbol_row=row,
        strategy_family="TREND_CONTINUATION", horizon_type="EXTENDED")

    assert result.volume == pytest.approx(corrected)
    assert corrected < oversized
    # Correct sizing is at least ~40x smaller (stale distance was ~44x tighter).
    assert corrected * 40 <= oversized


# ─── 2. MARKET SELL ───────────────────────────────────────────────────────────

def test_market_sell_uses_bid_not_ask_not_stale_entry():
    row = _usdcad_row(bid=BID, ask=ASK)
    # SELL: SL must be ABOVE entry. Stale entry deliberately far below.
    target = _target(side="SELL", entry=1.39000, sl=1.40500, tp=1.38000)
    result = volume_for_account(
        target=target, snapshot=_snapshot(), symbol_row=row,
        strategy_family="", horizon_type="SCALP")

    assert result.blocked_reason == ""
    assert result.sizing_entry == pytest.approx(BID)      # SELL fills at BID
    assert result.sizing_entry != pytest.approx(ASK)
    assert result.sizing_entry != pytest.approx(1.39000)  # not the stale entry
    assert result.stop_distance == pytest.approx(abs(SL - BID) if False else abs(1.40500 - BID))


# ─── 3. FAIL-CLOSED GEOMETRY ──────────────────────────────────────────────────

def test_market_buy_sl_at_or_above_ask_rejected():
    row = _usdcad_row(bid=BID, ask=ASK)
    # SL above ASK → invalid BUY geometry (the original defect direction).
    target = _target(side="BUY", entry=STALE_ENTRY, sl=ASK + 0.0005, tp=1.45)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "INVALID_STOP_GEOMETRY"


def test_market_sell_sl_at_or_below_bid_rejected():
    row = _usdcad_row(bid=BID, ask=ASK)
    target = _target(side="SELL", entry=1.39000, sl=BID - 0.0005, tp=1.30)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "INVALID_STOP_GEOMETRY"


def test_original_usdcad_inverted_geometry_would_fail_closed():
    """The historical BUY had SL (1.39389) ABOVE the stale entry (1.393765).

    If that stale entry were ever used as the sizing entry, the SL would be
    above it — the repair now rejects such inverted geometry rather than
    masking it with abs()."""
    # Force the "stale as market" scenario by making the quote absent and the
    # pending path unavailable is covered elsewhere; here we show the raw
    # geometry check via a LIMIT order carrying the historical numbers.
    row = _usdcad_row(bid=BID, ask=ASK)
    target = _target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.43,
                     entry_type="LIMIT")
    # LIMIT sizes off target.entry (1.393765); SL 1.393889 is ABOVE it → invalid.
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "INVALID_STOP_GEOMETRY"


# ─── 4. MISSING / INVALID MARKET SNAPSHOT ─────────────────────────────────────

def test_market_missing_quote_fails_closed_no_stale_fallback():
    row = _usdcad_row(bid=0.0, ask=0.0)      # no executable quote
    target = _target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.43)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "MARKET_PRICE_UNAVAILABLE"
    # Must NOT have fallen back to the stale entry.
    assert result.sizing_entry == 0.0


def test_market_missing_quote_key_fails_closed():
    row = _usdcad_row(bid=BID, ask=ASK)
    row.pop("ask")   # ask entirely absent
    target = _target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.43)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "MARKET_PRICE_UNAVAILABLE"


# ─── 5. BROKER MINIMUM STOP DISTANCE ──────────────────────────────────────────

def test_stop_below_broker_minimum_rejected():
    row = _usdcad_row(bid=BID, ask=ASK)  # min stop = 10 * 0.00001 = 0.0001
    # SL only 0.00005 (half a pip) below ASK → below broker minimum.
    target = _target(side="BUY", entry=STALE_ENTRY, sl=ASK - 0.00005, tp=1.45)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.volume == 0.0
    assert result.blocked_reason == "STOP_TOO_CLOSE"


# ─── 6. LIMIT / STOP PENDING ORDERS PRESERVED ─────────────────────────────────

@pytest.mark.parametrize("entry_type", ["LIMIT", "STOP"])
def test_pending_orders_size_off_intended_entry(entry_type):
    row = _usdcad_row(bid=BID, ask=ASK)
    # Pending BUY entry at 1.39500 with SL 1.39000 (valid: SL below entry).
    target = _target(side="BUY", entry=1.39500, sl=1.39000, tp=1.41000,
                     entry_type=entry_type)
    result = volume_for_account(target=target, snapshot=_snapshot(), symbol_row=row)
    assert result.blocked_reason == ""
    # Pending sizing uses the intended order entry, NOT the live ask.
    assert result.sizing_entry == pytest.approx(1.39500)
    assert result.sizing_entry != pytest.approx(ASK)
    assert result.stop_distance == pytest.approx(0.005)


# ─── 7. RISK-BUDGET INVARIANT ─────────────────────────────────────────────────

@pytest.mark.parametrize("balance", [9704.0, 50000.0, 95071.0])
def test_normalized_volume_never_exceeds_risk_budget(balance):
    """Actual SL loss of the sized volume must not exceed the risk budget.

    Because volume normalization floors (never rounds up), the realised risk
    is always at or below the configured monetary budget.
    """
    tick_value, tick_size = 0.7, 0.00001
    row = _usdcad_row(bid=BID, ask=ASK, tick_value=tick_value)
    target = _target(side="BUY", entry=STALE_ENTRY, sl=SL, tp=1.43)
    result = volume_for_account(
        target=target, snapshot=_snapshot(balance), symbol_row=row,
        strategy_family="TREND_CONTINUATION", horizon_type="EXTENDED")
    assert result.blocked_reason == ""

    stop_in_ticks = result.stop_distance / tick_size
    actual_sl_loss = result.volume * stop_in_ticks * tick_value
    # Never exceeds budget (floor rounding); allow tiny numerical tolerance.
    assert actual_sl_loss <= result.risk_amount + 1e-6
