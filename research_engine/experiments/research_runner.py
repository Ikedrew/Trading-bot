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


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITATIVE STATUS EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════
#
# Status ownership (research_engine/experiments/report_contract.py):
#     report["status"]         — whether the research question actually
#                                completed with sufficient evidence
#                                (COMPLETE / WAITING_DATA / BLOCKED /
#                                INSUFFICIENT_DATA).  AUTHORITATIVE.
#     report["recommendation"] — what the engine recommends based on that
#                                result (action/finding label: COMPLETE,
#                                WAIT, PROMOTE_CALIBRATION, POSITIVE_EDGE,
#                                NEGATIVE_EDGE, SHADOW_TRUSTED, ...).
#                                NOT the run status.
#
# The summary must report the research result's actual status. It must never
# silently default to COMPLETE, and a malformed/unknown result shape must be
# reported explicitly.


def _extract_run_status(report: Any) -> tuple[str, str]:
    """
    Extract the authoritative research-run status from a runner result.

    Returns (status, status_source) where status_source is one of:
        "report"         — taken from report["status"] (canonical contract)
        "recommendation" — legacy report family without a top-level status;
                           the nested recommendation.status is the only
                           status-bearing field (explicit, surfaced as such)
        "error"          — the runner result is not even a dict

    Never fabricates or defaults to COMPLETE.
    """
    if not isinstance(report, dict):
        return "MALFORMED_REPORT", "error"

    status = report.get("status")
    if isinstance(status, str) and status:
        return status, "report"

    # Known legacy shape: no top-level status; nested recommendation.status
    # carries the only status information (see normalize_legacy_report).
    rec = report.get("recommendation")
    if isinstance(rec, dict):
        rec_status = rec.get("status")
        if isinstance(rec_status, str) and rec_status:
            return rec_status, "recommendation"
    return "UNKNOWN_STATUS", "missing"


def _extract_sample(report: Any) -> int:
    """Sample size from the same authoritative result context (no fabrication)."""
    if not isinstance(report, dict):
        return 0
    dataset = report.get("dataset", {})
    if not isinstance(dataset, dict):
        return 0
    return dataset.get("sample_size", 0) or dataset.get("r_multiples_used", 0)


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
            # Authoritative research-run status (report["status"]), NEVER the
            # recommendation/action label. Recommendation carried separately.
            status_val, status_source = _extract_run_status(report)
            results[qid] = {
                "status": status_val,
                "status_source": status_source,
                "sample": _extract_sample(report),
                "recommendation": report.get("recommendation", ""),
            }
        except Exception as e:
            results[qid] = {"status": "ERROR", "status_source": "error", "error": str(e)[:100]}
    return results


if __name__ == "__main__":
    results = run_all()
    for qid, info in sorted(results.items()):
        print(f"  {qid}: {info['status']} (n={info.get('sample', '?')})")
