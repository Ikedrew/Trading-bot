"""Account-scoped execution routing (F) — isolated worker per account.

Destination is explicit:
    METAQUOTES target -> METAQUOTES worker/terminal
    ADMIRALS   target -> ADMIRALS worker/terminal
    VANTAGE    target -> VANTAGE worker/terminal

No login switching. No shared mutable MT5 session. Each eligible target is
executed inside that account's isolated worker subprocess (core.accounts
manager/worker boundary). Execution-disabled accounts are evaluated but
never submit orders. One account's failure/timeout never stops the others.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any, Callable


def execution_enabled_for(account_config: Any, *, global_execution_enabled: bool = True) -> bool:
    """Safety switches preserved: enabled + role + global kill-switch.

    Initial safe behaviour:
        METAQUOTES baseline -> preserve existing production behaviour
        ADMIRALS/VANTAGE observe_only -> execution_enabled=False
    """
    if not global_execution_enabled:
        return False
    if not bool(getattr(account_config, "enabled", False)):
        return False
    return str(getattr(account_config, "role", "observe_only")) != "observe_only"


def route_executions(
    *,
    routed: list[dict],
    execute_one: Callable[[dict], dict],
    timeout: float = 25.0,
) -> list[dict]:
    """Execute eligible account targets concurrently with per-account isolation.

    Each item in `routed` must carry: target / account_config / snapshot /
    eligibility / volume / broker_symbol / execution_enabled. Items with
    eligible=False or execution_enabled=False are returned unexecuted
    (order_send never called). Failures/timeouts are captured locally.
    """
    results: list[dict | None] = [None] * len(routed)
    pending: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, len(routed))) as pool:
        for index, item in enumerate(routed):
            if not item.get("eligibility", {}).get("eligible"):
                results[index] = {**item, "executed": False, "status": "BLOCKED",
                                  "comment": ",".join(item["eligibility"].get("reasons", []))}
                continue
            if not item.get("execution_enabled"):
                results[index] = {**item, "executed": False, "status": "OBSERVED",
                                  "comment": "EXECUTION_DISABLED"}
                continue
            pending[pool.submit(_guarded_execute, execute_one, item)] = index
        for future in as_completed(pending):
            index = pending[future]
            try:
                results[index] = future.result(timeout=timeout)
            except Exception as exc:
                results[index] = {**routed[index], "executed": False,
                                  "status": "WORKER_TIMEOUT" if "timeout" in type(exc).__name__.lower() else "WORKER_FAILED",
                                  "comment": type(exc).__name__}
    return [r if r is not None else {**routed[i], "executed": False, "status": "SKIPPED", "comment": "NO_RESULT"}
            for i, r in enumerate(results)]


def _guarded_execute(execute_one: Callable[[dict], dict], item: dict) -> dict:
    started = time.perf_counter()
    try:
        outcome = execute_one(item)
        outcome.setdefault("account_id", item["target"].account_id)
        outcome.setdefault("account_execution_id", item["target"].account_execution_id)
        outcome.setdefault("trade_id", item["target"].trade_id)
        outcome.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
        return {**item, **outcome}
    except Exception as exc:
        return {**item, "executed": False, "status": "WORKER_FAILED",
                "comment": f"{type(exc).__name__}:{str(exc)[:120]}",
                "latency_ms": int((time.perf_counter() - started) * 1000)}


def build_account_order_request(*, item: dict) -> dict:
    """Broker request for ONE account: resolved broker symbol + own volume/prices."""
    target = item["target"]
    volume = float(item["volume"].volume) if hasattr(item.get("volume"), "volume") else float(item.get("volume") or 0.0)
    return {
        "account_id": target.account_id,
        "broker": target.broker,
        "broker_server": target.broker_server,
        "login": target.login,
        "canonical_symbol": target.canonical_symbol,
        "broker_symbol": item.get("broker_symbol") or target.broker_symbol,
        "canonical_opportunity_id": target.canonical_opportunity_id,
        "correlation_id": target.correlation_id,
        "decision_id": target.decision_id,
        "account_execution_id": target.account_execution_id,
        "trade_id": target.trade_id,
        "requested_volume": volume,
        "entry": float(target.entry),
        "sl": float(target.sl),
        "tp": float(target.tp),
        "pattern": target.pattern,
        "observation_id": target.observation_id,
    }


def target_state(item: dict) -> dict:
    """Runtime state every execution child must carry."""
    target = item["target"]
    volume = float(item["volume"].volume) if hasattr(item.get("volume"), "volume") else float(item.get("volume") or 0.0)
    return {
        "account_id": target.account_id,
        "broker": target.broker,
        "broker_server": target.broker_server,
        "canonical_symbol": target.canonical_symbol,
        "broker_symbol": item.get("broker_symbol") or target.broker_symbol,
        "canonical_opportunity_id": target.canonical_opportunity_id,
        "correlation_id": target.correlation_id,
        "decision_id": target.decision_id,
        "account_execution_id": target.account_execution_id,
        "trade_id": target.trade_id,
        "requested_volume": volume,
        "entry": float(target.entry),
        "sl": float(target.sl),
        "tp": float(target.tp),
    }
