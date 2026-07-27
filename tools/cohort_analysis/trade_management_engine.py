"""
Trade Management Engine — Converts ManagementPolicy into live trade exit behavior.

Handles:
  - Break-even stop logic
  - Trailing stop logic
  - Partial take-profit logic

ONLY affects stop loss management, take profit, and trade modifications.
Does NOT modify entry logic, scoring, or execution decisions.
"""

from __future__ import annotations

from typing import Any

from tools.cohort_analysis.cohort_policy_types import ManagementPolicy


class TradeManagementEngine:
    """
    Evaluates trade management decisions based on a ManagementPolicy.

    Usage:
        engine = TradeManagementEngine(policy)
        trade = engine.update_trade(trade, current_price)
    """

    def __init__(self, policy: ManagementPolicy) -> None:
        self.policy = policy

    def update_trade(self, trade: dict[str, Any], current_price: float) -> dict[str, Any]:
        """
        Evaluate and apply trade management rules based on policy.

        Args:
            trade: Trade dict with at minimum:
                - entry_price (float)
                - stop_loss (float)
                - take_profit (float)
                - side ("BUY" / "SELL")
                - risk_distance (float): |entry - initial_sl|
            current_price: Live market price.

        Returns:
            Updated trade dict with modified SL, partial flags, trailing state.
        """
        entry = trade.get("entry_price", 0.0)
        risk = trade.get("risk_distance", 0.0)
        side = trade.get("side", "BUY")

        if risk <= 0 or entry <= 0:
            return trade

        # Compute unrealized R
        if side == "BUY":
            unrealized_r = (current_price - entry) / risk
        else:
            unrealized_r = (entry - current_price) / risk

        trade["unrealized_r"] = round(unrealized_r, 4)

        # Track MFE
        prev_mfe = trade.get("mfe_r", 0.0)
        if unrealized_r > prev_mfe:
            trade["mfe_r"] = round(unrealized_r, 4)

        # Apply management rules
        self._apply_break_even(trade, unrealized_r, entry, risk, side)
        self._apply_trailing(trade, unrealized_r, entry, risk, side)
        self._apply_partial_tp(trade, unrealized_r)

        return trade

    # ─── BREAK-EVEN LOGIC ─────────────────────────────────────────────────────

    def _apply_break_even(
        self,
        trade: dict[str, Any],
        unrealized_r: float,
        entry: float,
        risk: float,
        side: str,
    ) -> None:
        """Apply break-even stop based on policy mode."""
        if self.policy.break_even_mode == "OFF":
            return

        if trade.get("be_triggered", False):
            return  # Already moved to BE

        trigger_r = self._be_trigger_level()

        if unrealized_r >= trigger_r:
            # Move SL to entry (breakeven)
            if side == "BUY":
                trade["stop_loss"] = entry
            else:
                trade["stop_loss"] = entry
            trade["be_triggered"] = True

    def _be_trigger_level(self) -> float:
        """Return R level at which BE activates."""
        if self.policy.break_even_mode == "EARLY":
            return 0.5
        elif self.policy.break_even_mode == "DELAYED":
            return 1.0
        return 999.0  # Effectively OFF

    # ─── TRAILING STOP LOGIC ──────────────────────────────────────────────────

    def _apply_trailing(
        self,
        trade: dict[str, Any],
        unrealized_r: float,
        entry: float,
        risk: float,
        side: str,
    ) -> None:
        """Apply trailing stop based on policy mode."""
        if self.policy.trailing_mode == "OFF":
            return

        activation_r, distance_r = self._trail_params()

        mfe_r = trade.get("mfe_r", 0.0)

        if mfe_r < activation_r:
            return  # Not yet activated

        # Compute trailing stop level
        trail_r = mfe_r - distance_r
        if trail_r <= 0:
            return  # Trail hasn't locked anything meaningful

        # Convert trail R to price
        if side == "BUY":
            trail_price = entry + (trail_r * risk)
            current_sl = trade.get("stop_loss", 0.0)
            if trail_price > current_sl:
                trade["stop_loss"] = round(trail_price, 6)
                trade["trailing_active"] = True
        else:
            trail_price = entry - (trail_r * risk)
            current_sl = trade.get("stop_loss", 999999.0)
            if trail_price < current_sl:
                trade["stop_loss"] = round(trail_price, 6)
                trade["trailing_active"] = True

    def _trail_params(self) -> tuple[float, float]:
        """Return (activation_r, distance_r) for trailing mode."""
        if self.policy.trailing_mode == "AGGRESSIVE":
            return 1.0, 0.5   # Activate at +1R, trail 0.5R behind
        elif self.policy.trailing_mode == "LIGHT":
            return 2.0, 1.0   # Activate at +2R, trail 1R behind
        return 999.0, 999.0   # Effectively OFF

    # ─── PARTIAL TAKE-PROFIT LOGIC ────────────────────────────────────────────

    def _apply_partial_tp(
        self,
        trade: dict[str, Any],
        unrealized_r: float,
    ) -> None:
        """Apply partial take-profit based on policy mode."""
        if self.policy.partial_tp_mode == "OFF":
            return

        if trade.get("partial_executed", False):
            return  # Already taken partial

        trigger_r = self._partial_trigger_level()

        if unrealized_r >= trigger_r:
            trade["partial_executed"] = True
            trade["partial_trigger_r"] = trigger_r
            trade["partial_fraction"] = 0.5  # Close 50%

    def _partial_trigger_level(self) -> float:
        """Return R level at which partial TP triggers."""
        if self.policy.partial_tp_mode == "AGGRESSIVE":
            return 1.0
        elif self.policy.partial_tp_mode == "STANDARD":
            return 2.0
        return 999.0  # Effectively OFF
