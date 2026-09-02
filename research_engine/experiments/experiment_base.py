"""
Shared Experiment Base — Common infrastructure for all research experiments.

Provides:
    - Standard report contract
    - Dataset fingerprinting
    - Readiness validation
    - Shadow trade loading
    - Report persistence
    - Knowledge map integration

Every experiment imports from here rather than duplicating this logic.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.data_access.s3_source import get_default_source

# Production-contract dataset names read via the shared S3 data-access layer.
_SHADOW_DATASET = "shadow_trades"
_RESEARCH_SHADOW_DATASET = "research_shadow_trades"
_REPORTS_DIR = Path("analysis/reports")
_KNOWLEDGE_PATH = Path("analysis/summaries/research_knowledge.json")


# ═══════════════════════════════════════════════════════════════════════════════
# READINESS STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class ReadinessStatus:
    READY = "READY"
    WAITING_DATA = "WAITING_DATA"
    BLOCKED = "BLOCKED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    COMPLETE = "COMPLETE"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════


def load_shadow_trades(
    *,
    epoch: str = "CURRENT",
    include_all_epochs: bool = False,
) -> list[dict[str, Any]]:
    """
    Load shadow trade records filtered by data epoch.

    Default: CURRENT epoch only (safe for implementation decisions).
    Historical data requires explicit opt-in via include_all_epochs=True.

    Args:
        epoch: Which epoch to load. "CURRENT" (default), "TRANSITIONAL", "LEGACY", or "ALL".
        include_all_epochs: If True, ignores epoch filter and returns everything.
                           MUST be explicitly set — prevents accidental contamination.

    Returns:
        List of shadow trade dicts matching the requested epoch.
    """
    from research_engine.data_quality.classifier import classify_record, DataEpoch

    _source = get_default_source()
    records: list[dict[str, Any]] = []
    for dataset in (_SHADOW_DATASET, _RESEARCH_SHADOW_DATASET):
        records.extend(_source.read_dataset(dataset))

    # Epoch filtering (default: CURRENT only)
    if include_all_epochs or epoch == "ALL":
        return records

    epoch_map = {
        "CURRENT": DataEpoch.CURRENT,
        "TRANSITIONAL": DataEpoch.TRANSITIONAL,
        "LEGACY": DataEpoch.LEGACY,
    }
    target_epoch = epoch_map.get(epoch, DataEpoch.CURRENT)
    return [r for r in records if classify_record(r) == target_epoch]


def load_shadow_trades_all() -> list[dict[str, Any]]:
    """
    Load ALL shadow trades (all epochs). Use for historical comparison ONLY.

    WARNING: Results from this function MUST NOT be used to make
    implementation decisions about the current system. Use load_shadow_trades()
    (CURRENT epoch) for all promotion/implementation research.
    """
    return load_shadow_trades(include_all_epochs=True)


def extract_r_multiples(records: list[dict[str, Any]]) -> list[float]:
    """Extract R-multiple values from shadow trade records."""
    values: list[float] = []
    for r in records:
        rm = _deep_get(r, "simulated_outcome", "pnl_r_multiple")
        if rm is not None:
            values.append(float(rm))
    return values


def extract_field(record: dict[str, Any], *paths: str) -> Any:
    """Extract a field trying multiple nested paths."""
    return _deep_get(record, *paths)


def _deep_get(d: Any, *keys: str) -> Any:
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


# ═══════════════════════════════════════════════════════════════════════════════
# DATASET FINGERPRINT
# ═══════════════════════════════════════════════════════════════════════════════


def build_fingerprint(
    records_used: int,
    records_excluded: int,
    source: str = "shadow_trades",
    validation_score: str = "UNKNOWN",
    epoch: str = "CURRENT",
) -> dict[str, Any]:
    """Build a standard dataset fingerprint with epoch metadata."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "dataset_id": f"{source}_{today}",
        "records_used": records_used,
        "records_excluded": records_excluded,
        "source": source,
        "epoch": epoch,
        "architecture_version": "new_pipeline_v1.2",
        "validation_score": validation_score,
        "generated": today,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# READINESS VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


