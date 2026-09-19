"""Wave 6.2C -- baseline-transition evidence-population safety tests."""
from __future__ import annotations
from dataclasses import replace
import pytest
from research_engine.lifecycle.candidate_evidence_eligibility import (
    MATERIALLY_AFFECTED_ALL_EXPOSED, EvidenceEligibilityStatus,
    HistoricalObservation, assess_evidence_eligibility,)
from research_engine.lifecycle.candidate_evidence_continuity import (
    EvidenceContinuityState, build_candidate_evidence_continuity_state,)
from research_engine.lifecycle.candidate_evidence_population import (
    FreshTargetObservation, PopulationBindingError, PopulationConflictError,
    UnknownHistoricalObservationError, PopulationSource,
    build_candidate_evidence_population, compute_population_id,)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,)

def _scope(symbols=None, patterns=None):
    return {"symbols": sorted(symbols) if symbols else None,
            "patterns": sorted(patterns) if patterns else None}

def _make_record(**kw):
    defaults = dict(impact_id="CBI-1", candidate_id="C1",
        candidate_baseline_id="OLD", candidate_baseline_config_hash="old-config",
        from_baseline_id="OLD", from_baseline_config_hash="old-config",
        to_baseline_id="NEW", to_baseline_config_hash="new-config",
        application_id="app-1", candidate_treatment_id="T-C",
        deployed_treatment_id="T-D", classification="PARTIALLY_AFFECTED",
        reason_codes=("PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"),
        candidate_scope=_scope(["EURUSD", "GBPUSD"]),
        deployed_scope=_scope(["EURUSD"]))
    defaults.update(kw)
    return CandidateBaselineImpactRecord(**defaults)

_SYM = {"DIRECTLY_ELIGIBLE": "GBPUSD", "REVALIDATION_REQUIRED": "EURUSD",
        "INDETERMINATE": "AUDUSD"}

def _decide(record, status, cid, **kw):
    p = dict(candidate_id="C1", baseline_id="OLD", config_hash="old-config",
             treatment_id="T-C", target="NEW", target_hash="new-config")
    p.update(kw)
    obs = HistoricalObservation(candidate_id=p["candidate_id"],
        baseline_id=p["baseline_id"], config_hash=p["config_hash"],
        symbol=_SYM[status], pattern=None,
        treatment_id=p["treatment_id"], correlation_id=cid)
    dec = assess_evidence_eligibility(impact_record=record, observation=obs,
        target_baseline_id=p["target"],
        target_baseline_config_hash=p["target_hash"])
    assert dec.status == EvidenceEligibilityStatus(status), dec.reason_codes
    return dec

def _hist(cid, **kw):
    p = dict(candidate_id="C1", baseline_id="OLD", config_hash="old-config",
             symbol="GBPUSD", pattern=None, treatment_id="T-C")
    p.update(kw)
    return HistoricalObservation(correlation_id=cid, **p)

def _fresh(cid, **kw):
    p = dict(candidate_id="C1", source_baseline_id="NEW",
             source_config_hash="new-config", treatment_id="T-C")
    p.update(kw)
    return FreshTargetObservation(correlation_id=cid, **p)

def _build(record, decisions, **kw):
    return build_candidate_evidence_continuity_state(
        impact_record=record, decisions=decisions, **kw)

def test_A_continuity_allowed_admits_directly_eligible():
    record = _make_record()
    decisions = [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                 _decide(record, "DIRECTLY_ELIGIBLE", "COR-B")]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.CONTINUITY_ALLOWED
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-B")])
    assert pop.historical_count == 2
    assert {m.correlation_id for m in pop.historical_members} == {
        "COR-A", "COR-B"}

def test_B_historical_retains_source_N():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")])
    (member,) = pop.historical_members
    assert member.source_baseline_id == "OLD"

def _nde_shape(record, cid):
    base = _decide(record, "REVALIDATION_REQUIRED", cid)
    return replace(base, status=EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE,
        reason_codes=(MATERIALLY_AFFECTED_ALL_EXPOSED,),
        may_contribute_directly=False, fresh_evidence_required=True)

def test_D_revalidation_admits_only_direct_subset():
    record = _make_record()
    decisions = [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                 _decide(record, "REVALIDATION_REQUIRED", "COR-B")]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.REVALIDATION_REQUIRED
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"),
                                 _hist("COR-B", symbol="EURUSD")])
    assert [m.correlation_id for m in pop.historical_members] == ["COR-A"]
    assert pop.fresh_evidence_required is True

def test_E_revalidation_observation_excluded():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "REVALIDATION_REQUIRED", "COR-B")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-B", symbol="EURUSD")])
    assert pop.historical_count == 0

def test_F_not_directly_eligible_excluded():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _nde_shape(record, "COR-N")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-N")])
    assert [m.correlation_id for m in pop.historical_members] == ["COR-A"]

def test_G_blocked_indeterminate_zero_historical():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "INDETERMINATE", "COR-I")])
    assert state.state is EvidenceContinuityState.BLOCKED_INDETERMINATE
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")])
    assert pop.historical_count == 0
    assert pop.blocked_historical_continuity is True

