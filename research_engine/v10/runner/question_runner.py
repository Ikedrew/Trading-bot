"""
Generic Question Runner.

Resolves a question's contract → populations → primitives → finding.
Does NOT hard-code question-specific logic. Questions are declarative.

Flow:
    Question → Contract validation → Population resolution → Join resolution
    → Primitive execution → Evidence composition → ResearchFinding

A failed question produces an error finding rather than crashing the run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.control_plane.finding_schema import ResearchFinding
from research_engine.v10.runner.primitives.base import (
    AnalysisPrimitive,
    AnalysisRegistry,
    AnalysisResult,
)
from research_engine.v10.universes.models import NewEngineQuestion, Universe


# ═══════════════════════════════════════════════════════════════════════════════
# RUN CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RunContext:
    """
    Reproducible execution context for a research run.

    Contains all metadata needed to answer:
    "Exactly what data and logic produced this result?"
    """
    run_id: str = ""
    timestamp: str = ""
    engine_version: str = "1.0.0"
    question_bank_version: str = "1.0.0"
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)
    primitive_versions: dict[str, str] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = f"run_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "engine_version": self.engine_version,
            "question_bank_version": self.question_bank_version,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "primitive_versions": self.primitive_versions,
            "configuration": self.configuration,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION RUNNER
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class QuestionExecutionResult:
    """Result of running a single question."""
    question_id: str
    success: bool
    finding: ResearchFinding | None = None
    error: str = ""


class QuestionRunner:
    """
    Generic question execution engine.

    Resolves contracts, populations, and primitives to produce findings.
    Does NOT contain question-specific logic — that's in the primitives.
    """

    def __init__(
        self,
        registry: AnalysisRegistry,
        primitive_mapping: dict[str, list[str]] | None = None,
    ):
        """
        Args:
            registry: The analysis primitive registry.
            primitive_mapping: question_id → list of primitive names to execute.
        """
        self._registry = registry
        self._mapping = primitive_mapping or {}

    def run_question(
        self,
        question: NewEngineQuestion,
        population: list[dict[str, Any]],
        context: RunContext,
        parameters: dict[str, Any] | None = None,
    ) -> QuestionExecutionResult:
        """
        Execute a single question against a resolved population.

        Args:
            question: The question definition.
            population: Resolved, normalised population records.
            context: Reproducible run context.
            parameters: Question-specific parameters for primitives.

        Returns:
            QuestionExecutionResult with finding or error.
        """
        qid = question.question_id

        # 1. Determine which primitives to run
        primitive_names = self._resolve_primitives(question)
        if not primitive_names:
            return QuestionExecutionResult(
                question_id=qid, success=False,
                error=f"No primitives mapped for question {qid}",
            )

        # 2. Execute each primitive (isolated — one failure doesn't crash others)
        results: list[AnalysisResult] = []
        for pname in primitive_names:
            primitive = self._registry.get(pname)
            if primitive is None:
                results.append(AnalysisResult(
                    analysis_type=pname, success=False,
                    error=f"Primitive '{pname}' not found in registry",
                ))
                continue
            result = primitive.safe_analyse(population, parameters)
            results.append(result)

        # 3. Compose evidence into a ResearchFinding
        finding = compose_evidence(question, results, context, population)

        return QuestionExecutionResult(
            question_id=qid, success=True, finding=finding,
        )

    def run_batch(
        self,
        questions: list[NewEngineQuestion],
        populations: dict[str, list[dict[str, Any]]],
        context: RunContext,
        parameters: dict[str, dict[str, Any]] | None = None,
    ) -> list[QuestionExecutionResult]:
        """
        Execute multiple questions independently.

        A failed question does NOT prevent others from running.

        Args:
            questions: List of questions to execute.
            populations: question_id → resolved population.
            context: Shared run context.
            parameters: question_id → parameters dict.

        Returns:
            List of results (one per question, in order).
        """
        params = parameters or {}
        results = []

        for q in questions:
            pop = populations.get(q.question_id, [])
            qparams = params.get(q.question_id)

            try:
                result = self.run_question(q, pop, context, qparams)
            except Exception as exc:
                result = QuestionExecutionResult(
                    question_id=q.question_id, success=False,
                    error=f"Unhandled: {type(exc).__name__}: {exc}",
                )
            results.append(result)

        return results

    def _resolve_primitives(self, question: NewEngineQuestion) -> list[str]:
        """Determine which primitives a question needs."""
        # Check explicit mapping first
        if question.question_id in self._mapping:
            return self._mapping[question.question_id]

        # Fall back to analysis_type from question contract
        primary = question.analysis_type.value
        primitives = [primary]

        # Add anomaly/exceptional if question declares those views
        from research_engine.v10.universes.models import ViewType
        if ViewType.ANOMALOUS in question.views:
            primitives.append("anomaly_analysis")
        if ViewType.EXCEPTIONAL in question.views:
            primitives.append("exceptional_analysis")

        return primitives


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE COMPOSER
# ═══════════════════════════════════════════════════════════════════════════════


def compose_evidence(
    question: NewEngineQuestion,
    results: list[AnalysisResult],
    context: RunContext,
    population: list[dict[str, Any]],
) -> ResearchFinding:
    """
    Compose primitive results into a structured ResearchFinding.

    Merges metrics, evidence, warnings from all primitives.
    Produces structured anomaly_view and exceptional_view with metadata.
    Questions that don't support a view get NOT_APPLICABLE.
    Views with insufficient data get INCONCLUSIVE.
    """
    from research_engine.v10.universes.models import ViewType

    # Merge all primitive outputs
    all_metrics: dict[str, Any] = {}
    all_evidence: list[str] = []
    all_warnings: list[str] = []
    all_segments: dict[str, Any] = {}
    all_comparisons: dict[str, Any] = {}
    all_distributions: dict[str, Any] = {}
    all_effect_sizes: dict[str, Any] = {}
    all_statistical: dict[str, Any] = {}

    primary_result: AnalysisResult | None = None
    anomaly_result: AnalysisResult | None = None
    exceptional_result: AnalysisResult | None = None

    for r in results:
        if r.analysis_type == "anomaly_analysis":
            anomaly_result = r
            if not r.success:
                all_warnings.append(f"[{r.analysis_type}] FAILED: {r.error}")
            continue
        elif r.analysis_type == "exceptional_analysis":
            exceptional_result = r
            if not r.success:
                all_warnings.append(f"[{r.analysis_type}] FAILED: {r.error}")
            continue

        if not r.success:
            all_warnings.append(f"[{r.analysis_type}] FAILED: {r.error}")
            continue

        # First successful non-view result is primary
        if primary_result is None:
            primary_result = r

        # Merge metrics (prefix with primitive name if collision)
        for k, v in r.metrics.items():
            key = k if k not in all_metrics else f"{r.analysis_type}_{k}"
            all_metrics[key] = v

        all_evidence.extend(r.evidence)
        all_warnings.extend(r.warnings)

        if r.segments:
            all_segments[r.analysis_type] = r.segments
        if r.comparisons:
            all_comparisons[r.analysis_type] = r.comparisons
        if r.distributions:
            all_distributions.update(r.distributions)
        if r.effect_sizes:
            all_effect_sizes[r.analysis_type] = r.effect_sizes
        if r.statistical_results:
            all_statistical[r.analysis_type] = r.statistical_results

    # ─── Build structured anomaly view ────────────────────────────────────────
    anomaly_view = _build_anomaly_view(question, anomaly_result, population)

    # ─── Build structured exceptional view ────────────────────────────────────
    exceptional_view = _build_exceptional_view(question, exceptional_result, population)

    # Determine outcome and confidence
    outcome = _determine_outcome(primary_result, all_metrics)
    confidence = _determine_confidence(primary_result, len(population))

    # Build angle evidence
    angle_evidence: dict[str, dict[str, Any]] = {}
    for u in question.required_universes:
        angle_evidence[u.value] = {"included": True}

    finding = ResearchFinding(
        question_id=question.question_id,
        title=question.title,
        run_id=context.run_id,
        run_timestamp=context.timestamp,
        # Reproducibility
        engine_version=context.engine_version,
        question_version=context.question_bank_version,
        population_versions=context.population_versions,
        universe_versions=context.universe_versions,
        data_snapshot_timestamp=context.timestamp,
        analysis_version=primary_result.primitive_version if primary_result else "N/A",
        # Population context
        populations_used=[p.value for p in question.required_populations],
        universes_used=[u.value for u in question.required_universes],
        sample_sizes={"total": len(population)},
        # Evidence
        evidence={
            "primitives_executed": [r.analysis_type for r in results],
            "primary_analysis": primary_result.analysis_type if primary_result else "",
        },
        angle_evidence=angle_evidence,
        # Metrics
        primary_metrics=all_metrics,
        statistical_results=all_statistical,
        effect_sizes=all_effect_sizes,
        # Views
        normal_view={"sample_size": len(population)},
        anomaly_view=anomaly_view,
        exceptional_view=exceptional_view,
        conditioned_views=all_segments,
        # Conclusion
        outcome=outcome,
        conclusion="; ".join(all_evidence) if all_evidence else "No conclusion",
        confidence=confidence,
        # Quality
        limitations=all_warnings,
        data_quality_warnings=[w for w in all_warnings if "sample" in w.lower()],
    )

    return finding


def _build_anomaly_view(
    question: NewEngineQuestion,
    anomaly_result: AnalysisResult | None,
    population: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build structured anomaly view with proper metadata."""
    from research_engine.v10.universes.models import ViewType

    # Check if question supports anomaly view
    if ViewType.ANOMALOUS not in question.views:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "Question does not declare ANOMALOUS view",
        }

    # Check if primitive was executed
    if anomaly_result is None:
        return {
            "status": "NOT_EXECUTED",
            "reason": "Anomaly analysis primitive not executed",
        }

    if not anomaly_result.success:
        return {
            "status": "ERROR",
            "reason": anomaly_result.error,
        }

    # Check for sufficient data
    anomaly_count = anomaly_result.metrics.get("anomaly_count", 0)
    normal_count = anomaly_result.metrics.get("normal_count", 0)

    if anomaly_count == 0:
        return {
            "status": "INCONCLUSIVE",
            "reason": "No anomalous records found in population",
            "criteria": {"anomaly_field": "anomaly", "threshold": "anomaly == True"},
            "normal_count": normal_count,
            "anomaly_count": 0,
        }

    # Full view with metadata
    return {
        "status": "AVAILABLE",
        "criteria": {"anomaly_field": "anomaly", "threshold": "anomaly == True"},
        "normal_count": normal_count,
        "anomaly_count": anomaly_count,
        "anomaly_rate": anomaly_result.metrics.get("anomaly_rate", 0),
        "normal_mean": anomaly_result.metrics.get("normal_mean"),
        "anomaly_mean": anomaly_result.metrics.get("anomaly_mean"),
        "comparison": anomaly_result.comparisons,
        "impact": (
            "Anomalous records have materially different outcomes"
            if anomaly_result.comparisons
            else "Impact not measurable"
        ),
        "sample_sizes": anomaly_result.sub_sample_sizes,
    }


