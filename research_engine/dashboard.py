"""
Research Readiness Dashboard — Single entry point for research system status.

Answers: "Can I trust the research I am about to run?"

Computes:
    - Data coverage per field
    - Lineage status (decision → outcome join quality)
    - Per-question readiness (READY / WAITING_DATA / BLOCKED)
    - Overall readiness score (0–100)
    - Critical blockers list
    - Experiment execution gates

Usage:
    from research_engine.dashboard import generate_dashboard, print_dashboard, can_execute

    dashboard = generate_dashboard()
    print_dashboard(dashboard)

    if can_execute("E1", dashboard):
        run_experiment_e1()

CLI:
    python -m research_engine.dashboard
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research_engine.data_access.s3_source import get_default_source
from research_engine.registry.registry_audit import audit_registry
from research_engine.registry.research_question_models import (
    QuestionAuditResult,
    QuestionStatus,
)
from research_engine.registry.research_question_registry import REGISTRY_BY_ID
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


# ─── DASHBOARD MODEL ──────────────────────────────────────────────────────────

@dataclass
class CoverageEntry:
    """One field's coverage status."""
    field: str
    coverage_pct: float
    status: str  # "ready", "usable", "collecting", "insufficient"
    threshold: float = 0.80

    @property
    def icon(self) -> str:
        if self.coverage_pct >= self.threshold:
            return "✅"
        if self.coverage_pct >= 0.50:
            return "⚠️"
        if self.coverage_pct > 0.0:
            return "❌"
        return "❌"


@dataclass
class ResearchDashboard:
    """Complete research system status snapshot."""

    # ─── DATASET IDENTITY ─────────────────────────────────────────────
    dataset_name: str
    total_records: int
    source: str
    generated_at: str

    # ─── COVERAGE ─────────────────────────────────────────────────────
    coverage: list[CoverageEntry] = field(default_factory=list)

    # ─── LINEAGE ──────────────────────────────────────────────────────
    lineage_coverage_pct: float = 0.0
    lineage_status: str = "UNKNOWN"
    lineage_reason: str = ""

    # ─── QUESTION STATUS ──────────────────────────────────────────────
    questions_ready: list[QuestionAuditResult] = field(default_factory=list)
    questions_waiting: list[QuestionAuditResult] = field(default_factory=list)
    questions_blocked: list[QuestionAuditResult] = field(default_factory=list)

    # ─── READINESS SCORE ──────────────────────────────────────────────
    readiness_score: int = 0  # 0–100
    readiness_status: str = "NOT_READY"  # READY / PARTIAL / NOT_READY

    # ─── BLOCKERS ─────────────────────────────────────────────────────
    critical_blockers: list[str] = field(default_factory=list)

    # ─── STRATEGY VALIDATION ──────────────────────────────────────────
    strategy_contaminated: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence or API output."""
        return {
            "dataset_name": self.dataset_name,
            "total_records": self.total_records,
            "source": self.source,
            "generated_at": self.generated_at,
            "coverage": {e.field: {"pct": round(e.coverage_pct, 4), "status": e.status} for e in self.coverage},
            "lineage": {"coverage_pct": round(self.lineage_coverage_pct, 4), "status": self.lineage_status, "reason": self.lineage_reason},
            "questions": {"ready": len(self.questions_ready), "waiting": len(self.questions_waiting), "blocked": len(self.questions_blocked)},
            "readiness_score": self.readiness_score,
            "readiness_status": self.readiness_status,
            "critical_blockers": self.critical_blockers,
            "strategy_contaminated": self.strategy_contaminated,
        }


# ─── READINESS SCORE COMPUTATION ──────────────────────────────────────────────

def _compute_readiness_score(validation: ResearchValidationResult) -> int:
    """
    Compute overall readiness score (0–100) from coverage metrics.

    Weights:
        Outcome:         25 points (most critical — no outcomes = no research)
        Lineage:         20 points (joins enable context analysis)
        Pattern:         15 points (pattern research is primary)
        Strategy:        15 points (strategy separation)
        H4 regime:       10 points (HTF context)
        Market phase:    10 points (lifecycle research)
        Horizon:          5 points (horizon-specific analysis)
    """
    score = 0.0
    score += min(25.0, validation.outcome_coverage.coverage_pct * 25.0)
    score += min(20.0, validation.lineage_coverage.coverage_pct * 20.0)
    score += min(15.0, validation.pattern_coverage.coverage_pct * 15.0)
    score += min(15.0, validation.strategy_coverage.coverage_pct * 15.0)
    score += min(10.0, validation.h4_regime_coverage.coverage_pct * 10.0)
    score += min(10.0, validation.market_phase_coverage.coverage_pct * 10.0)
    score += min(5.0, validation.horizon_coverage.coverage_pct * 5.0)
    return min(100, max(0, int(round(score))))


def _classify_readiness(score: int) -> str:
    """Classify overall readiness from score."""
    if score >= 80:
        return "READY"
    if score >= 50:
        return "PARTIAL"
    return "NOT_READY"


def _field_status(pct: float, threshold: float = 0.80) -> str:
    """Classify a single field's readiness."""
    if pct >= threshold:
        return "ready"
    if pct >= 0.50:
        return "usable"
    if pct > 0.0:
        return "collecting"
    return "insufficient"


