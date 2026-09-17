"""
Wave 4D.3 — Candidate scope fidelity.

Proves that a candidate treatment runs on EXACTLY the experimental population it
declares: treatment parameters (e.g. a "symbol" field) are NOT silently
reinterpreted as experiment-scope filters, explicit scope restricts correctly,
malformed explicit scope fails closed, and symbol_exclusion treatment semantics
are preserved (and still fabricate no candidate shadow).

Uses the same mock pattern as tests/test_wave4d1_treatment_fidelity.py
(mocked registry + shadow engine); no production or baseline state is touched.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_engine.lifecycle.candidate_shadow_hook import (
    _candidate_applies,
    candidate_in_scope,
    candidate_trade_id,
    extract_treatment_id,
    open_candidate_shadows,
    resolve_candidate_treatment,
)

_P_REG = "research_engine.v10.candidates.candidate_registry.CandidateRegistry"
_P_ENG = "core.shadow_trades.get_shadow_engine"


@dataclass
class MockCandidate:
    candidate_id: str = "OPT-4d3-001"
    status: str = "SHADOW_TESTING"
    change_definition: dict = field(
        default_factory=lambda: {"type": "direction_inversion"}
    )


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


# ─── 1. direction_inversion is NOT restricted by a stray treatment "symbol" ──

class TestTreatmentParamIsNotScope:
    def test_direction_inversion_symbol_param_does_not_restrict(self):
        # A "symbol" field on a NON-exclusion treatment is a treatment parameter,
        # never experiment scope. The candidate remains eligible on any symbol.
        c = MockCandidate(change_definition={
            "type": "direction_inversion", "symbol": "GBPUSD",
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert _candidate_applies(c, symbol="GBPUSD", pattern="TBC")

    # ─── 2. geometry_modification is NOT restricted by treatment parameters ──
    def test_geometry_modification_params_do_not_restrict(self):
        c = MockCandidate(change_definition={
            "type": "geometry_modification", "stop_multiplier": 2.0,
            "symbol": "GBPUSD",  # stray treatment param — must NOT scope
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert _candidate_applies(c, symbol="AUDUSD", pattern="ANY")

    # ─── 4. treatment params do not become scope params accidentally ────────
    def test_no_treatment_field_becomes_scope(self):
        c = MockCandidate(change_definition={
            "type": "geometry_modification", "stop_multiplier": 1.5,
            "symbol": "USDJPY", "action": "adjust_stop_construction",
        })
        # None of symbol/action/stop_multiplier narrow the experiment.
        for sym in ("EURUSD", "USDJPY", "NZDUSD"):
            in_scope, reason = candidate_in_scope(c, symbol=sym, pattern="TBC")
            assert in_scope, (sym, reason)


# ─── 3. explicit scope restricts correctly ───────────────────────────────────

class TestExplicitScope:
    def test_explicit_symbol_scope_restricts(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion",
            "scope": {"symbols": ["GBPUSD", "AUDUSD"]},
        })
        assert not _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert _candidate_applies(c, symbol="GBPUSD", pattern="TBC")
        assert _candidate_applies(c, symbol="AUDUSD", pattern="TBC")

    def test_explicit_pattern_scope_restricts(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion",
            "scope": {"patterns": ["TBC"]},
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="OTHER")

    def test_explicit_scope_symbol_and_pattern_both_required(self):
        c = MockCandidate(change_definition={
            "type": "geometry_modification", "stop_multiplier": 2.0,
            "scope": {"symbols": ["GBPUSD"], "patterns": ["TWS"]},
        })
        assert _candidate_applies(c, symbol="GBPUSD", pattern="TWS")
        assert not _candidate_applies(c, symbol="GBPUSD", pattern="TBC")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="TWS")

    def test_legacy_top_level_patterns_still_scopes(self):
        # Backward-compatible documented scope field (pre-4D.3 contract).
        c = MockCandidate(change_definition={
            "type": "direction_inversion", "patterns": ["TBC"],
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="OTHER")

    def test_explicit_scope_patterns_overrides_legacy_top_level(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion",
            "patterns": ["TBC"],                 # legacy
            "scope": {"patterns": ["TWS"]},      # explicit takes precedence
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TWS")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="TBC")


# ─── 7. malformed explicit scope fails closed ────────────────────────────────

class TestMalformedScopeFailsClosed:
    @pytest.mark.parametrize("bad_scope", [
        "GBPUSD",                       # not a dict
        {"symbols": "GBPUSD"},          # not a list
        {"symbols": [""]},              # empty entry
        {"symbols": [123]},             # non-string entry
        {"patterns": [None]},           # non-string entry
    ])
    def test_malformed_scope_fails_closed(self, bad_scope):
        c = MockCandidate(change_definition={
            "type": "direction_inversion", "scope": bad_scope,
        })
        in_scope, reason = candidate_in_scope(c, symbol="GBPUSD", pattern="TBC")
        assert not in_scope
        assert reason == "malformed_scope"
        assert not _candidate_applies(c, symbol="GBPUSD", pattern="TBC")


# ─── 5-6. out-of-scope opens no shadow; in-scope reaches the 4D.1 resolver ───

class TestScopeGatesShadowCreation:
    def test_out_of_scope_candidate_opens_no_shadow(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion",
            "scope": {"symbols": ["GBPUSD"]},
        })
        # Opportunity is EURUSD → out of scope → no candidate shadow.
        count, eng = _call_hook([c], symbol="EURUSD", pattern="TBC")
        assert count == 0
        assert eng.opened == []

    def test_in_scope_candidate_reaches_4d1_resolver_and_opens_shadow(self):
        c = MockCandidate(candidate_id="OPT-INSCOPE", change_definition={
            "type": "direction_inversion",
            "scope": {"symbols": ["EURUSD"]},
        })
        count, eng = _call_hook([c], symbol="EURUSD", pattern="TBC")
        assert count == 1
        opened = eng.opened[0]
        assert opened["shadow_type"] == "CANDIDATE_OPT-INSCOPE"
        # Reached the 4D.1 resolver: the trade_id carries a real treatment_id.
        tid = extract_treatment_id(opened["trade_id"])
        assert tid is not None and len(tid) == 16

    def test_malformed_scope_candidate_opens_no_shadow(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion", "scope": {"symbols": "EURUSD"},
        })
        count, eng = _call_hook([c], symbol="EURUSD", pattern="TBC")
        assert count == 0
        assert eng.opened == []


# ─── 8. one out-of-scope/malformed candidate does not block a valid one ──────

class TestContainment:
    def test_out_of_scope_candidate_does_not_block_valid_candidate(self):
        out = MockCandidate(candidate_id="OPT-OUT", change_definition={
            "type": "direction_inversion", "scope": {"symbols": ["GBPUSD"]},
        })
        good = MockCandidate(candidate_id="OPT-GOOD", change_definition={
            "type": "direction_inversion", "scope": {"symbols": ["EURUSD"]},
        })
        count, eng = _call_hook([out, good], symbol="EURUSD", pattern="TBC")
        assert count == 1
        assert len(eng.opened) == 1
        assert eng.opened[0]["shadow_type"] == "CANDIDATE_OPT-GOOD"

    def test_malformed_scope_candidate_does_not_block_valid_candidate(self):
        bad = MockCandidate(candidate_id="OPT-BAD", change_definition={
            "type": "direction_inversion", "scope": {"symbols": [123]},
        })
        good = MockCandidate(candidate_id="OPT-GOOD", change_definition={
            "type": "direction_inversion",
        })
        count, eng = _call_hook([bad, good], symbol="EURUSD", pattern="TBC")
        assert count == 1
        assert eng.opened[0]["shadow_type"] == "CANDIDATE_OPT-GOOD"


# ─── 9-10. symbol_exclusion treatment semantics preserved; no fabrication ────

class TestSymbolExclusion:
    def test_symbol_exclusion_is_treatment_not_generic_scope(self):
        # "symbol" here is WHAT is excluded, a treatment parameter. It is
        # eligible only on the excluded symbol (truthful), but NOT converted
        # into a generic symbol scope for other treatment types.
        c = MockCandidate(change_definition={
            "type": "symbol_exclusion", "symbol": "EURUSD",
        })
        in_scope, _ = candidate_in_scope(c, symbol="EURUSD", pattern="TBC")
        assert in_scope
        in_scope2, reason2 = candidate_in_scope(c, symbol="GBPUSD", pattern="TBC")
        assert not in_scope2
        assert reason2 == "symbol_exclusion_wrong_symbol"

    def test_symbol_exclusion_opens_no_shadow_on_excluded_symbol(self):
        c = MockCandidate(change_definition={
            "type": "symbol_exclusion", "symbol": "EURUSD",
        })
        # Even on the excluded symbol (in-scope), the 4D.1 resolver returns
        # symbol_exclusion_no_shadow → NO candidate observation is fabricated.
        count, eng = _call_hook([c], symbol="EURUSD", pattern="TBC")
        assert count == 0
        assert eng.opened == []

    def test_symbol_exclusion_resolver_returns_no_shadow(self):
        res, reason = resolve_candidate_treatment(
            change_definition={"type": "symbol_exclusion", "symbol": "EURUSD"},
            direction="SELL", entry_price=1.085, stop_loss=1.086,
            take_profit=1.083, risk_distance=0.001, symbol="EURUSD", pattern="TBC",
        )
        assert res is None
        assert reason == "symbol_exclusion_no_shadow"


# ─── 11-14. 4D.1/4D.2 identity + correlation/candidate id semantics unchanged ─

class TestIdentityUnchanged:
    def test_treatment_id_generation_unchanged(self):
        # 4D.1: deterministic treatment_id, same declaration → same identity.
        res_a, _ = resolve_candidate_treatment(
            change_definition={"type": "geometry_modification", "stop_multiplier": 2.0},
            direction="BUY", entry_price=1.0, stop_loss=0.99, take_profit=1.03,
            risk_distance=0.01, symbol="EURUSD", pattern="X",
        )
        res_b, _ = resolve_candidate_treatment(
            change_definition={"type": "geometry_modification", "stop_multiplier": 2.0},
            direction="BUY", entry_price=1.0, stop_loss=0.99, take_profit=1.03,
            risk_distance=0.01, symbol="EURUSD", pattern="X",
        )
        assert res_a.treatment_id == res_b.treatment_id
        assert len(res_a.treatment_id) == 16

    def test_treatment_id_continuity_mint_and_parse_unchanged(self):
        # 4D.2: mint → parse round-trip is unchanged by 4D.3.
        tid = "abcdef0123456789"
        trade_id = candidate_trade_id("OPT-X", 7, "EURUSD", tid)
        assert extract_treatment_id(trade_id) == tid

    def test_correlation_and_candidate_id_semantics_unchanged(self):
        c = MockCandidate(candidate_id="OPT-IDS", change_definition={
            "type": "direction_inversion", "scope": {"symbols": ["EURUSD"]},
        })
        count, eng = _call_hook([c], symbol="EURUSD", pattern="TBC",
                                correlation_id="COR-SPECIFIC",
                                entity_id="EURUSD_1000")
        assert count == 1
        opened = eng.opened[0]
        assert opened["correlation_id"] == "COR-SPECIFIC"      # unchanged
        assert opened["entity_id"] == "EURUSD_1000"            # unchanged
        assert opened["shadow_type"] == "CANDIDATE_OPT-IDS"    # candidate_id unchanged
        assert opened["trade_id"].startswith("candidate_OPT-IDS_")
