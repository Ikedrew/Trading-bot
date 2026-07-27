"""
Shadow Room Engine — Parallel full-compute architecture model.

Runs ALL 5 rooms to completion for every candidate, regardless of
whether the live engine exited early. Never influences execution.

Two simultaneous representations:
    🟥 LIVE ENGINE FLOW — observed only (unchanged, authoritative for trading)
    🟦 SHADOW ROOM FLOW — full compute (authoritative for analysis)

STRICT RULES:
    - NEVER modifies live engine state or decisions
    - NEVER overrides execution outcomes
    - NEVER removes or suppresses early exits in live path
    - Shadow system is PURE ANALYSIS ONLY

Architecture:
    For every candidate, shadow independently computes:
    Room 1: Pattern + Strategy (already available from live)
    Room 2: Full dual scoring + market state + EV (may need re-compute if live exited before EV)
    Room 3: Full RR + policy + ranking (even when live blocked early)
    Room 4: Simulated execution decision (would it have traded?)
    Room 5: Full structured trace

Design: deterministic, passive, no side effects on execution.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal


# ─── LOG PATH ─────────────────────────────────────────────────────────────────

_SHADOW_LOG = Path("logs/shadow_rooms.jsonl")
_SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)


# ─── MAIN SHADOW COMPUTE ──────────────────────────────────────────────────────

def run_shadow_rooms(
    *,
    symbol: str,
    cycle_id: int,
    bar_time: float,
    candles: list[Candle],
    closed_i: int,
    bid: float,
    ask: float,
    engine_state: Any,
    config: Any,
    detected_patterns: list[Signal],
    risk_manager: Any,
    htf_context: Any = None,
    live_engine_result: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Run full shadow Room 1–5 compute in parallel with live engine.

    This function ALWAYS computes all stages regardless of live engine outcome.
    It NEVER modifies live state. Uses deepcopy where needed.

    Args:
        symbol: Trading pair
        cycle_id: Current cycle
        bar_time: MT5 bar timestamp
        candles: Full candle history
        closed_i: Last closed bar index
        bid/ask: Current prices
        engine_state: Current EngineState (READ ONLY — shadow uses copy)
        config: Config module
        detected_patterns: Patterns from pattern gate
        risk_manager: RiskManager instance
        htf_context: Optional HTF data
        live_engine_result: What the live engine actually produced

    Returns:
        Full shadow trace dict (or None on failure)
    """
    try:
        import copy

        # Shadow operates on a COPY — never contaminates live state
        shadow_state = copy.deepcopy(engine_state)

        # ─── ROOM 1: MARKET INTELLIGENCE ──────────────────────────────
        # Already computed by live engine (patterns detected upstream)
        room_1 = _shadow_room_1(detected_patterns, live_engine_result)

        # ─── ROOM 2: SCORING ENGINE (FULL COMPUTE) ────────────────────
        room_2 = _shadow_room_2(
            candles=candles,
            closed_i=closed_i,
            detected_patterns=detected_patterns,
            engine_state=shadow_state,
            config=config,
            htf_context=htf_context,
            bid=bid,
            ask=ask,
        )

        # ─── ROOM 3: OPPORTUNITY ENGINE (FULL — NO BLOCKING) ─────────
        room_3 = _shadow_room_3(
            symbol=symbol,
            room_2_output=room_2,
            candles=candles,
            closed_i=closed_i,
            detected_patterns=detected_patterns,
            engine_state=shadow_state,
            risk_manager=risk_manager,
            bid=bid,
            ask=ask,
        )

        # ─── ROOM 4: EXECUTION SIMULATION ─────────────────────────────
        room_4 = _shadow_room_4(room_2, room_3)

        # ─── ROOM 5: LOGGING ──────────────────────────────────────────
        room_5 = {"shadow_log": True, "full_compute": True}

        # ─── LIVE ENGINE TRACE (observed) ─────────────────────────────
        live_trace = _build_live_trace(live_engine_result)

        # ─── COMPARISON ───────────────────────────────────────────────
        comparison = _compare(live_trace, room_2, room_3, room_4, live_engine_result)

        # ─── BUILD OUTPUT ─────────────────────────────────────────────
        entity_id = f"{symbol}_{int(bar_time)}"
        output = {
            "entity_id": entity_id,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),

            "LIVE_ENGINE_TRACE": live_trace,

            "SHADOW_ROOM_TRACE": {
                "room_1": room_1,
                "room_2": room_2,
                "room_3": room_3,
                "room_4": room_4,
                "room_5": room_5,
            },

            "COMPARISON_LAYER": comparison,
        }

        # Persist
        _persist(output)

        # Discord alert on significant divergence
        if comparison.get("divergence_detected"):
            _emit_divergence_alert(symbol, output)

        return output

    except Exception:
        return None  # Shadow failure must NEVER affect live execution


