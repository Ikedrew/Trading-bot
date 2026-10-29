"""
Configuration Reader — Answers: "What configuration is active?"

Reads from: core/config.py (importable, all values accessible)
"""

from __future__ import annotations

from typing import Any


def get_config_snapshot() -> dict[str, Any]:
    """
    Read all important configuration values.

    Returns structured dict grouped by category:
        - execution (enabled, dry_run, mode)
        - horizons (permitted, authority)
        - limits (positions, risk, trades)
        - guards (enabled/disabled toggles)
        - features (pipeline, research, market context)
    """
    result: dict[str, Any] = {
        "execution": {},
        "horizons": {},
        "limits": {},
        "guards": {},
        "features": {},
    }

    try:
        from core import config

        result["execution"] = {
            "EXECUTION_ENABLED": getattr(config, "EXECUTION_ENABLED", None),
            "DRY_RUN": getattr(config, "DRY_RUN", None),
            "REPLAY_MODE": getattr(config, "REPLAY_MODE", None),
            "TRADE_MANAGEMENT_ENABLED": getattr(config, "TRADE_MANAGEMENT_ENABLED", None),
            "POSITION_CLOSE_ENABLED": getattr(config, "POSITION_CLOSE_ENABLED", None),
        }

        result["horizons"] = {
            "PERMITTED_HORIZONS": getattr(config, "PERMITTED_HORIZONS", []),
            "HORIZON_AUTHORITY_ENABLED": getattr(config, "HORIZON_AUTHORITY_ENABLED", None),
            "HORIZON_MAX_TOTAL_POSITIONS": getattr(config, "HORIZON_MAX_TOTAL_POSITIONS", None),
            "HORIZON_MAX_POSITIONS_PER_SYMBOL": getattr(config, "HORIZON_MAX_POSITIONS_PER_SYMBOL", None),
        }

        result["limits"] = {
            "MAX_OPEN_POSITIONS": getattr(config, "MAX_OPEN_POSITIONS", None),
            "MAX_TOTAL_OPEN_POSITIONS": getattr(config, "MAX_TOTAL_OPEN_POSITIONS", None),
            "FIXED_LOT": getattr(config, "FIXED_LOT", None),
            "MAX_TRADES_PER_DAY_TOTAL": getattr(config, "MAX_TRADES_PER_DAY_TOTAL", None),
            "MAX_TRADES_PER_DAY_PER_SYMBOL": getattr(config, "MAX_TRADES_PER_DAY_PER_SYMBOL", None),
            "MAX_TOTAL_RISK_EXPOSURE_PCT": getattr(config, "MAX_TOTAL_RISK_EXPOSURE_PCT", None),
        }

        result["guards"] = {
            "CORRELATION_GUARD_ENABLED": getattr(config, "CORRELATION_GUARD_ENABLED", None),
            "PORTFOLIO_EXPOSURE_GUARD_ENABLED": getattr(config, "PORTFOLIO_EXPOSURE_GUARD_ENABLED", None),
            "REGIME_GUARD_ENABLED": getattr(config, "REGIME_GUARD_ENABLED", None),
        }

        result["features"] = {
            "USE_NEW_PIPELINE": getattr(config, "USE_NEW_PIPELINE", None),
            "ENABLE_EV_GATE": getattr(config, "ENABLE_EV_GATE", None),
            "MARKET_CONTEXT_ENABLED": getattr(config, "MARKET_CONTEXT_ENABLED", None),
            "PORTFOLIO_RANKING_AUTHORITY": getattr(config, "PORTFOLIO_RANKING_AUTHORITY", None),
            "EVENT_STREAM_S3_MIRROR": getattr(config, "EVENT_STREAM_S3_MIRROR", None),
        }

    except Exception:
        pass

    return result
