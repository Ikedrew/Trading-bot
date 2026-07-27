"""
Horizon Research Report — Compares expected vs observed horizon behaviour.

Generates structured reports from HorizonResearchContract + HorizonObservation.
Reports are informational ONLY — they never modify profiles or execution.

THIS MODULE DOES NOT:
    - Affect execution decisions
    - Modify trade management
    - Update research contracts automatically
    - Gate any execution path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.horizon.research_contract import (
    HorizonResearchContract,
    HorizonObservation,
    ValidationStatus,
    compare_contract_to_observation,
    ContractAssessment,
    get_active_contract,
    ACTIVE_CONTRACT_VERSION,
)
from core.horizon.observation_builder import (
    build_horizon_observation,
    build_all_horizon_observations,
)


# ═══════════════════════════════════════════════════════════════════════════════
# OVERALL STATUS
# ═══════════════════════════════════════════════════════════════════════════════

class OverallStatus(str, Enum):
    """Overall horizon research assessment."""
    VALIDATED = "VALIDATED"
    PARTIALLY_VALIDATED = "PARTIALLY_VALIDATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HorizonResearchReport:
    """
    Complete research report for one horizon.

    Contains per-metric assessments, overall status, and recommendations.
    Serializable for persistence to S3/research outputs.
    """

    horizon: str
    contract_version: str
    observation_sample_size: int
    metric_assessments: list[ContractAssessment] = field(default_factory=list)
    overall_status: OverallStatus = OverallStatus.INSUFFICIENT_DATA
    recommendations: list[str] = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation."""
        return {
            "horizon": self.horizon,
            "contract_version": self.contract_version,
            "sample_size": self.observation_sample_size,
            "metrics": {
                a.field: {
                    "expected_min": a.expected_min,
                    "expected_max": a.expected_max,
                    "observed": a.observed,
                    "status": a.status.value,
                    "deviation_pct": a.deviation_pct,
                }
                for a in self.metric_assessments
            },
            "overall_status": self.overall_status.value,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_horizon_report(
    contract: HorizonResearchContract,
    observation: HorizonObservation,
    *,
    min_sample_size: int = 20,
) -> HorizonResearchReport:
    """
    Generate a research report comparing contract expectations to observation.

    Args:
        contract: Research hypothesis (expected values).
        observation: Measured reality.
        min_sample_size: Minimum trades for valid assessment.

    Returns:
        HorizonResearchReport with metric assessments, overall status, recommendations.
    """
    assessments = compare_contract_to_observation(
        contract, observation, min_sample_size=min_sample_size
    )

    overall = _compute_overall_status(assessments)
    recommendations = _generate_recommendations(contract, observation, assessments)

    return HorizonResearchReport(
        horizon=contract.horizon,
        contract_version=contract.profile_version,
        observation_sample_size=observation.sample_size,
        metric_assessments=assessments,
        overall_status=overall,
        recommendations=recommendations,
    )


def generate_all_horizon_reports(
    trades: list[Any],
    *,
    min_sample_size: int = 20,
) -> dict[str, HorizonResearchReport]:
    """
    Generate reports for all horizons from a mixed list of trades.

    Horizons with zero trades produce INSUFFICIENT_DATA reports (never fails).

    Args:
        trades: List of TradeRecord instances.
        min_sample_size: Minimum trades for valid assessment.

    Returns:
        Dict keyed by horizon name.
    """
    observations = build_all_horizon_observations(trades)
    reports: dict[str, HorizonResearchReport] = {}

    for horizon in ("SCALP", "INTRADAY", "EXTENDED"):
        contract = get_active_contract(horizon)
        obs = observations[horizon]

        if contract is None:
            reports[horizon] = HorizonResearchReport(
                horizon=horizon,
                contract_version="UNKNOWN",
                observation_sample_size=0,
                overall_status=OverallStatus.INSUFFICIENT_DATA,
                recommendations=["No research contract defined for this horizon."],
            )
        else:
            reports[horizon] = generate_horizon_report(
                contract, obs, min_sample_size=min_sample_size
            )

    return reports


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_overall_status(assessments: list[ContractAssessment]) -> OverallStatus:
    """
    Determine overall status from per-metric assessments.

    Rules:
        - If any metric is INSUFFICIENT_DATA → INSUFFICIENT_DATA
        - If all metrics VALIDATED → VALIDATED
        - If all metrics REVIEW_REQUIRED → REVIEW_REQUIRED
        - Mix of VALIDATED and REVIEW_REQUIRED → PARTIALLY_VALIDATED
    """
    if not assessments:
        return OverallStatus.INSUFFICIENT_DATA

    statuses = {a.status for a in assessments}

    if ValidationStatus.INSUFFICIENT_DATA in statuses:
        return OverallStatus.INSUFFICIENT_DATA

    if statuses == {ValidationStatus.VALIDATED}:
        return OverallStatus.VALIDATED

    if statuses == {ValidationStatus.REVIEW_REQUIRED}:
        return OverallStatus.REVIEW_REQUIRED

    return OverallStatus.PARTIALLY_VALIDATED


def _generate_recommendations(
    contract: HorizonResearchContract,
    observation: HorizonObservation,
    assessments: list[ContractAssessment],
) -> list[str]:
    """Generate human-readable recommendations from assessment results."""
    recs: list[str] = []

    for a in assessments:
        if a.status == ValidationStatus.INSUFFICIENT_DATA:
            recs.append(
                f"Insufficient data for {a.field} assessment "
                f"(need {int(a.expected_min)}+ samples, have {int(a.observed)})."
            )
        elif a.status == ValidationStatus.REVIEW_REQUIRED:
            if a.observed < a.expected_min:
                recs.append(
                    f"Observed {a.field} ({a.observed:.2f}) is below expected "
                    f"range [{a.expected_min:.2f}, {a.expected_max:.2f}]. "
                    f"Review assumption after additional samples."
                )
            else:
                recs.append(
                    f"Observed {a.field} ({a.observed:.2f}) exceeds expected "
                    f"range [{a.expected_min:.2f}, {a.expected_max:.2f}]. "
                    f"Review assumption after additional samples."
                )

    if not recs and assessments:
        recs.append("All metrics within expected ranges. Contract validated.")

    return recs
