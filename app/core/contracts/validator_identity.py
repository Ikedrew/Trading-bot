"""
Validator Identity — Immutable governance metadata for contract validators.

Every validator in the system is a governed, versioned component with a
permanent identity. This module defines the identity model and the
read-only validator registry.

IDENTITY RULES:
    - validator_id: Globally unique, NEVER recycled or renamed
    - introduced_date: NEVER changes after release
    - introduced_in_arc: NEVER changes after release

EVOLUTION RULES (fields that MAY change):
    - validator_version: Increments when implementation logic changes
    - contract_version: Increments when the contract itself changes
    - description: May be updated for clarity

GOVERNANCE:
    Validator identity exists independently of business logic.
    It provides the foundation for forensic auditing, replay
    reproducibility, and architectural evolution.

Usage:
    from core.contracts.validator_identity import ValidatorIdentity

    identity = ValidatorIdentity(
        validator_id="SCHEMA_001",
        validator_name="SchemaValidator",
        validator_version=2,
        contract_name="trade_truth_schema",
        contract_version="v2",
        introduced_in_arc="Arc1",
        introduced_date="2026-07",
        owner="Architecture",
        description="Validates Trade Truth v2 schema completeness.",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidatorIdentity:
    """
    Immutable governance metadata for a contract validator.

    IMMUTABLE FIELDS (never change once released):
        validator_id:       Globally unique identifier (e.g., "SCHEMA_001")
        introduced_date:    When first deployed (e.g., "2026-07")
        introduced_in_arc:  Which architecture arc introduced this (e.g., "Arc1")

    EVOLVING FIELDS (may increment over time):
        validator_version:  Implementation version (increment on logic change)
        contract_version:   Contract version being enforced
        description:        Human-readable description (may be refined)

    FUTURE-COMPATIBLE FIELDS (reserved for expansion):
        metadata:           Dict for future additions without breaking schema
    """

    # ─── IMMUTABLE (never change after release) ───────────────────────
    validator_id: str               # Globally unique, never recycled
    validator_name: str             # Human-readable name
    introduced_in_arc: str          # Architecture arc (e.g., "Arc1")
    introduced_date: str            # ISO month (e.g., "2026-07")

    # ─── EVOLVING (may increment) ────────────────────────────────────
    validator_version: int          # Implementation version
    contract_name: str              # Contract being enforced
    contract_version: str           # Version of that contract
    owner: str                      # Ownership domain (e.g., "Architecture")
    description: str                # What this validator does

    # ─── FUTURE-COMPATIBLE (extension slot) ───────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ─── DEPENDENCY DECLARATION ───────────────────────────────────────
    depends_on: tuple[str, ...] = ()  # Validator IDs this validator requires

    # ─── CONFIDENCE POLICY ────────────────────────────────────────────
    default_confidence: int = 100  # Default confidence (0–100) for this validator

    def to_dict(self) -> dict[str, Any]:
        """Serialize identity for persistence and audit reports."""
        return {
            "validator_id": self.validator_id,
            "validator_name": self.validator_name,
            "validator_version": self.validator_version,
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "introduced_in_arc": self.introduced_in_arc,
            "introduced_date": self.introduced_date,
            "owner": self.owner,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "default_confidence": self.default_confidence,
            "metadata": self.metadata if self.metadata else {},
        }

    @property
    def qualified_id(self) -> str:
        """Full qualified identifier: {id}@v{version}."""
        return f"{self.validator_id}@v{self.validator_version}"


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATOR REGISTRY (read-only governance view)
# ═══════════════════════════════════════════════════════════════════════════════

class ValidatorRegistry:
    """
    Read-only registry of all validator identities in the system.

    Provides governance inspection:
        - List all registered validators
        - Lookup by ID
        - Filter by arc, owner, contract
        - Export full registry for audit

    This registry is populated automatically when validators register
    with the ContractEnforcer. It is strictly READ-ONLY — no mutations
    after initial registration.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ValidatorIdentity] = {}  # id → identity

    def _register(self, identity: ValidatorIdentity) -> None:
        """
        Internal: Add identity to registry.

        Called by ContractEnforcer during validator registration.
        Validates uniqueness constraint.
        """
        if identity.validator_id in self._entries:
            existing = self._entries[identity.validator_id]
            if existing.validator_name != identity.validator_name:
                raise ValueError(
                    f"Validator ID collision: '{identity.validator_id}' already "
                    f"registered to '{existing.validator_name}', cannot assign "
                    f"to '{identity.validator_name}'"
                )
            # Same validator re-registering (e.g., version upgrade) — update
        self._entries[identity.validator_id] = identity

    @property
    def count(self) -> int:
        """Number of registered validators."""
        return len(self._entries)

    def get(self, validator_id: str) -> ValidatorIdentity | None:
        """Lookup validator identity by ID."""
        return self._entries.get(validator_id)

    def list_all(self) -> list[ValidatorIdentity]:
        """List all registered validator identities."""
        return list(self._entries.values())

    def list_ids(self) -> list[str]:
        """List all registered validator IDs."""
        return list(self._entries.keys())

    def filter_by_arc(self, arc: str) -> list[ValidatorIdentity]:
        """Filter validators by introduction arc."""
        return [v for v in self._entries.values() if v.introduced_in_arc == arc]

    def filter_by_owner(self, owner: str) -> list[ValidatorIdentity]:
        """Filter validators by ownership domain."""
        return [v for v in self._entries.values() if v.owner == owner]

    def filter_by_contract(self, contract_name: str) -> list[ValidatorIdentity]:
        """Filter validators by contract name."""
        return [v for v in self._entries.values() if v.contract_name == contract_name]

    def export_registry(self) -> dict[str, Any]:
        """Export full registry for audit/governance reports."""
        return {
            "registry_version": "validator_registry_v1",
            "total_validators": self.count,
            "validators": {
                vid: identity.to_dict()
                for vid, identity in sorted(self._entries.items())
            },
            "by_arc": {
                arc: [v.validator_id for v in self.filter_by_arc(arc)]
                for arc in sorted(set(v.introduced_in_arc for v in self._entries.values()))
            },
            "by_owner": {
                owner: [v.validator_id for v in self.filter_by_owner(owner)]
                for owner in sorted(set(v.owner for v in self._entries.values()))
            },
        }
