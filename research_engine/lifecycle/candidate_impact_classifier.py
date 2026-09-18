"""Wave 6.1A — pure candidate baseline-impact classification.

Classifies how much a frozen candidate treatment_spec (the experimental
treatment tested against baseline N) overlaps the frozen deployed
treatment_spec (the production change carried into baseline N+1).

CONTRACT (pure classification ONLY):
    - Inputs are the FROZEN canonical JSON treatment_spec texts exactly as
      frozen by Wave 5.3A (research_engine.lifecycle.treatment_provenance),
      bound to their treatment_id. The mutable
      CandidateRecord.change_definition is NEVER consulted.
    - No file I/O, no stores, no globals, no current-baseline lookup, no
      mutation, no timestamps. Identical inputs always produce an identical
      result.
    - Scope semantics are exactly the Wave 4D semantics
      (research_engine.lifecycle.candidate_shadow_hook.candidate_in_scope /
      canonical_candidate_scope): scope.symbols and scope.patterns, symbol
      AND pattern combine (Cartesian population), absent dimension = broad,
      exact case-sensitive membership (no folding), the top-level treatment
      "symbol" is NOT generic scope (and cannot even appear in a frozen
      spec), malformed scope fails closed.
    - Malformed/insufficient provenance fails closed to the explicit
      INDETERMINATE state with MALFORMED_PROVENANCE.
    - Conservative rules: UNAFFECTED requires PROVEN disjointness of at
      least one scope dimension; a broad candidate dimension with any
      intersection can never prove independence.

This module does NOT persist impact records, reuse/relabel evidence,
revalidate or redevelop candidates, mutate lifecycle state, or implement
NO_VIABLE_REDEVELOPMENT (later waves).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from research_engine.lifecycle.treatment_provenance import validate_treatment_spec

__all__ = [
    "ImpactClassification",
    "NormalizedScope",
    "CandidateImpactResult",
    "classify_candidate_impact",
]


class ImpactClassification(str, Enum):
    """Deterministic baseline-impact states. INDETERMINATE is the explicit
    fail-closed state for malformed/insufficient frozen provenance."""

    UNAFFECTED = "UNAFFECTED"
    PARTIALLY_AFFECTED = "PARTIALLY_AFFECTED"
    MATERIALLY_AFFECTED = "MATERIALLY_AFFECTED"
    INDETERMINATE = "INDETERMINATE"


# ─── Reason-code vocabulary (small, factual, closed set) ─────────────────────

DISJOINT_SYMBOL_SCOPE = "DISJOINT_SYMBOL_SCOPE"
DISJOINT_PATTERN_SCOPE = "DISJOINT_PATTERN_SCOPE"
PARTIAL_SCOPE_OVERLAP = "PARTIAL_SCOPE_OVERLAP"
FULL_SCOPE_EXPOSURE = "FULL_SCOPE_EXPOSURE"
BROAD_CANDIDATE_SCOPE = "BROAD_CANDIDATE_SCOPE"
BROAD_DEPLOYED_SCOPE = "BROAD_DEPLOYED_SCOPE"
SAME_TREATMENT_TYPE = "SAME_TREATMENT_TYPE"
DIFFERENT_TREATMENT_TYPE = "DIFFERENT_TREATMENT_TYPE"
MALFORMED_PROVENANCE = "MALFORMED_PROVENANCE"

# Fixed deterministic emission order for reason codes.
_REASON_ORDER = (
    DISJOINT_SYMBOL_SCOPE,
    DISJOINT_PATTERN_SCOPE,
    BROAD_CANDIDATE_SCOPE,
    BROAD_DEPLOYED_SCOPE,
    FULL_SCOPE_EXPOSURE,
    PARTIAL_SCOPE_OVERLAP,
    SAME_TREATMENT_TYPE,
    DIFFERENT_TREATMENT_TYPE,
    MALFORMED_PROVENANCE,
)


@dataclass(frozen=True)
class NormalizedScope:
    """Wave 4D normalized experiment scope. A dimension of None is BROAD
    (applies to everything); otherwise an exact, case-sensitive, sorted
    tuple of members."""

    symbols: tuple[str, ...] | None
    patterns: tuple[str, ...] | None


@dataclass(frozen=True)
class CandidateImpactResult:
    """Machine-readable classification result. Scope fields are None exactly
    when classification is INDETERMINATE (scope could not be recovered)."""

    classification: ImpactClassification
    reason_codes: tuple[str, ...]
    candidate_treatment_id: str
    candidate_treatment_type: str
    deployed_treatment_id: str
    deployed_treatment_type: str
    candidate_scope: NormalizedScope | None
    deployed_scope: NormalizedScope | None


def _frozen_value(treatment_id: object, treatment_spec: object) -> dict:
    """Parse and fail-closed validate ONE frozen provenance pair using the
    Wave 5.3A authority (canonical JSON, exact shape, id binding)."""
    if not isinstance(treatment_id, str) or not treatment_id.strip():
        raise ValueError("Missing frozen treatment_id")
    if not isinstance(treatment_spec, str) or not treatment_spec:
        raise ValueError("Missing frozen treatment_spec")
    spec = validate_treatment_spec(treatment_spec, treatment_id)
    return json.loads(spec)


def _best_effort_type(treatment_spec: object) -> str:
    """Factual best-effort change_type recovery for INDETERMINATE results.
    Never raises; empty string when unrecoverable."""
    try:
        value = json.loads(treatment_spec)
        change_type = value.get("change_type")
        return change_type if isinstance(change_type, str) else ""
    except (ValueError, TypeError):
        return ""


def _intersect_dim(cand: tuple[str, ...] | None, dep: tuple[str, ...] | None):
    """Cartesian dimension intersection. None = broad intersection, () =
    PROVEN empty (disjoint), otherwise the exact sorted common members."""
    if cand is None:
        return None if dep is None else tuple(dep)
    if dep is None:
        return tuple(cand)
    return tuple(sorted(set(cand) & set(dep)))


def _covers(cand: tuple[str, ...] | None, dep: tuple[str, ...] | None) -> bool:
    """True when the deployed dimension covers the WHOLE candidate dimension
    (a broad deployed dimension covers everything; a broad candidate
    dimension is only ever covered by a broad deployed one)."""
    if dep is None:
        return True
    if cand is None:
        return False
    return set(cand) <= set(dep)


def _ordered(reasons: list[str]) -> tuple[str, ...]:
    rank = {code: i for i, code in enumerate(_REASON_ORDER)}
    return tuple(sorted(set(reasons), key=rank.__getitem__))


def _malformed_result(candidate_treatment_id: object, candidate_treatment_spec: object,
                      deployed_treatment_id: object, deployed_treatment_spec: object) -> CandidateImpactResult:
    return CandidateImpactResult(
        classification=ImpactClassification.INDETERMINATE,
        reason_codes=(MALFORMED_PROVENANCE,),
        candidate_treatment_id=candidate_treatment_id if isinstance(candidate_treatment_id, str) else "",
        candidate_treatment_type=_best_effort_type(candidate_treatment_spec),
        deployed_treatment_id=deployed_treatment_id if isinstance(deployed_treatment_id, str) else "",
        deployed_treatment_type=_best_effort_type(deployed_treatment_spec),
        candidate_scope=None,
        deployed_scope=None,
    )


def _normalized_scope(scope: dict) -> NormalizedScope:
    symbols = scope["symbols"]
    patterns = scope["patterns"]
    return NormalizedScope(
        symbols=tuple(symbols) if symbols else None,
        patterns=tuple(patterns) if patterns else None,
    )


def classify_candidate_impact(
    candidate_treatment_id: object,
    candidate_treatment_spec: object,
    deployed_treatment_id: object,
    deployed_treatment_spec: object,
) -> CandidateImpactResult:
    """Classify a frozen candidate treatment against a frozen deployed
    treatment. Pure and deterministic; raises nothing.

    UNAFFECTED           — at least one scope dimension is PROVEN disjoint.
    PARTIALLY_AFFECTED   — intersection exists but only part of the
                           candidate's (non-broad) scope is covered.
    MATERIALLY_AFFECTED  — the candidate's full scope is exposed, OR a
                           bounded deployment lies inside a broad candidate
                           dimension (independence cannot be proven).
    INDETERMINATE        — malformed/insufficient frozen provenance
                           (fail closed).
    """
    try:
        cand_value = _frozen_value(candidate_treatment_id, candidate_treatment_spec)
        dep_value = _frozen_value(deployed_treatment_id, deployed_treatment_spec)
    except (ValueError, TypeError, KeyError):
        return _malformed_result(
            candidate_treatment_id, candidate_treatment_spec,
            deployed_treatment_id, deployed_treatment_spec,
        )

    cand_scope = _normalized_scope(cand_value["scope"])
    dep_scope = _normalized_scope(dep_value["scope"])

    reasons = [
        SAME_TREATMENT_TYPE if cand_value["change_type"] == dep_value["change_type"]
        else DIFFERENT_TREATMENT_TYPE,
    ]

    sym_int = _intersect_dim(cand_scope.symbols, dep_scope.symbols)
    pat_int = _intersect_dim(cand_scope.patterns, dep_scope.patterns)

    if sym_int == () or pat_int == ():
        # Cartesian proven disjointness: no opportunity can be in both
        # populations, so independence IS proven.
        if sym_int == ():
            reasons.append(DISJOINT_SYMBOL_SCOPE)
        if pat_int == ():
            reasons.append(DISJOINT_PATTERN_SCOPE)
        classification = ImpactClassification.UNAFFECTED
    else:
        # Intersection exists; independence can no longer be proven.
        if dep_scope.symbols is None or dep_scope.patterns is None:
            reasons.append(BROAD_DEPLOYED_SCOPE)
        if cand_scope.symbols is None or cand_scope.patterns is None:
            reasons.append(BROAD_CANDIDATE_SCOPE)
        # A BOUNDED deployment region lying inside an UNBOUNDED candidate
        # dimension: exposure can never be quantified, so independence
        # cannot safely be proven (conservative, never UNAFFECTED).
        broad_exposed = (
            (cand_scope.symbols is None and dep_scope.symbols is not None)
            or (cand_scope.patterns is None and dep_scope.patterns is not None)
        )
        covered = _covers(cand_scope.symbols, dep_scope.symbols) and _covers(
            cand_scope.patterns, dep_scope.patterns)
        if broad_exposed:
            classification = ImpactClassification.MATERIALLY_AFFECTED
        elif covered:
            reasons.append(FULL_SCOPE_EXPOSURE)
            classification = ImpactClassification.MATERIALLY_AFFECTED
        else:
            reasons.append(PARTIAL_SCOPE_OVERLAP)
            classification = ImpactClassification.PARTIALLY_AFFECTED

    return CandidateImpactResult(
        classification=classification,
        reason_codes=_ordered(reasons),
        candidate_treatment_id=cand_value["treatment_id"],
        candidate_treatment_type=cand_value["change_type"],
        deployed_treatment_id=dep_value["treatment_id"],
        deployed_treatment_type=dep_value["change_type"],
        candidate_scope=cand_scope,
        deployed_scope=dep_scope,
    )

