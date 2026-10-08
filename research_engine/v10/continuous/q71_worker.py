"""Governed execution worker for generated (Q71+) research questions.

Audit 1 found that generated questions had no production execution path: they
were generated, queued and displayed, but nothing ever ran them.  This module is
that path.

The worker is deliberately narrow.  It resolves an evaluator through the
governed registry, verifies governed evidence, executes the evaluator inside the
same snapshot-bound environment the canonical cycle uses, transports the
evaluator's own governed scientific result (Repair Block 1 contract) without
inventing anything, persists an immutable result and records the outcome.

Authority rules:

* The worker never decides science.  Execution status comes from the evaluator's
  own report; the only statuses the worker may originate are operational
  (MISSING_EVALUATOR, WAITING_FOR_DATA, IMPLEMENTATION_BLOCKED).
* The worker never writes findings, hypotheses or candidates.  Only
  ``scientific_state_bridge`` may do that, from the persisted immutable result.
* Malformed evaluator output fails closed.
* One question, one evidence epoch and one evaluator identity is one work item.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import hashlib

from research_engine.control_plane.governed_counterfactual_evidence import (
    MISSING_COUNTERFACTUAL_EVIDENCE,
    CounterfactualEvidenceError,
    verify_governed_counterfactual_binding,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.governed_scientific_result import (
    ScientificResultError, governed_scientific_metrics,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.v10.continuous.canonical_question_cycle import (
    _build_context, _dataset_material, _snapshot_only_runner_environment,
)
from research_engine.v10.continuous.generated_question_lifecycle import (
    BLOCKED, IMPLEMENTATION_BLOCKED, INVALID, MISSING_EVALUATOR,
    RUNNING, TERMINAL_STATES, WAITING_FOR_DATA,
    execution_status_from_report_status, generation_to_lifecycle,
    scientific_status,
)
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionBatch, GeneratedQuestionExecutionStore,
    GeneratedQuestionResult, GeneratedQuestionResultStore, work_item_identity,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    GeneratedQuestionEvaluatorRegistry, GeneratedQuestionEvaluatorRegistration,
    GeneratedQuestionEvaluatorResolution, UNKNOWN,
    evaluator_request_from_specification,
)
from research_engine.v10.continuous.q71_orchestration import (
    DEFAULT_Q71_STATE_PATH, load_q71_state,
)
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS, MANIFEST_DIRECTORY, InvestigationSnapshot,
    SnapshotBoundDatasetReader, load_investigation_snapshot_id,
)


GENERATED_QUESTION_REGISTRY_VERSION = "generated_question_registry_v1"
GENERATED_EVALUATION_IDENTITY_SCHEMA = "generated_question_evaluation_identity_v1"
DEFAULT_MAX_GENERATED_QUESTIONS_PER_RUN = 1
DEFAULT_MAX_ATTEMPTS = 3

#: Operational reason codes the worker may originate itself.
EVIDENCE_UNAVAILABLE_REASON = "GOVERNED_EVIDENCE_UNAVAILABLE"
EVALUATOR_MALFORMED_REASON = "EVALUATOR_OUTPUT_MALFORMED"
EVALUATOR_FAILED_REASON = "EVALUATOR_EXECUTION_FAILED"
EVALUATOR_SIGNATURE_REASON = "EVALUATOR_SIGNATURE_UNSATISFIED"


class GeneratedQuestionWorkerError(RuntimeError):
    """The worker cannot continue without risking unsupported science."""


@dataclass(frozen=True)
class GeneratedExecutionPolicy:
    """Bounded autonomy for generated-question execution."""

    max_questions_per_run: int = DEFAULT_MAX_GENERATED_QUESTIONS_PER_RUN
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    execute: bool = True

    def __post_init__(self) -> None:
        if (isinstance(self.max_questions_per_run, bool)
                or not isinstance(self.max_questions_per_run, int)
                or self.max_questions_per_run < 1):
            raise GeneratedQuestionWorkerError("GENERATED_MAX_QUESTIONS_INVALID")
        if (isinstance(self.max_attempts, bool)
                or not isinstance(self.max_attempts, int)
                or self.max_attempts < 1):
            raise GeneratedQuestionWorkerError("GENERATED_MAX_ATTEMPTS_INVALID")


@dataclass
class _SnapshotContext:
    snapshot: InvestigationSnapshot | None = None
    reader: SnapshotBoundDatasetReader | None = None
    material: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    datasets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    failure: str | None = None


def _load_snapshot_context(
    *, snapshot_id: str, manifest_directory: Path | str,
    source: Any | None = None, required_datasets: Sequence[str] = (),
) -> _SnapshotContext:
    """Load the frozen snapshot and only the datasets the evaluator declares."""
    try:
        snapshot = load_investigation_snapshot_id(
            snapshot_id, manifest_directory=Path(manifest_directory))
    except Exception as exc:
        return _SnapshotContext(failure=f"{type(exc).__name__}:{exc}")
    try:
        reader = SnapshotBoundDatasetReader(snapshot, source=source)
    except Exception as exc:
        return _SnapshotContext(failure=f"{type(exc).__name__}:{exc}")
    wanted = [name for name in required_datasets if name in BOUND_DATASETS]
    bindings = {item.dataset: item for item in snapshot.datasets}
    datasets: dict[str, list[dict[str, Any]]] = {}
    for name in wanted:
        binding = bindings.get(name)
        if binding is None or binding.presence != "PRESENT":
            continue
        try:
            datasets[name] = reader.cycle_cached_dataset(name)
        except Exception as exc:
            return _SnapshotContext(failure=f"{type(exc).__name__}:{exc}")
    return _SnapshotContext(
        snapshot=snapshot, reader=reader,
        material=_dataset_material(snapshot), datasets=datasets)


def _evidence_references(
    context: _SnapshotContext, datasets: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    references: list[dict[str, Any]] = []
    for dataset in datasets:
        for item in context.material.get(dataset, ()):
            references.append({"dataset": dataset, **dict(item)})
    return tuple(references)


def _admit_governed_counterfactual(
    context: "_SnapshotContext", *, evidence: Any, binding: Any,
) -> tuple[Any, Any, tuple[str, str] | None]:
    """Admit frozen governed counterfactual evidence to THIS snapshot only.

    The common investigation snapshot's bound-dataset set is closed, so the
    admission is an explicit governed membership pinned to the snapshot's exact
    identity.  A binding from another frontier or epoch, an artifact that does
    not match its binding, or an artifact supplied without a governed membership
    at all, all fail closed: the worker never admits ungoverned evidence and
    never repairs a mismatch.
    """
    if evidence is None and binding is None:
        return None, None, None
    if evidence is None or binding is None:
        return None, None, (
            MISSING_COUNTERFACTUAL_EVIDENCE,
            "a governed counterfactual artifact requires its snapshot-pinned "
            "governed evidence membership, and vice versa")
    try:
        verified = verify_governed_counterfactual_binding(
            binding,
            snapshot_id=str(getattr(context.snapshot, "snapshot_id", "")),
            snapshot_fingerprint=str(
                getattr(context.snapshot, "snapshot_fingerprint", "")),
            investigation_epoch=str(
                getattr(context.snapshot, "evidence_epoch", "")),
            evidence=evidence)
    except CounterfactualEvidenceError as exc:
        return None, None, (str(exc).split(":", 1)[0], str(exc))
    return evidence, verified, None


def _load_evaluator(
    registration: GeneratedQuestionEvaluatorRegistration,
) -> Callable[..., Any]:
    module = importlib.import_module(registration.runner_module)
    runner = getattr(module, registration.runner_function, None)
    if not callable(runner):
        raise GeneratedQuestionWorkerError(
            "EVALUATOR_NOT_CALLABLE:" + registration.qualified_runner)
    return runner

def _evaluator_kwargs(
    runner: Callable[..., Any], *, generated_question_id: str,
    specification: Mapping[str, Any],
    resolution: GeneratedQuestionEvaluatorResolution,
    context: _SnapshotContext, required_datasets: Sequence[str],
    governed_counterfactual_evidence: Any = None,
    governed_counterfactual_binding: Any = None,
) -> dict[str, Any]:
    """Bind the governed evidence to the evaluator's declared signature.

    A required parameter the worker cannot satisfy fails closed: the worker never
    invents evidence to make an evaluator run.  ``governed_counterfactual_evidence``
    is frozen governed evidence produced upstream and admitted to this snapshot;
    the worker only ever passes on what the caller proved belongs to it.
    """
    cell = specification.get("observation_cell")
    cell = dict(cell) if isinstance(cell, Mapping) else {}
    requirements = specification.get("evidence_requirements")
    requirements = dict(requirements) if isinstance(requirements, Mapping) else {}
    available: dict[str, Any] = {
        "generated_question_id": generated_question_id,
        "question_id": generated_question_id,
        "question": dict(specification),
        "generated_question": dict(specification),
        "observation_cell": cell,
        "observation_cell_identity": str(
            cell.get("cell_identity") or specification.get("target_ref") or ""),
        "evidence_class": str(
            cell.get("evidence_class") or requirements.get("evidence_class")
            or UNKNOWN),
        "population_identity": str(
            cell.get("population_identity")
            or requirements.get("population_identity") or UNKNOWN),
        "horizon": str(
            cell.get("horizon") or requirements.get("horizon") or UNKNOWN),
        "question_type": str(specification.get("question_type") or UNKNOWN),
        "metric_family": str(specification.get("metric_family") or UNKNOWN),
        "intervention_class": str(
            specification.get("intervention_class") or UNKNOWN),
        "evidence_requirements": requirements,
        "evidence_references": _evidence_references(context, required_datasets),
        "datasets": context.datasets,
        "source": context.reader,
        "persist": False,
        "snapshot_id": str(getattr(context.snapshot, "snapshot_id", "")),
        "snapshot_fingerprint": str(
            getattr(context.snapshot, "snapshot_fingerprint", "")),
        "investigation_epoch": str(getattr(context.snapshot, "evidence_epoch", "")),
        "evidence_frontier": str(getattr(context.snapshot, "end_date", "")),
        "evaluator_key": str(resolution.evaluator_key or ""),
        # Frozen governed evidence admitted to THIS snapshot.  The artifact is
        # never read from disk here: the caller produced it upstream and the
        # worker proved its membership before binding it.
        "governed_counterfactual_evidence": governed_counterfactual_evidence,
        "governed_counterfactual_binding": governed_counterfactual_binding,
    }
    primary = required_datasets[0] if required_datasets else None
    for dataset in required_datasets:
        available[dataset] = context.datasets.get(dataset, [])
    available["records"] = context.datasets.get(primary or "", [])
    available["shadow_trades"] = context.datasets.get("shadow_trades")
    signature = inspect.signature(runner)
    bound: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            for key, value in available.items():
                bound.setdefault(key, value)
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if name in available:
            bound[name] = available[name]
            continue
        if parameter.default is inspect.Parameter.empty:
            raise GeneratedQuestionWorkerError(
                EVALUATOR_SIGNATURE_REASON + ":" + name)
    return bound


def _sequence(value: Any) -> tuple[Any, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _generated_sample_n(report: Mapping[str, Any]) -> int | None:
    """Read only evaluator-supplied sample sizes; never infer a population."""
    candidates: list[Any] = [
        report.get("sample_n"), report.get("sample_size"), report.get("n")]
    dataset = report.get("dataset")
    if isinstance(dataset, Mapping):
        candidates.extend(
            (dataset.get("sample_size"), dataset.get("r_multiples_used")))
    sizes = report.get("sample_sizes")
    if isinstance(sizes, Mapping):
        candidates.extend((sizes.get("total"), sizes.get("n")))
    for value in candidates:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def _generated_report_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    """Collect the evaluator's own reported metrics.  Nothing is invented."""
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
    return dict(sorted(metrics.items()))

