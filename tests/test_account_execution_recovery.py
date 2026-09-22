"""Focused tests for the confirmed fan-out snapshot failure (minimal repair).

Incident (proven):
    execute_multi_account_fanout()
    → diagnose() → 3 isolated workers
    → each worker calls running_terminal_processes()
    → PowerShell inventory has a hard timeout=10
    → under live host load raises subprocess.TimeoutExpired
    → run_worker() catch-all returns unavailable([... 'WORKER_READ_FAILED'])
    → all account eligibility fields become unavailable → all routes BLOCKED
    → mt5.order_send() never reached.

These tests reproduce the incident without a terminal or a broker and prove:

1. the inventory budget is configurable and a timeout is classified explicitly;
2. an INCONCLUSIVE inventory no longer poisons the account snapshot;
3. the real terminal-inventory failure reason stays observable (worker + parent);
4. a healthy published snapshot still reaches the execute_one / order_send path;
5. an unavailable snapshot still fails closed and never calls order_send.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

import core.runtime.fanout_execution as fe
from core.accounts import execution_worker as exw
from core.accounts import manager
from core.accounts import terminal
from core.accounts import worker as worker_mod
from core.accounts.account_router import route_executions
from core.accounts.config import ACCOUNT_IDS, load_accounts
from core.accounts.fanout import CanonicalDecision
from core.accounts.fanout_orchestrator import prepare_account_routes
from core.accounts.terminal import (
    DEFAULT_INVENTORY_TIMEOUT_SECONDS, INVENTORY_TIMEOUT_ENV,
    TERMINAL_INVENTORY_OBSERVED, TERMINAL_INVENTORY_TIMEOUT,
    TERMINAL_INVENTORY_UNAVAILABLE, TerminalInventoryError,
    TerminalInventoryTimeout, terminal_inventory_timeout,
    terminal_process_inventory,
)
from tests._account_fake_mt5 import FakeMT5

BROKER_SUFFIX = {"METAQUOTES": ".mq", "ADMIRALS": ".adm", "VANTAGE": ".van"}
TICKS = {"METAQUOTES": 1.0, "ADMIRALS": 2.0, "VANTAGE": 4.0}
BALANCES = {"METAQUOTES": 10000.0, "ADMIRALS": 20000.0, "VANTAGE": 30000.0}

requires_windows_inventory = pytest.mark.skipif(
    os.name != "nt", reason="terminal inventory uses a Windows PowerShell query")


# ─── fixtures / helpers ───────────────────────────────────────────────────────

def _env(tmp_path):
    """Three enabled execution-capable accounts on isolated terminals."""
    env = {}
    for i, account_id in enumerate(ACCOUNT_IDS, 1):
        prefix = f"MT5_{account_id}_"
        env.update({
            prefix + "ENABLED": "true",
            prefix + "LOGIN": str(1000 + i),
            prefix + "SERVER": account_id + "-Demo",
            prefix + "TERMINAL_PATH": str(tmp_path / account_id / "terminal64.exe"),
            prefix + "ROLE": "baseline",
        })
    mapping = {c: c + BROKER_SUFFIX["METAQUOTES"] for c in
               ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
                "AUDUSD", "NZDUSD", "NAS100", "US500", "XAUUSD")}
    env["MT5_METAQUOTES_SYMBOL_MAP"] = json.dumps(mapping)
    return env


@pytest.fixture
def accounts(tmp_path):
    return load_accounts(_env(tmp_path))


def _decision():
    return CanonicalDecision("opp-inc", "cor-inc", "dec-inc", "EURUSD", "BUY",
                             1.10002, 1.09902, 1.10202, 0.1, "PATTERN", "obs-inc")


def _timeout_run(*args, **kwargs):
    raise subprocess.TimeoutExpired(args[0] if args else "powershell.exe",
                                    kwargs.get("timeout", 10))


def _exiting(returncode, *, stdout="", stderr=""):
    def run(*args, **kwargs):
        return NS(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def _report(account, **overrides):
    """A healthy published snapshot for one account (no worker call)."""
    fake = FakeMT5(account, suffix=BROKER_SUFFIX[account.account_id])
    fake.account.balance = BALANCES[account.account_id]
    fake.account.margin_free = BALANCES[account.account_id] - 2.0
    fake.account.leverage = 100
    report = worker_mod.AccountReader(account, fake).snapshot()
    for row in report["symbols"]:
        if row["status"] == "available":
            row["trade_tick_value"] = TICKS[account.account_id]
            row["bid"], row["ask"] = 1.10001, 1.10002
    report.update(overrides)
    return report


def _snapshots(accounts):
    return {a.account_id: _report(a) for a in accounts}


# ─── 1. INVENTORY BUDGET IS CONFIGURABLE, TIMEOUT IS CLASSIFIED ───────────────

def test_inventory_timeout_is_inconclusive_never_absent():
    """One shared policy: observed / inconclusive / fail-closed read failure."""
    def timed_out():
        raise TerminalInventoryTimeout(TERMINAL_INVENTORY_TIMEOUT)

    def absent():
        return []

    def unreadable():
        raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE)

    assert terminal_process_inventory(timed_out) == (None, TERMINAL_INVENTORY_TIMEOUT)
    assert terminal_process_inventory(absent) == ([], TERMINAL_INVENTORY_OBSERVED)
    # A real read failure is never swallowed: it propagates so the caller keeps
    # fail-closed behaviour AND the real code.
    with pytest.raises(TerminalInventoryError) as exc:
        terminal_process_inventory(unreadable)
    assert str(exc.value) == TERMINAL_INVENTORY_UNAVAILABLE
    assert issubclass(TerminalInventoryTimeout, TerminalInventoryError)


@requires_windows_inventory
def test_inventory_budget_is_configurable(monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(terminal.subprocess, "run", run)
    monkeypatch.delenv(INVENTORY_TIMEOUT_ENV, raising=False)
    assert terminal_inventory_timeout() == DEFAULT_INVENTORY_TIMEOUT_SECONDS
    with pytest.raises(TerminalInventoryTimeout) as exc:
        terminal.running_terminal_processes()
    assert str(exc.value) == TERMINAL_INVENTORY_TIMEOUT
    assert seen["timeout"] == DEFAULT_INVENTORY_TIMEOUT_SECONDS

    monkeypatch.setenv(INVENTORY_TIMEOUT_ENV, "2.5")
    assert terminal_inventory_timeout() == 2.5
    with pytest.raises(TerminalInventoryTimeout):
        terminal.running_terminal_processes()
    assert seen["timeout"] == 2.5

    # An explicit per-call budget still wins (no hard-coded value anywhere).
    with pytest.raises(TerminalInventoryTimeout):
        terminal.running_terminal_processes(timeout=0.5)
    assert seen["timeout"] == 0.5

    for invalid in ("not-a-number", "0", "-3"):
        monkeypatch.setenv(INVENTORY_TIMEOUT_ENV, invalid)
        assert terminal_inventory_timeout() == DEFAULT_INVENTORY_TIMEOUT_SECONDS

    monkeypatch.setattr(terminal, "running_terminal_processes", lambda **_kw: [])
    assert terminal_process_inventory(terminal.running_terminals) == (
        [], TERMINAL_INVENTORY_OBSERVED)


# ─── 2. WORKER SNAPSHOT SURVIVES AN INVENTORY TIMEOUT ─────────────────────────

@requires_windows_inventory
def test_worker_snapshot_survives_inventory_timeout(accounts, monkeypatch):
    """The incident's first failure: a frozen inventory poisoned the snapshot."""
    account = accounts[0]
    monkeypatch.setattr(terminal.subprocess, "run", _timeout_run)
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        FakeMT5(account, suffix=BROKER_SUFFIX[account.account_id]))

    report = worker_mod.run_worker(account)

    assert report["connected"] is True
    assert report["identity_verified"] is True
    assert report["balance"] == 10000.0
    assert report["equity"] is not None and report["leverage"] is not None
    # Not poisoned: no unavailable reasons, real account facts present.
    assert report["reasons"] == []
    assert "WORKER_READ_FAILED" not in report["reasons"]
    assert any(row["status"] == "available" for row in report["symbols"])
    # ...and the real inventory failure reason stays observable.
    assert report["terminal_inventory"] == TERMINAL_INVENTORY_TIMEOUT


