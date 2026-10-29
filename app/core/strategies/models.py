"""
Strategy Framework Models — Data definitions for the strategy layer.

Strategies are research hypotheses that describe HOW a particular market
behaviour should be exploited. They sit between Strategy Family (WHAT behaviour)
and Pattern (WHAT trigger confirms it).

Hierarchy:
    Strategy Family: "What behaviour are we exploiting?" (REVERSAL, MOMENTUM, etc.)
    Strategy:        "How do we exploit that behaviour?" (range_reversal_v1, etc.)
    Pattern:         "What trigger confirms the opportunity?" (HAMMER, etc.)

All models are immutable. No runtime side effects. No trading logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.strategy_family.models import StrategyFamily


class StrategyStatus(str, Enum):
    """
    Lifecycle status of a strategy.

    Strategies progress through a research-driven lifecycle. Each transition
    requires evidence. No strategy can skip stages.

    Lifecycle:
        HYPOTHESIS → RESEARCHING → SHADOW_TESTING → VALIDATED → ACTIVE → DISABLED
    """
    HYPOTHESIS = "HYPOTHESIS"
    """Initial state. Strategy defined but not yet under research."""

    RESEARCHING = "RESEARCHING"
    """Actively being researched. Data collection in progress."""

    SHADOW_TESTING = "SHADOW_TESTING"
    """Running in shadow mode — generating signals but not executing trades."""

    VALIDATED = "VALIDATED"
    """Research complete. Evidence supports positive expectancy. Awaiting activation."""

    ACTIVE = "ACTIVE"
    """Live. Influencing trade decisions. Requires ongoing monitoring."""

    DISABLED = "DISABLED"
    """Deactivated. May have been active previously. Evidence degraded or superseded."""


@dataclass(frozen=True)
class RiskModel:
    """
    Risk parameters for a strategy.

    Defines how risk is managed when this strategy generates a signal.
    """
    stop_loss_method: str = "STRUCTURE"      # STRUCTURE | FIXED_R | ATR_BASED
    risk_reward_minimum: float = 1.5
    max_risk_per_trade: float = 0.01         # 1% of equity
    trailing_stop: bool = False
    invalidation_type: str = "PRICE_BASED"   # PRICE_BASED | TIME_BASED | STRUCTURE_BREAK
    notes: str = ""


@dataclass(frozen=True)
class ExitModel:
    """
    Exit logic for a strategy.

    Defines how and when positions are closed.
    """
    take_profit_method: str = "STRUCTURE"    # STRUCTURE | FIXED_R | EXTENSION
    partial_exit: bool = False
    time_based_exit: bool = True
    max_hold_candles: int = 20
    trailing_exit: bool = False
    notes: str = ""


@dataclass(frozen=True)
class EvidenceStatus:
    """
    Current research evidence backing this strategy.

    Tracks what has been tested and what the findings show.
    """
    sample_size: int = 0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    p_value: float = 1.0                     # 1.0 = no evidence
    walk_forward_validated: bool = False
    out_of_sample_validated: bool = False
    last_evaluation_date: str = ""
    experiment_sources: tuple[str, ...] = ()
    notes: str = ""

    @property
    def has_evidence(self) -> bool:
        """Whether any research data exists for this strategy."""
        return self.sample_size > 0

    @property
    def meets_activation_criteria(self) -> bool:
        """Whether evidence is strong enough for ACTIVE status."""
        return (
            self.sample_size >= 100
            and self.expectancy_r > 0
            and self.p_value < 0.05
            and self.walk_forward_validated
            and self.out_of_sample_validated
        )


@dataclass(frozen=True)
class StrategyDefinition:
    """
    Complete definition of a trading strategy.

    A strategy is a research hypothesis about how to exploit a specific
    market behaviour. It is NOT a trading system until validated and activated.

    Required fields define the strategy's identity and conditions.
    Evidence fields track research progress.
    """
    # Identity
    strategy_id: str
    name: str
    description: str

    # Classification
    strategy_family: StrategyFamily
    valid_market_phases: tuple[str, ...]

    # Context requirements
    required_context: tuple[str, ...] = ()   # e.g. ("h4_regime", "market_phase", "h1_bias")

    # Signal definition
    trigger_patterns: tuple[str, ...] = ()   # Patterns that can trigger this strategy
    entry_conditions: tuple[str, ...] = ()   # Additional conditions beyond pattern
    invalidation_conditions: tuple[str, ...] = ()  # What cancels the setup

    # Risk and exit
    risk_model: RiskModel = field(default_factory=RiskModel)
    exit_model: ExitModel = field(default_factory=ExitModel)

    # Research status
    status: StrategyStatus = StrategyStatus.HYPOTHESIS
    evidence_status: EvidenceStatus = field(default_factory=EvidenceStatus)

    # Metadata
    version: str = "1.0"
    author: str = "research_engine"
    created_date: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Whether this strategy is currently influencing decisions."""
        return self.status == StrategyStatus.ACTIVE

    @property
    def is_hypothesis(self) -> bool:
        """Whether this strategy is still just an idea."""
        return self.status == StrategyStatus.HYPOTHESIS

    @property
    def can_activate(self) -> bool:
        """Whether evidence supports activation."""
        return (
            self.status == StrategyStatus.VALIDATED
            and self.evidence_status.meets_activation_criteria
        )

    @property
    def family_name(self) -> str:
        return self.strategy_family.value


@dataclass(frozen=True)
class StrategyEvaluationResult:
    """
    Result of evaluating a strategy against the current market context.

    Produced by StrategyAuthority when assessing which strategies are
    eligible for the current conditions.
    """
    strategy_id: str
    eligible: bool
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def reason_summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "No reasons provided"