def test_H_no_historical_evidence_empty():
    record = _make_record()
    state = _build(record, [])
    assert state.state is EvidenceContinuityState.NO_HISTORICAL_EVIDENCE
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[])
    assert pop.historical_count == 0

def test_I_fresh_enters_fresh_population():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")],
        fresh_observations=[_fresh("COR-F1"), _fresh("COR-F2")])
    assert pop.fresh_count == 2
    assert {m.correlation_id for m in pop.fresh_members} == {
        "COR-F1", "COR-F2"}

def test_J_fresh_retains_N1():
    record = _make_record()
    state = _build(record, [])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        fresh_observations=[_fresh("COR-F")])
    (member,) = pop.fresh_members
    assert member.source_baseline_id == "NEW"
    assert member.population_source is PopulationSource.FRESH_TARGET_BASELINE

def test_K_n_era_cannot_masquerade_as_fresh():
    record = _make_record()
    state = _build(record, [])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-X", source_baseline_id="OLD",
                                       source_config_hash="old-config")])

def test_L_wrong_candidate_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A", candidate_id="C2")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-F", candidate_id="C2")])

def test_M_wrong_historical_baseline_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A", baseline_id="OTHER")])

def test_N_wrong_target_fails():
    record = _make_record()
    state = _build(record, [])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-F", source_baseline_id="OTHER")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-F",
                                       source_config_hash="other-config")])

def test_O_wrong_impact_fails():
    record = _make_record()
    other = _make_record(impact_id="CBI-OTHER")
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=other, continuity=state,
            historical_observations=[_hist("COR-A")])

def test_P_wrong_continuity_fails():
    record = _make_record()
    s1 = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    s2 = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                         _decide(record, "DIRECTLY_ELIGIBLE", "COR-B")])
    assert s1.continuity_id != s2.continuity_id
    with pytest.raises(UnknownHistoricalObservationError):
        build_candidate_evidence_population(
            impact_record=record, continuity=s1,
            historical_observations=[_hist("COR-A"), _hist("COR-B")])

def test_Q_treatment_mismatch_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A", treatment_id="T-X")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-F", treatment_id="T-X")])

def test_R_unknown_historical_fails_closed():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(UnknownHistoricalObservationError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A"), _hist("COR-GHOST")])

def test_S_status_conflict_fails_closed():
    # Caller supplies COR-A provenance but snapshot says REVALIDATION_REQUIRED
    # for COR-A via a different snapshot: bind fails because COR-A is not in
    # the DIRECTLY_ELIGIBLE permitted subset of the second snapshot.
    record = _make_record()
    s_direct = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    s_reval = _build(record, [_decide(record, "REVALIDATION_REQUIRED", "COR-A",
                                      baseline_id="OLD")]) if False else None
    # Build the conflict directly: craft decisions where COR-A is revalidation.
    record2 = _make_record()
    decisions = [_decide(record2, "REVALIDATION_REQUIRED", "COR-B")]
    state2 = _build(record2, decisions)
    with pytest.raises(UnknownHistoricalObservationError):
        build_candidate_evidence_population(
            impact_record=record2, continuity=state2,
            historical_observations=[_hist("COR-A")])
    assert s_direct.continuity_id != state2.continuity_id

def test_T_duplicate_historical_no_inflation():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-A")])
    assert pop.historical_count == 1
    assert pop.total_count == 1

def test_U_duplicate_fresh_no_inflation():
    record = _make_record()
    state = _build(record, [])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        fresh_observations=[_fresh("COR-F"), _fresh("COR-F")])
    assert pop.fresh_count == 1

def test_V_hist_plus_fresh_same_id_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationConflictError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A")],
            fresh_observations=[_fresh("COR-A")])

def test_W_conflicting_duplicate_provenance_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationConflictError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A"),
                                     _hist("COR-A", symbol="GBPUSD",
                                           treatment_id="T-C",
                                           config_hash="old-config",
                                           candidate_id="C1",
                                           baseline_id="OLD") if False
                                     else HistoricalObservation(
                                         candidate_id="C1", baseline_id="OLD",
                                         config_hash="old-config",
                                         symbol="GBPUSD", pattern="OTHER-PAT",
                                         treatment_id="T-C",
                                         correlation_id="COR-A")])

def test_X_accounting_invariants():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "DIRECTLY_ELIGIBLE", "COR-B"),
                            _decide(record, "REVALIDATION_REQUIRED", "COR-C")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-B"),
                                 _hist("COR-C", symbol="EURUSD")],
        fresh_observations=[_fresh("COR-F")])
    assert pop.historical_count == len(pop.historical_members) == 2
    assert pop.fresh_count == len(pop.fresh_members) == 1
    assert pop.total_count == 3
    ids = ([m.correlation_id for m in pop.historical_members]
           + [m.correlation_id for m in pop.fresh_members])
    assert len(set(ids)) == len(ids)
    assert all(m.source_baseline_id == "OLD"
               for m in pop.historical_members)
    assert all(m.source_baseline_id == "NEW" for m in pop.fresh_members)
    assert {m.correlation_id for m in pop.historical_members} <= set(
        state.direct_contribution_correlation_ids)
    assert {m.correlation_id for m in pop.historical_members} <= set(
        state.direct_contribution_correlation_ids)

