"""
Visibility Layer — Dual-trace observability system (Design vs Reality).

Captures BOTH:
    1. INTENDED ARCHITECTURE TRACE — what the system is designed to do per room
    2. ACTUAL ENGINE TRACE — what the engine actually produced

This enables direct comparison: DESIGN vs REALITY gap analysis.

STRICT RULES:
    - NEVER fabricates missing data
    - NEVER assumes room outputs exist if not present
    - NEVER modifies engine behaviour
    - NEVER simulates pipeline completion
    - Only observes and mirrors

Architecture rooms (reference model):
    ROOM 1: MARKET_DATA (pattern detection + strategy classification)
    ROOM 2: QUANT_ENGINE (dual scoring + market state + EV)
    ROOM 3: DECISION_ENGINE (RR + policy + pool + ranking + top-k + bias FSM)
    ROOM 4: EXECUTION_ENGINE (final decision)
    ROOM 5: LOGGING_ENGINE (mirror layer)

Design: purely passive, deterministic, no side effects.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any


# ─── LOG OUTPUT PATH ──────────────────────────────────────────────────────────

_LOG_PATH = Path("logs/visibility_trace.jsonl")
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


# ─── MAIN TRACE FUNCTION ─────────────────────────────────────────────────────

def emit_visibility_trace(
    *,
    symbol: str,
    cycle_id: int,
    bar_time: float,
    engine_result: dict[str, Any],
    bias_phase: str = "?",
) -> None:
    """
    Emit a full dual-layer visibility trace for one symbol cycle.

    Captures intended architecture flow AND actual engine output.
    Highlights divergence between design and reality.

    Args:
        symbol: Trading pair
        cycle_id: Current cycle number
        bar_time: MT5 bar timestamp (entity origin)
        engine_result: Raw output dict from run_new_engine()
        bias_phase: Current bias FSM phase

    This function is PURELY OBSERVATIONAL. try/except: pass at call site.
    """
    try:
        now = _time.time()
        entity_id = f"{symbol}_{int(bar_time)}"
        action = engine_result.get("action", "UNKNOWN")
        reason = engine_result.get("reason", "")

        # ─── INTENDED ARCHITECTURE TRACE (DESIGN VIEW) ────────────────
        intended = _build_intended_trace(engine_result, bias_phase)

        # ─── ACTUAL ENGINE TRACE (REALITY VIEW) ───────────────────────
        actual = _build_actual_trace(engine_result, reason)

        # ─── DIAGNOSTICS ──────────────────────────────────────────────
        diagnostics = _compute_diagnostics(intended, actual, engine_result)

        # ─── BUILD FULL EVENT ─────────────────────────────────────────
        event = {
            "event_type": "VISIBILITY_TRACE",
            "entity_id": entity_id,
            "symbol": symbol,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now)),
            "cycle_id": cycle_id,
            "bar_time": int(bar_time),

            "INTENDED_ARCHITECTURE_TRACE": intended,
            "ACTUAL_ENGINE_TRACE": actual,
            "DIAGNOSTICS": diagnostics,
        }

        # ─── PERSIST ──────────────────────────────────────────────────
        _persist(event)

        # ─── DISCORD (only on gaps) ───────────────────────────────────
        if diagnostics.get("design_vs_reality_gap"):
            _emit_gap_alert(symbol, event)

    except Exception:
        pass  # Visibility layer must never affect execution


# ─── INTENDED TRACE BUILDER ───────────────────────────────────────────────────

def _build_intended_trace(result: dict[str, Any], bias_phase: str) -> dict[str, Any]:
    """Build what SHOULD exist in each room based on architecture spec."""
    return {
        "room_1": {
            "1.1_pattern_detection": "expected_present",
            "1.2_strategy_classification": "expected_present",
        },
        "room_2": {
            "2.1_dual_scoring": "expected_present",
            "2.2_market_state": "expected_present",
            "2.3_expected_value": "expected_present",
        },
        "room_3": {
            "3.1_rr_derivation": "expected_present",
            "3.2_execution_policy_gates": "expected_present",
            "3.3_opportunity_pool": "expected_present",
            "3.4_ranking_engine": "expected_present",
            "3.5_top_k_selection": "expected_present",
            "3.6_bias_fsm": "expected_present",
        },
        "room_4": {
            "4.1_execution_decision": "expected_present",
        },
        "room_5": {
            "5.1_structured_logging": "expected_present",
        },
    }


# ─── ACTUAL TRACE BUILDER ─────────────────────────────────────────────────────

def _build_actual_trace(result: dict[str, Any], reason: str) -> dict[str, Any]:
    """Build what the engine ACTUALLY produced — mark missing stages."""

    # Determine what was actually computed based on result fields
    has_pattern = bool(result.get("pattern"))
    has_strategy = bool(result.get("strategy"))
    has_neutral_score = result.get("score_neutral") is not None
    has_strategy_score = result.get("score_strategy") is not None
    has_market_state = bool(result.get("market_state"))
    has_ev = result.get("ev") is not None
    has_rr = result.get("rr_effective") is not None and result.get("rr_effective", 0) > 0
    has_policy = result.get("policy_trade_allowed") is not None
    has_intent = result.get("intent") is not None
    action = result.get("action", "NO_TRADE")

    # Detect early exit location
    early_exit = False
    exit_location = ""
    missing_rooms: list[str] = []

    if "no_viable_pattern" in reason:
        early_exit = True
        exit_location = "ROOM_1 (no pattern)"
        missing_rooms = ["room_2", "room_3", "room_4"]
    elif not has_neutral_score:
        early_exit = True
        exit_location = "ROOM_2 (scoring not reached)"
        missing_rooms = ["room_2", "room_3", "room_4"]
    elif "score_below_threshold" in reason or "policy_blocked" in reason:
        early_exit = True
        exit_location = "ROOM_2/3 (score/policy gate)"
        missing_rooms = ["room_3_partial", "room_4"]
    elif "confirmation_failed" in reason or "low_confirmation_score" in reason:
        early_exit = True
        exit_location = "ROOM_3 (confirmation reduced EV)"
        missing_rooms = ["room_3_partial", "room_4"]
    elif "risk_rejected" in reason:
        early_exit = True
        exit_location = "ROOM_3 (risk gate)"
        missing_rooms = ["room_4_partial"]
    elif "ev_policy_blocked" in reason:
        early_exit = True
        exit_location = "ROOM_3 (EV/RR policy)"
        missing_rooms = []  # EV computed but blocked

    return {
        "room_1": {
            "pattern_detection": result.get("pattern") or "MISSING" if not has_pattern else result.get("pattern"),
            "strategy_classification": result.get("strategy") or "MISSING",
            "strategy_confidence": result.get("strategy_confidence", 0.0),
            "present": has_pattern and has_strategy,
        },
        "room_2": {
            "score_neutral": result.get("score_neutral"),
            "score_strategy": result.get("score_strategy"),
            "market_state": result.get("market_state"),
            "ev": result.get("ev"),
            "present": has_neutral_score and has_market_state,
            "ev_computed": has_ev,
        },
        "room_3": {
            "rr_effective": result.get("rr_effective"),
            "policy_trade_allowed": result.get("policy_trade_allowed"),
            "policy_required_rr": result.get("policy_required_rr"),
            "bias_fsm_phase": result.get("_bias_phase", "?"),
            "present": has_policy,
            "rr_computed": has_rr,
            "ranking_received_full_pool": not early_exit or exit_location == "",
        },
        "room_4": {
            "execution_decision": action,
            "has_intent": has_intent,
            "present": action in ("EXECUTE", "NO_TRADE") and not ("room_4" in missing_rooms),
        },
        "room_5": {
            "logging": "present",
        },
        "missing_rooms": missing_rooms,
        "early_exit_detected": early_exit,
        "exit_reason": reason,
        "filter_bottleneck_location": exit_location,
    }


# ─── DIAGNOSTICS ──────────────────────────────────────────────────────────────

def _compute_diagnostics(
    intended: dict[str, Any],
    actual: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Compute gap analysis between design and reality."""

    early_exit = actual.get("early_exit_detected", False)
    missing = actual.get("missing_rooms", [])
    has_ev = actual.get("room_2", {}).get("ev_computed", False)
    has_rr = actual.get("room_3", {}).get("rr_computed", False)
    room_3_present = actual.get("room_3", {}).get("present", False)
    ranking_full = actual.get("room_3", {}).get("ranking_received_full_pool", False)
    action = result.get("action", "NO_TRADE")

    return {
        "design_vs_reality_gap": early_exit or len(missing) > 0,
        "ranking_received_full_pool": ranking_full,
        "ev_reached_room_4": has_ev and action == "EXECUTE",
        "rr_reached_room_4": has_rr and action == "EXECUTE",
        "data_loss_detected": len(missing) > 0,
        "rooms_missing_count": len(missing),
        "bottleneck": actual.get("filter_bottleneck_location", ""),
        "action": action,
    }


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def _persist(event: dict[str, Any]) -> None:
    """Append to local JSONL file."""
    try:
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


