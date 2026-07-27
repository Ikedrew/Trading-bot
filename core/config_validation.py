"""
Config validation and freeze — runs once at startup.

Validates all critical runtime config values, fails fast on invalid configuration,
then freezes the config module against accidental mutation.
"""

from __future__ import annotations

import logging
import time

from core import config

logger = logging.getLogger(__name__)


class ConfigValidationError(ValueError):
    """Raised when config validation fails."""
    pass


def _check(condition: bool, message: str, errors: list[str]) -> None:
    """Append error message if condition is False."""
    if not condition:
        errors.append(message)


def _validate_all() -> list[str]:
    """Run all validation checks. Returns list of error messages (empty = valid)."""
    errors: list[str] = []

    # ─── RISK PARAMETERS ──────────────────────────────────────────────
    _check(
        isinstance(getattr(config, "FIXED_LOT", None), (int, float)) and config.FIXED_LOT > 0,
        f"FIXED_LOT must be > 0 (got {getattr(config, 'FIXED_LOT', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "BASE_RR", None), (int, float)) and config.BASE_RR >= 1.0,
        f"BASE_RR must be >= 1.0 (got {getattr(config, 'BASE_RR', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "MIN_RR", None), (int, float)) and config.MIN_RR >= 0,
        f"MIN_RR must be >= 0 (got {getattr(config, 'MIN_RR', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "SL_BUFFER", None), (int, float)) and config.SL_BUFFER >= 0,
        f"SL_BUFFER must be >= 0 (got {getattr(config, 'SL_BUFFER', None)})",
        errors,
    )
    # Logical: MIN_RR <= BASE_RR
    if isinstance(getattr(config, "MIN_RR", None), (int, float)) and isinstance(getattr(config, "BASE_RR", None), (int, float)):
        _check(
            config.MIN_RR <= config.BASE_RR,
            f"MIN_RR ({config.MIN_RR}) must be <= BASE_RR ({config.BASE_RR})",
            errors,
        )

    # ─── TIMING PARAMETERS ────────────────────────────────────────────
    _check(
        isinstance(getattr(config, "POLL_SECONDS", None), (int, float)) and config.POLL_SECONDS > 0,
        f"POLL_SECONDS must be > 0 (got {getattr(config, 'POLL_SECONDS', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "COOLDOWN_SECONDS", None), (int, float)) and config.COOLDOWN_SECONDS >= 0,
        f"COOLDOWN_SECONDS must be >= 0 (got {getattr(config, 'COOLDOWN_SECONDS', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "MT5_RECONNECT_COOLDOWN_SECONDS", None), (int, float)) and config.MT5_RECONNECT_COOLDOWN_SECONDS > 0,
        f"MT5_RECONNECT_COOLDOWN_SECONDS must be > 0 (got {getattr(config, 'MT5_RECONNECT_COOLDOWN_SECONDS', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "MT5_RECONNECT_MAX_COOLDOWN_SECONDS", None), (int, float)) and config.MT5_RECONNECT_MAX_COOLDOWN_SECONDS > 0,
        f"MT5_RECONNECT_MAX_COOLDOWN_SECONDS must be > 0 (got {getattr(config, 'MT5_RECONNECT_MAX_COOLDOWN_SECONDS', None)})",
        errors,
    )

    # ─── STALE DATA DETECTION ─────────────────────────────────────────
    _check(
        isinstance(getattr(config, "STALE_TICK_TIMEOUT_SECONDS", None), (int, float)) and config.STALE_TICK_TIMEOUT_SECONDS > 0,
        f"STALE_TICK_TIMEOUT_SECONDS must be > 0 (got {getattr(config, 'STALE_TICK_TIMEOUT_SECONDS', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "STALE_CANDLE_TIMEOUT_SECONDS", None), (int, float)) and config.STALE_CANDLE_TIMEOUT_SECONDS > 0,
        f"STALE_CANDLE_TIMEOUT_SECONDS must be > 0 (got {getattr(config, 'STALE_CANDLE_TIMEOUT_SECONDS', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "LIVENESS_STALL_THRESHOLD_SECONDS", None), (int, float)) and config.LIVENESS_STALL_THRESHOLD_SECONDS > 0,
        f"LIVENESS_STALL_THRESHOLD_SECONDS must be > 0 (got {getattr(config, 'LIVENESS_STALL_THRESHOLD_SECONDS', None)})",
        errors,
    )

    # ─── TYPE CHECKS ──────────────────────────────────────────────────
    _canonical = getattr(config, "CANONICAL_SYMBOLS", None)
    _symbols = getattr(config, "SYMBOLS", None)
    _has_symbols = (
        (isinstance(_canonical, (list, tuple)) and len(_canonical) > 0)
        or (isinstance(_symbols, (list, tuple)) and len(_symbols) > 0)
    )
    _check(
        _has_symbols,
        f"CANONICAL_SYMBOLS or SYMBOLS must be a non-empty list/tuple",
        errors,
    )
    # Validate each symbol is a string
    symbols = list(_canonical or _symbols or [])
    if isinstance(symbols, (list, tuple)):
        for i, s in enumerate(symbols):
            _check(
                isinstance(s, str) and len(s) > 0,
                f"SYMBOLS[{i}] must be a non-empty string (got {s!r})",
                errors,
            )
    _check(
        isinstance(getattr(config, "BOT_MAGIC", None), int),
        f"BOT_MAGIC must be int (got {type(getattr(config, 'BOT_MAGIC', None)).__name__})",
        errors,
    )
    # G1: Validate strategy registry exists
    _registry = getattr(config, "MAGIC_NUMBER_REGISTRY", None)
    _check(
        isinstance(_registry, dict) and len(_registry) > 0,
        "MAGIC_NUMBER_REGISTRY must be a non-empty dict",
        errors,
    )
    _strategy_name = getattr(config, "STRATEGY_NAME", None)
    _check(
        isinstance(_strategy_name, str) and len(_strategy_name) > 0,
        "STRATEGY_NAME must be a non-empty string",
        errors,
    )
    _check(
        isinstance(getattr(config, "TIMEFRAME", None), int),
        f"TIMEFRAME must be int (got {type(getattr(config, 'TIMEFRAME', None)).__name__})",
        errors,
    )
    _check(
        isinstance(getattr(config, "CANDLE_COUNT", None), int) and config.CANDLE_COUNT > 0,
        f"CANDLE_COUNT must be int > 0 (got {getattr(config, 'CANDLE_COUNT', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "MAX_OPEN_POSITIONS", None), int) and config.MAX_OPEN_POSITIONS >= 1,
        f"MAX_OPEN_POSITIONS must be int >= 1 (got {getattr(config, 'MAX_OPEN_POSITIONS', None)})",
        errors,
    )

    # ─── TRADE MANAGEMENT ─────────────────────────────────────────────
    _check(
        isinstance(getattr(config, "TM_BREAK_EVEN_TRIGGER_RR", None), (int, float)) and config.TM_BREAK_EVEN_TRIGGER_RR >= 0,
        f"TM_BREAK_EVEN_TRIGGER_RR must be >= 0 (got {getattr(config, 'TM_BREAK_EVEN_TRIGGER_RR', None)})",
        errors,
    )
    _check(
        isinstance(getattr(config, "TM_TRAILING_START_RR", None), (int, float)) and config.TM_TRAILING_START_RR >= 0,
        f"TM_TRAILING_START_RR must be >= 0 (got {getattr(config, 'TM_TRAILING_START_RR', None)})",
        errors,
    )
    tp_frac = getattr(config, "TM_PARTIAL_TP_FRACTION", None)
    _check(
        isinstance(tp_frac, (int, float)) and 0 <= tp_frac <= 1,
        f"TM_PARTIAL_TP_FRACTION must be between 0 and 1 (got {tp_frac})",
        errors,
    )
    tp_path = getattr(config, "TM_PARTIAL_TP_PATH_FRACTION", None)
    _check(
        isinstance(tp_path, (int, float)) and 0 <= tp_path <= 1,
        f"TM_PARTIAL_TP_PATH_FRACTION must be between 0 and 1 (got {tp_path})",
        errors,
    )

    # ─── REPLAY WINDOW LOGIC ──────────────────────────────────────────
    start_t = getattr(config, "REPLAY_START_TIME", None)
    end_t = getattr(config, "REPLAY_END_TIME", None)
    if start_t is not None and end_t is not None:
        _check(
            int(start_t) <= int(end_t),
            f"REPLAY_START_TIME ({start_t}) must be <= REPLAY_END_TIME ({end_t})",
            errors,
        )

    # ─── PRINT MODE ───────────────────────────────────────────────────
    valid_modes = {"FULL_DEBUG", "EVENT_ONLY", "SILENT"}
    pm = str(getattr(config, "PRINT_MODE", "EVENT_ONLY")).upper()
    _check(
        pm in valid_modes,
        f"PRINT_MODE must be one of {valid_modes} (got {pm!r})",
        errors,
    )

    # ─── POSITION SIZING ──────────────────────────────────────────────
    valid_sizing = {"FIXED", "DYNAMIC"}
    sizing = str(getattr(config, "POSITION_SIZING_MODE", "FIXED")).upper()
    _check(
        sizing in valid_sizing,
        f"POSITION_SIZING_MODE must be one of {valid_sizing} (got {sizing!r})",
        errors,
    )
    risk_pct = getattr(config, "RISK_PER_TRADE_PERCENT", None)
    if sizing == "DYNAMIC":
        _check(
            isinstance(risk_pct, (int, float)) and 0 < risk_pct <= 10.0,
            f"RISK_PER_TRADE_PERCENT must be > 0 and <= 10.0 for DYNAMIC mode (got {risk_pct})",
            errors,
        )

    return errors


