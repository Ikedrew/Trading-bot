"""V10 Execution Engine — Final gate before broker.

Converts a risk-approved trade plan into a broker-ready order.
Performs execution-environment checks (spread, session, margin, connectivity).

CANNOT:
  - Create trades
  - Choose direction
  - Select strategy
  - Change horizon
  - Override risk rejection
  - Modify stop/target from upstream

CAN:
  - Reject if broker conditions are unsafe
  - Map entry method → order type
  - Apply slippage protection
  - Gate on spread/session/connectivity
"""

from __future__ import annotations

from core.v10.market_state import V10MarketState
from core.v10.entry_model import EntryDecision, EntryStatus, EntryMethod
from core.v10.risk_model import RiskDecision
from core.v10.broker_context import BrokerContext
from core.v10.execution_model import (
    ExecutionDecision, OrderDetails, ExecutionProtection, ExecutionType,
)
from core.instrument_utils import get_pip_size


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

MAX_SPREAD_ATR_RATIO = 0.30              # Reject if spread > 30% of M5 ATR
DEFAULT_SLIPPAGE_PIPS = 2.0              # Max acceptable slippage in pips
DEFAULT_TIMEOUT = 5.0                    # Order timeout seconds


def build_execution_decision(
    entry: EntryDecision,
    risk: RiskDecision,
    market_state: V10MarketState,
    broker: BrokerContext,
) -> ExecutionDecision:
    """
    Build the final execution decision.

    Only approves if ALL upstream decisions are valid AND
    broker conditions support safe execution.
    """
    reasoning: list[str] = []
    checks: dict[str, bool] = {}

    # ─── GATE 1: Risk must be approved ────────────────────────
    checks["risk_approved"] = risk.approved
    if not risk.approved:
        return _reject(entry, market_state, f"Risk rejected: {risk.rejection_reason}", checks)

    # ─── GATE 2: Entry must be ready ──────────────────────────
    entry_ready = entry.entry_status == EntryStatus.READY.value
    checks["entry_ready"] = entry_ready
    if not entry_ready:
        return _reject(entry, market_state, f"Entry not ready: {entry.entry_status}", checks)

    # ─── GATE 3: Broker connected ────────────────────────────
    checks["broker_connected"] = broker.connected
    if not broker.connected:
        return _reject(entry, market_state, "Broker disconnected", checks)

    # ─── GATE 4: Symbol available ────────────────────────────
    checks["symbol_available"] = broker.symbol_available
    if not broker.symbol_available:
        return _reject(entry, market_state, f"Symbol {market_state.symbol} not available", checks)

    # ─── GATE 5: Market open ─────────────────────────────────
    checks["market_open"] = broker.market_open
    if not broker.market_open:
        return _reject(entry, market_state, "Market closed", checks)

    # ─── GATE 6: Spread check ────────────────────────────────
    spread_valid = _check_spread(broker.spread, market_state, entry)
    checks["spread_valid"] = spread_valid
    if not spread_valid:
        return _reject(entry, market_state,
                       f"Spread too high ({broker.spread}) relative to stop distance", checks)
    reasoning.append(f"Spread acceptable: {broker.spread}")

    # ─── GATE 7: Margin available ────────────────────────────
    margin_ok = broker.available_margin > 0
    checks["margin_available"] = margin_ok
    if not margin_ok:
        return _reject(entry, market_state, "Insufficient margin", checks)

    # ─── GATE 8: Volume validation ───────────────────────────
    volume = risk.risk_profile.position_size
    if broker.volume_min > 0 and volume < broker.volume_min:
        checks["volume_valid"] = False
        return _reject(entry, market_state,
                       f"Volume {volume:.4f} below minimum {broker.volume_min}", checks)
    if broker.volume_max > 0 and volume > broker.volume_max:
        checks["volume_valid"] = False
        return _reject(entry, market_state,
                       f"Volume {volume:.4f} exceeds maximum {broker.volume_max}", checks)
    checks["volume_valid"] = True

    # ─── GATE 9: Stops level validation ──────────────────────
    if broker.stops_level > 0 and broker.point > 0:
        min_stop_distance = broker.stops_level * broker.point
        if entry.risk_distance < min_stop_distance:
            checks["stops_level_ok"] = False
            return _reject(entry, market_state,
                           f"Stop distance {entry.risk_distance:.5f} below broker minimum {min_stop_distance:.5f}", checks)
    checks["stops_level_ok"] = True

    # ─── GATE 10: Execution allowed (all passed) ─────────────
    checks["execution_allowed"] = True
    reasoning.append("All execution checks passed")

    # ─── MAP ORDER TYPE ───────────────────────────────────────
    order_type = _map_order_type(entry.entry_method)
    reasoning.append(f"Order type: {order_type} (from {entry.entry_method})")

    # ─── BUILD ORDER ──────────────────────────────────────────
    order = OrderDetails(
        symbol=market_state.symbol,
        direction=entry.trade_direction,
        order_type=order_type,
        volume=risk.risk_profile.position_size,
        entry_price=entry.entry_price,
        stop_loss=entry.stop_reference.price,
        take_profit=entry.target_reference.price,
    )

    # ─── PROTECTION ───────────────────────────────────────────
    pip_size = get_pip_size(market_state.symbol)
    max_slippage = DEFAULT_SLIPPAGE_PIPS * pip_size
    protection = ExecutionProtection(
        max_slippage_price=max_slippage,
        timeout_seconds=DEFAULT_TIMEOUT,
        retry_allowed=(order_type == ExecutionType.MARKET.value),
    )

    reasoning.append(f"APPROVED for execution: {order.direction} {order.volume:.4f} @ {order.entry_price:.5f}")

    return ExecutionDecision(
        opportunity_id=entry.opportunity_id,
        symbol=market_state.symbol,
        timestamp_utc=market_state.timestamp_utc,
        approved=True,
        rejection_reason="",
        order_details=order,
        execution_checks=checks,
        protection=protection,
        reasoning=reasoning,
    )


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════


def _reject(entry: EntryDecision, state: V10MarketState, reason: str, checks: dict) -> ExecutionDecision:
    return ExecutionDecision(
        opportunity_id=entry.opportunity_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        approved=False,
        rejection_reason=reason,
        execution_checks=checks,
        reasoning=[f"REJECTED: {reason}"],
    )


def _check_spread(spread: float, state: V10MarketState, entry: EntryDecision) -> bool:
    """Spread must be reasonable relative to stop distance."""
    if spread <= 0:
        return True  # No spread data = assume ok
    if entry.risk_distance <= 0:
        return False
    # Spread should be < 30% of stop distance
    ratio = spread / entry.risk_distance
    return ratio < MAX_SPREAD_ATR_RATIO


def _map_order_type(entry_method: str) -> str:
    """Map V10 entry method to broker order type."""
    if entry_method == EntryMethod.LIMIT_ENTRY.value:
        return ExecutionType.LIMIT.value
    elif entry_method == EntryMethod.BREAK_ENTRY.value:
        return ExecutionType.STOP.value
    else:
        # CONFIRMATION_ENTRY → market order (confirmation already received)
        return ExecutionType.MARKET.value
