"""
Tests for Global Correlation Key (Decision Spine ID).

Covers:
    - Correlation ID generation (format, uniqueness, determinism)
    - Shadow trade has correlation_id
    - Violations include correlation_id
    - Correlation consistent across layers
    - No duplicates for same decision cycle
    - Missing correlation_id triggers rule
    - CorrelationContext propagation
    - ViolationStore correlation by correlation_id
"""

from __future__ import annotations

import pytest

from core.correlation import (
    CorrelationContext,
    clear_active_correlation,
    generate_correlation_id,
    get_active_correlation,
    set_active_correlation,
)
from core.contracts import ContractViolation, Severity, get_rule_registry
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineStore
from core.contracts.violation_id import ViolationStore


# -------------------------------------------------------------------------------
# TEST: CORRELATION ID GENERATION
# -------------------------------------------------------------------------------

class TestCorrelationIdGeneration:
    def test_format(self):
        cor_id = generate_correlation_id(cycle_id=182831, symbol="EURUSD")
        assert cor_id.startswith("COR-")
        parts = cor_id.split("-")
        assert len(parts) == 5  # COR-YYYYMMDD-cycle-SYMBOL-HASH
        assert parts[0] == "COR"
        assert len(parts[1]) == 8  # YYYYMMDD
        assert parts[2] == "182831"
        assert parts[3] == "EURUSD"
        assert len(parts[4]) == 4  # hash suffix

    def test_deterministic(self):
        """Same inputs + same timestamp ? same ID."""
        ts = 1720108800.0  # Fixed timestamp
        id1 = generate_correlation_id(cycle_id=100, symbol="GBPUSD", timestamp=ts)
        id2 = generate_correlation_id(cycle_id=100, symbol="GBPUSD", timestamp=ts)
        assert id1 == id2

    def test_unique_different_cycles(self):
        ts = 1720108800.0
        id1 = generate_correlation_id(cycle_id=100, symbol="EURUSD", timestamp=ts)
        id2 = generate_correlation_id(cycle_id=101, symbol="EURUSD", timestamp=ts)
        assert id1 != id2

    def test_unique_different_symbols(self):
        ts = 1720108800.0
        id1 = generate_correlation_id(cycle_id=100, symbol="EURUSD", timestamp=ts)
        id2 = generate_correlation_id(cycle_id=100, symbol="GBPUSD", timestamp=ts)
        assert id1 != id2

    def test_symbol_shortening(self):
        cor_id = generate_correlation_id(cycle_id=1, symbol="EURUSD", timestamp=1720108800.0)
        assert "EURUSD" in cor_id
        assert "_SB" not in cor_id


# -------------------------------------------------------------------------------
# TEST: CORRELATION CONTEXT
# -------------------------------------------------------------------------------

class TestCorrelationContext:
    def test_context_sets_active(self):
        cor_id = "COR-20260704-100-EURUSD-ABCD"
        with CorrelationContext(cor_id, "EURUSD"):
            assert get_active_correlation("EURUSD") == cor_id
        # After exit, cleared
        assert get_active_correlation("EURUSD") == ""

    def test_context_restores_previous(self):
        set_active_correlation("EURUSD", "OLD")
        with CorrelationContext("NEW", "EURUSD"):
            assert get_active_correlation("EURUSD") == "NEW"
        assert get_active_correlation("EURUSD") == "OLD"
        clear_active_correlation("EURUSD")

    def test_context_property(self):
        with CorrelationContext("COR-TEST", "X") as ctx:
            assert ctx.correlation_id == "COR-TEST"

    def test_different_symbols_independent(self):
        set_active_correlation("EURUSD", "COR-EUR")
        set_active_correlation("GBPUSD", "COR-GBP")
        assert get_active_correlation("EURUSD") == "COR-EUR"
        assert get_active_correlation("GBPUSD") == "COR-GBP"
        clear_active_correlation("EURUSD")
        clear_active_correlation("GBPUSD")


# -------------------------------------------------------------------------------
# TEST: SHADOW TRADE CORRELATION
# -------------------------------------------------------------------------------

