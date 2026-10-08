"""Production deep-work cadence service for the continuous research loop.

Provides a single deterministic service tick that:

1. Attempts one fast research cycle (run_continuous_research_cycle).
2. If deep jobs are pending, attempts one deep job (run_deep_research_job).

The caller decides the inter-tick interval (FAST_CYCLE_MIN_INTERVAL).
This module never enters a loop — callers own the scheduling policy.

Governed guarantees:
- Ordinary continuous cycle and deep worker never overlap (shared lease).
- Failed deep jobs respect cooldown and max_attempts before stopping.
- Cadence settings are validated at construction and surfaced in reports.
- No evidence is lost because the cadence controls when cycles run, not
  whether new S3 objects are visible (the frontier coordinator decides that).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research_engine.v10.continuous.research_work_queue import (
    DEFAULT_DEEP_WORK_COOLDOWN_SECONDS,
    DEFAULT_MAX_PENDING_DEEP_JOBS,
    ResearchExecutionPolicy,
    ResearchWorkQueueStore,
)


CADENCE_SCHEMA = "research_cadence_config_v1"
TICK_REPORT_SCHEMA = "research_cadence_tick_report_v1"

# Default cadence: at least 60 s between fast cycles so the system does not
# hammer the S3 listing API when there is nothing new to discover.
DEFAULT_FAST_CYCLE_MIN_INTERVAL_SECONDS = 60.0

# Deep concurrency cap — one job at a time is the explicit default.
DEFAULT_DEEP_WORK_MAX_CONCURRENCY = 1


class ResearchCadenceError(RuntimeError):
    """A cadence configuration or tick is structurally invalid."""


@dataclass(frozen=True)
class ResearchCadenceConfig:
    """Immutable cadence configuration.

    All timing values are in seconds.  Validation happens at construction so
    a misconfigured service fails immediately rather than silently.
    """

    fast_cycle_min_interval_seconds: float = DEFAULT_FAST_CYCLE_MIN_INTERVAL_SECONDS
    deep_work_cooldown_seconds: float = DEFAULT_DEEP_WORK_COOLDOWN_SECONDS
    deep_work_max_concurrency: int = DEFAULT_DEEP_WORK_MAX_CONCURRENCY
    max_pending_deep_jobs: int = DEFAULT_MAX_PENDING_DEEP_JOBS

    def __post_init__(self) -> None:
        if self.fast_cycle_min_interval_seconds < 0:
            raise ResearchCadenceError("FAST_CYCLE_MIN_INTERVAL_NEGATIVE")
        if self.deep_work_cooldown_seconds < 0:
            raise ResearchCadenceError("DEEP_WORK_COOLDOWN_NEGATIVE")
        if self.deep_work_max_concurrency < 1:
            raise ResearchCadenceError("DEEP_WORK_MAX_CONCURRENCY_BELOW_1")
        if self.max_pending_deep_jobs < 1:
            raise ResearchCadenceError("MAX_PENDING_DEEP_JOBS_BELOW_1")

    def execution_policy(self) -> ResearchExecutionPolicy:
        return ResearchExecutionPolicy(
            deep_work_cooldown_seconds=self.deep_work_cooldown_seconds,
            max_pending_deep_jobs=self.max_pending_deep_jobs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cadence_schema": CADENCE_SCHEMA,
            "fast_cycle_min_interval_seconds": self.fast_cycle_min_interval_seconds,
            "deep_work_cooldown_seconds": self.deep_work_cooldown_seconds,
            "deep_work_max_concurrency": self.deep_work_max_concurrency,
            "max_pending_deep_jobs": self.max_pending_deep_jobs,
        }


@dataclass
class CadenceTickReport:
    """Result of one cadence tick: one optional fast cycle + one optional deep job."""

    tick_id: str
    tick_started_at: str
    tick_completed_at: str
    fast_cycle_attempted: bool
    fast_cycle_outcome: str | None
    fast_cycle_elapsed_seconds: float | None
    deep_job_attempted: bool
    deep_job_outcome: str | None
    deep_job_id: str | None
    deep_job_elapsed_seconds: float | None
    deep_pending_after: int
    deep_lag_epochs: int
    skipped_reason: str | None
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tick_schema": TICK_REPORT_SCHEMA, **asdict(self)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pending_deep_count(state_root: Path) -> int:
    path = state_root / "deep_work_queue.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sum(
            1 for job in (data.get("jobs") or [])
            if job.get("state") in {"PENDING", "RUNNING"}
        )
    except Exception:
        return 0


def run_cadence_tick(
    *,
    state_root: Path | str = Path("data/research/continuous"),
    config: ResearchCadenceConfig | None = None,
    last_fast_cycle_at: str | None = None,
    force_fast_cycle: bool = False,
    force_deep_job: bool = False,
    now: str | None = None,
) -> CadenceTickReport:
    """Execute one cadence tick.

    A tick is the unit of work invoked by a service loop.  It:
    - Runs a fast cycle if the min-interval has elapsed (or forced).
    - Runs one deep job if any are pending and cooldown has elapsed (or forced).

    The shared cycle lease in run_continuous_research_cycle /
    run_deep_research_job prevents these from overlapping even if the caller
    ignores the return value and calls both again immediately.

    This function never raises.  All errors are captured in the report so the
    caller can log and continue.
    """
    # Import here to avoid circular imports at module level.
    from research_engine.v10.continuous.research_loop import (
        run_continuous_research_cycle,
        run_deep_research_job,
    )

    resolved_config = config or ResearchCadenceConfig()
    root = Path(state_root)
    stamp = now or _utc_now()
    tick_id = "TICK-" + stamp.replace(":", "").replace("-", "").replace("+", "")[:20]

    fast_attempted = False
    fast_outcome: str | None = None
    fast_elapsed: float | None = None
    deep_attempted = False
    deep_outcome: str | None = None
    deep_job_id: str | None = None
    deep_elapsed: float | None = None
    skipped_reason: str | None = None

    # ── Fast cycle eligibility ──────────────────────────────────────────────
    should_fast = force_fast_cycle
    if not should_fast and last_fast_cycle_at is not None:
        try:
            last = datetime.fromisoformat(
                last_fast_cycle_at.replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            elapsed = (now_dt - last).total_seconds()
            should_fast = elapsed >= resolved_config.fast_cycle_min_interval_seconds
        except Exception:
            should_fast = True  # fail open on unparseable timestamp
    elif not should_fast and last_fast_cycle_at is None:
        should_fast = True  # first tick always runs

    if should_fast:
        fast_attempted = True
        t0 = time.perf_counter()
        try:
            # The production Q71+ evaluator registry is the single eligibility
            # authority for generated questions.  Absence is a legitimate state
            # (every generated question then stays MISSING_EVALUATOR), so a
            # failure to load it must never fabricate capability.
            from research_engine.v10.continuous.research_loop import (
                _production_evaluator_registry,
            )

            try:
                evaluator_registry = _production_evaluator_registry()
            except Exception:
                evaluator_registry = None
            result = run_continuous_research_cycle(
                state_root=root, q71_evaluator_registry=evaluator_registry)
            fast_outcome = str(getattr(result, "cycle_outcome", "UNKNOWN"))
        except Exception as exc:
            fast_outcome = f"EXCEPTION:{type(exc).__name__}:{exc}"
        fast_elapsed = time.perf_counter() - t0
    else:
        skipped_reason = "FAST_CYCLE_MIN_INTERVAL_NOT_ELAPSED"

    # ── Deep job eligibility ────────────────────────────────────────────────
    pending = _pending_deep_count(root)
    should_deep = (pending > 0 or force_deep_job) and (
        should_fast or force_deep_job
    )
    if should_deep:
        deep_attempted = True
        t0 = time.perf_counter()
        try:
            deep_result = run_deep_research_job(
                state_root=root,
                execution_policy=resolved_config.execution_policy(),
                now=_utc_now(),
            )
            deep_outcome = str(deep_result.get("status", "UNKNOWN"))
            deep_job_id = deep_result.get("job_id")
        except Exception as exc:
            deep_outcome = f"EXCEPTION:{type(exc).__name__}:{exc}"
        deep_elapsed = time.perf_counter() - t0

    # ── Lag metrics after tick ──────────────────────────────────────────────
    pending_after = _pending_deep_count(root)
    lag_epochs = 0
    try:
        queue = ResearchWorkQueueStore(root / "deep_work_queue.json")
        metrics = queue.metrics(generated_at=_utc_now())
        lag_epochs = int(metrics.get("lag_epochs") or 0)
        pending_after = int(metrics.get("pending_deep_jobs") or 0)
    except Exception:
        pass

    return CadenceTickReport(
        tick_id=tick_id,
        tick_started_at=stamp,
        tick_completed_at=_utc_now(),
        fast_cycle_attempted=fast_attempted,
        fast_cycle_outcome=fast_outcome,
        fast_cycle_elapsed_seconds=fast_elapsed,
        deep_job_attempted=deep_attempted,
        deep_job_outcome=deep_outcome,
        deep_job_id=deep_job_id,
        deep_job_elapsed_seconds=deep_elapsed,
        deep_pending_after=pending_after,
        deep_lag_epochs=lag_epochs,
        skipped_reason=skipped_reason,
        config=resolved_config.to_dict(),
    )


def run_cadence_service(
    *,
    state_root: Path | str = Path("data/research/continuous"),
    config: ResearchCadenceConfig | None = None,
    tick_interval_seconds: float = DEFAULT_FAST_CYCLE_MIN_INTERVAL_SECONDS,
    max_ticks: int | None = None,
    on_tick: Any = None,
) -> None:
    """Simple deterministic service loop.

    Runs cadence ticks in a loop with ``tick_interval_seconds`` sleep between
    them.  ``max_ticks`` bounds the loop for testing; in production leave it
    None.  ``on_tick`` is an optional callable that receives the
    CadenceTickReport — use it for logging/monitoring.

    This never raises — individual tick failures are captured in reports and
    reported via on_tick.  The loop itself only exits on KeyboardInterrupt or
    when max_ticks is reached.
    """
    resolved_config = config or ResearchCadenceConfig()
    last_fast_at: str | None = None
    tick_count = 0
    while True:
        report = run_cadence_tick(
            state_root=state_root,
            config=resolved_config,
            last_fast_cycle_at=last_fast_at,
        )
        if report.fast_cycle_attempted:
            last_fast_at = report.tick_started_at
        if on_tick is not None:
            try:
                on_tick(report)
            except Exception:
                pass
        tick_count += 1
        if max_ticks is not None and tick_count >= max_ticks:
            break
        try:
            time.sleep(tick_interval_seconds)
        except KeyboardInterrupt:
            break


__all__ = [
    "CADENCE_SCHEMA",
    "DEFAULT_DEEP_WORK_COOLDOWN_SECONDS",
    "DEFAULT_DEEP_WORK_MAX_CONCURRENCY",
    "DEFAULT_FAST_CYCLE_MIN_INTERVAL_SECONDS",
    "DEFAULT_MAX_PENDING_DEEP_JOBS",
    "CadenceTickReport",
    "ResearchCadenceConfig",
    "ResearchCadenceError",
    "run_cadence_service",
    "run_cadence_tick",
]
