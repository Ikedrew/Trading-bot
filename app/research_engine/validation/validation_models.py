"""
Research Dataset Validation — Data Models.

Defines the structured output of dataset validation checks.
Used by dataset_validator.py and consumed by research experiments.

No imports from core pipeline. Pure data definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ValidationMode(str, Enum):
    """How the validator should behave on failure."""
    STRICT = "STRICT"      # Invalid dataset stops execution
    WARNING = "WARNING"    # Experiment runs but reports limitations


class DataSource(str, Enum):
    """Detected origin of the dataset."""
    REPLAY = "REPLAY"
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    TRADE_TRUTH = "TRADE_TRUTH"
    TEST = "TEST"
    UNKNOWN = "UNKNOWN"


class ResearchCategory(str, Enum):
    """Type of research being attempted — determines required fields."""
    PATTERN_ONLY = "PATTERN_ONLY"       # pattern + outcome required
    HTF_RESEARCH = "HTF_RESEARCH"       # H4 regime + H1 bias required
    PHASE_RESEARCH = "PHASE_RESEARCH"   # market_phase required
    EXECUTION = "EXECUTION"             # fill data required
    GENERAL = "GENERAL"                 # no special requirements


@dataclass(frozen=True)
class CoverageMetric:
    """Coverage measurement for a single field or field group."""
    field_name: str
    populated_count: int
    total_count: int
    unknown_count: int = 0  # Records where field exists but value is UNKNOWN/NEUTRAL/empty

    @property
    def coverage_pct(self) -> float:
        """Fraction of records with meaningful (non-unknown) values."""
        if self.total_count == 0:
            return 0.0
        return (self.populated_count - self.unknown_count) / self.total_count

    @property
    def populated_pct(self) -> float:
        """Fraction of records where field is present (including unknown values)."""
        if self.total_count == 0:
            return 0.0
        return self.populated_count / self.total_count


@dataclass(frozen=True)
class ValidationThresholds:
    """Configurable thresholds for research suitability."""
    htf_regime_min_coverage: float = 0.80    # 80% H4 regime required for HTF research
    h1_bias_min_coverage: float = 0.80       # 80% H1 bias required for HTF research
    market_phase_min_coverage: float = 0.80  # 80% phase required for phase research
    min_sample_size: int = 20                # Minimum records for any analysis
    min_sample_size_per_group: int = 5       # Minimum per cross-tab cell


@dataclass(frozen=True)
class ResearchValidationResult:
    """
    Complete validation output for a research dataset.

    Produced before experiment execution. Attached to report output.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    dataset_name: str
    source: DataSource
    total_records: int

    # ─── COVERAGE METRICS ─────────────────────────────────────────────
    h4_regime_coverage: CoverageMetric
    h1_bias_coverage: CoverageMetric
    market_phase_coverage: CoverageMetric
    pattern_coverage: CoverageMetric
    outcome_coverage: CoverageMetric  # R-multiple / exit_reason available
    lineage_coverage: CoverageMetric  # entity_id / correlation_id for research joins
    strategy_coverage: CoverageMetric  # Clean strategy identity (REVERSAL/CONTINUATION/FALSE_BREAK)
    horizon_coverage: CoverageMetric   # Trade horizon (SCALP/INTRADAY/EXTENDED)

    # ─── SUITABILITY ──────────────────────────────────────────────────
    suitable_for_htf_research: bool
    suitable_for_phase_research: bool
    suitable_for_pattern_research: bool
    suitable_for_execution_research: bool

    # ─── DIAGNOSTICS ──────────────────────────────────────────────────
    warnings: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    validation_passed: bool = True
    strategy_contaminated: int = 0     # Count of records with combined strategy_horizon format

    def to_dict(self) -> dict:
        """Serialize for inclusion in research reports."""
        return {
            "dataset_name": self.dataset_name,
            "source": self.source.value,
            "total_records": self.total_records,
            "coverage": {
                "h4_regime": round(self.h4_regime_coverage.coverage_pct, 4),
                "h1_bias": round(self.h1_bias_coverage.coverage_pct, 4),
                "market_phase": round(self.market_phase_coverage.coverage_pct, 4),
                "pattern": round(self.pattern_coverage.coverage_pct, 4),
                "outcome": round(self.outcome_coverage.coverage_pct, 4),
                "decision_lineage": round(self.lineage_coverage.coverage_pct, 4),
                "strategy": round(self.strategy_coverage.coverage_pct, 4),
                "horizon": round(self.horizon_coverage.coverage_pct, 4),
            },
            "suitability": {
                "htf_research": self.suitable_for_htf_research,
                "phase_research": self.suitable_for_phase_research,
                "pattern_research": self.suitable_for_pattern_research,
                "execution_research": self.suitable_for_execution_research,
            },
            "warnings": list(self.warnings),
            "missing_fields": list(self.missing_fields),
            "validation_passed": self.validation_passed,
        }
