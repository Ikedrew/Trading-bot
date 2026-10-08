"""Production Q71+ evaluator registry (Repair Block 2A).

The generated-question evaluator registry is the single authority for whether a
generated question can execute.  Repair Block 2 delivered that authority but
registered nothing, so production capability was dormant.  This module builds,
persists and verifies the *real* production registry from the governed
evidence-class catalogue and the production evaluator families.

Authority rules enforced here:

* Registrations are built from the catalogue in a deterministic, declared order.
  Nothing is registered that is not a declared production evidence class.
* Scope is explicit and never generic: every registration declares its own
  question types, evidence classes, metric families and intervention classes,
  and no registration declares ``UNKNOWN`` for the evidence class it owns.
* Only ``research_engine.*`` runners may be registered.  A test module can never
  leak a fixture registration into the production registry.
* The registry identity is reproducible from the declarations alone and is
  re-verified on load, so a silently edited registry file is detected rather
  than trusted.
* Absence of the registry file is a legitimate production state: every generated
  question then resolves to ``MISSING_EVALUATOR``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.q71_evidence_classes import (
    PRODUCTION_EVIDENCE_CLASSES,
    ProductionEvidenceClass,
    evidence_class_identity,
    unsupported_evidence_class_catalogue,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    DEFAULT_REGISTRY_PATH,
    GeneratedQuestionEvaluatorRegistration,
    GeneratedQuestionEvaluatorRegistry,
    GeneratedQuestionEvaluatorRegistryError,
    UNKNOWN,
)

PRODUCTION_REGISTRY_MODULE_PREFIX = "research_engine."

#: The governed question types the production observation space mints.  A
#: generated question whose question type is outside this set has no production
#: evaluator and stays MISSING_EVALUATOR.
PRODUCTION_QUESTION_TYPES = ("COVERAGE_GAP",)

#: The metric family and intervention class a generated coverage question
#: declares today: both are explicitly UNKNOWN, and an evaluator that supports
#: such a question must declare that it supports an unknown family/class because
#: it is the evaluator that decides what is measurable.
PRODUCTION_UNKNOWN_DIMENSION = (UNKNOWN,)

#: Declared production evaluator families, in deterministic registration order.
#: Each entry binds one governed evidence class to one runner function.
PRODUCTION_EVALUATOR_FAMILIES: tuple[Mapping[str, str], ...] = (
    {
        "evidence_class": "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
        "evaluator_key": "q71.expectancy.completed_shadow_lifecycle",
        "runner_module": "research_engine.experiments.q71_production_evaluators",
        "runner_function": "completed_shadow_lifecycle_expectancy",
        "evaluator_version": "v1",
        "capability_class": "GENERATED_QUESTION_OUTCOME_EXPECTANCY_EVALUATOR",
    },
    {
        "evidence_class": (
            "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE"),
        "evaluator_key": "q71.execution.session_conditioned_slippage",
        "runner_module": "research_engine.experiments.q71_production_evaluators",
        "runner_function": "session_conditioned_execution_slippage",
        "evaluator_version": "v1",
        "capability_class": "GENERATED_QUESTION_EXECUTION_QUALITY_EVALUATOR",
    },
    {
        "evidence_class": "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "evaluator_key": "q71.descriptive.exit_path_distribution",
        "runner_module": "research_engine.experiments.q71_production_evaluators",
        "runner_function": "governed_exit_path_distribution",
        "evaluator_version": "v1",
        "capability_class": "GENERATED_QUESTION_DESCRIPTIVE_EVALUATOR",
    },
    {
        # The only candidate-capable production family: the canonical HD09
        # governed exit-policy counterfactual authority.  Its governed
        # intervention is resolved from the governed policy catalogue at
        # evaluation time, never named statically and never inferred.
        "evidence_class": "GOVERNED_EXIT_POLICY_COUNTERFACTUAL",
        "evaluator_key": "q71.counterfactual.governed_exit_policy",
        "runner_module": "research_engine.experiments.q71_production_evaluators",
        "runner_function": "governed_exit_policy_counterfactual",
        "evaluator_version": "v1",
        "capability_class": (
            "GENERATED_QUESTION_GOVERNED_COUNTERFACTUAL_INTERVENTION_EVALUATOR"),
    },
)


class ProductionEvaluatorRegistryError(RuntimeError):
    """The production registry could not be built, verified or loaded."""


def _declaration(evidence_class: str) -> ProductionEvidenceClass:
    for item in PRODUCTION_EVIDENCE_CLASSES:
        if item.evidence_class == evidence_class:
            return item
    raise ProductionEvaluatorRegistryError(
        "PRODUCTION_EVALUATOR_EVIDENCE_CLASS_NOT_DECLARED:" + evidence_class)


def production_evaluator_registrations(
) -> tuple[GeneratedQuestionEvaluatorRegistration, ...]:
    """Build the production registrations in their declared, deterministic order."""
    registrations: list[GeneratedQuestionEvaluatorRegistration] = []
    for family in PRODUCTION_EVALUATOR_FAMILIES:
        module = str(family["runner_module"])
        if not module.startswith(PRODUCTION_REGISTRY_MODULE_PREFIX):
            raise ProductionEvaluatorRegistryError(
                "PRODUCTION_EVALUATOR_RUNNER_NOT_PRODUCTION_MODULE:" + module)
        declaration = _declaration(str(family["evidence_class"]))
        if declaration.capability_class != str(family["capability_class"]):
            raise ProductionEvaluatorRegistryError(
                "PRODUCTION_EVALUATOR_CAPABILITY_CLASS_MISMATCH:"
                + str(family["evaluator_key"]))
        registrations.append(GeneratedQuestionEvaluatorRegistration(
            evaluator_key=str(family["evaluator_key"]),
            runner_module=module,
            runner_function=str(family["runner_function"]),
            evaluator_version=str(family["evaluator_version"]),
            capability_class=str(family["capability_class"]),
            question_types=PRODUCTION_QUESTION_TYPES,
            # One evidence class per registration: the scope dimension that
            # actually discriminates a generated coverage question.
            evidence_classes=(declaration.evidence_class,),
            metric_families=PRODUCTION_UNKNOWN_DIMENSION,
            intervention_classes=PRODUCTION_UNKNOWN_DIMENSION,
            required_datasets=declaration.governed_datasets,
            observation_cells=(),
            report_schema_version="generated_question_report_v1",
            declared_scope_note=declaration.declared_scope_note,
        ))
    keys = [item.evaluator_key for item in registrations]
    if len(set(keys)) != len(keys):
        raise ProductionEvaluatorRegistryError(
            "PRODUCTION_EVALUATOR_DUPLICATE_KEY")
    return tuple(registrations)


def build_production_evaluator_registry(
    path: Path | str | None = None,
) -> GeneratedQuestionEvaluatorRegistry:
    """Return an in-memory production registry (optionally bound to ``path``)."""
    return GeneratedQuestionEvaluatorRegistry(
        path, registrations=production_evaluator_registrations())


def production_registry_identity() -> str:
    """Reproducible identity of the production registry declarations."""
    return build_production_evaluator_registry().registry_identity


def production_registry_document() -> dict[str, Any]:
    """The exact persisted document the production registry is written as."""
    registry = build_production_evaluator_registry()
    return {
        **registry._document(),
        "registry_identity": registry.registry_identity,
        "evidence_class_catalogue_identity": evidence_class_identity(),
    }


def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(document) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_production_evaluator_registry(
    path: Path | str | None = None,
) -> GeneratedQuestionEvaluatorRegistry:
    """Build and persist the production registry at its production path.

    The persisted document carries the registry identity and the evidence-class
    catalogue identity alongside the canonical registrations, so tampering is
    detectable from the file itself as well as by recomputation.
    """
    resolved = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    registry = build_production_evaluator_registry()
    _atomic_write(resolved, {
        **registry._document(),
        "registry_identity": registry.registry_identity,
        "evidence_class_catalogue_identity": evidence_class_identity(),
    })
    return GeneratedQuestionEvaluatorRegistry(resolved)


def load_production_evaluator_registry(
    path: Path | str | None = None,
) -> GeneratedQuestionEvaluatorRegistry | None:
    """Load the persisted production registry, or ``None`` when none is deployed.

    Absence is a legitimate state: generated questions then resolve to
    ``MISSING_EVALUATOR``.  A present-but-unverifiable registry fails closed by
    raising rather than silently degrading.
    """
    resolved = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    if not resolved.exists():
        return None
    registry = GeneratedQuestionEvaluatorRegistry(resolved)
    for registration in registry.all():
        if not registration.runner_module.startswith(
                PRODUCTION_REGISTRY_MODULE_PREFIX):
            raise ProductionEvaluatorRegistryError(
                "PRODUCTION_REGISTRY_CONTAINS_NON_PRODUCTION_EVALUATOR:"
                + registration.evaluator_key)
    declared = _declared_identity(resolved)
    if declared is not None and declared != registry.registry_identity:
        raise ProductionEvaluatorRegistryError(
            "PRODUCTION_REGISTRY_IDENTITY_MISMATCH")
    return registry


def _declared_identity(path: Path) -> str | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, Mapping):
        return None
    value = document.get("registry_identity")
    return str(value) if value else None


def verify_production_registry(
    path: Path | str | None = None,
) -> tuple[bool, str]:
    """Verify a persisted production registry against the declarations.

    Returns ``(verified, detail)``.  A missing file is reported as unverified
    with an explicit reason; it is never silently treated as valid.
    """
    resolved = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    if not resolved.exists():
        return False, "PRODUCTION_REGISTRY_NOT_DEPLOYED"
    try:
        loaded = GeneratedQuestionEvaluatorRegistry(resolved)
    except GeneratedQuestionEvaluatorRegistryError as exc:
        return False, "PRODUCTION_REGISTRY_INVALID:" + str(exc)
    expected = production_registry_identity()
    if loaded.registry_identity != expected:
        return False, "PRODUCTION_REGISTRY_IDENTITY_MISMATCH"
    return True, "VERIFIED"


def production_capability_matrix() -> dict[str, Any]:
    """The production capability matrix for the generated-question space.

    Every structural family of the generated-question space appears exactly
    once, supported or not.  The denominator is the real structural family set
    (``structural_generated_question_families``); it is never narrowed to
    improve the percentage.
    """
    unsupported = unsupported_evidence_class_catalogue()
    rows: list[dict[str, Any]] = []
    for family in PRODUCTION_EVALUATOR_FAMILIES:
        declaration = _declaration(str(family["evidence_class"]))
        rows.append({
            "structural_family": declaration.evidence_class,
            "evaluator_available": True,
            "evaluator_key": str(family["evaluator_key"]),
            "evaluator_version": str(family["evaluator_version"]),
            "runner": (
                str(family["runner_module"]) + "." + str(family["runner_function"])),
            "evidence_requirements": list(declaration.governed_datasets),
            "observation_grain": declaration.observation_grain,
            "estimand": declaration.estimand,
            "statistical_method": declaration.statistical_method,
            "minimum_evidence": dict(declaration.minimum_evidence),
            "scientific_finding_capable": declaration.scientific_finding_capable,
            "hypothesis_capable": declaration.hypothesis_capable,
            "candidate_capable": declaration.candidate_capable,
            "governed_intervention_policy_id": (
                declaration.governed_intervention_policy_id),
            "governed_intervention_authority": (
                declaration.governed_intervention_authority),
            "governed_intervention_policy_catalogue": (
                declaration.governed_intervention_policy_catalogue),
            "unsupported_reason": None,
        })
    for token, entry in sorted(unsupported.items()):
        rows.append({
            "structural_family": token,
            "evaluator_available": False,
            "evaluator_key": None,
            "evaluator_version": None,
            "runner": None,
            "evidence_requirements": [],
            "observation_grain": None,
            "estimand": None,
            "statistical_method": None,
            "minimum_evidence": {},
            "scientific_finding_capable": False,
            "hypothesis_capable": False,
            "candidate_capable": False,
            "governed_intervention_policy_id": None,
            "governed_intervention_authority": None,
            "governed_intervention_policy_catalogue": None,
            "unsupported_reason": str(entry["reason"]),
            "unsupported_detail": str(entry["detail"]),
            "unsupported_provenance": str(entry["provenance"]),
        })
    supported = [row for row in rows if row["evaluator_available"]]
    unsupported_rows = [row for row in rows if not row["evaluator_available"]]
    total = len(rows)
    return {
        "matrix_schema": "q71_production_capability_matrix_v1",
        "registry_identity": production_registry_identity(),
        "evidence_class_catalogue_identity": evidence_class_identity(),
        "total_structural_families": total,
        "supported_families": len(supported),
        "unsupported_families": len(unsupported_rows),
        "supported_fraction": (
            (len(supported) / total) if total else 0.0),
        "finding_capable_families": [
            row["structural_family"] for row in supported
            if row["scientific_finding_capable"]],
        "hypothesis_capable_families": [
            row["structural_family"] for row in supported
            if row["hypothesis_capable"]],
        "candidate_capable_families": [
            row["structural_family"] for row in supported
            if row["candidate_capable"]],
        "unsupported_family_reasons": {
            row["structural_family"]: row["unsupported_reason"]
            for row in unsupported_rows},
        "generated_question_structural_dimensions": {
            "question_types": list(PRODUCTION_QUESTION_TYPES),
            "metric_families": list(PRODUCTION_UNKNOWN_DIMENSION),
            "intervention_classes": list(PRODUCTION_UNKNOWN_DIMENSION),
            "discriminating_dimension": "evidence_class",
            "observation_cells": (
                "not pinned: production observation cells are minted from the "
                "observation space, so a registration declares its whole "
                "evidence-class scope rather than enumerating cells"),
        },
        "families": rows,
    }


__all__ = [
    "PRODUCTION_EVALUATOR_FAMILIES",
    "PRODUCTION_QUESTION_TYPES",
    "PRODUCTION_REGISTRY_MODULE_PREFIX",
    "PRODUCTION_UNKNOWN_DIMENSION",
    "ProductionEvaluatorRegistryError",
    "build_production_evaluator_registry",
    "load_production_evaluator_registry",
    "production_capability_matrix",
    "production_evaluator_registrations",
    "production_registry_document",
    "production_registry_identity",
    "verify_production_registry",
    "write_production_evaluator_registry",
]