# ─── CRITICAL BLOCKERS ────────────────────────────────────────────────────────

def _identify_blockers(validation: ResearchValidationResult) -> list[str]:
    """Identify critical research blockers in priority order."""
    blockers: list[str] = []

    if validation.lineage_coverage.coverage_pct < 0.80:
        blockers.append(
            f"Lineage coverage {validation.lineage_coverage.coverage_pct:.0%} — "
            f"collect new shadow trades with entity_id (target: 80%)"
        )

    if validation.h4_regime_coverage.coverage_pct < 0.80:
        blockers.append(
            f"H4 regime coverage {validation.h4_regime_coverage.coverage_pct:.0%} — "
            f"run on live feed (not replay) to populate HTF context"
        )

    if validation.strategy_coverage.coverage_pct < 0.50:
        blockers.append(
            f"Strategy coverage {validation.strategy_coverage.coverage_pct:.0%} — "
            f"new records will have clean values (wiring deployed)"
        )

    if validation.market_phase_coverage.coverage_pct < 0.80:
        blockers.append(
            f"Market phase coverage {validation.market_phase_coverage.coverage_pct:.0%} — "
            f"phase wired, needs 2-4 weeks collection"
        )

    if validation.horizon_coverage.coverage_pct < 0.50:
        blockers.append(
            f"Horizon coverage {validation.horizon_coverage.coverage_pct:.0%} — "
            f"horizon separation deployed, needs collection"
        )

    if validation.strategy_contaminated > 0:
        blockers.append(
            f"Strategy contamination: {validation.strategy_contaminated} records "
            f"have combined strategy_horizon format"
        )

    return blockers


# ─── DASHBOARD GENERATION ─────────────────────────────────────────────────────

