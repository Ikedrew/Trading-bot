"""
Experiment Protocol — Defines composable experiment types for hypothesis testing.

Each protocol specifies:
    - What population to use
    - What counterfactual to simulate
    - What validation to apply
    - What controls to run

Protocols compose existing primitives from:
    - research_engine/experiments/experiment_base.py (data loading, fingerprinting)
    - research_engine/v10/research_governance/evidence_maturity.py (maturity assessment)
    - core/shadow_trades.py (shadow simulation methodology)

This module NEVER modifies production V10.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class ExperimentType(str, Enum):
    """Types of research experiments."""
    COUNTERFACTUAL_GEOMETRY = "COUNTERFACTUAL_GEOMETRY"     # Change SL/TP, keep direction
    DIRECTION_INVERSION = "DIRECTION_INVERSION"             # Flip direction, adjust geometry
    POPULATION_COMPARISON = "POPULATION_COMPARISON"         # Compare two populations
    CONDITIONING_ANALYSIS = "CONDITIONING_ANALYSIS"         # Segment by variable
    PLACEBO_CONTROL = "PLACEBO_CONTROL"                     # Run same test on unrelated population
    OOS_VALIDATION = "OOS_VALIDATION"                       # Chronological train/test split
    ROBUSTNESS_CHECK = "ROBUSTNESS_CHECK"                   # Outlier removal, symbol exclusion


class ExperimentStatus(str, Enum):
    """Execution status of an experiment."""
    DEFINED = "DEFINED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class PopulationSpec:
    """Defines which observations to include in an experiment."""
    pattern_filter: list[str] = field(default_factory=list)   # e.g. ["THREE_BLACK_CROWS"]
    symbol_filter: list[str] = field(default_factory=list)    # e.g. ["USDJPY", "EURUSD"]
    direction_filter: str = ""                                 # "BUY" | "SELL" | "" (all)
    require_correlation_id: bool = True                        # Exclude test/synthetic data
    min_sample_size: int = 30                                  # Minimum N for experiment
    epoch: str = "CURRENT"                                     # Data epoch


@dataclass
class SimulationSpec:
    """Defines how to simulate the counterfactual."""
    direction: str = ""              # Override direction ("BUY"/"SELL"/"SAME"/"INVERT")
    stop_multiplier: float = 1.0     # Multiplier for original risk distance
    tp_multiplier: float = 1.0       # Multiplier for original RR (e.g. 3.0 = 3R TP)
    max_bars: int = 60               # Horizon
    sl_checked_first: bool = True    # Conservative SL check order


@dataclass
class ValidationSpec:
    """Defines what validation to apply to results."""
    oos_split: float = 0.6                    # Training fraction (0.6 = 60/40 split)
    bootstrap_n: int = 2000                    # Bootstrap iterations
    bootstrap_ci: float = 0.90                 # Confidence interval level
    permutation_n: int = 5000                  # Permutation test iterations
    require_oos_positive: bool = True          # Must OOS be positive?
    require_ci_above_zero: bool = False        # Must CI lower bound > 0?
    bonferroni_tests: int = 1                  # Number of tests for correction
    min_symbols_positive: int = 3              # Minimum symbols with positive R


@dataclass
class ExperimentDefinition:
    """
    Complete definition of a research experiment.

    Composed from population + simulation + validation specs.
    Linked to a hypothesis via hypothesis_id.
    """
    experiment_id: str = ""
    hypothesis_id: str = ""
    experiment_type: ExperimentType = ExperimentType.COUNTERFACTUAL_GEOMETRY
    title: str = ""
    description: str = ""

    population: PopulationSpec = field(default_factory=PopulationSpec)
    simulation: SimulationSpec = field(default_factory=SimulationSpec)
    validation: ValidationSpec = field(default_factory=ValidationSpec)

    # Placebo specification (if applicable)
    placebo_patterns: list[str] = field(default_factory=list)  # Patterns to test as placebo
    placebo_min_positive_fraction: float = 0.5  # If > this fraction positive, hypothesis weakened

    status: ExperimentStatus = ExperimentStatus.DEFINED
    created_timestamp: str = ""

    def __post_init__(self):
        if not self.experiment_id:
            self.experiment_id = f"EXP-{uuid.uuid4().hex[:8]}"
        if not self.created_timestamp:
            self.created_timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "experiment_type": self.experiment_type.value,
            "title": self.title,
            "description": self.description,
            "population": {
                "pattern_filter": self.population.pattern_filter,
                "symbol_filter": self.population.symbol_filter,
                "direction_filter": self.population.direction_filter,
                "require_correlation_id": self.population.require_correlation_id,
                "min_sample_size": self.population.min_sample_size,
                "epoch": self.population.epoch,
            },
            "simulation": {
                "direction": self.simulation.direction,
                "stop_multiplier": self.simulation.stop_multiplier,
                "tp_multiplier": self.simulation.tp_multiplier,
                "max_bars": self.simulation.max_bars,
                "sl_checked_first": self.simulation.sl_checked_first,
            },
            "validation": {
                "oos_split": self.validation.oos_split,
                "bootstrap_n": self.validation.bootstrap_n,
                "bootstrap_ci": self.validation.bootstrap_ci,
                "permutation_n": self.validation.permutation_n,
                "require_oos_positive": self.validation.require_oos_positive,
                "bonferroni_tests": self.validation.bonferroni_tests,
                "min_symbols_positive": self.validation.min_symbols_positive,
            },
            "placebo_patterns": self.placebo_patterns,
            "placebo_min_positive_fraction": self.placebo_min_positive_fraction,
            "status": self.status.value,
            "created_timestamp": self.created_timestamp,
        }


@dataclass
class ExperimentResult:
    """
    Complete result of a research experiment execution.

    Contains all metrics needed for governance decisions.
    """
    experiment_id: str = ""
    hypothesis_id: str = ""
    status: str = "complete"

    # Primary metrics
    n: int = 0
    mean_r: float = 0.0
    median_r: float = 0.0
    total_r: float = 0.0
    win_rate: float = 0.0
    std_dev: float = 0.0

    # Confidence
    ci_lower: float | None = None
    ci_upper: float | None = None
    permutation_p: float | None = None

    # Exit distribution
    sl_rate: float = 0.0
    tp_rate: float = 0.0
    timeout_rate: float = 0.0

    # Excursion
    mean_mfe: float = 0.0
    mean_mae: float = 0.0

    # OOS
    oos_n: int = 0
    oos_mean_r: float = 0.0
    oos_ci_lower: float | None = None
    oos_ci_upper: float | None = None

    # Placebo
    placebo_positive_fraction: float = 0.0
    placebo_patterns_tested: int = 0
    placebo_passes: bool = True         # True if hypothesis is specific (not general)

    # Symbol robustness
    symbols_positive: int = 0
    symbols_total: int = 0
    survives_best_symbol_removal: bool = False

    # Outlier robustness
    survives_top10_removal: bool = False
    survives_top20_removal: bool = False
    top10_contribution_pct: float = 0.0

    # Temporal
    periods_positive: int = 0
    periods_total: int = 0

    # Governance assessment (computed)
    evidence_maturity: str = ""         # From evidence_maturity.py
    decision_status: str = ""           # From evidence_maturity.py assess_decision
    classification: str = ""            # GREEN / AMBER / RED

    # Metadata
    timestamp: str = ""
    duration_seconds: float = 0.0
    dataset_fingerprint: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @property
    def passes_validation(self) -> bool:
        """Does this result meet the minimum validation criteria?"""
        return (
            self.mean_r > 0
            and (self.oos_mean_r > 0 if self.oos_n > 0 else True)
            and self.placebo_passes
            and self.symbols_positive >= 3
            and self.periods_positive >= 2
        )
