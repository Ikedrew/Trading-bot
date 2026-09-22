"""Isolated per-account execution worker (F). JSON in/out. order_send only here."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from .config import AccountConfig
from .terminal import (
    TERMINAL_INVENTORY_OBSERVED, TERMINAL_INVENTORY_UNAVAILABLE,
    TerminalInventoryError, running_terminals, terminal_lease,
    terminal_process_inventory,
)
from .worker import AccountReader, AccountReadError, unavailable


def _lineage(target: dict) -> dict:
    keys = ("broker", "broker_server", "canonical_symbol", "broker_symbol",
            "canonical_opportunity_id", "correlation_id", "decision_id",
            "account_execution_id", "trade_id")
    return {k: target.get(k) for k in keys}


# Tiny numerical tolerance for float/broker-calc noise only. NOT a slippage
# allowance — the configured risk budget remains the authority.
_RISK_TOLERANCE = 1e-6


def _validate_execution_risk(*, mt5, account, side: str, broker_symbol: str,
                             market_price: float, sl: float, volume: float,
                             risk_budget) -> tuple[bool, dict]:
    """Final fail-closed monetary-risk check immediately before order_send.

    Returns ``(ok, evidence)``. ``ok`` is True only when the realised SL risk
    is within the permitted budget. On rejection ``evidence['comment']`` is one
    of:

      * EXECUTION_RISK_BUDGET_UNAVAILABLE — no permitted budget was carried.
      * EXECUTION_RISK_CALC_FAILED        — broker risk calc unusable.
      * EXECUTION_RISK_EXCEEDED           — realised SL risk > budget.

    Risk is the |loss| if price moves from the fresh executable price to the
    submitted SL for the requested volume, via the account's own broker spec
    (mt5.order_calc_profit — no generic pip assumptions).
    """
    permitted = float(risk_budget) if isinstance(risk_budget, (int, float)) else None

    def _evidence(**extra) -> dict:
        base = {
            "risk_budget": permitted,
            "execution_price": float(market_price) if market_price else None,
            "sl": float(sl) if sl else None,
            "requested_volume": float(volume) if volume else None,
            "execution_risk_amount": None,
        }
        base.update(extra)
        return base

    # Budget must be known for a live submission — never assume a default.
    if permitted is None or permitted <= 0:
        return False, _evidence(comment="EXECUTION_RISK_BUDGET_UNAVAILABLE")

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    try:
        loss = mt5.order_calc_profit(order_type, broker_symbol, volume,
                                     market_price, sl)
    except Exception:
        loss = None
    if loss is None or not isinstance(loss, (int, float)):
        return False, _evidence(comment="EXECUTION_RISK_CALC_FAILED")

    execution_risk_amount = abs(float(loss))
    evidence = _evidence(
        execution_risk_amount=round(execution_risk_amount, 6),
        excess_amount=round(execution_risk_amount - permitted, 6),
        excess_ratio=(round(execution_risk_amount / permitted, 4) if permitted else None),
    )
    if execution_risk_amount > permitted + _RISK_TOLERANCE:
        evidence["comment"] = "EXECUTION_RISK_EXCEEDED"
        return False, evidence
    return True, evidence  # within budget → allowed



def execute_pinned(request: dict, mt5) -> dict:
    from core.mt5_symbol_spec import (
        MT5SymbolSpec, validate_stops, validate_volume,
        filling_mode_constant, select_filling_mode, validate_trade_mode,
    )
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
    side = str(order.get("side") or "")

    # ─── BROKER TRADE-MODE GATE (fail closed BEFORE order_send) ─────
    # Disabled instruments (e.g. MetaQuotes USTEC/US500 trade_mode=0) and
    # side-restricted symbols must clean-skip here, never reach the broker.
    trade_mode_error = validate_trade_mode(spec, side)
    if trade_mode_error:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": trade_mode_error,
                "broker_trade_mode": spec.trade_mode, **_lineage(target)}

    # ─── BROKER FILLING-MODE NEGOTIATION (never hardcode IOC) ───────
    # MetaQuotes FX symbols commonly report FOK-only support; sending IOC
    # there yields 10030 Unsupported filling mode. Negotiate from the actual
    # broker spec and fail closed when no supported mode exists.
    filling = select_filling_mode(spec.filling_mode)
    if filling is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": "NO_SUPPORTED_FILLING_MODE",
                "broker_filling_mode": spec.filling_mode, **_lineage(target)}

    volume = float(order.get("requested_volume") or 0.0)
    volume_error = validate_volume(spec, volume)
    if volume_error:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": volume_error, **_lineage(target)}
    tick = reader.read("symbol_info_tick", broker_symbol)
    if tick is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": "NO_TICK", **_lineage(target)}
    market = float(tick.ask if side == "BUY" else tick.bid)
    # ─── BROKER STOP-DISTANCE VALIDATION (Vantage 10016 fix) ────────
    # Normalise the canonical SL/TP onto the destination broker's price
    # grid FIRST, then validate distances against THAT spec. Validating
    # raw canonical prices against a different broker's point/digits
    # rejects orders the broker would accept after normalisation.
    canonical_sl = float(order.get("sl") or 0.0)
    canonical_tp = float(order.get("tp") or 0.0)
    sl = spec.normalize_price(canonical_sl)
    tp = spec.normalize_price(canonical_tp)
    stops_error = validate_stops(spec, market_price=market,
                                 sl=sl, tp=tp, include_freeze=True)
    if stops_error:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", "comment": stops_error,
                "canonical_sl": canonical_sl, "canonical_tp": canonical_tp,
                "broker_sl": sl, "broker_tp": tp, **_lineage(target)}

    # ─── EXECUTION-TIME RISK RECHECK (fail closed BEFORE order_send) ─────
    # Price can move between account sizing and execution. Re-evaluate the
    # realised SL risk of the requested volume against the worker's FRESH
    # executable price and the SAME monetary budget that produced the volume.
    # Reject (never silently resize) if it exceeds budget. This is the final
    # blast-radius guard against oversized submissions.
    risk_ok, risk_evidence = _validate_execution_risk(
        mt5=mt5, account=account, side=side, broker_symbol=broker_symbol,
        market_price=market, sl=sl, volume=volume,
        risk_budget=order.get("risk_amount"))
    if not risk_ok:
        return {"account_id": account.account_id, "executed": False,
                "status": "BLOCKED", **risk_evidence,
                "canonical_sl": canonical_sl, "canonical_tp": canonical_tp,
                "broker_sl": sl, "broker_tp": tp, **_lineage(target)}

    price = spec.normalize_price(float(tick.ask if side == "BUY" else tick.bid))
    broker_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": int(order.get("deviation", 20)),
        "magic": int(order.get("magic", 713001)),
        "comment": str(order.get("comment", "multi-account"))[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode_constant(filling, mt5),
    }
    reader.verify()
    result = mt5.order_send(broker_request)
    reader.verify()
    if result is None:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": "order_send_none",
                "filling_mode": filling,
                "broker_sl": sl, "broker_tp": tp,
                "canonical_sl": canonical_sl, "canonical_tp": canonical_tp,
                **_lineage(target)}
    ok = int(getattr(result, "retcode", -1)) == int(mt5.TRADE_RETCODE_DONE)
    ownership = None
    if ok:
        from .position_state import ownership_from_fill, save
        try:
            ownership = ownership_from_fill(account, reader, result,
                symbol=target["canonical_symbol"], broker_symbol=broker_symbol,
                magic=int(broker_request["magic"]),
                **{key: target.get(key, "") for key in ("canonical_opportunity_id",
                    "correlation_id", "decision_id", "account_execution_id", "trade_id")})
            if ownership is not None:
                save(ownership, pattern=target.get("pattern", ""),
                     trade_horizon=target.get("metadata", {}).get("horizon", "SCALP"),
                     sl=broker_request["sl"], tp=broker_request["tp"], status="open")
        except Exception:
            # Entry outcome is immutable. A failed identity read/checkpoint does
            # not turn an executed order into a retryable entry failure.
            pass
    return {"account_id": account.account_id, "executed": True,
            "status": "FILLED" if ok else "REJECTED",
            "ownership": ownership._asdict() if ownership else None,
            "lifecycle_side": side,
            "lifecycle_bid": float(tick.bid), "lifecycle_ask": float(tick.ask),
            # Execution-time risk-recheck evidence (order passed the budget guard).
            "risk_budget": risk_evidence.get("risk_budget"),
            "execution_risk_amount": risk_evidence.get("execution_risk_amount"),
            "execution_price": risk_evidence.get("execution_price"),
            "ok": ok, "retcode": int(getattr(result, "retcode", -1)),
            "deal": int(getattr(result, "deal", 0) or 0),
            "order": int(getattr(result, "order", 0) or 0),
            "comment": str(getattr(result, "comment", "")),
            "fill_price": float(getattr(result, "price", 0.0) or 0.0),
            "filling_mode": filling,
            "canonical_sl": canonical_sl, "canonical_tp": canonical_tp,
            "broker_sl": sl, "broker_tp": tp,
            **_lineage(target)}


def run_execution_worker(account, payload, *, mt5=None) -> dict:
    from .config import terminal_key
    if not account.enabled or account.errors():
        return unavailable(account, account.errors() or ["DISABLED"])
    try:
        # Liveness PRE-FLIGHT only. An INCONCLUSIVE inventory (host-load
        # timeout) must never block a verified order — the authoritative
        # deciders stay mt5.initialize + the identity verification performed
        # inside execute_pinned (AccountReader.verify before/after order_send).
        paths, inventory_state = terminal_process_inventory(running_terminals)
        if paths is not None and terminal_key(account.terminal_path) not in {terminal_key(p) for p in paths}:
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
                outcome = execute_pinned({"account": asdict(account),
                                          "target": payload["target"],
                                          "order": payload["order"]}, mt5)
                if inventory_state != TERMINAL_INVENTORY_OBSERVED:
                    # Degraded pre-flight evidence only; the order path itself
                    # remains identity-verified and fail-closed.
                    outcome.setdefault("terminal_inventory", inventory_state)
                return outcome
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
    except AccountReadError as exc:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED", "comment": str(exc)}
    except TerminalInventoryError as exc:
        return {"account_id": account.account_id, "executed": False,
                "status": "FAILED",
                "comment": str(exc) or TERMINAL_INVENTORY_UNAVAILABLE}
    except Exception:
        return {"account_id": account.account_id, "executed": False,
                "status": "WORKER_FAILED", "comment": "WORKER_READ_FAILED"}


def main() -> None:
    payload = json.load(sys.stdin)
    account = AccountConfig(**payload["account"])
    print(json.dumps(run_execution_worker(account, payload), allow_nan=False))


if __name__ == "__main__":
    main()

