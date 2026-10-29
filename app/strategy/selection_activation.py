"""
Selection Activation (v1.3) — Full strategy expression pipeline.

Orchestrates the complete 1.3 pipeline:
    1. Regime classification
    2. Eligibility matrix (HARD BINARY GATE)
    3. Pattern → strategy mapping
    4. Activation scoring (soft weights)
    5. Hard gating (validation rules)
    6. Final selection (ranking)

CRITICAL RULE: Eligibility is evaluated BEFORE any scoring.
If a strategy fails eligibility → completely removed, cannot be recovered.

Design: deterministic, pure function, no side effects.
"""

from __future__ import annotations

from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal
from strategy.schema_activation import (
    ActivationResult, StrategyCandidate, RejectedStrategy, RegimeOutput,
)
from strategy.regime_activation import classify_regime
from strategy.eligibility_activation import compute_eligibility
from strategy.mapping_activation import get_candidate_strategies, get_raw_pressure
from strategy.gating_activation import extract_context, gate_reversal, gate_false_break, gate_continuation


# ─── REGIME MODULATION MULTIPLIERS ────────────────────────────────────────────

_REGIME_MULTIPLIERS = {
    "TRENDING": {"REVERSAL": 0.4, "FALSE_BREAK": 0.5, "CONTINUATION": 1.3},
    "RANGE": {"REVERSAL": 1.2, "FALSE_BREAK": 1.3, "CONTINUATION": 0.3},
    "TRANSITIONAL": {"REVERSAL": 0.5, "FALSE_BREAK": 0.5, "CONTINUATION": 0.5},
}


# ─── SOFT WEIGHT COMPUTATION ──────────────────────────────────────────────────

def _compute_reversal_weight(context: dict[str, Any]) -> float:
    w = 0.0
    if context.get("liquidity_sweep") and context.get("at_key_level") and context.get("rejection"):
        w = 0.85
    elif context.get("liquidity_sweep") and context.get("rejection"):
        w = 0.70
    elif context.get("at_key_level") and context.get("rejection"):
        w = 0.60
    elif context.get("at_key_level") or context.get("liquidity_sweep"):
        w = 0.40
    else:
        w = 0.25
    return w


def _compute_false_break_weight(context: dict[str, Any]) -> float:
    w = 0.0
    if context.get("liquidity_sweep") and context.get("rejection") and context.get("fb_return_within_3"):
        w = 0.90
    elif context.get("liquidity_sweep") and context.get("rejection"):
        w = 0.65
    elif context.get("liquidity_sweep"):
        w = 0.40
    return w


def _compute_continuation_weight(context: dict[str, Any], regime: RegimeOutput) -> float:
    w = 0.0
    aligned = context.get("swing_direction") in ("BULLISH", "BEARISH")
    bos = context.get("swing_break_confirmed", False)
    displacement = context.get("strong_displacement", False)

    if aligned and displacement and bos:
        w = 0.90
    elif aligned and displacement:
        w = 0.70
    elif aligned and bos:
        w = 0.65
    elif aligned:
        w = 0.45
    else:
        w = 0.25

    if context.get("rejection") and context.get("at_key_level"):
        w *= 0.6
    return w


# ─── MAIN ORCHESTRATOR ────────────────────────────────────────────────────────