@requires_windows_inventory
def test_worker_identity_verification_still_authoritative_after_timeout(accounts, monkeypatch):
    """An inconclusive inventory never weakens identity checks (fail closed)."""
    account = accounts[0]
    fake = FakeMT5(account)
    fake.account.login = accounts[1].login          # wrong account behind the path
    monkeypatch.setattr(terminal.subprocess, "run", _timeout_run)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)

    report = worker_mod.run_worker(account)

    assert report["reasons"] == ["ACCOUNT_IDENTITY_MISMATCH"]
    assert report["connected"] is False
    assert report["balance"] is None


@requires_windows_inventory
def test_worker_reports_real_inventory_failure_and_fails_closed(accounts, monkeypatch):
    """A readable-inventory failure keeps its real code, never WORKER_READ_FAILED."""
    account = accounts[0]
    monkeypatch.setattr(terminal.subprocess, "run", _exiting(1))
    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMT5(account))

    report = worker_mod.run_worker(account)

    assert report["reasons"] == [TERMINAL_INVENTORY_UNAVAILABLE]
    assert report["connected"] is False and report["identity_verified"] is False
    assert report["balance"] is None


@requires_windows_inventory
def test_unparseable_inventory_output_fails_closed(monkeypatch):
    monkeypatch.setattr(terminal.subprocess, "run", _exiting(0, stdout="not-json"))
    with pytest.raises(TerminalInventoryError) as exc:
        terminal.running_terminal_processes()
    assert str(exc.value) == TERMINAL_INVENTORY_UNAVAILABLE


