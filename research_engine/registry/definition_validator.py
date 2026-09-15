"""Canonical Research Question Definition validator.

READ-ONLY: does not modify definitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    QuestionLifecycle,
    ResearchQuestion,
    ResearchQuestionDefinition,
    ValidationSeverity,
)


# X5 was repaired in RW3.5: predicted_ev_r_v1 (reused from D3) is a versioned
# pre-decision EV authority with explicit producer, probability model, reward/
# risk inputs, semantic stage and units. No unresolved-authority questions
# remain in this baseline.
KNOWN_UNRESOLVED_AUTH: dict[str, list[str]] = {}

SEMANTIC_MISMATCH_QIDS = {
    "X3",
    "L1", "L2", "L3", "L4", "EX5", "EX6", "EX7", "EX8", "EXEC1",
}


@dataclass(frozen=True)
class ValidationResult:
    question_id: str
    severity: ValidationSeverity
    category: str
    message: str

    def to_dict(self):
        return {
            "question_id": self.question_id,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
        }


@dataclass
class DefinitionValidationReport:
    question_id: str
    results: list

    @property
    def has_errors(self):
        return any(r.severity == ValidationSeverity.ERROR for r in self.results)

    @property
    def has_warnings(self):
        return any(r.severity == ValidationSeverity.WARNING for r in self.results)

    @property
    def is_valid(self):
        return not self.has_errors

    @property
    def is_under_specified(self):
        return any(
            "unresolved" in r.category.lower() or "missing" in r.category.lower()
            for r in self.results
        )

    @property
    def is_semantic_mismatch(self):
        return any("mismatch" in r.category.lower() for r in self.results)

    def to_dict(self):
        return {
            "question_id": self.question_id,
            "is_valid": self.is_valid,
            "is_under_specified": self.is_under_specified,
            "is_semantic_mismatch": self.is_semantic_mismatch,
            "results": [r.to_dict() for r in self.results],
        }

def validate_definition(definition, registry_ids=None, report_index=None):
    """Validate a single ResearchQuestionDefinition against Wave A contract.

    READ-ONLY: never mutates the definition or any operational state.

    Optional read-only context:
      registry_ids: iterable of known canonical IDs for dependency checks.
      report_index: mapping report_filename -> list of question IDs claiming it.
    """
    results = []
    qid = definition.canonical_question_id

    if not isinstance(definition.definition_version, int) or definition.definition_version != 1:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "INVALID_VERSION",
            f"definition_version must be exactly 1 for V1 baseline, got {definition.definition_version!r}"
        ))

    valid_lifecycles = set(QuestionLifecycle)
    if definition.lifecycle_status not in valid_lifecycles:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "INVALID_LIFECYCLE",
            f"lifecycle_status={definition.lifecycle_status} is not valid"
        ))

    if not definition.question_wording:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "MISSING_WORDING",
            "question_wording is empty"
        ))

    if not definition.hypothesis and not definition.null_hypothesis:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "MISSING_HYPOTHESIS",
            "Both hypothesis and null_hypothesis are empty"
        ))

    if not definition.population_definition:
        results.append(ValidationResult(
            qid, ValidationSeverity.WARNING, "MISSING_POPULATION",
            "population_definition is empty"
        ))

    if not definition.metric_definition:
        results.append(ValidationResult(
            qid, ValidationSeverity.WARNING, "MISSING_METRIC",
            "metric_definition is empty"
        ))

    if not definition.evidence_authorities:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "MISSING_AUTHORITY",
            "No evidence_authorities declared"
        ))

    if qid in KNOWN_UNRESOLVED_AUTH:
        for reason in KNOWN_UNRESOLVED_AUTH[qid]:
            results.append(ValidationResult(
                qid, ValidationSeverity.ERROR, "UNRESOLVED_AUTHORITY",
                reason
            ))

    if len(definition.evidence_authorities) > 1 and definition.join_contract is None:
        results.append(ValidationResult(
            qid, ValidationSeverity.WARNING, "MISSING_JOIN_CONTRACT",
            "Multi-source evidence requires join_contract"
        ))

    if not definition.epoch_requirement:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "MISSING_EPOCH_REQUIREMENT",
            "epoch_requirement is empty"
        ))

    if definition.minimum_sample is None and definition.runner_function:
        results.append(ValidationResult(
            qid, ValidationSeverity.INFO, "NO_MINIMUM_SAMPLE",
            "No minimum_sample threshold declared"
        ))

    if not definition.runner_module:
        results.append(ValidationResult(
            qid, ValidationSeverity.INFO, "MISSING_RUNNER",
            "No runner_module declared (NO_RUNNER question)"
        ))
    elif not definition.runner_function:
        results.append(ValidationResult(
            qid, ValidationSeverity.ERROR, "AMBIGUOUS_RUNNER_MAPPING",
            "runner_module declared but runner_function is empty"
        ))
    if definition.runner_module and not definition.report_filename:
        results.append(ValidationResult(
            qid, ValidationSeverity.WARNING, "MISSING_REPORT_MAPPING",
            "runner declared but report_filename is empty"
        ))
    if registry_ids is not None:
        known = set(registry_ids)
        for dep in (definition.depends_on or ()):
            if dep not in known:
                results.append(ValidationResult(
                    qid, ValidationSeverity.ERROR, "DEPENDENCY_ERROR",
                    f"depends_on unknown question {dep!r}"
                ))
            elif dep == qid:
                results.append(ValidationResult(
                    qid, ValidationSeverity.ERROR, "DEPENDENCY_ERROR",
                    "question depends on itself"
                ))
    if report_index and definition.report_filename:
        claimants = report_index.get(definition.report_filename, [])
        if len(claimants) > 1:
            results.append(ValidationResult(
                qid, ValidationSeverity.WARNING, "AMBIGUOUS_REPORT_MAPPING",
                f"report {definition.report_filename!r} claimed by {sorted(claimants)}"
            ))
    if qid in SEMANTIC_MISMATCH_QIDS:
        results.append(ValidationResult(
            qid, ValidationSeverity.WARNING, "SEMANTIC_MISMATCH",
            f"Runner output may not fully match question wording for {qid}"
        ))
    return DefinitionValidationReport(qid, results)


def validate_all_definitions(definitions, registry_ids=None, report_index=None):
    if registry_ids is None:
        registry_ids = set(definitions.keys())
    if report_index is None:
        report_index = {}
        for _qid, _d in definitions.items():
            if getattr(_d, "report_filename", ""):
                report_index.setdefault(_d.report_filename, []).append(_qid)
    return {
        qid: validate_definition(d, registry_ids=registry_ids, report_index=report_index)
        for qid, d in definitions.items()
    }


def get_question_health(report):
    if not report.results:
        return "VALID"
    cats = {r.category for r in report.results}
    if "MISSING_RUNNER" in cats and len(cats) == 1:
        return "NO_RUNNER"
    if report.is_semantic_mismatch:
        return "SEMANTIC_MISMATCH"
    if report.is_under_specified:
        return "UNDER_SPECIFIED"
    if report.has_errors:
        return "INVALID"
    if report.has_warnings:
        return "VALID_WITH_WARNINGS"
    return "VALID"


def build_definitions_from_registry(registry):
    from research_engine.registry.research_question_models import CompletionRule, JoinContract

    definitions = {}
    for q in registry:
        authorities = [
            EvidenceAuthority(dataset=ds.value, current_eligibility=True)
            for ds in q.data_sources
        ]

        join_contract = None
        if len(q.data_sources) > 1:
            join_contract = JoinContract(
                join_keys=tuple(f"{ds.value}_id" for ds in q.data_sources),
                cardinality="many_to_one",
                conflict_policy="reject",
                description=f"Join {len(q.data_sources)} data sources by lineage key",
            )

        completion_rule = None
        sample_rules = [r for r in q.validation_rules if r.field == "sample_size"]
        if sample_rules:
            completion_rule = CompletionRule(
                rule_type="sample_reached",
                threshold=int(sample_rules[0].threshold),
                description=sample_rules[0].description,
            )

        minimum_sample = None
        for rule in q.validation_rules:
            if rule.field == "sample_size" and rule.operator == ">=":
                minimum_sample = int(rule.threshold)
                break

        definitions[q.id] = ResearchQuestionDefinition(
            canonical_question_id=q.id,
            definition_version=1,
            lifecycle_status=QuestionLifecycle.ACTIVE,
            question_wording=q.description,
            research_intent=f"Evaluate {q.title.lower()}",
            hypothesis="",
            null_hypothesis="",
            population_definition="",
            metric_definition="",
            evidence_authorities=tuple(authorities),
            join_contract=join_contract,
            epoch_requirement="CURRENT",
            minimum_sample=minimum_sample,
            completion_rule=completion_rule,
            runner_module=q.runner_module,
            runner_function=q.runner_function,
            report_filename=q.report_filename,
            depends_on=q.depends_on,
            legacy_ids=q.legacy_ids,
        )
    # Wave A1 Safe Definition Closure: enrich the closed target subset with
    # authoritative scientific definitions derived only from existing registry,
    # runner, evidence-resolver and readiness semantics. Unresolved targets and
    # all non-target questions are returned unchanged (fail-closed).
    from research_engine.registry.wave_a1_definitions import (
        apply_wave_a1_definitions,
    )
    from research_engine.registry.wave_a2_definitions import (
        apply_wave_a2_definitions,
    )
    from research_engine.registry.wave_a3_definitions import (
        apply_wave_a3_definitions,
    )
    from research_engine.registry.wave_a4_definitions import (
        apply_wave_a4_definitions,
    )
    from research_engine.registry.wave_a5_definitions import (
        apply_wave_a5_definitions,
    )
    from research_engine.registry.wave_a_no_runner_definitions import (
        apply_wave_a_no_runner_designs,
    )
    from research_engine.registry.rw2_definitions import apply_rw2_definitions
    from research_engine.registry.rw3_d2_definitions import apply_d2_definition
    from research_engine.registry.rw3_d3_definitions import apply_d3_definition
    from research_engine.registry.rw3_d4_definitions import apply_d4_definition
    from research_engine.registry.rw3_d5_definitions import apply_d5_definition
    from research_engine.registry.rw3_x5_definitions import apply_x5_definition
    from research_engine.registry.rw4_d6_definitions import apply_d6_definition

    definitions = apply_wave_a1_definitions(definitions)
    definitions = apply_wave_a2_definitions(definitions)
    definitions = apply_wave_a3_definitions(definitions)
    definitions = apply_wave_a4_definitions(definitions)
    definitions = apply_wave_a5_definitions(definitions)
    definitions = apply_wave_a_no_runner_designs(definitions)
    definitions = apply_rw2_definitions(definitions)
    definitions = apply_d2_definition(definitions)
    definitions = apply_d3_definition(definitions)
    definitions = apply_d4_definition(definitions)
    definitions = apply_d5_definition(definitions)
    definitions = apply_x5_definition(definitions)
    return apply_d6_definition(definitions)


def validate_runner_registry_threshold_alignment(question, question_id):
    results = []

    if question.runner_module:
        try:
            import importlib
            spec = importlib.util.find_spec(question.runner_module)
            if spec is None:
                results.append(ValidationResult(
                    question_id, ValidationSeverity.WARNING, "RUNNER_NOT_FOUND",
                    f"Runner module {question.runner_module} cannot be resolved"
                ))
        except ImportError:
            results.append(ValidationResult(
                question_id, ValidationSeverity.WARNING, "RUNNER_IMPORT_ERROR",
                f"Cannot import runner module {question.runner_module}"
            ))

    if question_id in SEMANTIC_MISMATCH_QIDS:
        results.append(ValidationResult(
            question_id, ValidationSeverity.WARNING, "SEMANTIC_MISMATCH",
            f"Runner output may not fully match question wording for {question_id}"
        ))

    return DefinitionValidationReport(question_id, results)
