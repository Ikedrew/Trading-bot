"""Read-only history resolution for canonical research reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.control_plane.models import (
    CanonicalReportRecord,
    QuestionState,
    ReportValidity,
)
from research_engine.control_plane.report_ownership import resolve_report_ownership
from research_engine.control_plane.report_resolver import (
    _extract_confidence,
    _extract_epoch,
    _extract_finding,
    _extract_recommendation,
    _extract_sample_size,
    _extract_status,
    _extract_timestamp,
    load_report_for_question,
    resolve_report_validity,
)

_AUTHORITATIVE_STATUSES = frozenset({"COMPLETE"})


def _parse_timestamp(report: dict[str, Any]) -> str:
    ts = _extract_timestamp(report)
    if ts:
        return ts
    return report.get("generated", "")


def _parse_fingerprint(report: dict[str, Any]) -> dict[str, Any]:
    fp = report.get("fingerprint", {})
    if isinstance(fp, dict):
        return fp
    return {}


def _parse_source_question_id(report: dict[str, Any], canonical_id: str) -> str:
    qid = report.get("question_id", "")
    if qid and isinstance(qid, str):
        return qid
    return canonical_id


def _classify_report(
    report: dict[str, Any],
    canonical_question_id: str,
    report_filename: str,
    known_invalidations: dict[str, set[str]],
    accepted_question_ids: Iterable[str] = (),
) -> CanonicalReportRecord:
    filename = Path(report_filename).name
    validity, reason = resolve_report_validity(
        report_filename,
        report,
        expected_question_id=canonical_question_id,
        accepted_question_ids=accepted_question_ids,
    )
    if validity == ReportValidity.VALID_CURRENT:
        fingerprint = _parse_fingerprint(report)
        dataset_id = fingerprint.get("dataset_id", "")
        if dataset_id in known_invalidations.get(filename.lower(), set()):
            validity = ReportValidity.INVALIDATED
            reason = f"Explicitly invalidated via invalidation ledger: {dataset_id}"
    sample_size = _extract_sample_size(report)
    finding = _extract_finding(report) if validity == ReportValidity.VALID_CURRENT else ""
    confidence = _extract_confidence(report)
    recommendation = _extract_recommendation(report)
    epoch = _extract_epoch(report)
    status = _extract_status(report)
    generated_at = _parse_timestamp(report)
    source_qid = _parse_source_question_id(report, canonical_question_id)
    return CanonicalReportRecord(
        canonical_question_id=canonical_question_id,
        report_path=filename,
        source_question_id=source_qid,
        generated_at=generated_at,
        evidence_epoch=epoch,
        report_status=status,
        report_validity=validity,
        validity_reason=reason,
        sample_size=sample_size,
        finding=finding,
        confidence=confidence,
        recommendation=recommendation,
        evidence_fingerprint=_parse_fingerprint(report),
        superseded_by=None,
        invalidation_reason=None,
        is_authoritative=False,
    )


def _report_belongs_to_question(
    report_data: dict[str, Any] | None,
    report_path: Path,
    canonical_question_id: str,
    legacy_ids: tuple[str, ...] | list[str],
    expected_filename: str,
) -> bool:
    """Check if a report artifact belongs to this canonical question.

    A report belongs if:
    - The artifact's adjudicated canonical owner is this question (fail closed
      for every other question), AND
    - Its question_id matches the canonical ID, OR
    - Its question_id matches a declared legacy alias, OR
    - Its filename matches the expected report filename for this question
      (for reports without question_id field)
    """
    if not resolve_report_ownership(
        report_path.name, canonical_question_id, report_metadata=report_data
    ).allowed:
        # Adjudicated artifact owned by another canonical question: legacy
        # identity compatibility may never transfer ownership or completion.
        return False
    if report_data is None:
        return False

    # Check by question_id identity
    report_qid = str(report_data.get("question_id", "")).strip().upper()
    if report_qid:
        accepted_ids = {canonical_question_id.upper()} | {qid.upper() for qid in legacy_ids}
        return report_qid in accepted_ids

    # No question_id: check by filename match
    if expected_filename and report_path.name.lower() == expected_filename.lower():
        return True

    return False


def resolve_report_history(
    canonical_question_id: str,
    report_filename: str,
    reports_dir: Path,
    known_invalidations: dict[str, set[str]] | None = None,
) -> QuestionState:
    if known_invalidations is None:
        known_invalidations = {}
    from research_engine.registry.research_question_registry import REGISTRY
    by_id = {q.id: q for q in REGISTRY}
    question = by_id.get(canonical_question_id.upper())
    if question is None:
        raise KeyError(f"Unknown canonical question: {canonical_question_id}")
    accepted_ids = list(question.legacy_ids) if question else []
    legacy_ids = tuple(question.legacy_ids) if question else ()
    expected_filename = report_filename.strip() if report_filename else ""
    all_reports: list[CanonicalReportRecord] = []
    target_filename = report_filename.strip() if report_filename else ""
    if reports_dir.is_dir():
        for path in sorted(reports_dir.glob("*.json")):
            report_data = _load_report_safely(path)
            # Identity routing: only include reports that belong to this question
            if not _report_belongs_to_question(
                report_data, path, canonical_question_id, legacy_ids, expected_filename
            ):
                continue
            if report_data is None:
                # File exists but could not be parsed - include as UNKNOWN
                # only if filename matches expected (identity by filename)
                if not (expected_filename and path.name.lower() == expected_filename.lower()):
                    continue
                all_reports.append(
                    CanonicalReportRecord(
                        canonical_question_id=canonical_question_id,
                        report_path=str(path.name),
                        source_question_id="",
                        generated_at="",
                        evidence_epoch="",
                        report_status="",
                        report_validity=ReportValidity.UNKNOWN,
                        validity_reason="Report file exists but could not be parsed",
                        sample_size=None,
                        finding="",
                        confidence="",
                        recommendation="",
                        evidence_fingerprint={},
                        superseded_by=None,
                        invalidation_reason=None,
                        is_authoritative=False,
                    )
                )
                continue
            record = _classify_report(
                report_data,
                canonical_question_id,
                path.name,
                known_invalidations,
                accepted_ids,
            )
            all_reports.append(record)
    if target_filename:
        report_data, _ = load_report_for_question(
            canonical_question_id,
            target_filename,
            reports_dir=reports_dir,
        )
        if report_data is not None:
            existing_paths = {r.report_path for r in all_reports}
            if target_filename not in existing_paths:
                record = _classify_report(
                    report_data,
                    canonical_question_id,
                    target_filename,
                    known_invalidations,
                    accepted_ids,
                )
                all_reports.append(record)
    authoritative: CanonicalReportRecord | None = None
    superseded_ids: set[str] = set()
    valid_current_reports = [
        r for r in all_reports
        if r.report_validity == ReportValidity.VALID_CURRENT
        and r.report_status in _AUTHORITATIVE_STATUSES
    ]
    if valid_current_reports:
        valid_current_reports.sort(
            key=lambda r: r.generated_at or "",
            reverse=True,
        )
        if len(valid_current_reports) == 1:
            authoritative = valid_current_reports[0]
            authoritative.is_authoritative = True
        elif len(valid_current_reports) > 1:
            newest = valid_current_reports[0]
            second_newest = valid_current_reports[1]
            if newest.generated_at and second_newest.generated_at:
                if newest.generated_at != second_newest.generated_at:
                    authoritative = newest
                    authoritative.is_authoritative = True
                    for r in valid_current_reports[1:]:
                        r.report_validity = ReportValidity.SUPERSEDED
                        r.validity_reason = (
                            f"Superseded by {newest.report_path} "
                            f"(newer VALID_CURRENT report from {newest.generated_at})"
                        )
                        r.superseded_by = newest.report_path
                        superseded_ids.add(r.report_path)
                else:
                    raise AmbiguousReportError(
                        canonical_question_id,
                        [r.report_path for r in valid_current_reports],
                        "Multiple VALID_CURRENT reports with identical timestamps cannot be safely ordered",
                    )
            else:
                raise AmbiguousReportError(
                    canonical_question_id,
                    [r.report_path for r in valid_current_reports],
                    "Multiple VALID_CURRENT reports with unorderable timestamps",
                )
    invalidated_count = sum(1 for r in all_reports if r.report_validity == ReportValidity.INVALIDATED)
    stale_count = sum(1 for r in all_reports if r.report_validity == ReportValidity.STALE)
    legacy_count = sum(1 for r in all_reports if r.report_validity == ReportValidity.LEGACY)
    superseded_count = sum(1 for r in all_reports if r.report_validity == ReportValidity.SUPERSEDED)
    malformed_count = sum(
        1 for r in all_reports
        if r.report_validity in (ReportValidity.UNKNOWN, ReportValidity.MISSING)
    )
    latest_timestamp = ""
    for r in all_reports:
        if r.is_authoritative and r.generated_at:
            latest_timestamp = r.generated_at
            break
    return QuestionState(
        question_id=canonical_question_id,
        title="",
        description="",
        category="",
        report_history=all_reports,
        report_history_count=len(all_reports),
        invalidated_report_count=invalidated_count,
        stale_report_count=stale_count,
        superseded_report_count=superseded_count,
        legacy_report_count=legacy_count,
        malformed_report_count=malformed_count,
        latest_valid_run_timestamp=latest_timestamp,
        authoritative_report=authoritative,
    )


def _load_report_safely(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


class AmbiguousReportError(ValueError):
    def __init__(self, question_id: str, report_paths: list[str], message: str):
        self.question_id = question_id
        self.report_paths = report_paths
        super().__init__(message)
