"""
Strategy Family Authority — Architectural scaffold for strategy family selection.

This module defines the INTERFACE and REGISTRY for strategy families without
implementing any trading behaviour. Strategy families will plug in here once
research validates which families produce positive expectancy in which conditions.

Architecture:
    MarketContext (regime + phase)
        ↓
    StrategyFamilyAuthority (selects eligible families)
        ↓
    Pattern Detection (scoped to eligible family patterns)
        ↓
    Scoring + Decision

Current state: SCAFFOLD ONLY.
    - No families are active for filtering
    - No runtime behaviour is changed
    - The authority returns ALL patterns as eligible (passthrough)
    - Activation requires validated research evidence (M9, M10 complete)

Activation gate (from research_engine/reports/strategy_context_alignment.md):
    - Minimum 100 trades in specific phase x family combination
    - EV significantly > 0 (p < 0.05)
    - Walk-forward validated
    - No contamination in dataset
"""

from core.strategy_family.models import (
    EligibilityReason,
    FamilyEligibility,
    FamilySelectionResult,
    PatternClassification,
    ResearchValidation,
    StrategyFamily,
)
from core.strategy_family.authority import StrategyFamilyAuthority
from core.strategy_family.registry import (
    EMPTY_FAMILIES,
    FAMILY_REGISTRY,
    classify_pattern,
    get_all_known_patterns,
    get_family_distribution,
    get_patterns_for_family,
    is_known_pattern,
)
from core.strategy_family.diagnostics import (
    format_diagnostic_report,
    format_pattern_report,
    get_summary_dict,
)

__all__ = [
    # Models
    "EligibilityReason",
    "FamilyEligibility",
    "FamilySelectionResult",
    "PatternClassification",
    "ResearchValidation",
    "StrategyFamily",
    # Authority
    "StrategyFamilyAuthority",
    # Registry
    "EMPTY_FAMILIES",
    "FAMILY_REGISTRY",
    "classify_pattern",
    "get_all_known_patterns",
    "get_family_distribution",
    "get_patterns_for_family",
    "is_known_pattern",
    # Diagnostics
    "format_diagnostic_report",
    "format_pattern_report",
    "get_summary_dict",
]