def relevant_evidence_identity(state_row: Mapping[str, Any]) -> str:
    """The generated question's own governed evidence identity.

    Derived from the coverage evidence the generation stage last observed for
    this observation cell: the coverage snapshot identity and the evidence
    references.  A change to unrelated datasets therefore cannot make an
    unaffected generated question stale.
    """
    history = list(state_row.get("evidence_history") or ())
    last = history[-1] if history else {}
    return _digest({
        "coverage_snapshot_id": last.get("coverage_snapshot_id")
        or state_row.get("last_evaluated_snapshot"),
        "inventory_id": last.get("inventory_id"),
        "evidence_refs": sorted(str(item) for item in (last.get("evidence_refs") or ())),
    })


def _digest(value: Any) -> str:
    from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _evaluation_identity(
    *, generated_question_id: str, question_version: str,
    registration: GeneratedQuestionEvaluatorRegistration,
    registry_identity: str,
) -> dict[str, Any]:
    material = {
        "schema": GENERATED_EVALUATION_IDENTITY_SCHEMA,
        "question_id": generated_question_id,
        "question_version": question_version,
        "evaluator_key": registration.evaluator_key,
        "evaluator_version": registration.evaluator_version,
        "evaluator_identity_digest": registration.evaluator_identity_digest,
        "evaluator_registry_identity": registry_identity,
    }
    return {**material, "evaluation_identity_digest": _digest(material)}


