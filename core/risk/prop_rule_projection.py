"""PRE-TRADE PROJECTION for prop rule enforcement (Block 3C).

THE PROBLEM
-----------
"Current open risk is 4% against a 5% limit" is not a safe reason to allow a
2% trade. Evaluating only the CURRENT state would let the proposed order create
the breach. So entry-sensitive limits must be evaluated twice: against current
state AND against the state that WOULD EXIST after the order fills.

THE METHOD — WHY 3C DOES NOT DUPLICATE 3B
------------------------------------------
Block 3C does NOT re-implement any limit comparison. It builds a PROJECTED
:class:`AccountEvaluationState` and hands that projected state back to Block
3B's own evaluators, which then apply exactly the same arithmetic they applied
to the current state. One implementation of every rule; two states evaluated.

    current  4%  ->  3B.evaluate_pack(current_state)   -> PASS
    projected 6% ->  3B.evaluate_pack(projected_state) -> BREACH  <- blocks entry

WHAT IS PROJECTED
-----------------
Only quantities a proposed order can actually change:

* total open risk, and the per-symbol / correlation-cluster / directional risk
  aggregates, combined with the EXACT planned monetary risk from the broker
  request (not a pip estimate),
* position count, per-symbol position count, and lot sizes,
* the largest single-position risk.

WHAT IS NOT PROJECTED
---------------------
Anchors, high-water marks, realised P&L, trading-day history, account balance
and equity. A pending order does not change any of them, and pretending
otherwise would be a fabricated state. Those fields are carried through
unchanged, so a daily-loss or drawdown rule sees the same evidence it would
have seen anyway.

EXACTNESS
---------
Every projected value is either an authoritative 2B/2C figure plus the exact
planned risk, or ``None`` with the field reported unavailable. A partial risk
floor is never presented as a total: if the current exposure was not
authoritative, the projection stays unavailable and 3B returns INDETERMINATE.
"""


from __future__ import annotations
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from core.risk.prop_rule_state import (
    AccountEvaluationState,
    StateStatus,
    content_hash,
)


class ProjectionError(RuntimeError):
    """The proposed order could not be projected safely (fail closed)."""


@dataclass(frozen=True)
class PlannedOrder:
    """A broker-normalised proposed entry, ready for projection.

    ``planned_risk_amount`` MUST be the exact account-currency monetary risk of
    the intended SL/volume as computed by the broker's own order-profit
    calculation. It is ``None`` when that exact figure could not be obtained,
    which makes every monetary projection INDETERMINATE rather than guessed
    from pips.
    """

    canonical_symbol: str
    side: str
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float = 0.0
    planned_risk_amount: float | None = None
    broker_symbol: str = ""
    deviation: int = 20
    magic: int = 0

    def __post_init__(self) -> None:
        if not str(self.canonical_symbol or "").strip():
            raise ProjectionError("PLANNED_ORDER_REQUIRES_CANONICAL_SYMBOL")
        if str(self.side or "").strip().upper() not in {"BUY", "SELL"}:
            raise ProjectionError("PLANNED_ORDER_SIDE_MUST_BUY_OR_SELL")
        if float(self.volume) <= 0:
            raise ProjectionError("PLANNED_ORDER_VOLUME_MUST_BE_POSITIVE")
        if float(self.entry_price) <= 0:
            raise ProjectionError("PLANNED_ORDER_ENTRY_MUST_BE_POSITIVE")
        risk = self.planned_risk_amount
        if risk is not None:
            risk = float(risk)
            if risk < 0:
                raise ProjectionError("PLANNED_ORDER_RISK_MUST_BE_NON_NEGATIVE")
            object.__setattr__(self, "planned_risk_amount", risk)
        object.__setattr__(self, "side", str(self.side).strip().upper())
        object.__setattr__(
            self, "canonical_symbol", str(self.canonical_symbol).strip().upper()
        )

    @property
    def has_exact_risk(self) -> bool:
        """Whether the exact planned monetary risk is known."""
        return self.planned_risk_amount is not None

    def identity(self) -> str:
        """Deterministic identity of THIS proposed order, for audit lineage."""
        payload = {
            "canonical_symbol": self.canonical_symbol,
            "side": self.side,
            "volume": float(self.volume),
            "entry_price": float(self.entry_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "planned_risk_amount": self.planned_risk_amount,
        }
        return "plan_" + content_hash(payload)[:32]



# ═════════════════════════════════════════════════════════════════════════════
# EXPOSURE INPUTS — the exact 2B/2C evidence the projection reads
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExposureProjection:
    """The post-fill exposure a proposed order would create.

    Every field is ``None`` when the CURRENT exposure it extends was not
    authoritative. A partial floor is never upgraded into a total here: that is
    the single most dangerous projection bug and it fails closed by construction.
    """

    total_open_risk: float | None = None
    position_count: int | None = None
    largest_position_risk: float | None = None
    largest_lot_size: float | None = None
    symbol_position_count: int | None = None
    symbol_risk: float | None = None
    largest_symbol_risk: float | None = None
    largest_symbol: str = ""
    cluster_risk: float | None = None
    cluster_id: str = ""
    directional_risk: float | None = None
    direction: str = ""
    #: Fields the projection could NOT establish, so 3B will be INDETERMINATE.
    unavailable_fields: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.unavailable_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_open_risk": self.total_open_risk,
            "position_count": self.position_count,
            "largest_position_risk": self.largest_position_risk,
            "largest_lot_size": self.largest_lot_size,
            "symbol_position_count": self.symbol_position_count,
            "symbol_risk": self.symbol_risk,
            "largest_symbol_risk": self.largest_symbol_risk,
            "largest_symbol": self.largest_symbol,
            "cluster_risk": self.cluster_risk,
            "cluster_id": self.cluster_id,
            "directional_risk": self.directional_risk,
            "direction": self.direction,
            "unavailable_fields": list(self.unavailable_fields),
        }


