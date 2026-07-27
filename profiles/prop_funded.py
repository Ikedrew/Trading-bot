"""
Prop firm funded profile — conservative, capital preservation focused.

Suitable for live funded prop accounts where the priority is
staying within drawdown rules and maintaining consistency.
"""

PROFILE_NAME = "prop_funded"

OVERRIDES = {
    "ENABLE_DRAWDOWN_GUARD": True,
    "MAX_DRAWDOWN_PERCENT": 5.0,
    "ENABLE_DAILY_LOSS_LIMIT": True,
    "DAILY_LOSS_LIMIT_PERCENT": 3.0,
    "MAX_TOTAL_OPEN_POSITIONS": 2,
    "MAX_TOTAL_RISK_EXPOSURE_PCT": 2.0,
    "MAX_TRADES_PER_DAY_TOTAL": 10,
    "MAX_TRADES_PER_DAY_PER_SYMBOL": 3,
    "RISK_PER_TRADE_PERCENT": 0.5,
    "TRADING_HOURS_START_UTC": 8,
    "TRADING_HOURS_END_UTC": 19,
    "BLOCK_FRIDAY_AFTER_HOUR": 16,
    "FIXED_LOT": 0.01,
}
