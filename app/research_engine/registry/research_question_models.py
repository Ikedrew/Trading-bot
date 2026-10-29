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
from typing import Any


class QuestionLifecycle(str, Enum):
    """Definition lifecycle status (separate from operational readiness)."""
    PROPOSED = "PROPOSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"
    SUPERSEDED = "SUPERSEDED"


class EvidenceProducer(str, Enum):
    """Known authoritative evidence producers."""
    SHADOW_TRADES = "shadow_trades"
    DECISION_TRACE = "decision_trace"
    TRADE_TRUTH = "trade_truth"
    PROTECTION_AUDIT = "protection_audit_v1"
    RISK_DEVIATION = "risk_deviation_v1"
    EXECUTION_RESULTS = "execution_results_v1"
    PORTFOLIO_RANKINGS = "portfolio_rankings_v1"


class ValidationSeverity(str, Enum):
    """Severity for definition validation results."""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class EvidenceAuthority:
    """Semantic authority definition for a piece of evidence."""
    dataset: str
    schema_version: str | None = None
    producer: EvidenceProducer | None = None
    field_path: str | None = None  # e.g. "outcome.r_multiple_realised"
    semantic_meaning: str = ""
    current_eligibility: bool = True  # False = historical-only/unresolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "schema_version": self.schema_version,
            "producer": self.producer.value if self.producer else None,
            "field_path": self.field_path,
            "semantic_meaning": self.semantic_meaning,
            "current_eligibility": self.current_eligibility,
        }


@dataclass(frozen=True)
class JoinContract:
    """Join semantics for multi-source evidence."""
    join_keys: tuple[str, ...]
    cardinality: str = "many_to_one"  # one_to_one, one_to_many, many_to_many
    conflict_policy: str = "reject"  # reject, prefer_first, prefer_latest
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "join_keys": list(self.join_keys),
            "cardinality": self.cardinality,
            "conflict_policy": self.conflict_policy,
            "description": self.description,
        }


@dataclass(frozen=True)
class CompletionRule:
    """Rule describing when a question's research is considered complete."""
    rule_type: str = "sample_reached"  # sample_reached, report_exists, manual
    threshold: int | None = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "threshold": self.threshold,
            "description": self.description,
        }


@dataclass
class ResearchQuestionDefinition:
    """Extended canonical definition of a research question.

    This is the authoritative definition contract.
    definition_version starts at 1 for V1 baseline.
    """
    # --- Core identity ---
    canonical_question_id: str
    definition_version: int = 1  # V1 baseline — must start at 1
    lifecycle_status: QuestionLifecycle = QuestionLifecycle.ACTIVE

    # --- Scientific question ---
    question_wording: str = ""
    research_intent: str = ""
    hypothesis: str = ""
    null_hypothesis: str = ""

    # --- Evidence definitions ---
    population_definition: str = ""
    metric_definition: str = ""
    evidence_authorities: tuple[EvidenceAuthority, ...] = ()

    # --- Join semantics ---
    join_contract: JoinContract | None = None

    # --- Requirements ---
    epoch_requirement: str = "CURRENT"  # CURRENT, MIXED, HISTORICAL
    minimum_sample: int | None = None

    # --- Completion ---
    completion_rule: CompletionRule | None = None

    # --- Runner/report mapping ---
    runner_module: str = ""
    runner_function: str = ""
    report_filename: str = ""

    # --- Dependencies ---
    depends_on: tuple[str, ...] = ()

    # --- Legacy aliases ---
    legacy_ids: tuple[str, ...] = ()

    # --- Governed scientific ownership ---
    scientific_owner_id: str = ""  # non-empty only for explicit aliases

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_question_id": self.canonical_question_id,
            "definition_version": self.definition_version,
            "lifecycle_status": self.lifecycle_status.value,
            "question_wording": self.question_wording,
            "research_intent": self.research_intent,
            "hypothesis": self.hypothesis,
            "null_hypothesis": self.null_hypothesis,
            "population_definition": self.population_definition,
            "metric_definition": self.metric_definition,
            "evidence_authorities": [a.to_dict() for a in self.evidence_authorities],
            "join_contract": self.join_contract.to_dict() if self.join_contract else None,
            "epoch_requirement": self.epoch_requirement,
            "minimum_sample": self.minimum_sample,
            "completion_rule": self.completion_rule.to_dict() if self.completion_rule else None,
            "runner_module": self.runner_module,
            "runner_function": self.runner_function,
            "report_filename": self.report_filename,
            "depends_on": list(self.depends_on),
            "legacy_ids": list(self.legacy_ids),
            "scientific_owner_id": self.scientific_owner_id,
        }


