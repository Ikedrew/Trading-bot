"""
Durable open-position excursion state — MFE/MAE survive bot restarts.

Proves the invariant:
    OPEN → excursion → persist → RESTART → restore historical extremes →
    continue → RESTART → CLOSE → trade_truth reflects the FULL trade lifetime.

Excursion state is observational telemetry; these tests also prove it never
affects SL/TP/close/risk. Persistence is per-broker-ticket and written only when
an extreme changes.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_management.position import Position, PositionStatus
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management import excursion_state as ex
from core.trade_management.excursion_state import (
    persist_excursion, load_excursion, restore_extremes,
)
from core.trade_identity import TradeIdentity
from core.trade_journal import build_trade_record
from core.trade_truth import build_trade_truth, validate_trade_truth
from strategy.signals import Side
from risk.models import OrderIntent
from execution.mt5_execution import ExecutionResult


def _cfg():
    return TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )


@pytest.fixture
def excursion_dir(tmp_path):
    """Redirect durable excursion state to a temp dir."""
    d = tmp_path / "position_excursion"
    with patch.object(ex.config, "POSITION_EXCURSION_DIR", str(d), create=True):
        yield d


def _open_via_manager(tm, *, side, ticket, entry=1.1000, sl=1.0950, tp=1.1100,
                      bid=1.1000, ask=1.1001, canonical="C*1*P"):
    intent = OrderIntent(symbol="EURUSD", side=side, volume=0.1,
                         entry_reference=entry, sl=sl, tp=tp,
                         entry_type="MARKET", pattern="ENGULFING_BULLISH")
    execu = ExecutionResult(ok=True, retcode=10009, deal=ticket, order=ticket,
                            fill_price=entry, comment="ok")
    ident = TradeIdentity(correlation_id="COR-1", decision_id="d1",
                          canonical_opportunity_id=canonical, observation_id="obs1")
    pos = tm.register_from_execution(intent, magic=713001, execution=execu,
                                     entry_fill_price=entry, bid=bid, ask=ask,
                                     open_time_s=1717400000.0, trade_identity=ident)
    return pos


def _recover(tm, *, ticket, side_type, price_current, sl=1.0950, tp=1.1100,
             entry=1.1000):
    bp = MagicMock()
    bp.ticket = ticket; bp.symbol = "EURUSD"; bp.type = side_type
    bp.magic = 713001; bp.price_open = entry; bp.sl = sl; bp.tp = tp
    bp.volume = 0.1; bp.time = 1717400000; bp.price_current = price_current
    from core.runtime.startup_recovery import recover_positions_on_startup
    with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
         patch("core.runtime.startup_recovery.mt5") as m:
        m.ORDER_TYPE_BUY = 0
        recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)
    return tm.positions_open()[0]


# ─── restore_extremes unit rules (directional) ───────────────────────────────

def test_restore_extremes_buy_extends_not_erases():
    # saved worst = 1.0970, current less adverse 1.0990 → MAE stays 1.0970.
    mfe, mae = restore_extremes(side_name="BUY", saved_mfe=1.1040, saved_mae=1.0970, current_price=1.0990)
    assert mae == 1.0970          # historical worst preserved
    assert mfe == 1.1040          # current 1.0990 < saved MFE → unchanged
    # current MORE favourable extends MFE.
    mfe2, _ = restore_extremes(side_name="BUY", saved_mfe=1.1040, saved_mae=1.0970, current_price=1.1050)
    assert mfe2 == 1.1050
    # current MORE adverse extends MAE.
    _, mae2 = restore_extremes(side_name="BUY", saved_mfe=1.1040, saved_mae=1.0970, current_price=1.0960)
    assert mae2 == 1.0960


def test_restore_extremes_sell_extends_not_erases():
    mfe, mae = restore_extremes(side_name="SELL", saved_mfe=1.0960, saved_mae=1.1030, current_price=1.1010)
    assert mae == 1.1030          # historical worst (highest ask) preserved
    assert mfe == 1.0960          # current 1.1010 > saved MFE → unchanged
    mfe2, _ = restore_extremes(side_name="SELL", saved_mfe=1.0960, saved_mae=1.1030, current_price=1.0950)
    assert mfe2 == 1.0950         # more favourable (lower)
    _, mae2 = restore_extremes(side_name="SELL", saved_mfe=1.0960, saved_mae=1.1030, current_price=1.1040)
    assert mae2 == 1.1040         # more adverse (higher)


# ─── Test A/B — MFE & MAE survive restart ─────────────────────────────────────

def test_A_mfe_survives_restart(excursion_dir):
    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.BUY, ticket=1001)
    tm.on_price_update("EURUSD", bid=1.1080, ask=1.1081, time_s=1.0)  # favourable extreme
    assert pos.max_favourable_price == pytest.approx(1.1080)
    # Restart: current price LESS favourable than historical MFE.
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1001, side_type=0, price_current=1.1010)
    assert rec.max_favourable_price == pytest.approx(1.1080)   # historical, not 1.1010
    assert rec.excursion_provenance == "full_lifecycle"


def test_B_mae_survives_restart(excursion_dir):
    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.BUY, ticket=1002)
    tm.on_price_update("EURUSD", bid=1.0965, ask=1.0966, time_s=1.0)  # adverse extreme
    assert pos.max_adverse_price == pytest.approx(1.0965)
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1002, side_type=0, price_current=1.0995)
    assert rec.max_adverse_price == pytest.approx(1.0965)      # historical worst


# ─── Test C/D — current price extends extremes ────────────────────────────────

def test_C_current_price_extends_mfe(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=1003)
    tm.on_price_update("EURUSD", bid=1.1040, ask=1.1041, time_s=1.0)
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1003, side_type=0, price_current=1.1090)  # more favourable
    assert rec.max_favourable_price == pytest.approx(1.1090)


def test_D_current_price_extends_mae(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=1004)
    tm.on_price_update("EURUSD", bid=1.0980, ask=1.0981, time_s=1.0)
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1004, side_type=0, price_current=1.0955)  # more adverse
    assert rec.max_adverse_price == pytest.approx(1.0955)


# ─── Test E — historical extreme never erased ─────────────────────────────────

def test_E_historical_never_erased(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=1005)
    tm.on_price_update("EURUSD", bid=1.1070, ask=1.1071, time_s=1.0)  # MFE
    tm.on_price_update("EURUSD", bid=1.0960, ask=1.0961, time_s=2.0)  # MAE
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1005, side_type=0, price_current=1.1005)  # between
    assert rec.max_favourable_price == pytest.approx(1.1070)
    assert rec.max_adverse_price == pytest.approx(1.0960)


# ─── Test F — multiple restarts, full lifetime ────────────────────────────────

def test_F_multiple_restarts_full_lifetime(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=1006)
    tm.on_price_update("EURUSD", bid=1.0975, ask=1.0976, time_s=1.0)  # early adverse
    # RESTART 1
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=1006, side_type=0, price_current=1.1000)
    tm2.on_price_update("EURUSD", bid=1.1085, ask=1.1086, time_s=2.0)  # favourable
    # RESTART 2
    tm3 = TradeStateManager(_cfg())
    rec = _recover(tm3, ticket=1006, side_type=0, price_current=1.1020)
    assert rec.max_favourable_price == pytest.approx(1.1085)   # from restart-1 period
    assert rec.max_adverse_price == pytest.approx(1.0975)      # from pre-restart-1 period
    # CLOSE
    rec.status = PositionStatus.CLOSED
    trec = build_trade_record(position=rec, exit_price=1.1050,
                              exit_time=rec.open_time + 7200, close_reason="take_profit")
    # full-lifetime R (risk 0.0050): MFE 0.0085/0.0050=1.7R ; MAE 0.0025/0.0050=0.5R
    assert trec.mfe_r == pytest.approx(1.7)
    assert trec.mae_r == pytest.approx(0.5)


# ─── Test G/H — direction conventions across persistence + restart ────────────

def test_G_buy_convention(excursion_dir):
    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.BUY, ticket=1007)
    tm.on_price_update("EURUSD", bid=1.1050, ask=1.1051, time_s=1.0)   # MFE=highest bid
    tm.on_price_update("EURUSD", bid=1.0970, ask=1.0971, time_s=2.0)   # MAE=lowest bid
    saved = load_excursion(1007)
    assert saved["max_favourable_price"] == pytest.approx(1.1050)
    assert saved["max_adverse_price"] == pytest.approx(1.0970)


def test_H_sell_convention(excursion_dir):
    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.SELL, ticket=1008, sl=1.1050, tp=1.0900,
                            bid=1.0999, ask=1.1000)
    tm.on_price_update("EURUSD", bid=1.0949, ask=1.0950, time_s=1.0)   # MFE=lowest ask
    tm.on_price_update("EURUSD", bid=1.1029, ask=1.1030, time_s=2.0)   # MAE=highest ask
    saved = load_excursion(1008)
    assert saved["max_favourable_price"] == pytest.approx(1.0950)
    assert saved["max_adverse_price"] == pytest.approx(1.1030)
    assert saved["side"] == "SELL"


# ─── Test I — position identity isolation (same symbol, different tickets) ─────

def test_I_identity_isolation(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=2001, canonical="C*A")
    _open_via_manager(tm, side=Side.BUY, ticket=2002, canonical="C*B")
    # Distinct histories driven by the same symbol update (both BUY here);
    # give them different extremes by persisting directly.
    p1 = [p for p in tm.positions_open() if p.mt5_ticket == 2001][0]
    p2 = [p for p in tm.positions_open() if p.mt5_ticket == 2002][0]
    p1.max_favourable_price, p1.max_adverse_price = 1.1090, 1.0980
    p2.max_favourable_price, p2.max_adverse_price = 1.1030, 1.0940
    persist_excursion(p1); persist_excursion(p2)
    s1, s2 = load_excursion(2001), load_excursion(2002)
    assert s1["max_favourable_price"] == pytest.approx(1.1090)
    assert s1["max_adverse_price"] == pytest.approx(1.0980)
    assert s2["max_favourable_price"] == pytest.approx(1.1030)
    assert s2["max_adverse_price"] == pytest.approx(1.0940)
    # Recover position 2002 → gets ONLY its own state.
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=2002, side_type=0, price_current=1.1000)
    assert rec.max_favourable_price == pytest.approx(1.1030)
    assert rec.max_adverse_price == pytest.approx(1.0940)


# ─── Test J — legacy/no persisted excursion ───────────────────────────────────

def test_J_legacy_no_persisted_state(excursion_dir):
    # No persist for ticket 3001 → recovery falls back to current-price seed.
    tm = TradeStateManager(_cfg())
    rec = _recover(tm, ticket=3001, side_type=0, price_current=1.0990)
    assert rec.max_favourable_price == pytest.approx(1.0990)   # seeded
    assert rec.max_adverse_price == pytest.approx(1.0990)      # seeded, not fabricated
    assert rec.excursion_provenance == "recovery_seeded"


# ─── Test K — full close outcome into trade_truth ─────────────────────────────

def test_K_full_close_outcome(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=4001)
    tm.on_price_update("EURUSD", bid=1.0975, ask=1.0976, time_s=1.0)   # adverse
    tm.on_price_update("EURUSD", bid=1.1030, ask=1.1031, time_s=2.0)   # favourable
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=4001, side_type=0, price_current=1.1000)
    tm2.on_price_update("EURUSD", bid=1.1090, ask=1.1091, time_s=3.0)  # further favourable
    assert rec.max_favourable_price == pytest.approx(1.1090)
    assert rec.max_adverse_price == pytest.approx(1.0975)
    rec.status = PositionStatus.CLOSED
    trec = build_trade_record(position=rec, exit_price=1.1050,
                              exit_time=rec.open_time + 7200, close_reason="take_profit")
    truth = build_trade_truth(
        trade_id=trec.trade_id, correlation_id="COR-1",
        canonical_opportunity_id="C*1*P", symbol="EURUSD",
        entry_fill_price=trec.entry_price, exit_fill_price=trec.exit_price,
        volume_executed=trec.final_volume, entry_timestamp_broker=trec.entry_time,
        exit_timestamp_broker=trec.exit_time, pnl_realised=trec.realised_pnl,
        r_multiple_realised=1.0, commission=trec.commission, swap=trec.swap,
        net_profit=trec.net_pnl, exit_reason="take_profit_hit",
        max_favourable_price=trec.max_favourable_price, max_adverse_price=trec.max_adverse_price,
        mfe_r=trec.mfe_r, mae_r=trec.mae_r, excursion_provenance=trec.excursion_provenance,
    )
    valid, reason = validate_trade_truth(truth)
    assert valid, reason
    out = truth["outcome"]
    assert out["max_favourable_price"] == pytest.approx(1.1090)   # full lifetime
    assert out["max_adverse_price"] == pytest.approx(1.0975)
    assert out["mfe_r"] == pytest.approx(1.8)   # 0.0090/0.0050
    assert out["mae_r"] == pytest.approx(0.5)   # 0.0025/0.0050
    assert out["excursion_provenance"] == "full_lifecycle"


# ─── NEGATIVE ASSERTIONS ──────────────────────────────────────────────────────

def test_negative_stale_ticket_not_reused(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=5001)
    tm.on_price_update("EURUSD", bid=1.1050, ask=1.1051, time_s=1.0)
    # Loading a DIFFERENT ticket returns None (never cross-attach).
    assert load_excursion(9999) is None


def test_negative_persistence_does_not_alter_sl_tp_close(excursion_dir):
    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.BUY, ticket=5002)
    sl0, tp0, st0 = pos.stop_loss, pos.take_profit, pos.status
    tm.on_price_update("EURUSD", bid=1.0960, ask=1.0961, time_s=1.0)   # deep adverse (persists)
    assert pos.max_adverse_price == pytest.approx(1.0960)
    assert pos.stop_loss == sl0 and pos.take_profit == tp0 and pos.status == st0
    assert pos in tm.positions_open()


def test_negative_write_only_on_extreme_change(excursion_dir):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=5003)
    # First establishes a new adverse extreme → writes.
    with patch("core.trade_management.excursion_state.persist_excursion") as mock_persist:
        tm.on_price_update("EURUSD", bid=1.0970, ask=1.0971, time_s=1.0)  # new MAE → write
        tm.on_price_update("EURUSD", bid=1.0985, ask=1.0986, time_s=2.0)  # less adverse → NO write
        tm.on_price_update("EURUSD", bid=1.0990, ask=1.0991, time_s=3.0)  # still less adverse → NO write
    # Only the extreme-changing update triggered a persist.
    assert mock_persist.call_count == 1
