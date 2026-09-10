"""Isolated per-account execution worker (F). JSON in/out. order_send only here."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from .config import AccountConfig
from .terminal import running_terminals, terminal_lease
from .worker import AccountReader, AccountReadError, unavailable


def _lineage(target: dict) -> dict:
    keys = ("broker", "broker_server", "canonical_symbol", "broker_symbol",
            "canonical_opportunity_id", "correlation_id", "decision_id",
            "account_execution_id", "trade_id")
    return {k: target.get(k) for k in keys}



def execute_pinned(request: dict, mt5) -> dict:
    from core.mt5_symbol_spec import MT5SymbolSpec, validate_stops, validate_volume
    account = AccountConfig(**request["account"])
    target = request["target"]
    order = request["order"]
    if target["account_id"] != account.account_id:
        return {"account_id": account.account_id, "executed": False,
                "status": "IDENTITY_MISMATCH", "comment": "TARGET_MISMATCH"}
    reader = AccountReader(account, mt5)
    reader.verify()
    broker_symbol = str(order.get("broker_symbol") or "")
    if not broker_symbol:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": "SYMBOL_UNAVAILABLE",
                **_lineage(target)}
    info = reader.read("symbol_info", broker_symbol)
    if info is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": "SYMBOL_UNAVAILABLE",
                **_lineage(target)}
    spec = MT5SymbolSpec.from_info(broker_symbol, info)
    volume = float(order.get("requested_volume") or 0.0)
    volume_error = validate_volume(spec, volume)
    if volume_error:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": volume_error, **_lineage(target)}
    side = str(order.get("side") or "")
    tick = reader.read("symbol_info_tick", broker_symbol)
    if tick is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": "NO_TICK", **_lineage(target)}
    market = float(tick.ask if side == "BUY" else tick.bid)
    stops_error = validate_stops(spec, market_price=market,
                                 sl=float(order.get("sl") or 0.0),
                                 tp=float(order.get("tp") or 0.0),
                                 include_freeze=True)
    if stops_error:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": stops_error, **_lineage(target)}
    price = spec.normalize_price(float(tick.ask if side == "BUY" else tick.bid))
    broker_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": spec.normalize_price(float(order.get("sl") or 0.0)),
        "tp": spec.normalize_price(float(order.get("tp") or 0.0)),
        "deviation": int(order.get("deviation", 20)),
        "magic": int(order.get("magic", 713001)),
        "comment": str(order.get("comment", "multi-account"))[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    reader.verify()
    result = mt5.order_send(broker_request)
    reader.verify()
    if result is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": "order_send_none",
                **_lineage(target)}
    ok = int(getattr(result, "retcode", -1)) == int(mt5.TRADE_RETCODE_DONE)
    return {"account_id": account.account_id, "executed": True,
            "status": "FILLED" if ok else "REJECTED",
            "ok": ok, "retcode": int(getattr(result, "retcode", -1)),
            "deal": int(getattr(result, "deal", 0) or 0),
            "order": int(getattr(result, "order", 0) or 0),
            "comment": str(getattr(result, "comment", "")),
            "fill_price": float(getattr(result, "price", 0.0) or 0.0),
            **_lineage(target)}


def run_execution_worker(account, payload, *, mt5=None) -> dict:
    from .config import terminal_key
    if not account.enabled or account.errors():
        return unavailable(account, account.errors() or ["DISABLED"])
    try:
        paths = running_terminals()
        if terminal_key(account.terminal_path) not in {terminal_key(p) for p in paths}:
            return {"account_id": account.account_id, "executed": False,
                    "status": "FAILED", "comment": "TERMINAL_NOT_RUNNING"}
        with terminal_lease(account.terminal_path):
            if mt5 is None:
                import MetaTrader5 as mt5  # type: ignore
            try:
                options = {"timeout": 5000}
                if account.portable:
                    options["portable"] = True
                if not mt5.initialize(account.terminal_path, **options):
                    return {"account_id": account.account_id, "executed": False,
                            "status": "FAILED", "comment": "INITIALIZE_FAILED"}
                return execute_pinned({"account": asdict(account),
                                       "target": payload["target"],
                                       "order": payload["order"]}, mt5)
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
    except AccountReadError as exc:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": str(exc)}
    except Exception:
        return {"account_id": account.account_id, "executed": False,
                "status": "WORKER_FAILED", "comment": "WORKER_READ_FAILED"}


def main() -> None:
    payload = json.load(sys.stdin)
    account = AccountConfig(**payload["account"])
    print(json.dumps(run_execution_worker(account, payload), allow_nan=False))


if __name__ == "__main__":
    main()

