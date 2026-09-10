"""Feature-gated multi-account fan-out bridge for the live runtime (D boundary).

Placed in the live scanner between the canonical EXECUTE decision and broker
I/O:

    live runtime
    → canonical EXECUTE (unchanged, one decision)
    → multi-account feature gate
    → build_canonical_decision            (core.accounts.live_fanout)
    → execute_fanned_out                  (core.accounts.live_fanout)
    → prepare_account_routes              (core.accounts.fanout_orchestrator)
    → per-account workers                 (core.accounts.execution_worker)

When MULTI_ACCOUNT_FANOUT_ENABLED=true  the canonical decision is fanned out to
per-account execution workers. When false the legacy single-account
ExecutionOrchestrator path is used unchanged.

This module NEVER re-evaluates strategy, opportunity validation, entries,
SL/TP, horizons, scoring or risk methodology. The canonical decision is built
from the existing OrderIntent and inherits its lineage verbatim
(canonical_opportunity_id / correlation_id / decision_id / observation_id).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

ROOT = str(Path(__file__).resolve().parents[2])

# Same pinned-account worker env isolation as core/accounts/manager.run_isolated:
# no account/AWS/Discord secret crosses the process boundary, and no login
# switching is ever possible (workers attach to the already-logged-in terminal).
_WORKER_ENV_KEYS = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "COMSPEC", "SYSTEMDRIVE",
})

MULTI_ACCOUNT_FANOUT_ENABLED = "MULTI_ACCOUNT_FANOUT_ENABLED"


def fanout_flag() -> bool:
    """Read the feature flag the same way core/accounts/live_fanout does."""
    raw = os.getenv(MULTI_ACCOUNT_FANOUT_ENABLED, "false")
    return str(raw).strip().lower() in ("1", "true", "yes")


def multi_account_fanout_enabled() -> bool:
    """Feature gate — delegates to the completed fan-out module (D boundary)."""
    from core.accounts.live_fanout import fanout_enabled
    return fanout_enabled()


# ─── ISOLATED PER-ACCOUNT WORKER (F) ──────────────────────────────────────────


def _isolated_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.upper() in _WORKER_ENV_KEYS}


def run_pinned_worker(account: Any, target: dict, order: dict) -> dict:
    """Execute ONE account target in an isolated subprocess.

    Only `order_send` in core/accounts/execution_worker.py may reach the
    broker. The parent bot process never switches login or shares its own
    MT5 session with another account's terminal.
    """
    payload = {"account": asdict(account), "target": target, "order": order}
    child_env = _isolated_env()
    child_env["PYTHONIOENCODING"] = "utf-8"
    try:
        child = subprocess.run(
            [sys.executable, "-m", "core.accounts.execution_worker"],
            input=json.dumps(payload), capture_output=True, encoding="utf-8",
            timeout=25.0, cwd=ROOT, env=child_env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if child.returncode:
            return {"account_id": account.account_id, "executed": False,
                    "status": "WORKER_FAILED", "comment": "WORKER_PROCESS_FAILED"}
        response = json.loads(child.stdout or "{}")
        if response.get("account_id") != account.account_id:
            return {"account_id": account.account_id, "executed": False,
                    "status": "WORKER_FAILED", "comment": "WORKER_IDENTITY_MISMATCH"}
        return response
    except subprocess.TimeoutExpired:
        return {"account_id": account.account_id, "executed": False,
                "status": "WORKER_TIMEOUT", "comment": "WORKER_TIMEOUT"}
    except Exception:
        return {"account_id": account.account_id, "executed": False,
                "status": "WORKER_FAILED", "comment": "WORKER_READ_FAILED"}


def _build_worker_payload(route_item: dict, canonical: Any) -> tuple[dict, dict]:
    """Map a prepared account route to the worker's JSON contract.

    Reuses the existing account_router builders (build_account_order_request /
    target_state) so no execution request is invented here.
    """
    from core.accounts.account_router import build_account_order_request, target_state
    from core import config as _cfg

    target = route_item["target"]
    broker_symbol = route_item.get("broker_symbol") or target.broker_symbol
    target_dict = target_state(route_item)
    target_dict["broker_symbol"] = broker_symbol
    target_dict["pattern"] = getattr(target, "pattern", "")
    target_dict["metadata"] = dict(getattr(target, "metadata", {}) or {})
    order = build_account_order_request(item=route_item)
    order.update({
        "broker_symbol": broker_symbol,
        "side": str(getattr(canonical, "side", "BUY")),
        "deviation": int(getattr(_cfg, "MT5_DEVIATION", 20) or 20),
        "magic": int(getattr(_cfg, "BOT_MAGIC", 713001) or 713001),
        "comment": "multi-account",
    })
    return target_dict, order


def _execute_one_for(canonical: Any) -> Callable[[dict], dict]:
    """Return the route_executions `execute_one` callback for live fan-out."""

    def _execute_one(route_item: dict) -> dict:
        account = route_item["account_config"]
        target, order = _build_worker_payload(route_item, canonical)
        return run_pinned_worker(account, target, order)

    return _execute_one
# ─── CANONICAL DECISION → FAN-OUT → OUTCOMES ──────────────────────────────────


def execute_multi_account_fanout(
    *,
    intent: Any,
    symbol: str,
    cycle_id: int,
    decision_id: str,
    correlation_id: str,
    entity_id: str = "",
    observation_id: str = "",
    canonical_opportunity_id: str = "",
    strategy_family: str = "",
    bid: float = 0.0,
    ask: float = 0.0,
) -> Any:
    """Fan out ONE canonical EXECUTE to per-account execution workers.

    Returns an ExecutionOutcome-compatible object for the primary
    (METAQUOTES) account so the existing live scanner post-execution block
    keeps behaving unchanged. Secondary account outcomes are registered by the
    existing D–F machinery (route_executions → accept_account_execution).
    """
    from core.accounts.config import load_accounts
    from core.accounts.manager import diagnose
    from core.accounts.live_fanout import build_canonical_decision, execute_fanned_out
    from core import config as _cfg

    canonical = build_canonical_decision(
        intent=intent,
        canonical_opportunity_id=canonical_opportunity_id,
        correlation_id=correlation_id,
        decision_id=decision_id,
        observation_id=observation_id,
    )

    accounts = tuple(load_accounts())
    reports = diagnose(accounts, timeout=25)
    snapshots = {r["account_id"]: r for r in reports}
    broker_symbols = {}
    for account in accounts:
        row = _find_symbol_row(snapshots.get(account.account_id, {}), canonical.canonical_symbol)
        available = row is not None and row.get("status") == "available" and bool(row.get("broker_symbol"))
        broker_symbols[account.account_id] = (row or {}).get("broker_symbol") if available else None

    horizon_type = str((intent.metadata or {}).get("horizon", "SCALP") or "SCALP")

    outcomes = execute_fanned_out(
        decision=canonical,
        accounts=accounts,
        snapshots=snapshots,
        broker_symbols=broker_symbols,
        strategy_family=strategy_family or "",
        horizon_type=horizon_type,
        global_execution_enabled=bool(getattr(_cfg, "EXECUTION_ENABLED", True)),
        execute_one=_execute_one_for(canonical),
        timeout=25.0,
    )
    _log_fanout_outcomes(symbol, canonical, outcomes)
    _persist_account_results(outcomes, cycle_id=cycle_id, entity_id=entity_id,
                             bid=bid, ask=ask)
    return build_primary_execution_outcome(outcomes)


def _find_symbol_row(snapshot: dict, canonical_symbol: str) -> dict | None:
    for row in snapshot.get("symbols", []) or []:
        if row.get("canonical_symbol") == canonical_symbol:
            return row
    return None


def _log_fanout_outcomes(symbol: str, canonical: Any, outcomes: list[dict]) -> None:
    for out in outcomes:
        target = out.get("target")
        account_id = getattr(target, "account_id", None) or out.get("account_id", "")
        log.info(
            "[MULTI_ACCOUNT_FANOUT] symbol=%s decision_id=%s account=%s "
            "status=%s executed=%s account_execution_id=%s trade_id=%s comment=%s",
            symbol, canonical.decision_id, account_id, out.get("status", "?"),
            bool(out.get("executed")),
            getattr(target, "account_execution_id", "") if target else out.get("account_execution_id", ""),
            getattr(target, "trade_id", "") if target else out.get("trade_id", ""),
            str(out.get("comment", ""))[:80],
        )
def _persist_account_results(
    outcomes: list[dict], *, cycle_id: int, entity_id: str,
    bid: float, ask: float,
) -> None:
    """Observational Phase-H persistence for every fan-out child attempt."""
    try:
        from core.persistence.execution_result_writer import persist_execution_result
    except Exception:
        return
    for out in outcomes:
        target = out.get("target")
        if target is None:
            continue
        try:
            fill = out.get("fill_price") or 0.0
            entry = float(target.entry)
            persist_execution_result(
                symbol=getattr(target, "canonical_symbol", ""),
                cycle_id=cycle_id,
                result_ok=bool(out.get("ok")),
                retcode=int(out.get("retcode", -1)),
                deal=int(out.get("deal", 0) or 0),
                order=int(out.get("order", 0) or 0),
                comment=str(out.get("comment", "") or ""),
                fill_price=float(fill) if fill else None,
                side=str(out.get("lifecycle_side", "")),
                volume=_outcome_volume(out),
                entry_reference=entry,
                sl=float(target.sl),
                tp=float(target.tp),
                pattern=getattr(target, "pattern", ""),
                decision_id=getattr(target, "decision_id", ""),
                correlation_id=getattr(target, "correlation_id", ""),
                entity_id=entity_id,
                observation_id=getattr(target, "observation_id", ""),
                canonical_opportunity_id=getattr(target, "canonical_opportunity_id", ""),
                account_id=getattr(target, "account_id", ""),
                broker=getattr(target, "broker", ""),
                broker_server=getattr(target, "broker_server", ""),
                position_ticket=int(out.get("order", 0) or 0),
                broker_symbol=route_broker_symbol(out),
                decision_ts_utc_ms=0,
                slippage=abs(fill - entry) if fill and entry else 0.0,
                slippage_measured=bool(fill and entry),
                bid_at_execution=bid,
                ask_at_execution=ask,
                risk_distance=abs(entry - float(target.sl)),
            )
        except Exception:
            pass  # Observational persistence must never affect trading


def route_broker_symbol(out: dict) -> str:
    target = out.get("target")
    if target is not None and getattr(target, "broker_symbol", None):
        return target.broker_symbol
    return str(out.get("broker_symbol", "") or "")


def _outcome_volume(out: dict) -> float:
    volume = out.get("volume")
    if hasattr(volume, "volume"):
        return float(volume.volume)
    return float(volume or 0.0)


# ─── PRIMARY OUTCOME ADAPTER ──────────────────────────────────────────────────


class _FanoutResult:
    """ExecutionResult-compatible view of one fan-out child outcome."""

    __slots__ = ("ok", "retcode", "deal", "order", "comment",
                 "fill_price", "price", "volume", "ownership")

    def __init__(self, outcome: dict):
        fill = outcome.get("fill_price") or 0.0
        self.ok = bool(outcome.get("ok"))
        self.retcode = int(outcome.get("retcode", -1))
        self.deal = int(outcome.get("deal", 0) or 0)
        self.order = int(outcome.get("order", 0) or 0)
        self.comment = str(outcome.get("comment", "") or "")
        self.fill_price = float(fill) if fill else None
        self.price = float(fill) if fill else 0.0
        self.volume = _outcome_volume(outcome)
        self.ownership = _restore_ownership(outcome.get("ownership"))


def _restore_ownership(raw):
    if not raw or not isinstance(raw, dict):
        return None
    try:
        from core.position_ownership import PositionOwnership
        return PositionOwnership(**raw)
    except Exception:
        return None


def primary_account_outcome(outcomes: list[dict], *, primary: str = "METAQUOTES"):
    """Pick the primary (METAQUOTES) child; fall back to the first outcome."""
    primary_out = None
    first = None
    for out in outcomes:
        target = out.get("target")
        account_id = getattr(target, "account_id", None) or out.get("account_id")
        if first is None:
            first = out
        if account_id == primary:
            primary_out = out
            break
    return primary_out if primary_out is not None else first


def build_primary_execution_outcome(outcomes: list[dict]):
    """Build an ExecutionOutcome for the existing single-account caller block.

    executed=True only when the primary worker submitted an order; ok/result
    mirror the primary fill. A local account block maps to executed=False with
    the block reason — it must NOT mutate the canonical decision.
    """
    from execution.execution_orchestrator import ExecutionOutcome
    from core.clock import utc_ms

    primary = primary_account_outcome(outcomes)
    if primary is None:
        return ExecutionOutcome(executed=False, error="no_multi_account_outcome")
    if primary.get("executed"):
        result = _FanoutResult(primary)
        return ExecutionOutcome(
            executed=True,
            ok=result.ok,
            result=result,
            decision_ts_utc_ms=utc_ms(),
        )
    reasons = str(primary.get("comment") or "")
    if not reasons:
        eligibility = primary.get("eligibility") or {}
        reasons = ",".join(eligibility.get("reasons", []) or ["blocked"])
    return ExecutionOutcome(executed=False, error=reasons or "execution_not_attempted")


# ─── SINGLE EXECUTION ROUTE (feature gate) ────────────────────────────────────


def dispatch_execution(
    *,
    intent: Any,
    symbol: str,
    cycle_id: int,
    decision_id: str,
    correlation_id: str,
    entity_id: str = "",
    observation_id: str = "",
    canonical_opportunity_id: str = "",
    strategy_family: str = "",
    bid: float = 0.0,
    ask: float = 0.0,
    legacy_execute: Callable | None = None,
):
    """Execute the canonical decision through exactly ONE route.

    fanout ON  → multi-account execution only (legacy never runs)
    fanout OFF → legacy single-account execution only (fan-out never runs)

    NO_TRADE / PATTERN_REJECT / RISK_BLOCK never reach this boundary — this
    dispatch is reached after the canonical decision is EXECUTE and the
    runtime guard chain has passed.
    """
    if multi_account_fanout_enabled():
        return execute_multi_account_fanout(
            intent=intent,
            symbol=symbol,
            cycle_id=cycle_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
            entity_id=entity_id,
            observation_id=observation_id,
            canonical_opportunity_id=canonical_opportunity_id,
            strategy_family=strategy_family,
            bid=bid,
            ask=ask,
        )
    if legacy_execute is None:
        raise RuntimeError("LEGACY_EXECUTION_UNAVAILABLE")
    return legacy_execute()