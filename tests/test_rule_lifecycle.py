"""
Tests for Contract Rule Lifecycle & Deprecation.

Covers:
    - Duplicate IDs rejected
    - Deprecated rules remain searchable
    - Replacement chain works
    - Old violations still resolve correctly
    - Registry exposes lifecycle history
    - Historical rule identity never changes
    - Status transitions (ACTIVE → DEPRECATED → REPLACED)
    - Cannot deprecate unknown rule
    - Cannot deprecate already-deprecated rule
    - Replacement must exist before deprecation
"""

from __future__ import annotations

import pytest

from core.contracts.contract_rule import ContractRule, RuleRegistry, RuleStatus
from core.contracts.severity import Severity


@pytest.fixture
def registry():
    """Fresh registry for each test."""
    return RuleRegistry()


@pytest.fixture
def populated_registry(registry):
    """Registry with a few rules for lifecycle testing."""
    registry.register(ContractRule(
        rule_id="PERSIST_TIME_003",
        title="Time Travel Detected",
        description="Exit timestamp before entry.",
        validator_id="PERSISTENCE_001",
        severity=Severity.ERROR,
        confidence=90,
        introduced_in="Arc1",
    ))
    registry.register(ContractRule(
        rule_id="PERSIST_TIME_009",
        title="Time Travel V2",
        description="Exit timestamp before entry (with tolerance).",
        validator_id="PERSISTENCE_001",
        severity=Severity.ERROR,
        confidence=92,
        introduced_in="Arc2",
    ))
    registry.register(ContractRule(
        rule_id="SCHEMA_SECTION_001",
        title="Missing Required Section",
        description="Required section absent.",
        validator_id="SCHEMA_001",
        severity=Severity.ERROR,
        confidence=100,
        introduced_in="Arc1",
    ))
    return registry


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: DUPLICATE ID REJECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateRejection:
    def test_same_rule_idempotent(self, registry):
        """Re-registering the same rule (same title/validator) is idempotent."""
        rule = ContractRule(
            rule_id="X_001", title="Test", description="D",
            validator_id="V", severity=Severity.ERROR,
        )
        registry.register(rule)
        registry.register(rule)  # Should not raise
        assert registry.count == 1

    def test_different_rule_same_id_rejected(self, registry):
        """Different rule with same ID is a collision."""
        registry.register(ContractRule(
            rule_id="COLL_001", title="First", description="D",
            validator_id="V1", severity=Severity.ERROR,
        ))
        with pytest.raises(ValueError, match="collision"):
            registry.register(ContractRule(
                rule_id="COLL_001", title="Different", description="D",
                validator_id="V2", severity=Severity.WARNING,
            ))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: DEPRECATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeprecation:
    def test_deprecate_without_replacement(self, populated_registry):
        """A rule can be deprecated without specifying a replacement."""
        result = populated_registry.deprecate(
            "PERSIST_TIME_003",
            reason="No longer needed",
            deprecated_in="Arc2",
        )
        assert result.status == RuleStatus.DEPRECATED
        assert result.deprecated_in == "Arc2"
        assert result.replacement_reason == "No longer needed"
        assert result.replacement_rule_id == ""
        assert result.is_deprecated is True
        assert result.is_active is False

    def test_deprecate_with_replacement(self, populated_registry):
        """A rule can be replaced by another, creating a linked chain."""
        result = populated_registry.deprecate(
            "PERSIST_TIME_003",
            reason="Replaced with stricter tolerance check",
            replacement_rule_id="PERSIST_TIME_009",
            deprecated_in="Arc2",
        )
        assert result.status == RuleStatus.REPLACED
        assert result.replacement_rule_id == "PERSIST_TIME_009"
        assert result.has_replacement is True

    def test_cannot_deprecate_unknown_rule(self, populated_registry):
        with pytest.raises(KeyError, match="unknown"):
            populated_registry.deprecate("NONEXISTENT_001", reason="X")

    def test_cannot_deprecate_already_deprecated(self, populated_registry):
        populated_registry.deprecate("PERSIST_TIME_003", reason="First time")
        with pytest.raises(ValueError, match="already deprecated"):
            populated_registry.deprecate("PERSIST_TIME_003", reason="Second time")

    def test_replacement_must_exist(self, populated_registry):
        """Cannot specify a replacement that isn't registered."""
        with pytest.raises(KeyError, match="not registered"):
            populated_registry.deprecate(
                "SCHEMA_SECTION_001",
                reason="test",
                replacement_rule_id="NONEXISTENT_002",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: DEPRECATED RULES REMAIN SEARCHABLE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeprecatedSearchable:
    def test_deprecated_still_in_registry(self, populated_registry):
        """Deprecated rules are NEVER removed — they remain forever."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")
        rule = populated_registry.get("PERSIST_TIME_003")
        assert rule is not None
        assert rule.title == "Time Travel Detected"  # Original meaning preserved
        assert rule.is_deprecated is True

    def test_deprecated_in_search_results(self, populated_registry):
        """Search returns both active and deprecated rules."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")
        results = populated_registry.search("PERSIST_TIME")
        assert len(results) == 2  # Both 003 (deprecated) and 009 (active)

    def test_deprecated_in_rules_list(self, populated_registry):
        """rules() returns all rules including deprecated."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")
        all_rules = populated_registry.rules()
        ids = [r.rule_id for r in all_rules]
        assert "PERSIST_TIME_003" in ids

    def test_active_rules_filter(self, populated_registry):
        """active_rules() excludes deprecated."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")
        active = populated_registry.active_rules()
        ids = [r.rule_id for r in active]
        assert "PERSIST_TIME_003" not in ids
        assert "PERSIST_TIME_009" in ids

    def test_deprecated_rules_filter(self, populated_registry):
        """deprecated_rules() only returns deprecated."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")
        deprecated = populated_registry.deprecated_rules()
        ids = [r.rule_id for r in deprecated]
        assert "PERSIST_TIME_003" in ids
        assert "PERSIST_TIME_009" not in ids


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: REPLACEMENT CHAIN
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplacementChain:
    def test_single_replacement(self, populated_registry):
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            replacement_rule_id="PERSIST_TIME_009",
            reason="V2",
        )
        chain = populated_registry.get_replacement_chain("PERSIST_TIME_003")
        assert chain == ["PERSIST_TIME_003", "PERSIST_TIME_009"]

    def test_multi_step_chain(self, registry):
        """Chains can be multiple levels deep."""
        registry.register(ContractRule(rule_id="V1", title="T", description="D", validator_id="V", severity=Severity.ERROR))
        registry.register(ContractRule(rule_id="V2", title="T2", description="D", validator_id="V", severity=Severity.ERROR))
        registry.register(ContractRule(rule_id="V3", title="T3", description="D", validator_id="V", severity=Severity.ERROR))

        registry.deprecate("V1", replacement_rule_id="V2", reason="step 1")
        registry.deprecate("V2", replacement_rule_id="V3", reason="step 2")

        chain = registry.get_replacement_chain("V1")
        assert chain == ["V1", "V2", "V3"]

    def test_resolve_active_from_deprecated(self, populated_registry):
        """resolve_active() follows chain to find current active rule."""
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            replacement_rule_id="PERSIST_TIME_009",
            reason="V2",
        )
        active = populated_registry.resolve_active("PERSIST_TIME_003")
        assert active is not None
        assert active.rule_id == "PERSIST_TIME_009"
        assert active.is_active is True

    def test_resolve_active_already_active(self, populated_registry):
        """resolve_active() on an active rule returns itself."""
        active = populated_registry.resolve_active("PERSIST_TIME_009")
        assert active is not None
        assert active.rule_id == "PERSIST_TIME_009"

    def test_resolve_active_no_replacement(self, populated_registry):
        """Deprecated without replacement → resolve_active returns None."""
        populated_registry.deprecate("PERSIST_TIME_003", reason="No replacement")
        active = populated_registry.resolve_active("PERSIST_TIME_003")
        # The deprecated rule itself has no replacement,
        # but it IS in the chain. resolve should return None since
        # PERSIST_TIME_003 is not active.
        assert active is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: HISTORICAL INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoricalIntegrity:
    def test_original_meaning_preserved_after_deprecation(self, populated_registry):
        """After deprecation, the original title/description remain unchanged."""
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            reason="Replaced",
            replacement_rule_id="PERSIST_TIME_009",
            deprecated_in="Arc2",
        )
        rule = populated_registry.get("PERSIST_TIME_003")
        assert rule.title == "Time Travel Detected"
        assert rule.description == "Exit timestamp before entry."
        assert rule.severity == Severity.ERROR
        assert rule.confidence == 90
        assert rule.introduced_in == "Arc1"

    def test_old_violation_resolves_to_original(self, populated_registry):
        """A historical violation referencing the old rule still resolves."""
        # Simulate: violation was recorded with PERSIST_TIME_003
        old_violation_rule_id = "PERSIST_TIME_003"

        # Now deprecate it
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            replacement_rule_id="PERSIST_TIME_009",
            reason="V2",
        )

        # Historical resolution: old violations ALWAYS get original definition
        resolved = populated_registry.get(old_violation_rule_id)
        assert resolved is not None
        assert resolved.rule_id == "PERSIST_TIME_003"
        assert resolved.title == "Time Travel Detected"
        # The rule tells us it's deprecated and what replaced it
        assert resolved.is_deprecated is True
        assert resolved.replacement_rule_id == "PERSIST_TIME_009"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EXPORT WITH LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

class TestExportLifecycle:
    def test_export_includes_lifecycle(self, populated_registry):
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            replacement_rule_id="PERSIST_TIME_009",
            reason="V2",
            deprecated_in="Arc2",
        )
        export = populated_registry.export()
        assert export["registry_version"] == "rule_registry_v2"
        assert export["active_rules"] == 2
        assert export["deprecated_rules"] == 1
        assert "by_status" in export
        assert "REPLACED" in export["by_status"]
        assert "PERSIST_TIME_003" in export["by_status"]["REPLACED"]
        assert "replacement_chains" in export
        assert "PERSIST_TIME_003" in export["replacement_chains"]

    def test_export_rule_dict_includes_lifecycle_fields(self, populated_registry):
        populated_registry.deprecate(
            "PERSIST_TIME_003",
            replacement_rule_id="PERSIST_TIME_009",
            reason="Stricter tolerance",
            deprecated_in="Arc2",
        )
        export = populated_registry.export()
        rule_dict = export["rules"]["PERSIST_TIME_003"]
        assert rule_dict["status"] == "REPLACED"
        assert rule_dict["deprecated"] is True
        assert rule_dict["deprecated_in"] == "Arc2"
        assert rule_dict["replacement_rule_id"] == "PERSIST_TIME_009"
        assert rule_dict["replacement_reason"] == "Stricter tolerance"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: COUNTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCounts:
    def test_counts_reflect_lifecycle(self, populated_registry):
        assert populated_registry.count == 3
        assert populated_registry.active_count == 3
        assert populated_registry.deprecated_count == 0

        populated_registry.deprecate("PERSIST_TIME_003", reason="Test")

        assert populated_registry.count == 3  # Never removed
        assert populated_registry.active_count == 2
        assert populated_registry.deprecated_count == 1
