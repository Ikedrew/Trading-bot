"""
Evidence Lineage.

Constructs the evidence lineage for research findings and lifecycle traces.

Lineage connects:
    CONCLUSION
        ↓
    ANALYSIS (methodology, version, parameters)
        ↓
    DATASET (population, version, inclusion criteria)
        ↓
    OBSERVATIONS (individual records with identity)
        ↓
    AUTHORITATIVE SOURCES (source files, universe builders)

This module constructs lineage from the existing research infrastructure
without duplicating universe data. It references existing version fields:
    - universe_versions
    - population_versions
    - trace_hash
    - run_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.provenance.model import (
    EvidenceProvenance,
    ProvenanceType,
    SourceKind,
    SourceReference,
)


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE LINEAGE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EvidenceLineage:
    """
    Complete evidence lineage for a research artifact.

    Answers: "What evidence produced this result, and can it be reproduced?"
    """
    # What was produced
    artifact_type: str = ""  # FINDING, LIFECYCLE_TRACE, METRIC, COMPARISON
    artifact_id: str = ""  # run_id, trace_hash, or question_id

    # Versions (reference existing infrastructure)
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)
    trace_hash: str = ""
    run_id: str = ""

    # Source evidence
    source_universes: list[str] = field(default_factory=list)
    source_populations: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    # Provenance of key evidence items
    evidence_provenance: dict[str, EvidenceProvenance] = field(default_factory=dict)

    # Methodology
    analysis_method: str = ""
    analysis_version: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "trace_hash": self.trace_hash,
            "run_id": self.run_id,
            "source_universes": self.source_universes,
            "source_populations": self.source_populations,
            "source_files": self.source_files,
            "evidence_provenance": {
                k: v.to_dict() for k, v in self.evidence_provenance.items()
            },
            "analysis_method": self.analysis_method,
            "analysis_version": self.analysis_version,
            "parameters": self.parameters,
        }

    @staticmethod
    def for_finding(
        question_id: str,
        run_id: str,
        universe_versions: dict[str, str],
        population_versions: dict[str, str],
        populations_used: list[str],
        universes_used: list[str],
        analysis_method: str = "",
        analysis_version: str = "",
    ) -> "EvidenceLineage":
        """Construct lineage for a research finding."""
        return EvidenceLineage(
            artifact_type="FINDING",
            artifact_id=f"{question_id}_{run_id}",
            universe_versions=universe_versions,
            population_versions=population_versions,
            run_id=run_id,
            source_universes=universes_used,
            source_populations=populations_used,
            analysis_method=analysis_method,
            analysis_version=analysis_version,
        )

    @staticmethod
    def for_lifecycle_trace(
        entity_id: str,
        trace_hash: str,
        universe_versions: dict[str, str],
        present_universes: list[str],
    ) -> "EvidenceLineage":
        """Construct lineage for a lifecycle trace."""
        return EvidenceLineage(
            artifact_type="LIFECYCLE_TRACE",
            artifact_id=entity_id,
            trace_hash=trace_hash,
            universe_versions=universe_versions,
            source_universes=present_universes,
            evidence_provenance={
                "trace": EvidenceProvenance.reconstructed(present_universes),
            },
        )

    @staticmethod
    def for_metric(
        metric_name: str,
        universe: str,
        population: str,
        population_version: str = "",
        transformation: str = "",
    ) -> "EvidenceLineage":
        """Construct lineage for a derived analytical metric."""
        return EvidenceLineage(
            artifact_type="METRIC",
            artifact_id=metric_name,
            source_universes=[universe],
            source_populations=[population],
            population_versions={population: population_version} if population_version else {},
            analysis_method=transformation,
            evidence_provenance={
                metric_name: EvidenceProvenance.derived(
                    universe=universe,
                    derived_from=[population],
                    transformation=transformation,
                    population_version=population_version,
                ),
            },
        )
