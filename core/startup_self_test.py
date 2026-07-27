"""
I5: Startup Self-Test — Comprehensive pre-flight verification.

Verifies the entire trading stack is operational before live trading begins.
The system does not start trading unless all critical dependencies pass.

Fail-fast: any failure aborts startup immediately.
No partial success allowed.

Test sequence:
1. Configuration Integrity
2. MT5 Connection
3. Account Validation
4. Symbol Resolution
5. Market Data Retrieval
6. Tick Data Availability
7. Position Query Verification
8. Daily State Recovery
9. Heartbeat / Watchdog Compatibility
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call
from core.heartbeat import write_heartbeat, read_heartbeat, STATUS_STARTING

logger = logging.getLogger(__name__)


# ─── ERRORS ───────────────────────────────────────────────────────────────────

class StartupSelfTestError(RuntimeError):
    """Fatal startup self-test failure — trading must not begin."""
    pass


# ─── RESULT TRACKING ──────────────────────────────────────────────────────────

_CHECK_NAMES = [
    "Config",
    "MT5",
    "Account",
    "Symbols",
    "Candles",
    "Ticks",
    "Positions",
    "State",
    "Heartbeat",
]


def _fail(check_name: str, reason: str, symbol: str = "") -> None:
    """Log failure and raise StartupSelfTestError."""
    sym_info = f" Symbol: {symbol}" if symbol else ""
    msg = (
        f"[SELF_TEST_FAILED] Check: {check_name}{sym_info} "
        f"Reason: {reason}"
    )
    logger.critical(msg)
    raise StartupSelfTestError(msg)


def _pass(check_name: str, detail: str = "") -> None:
    """Log successful check."""
    extra = f" ({detail})" if detail else ""
    logger.info("[SELF_TEST] %s%s OK", check_name, extra)


# ─── SELF-TEST IMPLEMENTATION ─────────────────────────────────────────────────

def run_startup_self_test(*, symbols: list[str] | None = None) -> None:
    """
    Run comprehensive startup self-test suite.

    Must be called AFTER:
    - Config validation + freeze
    - Profile loading
    - Strategy identity resolution
    - MT5 initialization
    - Account validation

    Must be called BEFORE:
    - Scanner startup
    - Any trade execution

    Args:
        symbols: List of trading symbols to verify. If None, reads from config.

    Raises:
        StartupSelfTestError on any failure.
    """
    results: dict[str, str] = {}

    try:
        # 1. Configuration Integrity
        _check_config_integrity()
        results["Config"] = "PASS"

        # 2. MT5 Connection
        _check_mt5_connection()
        results["MT5"] = "PASS"

        # 3. Account Validation
        _check_account()
        results["Account"] = "PASS"

        # 4. Symbol Resolution
        sym_list = symbols or _get_symbols()
        _check_symbol_resolution(sym_list)
        results["Symbols"] = "PASS"

        # 5. Market Data Retrieval
        _check_candle_retrieval(sym_list)
        results["Candles"] = "PASS"

        # 6. Tick Data Availability
        _check_tick_data(sym_list)
        results["Ticks"] = "PASS"

        # 7. Position Query Verification
        _check_position_query()
        results["Positions"] = "PASS"

        # 8. Daily State Recovery
        _check_state_recovery()
        results["State"] = "PASS"

        # 9. Heartbeat / Watchdog Compatibility
        _check_heartbeat()
        results["Heartbeat"] = "PASS"

    except StartupSelfTestError:
        raise  # Re-raise — already logged
    except Exception as exc:
        _fail("UNEXPECTED", str(exc))

    # All passed — emit summary
    summary = " | ".join(f"{k}: {v}" for k, v in results.items())
    logger.info("[SELF_TEST_PASSED] %s", summary)


# ─── INDIVIDUAL CHECKS ────────────────────────────────────────────────────────

def _check_config_integrity() -> None:
    """Verify configuration is loaded and validated."""
    try:
        from core import config

        # Strategy identity resolved
        strategy = getattr(config, "STRATEGY_NAME", None)
        if not strategy:
            _fail("CONFIG_INTEGRITY", "STRATEGY_NAME not set")

        registry = getattr(config, "MAGIC_NUMBER_REGISTRY", None)
        if not registry or strategy not in registry:
            _fail("CONFIG_INTEGRITY", f"Strategy '{strategy}' not in MAGIC_NUMBER_REGISTRY")

        # BOT_MAGIC is valid int
        magic = getattr(config, "BOT_MAGIC", None)
        if not isinstance(magic, int) or magic <= 0:
            _fail("CONFIG_INTEGRITY", f"BOT_MAGIC invalid: {magic}")

        # Risk limits are sane
        max_dd = getattr(config, "MAX_DRAWDOWN_PERCENT", 0)
        if not isinstance(max_dd, (int, float)) or max_dd <= 0:
            _fail("CONFIG_INTEGRITY", f"MAX_DRAWDOWN_PERCENT invalid: {max_dd}")

        daily_loss = getattr(config, "DAILY_LOSS_LIMIT_PERCENT", 0)
        if not isinstance(daily_loss, (int, float)) or daily_loss <= 0:
            _fail("CONFIG_INTEGRITY", f"DAILY_LOSS_LIMIT_PERCENT invalid: {daily_loss}")

        # Blocked regimes valid
        blocked = getattr(config, "BLOCKED_REGIMES", None)
        if blocked is not None and not isinstance(blocked, (list, tuple)):
            _fail("CONFIG_INTEGRITY", f"BLOCKED_REGIMES must be list, got {type(blocked).__name__}")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("CONFIG_INTEGRITY", str(exc))

    _pass("Config validation")


def _check_mt5_connection() -> None:
    """Verify MT5 terminal is connected and responsive."""
    try:
        # Check terminal info
        term_info = mt5_call(mt5.terminal_info)
        if term_info is None:
            _fail("MT5_CONNECTION", "terminal_info() returned None — terminal not connected")

        # Check version
        version = mt5.version()
        if version is None:
            _fail("MT5_CONNECTION", "version() returned None")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("MT5_CONNECTION", str(exc))

    _pass("MT5 terminal connection")


def _check_account() -> None:
    """Verify account is accessible with valid data."""
    try:
        info = mt5_call(mt5.account_info)
        if info is None:
            _fail("ACCOUNT_VALIDATION", "account_info() returned None")

        if not hasattr(info, "login") or info.login <= 0:
            _fail("ACCOUNT_VALIDATION", "Account login invalid or missing")

        if not hasattr(info, "balance") or float(info.balance) <= 0:
            _fail("ACCOUNT_VALIDATION", f"Account balance invalid: {getattr(info, 'balance', 'N/A')}")

        if not hasattr(info, "leverage") or int(info.leverage) <= 0:
            _fail("ACCOUNT_VALIDATION", f"Account leverage invalid: {getattr(info, 'leverage', 'N/A')}")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("ACCOUNT_VALIDATION", str(exc))

    _pass("Account validation")


def _check_symbol_resolution(symbols: list[str]) -> None:
    """Verify all configured symbols resolve in MT5."""
    try:
        for symbol in symbols:
            info = mt5_call(mt5.symbol_info, symbol)
            if info is None:
                _fail("SYMBOL_RESOLUTION", f"symbol_info returned None", symbol=symbol)

            # Ensure symbol is visible (selected in Market Watch)
            if not info.visible:
                # Try to select it
                if not mt5.symbol_select(symbol, True):
                    _fail("SYMBOL_RESOLUTION", "Cannot select symbol in Market Watch", symbol=symbol)

            _pass(f"{symbol} resolved")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("SYMBOL_RESOLUTION", str(exc))

    _pass("Symbol resolution", f"{len(symbols)} symbols")


def _check_candle_retrieval(symbols: list[str]) -> None:
    """Verify candle data is available for all symbols."""
    try:
        from core import config

        timeframe = getattr(config, "TIMEFRAME", mt5.TIMEFRAME_M5)

        for symbol in symbols:
            rates = mt5_call(mt5.copy_rates_from_pos, symbol, timeframe, 0, 1)
            if rates is None or len(rates) == 0:
                _fail("CANDLE_RETRIEVAL", "copy_rates returned None or empty", symbol=symbol)

            _pass(f"{symbol} candle retrieval")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("CANDLE_RETRIEVAL", str(exc))

    _pass("Candle retrieval", f"{len(symbols)} symbols")


def _check_tick_data(symbols: list[str]) -> None:
    """Verify tick data is available for all symbols."""
    try:
        for symbol in symbols:
            tick = mt5_call(mt5.symbol_info_tick, symbol)
            if tick is None:
                _fail("TICK_DATA", "symbol_info_tick returned None", symbol=symbol)

            if float(tick.bid) <= 0 or float(tick.ask) <= 0:
                _fail("TICK_DATA", f"Invalid tick: bid={tick.bid} ask={tick.ask}", symbol=symbol)

            _pass(f"{symbol} tick data")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("TICK_DATA", str(exc))

    _pass("Tick data", f"{len(symbols)} symbols")


def _check_position_query() -> None:
    """Verify position queries work (broker permissions)."""
    try:
        # positions_get() should return tuple or empty tuple — NOT None
        result = mt5_call(mt5.positions_get)
        if result is None:
            _fail("POSITION_QUERY", "positions_get() returned None — broker permission issue")

        # Success — doesn't need to have positions, just needs to work

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("POSITION_QUERY", str(exc))

    _pass("Position query")


def _check_state_recovery() -> None:
    """Verify persistence systems can load or initialize."""
    errors: list[str] = []

    # Daily loss guard state
    try:
        from risk.daily_loss_guard import DailyLossGuard
        DailyLossGuard()  # Loads or initializes state
    except Exception as exc:
        errors.append(f"DailyLossGuard: {exc}")

    # Daily trade limit state
    try:
        from risk.daily_trade_limit import DailyTradeLimitManager
        DailyTradeLimitManager()  # Loads or initializes state
    except Exception as exc:
        errors.append(f"DailyTradeLimitManager: {exc}")

    # Trade cooldown state
    try:
        from risk.trade_cooldown import TradeCooldownManager
        TradeCooldownManager()  # Loads or initializes state
    except Exception as exc:
        errors.append(f"TradeCooldownManager: {exc}")

    # Daily reset coordinator
    try:
        from core.daily_reset import DailyResetCoordinator
        DailyResetCoordinator()  # Loads or initializes state
    except Exception as exc:
        errors.append(f"DailyResetCoordinator: {exc}")

    if errors:
        _fail("STATE_RECOVERY", "; ".join(errors))

    _pass("State recovery")


def _check_heartbeat() -> None:
    """Verify heartbeat system can write and read."""
    try:
        # Write test heartbeat
        success = write_heartbeat(status=STATUS_STARTING, extra={"self_test": True})
        if not success:
            _fail("HEARTBEAT", "write_heartbeat() returned False — path unavailable")

        # Read it back
        data = read_heartbeat()
        if data is None:
            _fail("HEARTBEAT", "read_heartbeat() returned None after write")

        if data.get("status") != STATUS_STARTING:
            _fail("HEARTBEAT", f"Heartbeat readback mismatch: {data.get('status')}")

    except StartupSelfTestError:
        raise
    except Exception as exc:
        _fail("HEARTBEAT", str(exc))

    _pass("Heartbeat system")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get_symbols() -> list[str]:
    """Get symbol list from config (prefers CANONICAL_SYMBOLS)."""
    try:
        from core import config
        canonical = getattr(config, "CANONICAL_SYMBOLS", None)
        if canonical and isinstance(canonical, (list, tuple)):
            return list(canonical)
        symbols = getattr(config, "SYMBOLS", None)
        if symbols and isinstance(symbols, (list, tuple)):
            return list(symbols)
        symbol = getattr(config, "SYMBOL", None)
        if symbol:
            if isinstance(symbol, (list, tuple)):
                return list(symbol)
            return [str(symbol)]
    except ImportError:
        pass
    return []
