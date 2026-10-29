"""
Strategy Eligibility Matrix — Binary hard gate for strategy existence.

This is the FIRST filter in the 1.3 pipeline.
It determines which strategies are allowed to EXIST in the decision space.

If a strategy fails eligibility:
    → completely removed from pipeline
    → cannot be recovered by scoring or weighting
    → no exceptions

This is NOT weighted. It is TRUE/FALSE only.

Evaluated BEFORE: mapping, gating, scoring, selection.

Design: deterministic, pure function, no side effects.
"""

from __future__ import annotations

from strategy.schema_activation import RegimeOutput


# ─── ELIGIBILITY MATRIX ───────────────────────────────────────────────────────
# Binary: TRUE = strategy can exist, FALSE = strategy is removed

_ELIGIBILITY_MATRIX: dict[str, dict[str, bool]] = {
    "TRENDING": {
        "CONTINUATION": True,
        "REVERSAL": True,
        "FALSE_BREAK": True,
    },
    "RANGE": {
        "CONTINUATION": False,   # Blocked unless BOS confirmed (handled below)
        "REVERSAL": True,
        "FALSE_BREAK": True,
    },
    "TRANSITIONAL": {
        "CONTINUATION": True,    # Dampened later, but eligible
        "REVERSAL": True,
        "FALSE_BREAK": True,
    },
}


def compute_eligibility(
    regime: RegimeOutput,
    swing_break_confirmed: bool = False,
) -> dict[str, bool | str]:
    """
    Compute binary eligibility for each strategy type.

    Args:
        regime: Regime classification output
        swing_break_confirmed: Whether BOS has been confirmed

    Returns:
        {
            "CONTINUATION": True/False,
            "REVERSAL": True/False,
            "FALSE_BREAK": True/False,
            "rejection_reasons": {strategy: reason, ...}
        }
    """
    effective_regime = regime.regime

    # Hard confidence gate: force TRANSITIONAL if low confidence
    if regime.regime_confidence < 0.6:
        effective_regime = "TRANSITIONAL"

    matrix = _ELIGIBILITY_MATRIX.get(effective_regime, _ELIGIBILITY_MATRIX["TRANSITIONAL"])

    eligibility: dict[str, bool] = {}
    rejection_reasons: dict[str, str] = {}

    for strategy, base_eligible in matrix.items():
        eligible = base_eligible

        # Special override: CONTINUATION allowed in RANGE if BOS confirmed
        if strategy == "CONTINUATION" and effective_regime == "RANGE" and swing_break_confirmed:
            eligible = True

        # Very low confidence: only REVERSAL allowed
        if regime.regime_confidence < 0.4 and strategy != "REVERSAL":
            eligible = False
            rejection_reasons[strategy] = "regime_confidence_below_0.4 (only REVERSAL allowed)"

        # Record rejection reason
        if not eligible and strategy not in rejection_reasons:
            if strategy == "CONTINUATION" and effective_regime == "RANGE":
                rejection_reasons[strategy] = "RANGE_regime_no_BOS"
            else:
                rejection_reasons[strategy] = f"eligibility_matrix_{effective_regime}"

        eligibility[strategy] = eligible

    return {
        "CONTINUATION": eligibility.get("CONTINUATION", False),
        "REVERSAL": eligibility.get("REVERSAL", True),
        "FALSE_BREAK": eligibility.get("FALSE_BREAK", True),
        "effective_regime": effective_regime,
        "rejection_reasons": rejection_reasons,
    }
