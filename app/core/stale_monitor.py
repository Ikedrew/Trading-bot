"""Stale data detection monitor for live trading feeds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StaleCheckResult:
    is_stale: bool
    escalation_level: int  # 0=normal, 1=warning, 2=escalation, 3=critical
    stale_duration_seconds: float
    action: str  # "none", "log_warning", "log_escalation", "force_disconnect"


class StaleDataMonitor:
    def __init__(self, symbol: str, config_module: Any) -> None:
        self.symbol = symbol
        self.last_tick_time: int | None = None
        self.last_candle_time: int | None = None
        self.last_data_update_time: float | None = None
        self.stale_state: bool = False
        self._tick_stale_since: float | None = None
        self._candle_stale_since: float | None = None
        self._escalation_level: int = 0

        self.stale_tick_timeout = float(getattr(config_module, "STALE_TICK_TIMEOUT_SECONDS", 30.0))
        self.stale_candle_timeout = float(getattr(config_module, "STALE_CANDLE_TIMEOUT_SECONDS", 600.0))
        self.heartbeat_timeout = float(getattr(config_module, "MARKET_HEARTBEAT_TIMEOUT_SECONDS", 120.0))
        self.escalation_warning = float(getattr(config_module, "STALE_ESCALATION_WARNING_SECONDS", 60.0))
        self.escalation_critical = float(getattr(config_module, "STALE_ESCALATION_CRITICAL_SECONDS", 300.0))

    def on_tick(self, tick_time: int, wall_clock: float) -> StaleCheckResult:
        if self.last_tick_time is None:
            self.last_tick_time = tick_time
            self.last_data_update_time = wall_clock
            return StaleCheckResult(False, 0, 0.0, "none")

        if tick_time > self.last_tick_time:
            # Fresh tick
            self.last_tick_time = tick_time
            self.last_data_update_time = wall_clock
            self._tick_stale_since = None
            self._reset_if_all_fresh()
            return StaleCheckResult(False, 0, 0.0, "none")

        # Tick timestamp unchanged — start or continue grace period
        if self._tick_stale_since is None:
            self._tick_stale_since = wall_clock

        stale_duration = wall_clock - self._tick_stale_since

        # Grace period: only declare stale after threshold exceeded
        if stale_duration < self.stale_tick_timeout:
            return StaleCheckResult(False, 0, stale_duration, "none")

        # Beyond grace period — genuinely stale
        level = self._compute_escalation(stale_duration - self.stale_tick_timeout)
        self._escalation_level = max(self._escalation_level, level)
        self.stale_state = True

        action = "none"
        if level >= 3:
            action = "force_disconnect"
        elif level >= 2:
            action = "log_escalation"
        elif level >= 1:
            action = "log_warning"

        return StaleCheckResult(True, level, stale_duration, action)

    def on_candle(self, candle_time: int, wall_clock: float) -> StaleCheckResult:
        if self.last_candle_time is None:
            self.last_candle_time = candle_time
            self.last_data_update_time = wall_clock
            return StaleCheckResult(False, 0, 0.0, "none")

        if candle_time > self.last_candle_time:
            # Fresh candle
            self.last_candle_time = candle_time
            self.last_data_update_time = wall_clock
            self._candle_stale_since = None
            self._reset_if_all_fresh()
            return StaleCheckResult(False, 0, 0.0, "none")

        # Candle not progressed — check if beyond timeout
        if self._candle_stale_since is None:
            self._candle_stale_since = wall_clock

        stale_duration = wall_clock - self._candle_stale_since
        if stale_duration <= self.stale_candle_timeout:
            return StaleCheckResult(False, 0, stale_duration, "none")  # Normal inter-bar wait

        level = self._compute_escalation(stale_duration - self.stale_candle_timeout)
        self._escalation_level = max(self._escalation_level, level)
        self.stale_state = True

        action = "none"
        if level >= 3:
            action = "force_disconnect"
        elif level >= 2:
            action = "log_escalation"
        elif level >= 1:
            action = "log_warning"

        return StaleCheckResult(True, level, stale_duration, action)

    def check_heartbeat(self, wall_clock: float) -> StaleCheckResult:
        if self.last_data_update_time is None:
            return StaleCheckResult(False, 0, 0.0, "none")

        elapsed = wall_clock - self.last_data_update_time
        if elapsed <= self.heartbeat_timeout:
            return StaleCheckResult(False, 0, elapsed, "none")

        self.stale_state = True
        self._escalation_level = 3
        return StaleCheckResult(True, 3, elapsed, "force_disconnect")

    def _compute_escalation(self, stale_duration: float) -> int:
        if stale_duration >= self.escalation_critical:
            return 3
        elif stale_duration >= self.escalation_warning:
            return 2
        elif stale_duration > 0:
            return 1
        return 0

    def _reset_if_all_fresh(self) -> None:
        if self._tick_stale_since is None and self._candle_stale_since is None:
            self.stale_state = False
            self._escalation_level = 0
