"""
Tests for the Contract Enforcement Framework.

Validates:
    - Validator registration and extensibility
    - Severity classification
    - Quarantine behaviour
    - Clean record pass-through
    - Invalid record detection
    - Causal chain enforcement
    - Metrics tracking
    - Batch validation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.contracts import (
    BaseValidator,
    ContractViolation,
    Severity,
    ValidationResult,
)
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineRecord, QuarantineStore


# -------------------------------------------------------------------------------
# TEST FIXTURES
# -------------------------------------------------------------------------------

@pytest.fixture
def enforcer(tmp_path):
    """Fresh enforcer with quarantine in temp dir."""
    store = QuarantineStore(local_dir=str(tmp_path / "quarantine"))
    e = ContractEnforcer(quarantine_store=store)

    # Register all built-in validators
    from core.contracts.validators.schema_validator import SchemaValidator
    from core.contracts.validators.feature_role_validator import FeatureRoleValidator
    from core.contracts.validators.immutability_validator import ImmutabilityValidator
    from core.contracts.validators.persistence_validator import PersistenceValidator
    from core.contracts.validators.causal_validator import CausalValidator

    e.register(SchemaValidator())
    e.register(FeatureRoleValidator())
    e.register(ImmutabilityValidator())
    e.register(PersistenceValidator())
    e.register(CausalValidator())
    return e


@pytest.fixture
def clean_record():
    """A fully valid Trade Truth v2 record."""
    return {
        "schema_version": "trade_truth_v2",
        "trade_id": "SHADOW_001",
        "symbol": "EURUSD",
        "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        "prices": {
            "entry_price": 1.1000,
            "exit_price": 1.1020,
            "stop_loss": 1.0990,
            "take_profit": 1.1030,
        },
        "position": {"direction": "BUY", "lot_size": 0.01},
        "outcome": {
            "r_multiple": 2.0,
            "mfe_r": 2.5,
            "mae_r": 0.3,
            "pnl_price": 0.0020,
            "exit_reason": "take_profit",
            "bars_held": 12,
        },
        "strategy_meta": {
            "pattern": "BULLISH_ENGULFING",
            "strategy": "momentum_v1",
            "score": 5.2,
        },
        "htf_snapshot": {
            "alignment_score": 0.8,
            "H4": {"bias": "BULLISH", "regime": "TRENDING"},
        },
        "derived_metrics": {
            "exit_efficiency": 0.8,
            "reward_risk_ratio": 3.0,
            "time_in_trade_minutes": 60.0,
        },
        "risk_model": {"risk_price_distance": 0.0010},
        "edges": {"session": "LONDON", "regime": "TRENDING"},
    }


# -------------------------------------------------------------------------------
# TEST: SEVERITY MODEL
# -------------------------------------------------------------------------------

class TestSeverity:
    def test_severity_ordering(self):
        assert Severity.INFO < Severity.WARNING < Severity.ERROR < Severity.CRITICAL < Severity.FATAL

    def test_blocks_propagation(self):
        assert not Severity.INFO.blocks_propagation
        assert not Severity.WARNING.blocks_propagation
        assert Severity.ERROR.blocks_propagation
        assert Severity.CRITICAL.blocks_propagation
        assert Severity.FATAL.blocks_propagation

    def test_requires_quarantine(self):
        assert not Severity.WARNING.requires_quarantine
        assert Severity.ERROR.requires_quarantine
        assert Severity.CRITICAL.requires_quarantine

    def test_requires_alert(self):
        assert not Severity.ERROR.requires_alert
        assert Severity.CRITICAL.requires_alert
        assert Severity.FATAL.requires_alert

    def test_requires_halt(self):
        assert not Severity.CRITICAL.requires_halt
        assert Severity.FATAL.requires_halt


# -------------------------------------------------------------------------------
# TEST: CLEAN RECORD PASS-THROUGH
# -------------------------------------------------------------------------------

class TestCleanRecord:
    def test_clean_record_passes(self, enforcer, clean_record):
        result = enforcer.validate(clean_record, layer="shadow_trades")
        assert result.valid is True
        assert result.quarantined is False
        assert result.should_propagate is True
        assert result.max_severity <= Severity.WARNING

    def test_clean_record_metrics(self, enforcer, clean_record):
        enforcer.validate(clean_record, layer="shadow_trades")
        metrics = enforcer.metrics()
        assert metrics["total_validated"] == 1
        assert metrics["clean_total"] >= 1


# -------------------------------------------------------------------------------
# TEST: SCHEMA VIOLATIONS
# -------------------------------------------------------------------------------

class TestSchemaValidator:
    def test_missing_outcome_section(self, enforcer):
        record = {
            "trade_id": "BAD_001",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert result.quarantined is True
        assert any("outcome" in v.reason for v in result.violations)

    def test_missing_timestamps(self, enforcer):
        record = {
            "trade_id": "BAD_002",
            "symbol": "EURUSD",
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert result.quarantined is True


# -------------------------------------------------------------------------------
# TEST: PERSISTENCE VIOLATIONS
# -------------------------------------------------------------------------------

class TestPersistenceValidator:
    def test_empty_symbol(self, enforcer):
        record = {
            "trade_id": "BAD_003",
            "symbol": "",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any("symbol" in v.field_name for v in result.violations)

    def test_insane_r_multiple(self, enforcer):
        record = {
            "trade_id": "BAD_004",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1000.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any("sanity" in v.reason.lower() or "inflation" in v.reason.lower() for v in result.violations)

    def test_time_travel(self, enforcer):
        record = {
            "trade_id": "BAD_005",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700003600, "exit_time": 1700000000},  # reversed
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any("time travel" in v.reason.lower() for v in result.violations)

    def test_zero_risk_distance(self, enforcer):
        record = {
            "trade_id": "BAD_006",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.1},  # SL = entry
            "outcome": {"r_multiple": 2.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any(v.severity >= Severity.CRITICAL for v in result.violations)


# -------------------------------------------------------------------------------
# TEST: CAUSAL VIOLATIONS
# -------------------------------------------------------------------------------

class TestCausalValidator:
    def test_stop_loss_with_positive_r(self, enforcer):
        record = {
            "trade_id": "CAUSAL_001",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 2.0, "exit_reason": "stop_loss", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any("causally impossible" in v.reason for v in result.violations)

    def test_take_profit_with_negative_r(self, enforcer):
        record = {
            "trade_id": "CAUSAL_002",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": -1.5, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.valid is False
        assert any("causally impossible" in v.reason for v in result.violations)


# -------------------------------------------------------------------------------
# TEST: QUARANTINE SYSTEM
# -------------------------------------------------------------------------------

class TestQuarantine:
    def test_quarantined_record_persisted(self, enforcer, tmp_path):
        record = {
            "trade_id": "QUARANTINE_001",
            "symbol": "",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        assert result.quarantined is True
        assert result.quarantine_record is not None
        assert result.quarantine_record.record_id == "QUARANTINE_001"
        assert result.quarantine_record.layer == "shadow_trades"

        # Verify quarantine persisted to disk
        q_files = list((tmp_path / "quarantine").rglob("*.jsonl"))
        assert len(q_files) > 0

        # Verify original payload preserved
        with open(q_files[0], "r") as f:
            data = json.loads(f.readline())
            assert data["original_payload"]["trade_id"] == "QUARANTINE_001"
            assert data["original_payload"]["symbol"] == ""  # Original preserved

    def test_quarantine_store_stats(self, enforcer):
        record = {
            "trade_id": "STATS_001",
            "symbol": "",
            "outcome": {"r_multiple": 1.0},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "strategy_meta": {"pattern": "X", "strategy": "Y"},
        }
        enforcer.validate(record, layer="test_layer")
        stats = enforcer.quarantine_store.stats()
        assert stats["total_quarantined"] >= 1
        assert "test_layer" in stats["by_layer"]


# -------------------------------------------------------------------------------
# TEST: EXTENSIBILITY
# -------------------------------------------------------------------------------

class TestExtensibility:
    def test_custom_validator_registration(self, enforcer):
        """Adding a new validator requires NO changes to engine."""

        class CustomValidator(BaseValidator):
            @property
            def identity(self) -> "ValidatorIdentity":
                from core.contracts.validator_identity import ValidatorIdentity
                return ValidatorIdentity(
                    validator_id="CUSTOM_TEST_001",
                    validator_name="CustomTestValidator",
                    validator_version=1,
                    contract_name="custom_test_contract",
                    contract_version="v1",
                    introduced_in_arc="Arc1",
                    introduced_date="2026-07",
                    owner="Test",
                    description="Test custom validator for extensibility verification.",
                )

            def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
                if record.get("custom_field") == "INVALID":
                    return [ContractViolation(
                        contract_name=self.contract_name,
                        validator_name=self.name,
                        validator_id=self.validator_id,
                        validator_version=self.validator_version,
                        severity=Severity.ERROR,
                        reason="Custom field is INVALID",
                        layer=layer,
                    )]
                return []

        enforcer.register(CustomValidator())
        assert "CustomTestValidator" in enforcer.registered_validators

        # Custom validator triggers on matching record
        result = enforcer.validate(
            {"custom_field": "INVALID", "outcome": {"r_multiple": 1.0},
             "timestamps": {"entry_time": 1, "exit_time": 2},
             "prices": {"entry_price": 1.1, "stop_loss": 1.09, "exit_price": 1.12},
             "symbol": "TEST", "trade_id": "X",
             "strategy_meta": {"pattern": "A", "strategy": "B"}},
            layer="test",
        )
        assert any(v.contract_name == "custom_test_contract" for v in result.violations)

    def test_unregister_validator(self, enforcer):
        initial_count = enforcer.validator_count
        enforcer.unregister("SchemaValidator")
        assert enforcer.validator_count == initial_count - 1
        assert "SchemaValidator" not in enforcer.registered_validators


# -------------------------------------------------------------------------------
# TEST: BATCH VALIDATION
# -------------------------------------------------------------------------------

class TestBatchValidation:
    def test_batch_filters_invalid(self, enforcer, clean_record):
        bad_record = {
            "trade_id": "BAD_BATCH",
            "symbol": "",
            "outcome": {"r_multiple": 1.0},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "strategy_meta": {"pattern": "A", "strategy": "B"},
        }
        records = [clean_record, bad_record, clean_record]
        clean, results = enforcer.validate_batch(records, layer="test")

        # bad_record should be filtered out
        assert len(clean) == 2
        assert len(results) == 3
        assert results[0].valid is True
        assert results[1].valid is False
        assert results[2].valid is True


# -------------------------------------------------------------------------------
# TEST: METRICS
# -------------------------------------------------------------------------------

class TestMetrics:
    def test_metrics_structure(self, enforcer, clean_record):
        enforcer.validate(clean_record, layer="shadow_trades")
        metrics = enforcer.metrics()

        assert "total_validated" in metrics
        assert "severity_counts" in metrics
        assert "quarantine_rate" in metrics
        assert "avg_validation_ms" in metrics
        assert "validator_failures" in metrics
        assert "most_violated_contracts" in metrics
        assert "violations_by_layer" in metrics
        assert "registered_validators" in metrics

    def test_metrics_reset(self, enforcer, clean_record):
        enforcer.validate(clean_record, layer="test")
        enforcer.reset_metrics()
        metrics = enforcer.metrics()
        assert metrics["total_validated"] == 0


# -------------------------------------------------------------------------------
# TEST: VALIDATION NEVER MUTATES RECORD
# -------------------------------------------------------------------------------

class TestReadOnly:
    def test_validation_does_not_mutate(self, enforcer, clean_record):
        import copy
        original = copy.deepcopy(clean_record)
        enforcer.validate(clean_record, layer="shadow_trades")
        assert clean_record == original

    def test_invalid_record_not_mutated(self, enforcer):
        import copy
        bad = {
            "trade_id": "MUTATE_TEST",
            "symbol": "",
            "outcome": {"r_multiple": 999.0, "exit_reason": "stop_loss"},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "strategy_meta": {"pattern": "X", "strategy": "Y"},
        }
        original = copy.deepcopy(bad)
        enforcer.validate(bad, layer="test")
        assert bad == original


# -------------------------------------------------------------------------------
# TEST: VALIDATOR IDENTITY & GOVERNANCE
# -------------------------------------------------------------------------------

class TestValidatorIdentity:
    def test_all_validators_have_identity(self, enforcer):
        """Every registered validator must have immutable identity metadata."""
        for v in enforcer._validators:
            identity = v.identity
            assert identity.validator_id, f"{v.name} has no validator_id"
            assert identity.validator_name, f"{v.name} has no validator_name"
            assert identity.validator_version >= 1, f"{v.name} has invalid version"
            assert identity.contract_name, f"{v.name} has no contract_name"
            assert identity.contract_version, f"{v.name} has no contract_version"
            assert identity.introduced_in_arc, f"{v.name} has no introduced_in_arc"
            assert identity.introduced_date, f"{v.name} has no introduced_date"
            assert identity.owner, f"{v.name} has no owner"
            assert identity.description, f"{v.name} has no description"

    def test_validator_ids_are_globally_unique(self, enforcer):
        """All validator_ids must be unique — no collisions."""
        ids = [v.identity.validator_id for v in enforcer._validators]
        assert len(ids) == len(set(ids)), f"Duplicate validator IDs: {ids}"

    def test_identity_fields_are_immutable(self, enforcer):
        """ValidatorIdentity is a frozen dataclass — cannot be mutated."""
        for v in enforcer._validators:
            identity = v.identity
            with pytest.raises(AttributeError):
                identity.validator_id = "HACKED"  # type: ignore

    def test_violations_carry_validator_id(self, enforcer):
        """Every violation must record the validator_id that produced it."""
        record = {
            "trade_id": "ID_TEST",
            "symbol": "",
            "outcome": {"r_multiple": 1.0, "exit_reason": "take_profit", "bars_held": 5},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "strategy_meta": {"pattern": "A", "strategy": "B"},
        }
        result = enforcer.validate(record, layer="test")
        # Should have violations from PersistenceValidator (empty symbol)
        error_violations = [v for v in result.violations if v.severity >= Severity.ERROR]
        assert len(error_violations) > 0
        for v in error_violations:
            assert v.validator_id != "", f"Violation missing validator_id: {v.reason}"
            assert v.validator_version >= 1, f"Violation missing validator_version: {v.reason}"

    def test_quarantine_records_validator_identity(self, enforcer):
        """Quarantine records must include the primary validator identity."""
        record = {
            "trade_id": "QR_ID_TEST",
            "symbol": "",
            "outcome": {"r_multiple": 1.0},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "strategy_meta": {"pattern": "A", "strategy": "B"},
        }
        result = enforcer.validate(record, layer="test")
        assert result.quarantined
        qr = result.quarantine_record
        assert qr.validator_id != "", "Quarantine missing validator_id"
        assert qr.validator_version >= 1, "Quarantine missing validator_version"

        # Serialized form also has identity
        d = qr.to_dict()
        assert "validator_id" in d
        assert "validator_version" in d
        assert d["quarantine_version"] == "quarantine_v2"

    def test_registry_read_only(self, enforcer):
        """Validator registry is accessible and reflects registered validators."""
        registry = enforcer.registry
        assert registry.count == enforcer.validator_count

        # Can lookup by ID
        schema = registry.get("SCHEMA_001")
        assert schema is not None
        assert schema.validator_name == "SchemaValidator"

        # Filter by arc
        arc1_validators = registry.filter_by_arc("Arc1")
        assert len(arc1_validators) == 5

        # Export is structured
        export = registry.export_registry()
        assert "validators" in export
        assert "by_arc" in export
        assert export["total_validators"] == 5

    def test_registry_rejects_id_collision(self):
        """Registry must reject duplicate IDs assigned to different validators."""
        from core.contracts.validator_identity import ValidatorIdentity, ValidatorRegistry

        reg = ValidatorRegistry()
        id1 = ValidatorIdentity(
            validator_id="COLLISION_TEST",
            validator_name="ValidatorA",
            validator_version=1,
            contract_name="test",
            contract_version="v1",
            introduced_in_arc="Arc1",
            introduced_date="2026-07",
            owner="Test",
            description="First",
        )
        id2 = ValidatorIdentity(
            validator_id="COLLISION_TEST",  # Same ID!
            validator_name="ValidatorB",     # Different name!
            validator_version=1,
            contract_name="test",
            contract_version="v1",
            introduced_in_arc="Arc1",
            introduced_date="2026-07",
            owner="Test",
            description="Second",
        )
        reg._register(id1)
        with pytest.raises(ValueError, match="collision"):
            reg._register(id2)
