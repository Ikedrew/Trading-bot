"""Governed evaluator registry for generated (Q71+) research questions.

Audit 1 found that generated questions had no production mechanism for
evaluator resolution: the orchestration consulted a caller-supplied string map
and the answer was never executable.  This module is that mechanism.

Authority rules enforced here:

* A registration declares an explicit capability *scope*.  Every scope
  dimension must be declared; a registration can never match a question outside
  its declared scope, and there is no wildcard dimension.
* Resolution is deterministic.  The candidate set is computed from governed
  question semantics (question type, evidence class, observation cell, metric
  family, intervention class and dataset requirements) and the winner must be
  unique.  Ambiguity fails closed instead of picking one.
* No evaluator is invented.  An empty registry resolves nothing and every
  generated question stays MISSING_EVALUATOR.
* Registration identity is stable and persisted: the evaluator identity digest
  is derived from the registration material and re-verified on load, so a
  silently edited registration cannot be mistaken for the one that produced an
  existing result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.governed_scientific_result import (
    SCIENTIFIC_RESULT_SCHEMA,
)


REGISTRY_SCHEMA = "generated_question_evaluator_registry_v1"
DEFAULT_REGISTRY_PATH = Path("data/research/continuous/q71_evaluator_registry.json")

#: Matching vocabulary.  UNKNOWN is a first-class, declared value: a question
#: whose metric family is not known must be matched by a registration that
#: explicitly declares it supports UNKNOWN metric families.
UNKNOWN = "UNKNOWN"

#: Resolution reason codes (closed).
RESOLVED_EXPLICIT_KEY = "RESOLVED_EXPLICIT_KEY"
RESOLVED_UNIQUE_SCOPE_MATCH = "RESOLVED_UNIQUE_SCOPE_MATCH"
NO_REGISTERED_EVALUATOR = "NO_REGISTERED_EVALUATOR"
EVALUATOR_KEY_UNKNOWN = "EVALUATOR_KEY_UNKNOWN"
EVALUATOR_OUT_OF_DECLARED_SCOPE = "EVALUATOR_OUT_OF_DECLARED_SCOPE"
AMBIGUOUS_EVALUATOR_MATCH = "AMBIGUOUS_EVALUATOR_MATCH"

RESOLUTION_REASON_CODES = frozenset({
    RESOLVED_EXPLICIT_KEY, RESOLVED_UNIQUE_SCOPE_MATCH, NO_REGISTERED_EVALUATOR,
    EVALUATOR_KEY_UNKNOWN, EVALUATOR_OUT_OF_DECLARED_SCOPE,
    AMBIGUOUS_EVALUATOR_MATCH,
})

#: Every dimension a registration must declare.
SCOPE_DIMENSIONS = (
    "question_types", "evidence_classes", "metric_families",
    "intervention_classes",
)


class GeneratedQuestionEvaluatorRegistryError(RuntimeError):
    """The registry is corrupt, or a registration/transition is invalid."""


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _tokens(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise GeneratedQuestionEvaluatorRegistryError(
            "EVALUATOR_SCOPE_INVALID:" + label)
    items = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_SCOPE_EMPTY_TOKEN:" + label)
        items.append(text)
    return tuple(sorted(set(items)))


def _text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GeneratedQuestionEvaluatorRegistryError(
            "EVALUATOR_REGISTRATION_FIELD_REQUIRED:" + label)
    return text


@dataclass(frozen=True)
class GeneratedQuestionEvaluatorRegistration:
    """A declared, versioned evaluator capability for generated questions."""

    evaluator_key: str
    runner_module: str
    runner_function: str
    evaluator_version: str
    capability_class: str
    question_types: tuple[str, ...]
    evidence_classes: tuple[str, ...]
    metric_families: tuple[str, ...]
    intervention_classes: tuple[str, ...]
    required_datasets: tuple[str, ...] = ()
    observation_cells: tuple[str, ...] = ()
    report_schema_version: str = ""
    scientific_result_schema: str = SCIENTIFIC_RESULT_SCHEMA
    requires_scientific_result: bool = True
    declared_scope_note: str = ""

    def __post_init__(self) -> None:
        for name in ("evaluator_key", "runner_module", "runner_function",
                     "evaluator_version", "capability_class"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in SCOPE_DIMENSIONS:
            values = _tokens(getattr(self, name), name)
            if not values:
                # An undeclared dimension is an unsupported dimension.  This is
                # what prevents "one evaluator fits all questions".
                raise GeneratedQuestionEvaluatorRegistryError(
                    "EVALUATOR_SCOPE_DIMENSION_UNDECLARED:" + name)
            object.__setattr__(self, name, values)
        object.__setattr__(
            self, "required_datasets",
            _tokens(self.required_datasets, "required_datasets"))
        object.__setattr__(
            self, "observation_cells",
            _tokens(self.observation_cells, "observation_cells"))
        if self.scientific_result_schema != SCIENTIFIC_RESULT_SCHEMA:
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_SCIENTIFIC_RESULT_SCHEMA_UNSUPPORTED:"
                + str(self.scientific_result_schema))

    @property
    def qualified_runner(self) -> str:
        return f"{self.runner_module}.{self.runner_function}"

    def identity_material(self) -> dict[str, Any]:
        """Everything that defines the evaluator's governed identity."""
        return {
            "registry_schema": REGISTRY_SCHEMA,
            "evaluator_key": self.evaluator_key,
            "runner_module": self.runner_module,
            "runner_function": self.runner_function,
            "evaluator_version": self.evaluator_version,
            "capability_class": self.capability_class,
            "question_types": list(self.question_types),
            "evidence_classes": list(self.evidence_classes),
            "metric_families": list(self.metric_families),
            "intervention_classes": list(self.intervention_classes),
            "required_datasets": list(self.required_datasets),
            "observation_cells": list(self.observation_cells),
            "report_schema_version": self.report_schema_version,
            "scientific_result_schema": self.scientific_result_schema,
            "requires_scientific_result": self.requires_scientific_result,
        }

    @property
    def evaluator_identity_digest(self) -> str:
        return _digest(self.identity_material())

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evaluator_identity_digest": self.evaluator_identity_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedQuestionEvaluatorRegistration":
        if not isinstance(value, Mapping):
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_REGISTRATION_MALFORMED")
        registration = cls(
            evaluator_key=value.get("evaluator_key"),
            runner_module=value.get("runner_module"),
            runner_function=value.get("runner_function"),
            evaluator_version=value.get("evaluator_version"),
            capability_class=value.get("capability_class"),
            question_types=value.get("question_types") or (),
            evidence_classes=value.get("evidence_classes") or (),
            metric_families=value.get("metric_families") or (),
            intervention_classes=value.get("intervention_classes") or (),
            required_datasets=value.get("required_datasets") or (),
            observation_cells=value.get("observation_cells") or (),
            report_schema_version=str(value.get("report_schema_version") or ""),
            scientific_result_schema=str(
                value.get("scientific_result_schema")
                or SCIENTIFIC_RESULT_SCHEMA),
            requires_scientific_result=bool(
                value.get("requires_scientific_result", True)),
            declared_scope_note=str(value.get("declared_scope_note") or ""),
        )
        declared = str(value.get("evaluator_identity_digest") or "")
        if declared and declared != registration.evaluator_identity_digest:
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_IDENTITY_DIGEST_MISMATCH:" + registration.evaluator_key)
        return registration


