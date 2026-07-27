"""
E2: Process Watchdog — Monitors bot heartbeat and restarts on failure.

Run as a separate process:
    python -m core.watchdog

Monitors:
- heartbeat.json age
- Triggers restart if stale
- Crash-loop protection (max restarts per hour)
- Respects graceful shutdown (status=SHUTDOWN → no restart)

Does NOT:
- Implement Windows Service complexity
- Manage position recovery (main.py handles that via D3)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time as _time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# ─── LOGGING SETUP ────────────────────────────────────────────────────────────

_LOG_DIR = "runtime"
_LOG_FILE = os.path.join(_LOG_DIR, "watchdog.log")


def _setup_logging() -> logging.Logger:
    """Configure watchdog-specific logging."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(ch)

    return logger


logger = _setup_logging()


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

@dataclass
class WatchdogConfig:
    """Watchdog configuration — loaded from config or defaults."""
    heartbeat_file: str = "runtime/heartbeat.json"
    poll_interval_seconds: float = 15.0
    stale_threshold_seconds: float = 120.0
    max_restarts_per_hour: int = 5
    bot_start_command: list[str] = field(default_factory=lambda: ["python", "main.py"])
    bot_working_dir: str = ""  # Empty = current directory


def load_watchdog_config() -> WatchdogConfig:
    """Load watchdog config from core.config if available, else use defaults."""
    cfg = WatchdogConfig()
    try:
        from core import config
        cfg.heartbeat_file = str(getattr(config, "HEARTBEAT_FILE", cfg.heartbeat_file))
        cfg.poll_interval_seconds = float(getattr(config, "WATCHDOG_POLL_INTERVAL_SECONDS", cfg.poll_interval_seconds))
        cfg.stale_threshold_seconds = float(getattr(config, "HEARTBEAT_STALE_THRESHOLD_SECONDS", cfg.stale_threshold_seconds))
        cfg.max_restarts_per_hour = int(getattr(config, "MAX_RESTARTS_PER_HOUR", cfg.max_restarts_per_hour))
        cmd = getattr(config, "BOT_START_COMMAND", None)
        if cmd and isinstance(cmd, (list, tuple)):
            cfg.bot_start_command = list(cmd)
    except ImportError:
        pass
    return cfg


# ─── RESTART EVENT ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RestartEvent:
    """Structured restart event for audit logging."""
    timestamp: float
    timestamp_iso: str
    event: str
    reason: str
    heartbeat_age: float
    restart_count_last_hour: int
    pid_terminated: int | None = None
    pid_started: int | None = None


def _log_restart_event(event: RestartEvent) -> None:
    """Write structured restart event to watchdog log."""
    try:
        log_path = Path(_LOG_DIR) / "watchdog_events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": event.timestamp_iso,
            "event": event.event,
            "reason": event.reason,
            "heartbeat_age": round(event.heartbeat_age, 1),
            "restart_count_last_hour": event.restart_count_last_hour,
            "pid_terminated": event.pid_terminated,
            "pid_started": event.pid_started,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as exc:
        logger.warning("[WATCHDOG] Failed to write event log: %s", exc)


# ─── WATCHDOG CORE ────────────────────────────────────────────────────────────

