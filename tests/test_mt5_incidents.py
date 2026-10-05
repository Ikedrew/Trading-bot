"""Injected native failures exercise real feed, startup and reconnect boundaries."""
from types import SimpleNamespace
from unittest.mock import Mock
import logging

import MetaTrader5 as mt5
import pytest
from core.mt5_incident import MT5ConnectionError, MT5ConnectionIncident, is_connection_error, mt5_failure

IPC_RECV = (-10002, "IPC recv failed")
IPC_GONE = (-10004, "No IPC connection")
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
           "AUDUSD", "NZDUSD", "USTEC", "US500", "XAUUSD"]


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(error=(1, "Success"), connected=True)
    monkeypatch.setattr(mt5, "last_error", lambda: state.error)
    monkeypatch.setattr(mt5, "symbols_get", lambda: tuple(SimpleNamespace(name=s) for s in SYMBOLS))
    monkeypatch.setattr(mt5, "symbol_select", lambda *a: True)
    monkeypatch.setattr(mt5, "terminal_info", lambda: SimpleNamespace(connected=state.connected))
    import core.mt5_connection as connection
    monkeypatch.setattr(connection, "mt5_call", lambda fn, *a, **kw: fn(*a, **{k:v for k,v in kw.items() if k != 'timeout'}))
    import data.mt5_data as data
    monkeypatch.setattr(data, "mt5_call", lambda fn, *a, **kw: fn(*a))
    return state


@pytest.fixture
def startup(monkeypatch, native):
    import core.runtime.scanner_init as init
    cfg = SimpleNamespace(CANONICAL_SYMBOLS=None, SYMBOLS=SYMBOLS,
                          TRADE_MANAGEMENT_ENABLED=False, MTF_ENABLED=False,
                          MARKET_CONTEXT_ENABLED=False, MT5_TERMINAL_MANAGER_ENABLED=False)
    monkeypatch.setattr(init, "config", cfg)
    monkeypatch.setattr(init, "load_engine_state", lambda s: None)
    monkeypatch.setattr(init, "_build_risk_manager", lambda: Mock())
    monkeypatch.setattr(init, "StaleDataMonitor", lambda *a: Mock())
    monkeypatch.setattr(init, "_ensure_enabled_account_terminals", Mock())
    return init


def messages(caplog, tag):
    return [r for r in caplog.records if tag in r.getMessage()]


@pytest.mark.parametrize("error", [IPC_RECV, IPC_GONE, (-10003, "IPC initialize failed"), (-10005, "IPC timeout")])
def test_connection_classification(error):
    assert is_connection_error(error)
    assert isinstance(mt5_failure(error, "symbols_get"), MT5ConnectionError)


@pytest.mark.parametrize("error", [(1, "Success"), (-4, "Not found"), (-2, "Invalid parameters"),
                                   (-6, "Authorization failed"), (-5, "Incompatible versions")])
def test_other_genuine_failures_are_not_ipc(error):
    assert not is_connection_error(error)
    assert not isinstance(mt5_failure(error, "symbol_info"), MT5ConnectionError)


@pytest.mark.parametrize("error", [IPC_RECV, IPC_GONE])
def test_startup_shared_ipc_aborts_once_for_ten_symbols(startup, native, monkeypatch, caplog, error):
    native.error = error
    call = Mock(return_value=None)
    monkeypatch.setattr(mt5, "symbols_get", call)
    assert startup.initialize_symbol_states(symbols=SYMBOLS, execution=Mock()) == []
    assert call.call_count == 1
    loss = messages(caplog, "[MT5_CONNECTION_LOST]")
    assert len(loss) == 1 and loss[0].levelno == logging.CRITICAL
    assert "affected_symbols=10" in loss[0].getMessage()
    assert not messages(caplog, "SYMBOL_INIT_FAIL")
    startup._ensure_enabled_account_terminals.assert_not_called()


def test_mid_startup_loss_discards_partial_states(startup, native, monkeypatch, caplog):
    original = mt5.symbols_get
    calls = [0]
    def inventory():
        calls[0] += 1
        if calls[0] >= 3:
            native.error = IPC_GONE
            return None
        return original()
    monkeypatch.setattr(mt5, "symbols_get", inventory)
    assert startup.initialize_symbol_states(symbols=SYMBOLS, execution=Mock()) == []
    assert calls[0] == 3
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    assert not messages(caplog, "SYMBOL_INIT_FAIL")


def test_missing_symbol_remains_visible_with_healthy_terminal(startup, native, caplog):
    states = startup.initialize_symbol_states(symbols=["MISSING", "EURUSD"], execution=Mock())
    assert [s.symbol for s in states] == ["EURUSD"]
    failures = messages(caplog, "SYMBOL_INIT_FAIL")
    assert len(failures) == 1 and "MISSING" in failures[0].getMessage()
    assert not messages(caplog, "MT5_CONNECTION_LOST")