def check_readiness(
    records: list[dict[str, Any]],
    min_samples: int = 50,
    require_lineage: bool = False,
    lineage_threshold: float = 0.80,
    require_outcome: bool = True,
    outcome_threshold: float = 0.95,
    require_strategy: bool = False,
    strategy_threshold: float = 0.50,
    require_no_contamination: bool = False,
) -> tuple[str, str, dict[str, float]]:
    """
    Check if data meets experiment requirements.

    Returns: (status, reason, coverage_dict)
    """
    if not records:
        return ReadinessStatus.INSUFFICIENT_DATA, "No records available", {}

    n = len(records)
    if n < min_samples:
        return ReadinessStatus.INSUFFICIENT_DATA, f"Only {n} records (need {min_samples})", {}

    # Compute coverage
    outcome_count = sum(1 for r in records if _deep_get(r, "simulated_outcome", "pnl_r_multiple") is not None)
    lineage_count = sum(1 for r in records if (_deep_get(r, "identity", "entity_id") or ""))
    strategy_count = 0
    contaminated = 0
    valid_strategies = {"REVERSAL", "CONTINUATION", "FALSE_BREAK"}
    combined_suffixes = {"_SCALP", "_INTRADAY", "_EXTENDED"}

    for r in records:
        s = _deep_get(r, "identity", "strategy_id") or _deep_get(r, "decision_snapshot", "strategy") or ""
        if s:
            if any(suffix in s for suffix in combined_suffixes):
                contaminated += 1
            elif s in valid_strategies:
                strategy_count += 1

    coverage = {
        "outcome": outcome_count / n if n > 0 else 0.0,
        "lineage": lineage_count / n if n > 0 else 0.0,
        "strategy": strategy_count / n if n > 0 else 0.0,
        "contamination_rate": contaminated / n if n > 0 else 0.0,
        "sample_size": float(n),
    }

    if require_outcome and coverage["outcome"] < outcome_threshold:
        return ReadinessStatus.WAITING_DATA, f"Outcome coverage {coverage['outcome']:.1%} < {outcome_threshold:.0%}", coverage

    if require_lineage and coverage["lineage"] < lineage_threshold:
        return ReadinessStatus.WAITING_DATA, f"Lineage coverage {coverage['lineage']:.1%} < {lineage_threshold:.0%}", coverage

    if require_strategy and coverage["strategy"] < strategy_threshold:
        return ReadinessStatus.WAITING_DATA, f"Strategy coverage {coverage['strategy']:.1%} < {strategy_threshold:.0%}", coverage

    if require_no_contamination and contaminated > 0:
        return ReadinessStatus.BLOCKED, f"{contaminated} contaminated records detected", coverage

    return ReadinessStatus.READY, "All requirements met", coverage


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════


def compute_confidence(n: int, significant: bool = False) -> str:
    """Determine confidence level from sample size and significance."""
    if n >= 200 and significant:
        return "HIGH"
    if n >= 100:
        return "HIGH" if significant else "MEDIUM"
    if n >= 50:
        return "MEDIUM"
    if n >= 20:
        return "LOW"
    return "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION & PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


def build_report(
    question_id: str,
    status: str,
    overall: dict[str, Any],
    confidence: str,
    dataset: dict[str, Any],
    fingerprint: dict[str, Any],
    recommendation: str,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standard experiment report following the common contract."""
    # Epoch safety: warn if fingerprint indicates mixed or non-CURRENT data
    _epoch = fingerprint.get("epoch", "UNKNOWN")
    _epoch_warnings: list[str] = []
    if _epoch not in ("CURRENT", "CURRENT_ONLY", "shadow_trades_current"):
        _epoch_warnings.append(
            f"EPOCH_WARNING: Data epoch is '{_epoch}'. "
            f"Results may not represent current system. "
            f"Use load_shadow_trades(epoch='CURRENT') for implementation decisions."
        )

    all_warnings = (warnings or []) + _epoch_warnings

    report = {
        "question_id": question_id,
        "status": status,
        "overall": overall,
        "confidence": confidence,
        "dataset": dataset,
        "fingerprint": fingerprint,
        "recommendation": recommendation,
        "assumptions": assumptions or [],
        "warnings": all_warnings,
        "epoch": _epoch,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": provenance or {
            "experiment_module": f"research_engine.experiments.{question_id.lower()}",
            "registry_id": question_id,
            "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre",
        },
    }

    # Validate against canonical contract
    from research_engine.experiments.report_contract import validate_report_contract
    valid, errors = validate_report_contract(report)
    if not valid:
        import logging
        logging.getLogger(__name__).warning(
            "[REPORT_CONTRACT] %s report has contract violations: %s", question_id, errors
        )

    return report


def persist_report(report: dict[str, Any], filename: str) -> Path:
    """Persist report to analysis/reports/."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORTS_DIR / filename
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def update_knowledge_map(
    question_id: str,
    finding: str,
    recommendation: str,
    is_rejection: bool = False,
) -> None:
    """Append finding to knowledge map without overwriting historical data."""
    _KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    knowledge: dict[str, Any] = {}
    if _KNOWLEDGE_PATH.exists():
        try:
            knowledge = json.loads(_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if "confirmed_facts" not in knowledge:
        knowledge["confirmed_facts"] = []
    if "rejected_hypotheses" not in knowledge:
        knowledge["rejected_hypotheses"] = []

    entry = f"{question_id}: {finding}"

    if is_rejection:
        if entry not in knowledge["rejected_hypotheses"]:
            knowledge["rejected_hypotheses"].append(entry)
    else:
        if entry not in knowledge["confirmed_facts"]:
            knowledge["confirmed_facts"].append(entry)

    knowledge["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _KNOWLEDGE_PATH.write_text(json.dumps(knowledge, indent=2, default=str), encoding="utf-8")
