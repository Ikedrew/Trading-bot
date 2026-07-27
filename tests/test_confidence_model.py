"""
Tests for the Confidence Model in the Contract Enforcement Framework.

Covers:
    - Confidence classification (numeric ? level)
    - Confidence immutability on ContractViolation
    - Confidence preserved through quarantine
    - Confidence in audit output (to_dict)
    - Independence of Severity and Confidence
    - Auto-classification from numeric value
    - Deterministic and probabilistic confidence support
    - Default confidence from validator identity
    - Per-violation confidence override
"""

from __future__ import annotations

import pytest

from core.contracts import (
    Confidence,
    ContractViolation,
    Severity,
    classify_confidence,
    confidence_to_numeric,
    get_enforcer,
)
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineStore


# -------------------------------------------------------------------------------
# TEST: CONFIDENCE CLASSIFICATION
# -------------------------------------------------------------------------------

class TestConfidenceClassification:
    def test_very_low_range(self):
        assert classify_confidence(0) == Confidence.VERY_LOW
        assert classify_confidence(10) == Confidence.VERY_LOW
        assert classify_confidence(20) == Confidence.VERY_LOW

    def test_low_range(self):
        assert classify_confidence(21) == Confidence.LOW
        assert classify_confidence(30) == Confidence.LOW
        assert classify_confidence(40) == Confidence.LOW

    def test_medium_range(self):
        assert classify_confidence(41) == Confidence.MEDIUM
        assert classify_confidence(50) == Confidence.MEDIUM
        assert classify_confidence(60) == Confidence.MEDIUM

    def test_high_range(self):
        assert classify_confidence(61) == Confidence.HIGH
        assert classify_confidence(70) == Confidence.HIGH
        assert classify_confidence(80) == Confidence.HIGH

    def test_very_high_range(self):
        assert classify_confidence(81) == Confidence.VERY_HIGH
        assert classify_confidence(90) == Confidence.VERY_HIGH
        assert classify_confidence(100) == Confidence.VERY_HIGH

    def test_clamping(self):
        assert classify_confidence(-10) == Confidence.VERY_LOW
        assert classify_confidence(200) == Confidence.VERY_HIGH

    def test_numeric_midpoints(self):
        assert confidence_to_numeric(Confidence.VERY_LOW) == 10
        assert confidence_to_numeric(Confidence.LOW) == 30
        assert confidence_to_numeric(Confidence.MEDIUM) == 50
        assert confidence_to_numeric(Confidence.HIGH) == 70
        assert confidence_to_numeric(Confidence.VERY_HIGH) == 90


# -------------------------------------------------------------------------------
# TEST: VIOLATION CONFIDENCE FIELDS
# -------------------------------------------------------------------------------

class TestViolationConfidence:
    def test_default_confidence_is_100(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestValidator",
            severity=Severity.ERROR,
            reason="test violation",
        )
        assert v.confidence == 100
        assert v.confidence_level == Confidence.VERY_HIGH

    def test_explicit_numeric_confidence(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestValidator",
            severity=Severity.WARNING,
            reason="uncertain finding",
            confidence=45,
        )
        assert v.confidence == 45
        assert v.confidence_level == Confidence.MEDIUM

    def test_auto_classification_from_numeric(self):
        """confidence_level is auto-derived from numeric confidence."""
        v = ContractViolation(
            contract_name="test",
            validator_name="TestValidator",
            severity=Severity.ERROR,
            reason="test",
            confidence=30,
        )
        assert v.confidence_level == Confidence.LOW

    def test_confidence_is_immutable(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestValidator",
            severity=Severity.ERROR,
            reason="test",
            confidence=75,
        )
        with pytest.raises(AttributeError):
            v.confidence = 50  # type: ignore
        with pytest.raises(AttributeError):
            v.confidence_level = Confidence.LOW  # type: ignore

    def test_confidence_in_to_dict(self):
        v = ContractViolation(
            contract_name="test",
            validator_name="TestValidator",
            severity=Severity.WARNING,
            reason="uncertain",
            confidence=55,
        )
        d = v.to_dict()
        assert d["confidence"] == 55
        assert d["confidence_level"] == "MEDIUM"

    def test_severity_and_confidence_independent(self):
        """Critical severity can have low confidence and vice versa."""
        v1 = ContractViolation(
            contract_name="test",
            validator_name="T",
            severity=Severity.CRITICAL,
            reason="critical but uncertain",
            confidence=30,
        )
        v2 = ContractViolation(
            contract_name="test",
            validator_name="T",
            severity=Severity.INFO,
            reason="info but certain",
            confidence=99,
        )
        assert v1.severity == Severity.CRITICAL
        assert v1.confidence_level == Confidence.LOW
        assert v2.severity == Severity.INFO
        assert v2.confidence_level == Confidence.VERY_HIGH


