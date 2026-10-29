"""
Strategy Conditions — Structured condition definitions.

Converts prose conditions from strategy definitions into evaluable
structured objects. Each condition maps to a specific data field
available at runtime from MarketContext or OpportunityAssessment.

Design:
    - Conditions are pure data (frozen dataclasses)
    - Each condition knows what data field it requires
    - Conditions do NOT evaluate themselves
    - The ConditionEvaluator handles evaluation logic
    - Missing data is reported, not treated as failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConditionCategory(str, Enum):
    """Category of condition being evaluated."""
    ENVIRONMENT = "ENVIRONMENT"   # Regime, phase, direction
    LOCATION = "LOCATION"         # Key levels, structure proximity
    STRUCTURE = "STRUCTURE"       # BOS, swing structure, order blocks
    MOMENTUM = "MOMENTUM"         # Bias alignment, trend strength
    TIMING = "TIMING"             # Trigger readiness, confirmation
    PATTERN = "PATTERN"           # Pattern detection, quality


class ConditionResult(str, Enum):
    """Outcome of evaluating a single condition."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    MISSING_DATA = "MISSING_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Condition:
    """
    A single evaluable condition for a strategy.

    Attributes:
        name: Machine-readable identifier (e.g. "regime_is_range")
        description: Human-readable explanation
        category: What aspect of the market this checks
        required: Whether this condition must pass for eligibility
        data_field: The MarketContext path this reads from
        evaluator_key: Which evaluation function handles this
        expected_values: Acceptable values (for enum-type checks)
        threshold: Numeric threshold (for numeric checks)
        comparison: How to compare ("eq", "in", "gte", "lte", "gt", "lt", "bool_true")
    """
    name: str
    description: str
    category: ConditionCategory
    required: bool = True
    data_field: str = ""
    evaluator_key: str = ""
    expected_values: tuple[str, ...] = ()
    threshold: float = 0.0
    comparison: str = "eq"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConditionEvaluation:
    """Result of evaluating one condition against market data."""
    condition: Condition
    result: ConditionResult
    actual_value: Any = None
    explanation: str = ""

    @property
    def passed(self) -> bool:
        return self.result == ConditionResult.PASSED

    @property
    def is_missing(self) -> bool:
        return self.result == ConditionResult.MISSING_DATA


@dataclass(frozen=True)
class StrategyConditionSet:
    """
    Complete set of conditions for one strategy.

    Groups conditions into environment (regime/phase match) and
    entry conditions (specific market features required).
    """
    strategy_id: str
    environment_conditions: tuple[Condition, ...] = ()
    entry_conditions: tuple[Condition, ...] = ()

    @property
    def all_conditions(self) -> tuple[Condition, ...]:
        return self.environment_conditions + self.entry_conditions

    @property
    def required_conditions(self) -> tuple[Condition, ...]:
        return tuple(c for c in self.all_conditions if c.required)

    @property
    def optional_conditions(self) -> tuple[Condition, ...]:
        return tuple(c for c in self.all_conditions if not c.required)


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITION DEFINITIONS PER STRATEGY
# ═══════════════════════════════════════════════════════════════════════════════
#
# Each strategy's prose conditions are converted to structured Condition objects.
# Only conditions that reference ACTUALLY AVAILABLE data fields are included.
# Conditions referencing unavailable data use evaluator_key="unavailable".
# ═══════════════════════════════════════════════════════════════════════════════


STRATEGY_CONDITIONS: dict[str, StrategyConditionSet] = {}


def _register_conditions(condition_set: StrategyConditionSet) -> None:
    STRATEGY_CONDITIONS[condition_set.strategy_id] = condition_set


# ─── range_reversal_v1 ────────────────────────────────────────────────────────

