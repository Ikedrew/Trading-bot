"""Wave 6.2A -- baseline-transition evidence eligibility tests.

Verifies the pure, read-only eligibility decision that maps a single historical
evidence observation + a 6.1B CandidateBaselineImpactRecord -> eligibility.

No AWS. No S3. No MT5. No broker. No live runtime.
"""

import pytest

from research_engine.lifecycle.candidate_evidence_eligibility import (
    EvidenceEligibilityDecision,
    EvidenceEligibilityStatus,
    HistoricalObservation,
    assess_evidence_eligibility,
    PROVEN_DISJOINT_SYMBOL,
    PROVEN_DISJOINT_PATTERN,
    OUTSIDE_DEPLOYED_OVERLAP,
    INSIDE_DEPLOYED_OVERLAP,
    MATERIALLY_AFFECTED_ALL_EXPOSED,
    WRONG_CANDIDATE,
    WRONG_HISTORICAL_BASELINE,
    WRONG_TARGET_BASELINE,
    WRONG_TARGET_CONFIG_HASH,
    WRONG_OBSERVATION_CONFIG_HASH,
    WRONG_TREATMENT_ID,
    INDETERMINATE_CLASSIFICATION,
    MALFORMED_IMPACT_RECORD,
    UNRECOGNIZED_CLASSIFICATION,
    MISSING_SYMBOL_PROVENANCE,
    MISSING_PATTERN_PROVENANCE,
    OBSERVATION_NOT_IN_CANDIDATE_SCOPE,
)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
)


def _scope(symbols=None, patterns=None):
    return {
        "symbols": sorted(symbols) if symbols else None,
        "patterns": sorted(patterns) if patterns else None,
    }


