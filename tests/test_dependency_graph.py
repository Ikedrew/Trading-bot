"""
Tests for the Validator Dependency Graph system.

Covers:
    - Normal dependency-ordered execution
    - Schema failure propagation (all downstream skipped)
    - Feature failure propagation (only dependents skipped)
    - Independent validator continues on unrelated failure
    - Circular dependency detection
    - Missing dependency detection
    - Self-dependency detection
    - Duplicate validator detection
    - Deterministic execution ordering
    - Validator skip reporting (execution log)
    - Graph export
"""

from __future__ import annotations

from typing import Any

import pytest

from core.contracts import (
    BaseValidator,
    ContractViolation,
    Severity,
    ValidatorState,
)
from core.contracts.dependency_graph import DependencyGraph, GraphValidationError
from core.contracts.engine import ContractEnforcer, ValidatorExecution
from core.contracts.quarantine import QuarantineStore
from core.contracts.validator_identity import ValidatorIdentity


# -------------------------------------------------------------------------------
# TEST HELPERS — Minimal validators for dependency testing
# -------------------------------------------------------------------------------

def _make_validator(
    vid: str,
    name: str,
    depends_on: tuple[str, ...] = (),
    *,
    fail: bool = False,
    error_severity: Severity = Severity.ERROR,
    applies: bool = True,
    raises: bool = False,
) -> BaseValidator:
    """Factory for test validators with configurable behaviour."""

    class _TestValidator(BaseValidator):
        @property
        def identity(self) -> ValidatorIdentity:
            return ValidatorIdentity(
                validator_id=vid,
                validator_name=name,
                validator_version=1,
                contract_name=f"test_contract_{vid.lower()}",
                contract_version="v1",
                introduced_in_arc="Arc1",
                introduced_date="2026-07",
                owner="Test",
                description=f"Test validator {name}",
                depends_on=depends_on,
            )

        def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
            return applies

        def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
            if raises:
                raise RuntimeError(f"Validator {name} exploded")
            if fail:
                return [ContractViolation(
                    contract_name=self.contract_name,
                    validator_name=self.name,
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    severity=error_severity,
                    reason=f"{name} failed intentionally",
                    layer=layer,
                )]
            return []

    return _TestValidator()


@pytest.fixture
def enforcer(tmp_path):
    """Fresh enforcer with quarantine in temp dir."""
    store = QuarantineStore(local_dir=str(tmp_path / "quarantine"))
    return ContractEnforcer(quarantine_store=store)


@pytest.fixture
def sample_record():
    """Minimal valid record for testing."""
    return {
        "trade_id": "TEST_001",
        "symbol": "EURUSD",
        "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
        "outcome": {"r_multiple": 2.0, "exit_reason": "take_profit", "bars_held": 10},
        "strategy_meta": {"pattern": "ENGULFING", "strategy": "momentum_v1"},
    }


# -------------------------------------------------------------------------------
# TEST: DEPENDENCY GRAPH MODEL
# -------------------------------------------------------------------------------

