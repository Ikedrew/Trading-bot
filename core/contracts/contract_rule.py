"""
Contract Rule — Immutable rule identity with permanent lifecycle governance.

A Contract Rule ID is a PERMANENT architectural identity.
Its meaning NEVER changes once released.
Only its lifecycle status may evolve.

LIFECYCLE MODEL:
    ACTIVE      → Rule is enforced in production
    DEPRECATED  → Rule exists for historical resolution but no longer enforced
    REPLACED    → Rule has been superseded by a new rule (chain linked)

GOVERNANCE RULES:
    - Rule IDs are NEVER recycled or reassigned
    - A released rule's meaning is IMMUTABLE
    - If the invariant changes: deprecate old → create new
    - Historical violations ALWAYS resolve to the original rule definition
    - No automatic migration of historical records

REPLACEMENT CHAIN:
    PERSIST_TIME_003 (DEPRECATED, replaced_by=PERSIST_TIME_009)
        → PERSIST_TIME_009 (ACTIVE)

    Historical logs referencing PERSIST_TIME_003 remain valid forever.

Usage:
    from core.contracts.contract_rule import ContractRule, RuleStatus, RuleRegistry

    rule = ContractRule(
        rule_id="PERSIST_TIME_003",
        title="Time Travel Detected",
        description="Exit timestamp occurs before entry timestamp.",
        severity=Severity.ERROR,
        confidence=90,
        validator_id="PERSISTENCE_001",
        status=RuleStatus.ACTIVE,
    )

    # Deprecating a rule:
    registry.deprecate(
        rule_id="PERSIST_TIME_003",
        reason="Replaced with stricter tolerance check",
        replacement_rule_id="PERSIST_TIME_009",
        deprecated_in="Arc2",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.contracts.confidence import Confidence, classify_confidence
from core.contracts.severity import Severity


# ═══════════════════════════════════════════════════════════════════════════════
# RULE LIFECYCLE STATUS
# ═══════════════════════════════════════════════════════════════════════════════

class RuleStatus(str, Enum):
    """Lifecycle status of a Contract Rule."""
    ACTIVE = "ACTIVE"           # Rule is enforced in production
    DEPRECATED = "DEPRECATED"   # Rule exists for history but no longer enforced
    REPLACED = "REPLACED"       # Rule superseded by another (chain linked)


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT RULE (frozen dataclass)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ContractRule:
    """
    Immutable identity for a single validation rule.

    PERMANENCE CONTRACT:
        Once released, a rule's identity and meaning are IMMUTABLE.
        Only lifecycle status may evolve (ACTIVE → DEPRECATED → REPLACED).
        Historical violations always resolve to the original definition.
    """

    # ─── IDENTITY (immutable, globally unique) ────────────────────────
    rule_id: str                    # Globally unique, NEVER recycled
    title: str                      # Short human-readable title
    description: str                # Full description of what this rule checks
    validator_id: str               # Which validator owns this rule

    # ─── CLASSIFICATION ───────────────────────────────────────────────
    severity: Severity              # Default severity when this rule fires
    confidence: int = 100           # Default confidence (0–100)

    # ─── DOCUMENTATION ────────────────────────────────────────────────
    documentation: str = ""         # Reference document
    recommendation: str = ""        # What to do when this rule fires
    introduced_in: str = "Arc1"     # Architecture arc that introduced this rule

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    status: RuleStatus = RuleStatus.ACTIVE
    deprecated: bool = False        # Legacy field (kept for backward compat)
    deprecated_in: str = ""         # Arc where deprecation occurred (e.g., "Arc2")
    replacement_rule_id: str = ""   # Rule ID that replaces this one (if REPLACED)
    replacement_reason: str = ""    # Why this rule was deprecated/replaced

    # ─── FUTURE-COMPATIBLE ────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def confidence_level(self) -> Confidence:
        """Classified confidence level from numeric value."""
        return classify_confidence(self.confidence)

    @property
    def is_active(self) -> bool:
        """Whether this rule is currently enforced."""
        return self.status == RuleStatus.ACTIVE

    @property
    def is_deprecated(self) -> bool:
        """Whether this rule has been deprecated or replaced."""
        return self.status in (RuleStatus.DEPRECATED, RuleStatus.REPLACED)

    @property
    def has_replacement(self) -> bool:
        """Whether a replacement rule exists."""
        return bool(self.replacement_rule_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for registry export and documentation generation."""
        d: dict[str, Any] = {
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
            "validator_id": self.validator_id,
            "severity": self.severity.name,
            "severity_level": int(self.severity),
            "confidence": self.confidence,
            "confidence_level": self.confidence_level.value,
            "documentation": self.documentation,
            "recommendation": self.recommendation,
            "introduced_in": self.introduced_in,
            "status": self.status.value,
            "deprecated": self.is_deprecated,
        }
        # Only include lifecycle fields when relevant
        if self.deprecated_in:
            d["deprecated_in"] = self.deprecated_in
        if self.replacement_rule_id:
            d["replacement_rule_id"] = self.replacement_rule_id
        if self.replacement_reason:
            d["replacement_reason"] = self.replacement_reason
        return d


