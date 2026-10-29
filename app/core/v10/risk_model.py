"""V10 Risk Model — Capital protection decision model.

Answers: "Given this trade plan, is the risk acceptable?"

Does NOT:
  - Find trades or choose strategies
  - Change direction or improve bad setups
  - Override market context
  - Create edge (a bad trade at lower size is still bad)

Contains:
  - Approval/rejection decision
  - Position sizing
  - Geometry validation
  - Exposure checks
  - Strategy/horizon-aware risk profiles
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AccountContext:
    """Live MT5 account state — no hardcoded defaults.
    
    All financial values default to 0.0 meaning "unavailable".
    The risk engine distinguishes unavailable (balance=0) from
    an actual zero-balance account (which would also be rejected).
    """
    # Identity
    login: int = 0
    server: str = ""
    currency: str = ""

    # Account configuration
    leverage: int = 0
    margin_mode: int = 0

    # Capital
    balance: float = 0.0
    equity: float = 0.0
    credit: float = 0.0
    profit: float = 0.0                   # Floating P&L of open positions

    # Margin
    margin: float = 0.0                   # Used margin
    margin_free: float = 0.0             # Available margin
    margin_level: float = 0.0            # Equity/margin × 100
    stop_out_level: float = 0.0          # Broker stopout level %

    # Position state (from scanner — MT5 doesn't track per-symbol natively)
    current_open_risk_pct: float = 0.0
    open_positions: int = 0
    daily_loss_pct: float = 0.0
    symbols_with_positions: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """True if account data was successfully read from MT5."""
        return self.balance > 0 or self.equity > 0


@dataclass(frozen=True)
class RiskProfile:
    """Computed risk parameters for the trade."""
    risk_percentage: float = 0.0
    max_loss_amount: float = 0.0
    position_size: float = 0.0


@dataclass(frozen=True)
class TradeGeometry:
    """Validated trade geometry."""
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    stop_distance: float = 0.0
    reward_distance: float = 0.0
    expected_rr: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    """Immutable risk assessment for a trade plan."""

    opportunity_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0

    approved: bool = False
    rejection_reason: str = ""

    risk_profile: RiskProfile = field(default_factory=RiskProfile)
    trade_geometry: TradeGeometry = field(default_factory=TradeGeometry)
    risk_checks: dict[str, bool] = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "approved": self.approved,
            "rejection_reason": self.rejection_reason,
            "risk_profile": {
                "risk_percentage": round(self.risk_profile.risk_percentage, 4),
                "max_loss_amount": round(self.risk_profile.max_loss_amount, 2),
                "position_size": round(self.risk_profile.position_size, 4),
            },
            "trade_geometry": {
                "entry_price": self.trade_geometry.entry_price,
                "stop_price": self.trade_geometry.stop_price,
                "target_price": self.trade_geometry.target_price,
                "stop_distance": self.trade_geometry.stop_distance,
                "reward_distance": self.trade_geometry.reward_distance,
                "expected_rr": round(self.trade_geometry.expected_rr, 2),
            },
            "risk_checks": dict(self.risk_checks),
            "reasoning": list(self.reasoning),
        }
