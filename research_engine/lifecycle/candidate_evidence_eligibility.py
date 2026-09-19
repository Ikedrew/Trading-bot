"""Wave 6.2A -- baseline-transition evidence eligibility.

Answer ONE scientific question:

    Given a historical Candidate X @ Baseline N and its canonical 6.1B
    CandidateBaselineImpactRecord for the N->N+1 production transition, which
    of Candidate X's individual historical evidence observations, if any, is
    scientifically eligible to remain usable when evaluating the candidate
    under Baseline N+1?

This step defines ONLY the eligibility science. It does NOT copy, relabel,
migrate, delete, reuse, or mutate evidence. It does NOT aggregate historical
and fresh N+1 evidence (that belongs to later Wave 6.2 work). It does NOT
create evaluations, promote candidates, or transition lifecycle state.

CONTRACT (pure / read-only):
    - Inputs are immutable: the frozen CandidateBaselineImpactRecord from
      6.1B and a HistoricalObservation carrying the persisted provenance of
      ONE historical evidence observation.
    - No file I/O, no stores, no globals, no current-baseline lookup, no
      mutable-state inference, no mutation, no timestamps, no randomness.
      Identical inputs always produce an identical EvidenceEligibilityDecision.
    - The 6.1A classification + reason_codes on the impact record remain the
      SOLE scope-overlap authority. This module never re-derives scope
      mathematics.
    - Evidence identity is NEVER rewritten: the observation's baseline_id
      remains its historical N value. Eligibility determines whether the N-era
      observation may INFORM evaluation under N+1 -- it never rewrites the
      observation's historical identity.

Evidence provenance actually persisted on historical observations:
    candidate_id     -> shadow_type CANDIDATE_<id> on shadow trades,
                         propagated to evaluation rows and paired observations.
    baseline_id      -> frozen evaluation row (Wave 4C.1 provenance).
    config_hash      -> frozen evaluation row.
    symbol           -> identity.symbol on shadow trades / trade_truth,
                         propagated to paired observations.
    pattern          -> decision_snapshot.pattern on raw shadow trades
                         (NOT propagated onto the paired-pair output, but
                         persisted on the raw shadow trade record).
    treatment_id     -> frozen treatment_spec, propagated to paired observations.
    correlation_id   -> opportunity identity, propagated to paired observations.

Dimensions NOT persisted on individual observations (the model fails closed
when these would be required):
    - pattern is NOT on the paired-pair output (candidate_pairing.build_prospective_pairs).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
)

__all__ = [
    "EvidenceEligibilityStatus",
    "HistoricalObservation",
    "EvidenceEligibilityDecision",
    "assess_evidence_eligibility",
    "PROVEN_DISJOINT_SYMBOL",
    "PROVEN_DISJOINT_PATTERN",
    "OUTSIDE_DEPLOYED_OVERLAP",
    "INSIDE_DEPLOYED_OVERLAP",
    "MATERIALLY_AFFECTED_ALL_EXPOSED",
    "WRONG_CANDIDATE",
    "WRONG_HISTORICAL_BASELINE",
    "WRONG_TARGET_BASELINE",
    "WRONG_TARGET_CONFIG_HASH",
    "WRONG_OBSERVATION_CONFIG_HASH",
    "WRONG_TREATMENT_ID",
    "INDETERMINATE_CLASSIFICATION",
    "MALFORMED_IMPACT_RECORD",
    "UNRECOGNIZED_CLASSIFICATION",
    "MISSING_SYMBOL_PROVENANCE",
    "MISSING_PATTERN_PROVENANCE",
    "OBSERVATION_NOT_IN_CANDIDATE_SCOPE",
]


# -- Eligibility statuses ------------------------------------------------------


class EvidenceEligibilityStatus(str, Enum):
    """Categorical eligibility states. Fail-closed: INDETERMINATE never
    grants direct eligibility and always requires fresh evidence."""

    DIRECTLY_ELIGIBLE = "DIRECTLY_ELIGIBLE"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    NOT_DIRECTLY_ELIGIBLE = "NOT_DIRECTLY_ELIGIBLE"
    INDETERMINATE = "INDETERMINATE"


# -- Eligibility reason codes (6.2A-specific) ----------------------------------
# These explain the observation-level eligibility decision. They complement --
# not replace -- the 6.1A classification reason_codes stored on the impact
# record, which are preserved verbatim in EvidenceEligibilityDecision.

# Direct eligibility -- observation provably outside the deployed production change:
PROVEN_DISJOINT_SYMBOL = "PROVEN_DISJOINT_SYMBOL"
PROVEN_DISJOINT_PATTERN = "PROVEN_DISJOINT_PATTERN"
OUTSIDE_DEPLOYED_OVERLAP = "OUTSIDE_DEPLOYED_OVERLAP"

# Revalidation -- observation provably inside the deployed scope overlap:
INSIDE_DEPLOYED_OVERLAP = "INSIDE_DEPLOYED_OVERLAP"

# Not directly eligible -- classification proves all candidate observations exposed:
MATERIALLY_AFFECTED_ALL_EXPOSED = "MATERIALLY_AFFECTED_ALL_EXPOSED"

# Fail-closed / indeterminate:
WRONG_CANDIDATE = "WRONG_CANDIDATE"
WRONG_HISTORICAL_BASELINE = "WRONG_HISTORICAL_BASELINE"
WRONG_TARGET_BASELINE = "WRONG_TARGET_BASELINE"
WRONG_TARGET_CONFIG_HASH = "WRONG_TARGET_CONFIG_HASH"
WRONG_OBSERVATION_CONFIG_HASH = "WRONG_OBSERVATION_CONFIG_HASH"
WRONG_TREATMENT_ID = "WRONG_TREATMENT_ID"
INDETERMINATE_CLASSIFICATION = "INDETERMINATE_CLASSIFICATION"
MALFORMED_IMPACT_RECORD = "MALFORMED_IMPACT_RECORD"
UNRECOGNIZED_CLASSIFICATION = "UNRECOGNIZED_CLASSIFICATION"
MISSING_SYMBOL_PROVENANCE = "MISSING_SYMBOL_PROVENANCE"
MISSING_PATTERN_PROVENANCE = "MISSING_PATTERN_PROVENANCE"
OBSERVATION_NOT_IN_CANDIDATE_SCOPE = "OBSERVATION_NOT_IN_CANDIDATE_SCOPE"


# Fixed deterministic emission order (mirrors 6.1A _REASON_ORDER pattern).
_REASON_ORDER = (
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


def _ordered(reasons: list[str]) -> tuple[str, ...]:
    """Sort reason codes into the fixed canonical emission order."""
    rank = {code: i for i, code in enumerate(_REASON_ORDER)}
    present = [r for r in reasons if r in rank]
    return tuple(sorted(set(present), key=rank.__getitem__))


# -- Historical observation provenance -----------------------------------------


@dataclass(frozen=True)
class HistoricalObservation:
    """The persisted provenance of ONE historical evidence observation.

    Every field reflects provenance that actually exists on persisted
    historical evidence records (shadow trades / trade_truth pairs and frozen
    evaluation rows). Fields set to None indicate the dimension was not
    persisted on this observation; the eligibility logic must fail-closed
    when a required dimension is unavailable.

    Immutable -- the observation's historical baseline_id is NEVER rewritten
    by the eligibility decision.
    """

    candidate_id: str
    baseline_id: str
    config_hash: str | None = None
    symbol: str | None = None
    pattern: str | None = None
    treatment_id: str | None = None


# -- Eligibility decision ------------------------------------------------------


@dataclass(frozen=True)
class EvidenceEligibilityDecision:
    """Deterministic, pure eligibility decision for one historical observation
    evaluated against a persisted N->N+1 baseline transition.

    INTENT and PROVENANCE ONLY. Does NOT mutate evidence, baselines,
    candidates, or lifecycle state. The observation's baseline_id remains its
    historical N value; this decision only determines whether the observation
    may INFORM evaluation under N+1 -- it never rewrites historical identity.
    """

    candidate_id: str
    historical_baseline_id: str
    target_baseline_id: str
    impact_id: str
    classification: str
    status: EvidenceEligibilityStatus
    reason_codes: tuple[str, ...]
    may_contribute_directly: bool
    fresh_evidence_required: bool
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "historical_baseline_id": self.historical_baseline_id,
            "target_baseline_id": self.target_baseline_id,
            "impact_id": self.impact_id,
            "classification": self.classification,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "may_contribute_directly": self.may_contribute_directly,
            "fresh_evidence_required": self.fresh_evidence_required,
            "limitations": list(self.limitations),
        }


# -- Scope helpers -------------------------------------------------------------


def _scope_set(scope: dict[str, Any] | None, key: str) -> set[str] | None:
    """Extract one dimension (symbols/patterns) from an impact-record scope dict.

    Returns None when the dimension is BROAD (applies to everything) or when
    the scope dict itself is None. Raises ValueError on malformed structure.
    """
    if scope is None:
        return None
    val = scope.get(key)
    if val is None:
        return None  # broad
    if not isinstance(val, list) or not all(
        isinstance(s, str) and s.strip() for s in val
    ):
        raise ValueError(f"Malformed scope {key}: {val!r}")
    return set(val)


# -- Cartesian exposure logic --------------------------------------------------


def _symbol_exposed_to_deployed(
    symbol: str | None,
    dep_symbols: set[str] | None,
) -> bool | None:
    """Is the observation's symbol exposed to the deployed scope?

    Returns True if provably exposed, False if provably NOT exposed, None if
    undeterminable from available provenance.
    """
    if dep_symbols is None:
        return True  # broad deployed scope: all symbols exposed
    if symbol is None:
        return None  # symbol provenance not persisted
    return symbol in dep_symbols


def _pattern_exposed_to_deployed(
    pattern: str | None,
    dep_patterns: set[str] | None,
) -> bool | None:
    """Is the observation's pattern exposed to the deployed scope?

    Returns True if provably exposed, False if provably NOT exposed, None if
    undeterminable.
    """
    if dep_patterns is None:
        return True
    if pattern is None:
        return None
    return pattern in dep_patterns


def _observation_in_candidate_scope(
    symbol: str | None,
    pattern: str | None,
    cand_symbols: set[str] | None,
    cand_patterns: set[str] | None,
) -> bool | None:
    """Is the observation provably within the candidate's tested scope?

    Cartesian membership: in-scope = symbol-in-scope AND pattern-in-scope.
    Returns True, False, or None (undeterminable).
    """
    if cand_symbols is None:
        sym_in: bool | None = True
    elif symbol is None:
        sym_in = None
    else:
        sym_in = symbol in cand_symbols

    if cand_patterns is None:
        pat_in: bool | None = True
    elif pattern is None:
        pat_in = None
    else:
        pat_in = pattern in cand_patterns

    if sym_in is False or pat_in is False:
        return False
    if sym_in is True and pat_in is True:
        return True
    return None


def _observation_exposed_to_deployed(
    symbol: str | None,
    pattern: str | None,
    dep_symbols: set[str] | None,
    dep_patterns: set[str] | None,
) -> bool | None:
    """Determine if the observation is exposed to the deployed production change.

    Cartesian exposure: exposed = symbol-exposed AND pattern-exposed.
    Returns True (exposed), False (not exposed), or None (cannot determine).
    """
    sym_exposed = _symbol_exposed_to_deployed(symbol, dep_symbols)
    pat_exposed = _pattern_exposed_to_deployed(pattern, dep_patterns)

    if sym_exposed is False or pat_exposed is False:
        return False
    if sym_exposed is True and pat_exposed is True:
        return True
    return None


# -- Decision builders ---------------------------------------------------------


def _failed_decision(
    observation: HistoricalObservation,
    impact_record: CandidateBaselineImpactRecord,
    status: EvidenceEligibilityStatus,
    reason_codes: list[str],
    limitations: list[str],
) -> EvidenceEligibilityDecision:
    """Build a non-eligible decision (INDETERMINATE / NOT_DIRECTLY_ELIGIBLE /
    REVALIDATION_REQUIRED)."""
    may_cons = status is EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    fresh = status is not EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE
    return EvidenceEligibilityDecision(
        candidate_id=observation.candidate_id,
        historical_baseline_id=observation.baseline_id,
        target_baseline_id=impact_record.to_baseline_id,
        impact_id=impact_record.impact_id,
        classification=impact_record.classification,
        status=status,
        reason_codes=_ordered(reason_codes),
        may_contribute_directly=may_cons,
        fresh_evidence_required=fresh,
        limitations=tuple(limitations),
    )


def _eligible_decision(
    observation: HistoricalObservation,
    impact_record: CandidateBaselineImpactRecord,
    reason_codes: list[str],
    limitations: list[str],
) -> EvidenceEligibilityDecision:
    """Build a DIRECTLY_ELIGIBLE decision."""
    return EvidenceEligibilityDecision(
        candidate_id=observation.candidate_id,
        historical_baseline_id=observation.baseline_id,
        target_baseline_id=impact_record.to_baseline_id,
        impact_id=impact_record.impact_id,
        classification=impact_record.classification,
        status=EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE,
        reason_codes=_ordered(reason_codes),
        may_contribute_directly=True,
        fresh_evidence_required=False,
        limitations=tuple(limitations),
    )


# -- Pure decision logic -------------------------------------------------------


def _validate_scope_dict(scope: dict[str, Any] | None) -> None:
    """Validate that a scope is either None or a dict with keys {symbols, patterns}."""
    if scope is None:
        return
    if not isinstance(scope, dict) or set(scope) != {"symbols", "patterns"}:
        raise ValueError(f"Malformed impact scope: {scope!r}")


def assess_evidence_eligibility(
    *,
    impact_record: CandidateBaselineImpactRecord,
    observation: HistoricalObservation,
    target_baseline_id: str,
    target_baseline_config_hash: str | None = None,
) -> EvidenceEligibilityDecision:
    """Determine whether ONE historical evidence observation is scientifically
    eligible to contribute directly when evaluating Candidate X under N+1.

    Pure and deterministic: identical inputs always yield an identical
    EvidenceEligibilityDecision. Performs ZERO persistence, ZERO lifecycle
    mutation, ZERO baseline/production mutation.

    The observation's baseline_id is NEVER rewritten: this decision only
    determines whether the N-era observation may INFORM evaluation under N+1.
    """
    classification = impact_record.classification
    impact_reasons = tuple(impact_record.reason_codes)

    # -- Step 1: Classification + identity/provenance verification (fail-closed) --
    # The 6.1A classification and reason_codes on the impact record are the SOLE
    # scope-overlap authority. This module never re-derives scope mathematics.

    if classification == "INDETERMINATE":
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [INDETERMINATE_CLASSIFICATION],
            ["Impact record carries INDETERMINATE classification from 6.1A"],
        )
    if classification not in ("UNAFFECTED", "PARTIALLY_AFFECTED", "MATERIALLY_AFFECTED"):
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [UNRECOGNIZED_CLASSIFICATION],
            [f"Unrecognized classification: {classification!r}"],
        )

    if observation.candidate_id != impact_record.candidate_id:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_CANDIDATE],
            [
                f"Observation candidate_id={observation.candidate_id!r} "
                f"!= impact_record candidate_id={impact_record.candidate_id!r}"
            ],
        )

    if observation.baseline_id != impact_record.from_baseline_id:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_HISTORICAL_BASELINE],
            [
                f"Observation baseline_id={observation.baseline_id!r} "
                f"!= from_baseline_id={impact_record.from_baseline_id!r}"
            ],
        )

    if observation.baseline_id != impact_record.candidate_baseline_id:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_HISTORICAL_BASELINE],
            [
                f"Observation baseline_id={observation.baseline_id!r} "
                f"!= candidate_baseline_id={impact_record.candidate_baseline_id!r}"
            ],
        )

    if target_baseline_id != impact_record.to_baseline_id:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_TARGET_BASELINE],
            [
                f"Target baseline_id={target_baseline_id!r} "
                f"!= to_baseline_id={impact_record.to_baseline_id!r}"
            ],
        )

    if (
        target_baseline_config_hash is not None
        and target_baseline_config_hash != impact_record.to_baseline_config_hash
    ):
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_TARGET_CONFIG_HASH],
            [
                f"Target config_hash={target_baseline_config_hash!r} "
                f"!= to_baseline_config_hash="
                f"{impact_record.to_baseline_config_hash!r}"
            ],
        )

    if (
        observation.config_hash is not None
        and observation.config_hash != impact_record.candidate_baseline_config_hash
    ):
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_OBSERVATION_CONFIG_HASH],
            [
                f"Observation config_hash={observation.config_hash!r} "
                f"!= candidate_baseline_config_hash="
                f"{impact_record.candidate_baseline_config_hash!r}"
            ],
        )

    if (
        observation.treatment_id is not None
        and observation.treatment_id != impact_record.candidate_treatment_id
    ):
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [WRONG_TREATMENT_ID],
            [
                f"Observation treatment_id={observation.treatment_id!r} "
                f"!= candidate_treatment_id="
                f"{impact_record.candidate_treatment_id!r}"
            ],
        )

    # -- Step 2: Parse scopes from the impact record --
    # Scopes come ONLY from the frozen 6.1B impact record; never re-derived.
    try:
        _validate_scope_dict(impact_record.candidate_scope)
        _validate_scope_dict(impact_record.deployed_scope)
    except ValueError:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [MALFORMED_IMPACT_RECORD],
            ["Impact record scope structure is malformed"],
        )

    try:
        cand_symbols = _scope_set(impact_record.candidate_scope, "symbols")
        cand_patterns = _scope_set(impact_record.candidate_scope, "patterns")
        dep_symbols = _scope_set(impact_record.deployed_scope, "symbols")
        dep_patterns = _scope_set(impact_record.deployed_scope, "patterns")
    except ValueError:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [MALFORMED_IMPACT_RECORD],
            ["Impact record scope values are malformed"],
        )

    # -- Step 3: Classification-based eligibility dispatch --
    if classification == "MATERIALLY_AFFECTED":
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE,
            [MATERIALLY_AFFECTED_ALL_EXPOSED],
            [
                "Candidate scope fully exposed by deployed production change; "
                "historical evidence cannot serve as direct N+1 proof.",
            ],
        )

    if classification == "UNAFFECTED":
        return _unaffected_eligibility(
            observation, impact_record, cand_symbols, cand_patterns,
            dep_symbols, dep_patterns, impact_reasons,
        )

    if classification == "PARTIALLY_AFFECTED":
        return _partially_affected_eligibility(
            observation, impact_record, cand_symbols, cand_patterns,
            dep_symbols, dep_patterns, impact_reasons,
        )

    return _failed_decision(
        observation, impact_record,
        EvidenceEligibilityStatus.INDETERMINATE,
        [UNRECOGNIZED_CLASSIFICATION],
        [f"Unhandled classification: {classification!r}"],
    )


def _unaffected_eligibility(
    observation,
    impact_record,
    cand_symbols,
    cand_patterns,
    dep_symbols,
    dep_patterns,
    impact_reasons,
):
    has_disjoint_symbol = "DISJOINT_SYMBOL_SCOPE" in impact_reasons
    has_disjoint_pattern = "DISJOINT_PATTERN_SCOPE" in impact_reasons

    if has_disjoint_symbol and cand_symbols is not None:
        if observation.symbol is None:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [MISSING_SYMBOL_PROVENANCE],
                [
                    "UNAFFECTED via DISJOINT_SYMBOL_SCOPE but observation "
                    "carries no symbol provenance; cannot prove "
                    "observation-level disjointness."
                ],
            )
        if observation.symbol not in cand_symbols:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [OBSERVATION_NOT_IN_CANDIDATE_SCOPE],
                [
                    f"Observation symbol={observation.symbol!r} is not in "
                    f"candidate scope symbols={sorted(cand_symbols)!r}; "
                    "provenance inconsistency."
                ],
            )
        return _eligible_decision(
            observation, impact_record,
            [PROVEN_DISJOINT_SYMBOL],
            ["Pattern-level exposure not verified"],
        )

    if has_disjoint_pattern and cand_patterns is not None:
        if observation.pattern is None:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [MISSING_PATTERN_PROVENANCE],
                [
                    "UNAFFECTED via DISJOINT_PATTERN_SCOPE but observation "
                    "carries no pattern provenance; cannot prove "
                    "observation-level disjointness."
                ],
            )
        if observation.pattern not in cand_patterns:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [OBSERVATION_NOT_IN_CANDIDATE_SCOPE],
                [
                    f"Observation pattern={observation.pattern!r} is not in "
                    f"candidate scope patterns={sorted(cand_patterns)!r}; "
                    "provenance inconsistency."
                ],
            )
        return _eligible_decision(
            observation, impact_record,
            [PROVEN_DISJOINT_PATTERN],
            ["Symbol-level exposure not verified"],
        )

    return _failed_decision(
        observation, impact_record,
        EvidenceEligibilityStatus.INDETERMINATE,
        [MALFORMED_IMPACT_RECORD],
        [
            f"UNAFFECTED classification but impact record lacks "
            f"DISJOINT_*_SCOPE reason codes ({impact_reasons!r})"
        ],
    )
def _partially_affected_eligibility(
    observation,
    impact_record,
    cand_symbols,
    cand_patterns,
    dep_symbols,
    dep_patterns,
    impact_reasons,
):
    in_cand = _observation_in_candidate_scope(
        observation.symbol, observation.pattern,
        cand_symbols, cand_patterns,
    )
    if in_cand is False:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.INDETERMINATE,
            [OBSERVATION_NOT_IN_CANDIDATE_SCOPE],
            [
                f"Observation (symbol={observation.symbol!r}, "
                f"pattern={observation.pattern!r}) is provably outside the "
                "candidate scope; provenance inconsistency."
            ],
        )
    if in_cand is None:
        if observation.symbol is None and observation.pattern is None:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [MISSING_SYMBOL_PROVENANCE, MISSING_PATTERN_PROVENANCE],
                [
                    "PARTIALLY_AFFECTED requires observation-level scope "
                    "provenance to determine overlap membership; none "
                    "available."
                ],
            )
        if observation.symbol is None and cand_symbols is not None:
            return _failed_decision(
                observation, impact_record,
                EvidenceEligibilityStatus.INDETERMINATE,
                [MISSING_SYMBOL_PROVENANCE],
                [
                    "Candidate scope has bounded symbols but observation "
                    "carries no symbol; cannot verify candidate-scope "
                    "membership."
                ],
            )

    exposed = _observation_exposed_to_deployed(
        observation.symbol, observation.pattern,
        dep_symbols, dep_patterns,
    )

    if exposed is False:
        limitations = []
        if observation.symbol is None:
            limitations.append(
                "Symbol provenance not persisted; pattern-level analysis "
                "determines exposure"
            )
        elif observation.pattern is None:
            limitations.append(
                "Pattern provenance not persisted; symbol-level analysis "
                "determines exposure"
            )
        return _eligible_decision(
            observation, impact_record,
            [OUTSIDE_DEPLOYED_OVERLAP],
            limitations,
        )

    if exposed is True:
        return _failed_decision(
            observation, impact_record,
            EvidenceEligibilityStatus.REVALIDATION_REQUIRED,
            [INSIDE_DEPLOYED_OVERLAP],
            [
                f"Observation is provably inside the deployed scope overlap "
                f"(6.1A reason_codes={impact_reasons!r}); fresh N+1 evidence "
                "required before treating as validated under N+1."
            ],
        )

    missing = []
    limiting_reason = (
        "Cannot determine deployed-scope exposure from available provenance."
    )
    if dep_symbols is not None and observation.symbol is None:
        missing.append(MISSING_SYMBOL_PROVENANCE)
        limiting_reason = (
            "Observation carries no symbol provenance and deployed scope has "
            "bounded symbols; cannot determine symbol-level exposure."
        )
    if dep_patterns is not None and observation.pattern is None:
        missing.append(MISSING_PATTERN_PROVENANCE)
        limiting_reason = (
            "Observation carries no pattern provenance and deployed scope has "
            "bounded patterns; cannot determine pattern-level exposure."
        )
    return _failed_decision(
        observation, impact_record,
        EvidenceEligibilityStatus.INDETERMINATE,
        missing if missing else [MALFORMED_IMPACT_RECORD],
        [limiting_reason],
    )
