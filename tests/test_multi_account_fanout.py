"""Focused D-F tests: fan-out, eligibility, risk, routing (mocks only)."""

from types import SimpleNamespace as NS

import pytest

from core.accounts.account_router import build_account_order_request
from core.accounts.account_router import execution_enabled_for
from core.accounts.account_router import route_executions
from core.accounts.account_router import target_state
from core.accounts.config import ACCOUNT_IDS, load_accounts
from core.accounts.fanout import CanonicalDecision, fan_out_canonical_decision
from core.accounts.fanout_orchestrator import prepare_account_routes
from core.accounts.live_fanout import build_canonical_decision
from core.accounts import worker as worker_mod
from tests._account_fake_mt5 import FakeMT5

BROKER_SUFFIX = {"METAQUOTES": ".mq", "ADMIRALS": ".adm", "VANTAGE": ".van"}
BALANCES = {"METAQUOTES": 10000.0, "ADMIRALS": 20000.0, "VANTAGE": 30000.0}
TICKS = {"METAQUOTES": 1.0, "ADMIRALS": 2.0, "VANTAGE": 4.0}


def _env(tmp_path):
    import json
    env = {}
    for i, account_id in enumerate(ACCOUNT_IDS, 1):
        prefix = f"MT5_{account_id}_"
        env.update({
            prefix + "ENABLED": "true",
            prefix + "LOGIN": str(1000 + i),
            prefix + "SERVER": account_id + "-Demo",
            prefix + "TERMINAL_PATH": str(tmp_path / account_id / "terminal64.exe"),
            prefix + "ROLE": "baseline" if account_id == "METAQUOTES" else "observe_only",
        })
    mapping = {c: c + BROKER_SUFFIX["METAQUOTES"] for c in
               ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
                "AUDUSD", "NZDUSD", "NAS100", "US500", "XAUUSD")}
    env["MT5_METAQUOTES_SYMBOL_MAP"] = json.dumps(mapping)
    mapping = {c: c + BROKER_SUFFIX["VANTAGE"] for c in
               ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
                "AUDUSD", "NZDUSD", "NAS100", "US500", "XAUUSD")}
    env["MT5_VANTAGE_SYMBOL_MAP"] = json.dumps(mapping)
    return env

@pytest.fixture
def accounts(tmp_path):
    return load_accounts(_env(tmp_path))


@pytest.fixture
def decision():
    return CanonicalDecision("opp-1", "cor-1", "dec-1", "EURUSD", "BUY",
                             1.10002, 1.09902, 1.10202, 0.25, "PATTERN", "obs-1")


def _snapshots(accounts, *, drop_admirals=()):
    snapshots = {}
    for account in accounts:
        fake = FakeMT5(account, suffix=BROKER_SUFFIX[account.account_id])
        for gone in (drop_admirals if account.account_id == "ADMIRALS" else ()):
            fake.specs.pop(gone + BROKER_SUFFIX[account.account_id], None)
        fake.account.balance = BALANCES[account.account_id]
        fake.account.margin_free = BALANCES[account.account_id] - 2.0
        fake.account.leverage = 100 if account.account_id == "METAQUOTES" else 200
        report = worker_mod.AccountReader(account, fake).snapshot()
        for row in report["symbols"]:
            if row["status"] == "available":
                # FX tick is 0.00001/1.0; index/CFD tick scaled so exact V10
                # sizing yields valid (non-minimum-blocked) lots in tests.
                if row["canonical_symbol"] in ("NAS100", "US500", "XAUUSD"):
                    row["trade_tick_size"] = 0.01
                    row["trade_tick_value"] = 1.0
                else:
                    row["trade_tick_value"] = TICKS[account.account_id]
        snapshots[account.account_id] = report
    return snapshots


def test_fanout_three_targets_shared_lineage_unique_ids(accounts, decision):
    targets = fan_out_canonical_decision(decision, accounts)
    assert [t.account_id for t in targets] == ["METAQUOTES", "ADMIRALS", "VANTAGE"]
    for target in targets:
        assert (target.canonical_opportunity_id, target.correlation_id,
                target.decision_id) == ("opp-1", "cor-1", "dec-1")
    assert len({t.account_execution_id for t in targets}) == 3
    assert len({t.trade_id for t in targets}) == 3
    assert decision.canonical_opportunity_id == "opp-1"