_register_conditions(StrategyConditionSet(
    strategy_id="range_reversal_v1",
    environment_conditions=(
        Condition(
            name="regime_is_ranging",
            description="H4 regime must be RANGING",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="regime",
            evaluator_key="enum_match",
            expected_values=("RANGING",),
            comparison="in",
        ),
        Condition(
            name="phase_is_reversal_or_exhaustion",
            description="Market phase must be REVERSAL or EXHAUSTION",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="phase",
            evaluator_key="enum_match",
            expected_values=("REVERSAL", "EXHAUSTION"),
            comparison="in",
        ),
    ),
    entry_conditions=(
        Condition(
            name="at_key_level",
            description="Price near M15 support/resistance",
            category=ConditionCategory.LOCATION,
            required=True,
            data_field="m15.at_key_level",
            evaluator_key="bool_check",
            comparison="bool_true",
        ),
        Condition(
            name="no_strong_momentum_against",
            description="M5 bias not strongly against reversal",
            category=ConditionCategory.MOMENTUM,
            required=False,
            data_field="m5.bias_strength",
            evaluator_key="numeric_threshold",
            threshold=70.0,
            comparison="lte",
        ),
        Condition(
            name="reversal_pattern_detected",
            description="A reversal-family pattern must be detected",
            category=ConditionCategory.PATTERN,
            required=True,
            data_field="pattern_detected",
            evaluator_key="pattern_family_check",
            expected_values=(
                "TWEEZER_TOP", "TWEEZER_BOTTOM", "HAMMER",
                "HANGING_MAN", "INVERTED_HAMMER", "SHOOTING_STAR",
                "MORNING_STAR", "EVENING_STAR",
                "BULLISH_ENGULFING", "BEARISH_ENGULFING",
            ),
            comparison="in",
        ),
        Condition(
            name="structure_quality_adequate",
            description="M15 quality score above minimum",
            category=ConditionCategory.STRUCTURE,
            required=False,
            data_field="m15.quality_score",
            evaluator_key="numeric_threshold",
            threshold=0.3,
            comparison="gte",
        ),
    ),
))


# ─── liquidity_sweep_reversal_v1 ──────────────────────────────────────────────

_register_conditions(StrategyConditionSet(
    strategy_id="liquidity_sweep_reversal_v1",
    environment_conditions=(
        Condition(
            name="phase_is_reversal_or_exhaustion_or_consolidation",
            description="Phase must be REVERSAL, EXHAUSTION, or CONSOLIDATION",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="phase",
            evaluator_key="enum_match",
            expected_values=("REVERSAL", "EXHAUSTION", "CONSOLIDATION"),
            comparison="in",
        ),
    ),
    entry_conditions=(
        Condition(
            name="at_key_level",
            description="Price near known level (proxy for liquidity)",
            category=ConditionCategory.LOCATION,
            required=True,
            data_field="m15.at_key_level",
            evaluator_key="bool_check",
            comparison="bool_true",
        ),
        Condition(
            name="order_block_present",
            description="Institutional interest zone detected",
            category=ConditionCategory.STRUCTURE,
            required=False,
            data_field="m15.order_block_present",
            evaluator_key="bool_check",
            comparison="bool_true",
        ),
        Condition(
            name="reversal_pattern_detected",
            description="Rejection pattern confirms sweep",
            category=ConditionCategory.PATTERN,
            required=True,
            data_field="pattern_detected",
            evaluator_key="pattern_family_check",
            expected_values=(
                "HAMMER", "SHOOTING_STAR",
                "BULLISH_ENGULFING", "BEARISH_ENGULFING",
                "TWEEZER_TOP", "TWEEZER_BOTTOM",
            ),
            comparison="in",
        ),
        Condition(
            name="liquidity_levels_available",
            description="Liquidity level data exists (NOT YET IMPLEMENTED)",
            category=ConditionCategory.LOCATION,
            required=True,
            data_field="liquidity_levels",
            evaluator_key="unavailable",
            comparison="eq",
        ),
    ),
))


# ─── momentum_expansion_v1 ────────────────────────────────────────────────────

_register_conditions(StrategyConditionSet(
    strategy_id="momentum_expansion_v1",
    environment_conditions=(
        Condition(
            name="regime_is_trending",
            description="H4 regime must be TRENDING",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="regime",
            evaluator_key="enum_match",
            expected_values=("TRENDING",),
            comparison="in",
        ),
        Condition(
            name="phase_is_impulse",
            description="Market phase must be IMPULSE",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="phase",
            evaluator_key="enum_match",
            expected_values=("IMPULSE",),
            comparison="in",
        ),
    ),
    entry_conditions=(
        Condition(
            name="h1_bias_aligns",
            description="H1 direction aligns with momentum",
            category=ConditionCategory.MOMENTUM,
            required=True,
            data_field="h1.direction",
            evaluator_key="bias_alignment_check",
            expected_values=("BULLISH", "BEARISH"),
            comparison="not_neutral",
        ),
        Condition(
            name="momentum_pattern_detected",
            description="A momentum-family pattern must be detected",
            category=ConditionCategory.PATTERN,
            required=True,
            data_field="pattern_detected",
            evaluator_key="pattern_family_check",
            expected_values=(
                "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS",
            ),
            comparison="in",
        ),
        Condition(
            name="h4_trend_strength_adequate",
            description="H4 trend strength above minimum",
            category=ConditionCategory.MOMENTUM,
            required=False,
            data_field="h4.trend_strength",
            evaluator_key="numeric_threshold",
            threshold=0.3,
            comparison="gte",
        ),
        Condition(
            name="bos_confirmed",
            description="Break of structure confirms direction",
            category=ConditionCategory.STRUCTURE,
            required=False,
            data_field="h1.bos_confirmed",
            evaluator_key="bool_check",
            comparison="bool_true",
        ),
    ),
))


