"""Resolve report artifacts at the canonical research-truth boundary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.invalidation import is_report_invalidated
from research_engine.control_plane.report_ownership import (
    OWNERSHIP_METADATA_CONFLICT,
    resolve_report_ownership,
)
from research_engine.validity_gates import validate_experiment_report

_KNOWN_INVALIDATED_DATASETS = {
    "r3_probability_of_ruin.json": frozenset({"shadow_trades_2026-07-27"}),
    "r4_drawdown_threshold.json": frozenset({"shadow_trades_2026-07-27"}),
    "r5_position_sizing.json": frozenset({"shadow_trades_2026-07-27"}),
}
_CURRENT_EPOCHS = frozenset({"CURRENT", "CURRENT_ONLY", "SHADOW_TRADES_CURRENT"})
_CURRENT_REPORT_STATUSES = frozenset({
    "COMPLETE", "READY", "WAITING_DATA", "BLOCKED",
    "INSUFFICIENT_DATA", "ERROR",
})
_AUTHORITATIVE_RESULT_STATUSES = frozenset({"COMPLETE"})
_REPORTS_DIR = Path("analysis/reports")


def _load_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _extract_epoch(report: dict[str, Any]) -> str:
    fingerprint = report.get("fingerprint", {})
    value = report.get("epoch")
    if not value and isinstance(fingerprint, dict):
        value = fingerprint.get("epoch") or fingerprint.get("data_epoch")
    return str(value or "").strip().upper()


def _numeric_value(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 0 else None


def _extract_sample_size(report: dict[str, Any]) -> int | None:
    locations = (
        report,
        report.get("dataset", {}),
        report.get("fingerprint", {}),
        report.get("overall", {}),
    )
    keys = (
        "sample_size", "n", "records_used", "r_multiples_used",
        "total_trades", "total_records", "total_analysed", "matched_trades",
    )
    for section in locations:
        if not isinstance(section, dict):
            continue
        for key in keys:
            if key in section:
                value = _numeric_value(section[key])
                if value is not None:
                    return value
    return None


def _extract_finding(report: dict[str, Any]) -> str:
    overall = report.get("overall", {})
    if isinstance(overall, dict):
        for key in ("finding", "conclusion", "summary"):
            value = overall.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("finding", "conclusion", "summary"):
        value = report.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_confidence(report: dict[str, Any]) -> str:
    overall = report.get("overall", {})
    if isinstance(overall, dict) and overall.get("confidence") is not None:
        return str(overall["confidence"])
    value = report.get("confidence")
    return str(value) if value is not None else ""


def _extract_status(report: dict[str, Any]) -> str:
    return str(report.get("status", "")).upper() if report else ""


def _extract_timestamp(report: dict[str, Any]) -> str:
    for key in ("generated", "generated_at", "timestamp", "last_run"):
        if report and report.get(key):
            return str(report[key])
    fp = report.get("fingerprint", {}) if report else {}
    if isinstance(fp, dict) and fp.get("generated"):
        return str(fp["generated"])
    return ""


def _extract_recommendation(report: dict[str, Any]) -> str:
    if not report:
        return ""
    value = report.get("recommendation")
    if isinstance(value, dict):
        return str(value.get("status", ""))
    return str(value) if value is not None else ""


def _extract_validity_reason(report, validity):
    if report is None:
        return "Report file not found or unreadable"
    if validity == ReportValidity.VALID_CURRENT:
        return f"CURRENT artifact with status {_extract_status(report)}"
    return ""


def _is_epoch_current(report: dict[str, Any]) -> bool:
    return _extract_epoch(report) in _CURRENT_EPOCHS


def _resolve_structured_provenance(
    report: dict[str, Any],
) -> tuple[bool, ReportValidity | None, str]:
    """Validate structured evidence provenance when a fingerprint carries it.

    The first boolean distinguishes an absent structure (legacy compatibility)
    from a present but invalid one (which must fail closed).
    """
    fingerprint = report.get("fingerprint", {})
    if not isinstance(fingerprint, dict) or "evidence_provenance" not in fingerprint:
        return False, None, ""

    from research_engine.control_plane.evidence_provenance import (
        CURRENT,
        validate_evidence_provenance,
    )

    valid, state, reason = validate_evidence_provenance(
        fingerprint.get("evidence_provenance")
    )
    if not valid:
        return True, ReportValidity.INVALIDATED, reason
    provenance = fingerprint["evidence_provenance"]
    components = provenance["components"]
    sources = [str(component["source"]) for component in components]
    expected_source = sources[0] if len(sources) == 1 else "MULTI_SOURCE"
    if (
        fingerprint.get("records_used") != provenance.get("records_used")
        or fingerprint.get("records_excluded") != provenance.get("records_excluded")
        or fingerprint.get("sources") != sources
        or fingerprint.get("source") != expected_source
    ):
        return (
            True,
            ReportValidity.INVALIDATED,
            "Fingerprint summary contradicts structured evidence provenance",
        )
    declared_epoch = _extract_epoch(report)
    if state != CURRENT:
        return True, ReportValidity.STALE, reason
    if declared_epoch != CURRENT:
        return (
            True,
            ReportValidity.INVALIDATED,
            "Structured CURRENT provenance contradicts the declared report epoch",
        )
    return True, None, reason


def _identity_is_accepted(
    report: dict[str, Any],
    expected_question_id: str,
    accepted_question_ids: Iterable[str],
) -> bool:
    if not expected_question_id:
        return True
    report_id = str(report.get("question_id", "")).strip().upper()
    accepted = {expected_question_id.upper()}
    accepted.update(str(value).upper() for value in accepted_question_ids)
    return bool(report_id) and report_id in accepted


def resolve_report_validity(
    report_filename: str,
    report_data: dict[str, Any] | None,
    *,
    expected_question_id: str = "",
    accepted_question_ids: Iterable[str] = (),
) -> tuple[ReportValidity, str]:
    # Report ownership is resolved before any artifact content is trusted.
    # An adjudicated artifact may only ever satisfy its canonical owner, and
    # artifact metadata may never silently contradict that owner.
    ownership = resolve_report_ownership(
        report_filename,
        expected_question_id,
        report_metadata=report_data,
    )
    if not ownership.allowed:
        if ownership.kind == OWNERSHIP_METADATA_CONFLICT:
            return ReportValidity.INVALIDATED, ownership.reason
        return ReportValidity.MISSING, ownership.reason
    if report_data is None:
        return ReportValidity.MISSING, "Report file not found or unreadable"
    filename = Path(report_filename).name.lower()
    fingerprint = report_data.get("fingerprint", {})
    dataset_id = str(fingerprint.get("dataset_id", "")) if isinstance(fingerprint, dict) else ""
    if dataset_id in _KNOWN_INVALIDATED_DATASETS.get(filename, frozenset()):
        label = filename.split("_", 1)[0].upper()
        return ReportValidity.INVALIDATED, f"Known invalid historical {label} evidence ({dataset_id})"
    status = _extract_status(report_data)
    if status == "INVALIDATED":
        return ReportValidity.INVALIDATED, "Report explicitly marked INVALIDATED"
    if not _identity_is_accepted(report_data, expected_question_id, accepted_question_ids):
        actual = str(report_data.get("question_id", "") or "MISSING")
        return (
            ReportValidity.INVALIDATED,
            f"Report identity {actual!r} does not match canonical question "
            f"{expected_question_id!r} or its declared legacy IDs",
        )
    if isinstance(fingerprint, dict):
        invalidation = is_report_invalidated(filename, fingerprint)
        if invalidation is not None:
            return ReportValidity.INVALIDATED, invalidation.reason
    has_structured, structured_validity, structured_reason = (
        _resolve_structured_provenance(report_data)
    )
    if has_structured and structured_validity is not None:
        return structured_validity, structured_reason
    epoch = _extract_epoch(report_data)
    if not epoch:
        return ReportValidity.LEGACY, "No evidence epoch; pre-CURRENT report"
    if epoch not in _CURRENT_EPOCHS:
        return ReportValidity.STALE, f"Evidence epoch {epoch!r} is not CURRENT"
    if status not in _CURRENT_REPORT_STATUSES:
        return ReportValidity.UNKNOWN, f"Unrecognised current report status {status!r}"
    if status in _AUTHORITATIVE_RESULT_STATUSES:
        assessment = validate_experiment_report(report_data)
        if assessment.gates_failed:
            failed = ", ".join(gate.gate_name for gate in assessment.gates_failed)
            return (
                ReportValidity.INVALIDATED,
                f"CURRENT report failed blocking validity gates: {failed}",
            )
    return ReportValidity.VALID_CURRENT, f"CURRENT artifact with status {status}"


def load_report_for_question(
    question_id: str,
    report_filename: str,
    *,
    reports_dir: str | Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    # Ownership gate: an adjudicated artifact may only be loaded by its owner.
    ownership = resolve_report_ownership(report_filename, question_id)
    if not ownership.allowed:
        return None, ""
    root = Path(reports_dir) if reports_dir is not None else _REPORTS_DIR
    if not report_filename or not report_filename.strip():
        return None, ""
    declared = Path(report_filename.strip())
    path = declared if declared.is_absolute() else root / declared.name
    return _load_report(path), str(path)


def report_contains_authoritative_result(
    report: dict[str, Any] | None,
    validity: ReportValidity,
) -> bool:
    return bool(
        report
        and validity == ReportValidity.VALID_CURRENT
        and _extract_status(report) in _AUTHORITATIVE_RESULT_STATUSES
    )
