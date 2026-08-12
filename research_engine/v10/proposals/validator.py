"""
Proposal Validator.

Compares baseline vs candidate metrics to determine whether
a proposed change improves the target metric without critical regressions.

Read-only. Never modifies the trading system.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.proposals.model import (
    Candidate,
    ChangeProposal,
    ValidationResult,
    ValidationStatus,
)


class ProposalValidator:
    """
    Validates a candidate against a baseline using research evidence.

    Does NOT execute trades or modify the trading system.
    Compares analytical metrics only.
    """

    def validate(
        self,
        proposal: ChangeProposal,
        candidate: Candidate,
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        target_metric: str = "mean_r",
        min_improvement: float = 0.0,
        critical_metrics: list[str] | None = None,
        sample_sizes: dict[str, int] | None = None,
        universe_versions: dict[str, str] | None = None,
        population_versions: dict[str, str] | None = None,
    ) -> ValidationResult:
        """
        Compare baseline and candidate metrics.

        Args:
            proposal: The change proposal being validated.
            candidate: The candidate configuration.
            baseline_metrics: Metrics from the current/baseline system.
            candidate_metrics: Metrics from the candidate system.
            target_metric: Primary metric to evaluate improvement.
            min_improvement: Minimum improvement threshold.
            critical_metrics: Metrics that must not regress.
            sample_sizes: Sample size information.
            universe_versions: For reproducibility.
            population_versions: For reproducibility.

        Returns:
            ValidationResult with status and comparison.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        vid = f"val_{proposal.proposal_id}_{uuid.uuid4().hex[:4]}"
        critical = critical_metrics or []
        samples = sample_sizes or {}

        # Compute deltas
        delta: dict[str, Any] = {}
        for key in set(list(baseline_metrics.keys()) + list(candidate_metrics.keys())):
            b_val = baseline_metrics.get(key)
            c_val = candidate_metrics.get(key)
            if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
                delta[key] = round(c_val - b_val, 6)

        # Check target improvement
        target_baseline = baseline_metrics.get(target_metric)
        target_candidate = candidate_metrics.get(target_metric)
        improvement = False
        target_delta = 0.0

        if isinstance(target_baseline, (int, float)) and isinstance(target_candidate, (int, float)):
            target_delta = target_candidate - target_baseline
            improvement = target_delta > min_improvement

        # Check critical regressions
        regression = False
        regression_details: list[str] = []
        for cm in critical:
            b = baseline_metrics.get(cm)
            c = candidate_metrics.get(cm)
            if isinstance(b, (int, float)) and isinstance(c, (int, float)):
                if c < b:  # Any decrease in critical metric = regression
                    regression = True
                    regression_details.append(f"{cm}: {b} → {c} (regression)")

        # Determine status
        limitations: list[str] = []

        # Sample sufficiency check
        min_sample = samples.get("minimum_required", 20)
        analytical = samples.get("analytical_sample", 0)
        if analytical < min_sample:
            limitations.append(f"Insufficient sample: {analytical} < {min_sample}")

        if limitations:
            status = ValidationStatus.BLOCKED.value
        elif regression:
            status = ValidationStatus.REJECTED.value
            limitations.extend(regression_details)
        elif improvement:
            status = ValidationStatus.VALIDATED.value
        elif target_baseline is None or target_candidate is None:
            status = ValidationStatus.INCONCLUSIVE.value
            limitations.append(f"Target metric '{target_metric}' missing from one or both sets")
        else:
            status = ValidationStatus.INCONCLUSIVE.value
            limitations.append(f"No meaningful improvement: delta={target_delta:.4f}")

        return ValidationResult(
            validation_id=vid,
            proposal_id=proposal.proposal_id,
            candidate_id=candidate.candidate_id,
            baseline_identity="current_production",
            candidate_identity=candidate.candidate_id,
            universe_versions=universe_versions or {},
            population_versions=population_versions or {},
            analysis_version="1.0.0",
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            delta_metrics=delta,
            sample_sizes=samples,
            status=status,
            improvement_detected=improvement,
            regression_detected=regression,
            target_metric=target_metric,
            target_improvement=round(target_delta, 6),
            limitations=limitations,
        )