def _build_transport_result(
    *, generated_question_id: str, question_version: str,
    context: _SnapshotContext, registration: GeneratedQuestionEvaluatorRegistration,
    execution_status: str, report: Mapping[str, Any],
    key_metrics: Mapping[str, Any], evidence_datasets: Sequence[str],
    missing_evidence: Sequence[str], evaluation_identity: Mapping[str, Any],
) -> CanonicalQuestionResult:
    """Transport the evaluator's report into the canonical result contract.

    Nothing is invented: absent values stay absent, and the scientific meaning
    arrives only through ``key_metrics`` (Repair Block 1 transport).
    """
    minimum = report.get("minimum_required_n")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
        minimum = None
    sample = _generated_sample_n(report)
    deficit = None if minimum is None or sample is None else max(0, minimum - sample)
    limitations = (
        *_sequence(report.get("limitations")),
        *_sequence(report.get("warnings")),
        *_sequence(report.get("research_gaps")),
    )
    answer = None
    for name in ("conclusion", "outcome", "answer", "recommendation"):
        value = report.get(name)
        if value not in (None, "", {}, []):
            answer = value
            break
    reason_code_value = report.get("reason_code")
    legacy_reason = report.get("reason")
    reason_code = (
        str(reason_code_value).strip() if reason_code_value not in (None, "")
        else str(legacy_reason).strip() if legacy_reason not in (None, "")
        else None)
    reason = str(legacy_reason).strip() if legacy_reason not in (None, "") else None
    failure = None
    if execution_status in {IMPLEMENTATION_BLOCKED, INVALID}:
        failure = str(
            report.get("failure_reason") or report.get("error")
            or report.get("reason") or report.get("blocked_reason")
            or "EVALUATOR_STATUS:" + execution_status)
    snapshot = context.snapshot
    return CanonicalQuestionResult(
        question_id=generated_question_id,
        question_version=question_version,
        snapshot_id=str(getattr(snapshot, "snapshot_id", "")),
        snapshot_fingerprint=str(getattr(snapshot, "snapshot_fingerprint", "")),
        investigation_epoch=str(getattr(snapshot, "evidence_epoch", "")),
        evaluated_at=str(getattr(snapshot, "created_at", "")),
        status=execution_status,
        substantive_answer=answer,
        sample_n=sample,
        minimum_required_n=minimum,
        sample_deficit=deficit,
        key_metrics=dict(key_metrics),
        confidence=report.get("confidence"),
        statistical_output=(
            report.get("statistical_output") or report.get("statistics")),
        evidence_datasets=tuple(evidence_datasets),
        evidence_references=_evidence_references(context, evidence_datasets),
        limitations=tuple(limitations),
        missing_evidence=tuple(sorted(set(str(item) for item in missing_evidence))),
        implementation_status=(
            "IMPLEMENTED" if execution_status != IMPLEMENTATION_BLOCKED
            else "IMPLEMENTATION_BLOCKED"),
        runner=registration.qualified_runner,
        runner_version=registration.evaluator_version,
        reason_code=reason_code,
        reason=reason,
        reason_details=report.get("reason_details"),
        failure_reason=failure,
        evaluation_identity=dict(evaluation_identity),
        evaluation_identity_digest=str(
            evaluation_identity.get("evaluation_identity_digest")),
    )

