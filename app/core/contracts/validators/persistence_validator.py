"""
Persistence Validator — Enforces persistence layer contracts.

Validates:
    - Record has an identifier (trade_id or equivalent)
    - Record has a symbol
    - Timestamps are valid (positive, entry < exit)
    - Prices are valid (positive, non-zero risk distance)
    - No impossible R-multiples (sanity bounds)

IDENTITY:
    validator_id:       PERSISTENCE_001
    validator_version:  1
    contract_name:      persistence_integrity
    contract_version:   v1
    introduced_in_arc:  Arc1
    introduced_date:    2026-07

RULES OWNED:
    PERSIST_IDENTITY_001, PERSIST_SYMBOL_001, PERSIST_TIME_001,
    PERSIST_TIME_002, PERSIST_TIME_003, PERSIST_PRICE_001,
    PERSIST_PRICE_002, PERSIST_R_001
"""

from __future__ import annotations

from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation

# ─── IMMUTABLE IDENTITY ───────────────────────────────────────────────────────

_IDENTITY = ValidatorIdentity(
    validator_id="PERSISTENCE_001",
    validator_name="PersistenceValidator",
    validator_version=1,
    contract_name="persistence_integrity",
    contract_version="v1",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Validates persistence-layer contracts: identity, timestamps, price sanity, and R-multiple bounds.",
    depends_on=("SCHEMA_001",),
    default_confidence=95,
)

# Sanity bounds for R-multiples
_MAX_SANE_R = 50.0
_MIN_SANE_R = -50.0


class PersistenceValidator(BaseValidator):
    """Validates persistence-layer contracts for trade records."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        return "outcome" in record or "prices" in record

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        _vid = self.validator_id
        _vver = self.validator_version
        _cn = self.contract_name
        _cv = self.contract_version
        _vn = self.name

        # ─── PERSIST_IDENTITY_001: Missing Record Identity ────────────
        record_id = record.get("trade_id") or record.get("record_id")
        if not record_id:
            violations.append(ContractViolation(
                contract_name=_cn, validator_name=_vn,
                validator_id=_vid, validator_version=_vver,
                severity=Severity.WARNING, confidence=95,
                rule_id="PERSIST_IDENTITY_001", rule_title="Missing Record Identity",
                reason="Record has no trade_id or record_id",
                layer=layer, field_name="trade_id", contract_version=_cv,
            ))

        # ─── PERSIST_SYMBOL_001: Invalid Symbol ──────────────────────
        symbol = record.get("symbol")
        if not symbol or not isinstance(symbol, str) or not symbol.strip():
            violations.append(ContractViolation(
                contract_name=_cn, validator_name=_vn,
                validator_id=_vid, validator_version=_vver,
                severity=Severity.ERROR, confidence=100,
                rule_id="PERSIST_SYMBOL_001", rule_title="Invalid Symbol",
                reason="Record has no valid symbol",
                layer=layer, field_name="symbol",
                expected="non-empty string", actual=repr(symbol), contract_version=_cv,
            ))

        # ─── TIMESTAMP VALIDATION ─────────────────────────────────────
        timestamps = record.get("timestamps", {})
        if isinstance(timestamps, dict):
            entry_time = timestamps.get("entry_time", 0)
            exit_time = timestamps.get("exit_time", 0)

            # PERSIST_TIME_001: Non-Positive Entry Time
            if isinstance(entry_time, (int, float)) and entry_time <= 0:
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=95,
                    rule_id="PERSIST_TIME_001", rule_title="Non-Positive Entry Time",
                    reason=f"entry_time is non-positive: {entry_time}",
                    layer=layer, field_name="timestamps.entry_time",
                    expected="> 0", actual=entry_time, contract_version=_cv,
                ))

            # PERSIST_TIME_002: Non-Positive Exit Time
            if isinstance(exit_time, (int, float)) and exit_time <= 0:
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=95,
                    rule_id="PERSIST_TIME_002", rule_title="Non-Positive Exit Time",
                    reason=f"exit_time is non-positive: {exit_time}",
                    layer=layer, field_name="timestamps.exit_time",
                    expected="> 0", actual=exit_time, contract_version=_cv,
                ))

            # PERSIST_TIME_003: Time Travel
            if (
                isinstance(entry_time, (int, float))
                and isinstance(exit_time, (int, float))
                and entry_time > 0 and exit_time > 0
                and exit_time < entry_time
            ):
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=90,
                    rule_id="PERSIST_TIME_003", rule_title="Time Travel Detected",
                    reason=f"exit_time ({exit_time}) < entry_time ({entry_time}) — time travel",
                    layer=layer, field_name="timestamps",
                    expected="exit_time >= entry_time",
                    actual=f"exit={exit_time}, entry={entry_time}", contract_version=_cv,
                ))

        # ─── PRICE VALIDATION ─────────────────────────────────────────
        prices = record.get("prices", {})
        if isinstance(prices, dict):
            entry_price = prices.get("entry_price", 0)
            stop_loss = prices.get("stop_loss", 0)

            # PERSIST_PRICE_001: Non-Positive Entry Price
            if isinstance(entry_price, (int, float)) and entry_price <= 0:
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.ERROR, confidence=100,
                    rule_id="PERSIST_PRICE_001", rule_title="Non-Positive Entry Price",
                    reason=f"entry_price is non-positive: {entry_price}",
                    layer=layer, field_name="prices.entry_price",
                    expected="> 0", actual=entry_price, contract_version=_cv,
                ))

            # PERSIST_PRICE_002: Zero Risk Distance
            if (
                isinstance(entry_price, (int, float))
                and isinstance(stop_loss, (int, float))
                and entry_price > 0 and stop_loss > 0
                and abs(entry_price - stop_loss) == 0
            ):
                violations.append(ContractViolation(
                    contract_name=_cn, validator_name=_vn,
                    validator_id=_vid, validator_version=_vver,
                    severity=Severity.CRITICAL, confidence=100,
                    rule_id="PERSIST_PRICE_002", rule_title="Zero Risk Distance",
                    reason="Zero risk distance: entry_price == stop_loss",
                    layer=layer, field_name="prices",
                    expected="entry_price != stop_loss",
                    actual=f"entry={entry_price}, sl={stop_loss}", contract_version=_cv,
                ))

        # ─── PERSIST_R_001: R-Multiple Sanity ─────────────────────────
        outcome = record.get("outcome", {})
        if isinstance(outcome, dict):
            r = outcome.get("r_multiple")
            if isinstance(r, (int, float)):
                if r > _MAX_SANE_R or r < _MIN_SANE_R:
                    violations.append(ContractViolation(
                        contract_name=_cn, validator_name=_vn,
                        validator_id=_vid, validator_version=_vver,
                        severity=Severity.CRITICAL, confidence=99,
                        rule_id="PERSIST_R_001", rule_title="R-Multiple Exceeds Sanity Bounds",
                        reason=(
                            f"R-multiple {r} exceeds sanity bounds "
                            f"[{_MIN_SANE_R}, {_MAX_SANE_R}] — possible inflation bug"
                        ),
                        layer=layer, field_name="outcome.r_multiple",
                        expected=f"[{_MIN_SANE_R}, {_MAX_SANE_R}]", actual=r, contract_version=_cv,
                    ))

        return violations
