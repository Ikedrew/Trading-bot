"""
Strategy Framework — Architecture for research-driven trading strategies.

This module defines the INTERFACE, REGISTRY, and AUTHORITY for trading strategies
without activating any of them. Strategies are research hypotheses that describe
how a particular market behaviour should be exploited.

Hierarchy:
    Strategy Family: "What behaviour are we exploiting?" (REVERSAL, MOMENTUM, etc.)
    Strategy:        "How do we exploit that behaviour?" (range_reversal_v1, etc.)
    Pattern:         "What trigger confirms the opportunity?" (HAMMER, etc.)

Current state: OBSERVATION ONLY.
    - 5 strategies registered as HYPOTHESIS
    - 0 strategies ACTIVE
    - No runtime behaviour is changed
    - No strategies influence trading decisions
    - Activation requires validated research evidence
"""

from core.strategies.models import (
    EvidenceStatus,
    ExitModel,
    RiskModel,
    StrategyDefinition,
    StrategyEvaluationResult,
    StrategyStatus,
)
from core.strategies.authority import StrategyAuthority
from core.strategies.registry import (
    STRATEGY_REGISTRY,
    get_active_strategies,
    get_all_strategies,
    get_status_distribution,
    get_strategies_by_family,
    get_strategies_by_status,
    get_strategy,
    get_strategy_ids,
)
from core.strategies.diagnostics import (
    format_diagnostic_report,
    get_summary_dict,
)

__all__ = [
    # Models
    "EvidenceStatus",
    "ExitModel",
    "RiskModel",
    "StrategyDefinition",
    "StrategyEvaluationResult",
    "StrategyStatus",
    # Authority
    "StrategyAuthority",
    # Registry
    "STRATEGY_REGISTRY",
    "get_active_strategies",
    "get_all_strategies",
    "get_status_distribution",
    "get_strategies_by_family",
    "get_strategies_by_status",
    "get_strategy",
    "get_strategy_ids",
    # Diagnostics
    "format_diagnostic_report",
    "get_summary_dict",
]
