"""Phase G: fake pinned workers only. No live MT5, S3 or messages."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json

import pytest
from core.accounts.config import AccountConfig
from core.accounts.lifecycle import LifecycleRouter, legacy_owner
from core.accounts.lifecycle_worker import execute_lifecycle
from core.accounts.worker import AccountReadError
from core.accounts import position_state
from core.position_ownership import PositionOwnership
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.position import PositionStatus
from core.runtime.startup_recovery import recover_positions_on_startup, _restore_identity_from_logs
from execution.mt5_execution import MT5Execution, ExecutionResult
from risk.models import OrderIntent
from strategy.signals import Side
from tests._account_fake_mt5 import FakeMT5

MAGIC = 713001

class Broker(FakeMT5):
    TRADE_ACTION_SLTP=6
    TRADE_ACTION_DEAL=1
    ORDER_TIME_GTC=0
    ORDER_FILLING_FOK=0
    ORDER_FILLING_IOC=1
    ORDER_FILLING_RETURN=2
    TRADE_RETCODE_DONE=10009
    def __init__(self, account):
        super().__init__(account, suffix='.x' if account.account_id != 'METAQUOTES' else '')
        symbol = 'EURUSD.x' if account.account_id != 'METAQUOTES' else 'EURUSD'
        self.position = NS(ticket=12345, symbol=symbol, magic=MAGIC, type=0,
            volume=.1, price_open=1.1, price_current=1.101, sl=1.09, tp=1.12, time=1700000000)
        self.positions=[self.position]
        self.history=[NS(ticket=44, position_id=12345, entry=1, profit=13., commission=-1.,
                         swap=-.1, price=1.12, time=1700000600, reason=5, volume=.1)]
        self.sent=[]
        self.fail=False
    def positions_get(self, **kwargs):
        self.calls.append(('positions_get', kwargs))
        if self.fail: raise RuntimeError('account unavailable')
        return [p for p in self.positions if
            ('ticket' not in kwargs or p.ticket == kwargs['ticket']) and
            ('symbol' not in kwargs or p.symbol == kwargs['symbol'])]
    def history_deals_get(self, **kwargs):
        self.calls.append(('history_deals_get', kwargs))
        return self.history
    def orders_get(self, **kwargs):
        self.calls.append(('orders_get', kwargs))
        return [NS(ticket=12345, symbol=self.position.symbol)]
    def order_send(self, request):
        self.sent.append(request.copy())
        if request['action'] == self.TRADE_ACTION_SLTP:
            self.position.sl=request['sl']; self.position.tp=request['tp']
        else: self.positions=[]
        return NS(retcode=10009, deal=55, order=66, comment='done', price=1.12)

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(position_state, 'STATE_DIR', tmp_path/'state')
    from core import config
    monkeypatch.setattr(config, 'DRY_RUN', False)
    monkeypatch.setattr(config, 'POSITION_CLOSE_ENABLED', True)
    monkeypatch.setattr(config, 'POSITION_EXCURSION_S3_MIRROR', False, raising=False)
    monkeypatch.setattr('core.kill_switch.is_kill_switch_active', lambda: False)
    monkeypatch.setattr('core.protection_verification._persist_result', lambda *a: None)
    monkeypatch.setattr('core.protection_verification._emit_discord_alert', lambda *a: None)
    monkeypatch.setattr('core.protection_verification.time.sleep', lambda *a: None)
    monkeypatch.setattr('core.trade_management.manager._persist_management_action', lambda **k: None)
    accounts=tuple(AccountConfig(account_id=a, broker=a.title(), server=a+'-demo',
        login=i+1, terminal_path=str(tmp_path/a/'terminal64.exe'), enabled=True,
        role='baseline' if a=='METAQUOTES' else 'observe_only',
        symbol_map=(('EURUSD', 'EURUSD' if a=='METAQUOTES' else 'EURUSD.x'),))
        for i,a in enumerate(('METAQUOTES','VANTAGE','ADMIRALS')))
    brokers={a.account_id: Broker(a) for a in accounts}
    calls=[]
    def transport(account, request):
        calls.append((account.account_id, request['operation']))
        value=execute_lifecycle(account, request, brokers[account.account_id])
        return dict(account_id=account.account_id, broker=account.broker, server=account.server,
                    login=account.login, identity_verified=True, value=value)
    router=LifecycleRouter(accounts, transport=transport)
    execution=MT5Execution(magic=MAGIC)
    manager=TradeStateManager(TradeManagementConfig(), execution=execution, lifecycle_router=router)
    return NS(accounts=accounts, brokers=brokers, calls=calls, router=router,
              execution=execution, manager=manager, tmp=tmp_path)

def owner(env, account='METAQUOTES', ticket=12345):
    a=env.router.account(account)
    return PositionOwnership(a.account_id, a.broker, a.server, ticket,
        order_ticket=12345, deal_ticket=777, canonical_symbol='EURUSD',
        broker_symbol=env.brokers[account].position.symbol,
        canonical_opportunity_id='opp-'+account, correlation_id='cor-'+account,
        decision_id='dec-'+account, account_execution_id='exec-'+account, trade_id='trade-'+account)

def register(env, account='METAQUOTES'):
    intent=OrderIntent('EURUSD', Side.BUY, .1, 1.1, 1.09, 1.12, metadata={'horizon':'SCALP'})
    return env.manager.register_from_execution(intent, magic=MAGIC,
        execution=ExecutionResult(True,10009,777,12345,'ok',1.1,owner(env,account)),entry_fill_price=1.1,bid=1.1,ask=1.10002)

def test_same_ticket_registry_and_lookup(env):
    a=register(env); b=register(env,'VANTAGE')
    assert len(env.manager.positions_open())==2
    assert a.position_id=='["METAQUOTES",12345]'
    assert b.position_id=='["VANTAGE",12345]'
    assert env.manager.get_position(account_id='METAQUOTES',position_ticket=12345) is a
    assert env.manager.get_position(account_id='VANTAGE',position_ticket=12345) is b
    with pytest.raises(TypeError): env.manager.get_position(position_ticket=12345)
    assert a.mt5_ticket==12345 and a.deal_id==777

def test_wrong_account_lookup_rejected(env):
    with pytest.raises(AccountReadError):
        env.router.call('VANTAGE','positions_get',owner=owner(env),magic=MAGIC)
    assert not env.calls

@pytest.mark.parametrize('operation', ['modify','close'])
def test_management_routes_and_rejects_wrong_account(env, operation):
    o=owner(env,'VANTAGE')
    args=dict(symbol='EURUSD',position_ticket=12345,ownership=o)
    method=env.execution.position_modify_sl_tp if operation=='modify' else env.execution.close_position
    if operation=='modify': args.update(sl=1.095,tp=1.12)
    assert not method(**args,account_id='METAQUOTES').ok
    assert method(**args).ok
    assert env.calls==[('VANTAGE',operation)]
    assert len(env.brokers['VANTAGE'].sent)==1
    assert not env.brokers['METAQUOTES'].sent
    assert env.brokers['VANTAGE'].sent[0]['symbol']=='EURUSD.x'

def test_manager_stops_and_retry_keys(env):
    a=register(env);b=register(env,'VANTAGE')
    env.brokers['METAQUOTES'].fail=True
    env.brokers['VANTAGE'].fail=True
    env.manager._push_stops_to_server_if_possible(a)
    env.manager._push_stops_to_server_if_possible(b)
    assert set(env.manager._sltp_retry_queue)=={a.position_id,b.position_id}
    env.brokers['VANTAGE'].fail=False
    env.manager.drain_sltp_retry_queue()
    assert set(env.manager._sltp_retry_queue)=={a.position_id}
    assert env.brokers['VANTAGE'].sent

def test_protection_verification_and_correction(env):
    from core.protection_verification import verify_protection
    env.brokers['VANTAGE'].position.sl=0
    result=verify_protection(symbol='EURUSD',position_ticket=12345,requested_sl=1.09,
        requested_tp=1.12,ownership=owner(env,'VANTAGE'),lifecycle_router=env.router,
        execution_module=env.execution,magic=MAGIC)
    assert result.correction_success
    assert env.calls==[('VANTAGE','positions_get'),('VANTAGE','modify'),('VANTAGE','positions_get')]
    assert not env.brokers['METAQUOTES'].sent

def test_history_and_orders_are_owned(env):
    p=register(env,'VANTAGE')
    detail=env.manager._query_broker_close_history(p)
    assert detail is not None
    assert env.calls==[('VANTAGE','history_deals_get')]
    assert env.router.read(p.ownership,'orders_get')[0].ticket==12345

def test_close_manager_routes(env):
    from core.trade_management.events import TradeLifecycleEvent
    p=register(env,'VANTAGE')
    env.manager._close_local(p,TradeLifecycleEvent.ON_TAKE_PROFIT_HIT,(1.12,1.1201),1700000600,{})
    assert p.status==PositionStatus.CLOSED
    assert env.calls==[('VANTAGE','close'),('VANTAGE','history_deals_get')]

def test_recovery_failure_isolation_and_identity(env):
    env.brokers['METAQUOTES'].fail=True
    n=recover_positions_on_startup(trade_manager=env.manager,symbol='EURUSD',magic=MAGIC)
    assert n==2
    assert {p.account_id for p in env.manager.positions_open()}=={'VANTAGE','ADMIRALS'}
    assert all(p.broker_symbol=='EURUSD.x' for p in env.manager.positions_open())
    assert set(env.calls)=={(a.account_id,'recover') for a in env.accounts}

def test_separate_lineage_survives_restart(env):
    for a in ('METAQUOTES','VANTAGE'):
        register(env,a)
    env.manager._by_id.clear()
    env.brokers['ADMIRALS'].positions=[]
    assert recover_positions_on_startup(trade_manager=env.manager,symbol='EURUSD',magic=MAGIC)==2
    for p in env.manager.positions_open():
        assert p.ownership.canonical_opportunity_id=='opp-'+p.account_id
        assert p.trade_identity.canonical_opportunity_id=='opp-'+p.account_id
        assert p.ownership.account_execution_id=='exec-'+p.account_id
        assert p.ownership.trade_id=='trade-'+p.account_id

def test_log_lookup_filters_account(env):
    directory=env.tmp/'logs/execution_results/EURUSD'; directory.mkdir(parents=True)
    rows=[dict(account_id=a.account_id,broker=a.broker,broker_server=a.server,
        position_ticket=12345,result_ok=True,canonical_opportunity_id='opp-'+a.account_id)
        for a in env.accounts]
    (directory/'2026-09-10.jsonl').write_text('\n'.join(map(json.dumps,rows)))
    for a in env.accounts:
        identity=_restore_identity_from_logs(symbol='EURUSD',ticket=12345,entry_price=1.1,
            account_id=a.account_id,broker=a.broker,broker_server=a.server)
        assert identity['canonical_opportunity_id']=='opp-'+a.account_id
    assert _restore_identity_from_logs(symbol='EURUSD',ticket=12345,entry_price=1.1)=={}

@pytest.mark.parametrize('account', ['', 'UNKNOWN'])
def test_missing_unknown_fail_closed(env,account):
    with pytest.raises(AccountReadError): env.router.read(owner(env)._replace(account_id=account),'positions_get',magic=MAGIC)
    assert not env.calls
    env.execution.lifecycle_router=env.router
    assert not env.execution.close_position('EURUSD',12345).ok

def test_registration_missing_ownership_fails(env):
    with pytest.raises(ValueError,match='MISSING_POSITION_OWNERSHIP'):
        env.manager.register_from_execution(OrderIntent('EURUSD',Side.BUY,.1,1.1,1.09,1.12),
            magic=MAGIC,execution=ExecutionResult(True,10009,777,12345,'ok'),entry_fill_price=1.1,bid=1.1,ask=1.10002)

def test_closed_position_not_restored(env):
    register(env)
    for broker in env.brokers.values(): broker.positions=[]
    env.manager._by_id.clear()
    assert recover_positions_on_startup(trade_manager=env.manager,symbol='EURUSD',magic=MAGIC)==0

def test_verified_metaquotes_only_compatibility(env):
    a=env.accounts[0]
    o=legacy_owner(ticket=12345,symbol='EURUSD',broker_symbol='EURUSD',
        mt5=env.brokers['METAQUOTES'],accounts=[a])
    assert o.account_id=='METAQUOTES'
    with pytest.raises(AccountReadError):
        legacy_owner(ticket=12345,symbol='EURUSD',broker_symbol='EURUSD',
            mt5=env.brokers['METAQUOTES'],accounts=env.accounts)
    env.brokers['METAQUOTES'].account.login=9999
    with pytest.raises(AccountReadError):
        legacy_owner(ticket=12345,symbol='EURUSD',broker_symbol='EURUSD',
            mt5=env.brokers['METAQUOTES'],accounts=[a])

def test_magic_and_unknown_read_fail_closed(env):
    env.brokers['METAQUOTES'].position.magic=42
    assert not env.execution.close_position('EURUSD',12345,ownership=owner(env)).ok
    assert not env.brokers['METAQUOTES'].sent

def test_worker_wrong_session_rejected(env):
    env.brokers['VANTAGE'].account.login=9999
    with pytest.raises(AccountReadError): env.router.read(owner(env,'VANTAGE'),'positions_get',magic=MAGIC)

def test_strategy_risk_horizon_inputs_unchanged(env):
    intent=OrderIntent('EURUSD',Side.BUY,.1,1.1,1.09,1.12,metadata={'horizon':'SWING'})
    p=env.manager.register_from_execution(intent,magic=MAGIC,
        execution=ExecutionResult(True,10009,777,12345,'ok',1.1,owner(env)),entry_fill_price=1.1,bid=1.1,ask=1.10002)
    assert (p.initial_sl,p.initial_tp,p.volume,p.trade_horizon)==(intent.sl,intent.tp,intent.volume,'SWING')
    assert intent.metadata=={'horizon':'SWING'}
