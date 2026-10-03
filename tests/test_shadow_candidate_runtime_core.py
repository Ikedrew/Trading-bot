"""Block 1: generic shadow-candidate runtime core tests (fake candidate)."""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow.candidate_persistence import (
    CandidateEventWriter,
    load_candidate_events,
)
from core.shadow.candidate_runtime import CandidateRegistration, CandidateRuntime
from tests.helpers.fake_candidate_adapter import FakeBarCountAdapter


def _rt(tmp=None, **kw):
    d = tmp or tempfile.mkdtemp(prefix="cand_core_")
    adapter = FakeBarCountAdapter(**kw)
    rt = CandidateRuntime(writer=CandidateEventWriter(base_dir=d))
    rt.register(CandidateRegistration(
        candidate_id=FakeBarCountAdapter.CANDIDATE_ID,
        policy_id=FakeBarCountAdapter.POLICY_ID,
        treatment_hash=FakeBarCountAdapter.TREATMENT_HASH,
        adapter=adapter))
    return rt, d, adapter


def _bind(rt, **kw):
    p = {"shadow_trade_id": "st1", "canonical_opportunity_id": "co1",
         "trade_horizon": "SCALP", "symbol": "EURUSD", "direction": "BUY",
         "entry_time": 1700000000, "entry_price": 1.1000,
         "stop_loss": 1.0990, "take_profit": 1.1020,
         "candidate_id": FakeBarCountAdapter.CANDIDATE_ID,
         "policy_id": FakeBarCountAdapter.POLICY_ID}
    p.update(kw)
    return rt.bind_shadow_lifecycle(**p)


def _bar(t, close=1.1005):
    return {"symbol": "EURUSD", "bar_time": t, "bar_high": close + 0.0005,
            "bar_low": close - 0.0005, "bar_close": close}


class TestRegistration:
    def test_registration_creates_one_lifecycle(self):
        rt, _, _ = _rt()
        rid = _bind(rt)
        assert len(rt.active_ids()) == 1
        assert rt.active_ids()[0] == rid

    def test_duplicate_init_stays_one(self):
        rt, _, _ = _rt()
        a = _bind(rt)
        b = _bind(rt)
        assert a == b
        assert len(rt.active_ids()) == 1

    def test_two_accounts_share_canonical_yields_one(self):
        rt, _, _ = _rt()
        a = _bind(rt, shadow_trade_id="stX",
                  canonical_opportunity_id="coShared")
        b = _bind(rt, shadow_trade_id="stX",
                  canonical_opportunity_id="coShared")
        assert a == b
        assert len(rt.active_ids()) == 1


class TestBars:
    def test_bar_reaches_runtime_once(self):
        rt, _, _ = _rt()
        _bind(rt)
        out = rt.evaluate_bar(**_bar(1700000300))
        assert out[0]["status"] == "PROGRESSED"
        snap = rt.snapshot(rt.active_ids()[0])
        assert snap["bars_elapsed"] == 1
        assert snap["last_evaluated_bar_time"] == 1700000300

    def test_duplicate_bar_ignored(self):
        rt, _, _ = _rt()
        _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        out = rt.evaluate_bar(**_bar(1700000300))
        assert out[0]["status"] == "DUPLICATE_IGNORED"
        assert rt.snapshot(rt.active_ids()[0])["bars_elapsed"] == 1

    def test_out_of_order_rejected(self):
        rt, _, _ = _rt()
        _bind(rt)
        rt.evaluate_bar(**_bar(1700000600))
        out = rt.evaluate_bar(**_bar(1700000300))
        assert out[0]["status"] == "DUPLICATE_IGNORED"
        assert rt.snapshot(rt.active_ids()[0])["bars_elapsed"] == 1

    def test_treatment_state_updates(self):
        rt, _, _ = _rt()
        _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        snap = rt.snapshot(rt.active_ids()[0])
        assert snap["treatment_state"]["bars_seen"] == 1

    def test_terminal_close_exactly_once(self):
        rt, _, _ = _rt(terminal_after_bars=2)
        rid = _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        out = rt.evaluate_bar(**_bar(1700000600))
        assert out[0]["status"] == "CLOSED"
        assert rt.close_count(rid) == 1
        assert rt.active_ids() == []
        assert rt.evaluate_bar(**_bar(1700000900)) == []

class _FailingWriter:
    base_dir = tempfile.mkdtemp(prefix="cand_fail_")

    def append(self, **kw):
        return False


class TestPersistenceIsolation:
    def test_candidate_stream_independent(self):
        rt, d, _ = _rt()
        rid = _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        evs = load_candidate_events(d)
        assert {"CANDIDATE_OPEN", "CANDIDATE_PROGRESS"} <= {e["event_type"] for e in evs}
        assert all(e["schema_version"] == "shadow_candidate_v1" for e in evs)
        assert all(e["candidate_runtime_id"] == rid for e in evs)
        import core.shadow.persistence as p
        assert "candidate" not in p._DEFAULT_BASE_DIR

    def test_persist_failure_preserves_baseline(self):
        rt, _, _ = _rt()
        rt._writer = _FailingWriter()
        rid = _bind(rt)
        assert rid is not None
        assert rt.active_ids() == []
        assert rid in rt.quarantined_ids()


