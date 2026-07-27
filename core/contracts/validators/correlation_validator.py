"""
Correlation Validator — Enforces the Decision Spine correlation_id contract.

The correlation_id is the SINGLE immutable spine linking all layers:
    events → shadow_trades → trade_truth → graph → attribution → optimisation

RULES:
    CORRELATION_MISSING_001  — No correlation_id present (WARNING upstream, ERROR in persistence)
    CORRELATION_FORMAT_001   — correlation_id present but wrong format
    CORRELATION_SPINE_001    — correlation_id mismatch detected across record fields (CRITICAL)

IDENTITY:
    validator_id:       CORRELATION_001
    validator_version:  1
    contract_name:      correlation_spine_integrity
    contract_version:   v1
    introduced_in_arc:  Arc1
    introduced_date:    2026-07

HARD RULES (from contract):
    - correlation_id is NEVER regenerated downstream
    - correlation_id is NEVER recomputed from data
    - If missing → contract violation (not silently defaulted)
    - If mismatched → CRITICAL violation
"""

from __future__ import annotations

import re
from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation

# ─── IMMUTABLE IDENTITY ───────────────────────────────────────────────────────

_IDENTITY = ValidatorIdentity(
    validator_id="CORRELATION_001",
    validator_name="CorrelationValidator",
    validator_version=1,
    contract_name="correlation_spine_integrity",
    contract_version="v1",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Enforces the Decision Spine correlation_id contract: presence, format, and cross-field consistency.",
    depends_on=("SCHEMA_001",),
    default_confidence=100,
)

# Format: COR-{8 digits}-{digits/alphanumeric}-{ALPHA up to 6}-{4 hex}
_CORRELATION_PATTERN = re.compile(r"^COR-\d{8}-.+-.+-[A-F0-9]{4}$")

# Layers where correlation_id is REQUIRED (ERROR if missing)
_PERSISTENCE_LAYERS = frozenset({
    "shadow_trades",
    "trade_truth",
    "trade_truth_graph",
    "edge_attribution",
    "edge_optimisation",
    "strategy_compiler",
})


class CorrelationValidator(BaseValidator):
    """Enforces the correlation_id spine contract."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """Applies to any trade-like record (has outcome, prices, or trade_id)."""
        return (
            "outcome" in record
            or "prices" in record
            or "trade_id" in record
        )

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        _vid = self.validator_id
        _vver = self.validator_version
        _cn = self.contract_name
        _cv = self.contract_version
        _vn = self.name

        cor = record.get("correlation_id", "")

        # ─── CORRELATION_MISSING_001: Absent correlation_id ───────────
        if not cor:
            # Severity depends on layer: ERROR in persistence, WARNING upstream
            sev = Severity.ERROR if layer in _PERSISTENCE_LAYERS else Severity.WARNING
            violations.append(ContractViolation(
                contract_name=_cn, validator_name=_vn,
                validator_id=_vid, validator_version=_vver,
                severity=sev, confidence=100,
                rule_id="CORRELATION_MISSING_001",
                rule_title="Missing Correlation ID",
                reason=f"Record has no correlation_id — cannot link to decision spine (layer={layer})",
                layer=layer, field_name="correlation_id",
                expected="COR-{{date}}-{{cycle}}-{{symbol}}-{{hash}}",
                actual="absent", contract_version=_cv,
            ))
            return violations  # No further checks possible

        # ─── CORRELATION_FORMAT_001: Wrong format ─────────────────────
        if not _CORRELATION_PATTERN.match(cor):
            violations.append(ContractViolation(
                contract_name=_cn, validator_name=_vn,
                validator_id=_vid, validator_version=_vver,
                severity=Severity.WARNING, confidence=90,
                rule_id="CORRELATION_FORMAT_001",
                rule_title="Invalid Correlation ID Format",
                reason=f"correlation_id '{cor}' does not match expected format COR-YYYYMMDD-cycle-SYMBOL-HASH",
                layer=layer, field_name="correlation_id",
                expected="COR-{YYYYMMDD}-{cycle}-{SYMBOL}-{HASH4}",
                actual=cor, contract_version=_cv,
            ))

        # ─── CORRELATION_SPINE_001: Cross-field mismatch ──────────────
        # Check if correlation_id symbol matches record symbol
        symbol = record.get("symbol", "")
        if cor and symbol and "-" in cor:
            parts = cor.split("-")
            if len(parts) >= 4:
                cor_symbol = parts[3]  # e.g., "EURUSD"
                record_sym_short = symbol.replace("_SB", "").replace("_sb", "")[:6].upper()
                if cor_symbol != record_sym_short and record_sym_short:
                    violations.append(ContractViolation(
                        contract_name=_cn, validator_name=_vn,
                        validator_id=_vid, validator_version=_vver,
                        severity=Severity.CRITICAL, confidence=95,
                        rule_id="CORRELATION_SPINE_001",
                        rule_title="Correlation ID Symbol Mismatch",
                        reason=(
                            f"correlation_id symbol '{cor_symbol}' does not match "
                            f"record symbol '{record_sym_short}' — possible spine corruption"
                        ),
                        layer=layer, field_name="correlation_id",
                        expected=f"COR-...-{record_sym_short}-...",
                        actual=cor, contract_version=_cv,
                    ))

        return violations
