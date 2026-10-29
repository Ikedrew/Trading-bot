"""
Contract Compliance Validation Layer.

Read-only, deterministic validation that the V10 Research Engine
conforms to its architectural contracts.

Components:
    - model: Compliance result/check data structures
    - rules: Deterministic contract-check rules
    - validator: Executes checks and produces compliance reports
"""

from research_engine.v10.compliance.model import (
    CheckStatus,
    ContractCheck,
    ContractComplianceReport,
)
from research_engine.v10.compliance.validator import ContractComplianceValidator

__all__ = [
    "CheckStatus",
    "ContractCheck",
    "ContractComplianceReport",
    "ContractComplianceValidator",
]
