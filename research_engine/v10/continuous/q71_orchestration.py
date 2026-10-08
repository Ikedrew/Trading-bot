"""Autonomous, bounded Q71+ generation over governed research coverage.

Question identity and execution eligibility are deliberately separate.  A
governed unresolved coverage cell may create a deterministic ``GEN-*`` question
while it is still waiting for evidence or for an evaluator; only READY questions
may enter the bounded agenda queue.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.governed_scientific_result import (
    SCIENTIFIC_RESULT_SCHEMA,
)

from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchKind, GeneratedResearchProposal,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.research_agenda import ResearchAgenda
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.lifecycle.research_coverage import admit_coverage_curiosity
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore
from research_engine.lifecycle.research_opportunity import (
    ConfirmationRequirement, DataAvailability, EvidentialInsufficiency,
    ExplanationDiscrimination, InformationValue, ObservationRequirement,
    OpportunityState, OpportunitySubjectKind, ProspectiveWait, QuestionResolution,
    ResearchAnswerability, ResearchCost, ResearchOpportunity,
)
from research_engine.v10.continuous.generated_question_lifecycle import (
    REENTRY_EVIDENCE_ARRIVED, REENTRY_EVIDENCE_CHANGED,
    REENTRY_EVALUATOR_REGISTERED, SUPERSEDED_BY_NEWER_GENERATION,
    effective_status, generation_to_lifecycle,
)
from research_engine.lifecycle.research_priority import POLICY_LEXICOGRAPHIC_V1
from research_engine.lifecycle.research_queue import ResearchQueue


Q71_STATE_SCHEMA = "continuous_q71_orchestration_v1"
DEFAULT_Q71_STATE_PATH = Path("data/research/continuous/q71_state.json")
TERMINAL_STATUSES = frozenset({"RETIRED", "SUPERSEDED", "INVALID"})


class Q71OrchestrationError(RuntimeError):
    pass


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": Q71_STATE_SCHEMA, "signals": {}, "question_states": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Q71OrchestrationError("Q71_STATE_UNREADABLE") from exc
    if (value.get("schema") != Q71_STATE_SCHEMA
            or not isinstance(value.get("signals"), dict)
            or not isinstance(value.get("question_states", {}), dict)):
        raise Q71OrchestrationError("Q71_STATE_INVALID")
    value.setdefault("question_states", {})
    return value


def _save_state(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise Q71OrchestrationError("Q71_STATE_PERSISTENCE_FAILED") from exc

def load_q71_state(path: Path | str = DEFAULT_Q71_STATE_PATH) -> dict[str, Any]:
    """Public, fail-closed reader shared by the orchestration and the worker."""
    return _load_state(Path(path))


def save_q71_state(path: Path | str, value: Mapping[str, Any]) -> None:
    """Public atomic writer shared by the orchestration and the worker."""
    _save_state(Path(path), value)




def _opportunity(record: Any, boundary: str, state_row: Mapping[str, Any]) -> ResearchOpportunity:
    specification = record.specification
    lifecycle = str(state_row.get("status") or specification.get("status") or "PROPOSED")
    if lifecycle in TERMINAL_STATUSES:
        state, reasons = OpportunityState.REFUSED, (lifecycle + "_GENERATED_RESEARCH",)
    elif lifecycle == "WAITING_FOR_DATA":
        state, reasons = OpportunityState.WAITING_DATA, ("EVIDENCE_UNAVAILABLE",)
    elif lifecycle == "MISSING_EVALUATOR":
        state, reasons = OpportunityState.BLOCKED, ("SNAPSHOT_BOUND_EVALUATOR_UNAVAILABLE",)
    elif lifecycle == "ACTIVE":
        state, reasons = OpportunityState.DEFERRED, ("RESEARCH_ALREADY_ACTIVE",)
    elif not (state_row.get("evaluator") or specification.get("snapshot_bound_runner")):
        state, reasons = OpportunityState.BLOCKED, ("SNAPSHOT_BOUND_EVALUATOR_UNAVAILABLE",)
    elif state_row.get("evidence_available", specification.get("evidence_available")) is not True:
        state, reasons = OpportunityState.WAITING_DATA, ("EVIDENCE_UNAVAILABLE",)
    else:
        state, reasons = OpportunityState.READY, ()
    info_raw = specification.get("information_value") or {}
    cost_raw = specification.get("research_cost") or {}
    information = InformationValue.create(
        question_state=info_raw.get("question_state", QuestionResolution.UNRESOLVED.value),
        evidential_insufficiency=info_raw.get("evidential_insufficiency", EvidentialInsufficiency.HIGH.value),
        explanation_discrimination=info_raw.get("explanation_discrimination", ExplanationDiscrimination.SINGLE_HYPOTHESIS_ONLY.value),
        answerability=info_raw.get("answerability", ResearchAnswerability.NOT_YET_DETERMINABLE.value),
        evidence_reference=f"generated_research:{record.generated_research_id}",
    )
    cost = ResearchCost.create(
        data_availability=cost_raw.get("data_availability", DataAvailability.UNKNOWN.value),
        additional_observations=cost_raw.get("additional_observations", ObservationRequirement.UNKNOWN.value),
        prospective_waiting=cost_raw.get("prospective_waiting", ProspectiveWait.UNKNOWN.value),
        confirmation_requirement=cost_raw.get("confirmation_requirement", ConfirmationRequirement.UNKNOWN.value),
        alternative_count=cost_raw.get("alternative_count"),
        dependency_depth=cost_raw.get("dependency_depth"),
    )
    return ResearchOpportunity.create(
        subject_kind=OpportunitySubjectKind.GENERATED_RESEARCH,
        subject_ref=record.generated_research_id, state=state,
        information=information, cost=cost, evidence_boundary=boundary,
        reason_codes=reasons, label=record.target_ref,
        provenance={"trigger_ref": record.trigger_ref, "parent_refs": list(record.parent_refs)},
    )


def _question_text(cell: Any) -> str:
    return (
        "What unresolved behaviour exists for governed research cell "
        f"{cell.cell_identity} ({cell.subject_identity}, {cell.population_identity}, "
        f"{cell.horizon}, {cell.evidence_class})?"
    )


def _coverage_proposal(signal: Any, cell: Any) -> GeneratedResearchProposal:
    """Create stable scientific identity from a governed cell, not an evidence epoch."""
    specification = {
        "kind": "coverage_gap_question",
        "definition_version": 1,
        "observation_cell": cell.semantic_material(),
        "evidence_requirements": {
            "population_identity": cell.population_identity,
            "evidence_class": cell.evidence_class,
            "horizon": cell.horizon,
            "dimension_identities": list(cell.dimension_identities),
            "interaction_identity": cell.interaction_identity or None,
            "slice_identity": cell.slice_identity or None,
        },
        "question_type": "COVERAGE_GAP",
        # Explicitly unknown semantics stay unknown; nothing is invented.
        "evidence_datasets": [],
        "required_fields": [],
        "target_population": None,
        # Absent evaluator_key means the governed registry decides.
        "evaluator_key": None,
        "evaluator_capability_class": None,
        "metric_family": "UNKNOWN",
        "intervention_class": "UNKNOWN",
        "creation_reason": "GOVERNED_COVERAGE_BLIND_SPOT",
        "re_entry_trigger": "COVERAGE_EVIDENCE_OR_EVALUATOR_CHANGE",
        "supersession_key": "coverage_cell:" + cell.cell_identity,
        "generation_version": 1,
        "expected_scientific_result_schema": SCIENTIFIC_RESULT_SCHEMA,

        "depends_on": [],
    }
    return GeneratedResearchProposal(
        research_kind=GeneratedResearchKind.EXPANSION,
        # Stable across later evidence snapshots so evidence arrival re-enters
        # the same question rather than minting a descendant every cycle.
        trigger_ref=f"coverage_cell:{cell.cell_identity}",
        target_kind="coverage_cell",
        target_ref=cell.cell_identity,
        specification=specification,
        dimension_ref=(cell.interaction_identity or (
            cell.dimension_identities[0] if cell.dimension_identities else None)),
        parent_refs=(),
    )


def _resolve_evaluator(
    question_id: str, cell: Any, specification: Mapping[str, Any],
    evaluator_registry: Mapping[str, str],
) -> str | None:
    embedded = specification.get("snapshot_bound_runner")
    if isinstance(embedded, str) and embedded.strip():
        return embedded
    for key in (question_id, cell.cell_identity, cell.evidence_class):
        value = evaluator_registry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _coverage_status(row: Any, evaluator: str | None) -> tuple[str, str | None]:
    evidence = row.evidence
    if evidence.conclusion_refs or evidence.applicable_memory_refs:
        return "RETIRED", "RESEARCH_GAP_RESOLVED"
    if evidence.active_research_refs:
        return "ACTIVE", "RESEARCH_ALREADY_ACTIVE"
    if row.state.value in {"BLOCKED", "NOT_OBSERVABLE"}:
        return "WAITING_FOR_DATA", row.state.value
    if not evidence.evidence_refs:
        return "WAITING_FOR_DATA", "EVIDENCE_UNAVAILABLE"
    if evaluator is None:
        return "MISSING_EVALUATOR", "SNAPSHOT_BOUND_EVALUATOR_UNAVAILABLE"
    return "READY", None


def _update_question_state(
    previous: Mapping[str, Any], *, question_id: str, cell: Any, coverage_row: Any,
    signal: Any, snapshot: Any, evaluator: str | None,
) -> dict[str, Any]:
    status, reason = _coverage_status(coverage_row, evaluator)
    old_status = str(previous.get("status") or "")
    status_reentered = old_status in {
        "WAITING_FOR_DATA", "MISSING_EVALUATOR", "RETIRED", "INVALID",
        "IMPLEMENTATION_BLOCKED",
    } and status in {"READY", "ACTIVE"}
    history = list(previous.get("evidence_history") or [])
    evidence_event = {
        "coverage_snapshot_id": snapshot.coverage_snapshot_identity,
        "inventory_id": snapshot.inventory.inventory_identity,
        "coverage_state": coverage_row.state.value,
        "evidence_refs": list(coverage_row.evidence.evidence_refs),
        "question_refs": list(coverage_row.evidence.question_refs),
        "research_attempt_refs": list(coverage_row.evidence.research_attempt_refs),
        "conclusion_refs": list(coverage_row.evidence.conclusion_refs),
    }
    evidence_changed = bool(history) and history[-1] != evidence_event
    if not history or evidence_changed:
        history.append(evidence_event)
    # A question re-enters when its own governed evidence changed, not when
    # unrelated evidence did: the evidence event is per observation cell.
    reentered = status_reentered or (
        evidence_changed and status in {"READY", "ACTIVE"})
    return {
        **dict(previous),
        "generated_question_id": question_id,
        "source_kind": "COVERAGE_GAP",
        "source_cell_id": cell.cell_identity,
        "source_signal_id": signal.signal_identity,
        "question": _question_text(cell),
        "status": status,
        "reason": reason,
        "evidence_available": bool(coverage_row.evidence.evidence_refs),
        "evaluator": evaluator,
        "evidence_requirements": {
            "population_identity": cell.population_identity,
            "evidence_class": cell.evidence_class,
            "horizon": cell.horizon,
            "dimension_identities": list(cell.dimension_identities),
            "interaction_identity": cell.interaction_identity or None,
            "slice_identity": cell.slice_identity or None,
        },
        "depends_on": [],
        "last_evaluated_snapshot": snapshot.coverage_snapshot_identity,
        "evidence_history": history,
        "reentry_count": int(previous.get("reentry_count") or 0) + int(reentered),
        "reentry_reason": (
            REENTRY_EVALUATOR_REGISTERED
            if status_reentered and old_status == "MISSING_EVALUATOR" and evaluator
            else REENTRY_EVIDENCE_ARRIVED
            if status_reentered and old_status == "WAITING_FOR_DATA"
            else REENTRY_EVIDENCE_CHANGED
            if reentered
            else previous.get("reentry_reason")),
        # Re-entry is an explicit governed authorisation, not an implicit reset.
        "reentry_authorized": bool(reentered),
        "generation_lifecycle_status": generation_to_lifecycle(status),
    }





def _apply_structural_supersession(
    records: Sequence[Any], question_states: dict[str, Any], *,
    snapshot_id: str, recorded_at: str,
) -> list[dict[str, Any]]:
    """Supersede older generated questions for the same governed cell.

    Supersession identity is structural only: the persisted ``supersession_key``
    (derived from the observation-cell identity) plus the declared
    ``generation_version``.  Free-text question wording is never compared, so
    semantic equivalence is never guessed.  The superseded question is retained
    historically with an explicit SUPERSEDED_BY lineage.
    """
    groups: dict[str, list[tuple[int, str]]] = {}
    for record in records:
        specification = record.specification
        key = str(specification.get("supersession_key") or "")
        if not key:
            continue
        state_row = question_states.get(record.generated_research_id) or {}
        if str(state_row.get("status") or "") in TERMINAL_STATUSES:
            continue
        groups.setdefault(key, []).append((
            int(specification.get("generation_version") or 0),
            record.generated_research_id,
        ))
    superseded: list[dict[str, Any]] = []
    for key, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # Deterministic winner: highest generation version, then lowest id.
        winner = sorted(members, key=lambda item: (-item[0], item[1]))[0][1]
        for version, question_id in sorted(members):
            if question_id == winner:
                continue
            previous = dict(question_states.get(question_id) or {})
            history = list(previous.get("supersession_history") or ())
            entry = {
                "status": "SUPERSEDED",
                "reason_code": SUPERSEDED_BY_NEWER_GENERATION,
                "superseded_by": winner,
                "supersession_key": key,
                "generation_version": version,
                "recorded_at": recorded_at,
            }
            if not history or history[-1] != entry:
                history.append(entry)
            question_states[question_id] = {
                **previous,
                "generated_question_id": question_id,
                "status": "SUPERSEDED",
                "reason": SUPERSEDED_BY_NEWER_GENERATION,
                "superseded_by": winner,
                "supersession_key": key,
                "supersession_history": history,
                "reentry_authorized": False,
                "last_evaluated_snapshot": snapshot_id,
            }
            superseded.append({
                "generated_question_id": question_id,
                "superseded_by": winner,
                "reason_code": SUPERSEDED_BY_NEWER_GENERATION,
            })
    return superseded


def run_q71_orchestration(
    *, snapshot_id: str, capacity: int = 10,
    coverage_store: ResearchCoverageStore | None = None,
    generated_store: GeneratedResearchStore | None = None,
    agenda_store: ResearchAgendaStore | None = None,
    state_path: Path | str = DEFAULT_Q71_STATE_PATH,
    evaluator_registry: Mapping[str, str] | Any | None = None,
    supersession_recorded_at: str = "",
) -> dict[str, Any]:
    """Generate, govern, persist and prioritise Q71+ research questions.

    ``evaluator_registry`` accepts either the governed
    ``GeneratedQuestionEvaluatorRegistry`` (authoritative, production) or a plain
    string mapping used by focused tests.  When a governed registry is supplied,
    its ``capability_index()`` is the single eligibility authority, so the
    question that generation declares executable is exactly the question the
    worker can execute.
    """
    if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 100:
        raise Q71OrchestrationError("Q71_CAPACITY_OUT_OF_RANGE")
    path = Path(state_path)
    state = _load_state(path)
    # Stores implement ``__len__``; an injected empty store is therefore falsey
    # and must not be replaced with the production default path.
    coverage = coverage_store if coverage_store is not None else ResearchCoverageStore()
    generated = generated_store if generated_store is not None else GeneratedResearchStore()
    agendas = agenda_store if agenda_store is not None else ResearchAgendaStore()
    if evaluator_registry is not None and hasattr(
            evaluator_registry, "capability_index"):
        evaluators = dict(evaluator_registry.capability_index())
    else:
        evaluators = dict(evaluator_registry or {})
    new_question_ids: list[str] = []
    retired_question_ids: list[str] = []
    superseded_questions: list[dict[str, Any]] = []
    snapshots = coverage.snapshots()
    if snapshots:
        latest = max(snapshots, key=lambda item: (
            item.observed_at, item.coverage_snapshot_identity))
        current_signals = admit_coverage_curiosity(latest)
        unseen = [signal for signal in current_signals
                  if signal.signal_identity not in state["signals"]]
        # Creation is bounded independently of how many historical questions
        # need a lifecycle refresh.
        admitted = unseen[:capacity]
        for signal in admitted:
            state["signals"][signal.signal_identity] = {
                **signal.to_dict(), "status": "PROPOSED",
                "execution_status": "QUESTION_IDENTITY_ADMITTED",
            }
        by_cell = {row.cell_identity: row for row in latest.classifications}
        by_signal_cell = {signal.target_ref: signal for signal in current_signals}
        for signal in admitted:
            cell = latest.observation_space.cell(signal.target_ref)
            if cell is None:
                state["signals"][signal.signal_identity]["status"] = "INVALID"
                state["signals"][signal.signal_identity]["reason"] = "COVERAGE_CELL_MISSING"
                continue
            record = generated.register(_coverage_proposal(signal, cell), provenance={
                "origin": "AUTONOMOUS_COVERAGE_GAP",
                "source_signal_id": signal.signal_identity,
            })
            if record.generated_research_id not in state["question_states"]:
                new_question_ids.append(record.generated_research_id)

        # Refresh every already-minted coverage question from current governed
        # evidence.  This is the re-entry/retirement seam.
        for record in generated.all():
            if record.specification.get("kind") != "coverage_gap_question":
                continue
            cell = latest.observation_space.cell(record.target_ref)
            previous = state["question_states"].get(record.generated_research_id, {})
            if cell is None or record.target_ref not in by_cell:
                state["question_states"][record.generated_research_id] = {
                    **previous, "generated_question_id": record.generated_research_id,
                    "source_kind": "COVERAGE_GAP", "source_cell_id": record.target_ref,
                    "status": "INVALID", "reason": "OBSERVATION_CELL_REMOVED",
                    "last_evaluated_snapshot": latest.coverage_snapshot_identity,
                }
                continue
            row = by_cell[record.target_ref]
            signal = by_signal_cell.get(record.target_ref)
            if signal is None:
                # A resolved cell no longer emits curiosity; retain its original
                # source signal and retire it from the governed coverage state.
                source_signal = previous.get("source_signal_id")
                status = "RETIRED" if row.state.value == "OBSERVED_AND_RESEARCHED" else "INVALID"
                reason = "RESEARCH_GAP_RESOLVED" if status == "RETIRED" else "CURIOSITY_NO_LONGER_ADMISSIBLE"
                history = list(previous.get("evidence_history") or [])
                evidence_event = {
                    "coverage_snapshot_id": latest.coverage_snapshot_identity,
                    "inventory_id": latest.inventory.inventory_identity,
                    "coverage_state": row.state.value,
                    "evidence_refs": list(row.evidence.evidence_refs),
                    "question_refs": list(row.evidence.question_refs),
                    "research_attempt_refs": list(row.evidence.research_attempt_refs),
                    "conclusion_refs": list(row.evidence.conclusion_refs),
                }
                if not history or history[-1] != evidence_event:
                    history.append(evidence_event)
                state["question_states"][record.generated_research_id] = {
                    **previous, "generated_question_id": record.generated_research_id,
                    "source_kind": "COVERAGE_GAP", "source_cell_id": record.target_ref,
                    "source_signal_id": source_signal, "status": status, "reason": reason,
                    "evidence_available": bool(row.evidence.evidence_refs),
                    "evidence_history": history,
                    "last_evaluated_snapshot": latest.coverage_snapshot_identity,
                }
                if status == "RETIRED" and previous.get("status") != "RETIRED":
                    retired_question_ids.append(record.generated_research_id)
                continue
            evaluator = _resolve_evaluator(
                record.generated_research_id, cell, record.specification, evaluators)
            updated = _update_question_state(
                previous, question_id=record.generated_research_id, cell=cell,
                coverage_row=row, signal=signal, snapshot=latest, evaluator=evaluator)
            state["question_states"][record.generated_research_id] = updated
            if updated["status"] == "RETIRED" and previous.get("status") != "RETIRED":
                retired_question_ids.append(record.generated_research_id)

        # Structural supersession: a newer governed generation for the same
        # observation cell supersedes an older one.  Semantic equivalence is
        # never inferred from free text.
        superseded_questions = _apply_structural_supersession(
            generated.all(), state["question_states"],
            snapshot_id=snapshot_id,
            recorded_at=supersession_recorded_at or str(
                getattr(latest, "observed_at", "") or ""))


    opportunities = tuple(
        _opportunity(record, snapshot_id, state.get("question_states", {}).get(
            record.generated_research_id, {}))
        for record in generated.all()
    )
    agenda_id = queue_id = None
    queue_rows: list[dict[str, Any]] = []
    if opportunities:
        agenda = ResearchAgenda.build(POLICY_LEXICOGRAPHIC_V1, opportunities,
                                      evidence_boundary=snapshot_id)
        agendas.register_agenda(agenda)
        queue = ResearchQueue.build(agenda, capacity=capacity)
        agendas.register_queue(queue)
        agenda_id, queue_id = agenda.agenda_identity, queue.queue_identity
        by_opportunity = {item.opportunity_identity: item for item in opportunities}
        for entry in queue.entries:
            item = by_opportunity[entry.opportunity_identity]
            queue_rows.append({"position": entry.position,
                               "opportunity_id": entry.opportunity_identity,
                               "generated_question_id": item.subject_ref,
                               "information_band": entry.information_band,
                               "cost_band": entry.cost_band,
                               "status": "QUEUED"})
    _save_state(path, state)
    queued_ids = {row["generated_question_id"] for row in queue_rows}
    generated_rows = []
    for record in generated.all():
        question_id = record.generated_research_id
        state_row = state.get("question_states", {}).get(question_id, {})
        specification = record.specification
        generation_status = (
            "QUEUED" if question_id in queued_ids
            else state_row.get("status", "PROPOSED"))
        execution_status = state_row.get("execution_status")
        generated_rows.append({
            "generated_question_id": question_id,
            "question": state_row.get("question", record.target_ref),
            "why_generated": record.trigger_ref,
            "source_opportunity": record.trigger_ref,
            "evidence_availability": state_row.get(
                "evidence_available", specification.get("evidence_available")),
            "status": generation_status,
            # The lifecycle status is the single normalised derivation shared
            # with the worker, the projection and the Lab.
            "lifecycle_status": effective_status(
                generation_status, execution_status),
            "priority": next((row["position"] for row in queue_rows
                              if row["generated_question_id"] == question_id), None),
            "sample_readiness": specification.get("sample_readiness"),
            "last_evaluated_snapshot": state_row.get("last_evaluated_snapshot"),
            "last_evidence_event": (
                (state_row.get("evidence_history") or [{}])[-1]),
            "evaluator": state_row.get(
                "evaluator", specification.get("evaluator_key")),
            "evaluator_key": state_row.get(
                "evaluator_key", specification.get("evaluator_key")),
            "evaluator_capability_class": specification.get(
                "evaluator_capability_class"),
            "evidence_requirements": state_row.get(
                "evidence_requirements", specification.get("evidence_requirements", {})),
            "evidence_datasets": list(
                specification.get("evidence_datasets") or ()),
            "depends_on": state_row.get(
                "depends_on", specification.get("depends_on", [])),
            "reentry_count": state_row.get("reentry_count", 0),
            "reentry_reason": state_row.get("reentry_reason"),
            "reentry_authorized": bool(state_row.get("reentry_authorized")),
            "question_type": specification.get("question_type"),
            "metric_family": specification.get("metric_family"),
            "intervention_class": specification.get("intervention_class"),
            "generation_version": specification.get("generation_version"),
            "supersession_key": specification.get("supersession_key"),
            "superseded_by": state_row.get("superseded_by"),
            "supersession_reason": state_row.get("supersession_reason"),
            "supersession_history": list(state_row.get("supersession_history") or ()),
            "expected_scientific_result_schema": specification.get(
                "expected_scientific_result_schema"),
            "source_lineage": list(record.parent_refs),
            "linked_findings": [], "linked_hypotheses": [], "linked_candidates": [],
            "retirement_reason": state_row.get("reason"),
        })
    return {
        "status": "COMPLETED", "agenda_id": agenda_id, "queue_id": queue_id,
        "new_question_ids": new_question_ids,
        "retired_question_ids": sorted(set(retired_question_ids)),
        "superseded_questions": superseded_questions,
        "generated_questions": generated_rows, "queue": queue_rows,
        "proposal_count": len(state["signals"]), "queue_capacity": capacity,
        "execution_mode": "QUEUE_ONLY_UNLESS_SNAPSHOT_BOUND_RUNNER_REGISTERED",
    }


__all__ = [
    "DEFAULT_Q71_STATE_PATH",
    "Q71OrchestrationError",
    "Q71_STATE_SCHEMA",
    "load_q71_state",
    "run_q71_orchestration",
    "save_q71_state",
]
