"""Wave 6.3B tests."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from tests.test_wave5_application_service import PersistentFake, env  # noqa: F401
from research_engine.lifecycle.candidate_evolution import (
    derive_transition_candidate_id,
    evolve_candidate_reconsideration,
    create_governed_successor,
    get_evolution_view,
    resolve_impact_for_transition,
    resolve_continuity_for_impact,
    EvolutionBindingError,
    EvolutionConflictError,
)
from research_engine.lifecycle.candidate_reconsideration import (
    decide_candidate_reconsideration,
    HistoricalCandidateOutcome,
    HistoricalHumanDecision,
)
from research_engine.lifecycle.candidate_reconsideration_history import (
    CandidateReconsiderationHistoryStore,
    ReconsiderationConflictError,
    compute_successor_candidate_id,
)
from research_engine.lifecycle.candidate_impact_history import (
    assess_candidate_impact,
    reconstruct_verified_transition,
)
from research_engine.lifecycle.candidate_evidence_eligibility import (
    HistoricalObservation,
    assess_evidence_eligibility,
)
from research_engine.lifecycle.candidate_evidence_continuity import (
    CandidateEvidenceContinuityStore,
    assess_candidate_evidence_continuity,
    EvidenceContinuityState,
)
from research_engine.lifecycle.treatment_provenance import canonical_spec
XA_SCOPE = {"symbols": ["EURUSD"], "patterns": ["TBC"]}
D_SCOPE = {"symbols": ["EURUSD"], "patterns": ["TBC"]}
XA_TID = "TRT-XA-HIST"
D_TID = "historical-treatment"

def _verified(env):
    env.approve()
    op = env.service().execute(env.app_id)
    assert op["phase"] == "COMPLETED"
    return op

def _paths(env, tmp_path):
    root = env.root
    return {
        "registry_dir": env.dirs["registry_dir"],
        "evaluations_dir": root / "evaluations",
        "decisions_dir": env.dirs["decisions_dir"],
        "application_path": root / "applications.jsonl",
        "operations_dir": root / "operations",
        "impact_dir": tmp_path / "impact",
        "continuity_dir": tmp_path / "continuity",
        "reconsideration_dir": tmp_path / "recon",
    }

def _mk_xa(env, tmp_path, cid="XA", eval_outcome="REJECTED",
           status="FAILED_VALIDATION", treatment_scope=None,
           treatment_id=None):
    scope = treatment_scope or dict(XA_SCOPE)
    tid = treatment_id or XA_TID
    spec = _spec(scope, tid)
    from research_engine.lifecycle.candidate_evaluator import (
        CandidateEvaluation)
    CandidateRegistry(env.dirs["registry_dir"]).create(CandidateRecord(
        candidate_id=cid, baseline_id="OLD",
        change_definition={"baseline_config_hash": "old-config"},
        status="PROPOSED"))
    for st in ("VALIDATING", status):
        CandidateRegistry(env.dirs["registry_dir"]).update_status(cid, st)
    ev = CandidateEvaluation(
        candidate_id=cid, evaluation_id=f"E-{cid}", treatment_id=tid,
        baseline_id="OLD", config_hash="old-config",
        decision=eval_outcome, confidence="HIGH", eligible_pairs=60,
        survives_outlier_removal=True)
    ev.treatment_spec = spec
    p = env.root / "evaluations" / f"{cid}.jsonl"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(ev.to_dict()) + "\n", encoding="utf-8")
    return spec

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord
def _spec(scope, tid, ctype="direction_inversion"):
    declared = {} if ctype == "direction_inversion" else {"stop_multiplier": 1.5}
    return canonical_spec({
        "change_type": ctype, "declared": declared,
        "scope": scope, "treatment_id": tid})


def _assess_xa(env, tmp_path, cid="XA", scope=None, symbol="EURUSD",
               with_decisions=True):
    P = _paths(env, tmp_path)
    _verified(env)
    impact = assess_candidate_impact(
        cid, env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"])
    tr = reconstruct_verified_transition(
        env.app_id, application_path=P["application_path"],
        operations_dir=P["operations_dir"])
    decs = []
    if with_decisions:
        obs = HistoricalObservation(
            candidate_id=cid, baseline_id="OLD", config_hash="old-config",
            symbol=symbol, pattern="TBC", treatment_id=XA_TID,
            correlation_id="corr-xa-1")
        decs.append(assess_evidence_eligibility(
            impact_record=impact, observation=obs,
            target_baseline_id=tr.to_baseline_id,
            target_baseline_config_hash=tr.to_baseline_config_hash))
    cont = assess_candidate_evidence_continuity(
        impact_record=impact, decisions=decs,
        continuity_dir=P["continuity_dir"])
    return P, impact, cont


def test_A_reconstructs_exact_transition(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    tr = reconstruct_verified_transition(
        env.app_id, application_path=P["application_path"],
        operations_dir=P["operations_dir"])
    assert tr.from_baseline_id == "OLD"
    assert tr.application_id == env.app_id
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert rec.historical_baseline_id == "OLD"
    assert rec.target_baseline_id == tr.to_baseline_id

def test_B_deployer_derived(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    dep, op_id = derive_transition_candidate_id(
        env.app_id, application_path=P["application_path"],
        operations_dir=P["operations_dir"])
    assert dep == "C1"
    assert op_id
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert rec.transition_candidate_id == "C1"

def test_C_matches_pure_63a(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    pure = decide_candidate_reconsideration(
        impact_record=impact, continuity=cont,
        historical_outcome=HistoricalCandidateOutcome(
            candidate_status="FAILED_VALIDATION",
            evaluation_decisions=("REJECTED",)),
        human_decision=HistoricalHumanDecision(present=False),
        transition_candidate_id="C1")
    assert rec.reconsideration_id == pure.reconsideration_id
    assert rec.reconsideration_status == "ELIGIBLE_FOR_RECONSIDERATION"

def test_D_persists_and_reloads(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    again = CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).get(rec.reconsideration_id)
    assert again is not None
    assert again.to_dict() == rec.to_dict()
    rec2 = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert rec2.to_dict() == rec.to_dict()
    assert len(CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).list_all()) == 1

def test_G_conflict_closed(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    bad = CandidateReconsiderationHistoryStore(P["reconsideration_dir"])
    import dataclasses
    tampered = dataclasses.replace(rec, reconsideration_status="NOT_ELIGIBLE")
    with pytest.raises(ReconsiderationConflictError):
        bad.append(tampered)

def test_H_corrupt_closed(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    path = CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).path
    path.write_text("{corrupt\n", encoding="utf-8")
    with pytest.raises(ValueError):
        CandidateReconsiderationHistoryStore(P["reconsideration_dir"])

def test_I_successor_single(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    before_apps = len(_read_apps(P["application_path"]))
    rec = evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    bound = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    reg = CandidateRegistry(P["registry_dir"])
    succ = reg.get(bound.successor_candidate_id)
    assert succ is not None
    assert succ.candidate_id != "XA"
    assert succ.baseline_id == rec.target_baseline_id
    assert succ.change_definition["baseline_config_hash"] == (
        rec.target_baseline_config_hash)
    pred = reg.get("XA")
    assert pred.baseline_id == "OLD"
    assert pred.status == "FAILED_VALIDATION"
    assert succ.validation_history == []
    again = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert again.successor_candidate_id == bound.successor_candidate_id
    assert len([c for c in reg.list_all()
                if c.candidate_id == bound.successor_candidate_id]) == 1
    assert len(_read_apps(P["application_path"])) == before_apps
    with pytest.raises(ReconsiderationConflictError):
        CandidateReconsiderationHistoryStore(
            P["reconsideration_dir"]).bind_successor(
                rec.reconsideration_id,
                successor_candidate_id="OTHER",
                successor_baseline_id=rec.target_baseline_id,
                successor_baseline_config_hash=(
                    rec.target_baseline_config_hash))

def _read_apps(path):
    if not path.is_file():
        return []
    return [line for line in path.read_text(
        encoding="utf-8").splitlines() if line.strip()]

    return canonical_spec({
        "change_type": ctype, "declared": declared,
        "scope": scope, "treatment_id": tid})

def _mk_record(env, tmp_path, impact, cont, dep, outcome, human):
    from research_engine.lifecycle.candidate_reconsideration import (
        decide_candidate_reconsideration as decide)
    from research_engine.lifecycle.candidate_reconsideration_history import (
        CandidateReconsiderationRecord as R)
    d = decide(
        impact_record=impact, continuity=cont,
        historical_outcome=outcome, human_decision=human,
        transition_candidate_id=dep)
    return R(
        reconsideration_id=d.reconsideration_id,
        historical_candidate_id="XA",
        historical_baseline_id=d.historical_baseline_id,
        historical_baseline_config_hash=d.historical_baseline_config_hash,
        target_baseline_id=d.target_baseline_id,
        target_baseline_config_hash=d.target_baseline_config_hash,
        impact_id=impact.impact_id, continuity_id=cont.continuity_id,
        candidate_treatment_id=XA_TID, application_id=env.app_id,
        operation_id="op-test", transition_candidate_id=dep,
        historical_candidate_status="FAILED_VALIDATION",
        historical_evaluation_outcome="REJECTED",
        historical_human_decision=(
            human.decision if human.present else "NONE"),
        reconsideration_status=d.status.value,
        reason_codes=tuple(d.reason_codes),
        fresh_evidence_required=bool(d.fresh_evidence_required))


def _run_evolve(env, tmp_path, P):
    return evolve_candidate_reconsideration(
        "XA", env.app_id, registry_dir=P["registry_dir"],
        evaluations_dir=P["evaluations_dir"],
        decisions_dir=P["decisions_dir"],
        application_path=P["application_path"],
        operations_dir=P["operations_dir"], impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])


def _no_other_candidates(registry_dir):
    return not [c for c in CandidateRegistry(registry_dir).list_all()
                if c.candidate_id not in ("XA", "C1")]


def test_V_fresh_required_no_successor(env, tmp_path):
    _mk_xa(env, tmp_path, treatment_scope={"symbols": ["EURUSD", "GBPUSD"],
                                           "patterns": ["TBC"]})
    P, impact, cont = _assess_xa(env, tmp_path)
    assert impact.classification == "PARTIALLY_AFFECTED"
    rec = _run_evolve(env, tmp_path, P)
    assert rec.reconsideration_status == "FRESH_EVIDENCE_REQUIRED"
    assert rec.fresh_evidence_required is True
    with pytest.raises(EvolutionBindingError):
        create_governed_successor(
            rec.reconsideration_id, registry_dir=P["registry_dir"],
            reconsideration_dir=P["reconsideration_dir"])
    assert _no_other_candidates(P["registry_dir"])


def test_W_not_eligible_no_successor(env, tmp_path):
    _mk_xa(env, tmp_path, treatment_scope={"symbols": ["GBPUSD"],
                                           "patterns": ["TBC"]})
    P, impact, cont = _assess_xa(env, tmp_path, symbol="GBPUSD")
    assert impact.classification == "UNAFFECTED"
    rec = _run_evolve(env, tmp_path, P)
    assert rec.reconsideration_status == "NOT_ELIGIBLE"
    with pytest.raises(EvolutionBindingError):
        create_governed_successor(
            rec.reconsideration_id, registry_dir=P["registry_dir"],
            reconsideration_dir=P["reconsideration_dir"])
    assert _no_other_candidates(P["registry_dir"])



def test_X_indeterminate_no_successor(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path, with_decisions=False)
    rec = _run_evolve(env, tmp_path, P)
    assert rec.reconsideration_status == "INDETERMINATE"
    with pytest.raises(EvolutionBindingError):
        create_governed_successor(
            rec.reconsideration_id, registry_dir=P["registry_dir"],
            reconsideration_dir=P["reconsideration_dir"])
    assert _no_other_candidates(P["registry_dir"])


def test_Y_human_reject_no_successor(env, tmp_path):
    from research_engine.v10.candidates.candidate_decision import (
        CandidateDecisionStore, HumanDecision)
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    CandidateDecisionStore(
        decisions_dir=str(P["decisions_dir"]))._append_row_atomic(
        HumanDecision(
            candidate_id="XA", decision="REJECT", actor="human",
            reason="reviewed", timestamp="2026-01-01T00:00:00+00:00",
            evaluation_id="E-XA", status_before="FAILED_VALIDATION",
            status_after="REJECTED", outcome="COMPLETED",
            recommendation_id="REC-E-XA"))
    rec = _run_evolve(env, tmp_path, P)
    assert rec.reconsideration_status == "NOT_ELIGIBLE"
    assert rec.historical_human_decision == "REJECT"
    assert "HUMAN_REJECTION_NOT_OVERRIDDEN" in rec.reason_codes
    with pytest.raises(EvolutionBindingError):
        create_governed_successor(
            rec.reconsideration_id, registry_dir=P["registry_dir"],
            reconsideration_dir=P["reconsideration_dir"])
    assert _no_other_candidates(P["registry_dir"])


def test_Z_circular_no_successor(env, tmp_path):
    from research_engine.lifecycle.candidate_reconsideration import (
        HistoricalHumanDecision)
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = _mk_record(
        env, tmp_path, impact, cont, dep="XA",
        outcome=HistoricalCandidateOutcome(
            candidate_status="FAILED_VALIDATION",
            evaluation_decisions=("REJECTED",)),
        human=HistoricalHumanDecision(present=False))
    assert rec.reconsideration_status == "NOT_ELIGIBLE"
    assert "CANDIDATE_CAUSED_TARGET_TRANSITION" in rec.reason_codes
    CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).append(rec)
    with pytest.raises(EvolutionBindingError):
        create_governed_successor(
            rec.reconsideration_id, registry_dir=P["registry_dir"],
            reconsideration_dir=P["reconsideration_dir"])
    assert _no_other_candidates(P["registry_dir"])



def _eligible_env(env, tmp_path):
    _mk_xa(env, tmp_path)
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = _run_evolve(env, tmp_path, P)
    return P, impact, cont, rec


def test_AB_active_NP2_historical_NP1_preserved(env, tmp_path):
    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    target = rec.target_baseline_id
    env.reg.save(BaselineSnapshot(
        snapshot_id="BL-NP2", config_hash="cfg-np2",
        configuration={"wave5_fake_policy": env.previous}))
    authority.set_active("BL-NP2", actor="test", reason="epoch advance")
    bound = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    succ = CandidateRegistry(P["registry_dir"]).get(
        bound.successor_candidate_id)
    assert succ.baseline_id == target
    assert succ.change_definition["baseline_config_hash"] == (
        rec.target_baseline_config_hash)
    assert bound.successor_baseline_id == target
    assert authority.get_active().active_baseline_id == "BL-NP2"
    fresh = CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).get(rec.reconsideration_id)
    assert fresh.target_baseline_id == target
    assert fresh.successor_candidate_id == bound.successor_candidate_id


def test_AC_no_evidence_relabelled(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    cont_before = CandidateEvidenceContinuityStore(
        P["continuity_dir"]).get(cont.continuity_id).to_dict()
    create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    cont_after = CandidateEvidenceContinuityStore(
        P["continuity_dir"]).get(cont.continuity_id).to_dict()
    assert cont_after == cont_before
    assert cont_before["historical_baseline_id"] == "OLD"
    assert cont_before["target_baseline_id"] == rec.target_baseline_id


def test_AD_no_inherited_authority(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)


def test_AE_AF_view_categorical_no_fake_numbers(env, tmp_path):
    _mk_xa(env, tmp_path, treatment_scope={"symbols": ["EURUSD", "GBPUSD"],
                                           "patterns": ["TBC"]})
    P, impact, cont = _assess_xa(env, tmp_path)
    rec = _run_evolve(env, tmp_path, P)
    view = get_evolution_view(
        "XA", env.app_id, impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    d = view.to_dict()
    assert view.reconsideration_status == "FRESH_EVIDENCE_REQUIRED"
    assert view.fresh_evidence_required is True
    assert view.fresh_evidence_present is not True
    assert view.impact_classification == "PARTIALLY_AFFECTED"
    assert view.continuity_state == "REVALIDATION_REQUIRED"
    assert view.blocked_reason
    assert all(isinstance(c, str) and c == c.upper()
               for c in view.reason_codes)
    for forbidden in ("percent", "pct", "eta", "completion", "progress",
                      "remaining"):
        assert not any(forbidden in k for k in d)


def test_AG_full_chain_reconstructable(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    bound = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    succ = CandidateRegistry(P["registry_dir"]).get(
        bound.successor_candidate_id)
    assert impact.impact_id == cont.impact_id == rec.impact_id
    assert cont.continuity_id == rec.continuity_id
    assert succ.change_definition["evolution_predecessor"] == "XA"
    assert succ.change_definition["evolution_reconsideration_id"] == (
        rec.reconsideration_id)
    fresh = CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).get(rec.reconsideration_id)
    assert fresh.successor_candidate_id == succ.candidate_id
    view = get_evolution_view(
        "XA", env.app_id, impact_dir=P["impact_dir"],
        continuity_dir=P["continuity_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert view.successor_candidate_id == succ.candidate_id
    assert view.blocked_reason == ""


def test_AH_AK_AL_zero_mutation(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    app_bytes = P["application_path"].read_bytes()
    dec_bytes = (Path(P["decisions_dir"]) /
                 "decisions.jsonl").read_bytes()
    ev_bytes = (P["evaluations_dir"] / "XA.jsonl").read_bytes()
    pointer_bytes = (env.root / "baselines" /
                     "active_baseline.json").read_bytes()
    adapter_bytes = env.adapter_path.read_bytes()
    impact_before = impact.to_dict()
    cont_before = cont.to_dict()
    reg_bytes = (Path(P["registry_dir"]) /
                 "candidates.jsonl").read_bytes()
    create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert P["application_path"].read_bytes() == app_bytes
    assert (Path(P["decisions_dir"]) /
            "decisions.jsonl").read_bytes() == dec_bytes
    assert (P["evaluations_dir"] / "XA.jsonl").read_bytes() == ev_bytes
    assert (env.root / "baselines" /
            "active_baseline.json").read_bytes() == pointer_bytes
    assert env.adapter_path.read_bytes() == adapter_bytes
    assert impact.to_dict() == impact_before
    assert cont.to_dict() == cont_before
    lines = [json.loads(line) for line in (Path(P["registry_dir"]) /
             "candidates.jsonl").read_text(
             encoding="utf-8").splitlines() if line.strip()]
    pred = [r for r in lines if r["candidate_id"] == "XA"]
    old_pred = [json.loads(line) for line in reg_bytes.decode(
        "utf-8").splitlines() if line.strip()
        and json.loads(line)["candidate_id"] == "XA"]
    assert pred == old_pred


def test_AN_distinct_transition_distinct_successor(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    sid1 = compute_successor_candidate_id(
        predecessor_candidate_id="XA",
        target_baseline_id=rec.target_baseline_id,
        reconsideration_id=rec.reconsideration_id)
    sid2 = compute_successor_candidate_id(
        predecessor_candidate_id="XA",
        target_baseline_id=rec.target_baseline_id + "-other",
        reconsideration_id=rec.reconsideration_id)
    sid3 = compute_successor_candidate_id(
        predecessor_candidate_id="XA",
        target_baseline_id=rec.target_baseline_id,
        reconsideration_id=rec.reconsideration_id + "-other")
    assert len({sid1, sid2, sid3}) == 3
    assert sid1 == compute_successor_candidate_id(
        predecessor_candidate_id="XA",
        target_baseline_id=rec.target_baseline_id,
        reconsideration_id=rec.reconsideration_id)


def test_T_restart_retry_same_successor(env, tmp_path):
    P, impact, cont, rec = _eligible_env(env, tmp_path)
    bound = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    # Full restart: brand-new store + registry instances from disk only.
    fresh_rec = CandidateReconsiderationHistoryStore(
        P["reconsideration_dir"]).get(rec.reconsideration_id)
    assert fresh_rec.scientific_key() == rec.scientific_key()
    assert fresh_rec.successor_candidate_id == bound.successor_candidate_id
    again = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    assert again.successor_candidate_id == bound.successor_candidate_id
    ids = [c.candidate_id for c in
           CandidateRegistry(P["registry_dir"]).list_all()
           if c.candidate_id == bound.successor_candidate_id]
    assert ids == [bound.successor_candidate_id]


    bound = create_governed_successor(
        rec.reconsideration_id, registry_dir=P["registry_dir"],
        reconsideration_dir=P["reconsideration_dir"])
    succ = CandidateRegistry(P["registry_dir"]).get(
        bound.successor_candidate_id)
    assert succ.validation_history == []
    assert succ.status == "PROPOSED"
    rec_path = (Path(env.dirs["recommendations_dir"]) /
                "recommendations.jsonl")
    if rec_path.is_file():
        rows = [json.loads(line) for line in
                rec_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert not [r for r in rows
                    if r.get("candidate_id") == succ.candidate_id]
    dec_path = (Path(P["decisions_dir"]) / "decisions.jsonl")
    if dec_path.is_file():
        rows = [json.loads(line) for line in
                dec_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert not [r for r in rows
                    if r.get("candidate_id") == succ.candidate_id]
    ledger = [json.loads(line) for line in _read_apps(
        P["application_path"])]
    assert not [r for r in ledger
                if r.get("candidate_id") == succ.candidate_id]

