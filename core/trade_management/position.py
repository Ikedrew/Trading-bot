"""Tracked position for post-entry lifecycle (Layer 9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from strategy.signals import Side

if TYPE_CHECKING:
    from core.trade_identity import TradeIdentity


class PositionStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"


@dataclass
class Position:
    """
    Authoritative local view of one managed position; kept in sync with price updates only.

    IDENTITY OWNERSHIP:
        Every Position owns the immutable TradeIdentity of the decision that created it.
        The identity is set at registration time and NEVER modified.
        Downstream persistence layers (Trade Journal, Trade Truth) read identity
        from this Position — never from transient thread-local context.
    """

    position_id: str
    symbol: str
    side: Side
    magic: int

    entry_price: float
    initial_sl: float
    initial_tp: float
    stop_loss: float
    take_profit: float

    volume: float
    open_time: float

    status: PositionStatus = PositionStatus.OPEN
    unrealised_pnl: float = 0.0

    #: Favourable excursion since open (price movement helping the trade), in price units.
    max_favourable_price: float = 0.0

    #: Adverse excursion since open (worst price against the position), in price units.
    #: None = no valid observation yet (unknown) — never seeded to a misleading
    #: market value. Observational telemetry only; never used for exit/stop logic.
    max_adverse_price: float | None = None

    #: Provenance of the excursion extremes:
    #:   "full_lifecycle"  — tracked since open (or restored from durable state)
    #:   "recovery_seeded" — no durable state on restart; seeded from broker price
    #:   "unknown"         — no valid excursion observation
    #: Observational metadata only; never used for any trading decision.
    excursion_provenance: str = "full_lifecycle"

    mt5_ticket: int | None = None
    deal_id: int = 0
    order_id: int = 0

    #: Echo only — never used for exit logic.
    pattern_tag: str = ""

    #: Trade horizon identity. Set at registration from OrderIntent.metadata["horizon"].
    #: Determines which HorizonExecutionProfile governs this position's lifecycle.
    #: INVARIANT: Every Position belongs to exactly one horizon profile.
    trade_horizon: str = "SCALP"

    #: Timestamp when position was closed (set by TradeStateManager)
    closed_time: float | None = None

    #: Immutable decision-origin identity. Set at registration, never modified.
    #: Carries the correlation_id, decision_id, cycle_id, strategy, pattern,
    #: and decision timestamp from the originating execution decision.
    #: Persistence layers read identity from here — never from thread-local context.
    trade_identity: "TradeIdentity | None" = None

    _meta: dict = field(default_factory=dict)

    @property
    def correlation_id(self) -> str:
        """Convenience accessor for the correlation_id from trade_identity."""
        if self.trade_identity is not None:
            return self.trade_identity.correlation_id
        return ""
