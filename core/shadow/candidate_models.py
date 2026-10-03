"""GENERIC shadow-candidate runtime state (Block 1).

Reusable prospective candidate state/identity/treatment contract.
GENERIC: knows nothing about OPT-DP1-002, trailing, readiness, promotion.
Policy-specific state lives inside opaque ``treatment_state``.
Execution isolation: pure data + protocol, no broker/execution imports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

SCHEMA_VERSION = "shadow_candidate_v1"
CANDIDATE_RUNTIME_VERSION = "candidate_runtime_v1"
CANDIDATE_MODEL_VERSION = "candidate_model_v1"
CANDIDATE_PRODUCER = "shadow_candidate_runtime"

CANDIDATE_EVENT_TYPES = (
    "CANDIDATE_OPEN",
    "CANDIDATE_PROGRESS",
    "CANDIDATE_CLOSE",
    "CANDIDATE_INVALID",
)

CANDIDATE_STATE_ACTIVE = "ACTIVE"
CANDIDATE_STATE_CLOSED = "CLOSED"
CANDIDATE_STATE_INVALID = "INVALID"


def candidate_runtime_id(
    *,
    shadow_trade_id: str,
    canonical_opportunity_id: str,
    trade_horizon: str,
    candidate_id: str,
    policy_id: str,
) -> str:
    """Deterministic unique candidate-lifecycle identity (no account ID)."""
    material = "|".join(
        (
            str(shadow_trade_id or ""),
            str(canonical_opportunity_id or ""),
            str(trade_horizon or "").upper(),
            str(candidate_id or ""),
            str(policy_id or ""),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"cand_{digest}"

@dataclass(frozen=True)
class CandidateIdentity:
    shadow_trade_id: str
    canonical_opportunity_id: str
    trade_horizon: str
    candidate_id: str
    policy_id: str
    treatment_hash: str = ""

    def validate(self) -> str | None:
        if not self.shadow_trade_id:
            return "CANDIDATE_LIFECYCLE_IDENTITY_MISSING"
        if not self.canonical_opportunity_id:
            return "CANDIDATE_CANONICAL_IDENTITY_MISSING"
        if not self.trade_horizon:
            return "CANDIDATE_HORIZON_MISSING"
        if not self.candidate_id:
            return "CANDIDATE_ID_MISSING"
        if not self.policy_id:
            return "CANDIDATE_POLICY_IDENTITY_INVALID"
        if not self.treatment_hash:
            return "CANDIDATE_TREATMENT_HASH_MISSING"
        return None

    @property
    def runtime_id(self) -> str:
        return candidate_runtime_id(
            shadow_trade_id=self.shadow_trade_id,
            canonical_opportunity_id=self.canonical_opportunity_id,
            trade_horizon=self.trade_horizon,
            candidate_id=self.candidate_id,
            policy_id=self.policy_id,
        )


def compute_candidate_r(direction, entry_price, exit_price, risk_distance):
    try:
        risk = float(risk_distance)
        if not (risk > 0):
            return None
        entry = float(entry_price)
        exit_ = float(exit_price)
    except (TypeError, ValueError):
        return None
    side = str(direction or "").upper()
    if side == "BUY":
        return (exit_ - entry) / risk
    if side == "SELL":
        return (entry - exit_) / risk
    return None



@dataclass(frozen=True)
class TreatmentBar:
    symbol: str
    bar_time_utc: int
    bar_open: float | None
    bar_high: float
    bar_low: float
    bar_close: float
    bar_index: int = 0


@dataclass(frozen=True)
class TreatmentResult:
    treatment_state: dict
    terminal: bool = False
    exit_reason: str = ""
    exit_price: float | None = None
    diagnostic: str = ""


class CandidateTreatmentAdapter(Protocol):
    def initialize(self, *, entry_geometry: dict) -> dict:
        ...  # pragma: no cover - interface

    def on_bar(self, *, entry_geometry: dict, risk_distance: float,
               direction: str, bar: TreatmentBar,
               prior_state: dict) -> TreatmentResult:
        ...  # pragma: no cover - interface

@dataclass
class CandidateState:
    candidate_id: str = ""
    policy_id: str = ""
    treatment_hash: str = ""
    shadow_trade_id: str = ""
    canonical_opportunity_id: str = ""
    trade_horizon: str = ""
    symbol: str = ""
    direction: str = ""
    entry_time: int = 0
    entry_price: float = 0.0
    original_stop_loss: float = 0.0
    original_take_profit: float = 0.0
    original_risk_distance: float = 0.0
    state: str = CANDIDATE_STATE_ACTIVE
    last_evaluated_bar_time: int = 0
    bars_elapsed: int = 0
    treatment_state: dict = field(default_factory=dict)
    candidate_exit_reason: str = ""
    candidate_exit_price: float | None = None
    candidate_exit_time: int | None = None
    candidate_r: float | None = None
    terminal: bool = False
    lineage: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    model_version: str = CANDIDATE_MODEL_VERSION
    runtime_version: str = CANDIDATE_RUNTIME_VERSION

    @property
    def identity(self) -> CandidateIdentity:
        return CandidateIdentity(
            shadow_trade_id=self.shadow_trade_id,
            canonical_opportunity_id=self.canonical_opportunity_id,
            trade_horizon=self.trade_horizon,
            candidate_id=self.candidate_id,
            policy_id=self.policy_id,
            treatment_hash=self.treatment_hash,
        )

    @property
    def runtime_id(self) -> str:
        return self.identity.runtime_id

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "policy_id": self.policy_id,
            "treatment_hash": self.treatment_hash,
            "shadow_trade_id": self.shadow_trade_id,
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "trade_horizon": self.trade_horizon,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "original_stop_loss": self.original_stop_loss,
            "original_take_profit": self.original_take_profit,
            "original_risk_distance": self.original_risk_distance,
            "state": self.state,
            "last_evaluated_bar_time": self.last_evaluated_bar_time,
            "bars_elapsed": self.bars_elapsed,
            "treatment_state": dict(self.treatment_state or {}),
            "candidate_exit_reason": self.candidate_exit_reason,
            "candidate_exit_price": self.candidate_exit_price,
            "candidate_exit_time": self.candidate_exit_time,
            "candidate_r": self.candidate_r,
            "terminal": bool(self.terminal),
            "lineage": dict(self.lineage or {}),
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "runtime_version": self.runtime_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CandidateState:
        get = dict(d or {}).get
        return cls(
            candidate_id=str(get("candidate_id", "") or ""),
            policy_id=str(get("policy_id", "") or ""),
            treatment_hash=str(get("treatment_hash", "") or ""),
            shadow_trade_id=str(get("shadow_trade_id", "") or ""),
            canonical_opportunity_id=str(get("canonical_opportunity_id", "") or ""),
            trade_horizon=str(get("trade_horizon", "") or ""),
            symbol=str(get("symbol", "") or ""),
            direction=str(get("direction", "") or ""),
            entry_time=int(get("entry_time", 0) or 0),
            entry_price=float(get("entry_price", 0.0) or 0.0),
            original_stop_loss=float(get("original_stop_loss", 0.0) or 0.0),
            original_take_profit=float(get("original_take_profit", 0.0) or 0.0),
            original_risk_distance=float(get("original_risk_distance", 0.0) or 0.0),
            state=str(get("state", CANDIDATE_STATE_ACTIVE) or CANDIDATE_STATE_ACTIVE),
            last_evaluated_bar_time=int(get("last_evaluated_bar_time", 0) or 0),
            bars_elapsed=int(get("bars_elapsed", 0) or 0),
            treatment_state=dict(get("treatment_state", {}) or {}),
            candidate_exit_reason=str(get("candidate_exit_reason", "") or ""),
            candidate_exit_price=get("candidate_exit_price"),
            candidate_exit_time=get("candidate_exit_time"),
            candidate_r=get("candidate_r"),
            terminal=bool(get("terminal", False)),
            lineage=dict(get("lineage", {}) or {}),
            schema_version=str(get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
            model_version=str(get("model_version", CANDIDATE_MODEL_VERSION) or CANDIDATE_MODEL_VERSION),
            runtime_version=str(get("runtime_version", CANDIDATE_RUNTIME_VERSION) or CANDIDATE_RUNTIME_VERSION),
        )


__all__ = [
    "CANDIDATE_EVENT_TYPES",
    "CANDIDATE_MODEL_VERSION",
    "CANDIDATE_PRODUCER",
    "CANDIDATE_RUNTIME_VERSION",
    "CANDIDATE_STATE_ACTIVE",
    "CANDIDATE_STATE_CLOSED",
    "CANDIDATE_STATE_INVALID",
    "SCHEMA_VERSION",
    "CandidateIdentity",
    "CandidateState",
    "CandidateTreatmentAdapter",
    "TreatmentBar",
    "TreatmentResult",
    "candidate_runtime_id",
    "compute_candidate_r",
]

