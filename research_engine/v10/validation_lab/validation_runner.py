"""
Validation Lab — Main runner that orchestrates the full validation flow.

Flow:
    Candidate → Load baseline → Replay → Compare → Regression check → Decision → Report
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.validation_lab.models import ValidationRun, ValidationStatus, ValidationDecision
from research_engine.v10.validation_lab.replay_engine import ReplayEngine
from research_engine.v10.validation_lab.comparison_engine import compare_metrics
from research_engine.v10.validation_lab.regression_checker import check_regressions
from research_engine.v10.research_governance.evidence_maturity import (
    assess_maturity, estimate_consistency,
)
from research_engine.v10.research_governance.confidence_engine import ConfidenceEngine

logger = logging.getLogger(__name__)

_REPORTS_DIR = "reports/research/validation"


class ValidationRunner:
    """
    Orchestrates complete candidate validation against a frozen baseline.

    Does NOT deploy changes. Produces evidence only.
    """

    def __init__(
        self,
        universe_file: str | None = None,
        reports_dir: str | None = None,
    ):
        self._replay = ReplayEngine(universe_file=universe_file)
        self._reports_dir = Path(reports_dir or _REPORTS_DIR)
        self._confidence_engine = ConfidenceEngine()

    def validate(
        self,
        candidate_id: str,
        changes: dict[str, Any],
        baseline_id: str = "",
        filters: dict[str, str] | None = None,
    ) -> ValidationRun:
        """
        Run a complete validation of a candidate against the baseline.

        Args:
            candidate_id: Identifier for this candidate
            changes: Parameter changes to evaluate (see ReplayEngine)
            baseline_id: Reference baseline snapshot ID
            filters: Optional segmentation filters (instrument, regime, etc.)

        Returns:
            ValidationRun with decision, confidence, and regression status.
        """
        validation_id = f"VAL_{candidate_id}_{int(time.time()) % 100000}"

        run = ValidationRun(
            validation_id=validation_id,
            candidate_id=candidate_id,
            baseline_id=baseline_id,
            dataset_filters=filters or {},
            status=ValidationStatus.RUNNING,
        )

        try:
            # Compute baseline metrics
            baseline_metrics = self._replay.baseline_metrics(filters)
            run.baseline_metrics = baseline_metrics

            # Compute candidate metrics
            candidate_metrics = self._replay.candidate_metrics(changes, filters)
            run.candidate_metrics = candidate_metrics

            # Compare
            comparison = compare_metrics(baseline_metrics, candidate_metrics)
            run.comparison = comparison

            # Regression check
            reg_result = check_regressions(baseline_metrics, candidate_metrics)
            run.regressions = reg_result["regressions"]

            # Determine sample size and governance
            sample = candidate_metrics.get("count", 0) or candidate_metrics.get("sample_size", 0)
            run.sample_size = sample

            # Apply governance
            exp_delta = (candidate_metrics.get("expectancy_r", 0) or 0) - (baseline_metrics.get("expectancy_r", 0) or 0)
            consistency = estimate_consistency(candidate_metrics)
            maturity = assess_maturity(sample, abs(exp_delta), consistency)
            run.evidence_maturity = maturity

            conf = self._confidence_engine.assess(
                sample_size=sample,
                effect_size=abs(exp_delta),
                recommendation="SUPPORTED" if exp_delta > 0 else "REJECTED",
            )
            run.confidence = conf["confidence"]

            # Decision
            decision = self._make_decision(comparison, reg_result, sample, exp_delta)
            run.decision = decision["decision"]
            run.recommendation = decision["recommendation"]
            run.limitations = decision["limitations"]
            run.population_description = _describe_population(filters, sample)

            run.status = ValidationStatus.COMPLETED

        except Exception as exc:
            run.status = ValidationStatus.FAILED
            run.limitations = [f"Validation failed: {type(exc).__name__}: {exc}"]
            run.decision = ValidationDecision.INCONCLUSIVE

        # Save report
        self._save_report(run)
        return run

    def _make_decision(
        self,
        comparison: dict,
        regression: dict,
        sample: int,
        exp_delta: float,
    ) -> dict[str, Any]:
        """Determine validation decision."""
        limitations = []

        if sample < 10:
            limitations.append(f"Very small sample (n={sample})")
            return {
                "decision": ValidationDecision.INCONCLUSIVE,
                "recommendation": "Insufficient data for validation",
                "limitations": limitations,
            }

        if regression["status"] == "SEVERE_REGRESSION":
            return {
                "decision": ValidationDecision.REGRESSION,
                "recommendation": "Candidate causes severe regression. Do not proceed.",
                "limitations": limitations,
            }

        if regression["status"] == "REGRESSION_DETECTED" and exp_delta <= 0:
            return {
                "decision": ValidationDecision.REGRESSION,
                "recommendation": "Candidate regresses without improvement. Reject.",
                "limitations": limitations,
            }

        if exp_delta > 0.05 and not regression["regressions_detected"]:
            if sample >= 30:
                return {
                    "decision": ValidationDecision.IMPROVED,
                    "recommendation": "Candidate shows improvement. Continue testing with forward data.",
                    "limitations": limitations,
                }
            else:
                limitations.append(f"Sample size (n={sample}) limits confidence")
                return {
                    "decision": ValidationDecision.IMPROVED,
                    "recommendation": "Promising improvement but sample is limited. Continue collecting data.",
                    "limitations": limitations,
                }

        if exp_delta > 0 and regression["regressions_detected"]:
            limitations.append("Improvement comes with regressions in other areas")
            return {
                "decision": ValidationDecision.INCONCLUSIVE,
                "recommendation": "Mixed results. Improvement detected but regressions exist. Investigate trade-offs.",
                "limitations": limitations,
            }

        return {
            "decision": ValidationDecision.NO_IMPROVEMENT,
            "recommendation": "Candidate does not improve baseline performance.",
            "limitations": limitations,
        }

    def _save_report(self, run: ValidationRun) -> None:
        """Save validation report."""
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        path = self._reports_dir / f"{run.validation_id}.json"
        path.write_text(json.dumps(run.to_dict(), indent=2, default=str), encoding="utf-8")

        md_path = self._reports_dir / f"{run.validation_id}.md"
        md_path.write_text(_build_md(run), encoding="utf-8")


def _describe_population(filters: dict[str, str] | None, sample: int) -> str:
    if not filters:
        return f"FULL ({sample} events)"
    parts = [f"{k}={v}" for k, v in sorted(filters.items())]
    return f"{' + '.join(parts)} ({sample} events)"


def _build_md(run: ValidationRun) -> str:
    md = []
    md.append(f"# Validation Report: {run.validation_id}")
    md.append("")
    md.append(f"**Candidate:** {run.candidate_id}")
    md.append(f"**Baseline:** {run.baseline_id}")
    md.append(f"**Decision:** {run.decision}")
    md.append(f"**Confidence:** {run.confidence}")
    md.append(f"**Sample:** {run.sample_size}")
    md.append("")

    if run.comparison.get("changes"):
        md.append("## Metrics Comparison")
        md.append("")
        md.append("| Metric | Baseline | Candidate | Delta |")
        md.append("|---|---|---|---|")
        for metric, vals in run.comparison["changes"].items():
            md.append(f"| {metric} | {vals['before']} | {vals['after']} | {vals['delta']:+.4f} |")
        md.append("")

    if run.regressions:
        md.append("## Regressions")
        md.append("")
        for r in run.regressions:
            md.append(f"- **{r['metric']}**: {r['baseline']} -> {r['candidate']} ({r['severity']})")
        md.append("")

    md.append(f"**Recommendation:** {run.recommendation}")
    if run.limitations:
        md.append(f"\n**Limitations:** {', '.join(run.limitations)}")
    md.append("\n---")
    return "\n".join(md)
