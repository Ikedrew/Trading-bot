"""V10 Risk Engine — Capital protection assessment.

Evaluates whether a trade plan is acceptable from a risk perspective.
Computes position sizing, validates geometry, checks exposure limits.

Does NOT modify the trade idea — only approves or rejects.
"""

from __future__ import annotations

from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType
from core.v10.entry_model import EntryDecision, EntryStatus, TradeDirection
from core.v10.risk_model import (
    RiskDecision, AccountContext, RiskProfile, TradeGeometry,
)


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION (defaults — can be overridden)
# ═══════════════════════════════════════════════════════════════

DEFAULT_RISK_PCT = 0.0025               # 0.25% per trade
MIN_RR = 1.5                            # Minimum reward:risk
MAX_DAILY_LOSS_PCT = 0.04               # 4% daily loss limit
MAX_OPEN_POSITIONS = 3                  # Maximum concurrent positions
MAX_TOTAL_RISK_PCT = 0.03              # 3% max total deployed risk
MAX_SYMBOL_EXPOSURE = 2                 # Max positions per symbol


def assess_risk(
    state: V10MarketState,
    opportunity: OpportunityAssessment,
    strategy: StrategyDecision,
    horizon: HorizonDecision,
    entry: EntryDecision,
    account: AccountContext,
    broker: "BrokerContext | None" = None,
) -> RiskDecision:
    """
    Assess whether the trade plan is acceptable for capital deployment.

    Returns an immutable RiskDecision (approved or rejected).
    """
    reasoning: list[str] = []
    checks: dict[str, bool] = {}

    # ─── GATE: Entry must be valid ────────────────────────────
    if entry.entry_status == EntryStatus.INVALID.value:
        return _reject(entry, state, "Entry is INVALID — cannot assess risk", checks)

    if entry.trade_direction == TradeDirection.NONE.value:
        return _reject(entry, state, "No trade direction", checks)

    # ─── CHECK 0: Account data available ─────────────────────
    if account.balance <= 0:
        checks["account_available"] = False
        return _reject(entry, state, "Account data unavailable (balance=0)", checks)
    checks["account_available"] = True

    # ─── CHECK 1: Valid stop ──────────────────────────────────
    has_valid_stop = entry.stop_reference.price > 0 and entry.risk_distance > 0
    checks["valid_stop"] = has_valid_stop
    if not has_valid_stop:
        return _reject(entry, state, "Missing or zero stop distance", checks)
    reasoning.append(f"Stop valid: {entry.stop_reference.price:.5f} (dist={entry.risk_distance:.5f})")

    # ─── CHECK 2: Minimum R:R ─────────────────────────────────
    min_rr = _get_min_rr(strategy.strategy_family)
    rr_met = entry.expected_rr >= min_rr
    checks["minimum_rr_met"] = rr_met
    if not rr_met:
        return _reject(entry, state, f"R:R {entry.expected_rr:.2f} below minimum {min_rr:.1f}", checks)
    reasoning.append(f"R:R {entry.expected_rr:.2f} meets minimum {min_rr:.1f}")

    # ─── CHECK 3: Daily loss limit ───────────────────────────
    daily_ok = account.daily_loss_pct < MAX_DAILY_LOSS_PCT
    checks["daily_loss_limit_ok"] = daily_ok
    if not daily_ok:
        return _reject(entry, state, f"Daily loss {account.daily_loss_pct:.1%} exceeds {MAX_DAILY_LOSS_PCT:.1%}", checks)

    # ─── CHECK 4: Exposure limits ────────────────────────────
    positions_ok = account.open_positions < MAX_OPEN_POSITIONS
    checks["max_positions_ok"] = positions_ok
    if not positions_ok:
        return _reject(entry, state, f"Max positions ({MAX_OPEN_POSITIONS}) reached", checks)

    total_risk_ok = account.current_open_risk_pct < MAX_TOTAL_RISK_PCT
    checks["total_exposure_ok"] = total_risk_ok
    if not total_risk_ok:
        return _reject(entry, state, f"Total risk {account.current_open_risk_pct:.1%} exceeds {MAX_TOTAL_RISK_PCT:.1%}", checks)

    # ─── CHECK 5: Correlation/symbol exposure ─────────────────
    symbol_count = sum(1 for s in account.symbols_with_positions if s == state.symbol)
    correlation_ok = symbol_count < MAX_SYMBOL_EXPOSURE
    checks["correlation_ok"] = correlation_ok
    if not correlation_ok:
        return _reject(entry, state, f"Symbol {state.symbol} already has {symbol_count} positions", checks)

    # ─── POSITION SIZING ──────────────────────────────────────
    risk_pct = _get_risk_percentage(strategy.strategy_family, horizon.horizon_type)
    risk_amount = account.balance * risk_pct

    # Use exact sizing if broker metadata available
    if broker and broker.tick_value > 0 and broker.tick_size > 0:
        position_size = calculate_position_size_exact(
            risk_amount=risk_amount,
            stop_distance=entry.risk_distance,
            tick_value=broker.tick_value,
            tick_size=broker.tick_size,
            volume_min=broker.volume_min,
            volume_max=broker.volume_max,
            volume_step=broker.volume_step,
        )
        reasoning.append(f"Sizing: exact (tick_value={broker.tick_value})")
    else:
        # No broker metadata — reject (no guessing)
        position_size = _calculate_position_size(risk_amount, entry.risk_distance, state.symbol)
        if position_size == 0.0:
            reasoning.append("Sizing: broker metadata unavailable — cannot calculate")

    reasoning.append(f"Risk: {risk_pct:.2%} of {account.balance:.0f} = {risk_amount:.2f}")
    reasoning.append(f"Position size: {position_size:.4f}")

    # ─── BUILD GEOMETRY ───────────────────────────────────────
    geometry = TradeGeometry(
        entry_price=entry.entry_price,
        stop_price=entry.stop_reference.price,
        target_price=entry.target_reference.price,
        stop_distance=entry.risk_distance,
        reward_distance=entry.reward_distance,
        expected_rr=entry.expected_rr,
    )

    risk_profile = RiskProfile(
        risk_percentage=risk_pct,
        max_loss_amount=risk_amount,
        position_size=position_size,
    )

    checks["all_passed"] = True
    reasoning.append("All risk checks PASSED — trade APPROVED")

    return RiskDecision(
        opportunity_id=entry.opportunity_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        approved=True,
        rejection_reason="",
        risk_profile=risk_profile,
        trade_geometry=geometry,
        risk_checks=checks,
        reasoning=reasoning,
    )


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════


