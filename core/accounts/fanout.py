"""Canonical decision fan-out (D) — single EXECUTE fans out to account targets.

Insertion point: immediately AFTER the canonical strategy decision
(V10 PipelineResult approved / OrderIntent) and BEFORE any broker I/O.

This module NEVER re-evaluates strategy, NEVER duplicates the canonical
decision, and NEVER mutates the parent. It only derives per-account
execution children that inherit the same parent lineage:

    canonical_opportunity_id / correlation_id / decision_id

Each child carries explicit account identity:
    account_id / broker / broker_server / canonical_symbol / broker_symbol
plus collision-safe runtime ids:
    account_execution_id / trade_id (uuid4 hex, unique per account).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass(frozen=True)
class CanonicalDecision:
    canonical_opportunity_id: str
    correlation_id: str
    decision_id: str
    canonical_symbol: str
    side: str  # "BUY" | "SELL"
    entry: float
    sl: float
    tp: float
    volume_hint: float = 0.0  # canonical reference only; per-account volume recomputed
    pattern: str = ""
    observation_id: str = ""


@dataclass(frozen=True)
class AccountTarget:
    account_id: str
    broker: str
    broker_server: str
    login: int
    canonical_symbol: str
    broker_symbol: str | None
    canonical_opportunity_id: str
    correlation_id: str
    decision_id: str
    account_execution_id: str
    trade_id: str
    requested_volume: float
    entry: float
    sl: float
    tp: float
    pattern: str = ""
    observation_id: str = ""
    metadata: dict = field(default_factory=dict)


def _new_scoped_ids(account_id: str) -> tuple[str, str]:
    return (
        f"exec_{account_id}_{uuid.uuid4().hex}",
        f"trade_{account_id}_{uuid.uuid4().hex}",
    )


def fan_out_canonical_decision(
    decision: CanonicalDecision,
    accounts: Any,
    *,
    broker_symbols: dict[str, str | None] | None = None,
) -> tuple[AccountTarget, ...]:
    """Fan out ONE canonical EXECUTE into per-account targets (D boundary).

    Args:
        decision: single canonical EXECUTE (never duplicated/mutated).
        accounts: iterable of AccountConfig (uses account_id/broker/server/login).
        broker_symbols: optional explicit {account_id: broker_symbol|None}.
            When omitted, broker_symbol is left None and resolved downstream
            at the execution/risk boundary (account snapshot). A None value
            means SYMBOL_UNAVAILABLE for that account only.

    Returns:
        Tuple of AccountTarget, one per provided account, all sharing the
        same parent lineage. Order follows input order (deterministic).
    """
    if not all((
        decision.canonical_opportunity_id,
        decision.correlation_id,
        decision.decision_id,
    )):
        raise ValueError("PARENT_LINEAGE_REQUIRED")
    broker_symbols = broker_symbols or {}
    targets: list[AccountTarget] = []
    for account in accounts:
        account_id = getattr(account, "account_id")
        execution_id, trade_id = _new_scoped_ids(account_id)
        targets.append(AccountTarget(
            account_id=account_id,
            broker=getattr(account, "broker", ""),
            broker_server=getattr(account, "server", ""),
            login=getattr(account, "login", 0) or 0,
            canonical_symbol=decision.canonical_symbol,
            broker_symbol=broker_symbols.get(account_id),
            canonical_opportunity_id=decision.canonical_opportunity_id,
            correlation_id=decision.correlation_id,
            decision_id=decision.decision_id,
            account_execution_id=execution_id,
            trade_id=trade_id,
            requested_volume=float(decision.volume_hint or 0.0),
            entry=float(decision.entry),
            sl=float(decision.sl),
            tp=float(decision.tp),
            pattern=decision.pattern,
            observation_id=decision.observation_id,
        ))
    return tuple(targets)


def decision_from_intent(
    *,
    intent: Any,
    canonical_opportunity_id: str,
    correlation_id: str,
    decision_id: str,
    observation_id: str = "",
) -> CanonicalDecision:
    """Adapt a canonical OrderIntent into a CanonicalDecision (no re-evaluation)."""
    side = getattr(intent.side, "name", str(intent.side))
    return CanonicalDecision(
        canonical_opportunity_id=canonical_opportunity_id,
        correlation_id=correlation_id,
        decision_id=decision_id,
        canonical_symbol=str(intent.symbol),
        side=str(side),
        entry=float(intent.entry_reference),
        sl=float(intent.sl),
        tp=float(intent.tp),
        volume_hint=float(getattr(intent, "volume", 0.0) or 0.0),
        pattern=str(getattr(intent, "pattern", "") or ""),
        observation_id=observation_id,
    )
