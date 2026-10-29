"""Canonical prop RULE PACK: identity, compilation, validation, store (Block 3A).

This is the single governed authority for "which prop rules applied to this
account, in this phase, at this account size, from this date".

It answers FOUR questions and nothing else:

1. IDENTITY   -- ``rule_pack_id`` is a deterministic function of the canonical
   payload. Same semantics -> same id. Phase, tier, currency, platform,
   jurisdiction or any material rule change -> a different id. Never a UUID.
2. VALIDITY   -- is this pack VALID / INCOMPLETE / INVALID / UNSUPPORTED /
   AMBIGUOUS / EXPIRED? Only ``VALID`` may be used to evaluate a challenge.
3. EVIDENCE   -- which telemetry does each rule need, and can this system
   evaluate it today?
4. LINEAGE    -- which governed source defined each rule, and which pack was in
   force on a given date?

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not evaluate a rule, block a trade, close a trade, modify a stop, pause
the bot, start a thread, open a file, or import any guard, broker or execution
module. Rule MODELLING is side-effect free. Block 3B owns state and historical
evaluation; Block 3C owns enforcement.

PERSISTENCE CHOICE (Block 3A section 36, option A)
--------------------------------------------------
A rule pack is CONFIGURATION / REFERENCE AUTHORITY, not live telemetry. It is
therefore served by an immutable in-process registry (``RulePackStore``) and
deliberately NOT registered in ``core.production_data_contract``: creating a
Production V1 *telemetry* dataset for a rules reference table would be a
category error, and it would pollute the certified Block 1/2 lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields, replace
from datetime import date, datetime, time, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from core.risk.prop_rule_enums import (
    COUNT_BASES,
    MONETARY_BASES,
    PERCENT_BASES,
    SOURCE_AUTHORITY,
    BreachKind,
    CurrencySemantics,
    DrawdownKind,
    EvaluationSupport,
    EvaluationTimeBasis,
    LimitBasis,
    PrecedenceLevel,
    RulePackStatus,
    RulePhase,
    RuleSeverity,
    RuleStatus,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_contracts import (
    RULE_TYPE_CONTRACTS,
    RULE_TYPE_SUPPORT,
    AutomationPermissionRule,
    ConsistencyRule,
    CopyTradingRestrictionRule,
    DailyLossRule,
    DailyProfitRule,
    DrawdownRule,
    HoldRestrictionRule,
    InactivityRule,
    IPRestrictionRule,
    NewsTradingRestrictionRule,
    OpenRiskLimitRule,
    PayoutRule,
    PositionLimitRule,
    ProfitTargetRule,
    RefundRule,
    ResetRule,
    RuleSource,
    ScopeOverride,
    TradingDayRule,
    UnknownRule,
    contract_for,
    required_telemetry_for,
    support_for,
)
from core.risk.prop_rule_telemetry import (
    TelemetryRequirement,
    block2_source_for,
    external_source_requirements,
    required_block2_telemetry,
    unsatisfied_requirements,
)
from core.risk.prop_rule_values import (
    Limit,
    RuleBase,
    RulePackValidationError,
    _require_text,
    validate_effective_window,
    validate_timezone,
)

SCHEMA_VERSION = "prop_rule_pack_v1"
ID_PREFIX = "prp1"


# â”€â”€â”€ ERRORS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class PropRulePackError(RuntimeError):
    """Base error for the canonical prop rule-pack contract."""


class RulePackNotFound(PropRulePackError):
    """No rule pack matches the requested identity.

    Raised instead of returning a "nearest" or default pack.
    """


class AmbiguousRulePackError(PropRulePackError):
    """More than one candidate pack matches the requested identity."""


class RulePackConflictError(PropRulePackError):
    """Two governed definitions contradict each other (fail closed)."""


# â”€â”€â”€ DETERMINISTIC CANONICAL ENCODING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _encode(value: Any) -> Any:
    """Deterministically encode a value for hashing and serialisation."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise RulePackValidationError("NAIVE_DATETIME_NOT_ALLOWED")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RulePackValidationError("NON_FINITE_NUMERIC_RULE_VALUE")
        # Normalise -0.0 and int/float spelling so semantically equal limits
        # always produce exactly one identity.
        return round(value, 10) + 0.0
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in sorted(value.items(), key=lambda i: str(i[0]))}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if is_dataclass_instance(value):
        return {f.name: _encode(getattr(value, f.name)) for f in dataclass_fields(value)}
    if value is None or isinstance(value, (str, int)):
        return value
    raise RulePackValidationError(f"UNSERIALISABLE_RULE_VALUE:{type(value).__name__}")


def is_dataclass_instance(value: Any) -> bool:
    from dataclasses import is_dataclass

    return is_dataclass(value) and not isinstance(value, type)


