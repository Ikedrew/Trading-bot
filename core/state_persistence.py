"""
EngineState warm-start persistence — save on shutdown, restore on startup.

Atomic writes, JSON-only, human-readable, one file per symbol.
Never blocks trading. Never crashes startup. Fully optional.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import config
from core.engine_state import EngineState
from strategy.signals import Side

logger = logging.getLogger(__name__)

# Fields persisted (pure strategy state only)
_PERSISTED_FIELDS = (
    "current_bias",
    "bias_phase",
    "bias_strength",
    "bias_age_seconds",
    "bias_confirmation_score",
    "bias_confirmation_count",
    "bias_contradiction_count",
    "last_strong_impulse_direction",
    "regime_state",
    "volatility_filter",
    # Cooldown context
    "last_successful_open_mono",
    # Structure context
    "structure_score",
    "structure_regime",
)

# Fields with special serialization (not simple scalars)
_FAILED_SETUP_TTL_SECONDS = 1800.0  # 30 minutes — discard older failures on restore


def _get_persist_dir() -> Path:
    return Path(getattr(config, "ENGINE_STATE_PERSIST_DIR", "logs/state"))


def _serialize_state(state: EngineState, symbol: str) -> dict[str, Any]:
    """Extract persisted fields into a JSON-safe dict."""
    data: dict[str, Any] = {
        "_meta": {
            "symbol": symbol,
            "saved_at_utc": datetime.now(tz=timezone.utc).isoformat(),
            "saved_at_unix": time.time(),
        }
    }
    for field in _PERSISTED_FIELDS:
        value = getattr(state, field, None)
        if isinstance(value, Side):
            data[field] = value.name
        else:
            data[field] = value

    # Special: last_failed_setups (deque of tuples: price, price, timestamp, pattern)
    failed = getattr(state, "last_failed_setups", None)
    if failed is not None:
        data["last_failed_setups"] = [list(entry) for entry in failed]
    else:
        data["last_failed_setups"] = []

    return data


def _deserialize_into_state(data: dict[str, Any], state: EngineState) -> None:
    """Apply persisted fields onto an EngineState instance."""
    for field in _PERSISTED_FIELDS:
        if field not in data:
            continue
        value = data[field]
        # Restore Side enums
        if field in ("current_bias", "last_strong_impulse_direction"):
            if value is None:
                setattr(state, field, None)
            elif isinstance(value, str) and value in ("BUY", "SELL"):
                setattr(state, field, Side[value])
            # else: skip invalid — leave default
        else:
            setattr(state, field, value)

    # Special: last_failed_setups (deque of tuples with TTL filtering)
    raw_failures = data.get("last_failed_setups", [])
    if raw_failures and isinstance(raw_failures, list):
        now = time.time()
        restored: list[tuple] = []
        for entry in raw_failures:
            if not isinstance(entry, (list, tuple)) or len(entry) < 4:
                continue
            try:
                # entry = [price1, price2, timestamp, pattern_name]
                entry_time = float(entry[2])
                age = now - entry_time
                if age <= _FAILED_SETUP_TTL_SECONDS:
                    restored.append(tuple(entry))
            except (ValueError, TypeError):
                continue
        if restored:
            from collections import deque as _deque
            state.last_failed_setups = _deque(restored, maxlen=20)
            logger.info(
                "[ENGINE_STATE_RESTORE] last_failed_setups restored=%d discarded_expired=%d",
                len(restored), len(raw_failures) - len(restored),
            )


def save_engine_states(states: list[tuple[str, EngineState]]) -> None:
    """
    Persist all symbol EngineStates to disk. Call on graceful shutdown only.

    Args:
        states: list of (symbol, EngineState) tuples
    """
    if not getattr(config, "ENGINE_STATE_WARM_START_ENABLED", False):
        return

    persist_dir = _get_persist_dir()

    for symbol, state in states:
        try:
            persist_dir.mkdir(parents=True, exist_ok=True)
            filepath = persist_dir / f"{symbol}.json"

            data = _serialize_state(state, symbol)
            json_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")

            # Atomic write: temp file → flush → fsync → replace
            fd, tmp_path = tempfile.mkstemp(
                dir=str(persist_dir), suffix=".tmp", prefix=f"{symbol}_"
            )
            try:
                os.write(fd, json_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_path, str(filepath))

            logger.info("[ENGINE_STATE_SAVE_SUCCESS] symbol=%s path=%s", symbol, filepath)

        except Exception as exc:
            logger.error("[ENGINE_STATE_SAVE_ERROR] symbol=%s error=%s", symbol, exc)


def load_engine_state(symbol: str) -> EngineState | None:
    """
    Attempt to restore EngineState from disk for a symbol.

    Returns populated EngineState if valid snapshot exists, None otherwise.
    Never raises — logs and returns None on any failure.
    """
    if not getattr(config, "ENGINE_STATE_WARM_START_ENABLED", False):
        return None

    try:
        persist_dir = _get_persist_dir()
        filepath = persist_dir / f"{symbol}.json"

        if not filepath.exists():
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate structure
        if not isinstance(data, dict):
            logger.warning("[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=invalid_json_structure", symbol)
            return None

        meta = data.get("_meta", {})
        saved_at = meta.get("saved_at_unix", 0)

        # Age check
        max_age = float(getattr(config, "ENGINE_STATE_MAX_AGE_SECONDS", 86400))
        age_seconds = time.time() - float(saved_at)
        if age_seconds > max_age:
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=stale_snapshot age_seconds=%.0f max_age=%.0f",
                symbol, age_seconds, max_age,
            )
            return None

        # Validate required fields present
        missing = [f for f in ("bias_phase", "regime_state") if f not in data]
        if missing:
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=missing_fields fields=%s",
                symbol, missing,
            )
            return None

        # Validate enum values
        bias_phase = data.get("bias_phase", "")
        if bias_phase not in ("EXPIRED", "BUILDING", "CONFIRMED", "LOCKED"):
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=invalid_bias_phase value=%s",
                symbol, bias_phase,
            )
            return None

        regime = data.get("regime_state", "")
        if regime not in ("RANGING", "TRENDING", "VOLATILE", "CHOPPY", "TREND_UP", "TREND_DOWN"):
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=invalid_regime_state value=%s",
                symbol, regime,
            )
            return None

        current_bias = data.get("current_bias")
        if current_bias is not None and current_bias not in ("BUY", "SELL"):
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=invalid_current_bias value=%s",
                symbol, current_bias,
            )
            return None

        impulse = data.get("last_strong_impulse_direction")
        if impulse is not None and impulse not in ("BUY", "SELL"):
            logger.warning(
                "[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=invalid_impulse_direction value=%s",
                symbol, impulse,
            )
            return None

        # Build state
        state = EngineState()
        _deserialize_into_state(data, state)

        logger.info(
            "[ENGINE_STATE_RESTORE_SUCCESS] symbol=%s age_seconds=%.0f bias_phase=%s regime=%s",
            symbol, age_seconds, state.bias_phase, state.regime_state,
        )
        return state

    except Exception as exc:
        logger.warning("[ENGINE_STATE_RESTORE_SKIPPED] symbol=%s reason=exception error=%s", symbol, exc)
        return None