class TestShadowTradeCorrelation:
    def test_shadow_trade_accepts_correlation_id(self):
        from core.shadow_trades import ShadowTradeEngine
        engine = ShadowTradeEngine()
        trade = engine.open_trade(
            trade_id="T1", cycle_id=100, symbol="EURUSD",
            direction="BUY", entry_price=1.1, stop_loss=1.099,
            take_profit=1.103, entry_time=1700000000.0,
            correlation_id="COR-20260704-100-EURUSD-ABCD",
        )
        assert trade.correlation_id == "COR-20260704-100-EURUSD-ABCD"

    def test_truth_record_includes_correlation_id(self):
        from core.shadow_trades import ShadowTradeEngine
        engine = ShadowTradeEngine()
        engine.open_trade(
            trade_id="T2", cycle_id=200, symbol="EURUSD",
            direction="BUY", entry_price=1.1, stop_loss=1.099,
            take_profit=1.103, entry_time=1700000000.0,
            correlation_id="COR-20260704-200-EURUSD-BEEF",
        )
        # Evaluate a bar that hits TP
        records = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.104, bar_low=1.0995, bar_close=1.103,
            bar_time=1700000300.0, bar_index=1,
        )
        assert len(records) == 1
        # STR schema: correlation_id in identity section
        assert records[0]["identity"]["correlation_id"] == "COR-20260704-200-EURUSD-BEEF"


# -------------------------------------------------------------------------------
# TEST: VIOLATION INCLUDES CORRELATION_ID
# -------------------------------------------------------------------------------

class TestViolationCorrelation:
    def test_violation_accepts_correlation_id(self):
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
            correlation_id="COR-20260704-100-EURUSD-ABCD",
        )
        assert v.correlation_id == "COR-20260704-100-EURUSD-ABCD"

    def test_violation_correlation_in_to_dict(self):
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
            correlation_id="COR-TEST",
        )
        d = v.to_dict()
        assert d["correlation_id"] == "COR-TEST"

    def test_violation_correlation_immutable(self):
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
            correlation_id="COR-X",
        )
        with pytest.raises(AttributeError):
            v.correlation_id = "HACKED"  # type: ignore


# -------------------------------------------------------------------------------
# TEST: MISSING CORRELATION TRIGGERS RULE
# -------------------------------------------------------------------------------

class TestMissingCorrelationRule:
    def test_missing_correlation_produces_warning(self, tmp_path):
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        from core.contracts.validators.correlation_validator import CorrelationValidator
        e.register(SchemaValidator())
        e.register(PersistenceValidator())
        e.register(CorrelationValidator())

        # Record WITHOUT correlation_id
        record = {
            "trade_id": "NO_COR",
            "symbol": "EURUSD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 2.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = e.validate(record, layer="test")
        cor_violations = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor_violations) == 1
        assert cor_violations[0].severity == Severity.WARNING

    def test_present_correlation_no_warning(self, tmp_path):
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        from core.contracts.validators.correlation_validator import CorrelationValidator
        e.register(SchemaValidator())
        e.register(PersistenceValidator())
        e.register(CorrelationValidator())

        # Record WITH correlation_id
        record = {
            "trade_id": "HAS_COR",
            "symbol": "EURUSD",
            "correlation_id": "COR-20260704-100-EURUSD-ABCD",
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
            "prices": {"entry_price": 1.1, "exit_price": 1.102, "stop_loss": 1.099},
            "outcome": {"r_multiple": 2.0, "exit_reason": "take_profit", "bars_held": 5},
            "strategy_meta": {"pattern": "HAMMER", "strategy": "momentum_v1"},
        }
        result = e.validate(record, layer="test")
        cor_violations = [v for v in result.violations if v.rule_id == "CORRELATION_MISSING_001"]
        assert len(cor_violations) == 0


# -------------------------------------------------------------------------------
# TEST: TRADE TRUTH GRAPH PROPAGATION
# -------------------------------------------------------------------------------

class TestGraphPropagation:
    def test_graph_node_includes_correlation_id(self):
        from core.trade_truth_graph import build_graph_node

        node = build_graph_node(
            trade_id="G1",
            correlation_id="COR-20260704-100-EURUSD-ABCD",
            symbol="EURUSD",
            cycle_id=100,
        )
        assert node["correlation_id"] == "COR-20260704-100-EURUSD-ABCD"


# -------------------------------------------------------------------------------
# TEST: STORE CORRELATION LOOKUP
# -------------------------------------------------------------------------------

class TestStoreCorrelationLookup:
    def test_find_by_correlation(self):
        """ViolationStore can find violations by correlation_id."""
        store = ViolationStore(max_entries=100)
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="r",
            correlation_id="COR-20260704-100-EURUSD-ABCD",
        )
        v_dict = v.to_dict()
        store.store(v_dict, record_id="T1")

        # Lookup by the violation_id
        found = store.get_violation(v.violation_id)
        assert found is not None
        assert found["correlation_id"] == "COR-20260704-100-EURUSD-ABCD"
