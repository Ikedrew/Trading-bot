"""Managed process-level runtime for canonical delivery and recovery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any

from core.canonical_delivery import get_delivery_outbox, recover_local_handoffs
from core.canonical_delivery_worker import CanonicalDeliveryWorker, DeliveryState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryServiceStatus:
    running: bool
    started_at: str | None
    last_run_at: str | None
    last_error: str | None
    last_batch_size: int
    startup_recovered_handoffs: int
    historical_triage: Any
    last_pass_seconds: float | None
    worker_status: Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanonicalDeliveryService:
    """One explicitly managed delivery loop, owned by the application lifecycle."""

    def __init__(
        self,
        *,
        worker: CanonicalDeliveryWorker | None = None,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 10,
        startup_recovery_limit: int = 1000,
        startup_triage_limit: int = 100000,
        shutdown_timeout_seconds: float = 65.0,
    ) -> None:
        if poll_interval_seconds <= 0 or batch_size <= 0 \
                or startup_recovery_limit <= 0 or startup_triage_limit < 0 \
                or shutdown_timeout_seconds <= 0:
            raise ValueError("INVALID_CANONICAL_DELIVERY_SERVICE_POLICY")
        self.outbox = worker.outbox if worker is not None else get_delivery_outbox()
        self.worker = worker or CanonicalDeliveryWorker(self.outbox)
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.startup_recovery_limit = startup_recovery_limit
        self.startup_triage_limit = startup_triage_limit
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started_at: str | None = None
        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._last_batch_size = 0
        self._startup_recovered_handoffs = 0
        self._historical_triage: Any = None
        self._last_pass_seconds: float | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop.is_set())

    def start(self) -> bool:
        """Recover local handoffs first, then start exactly one managed worker loop."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._started_at = _utc_now()
            # The reconciliation horizon is captured before any startup work so
            # every row this process creates (including recovered local
            # handoffs) is live; only rows that already existed are historical.
            horizon = self._started_at
            try:
                recovery = recover_local_handoffs(
                    outbox=self.outbox, max_items=self.startup_recovery_limit,
                )
                self._startup_recovered_handoffs += recovery.recovered
                if recovery.failed:
                    self._last_error = (
                        f"STARTUP_LOCAL_HANDOFF_RECOVERY_FAILED:{recovery.failed}")
            except Exception as exc:
                self._last_error = f"STARTUP_RECOVERY:{type(exc).__name__}:{exc}"
                logger.exception("[CANONICAL_DELIVERY_STARTUP_RECOVERY_FAILED]")

            # Bounded, one-shot triage of the pre-existing backlog. Runs once,
            # off the per-pass path, so historical terminal rows are never
            # reprocessed as active reconciliation work and never emit per-row
            # anomalies. Only one aggregate line is logged.
            try:
                self._historical_triage = self.worker.triage_historical_backlog(
                    horizon=horizon, max_items=self.startup_triage_limit,
                )
                _triage = self._historical_triage
                logger.info(
                    "[CANONICAL_HISTORICAL_RECONCILIATION_TRIAGE] scanned=%d "
                    "skipped_terminal_historical=%d deferred_to_active_pass=%d",
                    _triage.scanned, _triage.skipped_terminal_historical,
                    _triage.deferred_to_active_pass,
                )
            except Exception as exc:
                self._last_error = (
                    f"STARTUP_HISTORICAL_TRIAGE:{type(exc).__name__}:{exc}")
                logger.exception("[CANONICAL_HISTORICAL_TRIAGE_FAILED]")

            self._thread = threading.Thread(
                target=self._run_loop,
                name="canonical-delivery-worker",
                daemon=False,
            )
            self._thread.start()
            logger.info(
                "[CANONICAL_DELIVERY_SERVICE_STARTED] batch=%d poll_seconds=%.2f recovered_handoffs=%d",
                self.batch_size, self.poll_interval_seconds,
                self._startup_recovered_handoffs,
            )
            return True

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._last_run_at = _utc_now()
            _pass_started = time.perf_counter()
            completed = 0
            for _ in range(self.batch_size):
                if self._stop.is_set():
                    break
                try:
                    result = self.worker.run_once()
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}:{exc}"
                    logger.exception("[CANONICAL_DELIVERY_WORKER_ERROR]")
                    break
                if result is None:
                    break
                completed += 1
                if result.error_class:
                    self._last_error = (
                        f"{result.error_class}:{result.reconciliation_anomaly or ''}")
                elif result.reconciliation_anomaly:
                    self._last_error = result.reconciliation_anomaly
                elif result.state_after is DeliveryState.ACKNOWLEDGED:
                    self._last_error = None
            try:
                reconciliation = self.worker.reconcile_acknowledged(
                    max_items=self.batch_size,
                )
                for result in reconciliation:
                    if result.reconciliation_anomaly:
                        self._last_error = result.reconciliation_anomaly
                    elif result.lifecycle_reconciled:
                        self._last_error = None
            except Exception as exc:
                self._last_error = f"RECONCILIATION:{type(exc).__name__}:{exc}"
                logger.exception("[CANONICAL_DELIVERY_RECONCILIATION_ERROR]")
            self._last_batch_size = completed
            self._last_pass_seconds = time.perf_counter() - _pass_started
            self._stop.wait(self.poll_interval_seconds)

    def stop(self) -> bool:
        """Signal and join the managed loop; return false only on shutdown timeout."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return True
            self._stop.set()
        thread.join(self.shutdown_timeout_seconds)
        stopped = not thread.is_alive()
        if stopped:
            logger.info("[CANONICAL_DELIVERY_SERVICE_STOPPED]")
        else:
            self._last_error = "WORKER_SHUTDOWN_TIMEOUT"
            logger.error("[CANONICAL_DELIVERY_SERVICE_SHUTDOWN_TIMEOUT]")
        return stopped

    def status(self) -> DeliveryServiceStatus:
        try:
            worker_status = self.worker.status()
        except Exception as exc:
            worker_status = None
            self._last_error = f"STATUS_SNAPSHOT:{type(exc).__name__}:{exc}"
        return DeliveryServiceStatus(
            running=self.running,
            started_at=self._started_at,
            last_run_at=self._last_run_at,
            last_error=self._last_error,
            last_batch_size=self._last_batch_size,
            startup_recovered_handoffs=self._startup_recovered_handoffs,
            historical_triage=self._historical_triage,
            last_pass_seconds=self._last_pass_seconds,
            worker_status=worker_status,
        )


_SERVICE_LOCK = threading.Lock()
_SERVICE: CanonicalDeliveryService | None = None


def start_canonical_delivery_service() -> CanonicalDeliveryService:
    """Start or return the one process-level canonical delivery service."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None and _SERVICE.running:
            return _SERVICE
        _SERVICE = CanonicalDeliveryService()
        service = _SERVICE
    service.start()
    return service


def stop_canonical_delivery_service() -> bool:
    """Stop the process-level service during graceful application shutdown."""
    with _SERVICE_LOCK:
        service = _SERVICE
    return True if service is None else service.stop()


def canonical_delivery_service_status() -> DeliveryServiceStatus | None:
    with _SERVICE_LOCK:
        service = _SERVICE
    return None if service is None else service.status()


__all__ = [
    "CanonicalDeliveryService",
    "DeliveryServiceStatus",
    "canonical_delivery_service_status",
    "start_canonical_delivery_service",
    "stop_canonical_delivery_service",
]