def generate_dashboard(
    shadow_records: list[dict] | None = None,
    trace_records: list[dict] | None = None,
) -> ResearchDashboard:
    """
    Generate a complete research readiness dashboard.

    Loads data if not provided. Validates, audits questions, computes score.

    Args:
        shadow_records: Optional pre-loaded shadow trades (for testing)
        trace_records: Optional pre-loaded decision traces (for testing)

    Returns:
        ResearchDashboard with all metrics computed.
    """
    # Load data
    if shadow_records is None:
        shadow_records = _load_shadows()
    if trace_records is None:
        trace_records = _load_jsonl(_TRACE_DATASET)

    # Validate
    sv = validate_dataset(shadow_records, dataset_name="shadow_trades_combined")

    # Audit questions
    question_results = audit_registry(shadow_records=shadow_records, trace_records=trace_records)

    # Classify questions by status
    ready = [r for r in question_results if r.status == QuestionStatus.READY]
    waiting = [r for r in question_results if r.status == QuestionStatus.WAITING_DATA]
    blocked = [r for r in question_results if r.status == QuestionStatus.BLOCKED]

    # Build coverage entries
    coverage = [
        CoverageEntry("entity_id", sv.lineage_coverage.coverage_pct, _field_status(sv.lineage_coverage.coverage_pct)),
        CoverageEntry("outcome", sv.outcome_coverage.coverage_pct, _field_status(sv.outcome_coverage.coverage_pct)),
        CoverageEntry("pattern", sv.pattern_coverage.coverage_pct, _field_status(sv.pattern_coverage.coverage_pct)),
        CoverageEntry("strategy", sv.strategy_coverage.coverage_pct, _field_status(sv.strategy_coverage.coverage_pct, 0.50)),
        CoverageEntry("trade_horizon", sv.horizon_coverage.coverage_pct, _field_status(sv.horizon_coverage.coverage_pct, 0.50)),
        CoverageEntry("h4_regime", sv.h4_regime_coverage.coverage_pct, _field_status(sv.h4_regime_coverage.coverage_pct)),
        CoverageEntry("h1_bias", sv.h1_bias_coverage.coverage_pct, _field_status(sv.h1_bias_coverage.coverage_pct)),
        CoverageEntry("market_phase", sv.market_phase_coverage.coverage_pct, _field_status(sv.market_phase_coverage.coverage_pct)),
    ]

    # Lineage status
    lin_pct = sv.lineage_coverage.coverage_pct
    if lin_pct >= 0.80:
        lin_status = "READY"
        lin_reason = "Entity_id coverage sufficient for research joins"
    elif lin_pct >= 0.50:
        lin_status = "WARNING"
        lin_reason = "Partial lineage — some joins possible but incomplete"
    else:
        lin_status = "BLOCKED"
        lin_reason = "Historical trades created before lineage wiring"

    # Readiness score
    score = _compute_readiness_score(sv)
    status = _classify_readiness(score)

    # Blockers
    blockers = _identify_blockers(sv)

    return ResearchDashboard(
        dataset_name=sv.dataset_name,
        total_records=sv.total_records,
        source=sv.source.value,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        coverage=coverage,
        lineage_coverage_pct=lin_pct,
        lineage_status=lin_status,
        lineage_reason=lin_reason,
        questions_ready=ready,
        questions_waiting=waiting,
        questions_blocked=blocked,
        readiness_score=score,
        readiness_status=status,
        critical_blockers=blockers,
        strategy_contaminated=sv.strategy_contaminated,
    )


# ─── EXECUTION GATE ──────────────────────────────────────────────────────────

def can_execute(question_id: str, dashboard: ResearchDashboard | None = None) -> bool:
    """
    Check whether a specific research question can be safely executed.

    Returns True only if the question's status is READY.
    Generates dashboard if not provided.

    Args:
        question_id: Registry question ID (e.g. "E1", "M4")
        dashboard: Optional pre-computed dashboard

    Returns:
        True if the question is READY for execution.
    """
    if dashboard is None:
        dashboard = generate_dashboard()

    # Check if question is in the READY list
    for r in dashboard.questions_ready:
        if r.question_id == question_id:
            return True
    return False


def get_execution_gate(question_id: str, dashboard: ResearchDashboard | None = None) -> dict[str, Any]:
    """
    Get detailed execution gate information for a question.

    Returns a dict with:
        - allowed: bool
        - question_id: str
        - status: str
        - reason: str
        - readiness_score: int
        - required_readiness: int (80 for P0, 60 for P1, 40 for P2+)
    """
    if dashboard is None:
        dashboard = generate_dashboard()

    # Find the question in audit results
    all_results = dashboard.questions_ready + dashboard.questions_waiting + dashboard.questions_blocked
    result = next((r for r in all_results if r.question_id == question_id), None)

    if result is None:
        return {
            "allowed": False,
            "question_id": question_id,
            "status": "NOT_FOUND",
            "reason": f"Question {question_id} not in registry",
            "readiness_score": dashboard.readiness_score,
            "required_readiness": 80,
        }

    # Determine required readiness based on priority
    question_def = REGISTRY_BY_ID.get(question_id)
    if question_def and question_def.priority.value == "P0":
        required = 80
    elif question_def and question_def.priority.value == "P1":
        required = 60
    else:
        required = 40

    allowed = result.status == QuestionStatus.READY

    return {
        "allowed": allowed,
        "question_id": question_id,
        "status": result.status.value,
        "reason": result.reason,
        "readiness_score": dashboard.readiness_score,
        "required_readiness": required,
        "failed_rules": list(result.failed_rules),
    }


