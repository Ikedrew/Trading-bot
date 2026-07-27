"""
Event-Driven Observability Layer — State-diff event system per symbol.

Converts continuous engine outputs into discrete events that only emit
when meaningful state changes occur. Routes to per-symbol Discord channels.

Architecture:
    ENGINE OUTPUT → STATE DIFF → SYMBOL ROUTER → DISCORD (only on change)

Design rules:
    - NEVER modifies engine logic or output
    - NEVER affects execution flow
    - Only emits on meaningful state transitions
    - Silent when nothing changes
    - Per-symbol isolated event streams

This is an intelligence feed, not a debug console.
"""

from __future__ import annotations

from typing import Any


# ─── STATE CACHE (per symbol, in-memory) ──────────────────────────────────────

_STATE_CACHE: dict[str, dict[str, Any]] = {}


# ─── SYMBOL → DISCORD CHANNEL ROUTING ────────────────────────────────────────

_SYMBOL_CHANNELS: dict[str, str] = {
    "EURUSD": "eurusd-sb",
    "GBPUSD": "gbpusd-sb",
    "USDJPY": "usdjpy-sb",
    "USDCHF": "usdchf-sb",
    "USDCAD": "usdcad-sb",
    "AUDUSD": "audusd-sb",
    "NZDUSD": "nzdusd-sb",
}


# ─── CONFIGURABLE THRESHOLDS ─────────────────────────────────────────────────

_EV_CHANGE_THRESHOLD = 0.00005  # EV must change by at least this to count as meaningful
_SCORE_CHANGE_THRESHOLD = 0.05  # Composite score must shift by this to emit


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def observe_engine_output(result: dict[str, Any]) -> None:
    """
    Process engine output and emit Discord event ONLY if state changed meaningfully.

    Call this once per symbol per cycle AFTER engine produces result.
    Fully passive. try/except safe. Never affects execution.

    Args:
        result: Raw engine output dict (must contain 'symbol' key)
    """
    try:
        symbol = result.get("symbol")
        if not symbol:
            return

        # Extract observable state from engine output
        new_state = _extract_state(result)

        # Get previous state
        old_state = _STATE_CACHE.get(symbol)

        # Determine if we should emit
        if old_state is None:
            # First occurrence — always emit
            _emit_activation(symbol, new_state)
            _STATE_CACHE[symbol] = new_state
            return

        changes = _compute_diff(old_state, new_state)
        if not changes:
            return  # No meaningful change — stay silent

        # Emit state update
        _emit_update(symbol, old_state, new_state, changes)
        _STATE_CACHE[symbol] = new_state

    except Exception:
        pass  # Observability failure must never affect runtime


# ─── STATE EXTRACTION ─────────────────────────────────────────────────────────

def _extract_state(result: dict[str, Any]) -> dict[str, Any]:
    """Extract observable fields from engine output."""
    return {
        "action": result.get("action"),
        "pattern": result.get("pattern"),
        "strategy": result.get("strategy"),
        "strategy_confidence": result.get("strategy_confidence", 0.0),
        "score": result.get("score", 0.0),
        "score_neutral": result.get("score_neutral", 0.0),
        "ev": result.get("ev", 0.0),
        "ev_positive": result.get("ev_positive", False),
        "market_state": result.get("market_state"),
        "rr_effective": result.get("rr_effective", 0.0),
        "policy_trade_allowed": result.get("policy_trade_allowed", False),
    }


# ─── STATE DIFF LOGIC ─────────────────────────────────────────────────────────

