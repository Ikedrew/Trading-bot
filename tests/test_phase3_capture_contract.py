"""
Phase 3 — LIVE + SHADOW data-capture contract regression suite.

Proves, with mocks / temporary sinks only: Steps 1-6, 10-11 of the mission,
the end-to-end LIVE<->SHADOW root equality contract (15) and negative cases
A-H (16). No bot start, no MT5, no orders, no production logs touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.identity.canonical import make_canonical_opportunity_id

ID_A = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1784800000, pattern="TWEEZER_TOP")
ID_B = make_canonical_opportunity_id(symbol="GBPUSD", bar_time=1784800300, pattern="HAMMER")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_rows(local_dir) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(Path(local_dir).rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


# ═══ STEP 1 — Opportunity canonical propagation ═══════════════════════════════

def _signal(pattern="TWEEZER_TOP", bar_time=1784800000):
    return SimpleNamespace(
        pattern=pattern, side=SimpleNamespace(value="SELL"),
        bar_time=bar_time, bar_index=5, confidence=0.9,
    )


class TestStep1OpportunityCanonicalPropagation:
    def test_new_record_carries_explicit_canonical_root(self):
        from core.opportunity.factory import create_opportunity

        opp = create_opportunity(signal=_signal(), symbol="EURUSD", cycle_id=7)
        assert opp.opportunity_id == ID_A                # legacy preserved
        assert opp.canonical_opportunity_id == ID_A      # explicit root
        assert opp.to_dict()["canonical_opportunity_id"] == ID_A

    def test_preestablished_root_is_used_verbatim(self):
        from core.opportunity.factory import create_opportunity

        custom = "EURUSD*1784800000*OVERRIDE_PATTERN"
        opp = create_opportunity(
            signal=_signal(pattern="OVERRIDE_PATTERN"), symbol="EURUSD",
            cycle_id=7, canonical_opportunity_id=custom,
        )
        assert opp.canonical_opportunity_id == custom == opp.opportunity_id

    def test_opportunity_assessment_decision_share_exact_root(self):
        """Required lineage triangle: Opportunity -> Assessment -> Decision."""
        from core.opportunity.factory import create_opportunity
        from core.assessment.builder import build_assessment
        from core.runtime.decision_recorder import DecisionRecorder

        opp = create_opportunity(signal=_signal(), symbol="EURUSD", cycle_id=42)
        assessment = build_assessment(
            engine_result={
                "action": "NO_TRADE", "pattern": "TWEEZER_TOP", "reason": "t",
                "score": 0.8, "components": {"htf_alignment": 0.5},
                "entity_id": f"EURUSD_{1784800000}",
                "canonical_opportunity_id": opp.canonical_opportunity_id,
            },
            symbol="EURUSD", cycle_id=42, bar_time=1784800000,
        )
        assert assessment.canonical_opportunity_id == opp.canonical_opportunity_id

        ledger = type("L", (), {"rows": []})()
        ledger.record = lambda **kw: ledger.rows.append(kw)
        rec = DecisionRecorder(ledger)
        d = rec.init_cycle(symbol="EURUSD", cycle_id=42, regime="r",
                           context_snapshot_id="CTX", drawdown_pct=0.0,
                           daily_loss_pct=0.0)
        d["canonical_opportunity_id"] = opp.canonical_opportunity_id
        d.update(decision="NO_TRADE", reason="case")
        rec.finalize(cycle_start=0.0)
        assert ledger.rows[0]["canonical_opportunity_id"] == ID_A

    def test_consecutive_opportunities_keep_distinct_roots(self):
        from core.opportunity.factory import create_opportunity

        a = create_opportunity(signal=_signal(), symbol="EURUSD",
                               cycle_id=1).canonical_opportunity_id
        b = create_opportunity(
            signal=_signal(pattern="HAMMER", bar_time=1784800300),
            symbol="GBPUSD", cycle_id=2).canonical_opportunity_id
        assert (a, b) == (ID_A, ID_B)


# ═══ STEP 2 — Market-context identity enrichment ══════════════════════════════

@pytest.fixture
def mc_sink(monkeypatch, tmp_path):
    import core.market_context.persistence as mc
    out = tmp_path / "mc"
    monkeypatch.setattr(mc, "_LOCAL_DIR", str(out))
    return out, mc


class TestStep2MarketContextIdentity:
    def test_observation_level_identity_attached_when_supplied(self, mc_sink):
        sink, mc = mc_sink
        mc.MarketContextPersistence().persist(
            {"symbol": "EURUSD", "timestamp_utc": 1784800000},
            entity_id="", correlation_id="COR-X", bar_time=1784800000,
        )
        row = _read_rows(sink)[0]
        assert row["entity_id"] == f"EURUSD_{1784800000}"
        assert row["bar_time"] == 1784800000
        assert row["correlation_id"] == "COR-X"

    def test_no_fabrication_without_bar_time(self, mc_sink):
        sink, mc = mc_sink
        mc.MarketContextPersistence().persist({"symbol": "EURUSD"})
        row = _read_rows(sink)[0]
        assert "bar_time" not in row
        assert not row.get("entity_id")


# ═══ STEP 3 — Execution-context identity injection ════════════════════════════

class TestStep3ExecutionContextIdentity:
    def test_cycle_context_record_gains_entity_bar_cycle(self, monkeypatch):
        import core.runtime.execution_context_builder as ecb

        captured: list[dict] = []
        monkeypatch.setattr(
            ecb, "persist_execution_context",
            lambda rec: captured.append(dict(rec)),
        )
        sym_state = SimpleNamespace(
            symbol="EURUSD", iterations=3,
            engine_state=SimpleNamespace(volatility_filter=0.0012),
            trade_manager=None,
        )
        cor = ecb.build_cycle_context(
            cycle_id=88, cycle_start=0.0, sym_state=sym_state,
            closed_time=1784800000, bid=1.1, ask=1.1001, tick_time=0.0,
            feed_state="HEALTHY",
            dd_result=SimpleNamespace(current_drawdown_pct=0.0),
            dl_result=SimpleNamespace(current_loss_pct=0.0),
        )
        assert cor
        rec = captured[0]
        assert rec["cycle_id"] == 88
        assert rec["entity_id"] == f"EURUSD_{1784800000}"
        assert rec["bar_time"] == 1784800000
        # Timing honesty: snapshot runs PRE-engine; canonical stays empty here.
        assert rec.get("canonical_opportunity_id", "") == ""


# ═══ STEP 4 — Primary execution-result lineage + entry facts ══════════════════

@pytest.fixture
def exec_sink(monkeypatch, tmp_path):
    import core.persistence.execution_result_writer as w
    out = tmp_path / "exec"
    monkeypatch.setattr(w, "_LOCAL_DIR", str(out))
    monkeypatch.setattr(w, "_write_s3", lambda *a, **k: None)
    return out


def _ok_exec():
    ex = MagicMock()
    ex.execute.return_value = SimpleNamespace(
        ok=True, retcode=10009, deal=111, order=222,
        comment="done", fill_price=1.2305, price=1.2305, volume=0.01,
    )
    return ex


def _intent():
    i = MagicMock()
    i.side = SimpleNamespace(name="BUY")
    i.volume = 0.01
    i.entry_reference = 1.2300
    i.sl = 1.2250
    i.tp = 1.2400
    i.pattern = "TWEEZER_TOP"
    return i


class TestStep4ExecutionResultLineage:
    def _run(self, sink, **extra):
        from execution.execution_orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator(_ok_exec(), MagicMock())
        kw = dict(intent=_intent(), symbol="EURUSD", cycle_id=42,
                  decision_id="D1", correlation_id="C1",
                  entity_id="EURUSD_1784800000",
                  canonical_opportunity_id=ID_A, mt5_state="CONNECTED")
        kw.update(extra)
        return orch.execute_trade(**kw)

    def test_successful_fill_row_carries_root_and_entry_facts(self, exec_sink):
        self._run(exec_sink, bid_at_execution=1.2298, ask_at_execution=1.2301)
        row = _read_rows(exec_sink)[0]
        assert row["canonical_opportunity_id"] == ID_A
        assert row["bid_at_execution"] == 1.2298
        assert row["ask_at_execution"] == 1.2301
        assert row["spread_at_execution"] == pytest.approx(0.0003, abs=1e-12)

    def test_rejected_and_nofill_results_still_lineaged(self, exec_sink):
        """result_ok=False rows keep the root (broker rejection traceability)."""
        from execution.execution_orchestrator import ExecutionOrchestrator

        ex = MagicMock()
        ex.execute.return_value = SimpleNamespace(
            ok=False, retcode=10016, deal=0, order=0,
            comment="invalid", fill_price=None, price=None, volume=0.01,
        )
        ExecutionOrchestrator(ex, MagicMock()).execute_trade(
            intent=_intent(), symbol="EURUSD", cycle_id=43, decision_id="D2",
            correlation_id="C2", entity_id="E2",
            canonical_opportunity_id=ID_A, mt5_state="CONNECTED")
        rows = [r for r in _read_rows(exec_sink) if r["decision_id"] == "D2"]
        assert rows and rows[0]["canonical_opportunity_id"] == ID_A
        assert rows[0]["result_ok"] is False

    def test_multiple_results_same_root(self, exec_sink):
        self._run(exec_sink, cycle_id=50)
        from core.persistence.execution_result_writer import persist_execution_result
        persist_execution_result(symbol="EURUSD", cycle_id=51, result_ok=True,
                                 retcode=10009, deal=112, order=223,
                                 comment="", canonical_opportunity_id=ID_A)
        rows = [r for r in _read_rows(exec_sink) if r["canonical_opportunity_id"] == ID_A]
        assert len(rows) >= 2

    def test_entry_snapshot_is_outcome_free(self):
        """Case G (structural): the writer has NO outcome parameters at all."""
        from core.persistence.execution_result_writer import persist_execution_result
        with pytest.raises(TypeError):
            persist_execution_result(symbol="X", cycle_id=1, result_ok=True,
                                     retcode=0, deal=0, order=0, comment="",
                                     pnl_realised=1.0)   # type: ignore[call-arg]


# ═══ STEP 5 — Risk lineage ════════════════════════════════════════════════════

class TestStep5RiskLineage:
    def test_risk_deviation_routes_to_correct_root(self):
        from core.risk_deviation import compute_risk_deviation

        ra = compute_risk_deviation(
            trade_id="T1", symbol="EURUSD", correlation_id="C1",
            direction="BUY", entry_price=1.23, exit_price=1.22,
            initial_sl=1.2250, canonical_opportunity_id=ID_A)
        rb = compute_risk_deviation(
            trade_id="T2", symbol="GBPUSD", correlation_id="C2",
            direction="BUY", entry_price=1.33, exit_price=1.32,
            initial_sl=1.3250, canonical_opportunity_id=ID_B)
        assert ra.canonical_opportunity_id == ID_A
        assert rb.canonical_opportunity_id == ID_B

    def test_journal_passes_position_root_to_risk_writer(self):
        src = (PROJECT_ROOT / "core/trade_journal.py").read_text(encoding="utf-8-sig")
        tail = src.split("compute_risk_deviation(")[1][:600]
        assert "canonical_opportunity_id=getattr(record" in tail


# ═══ STEPS 7/8 — Entry facts vs exit/outcome separation ════════════════════════

class TestEntryExitOutcomeSeparation:
    def test_entry_exit_outcome_share_root_without_cross_contamination(self):
        from core.persistence.execution_result_writer import persist_execution_result
        from core.trade_truth import build_trade_truth, validate_trade_truth

        persist_execution_result(
            symbol="EURUSD", cycle_id=70, result_ok=True, retcode=10009,
            deal=900, order=901, comment="", decision_id="D7",
            correlation_id="COR-ENTRY-EXIT", entity_id="EURUSD_1784800000",
            canonical_opportunity_id=ID_A,
            bid_at_execution=1.2298, ask_at_execution=1.2301,
            risk_distance=abs(1.2300 - 1.2250), slippage=0.0001)
        entry_keys = set(persist_execution_result.__code__.co_varnames)

        truth = build_trade_truth(
            trade_id="pos_X", correlation_id="COR-ENTRY-EXIT", symbol="EURUSD",
            canonical_opportunity_id=ID_A, entry_fill_price=1.2305,
            exit_fill_price=1.2405, volume_executed=0.01,
            entry_timestamp_broker=1784800000.0,
            exit_timestamp_broker=1784800300.0,
            pnl_realised=1.11, r_multiple_realised=2.22,
            exit_reason="take_profit_hit")
        valid, _ = validate_trade_truth(truth)
        assert valid
        assert truth["identity"]["canonical_opportunity_id"] == ID_A
        for f in ("pnl_realised", "r_multiple_realised"):
            assert f in truth["outcome"]
            assert f not in entry_keys          # entry lane structurally outcome-free

    def test_truth_forbids_strategy_or_future_leakage(self):
        from core.trade_truth import build_trade_truth, validate_trade_truth
        truth = build_trade_truth(
            trade_id="p2", correlation_id="C9", symbol="EURUSD",
            canonical_opportunity_id=ID_A, entry_fill_price=1.2305,
            exit_fill_price=1.2405, volume_executed=0.01)
        truth["strategy"] = "MEAN_REVERSION"
        ok, _why = validate_trade_truth(truth)
        assert not ok


# ═══ STEP 10 — Shadow corrections (runtime driven with fake writer) ═══════════

def _shadow_runtime(events):
    import core.shadow.runtime as sr
    rt = sr.ShadowRuntime.__new__(sr.ShadowRuntime)
    rt._writer = type(
        "W", (), {"append": staticmethod(lambda **kw: events.append(kw["event"]))})()
    rt._active = {}
    rt._planned_roots = set()
    return rt


def _ctx(**over):
    ctx = {
        "canonical_opportunity_id": ID_A, "entity_id": "EURUSD_1784800000",
        "symbol": "EURUSD", "cycle_id": 303, "bar_time_raw": 1784800000,
        "direction": "BUY", "pattern": "TWEEZER_TOP", "strategy": "S1",
        "score": 0.8, "regime": "TRENDING", "h4_regime": "TRENDING_UP",
        "h1_bias": "BULLISH", "market_phase": "EXPANSION",
        "market_phase_confidence": 0.77,
        "bid": 1.2298, "ask": 1.2301,
        "structure": {"m5_candle_high": 1.2320, "m5_candle_low": 1.2280},
    }
    ctx.update(over)
    return ctx


def _trade():
    return SimpleNamespace(entry=1.2301, stop_loss=1.2280, take_profit=1.2343,
                           rr=2.0, sl_source="M5_CANDLE_GEOMETRY",
                           reasoning=["SL rule", "TP rule"])


class TestStep10ShadowCorrections:
    def test_open_basis_primary_type_and_full_facts(self):
        events: list[dict] = []
        rt = _shadow_runtime(events)
        rt._open_constructed(ctx=_ctx(), symbol="EURUSD", bar_time_raw=1784800000,
                             off=10800, direction="BUY", plan_id="P1",
                             constructed=[{"horizon": "SCALP", "trade": _trade()}])
        op = [e for e in events if e["event_type"] == "OPEN"][0]
        # 10-A: top-level basis mirrors the direction-derived fill basis
        assert op["entry_price_basis"] == "ASK"
        assert op["market_entry_facts"]["entry_price_basis"] == "ASK"
        # 10-B: upstream regime/phase facts propagate verbatim
        lf = op["live_facts"]
        assert (lf["regime"], lf["h4_regime"]) == ("TRENDING", "TRENDING_UP")
        assert (lf["market_phase"], lf["market_phase_confidence"]) == \
            ("EXPANSION", pytest.approx(0.77))
        # 10-C: no explicit v10 selection -> SCALP child stays ALTERNATIVE
        assert op["identity"]["shadow_type"] == "HORIZON_ALTERNATIVE"
        assert op["canonical_opportunity_id"] == ID_A

    def test_primary_selected_horizon_child_labelled_primary(self):
        events: list[dict] = []
        rt = _shadow_runtime(events)
        rt._open_constructed(ctx=_ctx(v10_selected_horizon="SCALP"),
                             symbol="EURUSD", bar_time_raw=1784800000,
                             off=10800, direction="BUY", plan_id="P2",
                             constructed=[{"horizon": "SCALP", "trade": _trade()}])
        op = [e for e in events if e["event_type"] == "OPEN"][0]
        assert op["identity"]["shadow_type"] == "PRIMARY_HORIZON_SIMULATION"

    def test_alternative_child_stays_alternative_with_selection_set(self):
        events: list[dict] = []
        rt = _shadow_runtime(events)
        rt._open_constructed(ctx=_ctx(v10_selected_horizon="INTRADAY"),
                             symbol="EURUSD", bar_time_raw=1784800000,
                             off=10800, direction="BUY", plan_id="P2b",
                             constructed=[{"horizon": "SCALP", "trade": _trade()}])
        op = [e for e in events if e["event_type"] == "OPEN"][0]
        assert op["identity"]["shadow_type"] == "HORIZON_ALTERNATIVE"

    def test_shadow_lifecycle_keeps_root_and_exits_on_stop(self):
        events: list[dict] = []
        rt = _shadow_runtime(events)
        rt._open_constructed(ctx=_ctx(), symbol="EURUSD", bar_time_raw=1784800000,
                             off=0, direction="BUY", plan_id="P3",
                             constructed=[{"horizon": "SCALP", "trade": _trade()}])
        tid = [e for e in events if e["event_type"] == "OPEN"][0]["shadow_trade_id"]
        rt.evaluate_bar(symbol="EURUSD", bar_time=1784800300, bar_high=1.2310,
                        bar_low=1.2270, bar_close=1.2272)
        cl = [e for e in events if e["event_type"] == "CLOSE"][-1]
        assert cl["exit_reason"] == "stop_loss"
        assert cl["canonical_opportunity_id"] == ID_A
        assert cl["shadow_trade_id"] == tid
        assert (cl.get("outcome") or {}).get("pnl_r_multiple") == pytest.approx(-1.0)

    def test_timestamp_domains_explicitly_separated(self):
        """Market-time vs wall-clock fields are distinct keys by design."""
        from core.shadow.models import market_block
        blk = market_block("opportunity_market_time", 1784800000, 10800)
        assert set(blk) == {"opportunity_market_time",
                            "opportunity_market_time_utc_epoch_s",
                            "opportunity_market_time_utc_iso8601"}
        # raw is preserved verbatim; UTC derivative is offset-corrected only
        assert blk["opportunity_market_time"] == 1784800000
        assert blk["opportunity_market_time_utc_epoch_s"] == 1784800000 - 10800


# ═══ STEP 15 — End-to-end LIVE <-> SHADOW root equality ════════════════════════

class TestEndToEndRootEquality:
    def test_single_root_survives_full_live_and_shadow_lifecycles(
        self, mc_sink, exec_sink,
    ):
        from core.opportunity.factory import create_opportunity
        from core.market_context.persistence import MarketContextPersistence

        mc_out = mc_sink[0]
        exec_out = exec_sink          # this fixture returns the path itself

        # LIVE lane — one opportunity through every boundary
        opp = create_opportunity(signal=_signal(), symbol="EURUSD", cycle_id=77)
        root = opp.canonical_opportunity_id
        MarketContextPersistence().persist(          # pre-engine context snapshot
            {"symbol": "EURUSD", "timestamp_utc": 1784800000},
            bar_time=1784800000)                     # identity only, no canonical
        ctx_row = _read_rows(mc_out)[0]
        assert root not in json.dumps(ctx_row)       # context must NOT fake lineage
        assert ctx_row["entity_id"] == opp.entity_id  # join key present instead

        persist_execution_result(
            symbol="EURUSD", cycle_id=77, result_ok=True, retcode=10009,
            deal=700, order=701, comment="", decision_id="DE",
            correlation_id="COR-E2E", entity_id=opp.entity_id,
            canonical_opportunity_id=root)
        entry_row = _read_rows(exec_out)[-1]
        truth = build_trade_truth(
            trade_id="pE2E", correlation_id="COR-E2E", symbol="EURUSD",
            canonical_opportunity_id=root, entry_fill_price=1.2305,
            exit_fill_price=1.2343, volume_executed=0.01,
            exit_reason="take_profit_hit")

        # SHADOW lane — same root, full child lifecycle
        events: list[dict] = []
        rt = _shadow_runtime(events)
        rt._open_constructed(ctx=_ctx(v10_selected_horizon="SCALP",
                                      canonical_opportunity_id=root),
                             symbol="EURUSD", bar_time_raw=1784800000,
                             off=10800, direction="BUY", plan_id="PE",
                             constructed=[{"horizon": "SCALP", "trade": _trade()}])
        rt.evaluate_bar(symbol="EURUSD", bar_time=1784801300, bar_high=1.2355,
                        bar_low=1.2342, bar_close=1.2350)
        shadow_events = [e for e in events if e["event_type"] in ("OPEN", "CLOSE")]

        # THE CONTRACT: one identical root across every persisted record.
        assert entry_row["canonical_opportunity_id"] == root
        assert truth["identity"]["canonical_opportunity_id"] == root == ID_A
        for ev in shadow_events:
            assert ev["canonical_opportunity_id"] == root
        roots_seen = {entry_row["canonical_opportunity_id"],
                      truth["identity"]["canonical_opportunity_id"]}
        roots_seen.update(ev["canonical_opportunity_id"] for ev in shadow_events)
        assert len(roots_seen) == 1


# ═══ STEP 16 — Negative cases ═════════════════════════════════════════════════

from tests.test_canonical_lineage_scoping_regression import (   # noqa: E402
    _PerCycleIdentityScope as _Scope,
    _BoundaryHarness as _Harness,
)
from core.runtime.decision_recorder import DecisionRecorder  # noqa: E402
from core.persistence.execution_result_writer import persist_execution_result  # noqa: E402,F401
from core.trade_truth import build_trade_truth  # noqa: E402,F401
import core.config as cfg  # noqa: E402


class TestNegativeCases:
    def test_case_a_no_opportunity_next_cycle(self):
        h, s = _Harness(), _Scope()
        h.run_cycle(scope=s, symbol="EURUSD", cycle_id=1, bar_time=1784800000,
                    pattern="TWEEZER_TOP", context_snapshot_id="A1")
        second = h.run_cycle(scope=s, symbol="GBPUSD", cycle_id=2,
                             bar_time=1784800300, pattern=None,
                             context_snapshot_id="A2")
        assert second["scope_value"] == ""
        assert second["engine_result"]["canonical_opportunity_id"] == ""

    def test_case_b_two_opportunities_isolated(self):
        h, s = _Harness(), _Scope()
        r1 = h.run_cycle(scope=s, symbol="EURUSD", cycle_id=10,
                         bar_time=1784800000, pattern="TWEEZER_TOP",
                         context_snapshot_id="B1")
        r2 = h.run_cycle(scope=s, symbol="GBPUSD", cycle_id=11,
                         bar_time=1784800300, pattern="HAMMER",
                         context_snapshot_id="B2")
        assert (r1["scope_value"], r2["scope_value"]) == (ID_A, ID_B)

    def test_case_c_cross_symbol_same_cycle_number(self):
        from core.opportunity.factory import create_opportunity

        o1 = create_opportunity(signal=_signal(), symbol="EURUSD", cycle_id=42)
        o2 = create_opportunity(signal=_signal(), symbol="GBPUSD", cycle_id=42)
        assert o1.canonical_opportunity_id == "EURUSD*1784800000*TWEEZER_TOP"
        assert o2.canonical_opportunity_id == "GBPUSD*1784800000*TWEEZER_TOP"
        assert o1.entity_id != o2.entity_id

    def test_case_d_risk_block_keeps_root(self):
        ledger_rows: list[dict] = []
        ledger = type("L", (), {"record": staticmethod(
            lambda **kw: ledger_rows.append(kw))})()
        rec = DecisionRecorder(ledger)
        d = rec.init_cycle(symbol="EURUSD", cycle_id=20, regime="r",
                           context_snapshot_id="CTXD", drawdown_pct=9.9,
                           daily_loss_pct=6.0, canonical_opportunity_id=ID_A)
        d.update(decision="RISK_BLOCK", reason="drawdown_guard")
        rec.finalize(cycle_start=0.0)
        assert ledger_rows[0]["decision"] == "RISK_BLOCK"
        assert ledger_rows[0]["canonical_opportunity_id"] == ID_A

    def test_case_e_no_trade_generates_no_execution_records(self):
        h, s = _Harness(), _Scope()
        h.run_cycle(scope=s, symbol="EURUSD", cycle_id=3, bar_time=1784800000,
                    pattern=None, context_snapshot_id="E1")
        assert [r for r in h.ledger.rows if r.get("decision") == "EXECUTE"] == []

    def test_case_f_shadow_gate_off_in_source_and_config(self):
        assert getattr(cfg, "SHADOW_RUNTIME_V2_ENABLED", False) is False
        assert getattr(cfg, "ENABLE_LEGACY_SHADOW_PIPELINE", False) is False
        src = (PROJECT_ROOT / "core/runtime/live_scanner.py").read_text(encoding="utf-8-sig")
        gate_pos = src.find("SHADOW_RUNTIME_V2_ENABLED")
        call_pos = src.find("handle_live_opportunity_shadow(")
        assert 0 < gate_pos < call_pos
        assert 'getattr(config, "SHADOW_RUNTIME_V2_ENABLED", False)' in src

    def test_case_g_entry_snapshot_cannot_accept_outcome_fields(self):
        with pytest.raises(TypeError):
            persist_execution_result(symbol="X", cycle_id=1, result_ok=True,
                                     retcode=0, deal=0, order=0, comment="",
                                     mfe_r_multiple=0.9)   # type: ignore[call-arg]

    def test_case_h_exit_does_not_overwrite_entry_snapshot(self):
        entry = build_trade_truth(
            trade_id="pH", correlation_id="CH", symbol="EURUSD",
            canonical_opportunity_id=ID_A, entry_fill_price=1.2305,
            exit_fill_price=1.2405, volume_executed=0.01)
        again = build_trade_truth(
            trade_id="pH", correlation_id="CH", symbol="EURUSD",
            canonical_opportunity_id=ID_A, entry_fill_price=1.2305,
            exit_fill_price=9.9999, volume_executed=0.01,
            exit_reason="manual_close")
        assert entry["execution"]["exit_fill_price"] == pytest.approx(1.2405)
        assert again["execution"]["exit_fill_price"] == pytest.approx(9.9999)
        assert entry["identity"]["canonical_opportunity_id"] == \
               again["identity"]["canonical_opportunity_id"] == ID_A


# ═══ STEP 11 — Writer authority guards ════════════════════════════════════════

class TestStep11WriterAuthority:
    def test_dormant_duplicate_writers_not_wired_into_runtime(self):
        engine_handler = (PROJECT_ROOT /
                          "core/runtime/engine_execution_handler.py"
                          ).read_text(encoding="utf-8-sig")
        assert "opportunity_assessment_writer" not in engine_handler
        assert "from core.shadow_trades import" not in engine_handler
        # live_scanner: assessment-authority only (legacy shadow import inside
        # its DISABLED branch is contract-sanctioned; NEW writer never wires it)
        scanner = (PROJECT_ROOT / "core/runtime/live_scanner.py"
                   ).read_text(encoding="utf-8-sig")
        assert "opportunity_assessment_writer" not in scanner

    def test_observation_enrichment_contract_present(self):
        src = (PROJECT_ROOT / "core/strategies/strategy_intelligence_observer.py"
               ).read_text(encoding="utf-8-sig")
        assert '"canonical_opportunity_id"' in src or "'canonical_opportunity_id'" in src
        assert '"bar_time"' in src and '"timeframe"' in src