# ─── trend_pullback_continuation_v1 ───────────────────────────────────────────

_register_conditions(StrategyConditionSet(
    strategy_id="trend_pullback_continuation_v1",
    environment_conditions=(
        Condition(
            name="regime_is_trending",
            description="H4 regime must be TRENDING",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="regime",
            evaluator_key="enum_match",
            expected_values=("TRENDING",),
            comparison="in",
        ),
        Condition(
            name="phase_is_pullback",
            description="Market phase must be PULLBACK",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="phase",
            evaluator_key="enum_match",
            expected_values=("PULLBACK",),
            comparison="in",
        ),
    ),
    entry_conditions=(
        Condition(
            name="h1_bias_aligns_with_trend",
            description="H1 direction aligns with trend",
            category=ConditionCategory.MOMENTUM,
            required=True,
            data_field="h1.direction",
            evaluator_key="bias_alignment_check",
            expected_values=("BULLISH", "BEARISH"),
            comparison="not_neutral",
        ),
        Condition(
            name="continuation_pattern_detected",
            description="Continuation pattern (NOT YET IN LIBRARY)",
            category=ConditionCategory.PATTERN,
            required=True,
            data_field="pattern_detected",
            evaluator_key="unavailable",
            expected_values=(),
            comparison="in",
        ),
        Condition(
            name="structure_quality_adequate",
            description="M15 quality score above minimum",
            category=ConditionCategory.STRUCTURE,
            required=False,
            data_field="m15.quality_score",
            evaluator_key="numeric_threshold",
            threshold=0.3,
            comparison="gte",
        ),
    ),
))


# ─── range_breakout_v1 ────────────────────────────────────────────────────────

_register_conditions(StrategyConditionSet(
    strategy_id="range_breakout_v1",
    environment_conditions=(
        Condition(
            name="phase_is_consolidation_or_impulse",
            description="Phase must be CONSOLIDATION or IMPULSE",
            category=ConditionCategory.ENVIRONMENT,
            required=True,
            data_field="phase",
            evaluator_key="enum_match",
            expected_values=("CONSOLIDATION", "IMPULSE"),
            comparison="in",
        ),
    ),
    entry_conditions=(
        Condition(
            name="trigger_ready",
            description="M5 execution trigger is ready",
            category=ConditionCategory.TIMING,
            required=False,
            data_field="m5.trigger_ready",
            evaluator_key="bool_check",
            comparison="bool_true",
        ),
        Condition(
            name="breakout_pattern_detected",
            description="Breakout pattern (NOT YET IN LIBRARY)",
            category=ConditionCategory.PATTERN,
            required=True,
            data_field="pattern_detected",
            evaluator_key="unavailable",
            expected_values=(),
            comparison="in",
        ),
        Condition(
            name="range_duration_available",
            description="Range duration data (NOT YET AVAILABLE)",
            category=ConditionCategory.STRUCTURE,
            required=True,
            data_field="range_duration",
            evaluator_key="unavailable",
            comparison="gte",
        ),
    ),
))


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_conditions_for_strategy(strategy_id: str) -> StrategyConditionSet | None:
    """Return the condition set for a given strategy."""
    return STRATEGY_CONDITIONS.get(strategy_id)


def get_all_condition_sets() -> list[StrategyConditionSet]:
    """Return all registered condition sets."""
    return list(STRATEGY_CONDITIONS.values())


def has_conditions(strategy_id: str) -> bool:
    """Whether structured conditions exist for a strategy."""
    return strategy_id in STRATEGY_CONDITIONS