class TestDependencyGraphModel:
    def test_basic_graph_build(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=[])
        graph.add_node("B", depends_on=["A"])
        graph.add_node("C", depends_on=["A", "B"])
        graph.build()
        assert graph.is_built
        assert graph.node_count == 3

    def test_execution_order_is_topological(self):
        graph = DependencyGraph()
        graph.add_node("C", depends_on=["A", "B"])
        graph.add_node("A", depends_on=[])
        graph.add_node("B", depends_on=["A"])
        graph.build()
        order = graph.execution_order
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("C")

    def test_execution_order_is_deterministic(self):
        """Same graph always produces same order."""
        for _ in range(10):
            graph = DependencyGraph()
            graph.add_node("X", depends_on=[])
            graph.add_node("Y", depends_on=[])
            graph.add_node("Z", depends_on=["X", "Y"])
            graph.build()
            assert graph.execution_order == ["X", "Y", "Z"]

    def test_circular_dependency_detected(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=["B"])
        graph.add_node("B", depends_on=["A"])
        with pytest.raises(GraphValidationError, match="[Cc]ircular"):
            graph.build()

    def test_self_dependency_detected(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=["A"])
        with pytest.raises(GraphValidationError, match="[Ss]elf"):
            graph.build()

    def test_missing_dependency_detected(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=["NONEXISTENT"])
        with pytest.raises(GraphValidationError, match="[Mm]issing"):
            graph.build()

    def test_cannot_modify_after_build(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=[])
        graph.build()
        with pytest.raises(RuntimeError, match="Cannot modify"):
            graph.add_node("B", depends_on=[])

    def test_get_all_downstream(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=[])
        graph.add_node("B", depends_on=["A"])
        graph.add_node("C", depends_on=["B"])
        graph.add_node("D", depends_on=["A"])
        graph.build()
        downstream = graph.get_all_downstream("A")
        assert downstream == {"B", "C", "D"}

    def test_compute_skip_set(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=[])
        graph.add_node("B", depends_on=["A"])
        graph.add_node("C", depends_on=["B"])
        graph.add_node("D", depends_on=[])  # Independent
        graph.build()
        skip = graph.compute_skip_set({"A"})
        assert "B" in skip
        assert "C" in skip
        assert "D" not in skip  # Independent

    def test_graph_export(self):
        graph = DependencyGraph()
        graph.add_node("A", depends_on=[])
        graph.add_node("B", depends_on=["A"])
        graph.build()
        export = graph.export()
        assert export["built"] is True
        assert export["node_count"] == 2
        assert export["execution_order"] == ["A", "B"]


# -------------------------------------------------------------------------------
# TEST: NORMAL EXECUTION (all pass)
# -------------------------------------------------------------------------------

class TestNormalExecution:
    def test_all_validators_pass(self, enforcer, sample_record):
        enforcer.register(_make_validator("ROOT", "Root"))
        enforcer.register(_make_validator("MID", "Mid", depends_on=("ROOT",)))
        enforcer.register(_make_validator("LEAF", "Leaf", depends_on=("MID",)))

        result = enforcer.validate(sample_record, layer="test")
        assert result.valid is True
        assert result.validators_run == 3
        assert len(result.skipped_validators) == 0
        assert all(e.state == ValidatorState.PASSED for e in result.execution_log)

    def test_execution_order_follows_graph(self, enforcer, sample_record):
        enforcer.register(_make_validator("C", "Third", depends_on=("A", "B")))
        enforcer.register(_make_validator("A", "First"))
        enforcer.register(_make_validator("B", "Second", depends_on=("A",)))

        result = enforcer.validate(sample_record, layer="test")
        ids = [e.validator_id for e in result.execution_log]
        assert ids.index("A") < ids.index("B")
        assert ids.index("B") < ids.index("C")


# -------------------------------------------------------------------------------
# TEST: SCHEMA FAILURE PROPAGATION
# -------------------------------------------------------------------------------

class TestSchemaFailurePropagation:
    def test_root_failure_skips_all_downstream(self, enforcer, sample_record):
        """If root (schema) fails, everything downstream is skipped."""
        enforcer.register(_make_validator("ROOT", "Root", fail=True))
        enforcer.register(_make_validator("MID", "Mid", depends_on=("ROOT",)))
        enforcer.register(_make_validator("LEAF", "Leaf", depends_on=("MID",)))

        result = enforcer.validate(sample_record, layer="test")
        assert result.valid is False
        assert result.validators_run == 1  # Only ROOT executed

        states = {e.validator_id: e.state for e in result.execution_log}
        assert states["ROOT"] == ValidatorState.FAILED
        assert states["MID"] == ValidatorState.SKIPPED
        assert states["LEAF"] == ValidatorState.SKIPPED

    def test_skipped_validators_produce_no_violations(self, enforcer, sample_record):
        """Skipped validators must NOT produce misleading violations."""
        enforcer.register(_make_validator("ROOT", "Root", fail=True))
        enforcer.register(_make_validator("MID", "Mid", depends_on=("ROOT",), fail=True))

        result = enforcer.validate(sample_record, layer="test")
        # Only ROOT's violation should appear
        assert len(result.violations) == 1
        assert result.violations[0].validator_id == "ROOT"


