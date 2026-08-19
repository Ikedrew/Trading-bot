"""
Tests for Candidate Shadow Hook — Stage-2 paired observations.
"""
import sys
import ast
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Any

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.candidate_shadow_hook import (
    open_candidate_shadows,
    _translate_change_definition,
    _candidate_applies,
)

# Patch targets (where the imports actually resolve)
_P_REG = "research_engine.v10.candidates.candidate_registry.CandidateRegistry"
_P_ENG = "core.shadow_trades.get_shadow_engine"


@dataclass
class MockCandidate:
    candidate_id: str = "OPT-test-001"
    status: str = "SHADOW_TESTING"
    change_definition: dict = field(default_factory=lambda: {"type": "direction_inversion"})


class MockShadowEngine:
    def __init__(self):
        self.opened = []
    def open_trade(self, **kwargs):
        self.opened.append(kwargs)


def _call_hook(registry_candidates, engine=None, **kw):
    """Helper to call open_candidate_shadows with mocked deps."""
    defaults = dict(symbol="EURUSD", cycle_id=1, direction="SELL",
                    entry_price=1.085, stop_loss=1.086, take_profit=1.083,
                    entry_time=1000.0, entry_bar_index=5,
                    correlation_id="COR-1", entity_id="EURUSD_1000",
                    pattern="TBC", score=0.6, bid=1.085, ask=1.0851)
    defaults.update(kw)
    eng = engine or MockShadowEngine()
    mock_reg = MagicMock()
    mock_reg.list_by_status.return_value = registry_candidates
    with patch(_P_REG, return_value=mock_reg):
        with patch(_P_ENG, return_value=eng):
            count = open_candidate_shadows(**defaults)
    return count, eng


class TestNoCandidates:
    def test_no_candidates_no_shadow(self):
        count, eng = _call_hook([])
        assert count == 0
        assert len(eng.opened) == 0

    def test_v10_primary_not_affected(self):
        count, eng = _call_hook([])
        assert len(eng.opened) == 0


class TestOneCandidate:
    def test_shadow_testing_opens_shadow(self):
        c = MockCandidate(change_definition={"type": "direction_inversion"})
        count, eng = _call_hook([c], correlation_id="COR-42", entity_id="E_42")
        assert count == 1
        assert eng.opened[0]["shadow_type"] == "CANDIDATE_OPT-test-001"
        assert eng.opened[0]["correlation_id"] == "COR-42"
        assert eng.opened[0]["entity_id"] == "E_42"


class TestMultipleCandidates:
    def test_multiple_shadows(self):
        c1 = MockCandidate(candidate_id="OPT-A", change_definition={"type": "direction_inversion"})
        c2 = MockCandidate(candidate_id="OPT-B", change_definition={"type": "geometry_modification", "stop_multiplier": 2.0})
        count, eng = _call_hook([c1, c2])
        assert count == 2
        types = {t["shadow_type"] for t in eng.opened}
        assert "CANDIDATE_OPT-A" in types
        assert "CANDIDATE_OPT-B" in types


class TestPairing:
    def test_same_correlation_and_entity(self):
        c = MockCandidate(change_definition={"type": "direction_inversion"})
        count, eng = _call_hook([c], correlation_id="COR-PAIR", entity_id="GB_2000")
        t = eng.opened[0]
        assert t["correlation_id"] == "COR-PAIR"
        assert t["entity_id"] == "GB_2000"
        assert "candidate" in t["trade_id"]


class TestDirectionInversion:
    def test_sell_becomes_buy(self):
        p = _translate_change_definition(change_definition={"type": "direction_inversion"},
            direction="SELL", entry_price=1.085, stop_loss=1.086,
            take_profit=1.083, risk_distance=0.001, symbol="EURUSD", pattern="TBC")
        assert p["direction"] == "BUY"
        assert p["stop_loss"] < 1.085
        assert p["take_profit"] > 1.085

    def test_buy_becomes_sell(self):
        p = _translate_change_definition(change_definition={"type": "direction_inversion"},
            direction="BUY", entry_price=1.085, stop_loss=1.084,
            take_profit=1.088, risk_distance=0.001, symbol="EURUSD", pattern="TWS")
        assert p["direction"] == "SELL"
        assert p["stop_loss"] > 1.085
        assert p["take_profit"] < 1.085


class TestGeometryModification:
    def test_wider_stop(self):
        p = _translate_change_definition(change_definition={"type": "geometry_modification", "stop_multiplier": 2.0},
            direction="BUY", entry_price=1.00, stop_loss=0.99,
            take_profit=1.03, risk_distance=0.01, symbol="EURUSD", pattern="X")
        assert p["direction"] == "BUY"
        assert p["stop_loss"] == pytest.approx(0.98)
        assert p["take_profit"] == 1.03


class TestUnsupportedType:
    def test_unknown_type_no_shadow(self):
        c = MockCandidate(change_definition={"type": "unknown_future_type"})
        count, eng = _call_hook([c])
        assert count == 0


class TestFailureIsolation:
    def test_error_does_not_propagate(self):
        with patch(_P_REG, side_effect=RuntimeError("boom")):
            count = open_candidate_shadows(
                symbol="EURUSD", cycle_id=1, direction="SELL",
                entry_price=1.085, stop_loss=1.086, take_profit=1.083,
                entry_time=1000.0, entry_bar_index=5,
                correlation_id="COR-1", entity_id="E_1",
                pattern="X", score=0.6, bid=1.085, ask=1.0851)
        assert count == 0


class TestGovernanceBoundary:
    def test_no_execution_imports(self):
        import research_engine.lifecycle.candidate_shadow_hook as hook
        source = open(hook.__file__, "r").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, 'module', '') or ''
                assert "mt5_execution" not in mod.lower()
                assert "execution_orchestrator" not in mod.lower()

    def test_no_config_imports(self):
        import research_engine.lifecycle.candidate_shadow_hook as hook
        source = open(hook.__file__, "r").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module == "core.config":
                    pytest.fail("Hook imports core.config")
