"""
Investigation Contracts — Category-driven mapping from FindingTrigger to experiment.

Defines the exact semantics for how each TriggerCategory should be investigated:
- Population scope and filters
- Simulation specification
- Experiment type
- Conditioning variable
- Whether it's supported in the current architecture

This replaces the hard-coded pattern_filter + direction="INVERT" assumption
in _investigate_eligible().

This module NEVER modifies production V10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_engine.lifecycle.finding_trigger import FindingTrigger, TriggerCategory
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition,
    ExperimentType,
    PopulationSpec,
    SimulationSpec,
)


@dataclass(frozen=True)
class InvestigationContract:
    """Defines how a trigger category should be investigated."""
    category: TriggerCategory
    experiment_type: ExperimentType
    direction: str                      # "INVERT" | "SAME"
    stop_multiplier: float = 1.0
    tp_multiplier: float = 1.0          # 1.0 = original TP; 3.0 = 3R TP for inversions
    population_scope: str = "PATTERN"   # "PATTERN" | "SYMBOL" | "FULL"
    conditioning_variable: str = ""     # "" | "symbol" | "regime" | "score" | "risk_distance" | "time"
    requires_pattern_filter: bool = True
    requires_symbol_filter: bool = False
    supported: bool = True
    unsupported_reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# THE CANONICAL CONTRACT MAP
# ═══════════════════════════════════════════════════════════════════════════════

INVESTIGATION_CONTRACTS: dict[TriggerCategory, InvestigationContract] = {
    TriggerCategory.POOR_PATTERN_PERFORMANCE: InvestigationContract(
        category=TriggerCategory.POOR_PATTERN_PERFORMANCE,
        experiment_type=ExperimentType.DIRECTION_INVERSION,
        direction="INVERT",
        tp_multiplier=3.0,
        population_scope="PATTERN",
        requires_pattern_filter=True,
    ),
    TriggerCategory.DIRECTION_ASYMMETRY: InvestigationContract(
        category=TriggerCategory.DIRECTION_ASYMMETRY,
        experiment_type=ExperimentType.DIRECTION_INVERSION,
        direction="INVERT",
        tp_multiplier=3.0,
        population_scope="PATTERN",
        requires_pattern_filter=True,
    ),
    TriggerCategory.STRONG_PATTERN_PERFORMANCE: InvestigationContract(
        category=TriggerCategory.STRONG_PATTERN_PERFORMANCE,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        tp_multiplier=1.0,
        population_scope="PATTERN",
        requires_pattern_filter=True,
    ),
    TriggerCategory.REGIME_ANOMALY: InvestigationContract(
        category=TriggerCategory.REGIME_ANOMALY,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        tp_multiplier=1.0,
        population_scope="FULL",
        conditioning_variable="regime",
        requires_pattern_filter=False,
    ),
    TriggerCategory.SYMBOL_ANOMALY: InvestigationContract(
        category=TriggerCategory.SYMBOL_ANOMALY,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        tp_multiplier=1.0,
        population_scope="SYMBOL",
        conditioning_variable="symbol",
        requires_pattern_filter=False,
        requires_symbol_filter=True,
    ),
    TriggerCategory.SCORE_MONOTONICITY: InvestigationContract(
        category=TriggerCategory.SCORE_MONOTONICITY,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        tp_multiplier=1.0,
        population_scope="FULL",
        conditioning_variable="score",
        requires_pattern_filter=False,
    ),
    TriggerCategory.GEOMETRY_ANOMALY: InvestigationContract(
        category=TriggerCategory.GEOMETRY_ANOMALY,
        experiment_type=ExperimentType.COUNTERFACTUAL_GEOMETRY,
        direction="SAME",
        stop_multiplier=1.5,  # Test wider stop
        tp_multiplier=1.0,
        population_scope="FULL",
        conditioning_variable="risk_distance",
        requires_pattern_filter=False,
    ),
    TriggerCategory.TEMPORAL_INSTABILITY: InvestigationContract(
        category=TriggerCategory.TEMPORAL_INSTABILITY,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        tp_multiplier=1.0,
        population_scope="FULL",
        conditioning_variable="time",
        requires_pattern_filter=False,
    ),
    TriggerCategory.EXECUTION_ANOMALY: InvestigationContract(
        category=TriggerCategory.EXECUTION_ANOMALY,
        experiment_type=ExperimentType.POPULATION_COMPARISON,
        direction="SAME",
        population_scope="FULL",
        supported=False,
        unsupported_reason="Requires execution-results data source not yet wired to shadow simulation",
    ),
    TriggerCategory.KNOWLEDGE_CONTRADICTION: InvestigationContract(
        category=TriggerCategory.KNOWLEDGE_CONTRADICTION,
        experiment_type=ExperimentType.ROBUSTNESS_CHECK,
        direction="SAME",
        population_scope="FULL",
        supported=False,
        unsupported_reason="Requires semantic fact-to-metric comparison not yet implemented",
    ),
}


def get_contract(category: TriggerCategory) -> InvestigationContract:
    """Get the investigation contract for a trigger category."""
    return INVESTIGATION_CONTRACTS.get(category, InvestigationContract(
        category=category,
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        direction="SAME",
        supported=False,
        unsupported_reason=f"No contract defined for {category.value}",
    ))


def build_experiment_from_trigger(trigger: FindingTrigger, *,
                                   hypothesis_id: str,
                                   min_sample_size: int = 30,
                                   ) -> tuple[ExperimentDefinition | None, str]:
    """
    Build the correct ExperimentDefinition from a FindingTrigger using its contract.
    
    Returns (definition, "") on success, (None, reason) on failure.
    """
    contract = get_contract(trigger.category)

    if not contract.supported:
        return None, f"Category {trigger.category.value} not supported: {contract.unsupported_reason}"

    # Build population spec from contract
    pattern_filter = trigger.suggested_patterns if contract.requires_pattern_filter else []
    symbol_filter = []
    if contract.requires_symbol_filter:
        sym = trigger.evidence.get("symbol", "")
        if sym:
            symbol_filter = [sym]
        else:
            return None, "SYMBOL_ANOMALY requires symbol in evidence but none found"

    pop = PopulationSpec(
        pattern_filter=pattern_filter,
        symbol_filter=symbol_filter,
        min_sample_size=min_sample_size,
    )

    # Build simulation spec from contract
    sim = SimulationSpec(
        direction=contract.direction,
        stop_multiplier=contract.stop_multiplier,
        tp_multiplier=contract.tp_multiplier,
        max_bars=60,
    )

    defn = ExperimentDefinition(
        hypothesis_id=hypothesis_id,
        experiment_type=contract.experiment_type,
        title=f"Auto: {trigger.title}",
        description=f"Investigation of {trigger.category.value}: {trigger.observation}",
        population=pop,
        simulation=sim,
    )

    return defn, ""