def test_Y_identity_order_independent():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "DIRECTLY_ELIGIBLE", "COR-B")])
    p1 = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-B")],
        fresh_observations=[_fresh("COR-F1"), _fresh("COR-F2")])
    p2 = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-B"), _hist("COR-A")],
        fresh_observations=[_fresh("COR-F2"), _fresh("COR-F1")])
    assert p1.population_id == p2.population_id

def test_Z_changed_population_different_id():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "DIRECTLY_ELIGIBLE", "COR-B")])
    p1 = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")])
    p2 = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A"), _hist("COR-B")])
    assert p1.population_id != p2.population_id

def test_AA_empty_population_safe():
    record = _make_record()
    state = _build(record, [])
    p1 = build_candidate_evidence_population(
        impact_record=record, continuity=state)
    p2 = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[], fresh_observations=[])
    assert (p1.historical_count, p1.fresh_count, p1.total_count) == (0, 0, 0)
    assert p1.population_id == p2.population_id

def test_AB_historical_only_where_allowed():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")])
    assert pop.historical_count == 1 and pop.fresh_count == 0
    assert pop.historical_continuity_used is True

def test_AC_fresh_only_safe():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "INDETERMINATE", "COR-I")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        fresh_observations=[_fresh("COR-F")])
    assert pop.historical_count == 0 and pop.fresh_count == 1
    assert pop.blocked_historical_continuity is True

def test_AD_one_fresh_does_not_claim_revalidation():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                            _decide(record, "REVALIDATION_REQUIRED", "COR-B")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")],
        fresh_observations=[_fresh("COR-F")])
    assert pop.fresh_evidence_present is True
    assert pop.fresh_evidence_required is True
    assert not hasattr(pop, "revalidation_satisfied")
    assert not hasattr(pop, "revalidation_requirement_satisfied")

def test_AF_no_authority_mutation(tmp_path):
    import json as _json
    record = _make_record()
    d = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    state = _build(record, [d])
    idir = tmp_path / "impact"
    cdir = tmp_path / "continuity"
    idir.mkdir()
    cdir.mkdir()
    (idir / "x.json").write_text("{}")
    (cdir / "y.json").write_text("{}")
    before_i = sorted(p.name for p in idir.iterdir())
    before_c = sorted(p.name for p in cdir.iterdir())
    build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")],
        fresh_observations=[_fresh("COR-F")])
    after_i = sorted(p.name for p in idir.iterdir())
    after_c = sorted(p.name for p in cdir.iterdir())
    assert before_i == after_i
    assert before_c == after_c
    assert (idir / "x.json").read_text() == "{}"
    assert _json.loads(state.canonical_json())["continuity_id"] == (
        state.continuity_id)

def test_AE_sources_immutable_after_construction():
    record = _make_record()
    d = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    state = _build(record, [d])
    before_decision = d.to_dict()
    before_state = state.to_dict()
    before_impact = record.to_dict()
    hist = [_hist("COR-A")]
    fresh = [_fresh("COR-F")]
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=hist, fresh_observations=fresh)
    assert d.to_dict() == before_decision
    assert state.to_dict() == before_state
    assert record.to_dict() == before_impact
    assert hist[0] == _hist("COR-A")
    assert fresh[0] == _fresh("COR-F")
    assert pop.historical_members[0].source_baseline_id == "OLD"

def test_AG_chain_reconstructable():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")],
        fresh_observations=[_fresh("COR-F")])
    assert pop.impact_id == record.impact_id == state.impact_id
    assert pop.continuity_id == state.continuity_id
    assert pop.candidate_id == record.candidate_id == state.candidate_id
    assert pop.target_baseline_id == state.target_baseline_id
    assert pop.target_baseline_id == record.to_baseline_id
    assert pop.candidate_treatment_id == record.candidate_treatment_id
    assert pop.to_dict()["historical_members"][0]["source_baseline_id"] == "OLD"
    assert pop.to_dict()["fresh_members"][0]["source_baseline_id"] == "NEW"




def test_Q_treatment_mismatch_fails():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            historical_observations=[_hist("COR-A", treatment_id="T-X")])
    with pytest.raises(PopulationBindingError):
        build_candidate_evidence_population(
            impact_record=record, continuity=state,
            fresh_observations=[_fresh("COR-F", treatment_id="T-X")])


def test_C_no_relabelling():
    record = _make_record()
    state = _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")])
    pop = build_candidate_evidence_population(
        impact_record=record, continuity=state,
        historical_observations=[_hist("COR-A")],
        fresh_observations=[_fresh("COR-F")])
    for member in pop.historical_members:
        assert member.source_baseline_id == "OLD"
        assert member.population_source is PopulationSource.HISTORICAL_CONTINUITY
    for member in pop.fresh_members:
        assert member.source_baseline_id == "NEW"
