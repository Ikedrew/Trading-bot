"""
Entry point: configure logging and choose execution mode.
"""

from __future__ import annotations

import logging
import signal
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core.loop import run_live, run_replay, run_replay_scanner, run_live_scanner  # noqa: E402
from core.mt5_validation import validate_account  # noqa: E402
from core.config_validation import validate_and_freeze_config  # noqa: E402  # noqa: E402
from core.discord_notifier import send_discord  # noqa: E402
from core.runtime.shutdown import request_shutdown, is_shutdown_requested  # noqa: E402
from risk.levels import validate_risk_coverage  # noqa: E402
from core.log_router import StructuredLogger

event_logger = StructuredLogger()

event_logger.event(
    "SYSTEM_BOOT_TEST",
    {
        "symbol": "SYSTEM",
        "message": "Testing full event path: local -> Discord -> S3",
        "severity": "INFO",
        "test_mode": True
    }
)

logger = logging.getLogger(__name__)

# ─── GRACEFUL SHUTDOWN ────────────────────────────────────────────────────────
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _shutdown_requested
    if _shutdown_requested:
        # Second signal → force exit
        logger.critical("[SHUTDOWN] forced exit (second signal)")
        sys.exit(1)
    _shutdown_requested = True
    request_shutdown(reason=f"signal:{signal.Signals(signum).name}" if hasattr(signal, "Signals") else f"signal:{signum}")
    logger.info("[SHUTDOWN] signal received — requesting graceful shutdown")


# ─── END GRACEFUL SHUTDOWN ────────────────────────────────────────────────────


