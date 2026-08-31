"""V10 Strategy Engine — Classifies opportunities into strategy families.

Consumes V10MarketState + OpportunityAssessment.
Produces StrategyDecision.

Evaluates each strategy family's conditions against market state.
If multiple match, uses priority order to select the winner.

Does NOT:
  - Create entries, stops, or targets
  - Execute trades
  - Override H1/H4 directional authority with M5 data
"""

from __future__ import annotations

from typing import Any

from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import (
    StrategyDecision, StrategyFamily, STRATEGY_PRIORITY,
)


def select_strategy(
    state: V10MarketState,
    opportunity: OpportunityAssessment,
    lineage: dict[str, Any] | None = None,
) -> StrategyDecision:
    """
    Select the best-fitting strategy family for the given opportunity.

    Only operates on VALID or WATCHING opportunities.
    Returns NONE for INVALID opportunities.

    Args:
        lineage: OPTIONAL persistence context (never affects selection).
            Recognised keys: canonical_opportunity_id, observation_id,
            decision_id, correlation_id, cycle_id, entity_id.
            When absent, canonical IDs are computed from state/opportunity.
            This parameter is observational only — it does NOT alter
            evaluation, sorting, or winner selection in any way.
    """
    if opportunity.opportunity_state == "INVALID":
        return StrategyDecision(
            opportunity_id=opportunity.observation_id,
            symbol=state.symbol,
            timestamp_utc=state.timestamp_utc,
            strategy_family=StrategyFamily.NONE.value,
            directional_context=opportunity.directional_bias,
            strategy_confidence=0.0,
            reasoning=["Opportunity state is INVALID — no strategy applicable"],
        )

    # Evaluate all strategy families
    candidates: list[tuple[StrategyFamily, float, list[str], dict[str, bool]]] = []

    # 1. LIQUIDITY_SWEEP_REVERSAL
    conf, reasons, conditions = _evaluate_liquidity_sweep(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.LIQUIDITY_SWEEP_REVERSAL, conf, reasons, conditions))

    # 2. FALSE_BREAK
    conf, reasons, conditions = _evaluate_false_break(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.FALSE_BREAK, conf, reasons, conditions))

    # 3. TREND_CONTINUATION
    conf, reasons, conditions = _evaluate_trend_continuation(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.TREND_CONTINUATION, conf, reasons, conditions))

    # 4. BREAKOUT_EXPANSION
    conf, reasons, conditions = _evaluate_breakout_expansion(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.BREAKOUT_EXPANSION, conf, reasons, conditions))

    # 5. MEAN_REVERSION
    conf, reasons, conditions = _evaluate_mean_reversion(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.MEAN_REVERSION, conf, reasons, conditions))

    # 6. RANGE_REACTION
    conf, reasons, conditions = _evaluate_range_reaction(state, opportunity)
    if conf > 0:
        candidates.append((StrategyFamily.RANGE_REACTION, conf, reasons, conditions))

    # Select winner by priority (if tied on confidence, priority wins)
    if not candidates:
        return StrategyDecision(
            opportunity_id=opportunity.observation_id,
            symbol=state.symbol,
            timestamp_utc=state.timestamp_utc,
            strategy_family=StrategyFamily.NONE.value,
            directional_context=opportunity.directional_bias,
            strategy_confidence=0.0,
            reasoning=["No strategy family conditions met"],
        )

    # Sort: first by priority index (lower = higher priority), then by confidence desc
    def sort_key(item):
        family, conf, _, _ = item
        try:
            priority_idx = STRATEGY_PRIORITY.index(family)
        except ValueError:
            priority_idx = 99
        return (priority_idx, -conf)

    candidates.sort(key=sort_key)
    winner, confidence, reasoning, conditions = candidates[0]

    # ─── PERSIST ALL CANDIDATES (observational only) ──────────────
    # Persist the complete post-sort candidate set BEFORE returning the
    # winner. The list order IS the rank — no re-ranking occurs here.
    # Persistence failure never affects the returned StrategyDecision.
    try:
        from core.persistence.strategy_candidates_writer import (
            build_candidate_records,
            persist_strategy_candidates,
        )
        _cand_records = build_candidate_records(
            candidates=candidates,
            winner_family=winner.value,
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            lineage=lineage,
        )
        persist_strategy_candidates(candidates=_cand_records)
    except Exception:
        pass  # Candidate persistence must NEVER affect strategy selection
    # ─── END CANDIDATE PERSISTENCE ────────────────────────────────

    return StrategyDecision(
        opportunity_id=opportunity.observation_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        strategy_family=winner.value,
        directional_context=opportunity.directional_bias,
        strategy_confidence=confidence,
        reasoning=reasoning,
        supporting_conditions=conditions,
    )


# ═══════════════════════════════════════════════════════════════
# STRATEGY FAMILY EVALUATORS
# Each returns (confidence, reasoning, conditions)
# confidence = 0 means "does not qualify"
# ═══════════════════════════════════════════════════════════════

