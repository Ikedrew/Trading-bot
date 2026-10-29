"""Wave 6.3B governed evolution."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class EvolutionBindingError(ValueError):
    pass

class EvolutionConflictError(ValueError):
    pass

def _non_empty(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())

def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise EvolutionBindingError("corrupt") from exc
    return rows
def _norm(v: Any) -> str:
    return str(v or "").strip()

def derive_transition_candidate_id(app_id, *, application_path,
                                  operations_dir=None):
    from research_engine.lifecycle.candidate_impact_history import (
        reconstruct_verified_transition)
    from research_engine.control_plane.application_ledger import (
        ApplicationLedger)
    ops = Path(operations_dir) if operations_dir else Path(
        application_path).parent / "operations"
    tr = reconstruct_verified_transition(
        app_id, application_path=application_path,
        operations_dir=ops)
    rows = [r for r in ApplicationLedger(
        application_path).list_all() if r.application_id == app_id]
    if not rows:
        raise EvolutionBindingError("no app history")
    deps = {r.candidate_id for r in rows if _non_empty(r.candidate_id)}
    if len(deps) != 1:
        raise EvolutionBindingError("ambiguous deployer")
    dep = next(iter(deps))
    bases = {r.baseline_id for r in rows if _non_empty(r.baseline_id)}
    if len(bases) != 1:
        raise EvolutionBindingError("ambiguous application baseline")
    if bases != {tr.from_baseline_id}:
        raise EvolutionBindingError("deployer baseline/transition conflict")
    return dep, tr.operation_id

def resolve_historical_outcome(cid, *, registry_dir, evaluations_dir):
    from research_engine.lifecycle.candidate_reconsideration import (
        HistoricalCandidateOutcome)
    from research_engine.v10.candidates.candidate_registry import (
        CandidateRegistry)
    cand = CandidateRegistry(storage_dir=str(registry_dir)).get(cid)
    if cand is None:
        raise EvolutionBindingError("unknown candidate")
    rows = _read_jsonl(Path(evaluations_dir) / f"{cid}.jsonl")
    decs: list[str] = []
    out = ""
    for row in rows:
        d = _norm(row.get("decision"))
        if d and d not in ("VALIDATED", "REJECTED", "INCONCLUSIVE"):
            raise EvolutionBindingError("bad outcome")
        if d and out and d != out:
            raise EvolutionBindingError("conflict outcome")
        if d:
            out = d
            decs.append(d)
    if not decs:
        raise EvolutionBindingError("missing eval history")
    return (HistoricalCandidateOutcome(
        candidate_status=cand.status,
        evaluation_decisions=tuple(decs)), cand.status, out)


def resolve_historical_human(cid, *, decisions_dir):
    from research_engine.lifecycle.candidate_reconsideration import (
        HistoricalHumanDecision)
    from research_engine.v10.candidates.candidate_decision import (
        CandidateDecisionStore)
    dec = CandidateDecisionStore(
        decisions_dir=str(decisions_dir)).get_decision(cid)
    if dec is None:
        return HistoricalHumanDecision(present=False), "NONE"
    if dec.outcome != "COMPLETED" or dec.decision not in (
            "ACCEPT", "REJECT"):
        return (HistoricalHumanDecision(
            present=True, decision="REJECT",
            outcome="COMPLETED"), "INDETERMINATE")
    return (HistoricalHumanDecision(
        present=True, decision=dec.decision,
        outcome="COMPLETED"), dec.decision)

def resolve_impact_for_transition(cid, app_id, *, impact_dir):
    from research_engine.lifecycle.candidate_impact_history import (
        CandidateImpactHistoryStore)
    recs = [r for r in CandidateImpactHistoryStore(
        impact_dir).list_all() if (r.candidate_id == cid
        and r.application_id == app_id)]
    if len(recs) != 1:
        raise EvolutionBindingError("impact not unique")
    return recs[0]

def resolve_continuity_for_impact(impact_record, *, continuity_dir):
    from research_engine.lifecycle.candidate_evidence_continuity import (
        CandidateEvidenceContinuityStore)
    recs = CandidateEvidenceContinuityStore(
        continuity_dir).list_for_impact(impact_record.impact_id)
    if len(recs) != 1:
        raise EvolutionBindingError("continuity not unique")
    cont = recs[0]
    if (cont.candidate_id != impact_record.candidate_id
            or cont.historical_baseline_id
            != impact_record.candidate_baseline_id
            or cont.target_baseline_id != impact_record.to_baseline_id
            or cont.impact_id != impact_record.impact_id
            or cont.candidate_treatment_id
            != impact_record.candidate_treatment_id):
        raise EvolutionBindingError("continuity mismatch")
    return cont


def evolve_candidate_reconsideration(
        cid, app_id, *, registry_dir, evaluations_dir,
        decisions_dir, application_path, operations_dir,
        impact_dir, continuity_dir, reconsideration_dir):
    from research_engine.lifecycle.candidate_reconsideration import (
        decide_candidate_reconsideration)
    from research_engine.lifecycle.candidate_impact_history import (
        reconstruct_verified_transition,
        resolve_candidate_historical_identity)
    from research_engine.lifecycle.candidate_reconsideration_history import (
        CandidateReconsiderationHistoryStore,
        CandidateReconsiderationRecord)
    tr = reconstruct_verified_transition(
        app_id, application_path=application_path,
        operations_dir=operations_dir)
    ident = resolve_candidate_historical_identity(
        cid, registry_dir=registry_dir, evaluations_dir=evaluations_dir)
    dep, op_id = derive_transition_candidate_id(
        app_id, application_path=application_path,
        operations_dir=operations_dir)
    impact = resolve_impact_for_transition(
        cid, app_id, impact_dir=impact_dir)
    if (impact.from_baseline_id != tr.from_baseline_id
            or impact.to_baseline_id != tr.to_baseline_id):
        raise EvolutionBindingError("impact/transition mismatch")
    cont = resolve_continuity_for_impact(
        impact, continuity_dir=continuity_dir)
    outcome, status, eval_out = resolve_historical_outcome(
        cid, registry_dir=registry_dir, evaluations_dir=evaluations_dir)
    human, human_label = resolve_historical_human(
        cid, decisions_dir=decisions_dir)
    decision = decide_candidate_reconsideration(
        impact_record=impact, continuity=cont,
        historical_outcome=outcome, human_decision=human,
        transition_candidate_id=dep)
    rec = CandidateReconsiderationRecord(
        reconsideration_id=decision.reconsideration_id,
        historical_candidate_id=cid,
        historical_baseline_id=decision.historical_baseline_id,
        historical_baseline_config_hash=(
            decision.historical_baseline_config_hash),
        target_baseline_id=decision.target_baseline_id,
        target_baseline_config_hash=decision.target_baseline_config_hash,
        impact_id=impact.impact_id, continuity_id=cont.continuity_id,
        candidate_treatment_id=ident.treatment_id,
        application_id=app_id, operation_id=op_id,
        transition_candidate_id=dep,
        historical_candidate_status=status,
        historical_evaluation_outcome=eval_out,
        historical_human_decision=human_label,
        reconsideration_status=decision.status.value,
        reason_codes=tuple(decision.reason_codes),
        fresh_evidence_required=bool(decision.fresh_evidence_required))
    CandidateReconsiderationHistoryStore(reconsideration_dir).append(rec)
    return rec
def create_governed_successor(
        rec_id, *, registry_dir, reconsideration_dir):
    from research_engine.lifecycle.candidate_reconsideration_history import (
        CandidateReconsiderationHistoryStore,
        compute_successor_candidate_id)
    from research_engine.v10.candidates.candidate_registry import (
        CandidateRegistry)
    from research_engine.v10.candidates.models import CandidateRecord
    store = CandidateReconsiderationHistoryStore(reconsideration_dir)
    rec = store.get(rec_id)
    if rec is None:
        raise EvolutionBindingError("unknown record")
    if rec.reconsideration_status != "ELIGIBLE_FOR_RECONSIDERATION":
        raise EvolutionBindingError("blocked status")
    sid = compute_successor_candidate_id(
        predecessor_candidate_id=rec.historical_candidate_id,
        target_baseline_id=rec.target_baseline_id,
        reconsideration_id=rec.reconsideration_id)
    reg = CandidateRegistry(storage_dir=str(registry_dir))
    if reg.get(rec.historical_candidate_id) is None:
        raise EvolutionBindingError("missing predecessor")
    ex = reg.get(sid)
    if ex is not None:
        if (ex.baseline_id != rec.target_baseline_id
                or (ex.change_definition or {}).get(
                    "baseline_config_hash")
                != rec.target_baseline_config_hash):
            raise EvolutionConflictError("conflict successor")
        return store.bind_successor(
            rec_id, successor_candidate_id=sid,
            successor_baseline_id=rec.target_baseline_id,
            successor_baseline_config_hash=(
                rec.target_baseline_config_hash))
    reg.create(CandidateRecord(
        candidate_id=sid, baseline_id=rec.target_baseline_id,
        change_definition={
            "baseline_config_hash": rec.target_baseline_config_hash,
            "evolution_predecessor": rec.historical_candidate_id,
            "evolution_reconsideration_id": rec.reconsideration_id},
        status="PROPOSED"))
    return store.bind_successor(
        rec_id, successor_candidate_id=sid,
        successor_baseline_id=rec.target_baseline_id,
        successor_baseline_config_hash=rec.target_baseline_config_hash)

@dataclass(frozen=True)
class CandidateEvolutionView:
    historical_candidate_id: str
    historical_baseline_id: str
    target_baseline_id: str
    historical_status: str
    historical_evaluation_outcome: str
    impact_classification: str
    continuity_state: str
    reconsideration_status: str
    reason_codes: tuple
    fresh_evidence_required: bool
    fresh_evidence_present: bool
    successor_candidate_id: str
    blocked_reason: str
    def to_dict(self):
        return {
            "historical_candidate_id": self.historical_candidate_id,
            "historical_baseline_id": self.historical_baseline_id,
            "target_baseline_id": self.target_baseline_id,
            "historical_status": self.historical_status,
            "historical_evaluation_outcome":
                self.historical_evaluation_outcome,
            "impact_classification": self.impact_classification,
            "continuity_state": self.continuity_state,
            "reconsideration_status": self.reconsideration_status,
            "reason_codes": list(self.reason_codes),
            "fresh_evidence_required": self.fresh_evidence_required,
            "fresh_evidence_present": self.fresh_evidence_present,
            "successor_candidate_id": self.successor_candidate_id,
            "blocked_reason": self.blocked_reason}

def get_evolution_view(
        cid, app_id, *, impact_dir, continuity_dir,
        reconsideration_dir):
    from research_engine.lifecycle.candidate_impact_history import (
        CandidateImpactHistoryStore)
    from research_engine.lifecycle.candidate_evidence_continuity import (
        CandidateEvidenceContinuityStore)
    from research_engine.lifecycle.candidate_reconsideration_history import (
        CandidateReconsiderationHistoryStore)
    impacts = [r for r in CandidateImpactHistoryStore(
        impact_dir).list_all() if (r.candidate_id == cid
        and r.application_id == app_id)]
    if len(impacts) != 1:
        raise EvolutionBindingError("impact not unique")
    impact = impacts[0]
    conts = CandidateEvidenceContinuityStore(
        continuity_dir).list_for_impact(impact.impact_id)
    if len(conts) != 1:
        raise EvolutionBindingError("continuity not unique")
    cont = conts[0]
    recs = [r for r in CandidateReconsiderationHistoryStore(
        reconsideration_dir).list_for_candidate(cid)
        if r.application_id == app_id]
    if len(recs) != 1:
        raise EvolutionBindingError("record not unique")
    rec = recs[0]
    if rec.impact_id != impact.impact_id:
        raise EvolutionBindingError("view impact mismatch")
    if rec.continuity_id != cont.continuity_id:
        raise EvolutionBindingError("view continuity mismatch")
    blocked = "" if rec.successor_candidate_id else ";".join(
        rec.reason_codes)
    state = (cont.state.value if hasattr(cont.state, "value")
             else str(cont.state))
    return CandidateEvolutionView(
        historical_candidate_id=cid,
        historical_baseline_id=rec.historical_baseline_id,
        target_baseline_id=rec.target_baseline_id,
        historical_status=rec.historical_candidate_status,
        historical_evaluation_outcome=rec.historical_evaluation_outcome,
        impact_classification=impact.classification,
        continuity_state=state,
        reconsideration_status=rec.reconsideration_status,
        reason_codes=tuple(rec.reason_codes),
        fresh_evidence_required=bool(rec.fresh_evidence_required),
        fresh_evidence_present=(
            bool(cont.fresh_evidence_present)
            if hasattr(cont, "fresh_evidence_present") else None),
        successor_candidate_id=rec.successor_candidate_id,
        blocked_reason=blocked)

