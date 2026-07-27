"""
Tests for the Immutable Contract Rule ID system.

Covers:
    - Every validator declares immutable Contract Rules
    - Every rule has a unique ID
    - Duplicate rule IDs are rejected
    - Violations include the rule_id
    - Quarantine records preserve the rule_id
    - Registry lookup works
    - Documentation metadata is accessible
    - Existing validator behaviour is unchanged
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.contracts import (
    ContractViolation,
    Severity,
    get_enforcer,
    get_rule_registry,
)
from core.contracts.contract_rule import ContractRule, RuleRegistry
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineStore


# -------------------------------------------------------------------------------
# TEST: CONTRACT RULE MODEL
# -------------------------------------------------------------------------------

class TestContractRuleModel:
    def test_rule_is_immutable(self):
        rule = ContractRule(
            rule_id="TEST_001",
            title="Test Rule",
            description="Test description",
            validator_id="TEST_V",
            severity=Severity.ERROR,
            confidence=95,
        )
        with pytest.raises(AttributeError):
            rule.rule_id = "HACKED"  # type: ignore

    def test_rule_to_dict(self):
        rule = ContractRule(
            rule_id="TEST_002",
            title="Another Rule",
            description="Checks something.",
            validator_id="TEST_V",
            severity=Severity.WARNING,
            confidence=65,
            documentation="test_doc",
            recommendation="Fix it.",
            introduced_in="Arc1",
        )
        d = rule.to_dict()
        assert d["rule_id"] == "TEST_002"
        assert d["title"] == "Another Rule"
        assert d["severity"] == "WARNING"
        assert d["confidence"] == 65
        assert d["confidence_level"] == "HIGH"
        assert d["documentation"] == "test_doc"
        assert d["recommendation"] == "Fix it."
        assert d["deprecated"] is False


# -------------------------------------------------------------------------------
# TEST: RULE REGISTRY
# -------------------------------------------------------------------------------

class TestRuleRegistry:
    def test_register_and_lookup(self):
        reg = RuleRegistry()
        rule = ContractRule(
            rule_id="REG_001", title="T", description="D",
            validator_id="V", severity=Severity.ERROR,
        )
        reg.register(rule)
        assert reg.get("REG_001") == rule
        assert reg.count == 1

    def test_duplicate_id_rejected(self):
        reg = RuleRegistry()
        rule1 = ContractRule(
            rule_id="DUP_001", title="First", description="D",
            validator_id="V1", severity=Severity.ERROR,
        )
        rule2 = ContractRule(
            rule_id="DUP_001", title="Second", description="D",
            validator_id="V2", severity=Severity.WARNING,
        )
        reg.register(rule1)
        with pytest.raises(ValueError, match="collision"):
            reg.register(rule2)

    def test_search_by_prefix(self):
        reg = RuleRegistry()
        reg.register(ContractRule(rule_id="PERSIST_TIME_001", title="T", description="D", validator_id="V", severity=Severity.ERROR))
        reg.register(ContractRule(rule_id="PERSIST_TIME_002", title="T", description="D", validator_id="V", severity=Severity.ERROR))
        reg.register(ContractRule(rule_id="SCHEMA_FIELD_001", title="T", description="D", validator_id="V", severity=Severity.ERROR))

        results = reg.search("PERSIST_TIME")
        assert len(results) == 2

    def test_filter_by_validator(self):
        reg = RuleRegistry()
        reg.register(ContractRule(rule_id="A_001", title="T", description="D", validator_id="VAL_A", severity=Severity.ERROR))
        reg.register(ContractRule(rule_id="B_001", title="T", description="D", validator_id="VAL_B", severity=Severity.ERROR))
        assert len(reg.filter_by_validator("VAL_A")) == 1

    def test_filter_by_severity(self):
        reg = RuleRegistry()
        reg.register(ContractRule(rule_id="E_001", title="T", description="D", validator_id="V", severity=Severity.ERROR))
        reg.register(ContractRule(rule_id="W_001", title="T", description="D", validator_id="V", severity=Severity.WARNING))
        assert len(reg.filter_by_severity(Severity.ERROR)) == 1

    def test_export(self):
        reg = RuleRegistry()
        reg.register(ContractRule(rule_id="EXP_001", title="T", description="D", validator_id="V", severity=Severity.ERROR))
        export = reg.export()
        assert export["total_rules"] == 1
        assert "EXP_001" in export["rules"]
        assert "by_validator" in export
        assert "by_severity" in export


# -------------------------------------------------------------------------------
# TEST: GLOBAL REGISTRY WITH REAL RULES
# -------------------------------------------------------------------------------

class TestGlobalRuleRegistry:
    def test_all_rules_registered(self):
        """All 22 contract rules should be registered globally."""
        from core.contracts.rules import ALL_RULES, register_all_rules
        register_all_rules()
        registry = get_rule_registry()
        assert registry.count >= len(ALL_RULES)

    def test_all_rule_ids_unique(self):
        from core.contracts.rules import ALL_RULES
        ids = [r.rule_id for r in ALL_RULES]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_rules_have_documentation(self):
        from core.contracts.rules import ALL_RULES
        for rule in ALL_RULES:
            assert rule.rule_id, "Rule missing rule_id"
            assert rule.title, f"{rule.rule_id} missing title"
            assert rule.description, f"{rule.rule_id} missing description"
            assert rule.validator_id, f"{rule.rule_id} missing validator_id"
            assert rule.introduced_in, f"{rule.rule_id} missing introduced_in"

    def test_registry_lookup(self):
        from core.contracts.rules import register_all_rules
        register_all_rules()
        registry = get_rule_registry()

        rule = registry.get("PERSIST_TIME_003")
        assert rule is not None
        assert rule.title == "Time Travel Detected"
        assert rule.severity == Severity.ERROR
        assert rule.confidence == 90
        assert rule.recommendation != ""

    def test_registry_search(self):
        from core.contracts.rules import register_all_rules
        register_all_rules()
        registry = get_rule_registry()

        causal_rules = registry.search("CAUSAL_")
        assert len(causal_rules) >= 4


# -------------------------------------------------------------------------------
# TEST: VIOLATIONS INCLUDE RULE_ID
# -------------------------------------------------------------------------------

class TestViolationRuleId:
    def test_violation_carries_rule_id(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestVal",
            severity=Severity.ERROR,
            reason="test",
            rule_id="PERSIST_R_001",
            rule_title="R-Multiple Exceeds Sanity Bounds",
        )
        assert v.rule_id == "PERSIST_R_001"
        assert v.rule_title == "R-Multiple Exceeds Sanity Bounds"

    def test_violation_rule_id_in_to_dict(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestVal",
            severity=Severity.ERROR,
            reason="test",
            rule_id="SCHEMA_SECTION_001",
            rule_title="Missing Required Section",
        )
        d = v.to_dict()
        assert d["rule_id"] == "SCHEMA_SECTION_001"
        assert d["rule_title"] == "Missing Required Section"

    def test_rule_id_is_immutable(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestVal",
            severity=Severity.ERROR,
            reason="test",
            rule_id="X",
            rule_title="Y",
        )
        with pytest.raises(AttributeError):
            v.rule_id = "HACKED"  # type: ignore

    def test_real_validators_produce_rule_ids(self, tmp_path):
        """Real production validators produce violations with rule_ids."""
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        e.register(SchemaValidator())
        e.register(PersistenceValidator())

        # Trigger violations
        record = {
            "trade_id": "RULE_TEST",
            "symbol": "",
            "outcome": {"r_multiple": 999.0},
            "prices": {"entry_price": 1.1, "stop_loss": 1.099, "exit_price": 1.12},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        }
        result = e.validate(record, layer="test")
        assert len(result.violations) > 0

        # Every violation should have a rule_id
        for v in result.violations:
            assert v.rule_id != "", f"Violation missing rule_id: {v.reason}"
            assert v.rule_title != "", f"Violation missing rule_title: {v.reason}"


# -------------------------------------------------------------------------------
# TEST: QUARANTINE PRESERVES RULE_ID
# -------------------------------------------------------------------------------

class TestQuarantineRuleId:
    def test_quarantine_preserves_rule_id(self, tmp_path):
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        violations = [ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="V_001", validator_version=1,
            severity=Severity.ERROR, confidence=99,
            rule_id="PERSIST_R_001", rule_title="R-Multiple Exceeds Sanity Bounds",
            reason="R too high",
        )]
        record = {"trade_id": "QR_RULE", "symbol": "EURUSD"}
        qr = store.quarantine(record=record, violations=violations, layer="test")

        d = qr.to_dict()
        assert d["violations"][0]["rule_id"] == "PERSIST_R_001"
        assert d["violations"][0]["rule_title"] == "R-Multiple Exceeds Sanity Bounds"

    def test_quarantine_persisted_with_rule_id(self, tmp_path):
        """Quarantine JSONL on disk includes rule_id."""
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        violations = [ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="V_001", validator_version=1,
            severity=Severity.ERROR,
            rule_id="CAUSAL_SIGN_001", rule_title="Stop Loss Exit With Positive R",
            reason="causal issue",
        )]
        store.quarantine(record={"trade_id": "DISK_TEST"}, violations=violations, layer="test")

        # Read back from disk
        records = store.load_quarantined(layer="test")
        assert len(records) >= 1
        assert records[0]["violations"][0]["rule_id"] == "CAUSAL_SIGN_001"