# ═══════════════════════════════════════════════════════════════════════════════
# RULE REGISTRY (read-only governance, supports lifecycle)
# ═══════════════════════════════════════════════════════════════════════════════

class RuleRegistry:
    """
    Permanent registry of all Contract Rules — active and historical.

    GOVERNANCE:
        - Rule IDs are NEVER removed (even deprecated rules remain searchable)
        - Duplicate IDs are rejected (immutable identity contract)
        - Deprecation only changes lifecycle status, never removes the rule
        - Replacement chains are tracked for auditing
        - Historical violations always resolve to the original definition

    Behaves like a database migration system:
        Rules are permanent. Only their lifecycle evolves.
    """

    def __init__(self) -> None:
        self._rules: dict[str, ContractRule] = {}

    def register(self, rule: ContractRule) -> None:
        """
        Register a rule. Rejects duplicate IDs with different definitions.

        Same rule re-registered (idempotent) is allowed.
        Different rule with same ID → ValueError (collision).
        """
        if rule.rule_id in self._rules:
            existing = self._rules[rule.rule_id]
            if existing.title != rule.title or existing.validator_id != rule.validator_id:
                raise ValueError(
                    f"Rule ID collision: '{rule.rule_id}' already registered "
                    f"(existing: '{existing.title}' by {existing.validator_id}, "
                    f"new: '{rule.title}' by {rule.validator_id})"
                )
        self._rules[rule.rule_id] = rule

    def register_many(self, rules: list[ContractRule]) -> None:
        """Register multiple rules. Fails fast on collision."""
        for rule in rules:
            self.register(rule)

    # ─── LIFECYCLE OPERATIONS ─────────────────────────────────────────

    def deprecate(
        self,
        rule_id: str,
        *,
        reason: str = "",
        replacement_rule_id: str = "",
        deprecated_in: str = "",
    ) -> ContractRule:
        """
        Deprecate a rule. The rule remains in the registry forever.

        Args:
            rule_id: The rule to deprecate
            reason: Why it's being deprecated
            replacement_rule_id: The new rule that replaces it (optional)
            deprecated_in: Architecture arc where deprecation occurred

        Returns:
            The new deprecated version of the rule.

        Raises:
            KeyError: If rule_id doesn't exist
            ValueError: If rule is already deprecated
        """
        existing = self._rules.get(rule_id)
        if existing is None:
            raise KeyError(f"Cannot deprecate unknown rule: '{rule_id}'")
        if existing.is_deprecated:
            raise ValueError(f"Rule '{rule_id}' is already deprecated")

        # Validate replacement exists if specified
        if replacement_rule_id and replacement_rule_id not in self._rules:
            raise KeyError(
                f"Replacement rule '{replacement_rule_id}' not registered. "
                f"Register the replacement before deprecating '{rule_id}'."
            )

        # Create new frozen instance with updated lifecycle
        status = RuleStatus.REPLACED if replacement_rule_id else RuleStatus.DEPRECATED
        deprecated_rule = ContractRule(
            rule_id=existing.rule_id,
            title=existing.title,
            description=existing.description,
            validator_id=existing.validator_id,
            severity=existing.severity,
            confidence=existing.confidence,
            documentation=existing.documentation,
            recommendation=existing.recommendation,
            introduced_in=existing.introduced_in,
            status=status,
            deprecated=True,
            deprecated_in=deprecated_in,
            replacement_rule_id=replacement_rule_id,
            replacement_reason=reason,
            metadata=existing.metadata,
        )

        self._rules[rule_id] = deprecated_rule
        return deprecated_rule

    # ─── QUERY API ────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Total rules (active + deprecated)."""
        return len(self._rules)

    @property
    def active_count(self) -> int:
        """Only active rules."""
        return sum(1 for r in self._rules.values() if r.is_active)

    @property
    def deprecated_count(self) -> int:
        """Only deprecated/replaced rules."""
        return sum(1 for r in self._rules.values() if r.is_deprecated)

    def get(self, rule_id: str) -> ContractRule | None:
        """Lookup rule by ID (returns both active and deprecated)."""
        return self._rules.get(rule_id)

    def rules(self) -> list[ContractRule]:
        """All registered rules (active + deprecated), sorted by ID."""
        return sorted(self._rules.values(), key=lambda r: r.rule_id)

    def active_rules(self) -> list[ContractRule]:
        """Only active (enforced) rules."""
        return [r for r in self.rules() if r.is_active]

    def deprecated_rules(self) -> list[ContractRule]:
        """Only deprecated/replaced rules (historical)."""
        return [r for r in self.rules() if r.is_deprecated]

    def search(self, prefix: str) -> list[ContractRule]:
        """Search rules by ID prefix (includes deprecated)."""
        return [r for r in self._rules.values() if r.rule_id.startswith(prefix)]

    def filter_by_validator(self, validator_id: str) -> list[ContractRule]:
        """Get all rules owned by a specific validator."""
        return [r for r in self._rules.values() if r.validator_id == validator_id]

    def filter_by_severity(self, severity: Severity) -> list[ContractRule]:
        """Get all rules at a given severity level."""
        return [r for r in self._rules.values() if r.severity == severity]

    def filter_by_arc(self, arc: str) -> list[ContractRule]:
        """Get all rules introduced in a specific arc."""
        return [r for r in self._rules.values() if r.introduced_in == arc]

    def filter_by_status(self, status: RuleStatus) -> list[ContractRule]:
        """Get all rules with a specific lifecycle status."""
        return [r for r in self._rules.values() if r.status == status]

    # ─── REPLACEMENT CHAIN ────────────────────────────────────────────

    def get_replacement_chain(self, rule_id: str) -> list[str]:
        """
        Trace the replacement chain from a deprecated rule to its active successor.

        Returns list of rule IDs: [original, ..., current_active]
        """
        chain: list[str] = [rule_id]
        visited: set[str] = {rule_id}
        current = self._rules.get(rule_id)

        while current and current.replacement_rule_id:
            next_id = current.replacement_rule_id
            if next_id in visited:
                break  # Cycle guard (should never happen)
            chain.append(next_id)
            visited.add(next_id)
            current = self._rules.get(next_id)

        return chain

    def resolve_active(self, rule_id: str) -> ContractRule | None:
        """
        Resolve a rule_id to its current active replacement.

        If the rule is active, returns itself.
        If deprecated/replaced, follows the chain to the active successor.
        Returns None if no active rule found in the chain.
        """
        chain = self.get_replacement_chain(rule_id)
        for rid in reversed(chain):
            rule = self._rules.get(rid)
            if rule and rule.is_active:
                return rule
        return None

    # ─── EXPORT ───────────────────────────────────────────────────────

    def export(self) -> dict[str, Any]:
        """Export full registry for documentation generation."""
        by_validator: dict[str, list[str]] = {}
        for rule in self._rules.values():
            by_validator.setdefault(rule.validator_id, []).append(rule.rule_id)

        return {
            "registry_version": "rule_registry_v2",
            "total_rules": self.count,
            "active_rules": self.active_count,
            "deprecated_rules": self.deprecated_count,
            "rules": {r.rule_id: r.to_dict() for r in self.rules()},
            "by_validator": {k: sorted(v) for k, v in sorted(by_validator.items())},
            "by_severity": {
                sev.name: [r.rule_id for r in self.filter_by_severity(sev)]
                for sev in Severity
                if self.filter_by_severity(sev)
            },
            "by_status": {
                status.value: [r.rule_id for r in self.filter_by_status(status)]
                for status in RuleStatus
                if self.filter_by_status(status)
            },
            "replacement_chains": {
                r.rule_id: self.get_replacement_chain(r.rule_id)
                for r in self.deprecated_rules()
                if r.has_replacement
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL REGISTRY SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_rule_registry: RuleRegistry | None = None


def get_rule_registry() -> RuleRegistry:
    """Get or create the global rule registry singleton."""
    global _rule_registry
    if _rule_registry is None:
        _rule_registry = RuleRegistry()
    return _rule_registry