class TestRecovery:
    def _rt2(self, d):
        rt2 = CandidateRuntime(writer=CandidateEventWriter(base_dir=d))
        rt2.register(CandidateRegistration(
            candidate_id=FakeBarCountAdapter.CANDIDATE_ID,
            policy_id=FakeBarCountAdapter.POLICY_ID,
            treatment_hash=FakeBarCountAdapter.TREATMENT_HASH,
            adapter=FakeBarCountAdapter()))
        return rt2

    def test_restart_restores_state_and_watermark(self):
        rt, d, _ = _rt()
        rid = _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        rt.evaluate_bar(**_bar(1700000600))
        rt2 = self._rt2(d)
        rep = rt2.recover()
        assert rep["active"] == 1
        snap = rt2.snapshot(rid)
        assert snap["bars_elapsed"] == 2
        assert snap["last_evaluated_bar_time"] == 1700000600
        assert snap["treatment_state"]["bars_seen"] == 2
        out = rt2.evaluate_bar(**_bar(1700000600))
        assert out[0]["status"] == "DUPLICATE_IGNORED"

    def test_restart_after_terminal_no_duplicate(self):
        rt, d, _ = _rt(terminal_after_bars=1)
        _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        rt2 = self._rt2(d)
        rep = rt2.recover()
        assert rep["closed"] == 1
        assert rt2.active_ids() == []
        assert rt2.evaluate_bar(**_bar(1700000600)) == []

    def test_hash_mismatch_quarantined(self):
        rt, d, _ = _rt()
        _bind(rt)
        rt.evaluate_bar(**_bar(1700000300))
        rt2 = CandidateRuntime(writer=CandidateEventWriter(base_dir=d))
        rt2.register(CandidateRegistration(
            candidate_id=FakeBarCountAdapter.CANDIDATE_ID,
            policy_id=FakeBarCountAdapter.POLICY_ID,
            treatment_hash="DIFFERENT_HASH",
            adapter=FakeBarCountAdapter()))
        rep = rt2.recover()
        assert rep["active"] == 0
        assert len(rt2.quarantined_ids()) == 1


class TestIsolation:
    def test_no_execution_reachability(self):
        for mod in ("core.shadow.candidate_models",
                    "core.shadow.candidate_runtime",
                    "core.shadow.candidate_persistence",
                    "tests.helpers.fake_candidate_adapter"):
            src = Path(ROOT, *mod.split(".")).with_suffix(".py").read_text(
                encoding="utf-8")
            blob = src.lower()
            assert "order_send" not in blob
            assert "position modify" not in blob
            assert "position close" not in blob
            assert "mt5_execution" not in blob
            assert "execution_orchestrator" not in blob



class TestAuthoritativeBarHook:
    def test_same_bar_reaches_both_runtimes(self):
        import core.shadow.integration as integ
        calls = {}

        class FakeShadow:
            def evaluate_bar(self, **kw):
                calls["shadow"] = dict(kw)

        class FakeCandidate:
            def evaluate_bar(self, **kw):
                calls["candidate"] = dict(kw)
                return []

        import core.shadow.runtime as sr
        import core.shadow.candidate_runtime as cr
        real_sr, real_cr = sr.get_shadow_runtime, cr.get_candidate_runtime
        sr.get_shadow_runtime = lambda: FakeShadow()
        cr.get_candidate_runtime = lambda: FakeCandidate()
        try:
            integ.evaluate_closed_bar(
                symbol="EURUSD", bar_time_utc=1700000300,
                bar_high=1.1, bar_low=1.099, bar_close=1.0995,
                bar_index=7, bar_open=1.0992)
        finally:
            sr.get_shadow_runtime = real_sr
            cr.get_candidate_runtime = real_cr
            cr.reset_candidate_runtime()
        assert calls["shadow"]["bar_time"] == 1700000300
        assert calls["candidate"]["bar_time"] == 1700000300
        assert calls["candidate"]["bar_high"] == calls["shadow"]["bar_high"]

    def test_candidate_failure_cannot_break_baseline(self):
        import core.shadow.integration as integ
        calls = {}

        class FakeShadow:
            def evaluate_bar(self, **kw):
                calls["shadow"] = True

        class Boom:
            def evaluate_bar(self, **kw):
                raise RuntimeError("candidate boom")

        import core.shadow.runtime as sr
        import core.shadow.candidate_runtime as cr
        real_sr, real_cr = sr.get_shadow_runtime, cr.get_candidate_runtime
        sr.get_shadow_runtime = lambda: FakeShadow()
        cr.get_candidate_runtime = lambda: Boom()
        try:
            integ.evaluate_closed_bar(
                symbol="EURUSD", bar_time_utc=1700000300,
                bar_high=1.1, bar_low=1.099, bar_close=1.0995)
        finally:
            sr.get_shadow_runtime = real_sr
            cr.get_candidate_runtime = real_cr
            cr.reset_candidate_runtime()
        assert calls.get("shadow") is True