def test_absent_terminal_still_blocks_the_snapshot(accounts, monkeypatch):
    """A DEFINITE absence (readable inventory without the terminal) fails closed."""
    monkeypatch.setattr(worker_mod, "running_terminals", lambda: [])
    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMT5(accounts[0]))

    report = worker_mod.run_worker(accounts[0])

    assert report["reasons"] == ["TERMINAL_NOT_RUNNING_OR_NOT_VISIBLE"]
    assert report["connected"] is False

# ─── 3. PARENT SURFACES THE REAL CHILD FAILURE (CODE + STDERR DETAIL) ─────────

def test_child_failure_surfaces_exit_code_and_allowlisted_stderr(accounts, monkeypatch):
    secret = "ACCOUNT-SECRET-1234"
    stderr = (f"Traceback (most recent call last):\n  File \"...\"\n"
              f"RuntimeError: {TERMINAL_INVENTORY_TIMEOUT}\n{secret}\n")
    monkeypatch.setattr(manager.subprocess, "run", _exiting(1, stderr=stderr))

    report = manager.run_isolated(accounts[0])
    blob = json.dumps(report)

    assert report["reasons"] == ["WORKER_EXIT_1", TERMINAL_INVENTORY_TIMEOUT]
    assert report["connected"] is False and report["balance"] is None
    # Never a bare generic reason, never raw stderr text.
    assert "WORKER_FAILED" not in report["reasons"]
    assert secret not in blob and "Traceback" not in blob


def test_child_crash_code_is_labelled(accounts, monkeypatch):
    monkeypatch.setattr(manager.subprocess, "run", _exiting(-1073741819))
    report = manager.run_isolated(accounts[0])
    assert report["reasons"] == ["WORKER_EXIT_0xC0000005",
                                "WORKER_CRASHED_ACCESS_VIOLATION"]


def test_child_stderr_without_known_code_is_summarised_not_echoed(accounts, monkeypatch):
    monkeypatch.setattr(manager.subprocess, "run",
                        _exiting(2, stderr="platform text containing ACCOUNT-SECRET-1234"))
    report = manager.run_isolated(accounts[0])
    assert report["reasons"] == ["WORKER_EXIT_2", "WORKER_STDERR_PRESENT"]
    assert "ACCOUNT-SECRET-1234" not in json.dumps(report)


def test_child_exit_without_stderr_reports_only_the_code(accounts, monkeypatch):
    monkeypatch.setattr(manager.subprocess, "run", _exiting(3))
    assert manager.run_isolated(accounts[0])["reasons"] == ["WORKER_EXIT_3"]


