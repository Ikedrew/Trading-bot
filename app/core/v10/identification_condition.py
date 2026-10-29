"""
passed_identification_condition — Additive Data/Shadow derivation.

FIXED DECISION (§5.2):
    passed_identification_condition =
        (identification_verdict == VALID)
        AND (at least one horizon is eligible)

This module is a PURE, SIDE-EFFECT-FREE interpretation of already-produced
values. It does NOT:
    - Re-compute identification (see core/v10/opportunity_engine.py:assess_opportunity)
    - Re-compute horizon classification (see core/horizon/horizon_classifier.py:classify_horizons)
    - Decide which pattern is primary
    - Influence live trading decisions, risk, guards, or execution
    - Create, replace, or alter any existing identifier

Semantics are fixed and non-negotiable:
    VALID     + >=1 eligible horizon -> True
    VALID     +  0 eligible horizons  -> False
    WATCHING  + any                  -> False
    INVALID   + any                  -> False

WATCHING is NOT treated as passed. Eligibility is taken only from the explicit
sequence produced by classify_horizons — it is never inferred from confidence
or scores.
"""

from __future__ import annotations

from typing import Sequence

# Verdict vocabulary produced by core/v10/opportunity_engine.py:assess_opportunity
IDENTIFICATION_VERDICT_VALID = "VALID"
IDENTIFICATION_VERDICT_WATCHING = "WATCHING"
IDENTIFICATION_VERDICT_INVALID = "INVALID"


def compute_passed_identification_condition(
    *,
    identification_verdict: str,
    eligible_horizons: Sequence[str],
) -> bool:
    """
    Return whether an opportunity passed the Shadow identification condition.

    Exactly: (identification_verdict == VALID) AND (len(eligible_horizons) > 0).

    Args:
        identification_verdict: Already-produced verdict string
            (core/v10/opportunity_engine.py OpportunityAssessment.opportunity_state;
            one of VALID / WATCHING / INVALID). NOT recomputed here.
        eligible_horizons: Already-produced list of eligible horizon names
            (core/horizon/horizon_classifier.py HorizonClassificationResult.eligible_horizons).
            NOT recomputed here.

    Returns:
        True only for VALID + >=1 eligible horizon; False otherwise.

    Pure: does not mutate inputs, does not read broker/trading state, does not
    persist, and is intentionally unaware of execution/risk/policy.
    """
    return (
        identification_verdict == IDENTIFICATION_VERDICT_VALID
        and len(eligible_horizons) > 0
    )
