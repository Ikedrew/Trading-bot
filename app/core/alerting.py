"""
F1: External Alerting System — Structured event dispatch with channel routing.

Core design:
  - Alerts are structured JSON objects (not strings)
  - Channels are renderers of the same event
  - Non-blocking: alert failures never affect trading
  - Extensible: new channels added without modifying event producers

Channels:
  - Discord webhook (primary — full context)
  - Email (future — concise)
  - AWS API (future — raw JSON)
  - Local log (always — fallback)

Usage:
    from core.alerting import send_alert, AlertLevel
    send_alert(
        level=AlertLevel.CRITICAL,
        event_type="DRAWDOWN_BREACH",
        symbol="EURUSD",
        message="Drawdown limit exceeded",
        metrics={"drawdown": 3.8, "limit": 3.5},
    )
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─── ALERT LEVELS ─────────────────────────────────────────────────────────────

class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


# ─── STRUCTURED ALERT EVENT ───────────────────────────────────────────────────

@dataclass(frozen=True)
class AlertEvent:
    """Structured alert — the canonical unit dispatched to all channels."""
    level: AlertLevel
    event_type: str
    message: str
    timestamp: str  # ISO format UTC
    unix_time: float
    symbol: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (for AWS/API channel)."""
        return {
            "level": self.level.value,
            "event_type": self.event_type,
            "message": self.message,
            "timestamp": self.timestamp,
            "unix_time": self.unix_time,
            "symbol": self.symbol,
            "metrics": self.metrics,
            "state_snapshot": self.state_snapshot,
            "detail": self.detail,
        }

    def to_discord(self) -> str:
        """Rich formatted string for Discord webhook."""
        parts = [
            f"**[{self.level.value}] {self.event_type}**",
        ]
        if self.symbol:
            parts.append(f"Symbol: `{self.symbol}`")
        parts.append(f"Message: {self.message}")
        if self.metrics:
            metrics_str = " | ".join(f"{k}: {v}" for k, v in self.metrics.items())
            parts.append(f"Metrics: {metrics_str}")
        if self.state_snapshot:
            state_str = " | ".join(f"{k}: {v}" for k, v in self.state_snapshot.items())
            parts.append(f"State: {state_str}")
        parts.append(f"Time: {self.timestamp}")
        return "\n".join(parts)

    def to_email_subject(self) -> str:
        """Concise email subject line."""
        sym = f" {self.symbol}" if self.symbol else ""
        return f"[{self.level.value}]{sym} {self.event_type}"

    def to_email_body(self) -> str:
        """Short email body (5-10 lines max)."""
        lines = [
            f"{self.level.value}: {self.event_type}",
        ]
        if self.symbol:
            lines.append(f"Symbol: {self.symbol}")
        lines.append(self.message)
        if self.metrics:
            for k, v in self.metrics.items():
                lines.append(f"  {k}: {v}")
        lines.append(f"Time: {self.timestamp}")
        return "\n".join(lines)


# ─── CHANNEL INTERFACE ────────────────────────────────────────────────────────

class AlertChannel:
    """Base class for alert delivery channels."""
    def send(self, event: AlertEvent) -> bool:
        """Attempt to deliver alert. Returns True on success."""
        raise NotImplementedError


class DiscordWebhookChannel(AlertChannel):
    """Discord webhook delivery (primary channel)."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def send(self, event: AlertEvent) -> bool:
        if not self._url:
            return False
        try:
            import urllib.request
            payload = json.dumps({"content": event.to_discord()}).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 300
        except Exception as exc:
            logger.warning("[ALERT_DISCORD_FAILED] event=%s error=%s", event.event_type, exc)
            return False


class LogChannel(AlertChannel):
    """Local log fallback — always works, always enabled."""

    def send(self, event: AlertEvent) -> bool:
        level_map = {
            AlertLevel.INFO: logging.INFO,
            AlertLevel.WARNING: logging.WARNING,
            AlertLevel.CRITICAL: logging.CRITICAL,
            AlertLevel.EMERGENCY: logging.CRITICAL,
        }
        log_level = level_map.get(event.level, logging.INFO)
        logger.log(log_level, "[ALERT] %s | %s | %s", event.level.value, event.event_type, event.message)
        return True


# ─── THROTTLING ───────────────────────────────────────────────────────────────

_throttle_state: dict[str, float] = {}
_THROTTLE_SECONDS = {
    AlertLevel.INFO: 60.0,
    AlertLevel.WARNING: 30.0,
    AlertLevel.CRITICAL: 10.0,
    AlertLevel.EMERGENCY: 0.0,  # Never throttled
}


def _is_throttled(event: AlertEvent) -> bool:
    """Check if this event type should be throttled (too recent)."""
    if event.level == AlertLevel.EMERGENCY:
        return False  # EMERGENCY never throttled

    key = f"{event.level.value}:{event.event_type}:{event.symbol}"
    cooldown = _THROTTLE_SECONDS.get(event.level, 30.0)
    last_sent = _throttle_state.get(key, 0.0)

    if time.time() - last_sent < cooldown:
        return True

    _throttle_state[key] = time.time()
    return False


# ─── ALERT ROUTER ─────────────────────────────────────────────────────────────

_channels: list[AlertChannel] = []
_initialized: bool = False


def initialize_alerting() -> None:
    """
    Initialize alert channels from config. Call once at startup.
    Safe to call multiple times (idempotent).
    """
    global _channels, _initialized
    if _initialized:
        return

    _channels = [LogChannel()]  # Always have local log

    try:
        from core import config
        discord_url = getattr(config, "DISCORD_WEBHOOK_URL", "")
        if discord_url:
            _channels.append(DiscordWebhookChannel(discord_url))
            logger.info("[ALERTING] Discord channel configured")
    except ImportError:
        pass

    _initialized = True
    logger.info("[ALERTING] initialized channels=%d", len(_channels))


def send_alert(
    *,
    level: AlertLevel,
    event_type: str,
    message: str,
    symbol: str = "",
    metrics: dict[str, Any] | None = None,
    state_snapshot: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """
    Dispatch a structured alert to all configured channels.

    Non-blocking: dispatches in a daemon thread.
    Never raises. Never affects trading execution.

    Args:
        level: AlertLevel (INFO/WARNING/CRITICAL/EMERGENCY)
        event_type: Machine-readable event name (e.g., "DRAWDOWN_BREACH")
        message: Human-readable description
        symbol: Trading symbol (optional)
        metrics: Numeric data relevant to the alert
        state_snapshot: Current system state for context
        detail: Additional metadata
    """
    if not _initialized:
        initialize_alerting()

    now = datetime.now(tz=timezone.utc)
    event = AlertEvent(
        level=level,
        event_type=event_type,
        message=message,
        timestamp=now.isoformat(),
        unix_time=time.time(),
        symbol=symbol,
        metrics=metrics or {},
        state_snapshot=state_snapshot or {},
        detail=detail or {},
    )

    # Throttle check (except EMERGENCY)
    if _is_throttled(event):
        return

    # Non-blocking dispatch
    def _dispatch():
        for channel in _channels:
            try:
                channel.send(event)
            except Exception:
                pass  # Channel failure must never propagate

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()
