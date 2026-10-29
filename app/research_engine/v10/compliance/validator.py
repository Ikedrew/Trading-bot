"""
Contract Compliance Validator.

Executes all contract compliance checks and produces
a deterministic compliance report.

Read-only. Never modifies system state.
"""

from __future__ import annotations

import logging
from typing import Any

from research_engine.v10.compliance.model import (
    CheckStatus,
    ContractCheck,
    ContractComplianceReport,
)
from research_engine.v10.compliance.rules import ALL_RULES

logger = logging.getLogger(__name__)


class ContractComplianceValidator:
    """
    Executes all contract compliance rules and produces a report.

    Usage:
        validator = ContractComplianceValidator()
        report = validator.validate()
        print(report.status)  # COMPLIANT / PARTIALLY_COMPLIANT / NON_COMPLIANT
    """

    def validate(self) -> ContractComplianceReport:
        """
        Execute all registered contract checks.

        Returns:
            ContractComplianceReport with results of all checks.
        """
        report = ContractComplianceReport()

        for rule_fn in ALL_RULES:
            try:
                check = rule_fn()
                report.checks.append(check)
            except Exception as e:
                # Rule execution failed — record as INCONCLUSIVE
                check_id = getattr(rule_fn, "__doc__", rule_fn.__name__) or rule_fn.__name__
                report.checks.append(ContractCheck(
                    check_id=rule_fn.__name__,
                    category="RUNTIME_ERROR",
                    status=CheckStatus.INCONCLUSIVE,
                    description=f"Rule execution failed: {rule_fn.__name__}",
                    evidence=f"{type(e).__name__}: {e}",
                ))

        report.compute_status()
        return report

    def validate_category(self, category: str) -> ContractComplianceReport:
        """Execute only checks in a specific category."""
        report = ContractComplianceReport()

        for rule_fn in ALL_RULES:
            try:
                check = rule_fn()
                if check.category == category:
                    report.checks.append(check)
            except Exception as e:
                report.checks.append(ContractCheck(
                    check_id=rule_fn.__name__,
                    category="RUNTIME_ERROR",
                    status=CheckStatus.INCONCLUSIVE,
                    description=f"Rule failed: {rule_fn.__name__}",
                    evidence=str(e),
                ))

        report.compute_status()
        return report

    def summary(self) -> dict[str, Any]:
        """Run validation and return a concise summary."""
        report = self.validate()
        return {
            "status": report.status,
            "total": len(report.checks),
            "pass": report.pass_count,
            "fail": report.fail_count,
            "warning": report.warning_count,
            "inconclusive": report.inconclusive_count,
            "violations": [
                c.to_dict() for c in report.checks
                if c.status == CheckStatus.FAIL
            ],
        }
