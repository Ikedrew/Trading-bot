"""Wave 6.3A focused suite: candidate reconsideration eligibility."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from research_engine.lifecycle.candidate_reconsideration import (
    CandidateReconsiderationDecision, HistoricalCandidateOutcome,
    HistoricalHumanDecision, ReconsiderationStatus,
    decide_candidate_reconsideration,
)
from research_engine.lifecycle.candidate_evidence_continuity import (
    EvidenceContinuityState as ECS,
)
CID = "CAND-X"; HB = "BASE-N"; HH = "cfgN"; TB = "BASE-NP1"; TH = "cfgNP1"
IID = "IMP-1"; CCID = "ECC-1"; TRT = "TRT-X"; APP = "APP-D0"; DEPLOYER = "CAND-DEPLOYER"
def impact(cls="MATERIALLY_AFFECTED", cid=CID):
    return SimpleNamespace(impact_id=IID, classification=cls, candidate_id=cid, candidate_baseline_id=HB, candidate_baseline_config_hash=HH, to_baseline_id=TB, to_baseline_config_hash=TH, application_id=APP, candidate_treatment_id=TRT)
def cont(state, cid=CID, iid=IID):
    return SimpleNamespace(continuity_id=CCID, state=state, candidate_id=cid, historical_baseline_id=HB, historical_baseline_config_hash=HH, target_baseline_id=TB, target_baseline_config_hash=TH, impact_id=iid, application_id=APP, candidate_treatment_id=TRT)
def out(status, *evals):
    return HistoricalCandidateOutcome(candidate_status=status, evaluation_decisions=tuple(evals))
def nohuman():
    return HistoricalHumanDecision(present=False)
def kw(**o):
    b = dict(transition_candidate_id=DEPLOYER)
    b.update(o)
    return b
def test_A_failed_material_eligible():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION
    assert "MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT" in d.reason_codes
    assert d.fresh_evidence_required is True
def test_B_inconclusive_material_eligible():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("ARCHIVED", "INCONCLUSIVE"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION
    assert "MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT" in d.reason_codes
def test_C_unaffected_failed_not_eligible():
    d = decide_candidate_reconsideration(impact_record=impact("UNAFFECTED"), continuity=cont(ECS.CONTINUITY_ALLOWED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
    assert "BASELINE_CHANGE_UNAFFECTED" in d.reason_codes
def test_D_unaffected_inconclusive_not_eligible():
    d = decide_candidate_reconsideration(impact_record=impact("UNAFFECTED"), continuity=cont(ECS.CONTINUITY_ALLOWED), historical_outcome=out("ARCHIVED", "INCONCLUSIVE"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
def test_E_partial_revalidation_fresh_required():
    d = decide_candidate_reconsideration(impact_record=impact("PARTIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.FRESH_EVIDENCE_REQUIRED
    assert "PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION" in d.reason_codes
    assert d.fresh_evidence_required is True
def test_F_partial_continuity_allowed_no_reopen():
    d = decide_candidate_reconsideration(impact_record=impact("PARTIALLY_AFFECTED"), continuity=cont(ECS.CONTINUITY_ALLOWED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
def test_G_indeterminate_impact_closed():
    d = decide_candidate_reconsideration(impact_record=impact("INDETERMINATE"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
    assert "IMPACT_INDETERMINATE" in d.reason_codes
def test_H_blocked_continuity_closed():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.BLOCKED_INDETERMINATE), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
    assert "CONTINUITY_INDETERMINATE" in d.reason_codes
def test_I_active_not_redevelopment():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("SHADOW_TESTING", "INCONCLUSIVE"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
    assert "HISTORICAL_CANDIDATE_NOT_TERMINAL" in d.reason_codes
def test_J_successful_not_failed():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.CONTINUITY_ALLOWED), historical_outcome=out("ACCEPTED", "VALIDATED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
    assert "HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL" in d.reason_codes
def test_K_human_reject_not_overridden():
    hd = HistoricalHumanDecision(present=True, decision="REJECT", outcome="COMPLETED")
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("REJECTED", "REJECTED"), human_decision=hd, **kw())
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
    assert "HUMAN_REJECTION_NOT_OVERRIDDEN" in d.reason_codes
def test_L_circular_self():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), transition_candidate_id=CID)
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
    assert "CANDIDATE_CAUSED_TARGET_TRANSITION" in d.reason_codes
def test_M_wrong_candidate():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED, cid="OTHER"), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE

def test_N_wrong_hist():
    bad = SimpleNamespace(**{**vars(cont(ECS.REVALIDATION_REQUIRED)), "historical_baseline_id": "WRONG"})
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=bad, historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
def test_O_wrong_target():
    bad = SimpleNamespace(**{**vars(impact("MATERIALLY_AFFECTED")), "to_baseline_id": "WRONG"})
    d = decide_candidate_reconsideration(impact_record=bad, continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
def test_P_wrong_impact():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED, iid="OTHER-IMP"), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
def test_Q_population_mismatch():
    pop = SimpleNamespace(candidate_id=CID, impact_id=IID, continuity_id="OTHER", target_baseline_id=TB, target_baseline_config_hash=TH, candidate_treatment_id=TRT)
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), transition_candidate_id=DEPLOYER, population=pop)
    assert d.status is ReconsiderationStatus.INDETERMINATE
def test_R_treatment_mismatch():
    bad = SimpleNamespace(**{**vars(cont(ECS.REVALIDATION_REQUIRED)), "candidate_treatment_id": "OTHER-TRT"})
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=bad, historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
def test_S_conflicting_outcome():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED", "VALIDATED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
    assert "CONFLICTING_HISTORICAL_OUTCOME" in d.reason_codes
def test_T_deterministic():
    k = dict(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    a = decide_candidate_reconsideration(**k); b = decide_candidate_reconsideration(**k)
    assert a == b and a.reconsideration_id == b.reconsideration_id
def test_U_later_transition_distinct():
    k = dict(continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    a = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), **k)
    other = SimpleNamespace(**{**vars(impact("MATERIALLY_AFFECTED")), "to_baseline_id": "BASE-NP2", "to_baseline_config_hash": "cfgNP2"})
    otherc = SimpleNamespace(**{**vars(cont(ECS.REVALIDATION_REQUIRED)), "target_baseline_id": "BASE-NP2", "target_baseline_config_hash": "cfgNP2"})
    kk = {kk2: vv for kk2, vv in k.items() if kk2 != "continuity"}
    c = decide_candidate_reconsideration(impact_record=other, continuity=otherc, **kk)
    assert a.reconsideration_id != c.reconsideration_id
def test_V_bound_to_N():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.historical_baseline_id == HB and d.target_baseline_id == TB
def test_W_no_mutation():
    import research_engine.v10.candidates.models as models
    before = set(vars(models.CandidateStatus).keys())
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION
    assert set(vars(models.CandidateStatus).keys()) == before
def test_Y_zero_side_effect():
    import research_engine.lifecycle.candidate_reconsideration as m
    import inspect as _i
    src = _i.getsource(m.decide_candidate_reconsideration) + _i.getsource(m.compute_reconsideration_id)
    for token in ("open(", "update_status", "create_recommendation", "ApplicationLedger", "CandidateRegistry"):
        assert token not in src
def test_Z_chain():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    dd = d.to_dict()
    for f in ("candidate_id", "historical_baseline_id", "target_baseline_id", "impact_id", "continuity_id", "candidate_treatment_id", "historical_evaluation_outcome", "reason_codes", "status"):
        assert dd[f]
def test_AA_counts_no_trigger():
    pop = SimpleNamespace(candidate_id=CID, impact_id=IID, continuity_id=CCID, target_baseline_id=TB, target_baseline_config_hash=TH, candidate_treatment_id=TRT, historical_count=30)
    d = decide_candidate_reconsideration(impact_record=impact("UNAFFECTED"), continuity=cont(ECS.CONTINUITY_ALLOWED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), transition_candidate_id=DEPLOYER, population=pop)
    assert d.status is ReconsiderationStatus.NOT_ELIGIBLE
def test_AB_no_hist_evidence():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.NO_HISTORICAL_EVIDENCE), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman(), **kw())
    assert d.status is ReconsiderationStatus.INDETERMINATE
    assert "NO_HISTORICAL_EVIDENCE" in d.reason_codes
def test_missing_deployer_closed():
    d = decide_candidate_reconsideration(impact_record=impact("MATERIALLY_AFFECTED"), continuity=cont(ECS.REVALIDATION_REQUIRED), historical_outcome=out("FAILED_VALIDATION", "REJECTED"), human_decision=nohuman())
    assert d.status is ReconsiderationStatus.INDETERMINATE