# ─── FREEZE MECHANISM ─────────────────────────────────────────────────────────

_config_frozen = False


class _FrozenConfigGuard:
    """Module-level __setattr__ interceptor to prevent config mutation after freeze."""

    def __init__(self, module):
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_frozen", False)

    def freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_module"), name)

    def __setattr__(self, name: str, value):
        if object.__getattribute__(self, "_frozen"):
            raise RuntimeError(f"Config is frozen — cannot set '{name}' after validation")
        setattr(object.__getattribute__(self, "_module"), name, value)


def validate_and_freeze_config() -> None:
    """
    Validate all critical config values and freeze the module against mutation.
    Call once at startup before any runtime execution.

    Raises ConfigValidationError on invalid configuration.
    """
    import sys

    start = time.time()
    logger.info("[CONFIG_VALIDATION_START]")

    errors = _validate_all()

    if errors:
        for err in errors:
            logger.critical("[CONFIG_VALIDATION_ERROR] %s", err)
        raise ConfigValidationError(
            f"Config validation failed with {len(errors)} error(s):\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info("[CONFIG_VALIDATION_SUCCESS] validated in %dms", elapsed_ms)

    # Freeze config module
    guard = _FrozenConfigGuard(sys.modules["core.config"])
    guard.freeze()
    sys.modules["core.config"] = guard  # type: ignore[assignment]
    logger.info("[CONFIG_FROZEN] config module is now immutable")