def configure_logging() -> None:
    print_mode = str(getattr(config, "PRINT_MODE", "EVENT_ONLY")).upper()
    level_map = {
        "FULL_DEBUG": logging.INFO,
        "EVENT_ONLY": logging.WARNING,
        "SILENT": logging.ERROR,
    }
    base_level = level_map.get(print_mode, logging.WARNING)

    logging.basicConfig(
        level=base_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if print_mode in {"FULL_DEBUG", "EVENT_ONLY"}:
        logging.getLogger("core.loop").setLevel(logging.INFO)
        logging.getLogger("core.runtime").setLevel(logging.INFO)
        logging.getLogger("core.pipeline.dashboard").setLevel(logging.INFO)
    else:
        logging.getLogger("core.loop").setLevel(logging.ERROR)
        logging.getLogger("core.runtime").setLevel(logging.ERROR)

    if print_mode == "FULL_DEBUG":
        logging.getLogger("strategy.setup").setLevel(logging.INFO)
        logging.getLogger("core.engine").setLevel(logging.INFO)
    else:
        logging.getLogger("strategy.setup").setLevel(logging.WARNING)
        logging.getLogger("core.engine").setLevel(logging.WARNING)


def main() -> None:
    _start_time = _time.time()
    configure_logging()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Validate and freeze configuration before any runtime execution
    validate_and_freeze_config()

    # ─── DISCORD OBSERVABILITY LAYER ──────────────────────────────────
    _discord_logger = StructuredLogger()
    # Expose globally for runtime modules (read-only access)
    config._discord_logger = _discord_logger
    # ─── END DISCORD OBSERVABILITY ────────────────────────────────────

    # G4: Load and apply configuration profile (if TRADING_PROFILE env var set)
    from core.config_profile_loader import load_and_apply_profile
    _active_profile = load_and_apply_profile()

    # G1: Validate strategy registry and resolve identity
    from core.strategy_identity import resolve_strategy_identity
    _strategy_identity = resolve_strategy_identity()

    # Acquire instance lock (prevent duplicate runtime)
    from core.runtime.instance_lock import acquire_instance_lock, release_instance_lock
    if not acquire_instance_lock():
        sys.exit(1)

    # Verify all registered patterns have SL/TP rules
    validate_risk_coverage(strict=getattr(config, "STRICT_RISK_COVERAGE", False))

    # Reload trade journal dedup state (for idempotent persistence across restarts)
    try:
        from core.trade_journal import reload_persisted_ids
        reload_persisted_ids()
    except Exception:
        pass  # Journal reload failure must not block startup

    # Initialize external alerting system
    try:
        from core.alerting import initialize_alerting
        initialize_alerting()
    except Exception:
        pass  # Alerting init failure must not block startup

    mt5_owned = getattr(config, "MT5_CENTRALISED_INIT", True)
    _mt5_initialized = False

    if mt5_owned:
        import MetaTrader5 as mt5
        # Try explicit path first (handles multiple terminal installations)
        if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
            # Fallback: try default path
            if not mt5.initialize():
                logger.critical("[MT5_LIFECYCLE] initialize failed: %s", mt5.last_error())
                sys.exit(1)
        _mt5_initialized = True
        logger.info("[MT5_LIFECYCLE] initialized (centralised owner: main.py)")
        logger.info("[MT5_LIFECYCLE] version=%s terminal=%s", mt5.version(), mt5.terminal_info())

        # Startup validation (single source of truth)
        validate_account()

    # I5: Run startup self-test (fail-fast pre-flight check)
    if mt5_owned and _mt5_initialized and not config.REPLAY_MODE:
        from core.startup_self_test import run_startup_self_test
        run_startup_self_test()

    try:
        symbols = getattr(config, "CANONICAL_SYMBOLS", None) or getattr(config, "SYMBOLS", [])
        if not symbols:
            logger.critical("[CONFIG_ERROR] CANONICAL_SYMBOLS/SYMBOLS is empty — cannot start execution")
            if mt5_owned and _mt5_initialized:
                import MetaTrader5 as mt5
                mt5.shutdown()
            sys.exit(1)
        scanner_enabled = getattr(config, "MULTI_SYMBOL_SCANNER_ENABLED", False)

        # ─── CONFIG SNAPSHOT ──────────────────────────────────────────
        _mode_label = "replay" if config.REPLAY_MODE else "live"
        _scanner_label = "scanner" if scanner_enabled else "legacy"
        logger.info(
            "[CONFIG_SNAPSHOT] mode=%s_%s | symbols=%d | timeframe=M%d | lot=%.2f | rr=%.1f | "
            "cooldown=%ds | max_positions=%d | trade_mgmt=%s | dry_run=%s",
            _mode_label, _scanner_label, len(symbols),
            getattr(config, "TIMEFRAME", 5),
            getattr(config, "FIXED_LOT", 0.01),
            getattr(config, "BASE_RR", 2.0),
            int(getattr(config, "COOLDOWN_SECONDS", 300)),
            getattr(config, "MAX_OPEN_POSITIONS", 1),
            str(getattr(config, "TRADE_MANAGEMENT_ENABLED", True)),
            str(getattr(config, "DRY_RUN_EXECUTION_LOGS", True)),
        )
        # ─── END CONFIG SNAPSHOT ──────────────────────────────────────

        _startup_elapsed = _time.time() - _start_time
        logger.info(
            "[STARTUP_COMPLETE] mode=%s_%s | symbols=%d | startup_time=%.2fs | ts=%s",
            _mode_label, _scanner_label, len(symbols), _startup_elapsed,
            _time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Discord: system startup notification
        _discord_logger.event("SYSTEM_STARTUP", {
            "mode": f"{_mode_label}_{_scanner_label}",
            "symbols": len(symbols),
            "startup_time_s": round(_startup_elapsed, 2),
        })

        # Shadow EV Monitor: startup notification
        try:
            from core.research_assessment.promotion_monitor import emit_startup_notification
            emit_startup_notification()
        except Exception:
            pass  # Research monitor startup must never block trading

        # E2: Write STARTING heartbeat after all validation passes
        try:
            from core.heartbeat import write_heartbeat, STATUS_STARTING
            write_heartbeat(status=STATUS_STARTING, symbols=len(symbols))
        except Exception:
            pass

        if scanner_enabled and config.REPLAY_MODE:
            # Multi-symbol scanner: all symbols in one loop
            logger.info("[ROUTING] mode=replay_scanner | function=run_replay_scanner | symbols=%d", len(symbols))
            run_replay_scanner(symbols=symbols)
        elif scanner_enabled and not config.REPLAY_MODE:
            # Multi-symbol live scanner: all symbols in one loop
            logger.info("[ROUTING] mode=live_scanner | function=run_live_scanner | symbols=%d", len(symbols))
            run_live_scanner(symbols=symbols)
        else:
            # Legacy: per-symbol sequential execution
            _legacy_mode = "replay" if config.REPLAY_MODE else "live"
            _legacy_fn = "run_replay" if config.REPLAY_MODE else "run_live"
            logger.info("[ROUTING] mode=legacy_%s | function=%s | symbols=%d | sequential=True", _legacy_mode, _legacy_fn, len(symbols))
            for symbol in symbols:
                if _shutdown_requested or is_shutdown_requested():
                    logger.info("[SHUTDOWN] stopping symbol loop — shutdown requested")
                    break
                try:
                    if config.REPLAY_MODE:
                        run_replay(symbol=symbol)
                    else:
                        run_live(symbol=symbol)
                except Exception as sym_exc:
                    logger.error("[SYMBOL_ERROR] symbol=%s error=%s — skipping to next", symbol, sym_exc)
                    try:
                        _discord_logger.event("ERROR", {"location": "main:symbol_loop", "error_type": type(sym_exc).__name__, "message": str(sym_exc)[:200], "details": {"symbol": symbol}})
                    except Exception:
                        pass
                    continue
    finally:
        # I4: Record daily equity snapshot before shutdown
        try:
            from core.equity_curve_tracker import record_daily_equity_snapshot
            record_daily_equity_snapshot()
        except Exception:
            pass

        # E2: Write SHUTDOWN heartbeat (watchdog will not restart)
        try:
            from core.heartbeat import write_heartbeat, STATUS_SHUTDOWN
            write_heartbeat(status=STATUS_SHUTDOWN)
        except Exception:
            pass

        if mt5_owned and _mt5_initialized:
            import MetaTrader5 as mt5
            mt5.shutdown()
            logger.info("[MT5_LIFECYCLE] shutdown (centralised owner: main.py)")
        release_instance_lock()
        _uptime = int(_time.time() - _start_time)
        logger.info("[SHUTDOWN] process exit complete | uptime=%ds (%dm %ds)", _uptime, _uptime // 60, _uptime % 60)

        # Discord: system shutdown notification
        _discord_logger.event("SYSTEM_SHUTDOWN", {
            "uptime_s": _uptime,
            "uptime_human": f"{_uptime // 60}m {_uptime % 60}s",
        })


if __name__ == "__main__":
    main()