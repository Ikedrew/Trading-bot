"""
Strategy Knowledge Library — Registry.

Single source of truth for all strategy definitions.
Pure knowledge. No calculations. No execution logic.

Contains:
    - 6 family definitions
    - 17 strategy definitions
    - Query helpers (by family, by phase, by regime)
"""

from __future__ import annotations

from core.strategies.library.models import (
    ConfidenceLevel,
    EvidenceStatus,
    FamilyDefinition,
    StrategyDefinition,
    StrategyFamily,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FAMILY DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

FAMILY_DEFINITIONS: dict[StrategyFamily, FamilyDefinition] = {
    StrategyFamily.REVERSAL: FamilyDefinition(
        family=StrategyFamily.REVERSAL,
        hypothesis=(
            "Price has moved too far from equilibrium and "
            "opposing pressure is entering."
        ),
        description=(
            "Strategies exploiting exhaustion at extremes where the dominant "
            "move loses conviction and opposing participants gain control."
        ),
        typical_phases=("REVERSAL", "EXHAUSTION", "CONSOLIDATION"),
        typical_regimes=("RANGING", "TRANSITIONAL"),
    ),
    StrategyFamily.MOMENTUM: FamilyDefinition(
        family=StrategyFamily.MOMENTUM,
        hypothesis=(
            "The market has discovered directional agreement "
            "and expansion is likely."
        ),
        description=(
            "Strategies exploiting strong directional conviction where "
            "institutional participants commit to a direction."
        ),
        typical_phases=("IMPULSE",),
        typical_regimes=("TRENDING",),
    ),
    StrategyFamily.CONTINUATION: FamilyDefinition(
        family=StrategyFamily.CONTINUATION,
        hypothesis=(
            "The trend remains healthy and retracement "
            "creates opportunity."
        ),
        description=(
            "Strategies exploiting pullbacks within established trends "
            "where the dominant direction resumes after a measured pause."
        ),
        typical_phases=("PULLBACK",),
        typical_regimes=("TRENDING",),
    ),
    StrategyFamily.BREAKOUT: FamilyDefinition(
        family=StrategyFamily.BREAKOUT,
        hypothesis="Compression releases into expansion.",
        description=(
            "Strategies exploiting range resolution where accumulated "
            "energy from consolidation produces a strong directional move."
        ),
        typical_phases=("CONSOLIDATION", "IMPULSE"),
        typical_regimes=("RANGING", "TRANSITIONAL"),
    ),
    StrategyFamily.MEAN_REVERSION: FamilyDefinition(
        family=StrategyFamily.MEAN_REVERSION,
        hypothesis=(
            "Price has deviated too far from fair value "
            "and tends to normalise."
        ),
        description=(
            "Strategies exploiting statistical extremes where price "
            "has overextended relative to its mean and reversion is probable."
        ),
        typical_phases=("EXHAUSTION", "REVERSAL"),
        typical_regimes=("RANGING",),
    ),
    StrategyFamily.STRUCTURE: FamilyDefinition(
        family=StrategyFamily.STRUCTURE,
        hypothesis="Market control has changed.",
        description=(
            "Strategies exploiting structural shifts in market control "
            "such as break of structure or change of character."
        ),
        typical_phases=("IMPULSE", "REVERSAL"),
        typical_regimes=("TRENDING", "TRANSITIONAL"),
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_LIBRARY: dict[str, StrategyDefinition] = {}


def _register(s: StrategyDefinition) -> None:
    STRATEGY_LIBRARY[s.strategy_id] = s


# ─── REVERSAL FAMILY (3 strategies) ──────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="range_reversal_v1",
    name="Range Reversal V1",
    family=StrategyFamily.REVERSAL,
    hypothesis=(
        "Price has moved to an extreme and opposing pressure is entering."
    ),
    description=(
        "Exploits failed continuation at range extremes where price "
        "reaches a boundary and reversal patterns confirm rejection."
    ),
    valid_market_phases=("CONSOLIDATION", "REVERSAL"),
    valid_regimes=("RANGING",),
    preferred_context=(
        "range_established",
        "price_at_boundary",
        "momentum_weakening",
    ),
    required_conditions=(
        "range_extreme",
        "rejection_candle",
        "momentum_weakening",
        "support_resistance_nearby",
    ),
    invalid_conditions=(
        "strong_expansion",
        "range_break",
        "trending_regime",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.LOW,
))

_register(StrategyDefinition(
    strategy_id="liquidity_sweep_reversal_v1",
    name="Liquidity Sweep Reversal V1",
    family=StrategyFamily.REVERSAL,
    hypothesis=(
        "Price swept beyond a liquidity level and immediately reversed, "
        "indicating a stop hunt rather than genuine breakout."
    ),
    description=(
        "Exploits false breaks beyond known liquidity pools where "
        "price rejects immediately after triggering stops."
    ),
    valid_market_phases=("REVERSAL", "EXHAUSTION", "CONSOLIDATION"),
    valid_regimes=("RANGING", "TRANSITIONAL"),
    preferred_context=(
        "liquidity_level_known",
        "sweep_detected",
        "immediate_rejection",
    ),
    required_conditions=(
        "liquidity_taken",
        "immediate_rejection",
        "wick_dominant_candle",
        "volume_spike",
    ),
    invalid_conditions=(
        "price_acceptance_beyond_level",
        "follow_through_continuation",
        "no_rejection_within_2_bars",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="exhaustion_reversal_v1",
    name="Exhaustion Reversal V1",
    family=StrategyFamily.REVERSAL,
    hypothesis=(
        "Extended directional move has exhausted participants and "
        "opposing pressure will dominate."
    ),
    description=(
        "Exploits trend exhaustion where directional momentum decays "
        "after an extended move, creating reversal opportunity."
    ),
    valid_market_phases=("EXHAUSTION", "REVERSAL"),
    valid_regimes=("TRENDING", "TRANSITIONAL"),
    preferred_context=(
        "extended_move_completed",
        "momentum_divergence",
        "volume_declining",
    ),
    required_conditions=(
        "extended_trend_present",
        "momentum_divergence",
        "exhaustion_candle",
        "higher_timeframe_resistance",
    ),
    invalid_conditions=(
        "fresh_impulse",
        "momentum_accelerating",
        "no_divergence",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ─── MOMENTUM FAMILY (3 strategies) ──────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="momentum_expansion_v1",
    name="Momentum Expansion V1",
    family=StrategyFamily.MOMENTUM,
    hypothesis=(
        "Multiple strong directional candles indicate institutional "
        "commitment and continuation is likely."
    ),
    description=(
        "Exploits directional expansion where consecutive strong-bodied "
        "candles confirm participant agreement on direction."
    ),
    valid_market_phases=("IMPULSE",),
    valid_regimes=("TRENDING",),
    preferred_context=(
        "strong_directional_candles",
        "expanding_bodies",
        "aligned_bias",
    ),
    required_conditions=(
        "trending_regime",
        "impulse_phase",
        "directional_bias_aligned",
        "expanding_candle_bodies",
    ),
    invalid_conditions=(
        "reversal_candle",
        "phase_transition_away",
        "momentum_deceleration",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.LOW,
))

_register(StrategyDefinition(
    strategy_id="trend_acceleration_v1",
    name="Trend Acceleration V1",
    family=StrategyFamily.MOMENTUM,
    hypothesis=(
        "A confirmed trend is accelerating as new participants "
        "join the dominant direction."
    ),
    description=(
        "Exploits trend acceleration where velocity increases "
        "beyond the initial impulse rate."
    ),
    valid_market_phases=("IMPULSE",),
    valid_regimes=("TRENDING",),
    preferred_context=(
        "trend_confirmed",
        "increasing_velocity",
        "new_participant_volume",
    ),
    required_conditions=(
        "trend_confirmed",
        "velocity_increasing",
        "higher_timeframe_trend",
        "no_resistance_ahead",
    ),
    invalid_conditions=(
        "velocity_decreasing",
        "approaching_structure",
        "divergence_forming",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="impulse_followthrough_v1",
    name="Impulse Follow-Through V1",
    family=StrategyFamily.MOMENTUM,
    hypothesis=(
        "After a break of structure, the first pullback within "
        "the new impulse offers continuation."
    ),
    description=(
        "Exploits the immediate follow-through after a structural "
        "break where momentum confirms the new direction."
    ),
    valid_market_phases=("IMPULSE",),
    valid_regimes=("TRENDING", "TRANSITIONAL"),
    preferred_context=(
        "recent_bos",
        "first_pullback_after_break",
        "momentum_sustained",
    ),
    required_conditions=(
        "break_of_structure_confirmed",
        "first_pullback",
        "momentum_sustained",
        "direction_aligned",
    ),
    invalid_conditions=(
        "deep_retracement",
        "structure_reclaimed",
        "momentum_lost",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ─── CONTINUATION FAMILY (3 strategies) ───────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="trend_pullback_v1",
    name="Trend Pullback V1",
    family=StrategyFamily.CONTINUATION,
    hypothesis=(
        "In a healthy trend, measured pullbacks offer "
        "opportunity to join the dominant direction."
    ),
    description=(
        "Exploits trend resumption after a pullback where the "
        "dominant direction reasserts after a measured retracement."
    ),
    valid_market_phases=("PULLBACK",),
    valid_regimes=("TRENDING",),
    preferred_context=(
        "established_trend",
        "measured_retracement",
        "structure_intact",
    ),
    required_conditions=(
        "trending_regime",
        "pullback_phase",
        "structure_intact",
        "retracement_measured",
        "trend_resumption_signal",
    ),
    invalid_conditions=(
        "deep_retracement",
        "structure_broken",
        "regime_change",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="moving_average_pullback_v1",
    name="Moving Average Pullback V1",
    family=StrategyFamily.CONTINUATION,
    hypothesis=(
        "Dynamic support/resistance at key moving averages "
        "provides entry opportunity during trends."
    ),
    description=(
        "Exploits price interaction with key moving averages "
        "where trend participants defend dynamic levels."
    ),
    valid_market_phases=("PULLBACK",),
    valid_regimes=("TRENDING",),
    preferred_context=(
        "price_at_moving_average",
        "trend_intact",
        "ma_respected_previously",
    ),
    required_conditions=(
        "trending_regime",
        "price_near_key_ma",
        "ma_slope_aligned",
        "rejection_at_ma",
    ),
    invalid_conditions=(
        "ma_broken_with_conviction",
        "flat_ma_slope",
        "ranging_regime",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="structure_retest_v1",
    name="Structure Retest V1",
    family=StrategyFamily.CONTINUATION,
    hypothesis=(
        "Broken structure becomes support/resistance and "
        "retests offer continuation entry."
    ),
    description=(
        "Exploits the principle that broken resistance becomes "
        "support (and vice versa) during trend continuation."
    ),
    valid_market_phases=("PULLBACK",),
    valid_regimes=("TRENDING", "TRANSITIONAL"),
    preferred_context=(
        "recent_structure_break",
        "price_retesting_level",
        "confirmation_forming",
    ),
    required_conditions=(
        "structure_previously_broken",
        "price_retesting_broken_level",
        "rejection_at_retest",
        "trend_direction_intact",
    ),
    invalid_conditions=(
        "level_reclaimed",
        "no_rejection",
        "opposite_structure_break",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ─── BREAKOUT FAMILY (3 strategies) ──────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="range_breakout_v1",
    name="Range Breakout V1",
    family=StrategyFamily.BREAKOUT,
    hypothesis=(
        "After extended consolidation, the eventual breakout "
        "produces a strong directional move."
    ),
    description=(
        "Exploits range resolution where accumulated energy from "
        "compression produces expansion as trapped participants exit."
    ),
    valid_market_phases=("CONSOLIDATION", "IMPULSE"),
    valid_regimes=("RANGING", "TRANSITIONAL"),
    preferred_context=(
        "extended_consolidation",
        "compression_detected",
        "volume_building",
    ),
    required_conditions=(
        "range_established",
        "compression_detected",
        "breakout_candle",
        "volume_confirmation",
    ),
    invalid_conditions=(
        "false_breakout",
        "immediate_reentry",
        "volume_absent",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="volatility_breakout_v1",
    name="Volatility Breakout V1",
    family=StrategyFamily.BREAKOUT,
    hypothesis=(
        "Low volatility periods precede high volatility expansions "
        "and the direction of the break is tradeable."
    ),
    description=(
        "Exploits volatility compression-expansion cycles where "
        "narrow-range periods resolve into directional moves."
    ),
    valid_market_phases=("CONSOLIDATION", "IMPULSE"),
    valid_regimes=("RANGING", "TRANSITIONAL"),
    preferred_context=(
        "volatility_compressed",
        "atr_below_average",
        "narrow_range_bars",
    ),
    required_conditions=(
        "volatility_compression",
        "expansion_candle",
        "directional_conviction",
        "no_immediate_reversal",
    ),
    invalid_conditions=(
        "already_expanded",
        "doji_on_breakout",
        "no_follow_through",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="structure_breakout_v1",
    name="Structure Breakout V1",
    family=StrategyFamily.BREAKOUT,
    hypothesis=(
        "Breaking a key structural level with conviction "
        "indicates regime change and new direction."
    ),
    description=(
        "Exploits decisive breaks of multi-touch structural levels "
        "where institutional order flow commits to a new range."
    ),
    valid_market_phases=("IMPULSE", "CONSOLIDATION"),
    valid_regimes=("RANGING", "TRANSITIONAL", "TRENDING"),
    preferred_context=(
        "key_level_tested_multiple_times",
        "building_pressure",
        "conviction_candle",
    ),
    required_conditions=(
        "key_structure_identified",
        "decisive_break",
        "volume_on_break",
        "close_beyond_level",
    ),
    invalid_conditions=(
        "wick_only_break",
        "immediate_reclaim",
        "low_volume_break",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ─── MEAN_REVERSION FAMILY (3 strategies) ─────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="statistical_fade_v1",
    name="Statistical Fade V1",
    family=StrategyFamily.MEAN_REVERSION,
    hypothesis=(
        "Price has deviated beyond statistical norms and "
        "reversion to the mean is probable."
    ),
    description=(
        "Exploits statistical overextension where price moves "
        "beyond 2+ standard deviations from a mean measure."
    ),
    valid_market_phases=("EXHAUSTION", "REVERSAL"),
    valid_regimes=("RANGING",),
    preferred_context=(
        "statistical_extreme",
        "range_environment",
        "no_fundamental_driver",
    ),
    required_conditions=(
        "price_beyond_statistical_extreme",
        "range_regime_confirmed",
        "no_breakout_signal",
        "reversion_candle_forming",
    ),
    invalid_conditions=(
        "breakout_confirmed",
        "fundamental_event",
        "trending_regime",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="range_mean_reversion_v1",
    name="Range Mean Reversion V1",
    family=StrategyFamily.MEAN_REVERSION,
    hypothesis=(
        "Within a range, price tends to revert toward "
        "the midpoint after touching boundaries."
    ),
    description=(
        "Exploits the mean-reverting nature of ranging markets "
        "where price oscillates between boundaries."
    ),
    valid_market_phases=("CONSOLIDATION", "REVERSAL"),
    valid_regimes=("RANGING",),
    preferred_context=(
        "established_range",
        "price_at_boundary",
        "midpoint_target_clear",
    ),
    required_conditions=(
        "range_established",
        "price_at_range_extreme",
        "no_breakout_pressure",
        "mean_target_identified",
    ),
    invalid_conditions=(
        "range_expanding",
        "breakout_forming",
        "momentum_building",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="volatility_snapback_v1",
    name="Volatility Snapback V1",
    family=StrategyFamily.MEAN_REVERSION,
    hypothesis=(
        "Spike moves driven by volatility rather than conviction "
        "tend to retrace quickly."
    ),
    description=(
        "Exploits volatility-driven spikes that lack follow-through "
        "where the spike retraces once volatility normalises."
    ),
    valid_market_phases=("EXHAUSTION",),
    valid_regimes=("RANGING", "TRANSITIONAL"),
    preferred_context=(
        "volatility_spike",
        "no_follow_through",
        "wick_rejection",
    ),
    required_conditions=(
        "volatility_spike_detected",
        "no_directional_follow_through",
        "wick_rejection_present",
        "volatility_normalising",
    ),
    invalid_conditions=(
        "conviction_follow_through",
        "new_trend_forming",
        "fundamental_catalyst",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ─── STRUCTURE FAMILY (2 strategies) ─────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="bos_continuation_v1",
    name="BOS Continuation V1",
    family=StrategyFamily.STRUCTURE,
    hypothesis=(
        "Break of structure confirms trend continuation and "
        "the first retest offers entry."
    ),
    description=(
        "Exploits confirmed break of structure where the trend "
        "direction is validated and continuation is probable."
    ),
    valid_market_phases=("IMPULSE", "PULLBACK"),
    valid_regimes=("TRENDING",),
    preferred_context=(
        "bos_confirmed",
        "trend_aligned",
        "retest_forming",
    ),
    required_conditions=(
        "break_of_structure_confirmed",
        "trend_direction_aligned",
        "first_retest_after_bos",
        "structure_holding",
    ),
    invalid_conditions=(
        "structure_reclaimed",
        "opposite_bos",
        "deep_retracement",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))

_register(StrategyDefinition(
    strategy_id="choch_transition_v1",
    name="Change of Character Transition V1",
    family=StrategyFamily.STRUCTURE,
    hypothesis=(
        "Change of character signals regime transition and "
        "the new direction will develop."
    ),
    description=(
        "Exploits change of character (CHoCH) where market control "
        "shifts from one side to the other, indicating a new trend."
    ),
    valid_market_phases=("REVERSAL", "IMPULSE"),
    valid_regimes=("TRANSITIONAL", "TRENDING"),
    preferred_context=(
        "choch_detected",
        "previous_trend_exhausted",
        "new_direction_forming",
    ),
    required_conditions=(
        "change_of_character_confirmed",
        "previous_trend_exhausted",
        "new_direction_confirmed",
        "structure_supporting_new_direction",
    ),
    invalid_conditions=(
        "false_choch",
        "previous_trend_resuming",
        "no_follow_through",
    ),
    evidence_status=EvidenceStatus.HYPOTHESIS,
    confidence_level=ConfidenceLevel.NONE,
))


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_strategy(strategy_id: str) -> StrategyDefinition | None:
    """Retrieve a strategy by ID. Returns None if not found."""
    return STRATEGY_LIBRARY.get(strategy_id)


def get_all_strategies() -> list[StrategyDefinition]:
    """Return all registered strategies."""
    return list(STRATEGY_LIBRARY.values())


def get_strategies_by_family(family: StrategyFamily) -> list[StrategyDefinition]:
    """Return all strategies in a given family."""
    return [s for s in STRATEGY_LIBRARY.values() if s.family == family]


def get_strategies_for_phase(phase: str) -> list[StrategyDefinition]:
    """Return all strategies valid for a given market phase."""
    return [s for s in STRATEGY_LIBRARY.values() if phase in s.valid_market_phases]


def get_strategies_for_regime(regime: str) -> list[StrategyDefinition]:
    """Return all strategies valid for a given regime."""
    return [s for s in STRATEGY_LIBRARY.values() if regime in s.valid_regimes]


def get_strategies_for_context(
    *, phase: str = "", regime: str = ""
) -> list[StrategyDefinition]:
    """
    Return strategies matching both phase and regime.

    If either is empty, that filter is skipped.
    """
    results = list(STRATEGY_LIBRARY.values())
    if phase:
        results = [s for s in results if phase in s.valid_market_phases]
    if regime:
        results = [s for s in results if regime in s.valid_regimes]
    return results


def get_strategy_ids() -> list[str]:
    """Return all strategy IDs."""
    return list(STRATEGY_LIBRARY.keys())


def get_family_definition(family: StrategyFamily) -> FamilyDefinition | None:
    """Return a family definition."""
    return FAMILY_DEFINITIONS.get(family)


def get_all_family_definitions() -> list[FamilyDefinition]:
    """Return all family definitions."""
    return list(FAMILY_DEFINITIONS.values())