@dataclass(frozen=True)
class GeneratedQuestionEvaluatorRequest:
    """The governed semantics used to resolve an evaluator, nothing more."""

    generated_question_id: str
    question_type: str
    evidence_class: str
    observation_cell_identity: str
    metric_family: str = UNKNOWN
    intervention_class: str = UNKNOWN
    required_datasets: tuple[str, ...] = ()
    explicit_evaluator_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_question_id",
                           _text(self.generated_question_id, "generated_question_id"))
        for name in ("question_type", "evidence_class",
                     "observation_cell_identity"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self, "metric_family", str(self.metric_family or UNKNOWN).strip() or UNKNOWN)
        object.__setattr__(
            self, "intervention_class",
            str(self.intervention_class or UNKNOWN).strip() or UNKNOWN)
        object.__setattr__(
            self, "required_datasets",
            _tokens(self.required_datasets, "required_datasets"))
        key = self.explicit_evaluator_key
        object.__setattr__(
            self, "explicit_evaluator_key",
            None if key in (None, "") else str(key).strip())

    def identity_material(self) -> dict[str, Any]:
        return {
            "generated_question_id": self.generated_question_id,
            "question_type": self.question_type,
            "evidence_class": self.evidence_class,
            "observation_cell_identity": self.observation_cell_identity,
            "metric_family": self.metric_family,
            "intervention_class": self.intervention_class,
            "required_datasets": list(self.required_datasets),
            "explicit_evaluator_key": self.explicit_evaluator_key,
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.identity_material())


@dataclass(frozen=True)
class GeneratedQuestionEvaluatorResolution:
    """The deterministic outcome of evaluator resolution."""

    resolved: bool
    reason_code: str
    reason: str
    evaluator_key: str | None = None
    evaluator_identity_digest: str | None = None
    runner_module: str | None = None
    runner_function: str | None = None
    evaluator_version: str | None = None
    capability_class: str | None = None
    required_datasets: tuple[str, ...] = ()
    observation_cells: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "evaluator_key": self.evaluator_key,
            "evaluator_identity_digest": self.evaluator_identity_digest,
            "runner_module": self.runner_module,
            "runner_function": self.runner_function,
            "evaluator_version": self.evaluator_version,
            "capability_class": self.capability_class,
            "required_datasets": list(self.required_datasets),
            "observation_cells": list(self.observation_cells),
            "candidates": list(self.candidates),
        }


