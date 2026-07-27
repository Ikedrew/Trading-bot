"""
Cohort Policy Registry — Maps CohortKey → ManagementPolicy using predefined rules.

STATIC ANALYSIS LAYER ONLY.
Does NOT modify execution, scoring, or runtime decisions.
"""

from __future__ import annotations

from tools.cohort_analysis.cohort_policy_types import (
    CohortKey,
    ManagementPolicy,
    CohortPolicyRecord,
)


# ─── PREDEFINED POLICIES ──────────────────────────────────────────────────────

RUNNER_MODE = ManagementPolicy(
    name="RUNNER_MODE",
    description="Let winners run with aggressive trailing. High-conviction momentum capture.",
    trailing_mode="AGGRESSIVE",
    break_even_mode="DELAYED",
    partial_tp_mode="OFF",
    rr_bias=1.3,
    notes="Best for STRONG+EARLY in trending markets where MFE typically exceeds 3R.",
)

EXTENSION_MODE = ManagementPolicy(
    name="EXTENSION_MODE",
    description="Balanced extension with light trailing and early protection.",
    trailing_mode="LIGHT",
    break_even_mode="EARLY",
    partial_tp_mode="STANDARD",
    rr_bias=1.1,
    notes="Suitable for confirmed setups with moderate MFE expectation (2-3R).",
)

PROTECT_MODE = ManagementPolicy(
    name="PROTECT_MODE",
    description="Capital protection priority. Lock profit early, reduce exposure.",
    trailing_mode="OFF",
    break_even_mode="EARLY",
    partial_tp_mode="AGGRESSIVE",
    rr_bias=0.7,
    notes="For weak or late entries where reversal risk is elevated.",
)

STANDARD_MODE = ManagementPolicy(
    name="STANDARD_MODE",
    description="Baseline 2R behaviour with light trailing and standard partials.",
    trailing_mode="LIGHT",
    break_even_mode="EARLY",
    partial_tp_mode="STANDARD",
    rr_bias=1.0,
    notes="Default fallback when cohort data is insufficient or inconclusive.",
)

REDUCED_RUNNER_MODE = ManagementPolicy(
    name="REDUCED_RUNNER_MODE",
    description="Slightly conservative runner. Trail with early BE to protect base.",
    trailing_mode="LIGHT",
    break_even_mode="EARLY",
    partial_tp_mode="STANDARD",
    rr_bias=0.95,
    notes="For strong confirmation but late timing — protect against exhaustion.",
)


# ─── COHORT → POLICY REGISTRY ─────────────────────────────────────────────────

cohort_policy_map: dict[CohortKey, ManagementPolicy] = {
    CohortKey("STRONG", "EARLY", "TRENDING"): RUNNER_MODE,
    CohortKey("STRONG", "MID", "TRENDING"): EXTENSION_MODE,
    CohortKey("STRONG", "LATE", "TRENDING"): REDUCED_RUNNER_MODE,
    CohortKey("STRONG", "EARLY", "RANGING"): EXTENSION_MODE,
    CohortKey("STRONG", "MID", "RANGING"): STANDARD_MODE,
    CohortKey("STRONG", "LATE", "RANGING"): PROTECT_MODE,
    CohortKey("WEAK", "EARLY", "TRENDING"): STANDARD_MODE,
    CohortKey("WEAK", "MID", "TRENDING"): PROTECT_MODE,
    CohortKey("WEAK", "LATE", "TRENDING"): PROTECT_MODE,
    CohortKey("WEAK", "EARLY", "RANGING"): PROTECT_MODE,
    CohortKey("WEAK", "MID", "RANGING"): PROTECT_MODE,
    CohortKey("WEAK", "LATE", "RANGING"): PROTECT_MODE,
}


# ─── FALLBACK MAPS (partial matching) ─────────────────────────────────────────

_strength_fallback: dict[str, ManagementPolicy] = {
    "STRONG": EXTENSION_MODE,
    "WEAK": PROTECT_MODE,
    "INVALID": PROTECT_MODE,
}

_timing_fallback: dict[str, ManagementPolicy] = {
    "EARLY": EXTENSION_MODE,
    "MID": STANDARD_MODE,
    "LATE": PROTECT_MODE,
}


# ─── GETTER ───────────────────────────────────────────────────────────────────

def get_policy(cohort: CohortKey) -> ManagementPolicy:
    """
    Resolve management policy for a cohort.

    Resolution order:
      1. Exact match in cohort_policy_map
      2. Fallback by confirmation_strength
      3. Fallback by entry_timing
      4. STANDARD_MODE default

    Args:
        cohort: CohortKey identifying the trade context.

    Returns:
        ManagementPolicy for this cohort.
    """
    # Exact match
    if cohort in cohort_policy_map:
        return cohort_policy_map[cohort]

    # Fallback: strength
    if cohort.confirmation_strength in _strength_fallback:
        return _strength_fallback[cohort.confirmation_strength]

    # Fallback: timing
    if cohort.entry_timing in _timing_fallback:
        return _timing_fallback[cohort.entry_timing]

    return STANDARD_MODE


# ─── DEBUG / EXPLANATION ──────────────────────────────────────────────────────

def explain_policy_assignment(cohort: CohortKey, policy: ManagementPolicy | None = None) -> str:
    """
    Explain why a cohort receives a specific policy.

    Args:
        cohort: The CohortKey being evaluated.
        policy: The assigned policy (resolved via get_policy if None).

    Returns:
        Human-readable explanation string.
    """
    if policy is None:
        policy = get_policy(cohort)

    # Determine match type
    if cohort in cohort_policy_map:
        match_type = "EXACT"
    elif cohort.confirmation_strength in _strength_fallback:
        match_type = "STRENGTH_FALLBACK"
    elif cohort.entry_timing in _timing_fallback:
        match_type = "TIMING_FALLBACK"
    else:
        match_type = "DEFAULT"

    lines = [
        f"Cohort: {cohort.confirmation_strength} + {cohort.entry_timing} + {cohort.market_regime}",
        f"Policy: {policy.name}",
        f"Match:  {match_type}",
        f"",
        f"Trailing:   {policy.trailing_mode}",
        f"Break-Even: {policy.break_even_mode}",
        f"Partial TP: {policy.partial_tp_mode}",
        f"RR Bias:    {policy.rr_bias}",
        f"",
        f"Reason: {policy.description}",
        f"Notes:  {policy.notes}",
    ]

    return "\n".join(lines)
