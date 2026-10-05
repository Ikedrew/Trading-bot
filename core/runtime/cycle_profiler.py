"""
Cycle Profiler — lightweight wall-clock breakdown for the live scanner hot path.

Purpose:
    Measure the true cost of every meaningful stage of a scanner cycle using a
    monotonic clock (`time.perf_counter`), so runtime performance work is driven
    by measurement rather than by log spacing.

Design:
    - One `CycleProfiler` per scanner cycle.
    - `stage(name)` is a context manager that accumulates elapsed seconds into a
      named bucket (nested stages are supported; each bucket records its own
      inclusive time).
    - `symbol(name)` accumulates total time attributed to one symbol.
    - Accumulation is always on (perf_counter is ~100 ns; a cycle performs a few
      hundred samples), but the ranked report is only *printed* when profiling
      is enabled or when a cycle exceeds the liveness threshold.
    - Purely observational: it never influences control flow and never raises.

Enable/disable:
    Env `SCANNER_PROFILE=1` forces a ranked report every cycle.
    The scanner also requests a report automatically for any cycle whose total
    duration exceeds `LIVENESS_STALL_THRESHOLD_SECONDS`.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _env_enabled() -> bool:
    return os.getenv("SCANNER_PROFILE", "").strip().lower() in _TRUTHY


class CycleProfiler:
    """Accumulates per-stage and per-symbol wall-clock timings for one cycle."""

    __slots__ = ("cycle_id", "enabled", "_stages", "_counts", "_symbols", "_notes", "_t0")

    def __init__(self, cycle_id: int, enabled: bool | None = None) -> None:
        self.cycle_id = cycle_id
        self.enabled = _env_enabled() if enabled is None else enabled
        self._stages: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._symbols: dict[str, float] = {}
        self._notes: dict[str, Any] = {}
        self._t0 = time.perf_counter()

    def note(self, key: str, value: Any) -> None:
        """Attach an informational value to the cycle report."""
        self._notes[key] = value

    # ── stage timing ──────────────────────────────────────────────────────
    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Accumulate elapsed seconds under `name`."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._stages[name] = self._stages.get(name, 0.0) + elapsed
            self._counts[name] = self._counts.get(name, 0) + 1

    def add(self, name: str, elapsed: float) -> None:
        """Record a pre-measured duration under `name`."""
        self._stages[name] = self._stages.get(name, 0.0) + elapsed
        self._counts[name] = self._counts.get(name, 0) + 1

    def symbol(self, name: str, elapsed: float) -> None:
        """Attribute `elapsed` seconds to one symbol for the cycle."""
        self._symbols[name] = self._symbols.get(name, 0.0) + elapsed

    # ── reporting ─────────────────────────────────────────────────────────
    @property
    def total(self) -> float:
        return time.perf_counter() - self._t0

    def ranked(self) -> list[tuple[str, float, int]]:
        """Return (stage, seconds, calls) sorted by descending seconds."""
        return sorted(
            ((name, secs, self._counts.get(name, 0)) for name, secs in self._stages.items()),
            key=lambda item: item[1],
            reverse=True,
        )

    def report(self, total_override: float | None = None) -> str:
        total = self.total if total_override is None else total_override
        lines = [f"[PERF] cycle={self.cycle_id} total_s={total:.3f}"]
        for name, secs, calls in self.ranked():
            if secs < 0.001 and calls <= 1:
                continue
            lines.append(f"[PERF]   {name:<34s} {secs*1000:9.1f} ms  n={calls}")
        if self._symbols:
            worst = sorted(self._symbols.items(), key=lambda kv: kv[1], reverse=True)[:5]
            lines.append(
                "[PERF]   slowest_symbols "
                + ", ".join(f"{sym}={secs*1000:.0f}ms" for sym, secs in worst)
            )
        for key, value in self._notes.items():
            lines.append(f"[PERF]   {key}={value}")
        return "\n".join(lines)

    def emit(self, total_override: float | None = None, *, force: bool = False) -> None:
        """Log the ranked report (always when enabled, otherwise only if forced)."""
        if not (self.enabled or force):
            return
        try:
            logger.warning(self.report(total_override))
        except Exception:  # pragma: no cover - reporting must never break a cycle
            pass


def enabled() -> bool:
    """Whether per-cycle ranked reports are enabled via environment."""
    return _env_enabled()