def _build_exceptional_view(
    question: NewEngineQuestion,
    exceptional_result: AnalysisResult | None,
    population: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build structured exceptional view with proper metadata."""
    from research_engine.v10.universes.models import ViewType

    # Check if question supports exceptional view
    if ViewType.EXCEPTIONAL not in question.views:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "Question does not declare EXCEPTIONAL view",
        }

    # Check if primitive was executed
    if exceptional_result is None:
        return {
            "status": "NOT_EXECUTED",
            "reason": "Exceptional analysis primitive not executed",
        }

    if not exceptional_result.success:
        return {
            "status": "ERROR",
            "reason": exceptional_result.error,
        }

    # Check for sufficient data
    high_count = exceptional_result.metrics.get("exceptional_high_count", 0)
    low_count = exceptional_result.metrics.get("exceptional_low_count", 0)
    total_exceptional = high_count + low_count

    if total_exceptional == 0:
        return {
            "status": "INCONCLUSIVE",
            "reason": "No exceptional records found (thresholds: >2.0R, <-2.0R)",
            "criteria": {"threshold_high": 2.0, "threshold_low": -2.0},
            "normal_count": exceptional_result.metrics.get("normal_count", 0),
            "exceptional_count": 0,
        }

    # Full view
    return {
        "status": "AVAILABLE",
        "criteria": {"threshold_high": 2.0, "threshold_low": -2.0, "metric_field": "r_multiple"},
        "normal_count": exceptional_result.metrics.get("normal_count", 0),
        "exceptional_high_count": high_count,
        "exceptional_low_count": low_count,
        "exceptional_rate": exceptional_result.metrics.get("exceptional_rate", 0),
        "exceptional_high_mean": exceptional_result.metrics.get("exceptional_high_mean"),
        "exceptional_low_mean": exceptional_result.metrics.get("exceptional_low_mean"),
        "sample_sizes": exceptional_result.sub_sample_sizes,
        "evidence": exceptional_result.evidence,
    }


def _determine_outcome(
    primary: AnalysisResult | None, metrics: dict[str, Any]
) -> str:
    """Determine finding outcome from primary analysis metrics."""
    if primary is None or not primary.success:
        return "ANALYSIS_FAILED"

    # Use expectancy-based heuristic if available
    mean_r = metrics.get("mean_r") or metrics.get("expectancy")
    if mean_r is not None:
        if mean_r > 0.05:
            return "POSITIVE"
        elif mean_r < -0.05:
            return "NEGATIVE"
        else:
            return "INCONCLUSIVE"

    # Use trend if available (degradation)
    trend = metrics.get("trend")
    if trend:
        return trend

    # Use calibration
    cal_error = metrics.get("mean_calibration_error")
    if cal_error is not None:
        return "WELL_CALIBRATED" if cal_error < 0.1 else "POORLY_CALIBRATED"

    # Use monotonicity (predictive power)
    mono = metrics.get("monotonic")
    if mono is not None:
        return "PREDICTIVE" if mono else "NOT_PREDICTIVE"

    return "COMPLETED"


def _determine_confidence(
    primary: AnalysisResult | None, sample_size: int
) -> str:
    """Determine confidence level from sample size and result quality."""
    if primary is None:
        return "NONE"
    if sample_size >= 200:
        return "HIGH"
    elif sample_size >= 50:
        return "MEDIUM"
    elif sample_size >= 20:
        return "LOW"
    else:
        return "INSUFFICIENT"
