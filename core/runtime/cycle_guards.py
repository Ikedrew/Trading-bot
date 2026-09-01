"""
Cycle Guards — Cycle-level permission evaluation for the live scanner.

Determines whether a scan cycle is allowed to continue by composing
existing guards into a single evaluation call.

This module OWNS:
    - Cycle-level permission checks (drawdown, daily loss, kill switch)
    - Composition of existing guards in correct order
    - Determining allow/block result
    - Returning CyclePermission result

This module does NOT own:
    - Runtime loop control (continue/break)
    - Sleep or poll timing
    - Heartbeat writing
    - MT5 connection management
    - Trade execution or risk calculations
    - Strategy decisions or market scanning
    - Guard implementations (delegates to existing modules)
    - Discord/logging presentation (preserves existing logging)

Design: stateless evaluator — delegates to existing guard instances.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from risk.drawdown_guard import DrawdownGuard
from risk.daily_loss_guard import DailyLossGuard
from core.kill_switch import is_kill_switch_active
from core.runtime.risk_event_emitter import emit_risk_guard_result

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CyclePermission:
    """Result of cycle-level guard evaluation."""

    # Overall cycle permission
    cycle_allowed: bool
    """False = entire cycle must be skipped (e.g. drawdown block)."""

    # Individual guard states consumed downstream
    daily_loss_blocked: bool = False
    """True = new entries blocked but trade management continues."""

    kill_switch_active: bool = False
    """True = kill switch flag file present; entries blocked per-symbol."""

    # Block reason (only set when cycle_allowed=False)
    block_reason: str = ""

    # Drawdown result passthrough (consumed by heartbeat/observability)
    drawdown_result: Any = None

    # Daily loss result passthrough (consumed by per-symbol decision)
    daily_loss_result: Any = None


# ─── CYCLE GUARDS ─────────────────────────────────────────────────────────────

class CycleGuards:
    """
    Evaluates all cycle-level guards in correct order.

    Guard ordering (preserved from original):
        1. Drawdown guard — hard block (entire cycle skipped)
        2. Daily reset — housekeeping (resets daily trade limit)
        3. Daily loss limit — soft block (flag for per-symbol use)
        4. Kill switch snapshot — flag for per-symbol use

    Usage:
        guards = CycleGuards(config, drawdown_guard, daily_loss_guard,
                             daily_reset, daily_trade_limit)
        permission = guards.evaluate()
        if not permission.cycle_allowed:
            # handle hard block (heartbeat, sleep, continue)
    """

    def __init__(
        self,
        config: Any,
        drawdown_guard: DrawdownGuard,
        daily_loss_guard: DailyLossGuard,
        daily_reset: Any,
        daily_trade_limit: Any,
    ) -> None:
        self._config = config
        self._drawdown_guard = drawdown_guard
        self._daily_loss_guard = daily_loss_guard
        self._daily_reset = daily_reset
        self._daily_trade_limit = daily_trade_limit

    def evaluate(self) -> CyclePermission:
        """
        Evaluate all cycle-level guards in order.

        Returns CyclePermission indicating whether the cycle may proceed
        and downstream flags for per-symbol checks.

        Short-circuit: if drawdown guard blocks, remaining guards are NOT evaluated
        (preserves original behaviour).
        """
        # ─── 1. DRAWDOWN GUARD (hard block) ───────────────────────────
        _dd_result = self._drawdown_guard.check()
        if not _dd_result.allowed:
            emit_risk_guard_result("SYSTEM", "drawdown_guard", "REJECTED", "drawdown_limit_exceeded", {
                "current_drawdown_pct": getattr(_dd_result, "current_drawdown_pct", None),
                "max_drawdown_pct": getattr(_dd_result, "max_drawdown_pct", None),
                "layer": "DRAWDOWN",
                "scope": "cycle_block",
            })
            # Discord: drawdown block
            try:
                _dl = getattr(self._config, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("RISK_BLOCK", {
                        "guard": "drawdown",
                        "reason": "drawdown limit exceeded",
                        "details": {
                            "current_drawdown": getattr(_dd_result, "current_drawdown_pct", None),
                            "limit": getattr(_dd_result, "max_drawdown_pct", None),
                        },
                    })
            except Exception:
                pass

            return CyclePermission(
                cycle_allowed=False,
                block_reason="drawdown_limit_exceeded",
                drawdown_result=_dd_result,
            )
        # ─── END DRAWDOWN GUARD ───────────────────────────────────────

        # ─── 2. DAILY RESET CHECK (housekeeping) ─────────────────────
        _reset_triggered = self._daily_reset.evaluate()
        if _reset_triggered:
            self._daily_trade_limit.reset()
        # ─── END DAILY RESET ──────────────────────────────────────────

        # ─── 3. DAILY LOSS LIMIT (soft block — flag only) ────────────
        _dl_result = self._daily_loss_guard.check()
        _daily_loss_blocked = not _dl_result.allowed

        if _daily_loss_blocked:
            emit_risk_guard_result("SYSTEM", "daily_loss_guard", "REJECTED", "daily_loss_limit_reached", {
                "current_loss_pct": getattr(_dl_result, "current_loss_pct", None),
                "limit_pct": getattr(_dl_result, "limit_pct", None),
                "layer": "DAILY_LOSS",
                "scope": "cycle_block",
            })
            # Discord: daily loss block
            try:
                _dl = getattr(self._config, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("RISK_BLOCK", {
                        "guard": "daily_loss",
                        "reason": "daily loss limit reached",
                        "details": {
                            "current_loss": getattr(_dl_result, "current_loss_pct", None),
                            "limit": getattr(_dl_result, "limit_pct", None),
                        },
                    })
            except Exception:
                pass
        # ─── END DAILY LOSS LIMIT ─────────────────────────────────────

        # ─── 4. KILL SWITCH SNAPSHOT ──────────────────────────────────
        _kill_active = is_kill_switch_active()
        # ─── END KILL SWITCH SNAPSHOT ─────────────────────────────────

        return CyclePermission(
            cycle_allowed=True,
            daily_loss_blocked=_daily_loss_blocked,
            kill_switch_active=_kill_active,
            drawdown_result=_dd_result,
            daily_loss_result=_dl_result,
        )
