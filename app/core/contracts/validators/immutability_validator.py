"""
Immutability Validator — Enforces snapshot immutability contracts.

Validates that decision-time snapshot fields are structurally immutable:
    - htf_snapshot is frozen (MappingProxyType)
    - strategy_meta is frozen when expected
    - No mutable containers in snapshot fields

IDENTITY:
    validator_id:       IMMUTABILITY_001
    validator_version:  1
    contract_name:      snapshot_immutability
    contract_version:   v1
    introduced_in_arc:  Arc1
    introduced_date:    2026-07
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation

# ─── IMMUTABLE IDENTITY ───────────────────────────────────────────────────────

_IDENTITY = ValidatorIdentity(
    validator_id="IMMUTABILITY_001",
    validator_name="ImmutabilityValidator",
    validator_version=1,
    contract_name="snapshot_immutability",
    contract_version="v1",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Validates that decision-time snapshot fields maintain structural immutability in frozen-required layers.",
    depends_on=("SCHEMA_001",),  # Requires valid schema structure
    default_confidence=100,  # Deterministic type checks
)

# Layers that REQUIRE frozen snapshots (post-deserialization)
_FROZEN_REQUIRED_LAYERS = frozenset({
    "trade_truth_graph",
    "edge_attribution",
    "edge_optimisation",
    "strategy_compiler",
    "behaviour_validation",
    "offline_query",
})


class ImmutabilityValidator(BaseValidator):
    """Validates that snapshot fields maintain structural immutability."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """Only applies when layer requires frozen snapshots."""
        return layer in _FROZEN_REQUIRED_LAYERS

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        # Check htf_snapshot immutability
        htf = record.get("htf_snapshot")
        if htf is not None:
            if isinstance(htf, dict) and not isinstance(htf, MappingProxyType):
                violations.append(ContractViolation(
                    contract_name=self.contract_name,
                    validator_name=self.name,
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    severity=Severity.ERROR,
                    reason=(
                        "htf_snapshot is mutable dict in frozen-required layer — "
                        "must be MappingProxyType after deserialization"
                    ),
                    layer=layer,
                    field_name="htf_snapshot",
                    expected="MappingProxyType",
                    actual="dict",
                    contract_version=self.contract_version,
                ))
                _check_nested_mutability(htf, "htf_snapshot", violations, layer, self)

        # Check strategy_meta immutability
        strat = record.get("strategy_meta")
        if strat is not None:
            if isinstance(strat, dict) and not isinstance(strat, MappingProxyType):
                violations.append(ContractViolation(
                    contract_name=self.contract_name,
                    validator_name=self.name,
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    severity=Severity.WARNING,
                    reason=(
                        "strategy_meta is mutable dict in frozen-required layer — "
                        "should be MappingProxyType after deserialization"
                    ),
                    layer=layer,
                    field_name="strategy_meta",
                    expected="MappingProxyType",
                    actual="dict",
                    contract_version=self.contract_version,
                ))

        return violations


def _check_nested_mutability(
    obj: Any,
    path: str,
    violations: list[ContractViolation],
    layer: str,
    validator: ImmutabilityValidator,
    depth: int = 0,
) -> None:
    """Recursively check for mutable containers in snapshot fields."""
    if depth > 5:
        return

    if isinstance(obj, dict) and not isinstance(obj, MappingProxyType):
        for key, value in obj.items():
            child_path = f"{path}.{key}"
            if isinstance(value, list):
                violations.append(ContractViolation(
                    contract_name=validator.contract_name,
                    validator_name=validator.name,
                    validator_id=validator.validator_id,
                    validator_version=validator.validator_version,
                    severity=Severity.WARNING,
                    reason=f"Mutable list found in frozen snapshot: {child_path}",
                    layer=layer,
                    field_name=child_path,
                    expected="tuple",
                    actual="list",
                    contract_version=validator.contract_version,
                ))
            elif isinstance(value, dict) and not isinstance(value, MappingProxyType):
                _check_nested_mutability(value, child_path, violations, layer, validator, depth + 1)
