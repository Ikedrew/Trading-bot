"""Shared value types for the canonical prop rule model (Block 3A).

:mod:`core.risk.prop_rule_enums` defines the vocabulary;
:mod:`core.risk.prop_rule_telemetry` declares the evidence each rule needs; this
module defines the common typed value objects every rule is built from:

* :class:`Limit`     -- a numeric constraint with an EXPLICIT basis and currency.
* :class:`RuleSource` -- provenance. A rule is "official" only if recorded so.
* :class:`RuleBase`  -- the metadata every rule carries regardless of kind.

GUARANTEES ENFORCED HERE (fail closed, at construction time)
------------------------------------------------------------
1. No :class:`Limit` without a :class:`LimitBasis`. "5%" alone is rejected.
2. No monetary :class:`Limit` without explicit :class:`CurrencySemantics`.
3. No percentage limit at or below zero, and none above 100.
4. No negative threshold, no non-finite value.
5. No ``effective_to`` at or before ``effective_from``.
6. An IANA timezone is validated, never defaulted from the host machine.

No file, clock, network, broker, guard or execution dependency exists here.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from datetime import date, datetime, time, timezone
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.risk.prop_rule_enums import (
    COUNT_BASES,
    MONETARY_BASES,
    PERCENT_BASES,
    TIMEZONE_BOUND_TIME_BASES,
    BreachKind,
    CurrencySemantics,
    EvaluationTimeBasis,
    LimitBasis,
    PrecedenceLevel,
    RuleStatus,
    RuleType,
    RuleSeverity,
    SourceType,
)
from core.risk.prop_rule_telemetry import TelemetryRequirement

#: Hard ceiling for a percentage limit. A "120% drawdown" is never a real rule.
MAX_PERCENT = 100.0


class RulePackValidationError(RuntimeError):
    """A rule or rule pack violates the governed contract (fail closed)."""


def _require_text(value: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RulePackValidationError(code)
    return value.strip()


def validate_timezone(name: str | None) -> str | None:
    """Validate an IANA timezone name. Returns the name, or ``None`` if absent.

    A missing timezone is never silently replaced with the host machine's local
    zone. Absence is detected later by the rule validator when the rule's time
    basis requires one.
    """
    if name is None:
        return None
    text = _require_text(name, "TIMEZONE_REQUIRED")
    if text.upper() in {"UTC", "Z"}:
        return "UTC"
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise RulePackValidationError(f"UNKNOWN_IANA_TIMEZONE:{text}") from exc
    return text


def validate_effective_window(
    effective_from: datetime, effective_to: datetime | None
) -> None:
    """Both bounds must be timezone-aware and correctly ordered."""
    if effective_from.tzinfo is None:
        raise RulePackValidationError("EFFECTIVE_FROM_NAIVE_DATETIME")
    if effective_to is not None:
        if effective_to.tzinfo is None:
            raise RulePackValidationError("EFFECTIVE_TO_NAIVE_DATETIME")
        if effective_to <= effective_from:
            raise RulePackValidationError("EFFECTIVE_TO_BEFORE_EFFECTIVE_FROM")


@dataclass(frozen=True)
class Limit:
    """A numeric constraint with EXACT semantics.

    A ``Limit`` cannot exist without a :class:`LimitBasis`, and a monetary
    ``Limit`` cannot exist without explicit :class:`CurrencySemantics`. This is
    the central guarantee of Block 3A: a bare "5%" is rejected at construction.
    """

    basis: LimitBasis
    value: float | int
    currency_semantics: CurrencySemantics | None = None
    rule_currency: str | None = None
    conversion_source: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.basis, LimitBasis):
            raise RulePackValidationError("LIMIT_BASIS_REQUIRED")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise RulePackValidationError("LIMIT_VALUE_MUST_BE_NUMERIC")
        value = float(self.value)
        if not math.isfinite(value):
            raise RulePackValidationError("NON_FINITE_NUMERIC_RULE_VALUE")
        if value < 0:
            raise RulePackValidationError("NEGATIVE_THRESHOLD_NOT_ALLOWED")
        if self.basis in PERCENT_BASES:
            if value <= 0:
                raise RulePackValidationError("ZERO_PERCENT_THRESHOLD_NOT_ALLOWED")
            if value > MAX_PERCENT:
                raise RulePackValidationError("PERCENT_THRESHOLD_ABOVE_100")
        if self.basis in COUNT_BASES and value <= 0:
            raise RulePackValidationError("COUNT_THRESHOLD_MUST_BE_POSITIVE")
        # Normalise the stored value to float so ``5`` and ``5.0`` are ONE
        # semantic limit and can never produce two different rule-pack ids.
        object.__setattr__(self, "value", value)
        if self.basis in MONETARY_BASES and self.currency_semantics is None:
            raise RulePackValidationError("MONETARY_LIMIT_REQUIRES_CURRENCY_SEMANTICS")
        if self.basis in MONETARY_BASES:
            semantics = self.currency_semantics
            if semantics is CurrencySemantics.FIXED_RULE_CURRENCY and not self.rule_currency:
                raise RulePackValidationError("FIXED_RULE_CURRENCY_REQUIRES_CURRENCY")
            if semantics is CurrencySemantics.CONVERTED_REFERENCE_CURRENCY:
                if not self.rule_currency:
                    raise RulePackValidationError("CONVERTED_RULE_REQUIRES_REFERENCE_CURRENCY")
                if not self.conversion_source:
                    raise RulePackValidationError("CONVERTED_RULE_REQUIRES_CONVERSION_SOURCE")
        if self.currency_semantics is None and (self.rule_currency or self.conversion_source):
            raise RulePackValidationError("CURRENCY_FIELDS_REQUIRE_SEMANTICS")

    @property
    def is_monetary(self) -> bool:
        return self.basis in MONETARY_BASES

    @property
    def is_percentage(self) -> bool:
        return self.basis in PERCENT_BASES

    def is_percentage_of(self, basis: LimitBasis) -> bool:
        return self.basis is basis

    def same_semantics_as(self, other: "Limit") -> bool:
        """Semantic (not spelling) equality, used for same-precedence conflict."""
        return (
            self.basis is other.basis
            and float(self.value) == float(other.value)
            and self.currency_semantics is other.currency_semantics
            and (self.rule_currency or "") == (other.rule_currency or "")
            and (self.conversion_source or "") == (other.conversion_source or "")
        )

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in dataclass_fields(self)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Limit":
        return cls(
            basis=LimitBasis(payload["basis"]),
            value=payload["value"],
            currency_semantics=(
                CurrencySemantics(payload["currency_semantics"])
                if payload.get("currency_semantics") else None
            ),
            rule_currency=payload.get("rule_currency"),
            conversion_source=payload.get("conversion_source"),
            notes=payload.get("notes", ""),
        )



@dataclass(frozen=True)
class RuleSource:
    """Provenance of ONE governed definition.

    A rule is never presented as official by assumption -- only because the
    source recorded here is an official provider source.
    """

    source_type: SourceType
    source_reference: str
    retrieved_at: datetime | None = None
    source_effective_date: date | None = None
    content_hash: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, SourceType):
            raise RulePackValidationError("SOURCE_TYPE_REQUIRED")
        _require_text(self.source_reference, "SOURCE_REFERENCE_REQUIRED")
        if self.retrieved_at is not None and self.retrieved_at.tzinfo is None:
            raise RulePackValidationError("SOURCE_RETRIEVED_AT_NAIVE_DATETIME")
        if self.source_effective_date is not None and not isinstance(
            self.source_effective_date, date
        ):
            raise RulePackValidationError("SOURCE_EFFECTIVE_DATE_INVALID")

    @property
    def authority_rank(self) -> int:
        """Higher number == LOWER authority."""
        from core.risk.prop_rule_enums import SOURCE_AUTHORITY

        return SOURCE_AUTHORITY[self.source_type]

    @property
    def is_official(self) -> bool:
        """True only for an official provider source."""
        from core.risk.prop_rule_enums import OFFICIAL_SOURCE_TYPES

        return self.source_type in OFFICIAL_SOURCE_TYPES

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in dataclass_fields(self)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuleSource":
        retrieved = payload.get("retrieved_at")
        effective = payload.get("source_effective_date")
        return cls(
            source_type=SourceType(payload["source_type"]),
            source_reference=payload["source_reference"],
            retrieved_at=datetime.fromisoformat(retrieved) if retrieved else None,
            source_effective_date=date.fromisoformat(effective) if effective else None,
            content_hash=payload.get("content_hash"),
            notes=payload.get("notes", ""),
        )



@dataclass(frozen=True)
class RuleBase:
    """Metadata EVERY prop rule carries, whatever its kind.

    Subclasses add their own typed fields. Nothing here is optional-and-guessed:
    a missing timezone on a time-bound rule, a missing source or a malformed
    effective window is rejected at construction, not discovered at runtime.
    """

    rule_id: str
    rule_type: RuleType
    enabled: bool
    severity: RuleSeverity
    source: RuleSource
    effective_from: datetime
    effective_to: datetime | None = None
    notes: str = ""
    telemetry_requirements: tuple[TelemetryRequirement, ...] = ()
    evaluation_time_basis: EvaluationTimeBasis = EvaluationTimeBasis.NOT_TIME_SENSITIVE
    account_scope: str = "ALL_ACCOUNTS"
    breach_kind: BreachKind = BreachKind.HARD
    breach_persists_until_reset: bool = False
    precedence: PrecedenceLevel = PrecedenceLevel.PROGRAM
    timezone_name: str | None = None
    applies_to_symbols: tuple[str, ...] = ()
    applies_to_asset_classes: tuple[str, ...] = ()
    status: RuleStatus = RuleStatus.VALID
    unknown_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "RULE_ID_REQUIRED")
        if not isinstance(self.rule_type, RuleType):
            raise RulePackValidationError("RULE_TYPE_REQUIRED")
        if not isinstance(self.severity, RuleSeverity):
            raise RulePackValidationError("RULE_SEVERITY_REQUIRED")
        if not isinstance(self.severity, RuleSeverity):
            raise RulePackValidationError("RULE_SEVERITY_REQUIRED")
        if not isinstance(self.breach_kind, BreachKind):
            raise RulePackValidationError("RULE_BREACH_KIND_REQUIRED")
        if not isinstance(self.precedence, PrecedenceLevel):
            raise RulePackValidationError("RULE_PRECEDENCE_REQUIRED")
        if not isinstance(self.source, RuleSource):
            raise RulePackValidationError("RULE_SOURCE_REQUIRED")
        if not isinstance(self.enabled, bool):
            raise RulePackValidationError("RULE_ENABLED_MUST_BE_BOOL")
        validate_effective_window(self.effective_from, self.effective_to)
        if isinstance(self.telemetry_requirements, list):
            object.__setattr__(self, "telemetry_requirements", tuple(self.telemetry_requirements))
        for requirement in self.telemetry_requirements:
            if not isinstance(requirement, TelemetryRequirement):
                raise RulePackValidationError("TELEMETRY_REQUIREMENT_INVALID")
        object.__setattr__(self, "timezone_name", validate_timezone(self.timezone_name))
        object.__setattr__(self, "applies_to_symbols", tuple(self.applies_to_symbols))
        object.__setattr__(self, "applies_to_asset_classes", tuple(self.applies_to_asset_classes))

    @property
    def requires_timezone(self) -> bool:
        """Whether this rule's time basis binds it to an explicit timezone."""
        return self.evaluation_time_basis in TIMEZONE_BOUND_TIME_BASES

    @property
    def is_instrument_specific(self) -> bool:
        return bool(self.applies_to_symbols or self.applies_to_asset_classes)

    def is_effective_at(self, moment: datetime) -> bool:
        """Whether this rule was in force at ``moment``.

        This is what stops 2026 rules being retroactively applied to 2025 data.
        """
        if moment.tzinfo is None:
            raise RulePackValidationError("EFFECTIVE_LOOKUP_NAIVE_DATETIME")
        moment_utc = moment.astimezone(timezone.utc)
        if moment_utc < self.effective_from.astimezone(timezone.utc):
            return False
        if self.effective_to is None:
            return True
        return moment_utc < self.effective_to.astimezone(timezone.utc)

    def identity_payload(self) -> dict[str, Any]:
        """Canonical, order-independent payload that defines this rule's identity.

        ``status`` is excluded because it is DERIVED at validation time and must
        never change a rule's identity.
        """
        payload = {f.name: getattr(self, f.name) for f in dataclass_fields(self)}
        payload.pop("status", None)
        return payload
