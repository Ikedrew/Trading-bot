"""
Research Command Centre — Data Models (Phase 2 + Traceability).

Structured output models for the unified 11-section command centre report.
Pure data definitions. No trading logic. No experiment calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: SYSTEM STATE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SystemState:
    """Top-level system readiness."""
    infrastructure: str         # READY / NOT_READY
    data_collection: str        # READY / COLLECTING / BLOCKED
    strategy_evaluation: str    # READY / WAITING_DATA
    promotion_decisions: str    # READY / INSUFFICIENT_DATA


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: DATA HEALTH
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CoverageField:
    """Coverage measurement for a single field."""
    name: str
    pct: float          # 0.0 - 1.0
    status: str         # OK / LOW / MISSING


@dataclass
class DataHealth:
    """Health assessment of the research dataset."""
    record_count: int
    source: str
    outcome_coverage: CoverageField
    lineage_coverage: CoverageField
    context_fields: list[CoverageField] = field(default_factory=list)
    contamination_count: int = 0
    dataset_verdict: str = ""  # e.g. "NOT READY FOR FULL RESEARCH"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: RESEARCH READINESS (summary counts)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchReadiness:
    """Aggregate question readiness counts."""
    total_questions: int = 0
    complete: int = 0
    ready: int = 0
    waiting_data: int = 0
    blocked: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: ACTIVE QUESTIONS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class QuestionEntry:
    """A single research question with its status."""
    question_id: str
    title: str
    priority: str           # P0 / P1 / P2 / P3
    status: str             # COMPLETE / READY / WAITING_DATA / BLOCKED
    recommendation: str = ""
    blocker: str = ""
    sample_size: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: CONFIRMED FINDINGS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PatternFinding:
    """A confirmed finding about a specific pattern or strategy."""
    name: str
    ev: float | None = None
    sample_count: int = 0
    confidence: str = ""        # HIGH / MEDIUM / LOW
    win_rate: float | None = None


@dataclass
class ConfirmedFindings:
    """All confirmed research findings."""
    findings: list[str] = field(default_factory=list)
    pattern_findings: list[PatternFinding] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: REJECTED HYPOTHESES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RejectedHypothesis:
    """A hypothesis that has been invalidated by evidence."""
    hypothesis: str
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: SYSTEM EDGE / EV
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EVBreakdownEntry:
    """EV breakdown for a pattern or strategy."""
    name: str
    ev: float
    trades: int = 0
    win_rate: float = 0.0


@dataclass
class SystemEdge:
    """Current system expected value status."""
    current_ev: float | None = None
    dataset_name: str = ""
    eligible_trades: int = 0
    confidence: str = ""            # HIGH / MEDIUM / LOW / INSUFFICIENT_DATA
    edge_classification: str = ""   # STRONG_EDGE / MARGINAL_EDGE / NO_EDGE / NEGATIVE_EDGE
    win_rate: float = 0.0
    profit_factor: float = 0.0
    ev_trend: str = ""
    best_patterns: list[EVBreakdownEntry] = field(default_factory=list)
    worst_patterns: list[EVBreakdownEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: ARCHITECTURE STATUS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ArchitectureAuthority:
    """A confirmed timeframe authority assignment."""
    timeframe: str      # H4 / H1 / M15 / M5
    responsibility: str
    confirmed: bool = True


@dataclass
class ArchitectureStatus:
    """Confirmed architecture ownership state."""
    authorities: list[ArchitectureAuthority] = field(default_factory=list)
    additional_facts: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9: BLOCKERS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Blocker:
    """A specific blocker preventing progress."""
    area: str
    description: str
    impact: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10: RECOMMENDED NEXT ACTION
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Recommendation:
    """The research system's recommended next action."""
    current_phase: str
    reason: str
    missing_items: list[str] = field(default_factory=list)
    required_action: str = ""
    do_not: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11: RESEARCH TRACEABILITY
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DatasetFingerprint:
    """Identifies the exact dataset used for a research result."""
    dataset_id: str = ""            # e.g. "shadow_trades_2026_07_27"
    records_used: int = 0
    records_excluded: int = 0
    validation_score: str = ""      # HIGH / MEDIUM / LOW / UNKNOWN


@dataclass
class QuestionProvenance:
    """Traceability chain for a single research question."""
    question_id: str
    question_title: str
    experiment_module: str = ""         # e.g. "experiments.expected_value"
    experiment_function: str = ""       # e.g. "run"
    expected_output_location: str = ""  # e.g. "analysis/reports/q19_expected_value.json"
    last_run_timestamp: str = ""        # ISO timestamp or ""
    status: str = ""                    # COMPLETE / READY / BLOCKED / MISSING_OUTPUT
    result_available: bool = False      # True if output file exists with data
    displayed_in_command_center: bool = False
    dataset_fingerprint: DatasetFingerprint | None = None
    warning: str = ""                   # e.g. "Marked COMPLETE but no output found"