def test_broker_symbol_differs_per_account(accounts):
    nas = CanonicalDecision("o", "c", "d", "NAS100", "BUY",
                            20000.0, 19980.0, 20060.0, 0.1)
    maps = {"METAQUOTES": "USTEC", "ADMIRALS": None, "VANTAGE": "NAS100.i"}
    targets = fan_out_canonical_decision(nas, accounts, broker_symbols=maps)
    assert {t.account_id: t.broker_symbol for t in targets} == maps
def test_admirals_fx_may_be_eligible(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts))
    by_id = {r["target"].account_id: r for r in routes}
    assert by_id["ADMIRALS"]["eligibility"]["eligible"] is True
    assert by_id["ADMIRALS"]["broker_symbol"] == "EURUSD.adm"


def test_admirals_unavailable_blocks_locally_only(accounts):
    snaps = _snapshots(accounts, drop_admirals=("NAS100", "US500", "XAUUSD"))
    for symbol in ("NAS100", "US500", "XAUUSD"):
        check = CanonicalDecision("o", "c", "d", symbol, "BUY",
                                  20000.0, 19980.0, 20060.0, 0.1)
        routes = prepare_account_routes(decision=check, accounts=accounts,
                                        snapshots=snaps)
        by_id = {r["target"].account_id: r for r in routes}
        assert by_id["ADMIRALS"]["eligibility"]["eligible"] is False
        assert "SYMBOL_UNAVAILABLE" in by_id["ADMIRALS"]["eligibility"]["reasons"]
        assert by_id["METAQUOTES"]["eligibility"]["eligible"] is True
        assert by_id["VANTAGE"]["eligibility"]["eligible"] is True


def test_account_specific_balance_margin_volume(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts))
    by_id = {r["target"].account_id: r for r in routes}
    assert by_id["METAQUOTES"]["eligibility"]["balance"] == 10000.0
    assert by_id["ADMIRALS"]["eligibility"]["balance"] == 20000.0
    assert by_id["VANTAGE"]["eligibility"]["balance"] == 30000.0
    volumes = {k: v["volume"].volume for k, v in by_id.items()}
    assert len(set(volumes.values())) > 1
    assert volumes["VANTAGE"] != volumes["METAQUOTES"]


def test_volume_below_min_blocks(accounts):
    snaps = _snapshots(accounts)
    tiny = CanonicalDecision("o", "c", "d", "EURUSD", "BUY",
                             1.10002, 1.10001, 1.10202, 0.1)
    for report in snaps.values():
        for row in report["symbols"]:
            if row["canonical_symbol"] == "EURUSD":
                row["trade_tick_value"] = 100000.0  # huge loss/lot -> raw < min
    routes = prepare_account_routes(decision=tiny, accounts=accounts,
                                    snapshots=snaps)
    for route in routes:
        assert route["eligibility"]["eligible"] is False
        assert "VOLUME_BELOW_MIN" in route["eligibility"]["reasons"]


def test_one_block_does_not_block_others(accounts):
    snaps = _snapshots(accounts)
    snaps["ADMIRALS"]["margin_free"] = 0.0
    snaps["ADMIRALS"]["positions"] = None
    made = CanonicalDecision("o", "c", "d", "EURUSD", "BUY",
                             1.10002, 1.09902, 1.10202, 0.1)
    routes = prepare_account_routes(decision=made, accounts=accounts,
                                    snapshots=snaps)
    by_id = {r["target"].account_id: r for r in routes}
    assert by_id["ADMIRALS"]["eligibility"]["eligible"] is False
    assert by_id["METAQUOTES"]["eligibility"]["eligible"] is True
    assert by_id["VANTAGE"]["eligibility"]["eligible"] is True