# ─── ROOM 1: MARKET INTELLIGENCE ──────────────────────────────────────────────

def _shadow_room_1(patterns: list[Signal], live_result: dict[str, Any]) -> dict[str, Any]:
    """Room 1 output — patterns + strategy (already computed by live engine)."""
    return {
        "pattern_count": len(patterns),
        "patterns": [p.pattern for p in patterns] if patterns else [],
        "best_pattern": live_result.get("pattern"),
        "strategy": live_result.get("strategy"),
        "strategy_confidence": live_result.get("strategy_confidence", 0.0),
        "complete": True,
    }


# ─── ROOM 2: SCORING ENGINE (FULL COMPUTE) ────────────────────────────────────

def _shadow_room_2(
    *,
    candles: list[Candle],
    closed_i: int,
    detected_patterns: list[Signal],
    engine_state: Any,
    config: Any,
    htf_context: Any,
    bid: float,
    ask: float,
) -> dict[str, Any]:
    """Room 2 — compute scoring, market state, EV regardless of gates."""
    try:
        from core.pipeline.new_engine import _select_best_pattern, _compute_all_scores, _GLOBAL_WEIGHTS
        from core.pipeline.strategy_classifier import classify_strategy
        from core.pipeline.strategy_weights import get_weights_for_strategy

        best = _select_best_pattern(detected_patterns)
        if best is None:
            return {"complete": False, "reason": "no_pattern", "score_neutral": 0.0, "score_strategy": 0.0, "ev": None}

        classification = classify_strategy(
            pattern=best, candles=candles, closed_i=closed_i,
            engine_state=engine_state, htf_context=htf_context,
        )

        weights = get_weights_for_strategy(classification.strategy) if classification.confidence >= 0.5 else _GLOBAL_WEIGHTS

        components = _compute_all_scores(
            candles=candles, closed_i=closed_i, best_pattern=best,
            engine_state=engine_state, config=config, htf_context=htf_context,
        )

        score_neutral = round(sum(_GLOBAL_WEIGHTS.get(k, 0.0) * v for k, v in components.items()), 4)
        score_strategy = round(sum(weights.get(k, 0.0) * v for k, v in components.items()), 4)

        # Market state
        from core.pipeline.market_state_engine import get_market_state_engine
        mse = get_market_state_engine()
        # Note: using main MSE is safe (read + append only, no execution impact)
        market_state_result = mse.evaluate(score_neutral, score_strategy, classification.strategy.value)

        return {
            "complete": True,
            "score_neutral": score_neutral,
            "score_strategy": score_strategy,
            "delta": round(score_strategy - score_neutral, 4),
            "components": {k: round(v, 4) for k, v in components.items()},
            "market_state": market_state_result.state.value,
            "market_state_confidence": market_state_result.confidence,
            "strategy": classification.strategy.value,
            "best_pattern": best.pattern,
            "ev": None,  # Computed in Room 3 (needs SL/TP)
        }
    except Exception as e:
        return {"complete": False, "error": str(e)[:100]}


# ─── ROOM 3: OPPORTUNITY ENGINE (FULL — NO EARLY EXIT) ────────────────────────