# -------------------------------------------------------------------------------
# TEST: PARTIAL FAILURE PROPAGATION
# -------------------------------------------------------------------------------

class TestPartialFailurePropagation:
    def test_independent_validators_continue(self, enforcer, sample_record):
        """If Feature fails, independent Persistence still runs."""
        enforcer.register(_make_validator("SCHEMA", "Schema"))
        enforcer.register(_make_validator("FEATURE", "Feature", depends_on=("SCHEMA",), fail=True))
        enforcer.register(_make_validator("PERSIST", "Persist", depends_on=("SCHEMA",)))  # Independent of FEATURE
        enforcer.register(_make_validator("CAUSAL", "Causal", depends_on=("FEATURE", "PERSIST")))

        result = enforcer.validate(sample_record, layer="test")

        states = {e.validator_id: e.state for e in result.execution_log}
        assert states["SCHEMA"] == ValidatorState.PASSED
        assert states["FEATURE"] == ValidatorState.FAILED
        assert states["PERSIST"] == ValidatorState.PASSED  # Independent — still runs
        assert states["CAUSAL"] == ValidatorState.SKIPPED  # Depends on FEATURE which failed

    def test_only_dependents_skipped(self, enforcer, sample_record):
        """Only validators that DEPEND on the failed one are skipped."""
        enforcer.register(_make_validator("A", "Alpha"))
        enforcer.register(_make_validator("B", "Beta", depends_on=("A",), fail=True))
        enforcer.register(_make_validator("C", "Gamma", depends_on=("A",)))  # Independent of B
        enforcer.register(_make_validator("D", "Delta", depends_on=("B",)))  # Depends on B

        result = enforcer.validate(sample_record, layer="test")

        states = {e.validator_id: e.state for e in result.execution_log}
        assert states["A"] == ValidatorState.PASSED
        assert states["B"] == ValidatorState.FAILED
        assert states["C"] == ValidatorState.PASSED  # Independent of B
        assert states["D"] == ValidatorState.SKIPPED  # Depends on B


# -------------------------------------------------------------------------------
# TEST: VALIDATOR EXCEPTION HANDLING
# -------------------------------------------------------------------------------

class TestValidatorExceptions:
    def test_exception_treated_as_failure(self, enforcer, sample_record):
        """Validator that raises is treated as FAILED (skips dependents)."""
        enforcer.register(_make_validator("A", "Root"))
        enforcer.register(_make_validator("B", "Broken", depends_on=("A",), raises=True))
        enforcer.register(_make_validator("C", "Child", depends_on=("B",)))

        result = enforcer.validate(sample_record, layer="test")

        states = {e.validator_id: e.state for e in result.execution_log}
        assert states["A"] == ValidatorState.PASSED
        assert states["B"] == ValidatorState.ERROR
        assert states["C"] == ValidatorState.SKIPPED


# -------------------------------------------------------------------------------
# TEST: NOT_APPLICABLE HANDLING
# -------------------------------------------------------------------------------

class TestNotApplicable:
    def test_not_applicable_counts_as_pass_for_deps(self, enforcer, sample_record):
        """A non-applicable validator doesn't block its dependents."""
        enforcer.register(_make_validator("A", "Root", applies=False))
        enforcer.register(_make_validator("B", "Child", depends_on=("A",)))

        result = enforcer.validate(sample_record, layer="test")

        states = {e.validator_id: e.state for e in result.execution_log}
        assert states["A"] == ValidatorState.NOT_APPLICABLE
        assert states["B"] == ValidatorState.PASSED  # Proceeds normally


# -------------------------------------------------------------------------------
# TEST: REAL VALIDATORS DEPENDENCY ORDER
# -------------------------------------------------------------------------------

