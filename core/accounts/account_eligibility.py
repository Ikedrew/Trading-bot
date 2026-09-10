"""Account-specific eligibility (E) — one account never blocks another.

Evaluates the current equivalent of the worker snapshot against the
canonical target, reusing existing project vocabulary:

    enabled / execution_enabled / connected / identity_verified /
    trade_allowed / trade_expert / SYMBOL_UNAVAILABLE / INVALID_SYMBOL_SPEC /
    TRADING_NOT_ALLOWED / INSUFFICIENT_FREE_MARGIN / EXPOSURE_UNAVAILABLE ...

An account-specific block does NOT alter the canonical strategy decision.
"""

from __future__ import annotations

from typing import Any


def find_symbol_row(snapshot: dict, canonical_symbol: str) -> dict | None:
    for row in snapshot.get("symbols", []) or []:
        if row.get("canonical_symbol") == canonical_symbol:
            return row
    return None


def evaluate_account_target(
    *,
    target: Any,
    account_config: Any,
    snapshot: dict,
    execution_enabled: bool,
) -> dict:
    """Evaluate ONE account target against ITS OWN snapshot facts."""
    reasons: list[str] = []
    row = find_symbol_row(snapshot, target.canonical_symbol)
    status = (row or {}).get("status", "unavailable")
    broker_symbol = target.broker_symbol
    if broker_symbol is None and status == "available":
        broker_symbol = (row or {}).get("broker_symbol")


    if not bool(getattr(account_config, "enabled", False)):
        reasons.append("DISABLED")
    if not snapshot.get("connected"):
        reasons.append("NOT_CONNECTED")
    if not snapshot.get("identity_verified"):
        reasons.append("IDENTITY_NOT_VERIFIED")
    trade_allowed = bool(snapshot.get("trade_allowed")) and bool(snapshot.get("trade_expert"))
    terminal_ok = bool(snapshot.get("terminal_trade_allowed", snapshot.get("trade_allowed")))
    if not (trade_allowed and terminal_ok):
        reasons.append("TRADING_NOT_ALLOWED")
    if status != "available" or not broker_symbol:
        # Local block only — other accounts unaffected. Never guess mappings.
        reasons.append("SYMBOL_UNAVAILABLE")
    elif row is not None and not _spec_looks_valid(row):
        reasons.append("INVALID_SYMBOL_SPEC")
    if snapshot.get("positions") is None or snapshot.get("orders") is None:
        reasons.append("EXPOSURE_UNAVAILABLE")

    eligible = not reasons
    return {
        "account_id": target.account_id,
        "broker": target.broker,
        "broker_server": target.broker_server,
        "canonical_symbol": target.canonical_symbol,
        "broker_symbol": broker_symbol,
        "eligible": eligible,
        "execution_enabled": bool(execution_enabled),
        "reasons": reasons,
        "balance": snapshot.get("balance"),
        "equity": snapshot.get("equity"),
        "margin_free": snapshot.get("margin_free"),
        "leverage": snapshot.get("leverage"),
        "currency": snapshot.get("currency"),
        "account_execution_id": target.account_execution_id,
        "trade_id": target.trade_id,
        "canonical_opportunity_id": target.canonical_opportunity_id,
        "correlation_id": target.correlation_id,
        "decision_id": target.decision_id,
    }


def _spec_looks_valid(row: dict) -> bool:
    try:
        digits = int(row.get("digits"))
        point = float(row.get("point"))
        contract = float(row.get("trade_contract_size"))
        vmin = float(row.get("volume_min"))
        vstep = float(row.get("volume_step"))
        vmax = float(row.get("volume_max"))
        tick = float(row.get("trade_tick_size"))
        return (
            0 <= digits <= 15
            and point > 0 and contract > 0
            and vmin > 0 and vstep > 0 and vmax >= vmin and tick > 0
        )
    except (TypeError, ValueError):
        return False
