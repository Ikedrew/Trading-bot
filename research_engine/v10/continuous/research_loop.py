"""One bounded, restart-safe invocation of the four-block research loop."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.v10.continuous.canonical_question_cycle import run_canonical_question_cycle
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleStore, ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.frontier_coordinator import (
    NO_NEW_GOVERNED_EVIDENCE, SNAPSHOT_READY, run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.q71_orchestration import run_q71_orchestration
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_projection import (
    ResearchProjectionStore, build_unified_research_projection,
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


def _value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)


def _cycle_id(material: Mapping[str, Any]) -> str:
    return "CRCYCLE-" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()


def run_continuous_research_cycle(
    *, state_root: Path | str = Path("data/research/continuous"),
    frontier_runner: Callable[..., Any] = run_frontier_snapshot_cycle,
    question_runner: Callable[..., Any] = run_canonical_question_cycle,
    bridge_runner: Callable[..., Any] = run_scientific_state_bridge,
    q71_runner: Callable[..., Mapping[str, Any]] = run_q71_orchestration,
    frontier_kwargs: Mapping[str, Any] | None = None,
    question_kwargs: Mapping[str, Any] | None = None,
    bridge_kwargs: Mapping[str, Any] | None = None,
    q71_kwargs: Mapping[str, Any] | None = None,
    validation_executor: Callable[..., Mapping[str, Any]] | None = None,
    forward_executor: Callable[..., Mapping[str, Any]] | None = None,
    max_validation_jobs: int = 1,
    q71_capacity: int = 10,
    shadow_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> ContinuousResearchCycleResult:
    """Run one external-scheduler-friendly cycle; it never promotes live state."""
    root = Path(state_root)
    cycle_store = ContinuousCycleStore(root / "cycles")
    projection_store = ResearchProjectionStore(root / "projection")
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

    def failed(stage: str, exc: BaseException, *, identity: Mapping[str, Any]) -> ContinuousResearchCycleResult:
        stages[stage] = "FAILED"
        stamp = str(_value(frontier, "frontier_end", "") or started)
        result = ContinuousResearchCycleResult(
            continuous_cycle_id=_cycle_id({**identity, "failure_stage": stage,
                                           "failure": f"{type(exc).__name__}:{exc}"}),
            cycle_outcome="FAILED", frontier_snapshot_id=_value(frontier, "snapshot_id"),
            question_cycle_id=_value(question, "cycle_id"),
            bridge_run_id=_value(bridge, "bridge_run_id"),
            q71_agenda_id=q71.get("agenda_id"), q71_queue_id=q71.get("queue_id"),
            validation_queue_version=None, projection_version=None,
            predecessor_cycle_id=predecessor_id, started_at=started,
            completed_at=stamp, stage_statuses=dict(stages), failure_stage=stage,
            failure_reason=f"{type(exc).__name__}:{exc}", projection_path=projection_path,
        )
        cycle_store.save(result, successful=False)
        return result

    try:
        frontier = frontier_runner(**dict(frontier_kwargs or {}))
        stages["FRONTIER"] = str(_value(frontier, "status", "UNKNOWN"))
    except Exception as exc:
        return failed("FRONTIER", exc, identity={"predecessor": predecessor_id})
    started = str(_value(frontier, "frontier_end", "") or started)
    if _value(frontier, "status") == NO_NEW_GOVERNED_EVIDENCE:
        for stage in ("QUESTIONS", "SCIENTIFIC_STATE", "Q71_PLUS", "VALIDATION_QUEUE"):
            stages[stage] = "SKIPPED_NO_NEW_EVIDENCE"
        try:
            latest = projection_store.load_latest()
        except Exception as exc:
            return failed("PROJECTION", exc, identity={
                "snapshot": _value(frontier, "snapshot_id"), "predecessor": predecessor_id})
        stages["PROJECTION"] = "RETAINED" if latest else "NOT_AVAILABLE"
        material = {"outcome": "NO_NEW_RESEARCH_EVIDENCE",
                    "snapshot_id": _value(frontier, "snapshot_id"),
                    "frontier_id": _value(frontier, "frontier_id"),
                    "predecessor": predecessor_id}
        result = ContinuousResearchCycleResult(
            continuous_cycle_id=_cycle_id(material), cycle_outcome="NO_NEW_RESEARCH_EVIDENCE",
            frontier_snapshot_id=_value(frontier, "snapshot_id"), question_cycle_id=None,
            bridge_run_id=None, q71_agenda_id=None, q71_queue_id=None,
            validation_queue_version=None,
            projection_version=None if latest is None else latest.get("projection_version"),
            predecessor_cycle_id=predecessor_id, started_at=started, completed_at=started,
            stage_statuses=stages,
            projection_path=None if latest is None else str(projection_store.latest_path),
        )
        cycle_store.save(result, successful=True)
        return result
    if _value(frontier, "status") != SNAPSHOT_READY or _value(frontier, "verification_status") != "VERIFIED":
        return failed("FRONTIER", ContinuousResearchLoopError(
            "FRONTIER_DID_NOT_PRODUCE_VERIFIED_SNAPSHOT:" + str(_value(frontier, "failure_reason"))),
            identity={"frontier": _value(frontier, "frontier_id")})

    try:
        question = question_runner(frontier, **dict(question_kwargs or {}))
        if _value(question, "total_questions") != 70 or str(_value(question, "cycle_status")) not in {
            "COMPLETED", "COMPLETED_WITH_QUESTION_FAILURES",
        }:
            raise ContinuousResearchLoopError("CANONICAL_70_ACCOUNTING_INVALID")
        stages["QUESTIONS"] = str(_value(question, "cycle_status"))
    except Exception as exc:
        return failed("QUESTIONS", exc, identity={"snapshot": _value(frontier, "snapshot_id")})
    try:
        bridge = bridge_runner(question, **dict(bridge_kwargs or {}))
        if str(_value(bridge, "status")) not in {
            "COMPLETED", "COMPLETED_WITH_REVIEW_REQUIRED",
            "COMPLETED_WITH_ITEM_FAILURES", "NO_SCIENTIFIC_STATE_CHANGE",
        }:
            raise ContinuousResearchLoopError("SCIENTIFIC_STATE_BRIDGE_NOT_COMPLETE")
        stages["SCIENTIFIC_STATE"] = str(_value(bridge, "status"))
    except Exception as exc:
        return failed("SCIENTIFIC_STATE", exc, identity={"question_cycle": _value(question, "cycle_id")})

    try:
        qkwargs = dict(q71_kwargs or {})
        qkwargs.setdefault("snapshot_id", str(_value(frontier, "snapshot_id")))
        qkwargs.setdefault("capacity", q71_capacity)
        qkwargs.setdefault("state_path", root / "q71_state.json")
        q71 = q71_runner(**qkwargs)
        stages["Q71_PLUS"] = str(q71.get("status", "COMPLETED"))
    except Exception as exc:
        q71 = {"status": "FAILED_OPTIONAL", "failure_reason": f"{type(exc).__name__}:{exc}",
               "generated_questions": [], "queue": []}
        stages["Q71_PLUS"] = "FAILED_OPTIONAL"

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
    except Exception as exc:
        return failed("VALIDATION_QUEUE", exc, identity={"bridge": _value(bridge, "bridge_run_id")})

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
        cycle_id = _cycle_id(cycle_material)
        projection = build_unified_research_projection(
            continuous_cycle_id=cycle_id, frontier=frontier,
            question_projection=question_projection, bridge=bridge,
            scientific_store=scientific_store, optimisation_registry=registry,
            validation_store=validation_store, q71=q71,
            shadow_evidence=shadow_evidence, predecessor_projection=previous_projection,
        )
        projection_path = str(projection_store.save(projection))
        stages["PROJECTION"] = "COMPLETED"
    except Exception as exc:
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
    )
    try:
        cycle_store.save(result, successful=True)
    except Exception as exc:
        raise ContinuousResearchLoopError("CHECKPOINT_PERSISTENCE_FAILED") from exc
    return result


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


__all__ = ["ContinuousResearchLoopError", "run_continuous_research_cycle"]