def _make_record(
    classification="UNAFFECTED",
    reason_codes=(),
    candidate_scope=None,
    deployed_scope=None,
    **kw,
):
    defaults = dict(
        impact_id="impact-1",
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


def _make_obs(candidate_id="C1", baseline_id="OLD", **kw):
    defaults = dict(
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        config_hash="old-config",
        symbol=None,
        pattern=None,
        treatment_id="T-C",
    )
    defaults.update(kw)
    return HistoricalObservation(**defaults)


def _decide(record, obs, target="NEW", target_hash="new-config"):
    return assess_evidence_eligibility(
        impact_record=record,
        observation=obs,
        target_baseline_id=target,
        target_baseline_config_hash=target_hash,
    )


# A. Exact unaffected/disjoint evidence can be DIRECTLY_ELIGIBLE
def test_A_unaffected_disjoint_symbol_directly_eligible():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="GBPUSD", pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert d.may_contribute_directly is True
    assert d.fresh_evidence_required is False
    assert PROVEN_DISJOINT_SYMBOL in d.reason_codes


# B. UNAFFECTED without sufficient observation-level provenance does NOT get
#    automatic direct eligibility
def test_B_unaffected_missing_symbol_provenance_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"], patterns=None),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=None),
    )
    obs = _make_obs(symbol=None, pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MISSING_SYMBOL_PROVENANCE in d.reason_codes
    assert d.may_contribute_directly is False


# C. PARTIALLY_AFFECTED evidence outside proven overlap can remain directly
#    eligible where provenance proves disjointness
def test_C_partially_affected_outside_overlap_directly_eligible():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD", "GBPUSD"], patterns=None),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=None),
    )
    obs = _make_obs(symbol="GBPUSD", pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert OUTSIDE_DEPLOYED_OVERLAP in d.reason_codes
    assert d.may_contribute_directly is True


# D. PARTIALLY_AFFECTED evidence inside affected overlap requires revalidation
def test_D_partially_affected_inside_overlap_revalidation_required():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD", "GBPUSD"], patterns=None),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=None),
    )
    obs = _make_obs(symbol="EURUSD", pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.REVALIDATION_REQUIRED
    assert INSIDE_DEPLOYED_OVERLAP in d.reason_codes
    assert d.fresh_evidence_required is True
    assert d.may_contribute_directly is False

# E. PARTIALLY_AFFECTED with insufficient provenance fails closed
def test_E_partially_affected_insufficient_provenance_fails_closed():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["GBPUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol=None, pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MISSING_SYMBOL_PROVENANCE in d.reason_codes
    assert MISSING_PATTERN_PROVENANCE in d.reason_codes
    assert d.may_contribute_directly is False


# F. MATERIALLY_AFFECTED historical evidence is not direct N+1 proof
def test_F_materially_affected_not_directly_eligible():
    record = _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=["FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE
    assert MATERIALLY_AFFECTED_ALL_EXPOSED in d.reason_codes
    assert d.may_contribute_directly is False
    assert d.fresh_evidence_required is True


# G. INDETERMINATE never becomes directly eligible
def test_G_indeterminate_never_directly_eligible():
    record = _make_record(
        classification="INDETERMINATE",
        reason_codes=["MALFORMED_PROVENANCE"],
        candidate_scope=None,
        deployed_scope=None,
    )
    obs = _make_obs(symbol="EURUSD", pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert INDETERMINATE_CLASSIFICATION in d.reason_codes
    assert d.may_contribute_directly is False


# H. Historical baseline_id remains N and is never rewritten to N+1
def test_H_baseline_id_never_rewritten():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"], patterns=None),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=None),
    )
    obs = _make_obs(symbol="GBPUSD", pattern=None)
    d = _decide(record, obs)
    assert d.historical_baseline_id == "OLD"
    assert obs.baseline_id == "OLD"
    assert d.target_baseline_id == "NEW"
    assert d.historical_baseline_id != d.target_baseline_id
# I. Wrong candidate fails closed
def test_I_wrong_candidate_fails_closed():
    record = _make_record(candidate_id="C1")
    obs = _make_obs(candidate_id="C2")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_CANDIDATE in d.reason_codes


# J. Wrong historical baseline fails closed
def test_J_wrong_historical_baseline_fails_closed():
    record = _make_record(from_baseline_id="OLD", candidate_baseline_id="OLD")
    obs = _make_obs(baseline_id="WRONG")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_HISTORICAL_BASELINE in d.reason_codes


# K. Wrong target baseline / wrong transition fails closed
def test_K_wrong_target_baseline_fails_closed():
    record = _make_record(to_baseline_id="NEW", to_baseline_config_hash="new-config")
    obs = _make_obs()
    d = _decide(record, obs, target="WRONG")
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_TARGET_BASELINE in d.reason_codes


def test_K2_wrong_target_config_hash_fails_closed():
    record = _make_record(to_baseline_id="NEW", to_baseline_config_hash="new-config")
    obs = _make_obs()
    d = _decide(record, obs, target="NEW", target_hash="wrong-hash")
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_TARGET_CONFIG_HASH in d.reason_codes


# L. Malformed/missing impact provenance fails closed
def test_L_malformed_scope_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope={"bad_key": ["EURUSD"]},
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MALFORMED_IMPACT_RECORD in d.reason_codes


def test_L2_malformed_scope_values_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope={"symbols": "not-a-list", "patterns": None},
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MALFORMED_IMPACT_RECORD in d.reason_codes


def test_L3_wrong_observation_config_hash_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD", config_hash="wrong-config")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_OBSERVATION_CONFIG_HASH in d.reason_codes


def test_L4_wrong_treatment_id_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
        candidate_treatment_id="T-C",
    )
    obs = _make_obs(symbol="GBPUSD", treatment_id="WRONG-TREATMENT")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert WRONG_TREATMENT_ID in d.reason_codes


def test_L5_unrecognized_classification_fails_closed():
    record = _make_record(
        classification="UNKNOWN",
        reason_codes=["SOMETHING"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert UNRECOGNIZED_CLASSIFICATION in d.reason_codes
# M. Deterministic same-input decision
def test_M_deterministic_same_input():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD", "GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d1 = _decide(record, obs)
    d2 = _decide(record, obs)
    d3 = _decide(record, obs)
    assert d1 == d2 == d3
    assert d1.to_dict() == d2.to_dict() == d3.to_dict()
    assert d1.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE


# N. Zero persistence / lifecycle / production mutation
def test_N_zero_side_effects():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs1 = _make_obs(symbol="GBPUSD")
    obs2 = _make_obs(symbol="EURUSD")
    record_snapshot = record.to_dict()
    obs1_snapshot = obs1
    obs2_snapshot = obs2

    d1 = _decide(record, obs1)
    d2 = _decide(record, obs2)

    # Observations and record unchanged (immutable, no mutation).
    assert obs1 == obs1_snapshot
    assert obs2 == obs2_snapshot
    assert record.to_dict() == record_snapshot
    # Historical baseline identity preserved.
    assert record.from_baseline_id == "OLD"
    assert record.to_baseline_id == "NEW"
    # Independent decisions; EURUSD is not in the candidate scope -> fail closed.
    assert d1.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert d2.status == EvidenceEligibilityStatus.INDETERMINATE
    assert d2.reason_codes == (OBSERVATION_NOT_IN_CANDIDATE_SCOPE,)


# O. Historical evidence remains available even when not directly eligible
def test_O_historical_evidence_remains_available():
    record = _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=["FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Hammer", treatment_id="T-C")
    d = _decide(record, obs)

    assert d.status == EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE
    # The historical identity is preserved (NOT rewritten to N+1).
    assert d.candidate_id == "C1"
    assert d.historical_baseline_id == "OLD"
    assert d.target_baseline_id == "NEW"
    assert d.impact_id == "impact-1"
    assert d.classification == "MATERIALLY_AFFECTED"
    # The historical evidence observation itself is intact and untouched.
    assert obs.baseline_id == "OLD"
    assert obs.symbol == "EURUSD"
    assert obs.pattern == "Hammer"
    assert obs.treatment_id == "T-C"
    assert obs.config_hash == "old-config"
# ─── Additional edge cases ─────────────────────────────────────────────────────


# PARTIALLY_AFFECTED with disjoint pattern (symbols same, patterns disjoint)
def test_partially_affected_disjoint_pattern_eligible():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer", "Engulfing"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Engulfing")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert OUTSIDE_DEPLOYED_OVERLAP in d.reason_codes


# PARTIALLY_AFFECTED with pattern inside deployed overlap
def test_partially_affected_pattern_inside_overlap_revalidation():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer", "Engulfing"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.REVALIDATION_REQUIRED
    assert INSIDE_DEPLOYED_OVERLAP in d.reason_codes


# UNAFFECTED via disjoint pattern (symbols same, patterns disjoint)
def test_unaffected_disjoint_pattern_directly_eligible():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_PATTERN_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Engulfing"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert PROVEN_DISJOINT_PATTERN in d.reason_codes


# UNAFFECTED via disjoint symbol but obs symbol not in candidate scope
def test_unaffected_disjoint_symbol_obs_not_in_candidate_scope():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="AUDUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert OBSERVATION_NOT_IN_CANDIDATE_SCOPE in d.reason_codes


# UNAFFECTED via disjoint pattern but obs pattern not in candidate scope
def test_unaffected_disjoint_pattern_obs_not_in_candidate_scope():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_PATTERN_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Engulfing"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern="Doji")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert OBSERVATION_NOT_IN_CANDIDATE_SCOPE in d.reason_codes


# UNAFFECTED with disjoint symbol but no pattern provenance -> symbol proof suffices
def test_unaffected_disjoint_symbol_no_pattern_provenance_still_eligible():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="GBPUSD", pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert PROVEN_DISJOINT_SYMBOL in d.reason_codes


# UNAFFECTED via disjoint pattern but no pattern provenance -> fail closed
def test_unaffected_disjoint_pattern_missing_pattern_provenance_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_PATTERN_SCOPE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Engulfing"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MISSING_PATTERN_PROVENANCE in d.reason_codes


# UNAFFECTED classification but no DISJOINT reason codes -> fail closed
def test_unaffected_without_disjoint_reasons_fails_closed():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MALFORMED_IMPACT_RECORD in d.reason_codes
# MATERIALLY_AFFECTED with broad deployed scope
def test_materially_affected_broad_deployed_scope():
    record = _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=["FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=None,
    )
    obs = _make_obs(symbol="EURUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE
    assert MATERIALLY_AFFECTED_ALL_EXPOSED in d.reason_codes


# PARTIALLY_AFFECTED: broad candidate scope, bounded deployed, obs outside
def test_partially_affected_broad_candidate_outside_deployed_eligible():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=None,
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    assert OUTSIDE_DEPLOYED_OVERLAP in d.reason_codes


# PARTIALLY_AFFECTED: broad candidate scope, bounded deployed, obs inside
def test_partially_affected_broad_candidate_inside_deployed_revalidation():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=None,
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="EURUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.REVALIDATION_REQUIRED
    assert INSIDE_DEPLOYED_OVERLAP in d.reason_codes


# PARTIALLY_AFFECTED: observation provably outside candidate scope -> fail closed
def test_partially_affected_obs_outside_candidate_scope_fails_closed():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert OBSERVATION_NOT_IN_CANDIDATE_SCOPE in d.reason_codes


# PARTIALLY_AFFECTED: broad deployed scope -> in-scope obs requires revalidation
def test_partially_affected_broad_deployed_requires_revalidation():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=None,
    )
    obs = _make_obs(symbol="EURUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.REVALIDATION_REQUIRED
    assert INSIDE_DEPLOYED_OVERLAP in d.reason_codes


# PARTIALLY_AFFECTED: candidate scope bounded on symbols but obs has no symbol
def test_partially_affected_missing_symbol_candidate_bounded_fails_closed():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=None),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=None),
    )
    obs = _make_obs(symbol=None, pattern="Hammer")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MISSING_SYMBOL_PROVENANCE in d.reason_codes


# PARTIALLY_AFFECTED: candidate pattern-bounded but obs has no pattern
def test_partially_affected_missing_pattern_provenance_fails_closed():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
    )
    obs = _make_obs(symbol="EURUSD", pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    assert MISSING_PATTERN_PROVENANCE in d.reason_codes
# to_dict round-trip
def test_to_dict_round_trip():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    dd = d.to_dict()
    assert dd["status"] == "DIRECTLY_ELIGIBLE"
    assert dd["candidate_id"] == "C1"
    assert dd["historical_baseline_id"] == "OLD"
    assert dd["target_baseline_id"] == "NEW"
    assert dd["impact_id"] == "impact-1"
    assert dd["classification"] == "UNAFFECTED"
    assert dd["may_contribute_directly"] is True
    assert dd["fresh_evidence_required"] is False
    assert dd["reason_codes"] == [PROVEN_DISJOINT_SYMBOL]


# Deterministic reason-code ordering (canonical emission order)
def test_reason_code_ordering_is_deterministic():
    record = _make_record(
        classification="PARTIALLY_AFFECTED",
        reason_codes=["PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"], patterns=["Hammer"]),
        deployed_scope=_scope(symbols=["EURUSD"], patterns=["Engulfing"]),
    )
    obs = _make_obs(symbol=None, pattern=None)
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.INDETERMINATE
    idx_sym = d.reason_codes.index(MISSING_SYMBOL_PROVENANCE)
    idx_pat = d.reason_codes.index(MISSING_PATTERN_PROVENANCE)
    assert idx_sym < idx_pat


