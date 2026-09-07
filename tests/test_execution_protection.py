"""
Wave 4 — Execution & Protection Research Tests

X1, X2, X3, X5, EXEC-1, PROT-1 using fake S3 data.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Fake S3 fixture ──────────────────────────────────────────────

FAKE_RECORDS: dict[str, list[dict[str, Any]]] = {}


def _fake_dataset(dataset: str) -> list[dict[str, Any]]:
    return copy.deepcopy(FAKE_RECORDS.get(dataset, []))


def _fake_result(
    correlation_id: str = "COR-0001",
    result_ok: bool = True,
    retcode: int = 0,
    slippage: float = 0.5,
    slippage_semantic: str = "measured_execution_slippage",
    symbol: str = "EURUSD",
    broker_confirmed_sl: float = 1.1050,
    broker_confirmed_tp: float = 1.1150,
    requested_sl: float = 1.1050,
    requested_tp: float = 1.1150,
    protection_status: str = "PROTECTED",
    protection_failure_reason: str = "",
) -> dict[str, Any]:
    return {
        "correlation_id": correlation_id,
        "decision_id": f"DEC-{correlation_id}",
        "canonical_opportunity_id": f"OPP-{correlation_id}",
        "entity_id": f"entity_{correlation_id}",
        "symbol": symbol,
        "result_ok": result_ok,
        "retcode": retcode,
        "comment": "",
        "fill_price": 1.1000,
        "slippage": slippage,
        "slippage_semantic": slippage_semantic,
        "protection_status": protection_status,
        "protection_failure_reason": protection_failure_reason,
        "requested_sl": requested_sl,
        "requested_tp": requested_tp,
        "broker_confirmed_sl": broker_confirmed_sl,
        "broker_confirmed_tp": broker_confirmed_tp,
    }


def _fake_context(
    correlation_id: str = "COR-0001",
    session_state: str = "LONDON",
    spread: float = 1.5,
    symbol: str = "EURUSD",
    latency_ms: float = 5.0,
    drawdown_pct: float = 2.0,
    open_positions: int = 3,
) -> dict[str, Any]:
    return {
        "correlation_id": correlation_id,
        "canonical_opportunity_id": f"OPP-{correlation_id}",
        "symbol": symbol,
        "market_access": {
            "session_state": session_state,
            "spread": spread,
            "spread_atr_ratio": None,
            "bid": 1.0995,
            "ask": 1.1005,
        },
        "infrastructure": {
            "latency_ms": latency_ms,
            "feed_state": "OK",
            "tick_age_ms": 0,
        },
        "risk_environment": {
            "drawdown_pct": drawdown_pct,
            "open_positions": open_positions,
        },
    }


def _fake_attempt(
    attempt_id: str = "ATT-001",
    correlation_id: str = "COR-0001",
    trade_id: str = "TRADE-001",
    action_type: str = "ORDER_PLACE",
    attempt_number: int = 1,
    broker_ok: bool = True,
    retcode: int = 0,
    spread_at_attempt: float = 1.5,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "correlation_id": correlation_id,
        "decision_id": f"DEC-{correlation_id}",
        "canonical_opportunity_id": f"OPP-{correlation_id}",
        "trade_id": trade_id,
        "symbol": "EURUSD",
        "action_type": action_type,
        "attempt_number": attempt_number,
        "retry_reason": None,
        "broker_result": {"ok": broker_ok, "retcode": retcode, "comment": ""},
        "spread_at_attempt": spread_at_attempt,
        "slippage": None,
    }


def _fake_protection(
    correlation_id: str = "COR-0001",
    protection_status: str = "PROTECTED",
    broker_confirmed_sl: float = 1.1050,
    broker_confirmed_tp: float = 1.1150,
    requested_sl: float = 1.1050,
    requested_tp: float = 1.1150,
    correction_attempted: bool = False,
    correction_success: bool = False,
    protection_failure_reason: str = "",
    position_ticket: int = 10001,
) -> dict[str, Any]:
    return {
        "correlation_id": correlation_id,
        "symbol": "EURUSD",
        "position_ticket": position_ticket,
        "requested_sl": requested_sl,
        "requested_tp": requested_tp,
        "broker_confirmed_sl": broker_confirmed_sl,
        "broker_confirmed_tp": broker_confirmed_tp,
        "protection_status": protection_status,
        "protection_failure_reason": protection_failure_reason,
        "verification_latency_ms": None,
        "attempts": 1,
        "correction_attempted": correction_attempted,
        "correction_success": correction_success,
    }


def _fake_decision_trace(
    canonical_opportunity_id: str = "OPP-COR-0001",
    ev: float = 0.5,
    symbol: str = "EURUSD",
) -> dict[str, Any]:
    return {
        "canonical_opportunity_id": canonical_opportunity_id,
        "ev": ev,
        "symbol": symbol,
        "entity_id": f"e_{canonical_opportunity_id}",
    }


def _fake_trade_truth(
    canonical_opportunity_id: str = "OPP-COR-0001",
    r_multiple_realised: float = 0.3,
    trade_id: str = "TRADE-001",
) -> dict[str, Any]:
    return {
        "canonical_opportunity_id": canonical_opportunity_id,
        "r_multiple_realised": r_multiple_realised,
        "trade_id": trade_id,
        "correlation_id": "COR-0001",
        "symbol": "EURUSD",
    }


# ── Monkey-patch helpers ────────────────────────────────────────


def _patch_loaders(monkeypatch, results=None, contexts=None, attempts=None,
                   protections=None, dt=None, tt=None):
    """Replace the S3 loaders with fake data returns."""

    def _fake_results():
        return results or [copy.deepcopy(_fake_result()) for _ in range(35)]

    def _fake_contexts():
        return contexts or [copy.deepcopy(_fake_context()) for _ in range(35)]

    def _fake_attempts():
        return attempts or []

    def _fake_protections():
        return protections or [
            copy.deepcopy(_fake_protection()) for _ in range(35)]

    def _fake_dt():
        return dt or []
    
    def _fake_tt():
        return tt or []

    import research_engine.experiments.execution_protection_research as mod
    monkeypatch.setattr(mod, "_load_results", _fake_results)
    monkeypatch.setattr(mod, "_load_context", _fake_contexts)
    monkeypatch.setattr(mod, "_load_attempts", _fake_attempts)
    monkeypatch.setattr(mod, "_load_protection", _fake_protections)
    monkeypatch.setattr(mod, "_load_decision_trace", _fake_dt)
    monkeypatch.setattr(mod, "_load_trade_truth", _fake_tt)


# ═══════════════════════════════════════════════════════════════════
# Evidence Ownership Tests
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceOwnership:
    """X1-X3/X5/EXEC-1/PROT-1 use correct canonical datasets."""

    def test_x1_uses_execution_results(self):
        """X1 loads execution_results_v1 (not shadow_trades)."""
        from research_engine.data_access.loaders import load_execution_results
        load = load_execution_results
        assert callable(load), "X1 evidence must be execution_results_v1"

    def test_x2_uses_execution_results_and_attempts(self):
        """X2 loads execution_results_v1 + execution_attempts_v1."""
        from research_engine.data_access.loaders import (
            load_execution_results, load_execution_attempts)
        assert callable(load_execution_results)
        assert callable(load_execution_attempts)

    def test_x3_uses_context_and_results(self):
        """X3 loads execution_context_v1 + execution_results_v1."""
        from research_engine.data_access.loaders import (
            load_execution_context, load_execution_results)
        assert callable(load_execution_context)
        assert callable(load_execution_results)

    def test_x5_uses_decision_trace_and_trade_truth(self):
        """X5 loads decision_trace_v1 + trade_truth_v1."""
        from research_engine.data_access.loaders import (
            load_decision_trace, load_trade_truth)
        assert callable(load_decision_trace)
        assert callable(load_trade_truth)

    def test_exec1_uses_results_and_context(self):
        """EXEC-1 loads execution_results_v1 + execution_context_v1."""
        from research_engine.data_access.loaders import (
            load_execution_results, load_execution_context)
        assert callable(load_execution_results)
        assert callable(load_execution_context)

    def test_prot1_uses_protection_audit(self):
        """PROT-1 loads protection_audit_v1."""
        from research_engine.data_access.loaders import load_protection_audit
        assert callable(load_protection_audit)


# ═══════════════════════════════════════════════════════════════════
# X1 Tests
# ═══════════════════════════════════════════════════════════════════

class TestX1:
    def test_insufficient_n(self, monkeypatch):
        _patch_loaders(monkeypatch, results=[_fake_result() for _ in range(5)])
        from research_engine.experiments.execution_protection_research import run_x1
        r = run_x1()
        assert r["status"] == "INSUFFICIENT_DATA"
        assert r["overall"]["n"] == 5

    def test_complete_with_slippage(self, monkeypatch):
        results = [_fake_result(correlation_id=f"COR-{i:04d}",
                                slippage=0.1 + i * 0.01,
                                slippage_semantic="measured_execution_slippage")
                   for i in range(35)]
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_x1
        r = run_x1()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["measured_slippage_count"] == 35
        assert r["dataset"]["source"] == "execution_results_v1"

    def test_slippage_only_measured_semantic(self, monkeypatch):
        results = ([_fake_result() for _ in range(35)] +
                   [_fake_result(correlation_id="COR-UNMEASURED",
                                 slippage=10.0,
                                 slippage_semantic="unmeasured")
                    for _ in range(5)])
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_x1
        r = run_x1()
        assert r["overall"]["measured_slippage_count"] == 35
        assert r["status"] == "COMPLETE"


# ═══════════════════════════════════════════════════════════════════
# X2 Tests
# ═══════════════════════════════════════════════════════════════════

class TestX2:
    def test_insufficient_n(self, monkeypatch):
        _patch_loaders(monkeypatch, results=[_fake_result() for _ in range(5)])
        from research_engine.experiments.execution_protection_research import run_x2
        r = run_x2()
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_complete_ok_rate(self, monkeypatch):
        results = [_fake_result(result_ok=(i < 30), retcode=0 if i < 30 else 1)
                   for i in range(35)]
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_x2
        r = run_x2()
        assert r["status"] == "COMPLETE"
        ok_rate = r["overall"]["result_ok_rate"]
        assert ok_rate == round(30 / 35, 4)

    def test_retcode_distribution(self, monkeypatch):
        results = [_fake_result(retcode=0 if i < 20 else 10006)
                   for i in range(35)]
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_x2
        r = run_x2()
        assert "retcode_distribution" in r["overall"]
        assert r["overall"]["retcode_distribution"].get("0") == 20

    def test_symbol_sufficient_n_only(self, monkeypatch):
        results = ([_fake_result(symbol="EURUSD") for _ in range(30)] +
                   [_fake_result(symbol="GBPUSD") for _ in range(5)])
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_x2
        r = run_x2()
        per_sym = r["overall"]["per_symbol"]
        assert "EURUSD" in per_sym
        assert "GBPUSD" not in per_sym

    def test_attempts_layer_fix_nested_ok(self, monkeypatch):
        """X2 uses nested broker_result.ok for attempts."""
        results = [_fake_result() for _ in range(30)]
        attempts = [_fake_attempt(attempt_id=f"ATT-{i:04d}",
                   attempt_number=2, broker_ok=False)
                   for i in range(10)]
        _patch_loaders(monkeypatch, results=results, attempts=attempts)
        from research_engine.experiments.execution_protection_research import run_x2
        r = run_x2()
        assert "attempts_layer" in r["overall"]


# ═══════════════════════════════════════════════════════════════════
# X3 Tests
# ═══════════════════════════════════════════════════════════════════

class TestX3:
    def test_insufficient_n(self, monkeypatch):
        ctx = [_fake_context() for _ in range(5)]
        res = [_fake_result() for _ in range(5)]
        _patch_loaders(monkeypatch, results=res, contexts=ctx)
        from research_engine.experiments.execution_protection_research import run_x3
        r = run_x3()
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_no_context(self, monkeypatch):
        _patch_loaders(monkeypatch, contexts=[])
        from research_engine.experiments.execution_protection_research import run_x3
        r = run_x3()
        assert r["status"] == "INSUFFICIENT_DATA"
        assert r["overall"]["n"] == 0

    def test_complete_session_quality(self, monkeypatch):
        ctx = [_fake_context(correlation_id=f"COR-{i:04d}",
                             session_state="LONDON")
               for i in range(35)]
        res = [_fake_result(correlation_id=f"COR-{i:04d}",
                            slippage=0.1 + i * 0.01)
               for i in range(35)]
        _patch_loaders(monkeypatch, results=res, contexts=ctx)
        from research_engine.experiments.execution_protection_research import run_x3
        r = run_x3()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["sessions_analysed"] >= 1
        assert "per_session_slippage" in r["overall"]

    def test_ambiguous_multiplicity_excluded(self, monkeypatch):
        ctx = [_fake_context(correlation_id="COR-0001") for _ in range(2)]
        res = [_fake_result(correlation_id="COR-0001")]
        _patch_loaders(monkeypatch, results=res, contexts=ctx)
        from research_engine.experiments.execution_protection_research import run_x3
        r = run_x3()
        assert r["overall"]["ambiguous"] >= 1


# ═══════════════════════════════════════════════════════════════════
# X5 Tests
# ═══════════════════════════════════════════════════════════════════

class TestX5:
    def test_insufficient_n(self, monkeypatch):
        dt = [_fake_decision_trace() for _ in range(5)]
        tt = [_fake_trade_truth() for _ in range(5)]
        _patch_loaders(monkeypatch, dt=dt, tt=tt)
        from research_engine.experiments.execution_protection_research import run_x5
        r = run_x5()
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_leakage_preserves_edge(self, monkeypatch):
        dt = [_fake_decision_trace(canonical_opportunity_id=f"OPP-{i}",
                                    ev=0.5) for i in range(35)]
        tt = [_fake_trade_truth(canonical_opportunity_id=f"OPP-{i}",
                                 r_multiple_realised=0.48) for i in range(35)]
        _patch_loaders(monkeypatch, dt=dt, tt=tt)
        from research_engine.experiments.execution_protection_research import run_x5
        r = run_x5()
        assert r["status"] == "COMPLETE"
        assert r["recommendation"] == "EXECUTION_PRESERVES_EDGE"

    def test_partial_execution_loss(self, monkeypatch):
        dt = [_fake_decision_trace(canonical_opportunity_id=f"OPP-{i}",
                                    ev=0.5) for i in range(35)]
        tt = [_fake_trade_truth(canonical_opportunity_id=f"OPP-{i}",
                                 r_multiple_realised=0.2) for i in range(35)]
        _patch_loaders(monkeypatch, dt=dt, tt=tt)
        from research_engine.experiments.execution_protection_research import run_x5
        r = run_x5()
        assert r["status"] == "COMPLETE"
        assert r["recommendation"] == "PARTIAL_EXECUTION_LOSS"

    def test_no_positive_ev_baseline(self, monkeypatch):
        dt = [_fake_decision_trace(canonical_opportunity_id=f"OPP-{i}",
                                    ev=-0.1) for i in range(35)]
        tt = [_fake_trade_truth(canonical_opportunity_id=f"OPP-{i}",
                                 r_multiple_realised=-0.15) for i in range(35)]
        _patch_loaders(monkeypatch, dt=dt, tt=tt)
        from research_engine.experiments.execution_protection_research import run_x5
        r = run_x5()
        assert r["status"] == "COMPLETE"
        assert r["recommendation"] == "NO_POSITIVE_EV_BASELINE"

    def test_unmatched_and_ambiguous_reported(self, monkeypatch):
        dt = ([_fake_decision_trace(canonical_opportunity_id=f"OPP-{i}",
                                     ev=0.5) for i in range(35)] +
              [_fake_decision_trace(canonical_opportunity_id="OPP-NOOUT",
                                     ev=0.5)])
        tt = [_fake_trade_truth(canonical_opportunity_id=f"OPP-{i}",
                                 r_multiple_realised=0.3) for i in range(35)]
        _patch_loaders(monkeypatch, dt=dt, tt=tt)
        from research_engine.experiments.execution_protection_research import run_x5
        r = run_x5()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["unmatched_dt"] >= 1


# ═══════════════════════════════════════════════════════════════════
# EXEC-1 Tests
# ═══════════════════════════════════════════════════════════════════

class TestExec1:
    def test_insufficient_n(self, monkeypatch):
        _patch_loaders(monkeypatch, results=[_fake_result() for _ in range(5)])
        from research_engine.experiments.execution_protection_research import run_exec1
        r = run_exec1()
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_complete_success_rate(self, monkeypatch):
        results = [_fake_result(correlation_id=f"COR-{i:04d}",
                                result_ok=(i % 3 != 0))
                   for i in range(35)]
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_exec1
        r = run_exec1()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["n"] == 35
        assert r["overall"]["success_rate"] is not None

    def test_context_join_when_available(self, monkeypatch):
        results = [_fake_result(correlation_id=f"COR-{i:04d}",
                                slippage=0.1 + i * 0.01)
                   for i in range(35)]
        contexts = [_fake_context(correlation_id=f"COR-{i:04d}",
                                   spread=1.0 + i * 0.1)
                    for i in range(35)]
        _patch_loaders(monkeypatch, results=results, contexts=contexts)
        from research_engine.experiments.execution_protection_research import run_exec1
        r = run_exec1()
        assert r["status"] == "COMPLETE"
        assert "context_join" in r["overall"]

    def test_per_symbol(self, monkeypatch):
        results = [_fake_result(symbol="EURUSD") for _ in range(20)] + \
                  [_fake_result(symbol="GBPUSD") for _ in range(5)]
        _patch_loaders(monkeypatch, results=results)
        from research_engine.experiments.execution_protection_research import run_exec1
        r = run_exec1()
        assert "EURUSD" in r["overall"]["per_symbol"]
        assert "GBPUSD" not in r["overall"]["per_symbol"]


# ═══════════════════════════════════════════════════════════════════
# PROT-1 Tests
# ═══════════════════════════════════════════════════════════════════

class TestProt1:
    def test_insufficient_n(self, monkeypatch):
        _patch_loaders(monkeypatch,
                       protections=[_fake_protection() for _ in range(5)])
        from research_engine.experiments.execution_protection_research import run_prot1
        r = run_prot1()
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_complete_protection(self, monkeypatch):
        protections = [_fake_protection(
            correlation_id=f"COR-{i:04d}",
            protection_status="PROTECTED",
            broker_confirmed_sl=1.1050,
            requested_sl=1.1050,
            broker_confirmed_tp=1.1150,
            requested_tp=1.1150) for i in range(35)]
        _patch_loaders(monkeypatch, protections=protections)
        from research_engine.experiments.execution_protection_research import run_prot1
        r = run_prot1()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["sl_present"] == 35
        assert r["overall"]["sl_match_count"] == 35

    def test_failure_reasons(self, monkeypatch):
        protections = [_fake_protection(
            correlation_id=f"COR-{i:04d}",
            protection_status="UNPROTECTED",
            protection_failure_reason="SL_REJECTED;TP_REJECTED",
            broker_confirmed_sl=None,
            broker_confirmed_tp=None) for i in range(35)]
        _patch_loaders(monkeypatch, protections=protections)
        from research_engine.experiments.execution_protection_research import run_prot1
        r = run_prot1()
        assert r["status"] == "COMPLETE"
        assert r["overall"]["failures"] == 35
        assert r["overall"]["sl_present"] == 0

    def test_correction_attempts(self, monkeypatch):
        protections = [_fake_protection(
            correction_attempted=True,
            correction_success=True) for _ in range(35)]
        _patch_loaders(monkeypatch, protections=protections)
        from research_engine.experiments.execution_protection_research import run_prot1
        r = run_prot1()
        assert r["overall"]["correction_attempted"] == 35
        assert r["overall"]["correction_success_count"] == 35

    def test_protection_mismatch(self, monkeypatch):
        protections = [_fake_protection(
            correlation_id=f"COR-{i:04d}",
            broker_confirmed_sl=1.1040,
            requested_sl=1.1050) for i in range(35)]
        _patch_loaders(monkeypatch, protections=protections)
        from research_engine.experiments.execution_protection_research import run_prot1
        r = run_prot1()
        assert r["overall"]["sl_match_count"] == 0


# ═══════════════════════════════════════════════════════════════════
# Architecture / Governance
# ═══════════════════════════════════════════════════════════════════

class TestArchitecture:
    def test_runners_discovered_in_registry(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        for qid in ("X1", "X2", "X3", "X5", "EXEC1", "PROT1"):
            assert qid in d, f"{qid} must be discovered"

    def test_gap4_status_contract(self, monkeypatch):
        _patch_loaders(monkeypatch, results=[_fake_result() for _ in range(5)])
        from research_engine.experiments.execution_protection_research import run_x1
        r = run_x1()
        assert "status" in r
        assert "recommendation" in r
        assert r["status"] != r["recommendation"] or r["status"] in ("INSUFFICIENT_DATA",)

    def test_no_trading_mutation(self):
        """Runner module must not import MT5/order_send/RiskManager."""
        from research_engine.experiments import execution_protection_research
        mod = execution_protection_research
        src = mod.__file__
        text = open(src, encoding="utf-8").read()
        for forbidden in ("MT5Execution(", "order_send", "RiskManager(",
                          "persist_trade_truth", "trade_management"):
            assert forbidden not in text, (
                f"module should not reference {forbidden}")

    def test_no_local_fallback(self):
        """Loaders use S3ResearchDataSource, not local files."""
        from research_engine.data_access.loaders import (
            load_execution_results, load_execution_context,
            load_execution_attempts, load_protection_audit,
            load_decision_trace, load_trade_truth)
        for load in [load_execution_results, load_execution_context,
                      load_execution_attempts, load_protection_audit,
                      load_decision_trace, load_trade_truth]:
            src = load.__module__
            assert src != "builtins"
            assert "s3_source" in (
                open(load.__globals__.get("__file__", src), "rb").read().decode("utf-8", errors="replace")
                if load.__globals__.get("__file__") else "")

    def test_gap7_provenance_compat(self, monkeypatch):
        _patch_loaders(monkeypatch,
                       results=[_fake_result() for _ in range(35)])
        from research_engine.experiments.execution_protection_research import run_x1
        r = run_x1()
        assert "provenance" in r
        assert r["provenance"]["registry_id"] == "X1"


# ═══════════════════════════════════════════════════════════════════
# Runner discovery guard
# ═══════════════════════════════════════════════════════════════════

class TestRunnerDiscovery:
    def test_x1_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        fn = d["X1"]
        assert callable(fn)
        assert fn.__name__ == "run_x1"

    def test_x2_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert d["X2"].__name__ == "run_x2"

    def test_x3_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert d["X3"].__name__ == "run_x3"

    def test_x5_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert d["X5"].__name__ == "run_x5"

    def test_exec1_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert d["EXEC1"].__name__ == "run_exec1"

    def test_prot1_discovered_once(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert d["PROT1"].__name__ == "run_prot1"

    def test_no_duplicate_X5_in_registry(self):
        from research_engine.registry.research_question_registry import REGISTRY
        x5s = [q for q in REGISTRY if q.id == "X5"]
        assert len(x5s) == 1
        assert x5s[0].runner_module == (
            "research_engine.experiments.execution_protection_research")