def _symbol_exposure(portfolio: Any, canonical_symbol: str) -> Any:
    """The exact 2C per-symbol row for this canonical symbol, or ``None``."""
    if portfolio is None:
        return None
    symbol = str(canonical_symbol or "").strip().upper()
    for row in getattr(portfolio, "symbol_exposure", ()) or ():
        if str(getattr(row, "canonical_symbol", "")).strip().upper() == symbol:
            return row
    return None


def _authoritative_risk(row: Any) -> float | None:
    """A per-symbol/cluster total ONLY when 2C marked it complete.

    ``known_risk`` is a provable floor, not a total. Using it as a total would
    understate exposure, so an incomplete row returns ``None`` here.
    """
    if row is None:
        return None
    complete = bool(getattr(row, "risk_complete", False)) or bool(
        getattr(row, "cluster_risk_complete", False)
    )
    total = getattr(row, "total_risk", None)
    if total is None:
        total = getattr(row, "total_cluster_risk", None)
    if complete and total is not None:
        return float(total)
    return None


def _direction_of(side: str) -> str:
    return "LONG" if str(side).strip().upper() == "BUY" else "SHORT"


def project_exposure(
    *,
    state: AccountEvaluationState,
    order: PlannedOrder,
    correlation_model: Any = None,
    portfolio: Any = None,
) -> ExposureProjection:
    """Combine CURRENT 2B/2C exposure with the EXACT planned order risk.

    ``portfolio`` is the Block 2C ``PortfolioExposure`` for the SAME
    observation. When supplied, per-symbol and per-cluster figures come from the
    SAME canonical correlation model 2C used, so a projected cluster risk is
    measured with exactly the model the current cluster risk was. There is no
    ad-hoc correlation logic in 3C.

    No limit is compared here. This only computes what the exposure WOULD be;
    comparing it to a limit is 3B's job on the projected state.
    """
    missing: list[str] = []
    risk = order.planned_risk_amount
    if risk is None:
        # Without the exact broker risk figure no monetary projection is
        # possible. Every monetary field becomes unavailable, which 3B turns
        # into INDETERMINATE rather than an optimistic PASS.
        missing.extend(
            [
                "total_open_risk",
                "largest_position_risk",
                "symbol_risk",
                "cluster_risk",
                "directional_risk",
            ]
        )

    # Without the exact planned risk the PROJECTED total is genuinely unknown:
    # reporting the CURRENT total would understate post-fill exposure and let a
    # breaching order through. It is reported unavailable instead.
    if state.total_open_risk is None or risk is None:
        if state.total_open_risk is None:
            missing.append("total_open_risk")
        projected_total = None
    else:
        projected_total = float(state.total_open_risk) + float(risk)

    if state.position_count is None:
        missing.append("position_count")
        projected_count = None
    else:
        projected_count = int(state.position_count) + 1

    if state.largest_position_risk is None or risk is None:
        if state.largest_position_risk is None:
            missing.append("largest_position_risk")
        projected_largest_position = (
            float(risk) if (risk is not None and state.largest_position_risk is None) else None
        )
    else:
        projected_largest_position = max(
            float(state.largest_position_risk), float(risk)
        )

    if state.largest_lot_size is None:
        missing.append("largest_lot_size")
        projected_largest_lot = None
    else:
        projected_largest_lot = max(
            float(state.largest_lot_size), float(order.volume)
        )

    # ── per-symbol: from the SAME 2C canonical exposure record ──────────────
    symbol_row = _symbol_exposure(portfolio, order.canonical_symbol)
    if symbol_row is not None:
        symbol_count = int(getattr(symbol_row, "position_count", 0) or 0) + 1
        base = _authoritative_risk(symbol_row)
        if base is not None and risk is not None:
            symbol_risk = float(base) + float(risk)
        else:
            symbol_risk = None
            missing.append("symbol_risk")
    else:
        # No per-symbol row means this symbol currently has no exposure, so the
        # projected per-symbol risk is exactly the planned risk.
        symbol_count = 1
        symbol_risk = float(risk) if risk is not None else None
        if risk is None:
            missing.append("symbol_risk")

    if state.largest_symbol_risk is None or risk is None:
        if state.largest_symbol_risk is None:
            missing.append("largest_symbol_risk")
        projected_largest_symbol = (
            float(risk) if (risk is not None and state.largest_symbol_risk is None) else None
        )
    else:
        projected_largest_symbol = max(float(state.largest_symbol_risk), float(risk))

    # ── correlation cluster: mapped through the SAME governed model ─────────
    cluster_id = ""
    cluster_risk: float | None = None
    cluster_row = _cluster_exposure(portfolio, correlation_model, order.canonical_symbol)
    if cluster_row is not None:
        cluster_id = str(getattr(cluster_row, "cluster_id", "") or "")
        base = _authoritative_risk(cluster_row)
        if base is not None and risk is not None:
            cluster_risk = float(base) + float(risk)
        else:
            missing.append("cluster_risk")
    elif correlation_model is not None and risk is not None:
        # The model knows this symbol's cluster but no cluster row exists, so
        # the cluster currently holds nothing for it: the projection is the
        # planned risk itself.
        cluster_id = str(
            correlation_model.cluster_for_symbol(order.canonical_symbol)
        )
        cluster_risk = float(risk)

    # ── directional exposure: the proposed side's own aggregate ────────────
    direction = _direction_of(order.side)
    if state.directional_risk is None or risk is None:
        if state.directional_risk is None:
            missing.append("directional_risk")
        projected_directional = (
            float(risk) if (risk is not None and state.directional_risk is None) else None
        )
    else:
        projected_directional = max(float(state.directional_risk), float(risk))

    return ExposureProjection(
        total_open_risk=projected_total,
        position_count=projected_count,
        largest_position_risk=projected_largest_position,
        largest_lot_size=projected_largest_lot,
        symbol_position_count=symbol_count,
        symbol_risk=symbol_risk,
        largest_symbol_risk=projected_largest_symbol,
        largest_symbol=str(state.largest_symbol or order.canonical_symbol),
        cluster_risk=cluster_risk,
        cluster_id=cluster_id,
        directional_risk=projected_directional,
        direction=direction,
        unavailable_fields=tuple(sorted(set(missing))),
    )


