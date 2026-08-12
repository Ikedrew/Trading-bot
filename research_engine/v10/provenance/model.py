"""
Canonical Provenance Model.

Defines the provenance vocabulary and evidence representation
for all research evidence in the V10 Research Engine.

Every piece of research evidence can be classified as:
    OBSERVED     — directly recorded from an authoritative source at event time
    DERIVED      — deterministically calculated from observed evidence
    JOINED       — evidence from another universe connected via canonical identity
    RECONSTRUCTED — historical state assembled from independently persisted sources
    COUNTERFACTUAL — hypothetical/simulated evidence (never historical fact)
    UNKNOWN      — provenance cannot be determined (legacy/migration)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════════


class ProvenanceType(str, Enum):
    """Canonical provenance classification for research evidence."""
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    JOINED = "JOINED"
    RECONSTRUCTED = "RECONSTRUCTED"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    UNKNOWN = "UNKNOWN"


class SourceKind(str, Enum):
    """Kind of evidence source."""
    FILE = "FILE"
    UNIVERSE = "UNIVERSE"
    POPULATION = "POPULATION"
    CALCULATION = "CALCULATION"
    ENRICHMENT = "ENRICHMENT"
    TRACE = "TRACE"
    EXTERNAL = "EXTERNAL"


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE REFERENCE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SourceReference:
    """
    Identifies where evidence came from.

    A future researcher must be able to determine the origin
    of any piece of research evidence using this reference.
    """
    kind: str = ""  # FILE, UNIVERSE, POPULATION, CALCULATION, ENRICHMENT, TRACE
    reference: str = ""  # Path, universe name, population name, or method
    version: str = ""  # Content hash or schema version
    universe: str = ""  # Authoritative universe (e.g., "OUTCOME", "MARKET")
    entity_id: str = ""  # Join key where applicable
    join_key: str = ""  # Field used for join (e.g., "entity_id")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.kind:
            d["kind"] = self.kind
        if self.reference:
            d["reference"] = self.reference
        if self.version:
            d["version"] = self.version
        if self.universe:
            d["universe"] = self.universe
        if self.entity_id:
            d["entity_id"] = self.entity_id
        if self.join_key:
            d["join_key"] = self.join_key
        return d


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EvidenceProvenance:
    """
    Canonical provenance representation for one piece of research evidence.

    Minimal but sufficient to answer:
        - What kind of evidence is this?
        - Where did it come from?
        - Which universe owns it?
        - What version/snapshot produced it?
        - Can it be reproduced?
    """
    provenance_type: str = ProvenanceType.UNKNOWN.value
    source: SourceReference | None = None
    authoritative_universe: str = ""  # Which universe owns this evidence
    derived_from: list[str] = field(default_factory=list)  # Source fields/populations
    transformation: str = ""  # How it was derived (e.g., "mean(r_multiple)")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "provenance_type": self.provenance_type,
        }
        if self.source:
            d["source"] = self.source.to_dict()
        if self.authoritative_universe:
            d["authoritative_universe"] = self.authoritative_universe
        if self.derived_from:
            d["derived_from"] = self.derived_from
        if self.transformation:
            d["transformation"] = self.transformation
        return d

    @staticmethod
    def observed(universe: str, source_ref: str = "", version: str = "") -> "EvidenceProvenance":
        """Factory for OBSERVED evidence."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.OBSERVED.value,
            authoritative_universe=universe,
            source=SourceReference(
                kind=SourceKind.UNIVERSE.value,
                reference=source_ref or universe,
                version=version,
                universe=universe,
            ),
        )

    @staticmethod
    def derived(
        universe: str,
        derived_from: list[str],
        transformation: str = "",
        population_version: str = "",
    ) -> "EvidenceProvenance":
        """Factory for DERIVED evidence (analytical results)."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.DERIVED.value,
            authoritative_universe=universe,
            derived_from=derived_from,
            transformation=transformation,
            source=SourceReference(
                kind=SourceKind.CALCULATION.value,
                reference=transformation or "analytical_primitive",
                version=population_version,
                universe=universe,
            ),
        )

    @staticmethod
    def joined(
        authoritative_universe: str,
        join_key: str = "entity_id",
        source_version: str = "",
    ) -> "EvidenceProvenance":
        """Factory for JOINED evidence (cross-universe)."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.JOINED.value,
            authoritative_universe=authoritative_universe,
            source=SourceReference(
                kind=SourceKind.ENRICHMENT.value,
                reference=f"joined_from_{authoritative_universe}",
                version=source_version,
                universe=authoritative_universe,
                join_key=join_key,
            ),
        )

    @staticmethod
    def reconstructed(source_references: list[str] | None = None) -> "EvidenceProvenance":
        """Factory for RECONSTRUCTED evidence (lifecycle traces, etc.)."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.RECONSTRUCTED.value,
            derived_from=source_references or [],
            source=SourceReference(
                kind=SourceKind.TRACE.value,
                reference="lifecycle_trace_reconstruction",
            ),
        )

    @staticmethod
    def counterfactual(description: str = "") -> "EvidenceProvenance":
        """Factory for COUNTERFACTUAL evidence (simulated/hypothetical)."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.COUNTERFACTUAL.value,
            transformation=description or "hypothetical_analysis",
            source=SourceReference(
                kind=SourceKind.CALCULATION.value,
                reference="counterfactual_simulation",
            ),
        )

    @staticmethod
    def unknown() -> "EvidenceProvenance":
        """Factory for UNKNOWN provenance (legacy data without provenance)."""
        return EvidenceProvenance(
            provenance_type=ProvenanceType.UNKNOWN.value,
        )