class QuestionCategory(str, Enum):
    """Research question domain categories."""
    SYSTEM_EDGE = "SYSTEM_EDGE"           # E: Overall system expectancy
    MARKET_CONTEXT = "MARKET_CONTEXT"     # M: Regime, phase, HTF interactions
    DECISION_QUALITY = "DECISION_QUALITY" # D: Scoring, calibration, thresholds
    STRATEGY_HORIZON = "STRATEGY_HORIZON" # S: Strategy x horizon performance
    EXECUTION = "EXECUTION"               # X: Slippage, fills, broker
    SYSTEM_LEARNING = "SYSTEM_LEARNING"   # L: Degradation, improvement, drift
    RISK_MANAGEMENT = "RISK_MANAGEMENT"   # R: Guard effectiveness, risk layer value
    DATA_GOVERNANCE = "DATA_GOVERNANCE"   # G: Lineage, validity, research confidence
    PROMOTION_INTELLIGENCE = "PROMOTION_INTELLIGENCE"  # P: Promotion impact and readiness
    EXIT_MANAGEMENT = "EXIT_MANAGEMENT"   # EX: Exit policy optimisation and validation
    TRADE_MANAGEMENT = "TRADE_MANAGEMENT"  # MGMT: Trade management effectiveness
    PORTFOLIO_SELECTION = "PORTFOLIO_SELECTION"  # PORT: Portfolio ranking / selection quality
    OPPORTUNITY_SELECTION = "OPPORTUNITY_SELECTION"  # OPP: Opportunity-level selection quality


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
    MANAGEMENT_ACTIONS = "management_actions"
    HORIZON_CANDIDATES = "horizon_candidates"
    STRATEGY_CANDIDATES = "strategy_candidates"
    MARKET_CONTEXT = "market_context"
    EXECUTION_CONTEXT = "execution_context"
    EQUITY_CURVE = "equity_curve"
    SLIPPAGE_JOURNAL = "slippage_journal"
    EXECUTION_RESULTS = "execution_results_v1"
    PROTECTION_AUDIT = "protection_audit_v1"
    EXECUTION_ATTEMPTS = "execution_attempts_v1"
    RISK_DEVIATION = "risk_deviation_v1"
    PORTFOLIO_RANKINGS = "portfolio_rankings"
    PORTFOLIO_SHADOW = "portfolio_shadow"


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

    # --- IDENTITY ---
    id: str                             # e.g. "E1", "M4", "S2"
    category: QuestionCategory
    title: str
    description: str

    # --- REQUIREMENTS ---
    required_fields: tuple[str, ...]    # Fields that MUST exist in the dataset
    data_sources: tuple[DataSource, ...]  # Which datasets are needed
    priority: QuestionPriority

    # --- VALIDATION ---
    validation_rules: tuple[ValidationRule, ...] = ()

    # --- DEPENDENCIES ---
    depends_on: tuple[str, ...] = ()    # Other question IDs that must complete first

    # --- RUNNER ---
    runner_module: str = ""             # e.g. "research_engine.experiments.probability_of_ruin"
    runner_function: str = ""           # e.g. "run_probability_of_ruin"
    report_filename: str = ""           # e.g. "r3_probability_of_ruin.json"

    # --- LEGACY MAPPING ---
    legacy_ids: tuple[str, ...] = ()    # Old Q1-Q25 IDs this replaces

    # --- GOVERNED SCIENTIFIC OWNERSHIP ---
    # An alias remains a canonical identity but owns no independent runner or
    # report. Its state is explicitly projected from this scientific owner.
    scientific_owner_id: str = ""

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
            "scientific_owner_id": self.scientific_owner_id,
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