def _cluster_exposure(portfolio: Any, model: Any, canonical_symbol: str) -> Any:
    """The 2C cluster row for this symbol's governed cluster, or ``None``."""
    if portfolio is None or model is None:
        return None
    try:
        cluster_id = model.cluster_for_symbol(canonical_symbol)
    except Exception:
        return None
    for row in getattr(portfolio, "cluster_exposure", ()) or ():
        if str(getattr(row, "cluster_id", "")) == cluster_id:
            return row
    return None


# ═════════════════════════════════════════════════════════════════════════════
# PROJECTED STATE — the ONLY thing 3B is re-asked to evaluate
# ═════════════════════════════════════════════════════════════════════════════


def build_projected_state(
    *,
    state: AccountEvaluationState,
    order: PlannedOrder,
    projection: ExposureProjection,
    observed_at_utc: datetime,
) -> AccountEvaluationState:
    """Return the state that WOULD exist after ``order`` fills.

    Only exposure fields move. Anchors, high-water marks, the realised ledger
    and trading-day history are carried through byte-identically, because a
    pending order genuinely does not change them. That is what keeps the
    projected evaluation of a daily-loss or drawdown rule identical to the
    current one, and therefore free of duplicated arithmetic.

    PROJECTED IDENTITY
    ------------------
    A new ``evaluation_id`` namespace is used so a projected evaluation can
    never be confused with, or collide with, the current one in the audit.
    """
    if observed_at_utc.tzinfo is None:
        raise ProjectionError("PROJECTED_STATE_REQUIRES_AWARE_INSTANT")
    unavailable = tuple(
        sorted(set(state.unavailable_fields) | set(projection.unavailable_fields))
    )
    invalid = state.invalid_fields
    status = state.status
    if projection.unavailable_fields and status is StateStatus.COMPLETE:
        status = StateStatus.PARTIAL
    return replace(
        state,
        evaluation_id="proj_" + content_hash(
            {
                "base_evaluation_id": state.evaluation_id,
                "order": order.identity(),
                "observed_at_utc": observed_at_utc.isoformat(),
            }
        )[:32],
        observed_at_utc=observed_at_utc,
        total_open_risk=projection.total_open_risk,
        position_count=projection.position_count,
        largest_position_risk=projection.largest_position_risk,
        largest_lot_size=projection.largest_lot_size,
        largest_symbol_risk=projection.largest_symbol_risk,
        correlated_risk=projection.cluster_risk,
        largest_symbol=projection.largest_symbol or state.largest_symbol,
        directional_risk=projection.directional_risk,
        largest_direction=projection.direction or state.largest_direction,
        status=status,
        unavailable_fields=unavailable,
        invalid_fields=invalid,
    )
