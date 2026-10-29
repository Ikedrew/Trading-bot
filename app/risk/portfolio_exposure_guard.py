"""
A5: Portfolio Exposure Guard — Aggregate position count + risk cap.

Prevents portfolio-level overexposure by enforcing:
1. Maximum total concurrent open positions (across all symbols)
2. Maximum aggregate portfolio risk percentage

Stateless: derives exposure from live broker state (position count)
and TradeStateManager (risk calculation).

Fail-closed: if position state is unknown, blocks new entries.
Reconstructed automatically after D3 startup recovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call

if TYPE_CHECKING:
    from core.trade_management.position import Position

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "PORTFOLIO_EXPOSURE_GUARD_ENABLED", True))
    except ImportError:
        return True


def _get_max_positions() -> int:
    try:
        from core import config
        return int(getattr(config, "MAX_TOTAL_OPEN_POSITIONS", 3))
    except ImportError:
        return 3


def _get_max_risk_pct() -> float:
    try:
        from core import config
        return float(getattr(config, "MAX_TOTAL_RISK_EXPOSURE_PCT", 3.0))
    except ImportError:
        return 3.0


def _get_bot_magic() -> int:
    try:
        from core.strategy_identity import get_identity
        return get_identity().magic_number
    except (ImportError, RuntimeError):
        try:
            from core import config
            return int(getattr(config, "BOT_MAGIC", 713001))
        except ImportError:
            return 713001


def _get_strict_mode() -> bool:
    try:
        from core import config
        return bool(getattr(config, "STRICT_EXPOSURE_GUARDS", True))
    except ImportError:
        return True


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

REJECT_POSITION_LIMIT = "PORTFOLIO_POSITION_LIMIT"
REJECT_RISK_LIMIT = "PORTFOLIO_RISK_LIMIT"
REJECT_EXPOSURE_STATE_UNKNOWN = "PORTFOLIO_EXPOSURE_STATE_UNKNOWN"


@dataclass(frozen=True)
class PortfolioExposureResult:
    """Result of portfolio exposure guard evaluation."""
    allowed: bool
    reason: str = ""
    current_positions: int = 0
    max_positions: int = 0
    current_risk_pct: float = 0.0
    projected_risk_pct: float = 0.0
    max_risk_pct: float = 0.0


# ─── RISK CALCULATION ─────────────────────────────────────────────────────────

def _compute_position_risk_pct(position: "Position") -> float:
    """
    Compute risk percentage for a single position from its SL distance and volume.

    Uses MT5 order_calc_profit to convert SL distance to account currency,
    then divides by account balance.

    Falls back to config.RISK_PER_TRADE_PERCENT if MT5 calculation fails
    (conservative assumption: each position risks the configured amount).
    """
    from strategy.signals import Side

    # Validate: must have a meaningful stop-loss
    if position.stop_loss <= 0 or position.entry_price <= 0:
        # No SL → use configured risk per trade as conservative estimate
        try:
            from core import config
            return float(getattr(config, "RISK_PER_TRADE_PERCENT", 1.0))
        except ImportError:
            return 1.0

    # Calculate loss at SL for this position's volume
    try:
        if position.side is Side.BUY:
            order_type = mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL

        # Calculate P&L if price moved from entry to stop-loss
        loss = mt5_call(
            mt5.order_calc_profit,
            order_type,
            position.symbol,
            position.volume,
            position.entry_price,
            position.stop_loss,
        )

        if loss is None:
            # MT5 calc failed — use conservative fallback
            try:
                from core import config
                return float(getattr(config, "RISK_PER_TRADE_PERCENT", 1.0))
            except ImportError:
                return 1.0

        # loss is negative for a losing trade; take absolute value
        loss_abs = abs(float(loss))

        # Get account balance for percentage calculation
        info = mt5_call(mt5.account_info)
        if info is None or float(info.balance) <= 0:
            try:
                from core import config
                return float(getattr(config, "RISK_PER_TRADE_PERCENT", 1.0))
            except ImportError:
                return 1.0

        risk_pct = (loss_abs / float(info.balance)) * 100.0
        return round(risk_pct, 4)

    except Exception:
        # Any failure → conservative fallback
        try:
            from core import config
            return float(getattr(config, "RISK_PER_TRADE_PERCENT", 1.0))
        except ImportError:
            return 1.0


def _count_all_bot_positions(magic: int) -> int | None:
    """
    Count ALL open positions for our bot (across all symbols).

    Returns None if MT5 state is unknown (fail-closed scenario).
    """
    try:
        rows = mt5_call(mt5.positions_get)
    except Exception as exc:
        logger.error(
            "[PORTFOLIO_EXPOSURE_GUARD] positions_get_error=%s — fail-closed", exc,
        )
        return None

    if rows is None:
        logger.warning(
            "[PORTFOLIO_EXPOSURE_GUARD] positions_get=None — fail-closed",
        )
        return None

    if not rows:
        return 0

    return sum(1 for p in rows if int(p.magic) == magic)


# ─── MAIN GUARD FUNCTION ──────────────────────────────────────────────────────

def check_portfolio_exposure(
    *,
    proposed_risk_pct: float,
    open_positions: list[Any],
) -> PortfolioExposureResult:
    """
    Evaluate whether a new trade would violate portfolio-level exposure limits.

    Must be called AFTER spread/cooldown/correlation guards, BEFORE execution.

    Args:
        proposed_risk_pct: Risk percentage of the proposed new trade.
        open_positions: Current open positions from TradeStateManager.positions_open()
                        (includes D3-recovered positions).

    Returns:
        PortfolioExposureResult with allowed=True if safe to proceed.
    """
    if not _is_enabled():
        return PortfolioExposureResult(
            allowed=True,
            reason="PORTFOLIO_EXPOSURE_GUARD_DISABLED",
        )

    max_positions = _get_max_positions()
    max_risk_pct = _get_max_risk_pct()
    magic = _get_bot_magic()
    strict = _get_strict_mode()

    # ─── STEP 1: Count current positions (from broker — authoritative) ─────
    broker_count = _count_all_bot_positions(magic)

    if broker_count is None:
        # Fail-closed: cannot determine position state
        if strict:
            logger.warning(
                "[PORTFOLIO_EXPOSURE_GUARD] BLOCKED reason=%s "
                "detail=cannot_determine_position_state",
                REJECT_EXPOSURE_STATE_UNKNOWN,
            )
            return PortfolioExposureResult(
                allowed=False,
                reason=REJECT_EXPOSURE_STATE_UNKNOWN,
                current_positions=0,
                max_positions=max_positions,
            )
        else:
            # Lenient mode: use TradeStateManager count as fallback
            broker_count = len(open_positions)

    # ─── STEP 2: Position count check ─────────────────────────────────────
    if broker_count >= max_positions:
        logger.warning(
            "[PORTFOLIO_POSITION_LIMIT_BLOCK] current=%d limit=%d",
            broker_count, max_positions,
        )
        return PortfolioExposureResult(
            allowed=False,
            reason=REJECT_POSITION_LIMIT,
            current_positions=broker_count,
            max_positions=max_positions,
        )

    # ─── STEP 3: Aggregate risk check ─────────────────────────────────────
    current_risk = 0.0
    for pos in open_positions:
        current_risk += _compute_position_risk_pct(pos)

    current_risk = round(current_risk, 4)
    projected_risk = round(current_risk + proposed_risk_pct, 4)

    if projected_risk > max_risk_pct:
        logger.warning(
            "[PORTFOLIO_RISK_LIMIT_BLOCK] current=%.2f projected=%.2f limit=%.2f",
            current_risk, projected_risk, max_risk_pct,
        )
        return PortfolioExposureResult(
            allowed=False,
            reason=REJECT_RISK_LIMIT,
            current_positions=broker_count,
            max_positions=max_positions,
            current_risk_pct=current_risk,
            projected_risk_pct=projected_risk,
            max_risk_pct=max_risk_pct,
        )

    # ─── ALLOWED ──────────────────────────────────────────────────────────
    return PortfolioExposureResult(
        allowed=True,
        reason="",
        current_positions=broker_count,
        max_positions=max_positions,
        current_risk_pct=current_risk,
        projected_risk_pct=projected_risk,
        max_risk_pct=max_risk_pct,
    )
