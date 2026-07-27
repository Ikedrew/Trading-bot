"""
BaseValidator — Abstract base class for all contract validators.

Adding a new validator requires ONLY:
    1. Subclass BaseValidator
    2. Implement validate() and identity property
    3. Register with the ContractEnforcer

No changes to the enforcement engine are needed.

RULES FOR VALIDATORS:
    - MUST be read-only (never mutate the record)
    - MUST be deterministic (same input → same violations)
    - MUST be fast (avoid I/O, network, heavy computation)
    - MUST return a list of ContractViolation (empty = clean)
    - MUST NOT raise exceptions (catch internally, return violation)
    - MUST NOT make trading decisions
    - MUST NOT repair records
    - MUST NOT rewrite payloads
    - MUST NOT recompute missing values
    - MUST declare immutable identity metadata
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation


class BaseValidator(ABC):
    """
    Abstract base class for contract validators.

    Every validator MUST declare its identity via the `identity` property.
    This provides immutable governance metadata for forensic auditing,
    version tracking, and architectural traceability.

    Subclass this to create a new validator type.
    """

    @property
    @abstractmethod
    def identity(self) -> ValidatorIdentity:
        """
        Immutable governance metadata for this validator.

        MUST be declared explicitly. No runtime generation.
        No hashes. No timestamps. Deterministic.
        """
        ...

    # ─── CONVENIENCE PROPERTIES (delegated to identity) ───────────────

    @property
    def name(self) -> str:
        """Validator name (from identity)."""
        return self.identity.validator_name

    @property
    def validator_id(self) -> str:
        """Globally unique validator ID (from identity)."""
        return self.identity.validator_id

    @property
    def validator_version(self) -> int:
        """Implementation version (from identity)."""
        return self.identity.validator_version

    @property
    def contract_name(self) -> str:
        """Contract this validator enforces (from identity)."""
        return self.identity.contract_name

    @property
    def contract_version(self) -> str:
        """Version of the contract being enforced (from identity)."""
        return self.identity.contract_version

    @property
    def depends_on(self) -> tuple[str, ...]:
        """Validator IDs this validator requires to pass first (from identity)."""
        return self.identity.depends_on

    @property
    def default_confidence(self) -> int:
        """Default confidence percentage (0–100) for this validator (from identity)."""
        return self.identity.default_confidence

    # ─── VALIDATION CONTRACT ──────────────────────────────────────────

    @abstractmethod
    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        """
        Validate a record against this contract.

        Args:
            record: The record to validate (NEVER mutate this).
            layer: Which persistence layer is calling validation.

        Returns:
            List of violations found (empty = record is clean).

        MUST NOT raise. MUST NOT mutate record.
        """
        ...

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """
        Whether this validator applies to the given record/layer.

        Override to skip validation for irrelevant record types.
        Default: applies to everything.
        """
        return True
