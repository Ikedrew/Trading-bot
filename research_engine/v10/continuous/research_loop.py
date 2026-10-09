"""One bounded, restart-safe invocation of the four-block research loop."""
from __future__ import annotations

import argparse
from functools import wraps
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid
from typing import Any, Callable, Mapping

from research_engine.control_plane.governed_counterfactual_evidence import (
    COUNTERFACTUAL_EVIDENCE_CLASS,
    CounterfactualEvidenceError,
    CounterfactualEvidenceStore,
    DEFAULT_COUNTERFACTUAL_EVIDENCE_DIRECTORY,
    build_governed_counterfactual_evidence,
    bind_counterfactual_evidence,
    validate_governed_counterfactual_evidence,
)
from research_engine.control_plane.governed_m5_candle_authority import (
    DEFAULT_M5_CANDLE_AUTHORITY_DIRECTORY,
    M5_CANDLE_AUTHORITY_IDENTITY,
    CandleAuthorityError,
    M5CandleAuthorityStore,
    bind_m5_candle_authority,
    build_governed_m5_candle_authority,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.lifecycle.generated_research_store import (
    GeneratedResearchStore,
)
from research_engine.lifecycle.research_coverage_store import (
    ResearchCoverageStore,
)
from research_engine.v10.continuous.canonical_question_cycle import run_canonical_question_cycle
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleLease, ContinuousCycleProgressStore, ContinuousCycleStore,
    ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.frontier_coordinator import (
    FRONTIER_INCOMPLETE, NO_NEW_GOVERNED_EVIDENCE, SNAPSHOT_READY,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore, GeneratedQuestionResultStore,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    DEFAULT_REGISTRY_PATH as DEFAULT_EVALUATOR_REGISTRY_PATH,
    GeneratedQuestionEvaluatorRegistry,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy, run_generated_question_worker,
)
from research_engine.v10.continuous.q71_orchestration import (
    load_q71_state, run_q71_orchestration,
)
from research_engine.v10.continuous.production_coverage import (
    DEFAULT_PRODUCTION_COVERAGE_DIRECTORY,
    DEFAULT_PRODUCTION_COVERAGE_STORE_PATH,
    NO_PRODUCTION_OBSERVATION_SPACE,
    materialize_production_coverage,
    write_q71_coverage_source_mapping,
)
from research_engine.v10.investigation_snapshot import MANIFEST_DIRECTORY
from research_engine.v10.investigation_snapshot import (
    InvestigationSnapshot, load_investigation_snapshot_id,
    open_investigation_snapshot,
)
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_projection import (
    ResearchProjectionStore, build_evaluation_refresh_projection,
    build_research_work_refresh_projection, build_unified_research_projection,
)
from research_engine.v10.continuous.research_work_queue import (
    FAILED as DEEP_FAILED,
    PENDING,
    RUNNING,
    ResearchExecutionPolicy,
    ResearchWorkQueueError,
    ResearchWorkQueueStore,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    run_generated_scientific_bridge, run_scientific_state_bridge,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.validation_queue import (
    ValidationQueueStore, enqueue_forward_validation, enqueue_validation_handoff,
    process_validation_queue,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


STAGES = ("FRONTIER", "QUESTIONS", "SCIENTIFIC_STATE", "OBSERVATION_SPACE",
          "Q71_PLUS", "Q71_EXECUTION", "VALIDATION_QUEUE", "PROJECTION")


class ContinuousResearchLoopError(RuntimeError):
    pass


def _exclusive_continuous_cycle(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        root = Path(kwargs.get("state_root", Path("data/research/continuous")))
        lease_id = uuid.uuid4().hex.upper()
        with ContinuousCycleLease(root / "active_cycle.lock", lease_id=lease_id):
            return function(*args, **kwargs)
    return guarded


def _value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)


def _cycle_id(material: Mapping[str, Any]) -> str:
    return "CRCYCLE-" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()


def _attempt_cycle_id(cycle_attempt_id: str, identity: str) -> str:
    return _cycle_id({
        "cycle_attempt_id": cycle_attempt_id,
        "identity": identity,
    })


def _is_current_successful_projection(
    projection: Mapping[str, Any] | None, frontier: Any,
    stale_question_ids: Mapping[str, str] | None = None,
    canonical_projection: Mapping[str, Any] | None = None,
) -> bool:
    """True only when the retained projection is current in BOTH identities.

    Evidence identity alone is not sufficient: a published question result is
    current only when the governed evidence identity AND the governed evaluation
    identity still match.  If any question's evaluator has changed, the
    projection is no longer current even though the snapshot is unchanged.
    """
    if not projection or not projection.get("projection_version"):
        return False
    data_frontier = projection.get("data_frontier")
    questions = projection.get("canonical_questions")
    if not (
        isinstance(data_frontier, Mapping)
        and str(data_frontier.get("snapshot_id") or "")
        == str(_value(frontier, "snapshot_id") or "")
        and bool(data_frontier.get("last_successful_research_cycle"))
        and isinstance(questions, list)
        and len(questions) == 70
    ):
        return False
    if stale_question_ids:
        return False
    if canonical_projection is None:
        return True
    canonical_questions = canonical_projection.get("questions")
    if not isinstance(canonical_questions, Mapping):
        return False
    if set(canonical_questions) != set(BASELINE_QUESTION_IDS):
        return False
    if projection.get("canonical_question_cycle_id") != canonical_projection.get("cycle_id"):
        return False
    published_ids = [str(row.get("question_id") or "") for row in questions]
    if published_ids != list(BASELINE_QUESTION_IDS):
        return False
    for row in questions:
        question_id = str(row.get("question_id") or "")
        current = canonical_questions.get(question_id)
        if not isinstance(current, Mapping):
            return False
        published_result = row.get("result") or {}
        current_result = current.get("result") or {}
        if published_result.get("result_id") != current_result.get("result_id"):
            return False
    return True


def _retained_coherent_frontier_is_usable(frontier: Any) -> bool:
    """Whether no promoted evidence changed and the retained snapshot is safe.

    A partial newer candidate is usable only through the coordinator's explicit
    retained-verification contract.  Other incomplete/invalid frontier results
    remain fatal and can never reach question evaluation.
    """
    status = str(_value(frontier, "status") or "")
    if status == NO_NEW_GOVERNED_EVIDENCE:
        return True
    return (
        status == FRONTIER_INCOMPLETE
        and str(_value(frontier, "verification_status") or "")
        == "RETAINED_VERIFIED"
        and str(_value(frontier, "failure_reason") or "").startswith(
            "REQUIRED_DATASETS_NOT_YET_COHERENT_AT_NEWEST_COVERAGE:")
        and all(str(_value(frontier, name) or "") for name in (
            "snapshot_id", "fingerprint", "investigation_epoch"))
    )


def _stale_evaluation_questions(
    projection: Mapping[str, Any] | None, *,
    question_state_dir: Path,
    registry_loader: Callable[[], Any] | None = None,
) -> dict[str, str]:
    """Return questions whose published result is no longer current.

    This compares each published result's recorded governed evaluation identity
    against the currently authoritative evaluator.  It is diagnostic plus
    orchestration: it decides whether a no-new-evidence cycle may be a true
    no-op, and it never asserts anything scientific about the world.
    """
    if not projection:
        return {}
    rows = projection.get("canonical_questions")
    if not isinstance(rows, list):
        return {}
    recorded = {
        str(row.get("question_id")): ((row.get("result") or {}).get("evaluation_identity"))
        for row in rows if isinstance(row, Mapping)
    }
    try:
        if registry_loader is not None:
            questions = tuple(registry_loader())
        else:
            from research_engine.v10.continuous.canonical_question_cycle import _load_registry
            questions = _load_registry()
        from research_engine.registry.definition_validator import build_definitions_from_registry
        from research_engine.v10.continuous.canonical_question_cycle import REGISTRY_VERSION
        from research_engine.v10.continuous.evaluation_identity import (
            evaluation_identities, stale_question_ids,
        )
        identities = evaluation_identities(
            questions, build_definitions_from_registry(questions),
            registry_version=REGISTRY_VERSION)
    except Exception as exc:
        # Fail closed: if evaluation identity cannot be established, the retained
        # projection cannot be proven current, so the cycle must re-evaluate
        # rather than silently skip.
        print(f"evaluation identity check failed: {type(exc).__name__}:{exc}",
              file=sys.stderr)
        return {"__EVALUATION_IDENTITY_UNAVAILABLE__": "STALE_EVALUATION"}
    return stale_question_ids(recorded, identities)


def _governed_candle_authority_state(
    *, present: bool, authority: Any = None, binding: Any = None,
    reason: str | None = None, detail: str = "",
) -> dict[str, Any]:
    """The Lab-truth block for the governed M5 candle authority."""
    return {
        "authority_identity": M5_CANDLE_AUTHORITY_IDENTITY,
        "present": bool(present),
        "authority_id": (None if authority is None
                         else str(authority.authority_id)),
        "content_digest": (None if authority is None
                           else str(authority.content_digest)),
        "snapshot_id": (None if authority is None
                        else str(authority.snapshot_id)),
        "snapshot_fingerprint": (None if authority is None
                                 else str(authority.snapshot_fingerprint)),
        "investigation_epoch": (None if authority is None
                                else str(authority.investigation_epoch)),
        "frontier_start": (None if authority is None
                           else str(authority.frontier_start)),
        "frontier_end": (None if authority is None
                         else str(authority.frontier_end)),
        "symbols": ([] if authority is None else list(authority.symbols)),
        "bar_count": (0 if authority is None else int(authority.bar_count)),
        "first_bar_utc": (None if authority is None or not authority.bar_count
                          else str(authority.bars[0]["open_time_utc"])),
        "last_bar_utc": (None if authority is None or not authority.bar_count
                         else str(authority.bars[-1]["open_time_utc"])),
        "completeness": ({} if authority is None
                         else dict(authority.completeness)),
        "bound": binding is not None,
        "fail_closed_reason": None if present else (
            reason or "MISSING_M5_CANDLE_AUTHORITY"),
        "detail": str(detail or ""),
    }


def _materialize_governed_candle_authority(
    *, snapshot_id: str, manifest_directory: Path | str, source: Any,
    store: M5CandleAuthorityStore, producer: Callable[..., Any] | None,
    created_at: str,
) -> tuple[Any, Any, dict[str, Any]]:
    """Admit the governed M5 candle authority for exactly this frontier.

    This runs *upstream* of the Q71 worker, which never touches storage.  An
    unchanged frontier reuses its already-registered deterministic authority, so
    re-entry does not re-read or re-mint anything.  Any failure is a governed
    fail-closed state, never a fabricated authority.
    """
    if producer is None:
        return None, None, _governed_candle_authority_state(
            present=False, reason="CANDLE_AUTHORITY_PRODUCER_DISABLED",
            detail="no governed candle authority producer is configured")
    existing = store.for_snapshot(str(snapshot_id))
    if existing is not None:
        authority, binding = existing
        return authority, binding, _governed_candle_authority_state(
            present=True, authority=authority, binding=binding,
            detail="reused the registered deterministic authority")
    try:
        snapshot: InvestigationSnapshot = load_investigation_snapshot_id(
            str(snapshot_id), manifest_directory=Path(manifest_directory))
        reader = open_investigation_snapshot(
            str(snapshot_id), source=source,
            manifest_directory=Path(manifest_directory))
        shadow_rows = reader.cycle_cached_dataset("shadow_runtime")
        authority = producer(
            shadow_runtime_rows=shadow_rows,
            snapshot_id=str(snapshot.snapshot_id),
            snapshot_fingerprint=str(snapshot.snapshot_fingerprint),
            investigation_epoch=str(snapshot.evidence_epoch),
            frontier_start=str(snapshot.start_date),
            frontier_end=str(snapshot.end_date),
            snapshot_authority=dict(snapshot.source_authority),
            produced_at=str(created_at), source=source)
        binding = bind_m5_candle_authority(
            authority, bound_at=str(created_at))
        store.register(authority, bound_at=str(created_at))
    except CandleAuthorityError as exc:
        return None, None, _governed_candle_authority_state(
            present=False, reason=str(exc.reason_code), detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - a governed gap, never a crash
        return None, None, _governed_candle_authority_state(
            present=False, reason="CANDLE_AUTHORITY_UNAVAILABLE",
            detail=f"{type(exc).__name__}:{exc}")
    return authority, binding, _governed_candle_authority_state(
        present=True, authority=authority, binding=binding,
        detail="produced from the governed events_v1 M5 candle stream")


def _produce_governed_counterfactual_evidence(
    *, snapshot_id: str, manifest_directory: Path | str, source: Any,
    candle_authority: Any, candle_authority_binding: Any,
    store: CounterfactualEvidenceStore, producer: Callable[..., Any] | None,
    produced_at: str,
) -> tuple[Any, Any, dict[str, Any]]:
    """Freeze the governed counterfactual evidence upstream of the worker."""
    state: dict[str, Any] = {
        "evidence_class": COUNTERFACTUAL_EVIDENCE_CLASS,
        "present": False,
        "dataset_id": None,
        "content_digest": None,
        "scientifically_analysable": False,
        "reason_codes": [],
        "admissible_rows": 0,
        "excluded_rows": 0,
        "candle_authority_id": None,
        "fail_closed_reason": None,
        "detail": "",
    }
    if producer is None:
        state["fail_closed_reason"] = "COUNTERFACTUAL_PRODUCER_DISABLED"
        state["detail"] = (
            "no governed counterfactual evidence producer is configured")
        return None, None, state
    if candle_authority is None:
        state["fail_closed_reason"] = "MISSING_M5_CANDLE_AUTHORITY"
        state["detail"] = (
            "no governed M5 candle authority is bound to this frontier, so no "
            "counterfactual evidence can be produced")
        return None, None, state
    try:
        snapshot: InvestigationSnapshot = load_investigation_snapshot_id(
            str(snapshot_id), manifest_directory=Path(manifest_directory))
        reader = open_investigation_snapshot(
            str(snapshot_id), source=source,
            manifest_directory=Path(manifest_directory))
        shadow_rows = reader.cycle_cached_dataset("shadow_runtime")
        identities = {
            item.dataset: str(item.content_digest)
            for item in snapshot.datasets}
        identities["events"] = str(candle_authority.content_digest)
        artifact = producer(
            shadow_runtime_rows=shadow_rows,
            candle_authority=candle_authority,
            candle_authority_binding=candle_authority_binding,
            snapshot_id=str(snapshot.snapshot_id),
            snapshot_fingerprint=str(snapshot.snapshot_fingerprint),
            investigation_epoch=str(snapshot.evidence_epoch),
            frontier_start=str(snapshot.start_date),
            frontier_end=str(snapshot.end_date),
            produced_at=str(produced_at),
            source_dataset_identities=identities)
        artifact = validate_governed_counterfactual_evidence(artifact)
        binding = bind_counterfactual_evidence(
            artifact, bound_at=str(produced_at))
        store.register(artifact, bound_at=str(produced_at))
    except CounterfactualEvidenceError as exc:
        state["fail_closed_reason"] = str(exc).split(":", 1)[0]
        state["detail"] = str(exc)
        return None, None, state
    except CandleAuthorityError as exc:
        state["fail_closed_reason"] = str(exc.reason_code)
        state["detail"] = str(exc)
        return None, None, state
    except Exception as exc:  # noqa: BLE001 - a governed gap, never a crash
        state["fail_closed_reason"] = "MISSING_COUNTERFACTUAL_EVIDENCE"
        state["detail"] = f"{type(exc).__name__}:{exc}"
        return None, None, state
    state.update({
        "present": True,
        "dataset_id": str(artifact.dataset_id),
        "content_digest": str(artifact.content_digest),
        "scientifically_analysable": bool(artifact.scientifically_analysable),
        "reason_codes": list(artifact.reason_codes),
        "admissible_rows": len(artifact.rows),
        "excluded_rows": len(artifact.exclusions),
        "candle_authority_id": str(artifact.candle_authority_id),
        "detail": (
            "frozen upstream from the governed snapshot + candle authority"),
    })
    return artifact, binding, state


@_exclusive_continuous_cycle
def run_continuous_research_cycle(
    *, state_root: Path | str = Path("data/research/continuous"),
    frontier_runner: Callable[..., Any] = run_frontier_snapshot_cycle,
    question_runner: Callable[..., Any] = run_canonical_question_cycle,
    bridge_runner: Callable[..., Any] = run_scientific_state_bridge,
    q71_runner: Callable[..., Mapping[str, Any]] = run_q71_orchestration,
    question_registry_loader: Callable[[], Any] | None = None,
    frontier_kwargs: Mapping[str, Any] | None = None,
    question_kwargs: Mapping[str, Any] | None = None,
    bridge_kwargs: Mapping[str, Any] | None = None,
    q71_kwargs: Mapping[str, Any] | None = None,
    q71_evaluator_registry: GeneratedQuestionEvaluatorRegistry | None = None,
    q71_execution_policy: GeneratedExecutionPolicy | None = None,
    q71_manifest_directory: Path | str | None = None,
    q71_evidence_source: Any | None = None,
    generated_execution_store: GeneratedQuestionExecutionStore | None = None,
    generated_result_store: GeneratedQuestionResultStore | None = None,
    generated_bridge_runner: Callable[..., Any] = run_generated_scientific_bridge,
    validation_executor: Callable[..., Mapping[str, Any]] | None = None,
    forward_executor: Callable[..., Mapping[str, Any]] | None = None,
    max_validation_jobs: int = 1,
    q71_capacity: int = 10,
    observation_space_materializer: Callable[..., Any] = materialize_production_coverage,
    observation_space_manifest_directory: Path | str | None = None,
    observation_space_directory: Path | str | None = None,
    production_coverage_directory: Path | str | None = None,
    production_coverage_store_path: Path | str | None = None,
    shadow_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    enable_deep_work: bool = True,
    execution_policy: ResearchExecutionPolicy | None = None,
    deep_work_queue_path: Path | str | None = None,
    m5_candle_authority_producer: Callable[..., Any] | None = (
        build_governed_m5_candle_authority),
    m5_candle_authority_store: M5CandleAuthorityStore | None = None,
    candle_authority_directory: Path | str | None = None,
    counterfactual_evidence_producer: Callable[..., Any] | None = (
        build_governed_counterfactual_evidence),
    counterfactual_evidence_store: CounterfactualEvidenceStore | None = None,
    counterfactual_evidence_directory: Path | str | None = None,
) -> ContinuousResearchCycleResult:
    """Run one external-scheduler-friendly cycle; it never promotes live state.

    The governed M5 candle authority and the governed counterfactual evidence are
    produced here, *upstream* of the snapshot-only Q71 worker.  The worker only
    ever receives the frozen artifact plus its snapshot-pinned membership.
    """
    cycle_attempt_id = uuid.uuid4().hex.upper()
    root = Path(state_root)
    cycle_store = ContinuousCycleStore(root / "cycles")
    progress_store = ContinuousCycleProgressStore(root / "cycle_progress.json")
    projection_store = ResearchProjectionStore(root / "projection")
    research_work_store = ResearchWorkQueueStore(
        deep_work_queue_path or (root / "deep_work_queue.json"))
    validation_store: ValidationQueueStore | None = None
    question_state_dir = Path((question_kwargs or {}).get(
        "state_directory", "reports/research/questions/_canonical_cycle"))
    scientific_state_dir = Path((bridge_kwargs or {}).get(
        "scientific_state_directory", "data/research/scientific_state"))
    registry_dir = Path((bridge_kwargs or {}).get(
        "optimisation_registry_directory", "data/research/optimisation"))
    previous = cycle_store.load_latest_success()
    predecessor_id = previous.continuous_cycle_id if previous else None
    stages = {name: "NOT_RUN" for name in STAGES}
    frontier = question = bridge = None
    q71: Mapping[str, Any] = {}
    projection_path = None
    # The observation/coverage surface is explicit from the start: the Lab must
    # never show an empty-looking success state when no snapshot exists.
    observation_coverage: dict[str, Any] = {
        "status": NO_PRODUCTION_OBSERVATION_SPACE,
        "reason": "observation space stage has not run",
        "observation_space_snapshot_id": None,
        "production_coverage_snapshot_id": None,
        "total_governed_observation_cells": 0,
        "cell_count": 0,
        "conserved": False,
    }
    q71_coverage_store: ResearchCoverageStore | None = None
    q71_coverage_artifact: Any | None = None
    started = previous.completed_at if previous else ""
    evaluation_only_question_ids: tuple[str, ...] | None = None
    retained_projection: Mapping[str, Any] | None = None

    def progress(method: str, *args: Any, **kwargs: Any) -> None:
        try:
            getattr(progress_store, method)(*args, **kwargs)
        except Exception as exc:  # diagnostics must not alter scientific outcomes
            print(f"continuous progress persistence failed: {type(exc).__name__}:{exc}",
                  file=sys.stderr)

    progress("start", cycle_attempt_id=cycle_attempt_id)

    def failed(stage: str, exc: BaseException, *, identity: Mapping[str, Any]) -> ContinuousResearchCycleResult:
        stages[stage] = "FAILED"
        stamp = str(_value(frontier, "frontier_end", "") or started)
        failure_identity = _cycle_id({
            **identity, "failure_stage": stage,
            "failure": f"{type(exc).__name__}:{exc}",
        })
        result = ContinuousResearchCycleResult(
            continuous_cycle_id=_attempt_cycle_id(cycle_attempt_id, failure_identity),
            cycle_outcome="FAILED", frontier_snapshot_id=_value(frontier, "snapshot_id"),
            question_cycle_id=_value(question, "cycle_id"),
            bridge_run_id=_value(bridge, "bridge_run_id"),
            q71_agenda_id=q71.get("agenda_id"), q71_queue_id=q71.get("queue_id"),
            validation_queue_version=None, projection_version=None,
            predecessor_cycle_id=predecessor_id, started_at=started,
            completed_at=stamp, stage_statuses=dict(stages), failure_stage=stage,
            failure_reason=f"{type(exc).__name__}:{exc}", projection_path=projection_path,
            cycle_attempt_id=cycle_attempt_id,
            evidence_identity=(
                None if _value(frontier, "snapshot_id") is None
                else str(_value(frontier, "snapshot_id"))
            ),
        )
        cycle_store.save(result, successful=False)
        progress("finish", "FAILED", failure_stage=stage)
        return result

    progress("enter_stage", "FRONTIER")
    try:
        frontier = frontier_runner(**dict(frontier_kwargs or {}))
        stages["FRONTIER"] = str(_value(frontier, "status", "UNKNOWN"))
        progress("exit_stage", "FRONTIER", status=stages["FRONTIER"], details={
            "snapshot_id": _value(frontier, "snapshot_id"),
            "frontier_id": _value(frontier, "frontier_id"),
        })
    except Exception as exc:
        progress("exit_stage", "FRONTIER", status="FAILED", details={
            "failure": f"{type(exc).__name__}:{exc}"})
        return failed("FRONTIER", exc, identity={"predecessor": predecessor_id})
    started = str(_value(frontier, "frontier_end", "") or started)
    bootstrap_from_existing_snapshot = False
    if _retained_coherent_frontier_is_usable(frontier):
        try:
            latest = projection_store.load_latest()
            canonical_projection = QuestionCycleStore(question_state_dir).load_current()
        except Exception as exc:
            return failed("PROJECTION", exc, identity={
                "snapshot": _value(frontier, "snapshot_id"), "predecessor": predecessor_id})
        # Neither a no-op nor an explicitly retained incomplete candidate moves
        # the governed EVIDENCE frontier.  That says nothing about whether the
        # EVALUATOR is current, so evaluation identity is checked independently.
        stale_questions = _stale_evaluation_questions(
            latest, question_state_dir=question_state_dir,
            registry_loader=question_registry_loader)
        if _is_current_successful_projection(
                latest, frontier, stale_questions, canonical_projection):
            for stage in ("QUESTIONS", "SCIENTIFIC_STATE", "OBSERVATION_SPACE",
                          "Q71_PLUS", "Q71_EXECUTION", "VALIDATION_QUEUE"):
                stages[stage] = "SKIPPED_NO_NEW_EVIDENCE"
                progress("skip_stage", stage, "NO_NEW_EVIDENCE_WITH_CURRENT_PROJECTION")
            stages["PROJECTION"] = "RETAINED"
            progress("skip_stage", "PROJECTION", "CURRENT_PROJECTION_RETAINED")
            material = {"outcome": "NO_NEW_RESEARCH_EVIDENCE",
                        "snapshot_id": _value(frontier, "snapshot_id"),
                        "frontier_id": _value(frontier, "frontier_id"),
                        "predecessor": predecessor_id}
            evidence_identity = (
                None if _value(frontier, "snapshot_id") is None
                else str(_value(frontier, "snapshot_id"))
            )
            result = ContinuousResearchCycleResult(
                continuous_cycle_id=_attempt_cycle_id(cycle_attempt_id, _cycle_id(material)),
                cycle_outcome="NO_NEW_RESEARCH_EVIDENCE",
                frontier_snapshot_id=_value(frontier, "snapshot_id"), question_cycle_id=None,
                bridge_run_id=None, q71_agenda_id=None, q71_queue_id=None,
                validation_queue_version=None,
                projection_version=latest.get("projection_version"),
                predecessor_cycle_id=predecessor_id, started_at=started, completed_at=started,
                stage_statuses=stages,
                projection_path=str(projection_store.latest_path),
                cycle_attempt_id=cycle_attempt_id, evidence_identity=evidence_identity,
            )
            cycle_store.save(result, successful=True)
            progress("finish", "COMPLETED")
            return result
        if not all(str(_value(frontier, name) or "") for name in (
                "snapshot_id", "fingerprint", "investigation_epoch")):
            return failed("FRONTIER", ContinuousResearchLoopError(
                "BOOTSTRAP_FRONTIER_IDENTITY_INCOMPLETE"),
                identity={"frontier": _value(frontier, "frontier_id")})
        # The immutable frontier already exists, but the downstream read model
        # has never been published.  Run Blocks 2-4 once from that exact
        # snapshot; the canonical question runner validates its manifest,
        # fingerprint, epoch and bound object identities before evaluation.
        unknown = sorted(qid for qid in stale_questions if qid.startswith("__"))
        if unknown:
            return failed("QUESTIONS", ContinuousResearchLoopError(
                "EVALUATION_IDENTITY_UNAVAILABLE:" + ",".join(unknown)),
                identity={"frontier": _value(frontier, "frontier_id")})
        if latest is not None:
            evaluation_only_question_ids = tuple(sorted(stale_questions))
            retained_projection = latest
        bootstrap_from_existing_snapshot = True
    if (not bootstrap_from_existing_snapshot and (
            _value(frontier, "status") != SNAPSHOT_READY
            or _value(frontier, "verification_status") != "VERIFIED")):
        return failed("FRONTIER", ContinuousResearchLoopError(
            "FRONTIER_DID_NOT_PRODUCE_VERIFIED_SNAPSHOT:" + str(_value(frontier, "failure_reason"))),
            identity={"frontier": _value(frontier, "frontier_id")})

    progress("enter_stage", "QUESTIONS", details={
        "questions_entered": 0, "questions_completed": 0})
    try:
        question_options = dict(question_kwargs or {})

        def question_progress(event: Mapping[str, Any]) -> None:
            progress("update_stage", "QUESTIONS", event)

        question_options.setdefault("progress_callback", question_progress)
        if enable_deep_work:
            question_options.setdefault("deep_work_store", research_work_store)
            question_options.setdefault(
                "execution_policy", execution_policy or ResearchExecutionPolicy())
        if evaluation_only_question_ids is not None:
            question_options.setdefault(
                "evaluation_only_question_ids", evaluation_only_question_ids)
        question = question_runner(frontier, **question_options)
        if _value(question, "total_questions") != 70 or str(_value(question, "cycle_status")) not in {
            "COMPLETED", "COMPLETED_WITH_QUESTION_FAILURES",
        }:
            raise ContinuousResearchLoopError("CANONICAL_70_ACCOUNTING_INVALID")
        stages["QUESTIONS"] = str(_value(question, "cycle_status"))
        progress("exit_stage", "QUESTIONS", status=stages["QUESTIONS"], details={
            "questions_completed": _value(question, "total_questions", 0),
            "evaluated_count": _value(question, "evaluated_count", 0),
            "retained_count": _value(question, "retained_count", 0),
        })
    except Exception as exc:
        progress("exit_stage", "QUESTIONS", status="FAILED", details={
            "failure": f"{type(exc).__name__}:{exc}"})
        return failed("QUESTIONS", exc, identity={"snapshot": _value(frontier, "snapshot_id")})

    material_question_changes = tuple(_value(question, "changed_question_ids", ()) or ())
    if evaluation_only_question_ids is not None and not material_question_changes:
        for stage in ("SCIENTIFIC_STATE", "OBSERVATION_SPACE", "Q71_PLUS",
                      "VALIDATION_QUEUE"):
            stages[stage] = "SKIPPED_NO_MATERIAL_QUESTION_CHANGE"
            progress("skip_stage", stage, "EVALUATION_REFRESH_WITH_EQUIVALENT_SCIENCE")
        progress("enter_stage", "PROJECTION")
        try:
            if retained_projection is None:
                raise ContinuousResearchLoopError(
                    "EVALUATION_REFRESH_WITHOUT_RETAINED_PROJECTION")
            qstore = QuestionCycleStore(question_state_dir)
            question_projection = qstore.load_current()
            cycle_material = {
                "snapshot": _value(frontier, "snapshot_id"),
                "question": _value(question, "cycle_id"),
                "evaluation_refresh": evaluation_only_question_ids,
                "predecessor": predecessor_id,
            }
            cycle_id = _attempt_cycle_id(cycle_attempt_id, _cycle_id(cycle_material))
            projection = build_evaluation_refresh_projection(
                continuous_cycle_id=cycle_id, frontier=frontier,
                question_projection=question_projection,
                predecessor_projection=retained_projection,
                refreshed_question_ids=evaluation_only_question_ids,
            )
            projection_path = str(projection_store.save(projection))
            stages["PROJECTION"] = "COMPLETED_EVALUATION_REFRESH"
            progress("exit_stage", "PROJECTION", status=stages["PROJECTION"], details={
                "projection_version": projection["projection_version"],
                "projection_path": projection_path,
            })
        except Exception as exc:
            progress("exit_stage", "PROJECTION", status="FAILED", details={
                "failure": f"{type(exc).__name__}:{exc}"})
            return failed("PROJECTION", exc, identity={
                "question": _value(question, "cycle_id")})
        completed = str(_value(question, "completed_at", started))
        result = ContinuousResearchCycleResult(
            continuous_cycle_id=cycle_id, cycle_outcome="COMPLETED",
            frontier_snapshot_id=str(_value(frontier, "snapshot_id")),
            question_cycle_id=str(_value(question, "cycle_id")),
            bridge_run_id=None, q71_agenda_id=None, q71_queue_id=None,
            validation_queue_version=None,
            projection_version=projection["projection_version"],
            predecessor_cycle_id=predecessor_id, started_at=started,
            completed_at=completed, stage_statuses=stages,
            projection_path=projection_path, cycle_attempt_id=cycle_attempt_id,
            evidence_identity=str(_value(frontier, "snapshot_id")),
        )
        cycle_store.save(result, successful=True)
        progress("finish", "COMPLETED")
        return result

    progress("enter_stage", "SCIENTIFIC_STATE")
    try:
        bridge = bridge_runner(question, **dict(bridge_kwargs or {}))
        if str(_value(bridge, "status")) not in {
            "COMPLETED", "COMPLETED_WITH_REVIEW_REQUIRED",
            "COMPLETED_WITH_ITEM_FAILURES", "NO_SCIENTIFIC_STATE_CHANGE",
        }:
            raise ContinuousResearchLoopError("SCIENTIFIC_STATE_BRIDGE_NOT_COMPLETE")
        stages["SCIENTIFIC_STATE"] = str(_value(bridge, "status"))
        progress("exit_stage", "SCIENTIFIC_STATE", status=stages["SCIENTIFIC_STATE"])
    except Exception as exc:
        progress("exit_stage", "SCIENTIFIC_STATE", status="FAILED", details={
            "failure": f"{type(exc).__name__}:{exc}"})
        return failed("SCIENTIFIC_STATE", exc, identity={"question_cycle": _value(question, "cycle_id")})

    # OBSERVATION_SPACE: materialize the governed observation space from the real
    # frozen evidence frontier, build the immutable coverage snapshot and hand it
    # to Q71+ generation through the governed coverage store.  This is the
    # "eyes" of autonomous discovery: without it, generation has no governed
    # coverage to reason over, and the stage fails closed instead of inventing
    # one.
    progress("enter_stage", "OBSERVATION_SPACE")
    candle_authority = None
    candle_authority_binding = None
    candle_authority_state = _governed_candle_authority_state(present=False)
    counterfactual_evidence = None
    counterfactual_binding = None
    counterfactual_state: dict[str, Any] = {"present": False}
    try:
        coverage_projection = QuestionCycleStore(
            question_state_dir).load_current()
        active_research: dict[str, list[str]] = {}
        for job in research_work_store.ordered():
            if job.state in {PENDING, RUNNING}:
                active_research.setdefault(job.question_id, []).append(job.job_id)
        # The governed M5 candle authority is admitted *before* coverage so the
        # candidate-capable cell reports its true evidence readiness.  It is
        # produced upstream of the snapshot-only Q71 worker, which never reads
        # storage.
        candle_authority, candle_authority_binding, candle_authority_state = (
            _materialize_governed_candle_authority(
                snapshot_id=str(_value(frontier, "snapshot_id")),
                manifest_directory=Path(
                    observation_space_manifest_directory or MANIFEST_DIRECTORY),
                source=q71_evidence_source,
                store=(m5_candle_authority_store or M5CandleAuthorityStore(
                    candle_authority_directory
                    if candle_authority_directory is not None
                    else DEFAULT_M5_CANDLE_AUTHORITY_DIRECTORY)),
                producer=m5_candle_authority_producer,
                created_at=str(_value(question, "completed_at", started))))
        materialized = observation_space_materializer(
            snapshot_id=str(_value(frontier, "snapshot_id")),
            manifest_directory=Path(
                observation_space_manifest_directory or MANIFEST_DIRECTORY),
            observation_directory=Path(
                observation_space_directory or (root / "observation_space")),
            coverage_directory=Path(
                production_coverage_directory
                or DEFAULT_PRODUCTION_COVERAGE_DIRECTORY),
            coverage_store_path=Path(
                production_coverage_store_path
                or DEFAULT_PRODUCTION_COVERAGE_STORE_PATH),
            canonical_question_projection=coverage_projection,
            active_research_by_question={
                key: tuple(value) for key, value in active_research.items()},
            evaluator_registry=q71_evaluator_registry,
            q71_state=load_q71_state(root / "q71_state.json"),
            candle_authority=candle_authority,
            candle_authority_binding=candle_authority_binding,
            created_at=str(_value(question, "completed_at", started)),
            observed_at=str(_value(question, "completed_at", started)),
        )
        q71_coverage_store = materialized.coverage_store
        q71_coverage_artifact = materialized.coverage
        observation_coverage = dict(materialized.surface)
        observation_coverage["m5_candle_authority"] = {
            **(observation_coverage.get("m5_candle_authority") or {}),
            **candle_authority_state,
        }
        stages["OBSERVATION_SPACE"] = "COMPLETED"
        progress("exit_stage", "OBSERVATION_SPACE", status="COMPLETED", details={
            "observation_space_snapshot_id":
                materialized.observation_space_snapshot_id,
            "production_coverage_snapshot_id":
                materialized.production_coverage_snapshot_id,
            "cell_count": materialized.coverage.cell_count,
            "blind_spot_count": observation_coverage.get("blind_spot_count"),
            "m5_candle_authority_present": candle_authority_state["present"],
            "m5_candle_authority_id": candle_authority_state["authority_id"],
        })
    except Exception as exc:
        # Fail closed: Q71+ generation is handed an empty governed coverage
        # store, so no question can be minted from an unmaterialized space.
        stages["OBSERVATION_SPACE"] = "FAILED_CLOSED"
        observation_coverage = {
            "status": NO_PRODUCTION_OBSERVATION_SPACE,
            "reason": f"{type(exc).__name__}:{exc}",
            "observation_space_snapshot_id": None,
            "production_coverage_snapshot_id": None,
            "total_governed_observation_cells": 0,
            "cell_count": 0,
            "conserved": False,
            "m5_candle_authority": candle_authority_state,
        }
        candle_authority = None
        candle_authority_binding = None
        q71_coverage_store = ResearchCoverageStore(
            root / "observation_space" / "failed_closed_coverage.json")
        progress("exit_stage", "OBSERVATION_SPACE", status="FAILED_CLOSED",
                 details={"failure": f"{type(exc).__name__}:{exc}"})

    progress("enter_stage", "Q71_PLUS")
    try:
        qkwargs = dict(q71_kwargs or {})
        qkwargs.setdefault("snapshot_id", str(_value(frontier, "snapshot_id")))
        qkwargs.setdefault("capacity", q71_capacity)
        qkwargs.setdefault("state_path", root / "q71_state.json")
        if q71_coverage_store is not None:
            # The materialized production coverage snapshot is the ONLY coverage
            # authority Q71+ generation may read.
            qkwargs.setdefault("coverage_store", q71_coverage_store)
        if q71_evaluator_registry is not None:
            # The governed registry is the single eligibility authority: the
            # question generation declares executable must be exactly the
            # question the worker can execute.
            qkwargs.setdefault("evaluator_registry", q71_evaluator_registry)
        q71 = q71_runner(**qkwargs)
        stages["Q71_PLUS"] = str(q71.get("status", "COMPLETED"))
        progress("exit_stage", "Q71_PLUS", status=stages["Q71_PLUS"])
    except Exception as exc:
        q71 = {"status": "FAILED_OPTIONAL", "failure_reason": f"{type(exc).__name__}:{exc}",
               "generated_questions": [], "queue": []}
        stages["Q71_PLUS"] = "FAILED_OPTIONAL"
        progress("exit_stage", "Q71_PLUS", status="FAILED_OPTIONAL", details={
            "failure": f"{type(exc).__name__}:{exc}"})

    # Bind every generated coverage question back to the observation cell and
    # coverage snapshot that caused it to exist.  This is a pointer document: it
    # names the immutable coverage artifact it was derived from.
    if q71_coverage_artifact is not None:
        try:
            mapping_path = write_q71_coverage_source_mapping(
                q71_coverage_artifact,
                load_q71_state(root / "q71_state.json"),
                directory=Path(
                    production_coverage_directory
                    or DEFAULT_PRODUCTION_COVERAGE_DIRECTORY),
                generated_store=GeneratedResearchStore())
            progress("exit_stage", "OBSERVATION_SPACE", status="MAPPED", details={
                "q71_source_mapping_path": str(mapping_path)})
        except Exception as exc:  # diagnostics must not alter science
            print("q71 coverage source mapping failed: "
                  f"{type(exc).__name__}:{exc}", file=sys.stderr)

    progress("enter_stage", "Q71_EXECUTION")
    generated_execution: dict[str, Any] = {}
    generated_bridge = None
    try:
        if q71_evaluator_registry is None:
            stages["Q71_EXECUTION"] = "SKIPPED_NO_EVALUATOR_REGISTRY"
            progress("skip_stage", "Q71_EXECUTION", "NO_GOVERNED_EVALUATOR_REGISTRY")
        else:
            exec_store = generated_execution_store or GeneratedQuestionExecutionStore(
                root / "q71_execution_state.json")
            res_store = generated_result_store or GeneratedQuestionResultStore(
                root / "q71_results")
            # Produce the frozen governed counterfactual evidence upstream of the
            # worker.  The worker receives only the frozen artifact plus its
            # snapshot-pinned membership and never reads storage.
            (
                counterfactual_evidence, counterfactual_binding,
                counterfactual_state,
            ) = _produce_governed_counterfactual_evidence(
                snapshot_id=str(_value(frontier, "snapshot_id")),
                manifest_directory=Path(
                    observation_space_manifest_directory or MANIFEST_DIRECTORY),
                source=q71_evidence_source,
                candle_authority=candle_authority,
                candle_authority_binding=candle_authority_binding,
                store=(counterfactual_evidence_store
                       or CounterfactualEvidenceStore(
                           counterfactual_evidence_directory
                           if counterfactual_evidence_directory is not None
                           else DEFAULT_COUNTERFACTUAL_EVIDENCE_DIRECTORY)),
                producer=counterfactual_evidence_producer,
                produced_at=str(_value(question, "completed_at", started)))
            observation_coverage["counterfactual_evidence"] = (
                counterfactual_state)
            generated_execution = run_generated_question_worker(
                snapshot_id=str(_value(frontier, "snapshot_id")),
                evaluator_registry=q71_evaluator_registry,
                result_store=res_store, execution_store=exec_store,
                q71_state_path=root / "q71_state.json",
                **({"manifest_directory": q71_manifest_directory}
                   if q71_manifest_directory is not None else {}),
                **({"source": q71_evidence_source}
                   if q71_evidence_source is not None else {}),
                policy=q71_execution_policy or GeneratedExecutionPolicy(),
                # Wire 1: hand the SAME cycle's frozen governed counterfactual
                # evidence artifact and its snapshot-pinned governed membership
                # to the worker.  The worker never reads storage; it admits only
                # what verifies against this exact frontier.  When the artifact
                # is absent both are None and the worker keeps its existing
                # fail-closed path (it never falls back to latest/global state).
                governed_counterfactual_evidence=counterfactual_evidence,
                governed_counterfactual_binding=counterfactual_binding)
            batch_id = generated_execution.get("batch_id")
            if batch_id:
                generated_bridge = generated_bridge_runner(
                    batch_id, generated_result_store=res_store,
                    scientific_state_directory=scientific_state_dir,
                    optimisation_registry_directory=registry_dir)
                stages["Q71_EXECUTION"] = str(
                    _value(generated_bridge, "status", "COMPLETED"))
            else:
                stages["Q71_EXECUTION"] = "NO_EXECUTABLE_GENERATED_QUESTION"
            progress("exit_stage", "Q71_EXECUTION",
                     status=stages["Q71_EXECUTION"], details={
                         "executed": len(generated_execution.get("outcomes", [])),
                         "results": len(generated_execution.get("result_ids", [])),
                         "counterfactual_evidence_present":
                             bool(counterfactual_state.get("present")),
                         "counterfactual_fail_closed_reason":
                             counterfactual_state.get("fail_closed_reason"),
                     })
    except Exception as exc:
        # Generated execution is bounded and additive: a failure here must never
        # invalidate canonical scientific state, but it must be visible.
        generated_execution = {
            "status": "FAILED_OPTIONAL",
            "failure_reason": f"{type(exc).__name__}:{exc}",
            "outcomes": [], "result_ids": [],
        }
        observation_coverage["counterfactual_evidence"] = {
            **counterfactual_state,
            "fail_closed_reason": (
                counterfactual_state.get("fail_closed_reason")
                or "MISSING_COUNTERFACTUAL_EVIDENCE"),
            "detail": f"{type(exc).__name__}:{exc}",
        }
        stages["Q71_EXECUTION"] = "FAILED_OPTIONAL"
        progress("exit_stage", "Q71_EXECUTION", status="FAILED_OPTIONAL",
                 details={"failure": f"{type(exc).__name__}:{exc}"})


    progress("enter_stage", "VALIDATION_QUEUE", details={"max_jobs": max_validation_jobs})
    try:
        validation_store = ValidationQueueStore(root / "validation_queue.json")
        registry = OptimisationRegistry(str(registry_dir))
        registry.load()
        enqueue_validation_handoff(
            _value(bridge, "validation_handoff", ()), registry=registry,
            store=validation_store, snapshot_id=str(_value(frontier, "snapshot_id")),
            cycle_id=str(_value(question, "cycle_id")),
        )
        processed = process_validation_queue(
            store=validation_store, registry=registry,
            validation_executor=validation_executor, forward_executor=forward_executor,
            max_jobs=max_validation_jobs,
        )
        # Scan the authority, not only this process's transitions.  This closes
        # the crash window between a persisted VALIDATED transition and its
        # forward-validation handoff.
        for candidate in sorted(registry.list_candidates("VALIDATED"),
                                key=lambda row: row.candidate_id):
            plan = registry.get_plan(candidate.candidate_id)
            if plan:
                enqueue_forward_validation(
                    candidate, plan.to_dict(), store=validation_store,
                    snapshot_id=str(_value(frontier, "snapshot_id")),
                    cycle_id=str(_value(question, "cycle_id")),
                    timestamp=str(_value(question, "completed_at", "")),
                )
        if forward_executor is not None:
            follow = process_validation_queue(
                store=validation_store, registry=registry,
                validation_executor=validation_executor, forward_executor=forward_executor,
                max_jobs=max_validation_jobs,
            )
            processed["transitions"].extend(follow["transitions"])
            processed["shadow_eligibility"] = follow["shadow_eligibility"]
        queue_version = hashlib.sha256(canonical_json(
            [job.to_dict() for job in validation_store.ordered()]).encode("utf-8")).hexdigest()
        stages["VALIDATION_QUEUE"] = "COMPLETED"
        progress("exit_stage", "VALIDATION_QUEUE", status="COMPLETED", details={
            "transition_count": len(processed["transitions"]),
            "shadow_eligibility_count": len(processed["shadow_eligibility"]),
        })
    except Exception as exc:
        progress("exit_stage", "VALIDATION_QUEUE", status="FAILED", details={
            "failure": f"{type(exc).__name__}:{exc}"})
        return failed("VALIDATION_QUEUE", exc, identity={"bridge": _value(bridge, "bridge_run_id")})

    progress("enter_stage", "PROJECTION")
    try:
        if validation_store is None:  # pragma: no cover - guarded by validation stage
            raise ContinuousResearchLoopError("VALIDATION_QUEUE_NOT_INITIALISED")
        qstore = QuestionCycleStore(question_state_dir)
        question_projection = qstore.load_current()
        scientific_store = ScientificStateStore(scientific_state_dir)
        previous_projection = projection_store.load_latest()
        cycle_material = {
            "snapshot": _value(frontier, "snapshot_id"), "question": _value(question, "cycle_id"),
            "bridge": _value(bridge, "bridge_run_id"), "agenda": q71.get("agenda_id"),
            "validation_queue_version": queue_version, "predecessor": predecessor_id,
        }
        identity = _cycle_id(cycle_material)
        cycle_id = _attempt_cycle_id(cycle_attempt_id, identity)
        projection = build_unified_research_projection(
            continuous_cycle_id=cycle_id, frontier=frontier,
            question_projection=question_projection, bridge=bridge,
            scientific_store=scientific_store, optimisation_registry=registry,
            validation_store=validation_store, q71=q71,
            shadow_evidence=shadow_evidence, predecessor_projection=previous_projection,
            research_work_store=research_work_store,
            observation_coverage=observation_coverage,
            generated_execution_store=(
                generated_execution_store or GeneratedQuestionExecutionStore(
                    root / "q71_execution_state.json")),
            generated_result_store=(
                generated_result_store or GeneratedQuestionResultStore(
                    root / "q71_results")),
            projection_generated_at=str(_value(question, "completed_at", started)),
        )
        projection_path = str(projection_store.save(projection))
        stages["PROJECTION"] = "COMPLETED"
        progress("exit_stage", "PROJECTION", status="COMPLETED", details={
            "projection_version": projection["projection_version"],
            "projection_path": projection_path,
        })
    except Exception as exc:
        progress("exit_stage", "PROJECTION", status="FAILED", details={
            "failure": f"{type(exc).__name__}:{exc}"})
        return failed("PROJECTION", exc, identity={"bridge": _value(bridge, "bridge_run_id")})

    completed = str(_value(question, "completed_at", started))
    result = ContinuousResearchCycleResult(
        continuous_cycle_id=cycle_id, cycle_outcome="COMPLETED",
        frontier_snapshot_id=str(_value(frontier, "snapshot_id")),
        question_cycle_id=str(_value(question, "cycle_id")),
        bridge_run_id=str(_value(bridge, "bridge_run_id")),
        q71_agenda_id=q71.get("agenda_id"), q71_queue_id=q71.get("queue_id"),
        validation_queue_version=queue_version,
        projection_version=projection["projection_version"],
        predecessor_cycle_id=predecessor_id, started_at=started,
        completed_at=completed, stage_statuses=stages,
        validation_transitions=tuple(processed["transitions"]),
        shadow_eligibility=tuple(processed["shadow_eligibility"]),
        projection_path=projection_path,
        cycle_attempt_id=cycle_attempt_id,
        evidence_identity=str(_value(frontier, "snapshot_id")),
    )
    try:
        cycle_store.save(result, successful=True)
    except Exception as exc:
        raise ContinuousResearchLoopError("CHECKPOINT_PERSISTENCE_FAILED") from exc
    progress("finish", "COMPLETED")
    return result


@_exclusive_continuous_cycle
def run_deep_research_job(
    *, state_root: Path | str = Path("data/research/continuous"),
    question_runner: Callable[..., Any] = run_canonical_question_cycle,
    bridge_runner: Callable[..., Any] = run_scientific_state_bridge,
    question_kwargs: Mapping[str, Any] | None = None,
    bridge_kwargs: Mapping[str, Any] | None = None,
    execution_policy: ResearchExecutionPolicy | None = None,
    deep_work_queue_path: Path | str | None = None,
    now: str | None = None,
) -> Mapping[str, Any]:
    """Execute and publish at most one current governed deep-research job.

    The shared cycle lease prevents publication races with ordinary refreshes.
    The accepted unified projection is updated only after the queued snapshot,
    evaluator identity, canonical result, and scientific bridge all validate.
    """
    root = Path(state_root)
    policy = execution_policy or ResearchExecutionPolicy()
    stamp = now or datetime.now(timezone.utc).isoformat()
    queue = ResearchWorkQueueStore(
        deep_work_queue_path or (root / "deep_work_queue.json"))
    job = queue.claim_next(now=stamp, policy=policy)
    if job is None:
        return {
            "status": "NO_DEEP_WORK_READY",
            "research_lag": queue.metrics(generated_at=stamp),
        }
    projection_store = ResearchProjectionStore(root / "projection")
    accepted = projection_store.load_latest()
    try:
        if accepted is None:
            raise ResearchWorkQueueError("DEEP_WORK_WITHOUT_ACCEPTED_PROJECTION")
        frontier = dict(accepted.get("data_frontier") or {})
        if (
            str(frontier.get("snapshot_id") or "")
            != job.triggering_snapshot_id
            or str(frontier.get("investigation_epoch") or "")
            != job.triggering_epoch_id
        ):
            raise ResearchWorkQueueError("DEEP_WORK_TRIGGER_NOT_CURRENT")
        accepted_questions = {
            str(row.get("question_id") or ""): row
            for row in accepted.get("canonical_questions", ())
            if isinstance(row, Mapping)
        }
        accepted_entry = accepted_questions.get(job.question_id)
        if (
            accepted_entry is None
            or accepted_entry.get("deep_work_job_id") != job.job_id
        ):
            raise ResearchWorkQueueError("DEEP_WORK_NOT_ACCEPTED_PENDING_AUTHORITY")
        running_projection = build_research_work_refresh_projection(
            continuous_cycle_id=_cycle_id({
                "deep_work_job_id": job.job_id,
                "state": "RUNNING",
                "attempt": job.attempts,
            }),
            predecessor_projection=accepted,
            research_work_store=queue,
            projection_generated_at=stamp,
        )
        projection_store.save(running_projection)
        accepted = running_projection

        question_options = dict(question_kwargs or {})
        question_options.update({
            "evaluation_only_question_ids": (job.question_id,),
            "force_inline_question_ids": (job.question_id,),
            "execution_revision": job.job_id,
            "deep_work_store": queue,
            "execution_policy": policy,
        })
        question = question_runner(frontier, **question_options)
        qstore = QuestionCycleStore(question_options.get(
            "state_directory", "reports/research/questions/_canonical_cycle"))
        question_projection = qstore.load_current()
        if question_projection is None:
            raise ResearchWorkQueueError("DEEP_WORK_RESULT_PROJECTION_MISSING")
        entry = (question_projection.get("questions") or {}).get(job.question_id)
        result = (entry or {}).get("result") or {}
        if (
            str(result.get("snapshot_id") or "") != job.triggering_snapshot_id
            or str(result.get("evaluation_identity_digest") or "")
            != job.evaluator_identity_digest
            or str((entry or {}).get("work_state") or "") != "FRESH"
        ):
            raise ResearchWorkQueueError("DEEP_WORK_RESULT_AUTHORITY_MISMATCH")

        bridge = bridge_runner(question, **dict(bridge_kwargs or {}))
        if str(_value(bridge, "status")) not in {
            "COMPLETED", "COMPLETED_WITH_REVIEW_REQUIRED",
            "COMPLETED_WITH_ITEM_FAILURES", "NO_SCIENTIFIC_STATE_CHANGE",
        }:
            raise ContinuousResearchLoopError(
                "DEEP_WORK_SCIENTIFIC_STATE_BRIDGE_NOT_COMPLETE")

        queue.complete(
            job.job_id,
            result_id=str(result.get("result_id") or ""),
            result_snapshot_id=str(result.get("snapshot_id") or ""),
            evaluator_identity_digest=str(
                result.get("evaluation_identity_digest") or ""),
            completed_at=stamp,
        )

        scientific_state_dir = Path((bridge_kwargs or {}).get(
            "scientific_state_directory", "data/research/scientific_state"))
        registry_dir = Path((bridge_kwargs or {}).get(
            "optimisation_registry_directory", "data/research/optimisation"))
        scientific_store = ScientificStateStore(scientific_state_dir)
        registry = OptimisationRegistry(str(registry_dir))
        registry.load()
        validation_store = ValidationQueueStore(root / "validation_queue.json")
        enqueue_validation_handoff(
            _value(bridge, "validation_handoff", ()), registry=registry,
            store=validation_store, snapshot_id=job.triggering_snapshot_id,
            cycle_id=str(_value(question, "cycle_id")),
        )
        q71 = {
            "generated_questions": list(
                accepted.get("generated_questions") or ()),
            "queue": list(
                (accepted.get("investigations_and_work_queues") or {})
                .get("generated_question_queue") or ()),
            "new_question_ids": [],
            "retired_question_ids": [],
        }
        cycle_id = _cycle_id({
            "deep_work_job_id": job.job_id,
            "result_id": result["result_id"],
            "predecessor_projection_version": accepted["projection_version"],
        })
        projection = build_unified_research_projection(
            continuous_cycle_id=cycle_id,
            frontier=frontier,
            question_projection=question_projection,
            bridge=bridge,
            scientific_store=scientific_store,
            optimisation_registry=registry,
            validation_store=validation_store,
            q71=q71,
            predecessor_projection=accepted,
            research_work_store=queue,
            projection_generated_at=stamp,
        )
        projection_path = projection_store.save(projection)
        return {
            "status": "COMPLETED",
            "job_id": job.job_id,
            "question_id": job.question_id,
            "result_id": result["result_id"],
            "triggering_epoch_id": job.triggering_epoch_id,
            "projection_version": projection["projection_version"],
            "projection_path": str(projection_path),
            "research_lag": projection["research_lag"],
        }
    except Exception as exc:
        current = queue.jobs.get(job.job_id)
        if current is not None and current.state == "RUNNING":
            queue.fail(
                job.job_id,
                reason=f"{type(exc).__name__}:{exc}",
                failed_at=stamp,
            )
        elif current is not None and current.state == "COMPLETED":
            current.state = DEEP_FAILED
            current.failure_reason = (
                "POST_COMPLETION_PUBLICATION_FAILED:"
                + f"{type(exc).__name__}:{exc}")
            current.transitions.append({
                "state": DEEP_FAILED,
                "at": stamp,
                "reason": current.failure_reason,
            })
            queue.save()
        if accepted is not None and current is not None:
            try:
                failed_projection = build_research_work_refresh_projection(
                    continuous_cycle_id=_cycle_id({
                        "deep_work_job_id": job.job_id,
                        "state": "FAILED",
                        "attempt": job.attempts,
                    }),
                    predecessor_projection=accepted,
                    research_work_store=queue,
                    projection_generated_at=stamp,
                )
                projection_store.save(failed_projection)
            except Exception:
                # The queue remains the failure authority; never mask the
                # original evaluator/publication exception.
                pass
        raise


def _production_evaluator_registry(
    path: Path | str | None = None,
) -> GeneratedQuestionEvaluatorRegistry | None:
    """Load the governed Q71+ evaluator registry when one is deployed.

    Absence is a legitimate production state: with no registry, every generated
    question resolves to MISSING_EVALUATOR and is shown as such rather than
    being executed by an evaluator that was never registered.
    """
    resolved = Path(path) if path is not None else DEFAULT_EVALUATOR_REGISTRY_PATH
    if not resolved.exists():
        return None
    return GeneratedQuestionEvaluatorRegistry(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded continuous research cycle")
    parser.add_argument("--state-root", default="data/research/continuous")
    parser.add_argument("--q71-capacity", type=int, default=10)
    parser.add_argument("--max-validation-jobs", type=int, default=1)
    parser.add_argument("--q71-evaluator-registry", default=None)
    parser.add_argument("--q71-max-executions", type=int, default=1)
    args = parser.parse_args(argv)
    result = run_continuous_research_cycle(
        state_root=args.state_root, q71_capacity=args.q71_capacity,
        max_validation_jobs=args.max_validation_jobs,
        q71_evaluator_registry=_production_evaluator_registry(
            args.q71_evaluator_registry),
        q71_execution_policy=GeneratedExecutionPolicy(
            max_questions_per_run=args.q71_max_executions))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.cycle_outcome in {"COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContinuousResearchLoopError",
    "run_continuous_research_cycle",
    "run_deep_research_job",
]
