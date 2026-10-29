"""
Contract Enforcement Framework — Architectural guardian of the trading system.

This package provides the central authority for:
    - Schema validation
    - Feature role validation
    - Persistence contracts
    - Immutability contracts
    - Causal contracts
    - Future architecture contracts

No individual module should invent its own validation behaviour.
All validation passes through this single enforcement framework.

DESIGN PRINCIPLE:
    Validation is separate from business logic.
    Trading engines decide. Contract enforcement validates.
    Persistence stores. Analytics learns.

Usage:
    from core.contracts import enforce, Severity, ContractViolation
    from core.contracts import get_enforcer

    enforcer = get_enforcer()
    result = enforcer.validate(record, layer="shadow_trades")

    if result.quarantined:
        # Record is isolated — do NOT propagate downstream
        pass
    else:
        # Record is clean — safe for persistence + downstream
        persist(record)
"""

from core.contracts.severity import Severity
from core.contracts.confidence import Confidence, classify_confidence, confidence_to_numeric
from core.contracts.contract_rule import ContractRule, RuleRegistry, RuleStatus, get_rule_registry
from core.contracts.violation_id import generate_violation_id, ViolationStore, get_violation_store
from core.contracts.violation import ContractViolation
from core.contracts.quarantine import QuarantineRecord, QuarantineStore
from core.contracts.validator_identity import ValidatorIdentity, ValidatorRegistry
from core.contracts.dependency_graph import DependencyGraph, GraphValidationError, ValidatorState
from core.contracts.engine import ContractEnforcer, get_enforcer, ValidationResult, ValidatorExecution
from core.contracts.base_validator import BaseValidator

__all__ = [
    "Severity",
    "Confidence",
    "classify_confidence",
    "confidence_to_numeric",
    "ContractRule",
    "RuleRegistry",
    "RuleStatus",
    "get_rule_registry",
    "generate_violation_id",
    "ViolationStore",
    "get_violation_store",
    "ContractViolation",
    "QuarantineRecord",
    "QuarantineStore",
    "ValidatorIdentity",
    "ValidatorRegistry",
    "DependencyGraph",
    "GraphValidationError",
    "ValidatorState",
    "ContractEnforcer",
    "get_enforcer",
    "ValidationResult",
    "ValidatorExecution",
    "BaseValidator",
]
