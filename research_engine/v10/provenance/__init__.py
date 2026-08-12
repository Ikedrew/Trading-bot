"""
Evidence / Provenance / Versioning Layer.

Provides the canonical provenance model for research evidence governance.

Components:
    - model: Canonical provenance vocabulary and evidence representation
    - validation: Provenance validity checks
    - lineage: Evidence lineage for findings and lifecycle traces
"""

from research_engine.v10.provenance.model import (
    ProvenanceType,
    EvidenceProvenance,
    SourceReference,
)
from research_engine.v10.provenance.validation import ProvenanceValidator
from research_engine.v10.provenance.lineage import EvidenceLineage

__all__ = [
    "ProvenanceType",
    "EvidenceProvenance",
    "SourceReference",
    "ProvenanceValidator",
    "EvidenceLineage",
]
