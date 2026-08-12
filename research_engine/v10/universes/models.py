"""
New-Engine Question Bank — Data Models.

These models define the structure for the canonical research question registry.
Every question declares its analytical angles, required universes, populations,
joins, and anomaly/exceptional view requirements.

This is the ONLY question model used by the new engine.
Old registry models (ResearchQuestion, V10ResearchQuestion, QuestionDefinition)
are superseded — they remain as historical reference only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════


class Universe(str, Enum):
    """The six analytical universes + Shadow research world."""
    EXECUTION = "EXECUTION"
    DECISION = "DECISION"
    MARKET = "MARKET"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    OUTCOME = "OUTCOME"
    # Shadow research world
    SHADOW_OUTCOME = "SHADOW_OUTCOME"


class Population(str, Enum):
    """Named populations available across universes."""
    # Execution universe populations
    ALL_TRADES = "all_trades"
    WINNING_TRADES = "winning_trades"
    LOSING_TRADES = "losing_trades"
    ANOMALOUS_TRADES = "anomalous_trades"

    # Decision universe populations
    ALL_DECISIONS = "all_decisions"
    EXECUTE_DECISIONS = "execute_decisions"
    NO_TRADE_DECISIONS = "no_trade_decisions"
    REJECTED_AT_OPPORTUNITY = "rejected_at_opportunity"
    REJECTED_AT_STRATEGY = "rejected_at_strategy"
    REJECTED_AT_ENTRY = "rejected_at_entry"
    REJECTED_AT_RISK = "rejected_at_risk"
    REJECTED_AT_EXECUTION = "rejected_at_execution"
    HIGH_SCORE_DECISIONS = "high_score_decisions"
    LOW_SCORE_DECISIONS = "low_score_decisions"

    # Market universe populations
    ALL_MARKET_STATES = "all_market_states"
    TRENDING_REGIME = "trending_regime"
    RANGING_REGIME = "ranging_regime"
    TRANSITIONAL_REGIME = "transitional_regime"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    SESSION_LONDON = "session_london"
    SESSION_NY = "session_ny"
    SESSION_ASIA = "session_asia"

    # Strategy universe populations
    ALL_STRATEGIES = "all_strategies"
    TREND_CONTINUATION = "trend_continuation"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    STRATEGY_ELIGIBLE = "strategy_eligible"
    STRATEGY_SELECTED = "strategy_selected"
    STRATEGY_REJECTED = "strategy_rejected"

    # Risk universe populations
    ALL_RISK_EVALUATIONS = "all_risk_evaluations"
    RISK_APPROVED = "risk_approved"
    RISK_BLOCKED = "risk_blocked"

    # Outcome universe populations
    ALL_OUTCOMES = "all_outcomes"
    OUTCOME_WINS = "outcome_wins"
    OUTCOME_LOSSES = "outcome_losses"

    # Shadow Outcome universe populations
    ALL_SHADOW_OUTCOMES = "all_shadow_outcomes"
    SHADOW_WINS = "shadow_wins"
    SHADOW_LOSSES = "shadow_losses"
    PRIMARY_V10_SHADOW = "primary_v10_shadow"
    HORIZON_SCALP = "horizon_scalp"
    HORIZON_INTRADAY = "horizon_intraday"
    HORIZON_EXTENDED = "horizon_extended"
    SHADOW_FROM_EXECUTE = "shadow_from_execute"
    SHADOW_FROM_NO_TRADE = "shadow_from_no_trade"
    SHADOW_TP_HIT = "shadow_tp_hit"
    SHADOW_SL_HIT = "shadow_sl_hit"
    SHADOW_TIMEOUT = "shadow_timeout"


class JoinType(str, Enum):
    """How universes are joined for cross-angle questions."""
    ENTITY_ID = "entity_id"
    CORRELATION_ID = "correlation_id"
    SYMBOL_TIMESTAMP = "symbol_timestamp"
    CYCLE_ID = "cycle_id"
    TEMPORAL_PROXIMITY = "temporal_proximity"


class ViewType(str, Enum):
    """Types of analytical views a question may require."""
    NORMAL = "NORMAL"
    ANOMALOUS = "ANOMALOUS"
    EXCEPTIONAL = "EXCEPTIONAL"


class AnalysisType(str, Enum):
    """The analytical method required."""
    EXPECTANCY = "expectancy"
    CALIBRATION = "calibration"
    SEGMENTATION = "segmentation"
    COMPARISON = "comparison"
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    TEMPORAL = "temporal"
    SIMULATION = "simulation"
    COUNTERFACTUAL = "counterfactual"
    DEGRADATION = "degradation"


class QuestionStatus(str, Enum):
    """Current execution readiness."""
    READY = "READY"              # Can be executed now
    PARTIAL = "PARTIAL"          # Some data available, partial results possible
    NEEDS_POPULATION = "NEEDS_POPULATION"  # Universe not yet built
    BLOCKED = "BLOCKED"          # Missing fundamental data


# ═══════════════════════════════════════════════════════════════════════════════
# CORE MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AngleRequirement:
    """Declares which universe an angle uses and what populations it needs."""
    universe: Universe
    populations: tuple[Population, ...] = ()
    required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoinRequirement:
    """Declares how two universes are joined for a question."""
    from_universe: Universe
    to_universe: Universe
    join_type: JoinType
    join_field: str = ""


@dataclass(frozen=True)
class NewEngineQuestion:
    """
    A single research question in the new-engine canonical registry.

    This is the sole authoritative question model. All old registry models
    are superseded.
    """
    # Identity
    question_id: str
    title: str
    research_intent: str

    # Four-angle declaration
    angles: dict[Universe, bool] = field(default_factory=dict)
    angle_requirements: tuple[AngleRequirement, ...] = ()

    # Universe and population requirements
    required_universes: tuple[Universe, ...] = ()
    required_populations: tuple[Population, ...] = ()
    required_joins: tuple[JoinRequirement, ...] = ()

    # Analytical views
    views: tuple[ViewType, ...] = (ViewType.NORMAL,)

    # Analysis metadata
    analysis_type: AnalysisType = AnalysisType.EXPECTANCY
    minimum_sample_size: int = 20
    dependencies: tuple[str, ...] = ()

    # Execution status
    status: QuestionStatus = QuestionStatus.NEEDS_POPULATION

    # Traceability (NOT a runtime dependency)
    source_intent: tuple[str, ...] = ()

    # Decision context
    decision_enabled: str = ""

    def uses_execution(self) -> bool:
        return Universe.EXECUTION in self.required_universes

    def uses_decision(self) -> bool:
        return Universe.DECISION in self.required_universes

    def uses_market(self) -> bool:
        return Universe.MARKET in self.required_universes

    def uses_strategy(self) -> bool:
        return Universe.STRATEGY in self.required_universes

    @property
    def angle_count(self) -> int:
        return len(self.required_universes)

    @property
    def is_cross_angle(self) -> bool:
        return self.angle_count > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "research_intent": self.research_intent,
            "angles": {
                "execution": Universe.EXECUTION in self.required_universes,
                "decision": Universe.DECISION in self.required_universes,
                "market": Universe.MARKET in self.required_universes,
                "strategy": Universe.STRATEGY in self.required_universes,
            },
            "required_universes": [u.value for u in self.required_universes],
            "required_populations": [p.value for p in self.required_populations],
            "required_joins": [
                {
                    "from": j.from_universe.value,
                    "to": j.to_universe.value,
                    "type": j.join_type.value,
                    "field": j.join_field,
                }
                for j in self.required_joins
            ],
            "views": [v.value for v in self.views],
            "analysis_type": self.analysis_type.value,
            "minimum_sample_size": self.minimum_sample_size,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "source_intent": list(self.source_intent),
            "decision_enabled": self.decision_enabled,
        }
