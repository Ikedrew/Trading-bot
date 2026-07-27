"""
Prop firm challenge profile — aggressive target with strict drawdown rules.

Suitable for prop firm evaluation/challenge phases where the goal is
hitting profit target while respecting daily and total drawdown limits.
"""

PROFILE_NAME = "prop_challenge"

OVERRIDES = {
    "ENABLE_DRAWDOWN_GUARD": True,
    "MAX_DRAWDOWN_PERCENT": 10.0,
    "ENABLE_DAILY_LOSS_LIMIT": True,
    "DAILY_LOSS_LIMIT_PERCENT": 4.0,
    "MAX_TOTAL_OPEN_POSITIONS": 3,
    "MAX_TOTAL_RISK_EXPOSURE_PCT": 3.0,
    "MAX_TRADES_PER_DAY_TOTAL": 15,
    "MAX_TRADES_PER_DAY_PER_SYMBOL": 4,
    "RISK_PER_TRADE_PERCENT": 1.0,
    "TRADING_HOURS_START_UTC": 8,
    "TRADING_HOURS_END_UTC": 20,
    "BLOCK_FRIDAY_AFTER_HOUR": 18,
    "CHALLENGE_MODE_ENABLED": True,
    "CHALLENGE_PROFIT_TARGET_PERCENT": 8.0,
    "CHALLENGE_CONSERVATIVE_THRESHOLD_PERCENT": 80.0,
}
