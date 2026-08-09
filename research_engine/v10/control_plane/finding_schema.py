"""
Research Finding Schema.

Defines the structured finding model for question products.
The container is standardised; the evidence inside is question-specific.

A finding represents ONE completed analysis for ONE question against
ONE population snapshot. It contains everything needed to:
    - Understand what was found
    - Reproduce the analysis
    - Compare with previous findings
    - Identify research gaps
    - Record anomaly/exceptional observations

This module does NOT perform analysis. It defines the evidence structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchFinding:
    """
    A complete research finding for one question from one run.

    The container is standardised across all questions.
    The `evidence` field holds question-specific analytical results.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────────────
    question_id: str = ""
    title: str = ""
    run_id: str = ""
    run_timestamp: str = ""

    # ─── REPRODUCIBILITY ──────────────────────────────────────────────────────
    engine_version: str = "1.0.0"
    question_version: str = "1.0.0"
    population_versions: dict[str, str] = field(default_factory=dict)
    universe_versions: dict[str, str] = field(default_factory=dict)
    data_snapshot_timestamp: str = ""
    analysis_version: str = "1.0.0"

    # ─── POPULATION CONTEXT ───────────────────────────────────────────────────
    populations_used: list[str] = field(default_factory=list)
    universes_used: list[str] = field(default_factory=list)
    filters_applied: dict[str, Any] = field(default_factory=dict)
    segmentation: dict[str, Any] = field(default_factory=dict)
    sample_sizes: dict[str, int] = field(default_factory=dict)

    # ─── EVIDENCE (question-specific) ─────────────────────────────────────────
    # This is where question-specific analytical results live.
    # Different questions produce different evidence structures.
    evidence: dict[str, Any] = field(default_factory=dict)

    # ─── FOUR-ANGLE BREAKDOWN ─────────────────────────────────────────────────
    # Per-angle evidence (only populated for angles the question uses)
    angle_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ─── METRICS ──────────────────────────────────────────────────────────────
    primary_metrics: dict[str, Any] = field(default_factory=dict)
    statistical_results: dict[str, Any] = field(default_factory=dict)
    effect_sizes: dict[str, Any] = field(default_factory=dict)

    # ─── VIEWS ────────────────────────────────────────────────────────────────
    normal_view: dict[str, Any] = field(default_factory=dict)
    anomaly_view: dict[str, Any] = field(default_factory=dict)
    exceptional_view: dict[str, Any] = field(default_factory=dict)
    conditioned_views: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ─── CONCLUSION ───────────────────────────────────────────────────────────
    outcome: str = ""  # POSITIVE, NEGATIVE, INCONCLUSIVE, ANOMALOUS, etc.
    conclusion: str = ""  # Human-readable conclusion
    confidence: str = ""  # HIGH, MEDIUM, LOW, INSUFFICIENT
    recommendation: str = ""  # What action this implies

    # ─── QUALITY & LIMITATIONS ────────────────────────────────────────────────
    limitations: list[str] = field(default_factory=list)
    data_quality_warnings: list[str] = field(default_factory=list)
    supporting_evidence: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)

    # ─── COMPARISON WITH PREVIOUS ─────────────────────────────────────────────
    previous_run_id: str = ""
    previous_outcome: str = ""
    changes_from_previous: dict[str, Any] = field(default_factory=dict)

    # ─── RESEARCH GAPS ────────────────────────────────────────────────────────
    # Findings may identify gaps that generate candidate questions.
    # These are RECORDED here, never automatically activated.
    research_gaps: list[ResearchGap] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "run_id": self.run_id,
            "run_timestamp": self.run_timestamp,
            # Reproducibility
            "engine_version": self.engine_version,
            "question_version": self.question_version,
            "population_versions": self.population_versions,
            "universe_versions": self.universe_versions,
            "data_snapshot_timestamp": self.data_snapshot_timestamp,
            "analysis_version": self.analysis_version,
            # Population context
            "populations_used": self.populations_used,
            "universes_used": self.universes_used,
            "filters_applied": self.filters_applied,
            "segmentation": self.segmentation,
            "sample_sizes": self.sample_sizes,
            # Evidence
            "evidence": self.evidence,
            "angle_evidence": self.angle_evidence,
            # Metrics
            "primary_metrics": self.primary_metrics,
            "statistical_results": self.statistical_results,
            "effect_sizes": self.effect_sizes,
            # Views
            "normal_view": self.normal_view,
            "anomaly_view": self.anomaly_view,
            "exceptional_view": self.exceptional_view,
            "conditioned_views": self.conditioned_views,
            # Conclusion
            "outcome": self.outcome,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            # Quality
            "limitations": self.limitations,
            "data_quality_warnings": self.data_quality_warnings,
            "supporting_evidence": self.supporting_evidence,
            "contradictory_evidence": self.contradictory_evidence,
            # Comparison
            "previous_run_id": self.previous_run_id,
            "previous_outcome": self.previous_outcome,
            "changes_from_previous": self.changes_from_previous,
            # Gaps
            "research_gaps": [g.to_dict() for g in self.research_gaps],
        }


