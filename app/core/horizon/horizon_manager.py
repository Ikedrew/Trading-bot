"""
Horizon Manager — Central service for resolving horizon profiles.

ARCHITECTURAL INVARIANT:
    All components that need horizon-specific behaviour MUST resolve it
    through this manager. No hardcoded `if horizon == "SCALP":` logic.

Responsibilities:
    - Resolve horizon name → HorizonExecutionProfile
    - Validate horizon values
    - Provide default horizon (SCALP) when unspecified
    - Expose permitted horizons from config
    - Singleton access via get_horizon_manager()

This module does NOT own:
    - Horizon classification (owned by horizon_classifier)
    - Position limits (future: HorizonExecutionAuthority)
    - Trade management (owned by TradeStateManager)
    - Execution decisions

Usage:
    from core.horizon.horizon_manager import get_horizon_manager

    manager = get_horizon_manager()
    profile = manager.get_profile("INTRADAY")
    # profile.max_time_in_trade_seconds → horizon-specific value
"""

from __future__ import annotations

import logging
from typing import Any

from core.horizon.horizon_execution_profile import (
    DEFAULT_PROFILES,
    HorizonExecutionProfile,
)

logger = logging.getLogger(__name__)

# Default horizon used when none is specified (backward compatibility)
DEFAULT_HORIZON = "SCALP"

# Valid horizon names (must match TradeHorizon enum values)
VALID_HORIZONS = frozenset({"SCALP", "INTRADAY", "EXTENDED"})


