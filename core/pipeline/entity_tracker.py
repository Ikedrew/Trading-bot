"""
Entity Tracker — Continuous state logger for live trading pipeline.

Tracks trade opportunities and trades as STATEFUL ENTITIES that evolve
over time across pipeline rooms. Emits structured events on every update.

This is PURELY OBSERVATIONAL. It does NOT:
    - Make decisions
    - Filter candidates
    - Influence trading logic
    - Block or promote entities

It ONLY:
    - Observes entity creation, updates, blocking, promotion, execution
    - Emits structured JSON events per update
    - Tracks entity age and lifecycle progression
    - Maintains history for forensic replay

Entity model:
    entity_id: {symbol}_{origin_timestamp}
    lifecycle: ACTIVE → BLOCKED | PROMOTED → EXECUTED | CLOSED

Pipeline rooms (observed only):
    ROOM 1: MARKET_INTELLIGENCE (pattern + strategy classification)
    ROOM 2: SCORING_ENGINE (dual scoring + EV)
    ROOM 3: OPPORTUNITY_ENGINE (market state + execution policy)
    ROOM 4: EXECUTION_DECISION (final gate)
    ROOM 5: FINAL_LOGGING (post-execution)

Design: deterministic, passive, no side effects.
"""

from __future__ import annotations

import json
import time as _time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.clock import utc_ms, utc_ms_to_iso


# ─── ENTITY TYPES ─────────────────────────────────────────────────────────────

ROOMS = ("MARKET_INTELLIGENCE", "SCORING_ENGINE", "OPPORTUNITY_ENGINE", "EXECUTION_DECISION", "FINAL_LOGGING")

EVENT_TYPES = ("ENTER", "UPDATE", "COMPLETE", "BLOCK", "PROMOTE", "EXECUTE", "EXIT")


@dataclass
class TrackedEntity:
    """A live stateful entity being tracked through the pipeline."""
    entity_id: str
    symbol: str
    entity_type: str            # "OPPORTUNITY" or "TRADE"
    lifecycle_state: str        # "ACTIVE" / "BLOCKED" / "PROMOTED" / "CLOSED"
    created_at: float           # Unix timestamp of first observation
    last_update: float          # Unix timestamp of last update
    current_room: str           # Which pipeline room entity is in
    update_count: int = 0       # How many times this entity has been evaluated
    history: list[dict] = field(default_factory=list)  # Event history


# ─── ENTITY REGISTRY ──────────────────────────────────────────────────────────

_entities: dict[str, TrackedEntity] = {}


def _get_or_create_entity(symbol: str, origin_time: float) -> tuple[TrackedEntity, bool]:
    """Get existing entity or create new one. Returns (entity, is_new)."""
    entity_id = f"{symbol}_{int(origin_time)}"

    if entity_id in _entities:
        return _entities[entity_id], False

    entity = TrackedEntity(
        entity_id=entity_id,
        symbol=symbol,
        entity_type="OPPORTUNITY",
        lifecycle_state="ACTIVE",
        created_at=origin_time,
        last_update=utc_ms() / 1000.0,  # Canonical clock (seconds for compat)
        current_room="MARKET_INTELLIGENCE",
    )
    _entities[entity_id] = entity
    return entity, True


# ─── MAIN EVENT EMISSION ──────────────────────────────────────────────────────

def track_entity_update(
    *,
    symbol: str,
    origin_time: float,
    room: str,
    event_type: str,
    data: dict[str, Any],
    blocked: bool = False,
    block_reason: str | None = None,
    cycle_id: int = 0,
) -> dict[str, Any] | None:
    """
    Record an entity state update and emit a structured event.

    Called once per entity per room per cycle.

    Args:
        symbol: Trading pair
        origin_time: When this opportunity was first detected (bar time, unix seconds)
        room: Which pipeline room produced this update
        event_type: ENTER/UPDATE/COMPLETE/BLOCK/PROMOTE/EXECUTE/EXIT
        data: State snapshot (scores, pattern, strategy, etc.)
        blocked: Whether entity is blocked at this point
        block_reason: Why (if blocked)
        cycle_id: Current scan cycle number (explicit causal link)

    Returns:
        Structured event dict (also persisted internally)
    """
    try:
        entity, is_new = _get_or_create_entity(symbol, origin_time)

        now = utc_ms() / 1000.0  # Canonical clock → seconds for age computation
        age_seconds = round(now - entity.created_at, 2)

        # Update entity state
        from_room = entity.current_room
        entity.current_room = room
        entity.last_update = now
        entity.update_count += 1

        if blocked:
            entity.lifecycle_state = "BLOCKED"
        elif event_type == "EXECUTE":
            entity.lifecycle_state = "PROMOTED"
            entity.entity_type = "TRADE"
        elif event_type == "EXIT":
            entity.lifecycle_state = "CLOSED"

        # Canonical candle origin timestamp (seconds → millis)
        from core.clock import candle_ts_to_ms
        _origin_candle_ms = candle_ts_to_ms(origin_time)

        # Build event (canonical UTC millisecond timestamp)
        _ts_ms = utc_ms()
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "ts_utc_ms": _ts_ms,
            "timestamp": utc_ms_to_iso(_ts_ms),
            "entity_id": entity.entity_id,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "origin_candle_ts_utc_ms": _origin_candle_ms,
            "entity_type": entity.entity_type,
            "from_room": from_room,
            "to_room": room,
            "event_type": event_type,
            "age_seconds": age_seconds,
            "update_count": entity.update_count,
            "is_first_evaluation": is_new,
            "lifecycle_state": entity.lifecycle_state,
            "data": data,
            "decision": {
                "blocked": blocked,
                "reason": block_reason,
            },
        }

        # Store in history
        entity.history.append(event)

        # Emit to Discord (pair channel)
        _emit_discord(symbol, event)

        # Emit to local log
        _emit_local(event)

        # Cleanup closed/blocked entities (prevent memory leak)
        if entity.lifecycle_state in ("BLOCKED", "CLOSED"):
            _entities.pop(entity.entity_id, None)

        return event

    except Exception:
        return None  # Tracking failure must never affect execution