def _compute_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """
    Determine which fields changed meaningfully.

    Returns list of change descriptions (empty = no meaningful change).
    """
    changes: list[str] = []

    # Pattern changed
    if new["pattern"] and new["pattern"] != old.get("pattern"):
        changes.append(f"pattern: {old.get('pattern', 'none')} → {new['pattern']}")

    # Strategy changed
    if new["strategy"] and new["strategy"] != old.get("strategy"):
        changes.append(f"strategy: {old.get('strategy', 'none')} → {new['strategy']}")

    # Market state changed
    if new["market_state"] and new["market_state"] != old.get("market_state"):
        changes.append(f"market_state: {old.get('market_state', '?')} → {new['market_state']}")

    # Action changed (NO_TRADE → EXECUTE or vice versa)
    if new["action"] != old.get("action"):
        changes.append(f"action: {old.get('action', '?')} → {new['action']}")

    # EV changed beyond threshold
    old_ev = old.get("ev", 0.0) or 0.0
    new_ev = new.get("ev", 0.0) or 0.0
    if abs(new_ev - old_ev) >= _EV_CHANGE_THRESHOLD:
        changes.append(f"ev: {old_ev:+.6f} → {new_ev:+.6f}")

    # EV polarity flipped
    if new.get("ev_positive") != old.get("ev_positive"):
        _old_pol = "positive" if old.get("ev_positive") else "negative"
        _new_pol = "positive" if new.get("ev_positive") else "negative"
        changes.append(f"ev_polarity: {_old_pol} → {_new_pol}")

    # Policy permission changed
    if new.get("policy_trade_allowed") != old.get("policy_trade_allowed"):
        _old_p = "ALLOWED" if old.get("policy_trade_allowed") else "BLOCKED"
        _new_p = "ALLOWED" if new.get("policy_trade_allowed") else "BLOCKED"
        changes.append(f"permission: {_old_p} → {_new_p}")

    return changes


# ─── MESSAGE GENERATION ───────────────────────────────────────────────────────

def _emit_activation(symbol: str, state: dict[str, Any]) -> None:
    """Emit initial pattern detection message."""
    pattern = state.get("pattern") or "UNKNOWN"
    strategy = state.get("strategy") or "?"
    confidence = state.get("strategy_confidence", 0.0)
    ev = state.get("ev", 0.0) or 0.0
    market_state = state.get("market_state") or "?"
    action = state.get("action") or "?"

    msg = (
        f"📍 **{symbol} — PATTERN DETECTED**\n"
        f"```\n"
        f"Pattern:      {pattern}\n"
        f"Strategy:     {strategy} ({confidence:.2f})\n"
        f"EV:           {ev:+.6f}\n"
        f"Market State: {market_state}\n"
        f"Action:       {action}\n"
        f"```"
    )

    channel = _SYMBOL_CHANNELS.get(symbol, "decision-log")
    _send_to_discord(channel, msg)


def _emit_update(
    symbol: str,
    old: dict[str, Any],
    new: dict[str, Any],
    changes: list[str],
) -> None:
    """Emit state update message showing only what changed."""
    change_lines = "\n".join(f"  {c}" for c in changes)
    action = new.get("action") or "?"

    msg = (
        f"📊 **{symbol} — STATE UPDATE**\n"
        f"```\n"
        f"{change_lines}\n"
        f"Action: {action}\n"
        f"```"
    )

    channel = _SYMBOL_CHANNELS.get(symbol, "decision-log")
    _send_to_discord(channel, msg)


# ─── DISCORD SENDER (ABSTRACTED) ─────────────────────────────────────────────

def _send_to_discord(channel: str, message: str) -> None:
    """
    Send message to Discord channel.

    Uses existing discord_notifier infrastructure.
    Falls back to decision-log if per-symbol channel not configured.
    """
    try:
        from core.discord_notifier import send_discord
        send_discord(channel, message)
    except Exception:
        pass  # Discord failure must never affect anything


# ─── UTILITY ──────────────────────────────────────────────────────────────────

def reset_cache() -> None:
    """Clear state cache (useful for testing or restart)."""
    _STATE_CACHE.clear()


def get_cached_state(symbol: str) -> dict[str, Any] | None:
    """Read-only access to cached state for a symbol (for debugging)."""
    return _STATE_CACHE.get(symbol)