class HorizonManager:
    """
    Central resolution service for horizon execution profiles.

    Consumers call get_profile(horizon_name) to obtain the correct
    profile for any given trade horizon. The manager guarantees:
        - Every valid horizon returns a profile
        - Invalid/missing horizons fall back to DEFAULT_HORIZON with a warning
        - Profiles are built once at startup from config values
        - Permitted horizons are gated by config

    Thread-safe: profiles are immutable (frozen dataclass), built at init.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, HorizonExecutionProfile] = {}
        self._permitted_horizons: list[str] = []
        self._initialise_from_config()

    def _initialise_from_config(self) -> None:
        """
        Build execution profiles from config values.

        Phase 3B: Each profile reads from HORIZON_TRADE_MANAGEMENT[horizon].
        Falls back to global TM_* values if per-horizon config is missing.
        """
        try:
            from core import config

            # Per-horizon trade management config (Phase 3B)
            _horizon_tm = getattr(config, "HORIZON_TRADE_MANAGEMENT", {})

            # Global TM_* values as fallback
            _global_tm = {
                "break_even_trigger_rr": float(getattr(config, "TM_BREAK_EVEN_TRIGGER_RR", 0.0)),
                "break_even_buffer_rr": float(getattr(config, "TM_BREAK_EVEN_BUFFER_RR", 0.0)),
                "trailing_step": float(getattr(config, "TM_TRAILING_STEP", 0.0)),
                "trailing_start_rr": float(getattr(config, "TM_TRAILING_START_RR", 0.0)),
                "partial_tp_fraction": float(getattr(config, "TM_PARTIAL_TP_FRACTION", 0.0)),
                "partial_tp_path_fraction": float(getattr(config, "TM_PARTIAL_TP_PATH_FRACTION", 0.0)),
                "max_time_in_trade_seconds": float(getattr(config, "TM_MAX_TIME_IN_TRADE_SECONDS", 0.0)),
            }

            # Read permitted horizons
            self._permitted_horizons = list(
                getattr(config, "PERMITTED_HORIZONS", ["SCALP"])
            )

            # Build profiles — each horizon gets its own TM values
            for name, default in DEFAULT_PROFILES.items():
                # Per-horizon config takes priority, then global fallback
                _htc = _horizon_tm.get(name, _global_tm)
                self._profiles[name] = HorizonExecutionProfile(
                    name=name,
                    break_even_trigger_rr=float(_htc.get("break_even_trigger_rr", _global_tm["break_even_trigger_rr"])),
                    break_even_buffer_rr=float(_htc.get("break_even_buffer_rr", _global_tm["break_even_buffer_rr"])),
                    trailing_step=float(_htc.get("trailing_step", _global_tm["trailing_step"])),
                    trailing_start_rr=float(_htc.get("trailing_start_rr", _global_tm["trailing_start_rr"])),
                    partial_tp_fraction=float(_htc.get("partial_tp_fraction", _global_tm["partial_tp_fraction"])),
                    partial_tp_path_fraction=float(_htc.get("partial_tp_path_fraction", _global_tm["partial_tp_path_fraction"])),
                    max_time_in_trade_seconds=float(_htc.get("max_time_in_trade_seconds", _global_tm["max_time_in_trade_seconds"])),
                    max_concurrent_positions=default.max_concurrent_positions,
                    allocation_weight=default.allocation_weight,
                    expected_hold_minutes_min=default.expected_hold_minutes_min,
                    expected_hold_minutes_max=default.expected_hold_minutes_max,
                    typical_rr=default.typical_rr,
                )

        except Exception as e:
            logger.warning(
                "[HORIZON_MANAGER] Failed to read config, using defaults: %s", e
            )
            self._profiles = dict(DEFAULT_PROFILES)
            self._permitted_horizons = ["SCALP"]

    def get_profile(self, horizon: str) -> HorizonExecutionProfile:
        """
        Resolve a horizon name to its execution profile.

        Args:
            horizon: Horizon name ("SCALP", "INTRADAY", "EXTENDED")

        Returns:
            HorizonExecutionProfile for the given horizon.
            Falls back to DEFAULT_HORIZON profile if horizon is invalid/unknown.
        """
        _upper = horizon.upper() if horizon else DEFAULT_HORIZON

        if _upper not in VALID_HORIZONS:
            logger.warning(
                "[HORIZON_MANAGER] Invalid horizon '%s', falling back to %s",
                horizon, DEFAULT_HORIZON,
            )
            _upper = DEFAULT_HORIZON

        profile = self._profiles.get(_upper)
        if profile is None:
            # Should never happen if _initialise_from_config succeeded
            logger.error(
                "[HORIZON_MANAGER] Profile not found for '%s', returning SCALP default",
                _upper,
            )
            return DEFAULT_PROFILES[DEFAULT_HORIZON]

        return profile

    def is_permitted(self, horizon: str) -> bool:
        """
        Check if a horizon is currently permitted for execution.

        Permitted horizons are controlled by config.PERMITTED_HORIZONS.
        Phase 1: Only "SCALP" is permitted.
        """
        return horizon.upper() in self._permitted_horizons

    @property
    def permitted_horizons(self) -> list[str]:
        """List of currently permitted horizons (from config)."""
        return list(self._permitted_horizons)

    @property
    def all_profiles(self) -> dict[str, HorizonExecutionProfile]:
        """All registered profiles (for iteration/observability)."""
        return dict(self._profiles)

    def validate_horizon(self, horizon: str) -> str:
        """
        Validate and normalize a horizon string.

        Returns:
            Uppercase horizon name if valid.
            DEFAULT_HORIZON if invalid (with warning logged).
        """
        _upper = horizon.upper() if horizon else DEFAULT_HORIZON
        if _upper not in VALID_HORIZONS:
            logger.warning(
                "[HORIZON_MANAGER] Invalid horizon value '%s', defaulting to %s",
                horizon, DEFAULT_HORIZON,
            )
            return DEFAULT_HORIZON
        return _upper


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

_instance: HorizonManager | None = None


def get_horizon_manager() -> HorizonManager:
    """
    Get the global HorizonManager singleton.

    Thread-safe on first call (Python GIL protects single assignment).
    Subsequent calls return the cached instance.
    """
    global _instance
    if _instance is None:
        _instance = HorizonManager()
    return _instance


def reset_horizon_manager() -> None:
    """
    Reset the singleton (for testing only).

    Allows tests to reinitialise with different config values.
    """
    global _instance
    _instance = None
