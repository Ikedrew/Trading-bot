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

import json
from pathlib import Path
from typing import Any

from research_engine.report_builder import persist_report
from research_engine.validation import validate_dataset

_SHADOW_DIR = Path("logs/research_shadow_trades")
_SHADOW_DIR2 = Path("logs/shadow_trades")
_TRACE_DIR = Path("logs/decision_trace")


def _load_jsonl(directory: Path) -> list[dict]:
    """Load JSONL records from a directory tree."""
    records = []
    if not directory.exists():
        return records
    for f in sorted(directory.rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def run_all() -> dict[str, dict]:
    """
    Run all experiments using registry-driven runner discovery.

    Discovers runners from the registry, executes each one, attaches
    dataset validation, and returns a summary of results.
    """
    from research_engine.runner_discovery import get_all_runners

    # ─── PRE-EXPERIMENT DATASET VALIDATION ────────────────────────────
    _shadow_raw = []
    for d in [_SHADOW_DIR, _SHADOW_DIR2]:
        _shadow_raw.extend(_load_jsonl(d))
    _trace_data = _load_jsonl(_TRACE_DIR)

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