def _operational_outcome(
    execution_store: GeneratedQuestionExecutionStore, *, question_id: str,
    execution_status: str, reason_code: str, reason: str, stamp: str,
    evidence_epoch: str, evaluator_key: str, evaluator_identity_digest: str,
    evaluator_version: str, outcome: str,
) -> dict[str, Any]:
    """Record a worker-originated (non-scientific) outcome.

    No immutable result is written: no evaluator produced a governed scientific
    report, so fabricating one would be unsupported science.
    """
    execution_store.record_outcome(
        generated_question_id=question_id, work_item_id="",
        execution_status=execution_status,
        scientific_status="NOT_SCIENTIFICALLY_RESOLVED",
        reason_code=reason_code, reason=reason, result_id=None,
        evidence_epoch=evidence_epoch, evaluator_key=evaluator_key,
        evaluator_identity_digest=evaluator_identity_digest,
        evaluator_version=evaluator_version, completed_at=stamp)
    return {
        "generated_question_id": question_id, "outcome": outcome,
        "execution_status": execution_status, "reason_code": reason_code,
        "reason": reason, "result_id": None, "work_item_id": None,
        "scientific_status": "NOT_SCIENTIFICALLY_RESOLVED",
    }

def run_generated_question_worker(
    *,
    snapshot_id: str,
    evaluator_registry: GeneratedQuestionEvaluatorRegistry,
    result_store: GeneratedQuestionResultStore,
    execution_store: GeneratedQuestionExecutionStore,
    generated_store: GeneratedResearchStore | None = None,
    q71_state_path: Path | str = DEFAULT_Q71_STATE_PATH,
    manifest_directory: Path | str = MANIFEST_DIRECTORY,
    source: Any | None = None,
    policy: GeneratedExecutionPolicy | None = None,
    recorded_at: str | None = None,
    governed_counterfactual_evidence: Any = None,
    governed_counterfactual_binding: Any = None,
) -> dict[str, Any]:
    """Execute at most ``max_questions_per_run`` queued generated questions.

    ``governed_counterfactual_evidence`` is frozen governed counterfactual
    evidence produced upstream of this worker and admitted to ``snapshot_id`` by
    ``governed_counterfactual_binding``.  The worker never reads that evidence
    from disk and never admits it without a membership that verifies against its
    own snapshot.
    """
    resolved_policy = policy or GeneratedExecutionPolicy()
    if not isinstance(evaluator_registry, GeneratedQuestionEvaluatorRegistry):
        raise GeneratedQuestionWorkerError("GOVERNED_EVALUATOR_REGISTRY_REQUIRED")
    stamp = recorded_at or ""
    state = load_q71_state(q71_state_path)
    generated = (
        generated_store if generated_store is not None else GeneratedResearchStore())
    registry_identity = evaluator_registry.registry_identity
    outcomes: list[dict[str, Any]] = []
    batch_entries: list[dict[str, Any]] = []
    results_written: list[str] = []
    work_item_ids: list[str] = []
    remaining = resolved_policy.max_questions_per_run
    for record in generated.all():
        if remaining <= 0:
            break
        question_id = record.generated_research_id
        specification = record.specification
        if specification.get("kind") != "coverage_gap_question":
            continue
        state_row = state.get("question_states", {}).get(question_id) or {}
        if not state_row.get("status"):
            # Identity minted but never admitted to the governed agenda.
            continue
        execution_row = execution_store.state(question_id) or {}
        if (generation_to_lifecycle(state_row.get("status")) in TERMINAL_STATES
                or str(execution_row.get("execution_status") or "").upper()
                in TERMINAL_STATES):
            # A retired, superseded or invalidated question never executes unless
            # a governed authority explicitly re-authorises it.
            continue
        resolution = evaluator_registry.resolve(
            evaluator_request_from_specification(question_id, specification))
        evidence_identity = relevant_evidence_identity(state_row)
        evaluator_digest = str(resolution.evaluator_identity_digest or "")
        if not resolution.resolved:
            if (str(execution_row.get("execution_status") or "") == MISSING_EVALUATOR
                    and str(execution_row.get("reason_code") or "")
                    == resolution.reason_code):
                continue
            outcomes.append(_operational_outcome(
                execution_store, question_id=question_id,
                execution_status=MISSING_EVALUATOR,
                reason_code=resolution.reason_code, reason=resolution.reason,
                stamp=stamp, evidence_epoch=evidence_identity, evaluator_key="",
                evaluator_identity_digest="", evaluator_version="",
                outcome=MISSING_EVALUATOR))
            continue
        registration = evaluator_registry.registration_for_key(
            str(resolution.evaluator_key))
        if registration is None:  # pragma: no cover - resolver guarantees this
            raise GeneratedQuestionWorkerError(
                "RESOLVED_EVALUATOR_NOT_REGISTERED:"
                + str(resolution.evaluator_key))
        if (str(execution_row.get("result_evidence_identity") or "")
                == evidence_identity
                and str(execution_row.get("result_evaluator_digest") or "")
                == evaluator_digest
                and execution_row.get("latest_result_id")):
            # Unchanged relevant evidence under the same evaluator: the retained
            # immutable result is still current, so nothing is re-executed.
            continue
        if not state_row.get("evidence_available"):
            outcomes.append(_operational_outcome(
                execution_store, question_id=question_id,
                execution_status=WAITING_FOR_DATA,
                reason_code=EVIDENCE_UNAVAILABLE_REASON,
                reason="governed coverage evidence is not yet available",
                stamp=stamp, evidence_epoch=evidence_identity,
                evaluator_key=registration.evaluator_key,
                evaluator_identity_digest=evaluator_digest,
                evaluator_version=registration.evaluator_version,
                outcome=WAITING_FOR_DATA))
            continue
        outcome, entry, result_id = _execute_one(
            snapshot_id=snapshot_id, question_id=question_id,
            specification=specification, state_row=state_row,
            registration=registration, resolution=resolution,
            evidence_identity=evidence_identity,
            registry_identity=registry_identity,
            manifest_directory=manifest_directory, source=source,
            policy=resolved_policy, stamp=stamp,
            execution_store=execution_store, result_store=result_store,
            governed_counterfactual_evidence=governed_counterfactual_evidence,
            governed_counterfactual_binding=governed_counterfactual_binding)
        outcomes.append(outcome)
        if entry is not None:
            batch_entries.append(entry)
        if result_id:
            results_written.append(result_id)
            work_item_ids.append(str(outcome.get("work_item_id") or ""))
        remaining -= 1
    batch = None
    if batch_entries:
        batch = GeneratedQuestionExecutionBatch.build(
            snapshot_id=snapshot_id,
            snapshot_fingerprint=str(
                batch_entries[0].get("snapshot_fingerprint") or ""),
            investigation_epoch=str(
                batch_entries[0].get("investigation_epoch") or ""),
            evaluator_registry_identity=registry_identity,
            entries=sorted(
                batch_entries, key=lambda item: item["generated_question_id"]),
            recorded_at=stamp)
        result_store.record_batch(batch)
    return {
        "status": "COMPLETED",
        "snapshot_id": snapshot_id,
        "evaluator_registry_identity": registry_identity,
        "executed_question_ids": sorted(
            str(item.get("generated_question_id")) for item in outcomes),
        "result_ids": sorted(results_written),
        "work_item_ids": sorted(item for item in work_item_ids if item),
        "outcomes": outcomes,
        "batch_id": None if batch is None else batch.batch_id,
        "batch_result_ids": [] if batch is None else list(batch.result_ids),
        "execution_state_metrics": execution_store.metrics(),
    }

