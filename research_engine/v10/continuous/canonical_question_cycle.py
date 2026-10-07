"""Snapshot-bound execution cycle for the canonical 70-question programme.

The only evidence basis accepted here is an already verified immutable
``InvestigationSnapshot``.  The legacy 51-question coverage bank is neither
imported nor consulted.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import builtins
import gc
import importlib
import inspect
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence
from unittest.mock import patch

from research_engine.control_plane.evidence_resolver import (
    EvidenceResolution,
    EvidenceSnapshot,
    resolve_question_evidence,
)
from research_engine.control_plane.stage4_dataset_snapshot import fingerprint
from research_engine.control_plane.stage4_implementation_repairs import (
    build_current_population_authority,
)
from research_engine.data_access.shadow_runtime_ingestion import (
    reconstruct_completed_shadow_trades,
)
from research_engine.registry.baseline_manifest import (
    BASELINE_QUESTION_IDS,
    BASELINE_QUESTION_SET,
)
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.research_question_models import ResearchQuestion
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionCycleResult,
    CanonicalQuestionResult,
    DEFAULT_QUESTION_CYCLE_DIRECTORY,
    QUESTION_CYCLE_SCHEMA,
    QuestionCycleStore,
    atomic_json,
    immutable_json,
)
from research_engine.v10.continuous.research_work_queue import (
    DEEP,
    COMPLETED as DEEP_COMPLETED,
    FAILED as DEEP_FAILED,
    PENDING as DEEP_PENDING_STATE,
    RUNNING as DEEP_RUNNING_STATE,
    SUPERSEDED as DEEP_SUPERSEDED,
    ResearchExecutionPolicy,
    ResearchWorkQueueStore,
    deep_work_job,
)
from research_engine.v10.continuous.evaluation_identity import (
    REQUIRES_REEVALUATION as REQUIRES_REEVALUATION_VALUE,
    evaluation_identities,
    explain_staleness,
    is_result_current,
    stale_question_ids,
)
from research_engine.v10.continuous.cycle_state import process_memory_bytes
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    MANIFEST_DIRECTORY,
    InvestigationSnapshot,
    SnapshotBoundDatasetReader,
    load_investigation_snapshot_id,
)


REGISTRY_VERSION = "research_question_registry_v1"
EXPECTED_QUESTION_COUNT = 70

AFFECTED = "AFFECTED"
UNAFFECTED = "UNAFFECTED"
REQUIRES_RECHECK = "REQUIRES_RECHECK"
REQUIRES_REEVALUATION = REQUIRES_REEVALUATION_VALUE
UNRUNNABLE = "UNRUNNABLE"
ALIAS_OR_SUPERSEDED = "ALIAS_OR_SUPERSEDED"

COMPLETE = "COMPLETE"
NEGATIVE_RESULT = "NEGATIVE_RESULT"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
WAITING_FOR_DATA = "WAITING_FOR_DATA"
CANNOT_KNOW_YET = "CANNOT_KNOW_YET"
IMPLEMENTATION_BLOCKED = "IMPLEMENTATION_BLOCKED"
BLOCKED = "BLOCKED"
UNIMPLEMENTED = "UNIMPLEMENTED"
INVALID = "INVALID"

_REENTRY_STATUSES = frozenset({
    INSUFFICIENT_DATA, WAITING_FOR_DATA, CANNOT_KNOW_YET,
    BLOCKED, IMPLEMENTATION_BLOCKED, INVALID,
})
_SCIENTIFIC_REENTRY_STATUSES = frozenset({
    INSUFFICIENT_DATA, WAITING_FOR_DATA, CANNOT_KNOW_YET, BLOCKED,
})

_GOVERNED_REASON_REQUIRED_QUESTION_IDS = frozenset({
    "E3", "S2", "S3", "S5", "S6", "S7", "EXEC1", "G1",
})

# Registry logical source -> exact Block 1 physical snapshot authority. ``None``
# means the source is not in the common investigation snapshot and therefore
# cannot be silently loaded from current state.
_SOURCE_TO_SNAPSHOT_DATASET: dict[str, str | None] = {
    "shadow_trades": "shadow_runtime",
    "decision_trace": "decision_trace",
    "trade_truth": "trade_truth",
    "market_context": "market_context",
    "execution_context": "execution_context",
    "execution_results_v1": "execution_results",
    "execution_results": "execution_results",
    "protection_audit_v1": "protection_audit",
    "protection_audit": "protection_audit",
    "execution_attempts_v1": "execution_attempts",
    "execution_attempts": "execution_attempts",
    "risk_deviation_v1": "risk_deviation",
    "risk_deviation": "risk_deviation",
    "management_actions": None,
    "horizon_candidates": None,
    "strategy_candidates": None,
    "equity_curve": None,
    "slippage_journal": None,
    "portfolio_rankings": None,
    "portfolio_shadow": None,
    "opportunities": None,
    "assessments": None,
}


class CanonicalQuestionCycleError(RuntimeError):
    """System-level cycle failure; no successful cycle may be reported."""


class SnapshotEscapeError(RuntimeError):
    """A runner attempted evidence access outside the frozen snapshot."""


@dataclass(frozen=True)
class SnapshotQuestionExecutionContext:
    snapshot_id: str
    fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    predecessor_snapshot_id: str | None
    exact_membership: dict[str, tuple[dict[str, Any], ...]]
    dataset_status: dict[str, str]
    changed_datasets: tuple[str, ...]
    changed_datasets_known: bool
    datasets: dict[str, list[dict[str, Any]]]
    reader: SnapshotBoundDatasetReader
    runner_artifacts: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def evidence_references(self, question: ResearchQuestion) -> tuple[dict[str, Any], ...]:
        references: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in question.data_sources:
            physical = _SOURCE_TO_SNAPSHOT_DATASET.get(source.value)
            if physical is None or physical in seen:
                continue
            seen.add(physical)
            for item in self.exact_membership.get(physical, ()):
                references.append({"dataset": physical, **dict(item)})
        return tuple(references)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _frontier_input(value: Any) -> tuple[str, str | None, tuple[str, ...], bool]:
    if isinstance(value, str):
        return value, None, (), False
    snapshot_id = str(_value(value, "snapshot_id") or "")
    predecessor = _value(value, "predecessor_snapshot_id")
    changed = tuple(str(item) for item in (_value(value, "changed_datasets", ()) or ()))
    return snapshot_id, (None if predecessor is None else str(predecessor)), changed, True


def _validate_registry(registry: Sequence[ResearchQuestion]) -> tuple[ResearchQuestion, ...]:
    questions = tuple(registry)
    ids = [str(question.id) for question in questions]
    if len(ids) != len(set(ids)):
        raise CanonicalQuestionCycleError("DUPLICATE_CANONICAL_QUESTION_ID")
    if len(ids) != EXPECTED_QUESTION_COUNT:
        raise CanonicalQuestionCycleError(
            f"CANONICAL_QUESTION_COUNT_MISMATCH:{len(ids)}!={EXPECTED_QUESTION_COUNT}")
    if set(ids) != BASELINE_QUESTION_SET:
        missing = sorted(BASELINE_QUESTION_SET - set(ids))
        extra = sorted(set(ids) - BASELINE_QUESTION_SET)
        raise CanonicalQuestionCycleError(
            "CANONICAL_QUESTION_SET_MISMATCH:missing=" + repr(missing) + ":extra=" + repr(extra))
    by_id = {question.id: question for question in questions}
    return tuple(by_id[qid] for qid in BASELINE_QUESTION_IDS)


def _load_registry() -> tuple[ResearchQuestion, ...]:
    try:
        from research_engine.registry.research_question_registry import REGISTRY
    except Exception as exc:
        raise CanonicalQuestionCycleError(
            "CANONICAL_QUESTION_REGISTRY_LOAD_FAILED:" + f"{type(exc).__name__}:{exc}") from exc
    return _validate_registry(REGISTRY)


def _registry_identity(
    questions: Sequence[ResearchQuestion], definitions: Mapping[str, Any],
) -> str:
    return fingerprint({
        "registry_version": REGISTRY_VERSION,
        "questions": [question.to_dict() for question in questions],
        "definitions": [definitions[question.id].to_dict() for question in questions],
    })


def _dataset_material(snapshot: InvestigationSnapshot) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        binding.dataset: tuple(item.to_dict() for item in binding.objects)
        for binding in snapshot.datasets
    }


def _build_context(
    snapshot: InvestigationSnapshot,
    reader: SnapshotBoundDatasetReader,
    *,
    predecessor_snapshot_id: str | None,
    changed_datasets: tuple[str, ...],
    changed_datasets_known: bool,
) -> SnapshotQuestionExecutionContext:
    bindings = {item.dataset: item for item in snapshot.datasets}
    physical: dict[str, list[dict[str, Any]]] = {}
    status: dict[str, str] = {}
    for dataset in BOUND_DATASETS:
        binding = bindings[dataset]
        if binding.presence == "PRESENT":
            physical[dataset] = reader.cycle_cached_dataset(dataset)
            status[dataset] = "READY"
        else:
            status[dataset] = "MISSING"

    datasets: dict[str, list[dict[str, Any]]] = {}
    for dataset, rows in physical.items():
        datasets[dataset] = rows
    for alias, physical_name in _SOURCE_TO_SNAPSHOT_DATASET.items():
        if physical_name is not None and physical_name in physical:
            datasets[alias] = physical[physical_name]
    if "shadow_runtime" in physical:
        datasets["shadow_trades"] = reconstruct_completed_shadow_trades(
            [dict(row) for row in physical["shadow_runtime"]])

    return SnapshotQuestionExecutionContext(
        snapshot_id=snapshot.snapshot_id,
        fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_start=snapshot.start_date,
        frontier_end=snapshot.end_date,
        predecessor_snapshot_id=predecessor_snapshot_id,
        exact_membership=_dataset_material(snapshot),
        dataset_status=status,
        changed_datasets=changed_datasets,
        changed_datasets_known=changed_datasets_known,
        datasets=datasets,
        reader=reader,
    )


def _question_dependencies(question: ResearchQuestion) -> tuple[set[str], bool]:
    dependencies: set[str] = set()
    ambiguous = False
    for source in question.data_sources:
        name = source.value
        if name not in _SOURCE_TO_SNAPSHOT_DATASET:
            ambiguous = True
            continue
        physical = _SOURCE_TO_SNAPSHOT_DATASET[name]
        if physical is None:
            ambiguous = True
        else:
            dependencies.add(physical)
    return dependencies, ambiguous


def _deep_dependency_identity(
    question: ResearchQuestion,
    context: SnapshotQuestionExecutionContext,
    previous_results: Mapping[str, CanonicalQuestionResult],
) -> tuple[str, dict[str, str]]:
    datasets, ambiguous = _question_dependencies(question)
    prerequisite_identities = {
        dependency: previous_results[dependency].result_id
        for dependency in question.depends_on
        if dependency in previous_results
    }
    material = {
        "question_id": question.id,
        "datasets": {
            dataset: list(context.exact_membership.get(dataset, ()))
            for dataset in sorted(datasets)
        },
        "ambiguous_dependency": ambiguous,
        "prerequisite_identities": prerequisite_identities,
    }
    return fingerprint(material), prerequisite_identities


def plan_affected_questions(
    questions: Sequence[ResearchQuestion],
    context: SnapshotQuestionExecutionContext,
    previous_projection: Mapping[str, Any] | None,
    identities: Mapping[str, Mapping[str, Any]] | None = None,
    evaluation_only_question_ids: Sequence[str] | None = None,
) -> dict[str, str]:
    """Classify all 70 questions from registry dependencies and Block 1 delta.

    A question whose recorded result is no longer current under the authoritative
    governed evaluator is REQUIRES_REEVALUATION even when its evidence snapshot
    did not change.  This is the distinction between evidence identity and
    evaluation identity: unchanged evidence does not imply an unchanged
    interpretation.
    """
    previous_questions = (previous_projection or {}).get("questions") or {}
    first_snapshot = not previous_questions
    evaluation_only = (
        None if evaluation_only_question_ids is None
        else frozenset(str(item) for item in evaluation_only_question_ids)
    )
    plan: dict[str, str] = {}
    for question in questions:
        previous_entry = previous_questions.get(question.id) or {}
        previous_result = previous_entry.get("result") or {}
        if (
            not first_snapshot
            and evaluation_only is not None
            and question.id not in evaluation_only
        ):
            plan[question.id] = UNAFFECTED
            continue
        if question.scientific_owner_id:
            plan[question.id] = ALIAS_OR_SUPERSEDED
            continue
        if not question.runner_module or not question.runner_function:
            plan[question.id] = UNRUNNABLE
            continue
        if not first_snapshot and evaluation_only is not None:
            plan[question.id] = REQUIRES_REEVALUATION
            continue
        if not first_snapshot and identities is not None:
            current = identities.get(question.id)
            if current is not None:
                is_current, _ = is_result_current(
                    previous_result.get("evaluation_identity"), current)
                if not is_current:
                    plan[question.id] = REQUIRES_REEVALUATION
                    continue
        if first_snapshot:
            plan[question.id] = AFFECTED
            continue
        dependencies, ambiguous = _question_dependencies(question)
        previous_status = str(previous_result.get("status") or "")
        if not context.changed_datasets_known:
            plan[question.id] = REQUIRES_RECHECK
        elif dependencies & set(context.changed_datasets):
            plan[question.id] = AFFECTED
        elif previous_status in _REENTRY_STATUSES and ambiguous:
            # Existing governed re-entry states are rechecked only on a new
            # snapshot; this does not issue or bypass a scientific authorization.
            plan[question.id] = REQUIRES_RECHECK
        elif ambiguous:
            plan[question.id] = REQUIRES_RECHECK
        else:
            plan[question.id] = UNAFFECTED

    # Registry ``depends_on`` is a governed dependency edge, not a heuristic.
    # Close the plan transitively so a prerequisite selected for re-entry also
    # rechecks its dependants, even when their direct evidence datasets did not
    # change.  Unrelated questions remain retained.
    selected = {AFFECTED, REQUIRES_RECHECK, REQUIRES_REEVALUATION}
    changed = True
    while changed:
        changed = False
        for question in questions:
            if plan.get(question.id) != UNAFFECTED:
                continue
            if any(plan.get(dependency) in selected
                   for dependency in question.depends_on):
                plan[question.id] = REQUIRES_RECHECK
                changed = True
    return plan


def _discover_runners(questions: Sequence[ResearchQuestion]) -> tuple[
        dict[str, Callable[..., Mapping[str, Any]]], dict[str, str]]:
    runners: dict[str, Callable[..., Mapping[str, Any]]] = {}
    failures: dict[str, str] = {}
    for question in questions:
        if not question.runner_module or not question.runner_function:
            continue
        try:
            module = importlib.import_module(question.runner_module)
            runner = getattr(module, question.runner_function)
            if not callable(runner):
                raise TypeError("registered runner is not callable")
            runners[question.id] = runner
        except Exception as exc:
            failures[question.id] = f"{type(exc).__name__}:{exc}"
    return runners, failures


@contextmanager
def _snapshot_only_runner_environment(reader: SnapshotBoundDatasetReader):
    """Bind shared readers and deny direct filesystem/new-live-S3 evidence reads."""
    import research_engine.data_access.s3_source as s3_module

    previous = s3_module._default_source
    original_open = builtins.open
    original_path_open = Path.open

    def denied_open(*args: Any, **kwargs: Any):
        raise SnapshotEscapeError("RUNNER_FILESYSTEM_EVIDENCE_ACCESS_FORBIDDEN")

    def denied_path_open(*args: Any, **kwargs: Any):
        raise SnapshotEscapeError("RUNNER_FILESYSTEM_EVIDENCE_ACCESS_FORBIDDEN")

    def denied_client(*args: Any, **kwargs: Any):
        raise SnapshotEscapeError("RUNNER_LIVE_S3_ACCESS_FORBIDDEN")

    s3_module.set_default_source(reader)  # every sanctioned read is exact-bound
    try:
        with patch.object(builtins, "open", denied_open), \
                patch.object(Path, "open", denied_path_open), \
                patch.object(s3_module.S3ResearchDataSource, "_get_client", denied_client):
            yield
    finally:
        # Restore the exact previous run-scoped source without constructing a
        # new/current S3 reader as a side effect.
        s3_module.set_default_source(previous)
        assert builtins.open is original_open
        assert Path.open is original_path_open


def _runner_kwargs(
    runner: Callable[..., Any], question: ResearchQuestion,
    population: list[dict[str, Any]], context: SnapshotQuestionExecutionContext,
) -> dict[str, Any]:
    signature = inspect.signature(runner)
    kwargs: dict[str, Any] = {}
    shadow_population = context.datasets.get("shadow_trades", [])
    common: dict[str, Any] = {
        "shadow_trades": shadow_population,
        "records": population if question.id in {"L3", "L6", "EX2"} else shadow_population,
        "governed_records": population if question.id in {"L3", "L6", "EX2"} else shadow_population,
        "decision_records": context.datasets.get("decision_trace", []),
        "outcome_records": shadow_population,
        "execution_results": context.datasets.get("execution_results", []),
        "execution_contexts": context.datasets.get("execution_context", []),
        "decision_traces": context.datasets.get("decision_trace", []),
        "datasets": context.datasets,
        "source": context.reader,
        "persist": False,
        "governed_evidence": context.runner_artifacts.get(
            "governed_execution_evidence"),
        "governed_exit_evidence": context.runner_artifacts.get(
            "governed_exit_evidence"),
        "governed_risk_evidence": context.runner_artifacts.get(
            "governed_risk_evidence"),
        "governed_lineage_population": context.runner_artifacts.get(
            "governed_lineage_population"),
        "population_authority": context.runner_artifacts.get(
            "current_population_authority"),
    }
    unresolved: list[str] = []
    for name, parameter in signature.parameters.items():
        if name in common:
            kwargs[name] = common[name]
        elif parameter.default is inspect.Parameter.empty and parameter.kind not in {
                inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            unresolved.append(name)
    if unresolved:
        raise SnapshotEscapeError(
            "RUNNER_SNAPSHOT_INJECTION_UNSUPPORTED:"
            + question.id + ":" + ",".join(unresolved))
    return kwargs


def _execute_runner(
    runner: Callable[..., Mapping[str, Any]], question: ResearchQuestion,
    population: list[dict[str, Any]], context: SnapshotQuestionExecutionContext,
) -> Mapping[str, Any]:
    kwargs = _runner_kwargs(runner, question, population, context)
    with _snapshot_only_runner_environment(context.reader):
        report = runner(**kwargs)
    if not isinstance(report, Mapping):
        raise TypeError("RUNNER_RETURNED_NON_MAPPING")
    return report


def _minimum_required(question: ResearchQuestion, definition: Any) -> int | None:
    value = getattr(definition, "minimum_sample", None)
    if value is not None:
        return int(value)
    for rule in question.validation_rules:
        if rule.field == "sample_size" and rule.operator == ">=":
            return int(rule.threshold)
    return None


def _sample_n(report: Mapping[str, Any], resolution: EvidenceResolution) -> int | None:
    candidates: list[Any] = [
        report.get("sample_n"), report.get("sample_size"), report.get("n"),
    ]
    dataset = report.get("dataset")
    if isinstance(dataset, Mapping):
        candidates.extend((dataset.get("sample_size"), dataset.get("r_multiples_used")))
    sample_sizes = report.get("sample_sizes")
    if isinstance(sample_sizes, Mapping):
        candidates.extend((sample_sizes.get("total"), sample_sizes.get("n")))
    for value in candidates:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return resolution.usable_count


def _normalise_status(report: Mapping[str, Any]) -> str:
    raw = str(report.get("status") or "").strip().upper()
    aliases = {
        "WAITING_DATA": WAITING_FOR_DATA,
        "WAITING": WAITING_FOR_DATA,
        "ERROR": INVALID,
        "MALFORMED_REPORT": INVALID,
        "NO_EFFECT": NEGATIVE_RESULT,
    }
    status = aliases.get(raw, raw)
    allowed = {
        COMPLETE, NEGATIVE_RESULT, INSUFFICIENT_DATA, WAITING_FOR_DATA,
        CANNOT_KNOW_YET, BLOCKED, IMPLEMENTATION_BLOCKED, UNIMPLEMENTED, INVALID,
    }
    return status if status in allowed else INVALID


def _substantive_answer(report: Mapping[str, Any]) -> Any:
    for name in ("conclusion", "outcome", "answer", "recommendation"):
        value = report.get(name)
        if value not in (None, "", {}, []):
            return value
    return None


def _key_metrics(report: Mapping[str, Any], resolution: EvidenceResolution) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for container_name in ("key_metrics", "primary_metrics", "metrics"):
        container = report.get(container_name)
        if isinstance(container, Mapping):
            metrics.update({str(key): value for key, value in container.items()})
    for name in (
        "expectancy", "expectancy_r", "profit_factor", "pf", "win_rate",
        "drawdown", "max_drawdown", "effect_size", "confidence_interval",
    ):
        if name in report:
            metrics[name] = report[name]
    for name, value in resolution.metrics.items():
        metrics.setdefault(name, value)
    return dict(sorted(metrics.items()))


def _sequence(value: Any) -> tuple[Any, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _missing_evidence(resolution: EvidenceResolution, report: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    missing = {
        item.name for item in resolution.requirements
        if item.satisfied is False or (item.blocking and item.satisfied is not True)
    }
    if report is not None:
        missing.update(str(item) for item in _sequence(report.get("missing_evidence")))
    return tuple(sorted(missing))


def _base_result(
    question: ResearchQuestion, definition: Any,
    context: SnapshotQuestionExecutionContext,
    previous: CanonicalQuestionResult | None,
    evaluation_identity: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> CanonicalQuestionResult:
    minimum = _minimum_required(question, definition)
    values: dict[str, Any] = {
        "question_id": question.id,
        "question_version": f"{REGISTRY_VERSION}:{definition.definition_version}",
        "snapshot_id": context.snapshot_id,
        "snapshot_fingerprint": context.fingerprint,
        "investigation_epoch": context.investigation_epoch,
        # Snapshot creation is immutable and makes same-snapshot execution
        # deterministic; wall-clock execution time would not.
        "evaluated_at": context.reader.snapshot.created_at,
        "status": INVALID,
        "minimum_required_n": minimum,
        "previous_snapshot_id": None if previous is None else previous.snapshot_id,
        "previous_result_id": None if previous is None else previous.result_id,
        "runner": (
            f"{question.runner_module}.{question.runner_function}"
            if question.runner_module and question.runner_function else None),
        "runner_version": f"{REGISTRY_VERSION}:{definition.definition_version}",
        # Governed evaluation identity: the authoritative interpretation that
        # produced this result.  Distinct from snapshot/evidence identity.
        # The digest is carried inside the identity so the sub-object is
        # self-contained and directly comparable against current identity.
        "evaluation_identity": (
            None if evaluation_identity is None else dict(evaluation_identity)),
        "evaluation_identity_digest": (
            None if evaluation_identity is None
            else evaluation_identity.get("evaluation_identity_digest")),
    }
    values.update(overrides)
    return CanonicalQuestionResult(**values)


def _scientific_payload(result: CanonicalQuestionResult) -> dict[str, Any]:
    value = result.to_dict()
    for name in (
        "result_id", "snapshot_id", "snapshot_fingerprint", "investigation_epoch",
        "evaluated_at", "changed_since_previous", "previous_snapshot_id",
        "previous_result_id",
    ):
        value.pop(name, None)
    return value


def question_result_delta(
    previous: CanonicalQuestionResult | None,
    current: CanonicalQuestionResult,
) -> dict[str, Any]:
    if previous is None:
        return {
            "first_result": True,
            "status_changed": True,
            "substantive_answer_changed": current.substantive_answer is not None,
            "sample_n_changed": current.sample_n is not None,
            "sample_deficit_changed": current.sample_deficit is not None,
            "key_metrics_changed": bool(current.key_metrics),
            "evidence_availability_changed": bool(current.missing_evidence),
            "implementation_state_changed": True,
            "confidence_changed": current.confidence is not None,
            "statistical_output_changed": current.statistical_output is not None,
            "limitations_changed": bool(current.limitations),
            "reason_changed": current.reason_code is not None,
            "failure_reason_changed": current.failure_reason is not None,
            "authority_changed": True,
            "unchanged": False,
        }
    delta = {
        "first_result": False,
        "status_changed": previous.status != current.status,
        "substantive_answer_changed": previous.substantive_answer != current.substantive_answer,
        "sample_n_changed": previous.sample_n != current.sample_n,
        "sample_deficit_changed": previous.sample_deficit != current.sample_deficit,
        "key_metrics_changed": previous.key_metrics != current.key_metrics,
        "evidence_availability_changed": (
            previous.missing_evidence != current.missing_evidence
            or previous.evidence_datasets != current.evidence_datasets),
        "implementation_state_changed": (
            previous.implementation_status != current.implementation_status),
        "confidence_changed": previous.confidence != current.confidence,
        "statistical_output_changed": (
            previous.statistical_output != current.statistical_output),
        "limitations_changed": previous.limitations != current.limitations,
        "reason_changed": (
            previous.reason_code != current.reason_code
            or previous.reason != current.reason
            or previous.reason_details != current.reason_details
        ),
        "failure_reason_changed": previous.failure_reason != current.failure_reason,
        # Evaluation identity is deliberately excluded: a new evaluator may
        # reproduce the same scientific state without causing downstream churn.
        "authority_changed": (
            previous.question_version != current.question_version
            or previous.runner_version != current.runner_version),
    }
    delta["unchanged"] = not any(delta.values())
    return delta


def _result_from_evidence_gap(
    question: ResearchQuestion, definition: Any,
    resolution: EvidenceResolution, context: SnapshotQuestionExecutionContext,
    previous: CanonicalQuestionResult | None,
    evaluation_identity: Mapping[str, Any] | None = None,
) -> CanonicalQuestionResult:
    missing = _missing_evidence(resolution)
    unavailable_datasets = [
        str(item.get("source")) for item in resolution.sources
        if not item.get("available")
    ]
    status = WAITING_FOR_DATA if unavailable_datasets else INSUFFICIENT_DATA
    sample = resolution.usable_count
    minimum = _minimum_required(question, definition)
    deficit = None if minimum is None or sample is None else max(0, minimum - sample)
    return _base_result(
        question, definition, context, previous, evaluation_identity,
        status=status,
        sample_n=sample,
        sample_deficit=deficit,
        key_metrics=dict(sorted(resolution.metrics.items())),
        evidence_datasets=tuple(source.value for source in question.data_sources),
        evidence_references=context.evidence_references(question),
        limitations=(resolution.error,) if resolution.error else (),
        missing_evidence=missing,
        implementation_status="IMPLEMENTED_WAITING_EVIDENCE",
        failure_reason=resolution.error or None,
    )


def _normalise_report(
    question: ResearchQuestion, definition: Any,
    report: Mapping[str, Any], resolution: EvidenceResolution,
    context: SnapshotQuestionExecutionContext,
    previous: CanonicalQuestionResult | None,
    evaluation_identity: Mapping[str, Any] | None = None,
) -> CanonicalQuestionResult:
    sample = _sample_n(report, resolution)
    minimum = _minimum_required(question, definition)
    deficit = None if minimum is None or sample is None else max(0, minimum - sample)
    limitations = (
        *_sequence(report.get("limitations")),
        *_sequence(report.get("warnings")),
        *_sequence(report.get("research_gaps")),
    )
    status = _normalise_status(report)
    reason_code_value = report.get("reason_code")
    legacy_reason_value = report.get("reason")
    reason_code = str(reason_code_value).strip() if reason_code_value not in (None, "") else None
    if reason_code is None and legacy_reason_value not in (None, ""):
        reason_code = str(legacy_reason_value).strip()
    reason_value = report.get("reason")
    reason = str(reason_value).strip() if reason_value not in (None, "") else None
    reason_details = report.get("reason_details")
    governed_reason_required = (
        question.id in _GOVERNED_REASON_REQUIRED_QUESTION_IDS
        and status in {INSUFFICIENT_DATA, WAITING_FOR_DATA, CANNOT_KNOW_YET, BLOCKED}
    )
    failure = None
    if status in {IMPLEMENTATION_BLOCKED, INVALID}:
        failure = str(report.get("failure_reason") or report.get("error") or
                      report.get("reason") or report.get("blocked_reason") or
                      ("RUNNER_STATUS:BLOCKED" if status == IMPLEMENTATION_BLOCKED
                       else "RUNNER_STATUS_UNKNOWN_OR_INVALID"))
    if governed_reason_required and reason_code is None:
        status = INVALID
        failure = "RUNNER_REPORT_MISSING_GOVERNED_REASON"
    return _base_result(
        question, definition, context, previous, evaluation_identity,
        status=status,
        substantive_answer=_substantive_answer(report),
        sample_n=sample,
        sample_deficit=deficit,
        key_metrics=_key_metrics(report, resolution),
        confidence=report.get("confidence"),
        statistical_output=(report.get("statistical_output") or report.get("statistics")),
        evidence_datasets=tuple(source.value for source in question.data_sources),
        evidence_references=context.evidence_references(question),
        limitations=tuple(limitations),
        missing_evidence=_missing_evidence(resolution, report),
        implementation_status=("IMPLEMENTED" if status != IMPLEMENTATION_BLOCKED
                               else "IMPLEMENTATION_BLOCKED"),
        reason_code=reason_code,
        reason=reason,
        reason_details=reason_details,
        failure_reason=failure,
    )


def _previous_results(projection: Mapping[str, Any] | None) -> dict[str, CanonicalQuestionResult]:
    output: dict[str, CanonicalQuestionResult] = {}
    for question_id, entry in ((projection or {}).get("questions") or {}).items():
        output[str(question_id)] = CanonicalQuestionResult.from_dict(entry["result"])
    return output


def _status_counts(results: Mapping[str, CanonicalQuestionResult]) -> dict[str, int]:
    statuses = [result.status for result in results.values()]
    return {
        "complete": statuses.count(COMPLETE),
        "negative": statuses.count(NEGATIVE_RESULT),
        "insufficient": statuses.count(INSUFFICIENT_DATA),
        "waiting": statuses.count(WAITING_FOR_DATA) + statuses.count(CANNOT_KNOW_YET),
        "blocked": (
            statuses.count(BLOCKED)
            + statuses.count(IMPLEMENTATION_BLOCKED)
            + statuses.count(INVALID)
        ),
        "unimplemented": statuses.count(UNIMPLEMENTED),
        "alias": statuses.count(ALIAS_OR_SUPERSEDED),
    }


def run_canonical_question_cycle(
    frontier_result_or_snapshot_id: Any,
    *,
    source: Any | None = None,
    manifest_directory: Path | str = MANIFEST_DIRECTORY,
    state_directory: Path | str = DEFAULT_QUESTION_CYCLE_DIRECTORY,
    registry: Sequence[ResearchQuestion] | None = None,
    runners: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
    runner_executor: Callable[[Callable[..., Mapping[str, Any]], ResearchQuestion,
                               list[dict[str, Any]], SnapshotQuestionExecutionContext],
                              Mapping[str, Any]] | None = None,
    store: QuestionCycleStore | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    evaluation_only_question_ids: Sequence[str] | None = None,
    deep_work_store: ResearchWorkQueueStore | None = None,
    execution_policy: ResearchExecutionPolicy | None = None,
    force_inline_question_ids: Sequence[str] = (),
    execution_revision: str | None = None,
) -> CanonicalQuestionCycleResult:
    """Execute/retain exactly one result for every canonical question."""
    stage_started = time.perf_counter()

    def emit(**values: Any) -> None:
        if progress_callback is not None:
            progress_callback(values)

    snapshot_id, predecessor_hint, changed_datasets, changed_known = _frontier_input(
        frontier_result_or_snapshot_id)
    if not snapshot_id:
        raise CanonicalQuestionCycleError("BLOCK1_HANDOFF_WITHOUT_SNAPSHOT_ID")
    emit(phase="LOADING_SNAPSHOT", snapshot_id=snapshot_id)
    try:
        snapshot = load_investigation_snapshot_id(
            snapshot_id, manifest_directory=Path(manifest_directory))
        reader = SnapshotBoundDatasetReader(snapshot, source=source)
    except Exception as exc:
        raise CanonicalQuestionCycleError(
            "VERIFIED_SNAPSHOT_RESOLUTION_FAILED:" + f"{type(exc).__name__}:{exc}") from exc
    rss, peak = process_memory_bytes()
    emit(
        phase="SNAPSHOT_LOADED",
        snapshot_load_elapsed_seconds=round(time.perf_counter() - stage_started, 6),
        snapshot_records_by_dataset=reader.reads_by_dataset,
        snapshot_records_materialized=sum(reader.reads_by_dataset.values()),
        process_rss_bytes=rss,
        process_peak_rss_bytes=peak,
    )
    supplied_fingerprint = _value(frontier_result_or_snapshot_id, "fingerprint")
    supplied_epoch = _value(frontier_result_or_snapshot_id, "investigation_epoch")
    if supplied_fingerprint and str(supplied_fingerprint) != snapshot.snapshot_fingerprint:
        raise CanonicalQuestionCycleError("BLOCK1_SNAPSHOT_FINGERPRINT_MISMATCH")
    if supplied_epoch and str(supplied_epoch) != snapshot.evidence_epoch:
        raise CanonicalQuestionCycleError("BLOCK1_INVESTIGATION_EPOCH_MISMATCH")

    try:
        questions = _validate_registry(registry) if registry is not None else _load_registry()
        definitions = build_definitions_from_registry(questions)
    except CanonicalQuestionCycleError:
        raise
    except Exception as exc:
        raise CanonicalQuestionCycleError(
            "CANONICAL_QUESTION_REGISTRY_LOAD_FAILED:" + f"{type(exc).__name__}:{exc}") from exc
    if set(definitions) != BASELINE_QUESTION_SET:
        raise CanonicalQuestionCycleError("CANONICAL_DEFINITION_SET_MISMATCH")
    identities = evaluation_identities(
        questions, definitions, registry_version=REGISTRY_VERSION)
    registry_fingerprint = _registry_identity(questions, definitions)
    # The cycle identity covers evaluation identity, so re-evaluating the same
    # immutable snapshot under a changed evaluator produces a NEW governed cycle
    # instead of silently replaying the historical one.
    evaluation_identity_fingerprint = fingerprint({
        qid: identities[qid]["evaluation_identity_digest"]
        for qid in sorted(identities)
    })
    cycle_identity = {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "registry_fingerprint": registry_fingerprint,
        "evaluation_identity_fingerprint": evaluation_identity_fingerprint,
    }
    if execution_revision:
        cycle_identity["execution_revision"] = str(execution_revision)
    cycle_id = "QCYCLE-" + fingerprint(cycle_identity)[:32].upper()
    resolved_store = store or QuestionCycleStore(state_directory)
    existing = resolved_store.load_cycle(cycle_id)
    if existing is not None:
        # Idempotent repair of a missing/stale convenience projection.
        projection = resolved_store.load_projection(cycle_id)
        resolved_store.save_projection(projection)
        return existing

    previous_projection = resolved_store.load_current()
    predecessor_cycle_id = (
        None if previous_projection is None else str(previous_projection.get("cycle_id")))
    predecessor_snapshot_id = predecessor_hint or (
        None if previous_projection is None
        else str(previous_projection.get("cycle_snapshot_id") or "") or None)
    context = _build_context(
        snapshot, reader,
        predecessor_snapshot_id=predecessor_snapshot_id,
        changed_datasets=changed_datasets,
        changed_datasets_known=changed_known,
    )
    rss, peak = process_memory_bytes()
    emit(
        phase="CONTEXT_READY",
        context_elapsed_seconds=round(time.perf_counter() - stage_started, 6),
        context_records_by_dataset={name: len(rows) for name, rows in context.datasets.items()},
        process_rss_bytes=rss,
        process_peak_rss_bytes=peak,
    )
    plan = plan_affected_questions(
        questions, context, previous_projection, identities,
        evaluation_only_question_ids=evaluation_only_question_ids,
    )
    if set(plan) != BASELINE_QUESTION_SET:
        raise CanonicalQuestionCycleError("AFFECTED_PLAN_ACCOUNTING_MISMATCH")

    if runners is None:
        runner_map, discovery_failures = _discover_runners(questions)
    else:
        runner_map = dict(runners)
        discovery_failures = {}
    executor = runner_executor or _execute_runner
    previous_results = _previous_results(previous_projection)
    results: dict[str, CanonicalQuestionResult] = {}
    entries: dict[str, dict[str, Any]] = {}
    deltas: dict[str, dict[str, Any]] = {}
    evaluated_ids: list[str] = []
    retained_ids: list[str] = []
    failed_ids: list[str] = []
    deep_job_ids: list[str] = []
    deep_pending_ids: list[str] = []
    question_timings: list[dict[str, Any]] = []
    evidence_snapshot = EvidenceSnapshot(datasets=context.datasets)
    resolved_execution_policy = execution_policy or ResearchExecutionPolicy()
    force_inline = frozenset(str(item) for item in force_inline_question_ids)

    for question_index, question in enumerate(questions, start=1):
        qid = question.id
        question_started = time.perf_counter()
        resolution_seconds = 0.0
        population_seconds = 0.0
        runner_seconds = 0.0
        datasets_opened: dict[str, int] = {}
        population_count = 0
        rss, peak = process_memory_bytes()
        emit(
            phase="QUESTION_RUNNING", current_question_id=qid,
            questions_entered=question_index,
            questions_completed=question_index - 1,
            required_datasets=[source.value for source in question.data_sources],
            process_rss_bytes=rss, process_peak_rss_bytes=peak,
        )
        definition = definitions[qid]
        previous = previous_results.get(qid)
        classification = plan[qid]
        identity = identities.get(qid)
        retained = False
        runner_failed = False
        previous_entry = (
            ((previous_projection or {}).get("questions") or {}).get(qid) or {})
        previous_timing = previous_entry.get("execution_timing") or {}
        measured_runtime = previous_timing.get("runner_seconds")
        dependency_records = sum(
            len(context.datasets.get(source.value, ()))
            for source in question.data_sources
        )
        execution = resolved_execution_policy.classify(
            question,
            population_records=dependency_records,
            observed_runtime_seconds=(
                float(measured_runtime)
                if isinstance(measured_runtime, (int, float)) else None),
        )
        work_state = "FRESH"
        deep_job = None
        waiting_on_questions = tuple(
            dependency for dependency in question.depends_on
            if str((entries.get(dependency) or {}).get("work_state") or "")
            in {"DEEP_PENDING", "DEEP_RUNNING", "DEEP_STALE", "WAITING"}
        )

        if (
            classification in {AFFECTED, REQUIRES_RECHECK, REQUIRES_REEVALUATION}
            and waiting_on_questions
            and previous is not None
        ):
            result = previous
            retained = True
            retained_ids.append(qid)
            work_state = "WAITING"
            delta = {
                "first_result": False,
                "status_changed": False,
                "substantive_answer_changed": False,
                "sample_n_changed": False,
                "sample_deficit_changed": False,
                "key_metrics_changed": False,
                "evidence_availability_changed": False,
                "implementation_state_changed": False,
                "unchanged": True,
                "waiting_on_deferred_prerequisite": True,
            }
        elif (
            classification in {AFFECTED, REQUIRES_RECHECK, REQUIRES_REEVALUATION}
            and execution["execution_class"] == DEEP
            and previous is not None
            and deep_work_store is not None
            and qid not in force_inline
        ):
            dependency_identity, prerequisite_identities = (
                _deep_dependency_identity(
                    question, context, {**previous_results, **results}))
            proposed = deep_work_job(
                question_id=qid,
                evaluator=str(execution["evaluator"]),
                evaluator_identity_digest=str(
                    (identity or {}).get("evaluation_identity_digest") or ""),
                snapshot_id=context.snapshot_id,
                epoch_id=context.investigation_epoch,
                dependency_identity=dependency_identity,
                prerequisite_identities=prerequisite_identities,
                policy=execution,
                frontier_start=context.frontier_start,
                frontier_end=context.frontier_end,
                queued_at=snapshot.created_at,
            )
            deep_job = deep_work_store.enqueue(
                proposed,
                latest_scope_supersedes=bool(
                    execution.get("latest_scope_supersedes")),
                max_pending_jobs=resolved_execution_policy.max_pending_deep_jobs,
            )
            deep_job_ids.append(deep_job.job_id)
            deep_pending_ids.append(qid)
            result = previous
            retained = True
            retained_ids.append(qid)
            work_state = {
                DEEP_PENDING_STATE: "DEEP_PENDING",
                DEEP_RUNNING_STATE: "DEEP_RUNNING",
                DEEP_FAILED: "DEEP_STALE",
                DEEP_SUPERSEDED: "DEEP_STALE",
                DEEP_COMPLETED: "DEEP_STALE",
            }[deep_job.state]
            delta = {
                "first_result": False,
                "status_changed": False,
                "substantive_answer_changed": False,
                "sample_n_changed": False,
                "sample_deficit_changed": False,
                "key_metrics_changed": False,
                "evidence_availability_changed": False,
                "implementation_state_changed": False,
                "unchanged": True,
                "deferred_deep_work": True,
            }
        elif classification == UNAFFECTED:
            if previous is None:
                raise CanonicalQuestionCycleError("UNAFFECTED_QUESTION_WITHOUT_PREVIOUS_RESULT:" + qid)
            result = previous
            retained = True
            retained_ids.append(qid)
            work_state = "RETAINED_UNCHANGED"
            delta = {
                "first_result": False,
                "status_changed": False,
                "substantive_answer_changed": False,
                "sample_n_changed": False,
                "sample_deficit_changed": False,
                "key_metrics_changed": False,
                "evidence_availability_changed": False,
                "implementation_state_changed": False,
                "unchanged": True,
            }
        elif classification == ALIAS_OR_SUPERSEDED:
            owner_id = question.scientific_owner_id
            owner = results.get(owner_id) or previous_results.get(owner_id)
            if owner is None:
                raise CanonicalQuestionCycleError("ALIAS_OWNER_RESULT_MISSING:" + qid + ":" + owner_id)
            result = _base_result(
                question, definition, context, previous, identity,
                status=ALIAS_OR_SUPERSEDED,
                substantive_answer={
                    "scientific_owner_id": owner_id,
                    "owner_result_id": owner.result_id,
                    "owner_status": owner.status,
                },
                sample_n=owner.sample_n,
                minimum_required_n=owner.minimum_required_n,
                sample_deficit=owner.sample_deficit,
                key_metrics=owner.key_metrics,
                confidence=owner.confidence,
                evidence_datasets=owner.evidence_datasets,
                evidence_references=owner.evidence_references,
                limitations=("Canonical alias; no independent runner executed.",),
                missing_evidence=owner.missing_evidence,
                implementation_status="ALIAS_OF_" + owner_id,
                runner=None,
                runner_version=None,
            )
            delta = question_result_delta(previous, result)
        elif classification == UNRUNNABLE:
            reason = "NO_REGISTERED_RUNNER:" + qid
            result = _base_result(
                question, definition, context, previous, identity,
                status=UNIMPLEMENTED,
                evidence_datasets=tuple(source.value for source in question.data_sources),
                evidence_references=context.evidence_references(question),
                limitations=(reason,),
                missing_evidence=tuple(source.value for source in question.data_sources),
                implementation_status="UNIMPLEMENTED",
                failure_reason=reason,
            )
            delta = question_result_delta(previous, result)
        else:
            evaluated_ids.append(qid)
            try:
                context.runner_artifacts.clear()
                resolution_started = time.perf_counter()
                resolution = resolve_question_evidence(question, evidence_snapshot)
                context.runner_artifacts.update(resolution.runner_artifacts)
                resolution_seconds = time.perf_counter() - resolution_started
                datasets_opened = {
                    str(item["source"]): int(item["total_records"])
                    for item in resolution.sources
                }
                population_started = time.perf_counter()
                population = list(resolution.usable_records)
                if qid == "L6":
                    context.runner_artifacts["current_population_authority"] = (
                        build_current_population_authority(
                            qid, population, snapshot_id=context.snapshot_id,
                            evaluation_identity_digest=str(
                                (identity or {}).get("evaluation_identity_digest") or ""),
                        )
                    )
                population_seconds = time.perf_counter() - population_started
                population_count = len(population)
                blocking = bool(resolution.error) or any(
                    requirement.blocking and requirement.satisfied is not True
                    for requirement in resolution.requirements)
                if blocking or not population:
                    result = _result_from_evidence_gap(
                        question, definition, resolution, context, previous, identity)
                elif qid in discovery_failures or qid not in runner_map:
                    reason = discovery_failures.get(qid, "REGISTERED_RUNNER_NOT_IMPORTABLE")
                    result = _base_result(
                        question, definition, context, previous, identity,
                        status=IMPLEMENTATION_BLOCKED,
                        sample_n=resolution.usable_count,
                        key_metrics=dict(sorted(resolution.metrics.items())),
                        evidence_datasets=tuple(source.value for source in question.data_sources),
                        evidence_references=context.evidence_references(question),
                        missing_evidence=_missing_evidence(resolution),
                        implementation_status="IMPLEMENTATION_BLOCKED",
                        failure_reason=reason,
                    )
                    runner_failed = True
                else:
                    runner_started = time.perf_counter()
                    report = executor(runner_map[qid], question, population, context)
                    runner_seconds = time.perf_counter() - runner_started
                    result = _normalise_report(
                        question, definition, report, resolution, context, previous,
                        identity)
                    runner_failed = result.status in {IMPLEMENTATION_BLOCKED, INVALID}
            except Exception as exc:
                reason = f"{type(exc).__name__}:{exc}"
                if isinstance(exc, MemoryError):
                    # Resource exhaustion is neither a scientific verdict nor an
                    # INVALID question: the next governed cycle may re-attempt on a
                    # new snapshot. Keep the exact exception text for diagnosis.
                    status = IMPLEMENTATION_BLOCKED
                else:
                    status = IMPLEMENTATION_BLOCKED if isinstance(
                        exc, (SnapshotEscapeError, TypeError, ImportError, ModuleNotFoundError)) else INVALID
                result = _base_result(
                    question, definition, context, previous, identity,
                    status=status,
                    evidence_datasets=tuple(source.value for source in question.data_sources),
                    evidence_references=context.evidence_references(question),
                    limitations=(reason,),
                    implementation_status=(
                        "IMPLEMENTATION_BLOCKED" if status == IMPLEMENTATION_BLOCKED else "INVALID"),
                    failure_reason=reason,
                )
                runner_failed = True
            delta = question_result_delta(previous, result)

        if not retained:
            changed = not bool(delta.get("unchanged"))
            result = replace(result, changed_since_previous=changed, result_id="")
            if result.status in {
                    BLOCKED, IMPLEMENTATION_BLOCKED, INVALID, UNIMPLEMENTED}:
                work_state = "BLOCKED"
            elif result.status in {WAITING_FOR_DATA, CANNOT_KNOW_YET}:
                work_state = "WAITING"
            elif result.status == INSUFFICIENT_DATA:
                work_state = "INSUFFICIENT_DATA"
        results[qid] = result
        deltas[qid] = delta
        if runner_failed:
            failed_ids.append(qid)
        entries[qid] = {
            "question_id": qid,
            "planning_classification": classification,
            "cycle_snapshot_id": context.snapshot_id,
            "last_evaluated_snapshot_id": result.snapshot_id,
            "retained_previous": retained,
            "changed_this_cycle": False if retained else result.changed_since_previous,
            "runner_failed": runner_failed,
            "execution_class": execution["execution_class"],
            "execution_policy": execution,
            "work_state": work_state,
            "execution_freshness": (
                "DEEP_STALE" if deep_job is not None else work_state),
            "deep_work_job_id": (
                None if deep_job is None else deep_job.job_id),
            "triggering_evidence_epoch": (
                None if deep_job is None else deep_job.triggering_epoch_id),
            "retained_result_epoch": (
                result.investigation_epoch if retained else None),
            "waiting_on_question_ids": list(waiting_on_questions),
            "definition": {
                "question_version": f"{REGISTRY_VERSION}:{definition.definition_version}",
                "title": question.title,
                "question_text": question.description,
                "purpose": definition.research_intent,
                "evidence_requirements": [source.value for source in question.data_sources],
                "required_fields": list(question.required_fields),
                "depends_on": list(question.depends_on),
                "registered_runner": (
                    f"{question.runner_module}.{question.runner_function}"
                    if question.runner_module and question.runner_function else None),
                "scientific_owner_id": question.scientific_owner_id or None,
                "implementation_status": result.implementation_status,
            },
            "evaluation_identity": identity,
            "stale_evaluation": (
                None if classification != REQUIRES_REEVALUATION
                else explain_staleness(
                    ((previous_projection or {}).get("questions") or {})
                    .get(qid, {}).get("result", {}).get("evaluation_identity"),
                    identity,
                )
            ),
            "reentry_trigger": (
                None if result.status not in _SCIENTIFIC_REENTRY_STATUSES else {
                    "state": (
                        "DORMANT_UNCHANGED"
                        if classification == UNAFFECTED
                        else "ELIGIBLE_GOVERNED_CHANGE"
                    ),
                    "evidence_datasets": sorted(_question_dependencies(question)[0]),
                    "ambiguous_evidence_dependency": _question_dependencies(question)[1],
                    "prerequisite_question_ids": list(question.depends_on),
                    "missing_evidence": list(result.missing_evidence),
                    "evaluation_identity_digest": result.evaluation_identity_digest,
                }
            ),
            "result": result.to_dict(),
        }
        elapsed = time.perf_counter() - question_started
        timing = {
            "question_id": qid,
            "elapsed_seconds": round(elapsed, 6),
            "resolution_seconds": round(resolution_seconds, 6),
            "population_seconds": round(population_seconds, 6),
            "runner_seconds": round(runner_seconds, 6),
            "datasets_opened": datasets_opened,
            "records_materialized": sum(datasets_opened.values()),
            "population_records": population_count,
            "status": result.status,
        }
        question_timings.append(timing)
        entries[qid]["execution_timing"] = timing
        slowest = sorted(
            question_timings, key=lambda item: item["elapsed_seconds"], reverse=True)[:5]
        rss, peak = process_memory_bytes()
        emit(
            phase="QUESTION_COMPLETED", current_question_id=None,
            questions_entered=question_index, questions_completed=question_index,
            last_question=timing, slowest_questions=slowest,
            cumulative_question_elapsed_seconds=round(sum(
                item["elapsed_seconds"] for item in question_timings), 6),
            process_rss_bytes=rss, process_peak_rss_bytes=peak,
        )
        # Do not retain question-local resolver/report graphs across intervening
        # retained questions.  Shared immutable evidence remains owned by the
        # cycle-scoped EvidenceSnapshot cache and is therefore still reused.
        context.runner_artifacts.clear()
        if classification not in {UNAFFECTED, ALIAS_OR_SUPERSEDED, UNRUNNABLE}:
            resolution = population = report = None
            gc.collect()

    if len(results) != EXPECTED_QUESTION_COUNT or set(results) != BASELINE_QUESTION_SET:
        raise CanonicalQuestionCycleError("ALL_70_ACCOUNTING_INVARIANT_FAILED")
    if len(entries) != len(set(entries)):
        raise CanonicalQuestionCycleError("DUPLICATE_CURRENT_PROJECTION_QUESTION")

    changed_ids = tuple(qid for qid in BASELINE_QUESTION_IDS
                        if entries[qid]["changed_this_cycle"])
    unchanged_ids = tuple(qid for qid in BASELINE_QUESTION_IDS
                          if not entries[qid]["changed_this_cycle"])
    counts = _status_counts(results)
    if deep_work_store is not None:
        deep_work_store.record_epoch(
            epoch_id=context.investigation_epoch,
            snapshot_id=context.snapshot_id,
            closed_at=snapshot.created_at,
            deep_job_ids=deep_job_ids,
        )
    projection = {
        "cycle_schema": QUESTION_CYCLE_SCHEMA,
        "cycle_id": cycle_id,
        "cycle_snapshot_id": context.snapshot_id,
        "snapshot_fingerprint": context.fingerprint,
        "investigation_epoch": context.investigation_epoch,
        "predecessor_cycle_id": predecessor_cycle_id,
        "predecessor_snapshot_id": predecessor_snapshot_id,
        "registry_version": REGISTRY_VERSION,
        "registry_fingerprint": registry_fingerprint,
        "evaluation_identity_fingerprint": evaluation_identity_fingerprint,
        "stale_evaluation_question_ids": tuple(sorted(
            qid for qid, value in plan.items() if value == REQUIRES_REEVALUATION)),
        "deep_pending_question_ids": tuple(deep_pending_ids),
        "execution_policy_id": resolved_execution_policy.policy_id,
        "total_questions": EXPECTED_QUESTION_COUNT,
        "questions": entries,
    }
    timestamp = snapshot.created_at
    cycle_path = resolved_store.cycle_path(cycle_id)
    cycle = CanonicalQuestionCycleResult(
        cycle_id=cycle_id,
        snapshot_id=context.snapshot_id,
        fingerprint=context.fingerprint,
        investigation_epoch=context.investigation_epoch,
        predecessor_cycle_id=predecessor_cycle_id,
        predecessor_snapshot_id=predecessor_snapshot_id,
        frontier_start=context.frontier_start,
        frontier_end=context.frontier_end,
        total_questions=EXPECTED_QUESTION_COUNT,
        evaluated_count=len(evaluated_ids),
        retained_count=len(retained_ids),
        complete_count=counts["complete"],
        negative_result_count=counts["negative"],
        insufficient_count=counts["insufficient"],
        waiting_count=counts["waiting"],
        blocked_count=counts["blocked"],
        unimplemented_count=counts["unimplemented"],
        alias_count=counts["alias"],
        changed_question_ids=changed_ids,
        unchanged_question_ids=unchanged_ids,
        failed_question_ids=tuple(failed_ids),
        cycle_status="COMPLETED_WITH_QUESTION_FAILURES" if failed_ids else "COMPLETED",
        started_at=timestamp,
        completed_at=timestamp,
        current_projection_path=str(resolved_store.current_path),
        cycle_history_path=str(cycle_path),
        question_history_root=str(resolved_store.question_history_directory),
        planning=plan,
        question_deltas=deltas,
        evaluation_identity_fingerprint=evaluation_identity_fingerprint,
        stale_evaluation_question_ids=tuple(sorted(
            qid for qid, value in plan.items() if value == REQUIRES_REEVALUATION)),
        deep_pending_question_ids=tuple(deep_pending_ids),
        execution_policy_id=resolved_execution_policy.policy_id,
        result_ids={qid: results[qid].result_id for qid in BASELINE_QUESTION_IDS},
    )
    try:
        for qid in BASELINE_QUESTION_IDS:
            if not entries[qid]["retained_previous"]:
                resolved_store.save_question_result(results[qid])
        # The successful cycle authority is finalized last. If either the
        # mutable projection or final cycle write fails, the previous pointer is
        # restored and this invocation cannot appear successful.
        immutable_json(resolved_store.projection_path(cycle_id), projection)
        resolved_store.save_projection(projection)
        resolved_store.save_cycle(cycle)
    except Exception as exc:
        try:
            if previous_projection is None:
                resolved_store.current_path.unlink(missing_ok=True)
            else:
                atomic_json(resolved_store.current_path, previous_projection)
        except Exception as restore_exc:
            raise CanonicalQuestionCycleError(
                "QUESTION_CYCLE_PERSISTENCE_AND_POINTER_RECOVERY_FAILED:"
                + f"{type(exc).__name__}:{exc}:"
                + f"{type(restore_exc).__name__}:{restore_exc}") from restore_exc
        raise CanonicalQuestionCycleError(
            "QUESTION_CYCLE_PERSISTENCE_FAILED:" + f"{type(exc).__name__}:{exc}") from exc
    return cycle


__all__ = [
    "AFFECTED",
    "ALIAS_OR_SUPERSEDED",
    "CanonicalQuestionCycleError",
    "REQUIRES_RECHECK",
    "SnapshotEscapeError",
    "SnapshotQuestionExecutionContext",
    "UNAFFECTED",
    "UNRUNNABLE",
    "plan_affected_questions",
    "question_result_delta",
    "run_canonical_question_cycle",
]
