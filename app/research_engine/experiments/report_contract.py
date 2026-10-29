"""
Unified Research Report Contract — Single canonical schema for all experiments.

Every experiment (legacy Q-series and v2 experiments) must conform to this
contract. The contract validator prevents future schema drift.

This module is PURELY INFRASTRUCTURE. It does NOT modify trading logic.

Contract Fields (all required):
    question_id         str     e.g. "R3", "Q19", "E5"
    status              str     COMPLETE | WAITING_DATA | BLOCKED | INSUFFICIENT_DATA
    overall             dict    Primary experiment results
    confidence          str     HIGH | MEDIUM | LOW | INSUFFICIENT_DATA
    dataset             dict    {source, sample_size, ...}
    fingerprint         dict    {dataset_id, records_used, records_excluded, validation_score}
    recommendation      str     PROMOTE | MONITOR | WAIT | REJECT | COMPLETE | POSITIVE_EDGE | ...
    assumptions         list    List of assumption strings ([] if none)
    warnings            list    List of warning strings ([] if none)
    generated           str     ISO timestamp
    provenance          dict    {experiment_module, registry_id, pipeline}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL FIELD NAMES
# ═══════════════════════════════════════════════════════════════════════════════

REQUIRED_FIELDS = (
    "question_id",
    "status",
    "overall",
    "confidence",
    "dataset",
    "fingerprint",
    "recommendation",
    "assumptions",
    "warnings",
    "generated",
    "provenance",
)

VALID_STATUSES = frozenset({
    "COMPLETE",
    "WAITING_DATA",
    "BLOCKED",
    "INSUFFICIENT_DATA",
})

VALID_CONFIDENCE = frozenset({
    "HIGH",
    "MEDIUM",
    "LOW",
    "INSUFFICIENT_DATA",
})

REQUIRED_FINGERPRINT_FIELDS = ("dataset_id", "records_used", "records_excluded", "validation_score")
REQUIRED_PROVENANCE_FIELDS = ("experiment_module", "registry_id")


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════


def validate_report_contract(report: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate a report against the canonical contract.

    Returns (valid, errors) where errors is a list of violation descriptions.
    An empty errors list means the report is valid.
    """
    errors: list[str] = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in report:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors  # Can't validate further without required fields

    # Type checks
    if not isinstance(report["question_id"], str) or not report["question_id"]:
        errors.append("question_id must be a non-empty string")

    if report["status"] not in VALID_STATUSES:
        errors.append(f"Invalid status '{report['status']}'. Must be one of: {sorted(VALID_STATUSES)}")

    if not isinstance(report["overall"], dict):
        errors.append("overall must be a dict")

    if report["confidence"] not in VALID_CONFIDENCE:
        errors.append(f"Invalid confidence '{report['confidence']}'. Must be one of: {sorted(VALID_CONFIDENCE)}")

    if not isinstance(report["dataset"], dict):
        errors.append("dataset must be a dict")

    if not isinstance(report["fingerprint"], dict):
        errors.append("fingerprint must be a dict")
    else:
        for field in REQUIRED_FINGERPRINT_FIELDS:
            if field not in report["fingerprint"]:
                errors.append(f"fingerprint missing required field: {field}")

    if not isinstance(report["recommendation"], str):
        errors.append("recommendation must be a string")

    if not isinstance(report["assumptions"], list):
        errors.append("assumptions must be a list")

    if not isinstance(report["warnings"], list):
        errors.append("warnings must be a list")

    if not isinstance(report["generated"], str) or not report["generated"]:
        errors.append("generated must be a non-empty ISO timestamp string")

    if not isinstance(report["provenance"], dict):
        errors.append("provenance must be a dict")
    else:
        for field in REQUIRED_PROVENANCE_FIELDS:
            if field not in report["provenance"]:
                errors.append(f"provenance missing required field: {field}")

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY ADAPTER
# ═══════════════════════════════════════════════════════════════════════════════


def normalize_legacy_report(legacy_report: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a legacy (v1 wrap_report) format to the canonical contract.

    Legacy format:
        question_id, question_name, timestamp, dataset{source, sample_size},
        metrics, finding, recommendation{status, target}, data

    Canonical format:
        question_id, status, overall, confidence, dataset, fingerprint,
        recommendation, assumptions, warnings, generated, provenance
    """
    # Extract fields
    qid = legacy_report.get("question_id", "")
    dataset_info = legacy_report.get("dataset", {})
    sample_size = dataset_info.get("sample_size", 0)
    source = dataset_info.get("source", "")
    rec = legacy_report.get("recommendation", {})
    rec_status = rec.get("status", "") if isinstance(rec, dict) else str(rec)
    metrics = legacy_report.get("metrics", {})
    finding = legacy_report.get("finding", "")
    timestamp = legacy_report.get("timestamp", "")
    experiment_data = legacy_report.get("data", {})

    # Map legacy recommendation status to canonical status
    status_map = {
        "COMPLETE": "COMPLETE",
        "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
        "BLOCKED": "BLOCKED",
        "PROMOTE_CALIBRATION": "COMPLETE",
        "POSITIVE_EDGE": "COMPLETE",
        "WEIGHT_ADJUSTMENT": "COMPLETE",
        "KEEP_CURRENT": "COMPLETE",
    }
    status = status_map.get(rec_status, "COMPLETE")

    # Determine confidence from sample size
    if sample_size >= 100:
        confidence = "HIGH"
    elif sample_size >= 30:
        confidence = "MEDIUM"
    elif sample_size > 0:
        confidence = "LOW"
    else:
        confidence = "INSUFFICIENT_DATA"

    # Build fingerprint
    today = timestamp[:10] if timestamp else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fingerprint = {
        "dataset_id": f"{source}_{today}" if source else f"unknown_{today}",
        "records_used": sample_size,
        "records_excluded": 0,
        "validation_score": confidence,
    }

    return {
        "question_id": qid,
        "status": status,
        "overall": {**metrics, "finding": finding, **(experiment_data or {})},
        "confidence": confidence,
        "dataset": dataset_info,
        "fingerprint": fingerprint,
        "recommendation": rec_status,
        "assumptions": [],
        "warnings": [],
        "generated": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "experiment_module": f"research_engine.experiments.research_runner",
            "registry_id": qid,
            "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre",
            "format": "normalized_from_legacy_v1",
        },
    }
