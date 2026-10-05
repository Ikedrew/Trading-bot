"""
Startup Warm-Up — pay one-time lazy-init costs BEFORE the scanner loop.

Runtime performance incident (end-to-end):
    Several process-wide singletons used by the per-symbol hot path were created
    lazily on *first use*, i.e. inside the first scanner cycle.  Their one-time
    construction is expensive:

        CanonicalDeliveryOutbox()   ~32 s  (PRAGMA quick_check over a 149 MB DB)
        ShadowRuntime()             ~19 s  (recovery replays shadow event JSONL)
        LifecycleEvidenceLedger()    ~9 s  (parses the 64 MB obligation ledger)

    Because the first candle fetch triggers a canonical handoff, the first
    scanner cycle silently absorbed ~60 s of cold-start work and tripped the
    10 s liveness threshold.

    This module performs exactly the same construction the hot path would have
    done on first use, but explicitly at startup, before the loop begins.  It
    does not change what is constructed, when it is safe to construct, or any
    durability/validation guarantee — only *when* the cost is paid.

Contract:
    - Never raises: each dependency is isolated and timed.
    - Idempotent: singleton accessors return the existing instance on re-entry.
    - Observable: returns and logs per-dependency wall-clock seconds.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _warm_canonical_delivery_outbox() -> Any:
    from core.canonical_delivery import get_delivery_outbox
    return get_delivery_outbox()


def _warm_obligation_ledger() -> Any:
    from core.lifecycle_evidence_obligations import obligation_ledger
    return obligation_ledger()


def _warm_shadow_runtime() -> Any:
    from core import config
    if not getattr(config, "SHADOW_RUNTIME_V2_ENABLED", False):
        return None
    from core.shadow.runtime import get_shadow_runtime
    return get_shadow_runtime()


def _warm_candidate_runtime() -> Any:
    from core import config
    if not getattr(config, "SHADOW_RUNTIME_V2_ENABLED", False):
        return None
    from core.shadow.candidate_runtime import get_candidate_runtime
    runtime = get_candidate_runtime()
    ensure = getattr(runtime, "ensure_recovered", None)
    if callable(ensure):
        ensure()
    return runtime


def _warm_research_shadow_engine() -> Any:
    from core.research_assessment.research_shadow_engine import get_research_shadow_engine
    return get_research_shadow_engine()


def _warm_paper_engine() -> Any:
    from core.pipeline.paper_outcome_engine import get_paper_engine
    return get_paper_engine()


def _warm_decision_ledger() -> Any:
    from core.decision_ledger import get_ledger
    return get_ledger()


def _warm_observer_modules() -> None:
    """Import the per-symbol observer modules once, off the scanner cycle.

    Each observer is imported lazily inside the dispatch loop; the first import
    of a module (and its transitive dependencies) costs tens of milliseconds on
    the first cycle for every symbol. Importing them here removes that
    first-cycle latency without changing what runs.
    """
    import core.pipeline.event_observer  # noqa: F401
    import core.pipeline.forensic_logger  # noqa: F401
    import core.pipeline.entity_tracker  # noqa: F401
    import core.pipeline.visibility_layer  # noqa: F401
    import core.pipeline.shadow_rooms  # noqa: F401
    import core.decision_trace  # noqa: F401
    import core.strategies.strategy_intelligence_observer  # noqa: F401


# Ordered dependency warm-up registry.  Tests may monkeypatch this list.
WARMUP_DEPENDENCIES: list[tuple[str, Callable[[], Any]]] = [
    ("canonical_delivery_outbox", _warm_canonical_delivery_outbox),
    ("lifecycle_evidence_ledger", _warm_obligation_ledger),
    ("shadow_runtime", _warm_shadow_runtime),
    ("candidate_runtime", _warm_candidate_runtime),
    ("research_shadow_engine", _warm_research_shadow_engine),
    ("paper_outcome_engine", _warm_paper_engine),
    ("decision_ledger", _warm_decision_ledger),
    ("observer_modules", _warm_observer_modules),
]


def warm_runtime_dependencies(
    dependencies: list[tuple[str, Callable[[], Any]]] | None = None,
) -> dict[str, float]:
    """Eagerly construct hot-path singletons; return per-dependency seconds.

    Failures are logged (CRITICAL) and recorded as negative durations so the
    caller can see which dependency failed without this helper aborting startup.
    """
    deps = WARMUP_DEPENDENCIES if dependencies is None else dependencies
    timings: dict[str, float] = {}
    total = 0.0
    for label, factory in deps:
        start = time.perf_counter()
        try:
            factory()
            elapsed = time.perf_counter() - start
            timings[label] = elapsed
            total += elapsed
            logger.info("[STARTUP_WARMUP] %s ready in %.2fs", label, elapsed)
        except Exception as exc:  # noqa: BLE001 - warm-up must never abort startup
            elapsed = time.perf_counter() - start
            timings[label] = -elapsed
            logger.critical(
                "[STARTUP_WARMUP_FAILED] %s error=%s after %.2fs",
                label, type(exc).__name__, elapsed,
            )
    logger.info(
        "[STARTUP_WARMUP] complete total=%.2fs deps=%s",
        total, {k: round(v, 2) for k, v in timings.items()},
    )
    return timings


__all__ = ["WARMUP_DEPENDENCIES", "warm_runtime_dependencies"]
