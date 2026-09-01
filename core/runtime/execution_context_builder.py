"""
Execution Context Builder — Per-cycle environment snapshot capture.

Captures the runtime environment snapshot on every new bar for forensic
reconstruction. Generates a correlation ID and persists the execution context.

This module OWNS:
    - Per-cycle correlation ID generation
    - Session classification (LONDON/NY/ASIA/OFF_SESSION)
    - Spread/ATR ratio calculation
    - Execution context assembly and persistence
    - Returning the cycle correlation ID for downstream use

This module does NOT own:
    - Trading decisions
    - Risk decisions
    - Guard logic
    - Execution
    - Strategy logic
    - Runtime loop control
    - Decision ledger writes

Design: observational snapshot — never raises to caller, returns correlation_id.
"""

from __future__ import annotations

import logging
import time as _time_mod
from typing import Any

from core.correlation import generate_correlation_id
from core.execution_context import build_execution_context, persist_execution_context

logger = logging.getLogger(__name__)


def build_cycle_context(
    *,
    cycle_id: int,
    cycle_start: float,
    sym_state: Any,
    closed_time: int,
    bid: float,
    ask: float,
    tick_time: float,
    feed_state: str,
    dd_result: Any,
    dl_result: Any,
) -> str:
    """
    Build and persist per-cycle execution context snapshot.

    Generates a correlation ID, computes environment metrics, assembles
    the execution context, and persists it. Never raises.

    Args:
        cycle_id: Current cycle number.
        cycle_start: Cycle start timestamp (time.time()).
        sym_state: Per-symbol state object.
        closed_time: Bar close timestamp (raw broker-local).
        bid: Current bid price.
        ask: Current ask price.
        tick_time: Last tick timestamp.
        feed_state: Feed health classification string.
        dd_result: Drawdown guard result (for drawdown_pct).
        dl_result: Daily loss guard result (for daily_loss_pct).

    Returns:
        Correlation ID string (empty string on failure).
    """
    _cor_id_cycle = ""
    try:
        _spread_cycle = ask - bid if (bid > 0 and ask > 0) else 0.0
        _atr_cycle = getattr(sym_state.engine_state, "volatility_filter", 0.0) or 0.0
        _spread_atr_cycle = (_spread_cycle / _atr_cycle) if _atr_cycle > 0 else 0.0
        _cycle_latency_ec = int((_time_mod.time() - cycle_start) * 1000)

        _hour_ec = _time_mod.gmtime(int(closed_time)).tm_hour
        if 7 <= _hour_ec < 12:
            _session_ec = "LONDON"
        elif 12 <= _hour_ec < 17:
            _session_ec = "NY"
        elif 0 <= _hour_ec < 7:
            _session_ec = "ASIA"
        else:
            _session_ec = "OFF_SESSION"

        _cor_id_cycle = generate_correlation_id(
            cycle_id=cycle_id,
            symbol=sym_state.symbol,
            timestamp=float(closed_time),
        )
        _exec_ctx_cycle = build_execution_context(
            correlation_id=_cor_id_cycle,
            symbol=sym_state.symbol,
            timestamp_utc=float(closed_time),
            bid=bid,
            ask=ask,
            session_state=_session_ec,
            spread_atr_ratio=round(_spread_atr_cycle, 6),
            latency_ms=_cycle_latency_ec,
            feed_state=feed_state if feed_state else "HEALTHY",
            tick_age_ms=int((_time_mod.time() - tick_time) * 1000) if tick_time else 0,
            bars_since_last_gap=sym_state.iterations,
            drawdown_pct=getattr(dd_result, "current_drawdown_pct", 0.0) or 0.0,
            daily_loss_pct=getattr(dl_result, "current_loss_pct", 0.0) or 0.0,
            open_positions=len(sym_state.trade_manager.positions_open()) if sym_state.trade_manager else 0,
            correlation_exposure=0.0,
            last_candle_ts=int(closed_time) * 1000,
            last_feature_ts=0,
        )
        _ctx_record = (
            dict(_exec_ctx_cycle) if isinstance(_exec_ctx_cycle, dict)
            else _exec_ctx_cycle.to_dict()
        )
        # Phase 3 Step 3: observation-level identity + cycle/bar linkage.
        # The canonical root is NOT set here: this snapshot is captured
        # pre-engine, before any pattern can qualify (the field legitimately
        # remains ""). The canonical relationship is established downstream by
        # decision/assessment rows sharing this correlation_id and entity_id.
        try:
            _ts_int = int(_ctx_record.get("timestamp_utc") or closed_time)
            _ctx_record["bar_time"] = _ts_int
            _ctx_record.setdefault("entity_id", f"{sym_state.symbol}_{_ts_int}")
            _ctx_record["cycle_id"] = cycle_id
        except Exception:
            pass
        persist_execution_context(_ctx_record)
    except Exception:
        pass  # Execution context must never block trading
    return _cor_id_cycle
