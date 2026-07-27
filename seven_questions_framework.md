-- FORMER ORCHESTRATION.PY --

from core.questions import q1_trend, q2_area
from core import config

def decide(data):

    q1 = q1_trend.evaluate(data)
    q2 = q2_area.evaluate(data)
    q3 = q3_liquidity.evaluate(data)
    q4 = q4_confirmation.evaluate(data)
    q5 = q5_momentum.evaluate(data)
    q6 = q6_timing.evaluate(data)
    q7 = q7_risk.evaluate(data)

    if getattr(config, "Q_MODULE_DEBUG_LOGS", False):
        print(q1)
        print(q2)
        print(q3)
        print(q4)
        print(q5)
        print(q6)
        print(q7)

-- FORMER Q1_TREND.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    trend_score = 0

    # Higher highs forming
    if market_data.last_high > market_data.previous_high:
        trend_score += 1

    # Higher lows forming
    if market_data.last_low > market_data.previous_low:
        trend_score += 1

    # Channel support respected multiple times
    if market_data.channel_respect_count >= 3:
        trend_score += 1

    # Price positioned in upper half of channel
    if market_data.price > market_data.channel_midpoint:
        trend_score += 1

    # Pullbacks remain shallow
    if market_data.retracement_percent < 0.5:
        trend_score += 1

    # Bullish candle pressure stronger than bearish
    if market_data.bullish_candles > market_data.bearish_candles:
        trend_score += 1

    # Classification
    if trend_score >= 5:
        return ModuleOutput(1.0, "strong_bullish")

    elif trend_score >= 3:
        return ModuleOutput(0.5, "neutral")

    return ModuleOutput(0.0, "bearish")

-- Q2_AREA.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    area_score = 0

    # Price near key support
    if market_data.distance_to_support <= 5:
        area_score += 1

    # Price near key resistance
    if market_data.distance_to_resistance <= 5:
        area_score += 1

    # Area has been respected multiple times
    if market_data.area_touch_count >= 2:
        area_score += 1

    # Rejection wick present at area
    if market_data.rejection_wick_present:
        area_score += 1

    # Strong reaction from the area
    if market_data.reaction_strength >= market_data.minimum_reaction_strength:
        area_score += 1

    # Classification
    if area_score >= 4:
        return ModuleOutput(1.0, "strong_area")

    elif area_score >= 2:
        return ModuleOutput(0.5, "moderate_area")

    return ModuleOutput(0.0, "weak_area")

-- Q3_LIQUIDITY.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    liquidity_score = 0

    # Did price sweep previous high/low?
    if market_data.swept_previous_high:
        liquidity_score += 1

    if market_data.swept_previous_low:
        liquidity_score += 1

    # Did price reject quickly after sweep?
    if market_data.rejection_after_sweep:
        liquidity_score += 1

    # Was sweep size meaningful?
    if market_data.sweep_distance_pips >= 5:
        liquidity_score += 1

    # Did candle close back inside range?
    if market_data.closed_back_inside_range:
        liquidity_score += 1

    # Classification
    if liquidity_score >= 4:
        return ModuleOutput(1.0, "strong_liquidity_event")

    elif liquidity_score >= 2:
        return ModuleOutput(0.5, "moderate_liquidity_event")

    return ModuleOutput(0.0, "weak_liquidity_event")

-- Q4_CONFIRMATION.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    confirmation_score = 0

    # Strong candle close
    if market_data.confirmation_candle_body_percent >= 60:
        confirmation_score += 1

    # Candle closed beyond trigger level
    if market_data.closed_above_trigger_level:
        confirmation_score += 1

    # Volume or momentum increase
    if market_data.volume_increase:
        confirmation_score += 1

    # Rejection wick in expected direction
    if market_data.rejection_wick_confirmed:
        confirmation_score += 1

    # Consecutive confirmation candles
    if market_data.bullish_closes_in_row >= 2:
        confirmation_score += 1

    # Classification
    if confirmation_score >= 4:
        return ModuleOutput(1.0, "strong_confirmation")

    elif confirmation_score >= 2:
        return ModuleOutput(0.5, "moderate_confirmation")

    return ModuleOutput(0.0, "weak_confirmation")

-- Q5_MOMENTUM.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    momentum_score = 0

    # Strong bullish/bearish candle bodies
    if market_data.average_body_size >= market_data.average_body_threshold:
        momentum_score += 1

    # Consecutive directional candles
    if market_data.directional_closes >= 3:
        momentum_score += 1

    # Increasing candle body size
    if market_data.body_size_increasing:
        momentum_score += 1

    # Low opposing wick pressure
    if market_data.opposing_wick_percent <= 30:
        momentum_score += 1

    # Expansion from consolidation
    if market_data.breakout_expansion_detected:
        momentum_score += 1

    # Classification
    if momentum_score >= 4:
        return ModuleOutput(1.0, "strong_momentum")

    elif momentum_score >= 2:
        return ModuleOutput(0.5, "moderate_momentum")

    return ModuleOutput(0.0, "weak_momentum")

-- Q6_TIMING.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    timing_score = 0

    # Trading during active session
    if market_data.is_london_session:
        timing_score += 1

    if market_data.is_new_york_session:
        timing_score += 1

    # High volatility window
    if market_data.current_volatility >= market_data.minimum_volatility:
        timing_score += 1

    # Avoid low liquidity periods
    if not market_data.is_asian_range_dead_zone:
        timing_score += 1

    # Near session open/kill zone
    if market_data.is_kill_zone:
        timing_score += 1

    # Classification
    if timing_score >= 4:
        return ModuleOutput(1.0, "optimal_timing")

    elif timing_score >= 2:
        return ModuleOutput(0.5, "acceptable_timing")

    return ModuleOutput(0.0, "poor_timing")

-- Q7_RISK.PY --

from core.questions.types import ModuleOutput

def evaluate(market_data):

    risk_score = 0

    # Acceptable spread
    if market_data.current_spread <= market_data.max_allowed_spread:
        risk_score += 1

    # Good risk-to-reward ratio
    if market_data.risk_reward_ratio >= 2.0:
        risk_score += 1

    # Stop loss size acceptable
    if market_data.stop_loss_pips <= market_data.max_stop_loss:
        risk_score += 1

    # Volatility not excessive
    if market_data.current_volatility <= market_data.max_safe_volatility:
        risk_score += 1

    # Trade not too close to major news
    if not market_data.high_impact_news_nearby:
        risk_score += 1

    # Classification
    if risk_score >= 4:
        return ModuleOutput(1.0, "favorable_risk")

    elif risk_score >= 2:
        return ModuleOutput(0.5, "acceptable_risk")

    return ModuleOutput(0.0, "poor_risk")

-- TYPES.PY --

from dataclasses import dataclass

@dataclass
class ModuleOutput:
    score: float
    state: str