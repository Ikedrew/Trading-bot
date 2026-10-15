"""Read-only local cache preflight for an authenticated snapshot manifest.

This checks the production cache identity/path and the independent Stage B
header, length, and SHA-256 validator. It never authenticates S3 authority.
"""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable

from tools.stage_b_recovery import verify_cached_entry


def preflight_snapshot_cache(
    snapshot: Any, source: Any, *, max_workers: int = 8,
    deadline_seconds: float = 1200.0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Collect every local cache issue, with bounded concurrency and duration."""
    if max_workers < 1 or deadline_seconds <= 0:
        raise ValueError("CACHE_PREFLIGHT_BOUNDS_INVALID")
    objects = iter((binding.dataset, item.to_dict())
                   for binding in snapshot.datasets if binding.presence != "ABSENT"
                   for item in binding.objects)
    total = sum(len(binding.objects) for binding in snapshot.datasets
                if binding.presence != "ABSENT")
    before = source.performance_snapshot()
    started = time.perf_counter()
    checked = 0
    issues: list[dict[str, str]] = []
    pending: dict[Any, tuple[str, str]] = {}
    exhausted = False
    timed_out = False
    last_report = started

    def check(dataset: str, metadata: dict[str, Any]) -> str | None:
        try:
            verify_cached_entry(source, dataset, metadata)
            return None
        except Exception as exc:
            return f"{type(exc).__name__}:{exc}"

    def report(force: bool = False) -> None:
        nonlocal last_report
        now = time.perf_counter()
        if progress_callback is not None and (force or checked % 1000 == 0
                                              or now - last_report >= 10):
            progress_callback({"checked": checked, "total": total,
                               "issues": len(issues),
                               "elapsed_seconds": round(now - started, 3)})
            last_report = now

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        def fill() -> None:
            nonlocal exhausted
            while not exhausted and len(pending) < 4 * max_workers:
                try:
                    dataset, metadata = next(objects)
                except StopIteration:
                    exhausted = True
                    break
                pending[pool.submit(check, dataset, metadata)] = (
                    dataset, str(metadata["identifier"]))

        report(force=True)
        fill()
        while pending:
            remaining = deadline_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                timed_out = True
                break
            done, _ = wait(pending, timeout=min(10.0, remaining),
                           return_when=FIRST_COMPLETED)
            if not done:
                report(force=True)
                continue
            for future in done:
                dataset, identifier = pending.pop(future)
                error = future.result()
                checked += 1
                if error is not None:
                    issues.append({"dataset": dataset, "identifier": identifier,
                                   "error": error})
            report()
            fill()
        if timed_out:
            for future in pending:
                future.cancel()
    report(force=True)
    after = source.performance_snapshot()
    remote_calls = {name: after[name] - before[name]
                    for name in ("head_calls", "get_calls", "list_calls")}
    status = ("TIMEOUT" if timed_out else "REMOTE_IO_VIOLATION"
              if any(remote_calls.values()) else "INVALID" if issues else "PASS")
    return {"status": status, "snapshot_id": snapshot.snapshot_id,
            "checked": checked, "total": total,
            "issues": sorted(issues, key=lambda item: (item["dataset"], item["identifier"])),
            "remote_calls": remote_calls,
            "elapsed_seconds": round(time.perf_counter() - started, 3)}