_Result = tuple[float, list[str], dict[str, bool]]


def _evaluate_liquidity_sweep(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """LIQUIDITY_SWEEP_REVERSAL: liquidity taken + rejection + structure shift."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: liquidity involvement (liquidity above/below present + at zone)
    has_liquidity = state.location.liquidity_above or state.location.liquidity_below
    conditions["liquidity_present"] = has_liquidity
    if has_liquidity:
        score += 0.25
        reasons.append("Liquidity target present")

    # Required: rejection after liquidity event
    has_rejection = state.m5.rejection_present and state.m5.rejection_strength_atr >= 0.5
    conditions["rejection_after_sweep"] = has_rejection
    if has_rejection:
        score += 0.30
        reasons.append(f"Rejection: {state.m5.rejection_direction} ({state.m5.rejection_strength_atr:.1f} ATR)")

    # Required: structure shift (CHoCH or internal BOS opposing prior trend)
    has_shift = state.h1.choch_detected or (state.m15.internal_choch)
    conditions["structure_shift"] = has_shift
    if has_shift:
        score += 0.30
        reasons.append("Structure shift detected (CHoCH)")

    # Supporting: displacement
    if state.m15.displacement_present:
        score += 0.15
        conditions["displacement"] = True
        reasons.append("Displacement confirmed")
    else:
        conditions["displacement"] = False

    # Supporting: zone reaction
    conditions["zone_reaction"] = state.location.inside_institutional_zone
    if state.location.inside_institutional_zone:
        score += 0.10

    # Must meet all required conditions
    required_met = has_liquidity and has_rejection and has_shift
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions


def _evaluate_false_break(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """FALSE_BREAK: breakout attempt + failure + reclaim."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: at a level that was broken (session high/low, equal highs/lows)
    near_session_extreme = (
        state.h1.session_high > 0 and state.h1.session_low > 0
    )
    # Check if a key structural level existed (expanded: swing levels + liquidity flags)
    level_existed = (
        state.location.liquidity_above or
        state.location.liquidity_below or
        state.h1.swing_high > 0 or
        state.h1.swing_low > 0
    )
    conditions["breakout_attempted"] = level_existed

    # Required: failure to continue (rejection present)
    has_rejection = state.m5.rejection_present
    conditions["failure_to_continue"] = has_rejection

    # Required: reclaim of previous level (price back inside range)
    # Proxy: range_position between 0.2 and 0.8 (not at extreme anymore)
    reclaimed = 0.2 < state.location.range_position < 0.8
    conditions["reclaim_level"] = reclaimed

    if has_rejection:
        score += 0.30
        reasons.append("Rejection after break attempt")
    if level_existed:
        score += 0.30
        reasons.append("Structural level existed (breakable boundary)")
    if reclaimed:
        score += 0.20
        reasons.append("Level reclaimed — back inside range")

    # Supporting: rejection wick strength
    if state.m5.rejection_strength_atr >= 0.7:
        score += 0.15
        conditions["strong_rejection"] = True
        reasons.append("Strong rejection wick")
    else:
        conditions["strong_rejection"] = False

    required_met = level_existed and has_rejection and reclaimed
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions


def _evaluate_trend_continuation(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """TREND_CONTINUATION: strong HTF trend + pullback + continuation structure."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: strong HTF trend
    strong_trend = state.h4.trend in ("BULLISH", "BEARISH") and state.h4.trend_strength >= 0.5
    conditions["strong_htf_trend"] = strong_trend
    if strong_trend:
        score += 0.25
        reasons.append(f"H4 trend: {state.h4.trend} (strength={state.h4.trend_strength:.2f})")

    # Required: H1 directional structure aligned with H4
    h1_aligned = (
        state.h1.dominant_trend == state.h4.trend and
        state.h1.bos_confirmed and
        state.h1.bos_direction == state.h4.trend
    )
    conditions["h1_structure_aligned"] = h1_aligned
    if h1_aligned:
        score += 0.25
        reasons.append(f"H1 BOS {state.h1.bos_direction} aligned with H4")

    # Required: M15 pullback or refinement
    has_pullback = state.m15.pullback_active
    conditions["m15_pullback"] = has_pullback
    if has_pullback:
        score += 0.20
        reasons.append(f"M15 pullback (depth={state.m15.pullback_depth_atr:.1f} ATR)")

    # Supporting: pullback exhaustion signal (M5 rejection in trend direction)
    pullback_exhaustion = (
        state.m5.rejection_present and state.m5.rejection_direction == state.h4.trend
    )
    conditions["continuation_structure"] = pullback_exhaustion
    if pullback_exhaustion:
        score += 0.15
        reasons.append(f"M5 rejection {state.m5.rejection_direction} confirms pullback ending")

    # Supporting: trending regime
    conditions["trend_regime"] = state.regime.regime == "TRENDING"
    if state.regime.regime == "TRENDING":
        score += 0.10

    required_met = strong_trend and h1_aligned and has_pullback
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions


def _evaluate_breakout_expansion(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """BREAKOUT_EXPANSION: compression + volatility expansion + displacement."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: compression period (low volatility environment)
    compressed = state.regime.volatility_state == "CONTRACTION"
    conditions["compression_period"] = compressed
    if compressed:
        score += 0.25
        reasons.append("Volatility contraction detected")

    # Required: displacement (institutional expansion trigger)
    has_displacement = state.m15.displacement_present
    conditions["displacement"] = has_displacement
    if has_displacement:
        score += 0.40
        reasons.append(f"Displacement: {state.m15.displacement_direction} ({state.m15.displacement_magnitude_atr:.1f} ATR)")

    # Supporting: strong displacement magnitude
    conditions["strong_movement"] = state.m15.displacement_magnitude_atr >= 1.5
    if state.m15.displacement_magnitude_atr >= 1.5:
        score += 0.20
        reasons.append("Strong displacement (>1.5 ATR)")

    required_met = compressed and has_displacement
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions


def _evaluate_mean_reversion(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """MEAN_REVERSION: neutral HTF + range extreme + zone reaction."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: neutral or ranging environment
    htf_neutral = (
        state.htf_alignment.macro_bias == "NEUTRAL" or
        state.h4.trend == "NEUTRAL" or
        state.h4.trend_strength < 0.3 or
        state.regime.regime in ("RANGING", "NEUTRAL", "")
    )
    conditions["htf_neutral"] = htf_neutral
    if htf_neutral:
        score += 0.25
        reasons.append("HTF neutral/ranging environment")

    # Required: price near range boundary (premium or discount)
    # Guard: range_position == 0 is missing data, not a legitimate extreme
    at_extreme = (
        state.location.range_position >= 0.70 or
        (state.location.range_position <= 0.30 and state.location.range_position > 0)
    )
    conditions["at_range_boundary"] = at_extreme
    if at_extreme:
        score += 0.25
        reasons.append(f"At range extreme (position={state.location.range_position:.2f})")

    # Required: structural evidence at this level
    # (replaces dead inside_institutional_zone with available V10 structural signals)
    has_structural_level = (
        state.h1.swing_high > 0 or
        state.h1.swing_low > 0 or
        state.h1.bos_level > 0
    ) and state.h1.structural_clarity >= 0.5
    conditions["structural_level"] = has_structural_level
    if has_structural_level:
        score += 0.20
        reasons.append(f"Structural level present (clarity={state.h1.structural_clarity:.2f})")

    # Supporting: rejection / weak momentum
    if state.m5.rejection_present:
        score += 0.15
        conditions["rejection"] = True
        reasons.append("Rejection present")
    else:
        conditions["rejection"] = False

    # Supporting: momentum weak or opposing
    weak_momentum = state.regime.momentum_strength < 0.4
    conditions["weak_momentum"] = weak_momentum
    if weak_momentum:
        score += 0.10
        reasons.append("Weak momentum (supports reversion)")

    required_met = htf_neutral and at_extreme and has_structural_level
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions


def _evaluate_range_reaction(state: V10MarketState, opp: OpportunityAssessment) -> _Result:
    """RANGE_REACTION: established range + price at extreme + zone."""
    conditions: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    # Required: ranging regime
    is_ranging = state.regime.regime in ("RANGING", "NEUTRAL", "")
    conditions["ranging_regime"] = is_ranging
    if is_ranging:
        score += 0.20
        reasons.append("Ranging regime")

    # Required: at range extreme
    # Guard: range_position == 0 is missing data, not a legitimate extreme
    at_extreme = (
        state.location.range_position >= 0.70 or
        (state.location.range_position <= 0.30 and state.location.range_position > 0)
    )
    conditions["at_range_extreme"] = at_extreme
    if at_extreme:
        score += 0.25
        reasons.append(f"Range extreme (pos={state.location.range_position:.2f})")

    # Required: established range with clear defended boundaries
    # (replaces dead inside_institutional_zone / zone_quality with structural clarity)
    established_range = (
        state.h1.structural_clarity >= 0.7 and
        state.h1.swing_high > 0 and
        state.h1.swing_low > 0
    )
    conditions["established_range"] = established_range
    if established_range:
        score += 0.20
        reasons.append(f"Established range (clarity={state.h1.structural_clarity:.2f}, both boundaries defined)")

    # Supporting: live boundary defence (M5 rejection at extreme)
    if state.m5.rejection_present and at_extreme:
        score += 0.15
        conditions["boundary_defence"] = True
        reasons.append("M5 rejection at boundary (active defence)")
    else:
        conditions["boundary_defence"] = False

    # Supporting: mean-reverting behaviour
    if state.regime.momentum_strength < 0.3:
        score += 0.10
        conditions["mean_reverting"] = True
    else:
        conditions["mean_reverting"] = False

    required_met = is_ranging and at_extreme and established_range
    if not required_met:
        return 0.0, [], conditions

    return min(score, 1.0), reasons, conditions
