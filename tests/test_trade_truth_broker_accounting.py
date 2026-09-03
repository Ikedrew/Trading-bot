"""
Regression tests for the trade-truth broker P&L / cost accounting repair.

Root defect (fixed): the close path selected the wrong MT5 deal (queried
history by the DEAL ticket instead of the POSITION ticket, then took the first
`entry==1` deal from a broad window), persisting a foreign micro-deal's constant
P&L (-0.09 / -0.02) instead of the trade's real broker outcome. Secondary defect:
net_pnl used `gross + swap - commission` while MT5 commission is already NEGATIVE
(raw sign), double-counting the sign.

These tests pin the corrected V1 accounting contract at the calculation boundary
(build_trade_record) without touching close behaviour or MT5. Raw MT5 signs are
preserved: commission NEGATIVE = cost; net = realised_pnl + swap + commission.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_journal import build_trade_record
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


def _pos(ticket=999001, side=Side.SELL, entry=1.38391, sl=1.38507, volume=8.11):
    return Position(
        position_id=f"pos_{ticket}", symbol="USDCAD", side=side, magic=713001,
        entry_price=entry, initial_sl=sl, initial_tp=entry - 0.0020,
        stop_loss=sl, take_profit=entry - 0.0020, volume=volume,
        open_time=1788384000.0, status=PositionStatus.CLOSED, mt5_ticket=ticket,
        max_favourable_price=entry,
    )


# ─── Winning trade: broker gross positive, commission (raw negative) reduces net ─

def test_winning_trade_net_is_gross_plus_negative_commission():
    rec = build_trade_record(
        position=_pos(), exit_price=1.38377, exit_time=1788384600.0,
        close_reason="take_profit",
        realised_pnl_override=56.49,   # authoritative broker exit-deal profit
        commission=-36.50,             # aggregated entry+exit, raw MT5 sign (cost)
        swap=0.0,
    )
    assert rec.realised_pnl == pytest.approx(56.49)   # override wins, not price calc
    assert rec.commission == pytest.approx(-36.50)
    assert rec.net_pnl == pytest.approx(56.49 + 0.0 + (-36.50))   # = 19.99, still a winner
    assert rec.net_pnl > 0
    assert rec.commission_status == "measured_nonzero"


# ─── Losing trade: commission makes net more negative ─────────────────────────

def test_losing_trade_commission_deepens_loss():
    rec = build_trade_record(
        position=_pos(), exit_price=1.38450, exit_time=1788384600.0,
        close_reason="stop_loss",
        realised_pnl_override=-40.0, commission=-4.0, swap=-1.0,
    )
    assert rec.net_pnl == pytest.approx(-40.0 + (-1.0) + (-4.0))   # = -45.0


# ─── Commission sign convention is raw MT5 (negative = cost) ──────────────────

def test_commission_raw_negative_sign_reduces_net():
    rec = build_trade_record(
        position=_pos(), exit_price=1.38377, exit_time=1788384600.0,
        close_reason="take_profit",
        realised_pnl_override=100.0, commission=-5.0, swap=-2.0,
    )
    assert rec.net_pnl == pytest.approx(93.0)


# ─── Zero commission/swap: no sign/arithmetic anomaly ─────────────────────────

def test_zero_costs_net_equals_gross():
    rec = build_trade_record(
        position=_pos(), exit_price=1.38377, exit_time=1788384600.0,
        close_reason="take_profit",
        realised_pnl_override=56.49, commission=0.0, swap=0.0,
    )
    assert rec.net_pnl == pytest.approx(56.49)
    assert rec.commission_status == "measured_zero"


# ─── Broker override precedence: fallback price P&L must NOT replace broker ───

def test_broker_override_takes_precedence_over_price_calc():
    # Price calc for this SELL would be a large positive number; the broker
    # override must be persisted verbatim instead.
    rec = build_trade_record(
        position=_pos(), exit_price=1.38377, exit_time=1788384600.0,
        close_reason="take_profit",
        realised_pnl_override=56.49, commission=-36.50, swap=0.0,
    )
    assert rec.realised_pnl == pytest.approx(56.49)


# ─── Missing broker detail → costs unknown (None), net unknown, not fake 0 ────

def test_missing_broker_costs_leave_net_unknown():
    rec = build_trade_record(
        position=_pos(), exit_price=1.38377, exit_time=1788384600.0,
        close_reason="take_profit",
        realised_pnl_override=56.49,   # gross known
        commission=None, swap=None,    # costs not proven by broker
    )
    assert rec.commission is None and rec.swap is None
    assert rec.net_pnl is None                      # cannot net without costs
    assert rec.commission_status == "unknown"


# ─── Query uses POSITION ticket (order_id), not the deal ticket ───────────────

def test_query_broker_close_history_uses_order_id_not_deal_ticket():
    """Static guard: the close-history query must key on the MT5 position ticket
    (pos.order_id), never the deal-ticket field, and must filter to the
    position's own deals. Prevents regression to the foreign-deal defect."""
    import inspect
    from core.trade_management import manager as m
    src = inspect.getsource(m.TradeStateManager._query_broker_close_history)
    assert "pos.order_id" in src, "must query by the MT5 position ticket (order_id)"
    assert "position=position_id" in src
    assert "position_id" in src and "own_deals" in src, "must filter to this position's deals"
