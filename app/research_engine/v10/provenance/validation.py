"""
Provenance Validation.

Detects invalid or missing provenance declarations.

Rules:
    - DERIVED evidence must have identifiable source (derived_from)
    - JOINED evidence must have a join key and authoritative universe
    - RECONSTRUCTED evidence must have source references
    - COUNTERFACTUAL evidence must never be presented as OBSERVED
    - UNKNOWN is acceptable for legacy data but should be flagged
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.provenance.model import (
    EvidenceProvenance,
    ProvenanceType,
)


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION RESULTS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProvenanceIssue:
    """One identified provenance problem."""
    severity: str  # ERROR, WARNING, INFO
    provenance_type: str
    field_name: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "provenance_type": self.provenance_type,
            "field_name": self.field_name,
            "message": self.message,
        }


@dataclass
class ProvenanceValidationResult:
    """Result of validating provenance declarations."""
    valid: bool = True
    issues: list[ProvenanceIssue] = field(default_factory=list)
    checked: int = 0
    errors: int = 0
    warnings: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checked": self.checked,
            "errors": self.errors,
            "warnings": self.warnings,
            "issues": [i.to_dict() for i in self.issues],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════


class ProvenanceValidator:
    """
    Validates provenance declarations for research evidence.

    Does not fabricate missing provenance — only identifies problems.
    """

    def validate(
        self,
        provenance: EvidenceProvenance,
        field_name: str = "",
    ) -> ProvenanceValidationResult:
        """Validate a single provenance declaration."""
        result = ProvenanceValidationResult(checked=1)

        ptype = provenance.provenance_type

        if ptype == ProvenanceType.DERIVED.value:
            if not provenance.derived_from:
                result.issues.append(ProvenanceIssue(
                    severity="ERROR",
                    provenance_type=ptype,
                    field_name=field_name,
                    message="DERIVED evidence has no identifiable source (derived_from is empty)",
                ))

        elif ptype == ProvenanceType.JOINED.value:
            if not provenance.authoritative_universe:
                result.issues.append(ProvenanceIssue(
                    severity="ERROR",
                    provenance_type=ptype,
                    field_name=field_name,
                    message="JOINED evidence has no authoritative universe",
                ))
            if provenance.source and not provenance.source.join_key:
                result.issues.append(ProvenanceIssue(
                    severity="ERROR",
                    provenance_type=ptype,
                    field_name=field_name,
                    message="JOINED evidence has no join key",
                ))

        elif ptype == ProvenanceType.RECONSTRUCTED.value:
            if not provenance.derived_from and (not provenance.source or not provenance.source.reference):
                result.issues.append(ProvenanceIssue(
                    severity="WARNING",
                    provenance_type=ptype,
                    field_name=field_name,
                    message="RECONSTRUCTED evidence has no source references",
                ))

        elif ptype == ProvenanceType.UNKNOWN.value:
            result.issues.append(ProvenanceIssue(
                severity="INFO",
                provenance_type=ptype,
                field_name=field_name,
                message="Provenance is UNKNOWN (legacy or unmapped data)",
            ))

        # Count severities
        result.errors = sum(1 for i in result.issues if i.severity == "ERROR")
        result.warnings = sum(1 for i in result.issues if i.severity == "WARNING")
        result.valid = result.errors == 0

        return result

    def validate_batch(
        self,
        items: list[tuple[str, EvidenceProvenance]],
    ) -> ProvenanceValidationResult:
        """
        Validate multiple provenance declarations.

        Args:
            items: List of (field_name, provenance) tuples.
        """
        combined = ProvenanceValidationResult(checked=len(items))
        for field_name, prov in items:
            single = self.validate(prov, field_name)
            combined.issues.extend(single.issues)

        combined.errors = sum(1 for i in combined.issues if i.severity == "ERROR")
        combined.warnings = sum(1 for i in combined.issues if i.severity == "WARNING")
        combined.valid = combined.errors == 0
        return combined

    def check_counterfactual_contamination(
        self,
        items: list[tuple[str, EvidenceProvenance]],
    ) -> list[ProvenanceIssue]:
        """
        Check that COUNTERFACTUAL evidence is not mixed with OBSERVED.

        Returns issues if contamination is detected.
        """
        has_observed = any(
            p.provenance_type == ProvenanceType.OBSERVED.value for _, p in items
        )
        has_counterfactual = any(
            p.provenance_type == ProvenanceType.COUNTERFACTUAL.value for _, p in items
        )

        issues = []
        if has_observed and has_counterfactual:
            issues.append(ProvenanceIssue(
                severity="ERROR",
                provenance_type="MIXED",
                field_name="",
                message=(
                    "COUNTERFACTUAL evidence is mixed with OBSERVED evidence "
                    "in the same evidence set. These must remain separate."
                ),
            ))
        return issues
