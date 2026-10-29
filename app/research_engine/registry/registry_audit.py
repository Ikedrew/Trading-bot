"""
Registry Audit — Computes research question status from dataset validation.

Connects the Research Registry v2 to the Dataset Validator.
For each question, evaluates whether current data meets its requirements.

Usage:
    from research_engine.registry.registry_audit import audit_registry

    results = audit_registry()  # Loads data, validates, returns per-question status
    for r in results:
        print(f"{r.question_id}: {r.status.value} — {r.reason}")
"""

from __future__ import annotations

from typing import Any

from research_engine.data_access.s3_source import get_default_source
from research_engine.registry.research_question_models import (
    QuestionAuditResult,
    QuestionStatus,
    ResearchQuestion,
)
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.validation import validate_dataset, ResearchValidationResult


# ─── DATA LOADING ─────────────────────────────────────────────────────────────

_TRACE_DATASET = "decision_trace"


def _load_jsonl(dataset: str) -> list[dict]:
    """Read a production dataset from S3 via the shared data-access layer."""
    return get_default_source().read_dataset(dataset)


def _load_shadows() -> list[dict]:
    """Canonical production shadow population via the shadow_runtime ingestion layer.

    Reads the S3 shadow_runtime_v1 event stream and returns completed shadow
    outcomes in the internal research shape, plus the separate live-written
    research_shadow_trades dataset. No legacy dataset, no local fallback.
    """
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    records: list[dict] = list(ingest_completed_shadow_trades())
    records.extend(_load_jsonl("research_shadow_trades"))
    return records


# ─── COVERAGE MAPPING ─────────────────────────────────────────────────────────

def _get_coverage_value(validation: ResearchValidationResult, field: str) -> float:
    """
    Map a validation rule field name to the actual coverage value from validation result.

    Supports:
        h4_regime_coverage, h1_bias_coverage, market_phase_coverage,
        pattern_coverage, outcome_coverage, lineage_coverage,
        strategy_coverage, horizon_coverage
    """
    mapping = {
        "h4_regime_coverage": validation.h4_regime_coverage.coverage_pct,
        "h1_bias_coverage": validation.h1_bias_coverage.coverage_pct,
        "market_phase_coverage": validation.market_phase_coverage.coverage_pct,
        "pattern_coverage": validation.pattern_coverage.coverage_pct,
        "outcome_coverage": validation.outcome_coverage.coverage_pct,
        "lineage_coverage": validation.lineage_coverage.coverage_pct,
        "strategy_coverage": validation.strategy_coverage.coverage_pct,
        "horizon_coverage": validation.horizon_coverage.coverage_pct,
    }
    return mapping.get(field, 0.0)


# ─── QUESTION EVALUATION ──────────────────────────────────────────────────────

def _evaluate_question(
    question: ResearchQuestion,
    shadow_validation: ResearchValidationResult,
    trace_validation: ResearchValidationResult,
    min_sample_size: int = 20,
) -> QuestionAuditResult:
    """
    Evaluate a single research question against current dataset validation.

    Returns QuestionAuditResult with computed status.
    """
    # Use shadow validation as primary (has outcomes)
    # Use trace validation for decision-context fields
    primary = shadow_validation

    # Check minimum sample size
    if primary.total_records < min_sample_size:
        return QuestionAuditResult(
            question_id=question.id,
            title=question.title,
            category=question.category.value,
            priority=question.priority.value,
            status=QuestionStatus.WAITING_DATA,
            reason=f"Insufficient data: {primary.total_records} records (need {min_sample_size})",
        )

    # Evaluate validation rules
    failed_rules: list[str] = []
    coverage_snapshot: dict[str, float] = {}

    for rule in question.validation_rules:
        # Try shadow validation first, then trace
        value = _get_coverage_value(primary, rule.field)
        if value == 0.0:
            value = _get_coverage_value(trace_validation, rule.field)

        coverage_snapshot[rule.field] = round(value, 4)

        if not rule.evaluate(value):
            failed_rules.append(
                f"{rule.field}: {value:.1%} (need {rule.operator} {rule.threshold:.0%}) — {rule.description}"
            )

    # Determine status
    if not failed_rules:
        status = QuestionStatus.READY
        reason = "All validation rules pass"
    elif all("coverage" in r and "0.0%" in r for r in failed_rules):
        # All failures are zero-coverage — data not yet collected
        status = QuestionStatus.WAITING_DATA
        reason = f"{len(failed_rules)} field(s) have no data yet"
    else:
        status = QuestionStatus.BLOCKED
        reason = f"{len(failed_rules)} validation rule(s) failed"

    return QuestionAuditResult(
        question_id=question.id,
        title=question.title,
        category=question.category.value,
        priority=question.priority.value,
        status=status,
        reason=reason,
        failed_rules=tuple(failed_rules),
        coverage_snapshot=coverage_snapshot,
    )


# ─── MAIN AUDIT ──────────────────────────────────────────────────────────────

def audit_registry(
    shadow_records: list[dict] | None = None,
    trace_records: list[dict] | None = None,
) -> list[QuestionAuditResult]:
    """
    Audit all registered research questions against current data.

    Loads data if not provided. Returns ordered list of audit results.

    Args:
        shadow_records: Optional pre-loaded shadow trades (for testing)
        trace_records: Optional pre-loaded decision traces (for testing)

    Returns:
        List of QuestionAuditResult, one per registered question.
    """
    # Load data if not provided
    if shadow_records is None:
        shadow_records = _load_shadows()
    if trace_records is None:
        trace_records = _load_jsonl(_TRACE_DATASET)

    # Validate datasets
    shadow_validation = validate_dataset(shadow_records, dataset_name="shadow_trades_combined")
    trace_validation = validate_dataset(trace_records, dataset_name="decision_trace")

    # Evaluate each question
    results: list[QuestionAuditResult] = []
    for question in REGISTRY:
        result = _evaluate_question(question, shadow_validation, trace_validation)
        results.append(result)

    return results


def print_audit_report(results: list[QuestionAuditResult] | None = None) -> None:
    """Print a formatted audit report to stdout."""
    if results is None:
        results = audit_registry()

    # Group by status
    by_status: dict[str, list[QuestionAuditResult]] = {}
    for r in results:
        by_status.setdefault(r.status.value, []).append(r)

    print("=" * 70)
    print("RESEARCH REGISTRY v2 — STATUS AUDIT")
    print("=" * 70)
    print()

    # Summary counts
    for status in ("READY", "WAITING_DATA", "BLOCKED", "COMPLETE", "INVALIDATED"):
        count = len(by_status.get(status, []))
        print(f"  {status:15s}: {count}")
    print()

    # Detail table
    print(f"{'ID':<5} {'Title':<35} {'Priority':<4} {'Status':<14} {'Reason'}")
    print("-" * 100)
    for r in results:
        title = r.title[:33] + ".." if len(r.title) > 35 else r.title
        reason = r.reason[:40] + ".." if len(r.reason) > 42 else r.reason
        print(f"{r.question_id:<5} {title:<35} {r.priority:<4} {r.status.value:<14} {reason}")

    print()


if __name__ == "__main__":
    print_audit_report()
