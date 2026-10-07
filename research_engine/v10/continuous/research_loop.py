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

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.canonical_question_cycle import run_canonical_question_cycle
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleLease, ContinuousCycleProgressStore, ContinuousCycleStore,
    ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.frontier_coordinator import (
    FRONTIER_INCOMPLETE, NO_NEW_GOVERNED_EVIDENCE, SNAPSHOT_READY,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.q71_orchestration import run_q71_orchestration
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_projection import (
    ResearchProjectionStore, build_evaluation_refresh_projection,
    build_research_work_refresh_projection, build_unified_research_projection,
)
from research_engine.v10.continuous.research_work_queue import (
    FAILED as DEEP_FAILED,
    ResearchExecutionPolicy,
    ResearchWorkQueueError,
    ResearchWorkQueueStore,
)
from research_engine.v10.continuous.scientific_state_bridge import run_scientific_state_bridge
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.validation_queue import (
    ValidationQueueStore, enqueue_forward_validation, enqueue_validation_handoff,
    process_validation_queue,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


STAGES = ("FRONTIER", "QUESTIONS", "SCIENTIFIC_STATE", "Q71_PLUS",
          "VALIDATION_QUEUE", "PROJECTION")


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
    validation_executor: Callable[..., Mapping[str, Any]] | None = None,
    forward_executor: Callable[..., Mapping[str, Any]] | None = None,
    max_validation_jobs: int = 1,
    q71_capacity: int = 10,
    shadow_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    enable_deep_work: bool = True,
    execution_policy: ResearchExecutionPolicy | None = None,
    deep_work_queue_path: Path | str | None = None,
) -> ContinuousResearchCycleResult:
    """Run one external-scheduler-friendly cycle; it never promotes live state."""
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
            for stage in ("QUESTIONS", "SCIENTIFIC_STATE", "Q71_PLUS", "VALIDATION_QUEUE"):
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
        for stage in ("SCIENTIFIC_STATE", "Q71_PLUS", "VALIDATION_QUEUE"):
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

    progress("enter_stage", "Q71_PLUS")
    try:
        qkwargs = dict(q71_kwargs or {})
        qkwargs.setdefault("snapshot_id", str(_value(frontier, "snapshot_id")))
        qkwargs.setdefault("capacity", q71_capacity)
        qkwargs.setdefault("state_path", root / "q71_state.json")
        q71 = q71_runner(**qkwargs)
        stages["Q71_PLUS"] = str(q71.get("status", "COMPLETED"))
        progress("exit_stage", "Q71_PLUS", status=stages["Q71_PLUS"])
    except Exception as exc:
        q71 = {"status": "FAILED_OPTIONAL", "failure_reason": f"{type(exc).__name__}:{exc}",
               "generated_questions": [], "queue": []}
        stages["Q71_PLUS"] = "FAILED_OPTIONAL"
        progress("exit_stage", "Q71_PLUS", status="FAILED_OPTIONAL", details={
            "failure": f"{type(exc).__name__}:{exc}"})

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded continuous research cycle")
    parser.add_argument("--state-root", default="data/research/continuous")
    parser.add_argument("--q71-capacity", type=int, default=10)
    parser.add_argument("--max-validation-jobs", type=int, default=1)
    args = parser.parse_args(argv)
    result = run_continuous_research_cycle(
        state_root=args.state_root, q71_capacity=args.q71_capacity,
        max_validation_jobs=args.max_validation_jobs)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.cycle_outcome in {"COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContinuousResearchLoopError",
    "run_continuous_research_cycle",
    "run_deep_research_job",
]
