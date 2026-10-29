"""
Contract Compliance Model.

Machine-readable compliance result structures.
Deterministic — same architecture state produces same compliance report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CheckStatus:
    """Deterministic compliance check statuses."""
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class ContractCheck:
    """One contract compliance check."""
    check_id: str
    category: str  # UNIVERSE, POPULATION, CROSS_UNIVERSE, LIFECYCLE, PROVENANCE, OWNERSHIP, VERSION, RESEARCH, GOVERNANCE, PERSISTENCE
    status: str  # PASS, FAIL, WARNING, INCONCLUSIVE
    description: str
    evidence: str = ""
    violation: str = ""
    responsible_component: str = ""
    resolution: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "check_id": self.check_id,
            "category": self.category,
            "status": self.status,
            "description": self.description,
        }
        if self.evidence:
            d["evidence"] = self.evidence
        if self.violation:
            d["violation"] = self.violation
        if self.responsible_component:
            d["responsible_component"] = self.responsible_component
        if self.resolution:
            d["resolution"] = self.resolution
        return d


@dataclass
class ContractComplianceReport:
    """Aggregate compliance report across all contract categories."""
    contract_version: str = "1.0.0"
    status: str = ""  # COMPLIANT, PARTIALLY_COMPLIANT, NON_COMPLIANT, INCONCLUSIVE
    checks: list[ContractCheck] = field(default_factory=list)
    pass_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    inconclusive_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "status": self.status,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "warning_count": self.warning_count,
            "inconclusive_count": self.inconclusive_count,
            "total_checks": len(self.checks),
            "checks": [c.to_dict() for c in self.checks],
        }

    def compute_status(self) -> None:
        """Compute overall status from individual checks."""
        self.pass_count = sum(1 for c in self.checks if c.status == CheckStatus.PASS)
        self.fail_count = sum(1 for c in self.checks if c.status == CheckStatus.FAIL)
        self.warning_count = sum(1 for c in self.checks if c.status == CheckStatus.WARNING)
        self.inconclusive_count = sum(1 for c in self.checks if c.status == CheckStatus.INCONCLUSIVE)

        if self.fail_count > 0:
            self.status = "NON_COMPLIANT"
        elif self.warning_count > 0:
            self.status = "PARTIALLY_COMPLIANT"
        elif self.inconclusive_count > 0 and self.pass_count == 0:
            self.status = "INCONCLUSIVE"
        else:
            self.status = "COMPLIANT"
