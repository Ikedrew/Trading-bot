"""
Tests for the Unified Correlation Spine Contract.

Covers:
    - Missing correlation_id ? WARNING (upstream) / ERROR (persistence)
    - Invalid format ? WARNING
    - Symbol mismatch ? CRITICAL
    - Valid correlation passes all checks
    - Layer-aware severity escalation
    - CorrelationValidator in dependency graph
    - Spine consistency across shadow_trade ? truth ? graph
"""

from __future__ import annotations

import pytest

from core.contracts import ContractViolation, Severity, get_rule_registry
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineStore
from core.contracts.validators.correlation_validator import CorrelationValidator
from core.correlation import generate_correlation_id


@pytest.fixture
def enforcer(tmp_path):
    """Enforcer with schema + correlation validators."""
    store = QuarantineStore(local_dir=str(tmp_path / "q"))
    e = ContractEnforcer(quarantine_store=store)
    from core.contracts.validators.schema_validator import SchemaValidator
    from core.contracts.validators.persistence_validator import PersistenceValidator
    e.register(SchemaValidator())
    e.register(PersistenceValidator())
    e.register(CorrelationValidator())
    return e


@pytest.fixture
def valid_record():
    cor = generate_correlation_id(cycle_id=100, symbol="EURUSD", timestamp=1720108800.0)
    return {
        "trade_id": "SPINE_001",
        "symbol": "EURUSD",
        "correlation_id": cor,
        "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
        "outcome": {"r_multiple": 2.0, "exit_reason": "take_profit", "bars_held": 10},
        "strategy_meta": {"pattern": "ENGULFING", "strategy": "momentum_v1"},
    }


# -------------------------------------------------------------------------------
# TEST: VALID RECORD PASSES
# -------------------------------------------------------------------------------

class TestValidCorrelation:
    def test_valid_record_no_correlation_violations(self, enforcer, valid_record):
        result = enforcer.validate(valid_record, layer="shadow_trades")
        cor_violations = [v for v in result.violations if "CORRELATION" in v.rule_id]
        assert len(cor_violations) == 0


# -------------------------------------------------------------------------------
# TEST: MISSING CORRELATION — LAYER-AWARE SEVERITY
# -------------------------------------------------------------------------------