# -------------------------------------------------------------------------------
# TEST: QUARANTINE PRESERVES CONFIDENCE
# -------------------------------------------------------------------------------

class TestQuarantineConfidence:
    def test_quarantine_preserves_confidence(self, tmp_path):
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        violations = [
            ContractViolation(
                contract_name="test",
                validator_name="TestVal",
                validator_id="TEST_001",
                validator_version=1,
                severity=Severity.ERROR,
                reason="test failure",
                confidence=85,
            ),
        ]
        record = {"trade_id": "CONF_TEST", "symbol": "EURUSD"}
        qr = store.quarantine(record=record, violations=violations, layer="test")

        # Check quarantine serialization includes confidence
        d = qr.to_dict()
        assert d["violations"][0]["confidence"] == 85
        assert d["violations"][0]["confidence_level"] == "VERY_HIGH"


# -------------------------------------------------------------------------------
# TEST: REAL VALIDATORS CONFIDENCE VALUES
# -------------------------------------------------------------------------------

class TestRealValidatorConfidence:
    def test_schema_validator_deterministic_confidence(self):
        """SchemaValidator produces 100% confidence (deterministic checks)."""
        from core.contracts.validators.schema_validator import SchemaValidator
        v = SchemaValidator()
        assert v.default_confidence == 100

        # Trigger a violation
        violations = v.validate(
            {"outcome": "not_a_dict", "prices": {"entry_price": 1.1}},
            layer="test",
        )
        assert len(violations) > 0
        # All schema violations are 100% confidence
        for viol in violations:
            assert viol.confidence == 100

    def test_feature_role_validator_lower_confidence(self):
        """FeatureRoleValidator produces <100% for semantic checks."""
        from core.contracts.validators.feature_role_validator import FeatureRoleValidator
        v = FeatureRoleValidator()
        assert v.default_confidence == 65

    def test_causal_validator_variable_confidence(self):
        """CausalValidator uses different confidence per rule."""
        from core.contracts.validators.causal_validator import CausalValidator
        v = CausalValidator()
        assert v.default_confidence == 80

        # Trigger causal violation (SL exit with positive R)
        record = {
            "outcome": {"r_multiple": 2.0, "exit_reason": "stop_loss", "bars_held": 5},
            "strategy_meta": {"pattern": "X", "strategy": "Y"},
        }
        violations = v.validate(record, layer="test")
        causal_errors = [vv for vv in violations if vv.severity >= Severity.ERROR]
        assert len(causal_errors) > 0
        # Causal R/exit inconsistency has 85% confidence
        assert causal_errors[0].confidence == 85
        assert causal_errors[0].confidence_level == Confidence.VERY_HIGH

    def test_immutability_validator_deterministic(self):
        """ImmutabilityValidator is 100% deterministic."""
        from core.contracts.validators.immutability_validator import ImmutabilityValidator
        v = ImmutabilityValidator()
        assert v.default_confidence == 100

    def test_persistence_validator_high_confidence(self):
        """PersistenceValidator is 95% default (timestamps may have clock edge cases)."""
        from core.contracts.validators.persistence_validator import PersistenceValidator
        v = PersistenceValidator()
        assert v.default_confidence == 95


# -------------------------------------------------------------------------------
# TEST: END-TO-END CONFIDENCE IN ENFORCEMENT
# -------------------------------------------------------------------------------

class TestEndToEndConfidence:
    def test_validation_result_carries_confidence(self, tmp_path):
        """Full enforcement pipeline preserves confidence through to output."""
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.causal_validator import CausalValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        from core.contracts.validators.feature_role_validator import FeatureRoleValidator
        e.register(SchemaValidator())
        e.register(FeatureRoleValidator())
        e.register(PersistenceValidator())
        e.register(CausalValidator())

        # Record with causal impossibility
        record = {
            "trade_id": "CONF_E2E",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 2.0, "exit_reason": "stop_loss", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }

        result = e.validate(record, layer="test")

        # Should have causal violation with 85% confidence
        causal_v = [v for v in result.violations if v.validator_id == "CAUSAL_001"]
        assert len(causal_v) > 0
        assert causal_v[0].confidence == 85

        # Serialized result preserves confidence
        d = result.to_dict()
        for v_dict in d["violations"]:
            assert "confidence" in v_dict
            assert "confidence_level" in v_dict
