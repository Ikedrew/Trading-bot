"""Research Registry v2 — Structured research question management."""
from __future__ import annotations

from research_engine.registry.research_question_models import (
    DataSource,
    QuestionAuditResult,
    QuestionCategory,
    QuestionLifecycle,
    QuestionPriority,
    QuestionStatus,
    ResearchQuestion,
    ResearchQuestionDefinition,
    ValidationRule,
)
from research_engine.registry.research_question_registry import (
    REGISTRY,
    REGISTRY_BY_ID,
    get_question,
    get_questions_by_category,
    get_questions_by_priority,
)
from research_engine.registry.baseline_manifest import (
    BASELINE_VERSION,
    BASELINE_QUESTION_IDS,
    BASELINE_QUESTION_SET,
    is_baseline_question,
    validate_registry_against_baseline,
)
from research_engine.registry.definition_validator import (
    DefinitionValidationReport,
    ValidationResult,
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
    validate_definition,
    validate_runner_registry_threshold_alignment,
)

__all__ = [
    "DataSource",
    "QuestionAuditResult",
    "QuestionCategory",
    "QuestionLifecycle",
    "QuestionPriority",
    "QuestionStatus",
    "ResearchQuestion",
    "ResearchQuestionDefinition",
    "ValidationRule",
    "REGISTRY",
    "REGISTRY_BY_ID",
    "get_question",
    "get_questions_by_category",
    "get_questions_by_priority",
    "BASELINE_VERSION",
    "BASELINE_QUESTION_IDS",
    "BASELINE_QUESTION_SET",
    "is_baseline_question",
    "DefinitionValidationReport",
    "ValidationResult",
    "build_definitions_from_registry",
    "get_question_health",
    "validate_all_definitions",
    "validate_definition",
    "validate_runner_registry_threshold_alignment",
    "validate_registry_against_baseline",
]
