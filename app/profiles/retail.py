"""
Retail account profile — wider risk tolerances, relaxed limits.

Suitable for personal retail trading accounts with no external
drawdown rules or consistency requirements.
"""

PROFILE_NAME = "retail"

OVERRIDES = {
    "ENABLE_DRAWDOWN_GUARD": True,
    "MAX_DRAWDOWN_PERCENT": 15.0,
    "ENABLE_DAILY_LOSS_LIMIT": True,
    "DAILY_LOSS_LIMIT_PERCENT": 8.0,
    "MAX_TOTAL_OPEN_POSITIONS": 5,
    "MAX_TOTAL_RISK_EXPOSURE_PCT": 5.0,
    "MAX_TRADES_PER_DAY_TOTAL": 30,
    "MAX_TRADES_PER_DAY_PER_SYMBOL": 8,
    "RISK_PER_TRADE_PERCENT": 1.5,
    "TRADING_HOURS_START_UTC": 7,
    "TRADING_HOURS_END_UTC": 21,
}
