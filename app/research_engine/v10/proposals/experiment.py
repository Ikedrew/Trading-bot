"""
Candidate Experiment Runner.

Evaluates a candidate change against historical evidence by comparing
baseline and candidate metrics on the SAME population.

The experiment:
    - Takes historical evidence (from built universes)
    - Evaluates the baseline (current system as-is)
    - Evaluates the candidate (applying candidate filter/configuration)
    - Produces comparable metrics for both
    - Feeds the existing ValidationResult and PromotionGate

NEVER modifies live trading. NEVER executes against the broker.
All results are COUNTERFACTUAL research artifacts.

Candidate types:
    POPULATION_FILTER — segments existing population by a condition
    CONFIGURATION — applies a parameter change to the analytical model

If a candidate cannot be safely evaluated from historical data:
    status = BLOCKED
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from research_engine.v10.proposals.model import (
    Candidate,
    ChangeProposal,
    ValidationResult,
    ValidationStatus,
)
from research_engine.v10.proposals.validator import ProposalValidator
from research_engine.v10.proposals.promotion import PromotionGate


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT MODEL
# ═══════════════════════════════════════════════════════════════════════════════


class CandidateType:
    POPULATION_FILTER = "POPULATION_FILTER"
    CONFIGURATION = "CONFIGURATION"
    CODE_CHANGE = "CODE_CHANGE"


@dataclass
class ExperimentResult:
    """
    Complete experiment comparing baseline vs candidate.

    All candidate results are COUNTERFACTUAL — they represent what
    would have happened, not what actually happened in production.
    """
    experiment_id: str = ""
    proposal_id: str = ""
    candidate_id: str = ""
    candidate_type: str = ""

    # Identity
    baseline_identity: str = "current_production"
    candidate_identity: str = ""

    # Versions (for reproducibility)
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    # Populations
    baseline_population_size: int = 0
    candidate_population_size: int = 0

    # Metrics
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    delta_metrics: dict[str, Any] = field(default_factory=dict)

    # Result
    status: str = "PENDING"  # COMPLETED, BLOCKED, ERROR
    blocked_reason: str = ""

    # Timing
    started_at: str = ""
    completed_at: str = ""

    # Provenance
    provenance: str = "COUNTERFACTUAL"
    governance_note: str = (
        "This experiment result is COUNTERFACTUAL research evidence. "
        "It does not represent observed production outcomes and "
        "cannot modify the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "proposal_id": self.proposal_id,
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "baseline_identity": self.baseline_identity,
            "candidate_identity": self.candidate_identity,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "baseline_population_size": self.baseline_population_size,
            "candidate_population_size": self.candidate_population_size,
            "baseline_metrics": self.baseline_metrics,
            "candidate_metrics": self.candidate_metrics,
            "delta_metrics": self.delta_metrics,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "provenance": self.provenance,
            "governance_note": self.governance_note,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════


def calculate_metrics(population: list[dict[str, Any]], r_field: str = "r_multiple") -> dict[str, Any]:
    """
    Calculate standard research metrics from a population.

    Reuses the same analytical approach as the ExpectancyPrimitive.
    """
    r_values = [r[r_field] for r in population if r.get(r_field) is not None]
    n = len(r_values)

    if n == 0:
        return {"sample_size": 0, "mean_r": None, "win_rate": None}

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]
    mean_r = statistics.mean(r_values)
    total_r = sum(r_values)
    win_rate = len(wins) / n

    metrics: dict[str, Any] = {
        "sample_size": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "mean_r": round(mean_r, 4),
        "median_r": round(statistics.median(r_values), 4),
        "total_r": round(total_r, 4),
        "profit_factor": round(abs(sum(wins) / sum(losses)), 4) if losses and sum(losses) != 0 else None,
    }

    if n > 1:
        metrics["std_r"] = round(statistics.stdev(r_values), 4)

    # Max drawdown (sequential R)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_values:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    metrics["max_drawdown_r"] = round(max_dd, 4)

    return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER
# ═══════════════════════════════════════════════════════════════════════════════


class ExperimentRunner:
    """
    Runs a candidate experiment against historical evidence.

    Evaluates both baseline and candidate on the same historical data,
    produces comparable metrics, and feeds the existing validation framework.

    Usage:
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=proposal,
            candidate=candidate,
            population=execution_records,
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
    """

    def run_filter_experiment(
        self,
        proposal: ChangeProposal,
        candidate: Candidate,
        population: list[dict[str, Any]],
        candidate_filter: Callable[[dict[str, Any]], bool],
        r_field: str = "r_multiple",
        universe_versions: dict[str, str] | None = None,
        population_versions: dict[str, str] | None = None,
    ) -> ExperimentResult:
        """
        Run a POPULATION_FILTER experiment.

        The candidate filter selects which trades the candidate system
        WOULD have taken. The baseline is the full population (all trades
        the current system actually took).

        Args:
            proposal: The governing proposal.
            candidate: The candidate being tested.
            population: Historical trade records with r_multiple.
            candidate_filter: Function returning True for trades the candidate keeps.
            r_field: Field containing R-multiple values.
            universe_versions: For reproducibility.
            population_versions: For reproducibility.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        eid = f"exp_{proposal.proposal_id}_{uuid.uuid4().hex[:4]}"

        result = ExperimentResult(
            experiment_id=eid,
            proposal_id=proposal.proposal_id,
            candidate_id=candidate.candidate_id,
            candidate_type=CandidateType.POPULATION_FILTER,
            candidate_identity=candidate.candidate_id,
            universe_versions=universe_versions or {},
            population_versions=population_versions or {},
            started_at=now,
        )

        # Validate population
        if not population:
            result.status = "BLOCKED"
            result.blocked_reason = "Empty population — no historical evidence available"
            result.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return result

        # Baseline: full population (what the system actually did)
        baseline_metrics = calculate_metrics(population, r_field)
        result.baseline_population_size = baseline_metrics.get("sample_size", 0)
        result.baseline_metrics = baseline_metrics

        # Candidate: filtered population (what candidate would have done)
        try:
            candidate_population = [r for r in population if candidate_filter(r)]
        except Exception as e:
            result.status = "ERROR"
            result.blocked_reason = f"Candidate filter failed: {e}"
            result.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return result

        candidate_metrics = calculate_metrics(candidate_population, r_field)
        result.candidate_population_size = candidate_metrics.get("sample_size", 0)
        result.candidate_metrics = candidate_metrics

        # Compute deltas
        delta: dict[str, Any] = {}
        for key in baseline_metrics:
            b = baseline_metrics.get(key)
            c = candidate_metrics.get(key)
            if isinstance(b, (int, float)) and isinstance(c, (int, float)):
                delta[key] = round(c - b, 6)
        result.delta_metrics = delta

        result.status = "COMPLETED"
        result.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return result

    def run_code_change_experiment(
        self,
        proposal: ChangeProposal,
        candidate: Candidate,
    ) -> ExperimentResult:
        """
        Block a CODE_CHANGE candidate — cannot be evaluated from historical data alone.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ExperimentResult(
            experiment_id=f"exp_{proposal.proposal_id}_{uuid.uuid4().hex[:4]}",
            proposal_id=proposal.proposal_id,
            candidate_id=candidate.candidate_id,
            candidate_type=CandidateType.CODE_CHANGE,
            status="BLOCKED",
            blocked_reason="CODE_CHANGE candidates require implementation-specific testing that cannot be evaluated from historical data alone.",
            started_at=now,
            completed_at=now,
        )

    def to_validation_result(
        self,
        experiment: ExperimentResult,
        target_metric: str = "mean_r",
        min_improvement: float = 0.0,
        critical_metrics: list[str] | None = None,
        min_sample: int = 20,
    ) -> ValidationResult:
        """
        Convert an experiment result into the existing ValidationResult model.

        Feeds directly into the existing PromotionGate.
        """
        if experiment.status != "COMPLETED":
            return ValidationResult(
                validation_id=f"val_{experiment.experiment_id}",
                proposal_id=experiment.proposal_id,
                candidate_id=experiment.candidate_id,
                status=ValidationStatus.BLOCKED.value,
                limitations=[experiment.blocked_reason or "Experiment did not complete"],
            )

        # Use existing ProposalValidator for the comparison
        from research_engine.v10.proposals.model import ChangeProposal, Candidate as CandModel

        # Build minimal proposal/candidate for the validator interface
        dummy_proposal = ChangeProposal(proposal_id=experiment.proposal_id)
        dummy_candidate = CandModel(
            candidate_id=experiment.candidate_id,
            proposal_id=experiment.proposal_id,
        )

        validator = ProposalValidator()
        return validator.validate(
            proposal=dummy_proposal,
            candidate=dummy_candidate,
            baseline_metrics=experiment.baseline_metrics,
            candidate_metrics=experiment.candidate_metrics,
            target_metric=target_metric,
            min_improvement=min_improvement,
            critical_metrics=critical_metrics or [],
            sample_sizes={
                "analytical_sample": experiment.candidate_population_size,
                "minimum_required": min_sample,
            },
            universe_versions=experiment.universe_versions,
            population_versions=experiment.population_versions,
        )