# Impact record identity is preserved in the decision
def test_impact_identity_preserved_in_decision():
    record = _make_record(
        impact_id="impact-xyz",
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    assert d.impact_id == "impact-xyz"
    assert d.classification == "UNAFFECTED"


# Wave 6.1A/6.1B classification reason_codes are NOT copied into the eligibility
# reason_codes namespace (they stay 6.1A truth on the impact record).
def test_61a_reason_codes_not_mixed_into_eligibility_reason_codes():
    record = _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=["FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="EURUSD")
    d = _decide(record, obs)
    assert d.reason_codes == (MATERIALLY_AFFECTED_ALL_EXPOSED,)
    assert "FULL_SCOPE_EXPOSURE" not in d.reason_codes
    assert "SAME_TREATMENT_TYPE" not in d.reason_codes
    # 6.1A reason_codes still live unchanged on the impact record.
    assert record.reason_codes == ("FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE")


# Decision is a frozen dataclass (immutable value object).
def test_decision_is_immutable():
    record = _make_record(
        classification="UNAFFECTED",
        reason_codes=["DISJOINT_SYMBOL_SCOPE"],
        candidate_scope=_scope(symbols=["GBPUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="GBPUSD")
    d = _decide(record, obs)
    with pytest.raises(Exception):
        d.status = EvidenceEligibilityStatus.INDETERMINATE


# Observation is a frozen dataclass: historical baseline_id cannot be rewritten.
def test_observation_is_immutable():
    obs = _make_obs(symbol="GBPUSD")
    with pytest.raises(Exception):
        obs.baseline_id = "NEW"


# The decision for a non-eligible observation still exposes the historical
# baseline identity so downstream consumers cannot conflate it with N+1.
def test_non_eligible_decision_preserves_historical_baseline_identity():
    record = _make_record(
        classification="MATERIALLY_AFFECTED",
        reason_codes=["FULL_SCOPE_EXPOSURE", "SAME_TREATMENT_TYPE"],
        candidate_scope=_scope(symbols=["EURUSD"]),
        deployed_scope=_scope(symbols=["EURUSD"]),
    )
    obs = _make_obs(symbol="EURUSD")
    d = _decide(record, obs)
    assert d.status == EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE
    assert d.historical_baseline_id == "OLD"
    assert d.target_baseline_id == "NEW"
    # fresh evidence required under N+1, but the record of N-era evidence stands.
    assert d.fresh_evidence_required is True
    assert d.may_contribute_directly is False