# ─── DISCORD EMISSION ─────────────────────────────────────────────────────────

def _emit_discord(symbol: str, event: dict[str, Any]) -> None:
    """Send human-readable entity update to pair channel."""
    try:
        from core.discord_notifier import send_discord

        # Channel routing
        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"

        # Format
        etype = event["event_type"]
        room = event["to_room"]
        age = event["age_seconds"]
        blocked = event["decision"]["blocked"]
        reason = event["decision"]["reason"] or ""
        data = event["data"]

        # Compact Discord format
        _icon = {
            "ENTER": "🟢", "UPDATE": "🔄", "COMPLETE": "✅",
            "BLOCK": "🔴", "PROMOTE": "⬆️", "EXECUTE": "🚀", "EXIT": "🏁",
        }.get(etype, "📋")

        lines = [
            f"{_icon} **{etype}** | `{symbol}` | Room: {room}",
            f"⏱ Age: {age:.0f}s | Update #{event['update_count']}",
        ]

        if data.get("score"):
            lines.append(f"Score: {data['score']:.3f} | EV: {data.get('ev', 'N/A')}")
        if data.get("strategy"):
            lines.append(f"Strategy: {data['strategy']} | Pattern: {data.get('pattern', '?')}")
        if data.get("market_state"):
            lines.append(f"Market: {data['market_state']} | Bias: {data.get('bias', '?')}")
        if blocked:
            lines.append(f"❌ BLOCKED: {reason}")

        msg = "\n".join(lines)
        if len(msg) > 1950:
            msg = msg[:1947] + "..."
        send_discord(channel, msg)
    except Exception:
        pass


# ─── LOCAL LOG EMISSION ───────────────────────────────────────────────────────

def _emit_local(event: dict[str, Any]) -> None:
    """Emit entity event to unified event stream and legacy JSONL."""
    try:
        from core.event_stream import emit_entity
        emit_entity(event.get("symbol", ""), event, source="entity_tracker")
    except Exception:
        pass
    # Legacy file (backward compat — will be removed after full migration)
    try:
        from pathlib import Path
        log_path = Path("logs/entity_events.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


# ─── CONVENIENCE WRAPPERS ─────────────────────────────────────────────────────

def track_opportunity(
    *,
    symbol: str,
    bar_time: float,
    engine_result: dict[str, Any],
    cycle_id: int = 0,
) -> None:
    """
    Track a full engine cycle result as an entity update.

    Called once per symbol per cycle from live_scanner.
    Determines room and event type from engine_result fields.
    """
    try:
        action = engine_result.get("action", "NO_TRADE")
        reason = engine_result.get("reason", "")
        score = engine_result.get("score", 0.0)
        blocked = (action == "NO_TRADE")

        # Determine room based on where rejection occurred
        if "no_viable_pattern" in reason:
            room = "MARKET_INTELLIGENCE"
        elif "score_below_threshold" in reason or "policy_blocked" in reason:
            room = "SCORING_ENGINE"
        elif "ev_policy_blocked" in reason or "risk_rejected" in reason:
            room = "OPPORTUNITY_ENGINE"
        elif "confirmation_failed" in reason or "low_confirmation_score" in reason:
            room = "OPPORTUNITY_ENGINE"
        elif action == "EXECUTE":
            room = "EXECUTION_DECISION"
        else:
            room = "SCORING_ENGINE"

        # Determine event type
        if action == "EXECUTE":
            event_type = "PROMOTE"
        elif blocked:
            event_type = "BLOCK"
        else:
            event_type = "UPDATE"

        # Build data snapshot
        data = {
            "pattern": engine_result.get("pattern"),
            "strategy": engine_result.get("strategy"),
            "score": score,
            "score_neutral": engine_result.get("score_neutral"),
            "score_strategy": engine_result.get("score_strategy"),
            "ev": engine_result.get("ev"),
            "rr": engine_result.get("rr_effective"),
            "market_state": engine_result.get("market_state"),
            "bias": engine_result.get("_bias_phase"),
        }

        track_entity_update(
            symbol=symbol,
            origin_time=bar_time,
            room=room,
            event_type=event_type,
            data=data,
            blocked=blocked,
            block_reason=reason if blocked else None,
            cycle_id=cycle_id,
        )
    except Exception:
        pass  # Must never affect execution