@dataclass
class ResearchTraceability:
    """Section 11: Full provenance audit of all research questions."""
    questions: list[QuestionProvenance] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_complete: int = 0
    total_with_output: int = 0
    total_missing_output: int = 0
    total_stale: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL REPORT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchCommandReport:
    """
    Complete Research Command Centre report (Phase 2 + Traceability + Decision Gates).

    12 sections providing a unified view of the research system.
    This is REPORTING ONLY — does NOT influence trading decisions.
    """
    generated_at: str
    system_state: SystemState
    data_health: DataHealth
    research_readiness: ResearchReadiness
    active_questions: list[QuestionEntry]
    confirmed_findings: ConfirmedFindings
    rejected_hypotheses: list[RejectedHypothesis]
    system_edge: SystemEdge
    architecture_status: ArchitectureStatus
    blockers: list[Blocker]
    recommendation: Recommendation
    traceability: ResearchTraceability = field(default_factory=ResearchTraceability)
    decision_gates: Any = None  # DecisionGateReport (imported at runtime to avoid circular)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full report for JSON output."""
        return {
            "generated_at": self.generated_at,
            "system_state": {
                "infrastructure": self.system_state.infrastructure,
                "data_collection": self.system_state.data_collection,
                "strategy_evaluation": self.system_state.strategy_evaluation,
                "promotion_decisions": self.system_state.promotion_decisions,
            },
            "data_health": {
                "record_count": self.data_health.record_count,
                "source": self.data_health.source,
                "outcome_coverage": _cov_dict(self.data_health.outcome_coverage),
                "lineage_coverage": _cov_dict(self.data_health.lineage_coverage),
                "context_fields": [_cov_dict(f) for f in self.data_health.context_fields],
                "contamination_count": self.data_health.contamination_count,
                "dataset_verdict": self.data_health.dataset_verdict,
            },
            "research_readiness": {
                "total_questions": self.research_readiness.total_questions,
                "complete": self.research_readiness.complete,
                "ready": self.research_readiness.ready,
                "waiting_data": self.research_readiness.waiting_data,
                "blocked": self.research_readiness.blocked,
            },
            "active_questions": [
                {"id": q.question_id, "title": q.title, "priority": q.priority,
                 "status": q.status, "recommendation": q.recommendation,
                 "blocker": q.blocker, "sample_size": q.sample_size}
                for q in self.active_questions
            ],
            "confirmed_findings": {
                "findings": self.confirmed_findings.findings,
                "pattern_findings": [
                    {"name": p.name, "ev": p.ev, "n": p.sample_count,
                     "confidence": p.confidence, "win_rate": p.win_rate}
                    for p in self.confirmed_findings.pattern_findings
                ],
            },
            "rejected_hypotheses": [
                {"hypothesis": h.hypothesis, "reason": h.reason}
                for h in self.rejected_hypotheses
            ],
            "system_edge": {
                "current_ev": self.system_edge.current_ev,
                "dataset_name": self.system_edge.dataset_name,
                "eligible_trades": self.system_edge.eligible_trades,
                "confidence": self.system_edge.confidence,
                "edge_classification": self.system_edge.edge_classification,
                "win_rate": self.system_edge.win_rate,
                "profit_factor": self.system_edge.profit_factor,
                "ev_trend": self.system_edge.ev_trend,
                "best_patterns": [
                    {"name": e.name, "ev": e.ev, "trades": e.trades, "wr": e.win_rate}
                    for e in self.system_edge.best_patterns
                ],
                "worst_patterns": [
                    {"name": e.name, "ev": e.ev, "trades": e.trades, "wr": e.win_rate}
                    for e in self.system_edge.worst_patterns
                ],
                "warnings": self.system_edge.warnings,
            },
            "architecture_status": {
                "authorities": [
                    {"timeframe": a.timeframe, "responsibility": a.responsibility, "confirmed": a.confirmed}
                    for a in self.architecture_status.authorities
                ],
                "additional_facts": self.architecture_status.additional_facts,
            },
            "blockers": [
                {"area": b.area, "description": b.description, "impact": b.impact}
                for b in self.blockers
            ],
            "recommendation": {
                "current_phase": self.recommendation.current_phase,
                "reason": self.recommendation.reason,
                "missing_items": self.recommendation.missing_items,
                "required_action": self.recommendation.required_action,
                "do_not": self.recommendation.do_not,
            },
            "traceability": {
                "total_complete": self.traceability.total_complete,
                "total_with_output": self.traceability.total_with_output,
                "total_missing_output": self.traceability.total_missing_output,
                "total_stale": self.traceability.total_stale,
                "warnings": self.traceability.warnings,
                "questions": [
                    {
                        "question_id": q.question_id,
                        "question_title": q.question_title,
                        "experiment_module": q.experiment_module,
                        "output_location": q.expected_output_location,
                        "last_run": q.last_run_timestamp,
                        "status": q.status,
                        "result_available": q.result_available,
                        "displayed": q.displayed_in_command_center,
                        "dataset_fingerprint": {
                            "dataset_id": q.dataset_fingerprint.dataset_id,
                            "records_used": q.dataset_fingerprint.records_used,
                            "records_excluded": q.dataset_fingerprint.records_excluded,
                            "validation_score": q.dataset_fingerprint.validation_score,
                        } if q.dataset_fingerprint else None,
                        "warning": q.warning,
                    }
                    for q in self.traceability.questions
                ],
            },
            "decision_gates": self.decision_gates.to_dict() if self.decision_gates else None,
        }


def _cov_dict(c: CoverageField) -> dict[str, Any]:
    return {"name": c.name, "pct": round(c.pct * 100, 1), "status": c.status}
