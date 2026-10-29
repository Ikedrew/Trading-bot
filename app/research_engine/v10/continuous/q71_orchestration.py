"""Bounded orchestration over the existing generated-research/agenda authorities."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
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


class Q71OrchestrationError(RuntimeError):
    pass


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": Q71_STATE_SCHEMA, "signals": {}, "question_states": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Q71OrchestrationError("Q71_STATE_UNREADABLE") from exc
    if value.get("schema") != Q71_STATE_SCHEMA or not isinstance(value.get("signals"), dict):
        raise Q71OrchestrationError("Q71_STATE_INVALID")
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
    terminal = {"RETIRED", "SUPERSEDED", "INVALID"}
    if lifecycle in terminal:
        state, reasons = OpportunityState.REFUSED, (lifecycle + "_GENERATED_RESEARCH",)
    elif not specification.get("snapshot_bound_runner"):
        state, reasons = OpportunityState.WAITING_DATA, ("SNAPSHOT_BOUND_RUNNER_UNAVAILABLE",)
    elif specification.get("evidence_available") is not True:
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


def run_q71_orchestration(
    *, snapshot_id: str, capacity: int = 10,
    coverage_store: ResearchCoverageStore | None = None,
    generated_store: GeneratedResearchStore | None = None,
    agenda_store: ResearchAgendaStore | None = None,
    state_path: Path | str = DEFAULT_Q71_STATE_PATH,
) -> dict[str, Any]:
    """Admit bounded blind-spot proposals and prioritise existing GEN records.

    A coverage signal is deliberately not converted into a GEN identity here:
    existing curiosity eligibility/freeze governance must do that first.
    """
    path = Path(state_path)
    state = _load_state(path)
    coverage = coverage_store or ResearchCoverageStore()
    generated = generated_store or GeneratedResearchStore()
    agendas = agenda_store or ResearchAgendaStore()
    new_signal_ids: list[str] = []
    snapshots = coverage.snapshots()
    if snapshots:
        latest = max(snapshots, key=lambda item: (
            item.observed_at, item.coverage_snapshot_identity))
        signals = admit_coverage_curiosity(latest, existing_signal_identities=state["signals"])
        for signal in signals[:capacity]:
            state["signals"][signal.signal_identity] = {
                **signal.to_dict(), "status": "PROPOSED",
                "execution_status": "GOVERNED_CURIOSITY_FREEZE_REQUIRED",
            }
            new_signal_ids.append(signal.signal_identity)

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
        "question": record.target_ref,
        "why_generated": record.trigger_ref,
        "source_opportunity": record.trigger_ref,
        "evidence_availability": record.specification.get("evidence_available"),
        "status": ("QUEUED" if record.generated_research_id in queued_ids else
                   state.get("question_states", {}).get(
                       record.generated_research_id, {}).get("status", "PROPOSED")),
        "priority": next((row["position"] for row in queue_rows
                          if row["generated_question_id"] == record.generated_research_id), None),
        "sample_readiness": record.specification.get("sample_readiness"),
        "last_evaluated_snapshot": None,
        "source_lineage": list(record.parent_refs),
        "linked_findings": [], "linked_hypotheses": [], "linked_candidates": [],
        "retirement_reason": state.get("question_states", {}).get(
            record.generated_research_id, {}).get("reason"),
    } for record in generated.all()]
    generated_rows.extend({
        "generated_question_id": signal_id,
        "question": row.get("target_ref"),
        "why_generated": row.get("reason_code"),
        "source_opportunity": row.get("source_ref"),
        "status": "PROPOSED",
        "execution_status": row.get("execution_status"),
        "source_lineage": [row.get("source_identity")],
    } for signal_id, row in sorted(state["signals"].items()))
    return {
        "status": "COMPLETED", "agenda_id": agenda_id, "queue_id": queue_id,
        "new_question_ids": new_signal_ids, "retired_question_ids": [],
        "generated_questions": generated_rows, "queue": queue_rows,
        "proposal_count": len(state["signals"]), "queue_capacity": capacity,
        "execution_mode": "QUEUE_ONLY_UNLESS_SNAPSHOT_BOUND_RUNNER_REGISTERED",
    }


__all__ = ["Q71OrchestrationError", "run_q71_orchestration"]
