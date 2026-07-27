"""
Research Question Models — Structured definitions for the v2 registry.

Each research question is a frozen dataclass with:
    - identity (id, category, title)
    - requirements (fields, validation rules, data sources)
    - status (computed from dataset validation, not hardcoded)

No imports from core pipeline. Pure data definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class QuestionCategory(str, Enum):
    """Research question domain categories."""
    SYSTEM_EDGE = "SYSTEM_EDGE"           # E: Overall system expectancy
    MARKET_CONTEXT = "MARKET_CONTEXT"     # M: Regime, phase, HTF interactions
    DECISION_QUALITY = "DECISION_QUALITY" # D: Scoring, calibration, thresholds
    STRATEGY_HORIZON = "STRATEGY_HORIZON" # S: Strategy × horizon performance
    EXECUTION = "EXECUTION"               # X: Slippage, fills, broker
    SYSTEM_LEARNING = "SYSTEM_LEARNING"   # L: Degradation, improvement, drift
    RISK_MANAGEMENT = "RISK_MANAGEMENT"   # R: Guard effectiveness, risk layer value
    DATA_GOVERNANCE = "DATA_GOVERNANCE"   # G: Lineage, validity, research confidence
    PROMOTION_INTELLIGENCE = "PROMOTION_INTELLIGENCE"  # P: Promotion impact and readiness


class QuestionStatus(str, Enum):
    """Computed status of a research question."""
    READY = "READY"                   # All required fields + validation pass
    WAITING_DATA = "WAITING_DATA"     # Implementation exists, insufficient data
    BLOCKED = "BLOCKED"               # Required architecture/data missing
    COMPLETE = "COMPLETE"             # Executed and validated
    INVALIDATED = "INVALIDATED"       # Previous result exists but validation failed


class QuestionPriority(str, Enum):
    """Research priority for execution ordering."""
    P0 = "P0"   # Must answer before trading live
    P1 = "P1"   # Important for confidence
    P2 = "P2"   # Useful but can wait
    P3 = "P3"   # Future / nice to have


class DataSource(str, Enum):
    """Where the question's data comes from."""
    SHADOW_TRADES = "shadow_trades"
    DECISION_TRACE = "decision_trace"
    TRADE_TRUTH = "trade_truth"
    MARKET_CONTEXT = "market_context"
    EXECUTION_CONTEXT = "execution_context"
    EQUITY_CURVE = "equity_curve"
    SLIPPAGE_JOURNAL = "slippage_journal"


@dataclass(frozen=True)
class ValidationRule:
    """One validation requirement that must pass before the question can run."""
    field: str              # e.g. "market_phase_coverage", "lineage_coverage"
    operator: str           # ">", ">=", "<", "==", "!="
    threshold: float        # e.g. 0.80
    description: str = ""   # Human-readable explanation

    def evaluate(self, actual_value: float) -> bool:
        """Check if the actual coverage/metric meets this rule."""
        if self.operator == ">":
            return actual_value > self.threshold
        if self.operator == ">=":
            return actual_value >= self.threshold
        if self.operator == "<":
            return actual_value < self.threshold
        if self.operator == "<=":
            return actual_value <= self.threshold
        if self.operator == "==":
            return actual_value == self.threshold
        if self.operator == "!=":
            return actual_value != self.threshold
        return False


@dataclass(frozen=True)
class ResearchQuestion:
    """
    Complete definition of a research question.

    Status is NOT stored here — it is computed at audit time
    from dataset validation results.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    id: str                             # e.g. "E1", "M4", "S2"
    category: QuestionCategory
    title: str
    description: str

    # ─── REQUIREMENTS ─────────────────────────────────────────────────
    required_fields: tuple[str, ...]    # Fields that MUST exist in the dataset
    data_sources: tuple[DataSource, ...]  # Which datasets are needed
    priority: QuestionPriority

    # ─── VALIDATION ───────────────────────────────────────────────────
    validation_rules: tuple[ValidationRule, ...] = ()

    # ─── DEPENDENCIES ─────────────────────────────────────────────────
    depends_on: tuple[str, ...] = ()    # Other question IDs that must complete first

    # ─── RUNNER ───────────────────────────────────────────────────────
    runner_module: str = ""             # e.g. "research_engine.experiments.probability_of_ruin"
    runner_function: str = ""           # e.g. "run_probability_of_ruin"
    report_filename: str = ""           # e.g. "r3_probability_of_ruin.json"

    # ─── LEGACY MAPPING ───────────────────────────────────────────────
    legacy_ids: tuple[str, ...] = ()    # Old Q1-Q25 IDs this replaces

    def to_dict(self) -> dict:
        """Serialize for reporting."""
        return {
            "id": self.id,
            "category": self.category.value,
            "title": self.title,
            "description": self.description,
            "required_fields": list(self.required_fields),
            "data_sources": [ds.value for ds in self.data_sources],
            "priority": self.priority.value,
            "validation_rules": [
                {"field": r.field, "operator": r.operator, "threshold": r.threshold, "description": r.description}
                for r in self.validation_rules
            ],
            "depends_on": list(self.depends_on),
            "runner_module": self.runner_module,
            "runner_function": self.runner_function,
            "report_filename": self.report_filename,
            "legacy_ids": list(self.legacy_ids),
        }


@dataclass(frozen=True)
class QuestionAuditResult:
    """Result of evaluating a research question against current data."""
    question_id: str
    title: str
    category: str
    priority: str
    status: QuestionStatus
    reason: str                         # Why this status was assigned
    failed_rules: tuple[str, ...] = ()  # Which validation rules failed
    coverage_snapshot: dict = field(default_factory=dict)  # Relevant coverage metrics

    def to_dict(self) -> dict:
        """Serialize for reporting."""
        return {
            "question_id": self.question_id,
            "title": self.title,
            "category": self.category,
            "priority": self.priority,
            "status": self.status.value,
            "reason": self.reason,
            "failed_rules": list(self.failed_rules),
            "coverage_snapshot": self.coverage_snapshot,
        }