def test_worker_timeout_is_configurable(accounts, monkeypatch):
    seen = {}
    healthy = worker_mod.unavailable(accounts[0], ["INITIALIZE_FAILED"])

    def run(command, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return NS(returncode=0, stdout=json.dumps(healthy), stderr="")

    monkeypatch.setattr(manager.subprocess, "run", run)
    monkeypatch.delenv(manager.WORKER_TIMEOUT_ENV, raising=False)
    manager.run_isolated(accounts[0])
    assert seen["timeout"] == manager.DEFAULT_WORKER_TIMEOUT_SECONDS

    monkeypatch.setenv(manager.WORKER_TIMEOUT_ENV, "12.5")
    manager.run_isolated(accounts[0])
    assert seen["timeout"] == 12.5

    # An explicit caller budget still wins (e.g. python -m core.accounts --timeout).
    manager.run_isolated(accounts[0], timeout=3)
    assert seen["timeout"] == 3.0

    # The inventory budget is forwarded to the worker child (non-secret tunable).
    monkeypatch.setenv(INVENTORY_TIMEOUT_ENV, "4.5")
    captured = {}

    def run_capture(command, **kwargs):
        captured.update(kwargs)
        return NS(returncode=0, stdout=json.dumps(healthy), stderr="")

    monkeypatch.setattr(manager.subprocess, "run", run_capture)
    manager.run_isolated(accounts[0])
    assert captured["env"][INVENTORY_TIMEOUT_ENV] == "4.5"


def test_worker_timeout_still_fails_closed(accounts, monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("python", kwargs.get("timeout", 25))

    monkeypatch.setattr(manager.subprocess, "run", run)
    report = manager.run_isolated(accounts[0])
    assert report["reasons"] == ["WORKER_TIMEOUT"] and report["connected"] is False


# ─── 4. INCIDENT REPRODUCTION AT THE REAL PROCESS BOUNDARY ───────────────────

def test_incident_diagnose_returns_healthy_snapshots_on_inventory_timeout(accounts, monkeypatch):
    """diagnose() → isolated worker → frozen inventory → healthy snapshots."""
    monkeypatch.setattr(manager, "WORKER_MODULE",
                        "tests._account_inventory_timeout_worker")

    reports = manager.diagnose(accounts)

    assert [r["account_id"] for r in reports] == list(ACCOUNT_IDS)
    for report in reports:
        assert report["connected"] is True
        assert report["identity_verified"] is True
        assert report["balance"] is not None
        assert report["reasons"] == []
        assert report["execution_enabled"] is True
        # Real inventory reason preserved as evidence of a degraded pre-flight.
        assert report["terminal_inventory"] == TERMINAL_INVENTORY_TIMEOUT
        assert "WORKER_READ_FAILED" not in report["reasons"]


def test_incident_snapshots_produce_eligible_routes(accounts, monkeypatch):
    """The repaired snapshot list still yields per-account eligible routes."""
    monkeypatch.setattr(manager, "WORKER_MODULE",
                        "tests._account_inventory_timeout_worker")
    snapshots = {r["account_id"]: r for r in manager.diagnose(accounts)}

    routes = prepare_account_routes(decision=_decision(), accounts=accounts,
                                    snapshots=snapshots, global_execution_enabled=True)

    assert [r["target"].account_id for r in routes] == list(ACCOUNT_IDS)
    assert all(r["eligibility"]["eligible"] for r in routes)
    assert all(r["execution_enabled"] for r in routes)


# ─── 5. ROUTE EXECUTION: HEALTHY → order_send, UNAVAILABLE → FAIL CLOSED ─────

class FakeExecMT5:
    """Order-capable MT5 double. Records every order_send; never a real broker."""

    ACCOUNT_TRADE_MODE_DEMO = 0
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, cfg):
        self.cfg = cfg
        self.sent = []
        self.account = NS(login=cfg.login, server=cfg.server, trade_mode=0,
                          balance=10000.0, equity=10000.0, margin=1.0,
                          margin_free=9999.0, margin_level=100.0, leverage=100,
                          currency="USD", trade_allowed=True, trade_expert=True)
        self.terminal = NS(path=str(Path(cfg.terminal_path).parent),
                           data_path=str(Path(cfg.terminal_path).parent / "data"),
                           connected=True, trade_allowed=True, tradeapi_disabled=False)
        self.spec = dict(name="EURUSD", digits=5, point=0.00001,
                         trade_contract_size=100000.0, volume_min=0.01,
                         volume_step=0.01, volume_max=100.0,
                         trade_stops_level=10, trade_freeze_level=5,
                         trade_tick_size=0.00001, trade_tick_value=1.0,
                         trade_mode=4, filling_mode=self.ORDER_FILLING_IOC)

    def initialize(self, *args, **kwargs):
        return True

    def shutdown(self):
        pass

    def account_info(self):
        return self.account

    def terminal_info(self):
        return self.terminal

    def symbol_info(self, name):
        return NS(**self.spec)

    def symbol_info_tick(self, name):
        return NS(bid=1.10001, ask=1.10002)

    def positions_get(self, *args, **kwargs):
        return []

    def history_deals_get(self, *args, **kwargs):
        return []

    def order_calc_margin(self, *args, **kwargs):
        return 10.0

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        ticks = (price_close - price_open) / 0.00001
        signed = 1.0 if order_type == self.ORDER_TYPE_BUY else -1.0
        return ticks * 1.0 * float(volume) * signed

    def order_send(self, request):
        self.sent.append(request)
        return NS(retcode=10009, deal=7, order=8, comment="ok", price=1.10002)

    def last_error(self):
        return (0, "ok")


def _routes(accounts, snapshots):
    return prepare_account_routes(decision=_decision(), accounts=accounts,
                                  snapshots=snapshots, global_execution_enabled=True)


def _execute_one_in_process(sent):
    """Same production payload contract; MT5 double instead of a child process."""
    def execute_one(item):
        account = item["account_config"]
        target, order = fe._build_worker_payload(item, _decision())
        fake = FakeExecMT5(account)
        sent[account.account_id] = fake
        return exw.run_execution_worker(
            account, {"target": target, "order": order}, mt5=fake)
    return execute_one


def test_healthy_snapshot_reaches_order_send(accounts, monkeypatch):
    monkeypatch.setattr(exw, "running_terminals",
                        lambda: [a.terminal_path for a in accounts])
    routes = _routes(accounts, _snapshots(accounts))
    by_route = {r["target"].account_id: r for r in routes}
    sent = {}
    outcomes = route_executions(routed=routes,
                                execute_one=_execute_one_in_process(sent))

    assert [o["target"].account_id for o in outcomes] == list(ACCOUNT_IDS)
    assert all(o["executed"] is True and o["status"] == "FILLED" for o in outcomes)
    assert all(o["retcode"] == 10009 for o in outcomes)
    for account in accounts:
        fake = sent[account.account_id]
        assert len(fake.sent) == 1, "mt5.order_send must be reached exactly once"
        assert fake.sent[0]["symbol"] == "EURUSD" + BROKER_SUFFIX[account.account_id]
        assert fake.sent[0]["volume"] == pytest.approx(
            by_route[account.account_id]["volume"].volume)


@requires_windows_inventory
def test_order_path_survives_inventory_timeout(accounts, monkeypatch):
    """An inventory timeout at execution time must not block a verified order."""
    monkeypatch.setattr(terminal.subprocess, "run", _timeout_run)
    sent = {}
    outcomes = route_executions(routed=_routes(accounts, _snapshots(accounts)),
                                execute_one=_execute_one_in_process(sent))

    assert all(o["executed"] is True and o["status"] == "FILLED" for o in outcomes)
    assert all(len(f.sent) == 1 for f in sent.values())
    assert all(o["terminal_inventory"] == TERMINAL_INVENTORY_TIMEOUT for o in outcomes)


def test_unavailable_snapshot_fails_closed_without_order_send(accounts, monkeypatch):
    snapshots = _snapshots(accounts)
    doomed = accounts[0].account_id
    snapshots[doomed] = worker_mod.unavailable(accounts[0], [TERMINAL_INVENTORY_UNAVAILABLE])
    monkeypatch.setattr(exw, "running_terminals",
                        lambda: [a.terminal_path for a in accounts])
    sent = {}
    outcomes = route_executions(routed=_routes(accounts, snapshots),
                                execute_one=_execute_one_in_process(sent))
    by_id = {o["target"].account_id: o for o in outcomes}

    assert by_id[doomed]["executed"] is False
    assert by_id[doomed]["status"] == "BLOCKED"
    # Fail-closed at the route boundary...
    assert "NOT_CONNECTED" in by_id[doomed]["comment"]
    # ...and the REAL worker reason stays observable on the embedded snapshot.
    assert TERMINAL_INVENTORY_UNAVAILABLE in by_id[doomed]["snapshot"]["reasons"]
    assert doomed not in sent, "an unavailable account must never reach order_send"
    # Account-local failure only: the sibling accounts still execute.
    for account in accounts[1:]:
        assert by_id[account.account_id]["executed"] is True
        assert len(sent[account.account_id].sent) == 1


def test_incident_all_routes_blocked_when_every_snapshot_is_unavailable(accounts):
    """The pre-repair behaviour reproduced at the route boundary (fail closed)."""
    snapshots = {a.account_id: worker_mod.unavailable(a, ["WORKER_READ_FAILED"])
                 for a in accounts}
    calls = []

    def execute_one(item):
        calls.append(item)
        raise AssertionError("order path must never be reached")

    outcomes = route_executions(routed=_routes(accounts, snapshots),
                                execute_one=execute_one)

    assert calls == []
    assert all(o["executed"] is False and o["status"] == "BLOCKED" for o in outcomes)
    # The route comment carries the eligibility vocabulary; the real worker
    # reason stays observable on each outcome's embedded snapshot.
    assert all("NOT_CONNECTED" in o["comment"] for o in outcomes)
    assert all("WORKER_READ_FAILED" in o["snapshot"]["reasons"] for o in outcomes)

