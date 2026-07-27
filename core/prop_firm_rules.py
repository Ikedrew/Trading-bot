"""
H3: Prop Firm Rule Violation Detector — Contract compliance firewall.

Evaluates whether a proposed trade violates ANY prop firm contractual rule
before execution. This is a hard safety gate that prevents account
termination-level violations.

Unlike H1 (challenge progress) and H2 (consistency), this answers:
"Is this trade allowed under the prop firm contract rules?"

All rules are configurable per profile via PROP_FIRM_RULE_SET config.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── DATA MODELS ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PropFirmRuleSet:
    """
    Per-profile configurable prop firm rule set.

    All fields represent hard contract limits.
    """
    max_daily_loss_percent: float = 5.0
    max_total_drawdown_percent: float = 10.0
    max_position_hold_minutes: int | None = None  # None = no limit
    blocked_trading_hours: tuple = ()  # tuple of (start_hour, end_hour) pairs
    max_trades_per_day: int = 20
    max_lot_size: float = 1.0
    allow_weekend_holding: bool = False
    allow_news_trading: bool = False


@dataclass(frozen=True)
class TradeContext:
    """
    Context snapshot for rule evaluation.

    Assembled from live system state before each trade attempt.
    """
    symbol: str = ""
    lot_size: float = 0.0
    current_equity: float = 0.0
    daily_loss_percent: float = 0.0
    total_drawdown_percent: float = 0.0
    current_hour: int = 0
    current_weekday: int = 0  # 0=Monday, 6=Sunday
    trades_today: int = 0


# ─── REJECTION REASONS ────────────────────────────────────────────────────────

REJECT_DAILY_LOSS = "MAX_DAILY_LOSS_EXCEEDED"
REJECT_DRAWDOWN = "MAX_DRAWDOWN_EXCEEDED"
REJECT_TRADING_HOURS = "TRADING_HOURS_RESTRICTED"
REJECT_MAX_TRADES = "MAX_DAILY_TRADES_EXCEEDED"
REJECT_LOT_SIZE = "MAX_LOT_SIZE_EXCEEDED"
REJECT_WEEKEND = "WEEKEND_HOLDING_NOT_ALLOWED"


@dataclass(frozen=True)
class ComplianceResult:
    """Result of prop firm rule evaluation."""
    allowed: bool
    reason: str | None = None
    rule_triggered: str | None = None


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "PROP_FIRM_RULES_ENABLED", True))
    except ImportError:
        return True


def _get_rule_set() -> PropFirmRuleSet:
    """Load PropFirmRuleSet from config. Supports dict or PropFirmRuleSet instance."""
    try:
        from core import config
        raw = getattr(config, "PROP_FIRM_RULE_SET", None)

        if raw is None:
            return PropFirmRuleSet()

        if isinstance(raw, PropFirmRuleSet):
            return raw

        if isinstance(raw, dict):
            # Convert blocked_trading_hours list to tuple for frozen dataclass
            hours = raw.get("blocked_trading_hours", ())
            if isinstance(hours, list):
                hours = tuple(tuple(h) if isinstance(h, list) else h for h in hours)
            return PropFirmRuleSet(
                max_daily_loss_percent=float(raw.get("max_daily_loss_percent", 5.0)),
                max_total_drawdown_percent=float(raw.get("max_total_drawdown_percent", 10.0)),
                max_position_hold_minutes=raw.get("max_position_hold_minutes"),
                blocked_trading_hours=hours,
                max_trades_per_day=int(raw.get("max_trades_per_day", 20)),
                max_lot_size=float(raw.get("max_lot_size", 1.0)),
                allow_weekend_holding=bool(raw.get("allow_weekend_holding", False)),
                allow_news_trading=bool(raw.get("allow_news_trading", False)),
            )

        return PropFirmRuleSet()
    except ImportError:
        return PropFirmRuleSet()


# ─── COMPLIANCE ENGINE ────────────────────────────────────────────────────────

def check_prop_firm_rules(
    context: TradeContext,
    rules: PropFirmRuleSet | None = None,
) -> ComplianceResult:
    """
    Evaluate all prop firm contract rules against the trade context.

    Rules are checked in priority order. First violation wins.

    Args:
        context: Current trade context snapshot.
        rules: PropFirmRuleSet to evaluate against (default: from config).

    Returns:
        ComplianceResult with allowed=False if any rule is violated.
    """
    if rules is None:
        rules = _get_rule_set()

    # Rule A: Daily Loss
    if context.daily_loss_percent >= rules.max_daily_loss_percent:
        return ComplianceResult(
            allowed=False,
            reason=REJECT_DAILY_LOSS,
            rule_triggered="daily_loss",
        )

    # Rule B: Total Drawdown
    if context.total_drawdown_percent >= rules.max_total_drawdown_percent:
        return ComplianceResult(
            allowed=False,
            reason=REJECT_DRAWDOWN,
            rule_triggered="total_drawdown",
        )

    # Rule C: Trading Hours
    if rules.blocked_trading_hours:
        for start_h, end_h in rules.blocked_trading_hours:
            if start_h <= context.current_hour < end_h:
                return ComplianceResult(
                    allowed=False,
                    reason=REJECT_TRADING_HOURS,
                    rule_triggered="trading_hours",
                )

    # Rule D: Max Trades Per Day
    if context.trades_today >= rules.max_trades_per_day:
        return ComplianceResult(
            allowed=False,
            reason=REJECT_MAX_TRADES,
            rule_triggered="max_trades_per_day",
        )

    # Rule E: Position Size Limit
    if context.lot_size > rules.max_lot_size:
        return ComplianceResult(
            allowed=False,
            reason=REJECT_LOT_SIZE,
            rule_triggered="lot_size",
        )

    # Rule F: Weekend Holding
    if not rules.allow_weekend_holding:
        # Friday after market close or Saturday/Sunday
        if context.current_weekday >= 5:  # Saturday=5, Sunday=6
            return ComplianceResult(
                allowed=False,
                reason=REJECT_WEEKEND,
                rule_triggered="weekend_holding",
            )

    # All rules pass
    return ComplianceResult(allowed=True)


# ─── EXECUTION GATE (CONVENIENCE) ─────────────────────────────────────────────

def check_prop_firm_gate(
    *,
    symbol: str = "",
    lot_size: float = 0.0,
    daily_loss_percent: float = 0.0,
    total_drawdown_percent: float = 0.0,
    trades_today: int = 0,
) -> ComplianceResult:
    """
    Convenience gate for pipeline integration.

    Builds TradeContext from provided values + current time,
    loads rule set from config, and evaluates.
    """
    if not _is_enabled():
        return ComplianceResult(allowed=True, reason="PROP_FIRM_RULES_DISABLED")

    now = datetime.now(tz=timezone.utc)

    context = TradeContext(
        symbol=symbol,
        lot_size=lot_size,
        daily_loss_percent=daily_loss_percent,
        total_drawdown_percent=total_drawdown_percent,
        current_hour=now.hour,
        current_weekday=now.weekday(),
        trades_today=trades_today,
    )

    rules = _get_rule_set()
    result = check_prop_firm_rules(context, rules)

    if not result.allowed:
        logger.warning(
            "[PROP_RULE_BLOCKED] Rule: %s symbol=%s reason=%s",
            result.rule_triggered, symbol, result.reason,
        )
    else:
        logger.debug("[PROP_RULE_CHECK] PASSED symbol=%s", symbol)

    return result


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_prop_firm_config() -> list[str]:
    """Validate prop firm rule config at startup."""
    errors: list[str] = []
    if not _is_enabled():
        return errors

    rules = _get_rule_set()

    if rules.max_daily_loss_percent <= 0:
        errors.append(f"max_daily_loss_percent must be > 0 (got {rules.max_daily_loss_percent})")
    if rules.max_total_drawdown_percent <= 0:
        errors.append(f"max_total_drawdown_percent must be > 0 (got {rules.max_total_drawdown_percent})")
    if rules.max_trades_per_day <= 0:
        errors.append(f"max_trades_per_day must be > 0 (got {rules.max_trades_per_day})")
    if rules.max_lot_size <= 0:
        errors.append(f"max_lot_size must be > 0 (got {rules.max_lot_size})")

    return errors