def test_broker_failure_isolated(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts),
                                    global_execution_enabled=True)
    for route in routes:
        route["execution_enabled"] = True
    calls = []

    def execute_one(item):
        calls.append(item["target"].account_id)
        if item["target"].account_id == "METAQUOTES":
            raise TimeoutError("broker timeout")
        return {"executed": True, "status": "FILLED", "ok": True, "comment": "done"}

    outcomes = route_executions(routed=routes, execute_one=execute_one)
    by_id = {o["target"].account_id: o for o in outcomes}
    assert by_id["METAQUOTES"]["executed"] is False
    assert by_id["ADMIRALS"]["executed"] is True
    assert by_id["VANTAGE"]["executed"] is True
    assert set(calls) == {"METAQUOTES", "ADMIRALS", "VANTAGE"}


def test_worker_timeout_is_local(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts),
                                    global_execution_enabled=True)
    for route in routes:
        route["execution_enabled"] = True

    def execute_one(item):
        if item["target"].account_id == "ADMIRALS":
            raise TimeoutError("worker timeout")
        return {"executed": True, "status": "FILLED", "ok": True, "comment": "ok"}

    outcomes = route_executions(routed=routes, execute_one=execute_one, timeout=1)
    by_id = {o["target"].account_id: o for o in outcomes}
    assert by_id["ADMIRALS"]["executed"] is False
    assert by_id["METAQUOTES"]["executed"] is True
    assert by_id["VANTAGE"]["executed"] is True


def test_execution_disabled_never_calls_order_send(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts),
                                    global_execution_enabled=True)
    by_id = {r["target"].account_id: r for r in routes}
    by_id["METAQUOTES"]["execution_enabled"] = True
    by_id["ADMIRALS"]["execution_enabled"] = False
    by_id["VANTAGE"]["execution_enabled"] = False
    ordered = []

    def execute_one(item):
        ordered.append(item["target"].account_id)
        return {"executed": True, "status": "FILLED", "ok": True, "comment": "x"}

    outcomes = route_executions(
        routed=[by_id["METAQUOTES"], by_id["ADMIRALS"], by_id["VANTAGE"]],
        execute_one=execute_one)
    assert ordered == ["METAQUOTES"]
    assert all(o["status"] == "OBSERVED"
               for o in outcomes if o["target"].account_id != "METAQUOTES")


def test_safety_switches_observe_only_disabled(accounts):
    mq = [a for a in accounts if a.account_id == "METAQUOTES"][0]
    admirals = [a for a in accounts if a.account_id == "ADMIRALS"][0]
    assert execution_enabled_for(mq, global_execution_enabled=True) is True
    assert execution_enabled_for(admirals, global_execution_enabled=True) is False
    assert execution_enabled_for(mq, global_execution_enabled=False) is False


def test_metaquotes_only_backward_compatible(accounts, decision):
    snaps = _snapshots(accounts)
    only_mq = [a for a in accounts if a.account_id == "METAQUOTES"]
    routes = prepare_account_routes(decision=decision, accounts=only_mq,
                                    snapshots=snaps)
    assert len(routes) == 1 and routes[0]["target"].account_id == "METAQUOTES"
    assert target_state(routes[0])["broker_symbol"] == "EURUSD.mq"
    request = build_account_order_request(item=routes[0])
    assert request["broker_symbol"] == "EURUSD.mq"
    assert request["canonical_symbol"] == "EURUSD"


def test_child_runtime_state_fields(accounts, decision):
    routes = prepare_account_routes(decision=decision, accounts=accounts,
                                    snapshots=_snapshots(accounts))
    for route in routes:
        state = target_state(route)
        for key in ("account_id", "broker", "broker_server", "canonical_symbol",
                    "broker_symbol", "canonical_opportunity_id", "correlation_id",
                    "decision_id", "account_execution_id", "trade_id",
                    "requested_volume", "entry", "sl", "tp"):
            assert key in state


