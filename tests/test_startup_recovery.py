"""
Modernized D3 startup-recovery tests.

The pre-IPC version of this file patched ``core.runtime.startup_recovery.mt5_call``
and ``core.runtime.startup_recovery.mt5`` — symbols that no longer exist. Recovery
now runs through `LifecycleRouter` IPC with identity-verified worker transports,
so every scenario below uses a fake transport and a real `TradeStateManager`
(tests only, no MT5/S3/live trade).

Covers:
- Positions discovered and registered
- Empty broker state → no-op
- Wrong-magic positions filtered
- None trade_manager → no-op
- Duplicate protection (no double-registration)
- Management continuity (recovered positions respond to price updates)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.accounts import position_state
from core.accounts.config import AccountConfig
from core.accounts.lifecycle import LifecycleRouter
from core.accounts.position_state import position_key
from core.position_ownership import PositionOwnership
from core.runtime.startup_recovery import recover_positions_on_startup
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side

MAGIC = 713001


@pytest.fixture
def account(tmp_path):
    return AccountConfig(
        account_id='METAQUOTES', broker='MetaQuotes', server='MetaQuotes-Demo',
        login=111, terminal_path=str(tmp_path / 'METAQUOTES' / 'terminal64.exe'),
        enabled=True, role='baseline',
    )


@pytest.fixture
def cfg():
    return TradeManagementConfig()


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(position_state, 'STATE_DIR', tmp_path / 'state')


def _row(ticket=100, symbol='EURUSD', broker_symbol='EURUSD', magic=MAGIC,
         type_=0, volume=0.10, price_open=1.10000, price_current=1.10050,
         sl=1.09500, tp=1.11000, time=1717400000):
    return dict(ticket=ticket, symbol=symbol, broker_symbol=broker_symbol,
                magic=magic, type=type_, volume=volume,
                price_open=price_open, price_current=price_current,
                sl=sl, tp=tp, time=time)


def _run_recovery(tm, rows, account, symbol='EURUSD'):
    """Recover via a fake IPC transport; returns the adopted count."""
    def transport(acct, request):
        return dict(account_id=acct.account_id, broker=acct.broker,
                    server=acct.server, login=acct.login,
                    identity_verified=True,
                    value={'results': {symbol: list(rows) or []}, 'errors': {}})

    router = LifecycleRouter((account,), transport=transport)
    return recover_positions_on_startup(trade_manager=tm, symbol=symbol,
                                        magic=MAGIC, lifecycle_router=router)
class TestPositionDiscovery:
    def test_recovers_broker_positions(self, cfg, account, state_dir):
        """Open broker positions are registered into TradeStateManager."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        count = _run_recovery(tm, [_row(ticket=100)], account)

        assert count == 1
        assert len(tm.positions_open()) == 1
        pos = tm.positions_open()[0]
        assert pos.mt5_ticket == 100
        assert pos.symbol == 'EURUSD'
        assert pos.side == Side.BUY
        assert pos.entry_price == 1.10000
        assert pos.volume == 0.10

    def test_multiple_positions_recovered(self, cfg, account, state_dir):
        """Multiple positions are all recovered."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        count = _run_recovery(tm, [_row(ticket=101), _row(ticket=102),
                                   _row(ticket=103)], account)
        assert count == 3
        assert len(tm.positions_open()) == 3

    def test_sell_position_detected(self, cfg, account, state_dir):
        """SELL positions have correct side."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        _run_recovery(tm, [_row(ticket=200, type_=1)], account)
        assert tm.positions_open()[0].side == Side.SELL


class TestEmptyBrokerState:
    def test_no_positions_returns_zero(self, cfg, account, state_dir):
        """No broker positions -> graceful no-op."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        count = _run_recovery(tm, [], account)
        assert count == 0
        assert len(tm.positions_open()) == 0

    def test_none_response_returns_zero(self, cfg, account, state_dir):
        """Missing broker snapshot -> graceful no-op (fail closed, not crash)."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))

        def transport(acct, request):
            return dict(account_id=acct.account_id, broker=acct.broker,
                        server=acct.server, login=acct.login,
                        identity_verified=True,
                        value={'results': {},
                               'errors': {'EURUSD': 'POSITIONS_UNAVAILABLE'}})

        router = LifecycleRouter((account,), transport=transport)
        count = recover_positions_on_startup(trade_manager=tm, symbol='EURUSD',
                                             magic=MAGIC, lifecycle_router=router)
        assert count == 0

    def test_wrong_magic_filtered(self, cfg, account, state_dir):
        """Positions with a different magic are ignored."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        count = _run_recovery(tm, [_row(ticket=300, magic=999999)], account)
        assert count == 0

    def test_no_trade_manager_noop(self, account, state_dir):
        """None trade_manager -> no-op, no crash."""
        count = recover_positions_on_startup(trade_manager=None, symbol='EURUSD',
                                             magic=MAGIC, lifecycle_router=None)
        assert count == 0
class TestDuplicateProtection:
    def test_no_double_registration(self, cfg, account, state_dir):
        """Same position recovered twice -> only registered once."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        row = _row(ticket=400)
        _run_recovery(tm, [row], account)
        # Second recovery (simulates double-startup) must not duplicate.
        _run_recovery(tm, [row], account)
        assert len(tm.positions_open()) == 1

    def test_already_tracked_skipped(self, cfg, account, state_dir):
        """Position already in TradeStateManager is not duplicated."""
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        owner = PositionOwnership('METAQUOTES', 'MetaQuotes', 'MetaQuotes-Demo',
                                  500, canonical_symbol='EURUSD',
                                  broker_symbol='EURUSD')
        existing = Position(position_id=position_key(owner), symbol='EURUSD',
                            side=Side.BUY, magic=MAGIC, entry_price=1.1,
                            initial_sl=1.09, initial_tp=1.11, stop_loss=1.09,
                            take_profit=1.11, volume=0.1, open_time=1000.0,
                            status=PositionStatus.OPEN, mt5_ticket=500,
                            ownership=owner)
        tm._by_id[position_key(owner)] = existing
        count = _run_recovery(tm, [_row(ticket=500)], account)
        assert count == 0
        assert len(tm.positions_open()) == 1


class TestManagementContinuity:
    def test_recovered_position_responds_to_price_update(self, cfg, account, state_dir):
        """Recovered position is managed by on_price_update (BE/trailing)."""
        cfg = TradeManagementConfig(break_even_trigger_rr=1.0,
                                    break_even_buffer_rr=0.00005)
        tm = TradeStateManager(cfg, lifecycle_router=LifecycleRouter((account,)))
        row = _row(ticket=600, price_open=1.10000, sl=1.09500, tp=1.11000,
                   price_current=1.10600)
        _run_recovery(tm, [row], account)
        pos = tm.positions_open()[0]
        # Entry=1.10000, SL=1.09500, risk=0.005 -> BE trigger at 1R = 1.10500.
        # Price 1.10600 exceeds it, so BE should move SL to at least entry.
        tm.on_price_update('EURUSD', 1.10600, 1.10620, 1717401000.0)
        assert pos.stop_loss >= pos.entry_price