def canonical_json(value: Any) -> str:
    """Order-independent canonical JSON. Field ordering can never alter identity."""
    return json.dumps(_encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# â”€â”€â”€ RULE PACK IDENTITY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class RulePackIdentity:
    """Exact, deterministic identity of one rule pack.

    A rule pack is bound to ONE (provider, program, phase, account size, currency,
    platform, jurisdiction) tuple at ONE version and effective window. Changing
    the phase or the account tier necessarily changes the identity, because the
    same firm genuinely publishes different limits for each.

    Identity is a pure function of these fields plus the rule content; it is
    never a UUID and never depends on a wall clock or the environment.
    """

    provider: str
    program: str
    phase: RulePhase
    account_size: int
    currency: str
    rule_pack_version: str
    effective_from: datetime
    effective_to: datetime | None = None
    platform: str | None = None
    jurisdiction: str | None = None
    status: RulePackStatus = RulePackStatus.VALID

    def __post_init__(self) -> None:
        _require_text(self.provider, "PROVIDER_REQUIRED")
        _require_text(self.program, "PROGRAM_REQUIRED")
        _require_text(self.currency, "PACK_CURRENCY_REQUIRED")
        _require_text(self.rule_pack_version, "RULE_PACK_VERSION_REQUIRED")
        if not isinstance(self.phase, RulePhase):
            raise RulePackValidationError("PHASE_REQUIRED")
        if isinstance(self.account_size, bool) or not isinstance(self.account_size, int):
            raise RulePackValidationError("ACCOUNT_SIZE_MUST_BE_INT")
        if self.account_size <= 0:
            raise RulePackValidationError("ACCOUNT_SIZE_MUST_BE_POSITIVE")
        if len(self.currency.strip()) != 3:
            raise RulePackValidationError("PACK_CURRENCY_MUST_BE_ISO4217_LEN3")
        validate_effective_window(self.effective_from, self.effective_to)

    @property
    def tier_label(self) -> str:
        """Canonical account-size tier label, e.g. ``100K``."""
        if self.account_size % 1000 == 0:
            return f"{self.account_size // 1000}K"
        return str(self.account_size)

    def selection_key(self) -> tuple[str, str, RulePhase, int, str]:
        """The natural key a lookup by provider/program/phase/tier resolves on."""
        return (
            self.provider.strip().casefold(),
            self.program.strip().casefold(),
            self.phase,
            self.account_size,
            self.currency.strip().upper(),
        )

    def identity_payload(self) -> dict[str, Any]:
        """Canonical payload for identity. ``status`` is derived, so excluded."""
        return {
            "provider": self.provider,
            "program": self.program,
            "phase": self.phase,
            "account_size": self.account_size,
            "currency": self.currency,
            "rule_pack_version": self.rule_pack_version,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "platform": self.platform,
            "jurisdiction": self.jurisdiction,
        }

    def is_effective_at(self, moment: datetime) -> bool:
        """Whether this pack was the governed authority at ``moment``."""
        if moment.tzinfo is None:
            raise RulePackValidationError("EFFECTIVE_LOOKUP_NAIVE_DATETIME")
        moment_utc = moment.astimezone(timezone.utc)
        if moment_utc < self.effective_from.astimezone(timezone.utc):
            return False
        if self.effective_to is None:
            return True
        return moment_utc < self.effective_to.astimezone(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return _encode(self.identity_payload())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RulePackIdentity":
        effective_to = payload.get("effective_to")
        return cls(
            provider=payload["provider"],
            program=payload["program"],
            phase=RulePhase(payload["phase"]),
            account_size=int(payload["account_size"]),
            currency=payload["currency"],
            rule_pack_version=payload["rule_pack_version"],
            effective_from=datetime.fromisoformat(payload["effective_from"]),
            effective_to=datetime.fromisoformat(effective_to) if effective_to else None,
            platform=payload.get("platform"),
            jurisdiction=payload.get("jurisdiction"),
        )



# â”€â”€â”€ CONFLICT DETECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class RuleConflict:
    """One detected contradiction or defect. Always fails closed."""

    code: str
    rule_id: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "rule_id": self.rule_id, "detail": self.detail}


#: Codes that make a pack INVALID outright.
FATAL_CONFLICT_CODES: frozenset[str] = frozenset(
    {
        "DUPLICATE_RULE_ID",
        "DUPLICATE_PROVIDER_PROGRAM_VERSION_IDENTITY",
        "NEGATIVE_THRESHOLD",
        "EFFECTIVE_TO_BEFORE_EFFECTIVE_FROM",
        "MONETARY_LIMIT_MISSING_CURRENCY_SEMANTICS",
        "PERCENT_LIMIT_MISSING_BASIS",
        "RULE_IDENTITY_HASH_MISMATCH",
        "PACK_IDENTITY_MISMATCH",
    }
)

#: Codes that make a pack AMBIGUOUS: the sources disagree or under-specify.
AMBIGUOUS_CONFLICT_CODES: frozenset[str] = frozenset(
    {
        "SAME_PRECEDENCE_CONFLICT",
        "SAME_PRECEDENCE_DUPLICATE_DIFFERING_CONTENT",
        "SOURCE_DISAGREEMENT",
        "MISSING_SOURCE",
        "MISSING_PROVENANCE",
        "TRAILING_AND_STATIC_DRAWDOWN_BOTH_DECLARED",
        "CONFLICTING_PHASE_RULES",
        "UNSPECIFIED_AUTOMATION_VERDICT",
        "NEWS_BLACKOUT_WINDOW_UNSPECIFIED",
    }
)

#: Codes that make a pack INCOMPLETE: required evidence is not declared.
INCOMPLETE_CONFLICT_CODES: frozenset[str] = frozenset(
    {
        "MISSING_TELEMETRY_REQUIREMENTS",
        "MISSING_TIMEZONE",
        "MISSING_CURRENCY_SEMANTICS",
        "MISSING_LIMIT",
        "TRADING_DAY_CRITERION_UNSPECIFIED",
    }
)


def _rule_scope_key(rule: RuleBase) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Rules conflict when they govern the SAME rule type over the SAME scope.

    Scope matters: a firm-wide daily loss limit and a gold-only daily loss limit
    are an override relationship, not a contradiction.
    """
    return (
        rule.rule_type.value,
        tuple(sorted(rule.applies_to_symbols)),
        tuple(sorted(rule.applies_to_asset_classes)),
    )


def detect_rule_conflicts(rules: Sequence[RuleBase]) -> tuple[RuleConflict, ...]:
    """Detect every contradiction, gap and underspecification in a rule set.

    Detected (all fail closed, none silently resolved):

    * duplicate rule ids
    * two rules of the same type and scope at the SAME precedence with
      different content ("last one wins" is never a resolution)
    * static and trailing drawdown both declared as the primary max-DD rule
    * rules whose declared telemetry is incomplete for their own type
    * time-bound rules with no timezone
    * monetary rules with no currency semantics
    * percentage rules with no explicit basis
    * news rules whose blackout window the source never stated
    * automation verdicts left UNSPECIFIED
    * sources that disagree about the same rule
    """
    conflicts: list[RuleConflict] = []

    # â”€â”€ duplicate rule ids â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    seen_ids: dict[str, RuleBase] = {}
    for rule in rules:
        if rule.rule_id in seen_ids:
            conflicts.append(
                RuleConflict(
                    code="DUPLICATE_RULE_ID",
                    rule_id=rule.rule_id,
                    detail=f"rule_id {rule.rule_id!r} declared more than once",
                )
            )
        seen_ids[rule.rule_id] = rule

    # â”€â”€ same-precedence conflicts, grouped by type + scope â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    grouped: dict[tuple[str, tuple[str, ...], tuple[str, ...]], list[RuleBase]] = {}
    for rule in rules:
        if not rule.enabled:
            continue
        grouped.setdefault(_rule_scope_key(rule), []).append(rule)

    for (_, group) in sorted(grouped.items()):
        if len(group) < 2:
            continue
        by_precedence: dict[PrecedenceLevel, list[RuleBase]] = {}
        for rule in group:
            by_precedence.setdefault(rule.precedence, []).append(rule)
        for precedence, peers in sorted(by_precedence.items()):
            if len(peers) < 2:
                continue
            signatures = {content_hash(p.identity_payload()) for p in peers}
            if len(signatures) == 1:
                conflicts.append(
                    RuleConflict(
                        code="SAME_PRECEDENCE_DUPLICATE_DIFFERING_CONTENT",
                        rule_id=None,
                        detail=(
                            f"identical {peers[0].rule_type.value} declared {len(peers)}x at "
                            f"{precedence.name} -- deterministic resolution is ambiguous"
                        ),
                    )
                )
            else:
                ids = sorted(p.rule_id for p in peers)
                conflicts.append(
                    RuleConflict(
                        code="SAME_PRECEDENCE_CONFLICT",
                        rule_id=None,
                        detail=(
                            f"{peers[0].rule_type.value} declared at the same precedence "
                            f"{precedence.name} with different content: {ids}"
                        ),
                    )
                )

    # â”€â”€ static and trailing drawdown must not both be the primary limit â”€â”€â”€â”€
    kinds = {r.kind for r in rules if isinstance(r, DrawdownRule) and r.enabled}
    if DrawdownKind.STATIC in kinds and DrawdownKind.TRAILING in kinds:
        conflicts.append(
            RuleConflict(
                code="TRAILING_AND_STATIC_DRAWDOWN_BOTH_DECLARED",
                rule_id=None,
                detail="static and trailing drawdown are mutually exclusive primary limits",
            )
        )
    return tuple(conflicts)



def detect_per_rule_defects(rule: RuleBase) -> tuple[RuleConflict, ...]:
    """Defects in ONE rule that make it unusable, ambiguous or incomplete.

    These are the "fail closed on a bad definition" checks. They are separate
    from cross-rule conflicts because a single bad rule is detectable on its own.
    """
    found: list[RuleConflict] = []

    # â”€â”€ provenance is mandatory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if not rule.source or not rule.source.source_reference:
        found.append(
            RuleConflict("MISSING_SOURCE", rule.rule_id, "every rule must cite a governed source")
        )

    # â”€â”€ time-bound rules must name an explicit IANA timezone â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if rule.requires_timezone and not rule.timezone_name:
        found.append(
            RuleConflict(
                "MISSING_TIMEZONE",
                rule.rule_id,
                f"{rule.rule_type.value} uses {rule.evaluation_time_basis.value} "
                "and must bind to an explicit IANA timezone",
            )
        )

    # â”€â”€ declared evidence must cover what the rule type requires â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    required = set(required_telemetry_for(rule.rule_type))
    if rule.rule_type is not RuleType.UNKNOWN_EXTENSION:
        missing = required - set(rule.telemetry_requirements)
        if missing:
            found.append(
                RuleConflict(
                    "MISSING_TELEMETRY_REQUIREMENTS",
                    rule.rule_id,
                    f"{rule.rule_type.value} does not declare required evidence: "
                    f"{sorted(r.value for r in missing)}",
                )
            )

    # â”€â”€ percentage basis and monetary currency must be explicit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for limit in _limits_of(rule):
        if limit.is_monetary and limit.currency_semantics is None:
            found.append(
                RuleConflict(
                    "MISSING_CURRENCY_SEMANTICS",
                    rule.rule_id,
                    "monetary rule must declare currency semantics",
                )
            )
        if limit.basis is None:
            found.append(
                RuleConflict(
                    "PERCENT_LIMIT_MISSING_BASIS",
                    rule.rule_id,
                    "a numeric limit must state what it is a percentage OF",
                )
            )

    # â”€â”€ news rules: never invent a blackout window â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(rule, NewsTradingRestrictionRule) and rule.blackout_window_unspecified:
        found.append(
            RuleConflict(
                "NEWS_BLACKOUT_WINDOW_UNSPECIFIED",
                rule.rule_id,
                "source restricts news trading but never states the blackout window; "
                "minutes must not be invented",
            )
        )

    # â”€â”€ automation: UNSPECIFIED is ambiguity, not permission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(rule, AutomationPermissionRule) and rule.has_unspecified_verdict:
        found.append(
            RuleConflict(
                "UNSPECIFIED_AUTOMATION_VERDICT",
                rule.rule_id,
                "automation policy left UNSPECIFIED; the source wording is unclear",
            )
        )
    if isinstance(rule, CopyTradingRestrictionRule) and rule.has_unspecified_verdict:
        found.append(
            RuleConflict(
                "UNSPECIFIED_AUTOMATION_VERDICT",
                rule.rule_id,
                "copy-trading policy left UNSPECIFIED; the source wording is unclear",
            )
        )

    # â”€â”€ trading-day criteria must not be guessed â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(rule, TradingDayRule) and not rule.qualifying_criterion:
        found.append(
            RuleConflict(
                "TRADING_DAY_CRITERION_UNSPECIFIED",
                rule.rule_id,
                "what counts as a trading day is not defined by the source",
            )
        )
    return tuple(found)


def _limits_of(rule: RuleBase) -> tuple[Limit, ...]:
    """Every :class:`Limit` a rule carries, for uniform basis/currency checks."""
    candidates = [
        getattr(rule, name, None)
        for name in ("limit", "minimum_profit", "minimum_buffer", "reset_fee", "refund_amount")
    ]
    return tuple(limit for limit in candidates if isinstance(limit, Limit))



def detect_source_conflicts(rules: Sequence[RuleBase]) -> tuple[RuleConflict, ...]:
    """Expose disagreement between sources about the same rule type and scope.

    Sources are never silently reconciled. When an official rule page and an FAQ
    state different limits, the pack is AMBIGUOUS and the disagreement is
    reported with the authority ordering a human must resolve.
    """
    grouped: dict[tuple[str, tuple[str, ...]], list[RuleBase]] = {}
    for rule in rules:
        if not rule.enabled or not rule.source:
            continue
        grouped.setdefault(
            (rule.rule_type.value, tuple(sorted(rule.applies_to_symbols))), []
        ).append(rule)

    conflicts: list[RuleConflict] = []
    for (rule_type, _scope), peers in sorted(grouped.items()):
        if len(peers) < 2:
            continue
        signatures = {content_hash(p.identity_payload()) for p in peers}
        if len(signatures) == 1:
            continue
        sources = {p.source.source_type for p in peers}
        authorities = sorted(((SOURCE_AUTHORITY[s], s.value) for s in sources), reverse=True)
        conflicts.append(
            RuleConflict(
                code="SOURCE_DISAGREEMENT",
                rule_id=None,
                detail=(
                    f"{rule_type} declared inconsistently by "
                    f"{sorted(s.value for s in sources)}; authority order "
                    f"{authorities} requires human resolution"
                ),
            )
        )
    return tuple(conflicts)



# â”€â”€â”€ THE RULE PACK â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class RulePack:
    """An immutable, compiled, canonical set of prop rules for ONE identity.

    IMMUTABILITY: every field is frozen and the pack is never mutated in place.
    A firm that changes a rule produces a NEW pack with a NEW id and version;
    historical evaluations stay bound to the pack in force at the time.
    """

    identity: RulePackIdentity
    rules: tuple[RuleBase, ...]
    rule_pack_id: str = ""
    content_hash: str = ""
    conflicts: tuple[RuleConflict, ...] = ()
    overrides: tuple[ScopeOverride, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))
        object.__setattr__(self, "overrides", tuple(self.overrides))
        if self.rule_pack_id and self.rule_pack_id != derive_rule_pack_id(
            self.identity, self.rules
        ):
            raise RulePackValidationError("RULE_PACK_ID_MISMATCH")
        if self.rule_pack_id:
            object.__setattr__(self, "content_hash", self.rule_pack_id.split(":")[-1])

    # â”€â”€ status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @property
    def status(self) -> RulePackStatus:
        """Derived pack status. Never stored, so it can never go stale."""
        codes = {conflict.code for conflict in self.conflicts}
        if codes & FATAL_CONFLICT_CODES:
            return RulePackStatus.INVALID
        if codes & AMBIGUOUS_CONFLICT_CODES:
            return RulePackStatus.AMBIGUOUS
        if codes & INCOMPLETE_CONFLICT_CODES:
            return RulePackStatus.INCOMPLETE
        if any(r.rule_type is RuleType.UNKNOWN_EXTENSION for r in self.rules):
            return RulePackStatus.UNSUPPORTED
        return RulePackStatus.VALID

    @property
    def is_usable(self) -> bool:
        """Only a VALID pack may ever be used to evaluate a challenge."""
        return self.status is RulePackStatus.VALID

    def assert_usable(self) -> "RulePack":
        """Fail closed: raise unless this pack is VALID."""
        if not self.is_usable:
            raise RulePackConflictError(
                f"RULE_PACK_NOT_USABLE:{self.rule_pack_id}:{self.status.value}"
            )
        return self

    def as_of(self, moment: datetime) -> bool:
        """Whether this pack was in force at ``moment``."""
        return self.identity.is_effective_at(moment)

    def rules_effective_at(self, moment: datetime) -> tuple[RuleBase, ...]:
        """Only the rules actually in force at ``moment``.

        This is what stops 2026 rules being retroactively applied to 2025 data.
        """
        return tuple(rule for rule in self.rules if rule.is_effective_at(moment))


    # â”€â”€ evidence / support â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def required_telemetry(self) -> tuple[TelemetryRequirement, ...]:
        """Every distinct piece of evidence any rule in this pack needs."""
        needed: set[TelemetryRequirement] = set()
        for rule in self.rules:
            needed.update(rule.telemetry_requirements)
        return tuple(sorted(needed, key=lambda r: r.value))

    def block2_dependencies(self) -> tuple[str, ...]:
        """Which accepted Block 2 telemetry blocks this pack already relies on."""
        return required_block2_telemetry(self.required_telemetry())

    def unsatisfied_requirements(self) -> tuple[TelemetryRequirement, ...]:
        """Evidence Block 2 does NOT provide -- the exact Block 3B build list."""
        return unsatisfied_requirements(self.required_telemetry())

    def external_source_requirements(self) -> tuple[TelemetryRequirement, ...]:
        """Evidence that needs a source outside this system entirely."""
        return external_source_requirements(self.required_telemetry())

    def evaluation_support(self) -> dict[RuleType, EvaluationSupport]:
        """Rule type -> whether this system can evaluate it today."""
        return {rule.rule_type: support_for(rule.rule_type) for rule in self.rules}

    def supportable_now(self) -> tuple[RuleType, ...]:
        """Rule types evaluable from accepted Block 2 telemetry ALONE."""
        return tuple(
            sorted(
                {
                    rule.rule_type
                    for rule in self.rules
                    if support_for(rule.rule_type)
                    is EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
                },
                key=lambda t: t.value,
            )
        )

    def rules_of_type(self, rule_type: RuleType) -> tuple[RuleBase, ...]:
        return tuple(rule for rule in self.rules if rule.rule_type is rule_type)

    def has_rule(self, rule_type: RuleType) -> bool:
        """Whether this pack declares the rule at all (explicit absence)."""
        return any(rule.rule_type is rule_type for rule in self.rules)


# â”€â”€â”€ DETERMINISTIC IDENTITY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def derive_rule_pack_id(identity: RulePackIdentity, rules: Sequence[RuleBase]) -> str:
    """The deterministic rule-pack id.

    A pure function of (identity, canonical rule content). No UUID, no wall clock,
    no environment, no random salt. Consequently:

    * the same governed rules always produce the same id;
    * ANY material rule change produces a different id;
    * a phase, tier, currency, platform or version change produces a different id;
    * JSON field ordering can never change the id.
    """
    payload = {
        "schema": SCHEMA_VERSION,
        "identity": identity.identity_payload(),
        "rules": sorted(
            (_encode(rule.identity_payload()) for rule in rules),
            key=canonical_json,
        ),
    }
    return f"{ID_PREFIX}:{content_hash(payload)}"



# â”€â”€â”€ COMPILATION / NORMALISATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def canonical_rule_order(rules: Sequence[RuleBase]) -> tuple[RuleBase, ...]:
    """Deterministic, content-based rule ordering.

    Ordering is canonical, not insertion-based, so two packs built from the same
    governed rules in a different declaration order get the SAME id.
    """
    return tuple(
        sorted(
            rules,
            key=lambda rule: (
                rule.precedence.value,
                rule.rule_type.value,
                tuple(sorted(rule.applies_to_symbols)),
                tuple(sorted(rule.applies_to_asset_classes)),
                rule.rule_id,
            ),
        )
    )


def resolve_precedence(rules: Sequence[RuleBase]) -> tuple[RuleBase, ...]:
    """Apply the deterministic override order.

    FIRM_DEFAULT -> PROGRAM -> PHASE -> ACCOUNT_SIZE -> INSTRUMENT.

    A HIGHER precedence level overrides a lower one for the same rule type and
    scope. Two rules of the same type and scope at the SAME precedence are NOT
    resolved here: they are reported as a conflict so the pack fails closed.
    "Last one wins" is never a resolution.
    """
    winners: dict[tuple[str, tuple[str, ...], tuple[str, ...]], RuleBase] = {}
    for rule in rules:
        if not rule.enabled:
            continue
        key = _rule_scope_key(rule)
        incumbent = winners.get(key)
        if incumbent is None or rule.precedence.value > incumbent.precedence.value:
            winners[key] = rule
    return canonical_rule_order(tuple(winners.values()))


def compile_rule_pack(
    identity: RulePackIdentity,
    rules: Sequence[RuleBase],
    *,
    overrides: Sequence[ScopeOverride] = (),
    notes: str = "",
    strict: bool = True,
) -> RulePack:
    """Compile raw governed rule definitions into a canonical :class:`RulePack`.

    Compilation normalises ordering, resolves precedence, detects conflicts and
    produces a deterministic id. It performs NO runtime enforcement and has no
    side effect.

    Args:
        identity: exact provider/program/phase/tier/currency/version identity.
        rules: the governed rules, in any declaration order.
        overrides: instrument-level scope overrides.
        notes: free-text governance notes.
        strict: when True (default) a pack with any detected conflict still
            compiles, but its :attr:`RulePack.status` is non-VALID, so it can
            never be used to evaluate a challenge.

    Returns:
        An immutable, canonically ordered :class:`RulePack`.
    """
    ordered = canonical_rule_order(rules)

    # Conflict detection MUST see the RAW declared rules. Running it on the
    # precedence-resolved set would silently merge two conflicting definitions
    # and hide the very contradiction this block exists to fail closed on.
    raw = canonical_rule_order(tuple(rules))
    conflicts: list[RuleConflict] = []
    conflicts.extend(detect_rule_conflicts(raw))
    for rule in raw:
        conflicts.extend(detect_per_rule_defects(rule))
    conflicts.extend(detect_source_conflicts(raw))

    # Only after detection is the override order applied.
    resolved = resolve_precedence(ordered)

    pack = RulePack(
        identity=identity,
        rules=resolved,
        rule_pack_id=derive_rule_pack_id(identity, resolved),
        conflicts=tuple(conflicts),
        overrides=tuple(overrides),
        notes=notes,
    )
    if strict and not pack.is_usable:
        # Surfaced, not raised: the caller decides. The pack is already
        # unusable and will fail closed at :meth:`RulePack.assert_usable`.
        return pack
    return pack


def validate_rule_pack(pack: RulePack) -> RulePackStatus:
    """Validate a compiled pack and return its status (never mutates the pack)."""
    return pack.status



# â”€â”€â”€ SERIALISATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _rule_to_payload(rule: RuleBase) -> dict[str, Any]:
    """Canonical, class-tagged payload for one rule (round-trip safe)."""
    limit_fields = {"limit", "minimum_profit", "minimum_buffer", "reset_fee", "refund_amount"}
    payload: dict[str, Any] = {
        "contract": type(rule).__name__,
        **{
            f.name: getattr(rule, f.name)
            for f in dataclass_fields(rule)
            if f.name not in limit_fields | {"pnl_components"}
        },
    }
    for name in sorted(limit_fields):
        value = getattr(rule, name, None)
        if isinstance(value, Limit):
            payload[name] = value.to_dict()
        elif value is not None:
            payload[name] = _encode(value)
    components = getattr(rule, "pnl_components", None)
    if components is not None:
        payload["pnl_components"] = components.to_dict()
    return _encode(payload)


def rule_pack_to_dict(pack: RulePack) -> dict[str, Any]:
    """Canonical dict for a pack: identity, rules, conflicts, overrides."""
    return {
        "schema": SCHEMA_VERSION,
        "rule_pack_id": pack.rule_pack_id,
        "content_hash": pack.content_hash,
        "identity": pack.identity.to_dict(),
        "rules": [_rule_to_payload(rule) for rule in pack.rules],
        "overrides": [override.to_dict() for override in pack.overrides],
        "conflicts": [conflict.to_dict() for conflict in pack.conflicts],
        "status": pack.status.value,
        "notes": pack.notes,
    }


def rule_pack_to_json(pack: RulePack) -> str:
    """Deterministic canonical JSON. Field ordering never alters the id."""
    return canonical_json(rule_pack_to_dict(pack))



def _decode_rule(payload: Mapping[str, Any]) -> RuleBase:
    """Rebuild a typed rule from its canonical payload.

    Raises on an unknown contract rather than degrading to a generic object, so
    an unmodelled rule can never be silently reconstructed as something weaker.
    """
    from core.risk.prop_rule_contracts import PnLComponents

    data = dict(payload)
    contract_name = data.pop("contract", None)
    if not contract_name:
        raise RulePackValidationError("RULE_PAYLOAD_MISSING_CONTRACT")
    registry = {cls.__name__: cls for cls in RULE_TYPE_CONTRACTS.values()}
    contract = registry.get(contract_name)
    if contract is None:
        raise RulePackValidationError(f"UNKNOWN_RULE_CONTRACT:{contract_name}")

    limit_fields = {"limit", "minimum_profit", "minimum_buffer", "reset_fee", "refund_amount"}
    field_names = {f.name for f in dataclass_fields(contract)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in field_names:
            continue
        if key in limit_fields:
            kwargs[key] = Limit.from_dict(value) if isinstance(value, Mapping) else None
        elif key == "pnl_components":
            kwargs[key] = PnLComponents.from_dict(value) if isinstance(value, Mapping) else value
        elif key == "source":
            kwargs[key] = RuleSource.from_dict(value)
        else:
            kwargs[key] = _decode_scalar(contract, key, value)
    try:
        return contract(**kwargs)
    except RulePackValidationError:
        raise
    except TypeError as exc:  # pragma: no cover - defensive
        raise RulePackValidationError(f"RULE_RECONSTRUCTION_FAILED:{contract_name}") from exc


def _decode_scalar(contract: type, name: str, value: Any) -> Any:
    """Rehydrate one enum/datetime field using the contract's own annotations."""
    import typing

    if value is None:
        return None
    try:
        hints = typing.get_type_hints(contract)
    except Exception:  # pragma: no cover - defensive
        return value
    annotation = hints.get(name)
    if annotation is None:
        return value
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if args and isinstance(args[0], type) and issubclass(args[0], Enum):
            return args[0](value)
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation is datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if annotation is date:
        return date.fromisoformat(value) if isinstance(value, str) else value
    if annotation is time:
        return time.fromisoformat(value) if isinstance(value, str) else value
    if origin is tuple:
        args = typing.get_args(annotation)
        if args and isinstance(args[0], type) and issubclass(args[0], Enum):
            return tuple(args[0](item) for item in value)
        return tuple(value)
    return value


def rule_pack_from_dict(payload: Mapping[str, Any]) -> RulePack:
    """Rebuild a pack from its canonical dict, verifying the derived id."""
    schema = payload.get("schema")
    if schema != SCHEMA_VERSION:
        raise RulePackValidationError(f"UNSUPPORTED_RULE_PACK_SCHEMA:{schema}")
    identity = RulePackIdentity.from_dict(payload["identity"])
    rules = tuple(_decode_rule(item) for item in payload.get("rules", ()))
    overrides = tuple(ScopeOverride.from_dict(item) for item in payload.get("overrides", ()))
    conflicts = tuple(
        RuleConflict(code=item["code"], rule_id=item.get("rule_id"), detail=item.get("detail", ""))
        for item in payload.get("conflicts", ())
    )
    expected = derive_rule_pack_id(identity, rules)
    declared = payload.get("rule_pack_id") or ""
    if declared and declared != expected:
        raise RulePackValidationError("RULE_PACK_ID_MISMATCH")
    return RulePack(
        identity=identity,
        rules=rules,
        rule_pack_id=expected,
        conflicts=conflicts,
        overrides=overrides,
        notes=payload.get("notes", ""),
    )


def rule_pack_from_json(text: str) -> RulePack:
    """Parse canonical JSON back into a :class:`RulePack`."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulePackValidationError("RULE_PACK_JSON_INVALID") from exc
    return rule_pack_from_dict(payload)



# â”€â”€â”€ RULE PACK STORE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class RulePackStore:
    """Deterministic, immutable in-process registry of compiled rule packs.

    This is CONFIGURATION AUTHORITY, not telemetry. It deliberately performs no
    I/O: packs are registered as already-compiled immutable objects, which is
    what makes historical evaluation reproducible (the old pack object is still
    there, untouched, after a firm publishes new rules).

    LOOKUP IS STRICT BY DESIGN
    ---------------------------
    ``find_rule_pack`` never returns a "nearest match". Zero candidates raises
    :class:`RulePackNotFound`; more than one candidate raises
    :class:`AmbiguousRulePackError`. A silent nearest-match would evaluate a
    challenge against the wrong contract.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, RulePack] = {}
        self._by_key: dict[tuple[str, str, RulePhase, int, str], list[RulePack]] = {}

    # â”€â”€ registration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def register_rule_pack(self, pack: RulePack) -> RulePack:
        """Register an already-compiled, immutable pack.

        Registering the SAME id twice with different content is a hard error;
        re-registering an identical pack is idempotent.
        """
        if not isinstance(pack, RulePack):
            raise RulePackValidationError("REGISTER_REQUIRES_RULE_PACK")
        if not pack.rule_pack_id:
            raise RulePackValidationError("REGISTER_REQUIRES_DERIVED_RULE_PACK_ID")
        existing = self._by_id.get(pack.rule_pack_id)
        if existing is not None:
            if existing.content_hash != pack.content_hash:
                raise RulePackConflictError(
                    f"DUPLICATE_RULE_PACK_ID:{pack.rule_pack_id}"
                )
            return existing
        self._by_id[pack.rule_pack_id] = pack
        key = pack.identity.selection_key()
        self._by_key.setdefault(key, []).append(pack)
        return pack

    # â”€â”€ retrieval â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def get_rule_pack(self, rule_pack_id: str) -> RulePack:
        """Exact lookup by id. Raises when absent -- never returns a default."""
        try:
            return self._by_id[rule_pack_id]
        except KeyError as exc:
            raise RulePackNotFound(f"RULE_PACK_NOT_FOUND:{rule_pack_id}") from exc

    def find_rule_pack(
        self,
        provider: str,
        program: str,
        phase: RulePhase,
        account_size: int,
        as_of: datetime,
        *,
        currency: str | None = None,
        platform: str | None = None,
    ) -> RulePack:
        """The ONE pack in force for this identity at ``as_of``.

        Raises :class:`AmbiguousRulePackError` if several packs match -- for
        example two overlapping effective windows. There is no silent nearest
        match and no default.
        """
        if as_of.tzinfo is None:
            raise RulePackValidationError("AS_OF_NAIVE_DATETIME")
        candidates = [
            pack
            for pack in self._candidates_for(provider, program, phase, account_size)
            if pack.identity.is_effective_at(as_of)
        ]
        if currency is not None:
            candidates = [
                pack
                for pack in candidates
                if pack.identity.currency.strip().upper() == currency.strip().upper()
            ]
        if platform is not None:
            candidates = [pack for pack in candidates if pack.identity.platform == platform]
        if not candidates:
            raise RulePackNotFound(
                f"NO_RULE_PACK_FOR:{provider}:{program}:{phase.value}:{account_size}:{as_of.isoformat()}"
            )
        if len(candidates) > 1:
            ids = sorted(pack.rule_pack_id for pack in candidates)
            raise AmbiguousRulePackError(
                f"MULTIPLE_RULE_PACKS_MATCH:{provider}:{program}:{phase.value}:"
                f"{account_size}:{ids}"
            )
        return candidates[0]

    def _candidates_for(
        self, provider: str, program: str, phase: RulePhase, account_size: int
    ) -> list[RulePack]:
        prefix = (provider.strip().casefold(), program.strip().casefold())
        found: list[RulePack] = []
        for key, packs in self._by_key.items():
            if key[0] == prefix[0] and key[1] == prefix[1] and key[2] is phase and key[3] == account_size:
                found.extend(packs)
        return found

    def list_versions(
        self, provider: str, program: str, phase: RulePhase, account_size: int
    ) -> tuple[RulePack, ...]:
        """Every version of one identity, oldest first, for historical replay."""
        found = [
            pack
            for pack in self._candidates_for(provider, program, phase, account_size)
        ]
        return tuple(
            sorted(found, key=lambda p: (p.identity.effective_from, p.identity.rule_pack_version))
        )

    def validate_rule_pack(self, rule_pack_id: str) -> RulePackStatus:
        """Validate a registered pack and return its current status."""
        return self.get_rule_pack(rule_pack_id).status

    def all_rule_pack_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_id))

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, rule_pack_id: object) -> bool:
        return rule_pack_id in self._by_id