class TestRealValidatorGraph:
    def test_production_graph_builds_successfully(self):
        """The real production validator set produces a valid graph."""
        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.feature_role_validator import FeatureRoleValidator
        from core.contracts.validators.immutability_validator import ImmutabilityValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        from core.contracts.validators.causal_validator import CausalValidator

        store = QuarantineStore(local_dir="logs/quarantine_test")
        e = ContractEnforcer(quarantine_store=store)
        e.register(SchemaValidator())
        e.register(FeatureRoleValidator())
        e.register(ImmutabilityValidator())
        e.register(PersistenceValidator())
        e.register(CausalValidator())

        e.build_graph()  # Should not raise

        order = e.execution_order
        # Schema must be first (root)
        assert order[0] == "SCHEMA_001"
        # Causal must be last (most dependencies)
        assert order[-1] == "CAUSAL_001"
        # Feature, Immutability, Persistence all after Schema
        assert order.index("SCHEMA_001") < order.index("FEATURE_001")
        assert order.index("SCHEMA_001") < order.index("IMMUTABILITY_001")
        assert order.index("SCHEMA_001") < order.index("PERSISTENCE_001")

    def test_production_schema_failure_skips_causal(self):
        """If schema validation fails, causal validator is skipped."""
        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.feature_role_validator import FeatureRoleValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        from core.contracts.validators.causal_validator import CausalValidator

        store = QuarantineStore(local_dir="logs/quarantine_test")
        e = ContractEnforcer(quarantine_store=store)
        e.register(SchemaValidator())
        e.register(FeatureRoleValidator())
        e.register(PersistenceValidator())
        e.register(CausalValidator())

        # Record missing required sections but has "outcome" to trigger schema validator
        bad_record = {"trade_id": "X", "symbol": "EURUSD", "outcome": "not_a_dict"}

        result = e.validate(bad_record, layer="test")

        states = {ex.validator_id: ex.state for ex in result.execution_log}
        assert states["SCHEMA_001"] == ValidatorState.FAILED
        # All dependents of SCHEMA_001 should be skipped
        assert states.get("FEATURE_001") == ValidatorState.SKIPPED
        assert states.get("PERSISTENCE_001") == ValidatorState.SKIPPED
        assert states.get("CAUSAL_001") == ValidatorState.SKIPPED


# -------------------------------------------------------------------------------
# TEST: EXECUTION LOG / AUDIT REPORTING
# -------------------------------------------------------------------------------

class TestExecutionLog:
    def test_execution_log_present(self, enforcer, sample_record):
        enforcer.register(_make_validator("A", "Alpha"))
        enforcer.register(_make_validator("B", "Beta", depends_on=("A",)))

        result = enforcer.validate(sample_record, layer="test")
        assert len(result.execution_log) == 2
        assert all(isinstance(e, ValidatorExecution) for e in result.execution_log)

    def test_skip_reason_recorded(self, enforcer, sample_record):
        enforcer.register(_make_validator("A", "Alpha", fail=True))
        enforcer.register(_make_validator("B", "Beta", depends_on=("A",)))

        result = enforcer.validate(sample_record, layer="test")
        skipped = [e for e in result.execution_log if e.state == ValidatorState.SKIPPED]
        assert len(skipped) == 1
        assert "A" in skipped[0].skip_reason
        assert "FAILED" in skipped[0].skip_reason

    def test_to_dict_includes_execution_log(self, enforcer, sample_record):
        enforcer.register(_make_validator("A", "Alpha"))
        result = enforcer.validate(sample_record, layer="test")
        d = result.to_dict()
        assert "execution_log" in d
        assert len(d["execution_log"]) == 1
        assert d["execution_log"][0]["state"] == "PASSED"


# -------------------------------------------------------------------------------
# TEST: DETERMINISM
# -------------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_validation_same_result(self, enforcer, sample_record):
        enforcer.register(_make_validator("A", "Alpha"))
        enforcer.register(_make_validator("B", "Beta", depends_on=("A",), fail=True))
        enforcer.register(_make_validator("C", "Gamma", depends_on=("B",)))

        results = [enforcer.validate(sample_record, layer="test") for _ in range(5)]

        for r in results:
            assert r.valid is False
            assert r.failed_validators == ["B"]
            assert r.skipped_validators == ["C"]
