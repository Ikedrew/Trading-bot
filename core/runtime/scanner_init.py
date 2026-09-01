"""
Scanner Initialization — Symbol resolution and per-symbol state creation.

Handles all startup-only logic: resolving symbols, validating their existence
in the MT5 terminal, creating per-symbol state objects, and recovering open
positions from the broker.

This module OWNS:
    - Symbol list resolution (canonical → broker)
    - Symbol universe visibility diagnostic
    - Per-symbol state creation (_LiveSymbolState)
    - Feed connection and symbol resolution
    - Trade manager creation
    - MTF cache creation
    - Startup position recovery

This module does NOT own:
    - Runtime loop
    - Trading decisions
    - Execution
    - Dependency wiring beyond state creation
    - Shutdown handling

Design: initialization only — called once at startup, returns list of states.
"""

from __future__ import annotations

import logging
from typing import Any

from core import config
from core.engine import EngineState
from core.event_bus import EventState, TradeLifecycleLogger
from core.state_persistence import load_engine_state
from core.stale_monitor import StaleDataMonitor
from core.trade_management import TradeStateManager
from data.mt5_data import MT5DataFeed

from core.runtime.runtime_utils import (
    _build_risk_manager,
    _build_trade_management_config,
)

logger = logging.getLogger(__name__)


def initialize_symbol_states(
    *,
    symbols: list[str] | None,
    execution: Any,
) -> list[Any]:
    """
    Resolve symbols and create per-symbol state objects.

    Args:
        symbols: Optional explicit symbol list (overrides config).
        execution: MT5Execution instance (passed to TradeStateManager).

    Returns:
        List of _LiveSymbolState objects. May be empty if all symbols failed.
    """
    from core.runtime.live_scanner import _LiveSymbolState

    symbol_list = symbols or getattr(config, "CANONICAL_SYMBOLS", None) or getattr(config, "SYMBOLS", [])

    # ─── SYMBOL RESOLUTION (canonical → broker) ──────────────────────
    _canonical_list = getattr(config, "CANONICAL_SYMBOLS", None)
    if _canonical_list and not symbols:
        try:
            from core.symbol_resolver import resolve_all
            _symbol_map = resolve_all(_canonical_list, fail_mode="skip")
            if _symbol_map:
                symbol_list = list(_symbol_map.values())
                logger.info(
                    "[SYMBOL_RESOLUTION] resolved %d/%d canonical → broker: %s",
                    len(_symbol_map), len(_canonical_list),
                    {k: v for k, v in _symbol_map.items()},
                )
            else:
                logger.warning("[SYMBOL_RESOLUTION] no symbols resolved — falling back to config.SYMBOLS")
                symbol_list = getattr(config, "SYMBOLS", [])
        except Exception as _res_exc:
            logger.warning("[SYMBOL_RESOLUTION] resolver failed: %s — using config.SYMBOLS as-is", _res_exc)
            symbol_list = getattr(config, "SYMBOLS", [])
    # ─── END SYMBOL RESOLUTION ────────────────────────────────────────

    # ─── SYMBOL UNIVERSE VISIBILITY (diagnostic) ─────────────────────
    try:
        import MetaTrader5 as _mt5_diag
        _all_mt5_symbols = _mt5_diag.symbols_get()
        if _all_mt5_symbols:
            _mt5_names = {s.name for s in _all_mt5_symbols}
            logger.info("[MT5_SYMBOLS_COUNT] total_available=%d", len(_mt5_names))
            for sym_hint in symbol_list:
                _exists = sym_hint in _mt5_names
                if not _exists:
                    logger.warning("[SYMBOL_EXISTS] %s = NOT_FOUND in MT5 terminal", sym_hint)
                else:
                    logger.info("[SYMBOL_EXISTS] %s = FOUND", sym_hint)
        else:
            logger.warning("[MT5_SYMBOLS_COUNT] symbols_get() returned None/empty — MT5 may not be connected")
    except Exception as _diag_exc:
        logger.warning("[MT5_SYMBOLS_DIAG] diagnostic failed: %s", _diag_exc)
    # ─── END SYMBOL UNIVERSE VISIBILITY ───────────────────────────────

    # ─── CREATE PER-SYMBOL STATE ──────────────────────────────────────
    states: list[_LiveSymbolState] = []

    for sym_hint in symbol_list:
        try:
            # Force symbol activation in Market Watch before resolution
            try:
                import MetaTrader5 as _mt5_sel
                _mt5_sel.symbol_select(sym_hint, True)
            except Exception:
                pass

            feed = MT5DataFeed(sym_hint)
            feed.connect()
            resolved = feed.resolve_symbol()
            logger.info("[SYMBOL_INIT] hint=%s → resolved=%s", sym_hint, resolved)

            tm: TradeStateManager | None = None
            if getattr(config, "TRADE_MANAGEMENT_ENABLED", True):
                tm = TradeStateManager(
                    _build_trade_management_config(),
                    listener=TradeLifecycleLogger(),
                    execution=execution,
                )

            # MTF: create TimeframeCache if enabled
            _tf_cache = None
            if getattr(config, "MTF_ENABLED", False):
                from core.timeframes.cache import TimeframeCache
                _tf_cache = TimeframeCache(symbol=resolved, feed=feed, config=config)

            # Market Context: create builder if enabled
            _mc_builder = None
            if getattr(config, "MARKET_CONTEXT_ENABLED", False):
                try:
                    from core.market_context.builder import MarketContextBuilder
                    _mc_builder = MarketContextBuilder(symbol=resolved)
                except Exception:
                    pass  # Market context unavailable — proceed without

            states.append(_LiveSymbolState(
                symbol=resolved,
                feed=feed,
                engine_state=load_engine_state(resolved) or EngineState(),
                event_state=EventState(),
                risk=_build_risk_manager(),
                trade_manager=tm,
                stale_monitor=StaleDataMonitor(resolved, config),
                tf_cache=_tf_cache,
                market_context_builder=_mc_builder,
            ))

            # D3: Recover open broker positions into TradeStateManager
            if tm is not None:
                try:
                    from core.runtime.startup_recovery import recover_positions_on_startup
                    recover_positions_on_startup(
                        trade_manager=tm,
                        symbol=resolved,
                        magic=config.BOT_MAGIC,
                    )
                except Exception as _rec_exc:
                    logger.warning("[STARTUP_RECOVERY_ERROR] symbol=%s error=%s", resolved, _rec_exc)

            logger.info("[LIVE_SCANNER] initialized symbol=%s", resolved)
        except Exception as exc:
            logger.error(
                "[SYMBOL_INIT_FAIL] hint=%s → %s: %s (type=%s)",
                sym_hint, type(exc).__name__, exc, type(exc).__qualname__,
            )
            continue

    return states
