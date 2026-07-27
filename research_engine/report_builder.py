"""
Research Engine — Report Builder & Dashboard Generator.

Scans available research reports, reads recommendation status,
and generates research_dashboard.json.

Also provides the standard report schema wrapper for experiments.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.question_registry import QUESTIONS, get_question

_REPORTS_DIR = Path("analysis/reports")
_SUMMARIES_DIR = Path("analysis/summaries")
_ARTIFACTS_DIR = Path("analysis/artifacts")


# ─── STANDARD REPORT SCHEMA ──────────────────────────────────────────────────


def wrap_report(
    *,
    question_id: str,
    question_name: str,
    dataset_source: str,
    sample_size: int,
    metrics: dict[str, Any],
    finding: str,
    recommendation_status: str,
    recommendation_target: str = "",
    experiment_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Wrap experiment results in the standard research report contract.

    Every research report follows this schema. Experiment-specific data
    is preserved inside the 'data' field.
    """
    return {
        "question_id": question_id,
        "question_name": question_name,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": {
            "source": dataset_source,
            "sample_size": sample_size,
        },
        "metrics": metrics,
        "finding": finding,
        "recommendation": {
            "status": recommendation_status,
            "target": recommendation_target,
        },
        "data": experiment_data or {},
    }


def persist_report(report: dict[str, Any]) -> Path:
    """Persist a standard research report to analysis/reports/."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    qid = report.get("question_id", "unknown").lower()
    name = report.get("question_name", "").lower().replace(" ", "_")[:30]
    filename = f"{qid}_{name}.json" if name else f"{qid}.json"
    path = _REPORTS_DIR / filename
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


# ─── DASHBOARD GENERATOR ──────────────────────────────────────────────────────


def generate_dashboard() -> dict[str, Any]:
    """
    Generate research_dashboard.json from available reports + registry.

    Scans analysis/reports/ for existing results and combines with
    question registry status.
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    dashboard: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_questions": len(QUESTIONS),
        "questions": {},
    }

    # Load existing reports
    existing_reports: dict[str, dict] = {}
    for report_file in _REPORTS_DIR.glob("q*.json"):
        try:
            data = json.loads(report_file.read_text(encoding="utf-8"))
            qid = data.get("question_id", "")
            if qid:
                existing_reports[qid] = {
                    "file": str(report_file),
                    "timestamp": data.get("timestamp", ""),
                    "recommendation": data.get("recommendation", {}).get("status", ""),
                    "finding": data.get("finding", "")[:100],
                }
        except Exception:
            pass

    # Build per-question status
    for q in QUESTIONS:
        entry: dict[str, Any] = {
            "question": q.question,
            "priority": f"P{q.priority}",
            "implementation_status": q.status,
            "runner": q.runner or "not_implemented",
        }

        if q.id in existing_reports:
            report_info = existing_reports[q.id]
            entry["last_run"] = report_info["timestamp"]
            entry["recommendation"] = report_info["recommendation"]
            entry["report_file"] = report_info["file"]
            entry["status"] = "COMPLETE"
        elif q.status == "ready":
            entry["status"] = "READY"
        elif q.status == "blocked":
            entry["status"] = "BLOCKED"
            entry["blocker"] = q.blocker
        else:
            entry["status"] = "NOT_IMPLEMENTED"

        dashboard["questions"][q.id] = entry

    # Summary counts
    statuses = [v.get("status", "") for v in dashboard["questions"].values()]
    dashboard["summary"] = {
        "complete": statuses.count("COMPLETE"),
        "ready": statuses.count("READY"),
        "blocked": statuses.count("BLOCKED"),
        "not_implemented": statuses.count("NOT_IMPLEMENTED"),
    }

    # Persist
    _SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _SUMMARIES_DIR / "research_dashboard.json"
    output_path.write_text(json.dumps(dashboard, indent=2, default=str), encoding="utf-8")

    return dashboard


# ─── CALIBRATION ARTIFACT ─────────────────────────────────────────────────────


def generate_calibration_artifact(bucket_data: list[dict]) -> Path:
    """
    Generate the score calibration curve artifact from Q20 research.

    This is the PROMOTION artifact — what the bot would consume
    if calibration is approved. It does NOT alter runtime behaviour.
    It only records the research finding in a bot-consumable format.

    Args:
        bucket_data: List of dicts with score_min, score_max, actual_wr

    Returns:
        Path to the generated artifact.
    """
    mapping = []
    for bucket in bucket_data:
        if bucket.get("sufficient_data") and bucket.get("n", 0) > 0:
            # Parse bucket range
            bucket_label = bucket.get("bucket", "")
            parts = bucket_label.replace("+", "-1.00").split("-")
            try:
                score_min = float(parts[0])
                score_max = float(parts[1])
            except (IndexError, ValueError):
                continue
            mapping.append({
                "score_min": score_min,
                "score_max": score_max,
                "probability": round(bucket.get("actual_wr", 0), 4),
                "sample_size": bucket.get("n", 0),
            })

    artifact = {
        "version": "calibration_v1",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_question": "Q20",
        "status": "RESEARCH_ARTIFACT",
        "note": "This artifact does NOT alter runtime behaviour. It records the calibration curve for future promotion.",
        "mapping": mapping,
    }

    artifact_dir = _ARTIFACTS_DIR / "calibration"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifact_dir / "score_calibration_curve.json"
    output_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    return output_path


# ─── MAIN ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    dashboard = generate_dashboard()
    print(json.dumps(dashboard, indent=2))
