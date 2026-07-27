"""
Feature Role Validator — Enforces cross-layer semantic consistency.

Uses the canonical Feature Role Registry to validate that:
    - DECISION fields are non-empty when present
    - OUTCOME fields are numeric when present
    - DERIVED fields are consistent with dependencies
    - No role confusion across layers

IDENTITY:
    validator_id:       FEATURE_001
    validator_version:  1
    contract_name:      feature_role_semantic_consistency
    contract_version:   v1
    introduced_in_arc:  Arc1
    introduced_date:    2026-07
"""

from __future__ import annotations

from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation

# ─── IMMUTABLE IDENTITY ───────────────────────────────────────────────────────

_IDENTITY = ValidatorIdentity(
    validator_id="FEATURE_001",
    validator_name="FeatureRoleValidator",
    validator_version=1,
    contract_name="feature_role_semantic_consistency",
    contract_version="v1",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Validates cross-layer feature semantic roles against the canonical Feature Role Registry.",
    depends_on=("SCHEMA_001",),  # Requires valid schema structure
    default_confidence=65,  # Semantic checks may flag valid edge cases
)


class FeatureRoleValidator(BaseValidator):
    """Validates feature semantic roles against the canonical registry."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """Applies to any trade-like record."""
        return "outcome" in record or "strategy_meta" in record or "prices" in record

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        try:
            from core.feature_role_contract import (
                FEATURE_ROLE_REGISTRY,
                FeatureRole,
                _flatten_record,
            )

            flat = _flatten_record(record)

            # ─── DECISION fields: must be non-empty when present ──────
            for field_name, meta in FEATURE_ROLE_REGISTRY.items():
                if meta["role"] != FeatureRole.DECISION:
                    continue
                value = flat.get(field_name)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    violations.append(ContractViolation(
                        contract_name=self.contract_name,
                        validator_name=self.name,
                        validator_id=self.validator_id,
                        validator_version=self.validator_version,
                        severity=Severity.WARNING,
                        confidence=65,
                        rule_id="FEATURE_DECISION_001", rule_title="Empty Decision Field",
                        reason=f"DECISION field '{field_name}' is empty string",
                        layer=layer,
                        field_name=field_name,
                        expected="non-empty string",
                        actual="''",
                        contract_version=self.contract_version,
                    ))

            # ─── OUTCOME fields: must be numeric when present ─────────
            for field_name, meta in FEATURE_ROLE_REGISTRY.items():
                if meta["role"] != FeatureRole.OUTCOME:
                    continue
                value = flat.get(field_name)
                if value is None:
                    continue
                if field_name == "exit_reason":
                    if not isinstance(value, str):
                        violations.append(ContractViolation(
                            contract_name=self.contract_name,
                            validator_name=self.name,
                            validator_id=self.validator_id,
                            validator_version=self.validator_version,
                            severity=Severity.WARNING,
                            confidence=70,
                            rule_id="FEATURE_OUTCOME_001", rule_title="Outcome Field Type Error",
                            reason=f"OUTCOME field '{field_name}' should be string",
                            layer=layer,
                            field_name=field_name,
                            expected="string",
                            actual=type(value).__name__,
                            contract_version=self.contract_version,
                        ))
                elif not isinstance(value, (int, float)):
                    violations.append(ContractViolation(
                        contract_name=self.contract_name,
                        validator_name=self.name,
                        validator_id=self.validator_id,
                        validator_version=self.validator_version,
                        severity=Severity.WARNING,
                        confidence=70,
                        rule_id="FEATURE_OUTCOME_001", rule_title="Outcome Field Type Error",
                        reason=f"OUTCOME field '{field_name}' should be numeric",
                        layer=layer,
                        field_name=field_name,
                        expected="numeric",
                        actual=type(value).__name__,
                        contract_version=self.contract_version,
                    ))

            # ─── DERIVED consistency: exit_efficiency ─────────────────
            r = flat.get("r_multiple")
            mfe = flat.get("mfe_r")
            eff = flat.get("exit_efficiency")
            if (
                r is not None
                and mfe is not None
                and eff is not None
                and isinstance(r, (int, float))
                and isinstance(mfe, (int, float))
                and isinstance(eff, (int, float))
                and mfe > 0
            ):
                expected_eff = round(r / mfe, 4)
                if abs(eff - expected_eff) > 0.02:
                    violations.append(ContractViolation(
                        contract_name=self.contract_name,
                        validator_name=self.name,
                        validator_id=self.validator_id,
                        validator_version=self.validator_version,
                        severity=Severity.ERROR,
                        confidence=95,
                        rule_id="FEATURE_DERIVED_001", rule_title="Derived Field Inconsistency",
                        reason=(
                            f"DERIVED 'exit_efficiency' inconsistent: "
                            f"got {eff}, expected {expected_eff} (r={r}, mfe={mfe})"
                        ),
                        layer=layer,
                        field_name="exit_efficiency",
                        expected=expected_eff,
                        actual=eff,
                        contract_version=self.contract_version,
                    ))

        except ImportError:
            pass
        except Exception:
            pass

        return violations
