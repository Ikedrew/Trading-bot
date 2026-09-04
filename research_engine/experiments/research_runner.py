"""
Research Runner — Orchestration layer for executing all research experiments.

This module provides run_all() which discovers and executes every registered
experiment using the registry-driven runner discovery system.

It does NOT contain experiment implementations. All experiments live in:
    - research_engine/experiments/legacy_canonical.py (migrated Q-series)
    - research_engine/experiments/*.py (v2 standalone experiments)

This module is PURELY ORCHESTRATION. It does NOT modify trading logic.
"""

from __future__ import annotations

from typing import Any

from research_engine.data_access.s3_source import get_default_source
from research_engine.data_access.shadow_runtime_ingestion import (
    ingest_completed_shadow_trades,
)
from research_engine.report_builder import persist_report
from research_engine.validation import validate_dataset

_SHADOW_DATASET = "research_shadow_trades"
_TRACE_DATASET = "decision_trace"


def _load_jsonl(dataset: str) -> list[dict]:
    """Read a production dataset from S3 via the shared data-access layer."""
    return get_default_source().read_dataset(dataset)


def run_all() -> dict[str, dict]:
    """
    Run all experiments using registry-driven runner discovery.

    Discovers runners from the registry, executes each one, attaches
    dataset validation, and returns a summary of results.
    """
    from research_engine.runner_discovery import get_all_runners

    # ─── PRE-EXPERIMENT DATASET VALIDATION ────────────────────────────
    # Canonical production shadow source (S3 shadow_runtime_v1 event stream,
    # reconstructed into completed shadow outcomes), then the separate
    # live-written research_shadow_trades dataset. Order preserved.
    _shadow_raw = [
        *ingest_completed_shadow_trades(),
        *_load_jsonl(_SHADOW_DATASET),
    ]
    _trace_data = _load_jsonl(_TRACE_DATASET)

    shadow_validation = validate_dataset(
        _shadow_raw,
        dataset_name="shadow_trades_combined",
    )
    trace_validation = validate_dataset(
        _trace_data,
        dataset_name="decision_trace",
    )

    _validation_summary = {
        "shadow_trades": shadow_validation.to_dict(),
        "decision_trace": trace_validation.to_dict(),
    }
    # ─── END DATASET VALIDATION ───────────────────────────────────────

    # Get all runners from registry discovery
    all_runners = get_all_runners()

    results = {}
    for qid, runner in sorted(all_runners.items()):
        try:
            report = runner()
            # Attach dataset validation to every report
            report["dataset_validation"] = _validation_summary
            # Extract status
            rec = report.get("recommendation", {})
            if isinstance(rec, dict):
                status_val = rec.get("status", "?")
            else:
                status_val = str(rec)
            sample = (
                report.get("dataset", {}).get("sample_size", 0)
                or report.get("dataset", {}).get("r_multiples_used", 0)
            )
            results[qid] = {"status": status_val, "sample": sample}
        except Exception as e:
            results[qid] = {"status": "ERROR", "error": str(e)[:100]}
    return results


if __name__ == "__main__":
    results = run_all()
    for qid, info in sorted(results.items()):
        print(f"  {qid}: {info['status']} (n={info.get('sample', '?')})")