# ─── DISCORD GAP ALERT ────────────────────────────────────────────────────────

def _emit_gap_alert(symbol: str, event: dict[str, Any]) -> None:
    """Send design-vs-reality gap alert to pair channel (throttled)."""
    try:
        from core.discord_notifier import send_discord

        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"

        diag = event.get("DIAGNOSTICS", {})
        actual = event.get("ACTUAL_ENGINE_TRACE", {})
        bottleneck = diag.get("bottleneck", "?")
        missing_count = diag.get("rooms_missing_count", 0)

        msg = (
            f"⚠️ **DESIGN↔REALITY GAP** | `{symbol}` | Cycle {event.get('cycle_id')}\n"
            f"```\n"
            f"Bottleneck:    {bottleneck}\n"
            f"Rooms missing: {missing_count}\n"
            f"Early exit:    {actual.get('early_exit_detected', False)}\n"
            f"Exit reason:   {actual.get('exit_reason', '?')[:60]}\n"
            f"EV computed:   {actual.get('room_2', {}).get('ev_computed', False)}\n"
            f"RR computed:   {actual.get('room_3', {}).get('rr_computed', False)}\n"
            f"```"
        )

        if len(msg) > 1950:
            msg = msg[:1947] + "..."
        send_discord(channel, msg)
    except Exception:
        pass