def test_execution_worker_pinned_routing_and_identity(accounts):
    from core.accounts.execution_worker import execute_pinned
    from dataclasses import asdict
    from types import SimpleNamespace as NS

    class FakeExecMT5:
        ACCOUNT_TRADE_MODE_DEMO = 0
        TRADE_ACTION_DEAL = 1
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_TIME_GTC = 0
        ORDER_FILLING_IOC = 1
        TRADE_RETCODE_DONE = 10009

        def __init__(self, account):
            self.account = account
            self.sent = []

        def account_info(self):
            return self.account

        def terminal_info(self):
            return NS(path=str(__import__("pathlib").Path(
                self.cfg.terminal_path).parent),
                data_path=str(__import__("pathlib").Path(
                    self.cfg.terminal_path).parent / "data"),
                connected=True, trade_allowed=True, tradeapi_disabled=False)

        def symbol_info(self, name):
            return NS(name=name, digits=5, point=0.00001,
                      trade_contract_size=100000.0, volume_min=0.01,
                      volume_step=0.01, volume_max=100.0,
                      trade_stops_level=10, trade_freeze_level=5,
                      trade_tick_size=0.00001, trade_tick_value=1.0,
                      trade_mode=4, filling_mode=1)

        def symbol_info_tick(self, name):
            return NS(bid=1.1, ask=1.10002)

        def order_send(self, request):
            self.sent.append(request)
            return NS(retcode=10009, deal=7, order=8, comment="ok", price=1.1)

        def last_error(self):
            return (0, "ok")

    import pathlib
    by_id = {a.account_id: a for a in accounts}
    for account_id, broker_symbol in (("METAQUOTES", "EURUSD.mq"),
                                      ("ADMIRALS", "EURUSD.adm"),
                                      ("VANTAGE", "EURUSD.van")):
        cfg = by_id[account_id]
        mt5 = FakeExecMT5(NS(
            login=cfg.login, server=cfg.server, trade_mode=0,
            balance=10000.0, equity=10000.0, margin=1.0, margin_free=9999.0,
            margin_level=100.0, leverage=100, currency="USD",
            trade_allowed=True, trade_expert=True))
        mt5.cfg = cfg
        payload = {
            "account": asdict(cfg),
            "target": {"account_id": account_id, "broker": cfg.broker,
                       "broker_server": cfg.server, "canonical_symbol": "EURUSD",
                       "broker_symbol": broker_symbol,
                       "canonical_opportunity_id": "o", "correlation_id": "c",
                       "decision_id": "d", "account_execution_id": "e-" + account_id,
                       "trade_id": "t-" + account_id},
            "order": {"broker_symbol": broker_symbol, "requested_volume": 0.05,
                      "sl": 1.09, "tp": 1.12, "side": "BUY", "deviation": 20,
                      "magic": 713001, "comment": "fanout-test"},
        }
        out = execute_pinned(payload, mt5)
        assert out["executed"] is True and out["status"] == "FILLED"
        assert mt5.sent and mt5.sent[0]["symbol"] == broker_symbol
        assert out["account_execution_id"] == "e-" + account_id
    # Identity mismatch must never route to the wrong account.
    cfg = by_id["METAQUOTES"]
    mt5 = FakeExecMT5(NS(login=cfg.login, server=cfg.server, trade_mode=0,
                         balance=1.0, equity=1.0, margin=0.0, margin_free=1.0,
                         margin_level=0.0, leverage=1, currency="USD",
                         trade_allowed=True, trade_expert=True))
    mt5.cfg = cfg
    bad = {"account": asdict(cfg),
           "target": {"account_id": "ADMIRALS"},
           "order": {}}
    assert execute_pinned(bad, mt5)["status"] == "IDENTITY_MISMATCH"
    assert mt5.sent == []


def test_canonical_decision_from_intent_unchanged():
    intent = NS(symbol="EURUSD", side=NS(name="BUY"), volume=0.33,
               entry_reference=1.1, sl=1.09, tp=1.12, pattern="P")
    made = build_canonical_decision(intent=intent, canonical_opportunity_id="o",
                                    correlation_id="c", decision_id="d",
                                    observation_id="obs")
    assert (made.canonical_symbol, made.side, made.entry) == ("EURUSD", "BUY", 1.1)
    assert intent.volume == 0.33