def test_healthy_startup_is_unchanged(startup, caplog):
    states = startup.initialize_symbol_states(symbols=SYMBOLS, execution=Mock())
    assert [s.symbol for s in states] == SYMBOLS
    assert not messages(caplog, "MT5_CONNECTION_")


def test_canonical_resolution_does_not_skip_shared_ipc(startup, native, monkeypatch, caplog):
    startup.config.CANONICAL_SYMBOLS = SYMBOLS
    native.error = IPC_GONE
    get = Mock(return_value=None)
    monkeypatch.setattr(mt5, "symbols_get", get)
    assert startup.initialize_symbol_states(symbols=None, execution=Mock()) == []
    assert get.call_count == 1
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    assert not messages(caplog, "[SYMBOL_MAP]")


@pytest.mark.parametrize("connected,info", [(False, True), (True, False)])
def test_terminal_unavailable_startup_is_aggregate(startup, native, monkeypatch, caplog, connected, info):
    monkeypatch.setattr(mt5, "symbols_get", lambda: None)
    native.connected = connected
    if not info:
        monkeypatch.setattr(mt5, "terminal_info", lambda: None)
    assert startup.initialize_symbol_states(symbols=SYMBOLS, execution=Mock()) == []
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    assert not messages(caplog, "SYMBOL_INIT_FAIL")


def test_incident_dedup_changed_reason_recovery_and_escalation(caplog):
    caplog.set_level(logging.INFO)
    clock = [0.0]
    incident = MT5ConnectionIncident(10, logging.getLogger("test.incident"), clock=lambda: clock[0])
    for _ in range(100): incident.lost(IPC_RECV)
    incident.lost(IPC_GONE)
    for _ in range(100): incident.lost(IPC_GONE)
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    assert len(messages(caplog, "MT5_CONNECTION_CHANGED")) == 1
    clock[0] = 301
    for _ in range(100): incident.check_prolonged()
    assert len(messages(caplog, "MT5_CONNECTION_UNRECOVERED")) == 1
    incident.restored(); incident.restored()
    assert len(messages(caplog, "MT5_CONNECTION_RESTORED")) == 1
    incident.lost(IPC_GONE)
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 2


