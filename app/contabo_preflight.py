"""Read-only Vantage DEMO and original runtime configuration preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path

import MetaTrader5 as mt5

from core import config
from core.config_profile_loader import load_and_apply_profile
from core.symbol_resolver import active_account_symbol_map, resolve_all


def inspect() -> dict:
    load_and_apply_profile()
    expected = {
        "DRY_RUN": False,
        "EXECUTION_ENABLED": True,
        "TRADE_MANAGEMENT_ENABLED": True,
        "POSITION_CLOSE_ENABLED": True,
        "MULTI_SYMBOL_SCANNER_ENABLED": True,
        "MAX_OPEN_POSITIONS": 1,
        "MAX_TOTAL_OPEN_POSITIONS": 3,
        "MAX_TOTAL_RISK_EXPOSURE_PCT": 3.0,
        "ENABLE_DAILY_LOSS_LIMIT": True,
        "DAILY_LOSS_LIMIT_PERCENT": 4.0,
        "PORTFOLIO_EXPOSURE_GUARD_ENABLED": True,
        "STRICT_EXPOSURE_GUARDS": True,
    }
    for name, value in expected.items():
        if getattr(config, name) != value:
            raise RuntimeError("ORIGINAL_RUNTIME_CONFIG_MISMATCH:" + name)
    if (os.getenv("TRADING_PROFILE") or os.getenv("MULTI_ACCOUNT_FANOUT_ENABLED", "false").lower()
            not in ("false", "0")):
        raise RuntimeError("ORIGINAL_RUNTIME_ROUTE_MISMATCH")
    if (os.getenv("AWS_S3_BUCKET") != "trading-bot-v10-data" or
        os.getenv("AWS_S3_ENDPOINT_URL") != "https://s3.eu-west-3.idrivee2.com" or
        os.getenv("AWS_REGION") != "eu-west-3"):
        raise RuntimeError("IDRIVE_DESTINATION_MISMATCH")
    expected_login = os.environ["RESTORED_VANTAGE_LOGIN"]
    expected_server = os.environ["RESTORED_VANTAGE_SERVER"]
    expected_map = json.loads(os.environ["RESTORED_VANTAGE_SYMBOL_MAP"])
    if not isinstance(expected_map, dict) or set(expected_map) != set(config.CANONICAL_SYMBOLS):
        raise RuntimeError("VANTAGE_SYMBOL_MAP_INVALID")
    terminal = Path(config.MT5_TERMINAL_PATH)
    if not terminal.is_file():
        raise RuntimeError("VANTAGE_TERMINAL_MISSING")
    if not mt5.initialize(path=str(terminal), portable=config.MT5_TERMINAL_PORTABLE):
        raise RuntimeError("VANTAGE_MT5_INITIALIZE_FAILED")
    try:
        account = mt5.account_info()
        info = mt5.terminal_info()
        if (account is None or info is None or not info.connected or
            int(account.login) != int(expected_login) or
            str(account.server) != expected_server or
            account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO):
            raise RuntimeError("VANTAGE_DEMO_IDENTITY_MISMATCH")
        configured_map = active_account_symbol_map()
        if configured_map != expected_map:
            raise RuntimeError("VANTAGE_ACCOUNT_SYMBOL_MAP_MISMATCH")
        resolved = resolve_all(list(config.CANONICAL_SYMBOLS), fail_mode="raise")
        if resolved != expected_map:
            raise RuntimeError("VANTAGE_ORIGINAL_SCANNER_SYMBOL_MAP_MISMATCH")
        return {
            "result": "ORIGINAL_AWS_CONTABO_PREFLIGHT_PASS",
            "account": "VANTAGE_DEMO",
            "login_suffix": str(account.login)[-4:],
            "symbols": len(resolved),
            "max_positions_per_symbol": config.MAX_OPEN_POSITIONS,
            "max_total_positions": config.MAX_TOTAL_OPEN_POSITIONS,
            "daily_loss_percent": config.DAILY_LOSS_LIMIT_PERCENT,
            "portfolio_risk_percent": config.MAX_TOTAL_RISK_EXPOSURE_PCT,
            "horizons": config.PERMITTED_HORIZONS,
        }
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"result": "ORIGINAL_AWS_CONTABO_PREFLIGHT_FAIL",
                          "reason": str(exc).split(":")[0]}, sort_keys=True))
        raise SystemExit(1)
