"""Wave 5.3A: immutable treatment declaration/scope, never a live policy."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from research_engine.lifecycle import candidate_shadow_hook as hook


def resolve(**extra):
    return hook.resolve_candidate_treatment(
        change_definition={"type": "direction_inversion", **extra},
        direction="BUY", entry_price=1.0, stop_loss=0.99,
        take_profit=1.03, risk_distance=0.01, symbol="EURUSD", pattern="TBC",
    )[0]


@pytest.mark.parametrize("definition,symbols,patterns", [
    ({}, None, None),
    ({"scope": {"symbols": ["EURUSD"]}}, ["EURUSD"], None),
    ({"scope": {"patterns": ["TBC"]}}, None, ["TBC"]),
    ({"scope": {"symbols": ["EURUSD"], "patterns": ["TBC"]}}, ["EURUSD"], ["TBC"]),
    ({"patterns": ["TBC"]}, None, ["TBC"]),
    ({"symbol": "GBPUSD"}, None, None),
    ({"scope": {"patterns": []}, "patterns": ["TBC"]}, None, ["TBC"]),
    ({"scope": {"patterns": ["TBC"]}, "patterns": ["OTHER"]}, None, ["TBC"]),
])
def test_resolution_freezes_exact_spec(definition, symbols, patterns):
    resolution = resolve(**definition)
    spec = json.loads(resolution.treatment_spec)
    assert spec == {
        "change_type": "direction_inversion", "declared": {},
        "scope": {"symbols": symbols, "patterns": patterns},
        "treatment_id": resolution.treatment_id,
    }
    # JSON text is immutable, detached from mutable input containers.
    definition.clear()
    assert json.loads(resolution.treatment_spec) == spec


def test_same_historical_id_different_scope():
    eur = resolve(scope={"symbols": ["EURUSD"]})
    gbp = resolve(scope={"symbols": ["GBPUSD"]})
    assert eur.treatment_id == gbp.treatment_id
    assert eur.treatment_spec != gbp.treatment_spec
    applied = {k: v for k, v in eur.params.items() if k != "treatment_id"}
    assert eur.treatment_id == hook._canonical_treatment_id("direction_inversion", {}, applied)


def test_hook_passes_frozen_spec_with_unchanged_trade_id():
    candidate = SimpleNamespace(candidate_id="C1", change_definition={
        "type": "direction_inversion", "scope": {"symbols": ["EURUSD"]}})
    engine = Mock()
    with patch("research_engine.v10.candidates.candidate_registry.CandidateRegistry") as registry, patch(
        "core.shadow_trades.get_shadow_engine", return_value=engine
    ):
        registry.return_value.list_by_status.return_value = [candidate]
        assert hook.open_candidate_shadows(
            symbol="EURUSD", cycle_id=1, direction="BUY", entry_price=1.0,
            stop_loss=0.99, take_profit=1.03, entry_time=1000.0,
            entry_bar_index=5, correlation_id="COR-1", entity_id="EURUSD_1000",
            pattern="TBC", score=0.6, bid=1.0, ask=1.0001,
        ) == 1
    args = engine.open_trade.call_args.kwargs
    spec = args["treatment_spec"]
    assert json.loads(spec)["scope"] == {"symbols": ["EURUSD"], "patterns": None}
    assert args["trade_id"] == hook.candidate_trade_id(
        "C1", 1, "EURUSD", json.loads(spec)["treatment_id"])
    candidate.change_definition["scope"] = {}
    assert args["treatment_spec"] == spec



def test_real_shadow_serialization():
    from core.shadow_trades import ShadowTradeEngine, _build_shadow_open_record
    resolution = resolve(scope={"symbols": ["EURUSD"]})
    with patch.object(ShadowTradeEngine, "_recover_open_trades"), patch(
        "core.shadow_trades._persist_shadow_open"
    ):
        engine = ShadowTradeEngine()
        trade = engine.open_trade(
            trade_id=hook.candidate_trade_id("C1", 1, "EURUSD", resolution.treatment_id),
            cycle_id=1, symbol="EURUSD", direction="SELL", entry_price=1.0,
            stop_loss=1.01, take_profit=0.97, entry_time=1000,
            treatment_spec=resolution.treatment_spec,
        )
    for row in (_build_shadow_open_record(trade), engine._build_truth_record(trade)):
        assert json.loads(json.dumps(row))["identity"]["treatment_spec"] == resolution.treatment_spec

