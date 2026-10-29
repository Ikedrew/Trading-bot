"""
G1: Strategy Identity / Magic Number Registry.

Provides a validated strategy identity system that allows multiple strategies
to coexist safely on the same MT5 account. Each strategy instance is assigned
a unique magic number from the registry.

Guarantees:
- One strategy instance can never manage, count, modify, or close positions
  belonging to another strategy.
- Startup fails fast if registry is invalid (duplicates, missing strategy, etc.)
- All trading operations use the assigned magic from the identity context.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── STRATEGY IDENTITY ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyIdentity:
    """
    Immutable strategy identity loaded once at startup.

    All runtime operations must use identity.magic_number instead of
    hardcoded BOT_MAGIC values.
    """
    strategy_name: str
    magic_number: int


# Module-level singleton — set once during startup via resolve_strategy_identity()
_active_identity: StrategyIdentity | None = None


def get_identity() -> StrategyIdentity:
    """
    Return the active strategy identity.

    Raises RuntimeError if called before startup validation completes.
    """
    if _active_identity is None:
        raise RuntimeError(
            "[STRATEGY_IDENTITY] Identity not initialized. "
            "Call resolve_strategy_identity() during startup."
        )
    return _active_identity


# ─── REGISTRY VALIDATION ──────────────────────────────────────────────────────

class StrategyRegistryError(SystemExit):
    """Fatal error in strategy registry — system cannot start safely."""
    pass


def validate_magic_registry() -> None:
    """
    Validate the magic number registry at startup.

    Checks:
    1. MAGIC_NUMBER_REGISTRY exists and is non-empty
    2. STRATEGY_NAME is configured
    3. STRATEGY_NAME exists in registry
    4. All magic numbers are integers
    5. All magic numbers are unique (no duplicates)

    Raises StrategyRegistryError (SystemExit) on any failure.
    """
    from core import config

    registry = getattr(config, "MAGIC_NUMBER_REGISTRY", None)
    strategy_name = getattr(config, "STRATEGY_NAME", None)

    # Check 1: Registry exists and is non-empty
    if not registry or not isinstance(registry, dict):
        _fatal(
            "MAGIC_NUMBER_REGISTRY is missing or empty. "
            "Cannot operate without strategy identity."
        )

    # Check 2: STRATEGY_NAME is configured
    if not strategy_name or not isinstance(strategy_name, str):
        _fatal(
            "STRATEGY_NAME is not configured. "
            "Set STRATEGY_NAME to one of the registered strategies."
        )

    # Check 3: Strategy exists in registry
    if strategy_name not in registry:
        available = ", ".join(sorted(registry.keys()))
        _fatal(
            f"STRATEGY_NAME='{strategy_name}' not found in MAGIC_NUMBER_REGISTRY. "
            f"Available strategies: [{available}]"
        )

    # Check 4: All magic numbers are integers
    for name, magic in registry.items():
        if not isinstance(magic, int):
            _fatal(
                f"MAGIC_NUMBER_REGISTRY['{name}'] = {magic!r} — "
                f"magic numbers must be integers."
            )

    # Check 5: No duplicate magic numbers
    seen: dict[int, str] = {}
    for name, magic in registry.items():
        if magic in seen:
            _fatal(
                f"Duplicate magic detected: {magic} is assigned to both "
                f"'{seen[magic]}' and '{name}'. "
                f"Each strategy must have a unique magic number."
            )
        seen[magic] = name

    logger.info(
        "[STRATEGY_REGISTRY] validated registry=%d strategies, active=%s magic=%d",
        len(registry), strategy_name, registry[strategy_name],
    )


def resolve_strategy_identity() -> StrategyIdentity:
    """
    Validate registry and resolve the active strategy identity.

    Must be called once during startup, AFTER config is loaded.
    Sets the module-level singleton and returns the identity.

    This also maintains backward compatibility by ensuring config.BOT_MAGIC
    reflects the resolved magic number.
    """
    global _active_identity

    # Validate first
    validate_magic_registry()

    from core import config

    strategy_name = config.STRATEGY_NAME
    magic_number = config.MAGIC_NUMBER_REGISTRY[strategy_name]

    # Create immutable identity
    _active_identity = StrategyIdentity(
        strategy_name=strategy_name,
        magic_number=magic_number,
    )

    # Ensure config.BOT_MAGIC reflects the resolved magic for backward compatibility
    # This means all existing code reading config.BOT_MAGIC gets the correct value.
    config.BOT_MAGIC = magic_number

    logger.info(
        "[STRATEGY_IDENTITY] resolved strategy=%s magic=%d",
        strategy_name, magic_number,
    )

    return _active_identity


# ─── INTERNAL ─────────────────────────────────────────────────────────────────

def _fatal(message: str) -> None:
    """Log critical error and abort startup."""
    full_msg = f"[STRATEGY_REGISTRY_FATAL] {message} Startup aborted."
    logger.critical(full_msg)
    raise StrategyRegistryError(full_msg)
