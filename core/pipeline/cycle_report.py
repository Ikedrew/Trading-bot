"""
Cycle Report — End-of-cycle reporting and summary emission.

Emits pipeline trace summaries, cycle statistics, and throttled market
snapshots. Purely observational — never influences trading pipeline.

This module OWNS:
    - Cycle summary creation (pipeline trace)
    - Report emission (console print, log_cycle_summary_simple)
    - Market snapshot emission (throttled Discord)
    - Reporting-only calculations (dominant drop stage, latency)

This module does NOT own:
    - Trade decisions
    - Candidate filtering or modification
    - Strategy selection
    - Risk decisions
    - Execution
    - Runtime control (sleep, continue, break)
    - DecisionFunnel ownership (only reads)

Design: pure reporting — read-only on all inputs, never raises to caller.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any

from core.event_bus import log_cycle_summary_simple

logger = logging.getLogger(__name__)


def emit_cycle_report(
    *,
    cycle_id: int,
    cycle_start: float,
    n_symbols: int,
    cycle_drops: list[tuple[str, str, str]],
    cycle_had_trade: bool,
    this_cycle_new_bars: list[str],
    filter_hits: dict[str, int],
    states: list[Any],
    htf_context: Any,
    config: Any,
    # ─── LIFECYCLE DATA (Phase 5 — optional for backward compatibility) ─
    cycle_had_execute_decision: bool = False,
    cycle_had_execution_attempt: bool = False,
    cycle_had_fill: bool = False,
    cycle_execute_symbols: list[str] | None = None,
    cycle_execution_symbols: list[str] | None = None,
    cycle_filled_symbols: list[str] | None = None,
    cycle_blocked_symbols: list[str] | None = None,
    cycle_rejected_symbols: list[str] | None = None,
    cycle_decision_drops: list[tuple[str, str, str]] | None = None,
    cycle_execution_drops: list[tuple[str, str, str]] | None = None,
    cycle_broker_drops: list[tuple[str, Any, str]] | None = None,
) -> None:
    """
    Emit end-of-cycle reporting. Never raises. Returns None.

    Performs:
        1. New-bar trigger logging
        2. Pipeline lifecycle funnel summary (console print)
        3. log_cycle_summary_simple call
        4. Market snapshot (throttled: every 25 cycles via Discord)
    """
    try:
        _emit_cycle_summary(
            cycle_id=cycle_id,
            cycle_start=cycle_start,
            n_symbols=n_symbols,
            cycle_drops=cycle_drops,
            cycle_had_trade=cycle_had_trade,
            this_cycle_new_bars=this_cycle_new_bars,
            cycle_had_execute_decision=cycle_had_execute_decision,
            cycle_had_execution_attempt=cycle_had_execution_attempt,
            cycle_had_fill=cycle_had_fill,
            cycle_execute_symbols=cycle_execute_symbols or [],
            cycle_execution_symbols=cycle_execution_symbols or [],
            cycle_filled_symbols=cycle_filled_symbols or [],
            cycle_blocked_symbols=cycle_blocked_symbols or [],
            cycle_rejected_symbols=cycle_rejected_symbols or [],
            cycle_decision_drops=cycle_decision_drops or [],
            cycle_execution_drops=cycle_execution_drops or [],
            cycle_broker_drops=cycle_broker_drops or [],
        )
    except Exception:
        pass

    try:
        _emit_market_snapshot(
            cycle_id=cycle_id,
            cycle_drops=cycle_drops,
            filter_hits=filter_hits,
            states=states,
            htf_context=htf_context,
            config=config,
        )
    except Exception:
        pass


def _emit_cycle_summary(
    *,
    cycle_id: int,
    cycle_start: float,
    n_symbols: int,
    cycle_drops: list[tuple[str, str, str]],
    cycle_had_trade: bool,
    this_cycle_new_bars: list[str],
    cycle_had_execute_decision: bool = False,
    cycle_had_execution_attempt: bool = False,
    cycle_had_fill: bool = False,
    cycle_execute_symbols: list[str] | None = None,
    cycle_execution_symbols: list[str] | None = None,
    cycle_filled_symbols: list[str] | None = None,
    cycle_blocked_symbols: list[str] | None = None,
    cycle_rejected_symbols: list[str] | None = None,
    cycle_decision_drops: list[tuple[str, str, str]] | None = None,
    cycle_execution_drops: list[tuple[str, str, str]] | None = None,
    cycle_broker_drops: list[tuple[str, Any, str]] | None = None,
) -> None:
    """Emit cycle summary with lifecycle funnel."""
    # New-bar trigger count for this cycle
    if this_cycle_new_bars:
        logger.info(
            "[CYCLE_BARS] cycle=%d new_bars=%d symbols=%s",
            cycle_id, len(this_cycle_new_bars), this_cycle_new_bars,
        )

    _exec_syms = cycle_execute_symbols or []
    _attempt_syms = cycle_execution_symbols or []
    _filled_syms = cycle_filled_symbols or []
    _blocked_syms = cycle_blocked_symbols or []
    _rejected_syms = cycle_rejected_symbols or []
    _dec_drops = cycle_decision_drops or []
    _exec_drops = cycle_execution_drops or []
    _broker_drops = cycle_broker_drops or []

    # Only print if something happened
    has_activity = cycle_drops or cycle_had_trade or _exec_syms or _dec_drops

    if has_activity:
        print(f"\n{'='*50} CYCLE {cycle_id} PIPELINE TRACE {'='*50}")

        # ─── DECISION LAYER ───────────────────────────────────────
        if _exec_syms:
            print(f"  Decision Layer:")
            print(f"    ✅ EXECUTE decisions: {len(_exec_syms)} ({', '.join(_exec_syms)})")
        if _dec_drops:
            if not _exec_syms:
                print(f"  Decision Layer:")
            for _sym, _stg, _rsn in _dec_drops:
                print(f"    → {_sym:12s} | {_rsn[:60]}")

        # ─── EXECUTION LAYER ─────────────────────────────────────
        if _attempt_syms or _blocked_syms:
            print(f"  Execution Layer:")
            if _attempt_syms:
                print(f"    ✅ Execution attempts: {len(_attempt_syms)} ({', '.join(_attempt_syms)})")
            if _blocked_syms:
                for _sym, _guard, _rsn in _exec_drops:
                    print(f"    ❌ {_sym:12s} blocked by {_guard} | {_rsn[:50]}")

        # ─── BROKER LAYER ─────────────────────────────────────────
        if _filled_syms or _rejected_syms:
            print(f"  Broker Layer:")
            if _filled_syms:
                print(f"    ✅ Confirmed fills: {len(_filled_syms)} ({', '.join(_filled_syms)})")
            if _rejected_syms:
                for _sym, _retcode, _rsn in _broker_drops:
                    print(f"    ❌ {_sym:12s} rejected (retcode={_retcode})")

        # ─── SUMMARY LINE ─────────────────────────────────────────
        if _filled_syms:
            print(f"\n  ✅ TRADE FILLED: {', '.join(_filled_syms)}")
        elif _attempt_syms and not _filled_syms:
            print(f"\n  ⚠️ EXECUTION ATTEMPTED BUT NO FILL")
        elif _exec_syms and not _attempt_syms:
            print(f"\n  ⚠️ EXECUTE DECISION BUT BLOCKED BY GUARDS")
        elif _dec_drops and not _exec_syms:
            _stage_counts = Counter(stg for _, stg, _ in _dec_drops)
            _top_stage, _top_count = _stage_counts.most_common(1)[0]
            print(f"\n  ❌ NO TRADE | dominant drop: {_top_stage} ({_top_count}/{len(_dec_drops)} symbols)")

        print(f"{'='*120}\n")

    latency_ms = int((time.time() - cycle_start) * 1000)
    try:
        log_cycle_summary_simple(cycle_id, n_symbols, latency_ms)
    except Exception:
        pass


def _emit_market_snapshot(
    *,
    cycle_id: int,
    cycle_drops: list[tuple[str, str, str]],
    filter_hits: dict[str, int],
    states: list[Any],
    htf_context: Any,
    config: Any,
) -> None:
    """Emit throttled market snapshot via Discord (every 25 cycles)."""
    if not (cycle_id % 25 == 0 and cycle_id > 0 and cycle_drops):
        return

    _dl = getattr(config, "_discord_logger", None)
    if _dl is None:
        return

    # Use first symbol's state as representative snapshot
    _snap_sym = states[0] if states else None
    _snap_h4_bias = "UNKNOWN"
    _snap_m15_bias = "UNKNOWN"
    if htf_context is not None:
        if getattr(htf_context, "regime", None):
            _snap_h4_bias = getattr(htf_context.regime, "trend_bias", "UNKNOWN")
        if getattr(htf_context, "structure", None):
            _snap_m15_bias = f"q={htf_context.structure.quality_score:.2f}"
    _snap_m5_bias = _snap_sym.engine_state.current_bias.value if _snap_sym and _snap_sym.engine_state.current_bias else "NONE"
    _snap_last_drop = cycle_drops[-1][1] if cycle_drops else "none"
    _snap_active_filters = sum(1 for v in filter_hits.values() if v > 0 and v != filter_hits.get("trades_executed", 0))
    _dl.event("MARKET_SNAPSHOT", {
        "symbol": _snap_sym.symbol if _snap_sym else "ALL",
        "h4_bias": _snap_h4_bias,
        "m15_bias": _snap_m15_bias,
        "m5_bias": _snap_m5_bias,
        "last_drop_stage": _snap_last_drop,
        "active_filters": _snap_active_filters,
    })