class TestMissingCorrelation:
    def test_missing_in_upstream_is_warning(self, enforcer):
        record = {
            "trade_id": "UP_001", "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="unknown")
        cor = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor) == 1
        assert cor[0].severity == Severity.WARNING

    def test_missing_in_shadow_trades_is_error(self, enforcer):
        record = {
            "trade_id": "ST_001", "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        cor = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor) == 1
        assert cor[0].severity == Severity.ERROR

    def test_missing_in_trade_truth_is_error(self, enforcer):
        record = {
            "trade_id": "TT_001", "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="trade_truth")
        cor = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor) == 1
        assert cor[0].severity == Severity.ERROR

    def test_missing_in_edge_attribution_is_error(self, enforcer):
        record = {
            "trade_id": "EA_001", "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="edge_attribution")
        cor = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor) == 1
        assert cor[0].severity == Severity.ERROR


# -------------------------------------------------------------------------------
# TEST: FORMAT VALIDATION
# -------------------------------------------------------------------------------

class TestCorrelationFormat:
    def test_invalid_format_warns(self, enforcer):
        record = {
            "trade_id": "FMT_001", "symbol": "EURUSD",
            "correlation_id": "INVALID-FORMAT",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="test")
        fmt = [v for v in result.violations if v.rule_id == "CORRELATION_FORMAT_001"]
        assert len(fmt) == 1
        assert fmt[0].severity == Severity.WARNING

    def test_valid_format_no_warning(self, enforcer, valid_record):
        result = enforcer.validate(valid_record, layer="test")
        fmt = [v for v in result.violations if v.rule_id == "CORRELATION_FORMAT_001"]
        assert len(fmt) == 0


# -------------------------------------------------------------------------------
# TEST: SYMBOL MISMATCH ? CRITICAL
# -------------------------------------------------------------------------------

class TestSpineMismatch:
    def test_symbol_mismatch_is_critical(self, enforcer):
        """correlation_id for GBPUSD but record says EURUSD ? CRITICAL."""
        gbp_cor = generate_correlation_id(cycle_id=200, symbol="GBPUSD", timestamp=1720108800.0)
        record = {
            "trade_id": "MISMATCH_001",
            "symbol": "EURUSD",  # Different from correlation!
            "correlation_id": gbp_cor,
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        spine = [v for v in result.violations if v.rule_id == "CORRELATION_SPINE_001"]
        assert len(spine) == 1
        assert spine[0].severity == Severity.CRITICAL
        assert "does not match" in spine[0].reason.lower()

    def test_matching_symbol_no_spine_violation(self, enforcer, valid_record):
        result = enforcer.validate(valid_record, layer="shadow_trades")
        spine = [v for v in result.violations if v.rule_id == "CORRELATION_SPINE_001"]
        assert len(spine) == 0


# -------------------------------------------------------------------------------
# TEST: CORRELATION VALIDATOR IN GRAPH
# -------------------------------------------------------------------------------

class TestCorrelationInGraph:
    def test_correlation_validator_registered(self):
        """CorrelationValidator registers and executes in the graph."""
        from core.contracts import get_enforcer
        e = get_enforcer()
        assert "CorrelationValidator" in e.registered_validators

    def test_correlation_validator_identity(self):
        v = CorrelationValidator()
        assert v.validator_id == "CORRELATION_001"
        assert v.contract_name == "correlation_spine_integrity"
        assert v.depends_on == ("SCHEMA_001",)
        assert v.default_confidence == 100


# -------------------------------------------------------------------------------
# TEST: SPINE CONSISTENCY ACROSS LAYERS
# -------------------------------------------------------------------------------

class TestSpineConsistency:
    def test_same_correlation_across_shadow_lifecycle(self):
        """Same correlation_id propagates through the shadow_trade lifecycle.

        (The trade_truth_graph node hop was retired in the Production V1
        consolidation; correlation_id continuity is now verified end-to-end
        within the shadow_trades STR record itself.)
        """
        from core.shadow_trades import ShadowTradeEngine

        cor = generate_correlation_id(cycle_id=300, symbol="EURUSD", timestamp=1720108800.0)

        engine = ShadowTradeEngine()
        engine.open_trade(
            trade_id="SPINE_T1", cycle_id=300, symbol="EURUSD",
            direction="BUY", entry_price=1.1, stop_loss=1.099,
            take_profit=1.103, entry_time=1700000000.0,
            correlation_id=cor,
        )
        records = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.104, bar_low=1.0995, bar_close=1.103,
            bar_time=1700000300.0, bar_index=1,
        )
        assert len(records) == 1
        truth_record = records[0]
        # STR schema: correlation_id in identity section
        assert truth_record["identity"]["correlation_id"] == cor

    def test_null_prohibition(self, enforcer):
        """correlation_id cannot be None — treated as missing."""
        record = {
            "trade_id": "NULL_001", "symbol": "EURUSD",
            "correlation_id": None,  # Explicitly None
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 1.0},
        }
        result = enforcer.validate(record, layer="shadow_trades")
        cor = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor) == 1


# -------------------------------------------------------------------------------
# TEST: RULES REGISTERED IN GLOBAL REGISTRY
# -------------------------------------------------------------------------------

class TestCorrelationRulesRegistered:
    def test_all_correlation_rules_registered(self):
        from core.contracts.rules import register_all_rules
        register_all_rules()
        registry = get_rule_registry()
        assert registry.get("CORRELATION_MISSING_001") is not None
        assert registry.get("CORRELATION_FORMAT_001") is not None
        assert registry.get("CORRELATION_SPINE_001") is not None
        # SPINE_001 should be CRITICAL
        spine = registry.get("CORRELATION_SPINE_001")
        assert spine.severity == Severity.CRITICAL
