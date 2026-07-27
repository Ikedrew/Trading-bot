"""
Single decision authority (Stage 3): interprets FinishParams + layers → UnifiedDecision.

Subsystems only describe state and supply FinishParams proposals; this module is the
only place that turns them into Decision + bundle.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.pipeline.finish_params import FinishParams, finish_params_to_decision
from core.pipeline_types import (
    BarEvaluationContext,
    ConfirmationResult,
    ContextResult,
    Decision,
    PatternResult,
    QualityResult,
    ScoreResult,
    StructureResult,
    UnifiedDecision,
)


@dataclass
class DecisionEngine:
    """Stateless interpreter; keep as class for clear call sites (`engine.finalize(...)`)."""

    @staticmethod
    def market_environment_halt(reason: str) -> FinishParams:
        return FinishParams(should_trade=False, reason=reason)

    @staticmethod
    def to_decision(params: FinishParams) -> Decision:
        return finish_params_to_decision(params)

    @staticmethod
    def finalize(
        *,
        bar_context: BarEvaluationContext,
        last_completed_stage: str,
        ctx: ContextResult,
        pattern: PatternResult,
        confirmation: ConfirmationResult,
        structure: StructureResult,
        score: ScoreResult,
        quality: QualityResult,
        params: FinishParams,
    ) -> UnifiedDecision:
        return UnifiedDecision(
            bar_context=bar_context,
            context=ctx,
            pattern=pattern,
            confirmation=confirmation,
            structure=structure,
            score=score,
            quality=quality,
            last_completed_stage=last_completed_stage,
            decision_authority=params,
            decision=finish_params_to_decision(params),
        )
