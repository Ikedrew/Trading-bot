"""
Output Router — Post-engine orchestration layer.

Receives already-built audit + narrative outputs and routes them to:
1. AWS S3 (immutable history — primary storage, one file per cycle)
2. Discord (live view — narrative only)
3. Email hook (placeholder for future use)

STRICT RULES:
    - NEVER modifies engine decisions
    - NEVER modifies audit output
    - NEVER modifies narrative output
    - NEVER recomputes scores
    - NEVER introduces new metrics
    - NEVER affects execution flow
    - Fully passive (try/except: pass everywhere)

S3 Path Structure:
    s3://trading-engine/v1/{symbol}/{year}/{month}/{day}/cycle_{cycle_id}.json

Event Schema (v1.0.0):
    Each cycle produces ONE complete JSON file containing:
    - cycle_id, timestamp, symbol (identity)
    - decision (full engine output)
    - scores (raw scoring dictionary)
    - composite (final weighted score)
    - narrative (human-readable explanation)
    - audit (mathematical breakdown)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# ─── SCHEMA ───────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "v1.4.0"
_ENGINE_VERSION = "new_engine_v1.2"
# NOTE: Legacy bucket "trading-engine-strategy-events" is fully decommissioned.
# All S3 writes go exclusively to "trading-bot-data-mk1" via core/storage/s3_batch_writer.py.
_S3_BUCKET = None  # DECOMMISSIONED — no S3 writes from this module


# ═══════════════════════════════════════════════════════════════════
# ARCHITECTURE RULE:
# Only core/event_stream.py is allowed to write to S3.
# All other S3 writers are forbidden.
# ═══════════════════════════════════════════════════════════════════
# TYPE: ADAPTER
# CAPABILITIES: ["discord", "noop_s3"]
# STATUS: S3 disabled. Discord routing still active.
# ═══════════════════════════════════════════════════════════════════

def _assert_adapter_mode() -> None:
    try:
        from core import config
        assert getattr(config, "ADAPTER_MODE", True)
    except ImportError:
        pass

_assert_adapter_mode()


def safe_write_to_s3(event: dict[str, Any]) -> None:
    """
    ADAPTER: No-op S3 sink. All S3 writes routed through event_stream.
    Remove after migration.
    """
    return


# ─── MAIN ORCHESTRATOR ────────────────────────────────────────────────────────

def process_engine_output(
    *,
    symbol: str,
    decision: dict[str, Any],
    engine_state: Any,
    gate_results: dict[str, bool | str] | None = None,
    cycle_id: int,
    audit_output: dict[str, Any] | None = None,
    narrative_output: str | None = None,
    execution_result: Any = None,
) -> None:
    """
    Orchestrate persistence and routing of engine outputs.

    This function is a deterministic event recorder + router.
    It does NOT compute, evaluate, or influence anything.

    Args:
        symbol: Trading pair
        decision: Raw engine output dict (as-is from run_new_engine)
        engine_state: EngineState snapshot at decision time
        gate_results: Runtime guard pass/fail results
        cycle_id: Current cycle identifier
        audit_output: Pre-built audit dict (passed through unchanged)
        narrative_output: Pre-built narrative string (passed through unchanged)
        execution_result: Optional post-trade result
    """

    # ─── BUILD EVENT ──────────────────────────────────────────────────
    event = build_cycle_event(
        symbol=symbol,
        decision=decision,
        engine_state=engine_state,
        gate_results=gate_results,
        cycle_id=cycle_id,
        audit_output=audit_output,
        narrative_output=narrative_output,
        execution_result=execution_result,
    )

    # ─── STEP 1: AWS S3 PERSISTENCE (primary storage) ─────────────────
    safe_write_to_s3(event)

    # ─── STEP 2: DISCORD ROUTING (view layer only) ────────────────────
    _send_to_discord(narrative_output, symbol)

    # ─── STEP 3: EMAIL HOOK (placeholder) ─────────────────────────────
    _email_hook(event, narrative_output)


# ─── EVENT BUILDER ────────────────────────────────────────────────────────────

def build_cycle_event(
    *,
    symbol: str,
    decision: dict[str, Any],
    engine_state: Any,
    gate_results: dict[str, bool | str] | None = None,
    cycle_id: int,
    audit_output: dict[str, Any] | None = None,
    narrative_output: str | None = None,
    execution_result: Any = None,
) -> dict[str, Any]:
    """
    Build a complete strategy cycle event object.

    One event = one complete strategy cycle.
    Contains all information needed to replay the decision.

    Returns:
        Structured event dict ready for S3 persistence.
    """

    # Extract scores and composite from decision
    # ARCHITECTURE RULE: composite is READ-ONLY from engine output.
    # It must NEVER be recalculated from components downstream.
    scores = decision.get("components", {})
    composite = decision.get("score", 0.0)

    # Engine state snapshot (read-only extraction)
    state_snapshot = {
        "current_bias": _safe_attr(engine_state, "current_bias", fmt="enum"),
        "bias_phase": _safe_attr(engine_state, "bias_phase"),
        "bias_strength": _safe_attr(engine_state, "bias_strength"),
        "bias_age_seconds": _safe_attr(engine_state, "bias_age_seconds"),
        "regime_state": _safe_attr(engine_state, "regime_state"),
        "volatility_filter": _safe_attr(engine_state, "volatility_filter"),
        "bias_confirmation_count": _safe_attr(engine_state, "bias_confirmation_count"),
        "bias_contradiction_count": _safe_attr(engine_state, "bias_contradiction_count"),
        "structure_score": _safe_attr(engine_state, "structure_score"),
        "structure_regime": _safe_attr(engine_state, "structure_regime"),
    }

    # Execution result extraction
    exec_data = None
    if execution_result is not None:
        exec_data = {
            "ok": getattr(execution_result, "ok", None),
            "fill_price": getattr(execution_result, "fill_price", None),
            "error": getattr(execution_result, "error", None),
            "ticket": getattr(execution_result, "ticket", None),
        }

    # Intent extraction (if EXECUTE)
    intent_data = None
    intent = decision.get("intent")
    if intent is not None:
        intent_data = {
            "symbol": getattr(intent, "symbol", symbol),
            "side": getattr(intent, "side", None).name if hasattr(getattr(intent, "side", None), "name") else None,
            "entry_reference": getattr(intent, "entry_reference", None),
            "sl": getattr(intent, "sl", None),
            "tp": getattr(intent, "tp", None),
            "volume": getattr(intent, "volume", None),
            "pattern": getattr(intent, "pattern", None),
        }

    # Derived metrics (post-engine, never influences scoring)
    _confidence_pct = round(composite * 100, 2)
    _grade = _get_grade(_confidence_pct)
    _status = "PASS" if decision.get("action") == "EXECUTE" else "BLOCKED"

    return {
        # ─── Schema versioning ────────────────────────────────────
        "schema_version": _SCHEMA_VERSION,
        "engine_version": _ENGINE_VERSION,

        # ─── Core identity (required) ─────────────────────────────
        "cycle_id": cycle_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol,
        "event_id": uuid.uuid4().hex[:12],

        # ─── Engine output (full decision object) ─────────────────
        "decision": {
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "pattern": decision.get("pattern"),
            "side": decision.get("side"),
            "confirmation_strength": decision.get("confirmation_strength"),
            "intent": intent_data,
        },

        # ─── Strategy classification ──────────────────────────────
        "strategy": {
            "type": decision.get("strategy"),
            "confidence": decision.get("strategy_confidence"),
            "reasoning": decision.get("strategy_reasoning"),
            "weights_used": decision.get("weights_used"),
        },

        # ─── Dual scoring ─────────────────────────────────────────
        "scoring": {
            "score_neutral": decision.get("score_neutral"),
            "score_strategy": decision.get("score_strategy"),
            "delta": decision.get("delta"),
        },

        # ─── Market state ─────────────────────────────────────────
        "market_state": {
            "state": decision.get("market_state"),
            "confidence": decision.get("market_state_confidence"),
            "reasoning": decision.get("market_state_reasoning"),
        },

        # ─── Execution policy ─────────────────────────────────────
        "execution_policy": {
            "trade_allowed": decision.get("policy_trade_allowed"),
            "required_rr": decision.get("policy_required_rr"),
            "max_size_fraction": decision.get("policy_max_size_fraction"),
            "reasoning": decision.get("policy_reasoning"),
        },

        # ─── Expected value ───────────────────────────────────────
        "expected_value": {
            "ev": decision.get("ev"),
            "ev_positive": decision.get("ev_positive"),
            "ev_context": "comparative_ranking_metric",
            "p_success": decision.get("p_success"),
            "p_failure": decision.get("p_failure"),
            "reward": decision.get("ev_reward"),
            "risk": decision.get("ev_risk"),
            "rr_effective": decision.get("rr_effective"),
            "uncertainty_dampening": decision.get("ev_uncertainty_dampening"),
            "reasoning": decision.get("ev_reasoning"),
        },

        # ─── Scoring (raw + composite) ────────────────────────────
        "scores": scores,
        "composite": composite,

        # ─── Result summary (derived, Athena-ready) ───────────────
        "result_summary": {
            "composite": composite,
            "confidence_pct": _confidence_pct,
            "grade": _grade,
            "threshold": 0.40,
            "status": _status,
        },

        # ─── Engine state at decision time ────────────────────────
        "engine_state": state_snapshot,

        # ─── Gate results ─────────────────────────────────────────
        "gate_results": gate_results,

        # ─── Strategy transparency (pre-built, attached as-is) ────
        "narrative": narrative_output,
        "audit": audit_output,

        # ─── Execution outcome (populated post-trade) ─────────────
        "execution": exec_data,
    }


# ─── DISCORD ROUTING ──────────────────────────────────────────────────────────

def _send_to_discord(narrative: str | None, symbol: str) -> None:
    """
    Send narrative to Discord decision-log channel + per-symbol channel.

    Dual dispatch (temporary migration test):
        1. Always sends to decision-log (existing behaviour)
        2. Also sends to per-symbol channel (e.g. "gbpusd-sb")

    Only sends the narrative string. No JSON. No audit. No raw engine data.
    Discord has a 2000 char limit — truncate if needed.
    """
    if not narrative:
        return

    try:
        from core.discord_notifier import send_discord

        # Discord message limit is 2000 chars
        if len(narrative) > 1950:
            narrative = narrative[:1947] + "..."

        _msg = f"```\n{narrative}\n```"

        # 1. Original destination (keep)
        send_discord("decision-log", _msg)

        # 2. Per-symbol channel duplication (migration test)
        _symbol_channel = symbol.lower().replace("_", "-") if symbol else None
        if _symbol_channel:
            send_discord(_symbol_channel, _msg)
    except Exception:
        pass  # Discord failure must never affect execution


# ─── EMAIL HOOK (PLACEHOLDER) ─────────────────────────────────────────────────

def _email_hook(event: dict[str, Any], narrative: str | None) -> None:
    """
    Placeholder for future email notification.

    Currently does nothing. When implemented:
    - Consumes event or narrative
    - No logic dependencies
    - No dependency on success
    """
    pass


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _safe_attr(obj: Any, attr: str, fmt: str | None = None) -> Any:
    """Safely extract attribute from object without raising."""
    val = getattr(obj, attr, None)
    if val is None:
        return None
    if fmt == "enum" and hasattr(val, "value"):
        return val.value
    if fmt == "enum" and hasattr(val, "name"):
        return val.name
    return val


def _get_grade(confidence_pct: float) -> str:
    """Convert confidence percentage to letter grade (pure mapping)."""
    if confidence_pct >= 85:
        return "A"
    elif confidence_pct >= 70:
        return "B"
    elif confidence_pct >= 55:
        return "C"
    elif confidence_pct >= 40:
        return "D"
    elif confidence_pct >= 25:
        return "E"
    else:
        return "F"