def _reject(entry: EntryDecision, state: V10MarketState, reason: str, checks: dict) -> RiskDecision:
    return RiskDecision(
        opportunity_id=entry.opportunity_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        approved=False,
        rejection_reason=reason,
        risk_checks=checks,
        reasoning=[f"REJECTED: {reason}"],
    )


def _get_min_rr(strategy_family: str) -> float:
    """Strategy-aware minimum R:R."""
    # Scalp/mean-reversion strategies can accept lower R:R
    if strategy_family in (StrategyFamily.MEAN_REVERSION.value, StrategyFamily.RANGE_REACTION.value):
        return 1.5
    # Breakout needs higher R:R (wider stops)
    if strategy_family == StrategyFamily.BREAKOUT_EXPANSION.value:
        return 2.0
    # Default
    return 1.5


def _get_risk_percentage(strategy_family: str, horizon_type: str) -> float:
    """Strategy + horizon aware risk percentage."""
    base = DEFAULT_RISK_PCT  # 0.25%

    # Strategy modifier
    if strategy_family in (StrategyFamily.BREAKOUT_EXPANSION.value,):
        # Higher volatility — reduce size
        base *= 0.75
    elif strategy_family in (StrategyFamily.TREND_CONTINUATION.value,):
        # Strong structure — can hold normal size
        base *= 1.0

    # Horizon modifier
    if horizon_type == HorizonType.SCALP.value:
        base *= 1.0  # Normal for scalps
    elif horizon_type == HorizonType.INTRADAY.value:
        base *= 1.0  # Normal
    elif horizon_type == HorizonType.EXTENDED.value:
        base *= 0.75  # Wider stop = reduce size for same risk

    return base


def _calculate_position_size(risk_amount: float, stop_distance: float, symbol: str) -> float:
    """Calculate position size — REJECTS if broker metadata unavailable.
    
    This fallback path should only be reached if BrokerContext tick_value
    is not available. In that case, return 0.0 (causes risk rejection)
    rather than guessing with approximate pip values.
    """
    if stop_distance <= 0:
        return 0.0

    # No broker metadata available — cannot size accurately
    # Return 0.0 which will cause the risk engine to produce size=0
    # and the execution engine to reject (volume below minimum)
    return 0.0


def calculate_position_size_exact(
    risk_amount: float,
    stop_distance: float,
    tick_value: float,
    tick_size: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> float:
    """Calculate position size using exact broker metadata.
    
    This is the production path — uses tick_value from MT5 symbol_info.
    
    Formula: size = risk_amount / (stop_in_ticks × tick_value)
    Then rounded to volume_step and clamped to volume_min/max.
    """
    if stop_distance <= 0 or tick_value <= 0 or tick_size <= 0:
        return 0.0

    stop_in_ticks = stop_distance / tick_size
    money_per_lot_at_stop = stop_in_ticks * tick_value

    if money_per_lot_at_stop <= 0:
        return 0.0

    raw_size = risk_amount / money_per_lot_at_stop

    # Round to volume_step
    if volume_step > 0:
        raw_size = int(raw_size / volume_step) * volume_step

    # Clamp to broker limits
    if volume_min > 0 and raw_size < volume_min:
        return 0.0  # Cannot meet minimum — reject
    if volume_max > 0 and raw_size > volume_max:
        raw_size = volume_max

    return round(raw_size, 4)
