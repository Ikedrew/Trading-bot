"""
Horizon Execution Authority — Portfolio allocation guard for multi-horizon execution.

RESPONSIBILITY:
    Decides whether an eligible horizon is permitted to become a live execution.
    Evaluates portfolio capacity, symbol capacity, and horizon slot uniqueness.

POSITION IN EXECUTION FLOW:
    classify_horizons() → [THIS] → Guard Chain → Execution

THIS MODULE OWNS:
    - Portfolio capacity check (MAX_TOTAL_POSITIONS)
    - Per-symbol capacity check (MAX_POSITIONS_PER_SYMBOL)
    - Horizon slot uniqueness: (symbol, horizon) → at most 1 position
    - Permitted horizons gating
    - Structured decision logging

THIS MODULE DOES NOT OWN:
    - Risk guard logic (daily limit, cooldown, correlation, regime)
    - Broker execution
    - Position lifecycle
    - Trade management
    - Horizon classification

DESIGN RULES:
    - No hardcoded horizon names (no `if horizon == "SCALP":`)
    - All limits read from config
    - Future horizons (SWING, POSITION, NEWS) work without code changes
    - Authority returns a structured decision — never modifies state
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.trade_management.position import Position

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HorizonPermission:
    """
    Structured decision from the execution authority.

    Immutable after creation. Used for logging and flow control.
    """
    allowed: bool
    reason: str
    symbol: str
    requested_horizon: str
    existing_horizons: list[str] = field(default_factory=list)
    symbol_position_count: int = 0
    portfolio_position_count: int = 0
    slot_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation for logging/persistence."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "symbol": self.symbol,
            "requested_horizon": self.requested_horizon,
            "existing_horizons": self.existing_horizons,
            "symbol_position_count": self.symbol_position_count,
            "portfolio_position_count": self.portfolio_position_count,
            "slot_available": self.slot_available,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════════

class HorizonExecutionAuthority:
    """
    Portfolio allocation guard for horizon-aware execution.

    Evaluates whether a (symbol, horizon) combination is permitted to open
    given the current portfolio state.

    Gate order:
        1. Is horizon in PERMITTED_HORIZONS?
        2. Is (symbol, horizon) slot available?
        3. Is symbol below MAX_POSITIONS_PER_SYMBOL?
        4. Is portfolio below MAX_TOTAL_POSITIONS?

    All gates must pass for execution to proceed.
    """

    def __init__(self) -> None:
        self._max_total: int = 21
        self._max_per_symbol: int = 3
        self._permitted: list[str] = ["SCALP"]
        self._enabled: bool = False
        self._load_config()

    def _load_config(self) -> None:
        """Read limits from config. Safe fallback if config unavailable."""
        try:
            from core import config
            self._max_total = int(
                getattr(config, "HORIZON_MAX_TOTAL_POSITIONS", 21)
            )
            self._max_per_symbol = int(
                getattr(config, "HORIZON_MAX_POSITIONS_PER_SYMBOL", 3)
            )
            self._permitted = list(
                getattr(config, "PERMITTED_HORIZONS", ["SCALP"])
            )
            self._enabled = bool(
                getattr(config, "HORIZON_AUTHORITY_ENABLED", False)
            )
        except Exception:
            pass  # Use constructor defaults

    def can_open(
        self,
        *,
        symbol: str,
        horizon: str,
        current_positions: list["Position"],
    ) -> HorizonPermission:
        """
        Evaluate whether a (symbol, horizon) execution is permitted.

        Args:
            symbol: Instrument name (e.g., "EURUSD")
            horizon: Requested horizon (e.g., "SCALP", "INTRADAY", "EXTENDED")
            current_positions: All currently open positions across all symbols

        Returns:
            HorizonPermission with decision and metadata.
        """
        # Gather portfolio state
        _portfolio_count = len(current_positions)
        _symbol_positions = [
            p for p in current_positions if p.symbol == symbol
        ]
        _symbol_count = len(_symbol_positions)
        _existing_horizons = [
            getattr(p, "trade_horizon", "SCALP") for p in _symbol_positions
        ]
        _slot_available = horizon not in _existing_horizons

        # ─── GATE 1: Horizon permitted? ───────────────────────────────
        if horizon not in self._permitted:
            return self._build_result(
                allowed=False,
                reason="horizon_not_permitted",
                symbol=symbol,
                horizon=horizon,
                existing_horizons=_existing_horizons,
                symbol_count=_symbol_count,
                portfolio_count=_portfolio_count,
                slot_available=_slot_available,
            )

        # ─── GATE 2: Slot uniqueness ─────────────────────────────────
        if not _slot_available:
            return self._build_result(
                allowed=False,
                reason="slot_occupied",
                symbol=symbol,
                horizon=horizon,
                existing_horizons=_existing_horizons,
                symbol_count=_symbol_count,
                portfolio_count=_portfolio_count,
                slot_available=False,
            )

        # ─── GATE 3: Symbol capacity ─────────────────────────────────
        if _symbol_count >= self._max_per_symbol:
            return self._build_result(
                allowed=False,
                reason="symbol_limit_reached",
                symbol=symbol,
                horizon=horizon,
                existing_horizons=_existing_horizons,
                symbol_count=_symbol_count,
                portfolio_count=_portfolio_count,
                slot_available=_slot_available,
            )

        # ─── GATE 4: Portfolio capacity ───────────────────────────────
        if _portfolio_count >= self._max_total:
            return self._build_result(
                allowed=False,
                reason="portfolio_full",
                symbol=symbol,
                horizon=horizon,
                existing_horizons=_existing_horizons,
                symbol_count=_symbol_count,
                portfolio_count=_portfolio_count,
                slot_available=_slot_available,
            )

        # ─── ALL GATES PASSED ─────────────────────────────────────────
        return self._build_result(
            allowed=True,
            reason="all_checks_passed",
            symbol=symbol,
            horizon=horizon,
            existing_horizons=_existing_horizons,
            symbol_count=_symbol_count,
            portfolio_count=_portfolio_count,
            slot_available=True,
        )

    @property
    def enabled(self) -> bool:
        """Whether the authority is actively enforcing (vs log-only)."""
        return self._enabled

    @property
    def max_total_positions(self) -> int:
        return self._max_total

    @property
    def max_positions_per_symbol(self) -> int:
        return self._max_per_symbol

    @property
    def permitted_horizons(self) -> list[str]:
        return list(self._permitted)

    def _build_result(
        self,
        *,
        allowed: bool,
        reason: str,
        symbol: str,
        horizon: str,
        existing_horizons: list[str],
        symbol_count: int,
        portfolio_count: int,
        slot_available: bool,
    ) -> HorizonPermission:
        """Build result and emit structured log."""
        result = HorizonPermission(
            allowed=allowed,
            reason=reason,
            symbol=symbol,
            requested_horizon=horizon,
            existing_horizons=existing_horizons,
            symbol_position_count=symbol_count,
            portfolio_position_count=portfolio_count,
            slot_available=slot_available,
        )
        self._log_decision(result)
        return result

    def _log_decision(self, decision: HorizonPermission) -> None:
        """Emit structured log for observability."""
        _level = logging.INFO if decision.allowed else logging.WARNING
        logger.log(
            _level,
            "[HORIZON_AUTHORITY] symbol=%s requested_horizon=%s "
            "allowed=%s reason=%s existing_horizons=%s "
            "portfolio_usage=%d/%d symbol_usage=%d/%d",
            decision.symbol,
            decision.requested_horizon,
            decision.allowed,
            decision.reason,
            decision.existing_horizons,
            decision.portfolio_position_count,
            self._max_total,
            decision.symbol_position_count,
            self._max_per_symbol,
        )