def _shadow_room_3(
    *,
    symbol: str,
    room_2_output: dict[str, Any],
    candles: list[Candle],
    closed_i: int,
    detected_patterns: list[Signal],
    engine_state: Any,
    risk_manager: Any,
    bid: float,
    ask: float,
) -> dict[str, Any]:
    """Room 3 — full RR + EV + ranking. Never blocks."""
    try:
        if not room_2_output.get("complete"):
            return {"complete": False, "reason": "room_2_incomplete"}

        from core.pipeline.new_engine import _select_best_pattern, _compute_confirmation_score
        from strategy.signal_orchestrator import confirm_signal_detailed

        best = _select_best_pattern(detected_patterns)
        if best is None:
            return {"complete": False, "reason": "no_pattern"}

        # Confirmation score (probabilistic — mirrors live engine, NOT a gate)
        confirmation = confirm_signal_detailed(best, candles)
        confirmation_score = _compute_confirmation_score(best, candles, room_2_output.get("market_state", "TRANSITIONAL"))

        # Risk evaluation (shadow — records SL/TP even if score is low)
        risk_decision = risk_manager.evaluate_signal(symbol, best, candles, bid, ask)
        risk_accepted = risk_decision.accepted

        rr_effective = 0.0
        ev = None
        ev_positive = False

        if risk_accepted and risk_decision.intent:
            intent = risk_decision.intent
            entry = getattr(intent, "entry_reference", 0.0)
            sl = getattr(intent, "sl", 0.0)
            tp = getattr(intent, "tp", 0.0)
            risk_dist = abs(entry - sl)
            reward_dist = abs(tp - entry)
            rr_effective = round(reward_dist / risk_dist, 3) if risk_dist > 0 else 0.0

            # EV computation (shadow — with confirmation_score, matching live)
            try:
                from core.pipeline.expected_value import compute_expected_value
                from core.pipeline.market_state_engine import get_market_state_engine, MarketState

                mse = get_market_state_engine()
                from core.pipeline.market_state_engine import MarketStateResult
                ms_val = room_2_output.get("market_state", "TRANSITIONAL")
                try:
                    ms = MarketState(ms_val)
                except (ValueError, KeyError):
                    ms = MarketState.TRANSITIONAL
                ms_result = MarketStateResult(
                    state=ms,
                    confidence=room_2_output.get("market_state_confidence", 0.5),
                    delta_stability=0.5,
                    flip_rate=0.0,
                    score_consistency=0.5,
                    reasoning="shadow_reconstructed",
                )

                ev_result = compute_expected_value(
                    score_neutral=room_2_output.get("score_neutral", 0.0),
                    strategy_confidence=room_2_output.get("strategy", 0.5) if isinstance(room_2_output.get("strategy"), float) else 0.5,
                    market_state_result=ms_result,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    confirmation_score=confirmation_score,
                )
                ev = ev_result.ev
                ev_positive = ev_result.ev_positive
            except Exception:
                pass

        # Execution policy (shadow — mirrors live: EV + RR + score, NO confirmation gate)
        policy_would_allow = (
            room_2_output.get("score_neutral", 0.0) >= 0.30
            and ev_positive
            and rr_effective >= 1.5
        )

        return {
            "complete": True,
            "confirmation_score": confirmation_score,
            "risk_accepted": risk_accepted,
            "rr_effective": rr_effective,
            "ev": ev,
            "ev_positive": ev_positive,
            "policy_would_allow": policy_would_allow,
            "shadow_ranking_score": (ev or 0.0) * (1.0 if policy_would_allow else 0.3),
        }
    except Exception as e:
        return {"complete": False, "error": str(e)[:100]}


# ─── ROOM 4: EXECUTION SIMULATION ─────────────────────────────────────────────

