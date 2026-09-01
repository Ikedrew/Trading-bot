from __future__ import annotations
import logging
import time
from typing import Callable
from core import config
from core.engine import EngineState, process_bar
from core.engine_state import validate_engine_state
from core.decision_audit import persist_decision_audit
from core.event_bus import (
    EventState,
    emit_event,
    emit_bias_events,
    emit_setup_events,
    emit_trade_events,
    log_heartbeat,
    log_cycle_summary,
    set_active_symbol,
    is_full_debug_mode,
)
from data.mt5_data import MT5DataFeed
from risk.manager import RiskManager
from risk.models import OrderIntent

from core.runtime.runtime_utils import (
    _build_risk_manager,
    _build_trade_management_config,
    _closed_bar_index,
    _timeframe_seconds,
    _apply_replay_window,
)

logger = logging.getLogger(__name__)

# ─── REPLAY MODE ──────────────────────────────────────────────────────────────

def run_replay(
    *,
    symbol: str | None = None,
    on_intent: Callable[[OrderIntent], None] | None = None,
) -> None:
    symbol_hint = symbol if symbol is not None else config.SYMBOL
    feed = MT5DataFeed(symbol_hint)
    risk = _build_risk_manager()

    feed.connect()
    try:
        symbol = feed.resolve_symbol()
        set_active_symbol(symbol)
        state = EngineState()
        event_state = EventState()

        candles = feed.copy_rates_closed(symbol, config.TIMEFRAME, config.CANDLE_COUNT)
        min_required = max(config.SETUP_MA_PERIOD + 3, 2)
        start_i, end_i = _apply_replay_window(candles, min_required)
        interval_s = _timeframe_seconds(config.TIMEFRAME)
        start_time = candles[start_i].time if start_i < len(candles) else 0
        emit_event("ENGINE_START", start_i, start_time, f"mode=REPLAY | interval_s={interval_s} | bars={end_i - start_i}")

        for closed_i in range(start_i, end_i):
            sim_time_s = start_time + ((closed_i - start_i) * interval_s)
            closed_time = int(sim_time_s)
            bid = candles[closed_i].close
            ask = candles[closed_i].close

            # Validate EngineState before pipeline execution
            validate_engine_state(
                state,
                symbol=symbol,
                cycle_id=closed_i - start_i + 1,
                strict=getattr(config, "ENGINE_STATE_STRICT_VALIDATION", False),
            )

            unified = process_bar(
                candles=candles,
                closed_i=closed_i,
                symbol=symbol,
                risk=risk,
                state=state,
                bid=bid,
                ask=ask,
                now_s=sim_time_s,
                config=config,
            )
            decision = unified.decision

            bias_value = decision.bias.value if decision.bias is not None else "NONE"
            pattern_value = ",".join(decision.patterns) if decision.patterns else "NONE"
            score_value = int(decision.score)

            if is_full_debug_mode() and getattr(config, "FULL_DEBUG_REPLAY", False):
                emit_event(
                    "FULL_DEBUG",
                    closed_i,
                    closed_time,
                    (
                        f"bias={bias_value} | pattern={pattern_value} | score={score_value} "
                        f"| decision={decision.reason} | bias_age_seconds={decision.bias_age_seconds:.1f} "
                        f"| bias_window_phase={decision.bias_window_phase} "
                        f"| confluence_threshold_dynamic={decision.confluence_threshold_dynamic:.2f}"
                    ),
                )

            emit_bias_events(
                event_state,
                candle_i=closed_i,
                candle_time=closed_time,
                bias_value=bias_value,
                bias_phase=decision.bias_phase,
                bias_validation_score=decision.bias_validation_score,
                structure_ok=decision.structure_ok,
            )
            emit_setup_events(
                event_state,
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

            if decision.should_trade and on_intent is not None and decision.intent is not None:
                persist_decision_audit(
                    symbol=symbol, cycle_id=closed_i - start_i + 1, decision=unified,
                    engine_state=state, candles=candles, closed_i=closed_i, runtime_mode="REPLAY",
                )
                state.last_successful_open_mono = sim_time_s
                on_intent(decision.intent)
            elif decision.should_trade and decision.intent is not None:
                persist_decision_audit(
                    symbol=symbol, cycle_id=closed_i - start_i + 1, decision=unified,
                    engine_state=state, candles=candles, closed_i=closed_i, runtime_mode="REPLAY",
                )
                state.last_successful_open_mono = sim_time_s

            # ─── CYCLE OBSERVABILITY ──────────────────────────────────
            try:
                _cycle_id = closed_i - start_i + 1
                log_heartbeat(_cycle_id, sim_time_s, symbol, "REPLAY")
                _decision_label = "TRADE" if decision.should_trade else "NO_TRADE"
                log_cycle_summary(_cycle_id, "OK", "REPLAY", _decision_label, 0, decision.reason)
            except Exception:
                pass
            # ─── END CYCLE OBSERVABILITY ──────────────────────────────
    finally:
        feed.disconnect()