@dataclass
class ResearchGap:
    """A research gap identified by a finding."""
    gap_id: str = ""
    description: str = ""
    gap_type: str = ""  # INSUFFICIENT_EVIDENCE, MISSING_DATA, CONTRADICTORY, etc.
    source_question_id: str = ""
    source_run_id: str = ""
    suggested_question: str = ""  # Proposed follow-up question title
    suggested_angles: list[str] = field(default_factory=list)
    evidence: str = ""  # Why this gap matters

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "description": self.description,
            "gap_type": self.gap_type,
            "source_question_id": self.source_question_id,
            "source_run_id": self.source_run_id,
            "suggested_question": self.suggested_question,
            "suggested_angles": self.suggested_angles,
            "evidence": self.evidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════


def compare_findings(
    previous: ResearchFinding | dict[str, Any] | None,
    current: ResearchFinding,
) -> dict[str, Any]:
    """
    Compare current finding against previous to identify meaningful changes.

    Returns a dict describing what changed.
    """
    if previous is None:
        return {"status": "first_run", "detail": "No previous finding to compare"}

    prev = previous if isinstance(previous, dict) else previous.to_dict()
    changes: dict[str, Any] = {}

    # Outcome change
    prev_outcome = prev.get("outcome", "")
    if prev_outcome != current.outcome:
        changes["outcome_changed"] = {
            "from": prev_outcome,
            "to": current.outcome,
        }

    # Confidence change
    prev_conf = prev.get("confidence", "")
    if prev_conf != current.confidence:
        changes["confidence_changed"] = {
            "from": prev_conf,
            "to": current.confidence,
        }

    # Sample size change
    prev_sizes = prev.get("sample_sizes", {})
    curr_sizes = current.sample_sizes
    if prev_sizes and curr_sizes:
        for key in set(list(prev_sizes.keys()) + list(curr_sizes.keys())):
            p = prev_sizes.get(key, 0)
            c = curr_sizes.get(key, 0)
            if p != c:
                changes.setdefault("sample_size_changes", {})[key] = {
                    "from": p, "to": c
                }

    # Metric changes (primary_metrics)
    prev_metrics = prev.get("primary_metrics", {})
    curr_metrics = current.primary_metrics
    if prev_metrics and curr_metrics:
        for key in set(list(prev_metrics.keys()) + list(curr_metrics.keys())):
            p = prev_metrics.get(key)
            c = curr_metrics.get(key)
            if p != c:
                changes.setdefault("metric_changes", {})[key] = {
                    "from": p, "to": c
                }

    # New anomalies
    prev_anomaly = prev.get("anomaly_view", {})
    if current.anomaly_view and not prev_anomaly:
        changes["new_anomaly_view"] = True

    # New gaps
    if current.research_gaps:
        prev_gaps = prev.get("research_gaps", [])
        new_gap_count = len(current.research_gaps) - len(prev_gaps)
        if new_gap_count > 0:
            changes["new_research_gaps"] = new_gap_count

    if not changes:
        changes["status"] = "no_material_change"

    return changes
