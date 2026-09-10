"""Live fan-out entry (D boundary) — AFTER canonical EXECUTE, BEFORE broker I/O.

Active single-account path (before):
    V10 PipelineResult approved -> _build_order_intent -> prepare_execution
    -> runtime guard chain -> ExecutionOrchestrator.execute_trade
    -> MT5Execution.place_market (global MT5 session, broker_symbol_for)
    -> mt5.order_send

Fan-out path (after, flag-gated):
    V10 PipelineResult approved -> _build_order_intent (unchanged, once)
    -> prepare_execution (unchanged, once: correlation/decision/canonical ids)
    -> CanonicalDecision (frozen, same lineage)
    -> fan_out_canonical_decision -> per-account targets
    -> prepare_account_routes (eligibility + per-account volume)
    -> route via isolated execution workers (F)

Backward compat: when MULTI_ACCOUNT_FANOUT_ENABLED is False (default) or
only METAQUOTES execution is enabled, callers keep the existing
single-account path unchanged.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .fanout import CanonicalDecision, decision_from_intent


def fanout_enabled() -> bool:
    return os.getenv("MULTI_ACCOUNT_FANOUT_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def build_canonical_decision(
    *,
    intent: Any,
    canonical_opportunity_id: str,
    correlation_id: str,
    decision_id: str,
    observation_id: str = "",
) -> CanonicalDecision:
    return decision_from_intent(
        intent=intent,
        canonical_opportunity_id=canonical_opportunity_id,
        correlation_id=correlation_id,
        decision_id=decision_id,
        observation_id=observation_id,
    )


def execute_fanned_out(
    *,
    decision: CanonicalDecision,
    accounts,
    snapshots: dict[str, dict],
    broker_symbols: dict[str, str | None] | None = None,
    strategy_family: str = "",
    horizon_type: str = "SCALP",
    global_execution_enabled: bool = True,
    snapshot_provider: Callable | None = None,
    execute_one: Callable[[dict], dict] | None = None,
    timeout: float = 25.0,
) -> list[dict]:
    from .account_router import route_executions
    from .fanout_orchestrator import prepare_account_routes
    if snapshot_provider is not None:
        snapshots = snapshot_provider(decision, accounts)
    routes = prepare_account_routes(
        decision=decision, accounts=accounts, snapshots=snapshots,
        broker_symbols=broker_symbols, strategy_family=strategy_family,
        horizon_type=horizon_type,
        global_execution_enabled=global_execution_enabled)
    if execute_one is None:
        return routes
    return route_executions(routed=routes, execute_one=execute_one, timeout=timeout)