def _unresolved(reason_code: str, reason: str,
                candidates: Sequence[str] = ()) -> GeneratedQuestionEvaluatorResolution:
    return GeneratedQuestionEvaluatorResolution(
        resolved=False, reason_code=reason_code, reason=reason,
        candidates=tuple(sorted(set(candidates))),
    )


class GeneratedQuestionEvaluatorRegistry:
    """Persistent, deterministic evaluator capability authority for Q71+."""

    def __init__(
        self, path: Path | str | None = None,
        *, registrations: Sequence[GeneratedQuestionEvaluatorRegistration] = (),
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
        self._by_key: dict[str, GeneratedQuestionEvaluatorRegistration] = {}
        if path is not None:
            self._load()
        for registration in registrations:
            self.register(registration)

    # -- Introspection ---------------------------------------------------
    @property
    def registry_identity(self) -> str:
        return _digest({
            "registry_schema": REGISTRY_SCHEMA,
            "registrations": [
                item.identity_material() for item in self.all()],
        })

    def all(self) -> tuple[GeneratedQuestionEvaluatorRegistration, ...]:
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def registration_for_key(
        self, evaluator_key: str,
    ) -> GeneratedQuestionEvaluatorRegistration | None:
        return self._by_key.get(str(evaluator_key or "").strip())

    def capability_index(self) -> dict[str, str]:
        """Eligibility projection for ``q71_orchestration``.

        Keys are governed matching inputs (evidence class, observation cell,
        question type) so the generation stage can decide eligibility with the
        same authority that will later execute the question.
        """
        index: dict[str, str] = {}
        for registration in self.all():
            for name in ("evidence_classes", "observation_cells", "question_types"):
                for token in getattr(registration, name):
                    index.setdefault(token, registration.evaluator_key)
        return index

    # -- Mutation --------------------------------------------------------
    def register(
        self, registration: GeneratedQuestionEvaluatorRegistration,
    ) -> GeneratedQuestionEvaluatorRegistration:
        if not isinstance(registration, GeneratedQuestionEvaluatorRegistration):
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_REGISTRATION_REQUIRED")
        existing = self._by_key.get(registration.evaluator_key)
        if existing is not None:
            if (existing.evaluator_identity_digest
                    != registration.evaluator_identity_digest):
                raise GeneratedQuestionEvaluatorRegistryError(
                    "EVALUATOR_KEY_REBOUND_WITH_OTHER_SEMANTICS:"
                    + registration.evaluator_key)
            return existing
        for other in self._by_key.values():
            if (other.qualified_runner == registration.qualified_runner
                    and other.evaluator_key != registration.evaluator_key):
                raise GeneratedQuestionEvaluatorRegistryError(
                    "EVALUATOR_RUNNER_BOUND_TO_TWO_KEYS:"
                    + registration.qualified_runner)
        self._by_key[registration.evaluator_key] = registration
        return registration

    def unregister(self, evaluator_key: str) -> None:
        """Retire a registration; historical results keep their own identity."""
        self._by_key.pop(str(evaluator_key or "").strip(), None)

    # -- Resolution ------------------------------------------------------
    def _scope_mismatch(
        self, registration: GeneratedQuestionEvaluatorRegistration,
        request: GeneratedQuestionEvaluatorRequest,
    ) -> str | None:
        """Return the first violated scope dimension, or None when in scope."""
        checks = (
            ("question_types", request.question_type),
            ("evidence_classes", request.evidence_class),
            ("metric_families", request.metric_family),
            ("intervention_classes", request.intervention_class),
        )
        for name, value in checks:
            if value not in getattr(registration, name):
                return name
        if (registration.observation_cells
                and request.observation_cell_identity
                not in registration.observation_cells):
            return "observation_cells"
        if (registration.required_datasets and request.required_datasets
                and tuple(registration.required_datasets)
                != tuple(request.required_datasets)):
            return "required_datasets"
        return None

    def resolve(
        self, request: GeneratedQuestionEvaluatorRequest,
    ) -> GeneratedQuestionEvaluatorResolution:
        """Resolve exactly one evaluator, or fail closed with a reason."""
        if not isinstance(request, GeneratedQuestionEvaluatorRequest):
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_REQUEST_REQUIRED")
        if request.explicit_evaluator_key:
            explicit = self.registration_for_key(request.explicit_evaluator_key)
            if explicit is None:
                return _unresolved(
                    EVALUATOR_KEY_UNKNOWN,
                    "no registration declares evaluator key "
                    + request.explicit_evaluator_key)
            mismatch = self._scope_mismatch(explicit, request)
            if mismatch is not None:
                return _unresolved(
                    EVALUATOR_OUT_OF_DECLARED_SCOPE,
                    f"{explicit.evaluator_key} does not declare scope dimension "
                    f"{mismatch} for this question", (explicit.evaluator_key,))
            return self._resolved(explicit, RESOLVED_EXPLICIT_KEY)
        matching = [
            registration for registration in self.all()
            if self._scope_mismatch(registration, request) is None
        ]
        if not matching:
            return _unresolved(
                NO_REGISTERED_EVALUATOR,
                "no registration declares the scope of this generated question")
        specificity = max(len(item.observation_cells) for item in matching)
        best = [
            item for item in matching
            if len(item.observation_cells) == specificity
        ]
        if len(best) != 1:
            return _unresolved(
                AMBIGUOUS_EVALUATOR_MATCH,
                "more than one registration matches with equal specificity",
                [item.evaluator_key for item in best])
        return self._resolved(best[0], RESOLVED_UNIQUE_SCOPE_MATCH)

    def _resolved(
        self, registration: GeneratedQuestionEvaluatorRegistration, reason_code: str,
    ) -> GeneratedQuestionEvaluatorResolution:
        return GeneratedQuestionEvaluatorResolution(
            resolved=True, reason_code=reason_code,
            reason="resolved to " + registration.evaluator_key,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            runner_module=registration.runner_module,
            runner_function=registration.runner_function,
            evaluator_version=registration.evaluator_version,
            capability_class=registration.capability_class,
            required_datasets=tuple(registration.required_datasets),
            observation_cells=tuple(registration.observation_cells),
        )

    # -- Persistence -----------------------------------------------------
    def _document(self) -> dict[str, Any]:
        return {
            "format": REGISTRY_SCHEMA,
            "schema_version": 1,
            "registrations": [item.to_dict() for item in self.all()],
        }

    def save(self) -> None:
        payload = canonical_json(self._document()) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_REGISTRY_UNREADABLE") from exc
        if (not isinstance(document, Mapping)
                or document.get("format") != REGISTRY_SCHEMA
                or document.get("schema_version") != 1
                or not isinstance(document.get("registrations"), list)):
            raise GeneratedQuestionEvaluatorRegistryError(
                "EVALUATOR_REGISTRY_INVALID")
        for raw in document["registrations"]:
            self.register(
                GeneratedQuestionEvaluatorRegistration.from_dict(raw))


def evaluator_request_from_specification(
    generated_question_id: str, specification: Mapping[str, Any],
) -> GeneratedQuestionEvaluatorRequest:
    """Derive the resolution request from a persisted generated-question spec.

    Unknown semantics stay explicitly UNKNOWN; they are never guessed.
    """
    cell = specification.get("observation_cell")
    cell = dict(cell) if isinstance(cell, Mapping) else {}
    requirements = specification.get("evidence_requirements")
    requirements = dict(requirements) if isinstance(requirements, Mapping) else {}
    datasets = specification.get("evidence_datasets")
    if not isinstance(datasets, Sequence) or isinstance(datasets, (str, bytes)):
        datasets = ()
    return GeneratedQuestionEvaluatorRequest(
        generated_question_id=generated_question_id,
        question_type=str(specification.get("question_type") or UNKNOWN),
        evidence_class=str(
            cell.get("evidence_class") or requirements.get("evidence_class")
            or UNKNOWN),
        observation_cell_identity=str(
            cell.get("cell_identity") or specification.get("target_ref")
            or UNKNOWN),
        metric_family=str(specification.get("metric_family") or UNKNOWN),
        intervention_class=str(
            specification.get("intervention_class") or UNKNOWN),
        required_datasets=tuple(str(item) for item in datasets),
        explicit_evaluator_key=specification.get("evaluator_key") or None,
    )


__all__ = [
    "AMBIGUOUS_EVALUATOR_MATCH",
    "DEFAULT_REGISTRY_PATH",
    "EVALUATOR_KEY_UNKNOWN",
    "EVALUATOR_OUT_OF_DECLARED_SCOPE",
    "GeneratedQuestionEvaluatorRegistration",
    "GeneratedQuestionEvaluatorRegistry",
    "GeneratedQuestionEvaluatorRegistryError",
    "GeneratedQuestionEvaluatorRequest",
    "GeneratedQuestionEvaluatorResolution",
    "NO_REGISTERED_EVALUATOR",
    "REGISTRY_SCHEMA",
    "RESOLUTION_REASON_CODES",
    "RESOLVED_EXPLICIT_KEY",
    "RESOLVED_UNIQUE_SCOPE_MATCH",
    "SCOPE_DIMENSIONS",
    "UNKNOWN",
    "evaluator_request_from_specification",
]
