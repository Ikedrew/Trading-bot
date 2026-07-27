"""
Horizon Execution Profile — Single source of truth for horizon-specific behaviour.

ARCHITECTURAL INVARIANT:
    Every live Position belongs to exactly one HorizonExecutionProfile.
    All future horizon-specific behaviour MUST be resolved through this abstraction.
    No component should use `if trade_horizon == "SCALP":` — instead, obtain
    behaviour from the profile.

This module establishes the interface that later phases will implement:
    - Break-even policy
    - Trailing stop policy
    - Partial TP policy
    - Time exit policy
    - Portfolio allocation policy
    - Risk management defaults
    - Analytics metadata

Phase 1 (current): All profiles return IDENTICAL behaviour (current SCALP defaults).
Phase 2+: Each profile returns horizon-appropriate behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# ═══════════════════════════════════════════════════════════════════════════════
# POLICY PROTOCOLS (Future extension points — no implementation yet)
# ═══════════════════════════════════════════════════════════════════════════════

class BreakEvenPolicy(Protocol):
    """Interface for break-even behaviour (Phase 2+)."""
    def should_move_to_break_even(
        self, *, entry_price: float, current_price: float, stop_loss: float, side: str,
    ) -> bool: ...

    def break_even_target(self, *, entry_price: float, side: str) -> float: ...


class TrailingStopPolicy(Protocol):
    """Interface for trailing stop behaviour (Phase 2+)."""
    def compute_trailing_sl(
        self, *, entry_price: float, current_price: float, current_sl: float,
        max_favourable: float, side: str,
    ) -> float | None: ...


class PartialTakePolicy(Protocol):
    """Interface for partial take-profit behaviour (Phase 2+)."""
    def should_take_partial(
        self, *, entry_price: float, current_price: float, take_profit: float,
        volume: float, side: str,
    ) -> bool: ...

    def partial_volume(self, *, total_volume: float) -> float: ...


class TimeExitPolicy(Protocol):
    """Interface for time-based exit behaviour (Phase 2+)."""
    def should_exit_by_time(self, *, age_seconds: float) -> bool: ...


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON EXECUTION PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HorizonExecutionProfile:
    """
    Execution-focused profile for one trade horizon.

    Contains ALL parameters that downstream components may need to behave
    differently based on horizon. Phase 1 sets all horizons to identical
    SCALP-equivalent values (preserving current behaviour).

    Future phases attach policy objects for break-even, trailing, partial TP,
    and time exits without modifying this dataclass.

    Usage:
        profile = horizon_manager.get_profile("SCALP")
        max_time = profile.max_time_in_trade_seconds
        # Future:
        # if profile.time_exit_policy.should_exit_by_time(age_seconds=age):
        #     close_position()
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    name: str                           # "SCALP" | "INTRADAY" | "EXTENDED"

    # ─── TRADE MANAGEMENT PARAMETERS ─────────────────────────────────
    # Phase 1: These mirror the current global TradeManagementConfig values.
    # Phase 3: Each horizon gets distinct values.
    break_even_trigger_rr: float = 0.0
    break_even_buffer_rr: float = 0.0
    trailing_step: float = 0.0
    trailing_start_rr: float = 0.0
    partial_tp_fraction: float = 0.0
    partial_tp_path_fraction: float = 0.0
    max_time_in_trade_seconds: float = 0.0

    # ─── PORTFOLIO ALLOCATION (Phase 2) ───────────────────────────────
    max_concurrent_positions: int = 7   # Max positions at this horizon (across all symbols)
    allocation_weight: float = 1.0      # Relative weight for portfolio allocation

    # ─── ANALYTICS METADATA ───────────────────────────────────────────
    expected_hold_minutes_min: int = 0
    expected_hold_minutes_max: int = 0
    typical_rr: float = 2.0

    # ─── EXTENSIBILITY ────────────────────────────────────────────────
    # Future policy objects are passed via the metadata dict.
    # This avoids needing to modify the frozen dataclass for new policies.
    _policies: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def get_policy(self, policy_name: str) -> Any | None:
        """
        Retrieve a named policy object.

        Future phases register policies like:
            profile._policies["break_even"] = ScalpBreakEvenPolicy()

        Downstream components resolve behaviour via:
            policy = profile.get_policy("break_even")
            if policy and policy.should_move_to_break_even(...):
                ...

        Returns None if policy not registered (callers use default behaviour).
        """
        return self._policies.get(policy_name)

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation for observability/logging."""
        return {
            "name": self.name,
            "break_even_trigger_rr": self.break_even_trigger_rr,
            "break_even_buffer_rr": self.break_even_buffer_rr,
            "trailing_step": self.trailing_step,
            "trailing_start_rr": self.trailing_start_rr,
            "partial_tp_fraction": self.partial_tp_fraction,
            "partial_tp_path_fraction": self.partial_tp_path_fraction,
            "max_time_in_trade_seconds": self.max_time_in_trade_seconds,
            "max_concurrent_positions": self.max_concurrent_positions,
            "allocation_weight": self.allocation_weight,
            "expected_hold_minutes_min": self.expected_hold_minutes_min,
            "expected_hold_minutes_max": self.expected_hold_minutes_max,
            "typical_rr": self.typical_rr,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT PROFILES (Phase 1: ALL identical to current SCALP behaviour)
# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Values are read from config at startup via HorizonManager.
# These defaults exist only as fallbacks if config is unreachable.

DEFAULT_SCALP = HorizonExecutionProfile(
    name="SCALP",
    expected_hold_minutes_min=2,
    expected_hold_minutes_max=45,
    typical_rr=2.0,
)

DEFAULT_INTRADAY = HorizonExecutionProfile(
    name="INTRADAY",
    expected_hold_minutes_min=60,
    expected_hold_minutes_max=480,
    typical_rr=3.0,
)

DEFAULT_EXTENDED = HorizonExecutionProfile(
    name="EXTENDED",
    expected_hold_minutes_min=480,
    expected_hold_minutes_max=4320,
    typical_rr=4.0,
)

DEFAULT_PROFILES: dict[str, HorizonExecutionProfile] = {
    "SCALP": DEFAULT_SCALP,
    "INTRADAY": DEFAULT_INTRADAY,
    "EXTENDED": DEFAULT_EXTENDED,
}
