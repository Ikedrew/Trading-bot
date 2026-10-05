"""Governed evaluator identity for canonical question results.

Evidence identity answers "which immutable snapshot was read?".  Evaluation
identity answers the independent question "would the currently authoritative
evaluator interpret that same snapshot the same way?".

A published question result is current only when BOTH still match:

    same governed evidence identity  AND  same governed evaluation identity

This module derives evaluation identity from governed, explicit semantic
identity that already exists in this codebase:

* the registry question identity and its definition version,
* the registered runner identity (``module.function``),
* the evaluator's runner-scoped semantic version mapping
  (``EVALUATOR_SEMANTIC_VERSIONS``),
* runner-scoped report and governance/contract version mappings where that
  evaluator explicitly binds them.

Nothing here hashes source text, so an incidental, non-semantic edit (a
comment, a log line, a formatting change) never invalidates a scientific
result.  An evaluator whose interpretation changed must declare it, exactly as
it declares the relevant runner-scoped semantic authority.

The invalidation rule is fail-closed for the one component that cannot be
reconstructed from a legacy record: ``evaluator_semantic_version``.  A result
published before an evaluator declared that contract cannot be proven
equivalent to the current evaluator, so it is STALE_EVALUATION and eligible for
governed re-evaluation.  Evaluators that declare no such contract are retained,
so introducing this module does not by itself invalidate the whole programme.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import types
from typing import Any, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import fingerprint


EVALUATION_IDENTITY_SCHEMA = "question_evaluation_identity_v1"

# Orchestration reasons.  These are deliberately not public scientific
# statuses: a stale evaluator is an orchestration reason to re-run, never a
# scientific verdict about the world.
STALE_EVALUATION = "STALE_EVALUATION"
EVALUATOR_CHANGED = "EVALUATOR_CHANGED"
REQUIRES_REEVALUATION = "REQUIRES_REEVALUATION"

# The single fail-closed component.  Everything else in the identity is
# reconstructible from a legacy result record, so only this one forces
# re-evaluation when it first appears.
FAIL_CLOSED_COMPONENT = "evaluator_semantic_version"

RUNNER_SEMANTIC_VERSIONS = "EVALUATOR_SEMANTIC_VERSIONS"
RUNNER_REPORT_VERSIONS = "EVALUATOR_REPORT_SCHEMA_VERSIONS"
RUNNER_GOVERNANCE_VERSIONS = "EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS"


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _runner_version(module: types.ModuleType, mapping_name: str,
                    runner_function: str) -> str | None:
    """Read one explicitly runner-scoped semantic version.

    Module namespace discovery is intentionally forbidden here: shared modules
    commonly implement several canonical questions, and imported ``*_VERSION``
    constants are not proof that every function in that module owns them.
    """
    mapping = getattr(module, mapping_name, None)
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(runner_function)
    return value if isinstance(value, str) and value else None


def _runner_versions(module: types.ModuleType, mapping_name: str,
                     runner_function: str) -> dict[str, str]:
    mapping = getattr(module, mapping_name, None)
    if not isinstance(mapping, Mapping):
        return {}
    versions = mapping.get(runner_function)
    if not isinstance(versions, Mapping):
        return {}
    return {
        str(name): value for name, value in versions.items()
        if isinstance(name, str) and isinstance(value, str) and value
    }
def _load_runner_module(module_path: str) -> types.ModuleType | None:
    try:
        return importlib.import_module(module_path)
    except Exception:
        # An unimportable evaluator is itself a governed fact.  The question
        # cycle records the import failure; identity must not raise here.
        return None


def build_evaluation_identity(
    question: Any, definition: Any, *, registry_version: str,
) -> dict[str, Any]:
    """Compose the governed evaluation identity for one canonical question."""
    runner_module = str(_value(question, "runner_module", "") or "")
    runner_function = str(_value(question, "runner_function", "") or "")
    definition_version = _value(definition, "definition_version", None)

    module = _load_runner_module(runner_module) if runner_module else None
    semantic_version = (
        _runner_version(module, RUNNER_SEMANTIC_VERSIONS, runner_function)
        if module is not None else None
    )
    report_versions = (
        _runner_versions(module, RUNNER_REPORT_VERSIONS, runner_function)
        if module is not None else {}
    )
    governance_versions = (
        _runner_versions(module, RUNNER_GOVERNANCE_VERSIONS, runner_function)
        if module is not None else {}
    )

    identity: dict[str, Any] = {
        "evaluation_identity_schema": EVALUATION_IDENTITY_SCHEMA,
        "question_id": str(_value(question, "id", "")),
        "question_definition_version": (
            None if definition_version is None
            else f"{registry_version}:{definition_version}"
        ),
        "runner_identity": (
            f"{runner_module}.{runner_function}"
            if runner_module and runner_function else None
        ),
        # The fail-closed component: an explicit, governed declaration that the
        # evaluator's scientific interpretation is versioned.
        FAIL_CLOSED_COMPONENT: semantic_version,
        "report_schema_versions": dict(sorted(report_versions.items())),
        "governance_contract_versions": dict(sorted(governance_versions.items())),
    }
    identity["evaluation_identity_digest"] = evaluation_identity_digest(identity)
    return identity


def evaluation_identity_digest(identity: Mapping[str, Any]) -> str:
    material = {k: v for k, v in identity.items()
                if k != "evaluation_identity_digest"}
    return fingerprint(material)


def is_result_current(
    recorded: Mapping[str, Any] | None, current: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Apply the fail-closed invalidation rule.

    Returns ``(is_current, reason)``.  ``reason`` is one of
    ``STALE_EVALUATION``/``EVALUATOR_CHANGED`` when not current, else ``None``.
    """
    if not isinstance(recorded, Mapping) or not recorded:
        # A result that records no evaluation identity at all was published
        # before governed evaluator identity existed.  It is retained unless the
        # current evaluator declares a fail-closed semantic contract.
        if current.get(FAIL_CLOSED_COMPONENT):
            return False, STALE_EVALUATION
        return True, None

    if recorded.get("evaluation_identity_digest") == current.get(
            "evaluation_identity_digest"):
        return True, None

    if recorded.get(FAIL_CLOSED_COMPONENT) != current.get(FAIL_CLOSED_COMPONENT):
        return False, EVALUATOR_CHANGED
    if recorded.get("runner_identity") != current.get("runner_identity"):
        return False, EVALUATOR_CHANGED
    return False, STALE_EVALUATION


