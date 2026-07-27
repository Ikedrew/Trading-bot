"""
Strategy Condition Evaluator — Evaluates whether a strategy's
conditions are satisfied by the current market context.

This is OBSERVATION ONLY. It does not:
    - Place trades
    - Calculate entries
    - Modify scores
    - Approve execution
    - Connect to the decision engine

It answers ONE question:
    "Did this strategy's requirements occur?"

Usage:
    from core.strategies.condition_evaluator import (
        StrategyConditionEvaluator,
    )
    evaluator = StrategyConditionEvaluator()
    result = evaluator.evaluate(strategy_id, market_snapshot)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.strategies.conditions import (
    Condition,
    ConditionCategory,
    ConditionEvaluation,
    ConditionResult,
    StrategyConditionSet,
    get_conditions_for_strategy,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ConditionEvaluationResult:
    """
    Complete result of evaluating a strategy's conditions.

    Contains everything needed to understand whether a strategy's
    requirements were met, partially met, or missing data.
    """
    strategy_id: str
    eligible_by_phase: bool
    conditions_checked: int
    conditions_passed: int
    conditions_failed: int
    missing_data: tuple[str, ...]
    unavailable_conditions: tuple[str, ...]
    confidence: float
    overall_status: str  # FULLY_MET | PARTIALLY_MET | NOT_MET | INCOMPLETE
    explanation: str
    evaluations: tuple[ConditionEvaluation, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def all_required_passed(self) -> bool:
        """Whether all required conditions passed."""
        return all(
            e.passed for e in self.evaluations
            if e.condition.required
            and e.result != ConditionResult.MISSING_DATA
            and e.result != ConditionResult.NOT_APPLICABLE
        )

    @property
    def pass_rate(self) -> float:
        """Fraction of checked conditions that passed."""
        if self.conditions_checked == 0:
            return 0.0
        return self.conditions_passed / self.conditions_checked


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET SNAPSHOT (flat dict interface for evaluator)
# ═══════════════════════════════════════════════════════════════════════════════


def build_market_snapshot(
    *,
    regime: str = "",
    phase: str = "",
    direction: str = "",
    h4_regime: str = "",
    h4_trend_bias: str = "",
    h4_trend_strength: float = 0.0,
    h4_atr_ratio: float = 1.0,
    h1_direction: str = "",
    h1_swing_structure: str = "",
    h1_ema_position: float = 0.0,
    h1_bos_confirmed: bool = False,
    h1_bos_direction: str = "",
    m15_quality_score: float = 0.0,
    m15_at_key_level: bool = False,
    m15_order_block_present: bool = False,
    m15_nearest_support: float = 0.0,
    m15_nearest_resistance: float = 0.0,
    m5_bias_phase: str = "",
    m5_bias_strength: float = 0.0,
    m5_bias_direction: str = "",
    m5_trigger_ready: bool = False,
    m5_confirmation_strength: str = "",
    tradability_score: float = 0.0,
    alignment_score: float = 0.0,
    pattern_detected: str = "",
    pattern_quality: float = 0.0,
    **extra: Any,
) -> dict[str, Any]:
    """
    Build a flat market snapshot dict from available data.

    This is the standard interface between MarketContext and the evaluator.
    Can be constructed from a MarketContext object or from raw fields.
    """
    return {
        "regime": regime,
        "phase": phase,
        "direction": direction,
        "h4.regime": h4_regime,
        "h4.trend_bias": h4_trend_bias,
        "h4.trend_strength": h4_trend_strength,
        "h4.atr_ratio": h4_atr_ratio,
        "h1.direction": h1_direction,
        "h1.swing_structure": h1_swing_structure,
        "h1.ema_position": h1_ema_position,
        "h1.bos_confirmed": h1_bos_confirmed,
        "h1.bos_direction": h1_bos_direction,
        "m15.quality_score": m15_quality_score,
        "m15.at_key_level": m15_at_key_level,
        "m15.order_block_present": m15_order_block_present,
        "m15.nearest_support": m15_nearest_support,
        "m15.nearest_resistance": m15_nearest_resistance,
        "m5.bias_phase": m5_bias_phase,
        "m5.bias_strength": m5_bias_strength,
        "m5.bias_direction": m5_bias_direction,
        "m5.trigger_ready": m5_trigger_ready,
        "m5.confirmation_strength": m5_confirmation_strength,
        "tradability_score": tradability_score,
        "alignment_score": alignment_score,
        "pattern_detected": pattern_detected,
        "pattern_quality": pattern_quality,
        **extra,
    }


def snapshot_from_market_context(ctx: Any) -> dict[str, Any]:
    """
    Convert a MarketContext object into a flat snapshot dict.

    Args:
        ctx: A MarketContext instance (from core.market_context.models)

    Returns:
        Flat dict suitable for the evaluator.
    """
    return build_market_snapshot(
        regime=ctx.regime.value if hasattr(ctx.regime, "value") else str(ctx.regime),
        phase=ctx.phase.value if hasattr(ctx.phase, "value") else str(ctx.phase),
        direction=ctx.direction.value if hasattr(ctx.direction, "value") else str(ctx.direction),
        h4_regime=ctx.h4.regime if ctx.h4 else "",
        h4_trend_bias=ctx.h4.trend_bias if ctx.h4 else "",
        h4_trend_strength=ctx.h4.trend_strength if ctx.h4 else 0.0,
        h4_atr_ratio=ctx.h4.atr_ratio if ctx.h4 else 1.0,
        h1_direction=ctx.h1.direction if ctx.h1 else "",
        h1_swing_structure=ctx.h1.swing_structure if ctx.h1 else "",
        h1_ema_position=ctx.h1.ema_position if ctx.h1 else 0.0,
        h1_bos_confirmed=ctx.h1.bos_confirmed if ctx.h1 else False,
        h1_bos_direction=ctx.h1.bos_direction if ctx.h1 else "",
        m15_quality_score=ctx.m15.quality_score if ctx.m15 else 0.0,
        m15_at_key_level=ctx.m15.at_key_level if ctx.m15 else False,
        m15_order_block_present=ctx.m15.order_block_present if ctx.m15 else False,
        m15_nearest_support=ctx.m15.nearest_support if ctx.m15 else 0.0,
        m15_nearest_resistance=ctx.m15.nearest_resistance if ctx.m15 else 0.0,
        m5_bias_phase=ctx.m5.bias_phase if ctx.m5 else "",
        m5_bias_strength=ctx.m5.bias_strength if ctx.m5 else 0.0,
        m5_bias_direction=ctx.m5.bias_direction if ctx.m5 else "",
        m5_trigger_ready=ctx.m5.trigger_ready if ctx.m5 else False,
        m5_confirmation_strength=ctx.m5.confirmation_strength if ctx.m5 else "",
        tradability_score=ctx.tradability_score,
        alignment_score=ctx.alignment_score,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITION EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyConditionEvaluator:
    """
    Evaluates a strategy's conditions against current market data.

    OBSERVATION ONLY. Does not:
        - Place trades
        - Modify scores
        - Approve execution
        - Connect to decision pipeline

    Usage:
        evaluator = StrategyConditionEvaluator()
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL", ...)
        result = evaluator.evaluate("range_reversal_v1", snapshot)

        print(result.overall_status)  # "FULLY_MET" | "PARTIALLY_MET" | ...
        print(result.conditions_passed)  # 4
        print(result.missing_data)  # ("liquidity_levels",)
    """

    def evaluate(
        self,
        strategy_id: str,
        snapshot: dict[str, Any],
    ) -> ConditionEvaluationResult:
        """
        Evaluate all conditions for a strategy against market snapshot.

        Args:
            strategy_id: The strategy to evaluate
            snapshot: Flat dict of market data (from build_market_snapshot)

        Returns:
            ConditionEvaluationResult with full breakdown.
        """
        condition_set = get_conditions_for_strategy(strategy_id)

        if condition_set is None:
            return ConditionEvaluationResult(
                strategy_id=strategy_id,
                eligible_by_phase=False,
                conditions_checked=0,
                conditions_passed=0,
                conditions_failed=0,
                missing_data=(),
                unavailable_conditions=(),
                confidence=0.0,
                overall_status="NO_CONDITIONS_DEFINED",
                explanation=f"No structured conditions for '{strategy_id}'",
                metadata={"error": "strategy_not_found"},
            )

        evaluations: list[ConditionEvaluation] = []
        missing_data: list[str] = []
        unavailable: list[str] = []

        # Evaluate all conditions
        for condition in condition_set.all_conditions:
            evaluation = self._evaluate_condition(condition, snapshot)
            evaluations.append(evaluation)

            if evaluation.result == ConditionResult.MISSING_DATA:
                missing_data.append(condition.data_field)
            elif evaluation.result == ConditionResult.NOT_APPLICABLE:
                unavailable.append(condition.name)

        # Calculate summary
        checked = [e for e in evaluations
                   if e.result not in (ConditionResult.NOT_APPLICABLE,)]
        passed = [e for e in checked if e.passed]
        failed = [e for e in checked
                  if e.result == ConditionResult.FAILED]

        # Phase eligibility (environment conditions only)
        env_evals = [e for e in evaluations
                     if e.condition.category == ConditionCategory.ENVIRONMENT]
        eligible_by_phase = all(e.passed for e in env_evals) if env_evals else False

        # Overall status
        status = self._determine_status(
            evaluations, missing_data, unavailable
        )

        # Confidence: fraction of required conditions with data that passed
        required_with_data = [
            e for e in evaluations
            if e.condition.required
            and e.result not in (
                ConditionResult.MISSING_DATA,
                ConditionResult.NOT_APPLICABLE,
            )
        ]
        if required_with_data:
            confidence = sum(1 for e in required_with_data if e.passed) / len(required_with_data)
        else:
            confidence = 0.0

        explanation = self._build_explanation(
            strategy_id, status, len(passed), len(checked),
            missing_data, unavailable
        )

        return ConditionEvaluationResult(
            strategy_id=strategy_id,
            eligible_by_phase=eligible_by_phase,
            conditions_checked=len(checked),
            conditions_passed=len(passed),
            conditions_failed=len(failed),
            missing_data=tuple(missing_data),
            unavailable_conditions=tuple(unavailable),
            confidence=round(confidence, 4),
            overall_status=status,
            explanation=explanation,
            evaluations=tuple(evaluations),
            metadata={
                "total_conditions": len(condition_set.all_conditions),
                "required_count": len(condition_set.required_conditions),
                "optional_count": len(condition_set.optional_conditions),
            },
        )


    def evaluate_all(
        self,
        snapshot: dict[str, Any],
    ) -> list[ConditionEvaluationResult]:
        """
        Evaluate all registered strategies against the snapshot.

        Returns one ConditionEvaluationResult per strategy.
        """
        from core.strategies.conditions import STRATEGY_CONDITIONS
        results = []
        for strategy_id in STRATEGY_CONDITIONS:
            results.append(self.evaluate(strategy_id, snapshot))
        return results

    # ═══════════════════════════════════════════════════════════════════
    # PRIVATE: Condition evaluation logic
    # ═══════════════════════════════════════════════════════════════════

    def _evaluate_condition(
        self,
        condition: Condition,
        snapshot: dict[str, Any],
    ) -> ConditionEvaluation:
        """Evaluate a single condition against the snapshot."""

        # Unavailable data (feature not yet implemented)
        if condition.evaluator_key == "unavailable":
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.NOT_APPLICABLE,
                actual_value=None,
                explanation=f"Data not yet available: {condition.data_field}",
            )

        # Get the value from snapshot
        value = snapshot.get(condition.data_field)

        # Check if data exists
        if value is None or (isinstance(value, str) and value == ""):
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.MISSING_DATA,
                actual_value=value,
                explanation=f"No data for '{condition.data_field}'",
            )

        # Dispatch to appropriate evaluator
        if condition.evaluator_key == "enum_match":
            return self._eval_enum_match(condition, value)
        elif condition.evaluator_key == "bool_check":
            return self._eval_bool_check(condition, value)
        elif condition.evaluator_key == "numeric_threshold":
            return self._eval_numeric_threshold(condition, value)
        elif condition.evaluator_key == "pattern_family_check":
            return self._eval_pattern_family(condition, value)
        elif condition.evaluator_key == "bias_alignment_check":
            return self._eval_bias_alignment(condition, value)
        else:
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.MISSING_DATA,
                actual_value=value,
                explanation=f"Unknown evaluator: {condition.evaluator_key}",
            )


    def _eval_enum_match(
        self, condition: Condition, value: Any
    ) -> ConditionEvaluation:
        """Check if value is in the expected_values set."""
        str_value = str(value).upper() if value else ""
        passed = str_value in condition.expected_values

        return ConditionEvaluation(
            condition=condition,
            result=ConditionResult.PASSED if passed else ConditionResult.FAILED,
            actual_value=str_value,
            explanation=(
                f"{condition.data_field}='{str_value}' "
                f"{'in' if passed else 'NOT in'} "
                f"{list(condition.expected_values)}"
            ),
        )

    def _eval_bool_check(
        self, condition: Condition, value: Any
    ) -> ConditionEvaluation:
        """Check if value is truthy."""
        passed = bool(value)

        return ConditionEvaluation(
            condition=condition,
            result=ConditionResult.PASSED if passed else ConditionResult.FAILED,
            actual_value=value,
            explanation=(
                f"{condition.data_field}={value} "
                f"({'truthy' if passed else 'falsy'})"
            ),
        )

    def _eval_numeric_threshold(
        self, condition: Condition, value: Any
    ) -> ConditionEvaluation:
        """Check numeric value against threshold using comparison."""
        try:
            num_value = float(value)
        except (TypeError, ValueError):
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.MISSING_DATA,
                actual_value=value,
                explanation=f"Cannot convert '{value}' to number",
            )

        threshold = condition.threshold
        comp = condition.comparison

        if comp == "gte":
            passed = num_value >= threshold
        elif comp == "lte":
            passed = num_value <= threshold
        elif comp == "gt":
            passed = num_value > threshold
        elif comp == "lt":
            passed = num_value < threshold
        else:
            passed = num_value == threshold

        symbol = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}.get(comp, "==")

        return ConditionEvaluation(
            condition=condition,
            result=ConditionResult.PASSED if passed else ConditionResult.FAILED,
            actual_value=num_value,
            explanation=(
                f"{condition.data_field}={num_value:.4f} "
                f"{symbol} {threshold} → {'PASS' if passed else 'FAIL'}"
            ),
        )


    def _eval_pattern_family(
        self, condition: Condition, value: Any
    ) -> ConditionEvaluation:
        """Check if detected pattern is in the expected pattern set."""
        str_value = str(value).upper() if value else ""

        if not str_value:
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.MISSING_DATA,
                actual_value=value,
                explanation="No pattern detected",
            )

        passed = str_value in condition.expected_values

        return ConditionEvaluation(
            condition=condition,
            result=ConditionResult.PASSED if passed else ConditionResult.FAILED,
            actual_value=str_value,
            explanation=(
                f"Pattern '{str_value}' "
                f"{'is' if passed else 'is NOT'} in strategy triggers"
            ),
        )

    def _eval_bias_alignment(
        self, condition: Condition, value: Any
    ) -> ConditionEvaluation:
        """Check if bias/direction is not NEUTRAL (aligned with something)."""
        str_value = str(value).upper() if value else ""

        if not str_value:
            return ConditionEvaluation(
                condition=condition,
                result=ConditionResult.MISSING_DATA,
                actual_value=value,
                explanation="No direction data available",
            )

        passed = str_value != "NEUTRAL" and str_value != ""

        return ConditionEvaluation(
            condition=condition,
            result=ConditionResult.PASSED if passed else ConditionResult.FAILED,
            actual_value=str_value,
            explanation=(
                f"{condition.data_field}='{str_value}' "
                f"{'has directional bias' if passed else 'is NEUTRAL'}"
            ),
        )

    # ═══════════════════════════════════════════════════════════════════
    # PRIVATE: Status determination
    # ═══════════════════════════════════════════════════════════════════

    def _determine_status(
        self,
        evaluations: list[ConditionEvaluation],
        missing_data: list[str],
        unavailable: list[str],
    ) -> str:
        """Determine overall status from evaluations."""
        required_evals = [
            e for e in evaluations if e.condition.required
        ]

        if not required_evals:
            return "NO_CONDITIONS_DEFINED"

        required_passed = [e for e in required_evals if e.passed]
        required_failed = [
            e for e in required_evals
            if e.result == ConditionResult.FAILED
        ]
        required_missing = [
            e for e in required_evals
            if e.result in (ConditionResult.MISSING_DATA, ConditionResult.NOT_APPLICABLE)
        ]

        if required_failed:
            return "NOT_MET"
        elif required_missing and not required_passed:
            return "INCOMPLETE"
        elif required_missing:
            return "PARTIALLY_MET"
        elif len(required_passed) == len(required_evals):
            return "FULLY_MET"
        else:
            return "PARTIALLY_MET"

    def _build_explanation(
        self,
        strategy_id: str,
        status: str,
        passed: int,
        checked: int,
        missing: list[str],
        unavailable: list[str],
    ) -> str:
        """Build human-readable explanation."""
        parts = [f"Strategy '{strategy_id}': {status}"]
        parts.append(f"  Conditions: {passed}/{checked} passed")

        if missing:
            parts.append(f"  Missing data: {missing}")
        if unavailable:
            parts.append(f"  Unavailable features: {unavailable}")

        return "; ".join(parts)