class ProcessWatchdog:
    """
    Monitors bot heartbeat and restarts on detected failure.

    Crash-loop protection: limits restarts to MAX_RESTARTS_PER_HOUR.
    Respects graceful shutdown: status=SHUTDOWN → no restart.
    """

    def __init__(self, config: WatchdogConfig | None = None) -> None:
        self._cfg = config or load_watchdog_config()
        self._restart_timestamps: deque[float] = deque()
        self._bot_process: subprocess.Popen | None = None
        self._lockout: bool = False
        self._running: bool = False

    @property
    def is_locked_out(self) -> bool:
        return self._lockout

    @property
    def restart_count_last_hour(self) -> int:
        """Count restarts in the last 60 minutes."""
        cutoff = _time.time() - 3600.0
        while self._restart_timestamps and self._restart_timestamps[0] < cutoff:
            self._restart_timestamps.popleft()
        return len(self._restart_timestamps)

    def check_health(self) -> str:
        """
        Check bot health from heartbeat file.

        Returns:
            "HEALTHY" — heartbeat fresh, bot running
            "STALE" — heartbeat too old → needs restart
            "MISSING" — no heartbeat file → needs restart
            "SHUTDOWN" — graceful shutdown → do not restart
            "LOCKOUT" — restart limit exceeded
        """
        if self._lockout:
            return "LOCKOUT"

        hb_path = Path(self._cfg.heartbeat_file)

        # Read heartbeat
        try:
            if not hb_path.exists():
                return "MISSING"
            with open(hb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return "MISSING"

        if not isinstance(data, dict):
            return "MISSING"

        # Check for graceful shutdown
        status = data.get("status", "")
        if status == "SHUTDOWN":
            return "SHUTDOWN"

        # Check heartbeat age
        ts = data.get("timestamp")
        if ts is None or not isinstance(ts, (int, float)):
            return "MISSING"

        age = _time.time() - float(ts)
        if age > self._cfg.stale_threshold_seconds:
            return "STALE"

        return "HEALTHY"

    def should_restart(self) -> tuple[bool, str]:
        """
        Determine if a restart should be triggered.

        Returns:
            (should_restart: bool, reason: str)
        """
        health = self.check_health()

        if health == "HEALTHY":
            return False, "HEALTHY"
        if health == "SHUTDOWN":
            return False, "GRACEFUL_SHUTDOWN"
        if health == "LOCKOUT":
            return False, "LOCKOUT_ACTIVE"

        # STALE or MISSING → restart needed
        # Check crash-loop protection
        if self.restart_count_last_hour >= self._cfg.max_restarts_per_hour:
            self._lockout = True
            logger.critical(
                "[WATCHDOG] Restart limit exceeded (%d/%d in last hour). Entering lockout.",
                self.restart_count_last_hour, self._cfg.max_restarts_per_hour,
            )
            _log_restart_event(RestartEvent(
                timestamp=_time.time(),
                timestamp_iso=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                event="LOCKOUT_ENTERED",
                reason=f"max_restarts_exceeded ({self.restart_count_last_hour}/{self._cfg.max_restarts_per_hour})",
                heartbeat_age=self._get_heartbeat_age(),
                restart_count_last_hour=self.restart_count_last_hour,
            ))
            return False, "LOCKOUT_ENTERED"

        return True, health  # "STALE" or "MISSING"

    def restart_bot(self, reason: str = "STALE_HEARTBEAT") -> bool:
        """
        Terminate old process (if alive) and start new bot instance.

        Returns True if restart was successful, False otherwise.
        """
        hb_age = self._get_heartbeat_age()
        old_pid: int | None = None

        # Step 1: Terminate old process if still running
        if self._bot_process is not None:
            try:
                if self._bot_process.poll() is None:
                    old_pid = self._bot_process.pid
                    logger.info("[WATCHDOG] Terminating stale process pid=%d", old_pid)
                    self._bot_process.terminate()
                    try:
                        self._bot_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self._bot_process.kill()
                        self._bot_process.wait(timeout=5)
            except Exception as exc:
                logger.warning("[WATCHDOG] Error terminating process: %s", exc)

        # Step 2: Start new process
        try:
            cwd = self._cfg.bot_working_dir or None
            self._bot_process = subprocess.Popen(
                self._cfg.bot_start_command,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            new_pid = self._bot_process.pid
        except Exception as exc:
            logger.error("[WATCHDOG] Failed to start bot: %s", exc)
            return False

        # Step 3: Record restart
        self._restart_timestamps.append(_time.time())

        logger.info(
            "[WATCHDOG] Bot restarted. reason=%s heartbeat_age=%.1fs "
            "new_pid=%d restarts_last_hour=%d",
            reason, hb_age, new_pid, self.restart_count_last_hour,
        )

        _log_restart_event(RestartEvent(
            timestamp=_time.time(),
            timestamp_iso=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            event="PROCESS_RESTART",
            reason=reason,
            heartbeat_age=hb_age,
            restart_count_last_hour=self.restart_count_last_hour,
            pid_terminated=old_pid,
            pid_started=new_pid,
        ))

        return True

    def run(self) -> None:
        """
        Main watchdog loop. Runs indefinitely until interrupted.

        Polls heartbeat file at configured interval.
        Triggers restart when bot is detected as unhealthy.
        """
        self._running = True
        logger.info(
            "[WATCHDOG] Started. poll=%ds stale_threshold=%ds max_restarts=%d/hr",
            int(self._cfg.poll_interval_seconds),
            int(self._cfg.stale_threshold_seconds),
            self._cfg.max_restarts_per_hour,
        )

        try:
            while self._running:
                should, reason = self.should_restart()

                if should:
                    logger.warning(
                        "[WATCHDOG] Heartbeat stale (age=%.0fs) — triggering restart",
                        self._get_heartbeat_age(),
                    )
                    self.restart_bot(reason=reason)

                _time.sleep(self._cfg.poll_interval_seconds)

        except KeyboardInterrupt:
            logger.info("[WATCHDOG] Interrupted — shutting down")
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the watchdog to stop."""
        self._running = False

    def _get_heartbeat_age(self) -> float:
        """Get current heartbeat age in seconds. Returns 9999 if unavailable."""
        try:
            hb_path = Path(self._cfg.heartbeat_file)
            if not hb_path.exists():
                return 9999.0
            with open(hb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ts = data.get("timestamp", 0)
            return _time.time() - float(ts)
        except Exception:
            return 9999.0


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for standalone watchdog execution."""
    # Add project root to path
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    logger.info("[WATCHDOG] Process supervisor starting (pid=%d)", os.getpid())

    cfg = load_watchdog_config()
    watchdog = ProcessWatchdog(config=cfg)
    watchdog.run()


if __name__ == "__main__":
    main()