def test_runtime_blocks_backoff_then_restores(native, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    import core.runtime.mt5_health as health
    clock = [1000.0]
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    native.error = IPC_GONE
    monkeypatch.setattr(mt5, "terminal_info", lambda: None)
    initialize = Mock(return_value=False)
    monkeypatch.setattr(mt5, "initialize", initialize)
    monkeypatch.setattr(mt5, "shutdown", Mock())
    states = [SimpleNamespace(symbol=s, trade_manager=None) for s in SYMBOLS]
    incident = MT5ConnectionIncident(10, health.logger, clock=lambda: clock[0])
    manager = health.MT5HealthManager(states, SimpleNamespace(), incident=incident)
    assert not manager.check_and_reconnect()
    assert initialize.call_count == 0
    assert not manager.check_and_reconnect()
    assert initialize.call_count == 2
    for _ in range(100): assert not manager.check_and_reconnect()
    assert initialize.call_count == 2
    assert manager.mt5_state == "DISCONNECTED"
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    native.error = IPC_RECV; clock[0] += 21
    assert not manager.check_and_reconnect()
    assert len(messages(caplog, "MT5_CONNECTION_CHANGED")) == 1
    clock[0] += 301
    assert not manager.check_and_reconnect()
    assert len(messages(caplog, "MT5_CONNECTION_UNRECOVERED")) == 1
    monkeypatch.setattr(mt5, "terminal_info", lambda: SimpleNamespace(connected=True))
    initialize.return_value = True; native.error = (1, "Success"); clock[0] += 61
    assert not manager.check_and_reconnect()
    assert manager.check_and_reconnect()
    assert manager.check_and_reconnect()
    assert len(messages(caplog, "MT5_CONNECTION_RESTORED")) == 1


@pytest.mark.parametrize("operation", ["last_tick", "copy_rates_closed"])
def test_real_feed_and_bar_provider_propagate_ipc(native, monkeypatch, operation):
    from data.mt5_data import MT5DataFeed
    from core.runtime.bar_provider import BarProvider
    native.error = IPC_GONE
    monkeypatch.setattr(mt5, "symbol_info_tick", lambda *a: None)
    monkeypatch.setattr(mt5, "copy_rates_from_pos", lambda *a: None)
    feed = MT5DataFeed("EURUSD")
    with pytest.raises(MT5ConnectionError):
        if operation == "last_tick": feed.last_tick("EURUSD")
        else:
            BarProvider(SimpleNamespace(TIMEFRAME=5, CANDLE_COUNT=300)).fetch_bar(
                SimpleNamespace(feed=feed, symbol="EURUSD"))


def test_failed_resync_never_closes_positions_or_reports_restored(native, monkeypatch, caplog):
    import core.runtime.mt5_health as health
    native.error = IPC_GONE
    monkeypatch.setattr(mt5, "positions_get", lambda **kw: None)
    monkeypatch.setattr(mt5, "initialize", lambda **kw: True)
    monkeypatch.setattr(mt5, "shutdown", Mock())
    tm = Mock()
    manager = health.MT5HealthManager([SimpleNamespace(symbol="EURUSD", trade_manager=tm)], SimpleNamespace())
    manager.mark_unavailable(IPC_GONE)
    assert not manager.check_and_reconnect()
    assert manager.mt5_state == "DISCONNECTED"
    tm.positions_open.assert_not_called()
    assert not messages(caplog, "MT5_CONNECTION_RESTORED")


@pytest.mark.parametrize("operation", ["tick", "bar"])
def test_live_scanner_stops_remaining_symbols_and_defers_to_health_owner(native, monkeypatch, caplog, operation):
    """Run the actual scanner loop; a lost feed must not reach other symbols."""
    import core.runtime.live_scanner as scanner
    import core.daily_reset as daily_reset
    import core.evaluation.evaluation_runner as evaluation
    monkeypatch.setattr(evaluation, "shutdown_evaluation", Mock())
    first_feed, second_feed = Mock(), Mock()
    first_feed.last_tick.return_value = (1.0, 1.1, 123)
    if operation == "tick":
        first_feed.last_tick.side_effect = MT5ConnectionError(IPC_GONE, "tick")
    else:
        first_feed.copy_rates_closed.side_effect = MT5ConnectionError(IPC_GONE, "rates")
    states = [SimpleNamespace(symbol=s, feed=f, trade_manager=None,
                              stale_monitor=Mock(), engine_state=Mock())
              for s, f in zip(SYMBOLS, (first_feed, second_feed))]
    monkeypatch.setattr(scanner, "initialize_symbol_states", lambda **kw: states)
    for name in ("MT5Execution", "ExecutionOrchestrator", "DrawdownGuard", "DailyLossGuard",
                 "DailyTradeLimitManager", "TradeCooldownManager", "ObserverRegistry",
                 "RuntimeStateClassifier", "DecisionRecorder"):
        monkeypatch.setattr(scanner, name, Mock())
    monkeypatch.setattr(daily_reset, "DailyResetCoordinator", Mock())
    guards = Mock()
    guards.evaluate.return_value = SimpleNamespace(cycle_allowed=True, drawdown_result=None,
        daily_loss_result=None, daily_loss_blocked=False, kill_switch_active=False)
    monkeypatch.setattr(scanner, "CycleGuards", Mock(return_value=guards))
    monitor = Mock()
    monkeypatch.setattr(scanner, "HealthMonitor", Mock(return_value=monitor))
    ticks = Mock()
    ticks.evaluate.return_value.valid = True
    monkeypatch.setattr(scanner, "TickMonitor", Mock(return_value=ticks))
    monkeypatch.setattr(scanner, "drive_tick", Mock())
    monkeypatch.setattr(scanner, "get_ledger", Mock())
    monkeypatch.setattr(scanner, "save_engine_states", Mock())
    monkeypatch.setattr(scanner, "interruptible_sleep", lambda *a: None)
    monkeypatch.setattr(scanner, "is_shutdown_requested", lambda: False)
    monkeypatch.setattr(scanner, "set_active_symbol", Mock())
    monkeypatch.setattr(scanner, "emit_cycle_report", Mock())
    reconnect = Mock(return_value=False)
    import core.runtime.mt5_health as health
    monkeypatch.setattr(health, "attempt_reconnect", reconnect)
    scanner.run_live_scanner(symbols=[s.symbol for s in states], max_iterations=2)
    assert first_feed.last_tick.call_count == 1
    second_feed.last_tick.assert_not_called()
    assert reconnect.call_count == 1
    scanner.emit_cycle_report.assert_not_called()
    assert len(messages(caplog, "MT5_CONNECTION_LOST")) == 1
    assert not messages(caplog, "RUNTIME_EXCEPTION")
    assert all(call.args[-1] == "DISCONNECTED" for call in monitor.write_heartbeat.call_args_list)


def test_datafeed_lifecycle_ownership_is_unchanged(native, monkeypatch):
    from core import config
    from data.mt5_data import MT5DataFeed
    initialize, shutdown = Mock(return_value=True), Mock()
    monkeypatch.setattr(mt5, "initialize", initialize)
    monkeypatch.setattr(mt5, "shutdown", shutdown)
    feed = MT5DataFeed("EURUSD")
    monkeypatch.setattr(config, "MT5_CENTRALISED_INIT", True)
    feed.connect(); feed.disconnect()
    initialize.assert_not_called(); shutdown.assert_not_called()
    monkeypatch.setattr(config, "MT5_CENTRALISED_INIT", False)
    feed.connect(); feed.disconnect()
    assert initialize.call_count == 1 and shutdown.call_count == 1
