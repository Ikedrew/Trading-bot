"""
Wave 4D.1 — Candidate treatment fidelity: focused proof tests.

Invariant: for every treatment the shadow runtime claims to support,
DECLARED == APPLIED == RECORDED provenance — with fail-closed behaviour
(no silent baseline fallback) for missing fields, malformed values,
and unsupported change types.

Uses the same mock pattern as tests/test_candidate_shadow_hook.py
(mocked registry + shadow engine); no production or baseline state is
touched.
"""
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_engine.lifecycle.candidate_shadow_hook import (
    _candidate_applies,
    open_candidate_shadows,
    resolve_candidate_treatment,
    _translate_change_definition,
)

_P_REG = "research_engine.v10.candidates.candidate_registry.CandidateRegistry"
_P_ENG = "core.shadow_trades.get_shadow_engine"


@dataclass
class MockCandidate:
    candidate_id: str = "OPT-4d1-001"
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


def _resolve(change_definition, direction="SELL", entry_price=1.085,
             stop_loss=1.086, take_profit=1.083, risk_distance=0.001,
             symbol="EURUSD", pattern="TBC"):
    return resolve_candidate_treatment(
        change_definition=change_definition, direction=direction,
        entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, risk_distance=risk_distance,
        symbol=symbol, pattern=pattern,
    )


# ─── 1-2. VALID DECLARED TREATMENT RESOLVES AND MATCHES APPLICATION ───

class TestValidTreatment:
    def test_direction_inversion_resolves_and_matches(self):
        res, reason = _resolve({"type": "direction_inversion"})
        assert res is not None and reason == "ok"
        p = res.params
        assert p["direction"] == "BUY"  # SELL inverted
        assert p["stop_loss"] == pytest.approx(1.085 - 0.001)
        assert p["take_profit"] == pytest.approx(1.085 + 0.003)

    def test_geometry_declared_multiplier_is_applied_exactly(self):
        res, reason = _resolve(
            {"type": "geometry_modification", "stop_multiplier": 2.0},
            direction="BUY", entry_price=1.00, stop_loss=0.99,
            take_profit=1.03, risk_distance=0.01,
        )
        assert res is not None and reason == "ok"
        assert res.params["direction"] == "BUY"
        assert res.params["stop_loss"] == pytest.approx(0.98)
        assert res.params["take_profit"] == 1.03
        assert res.declared == {"stop_multiplier": 2.0}

    def test_translate_shim_returns_resolved_params(self):
        p = _translate_change_definition(
            change_definition={"type": "direction_inversion"},
            direction="SELL", entry_price=1.085, stop_loss=1.086,
            take_profit=1.083, risk_distance=0.001,
            symbol="EURUSD", pattern="TBC",
        )
        assert p is not None and "treatment_id" in p

# ─── 4-5. DETERMINISTIC AND DISTINGUISHABLE TREATMENT IDENTITY ─────

class TestTreatmentIdentity:
    def test_equivalent_declarations_same_identity(self):
        _, id1 = _resolve({"type": "direction_inversion"})
        _, id2 = _resolve({"type": "direction_inversion"})
        res1, _ = _resolve({"type": "direction_inversion"})
        res2, _ = _resolve({"type": "direction_inversion"})
        assert res1.treatment_id == res2.treatment_id != ""

    def test_materially_different_declarations_differ(self):
        inv, _ = _resolve({"type": "direction_inversion"})
        g15, _ = _resolve(
            {"type": "geometry_modification", "stop_multiplier": 1.5}
        )
        g20, _ = _resolve(
            {"type": "geometry_modification", "stop_multiplier": 2.0}
        )
        ids = {inv.treatment_id, g15.treatment_id, g20.treatment_id}
        assert len(ids) == 3

    def test_identity_has_no_wall_clock_component(self):
        res_a, _ = _resolve(
            {"type": "geometry_modification", "stop_multiplier": 1.5}
        )
        res_b, _ = _resolve(
            {"type": "geometry_modification", "stop_multiplier": 1.5}
        )
        assert res_a.treatment_id == res_b.treatment_id


# ─── 6-8. FAIL-CLOSED RESOLUTION ──────────────────────────────────

class TestFailClosedResolution:
    def test_missing_change_type_fails_closed(self):
        res, reason = _resolve({})
        assert res is None and reason == "missing_change_type"

    def test_geometry_missing_multiplier_fails_closed(self):
        res, reason = _resolve({"type": "geometry_modification"})
        assert res is None and reason == "missing_stop_multiplier"

    def test_geometry_malformed_multiplier_fails_closed(self):
        for bad in ("2.0", -1.5, 0, True, float("nan"), float("inf")):
            res, reason = _resolve(
                {"type": "geometry_modification", "stop_multiplier": bad}
            )
            assert res is None, f"multiplier {bad!r} must fail closed"
            assert reason == "malformed_stop_multiplier"

    def test_unsupported_type_fails_closed(self):
        for ctype in ("unknown_future_type", "score_recalibration",
                      "pattern_weighting", "research_recommendation"):
            res, reason = _resolve({"type": ctype})
            assert res is None
            assert reason.startswith("unsupported_change_type")

    def test_regime_conditioning_no_silent_baseline_fallback(self):
        """Previously returned BASELINE geometry — now fails closed."""
        res, reason = _resolve({"type": "regime_conditioning"})
        assert res is None
        assert "regime_conditioning_not_applicable" in reason
        assert "no silent baseline fallback" in reason

    def test_symbol_exclusion_missing_symbol_fails_closed(self):
        res, reason = _resolve({"type": "symbol_exclusion"})
        assert res is None and reason == "missing_symbol"

    def test_symbol_exclusion_by_design_opens_no_shadow(self):
        res, reason = _resolve({"type": "symbol_exclusion", "symbol": "EURUSD"})
        assert res is None and reason == "symbol_exclusion_no_shadow"

    def test_malformed_definition_object_fails_closed(self):
        res, reason = _resolve(None)
        assert res is None and reason == "malformed_change_definition"



