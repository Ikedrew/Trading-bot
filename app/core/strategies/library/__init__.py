"""
Strategy Knowledge Library — Pure knowledge representation.

Contains strategy definitions, family taxonomy, and query helpers.
No trading logic. No execution. No scoring. Observation only.

Usage:
    from core.strategies.library import (
        get_strategy,
        get_strategies_for_context,
        strategy_library_report,
    )

    # Query strategies for current market
    eligible = get_strategies_for_context(phase="IMPULSE", regime="TRENDING")

    # Get full report
    print(strategy_library_report())
"""

from core.strategies.library.models import (
    ConfidenceLevel,
    EvidenceStatus,
    FamilyDefinition,
    StrategyDefinition,
    StrategyFamily,
)
from core.strategies.library.registry import (
    FAMILY_DEFINITIONS,
    STRATEGY_LIBRARY,
    get_all_family_definitions,
    get_all_strategies,
    get_family_definition,
    get_strategies_by_family,
    get_strategies_for_context,
    get_strategies_for_phase,
    get_strategies_for_regime,
    get_strategy,
    get_strategy_ids,
)
from core.strategies.library.diagnostics import (
    context_query_report,
    get_library_summary,
    strategy_library_report,
)

__all__ = [
    # Models
    "ConfidenceLevel",
    "EvidenceStatus",
    "FamilyDefinition",
    "StrategyDefinition",
    "StrategyFamily",
    # Registry
    "FAMILY_DEFINITIONS",
    "STRATEGY_LIBRARY",
    "get_all_family_definitions",
    "get_all_strategies",
    "get_family_definition",
    "get_strategies_by_family",
    "get_strategies_for_context",
    "get_strategies_for_phase",
    "get_strategies_for_regime",
    "get_strategy",
    "get_strategy_ids",
    # Diagnostics
    "context_query_report",
    "get_library_summary",
    "strategy_library_report",
]
