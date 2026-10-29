"""
G4: Configuration Profile Loader.

Implements deterministic configuration layering:
    base config → profile overrides → environment overrides

Ensures deployment mode switching (retail/prop_challenge/prop_funded)
requires only an environment variable change — no manual config edits.

Startup fails fast if:
- Profile contains unknown keys not in base config
- Profile contains type mismatches
- Profile module is missing OVERRIDES dict
- Profile module cannot be found
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ─── HIGH-RISK KEYS (warn when overridden) ────────────────────────────────────

_HIGH_RISK_KEYS = frozenset({
    "MAX_DRAWDOWN_PERCENT",
    "DAILY_LOSS_LIMIT_PERCENT",
    "MAX_TOTAL_OPEN_POSITIONS",
    "MAX_TOTAL_RISK_EXPOSURE_PCT",
    "RISK_PER_TRADE_PERCENT",
    "ENABLE_DRAWDOWN_GUARD",
    "ENABLE_DAILY_LOSS_LIMIT",
})

# ─── ENV VAR NAME ─────────────────────────────────────────────────────────────

PROFILE_ENV_VAR = "TRADING_PROFILE"


# ─── ERRORS ───────────────────────────────────────────────────────────────────

class ConfigProfileError(SystemExit):
    """Fatal error in configuration profile — system cannot start safely."""
    pass


# ─── TYPE COERCION FOR ENV VARS ───────────────────────────────────────────────

def _coerce_env_value(value_str: str, reference_value: Any) -> Any:
    """
    Coerce a string environment variable to match the type of the reference value.

    Supports: int, float, bool, str, None (treated as str).
    Returns the coerced value, or raises ValueError on failure.
    """
    if reference_value is None:
        # Can't infer type — return as string
        return value_str

    ref_type = type(reference_value)

    if ref_type is bool:
        # Bool must be handled before int (bool is subclass of int)
        lower = value_str.lower().strip()
        if lower in ("true", "1", "yes", "on"):
            return True
        elif lower in ("false", "0", "no", "off"):
            return False
        else:
            raise ValueError(f"Cannot coerce '{value_str}' to bool")

    if ref_type is int:
        return int(value_str)

    if ref_type is float:
        return float(value_str)

    if ref_type is str:
        return value_str

    # Unsupported type — return as string
    return value_str


# ─── VALIDATION ───────────────────────────────────────────────────────────────

def _validate_overrides(overrides: dict[str, Any], base_config: Any) -> list[str]:
    """
    Validate profile overrides against base config.

    Returns list of error messages (empty = valid).
    Checks:
    1. All keys in overrides must exist in base config
    2. Types must be compatible (basic type check)
    """
    errors: list[str] = []

    for key, value in overrides.items():
        # Check key exists in base config
        if not hasattr(base_config, key):
            errors.append(
                f"Unknown config key '{key}' in profile — "
                f"not found in base config"
            )
            continue

        # Check type compatibility
        base_value = getattr(base_config, key)
        if base_value is None:
            continue  # Can't validate type against None

        base_type = type(base_value)
        value_type = type(value)

        # Allow int/float interchangeability (both are numeric)
        numeric_types = (int, float)
        if base_type in numeric_types and value_type in numeric_types:
            # Both numeric — but exclude bool masquerading as int
            if value_type is bool or base_type is bool:
                errors.append(
                    f"Type mismatch for '{key}': base is {base_type.__name__}, "
                    f"profile provides {value_type.__name__} ({value!r})"
                )
            continue

        # Allow bool specifically (don't let int pass as bool)
        if base_type is bool and value_type is not bool:
            errors.append(
                f"Type mismatch for '{key}': base is {base_type.__name__}, "
                f"profile provides {value_type.__name__} ({value!r})"
            )
            continue

        if base_type is not bool and value_type is bool:
            errors.append(
                f"Type mismatch for '{key}': base is {base_type.__name__}, "
                f"profile provides bool ({value!r})"
            )
            continue

        # General type check
        if not isinstance(value, base_type):
            errors.append(
                f"Type mismatch for '{key}': base is {base_type.__name__}, "
                f"profile provides {value_type.__name__} ({value!r})"
            )

    return errors


# ─── PROFILE LOADING ──────────────────────────────────────────────────────────

def _load_profile_module(profile_name: str) -> Any:
    """
    Load a profile module by name.

    Looks for: profiles/<profile_name>.py

    Raises ConfigProfileError if module not found or invalid.
    """
    module_path = f"profiles.{profile_name}"

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        _fatal(
            f"Profile '{profile_name}' not found. "
            f"Expected module at profiles/{profile_name}.py. "
            f"Error: {exc}"
        )
        return None  # unreachable

    # Validate OVERRIDES dict exists
    overrides = getattr(module, "OVERRIDES", None)
    if overrides is None or not isinstance(overrides, dict):
        _fatal(
            f"Profile '{profile_name}' is missing OVERRIDES dict. "
            f"Each profile must define: OVERRIDES = {{...}}"
        )
        return None  # unreachable

    return module


def _apply_overrides(config_module: Any, overrides: dict[str, Any]) -> int:
    """
    Apply override dict to config module.

    Returns number of values applied.
    """
    applied = 0
    for key, value in overrides.items():
        setattr(config_module, key, value)
        applied += 1
    return applied


# ─── ENVIRONMENT OVERRIDES ────────────────────────────────────────────────────

def _apply_env_overrides(config_module: Any) -> int:
    """
    Apply environment variable overrides to config.

    For each env var that matches a config key (case-sensitive),
    coerce the string value to the appropriate type and apply.

    Returns number of env overrides applied.
    """
    applied = 0

    for key in dir(config_module):
        # Skip private/dunder attributes
        if key.startswith("_"):
            continue

        # Check if env var exists with this name
        env_value = os.environ.get(key)
        if env_value is None:
            continue

        # Get reference value for type coercion
        base_value = getattr(config_module, key)

        # Skip non-overridable types (modules, functions, classes, complex structures)
        if callable(base_value) or isinstance(base_value, (type, frozenset, list, dict, tuple)):
            continue

        try:
            coerced = _coerce_env_value(env_value, base_value)
            setattr(config_module, key, coerced)
            applied += 1
            logger.info(
                "[CONFIG] env_override key=%s value=%s",
                key, repr(coerced),
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[CONFIG] env_override_failed key=%s value=%r error=%s — skipped",
                key, env_value, exc,
            )

    return applied


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def load_and_apply_profile() -> str | None:
    """
    Load and apply the active configuration profile.

    Reads TRADING_PROFILE env var to determine which profile to load.
    If not set, no profile is applied (base config only).

    Layering order:
        1. Base config (core/config.py) — already loaded
        2. Profile overrides (profiles/<name>.py OVERRIDES dict)
        3. Environment variable overrides (final priority)

    Returns:
        Profile name if applied, None if no profile selected.

    Raises:
        ConfigProfileError on validation failure (unknown keys, type mismatch).
    """
    from core import config

    logger.info("[CONFIG] Base config loaded")

    # ─── STEP 1: Check for active profile ─────────────────────────────
    profile_name = os.environ.get(PROFILE_ENV_VAR, "").strip()

    if not profile_name:
        # No profile selected — apply env overrides only
        env_count = _apply_env_overrides(config)
        if env_count > 0:
            logger.info("[CONFIG] Environment overrides applied: %d values", env_count)
        logger.info("[CONFIG] No profile selected (TRADING_PROFILE not set)")
        return None

    # ─── STEP 2: Load profile module ──────────────────────────────────
    profile_module = _load_profile_module(profile_name)
    overrides = profile_module.OVERRIDES

    logger.info("[CONFIG] Profile loaded: %s", profile_name)

    # ─── STEP 3: Validate overrides ──────────────────────────────────
    errors = _validate_overrides(overrides, config)
    if errors:
        error_list = "\n  - ".join(errors)
        _fatal(
            f"Profile '{profile_name}' validation failed:\n  - {error_list}"
        )

    # ─── STEP 4: Warn on high-risk overrides ──────────────────────────
    high_risk_overridden = [k for k in overrides if k in _HIGH_RISK_KEYS]
    if high_risk_overridden:
        logger.warning(
            "[CONFIG] Profile '%s' overrides high-risk settings: %s",
            profile_name, ", ".join(sorted(high_risk_overridden)),
        )

    # ─── STEP 5: Apply profile overrides ──────────────────────────────
    override_count = _apply_overrides(config, overrides)
    logger.info("[CONFIG] Overrides applied: %d values", override_count)

    # ─── STEP 6: Apply environment overrides (final priority) ─────────
    env_count = _apply_env_overrides(config)
    if env_count > 0:
        logger.info("[CONFIG] Environment overrides applied: %d values", env_count)

    # ─── STEP 7: Store active profile name on config for observability ─
    config.ACTIVE_PROFILE = profile_name

    return profile_name


# ─── INTERNAL ─────────────────────────────────────────────────────────────────

def _fatal(message: str) -> None:
    """Log critical error and abort startup."""
    full_msg = f"[CONFIG_PROFILE_FATAL] {message} Startup aborted."
    logger.critical(full_msg)
    raise ConfigProfileError(full_msg)