def run_strategy_activation(
    *,
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
    swing_direction: str = "NEUTRAL",
    swing_break_confirmed: bool = False,
    market_context_regime: str | None = None,
    market_context_regime_confidence: float | None = None,
) -> ActivationResult:
    """
    Full 1.3 strategy expression pipeline.

    Pipeline order (strict):
        1. Regime classification
        2. Eligibility matrix (binary hard gate — FIRST)
        3. Pattern → strategy mapping (candidates only)
        4. Intersection: eligible AND mapped = active candidates
        5. Activation scoring (soft weights for active candidates)
        6. Hard gating (structural validation)
        7. Regime modulation + confidence dampening
        8. Final selection (highest weight among gated)

    Every rejected strategy is logged with stage + reason.

    Args:
        market_context_regime: When provided (from H4 MarketContext), this becomes
            the authoritative regime. M5 classifier is skipped.
        market_context_regime_confidence: Confidence from H4 MarketContext.
    """
    rejected: list[RejectedStrategy] = []

    # ─── STEP 1: REGIME ───────────────────────────────────────────────
    # Authority: H4 MarketContext when available, M5 classifier as fallback
    if market_context_regime is not None:
        # H4 MarketContext provides authoritative regime
        regime = RegimeOutput(
            regime=market_context_regime,
            regime_confidence=market_context_regime_confidence or 0.5,
            volatility_state="MEDIUM",
            structure_state="ORDERLY",
            trend_strength=0.0,
            range_quality=0.0,
            noise_index=0.5,
            liquidity_condition="CLEAN",
            session_context="",
            notes="source=H4_MARKET_CONTEXT",
        )
    else:
        # Fallback: M5 classifier (deprecated authority — diagnostic only)
        regime = classify_regime(candles, closed_i)

    # ─── STEP 2: ELIGIBILITY MATRIX (HARD BINARY — FIRST) ────────────
    eligibility = compute_eligibility(regime, swing_break_confirmed)
    eligible_strategies = [s for s in ("CONTINUATION", "REVERSAL", "FALSE_BREAK") if eligibility.get(s)]

    # Log eligibility rejections
    elig_reasons = eligibility.get("rejection_reasons", {})
    for strat, reason in elig_reasons.items():
        rejected.append(RejectedStrategy(strat, reason, "ELIGIBILITY"))

    # ─── STEP 3: PATTERN → STRATEGY MAPPING ───────────────────────────
    mapped_types = get_candidate_strategies(pattern)

    # ─── RAW PRESSURE (pure pattern intent, before any filtering) ─────
    raw_pressure = get_raw_pressure(pattern)

    # ─── STEP 4: INTERSECTION (eligible AND mapped) ───────────────────
    active_candidates = [s for s in mapped_types if s in eligible_strategies]

    # Log mapping rejections (mapped but not eligible)
    for s in mapped_types:
        if s not in eligible_strategies and s not in elig_reasons:
            rejected.append(RejectedStrategy(s, "not_eligible_for_regime", "MAPPING"))

    # ─── STEP 5: CONTEXT EXTRACTION ───────────────────────────────────
    context = extract_context(candles, closed_i, pattern, swing_direction, swing_break_confirmed)

    if not context.get("valid", False):
        return ActivationResult(
            regime=regime.regime,
            regime_confidence=regime.regime_confidence,
            eligible_strategies=tuple(eligible_strategies),
            mapped_strategies=tuple(mapped_types),
            gated_strategies=(),
            strategy_candidates=(),
            selected_strategy=None,
            selected_weight=0.0,
            rejected_strategies=tuple(rejected) + (RejectedStrategy("ALL", "invalid_context", "GATING"),),
            raw_pressure=raw_pressure,
            final_pressure={"REVERSAL": 0.0, "FALSE_BREAK": 0.0, "CONTINUATION": 0.0},
            context_state=context,
        )

    # ─── STEP 6: GATING + ACTIVATION SCORING ─────────────────────────
    candidates: list[StrategyCandidate] = []
    gated_strategies: list[str] = []

    for strat_type in ("REVERSAL", "FALSE_BREAK", "CONTINUATION"):
        # Skip ineligible
        if strat_type not in active_candidates:
            if strat_type not in [r.strategy for r in rejected]:
                rejected.append(RejectedStrategy(strat_type, "not_in_active_candidates", "MAPPING"))
            candidates.append(StrategyCandidate(strat_type, False, 0.0, 0.0, ("ineligible_or_unmapped",)))
            continue

        # Hard gating
        if strat_type == "REVERSAL":
            passed, gate_reason = gate_reversal(context, regime)
            weight = _compute_reversal_weight(context) if passed else 0.0
        elif strat_type == "FALSE_BREAK":
            passed, gate_reason = gate_false_break(context, regime)
            weight = _compute_false_break_weight(context) if passed else 0.0
        else:
            passed, gate_reason = gate_continuation(context, regime, pattern)
            weight = _compute_continuation_weight(context, regime) if passed else 0.0

        if not passed:
            rejected.append(RejectedStrategy(strat_type, gate_reason, "GATING"))
            candidates.append(StrategyCandidate(strat_type, False, 0.0, 0.0, (gate_reason,)))
            continue

        gated_strategies.append(strat_type)

        # ─── STEP 7: REGIME MODULATION + CONFIDENCE DAMPENING ─────────
        multiplier = _REGIME_MULTIPLIERS.get(regime.regime, {}).get(strat_type, 1.0)
        weight *= multiplier

        if regime.regime_confidence < 0.6:
            weight *= 0.5

        # Vol expanding in trend suppresses reversal
        if strat_type == "REVERSAL" and context.get("vol_expanding") and regime.regime == "TRENDING":
            weight *= 0.3

        weight = round(min(1.0, max(0.0, weight)), 3)
        allowed = weight >= 0.20

        if not allowed:
            rejected.append(RejectedStrategy(strat_type, f"weight_too_low ({weight:.3f})", "SELECTION"))

        reasons = (f"regime={regime.regime}", f"mult={multiplier}", f"conf={regime.regime_confidence:.2f}")
        candidates.append(StrategyCandidate(strat_type, allowed, weight, min(1.0, weight * 1.1), reasons))

    # ─── STEP 8: FINAL SELECTION (STRICT — NO FALLBACK) ─────────────
    # RULE: If no candidates remain → NO TRADE. No exceptions.
    # RULE: Selection NEVER overrides eligibility or gating.
    # RULE: No "pick best of weak candidates" logic allowed.
    allowed_candidates = [c for c in candidates if c.allowed]
    if allowed_candidates:
        selected = max(allowed_candidates, key=lambda c: c.activation_weight)
        selected_strategy = selected.strategy
        selected_weight = selected.activation_weight
    else:
        # HARD RULE: empty candidates = NO TRADE. No forced selection.
        selected_strategy = None
        selected_weight = 0.0
        if not rejected:
            rejected.append(RejectedStrategy("ALL", "no_valid_candidates_after_full_pipeline", "SELECTION"))

    # Build final pressure (post-eligibility + gating + modulation)
    final_pressure = {c.strategy: c.activation_weight for c in candidates}
    for s in ("REVERSAL", "FALSE_BREAK", "CONTINUATION"):
        if s not in final_pressure:
            final_pressure[s] = 0.0

    return ActivationResult(
        regime=regime.regime,
        regime_confidence=regime.regime_confidence,
        eligible_strategies=tuple(eligible_strategies),
        mapped_strategies=tuple(mapped_types),
        gated_strategies=tuple(gated_strategies),
        strategy_candidates=tuple(candidates),
        selected_strategy=selected_strategy,
        selected_weight=selected_weight,
        rejected_strategies=tuple(rejected),
        raw_pressure=raw_pressure,
        final_pressure=final_pressure,
        context_state=context,
    )