def _execute_one(
    *, snapshot_id: str, question_id: str, specification: Mapping[str, Any],
    state_row: Mapping[str, Any],
    registration: GeneratedQuestionEvaluatorRegistration,
    resolution: GeneratedQuestionEvaluatorResolution, evidence_identity: str,
    registry_identity: str, manifest_directory: Path | str, source: Any | None,
    policy: GeneratedExecutionPolicy, stamp: str,
    execution_store: GeneratedQuestionExecutionStore,
    result_store: GeneratedQuestionResultStore,
    governed_counterfactual_evidence: Any = None,
    governed_counterfactual_binding: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Verify evidence, execute, transport and persist one generated question."""
    context = _load_snapshot_context(
        snapshot_id=snapshot_id, manifest_directory=manifest_directory,
        source=source, required_datasets=registration.required_datasets)
    missing_datasets = sorted(
        name for name in registration.required_datasets
        if not context.datasets.get(name))
    if context.failure or missing_datasets:
        reason_code = (
            EVIDENCE_UNAVAILABLE_REASON if context.failure
            else "EVIDENCE_DATASET_ABSENT:" + ",".join(missing_datasets))
        reason = context.failure or (
            "declared evidence datasets are not present in the frozen snapshot")
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=WAITING_FOR_DATA, reason_code=reason_code,
            reason=reason, stamp=stamp, evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=WAITING_FOR_DATA), None, None
    admitted_evidence, admitted_binding, refusal = _admit_governed_counterfactual(
        context, evidence=governed_counterfactual_evidence,
        binding=governed_counterfactual_binding)
    if refusal is not None:
        # A governed-evidence integrity failure must never look like a data gap.
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=BLOCKED, reason_code=refusal[0], reason=refusal[1],
            stamp=stamp, evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=BLOCKED), None, None
    work_item_id = work_item_identity(
        generated_question_id=question_id, evidence_epoch=evidence_identity,
        evaluator_identity_digest=registration.evaluator_identity_digest)
    try:
        execution_store.record_claim(
            generated_question_id=question_id, work_item_id=work_item_id,
            evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            claimed_at=stamp, max_attempts=policy.max_attempts)
    except GeneratedQuestionResultError as exc:
        text = str(exc)
        if text.startswith("GENERATED_WORK_ALREADY_RUNNING"):
            return {
                "generated_question_id": question_id,
                "outcome": "ALREADY_RUNNING", "execution_status": RUNNING,
                "reason_code": "GENERATED_WORK_ALREADY_RUNNING",
                "reason": text, "result_id": None,
                "work_item_id": work_item_id,
                "scientific_status": "NOT_SCIENTIFICALLY_RESOLVED",
            }, None, None
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=IMPLEMENTATION_BLOCKED,
            reason_code="GENERATED_WORK_RETRY_CAP_EXHAUSTED",
            reason=text, stamp=stamp, evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=IMPLEMENTATION_BLOCKED), None, None
    question_version = (
        f"{GENERATED_QUESTION_REGISTRY_VERSION}:"
        f"{specification.get('definition_version') or 1}")
    try:
        runner = _load_evaluator(registration)
        kwargs = _evaluator_kwargs(
            runner, generated_question_id=question_id,
            specification=specification, resolution=resolution,
            context=context, required_datasets=registration.required_datasets,
            governed_counterfactual_evidence=admitted_evidence,
            governed_counterfactual_binding=admitted_binding)
        with _snapshot_only_runner_environment(context.reader):
            report = runner(**kwargs)
        if not isinstance(report, Mapping):
            raise GeneratedQuestionWorkerError("EVALUATOR_RETURNED_NON_MAPPING")
        # Repair Block 1 transport: the evaluator declares its own scientific
        # result; this normalizer only validates and carries it.
        governed = governed_scientific_metrics(report)
        key_metrics = {**_generated_report_metrics(report), **governed}
    except ScientificResultError as exc:
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=IMPLEMENTATION_BLOCKED,
            reason_code=EVALUATOR_MALFORMED_REASON,
            reason="SCIENTIFIC_RESULT_MALFORMED:" + str(exc), stamp=stamp,
            evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=IMPLEMENTATION_BLOCKED), None, None
    except GeneratedQuestionWorkerError as exc:
        text = str(exc)
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=IMPLEMENTATION_BLOCKED,
            reason_code=(
                EVALUATOR_SIGNATURE_REASON
                if text.startswith(EVALUATOR_SIGNATURE_REASON)
                else EVALUATOR_MALFORMED_REASON),
            reason=text, stamp=stamp, evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=IMPLEMENTATION_BLOCKED), None, None
    except Exception as exc:
        return _operational_outcome(
            execution_store, question_id=question_id,
            execution_status=IMPLEMENTATION_BLOCKED,
            reason_code=EVALUATOR_FAILED_REASON,
            reason=f"{type(exc).__name__}:{exc}", stamp=stamp,
            evidence_epoch=evidence_identity,
            evaluator_key=registration.evaluator_key,
            evaluator_identity_digest=registration.evaluator_identity_digest,
            evaluator_version=registration.evaluator_version,
            outcome=IMPLEMENTATION_BLOCKED), None, None
    execution_status = execution_status_from_report_status(report.get("status"))
    scientific = scientific_status(
        execution_status, key_metrics.get("scientifically_meaningful"))
    evaluation_identity = _evaluation_identity(
        generated_question_id=question_id, question_version=question_version,
        registration=registration, registry_identity=registry_identity)
    transport = _build_transport_result(
        generated_question_id=question_id, question_version=question_version,
        context=context, registration=registration,
        execution_status=execution_status, report=report,
        key_metrics=key_metrics,
        evidence_datasets=registration.required_datasets,
        missing_evidence=report.get("missing_evidence") or (),
        evaluation_identity=evaluation_identity)
    cell = specification.get("observation_cell")
    cell = dict(cell) if isinstance(cell, Mapping) else {}
    previous_result_id = str(state_row.get("latest_result_id") or "") or None
    result = GeneratedQuestionResult.build(
        generated_question_id=question_id,
        observation_cell_identity=str(
            cell.get("cell_identity") or specification.get("target_ref") or ""),
        evidence_class=str(cell.get("evidence_class") or UNKNOWN),
        question_type=str(specification.get("question_type") or UNKNOWN),
        population_identity=str(cell.get("population_identity") or UNKNOWN),
        horizon=str(cell.get("horizon") or UNKNOWN),
        snapshot_id=transport.snapshot_id,
        snapshot_fingerprint=transport.snapshot_fingerprint,
        investigation_epoch=transport.investigation_epoch,
        evidence_frontier=str(getattr(context.snapshot, "end_date", "")),
        evaluator_key=registration.evaluator_key,
        evaluator_version=registration.evaluator_version,
        evaluator_identity_digest=registration.evaluator_identity_digest,
        capability_class=registration.capability_class,
        execution_identity=work_item_id, work_item_id=work_item_id,
        execution_status=execution_status, scientific_status=scientific,
        reason_code=str(transport.reason_code or ""),
        reason=str(transport.reason or ""),
        evidence_datasets=tuple(registration.required_datasets),
        evidence_references=transport.evidence_references,
        governed_scientific_metrics=dict(governed),
        transport_result=transport.to_dict(),
        predecessor_result_id=previous_result_id,
        recorded_at=stamp)
    result_store.save_result(result)
    state = execution_store.record_outcome(
        generated_question_id=question_id, work_item_id=work_item_id,
        execution_status=execution_status, scientific_status=scientific,
        reason_code=str(transport.reason_code or "") or "EVALUATOR_DECLARED_STATUS",
        reason=str(transport.reason or ""), result_id=result.result_id,
        evidence_epoch=evidence_identity,
        evaluator_key=registration.evaluator_key,
        evaluator_identity_digest=registration.evaluator_identity_digest,
        evaluator_version=registration.evaluator_version,
        completed_at=stamp, governed_scientific_metrics=dict(governed),
        predecessor_result_id=previous_result_id,
        result_evidence_identity=evidence_identity,
        result_snapshot_id=transport.snapshot_id)
    entry = {
        "generated_question_id": question_id,
        "result_id": result.result_id,
        "execution_status": execution_status,
        "scientific_status": scientific,
        "reason_code": state.get("reason_code"),
        "observation_cell_identity": result.observation_cell_identity,
        "snapshot_fingerprint": result.snapshot_fingerprint,
        "investigation_epoch": result.investigation_epoch,
        "evaluator_key": registration.evaluator_key,
        "evaluator_identity_digest": registration.evaluator_identity_digest,
        "evidence_identity": evidence_identity,
    }
    return {
        "generated_question_id": question_id, "outcome": execution_status,
        "execution_status": execution_status,
        "reason_code": state.get("reason_code"), "reason": state.get("reason"),
        "result_id": result.result_id, "work_item_id": work_item_id,
        "scientific_status": scientific,
    }, entry, result.result_id


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_GENERATED_QUESTIONS_PER_RUN",
    "EVALUATOR_FAILED_REASON",
    "EVALUATOR_MALFORMED_REASON",
    "EVALUATOR_SIGNATURE_REASON",
    "EVIDENCE_UNAVAILABLE_REASON",
    "GENERATED_EVALUATION_IDENTITY_SCHEMA",
    "GENERATED_QUESTION_REGISTRY_VERSION",
    "GeneratedExecutionPolicy",
    "GeneratedQuestionWorkerError",
    "relevant_evidence_identity",
    "run_generated_question_worker",
]