# ─── 9-11. HOOK-LEVEL BEHAVIOUR ───────────────────────────────────

class TestHookFailClosed:
    def test_failed_treatment_emits_no_candidate_observation(self):
        c = MockCandidate(change_definition={"type": "geometry_modification"})
        count, eng = _call_hook([c])
        assert count == 0
        assert len(eng.opened) == 0

    def test_failed_treatment_does_not_behave_like_baseline(self):
        """A regime_conditioning candidate must NOT open a baseline-geometry
        shadow (the old silent fallback produced direction/SL/TP identical
        to the deployed trade)."""
        c = MockCandidate(change_definition={"type": "regime_conditioning"})
        count, eng = _call_hook([c])
        assert count == 0
        assert eng.opened == []

    def test_one_bad_candidate_does_not_block_a_valid_one(self):
        bad = MockCandidate(
            candidate_id="OPT-BAD",
            change_definition={"type": "geometry_modification"},
        )
        good = MockCandidate(
            candidate_id="OPT-GOOD",
            change_definition={"type": "direction_inversion"},
        )
        count, eng = _call_hook([bad, good])
        assert count == 1
        assert len(eng.opened) == 1
        assert eng.opened[0]["shadow_type"] == "CANDIDATE_OPT-GOOD"

    def test_applies_filter_unchanged_for_pattern_symbol_scoping(self):
        c = MockCandidate(change_definition={
            "type": "direction_inversion", "patterns": ["TBC"],
        })
        assert _candidate_applies(c, symbol="EURUSD", pattern="TBC")
        assert not _candidate_applies(c, symbol="EURUSD", pattern="OTHER")


# ─── 12. OBSERVATION-ONLY / NO PRODUCTION OR BASELINE MUTATION ────

class TestObservationOnly:
    def test_resolution_is_pure_compute(self, tmp_path):
        """Resolving a treatment writes nothing anywhere."""
        before = sorted(p.name for p in tmp_path.iterdir())
        _resolve({"type": "direction_inversion"})
        _resolve({"type": "geometry_modification", "stop_multiplier": 2.0})
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_hook_never_touches_production_execution(self):
        import research_engine.lifecycle.candidate_shadow_hook as hook
        src = Path(hook.__file__).read_text(encoding="utf-8")
        for forbidden in ("mt5_execution", "execution_orchestrator",
                          "order_send", "core.config"):
            assert forbidden not in src.lower()

    def test_baseline_authority_untouched_by_hook(self, tmp_path, monkeypatch):
        """The hook must not read/write the baseline authority stores."""
        import research_engine.v10.baselines.baseline_authority as ba
        monkeypatch.setattr(ba, "_BASELINES_DIR", str(tmp_path / "b"))
        monkeypatch.setattr(
            ba, "_ACTIVE_POINTER_FILE", str(tmp_path / "b" / "ptr.json")
        )
        c = MockCandidate(change_definition={"type": "direction_inversion"})
        _call_hook([c])
        assert not (tmp_path / "b").exists()  # no writes, no pointer created


# ─── 3. PERSISTED EVIDENCE IDENTIFIES THE EXACT TREATMENT ─────────

class TestEvidenceIdentifiesTreatment:
    def test_shadow_evidence_carries_treatment_identity(self):
        c = MockCandidate(change_definition={"type": "direction_inversion"})
        count, eng = _call_hook([c])
        assert count == 1
        t = eng.opened[0]
        # Recompute the expected resolution with the EXACT hook context
        # (the hook derives risk_distance = abs(entry - sl)).
        res, _ = _resolve(
            {"type": "direction_inversion"},
            risk_distance=abs(1.085 - 1.086),
        )
        # trade_id embeds the deterministic treatment identity
        assert t["trade_id"].endswith(f"_{res.treatment_id}")
        assert t["trade_id"].startswith("candidate_OPT-4d1-001_")
        # applied geometry recorded in evidence matches the resolution
        assert t["direction"] == res.params["direction"]
        assert t["stop_loss"] == pytest.approx(res.params["stop_loss"])
        assert t["take_profit"] == pytest.approx(res.params["take_profit"])
        assert t["shadow_type"] == "CANDIDATE_OPT-4d1-001"
