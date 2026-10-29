"""
Report Generator — Produces structured research reports as JSON.

Reports are persisted to research_reports/ directory.
Each report is reproducible and timestamped.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPORTS_DIR = "research_reports"


def _get_reports_dir() -> Path:
    """Resolve the reports output directory."""
    here = Path(__file__).resolve().parent.parent.parent
    return here / _REPORTS_DIR


def generate_report(
    *,
    experiment_name: str,
    question_id: str,
    question_text: str,
    dataset_sources: list[str],
    sample_count: int,
    metrics: dict[str, Any],
    conclusion: str,
    confidence: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """
    Generate and persist a research report.

    Returns the path to the generated report file.
    """
    timestamp = datetime.now(timezone.utc)

    report = {
        "experiment_name": experiment_name,
        "question_id": question_id,
        "question": question_text,
        "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "timestamp_unix": round(timestamp.timestamp(), 3),
        "dataset_sources": dataset_sources,
        "sample_count": sample_count,
        "metrics": metrics,
        "conclusion": conclusion,
        "confidence": confidence,
        "metadata": metadata or {},
        "research_engine_version": "0.1.0",
        "reproducible": True,
    }

    # Write report
    reports_dir = _get_reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{question_id.lower()}_{experiment_name}_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    filepath = reports_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("[RESEARCH_REPORT] generated: %s", filepath.name)
    return filepath
