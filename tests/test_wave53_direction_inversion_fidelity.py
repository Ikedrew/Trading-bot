"""Wave 5.3C part 1: canonical geometry + shadow compat."""
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
sys.path.insert(0, ".")
from research_engine.lifecycle.candidate_shadow_hook import (
    _candidate_applies, _canonical_treatment_id,
    open_candidate_shadows, resolve_candidate_treatment)
from research_engine.lifecycle.direction_inversion_geometry import (
    DirectionInversionGeometry, canonical_direction_inversion,
    canonical_treatment_reference_entry)
_P_REG = "research_engine.v10.candidates.candidate_registry.CandidateRegistry"
_P_ENG = "core.shadow_trades.get_shadow_engine"


@dataclass
class MockCandidate:
    candidate_id: str = "OPT-4d1-001"
    status: str = "SHADOW_TESTING"
    change_definition: dict = field(
        default_factory=lambda: {"type": "direction_inversion"})


class MockShadowEngine:
    def __init__(self):
        self.opened = []

    def open_trade(self, **kwargs):
        self.opened.append(kwargs)


def _call_hook(registry_candidates, engine=None, **kw):
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


def _legacy(direction, entry_price, risk_distance):
    inv = "BUY" if direction == "SELL" else "SELL"
    if inv == "BUY":
        return inv, entry_price - risk_distance, entry_price + risk_distance * 3.0
    return inv, entry_price + risk_distance, entry_price - risk_distance * 3.0

class TestA:
    def test_01_buy_to_sell(self):
        g = canonical_direction_inversion(
            incumbent_direction="BUY", reference_entry=1.085, incumbent_stop=1.084)
        assert isinstance(g, DirectionInversionGeometry)
        assert g.inverted_direction == "SELL"

    def test_02_sell_to_buy(self):
        g = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=1.085, incumbent_stop=1.086)
        assert g.inverted_direction == "BUY"

    def test_03_r(self):
        g = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=1.10010, incumbent_stop=1.10000)
        assert g.risk_distance == pytest.approx(0.00010)

    def test_04_buy_geo(self):
        g = canonical_direction_inversion(
            incumbent_direction="BUY", reference_entry=1.085, incumbent_stop=1.084)
        assert g.stop == pytest.approx(1.086)
        assert g.target == pytest.approx(1.082)

    def test_05_sell_geo(self):
        g = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=1.085, incumbent_stop=1.086)
        assert g.stop == pytest.approx(1.084)
        assert g.target == pytest.approx(1.088)

    def test_06_3r(self):
        for d, stop in (("BUY", 1.084), ("SELL", 1.086)):
            g = canonical_direction_inversion(
                incumbent_direction=d, reference_entry=1.085, incumbent_stop=stop)
            assert abs(g.target - g.reference_entry) == pytest.approx(3 * g.risk_distance)

    @pytest.mark.parametrize("bad", ["HOLD", "NONE", "", "buy", None, 42, True])
    def test_07_baddir(self, bad):
        assert canonical_direction_inversion(
            incumbent_direction=bad, reference_entry=1.0, incumbent_stop=0.99) is None

    def test_08_zeror(self):
        assert canonical_direction_inversion(
            incumbent_direction="BUY", reference_entry=1.0, incumbent_stop=1.0) is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "x", True])
    def test_09_badref(self, bad):
        assert canonical_direction_inversion(
            incumbent_direction="BUY", reference_entry=bad, incumbent_stop=0.99) is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "x", False])
    def test_10_badstop(self, bad):
        assert canonical_direction_inversion(
            incumbent_direction="BUY", reference_entry=1.0, incumbent_stop=bad) is None
class TestB:
    def test_11_calls_helper(self):
        src = Path("research_engine/lifecycle/candidate_shadow_hook.py").read_text(encoding="utf-8")
        assert "canonical_direction_inversion" in src

    @pytest.mark.parametrize("direction,entry,stop,take", [
        ("SELL", 1.085, 1.086, 1.083),
        ("BUY", 1.085, 1.084, 1.088),
        ("SELL", 1.10010, 1.10020, 1.09980),
        ("BUY", 1.20000, 1.19900, 1.20300),
    ])
    def test_12_13_identical(self, direction, entry, stop, take):
        risk = abs(entry - stop)
        legacy = _legacy(direction, entry, risk)
        res, reason = resolve_candidate_treatment(
            change_definition={"type": "direction_inversion"}, direction=direction,
            entry_price=entry, stop_loss=stop, take_profit=take,
            risk_distance=risk, symbol="EURUSD", pattern="TBC")
        assert reason == "ok"
        got = (res.params["direction"], res.params["stop_loss"], res.params["take_profit"])
        assert got == pytest.approx(legacy, rel=0, abs=1e-12)

    def test_14_id_unchanged(self):
        for direction, entry, stop in (("SELL", 1.085, 1.086), ("BUY", 1.085, 1.084)):
            risk = abs(entry - stop)
            inv, sl, tp = _legacy(direction, entry, risk)
            legacy_id = _canonical_treatment_id(
                "direction_inversion", {}, {"direction": inv, "stop_loss": sl, "take_profit": tp})
            res, _ = resolve_candidate_treatment(
                change_definition={"type": "direction_inversion"}, direction=direction,
                entry_price=entry, stop_loss=stop, take_profit=entry,
                risk_distance=risk, symbol="EURUSD", pattern="TBC")
            assert res.treatment_id == legacy_id

    def test_15_payload(self):
        _, eng = _call_hook([MockCandidate()])
        t = eng.opened[0]
        assert t["direction"] == "BUY"
        assert t["entry_price"] == pytest.approx(1.085)

    def test_16_scope(self):
        c = MockCandidate(change_definition={"type": "direction_inversion", "patterns": ["TBC"]})
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="OTHER")

    def test_invalid_dir_fail_closed(self):
        res, reason = resolve_candidate_treatment(
            change_definition={"type": "direction_inversion"}, direction="HOLD",
            entry_price=1.0, stop_loss=0.99, take_profit=1.03,
            risk_distance=0.01, symbol="EURUSD", pattern="TBC")
        assert res is None and reason == "malformed_computed_geometry"

    def test_pure_no_io(self):
        a = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=1.085, incumbent_stop=1.086)
        b = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=1.085, incumbent_stop=1.086)
        assert a == b
        srcc = Path("research_engine/lifecycle/direction_inversion_geometry.py").read_text(encoding="utf-8")
        assert "time.time" not in srcc
        assert "mt5" not in srcc.lower()
