"""
Schema Validator — Enforces trade truth schema completeness and type correctness.

Validates that records conform to the Trade Truth v2 schema:
    - Required top-level sections present
    - Required fields within each section
    - Correct types for critical fields
    - Schema version is stamped

Does NOT:
    - Repair records
    - Infer missing values
    - Make trading decisions

IDENTITY:
    validator_id:       SCHEMA_001
    validator_version:  1
    contract_name:      trade_truth_schema
    contract_version:   v2
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
    validator_id="SCHEMA_001",
    validator_name="SchemaValidator",
    validator_version=1,
    contract_name="trade_truth_schema",
    contract_version="v2",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Validates Trade Truth v2 schema completeness and type correctness before downstream propagation.",
    depends_on=(),  # Root validator — no dependencies
    default_confidence=100,  # Deterministic structure checks
)

# ─── SCHEMA RULES ─────────────────────────────────────────────────────────────

# Required sections in a Trade Truth v2 record
_REQUIRED_SECTIONS = ("timestamps", "prices", "outcome")

# Required fields per section
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "timestamps": ["entry_time", "exit_time"],
    "prices": ["entry_price", "stop_loss", "exit_price"],
    "outcome": ["r_multiple"],
}

# Type expectations for critical fields
_TYPE_CHECKS: dict[str, tuple[str, type | tuple[type, ...]]] = {
    "outcome.r_multiple": ("outcome", (int, float)),
    "outcome.mfe_r": ("outcome", (int, float)),
    "outcome.mae_r": ("outcome", (int, float)),
    "prices.entry_price": ("prices", (int, float)),
    "prices.exit_price": ("prices", (int, float)),
    "prices.stop_loss": ("prices", (int, float)),
    "timestamps.entry_time": ("timestamps", (int, float)),
    "timestamps.exit_time": ("timestamps", (int, float)),
}


class SchemaValidator(BaseValidator):
    """Validates records against the Trade Truth v2 schema contract."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """Applies to any record that looks like a trade truth record."""
        return (
            "outcome" in record
            or "prices" in record
            or record.get("schema_version", "").startswith("trade_truth")
        )

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        _vid = self.validator_id
        _vver = self.validator_version
        _cn = self.contract_name
        _cv = self.contract_version
        _vn = self.name

        # Check required sections
        for section in _REQUIRED_SECTIONS:
            if section not in record:
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=100,
                    rule_id="SCHEMA_SECTION_001", rule_title="Missing Required Section",
                    reason=f"Missing required section: {section}",
                    layer=layer, field_name=section,
                    expected="dict", actual="absent", contract_version=_cv,
                ))
            elif not isinstance(record[section], dict):
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=100,
                    rule_id="SCHEMA_SECTION_002", rule_title="Section Wrong Type",
                    reason=f"Section '{section}' must be dict, got {type(record[section]).__name__}",
                    layer=layer, field_name=section,
                    expected="dict", actual=type(record[section]).__name__, contract_version=_cv,
                ))

        # Check required fields within sections
        for section, fields in _REQUIRED_FIELDS.items():
            section_data = record.get(section, {})
            if not isinstance(section_data, dict):
                continue
            for field_name in fields:
                if field_name not in section_data:
                    violations.append(ContractViolation(
                        contract_name=_cn, validator_name=_vn,
                        validator_id=_vid, validator_version=_vver,
                        severity=Severity.ERROR, confidence=100,
                        rule_id="SCHEMA_FIELD_001", rule_title="Missing Required Field",
                        reason=f"Missing required field: {section}.{field_name}",
                        layer=layer, field_name=f"{section}.{field_name}",
                        expected="present", actual="absent", contract_version=_cv,
                    ))

        # Type checks for critical numeric fields
        for field_path, (section, expected_types) in _TYPE_CHECKS.items():
            section_data = record.get(section, {})
            if not isinstance(section_data, dict):
                continue
            field_name = field_path.split(".")[-1]
            value = section_data.get(field_name)
            if value is None:
                continue
            if not isinstance(value, expected_types):
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.WARNING, confidence=100,
                    rule_id="SCHEMA_TYPE_001", rule_title="Field Type Mismatch",
                    reason=f"Type mismatch: {field_path} expected numeric, got {type(value).__name__}",
                    layer=layer, field_name=field_path,
                    expected="numeric", actual=type(value).__name__, contract_version=_cv,
                ))

        # Schema version check (INFO only)
        if "schema_version" not in record:
            violations.append(ContractViolation(
                contract_name=_cn, validator_name=_vn,
                validator_id=_vid, validator_version=_vver,
                severity=Severity.INFO, confidence=100,
                rule_id="SCHEMA_VERSION_001", rule_title="Missing Schema Version",
                reason="No schema_version field — record may be legacy format",
                layer=layer, field_name="schema_version", contract_version=_cv,
            ))

        return violations
