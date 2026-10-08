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
    reentered = old_status in {
        "WAITING_FOR_DATA", "MISSING_EVALUATOR", "RETIRED",
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
    if not history or history[-1] != evidence_event:
        history.append(evidence_event)
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
    }


def run_q71_orchestration(
    *, snapshot_id: str, capacity: int = 10,
    coverage_store: ResearchCoverageStore | None = None,
    generated_store: GeneratedResearchStore | None = None,
    agenda_store: ResearchAgendaStore | None = None,
    state_path: Path | str = DEFAULT_Q71_STATE_PATH,
    evaluator_registry: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Generate, govern, persist and prioritise Q71+ research questions."""
    if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 100:
        raise Q71OrchestrationError("Q71_CAPACITY_OUT_OF_RANGE")
    path = Path(state_path)
    state = _load_state(path)
    # Stores implement ``__len__``; an injected empty store is therefore falsey
    # and must not be replaced with the production default path.
    coverage = coverage_store if coverage_store is not None else ResearchCoverageStore()
    generated = generated_store if generated_store is not None else GeneratedResearchStore()
    agendas = agenda_store if agenda_store is not None else ResearchAgendaStore()
    evaluators = dict(evaluator_registry or {})
    new_question_ids: list[str] = []
    retired_question_ids: list[str] = []
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
    generated_rows = [{
        "generated_question_id": record.generated_research_id,
        "question": state.get("question_states", {}).get(
            record.generated_research_id, {}).get("question", record.target_ref),
        "why_generated": record.trigger_ref,
        "source_opportunity": record.trigger_ref,
        "evidence_availability": state.get("question_states", {}).get(
            record.generated_research_id, {}).get(
                "evidence_available", record.specification.get("evidence_available")),
        "status": ("QUEUED" if record.generated_research_id in queued_ids else
                   state.get("question_states", {}).get(
                       record.generated_research_id, {}).get("status", "PROPOSED")),
        "priority": next((row["position"] for row in queue_rows
                          if row["generated_question_id"] == record.generated_research_id), None),
        "sample_readiness": record.specification.get("sample_readiness"),
        "last_evaluated_snapshot": state.get("question_states", {}).get(
            record.generated_research_id, {}).get("last_evaluated_snapshot"),
        "evaluator": state.get("question_states", {}).get(
            record.generated_research_id, {}).get(
                "evaluator", record.specification.get("snapshot_bound_runner")),
        "evidence_requirements": state.get("question_states", {}).get(
            record.generated_research_id, {}).get(
                "evidence_requirements", record.specification.get("evidence_requirements", {})),
        "depends_on": state.get("question_states", {}).get(
            record.generated_research_id, {}).get(
                "depends_on", record.specification.get("depends_on", [])),
        "reentry_count": state.get("question_states", {}).get(
            record.generated_research_id, {}).get("reentry_count", 0),
        "source_lineage": list(record.parent_refs),
        "linked_findings": [], "linked_hypotheses": [], "linked_candidates": [],
        "retirement_reason": state.get("question_states", {}).get(
            record.generated_research_id, {}).get("reason"),
    } for record in generated.all()]
    return {
        "status": "COMPLETED", "agenda_id": agenda_id, "queue_id": queue_id,
        "new_question_ids": new_question_ids,
        "retired_question_ids": sorted(set(retired_question_ids)),
        "generated_questions": generated_rows, "queue": queue_rows,
        "proposal_count": len(state["signals"]), "queue_capacity": capacity,
        "execution_mode": "QUEUE_ONLY_UNLESS_SNAPSHOT_BOUND_RUNNER_REGISTERED",
    }


__all__ = ["Q71OrchestrationError", "run_q71_orchestration"]