def _shadow_room_4(room_2: dict[str, Any], room_3: dict[str, Any]) -> dict[str, Any]:
    """Room 4 — would the full pipeline have traded? (mirrors live EV-first logic)"""
    if not room_2.get("complete") or not room_3.get("complete"):
        return {"simulated_decision": "UNKNOWN", "reason": "incomplete_upstream"}

    # Mirrors live engine: EV + RR + score. NO confirmation boolean gate.
    would_trade = (
        room_3.get("risk_accepted", False)
        and room_3.get("ev_positive", False)
        and room_3.get("rr_effective", 0.0) >= 1.5
        and room_2.get("score_strategy", 0.0) >= 0.40
    )

    return {
        "simulated_decision": "TRADE" if would_trade else "NO_TRADE",
        "reason": "all_shadow_gates_passed" if would_trade else _get_shadow_block_reason(room_2, room_3),
    }


def _get_shadow_block_reason(room_2: dict, room_3: dict) -> str:
    """Determine why shadow would NOT trade."""
    if room_2.get("score_strategy", 0.0) < 0.40:
        return "shadow_score_below_threshold"
    if not room_3.get("risk_accepted", False):
        return "shadow_risk_rejected"
    if not room_3.get("ev_positive", False):
        return "shadow_ev_negative"
    if room_3.get("rr_effective", 0.0) < 1.5:
        return "shadow_rr_insufficient"
    return "shadow_unknown"


# ─── LIVE TRACE (observation only) ────────────────────────────────────────────

def _build_live_trace(result: dict[str, Any]) -> dict[str, Any]:
    """Observe what the live engine actually produced."""
    action = result.get("action", "NO_TRADE")
    reason = result.get("reason", "")
    early_exit = (action == "NO_TRADE")

    rooms_completed = ["room_1"]
    if result.get("score_neutral") is not None:
        rooms_completed.append("room_2")
    if result.get("rr_effective") is not None:
        rooms_completed.append("room_3")
    if action == "EXECUTE":
        rooms_completed.append("room_4")

    missing = [r for r in ["room_1", "room_2", "room_3", "room_4"] if r not in rooms_completed]

    return {
        "exit_detected": early_exit,
        "exit_reason": reason,
        "rooms_completed": rooms_completed,
        "final_decision": action,
        "missing_rooms": missing,
    }


# ─── COMPARISON LAYER ─────────────────────────────────────────────────────────

