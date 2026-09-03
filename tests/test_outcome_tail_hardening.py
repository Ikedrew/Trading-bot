"""
Outcome-tail hardening regression tests.

PART 1: research-grade null semantics for realised outcome fields (commission /
swap / net P&L) — unknown → None (JSON null), measured zero → 0.0, measured
nonzero → exact value, with provenance status fields.

PART 2: live-trade MAE (max adverse excursion) capture symmetric with the
existing MFE tracker, mae_r in R with null semantics, restart continuity under
the existing (memory-only, re-seeded) excursion contract, and proof that
MAE/MFE are passive telemetry that never affect trading.

All tests are observational/persistence tests — none exercise or alter trading
decisions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_management.position import Position, PositionStatus
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.sl_tp_rules import update_mfe_extreme, update_mae_extreme
from core.trade_journal import build_trade_record, _cost_status, _excursion_r
from core.trade_truth import build_trade_truth, validate_trade_truth
from strategy.signals import Side
from risk.models import OrderIntent


# ─── helpers ──────────────────────────────────────────────────────────────────

def _closed_position(
    *, side=Side.BUY, entry=1.1000, sl=1.0950, tp=1.1100, volume=0.10,
    mfe=None, mae=None, ticket=12345,
) -> Position:
    return Position(
        position_id=f"pos_{ticket}", symbol="EURUSD", side=side, magic=713001,
        entry_price=entry, initial_sl=sl, initial_tp=tp, stop_loss=sl, take_profit=tp,
        volume=volume, open_time=1717400000.0, status=PositionStatus.CLOSED,
        mt5_ticket=ticket,
        max_favourable_price=(entry if mfe is None else mfe),
        max_adverse_price=mae,
    )


def _cfg():
    return TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — NULL SEMANTICS
# ══════════════════════════════════════════════════════════════════════════════

def test_A_unknown_costs_are_null():
    """Commission/swap unknown → None (JSON null), NOT 0.0; net_pnl → None."""
    pos = _closed_position()
    rec = build_trade_record(
        position=pos, exit_price=1.1050, exit_time=pos.open_time + 3600,
        close_reason="take_profit",
        commission=None, swap=None, realised_pnl_override=50.0,
    )
    assert rec.commission is None
    assert rec.swap is None
    assert rec.net_pnl is None            # cannot compute net without costs
    assert rec.commission_status == "unknown"
    assert rec.swap_status == "unknown"
    # realised P&L is a proven value (broker override) — not unknown.
    assert rec.realised_pnl == 50.0


def test_B_measured_zero_stays_zero():
    """Broker explicitly reports 0.0 → preserved as 0.0 with measured_zero status."""
    pos = _closed_position()
    rec = build_trade_record(
        position=pos, exit_price=1.1050, exit_time=pos.open_time + 3600,
        close_reason="take_profit",
        commission=0.0, swap=0.0, realised_pnl_override=50.0,
    )
    assert rec.commission == 0.0
    assert rec.swap == 0.0
    assert rec.commission_status == "measured_zero"
    assert rec.swap_status == "measured_zero"
    assert rec.net_pnl == 50.0            # 50 + 0 - 0


def test_C_measured_nonzero_preserved():
    """Non-zero broker costs survive exactly with measured_nonzero status."""
    pos = _closed_position()
    rec = build_trade_record(
        position=pos, exit_price=1.1050, exit_time=pos.open_time + 3600,
        close_reason="take_profit",
        commission=-1.25, swap=0.40, realised_pnl_override=50.0,
    )
    assert rec.commission == -1.25
    assert rec.swap == 0.40
    assert rec.commission_status == "measured_nonzero"
    assert rec.swap_status == "measured_nonzero"
    # Raw MT5 signs: net = gross + swap + commission (commission -1.25 is a cost).
    assert rec.net_pnl == round(50.0 + 0.40 + (-1.25), 4)  # = 49.15


def test_cost_status_helper():
    assert _cost_status(None) == "unknown"
    assert _cost_status(0.0) == "measured_zero"
    assert _cost_status(-1.25) == "measured_nonzero"


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — MAE tracking (unit-level symmetry with MFE)
# ══════════════════════════════════════════════════════════════════════════════

def test_update_mae_symmetric_with_mfe():
    # BUY: MFE tracks highest bid, MAE tracks lowest bid (same side price).
    assert update_mfe_extreme(Side.BUY, bid=1.1010, ask=1.1012, current=1.1000) == 1.1010
    assert update_mae_extreme(Side.BUY, bid=1.0990, ask=1.0992, current=1.1000) == 1.0990
    # less-adverse observation does not overwrite the worst
    assert update_mae_extreme(Side.BUY, bid=1.0995, ask=1.0997, current=1.0990) == 1.0990
    # SELL: MFE tracks lowest ask, MAE tracks highest ask.
    assert update_mfe_extreme(Side.SELL, bid=1.0988, ask=1.0990, current=1.1000) == 1.0990
    assert update_mae_extreme(Side.SELL, bid=1.1008, ask=1.1010, current=1.1000) == 1.1010
    # None (unknown) seeds from first observation, never fabricated.
    assert update_mae_extreme(Side.BUY, bid=1.0990, ask=1.0992, current=None) == 1.0990


# ─── Test D — BUY MAE via the real manager price-update path ──────────────────

def test_D_buy_mae_tracked_via_manager():
    tm = TradeStateManager(_cfg())
    intent = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1,
                         entry_reference=1.1000, sl=1.0950, tp=1.1100,
                         entry_type="MARKET", pattern="ENGULFING_BULLISH")
    from execution.mt5_execution import ExecutionResult
    execu = ExecutionResult(ok=True, retcode=10009, deal=555, order=555,
                            fill_price=1.1000, comment="ok")
    pos = tm.register_from_execution(intent, magic=713001, execution=execu,
                                     entry_fill_price=1.1000, bid=1.1000, ask=1.1001,
                                     open_time_s=1717400000.0)
    # Feed favourable then adverse then less-adverse observations.
    tm.on_price_update("EURUSD", bid=1.1020, ask=1.1021, time_s=1.0)   # favourable
    tm.on_price_update("EURUSD", bid=1.0975, ask=1.0976, time_s=2.0)   # adverse (worst)
    tm.on_price_update("EURUSD", bid=1.0990, ask=1.0991, time_s=3.0)   # less adverse
    assert pos.max_favourable_price == pytest.approx(1.1020)  # highest bid
    assert pos.max_adverse_price == pytest.approx(1.0975)     # lowest bid (worst)
    # mae_r via initial risk geometry (risk = 0.0050; adverse = 0.0025 → 0.5R)
    mae_r = _excursion_r("BUY", 1.1000, pos.max_adverse_price, 1.0950, favourable=False)
    assert mae_r == pytest.approx(0.5)


# ─── Test E — SELL MAE ────────────────────────────────────────────────────────

def test_E_sell_mae_tracked_via_manager():
    tm = TradeStateManager(_cfg())
    intent = OrderIntent(symbol="EURUSD", side=Side.SELL, volume=0.1,
                         entry_reference=1.1000, sl=1.1050, tp=1.0900,
                         entry_type="MARKET", pattern="SHOOTING_STAR")
    from execution.mt5_execution import ExecutionResult
    execu = ExecutionResult(ok=True, retcode=10009, deal=556, order=556,
                            fill_price=1.1000, comment="ok")
    pos = tm.register_from_execution(intent, magic=713001, execution=execu,
                                     entry_fill_price=1.1000, bid=1.0999, ask=1.1000,
                                     open_time_s=1717400000.0)
    tm.on_price_update("EURUSD", bid=1.0979, ask=1.0980, time_s=1.0)   # favourable
    tm.on_price_update("EURUSD", bid=1.1024, ask=1.1025, time_s=2.0)   # adverse (worst)
    tm.on_price_update("EURUSD", bid=1.1009, ask=1.1010, time_s=3.0)   # less adverse
    assert pos.max_favourable_price == pytest.approx(1.0980)  # lowest ask
    assert pos.max_adverse_price == pytest.approx(1.1025)     # highest ask (worst)
    mae_r = _excursion_r("SELL", 1.1000, pos.max_adverse_price, 1.1050, favourable=False)
    assert mae_r == pytest.approx(0.5)    # adverse 0.0025 / risk 0.0050


# ─── Test F — measured-zero MAE ───────────────────────────────────────────────

def test_F_zero_mae_is_measured_not_unknown():
    """A trade observed at/above entry (BUY) never moves adversely → mae_r 0.0."""
    # max_adverse_price == entry (observed, never went below).
    pos = _closed_position(mae=1.1000)
    mae_r = _excursion_r("BUY", 1.1000, pos.max_adverse_price, 1.0950, favourable=False)
    assert mae_r == 0.0                    # measured zero
    assert mae_r is not None               # explicitly NOT unknown


# ─── Test G — unknown MAE stays None ──────────────────────────────────────────

def test_G_unknown_mae_is_none():
    # No adverse observation at all → unknown.
    assert _excursion_r("BUY", 1.1000, None, 1.0950, favourable=False) is None
    # Invalid initial risk geometry (entry == sl) → unknown, never fake 0.0.
    assert _excursion_r("BUY", 1.1000, 1.0975, 1.1000, favourable=False) is None


# ─── Test H — MFE and MAE coexist ─────────────────────────────────────────────

def test_H_mfe_and_mae_coexist():
    tm = TradeStateManager(_cfg())
    intent = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1,
                         entry_reference=1.1000, sl=1.0950, tp=1.1100,
                         entry_type="MARKET", pattern="ENGULFING_BULLISH")
    from execution.mt5_execution import ExecutionResult
    execu = ExecutionResult(ok=True, retcode=10009, deal=557, order=557,
                            fill_price=1.1000, comment="ok")
    pos = tm.register_from_execution(intent, magic=713001, execution=execu,
                                     entry_fill_price=1.1000, bid=1.1000, ask=1.1001,
                                     open_time_s=1717400000.0)
    tm.on_price_update("EURUSD", bid=1.1030, ask=1.1031, time_s=1.0)   # favourable extreme
    tm.on_price_update("EURUSD", bid=1.0970, ask=1.0971, time_s=2.0)   # adverse extreme
    # Both extremes are retained independently.
    assert pos.max_favourable_price == pytest.approx(1.1030)
    assert pos.max_adverse_price == pytest.approx(1.0970)


# ─── Test I — close persistence (authoritative trade_truth) ───────────────────

def test_I_close_persists_full_outcome_semantics():
    pos = _closed_position(mfe=1.1040, mae=1.0975)
    rec = build_trade_record(
        position=pos, exit_price=1.1050, exit_time=pos.open_time + 3600,
        close_reason="take_profit",
        commission=None, swap=0.0, realised_pnl_override=50.0,
    )
    # Record carries excursion + null-aware costs.
    assert rec.max_favourable_price == pytest.approx(1.1040)
    assert rec.max_adverse_price == pytest.approx(1.0975)
    assert rec.mfe_r == pytest.approx((1.1040 - 1.1000) / 0.0050)  # 0.8R
    assert rec.mae_r == pytest.approx((1.1000 - 1.0975) / 0.0050)  # 0.5R

    truth = build_trade_truth(
        trade_id=rec.trade_id, correlation_id="COR-1",
        canonical_opportunity_id="EURUSD*1*ENGULFING_BULLISH",
        symbol=rec.symbol, entry_fill_price=rec.entry_price, exit_fill_price=rec.exit_price,
        volume_executed=rec.final_volume, entry_timestamp_broker=rec.entry_time,
        exit_timestamp_broker=rec.exit_time, pnl_realised=rec.realised_pnl,
        r_multiple_realised=1.0, commission=rec.commission, swap=rec.swap,
        net_profit=rec.net_pnl, exit_reason="take_profit_hit",
        max_favourable_price=rec.max_favourable_price, max_adverse_price=rec.max_adverse_price,
        mfe_r=rec.mfe_r, mae_r=rec.mae_r,
    )
    valid, reason = validate_trade_truth(truth)
    assert valid, reason
    out = truth["outcome"]
    assert out["commission"] is None       # unknown → null
    assert out["swap"] == 0.0              # measured zero preserved
    assert out["net_profit"] is None       # unknown (commission unknown)
    assert out["max_favourable_price"] == pytest.approx(1.1040)
    assert out["max_adverse_price"] == pytest.approx(1.0975)
    assert out["mfe_r"] == pytest.approx(0.8)
    assert out["mae_r"] == pytest.approx(0.5)


# ─── Test J — restart continuity (memory-only re-seed contract) ───────────────

def test_J_restart_reseeds_excursion_per_existing_contract(tmp_path):
    """MAE follows the SAME restart contract as MFE: both are re-seeded from the
    broker current price on recovery (open-position excursion is memory-only in
    this architecture). Assert MAE is seeded (not left unknown) and continues to
    track adverse moves after restart — never fabricated to a misleading value."""
    from core.runtime.startup_recovery import recover_positions_on_startup
    from unittest.mock import MagicMock

    bp = MagicMock()
    bp.ticket = 900; bp.symbol = "EURUSD"; bp.type = 0  # BUY
    bp.magic = 713001; bp.price_open = 1.1000; bp.sl = 1.0950; bp.tp = 1.1100
    bp.volume = 0.1; bp.time = 1717400000; bp.price_current = 1.0990

    tm = TradeStateManager(_cfg())
    import os
    old = os.getcwd(); os.chdir(tmp_path)
    try:
        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as m:
            m.ORDER_TYPE_BUY = 0
            recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)
    finally:
        os.chdir(old)

    pos = tm.positions_open()[0]
    # Both MFE and MAE re-seeded from broker current price (same contract).
    assert pos.max_favourable_price == pytest.approx(1.0990)
    assert pos.max_adverse_price == pytest.approx(1.0990)   # seeded, not unknown/fabricated
    # A further adverse move after restart is captured.
    tm.on_price_update("EURUSD", bid=1.0965, ask=1.0966, time_s=10.0)
    assert pos.max_adverse_price == pytest.approx(1.0965)


# ══════════════════════════════════════════════════════════════════════════════
# NEGATIVE ASSERTIONS — telemetry is passive
# ══════════════════════════════════════════════════════════════════════════════

def test_mae_tracking_does_not_alter_stop_or_close_or_risk():
    """MAE tracking must not move SL/TP, close the trade, or alter risk state."""
    tm = TradeStateManager(_cfg())
    intent = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1,
                         entry_reference=1.1000, sl=1.0950, tp=1.1100,
                         entry_type="MARKET", pattern="ENGULFING_BULLISH")
    from execution.mt5_execution import ExecutionResult
    execu = ExecutionResult(ok=True, retcode=10009, deal=558, order=558,
                            fill_price=1.1000, comment="ok")
    pos = tm.register_from_execution(intent, magic=713001, execution=execu,
                                     entry_fill_price=1.1000, bid=1.1000, ask=1.1001,
                                     open_time_s=1717400000.0)
    sl_before, tp_before, status_before = pos.stop_loss, pos.take_profit, pos.status
    # Drive a deep adverse excursion (but not past the SL) — pure observation.
    tm.on_price_update("EURUSD", bid=1.0960, ask=1.0961, time_s=1.0)
    assert pos.max_adverse_price == pytest.approx(1.0960)
    # Stop/target/status unchanged by MAE observation.
    assert pos.stop_loss == sl_before
    assert pos.take_profit == tp_before
    assert pos.status == status_before
    assert pos in tm.positions_open()      # not closed by telemetry


def test_unknown_values_never_silently_zeroed():
    """Explicit negative: unknown commission/swap/net/MAE are None, not 0.0."""
    pos = _closed_position(mae=None)
    rec = build_trade_record(
        position=pos, exit_price=1.1050, exit_time=pos.open_time + 3600,
        close_reason="take_profit",
        commission=None, swap=None, realised_pnl_override=50.0,
    )
    assert rec.commission is None and rec.commission != 0.0
    assert rec.swap is None and rec.swap != 0.0
    assert rec.net_pnl is None
    assert rec.mae_r is None                # no adverse observation → unknown
    assert rec.max_adverse_price is None
