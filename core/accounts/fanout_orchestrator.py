"""Multi-account fan-out orchestrator (D+E+F glue, no strategy logic)."""

from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Any, Callable

from .account_eligibility import evaluate_account_target, find_symbol_row
from .account_router import build_account_order_request, execution_enabled_for
from .account_sizing import volume_for_account
from .fanout import fan_out_canonical_decision


def _row_for(snapshot: dict, canonical: str) -> dict | None:
    return find_symbol_row(snapshot, canonical)


def prepare_account_routes(
    *,
    decision,
    accounts,
    snapshots: dict[str, dict],
    broker_symbols: dict[str, str | None] | None = None,
    strategy_family: str = "",
    horizon_type: str = "SCALP",
    global_execution_enabled: bool = True,
) -> list[dict]:
    targets = fan_out_canonical_decision(
        decision, accounts, broker_symbols=broker_symbols)
    by_id = {a.account_id: a for a in accounts}
    routes: list[dict] = []
    for target in targets:
        account = by_id[target.account_id]
        capability = account.capability(target.canonical_symbol)
        if capability == 'UNSUPPORTED':
            logging.getLogger(__name__).info(
                "[ACCOUNT_FANOUT_SKIP] account=%s canonical=%s reason=SYMBOL_UNSUPPORTED",
                target.account_id, target.canonical_symbol)
            routes.append({
                "target": target,
                "account_config": account,
                "snapshot": snapshots.get(target.account_id, {}),
                "eligibility": {"eligible": False, "reasons": ["SYMBOL_UNAVAILABLE"],
                                "canonical_symbol": target.canonical_symbol,
                                "account_id": target.account_id,
                                "broker": target.broker,
                                "broker_server": target.broker_server},
                "volume": None,
                "broker_symbol": None,
                "execution_enabled": False,
                "symbol_row": None,
                "capability": capability,
            })
            continue
        if capability == 'INVALID':
            logging.getLogger(__name__).error(
                "[ACCOUNT_FANOUT_REJECTED] account=%s canonical=%s reason=CAPABILITY_INVALID",
                target.account_id, target.canonical_symbol)
            routes.append({
                "target": target,
                "account_config": account,
                "snapshot": snapshots.get(target.account_id, {}),
                "eligibility": {"eligible": False, "reasons": ["CAPABILITY_INVALID"],
                                "canonical_symbol": target.canonical_symbol,
                                "account_id": target.account_id,
                                "broker": target.broker,
                                "broker_server": target.broker_server},
                "volume": None,
                "broker_symbol": None,
                "execution_enabled": False,
                "symbol_row": None,
                "capability": capability,
            })
            continue
        snapshot = snapshots.get(target.account_id, {})
        if broker_symbols is not None and target.account_id in broker_symbols:
            override = broker_symbols[target.account_id]
            if override is None:
                snapshot = {**snapshot, "symbols": [
                    {**r, "status": "unavailable", "broker_symbol": None}
                    if r.get("canonical_symbol") == target.canonical_symbol else r
                    for r in (snapshot.get("symbols") or [])]}
        enabled = execution_enabled_for(
            account, global_execution_enabled=global_execution_enabled)
        eligibility = evaluate_account_target(
            target=target, account_config=account,
            snapshot=snapshot, execution_enabled=enabled)
        broker_symbol = eligibility.get("broker_symbol")
        row = _row_for(snapshot, target.canonical_symbol)
        if row is not None and broker_symbol and row.get("broker_symbol") != broker_symbol:
            row = {**row, "broker_symbol": broker_symbol}
        volume = volume_for_account(
            target=target, snapshot=snapshot, symbol_row=row,
            strategy_family=strategy_family, horizon_type=horizon_type)
        if volume.blocked_reason and "SYMBOL_UNAVAILABLE" not in eligibility["reasons"]:
            eligibility = {**eligibility, "eligible": False,
                           "reasons": [*eligibility["reasons"],
                                       volume.blocked_reason]}
        routes.append({
            "target": target,
            "account_config": account,
            "snapshot": snapshot,
            "eligibility": eligibility,
            "volume": volume,
            "broker_symbol": broker_symbol,
            "execution_enabled": enabled,
            "symbol_row": row,
            "capability": capability,
        })
    return routes
