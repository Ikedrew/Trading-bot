"""
B4: Position Ownership Validation — Cross-strategy interference prevention.

Enforces that every strategy instance can ONLY view, modify, and close
positions belonging to its assigned magic number.

A position belongs to exactly one strategy identity (magic number).
No cross-magic modification is ever allowed.

Builds on G1 Strategy Identity / Magic Registry.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _get_expected_magic() -> int:
    """Get the bot's assigned magic number from config."""
    try:
        from core import config
        return int(getattr(config, "BOT_MAGIC", 0))
    except ImportError:
        return 0


def _is_strict() -> bool:
    """Strict mode: blocks on violation. Non-strict: warns only."""
    try:
        from core import config
        return bool(getattr(config, "STRICT_POSITION_OWNERSHIP", True))
    except ImportError:
        return True


# ─── REJECTION REASON ─────────────────────────────────────────────────────────

REJECT_OWNERSHIP_VIOLATION = "POSITION_OWNERSHIP_VIOLATION"


# ─── CORE VALIDATION ─────────────────────────────────────────────────────────

def validate_position_ownership(position_magic: int, expected_magic: int | None = None) -> bool:
    """
    Validate that a position belongs to the current strategy.

    Args:
        position_magic: The magic number on the position being acted upon.
        expected_magic: Expected magic (default: from config.BOT_MAGIC).

    Returns:
        True if position belongs to this strategy, False otherwise.
    """
    if expected_magic is None:
        expected_magic = _get_expected_magic()

    return int(position_magic) == expected_magic


def enforce_position_ownership(
    *,
    position_magic: int,
    action: str,
    symbol: str = "",
    ticket: int = 0,
    expected_magic: int | None = None,
) -> bool:
    """
    Enforce ownership before any position modification.

    Logs CRITICAL and returns False if violation detected.
    In strict mode: action must be blocked.
    In non-strict mode: logs warning but returns True (allow).

    Args:
        position_magic: Magic number on the position.
        action: What's being attempted (CLOSE, MODIFY_SL_TP, PARTIAL_CLOSE).
        symbol: Symbol for logging context.
        ticket: Position ticket for logging context.
        expected_magic: Expected magic (default: from config.BOT_MAGIC).

    Returns:
        True if action is allowed, False if blocked.
    """
    if expected_magic is None:
        expected_magic = _get_expected_magic()

    if int(position_magic) == expected_magic:
        return True

    # VIOLATION DETECTED
    strict = _is_strict()

    if strict:
        logger.critical(
            "[OWNERSHIP_VIOLATION] Strategy attempted to %s foreign position "
            "Expected magic: %d Actual magic: %d symbol=%s ticket=%d Action: BLOCKED",
            action, expected_magic, position_magic, symbol, ticket,
        )
        return False
    else:
        logger.warning(
            "[OWNERSHIP_WARNING] Strategy attempted to %s foreign position "
            "Expected magic: %d Actual magic: %d symbol=%s ticket=%d "
            "Action: ALLOWED (strict mode disabled)",
            action, expected_magic, position_magic, symbol, ticket,
        )
        return True


def filter_owned_positions(positions: list[Any], expected_magic: int | None = None) -> list[Any]:
    """
    Filter a list of positions to only those owned by current strategy.

    Args:
        positions: List of position objects (must have .magic attribute).
        expected_magic: Expected magic (default: from config.BOT_MAGIC).

    Returns:
        Filtered list containing only positions matching our magic.
    """
    if expected_magic is None:
        expected_magic = _get_expected_magic()

    return [p for p in positions if int(getattr(p, "magic", 0)) == expected_magic]


# ─── STARTUP SCAN ─────────────────────────────────────────────────────────────

def scan_foreign_positions() -> list[dict]:
    """
    Scan broker for positions NOT belonging to current strategy.

    Returns list of foreign position summaries (for logging/alerting).
    Does not block startup — informational only.
    """
    expected_magic = _get_expected_magic()
    if expected_magic <= 0:
        return []

    try:
        import MetaTrader5 as mt5
        from core.mt5_timeout import mt5_call

        all_positions = mt5_call(mt5.positions_get)
        if all_positions is None or not all_positions:
            return []

        foreign = []
        for pos in all_positions:
            if int(pos.magic) != expected_magic:
                foreign.append({
                    "ticket": int(pos.ticket),
                    "symbol": str(pos.symbol),
                    "magic": int(pos.magic),
                    "volume": float(pos.volume),
                })

        if foreign:
            logger.warning(
                "[OWNERSHIP_SCAN] Found %d foreign positions on account "
                "(not managed by magic=%d): %s",
                len(foreign), expected_magic,
                ", ".join(f"{p['symbol']}(magic={p['magic']})" for p in foreign[:5]),
            )

        return foreign

    except Exception as exc:
        logger.warning("[OWNERSHIP_SCAN] scan_error=%s", exc)
        return []


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_ownership_config() -> list[str]:
    """Validate ownership configuration at startup."""
    errors: list[str] = []
    magic = _get_expected_magic()
    if magic <= 0:
        errors.append(f"BOT_MAGIC must be > 0 for ownership validation (got {magic})")
    return errors

