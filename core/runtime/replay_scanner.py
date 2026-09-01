from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from core import config
from core.engine import EngineState, process_bar
from core.engine_state import validate_engine_state
from core.decision_audit import persist_decision_audit
from core.event_bus import (
    EventState,
    emit_bias_events,
    emit_setup_events,
    emit_trade_events,
    log_heartbeat,
    log_cycle_summary_simple,
    set_active_symbol,
)
from data.mt5_data import MT5DataFeed, Candle
from risk.manager import RiskManager
from risk.models import OrderIntent
from strategy.signals import Side  # optional if referenced indirectly

from core.runtime.runtime_utils import (
    _build_risk_manager,
    _build_trade_management_config,
    _closed_bar_index,
    _timeframe_seconds,
    _apply_replay_window,
)

logger = logging.getLogger(__name__)   # NOT getlogger

# ─── MULTI-SYMBOL REPLAY SCANNER ─────────────────────────────────────────────

@dataclass
class _ReplaySymbolState:
    """Per-symbol state for the multi-symbol replay scanner."""
    symbol: str
    feed: MT5DataFeed
    engine_state: EngineState
    event_state: EventState
    risk: RiskManager
    candles: list[Candle]
    start_i: int
    current_i: int  # Current replay pointer (advances independently)
    end_i: int      # Exclusive upper bound (respects replay window)
    interval_s: int
    start_time: int
    completed: bool = False


def run_replay_scanner(
    *,
    symbols: list[str] | None = None,
    on_intent: Callable[[OrderIntent], None] | None = None,
) -> None:
    """
    Multi-symbol replay scanner: processes all symbols independently in a single loop.
    Each symbol advances one bar per cycle. Symbols progress at their own pace.
    """
    symbol_list = symbols or getattr(config, "SYMBOLS", [config.SYMBOL])

    # ─── INITIALIZE PER-SYMBOL STATE ──────────────────────────────────
    states: list[_ReplaySymbolState] = []
    for sym_hint in symbol_list:
        try:
            feed = MT5DataFeed(sym_hint)
            feed.connect()
            resolved = feed.resolve_symbol()
            candles = feed.copy_rates_closed(resolved, config.TIMEFRAME, config.CANDLE_COUNT)
            min_required = max(config.SETUP_MA_PERIOD + 3, 2)
            start_i, end_i = _apply_replay_window(candles, min_required)
            interval_s = _timeframe_seconds(config.TIMEFRAME)
            start_time = candles[start_i].time if start_i < len(candles) else 0

            states.append(_ReplaySymbolState(
                symbol=resolved,
                feed=feed,
                engine_state=EngineState(),
                event_state=EventState(),
                risk=_build_risk_manager(),
                candles=candles,
                start_i=start_i,
                current_i=start_i,
                end_i=end_i,
                interval_s=interval_s,
                start_time=start_time,
            ))
            logger.info("[REPLAY_SCANNER] initialized symbol=%s bars=%d start_i=%d", resolved, len(candles), min_required)
        except Exception as exc:
            logger.error("[REPLAY_SCANNER] failed to init symbol=%s: %s — skipping", sym_hint, exc)
            continue

    if not states:
        logger.critical("[REPLAY_SCANNER] no symbols initialized — aborting")
        return

    logger.info("[REPLAY_SCANNER] ENGINE_START | symbols=%d | mode=REPLAY_SCANNER", len(states))

    # ─── SCANNER LOOP ─────────────────────────────────────────────────
    cycle_id = 0
    try:
        while True:
            # Check if all symbols are completed
            active_states = [s for s in states if not s.completed]
            if not active_states:
                break

            cycle_id += 1

            # Process one bar per symbol per cycle
            for sym_state in active_states:
                try:
                    set_active_symbol(sym_state.symbol)
                    closed_i = sym_state.current_i

                    # Check if this symbol has more bars
                    if closed_i >= sym_state.end_i:
                        sym_state.completed = True
                        logger.info("[REPLAY_SCANNER] %s completed | bars_processed=%d",
                                    sym_state.symbol, closed_i - sym_state.start_i)
                        continue

                    # Compute simulated time for this bar
                    sim_time_s = sym_state.start_time + ((closed_i - sym_state.start_i) * sym_state.interval_s)
                    closed_time = int(sim_time_s)
                    bid = sym_state.candles[closed_i].close
                    ask = sym_state.candles[closed_i].close

                    # Validate EngineState before pipeline execution
                    validate_engine_state(
                        sym_state.engine_state,
                        symbol=sym_state.symbol,
                        cycle_id=cycle_id,
                        strict=getattr(config, "ENGINE_STATE_STRICT_VALIDATION", False),
                    )

                    # Run strategy pipeline
                    unified = process_bar(
                        candles=sym_state.candles,
                        closed_i=closed_i,
                        symbol=sym_state.symbol,
                        risk=sym_state.risk,
                        state=sym_state.engine_state,
                        bid=bid,
                        ask=ask,
                        now_s=sim_time_s,
                        config=config,
                    )
                    decision = unified.decision

                    bias_value = decision.bias.value if decision.bias is not None else "NONE"
                    pattern_value = ",".join(decision.patterns) if decision.patterns else "NONE"
                    score_value = int(decision.score)

                    # Emit events
                    emit_bias_events(
                        sym_state.event_state,
                        candle_i=closed_i,
                        candle_time=closed_time,
                        bias_value=bias_value,
                        bias_phase=decision.bias_phase,
                        bias_validation_score=decision.bias_validation_score,
                        structure_ok=decision.structure_ok,
                    )
                    emit_setup_events(
                        sym_state.event_state,
                        candle_i=closed_i,
                        candle_time=closed_time,
                        bias_value=bias_value,
                        pattern_value=pattern_value,
                        score_value=score_value,
                        decision_reason=decision.reason,
                        should_trade=decision.should_trade,
                    )
                    emit_trade_events(
                        candle_i=closed_i,
                        candle_time=closed_time,
                        bias_value=bias_value,
                        score_value=score_value,
                        should_trade=decision.should_trade,
                        execution_ok=None,
                    )

                    if decision.should_trade and decision.intent is not None:
                        persist_decision_audit(
                            symbol=sym_state.symbol, cycle_id=cycle_id, decision=unified,
                            engine_state=sym_state.engine_state, candles=sym_state.candles,
                            closed_i=closed_i, runtime_mode="REPLAY_SCANNER",
                        )
                        sym_state.engine_state.last_successful_open_mono = sim_time_s
                        if on_intent is not None:
                            on_intent(decision.intent)

                    # Advance this symbol's pointer
                    sym_state.current_i += 1

                except Exception as exc:
                    logger.error("[REPLAY_SCANNER] %s error at bar %d: %s — skipping bar",
                                 sym_state.symbol, sym_state.current_i, exc)
                    sym_state.current_i += 1  # Skip broken bar, continue

            # ─── CYCLE OBSERVABILITY ──────────────────────────────────
            try:
                log_heartbeat(cycle_id, float(cycle_id), "MULTI", "REPLAY_SCANNER")
                log_cycle_summary_simple(cycle_id, len(active_states), 0)
            except Exception:
                pass
            # ─── END CYCLE OBSERVABILITY ──────────────────────────────

        logger.info("[REPLAY_SCANNER] COMPLETE | cycles=%d | symbols=%d", cycle_id, len(states))

    finally:
        for s in states:
            try:
                s.feed.disconnect()
            except Exception:
                pass