def stale_question_ids(
    results: Mapping[str, Mapping[str, Any]],
    identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Return ``{question_id: reason}`` for results that are no longer current.

    Only questions that the current governed registry actually covers are judged.
    A projection entry with no authoritative evaluator to compare against has no
    governed evaluation identity to invalidate, so it is left to the question
    cycle's own accounting rather than being force-failed here.
    """
    stale: dict[str, str] = {}
    for question_id, recorded in results.items():
        current = identities.get(question_id)
        if current is None:
            continue
        is_current, reason = is_result_current(recorded, current)
        if not is_current:
            stale[question_id] = str(reason)
    return stale


def explain_staleness(
    recorded: Mapping[str, Any] | None, current: Mapping[str, Any],
) -> dict[str, Any]:
    """Explain exactly which evaluation-identity component differs.

    This is diagnostic only; it never influences scientific interpretation.
    """
    if not isinstance(recorded, Mapping):
        recorded = {}
    changed = sorted(
        name for name in set(recorded) | set(current)
        if name != "evaluation_identity_digest"
        and recorded.get(name) != current.get(name)
    )
    return {
        "recorded_evaluation_identity_digest": recorded.get("evaluation_identity_digest"),
        "current_evaluation_identity_digest": current.get("evaluation_identity_digest"),
        "changed_components": changed,
        "changed_component_values": {
            name: {"old": recorded.get(name), "new": current.get(name)}
            for name in changed
        },
    }


def evaluation_identities(
    questions: Sequence[Any], definitions: Mapping[str, Any], *,
    registry_version: str,
) -> dict[str, dict[str, Any]]:
    return {
        str(_value(question, "id")): build_evaluation_identity(
            question, definitions.get(str(_value(question, "id"))),
            registry_version=registry_version,
        )
        for question in questions
    }


__all__ = [
    "EVALUATOR_CHANGED",
    "EVALUATION_IDENTITY_SCHEMA",
    "FAIL_CLOSED_COMPONENT",
    "REQUIRES_REEVALUATION",
    "STALE_EVALUATION",
    "build_evaluation_identity",
    "evaluation_identities",
    "evaluation_identity_digest",
    "explain_staleness",
    "is_result_current",
    "stale_question_ids",
]
