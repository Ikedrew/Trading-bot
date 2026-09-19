"""Wave 6.2B — canonical evidence-continuity / revalidation state tests.

Proves the deterministic, durable continuity/revalidation state that summarises
Wave 6.2A per-observation eligibility decisions for exactly ONE Candidate X @
historical baseline N and ONE exact verified N -> N+1 impact record.

Proofs A-Z are implemented (precedence, accounting, provenance binding,
duplicate-observation safety, deterministic identity, append-only durability,
restart, chain reconstruction, flags, zero side effects).

No AWS. No S3. No MT5. No broker. No live runtime.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from research_engine.lifecycle.candidate_evidence_eligibility import (
    MATERIALLY_AFFECTED_ALL_EXPOSED,
    EvidenceEligibilityStatus,
    HistoricalObservation,
    assess_evidence_eligibility,
)
from research_engine.lifecycle.candidate_evidence_continuity import (
    ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE,
    MIXED_ELIGIBILITY_POPULATION,
    NO_HISTORICAL_OBSERVATIONS_ASSESSED,
    NOT_DIRECTLY_ELIGIBLE_EVIDENCE,
    REVALIDATION_REQUIRED_EVIDENCE,
    UNRESOLVED_INDETERMINATE_EVIDENCE,
    CandidateEvidenceContinuityStore,
    ContinuityAccountingError,
    ContinuityBindingError,
    ContinuityConflictError,
    ContinuityObservationIdentityError,
    EvidenceContinuityState,
    assess_candidate_evidence_continuity,
    build_candidate_evidence_continuity_state,
    reconstruct_evidence_continuity_chain,
)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
    CandidateImpactHistoryStore,
)


# ─── Helpers (real 6.2A decisions throughout) ─────────────────────────────────


def _scope(symbols=None, patterns=None):
    return {
        "symbols": sorted(symbols) if symbols else None,
        "patterns": sorted(patterns) if patterns else None,
    }


def _make_record(
    classification="PARTIALLY_AFFECTED",
    reason_codes=("PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"),
    candidate_scope=None,
    deployed_scope=None,
    **kw,
):
    defaults = dict(
        impact_id="CBI-1",
        candidate_id="C1",
        candidate_baseline_id="OLD",
        candidate_baseline_config_hash="old-config",
        from_baseline_id="OLD",
        from_baseline_config_hash="old-config",
        to_baseline_id="NEW",
        to_baseline_config_hash="new-config",
        application_id="app-1",
        candidate_treatment_id="T-C",
        deployed_treatment_id="T-D",
        classification=classification,
        reason_codes=tuple(reason_codes),
        candidate_scope=candidate_scope,
        deployed_scope=deployed_scope,
    )
    defaults.update(kw)
    return CandidateBaselineImpactRecord(**defaults)


# A PARTIALLY_AFFECTED record whose bounded scopes can yield the full 6.2A
# palette from ONE record: symbol in candidate scope but inside the deployed
# overlap -> REVALIDATION_REQUIRED; in candidate scope and outside the overlap
# -> DIRECTLY_ELIGIBLE; outside the candidate scope -> INDETERMINATE.
def _mixed_record(**kw):
    return _make_record(
        candidate_scope=_scope(["EURUSD", "GBPUSD"]),
        deployed_scope=_scope(["EURUSD"]),
        **kw,
    )


# A MATERIALLY_AFFECTED record: every observation is NOT_DIRECTLY_ELIGIBLE.
def _material_record(**kw):
    return _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=("MATERIAL_SCOPE_OVERLAP",),
        candidate_scope=_scope(["EURUSD", "GBPUSD"]),
        deployed_scope=_scope(["EURUSD"]),
        **kw,
    )


_SYMBOL_FOR_STATUS = {
    "DIRECTLY_ELIGIBLE": "GBPUSD",
    "REVALIDATION_REQUIRED": "EURUSD",
    "INDETERMINATE": "AUDUSD",
}


def _decide(
    record,
    status,
    correlation_id,
    candidate_id="C1",
    baseline_id="OLD",
    config_hash="old-config",
    treatment_id="T-C",
    target="NEW",
    target_hash="new-config",
    symbol=None,
    pattern=None,
    expect=True,
):
    """Run the REAL Wave 6.2A eligibility decision for one observation.

    ``expect=False`` is used when the test deliberately feeds a foreign
    provenance dimension, so 6.2A legitimately returns INDETERMINATE.
    """
    if symbol is None:
        symbol = _SYMBOL_FOR_STATUS[status]
    obs = HistoricalObservation(
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        config_hash=config_hash,
        symbol=symbol,
        pattern=pattern,
        treatment_id=treatment_id,
        correlation_id=correlation_id,
    )
    decision = assess_evidence_eligibility(
        impact_record=record,
        observation=obs,
        target_baseline_id=target,
        target_baseline_config_hash=target_hash,
    )
    if expect:
        got = decision.status
        assert got == EvidenceEligibilityStatus(status), decision.reason_codes
    return decision


def _build(record, decisions, **kw):
    return build_candidate_evidence_continuity_state(
        impact_record=record, decisions=decisions, **kw)


def _not_directly_eligible_shape(record, correlation_id):
    """A contract-valid 6.2A decision shape for NOT_DIRECTLY_ELIGIBLE.

    Wave 6.2A's per-observation dispatch makes EVERY observation
    NOT_DIRECTLY_ELIGIBLE under a MATERIALLY_AFFECTED record, so a single
    impact record can never produce DIRECTLY_ELIGIBLE + NOT_DIRECTLY_ELIGIBLE
    simultaneously. The aggregate precedence must still be provable for a mixed
    population, so this reuses the exact 6.2A fields emitted on that path.
    """
    base = _decide(record, "REVALIDATION_REQUIRED", correlation_id)
    return replace(
        base,
        status=EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE,
        reason_codes=(MATERIALLY_AFFECTED_ALL_EXPOSED,),
        may_contribute_directly=False,
        fresh_evidence_required=True,
    )


def _accounting(state):
    return (
        state.total_observations_assessed,
        state.directly_eligible_count,
        state.revalidation_required_count,
        state.not_directly_eligible_count,
        state.indeterminate_count,
    )


# ─── A-F: canonical continuity states + conservative precedence ───────────────


# A. Zero historical observations/decisions -> NO_HISTORICAL_EVIDENCE.
def test_A_zero_decisions_is_no_historical_evidence():
    record = _mixed_record()
    state = _build(record, [])
    assert state.state is EvidenceContinuityState.NO_HISTORICAL_EVIDENCE
    assert state.state is not EvidenceContinuityState.CONTINUITY_ALLOWED
    assert _accounting(state) == (0, 0, 0, 0, 0)
    assert state.observation_states == ()
    assert state.reason_codes == (NO_HISTORICAL_OBSERVATIONS_ASSESSED,)
    assert state.fresh_evidence_required is True
    assert state.direct_historical_contribution_allowed is False
    assert state.direct_contribution_correlation_ids == ()
    # An empty historical population is never treated as usable evidence.
    assert state.reason_codes != (ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE,)


# B. All assessed historical observations DIRECTLY_ELIGIBLE -> CONTINUITY_ALLOWED.
def test_B_all_directly_eligible_is_continuity_allowed():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-B"),
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-C"),
    ]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.CONTINUITY_ALLOWED
    assert _accounting(state) == (3, 3, 0, 0, 0)
    assert state.reason_codes == (ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE,)
    assert state.direct_historical_contribution_allowed is True
    assert state.fresh_evidence_required is False
    assert state.direct_contribution_correlation_ids == ("COR-A", "COR-B", "COR-C")


# C. DIRECTLY_ELIGIBLE + REVALIDATION_REQUIRED -> REVALIDATION_REQUIRED.
def test_C_directly_eligible_plus_revalidation_required():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
    ]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.REVALIDATION_REQUIRED
    assert _accounting(state) == (2, 1, 1, 0, 0)
    assert REVALIDATION_REQUIRED_EVIDENCE in state.reason_codes
    assert MIXED_ELIGIBILITY_POPULATION in state.reason_codes
    # Fresh N+1 evidence required; direct contribution ONLY for the subset.
    assert state.fresh_evidence_required is True
    assert state.direct_historical_contribution_allowed is True
    assert state.direct_contribution_correlation_ids == ("COR-A",)


# D. DIRECTLY_ELIGIBLE + NOT_DIRECTLY_ELIGIBLE -> REVALIDATION_REQUIRED.
def test_D_directly_eligible_plus_not_directly_eligible():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _not_directly_eligible_shape(record, "COR-B"),
    ]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.REVALIDATION_REQUIRED
    assert _accounting(state) == (2, 1, 0, 1, 0)
    assert NOT_DIRECTLY_ELIGIBLE_EVIDENCE in state.reason_codes
    assert MIXED_ELIGIBILITY_POPULATION in state.reason_codes
    assert state.fresh_evidence_required is True
    assert state.direct_historical_contribution_allowed is True
    assert state.direct_contribution_correlation_ids == ("COR-A",)


# D2. NOT_DIRECTLY_ELIGIBLE-only population -> REVALIDATION_REQUIRED with NO
#     direct contribution permitted.
def test_D2_not_directly_eligible_only_blocks_direct_contribution():
    record = _material_record()
    decisions = [
        _decide(record, "NOT_DIRECTLY_ELIGIBLE", "COR-A", symbol="EURUSD"),
        _decide(record, "NOT_DIRECTLY_ELIGIBLE", "COR-B", symbol="GBPUSD"),
    ]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.REVALIDATION_REQUIRED
    assert _accounting(state) == (2, 0, 0, 2, 0)
    assert state.reason_codes == (NOT_DIRECTLY_ELIGIBLE_EVIDENCE,)
    assert state.direct_historical_contribution_allowed is False
    assert state.direct_contribution_correlation_ids == ()
    assert state.fresh_evidence_required is True


# E. Any INDETERMINATE -> BLOCKED_INDETERMINATE (fail closed).
def test_E_any_indeterminate_blocks():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "INDETERMINATE", "COR-B"),
    ]
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.BLOCKED_INDETERMINATE
    assert _accounting(state) == (2, 1, 0, 0, 1)
    assert UNRESOLVED_INDETERMINATE_EVIDENCE in state.reason_codes
    # Aggregate boundary: no direct historical contribution, fresh evidence
    # required. The indeterminate observation is NEVER silently dropped.
    assert state.direct_historical_contribution_allowed is False
    assert state.direct_contribution_correlation_ids == ()
    assert state.fresh_evidence_required is True
    assert [s.correlation_id for s in state.observation_states] == ["COR-A", "COR-B"]


# F. One INDETERMINATE cannot be hidden by many DIRECTLY_ELIGIBLE decisions.
def test_F_indeterminate_not_hidden_by_many_directly_eligible():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", f"COR-{i:03d}") for i in range(50)
    ]
    decisions.append(_decide(record, "INDETERMINATE", "COR-ZZZ"))
    state = _build(record, decisions)
    assert state.state is EvidenceContinuityState.BLOCKED_INDETERMINATE
    assert _accounting(state) == (51, 50, 0, 0, 1)
    assert state.direct_historical_contribution_allowed is False
    assert state.direct_contribution_correlation_ids == ()
    assert UNRESOLVED_INDETERMINATE_EVIDENCE in state.reason_codes
    assert MIXED_ELIGIBILITY_POPULATION in state.reason_codes
    # No majority voting and no percentages exist anywhere in the record.
    assert not any(
        isinstance(v, float) for v in state.to_dict().values())


# G. Exact accounting invariant (and fail-closed when it cannot be proven).
def test_G_exact_accounting_invariant():
    record = _mixed_record()
    states = [
        _build(record, []),
        _build(record, [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")]),
        _build(record, [
            _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
            _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
            _decide(record, "INDETERMINATE", "COR-C"),
        ]),
    ]
    for state in states:
        total, direct, reval, not_direct, indet = _accounting(state)
        assert total == direct + reval + not_direct + indet
        assert total == len(state.observation_states)
        assert state.reason_code_counts == {
            code: sum(1 for s in state.observation_states if code in s.reason_codes)
            for code in sorted({
                c for s in state.observation_states for c in s.reason_codes})
        }

    # Tampered accounting can never reload.
    good = states[2]
    bad_total = good.to_dict()
    bad_total["total_observations_assessed"] = good.total_observations_assessed + 1
    with pytest.raises(ContinuityAccountingError):
        type(good).from_dict(bad_total)

    bad_count = good.to_dict()
    bad_count["indeterminate_count"] = 0
    with pytest.raises(ContinuityAccountingError):
        type(good).from_dict(bad_count)

    bad_negative = good.to_dict()
    bad_negative["directly_eligible_count"] = -1
    with pytest.raises(ContinuityAccountingError):
        type(good).from_dict(bad_negative)


# H. Deterministic reason-code accounting (input order independent).
def test_H_deterministic_reason_code_accounting():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-B"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-C"),
    ]
    forward = _build(record, decisions)
    reversed_state = _build(record, list(reversed(decisions)))

    assert forward.reason_codes == reversed_state.reason_codes
    assert forward.reason_codes == (
        REVALIDATION_REQUIRED_EVIDENCE, MIXED_ELIGIBILITY_POPULATION)
    assert forward.reason_code_counts == reversed_state.reason_code_counts
    assert forward.reason_code_counts == {
        "INSIDE_DEPLOYED_OVERLAP": 2,
        "OUTSIDE_DEPLOYED_OVERLAP": 1,
    }
    # Keys are in canonical (sorted) order and are descriptive counts only.
    assert list(forward.reason_code_counts) == sorted(forward.reason_code_counts)
    # The whole snapshot is identical, including the deterministic identity.
    assert forward.to_dict() == reversed_state.to_dict()
    assert forward.continuity_id == reversed_state.continuity_id


# ─── I-N: provenance binding fails closed ─────────────────────────────────────


# I. Wrong candidate fails closed.
def test_I_wrong_candidate_fails_closed():
    record = _mixed_record()
    foreign = _decide(record, "INDETERMINATE", "COR-A", candidate_id="C2", expect=False)
    assert foreign.candidate_id == "C2"
    with pytest.raises(ContinuityBindingError):
        _build(record, [foreign])


# J. Wrong historical baseline / config fails closed.
def test_J_wrong_historical_baseline_or_config_fails_closed(tmp_path):
    record = _mixed_record()
    good = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")

    foreign_baseline = _decide(
        record, "INDETERMINATE", "COR-A", baseline_id="OTHER", expect=False)
    with pytest.raises(ContinuityBindingError):
        _build(record, [foreign_baseline])

    # Historical config identity is bound by the exact persisted 6.1B chain.
    store_dir = tmp_path / "impact"
    CandidateImpactHistoryStore(store_dir).append(record)
    tampered = replace(
        record, candidate_baseline_config_hash="other-config",
        from_baseline_config_hash="other-config")
    with pytest.raises(ContinuityBindingError):
        _build(tampered, [good], impact_store=CandidateImpactHistoryStore(store_dir))

    # Target config identity must be the exact N -> N+1 target config.
    with pytest.raises(ContinuityBindingError):
        _build(record, [good], target_baseline_config_hash="wrong-target-config")


# K. Wrong target baseline fails closed.
def test_K_wrong_target_baseline_fails_closed():
    record = _mixed_record()
    good = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")

    # 6.2A already refuses a mismatched target per observation: the decision
    # remains bound to the impact record's exact target and is INDETERMINATE.
    foreign_target = _decide(
        record, "INDETERMINATE", "COR-A", target="OTHER", expect=False)
    assert foreign_target.target_baseline_id == "NEW"
    assert _build(record, [foreign_target]).state is (
        EvidenceContinuityState.BLOCKED_INDETERMINATE)

    # A decision carrying a foreign target baseline can never be aggregated.
    tampered = replace(good, target_baseline_id="OTHER")
    with pytest.raises(ContinuityBindingError):
        _build(record, [tampered])


# L. Wrong impact_id (wrong transition identity) fails closed.
def test_L_wrong_impact_id_fails_closed():
    record = _mixed_record()
    tampered = replace(_decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
                       impact_id="CBI-OTHER")
    with pytest.raises(ContinuityBindingError):
        _build(record, [tampered])


# M. Decisions from different N -> N+1 transitions are never aggregated.
def test_M_mixed_transition_decisions_fail_closed():
    record = _mixed_record()
    second = _mixed_record(
        impact_id="CBI-2", candidate_baseline_id="NEW",
        candidate_baseline_config_hash="new-config",
        from_baseline_id="NEW", from_baseline_config_hash="new-config",
        to_baseline_id="NEWER", to_baseline_config_hash="newer-config",
    )
    first_decision = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    second_decision = _decide(
        second, "DIRECTLY_ELIGIBLE", "COR-B", baseline_id="NEW",
        config_hash="new-config", target="NEWER", target_hash="newer-config")

    with pytest.raises(ContinuityBindingError):
        _build(record, [first_decision, second_decision])
    with pytest.raises(ContinuityBindingError):
        _build(second, [first_decision, second_decision])


# N. Treatment mismatch and missing observation identity fail closed.
def test_N_treatment_mismatch_and_missing_identity_fail_closed():
    record = _mixed_record()
    foreign_treatment = _decide(
        record, "INDETERMINATE", "COR-A", treatment_id="T-OTHER", expect=False)
    assert foreign_treatment.treatment_id == "T-OTHER"
    with pytest.raises(ContinuityBindingError):
        _build(record, [foreign_treatment])

    good = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    # No canonical observation identity -> durable aggregation cannot prove
    # duplicate safety; never infer it from list position or object identity.
    with pytest.raises(ContinuityObservationIdentityError):
        _build(record, [replace(good, correlation_id=None)])
    with pytest.raises(ContinuityObservationIdentityError):
        _build(record, [replace(good, correlation_id="   ")])

    # Malformed eligibility status / reason codes fail closed.
    with pytest.raises(ContinuityAccountingError):
        _build(record, [replace(good, status="NOT_A_STATUS")])
    with pytest.raises(ContinuityAccountingError):
        _build(record, [replace(good, reason_codes=("",))])


# ─── O-T: duplicate-observation safety + deterministic identity ───────────────


# O. The same historical observation is never counted twice.
def test_O_same_observation_not_counted_twice():
    record = _mixed_record()
    decision = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    equal_but_separate = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    assert decision == equal_but_separate
    assert decision is not equal_but_separate

    once = _build(record, [decision])
    twice = _build(record, [decision, equal_but_separate])

    assert once.total_observations_assessed == 1
    assert twice.total_observations_assessed == 1
    assert once.continuity_id == twice.continuity_id
    assert once.to_dict() == twice.to_dict()
    assert once.state is EvidenceContinuityState.CONTINUITY_ALLOWED
    assert [s.correlation_id for s in twice.observation_states] == ["COR-A"]


# P. The same observation identity with a CONFLICTING result fails closed.
def test_P_conflicting_duplicate_observation_fails_closed():
    record = _mixed_record()
    eligible = _decide(record, "DIRECTLY_ELIGIBLE", "COR-A")
    required = _decide(record, "REVALIDATION_REQUIRED", "COR-A")
    assert eligible.correlation_id == required.correlation_id == "COR-A"

    with pytest.raises(ContinuityObservationIdentityError):
        _build(record, [eligible, required])

    # A conflicting duplicate is never silently dropped to make the remaining
    # population look usable, and the indeterminate one cannot be hidden.
    indeterminate = _decide(record, "INDETERMINATE", "COR-A")
    with pytest.raises(ContinuityObservationIdentityError):
        _build(record, [eligible, required, indeterminate])


# Q. Identical input population -> identical deterministic continuity identity.
def test_Q_deterministic_same_input_identity(tmp_path):
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
    ]
    first = _build(record, decisions)
    second = _build(record, [
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
    ])
    assert first.continuity_id == second.continuity_id
    assert first.continuity_id.startswith("ECC-")
    assert first.to_dict() == second.to_dict()
    assert first == second

    # The identity never depends on the store, the clock or the active state.
    store_dir = tmp_path / "continuity"
    persisted = assess_candidate_evidence_continuity(
        impact_record=record, decisions=decisions, continuity_dir=store_dir)
    assert persisted.continuity_id == first.continuity_id


# R. A legitimately changed population produces a DISTINCT snapshot that never
#    overwrites the earlier one.
def test_R_changed_population_produces_distinct_snapshot(tmp_path):
    record = _mixed_record()
    store_dir = tmp_path / "continuity"
    smaller = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")],
        continuity_dir=store_dir,
    )
    snapshot_before = smaller.to_dict()

    larger = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[
            _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
            _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
        ],
        continuity_dir=store_dir,
    )

    assert larger.continuity_id != smaller.continuity_id
    assert larger.state is EvidenceContinuityState.REVALIDATION_REQUIRED
    store = CandidateEvidenceContinuityStore(store_dir)
    assert len(store.list_all()) == 2
    assert store.get(smaller.continuity_id).to_dict() == snapshot_before
    assert store.get(larger.continuity_id).to_dict() == larger.to_dict()


# S. Identical persistence is idempotent.
def test_S_identical_persistence_is_idempotent(tmp_path):
    record = _mixed_record()
    decisions = [_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")]
    store_dir = tmp_path / "continuity"

    first = assess_candidate_evidence_continuity(
        impact_record=record, decisions=decisions, continuity_dir=store_dir)
    second = assess_candidate_evidence_continuity(
        impact_record=record, decisions=decisions, continuity_dir=store_dir)
    assert first.continuity_id == second.continuity_id

    store = CandidateEvidenceContinuityStore(store_dir)
    assert store.append(second) is False
    assert len(store.list_all()) == 1
    assert store.get(first.continuity_id) == first


# T. The same identity with conflicting persisted content fails closed.
def test_T_conflicting_same_identity_fails_closed(tmp_path):
    record = _mixed_record()
    store_dir = tmp_path / "continuity"
    state = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")],
        continuity_dir=store_dir,
    )

    conflicting = replace(
        state,
        total_observations_assessed=state.total_observations_assessed + 1,
        indeterminate_count=1,
    )
    assert conflicting.continuity_id == state.continuity_id
    store = CandidateEvidenceContinuityStore(store_dir)
    with pytest.raises(ContinuityConflictError):
        store.append(conflicting)

    # Original historical truth is unchanged in memory AND on disk.
    assert store.get(state.continuity_id).to_dict() == state.to_dict()
    assert CandidateEvidenceContinuityStore(store_dir).get(
        state.continuity_id).to_dict() == state.to_dict()

    # Persisted conflicting VALID content under one identity fails closed.
    other = _build(record, [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
    ])
    conflicting_valid = replace(other, continuity_id=state.continuity_id)
    assert conflicting_valid.to_dict() != state.to_dict()
    path = CandidateEvidenceContinuityStore(store_dir).path
    path.write_text(
        state.canonical_json() + "\n" + conflicting_valid.canonical_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContinuityConflictError):
        CandidateEvidenceContinuityStore(store_dir)

    # A corrupt/invalid persisted line fails closed too.
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        CandidateEvidenceContinuityStore(store_dir)


# U. Restart reloads the exact state from a brand-new store instance.
def test_U_restart_reloads_exact_state(tmp_path):
    record = _mixed_record()
    store_dir = tmp_path / "continuity"
    written = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[
            _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
            _decide(record, "INDETERMINATE", "COR-B"),
        ],
        continuity_dir=store_dir,
    )

    reloaded = CandidateEvidenceContinuityStore(store_dir).get(written.continuity_id)
    assert reloaded is not None
    assert reloaded == written
    assert reloaded.to_dict() == written.to_dict()
    assert reloaded.continuity_id == written.continuity_id
    assert reloaded.state is EvidenceContinuityState.BLOCKED_INDETERMINATE
    assert reloaded.observation_states == written.observation_states
    assert reloaded.reason_codes == written.reason_codes
    assert reloaded.reason_code_counts == written.reason_code_counts
    assert reloaded.direct_historical_contribution_allowed is False
    assert reloaded.fresh_evidence_required is True
    assert type(reloaded).from_dict(
        json.loads(written.canonical_json())) == written


# V. A later baseline transition never overwrites earlier continuity history.
def test_V_later_transition_does_not_overwrite_history(tmp_path):
    record = _mixed_record()
    second = _mixed_record(
        impact_id="CBI-2", candidate_baseline_id="NEW",
        candidate_baseline_config_hash="new-config",
        from_baseline_id="NEW", from_baseline_config_hash="new-config",
        to_baseline_id="NEWER", to_baseline_config_hash="newer-config",
    )
    store_dir = tmp_path / "continuity"
    earlier = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[_decide(record, "DIRECTLY_ELIGIBLE", "COR-A")],
        continuity_dir=store_dir,
    )
    earlier_snapshot = earlier.to_dict()

    later = assess_candidate_evidence_continuity(
        impact_record=second,
        decisions=[_decide(
            second, "DIRECTLY_ELIGIBLE", "COR-B", baseline_id="NEW",
            config_hash="new-config", target="NEWER", target_hash="newer-config")],
        continuity_dir=store_dir,
    )

    assert later.continuity_id != earlier.continuity_id
    store = CandidateEvidenceContinuityStore(store_dir)
    assert len(store.list_all()) == 2
    assert store.get(earlier.continuity_id).to_dict() == earlier_snapshot
    # Per-transition history stays addressable.
    assert [s.continuity_id for s in store.list_for_impact("CBI-1")] == [
        earlier.continuity_id]
    assert [s.continuity_id for s in store.list_for_impact("CBI-2")] == [
        later.continuity_id]
    # Candidate history keeps BOTH snapshots in a deterministic canonical order.
    listed = [s.continuity_id for s in store.list_for_candidate("C1")]
    assert set(listed) == {earlier.continuity_id, later.continuity_id}
    assert listed == [s.continuity_id for s in store.list_for_candidate("C1")]
    assert listed == [s.continuity_id for s in sorted(
        [earlier, later],
        key=lambda s: (
            s.historical_baseline_id, s.target_baseline_id, s.continuity_id))]
    # No active-baseline pointer exists anywhere in this authority.
    assert not hasattr(store, "active") and not hasattr(store, "current")


# W. Historical baseline remains N; target remains N+1; nothing is rewritten.
def test_W_historical_baseline_remains_n_target_remains_n_plus_one():
    record = _mixed_record()
    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
    ]
    snapshots = [d.to_dict() for d in decisions]
    state = _build(record, decisions)

    assert state.historical_baseline_id == "OLD"
    assert state.historical_baseline_config_hash == "old-config"
    assert state.target_baseline_id == "NEW"
    assert state.target_baseline_config_hash == "new-config"
    assert state.impact_id == "CBI-1"
    assert state.application_id == "app-1"
    assert state.candidate_treatment_id == "T-C"
    assert state.candidate_id == "C1"

    # Every assessed observation remains bound to its historical N era, and the
    # 6.2A decisions themselves are untouched (no relabelling, no migration).
    for decision in decisions:
        assert decision.historical_baseline_id == "OLD"
        assert decision.target_baseline_id == "NEW"
    assert [d.to_dict() for d in decisions] == snapshots
    assert [s.correlation_id for s in state.observation_states] == ["COR-A", "COR-B"]


# X. Direct-contribution / fresh-evidence flags obey canonical semantics.
def test_X_flag_semantics_are_canonical():
    mixed = _mixed_record()
    material = _material_record()

    allowed = _build(mixed, [_decide(mixed, "DIRECTLY_ELIGIBLE", "COR-A")])
    revalidation = _build(mixed, [
        _decide(mixed, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(mixed, "REVALIDATION_REQUIRED", "COR-B"),
    ])
    blocked = _build(mixed, [_decide(mixed, "INDETERMINATE", "COR-A")])
    none_at_all = _build(mixed, [])
    not_direct = _build(material, [
        _decide(material, "NOT_DIRECTLY_ELIGIBLE", "COR-A", symbol="EURUSD")])

    assert (allowed.direct_historical_contribution_allowed,
            allowed.fresh_evidence_required) == (True, False)
    assert (revalidation.direct_historical_contribution_allowed,
            revalidation.fresh_evidence_required) == (True, True)
    assert revalidation.direct_contribution_correlation_ids == ("COR-A",)
    assert (not_direct.direct_historical_contribution_allowed,
            not_direct.fresh_evidence_required) == (False, True)
    assert (blocked.direct_historical_contribution_allowed,
            blocked.fresh_evidence_required) == (False, True)
    assert (none_at_all.direct_historical_contribution_allowed,
            none_at_all.fresh_evidence_required) == (False, True)

    # Tampered flags can never reload.
    for tampered in (
        replace(allowed, direct_historical_contribution_allowed=False),
        replace(allowed, fresh_evidence_required=True),
        replace(blocked, direct_historical_contribution_allowed=True),
        replace(none_at_all, fresh_evidence_required=False),
    ):
        with pytest.raises(ContinuityAccountingError):
            type(allowed).from_dict(tampered.to_dict())


# ─── Y-Z: zero side effects + reconstructable historical chain ───────────────


# Y. Only continuity history is persisted; every prior authority is unchanged.
def test_Y_only_continuity_history_is_persisted(tmp_path):
    record = _mixed_record()
    impact_dir = tmp_path / "impact"
    continuity_dir = tmp_path / "continuity"
    impact_store = CandidateImpactHistoryStore(impact_dir)
    assert impact_store.append(record) is True
    impact_bytes_before = impact_store.path.read_bytes()
    record_snapshot = record.to_dict()

    decisions = [
        _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
        _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
    ]
    decision_snapshots = [d.to_dict() for d in decisions]

    state = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=decisions,
        continuity_dir=continuity_dir,
        impact_store=CandidateImpactHistoryStore(impact_dir),
    )

    # No prior authority was mutated: impact history, impact record and every
    # 6.2A decision are byte/meaning stable, and historical baseline stays N.
    assert impact_store.path.read_bytes() == impact_bytes_before
    assert CandidateImpactHistoryStore(impact_dir).get(record.impact_id) == record
    assert record.to_dict() == record_snapshot
    assert [d.to_dict() for d in decisions] == decision_snapshots
    assert [d.historical_baseline_id for d in decisions] == ["OLD", "OLD"]
    assert state.total_observations_assessed == 2

    # The ONLY persisted artefacts are the pre-existing impact history and the
    # new continuity history.
    created = sorted(
        str(p.relative_to(tmp_path)).replace("\\", "/")
        for p in tmp_path.rglob("*")
        if p.is_file()
    )
    assert created == [
        "continuity/candidate_evidence_continuity.jsonl",
        "impact/candidate_impact_history.jsonl",
    ]
    for forbidden in (
        "evaluations", "registry", "applications", "recommendations",
        "human_decisions", "decisions", "baselines", "production",
        "deployments", "candidates",
    ):
        assert not (tmp_path / forbidden).exists()

    # The continuity history holds exactly ONE self-describing record.
    lines = CandidateEvidenceContinuityStore(continuity_dir).path.read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == state.to_dict()


# Z. The historical chain is reconstructable after restart through 6.2B.
def test_Z_historical_chain_reconstructable_after_restart(tmp_path):
    record = _mixed_record()
    second = _mixed_record(
        impact_id="CBI-2", candidate_baseline_id="NEW",
        candidate_baseline_config_hash="new-config",
        from_baseline_id="NEW", from_baseline_config_hash="new-config",
        to_baseline_id="NEWER", to_baseline_config_hash="newer-config",
    )
    impact_dir = tmp_path / "impact"
    continuity_dir = tmp_path / "continuity"
    assert CandidateImpactHistoryStore(impact_dir).append(record) is True
    assert CandidateImpactHistoryStore(impact_dir).append(second) is True

    earlier = assess_candidate_evidence_continuity(
        impact_record=record,
        decisions=[
            _decide(record, "DIRECTLY_ELIGIBLE", "COR-A"),
            _decide(record, "REVALIDATION_REQUIRED", "COR-B"),
        ],
        continuity_dir=continuity_dir,
        impact_store=CandidateImpactHistoryStore(impact_dir),
    )
    later = assess_candidate_evidence_continuity(
        impact_record=second,
        decisions=[_decide(
            second, "DIRECTLY_ELIGIBLE", "COR-C", baseline_id="NEW",
            config_hash="new-config", target="NEWER", target_hash="newer-config")],
        continuity_dir=continuity_dir,
    )

    # A NEW process/store instance reconstructs the chain from persisted truth.
    chain = reconstruct_evidence_continuity_chain(
        "C1", impact_dir=impact_dir, continuity_dir=continuity_dir)

    assert chain.candidate_id == "C1"
    assert set(r.impact_id for r in chain.impact_records) == {"CBI-1", "CBI-2"}
    # Deterministic canonical ordering (never wall-clock or active-pointer
    # ordering).
    assert [r.impact_id for r in chain.impact_records] == [
        r.impact_id for r in sorted(
            [record, second],
            key=lambda r: (r.candidate_baseline_id, r.to_baseline_id, r.impact_id))]
    assert [s.continuity_id for s in chain.continuity_states] == [
        s.continuity_id for s in sorted(
            [earlier, later],
            key=lambda s: (
                s.historical_baseline_id, s.target_baseline_id, s.continuity_id))]

    # Candidate X @ N -> frozen 6.1B classification -> 6.2A conclusions.
    impact_for_earlier = chain.impact_for_state(earlier.continuity_id)
    assert impact_for_earlier.impact_id == record.impact_id
    assert impact_for_earlier.candidate_id == earlier.candidate_id
    assert impact_for_earlier.classification == "PARTIALLY_AFFECTED"
    assert impact_for_earlier.to_baseline_id == "NEW"
    assert earlier.historical_baseline_id == impact_for_earlier.candidate_baseline_id
    assert earlier.target_baseline_id == impact_for_earlier.to_baseline_id
    assert chain.continuity_states_for_impact("CBI-1") == (earlier,)
    assert [s.to_dict() for s in chain.continuity_states] == [
        s.to_dict() for s in sorted(
            [earlier, later],
            key=lambda s: (
                s.historical_baseline_id, s.target_baseline_id, s.continuity_id))]
    assert [
        (s.correlation_id, s.status) for s in earlier.observation_states
    ] == [
        ("COR-A", "DIRECTLY_ELIGIBLE"),
        ("COR-B", "REVALIDATION_REQUIRED"),
    ]
    assert earlier.direct_contribution_correlation_ids == ("COR-A",)
    assert earlier.to_dict() in chain.to_dict()["continuity_states"]
    assert chain.to_dict()["candidate_id"] == "C1"

    # A different candidate has its own (empty) chain — never mixed.
    foreign = reconstruct_evidence_continuity_chain(
        "C2", impact_dir=impact_dir, continuity_dir=continuity_dir)
    assert foreign.impact_records == ()
    assert foreign.continuity_states == ()

    # A continuity snapshot whose exact impact record is not persisted FAILS
    # CLOSED rather than being reinterpreted against another transition.
    orphan_dir = tmp_path / "orphan"
    third = _mixed_record(impact_id="CBI-3")
    orphan = assess_candidate_evidence_continuity(
        impact_record=third,
        decisions=[_decide(third, "DIRECTLY_ELIGIBLE", "COR-D")],
        continuity_dir=orphan_dir,
    )
    assert CandidateEvidenceContinuityStore(
        orphan_dir).get(orphan.continuity_id) == orphan
    with pytest.raises(ContinuityBindingError):
        reconstruct_evidence_continuity_chain(
            "C1", impact_dir=impact_dir, continuity_dir=orphan_dir)