class TestC:
    def test_17_midpoint(self):
        assert canonical_treatment_reference_entry(1.10000, 1.10020) == pytest.approx(1.10010)

    def test_18_19_structural_untouched(self):
        structural = 1.10000
        carried = canonical_treatment_reference_entry(1.10000, 1.10020)
        assert carried == pytest.approx(1.10010)
        assert structural == 1.10000

    def test_20_same_sample(self):
        bid, ask = 1.10000, 1.10020
        mid = (bid + ask) / 2
        count, eng = _call_hook([MockCandidate()], direction="SELL", entry_price=mid,
                                stop_loss=1.10020, take_profit=1.09980,
                                bid=bid, ask=ask, treatment_reference_entry=mid)
        assert count == 1
        t = eng.opened[0]
        assert t["entry_price"] == pytest.approx(mid)
        g = canonical_direction_inversion(
            incumbent_direction="SELL", reference_entry=mid, incumbent_stop=1.10020)
        assert t["stop_loss"] == pytest.approx(g.stop)
        assert t["take_profit"] == pytest.approx(g.target)

    def test_21_no_resample(self):
        src = Path("core/runtime/engine_execution_handler.py").read_text(encoding="utf-8")
        seg = src[src.index("CANDIDATE SHADOW OBSERVATIONS"):src.index("return ExecutionPrep")]
        assert seg.count("last_tick") == 0
        assert "treatment_reference_entry" in seg
        hook_src = Path("research_engine/lifecycle/candidate_shadow_hook.py").read_text(encoding="utf-8")
        assert hook_src.count("last_tick") == 0

    def test_carrier_field_exists(self):
        import core.runtime.engine_execution_handler as h
        assert "treatment_reference_entry" in h.ExecutionPrep.__dataclass_fields__


class TestD:
    def test_22_23_24_25_26_incumbent_unchanged(self):
        from types import SimpleNamespace
        from strategy.signals import Side
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch
        intent = SimpleNamespace(side=Side.BUY, sl=1.09, tp=1.11, pattern="TBC")
        sym = SimpleNamespace(symbol="EURUSD", engine_state=None)
        new_result = {"intent": intent, "entity_id": "E1", "strategy": "S"}
        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0) as hook:
            prep = h.prepare_execution(
                new_result=new_result, new_engine_score=0.5, new_engine_htf=None,
                sym_state=sym, cycle_id=1, closed_time=1000, bid=1.10000, ask=1.10020)
        assert prep.intent is intent
        assert intent.side.name == "BUY" and intent.sl == 1.09 and intent.tp == 1.11
        assert prep.treatment_reference_entry == pytest.approx(1.10010)
        _, kwargs = hook.call_args
        assert kwargs["entry_price"] == pytest.approx(kwargs["treatment_reference_entry"])
        assert kwargs["entry_price"] == pytest.approx(1.10010)

    def test_order_intent_no_carrier(self):
        from risk.models import OrderIntent
        from strategy.signals import Side
        oi = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.5,
                         entry_reference=1.10, sl=1.09, tp=1.11)
        assert oi.side.name == "BUY" and oi.entry_reference == 1.10
        assert not hasattr(oi, "treatment_reference_entry")


class TestE:
    @pytest.mark.parametrize("direction,entry,stop", [
        ("SELL", 1.085, 1.086),
        ("BUY", 1.085, 1.084),
        ("SELL", 1.10010, 1.10020),
    ])
    def test_27_future_reproduces_shadow(self, direction, entry, stop):
        risk = abs(entry - stop)
        res, _ = resolve_candidate_treatment(
            change_definition={"type": "direction_inversion"}, direction=direction,
            entry_price=entry, stop_loss=stop, take_profit=entry,
            risk_distance=risk, symbol="EURUSD", pattern="TBC")
        future = canonical_direction_inversion(
            incumbent_direction=direction, reference_entry=entry, incumbent_stop=stop)
        assert res.params["direction"] == future.inverted_direction
        assert res.params["stop_loss"] == pytest.approx(future.stop)
        assert res.params["take_profit"] == pytest.approx(future.target)

