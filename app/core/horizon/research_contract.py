"""
Horizon Research Contract — Expected behaviour definitions for horizon research.

SEPARATION OF CONCERNS:
    HorizonExecutionProfile → defines what the bot DOES (trade management).
    HorizonResearchContract → defines what we EXPECT a horizon trade to look like.

Research contracts are hypotheses. They do NOT affect execution.
They allow the research engine to compare expectation vs reality.

VERSIONING:
    Every contract has a profile_version (e.g., "SCALP_RESEARCH_V1").
    When expectations change, a NEW version is created (V2, V3, ...).
    Previous versions are NEVER overwritten — historical comparison preserved.

INTEGRATION (future):
    - Research Engine reads contracts to assess shadow trade quality.
    - Trade Journal attaches contract version to completed trades.
    - S3 research outputs include contract metadata.
    - Horizon performance reports compare observed vs expected.

THIS MODULE DOES NOT:
    - Affect execution decisions.
    - Modify trade management.
    - Change horizon classification.
    - Gate any execution path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON RESULT
# ═══════════════════════════════════════════════════════════════════════════════

class ValidationStatus(str, Enum):
    """Result of comparing observation against expectation."""
    VALIDATED = "VALIDATED"              # Observation within expected range
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # Observation outside expected range
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # Not enough samples to assess


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HorizonResearchContract:
    """
    Defines expected behaviour for one horizon. A research hypothesis.

    Attributes:
        horizon: Horizon name ("SCALP", "INTRADAY", "EXTENDED")
        profile_version: Versioned identifier (e.g., "SCALP_RESEARCH_V1")
        expected_move_min_pips: Minimum expected price move (pips)
        expected_move_max_pips: Maximum expected price move (pips)
        expected_hold_min_minutes: Minimum expected holding time
        expected_hold_max_minutes: Maximum expected holding time
        expected_rr: Expected reward-to-risk ratio
        expected_win_rate: Expected win rate (0.0–1.0)
        expected_mae_pips: Expected maximum adverse excursion (pips)
        expected_mfe_pips: Expected maximum favourable excursion (pips)
        notes: Human-readable description of the hypothesis
    """

    horizon: str
    profile_version: str

    # Expected price movement
    expected_move_min_pips: float = 0.0
    expected_move_max_pips: float = 0.0

    # Expected holding duration
    expected_hold_min_minutes: float = 0.0
    expected_hold_max_minutes: float = 0.0

    # Expected performance
    expected_rr: float = 0.0
    expected_win_rate: float = 0.0

    # Expected excursion
    expected_mae_pips: float = 0.0
    expected_mfe_pips: float = 0.0

    # Documentation
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation for persistence/reporting."""
        return {
            "horizon": self.horizon,
            "profile_version": self.profile_version,
            "expected_move": {
                "min_pips": self.expected_move_min_pips,
                "max_pips": self.expected_move_max_pips,
            },
            "expected_hold": {
                "min_minutes": self.expected_hold_min_minutes,
                "max_minutes": self.expected_hold_max_minutes,
            },
            "expected_rr": self.expected_rr,
            "expected_win_rate": self.expected_win_rate,
            "expected_mae_pips": self.expected_mae_pips,
            "expected_mfe_pips": self.expected_mfe_pips,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HorizonResearchContract":
        """Reconstruct from serialized dict."""
        _move = data.get("expected_move", {})
        _hold = data.get("expected_hold", {})
        return cls(
            horizon=data["horizon"],
            profile_version=data["profile_version"],
            expected_move_min_pips=float(_move.get("min_pips", 0.0)),
            expected_move_max_pips=float(_move.get("max_pips", 0.0)),
            expected_hold_min_minutes=float(_hold.get("min_minutes", 0.0)),
            expected_hold_max_minutes=float(_hold.get("max_minutes", 0.0)),
            expected_rr=float(data.get("expected_rr", 0.0)),
            expected_win_rate=float(data.get("expected_win_rate", 0.0)),
            expected_mae_pips=float(data.get("expected_mae_pips", 0.0)),
            expected_mfe_pips=float(data.get("expected_mfe_pips", 0.0)),
            notes=data.get("notes", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HorizonObservation:
    """
    Measured reality for one horizon over a sample of trades.

    Generated by the research engine from trade_truth + shadow_trades data.
    Compared against HorizonResearchContract to assess hypothesis validity.
    """

    horizon: str
    profile_version: str
    sample_size: int = 0

    # Observed price movement (pips)
    observed_move_average_pips: float = 0.0
    observed_move_median_pips: float = 0.0
    observed_move_p95_pips: float = 0.0

    # Observed holding duration (minutes)
    observed_hold_average_minutes: float = 0.0
    observed_hold_median_minutes: float = 0.0

    # Observed performance
    observed_rr: float = 0.0
    observed_win_rate: float = 0.0
    observed_profit_factor: float = 0.0
    observed_expectancy: float = 0.0

    # Observed excursion (pips)
    observed_mae_pips: float = 0.0
    observed_mfe_pips: float = 0.0

    # Exit breakdown
    exit_reasons: dict[str, int] = field(default_factory=dict)

    # Metadata
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation."""
        return {
            "horizon": self.horizon,
            "profile_version": self.profile_version,
            "sample_size": self.sample_size,
            "observed_move_average_pips": round(self.observed_move_average_pips, 2),
            "observed_move_median_pips": round(self.observed_move_median_pips, 2),
            "observed_move_p95_pips": round(self.observed_move_p95_pips, 2),
            "observed_hold_average_minutes": round(self.observed_hold_average_minutes, 1),
            "observed_hold_median_minutes": round(self.observed_hold_median_minutes, 1),
            "observed_rr": round(self.observed_rr, 3),
            "observed_win_rate": round(self.observed_win_rate, 4),
            "observed_profit_factor": round(self.observed_profit_factor, 3),
            "observed_expectancy": round(self.observed_expectancy, 4),
            "observed_mae_pips": round(self.observed_mae_pips, 2),
            "observed_mfe_pips": round(self.observed_mfe_pips, 2),
            "exit_reasons": self.exit_reasons,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HorizonObservation":
        """Reconstruct from serialized dict."""
        return cls(
            horizon=data["horizon"],
            profile_version=data["profile_version"],
            sample_size=int(data.get("sample_size", 0)),
            observed_move_average_pips=float(data.get("observed_move_average_pips", 0.0)),
            observed_move_median_pips=float(data.get("observed_move_median_pips", 0.0)),
            observed_move_p95_pips=float(data.get("observed_move_p95_pips", 0.0)),
            observed_hold_average_minutes=float(data.get("observed_hold_average_minutes", 0.0)),
            observed_hold_median_minutes=float(data.get("observed_hold_median_minutes", 0.0)),
            observed_rr=float(data.get("observed_rr", 0.0)),
            observed_win_rate=float(data.get("observed_win_rate", 0.0)),
            observed_profit_factor=float(data.get("observed_profit_factor", 0.0)),
            observed_expectancy=float(data.get("observed_expectancy", 0.0)),
            observed_mae_pips=float(data.get("observed_mae_pips", 0.0)),
            observed_mfe_pips=float(data.get("observed_mfe_pips", 0.0)),
            exit_reasons=data.get("exit_reasons", {}),
            generated_at=data.get("generated_at", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ContractAssessment:
    """Result of comparing one observation against one contract."""

    horizon: str
    profile_version: str
    field: str                          # Which metric was compared
    expected_min: float
    expected_max: float
    observed: float
    status: ValidationStatus
    deviation_pct: float = 0.0          # How far outside expected (0 if within)

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "profile_version": self.profile_version,
            "field": self.field,
            "expected_min": self.expected_min,
            "expected_max": self.expected_max,
            "observed": self.observed,
            "status": self.status.value,
            "deviation_pct": round(self.deviation_pct, 2),
        }


def compare_contract_to_observation(
    contract: HorizonResearchContract,
    observation: HorizonObservation,
    *,
    min_sample_size: int = 20,
) -> list[ContractAssessment]:
    """
    Compare an observation against a research contract.

    Returns a list of per-metric assessments. Each metric is independently
    evaluated as VALIDATED, REVIEW_REQUIRED, or INSUFFICIENT_DATA.

    Args:
        contract: The research hypothesis (expected values).
        observation: The measured reality.
        min_sample_size: Minimum trades needed for a valid assessment.

    Returns:
        List of ContractAssessment for each comparable metric.
    """
    if observation.sample_size < min_sample_size:
        return [
            ContractAssessment(
                horizon=contract.horizon,
                profile_version=contract.profile_version,
                field="sample_size",
                expected_min=float(min_sample_size),
                expected_max=float(min_sample_size),
                observed=float(observation.sample_size),
                status=ValidationStatus.INSUFFICIENT_DATA,
            )
        ]

    assessments: list[ContractAssessment] = []

    # Hold duration
    if contract.expected_hold_max_minutes > 0:
        assessments.append(_assess_range(
            contract, "hold_average_minutes",
            contract.expected_hold_min_minutes,
            contract.expected_hold_max_minutes,
            observation.observed_hold_average_minutes,
        ))

    # RR
    if contract.expected_rr > 0:
        # RR is a single target — assess within ±30% tolerance
        _rr_min = contract.expected_rr * 0.7
        _rr_max = contract.expected_rr * 1.3
        assessments.append(_assess_range(
            contract, "rr", _rr_min, _rr_max, observation.observed_rr,
        ))

    # Win rate
    if contract.expected_win_rate > 0:
        # Win rate tolerance: ±15 percentage points
        _wr_min = max(0.0, contract.expected_win_rate - 0.15)
        _wr_max = min(1.0, contract.expected_win_rate + 0.15)
        assessments.append(_assess_range(
            contract, "win_rate", _wr_min, _wr_max, observation.observed_win_rate,
        ))

    # Move (pips)
    if contract.expected_move_max_pips > 0:
        assessments.append(_assess_range(
            contract, "move_pips",
            contract.expected_move_min_pips,
            contract.expected_move_max_pips,
            observation.observed_move_average_pips,
        ))

    # MAE
    if contract.expected_mae_pips > 0:
        # MAE: observed should be <= expected (lower is better)
        assessments.append(_assess_range(
            contract, "mae_pips", 0.0, contract.expected_mae_pips,
            observation.observed_mae_pips,
        ))

    # MFE
    if contract.expected_mfe_pips > 0:
        # MFE: observed should be >= some fraction of expected
        _mfe_min = contract.expected_mfe_pips * 0.5
        _mfe_max = contract.expected_mfe_pips * 2.0
        assessments.append(_assess_range(
            contract, "mfe_pips", _mfe_min, _mfe_max,
            observation.observed_mfe_pips,
        ))

    return assessments


def _assess_range(
    contract: HorizonResearchContract,
    field: str,
    expected_min: float,
    expected_max: float,
    observed: float,
) -> ContractAssessment:
    """Assess whether observed falls within [expected_min, expected_max]."""
    if expected_min <= observed <= expected_max:
        return ContractAssessment(
            horizon=contract.horizon,
            profile_version=contract.profile_version,
            field=field,
            expected_min=expected_min,
            expected_max=expected_max,
            observed=observed,
            status=ValidationStatus.VALIDATED,
            deviation_pct=0.0,
        )

    # Calculate deviation percentage
    if observed < expected_min:
        _range = expected_max - expected_min if expected_max > expected_min else 1.0
        _dev = ((expected_min - observed) / _range) * 100
    else:
        _range = expected_max - expected_min if expected_max > expected_min else 1.0
        _dev = ((observed - expected_max) / _range) * 100

    return ContractAssessment(
        horizon=contract.horizon,
        profile_version=contract.profile_version,
        field=field,
        expected_min=expected_min,
        expected_max=expected_max,
        observed=observed,
        status=ValidationStatus.REVIEW_REQUIRED,
        deviation_pct=round(_dev, 2),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# V1 RESEARCH CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

SCALP_RESEARCH_V1 = HorizonResearchContract(
    horizon="SCALP",
    profile_version="SCALP_RESEARCH_V1",
    expected_move_min_pips=3.0,
    expected_move_max_pips=15.0,
    expected_hold_min_minutes=2.0,
    expected_hold_max_minutes=90.0,
    expected_rr=2.0,
    expected_win_rate=0.45,
    expected_mae_pips=5.0,
    expected_mfe_pips=8.0,
    notes="Short-duration M5 structure trade. Quick resolution. Thesis: price reacts to M5 candle geometry within 1-2 bars.",
)

INTRADAY_RESEARCH_V1 = HorizonResearchContract(
    horizon="INTRADAY",
    profile_version="INTRADAY_RESEARCH_V1",
    expected_move_min_pips=10.0,
    expected_move_max_pips=50.0,
    expected_hold_min_minutes=30.0,
    expected_hold_max_minutes=240.0,
    expected_rr=3.0,
    expected_win_rate=0.40,
    expected_mae_pips=12.0,
    expected_mfe_pips=25.0,
    notes="Multi-hour M15/H1 structure trade. Thesis: price respects M15 structure levels within the session.",
)

EXTENDED_RESEARCH_V1 = HorizonResearchContract(
    horizon="EXTENDED",
    profile_version="EXTENDED_RESEARCH_V1",
    expected_move_min_pips=30.0,
    expected_move_max_pips=150.0,
    expected_hold_min_minutes=120.0,
    expected_hold_max_minutes=720.0,
    expected_rr=4.0,
    expected_win_rate=0.35,
    expected_mae_pips=25.0,
    expected_mfe_pips=60.0,
    notes="Multi-session H1/H4 structure trade. Thesis: confirmed H4 trend + H1 BOS produces sustained directional move.",
)

# Registry of all active research contracts (keyed by profile_version)
RESEARCH_CONTRACTS: dict[str, HorizonResearchContract] = {
    "SCALP_RESEARCH_V1": SCALP_RESEARCH_V1,
    "INTRADAY_RESEARCH_V1": INTRADAY_RESEARCH_V1,
    "EXTENDED_RESEARCH_V1": EXTENDED_RESEARCH_V1,
}

# Current active version per horizon
ACTIVE_CONTRACT_VERSION: dict[str, str] = {
    "SCALP": "SCALP_RESEARCH_V1",
    "INTRADAY": "INTRADAY_RESEARCH_V1",
    "EXTENDED": "EXTENDED_RESEARCH_V1",
}


def get_active_contract(horizon: str) -> HorizonResearchContract | None:
    """Get the currently active research contract for a horizon."""
    _version = ACTIVE_CONTRACT_VERSION.get(horizon.upper())
    if _version is None:
        return None
    return RESEARCH_CONTRACTS.get(_version)


def get_contract_by_version(version: str) -> HorizonResearchContract | None:
    """Get a specific versioned contract (for historical comparison)."""
    return RESEARCH_CONTRACTS.get(version)