def _compare(
    live: dict[str, Any],
    room_2: dict[str, Any],
    room_3: dict[str, Any],
    room_4: dict[str, Any],
    live_result: dict[str, Any],
) -> dict[str, Any]:
    """Compare live engine outcome vs shadow full compute with fingerprints."""
    live_decision = live.get("final_decision", "NO_TRADE")
    shadow_decision = room_4.get("simulated_decision", "UNKNOWN")

    divergence = (live_decision != shadow_decision) and shadow_decision != "UNKNOWN"

    # ─── DECISION FINGERPRINTS ────────────────────────────────────────
    live_fingerprint = {
        "decision": live_decision,
        "confirmation_score": live_result.get("confirmation_score"),
        "ev": live_result.get("ev"),
        "p_success": live_result.get("p_success"),
        "score_strategy": live_result.get("score_strategy"),
        "rr_effective": live_result.get("rr_effective"),
        "strategy": live_result.get("strategy"),
        "market_state": live_result.get("market_state"),
    }

    shadow_fingerprint = {
        "decision": shadow_decision,
        "confirmation_score": room_3.get("confirmation_score"),
        "ev": room_3.get("ev"),
        "p_success": None,  # Shadow doesn't expose intermediate p_success
        "score_strategy": room_2.get("score_strategy"),
        "rr_effective": room_3.get("rr_effective"),
        "strategy": room_2.get("strategy"),
        "market_state": room_2.get("market_state"),
    }

    # ─── DIVERGENCE REASONS (WHY they disagree) ───────────────────────
    divergence_reasons: list[str] = []
    if divergence:
        # Check confirmation delta
        live_conf = live_fingerprint.get("confirmation_score") or 0.0
        shadow_conf = shadow_fingerprint.get("confirmation_score") or 0.0
        if abs(live_conf - shadow_conf) > 0.05:
            divergence_reasons.append("confirmation_weighting")

        # Check EV threshold crossing
        live_ev = live_fingerprint.get("ev") or 0.0
        shadow_ev = shadow_fingerprint.get("ev") or 0.0
        if (live_ev > 0) != (shadow_ev > 0):
            divergence_reasons.append("ev_threshold_cross")
        elif abs(live_ev - shadow_ev) > 0.00005:
            divergence_reasons.append("ev_magnitude_shift")

        # Check RR difference
        live_rr = live_fingerprint.get("rr_effective") or 0.0
        shadow_rr = shadow_fingerprint.get("rr_effective") or 0.0
        if abs(live_rr - shadow_rr) > 0.1:
            divergence_reasons.append("risk_adjustment")

        # Check market state difference
        if live_fingerprint.get("market_state") != shadow_fingerprint.get("market_state"):
            divergence_reasons.append("regime_shift")

        # Check score difference
        live_score = live_fingerprint.get("score_strategy") or 0.0
        shadow_score = shadow_fingerprint.get("score_strategy") or 0.0
        if abs(live_score - shadow_score) > 0.03:
            divergence_reasons.append("scoring_delta")

        if not divergence_reasons:
            divergence_reasons.append("unknown_source")

    # ─── DELTAS ───────────────────────────────────────────────────────
    delta_ev = (live_fingerprint.get("ev") or 0.0) - (shadow_fingerprint.get("ev") or 0.0)
    delta_conf = (live_fingerprint.get("confirmation_score") or 0.0) - (shadow_fingerprint.get("confirmation_score") or 0.0)

    loss_points = []
    if live.get("exit_detected") and room_2.get("complete"):
        loss_points.append("live_exited_before_full_room_2_used")
    if live.get("exit_detected") and room_3.get("complete") and room_3.get("ev_positive"):
        loss_points.append("live_missed_positive_ev_opportunity")

    return {
        "divergence_detected": divergence,
        "live_decision": live_decision,
        "shadow_decision": shadow_decision,
        "live_fingerprint": live_fingerprint,
        "shadow_fingerprint": shadow_fingerprint,
        "delta_ev": round(delta_ev, 8),
        "delta_confirmation": round(delta_conf, 4),
        "divergence_reasons": divergence_reasons,
        "room_loss_points": loss_points,
        "ranking_starvation_detected": not room_3.get("complete", False),
        "ev_mismatch": (room_3.get("ev_positive", False) and live_decision == "NO_TRADE"),
        "execution_difference": f"live={live_decision} shadow={shadow_decision}" if divergence else "aligned",
    }


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def _persist(output: dict[str, Any]) -> None:
    """Append shadow trace to local JSONL."""
    try:
        with open(_SHADOW_LOG, "a") as f:
            f.write(json.dumps(output, default=str) + "\n")
    except Exception:
        pass


def _emit_divergence_alert(symbol: str, output: dict[str, Any]) -> None:
    """Alert on live↔shadow divergence with reasons via Discord."""
    try:
        from core.discord_notifier import send_discord
        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"

        comp = output.get("COMPARISON_LAYER", {})
        reasons = comp.get("divergence_reasons", [])
        delta_ev = comp.get("delta_ev", 0.0)
        delta_conf = comp.get("delta_confirmation", 0.0)

        msg = (
            f"🔀 **DIVERGENCE ALERT** | `{symbol}` | Cycle {output.get('cycle_id')}\n"
            f"```\n"
            f"Live:       {comp.get('live_decision')}\n"
            f"Shadow:     {comp.get('shadow_decision')}\n"
            f"Δ EV:       {delta_ev:+.8f}\n"
            f"Δ Confirm:  {delta_conf:+.4f}\n"
            f"Reasons:    {', '.join(reasons)}\n"
            f"EV missed:  {comp.get('ev_mismatch', False)}\n"
            f"```"
        )
        send_discord(channel, msg)
    except Exception:
        pass
