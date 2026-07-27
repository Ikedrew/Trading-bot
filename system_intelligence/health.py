"""
Dataset Health — Answers: "Are all 24 datasets receiving records?"

Reads from: logs/ directory tree (file timestamps + line counts)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOGS_DIR = Path("logs")
_EVENTS_DIR = Path("events")

_DATASETS = [
    ("decision_ledger", _LOGS_DIR / "decision_ledger"),
    ("decision_trace", _LOGS_DIR / "decision_trace"),
    ("decision_audit", _LOGS_DIR / "decision_audit"),
    ("trade_journal", _LOGS_DIR / "trade_journal"),
    ("trade_truth", _LOGS_DIR / "trade_truth"),
    ("shadow_trades", _LOGS_DIR / "shadow_trades"),
    ("execution_results", _LOGS_DIR / "execution_results"),
    ("execution_context", _LOGS_DIR / "execution_context"),
    ("opportunities", _LOGS_DIR / "opportunities"),
    ("assessments", _LOGS_DIR / "assessments"),
    ("opportunity_assessment_log", _LOGS_DIR / "opportunity_assessment_log"),
    ("market_context", _LOGS_DIR / "market_context"),
    ("portfolio_rankings", _LOGS_DIR / "portfolio_rankings"),
    ("portfolio_shadow", _LOGS_DIR / "portfolio_shadow"),
    ("protection_audit", _LOGS_DIR / "protection_audit"),
    ("risk_deviation", _LOGS_DIR / "risk_deviation"),
    ("learning", _LOGS_DIR / "learning"),
    ("edge_attribution", _LOGS_DIR / "edge_attribution"),
    ("edge_optimisation", _LOGS_DIR / "edge_optimisation"),
    ("strategy_compiler", _LOGS_DIR / "strategy_compiler"),
    ("research_shadow_trades", _LOGS_DIR / "research_shadow_trades"),
    ("trade_truth_graph", _LOGS_DIR / "trade_truth_graph"),
    ("quarantine", _LOGS_DIR / "quarantine"),
    ("events", _EVENTS_DIR),
]


def get_dataset_health() -> dict[str, Any]:
    """
    Check freshness and record counts for all persistence datasets.

    Returns:
        - datasets: list of per-dataset health dicts
        - summary: total healthy/stale/empty counts
        - checked_at: ISO timestamp
    """
    now = time.time()
    results: list[dict[str, Any]] = []
    healthy = 0
    stale = 0
    empty = 0

    for name, path in _DATASETS:
        entry = _check_dataset(name, path, now)
        results.append(entry)
        if entry["status"] == "HEALTHY":
            healthy += 1
        elif entry["status"] == "STALE":
            stale += 1
        else:
            empty += 1

    return {
        "datasets": results,
        "summary": {
            "total": len(_DATASETS),
            "healthy": healthy,
            "stale": stale,
            "empty": empty,
        },
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _check_dataset(name: str, path: Path, now: float) -> dict[str, Any]:
    """Check one dataset directory for freshness."""
    if not path.exists():
        return {"name": name, "status": "EMPTY", "latest_file": None, "age_hours": None, "records": 0}

    # Find latest JSONL file
    files = sorted(path.rglob("*.jsonl"))
    if not files:
        return {"name": name, "status": "EMPTY", "latest_file": None, "age_hours": None, "records": 0}

    latest = files[-1]
    mtime = latest.stat().st_mtime
    age_hours = round((now - mtime) / 3600, 1)

    # Count records in latest file
    try:
        records = sum(1 for line in open(latest, encoding="utf-8") if line.strip())
    except Exception:
        records = 0

    # Stale if older than 48 hours (allows for weekends)
    status = "HEALTHY" if age_hours < 48 else "STALE"

    return {
        "name": name,
        "status": status,
        "latest_file": latest.name,
        "age_hours": age_hours,
        "records": records,
    }