# ═══════════════════════════════════════════════════════════════════════════════════
# PHASE G: ACCOUNT-OWNED POSITION IDENTITY (account_id + position_ticket)
# ═══════════════════════════════════════════════════════════════════════════════════
#
# A broker ticket alone is NOT a sufficient position identity.
# The ownership boundary is (account_id, position_ticket).
#

from typing import NamedTuple


class PositionOwnership(NamedTuple):
    """Account-scoped position identity for management routing."""
    account_id: str
    broker: str
    broker_server: str
    position_ticket: int
    order_ticket: int | None = None
    deal_ticket: int | None = None
    canonical_symbol: str | None = None
    broker_symbol: str | None = None
    canonical_opportunity_id: str | None = None
    correlation_id: str | None = None
    decision_id: str | None = None
    account_execution_id: str | None = None
    trade_id: str | None = None

def validate_account_position_ownership(
    position: PositionOwnership,
    expected_account_id: str,
    expected_broker: str | None = None,
    expected_broker_server: str | None = None,
) -> bool:
    """
    Validate that a position belongs to the expected account.

    The ownership boundary is (account_id, position_ticket).
    A broker ticket alone is NOT sufficient — same numeric ticket on
    different accounts must represent distinct positions.
    """
    if not position.account_id or position.position_ticket <= 0:
        return False
    if position.account_id != expected_account_id:
        return False
    if expected_broker and position.broker != expected_broker:
        return False
    if expected_broker_server and position.broker_server != expected_broker_server:
        return False
    return True



def enforce_account_position_ownership(
    *,
    position: PositionOwnership,
    expected_account_id: str,
    action: str,
    symbol: str = "",
    strict: bool = True,
) -> bool:
    """
    Enforce account-owned position ownership before any management action.
    """
    if validate_account_position_ownership(position, expected_account_id):
        return True
    if strict:
        logger.critical(
            "[OWNERSHIP_VIOLATION] Account=%s attempted to %s position "
            "ticket=%d owned by account=%s broker=%s server=%s "
            "Action: BLOCKED symbol=%s",
            expected_account_id, action, position.position_ticket,
            position.account_id, position.broker, position.broker_server,
            symbol,
        )
        return False
    else:
        logger.warning(
            "[OWNERSHIP_WARNING] Account=%s attempted to %s position "
            "ticket=%d owned by account=%s broker=%s server=%s "
            "Action: ALLOWED (strict mode disabled) symbol=%s",
            expected_account_id, action, position.position_ticket,
            position.account_id, position.broker, position.broker_server,
            symbol,
        )
        return True

def build_position_ownership(
    *,
    account_id: str,
    broker: str,
    broker_server: str,
    position_ticket: int,
    order_ticket: int | None = None,
    deal_ticket: int | None = None,
    canonical_symbol: str | None = None,
    broker_symbol: str | None = None,
    canonical_opportunity_id: str | None = None,
    correlation_id: str | None = None,
    decision_id: str | None = None,
    account_execution_id: str | None = None,
    trade_id: str | None = None,
) -> PositionOwnership:
    """Build a PositionOwnership record from available lifecycle data."""
    return PositionOwnership(
        account_id=account_id,
        broker=broker,
        broker_server=broker_server,
        position_ticket=position_ticket,
        order_ticket=order_ticket,
        deal_ticket=deal_ticket,
        canonical_symbol=canonical_symbol,
        broker_symbol=broker_symbol,
        canonical_opportunity_id=canonical_opportunity_id,
        correlation_id=correlation_id,
        decision_id=decision_id,
        account_execution_id=account_execution_id,
        trade_id=trade_id,
    )


def position_identity_key(position: PositionOwnership) -> tuple:
    """Return the ownership identity key: (account_id, position_ticket)."""
    return (position.account_id, position.position_ticket)


def positions_equal_by_identity(pos1: PositionOwnership, pos2: PositionOwnership) -> bool:
    """Check if two positions represent the same owned position."""
    return position_identity_key(pos1) == position_identity_key(pos2)