# ─── CLI OUTPUT ───────────────────────────────────────────────────────────────

def print_dashboard(dashboard: ResearchDashboard | None = None) -> None:
    """Print formatted research readiness dashboard to stdout."""
    if dashboard is None:
        dashboard = generate_dashboard()

    print("=" * 60)
    print("RESEARCH SYSTEM STATUS")
    print("=" * 60)
    print()
    print(f"  Dataset:    {dashboard.dataset_name}")
    print(f"  Records:    {dashboard.total_records}")
    print(f"  Source:     {dashboard.source}")
    print(f"  Generated:  {dashboard.generated_at}")
    print()

    # Coverage
    print("-" * 60)
    print("DATA COVERAGE")
    print("-" * 60)
    print(f"  {'Field':<20} {'Coverage':<12} {'Status'}")
    for c in dashboard.coverage:
        pct_str = f"{c.coverage_pct:.1%}"
        print(f"  {c.field:<20} {pct_str:<12} {c.icon} {c.status}")
    print()

    # Lineage
    print("-" * 60)
    print("LINEAGE STATUS")
    print("-" * 60)
    print(f"  Coverage:   {dashboard.lineage_coverage_pct:.1%}")
    print(f"  Status:     {dashboard.lineage_status}")
    print(f"  Reason:     {dashboard.lineage_reason}")
    print()

    # Strategy contamination
    if dashboard.strategy_contaminated > 0:
        print(f"  ⚠️  Strategy contamination: {dashboard.strategy_contaminated} records")
    print()

    # Readiness score
    print("-" * 60)
    print("READINESS SCORE")
    print("-" * 60)
    print(f"  Score:      {dashboard.readiness_score} / 100")
    print(f"  Status:     {dashboard.readiness_status}")
    print()

    # Question status
    print("-" * 60)
    print("RESEARCH QUESTION STATUS")
    print("-" * 60)
    print(f"  READY:         {len(dashboard.questions_ready)}")
    print(f"  WAITING_DATA:  {len(dashboard.questions_waiting)}")
    print(f"  BLOCKED:       {len(dashboard.questions_blocked)}")
    print()

    # Ready questions
    if dashboard.questions_ready:
        print("  READY:")
        for q in dashboard.questions_ready:
            print(f"    {q.question_id:<5} {q.title}")
    print()

    # Blocked P0 questions
    blocked_p0 = [q for q in dashboard.questions_blocked if q.priority == "P0"]
    if blocked_p0:
        print("  BLOCKED (P0 — critical):")
        for q in blocked_p0:
            print(f"    {q.question_id:<5} {q.title}")
            if q.failed_rules:
                print(f"          {q.failed_rules[0][:70]}")
    print()

    # Critical blockers
    if dashboard.critical_blockers:
        print("-" * 60)
        print("CRITICAL BLOCKERS")
        print("-" * 60)
        for i, b in enumerate(dashboard.critical_blockers, 1):
            print(f"  {i}. {b}")
        print()

    # Final verdict
    print("=" * 60)
    if dashboard.readiness_status == "READY":
        print("RESEARCH READINESS: READY FOR FULL ANALYSIS")
    elif dashboard.readiness_status == "PARTIAL":
        print("RESEARCH READINESS: PARTIAL — some questions executable")
    else:
        print("RESEARCH READINESS: NOT READY FOR FULL ANALYSIS")
    print("=" * 60)
    print()

    # Next action
    if dashboard.readiness_status != "READY":
        print("Next recommended action:")
        if dashboard.critical_blockers:
            print(f"  {dashboard.critical_blockers[0]}")
        else:
            print("  Collect more data with current wiring active.")
    print()


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print_dashboard()
