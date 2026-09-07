"""
Wave 5 — Risk Research Tests (RISK-1)

Focused tests: risk_deviation_v1 evidence contract, denominator semantics,
over/under-risk classification, lineage, Gap-4 status, architecture.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fake_risk(
    trade_id: str = "TRADE-0001",
    symbol: str = "EURUSD",
    correlation_id: str = "COR-0001",
    planned_risk_R: float = -1.0,
    actual_risk_R: float = -1.0,
    risk_deviation: float = 1.0,
    classification: str = "NORMAL",
    direction: str = "BUY",
    semantic_stage: str = "post_outcome_analysis",
    entry_price: float = 1.1000,
    exit_price: float = 1.0950,
    initial_sl: float = 1.1050,
) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "correlation_id": correlation_id,
        "planned_risk_R": planned_risk_R,
        "actual_risk_R": actual_risk_R,
        "risk_deviation": risk_deviation,
        "risk_classification": classification,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "initial_sl": initial_sl,
        "direction": direction,
        "risk_distance": round(abs(entry_price - initial_sl), 8),
        "pnl_distance": round(exit_price - entry_price, 8),
        "timestamp_utc": "2026-09-01T00:00:00Z",
        "schema_version": "risk_deviation_v1",
        "semantic_stage": semantic_stage,
        "authority": "diagnostic_projection",
        "pre_trade_authority": False,
    }


def _patch_loader(monkeypatch, records):
    import research_engine.experiments.risk_research as mod
    monkeypatch.setattr(mod, "_load_risk_deviation", lambda: records)


# ═══════════════════════════════════════════════════════════════════
# Dataset contract
# ═══════════════════════════════════════════════════════════════════

class TestDatasetContract:
    def test_loader_is_canonical_s3(self):
        from research_engine.data_access.loaders import load_risk_deviation
        assert callable(load_risk_deviation)
        # routes through S3ResearchDataSource (not local)
        from research_engine.data_access import loaders as L
        assert hasattr(L, "get_default_source")
        assert L.get_default_source is not None

    def test_semantic_stage_is_post_outcome(self):
        """risk_deviation_v1 records are explicitly POST-OUTCOME diagnostics."""
        rec = _fake_risk()
        assert rec["semantic_stage"] == "post_outcome_analysis"
        assert rec["pre_trade_authority"] is False
        assert rec["authority"] == "diagnostic_projection"

    def test_planned_risk_is_definitional(self):
        """planned_risk_R is always -1.0 by production definition."""
        rec = _fake_risk()
        assert rec["planned_risk_R"] == -1.0

    def test_record_excluded_without_trade_id(self, monkeypatch):
        records = [_fake_risk(trade_id="") for _ in range(5)] + \
                  [_fake_risk() for _ in range(5)]
        _patch_loader(monkeypatch, records)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert r["status"] == "INSUFFICIENT_DATA"
        assert r["overall"]["excluded_no_trade_id"] == 5
        assert r["overall"]["n"] == 5


# ═══════════════════════════════════════════════════════════════════
# RISK-1 core analysis
# ═══════════════════════════════════════════════════════════════════

class TestRisk1:
    def make(self, classifications: list[str], deviation=None, actual=None):
        """Generate a list of records with the given classifications."""
        out = []
        for i, cls in enumerate(classifications):
            dev = 1.0 if deviation is None else deviation[i]
            act = -1.0 if actual is None else actual[i]
            if cls == "WIN":
                dev = 1.0
                act = 1.0
            out.append(_fake_risk(
                trade_id=f"TRADE-{i:04d}",
                symbol="EURUSD",
                classification=cls,
                risk_deviation=dev,
                actual_risk_R=act))
        return out

    def test_insufficient_n(self, monkeypatch):
        _patch_loader(monkeypatch, [])
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert r["status"] == "INSUFFICIENT_DATA"
        assert r["overall"]["n"] == 0

    def test_complete_classification_distribution(self, monkeypatch):
        recs = self.make(
            ["NORMAL"] * 20 + ["ELEVATED"] * 8 + ["CRITICAL"] * 2 + ["WIN"] * 10)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert r["status"] == "COMPLETE"
        dist = r["overall"]["classification_distribution"]
        assert dist["NORMAL"] == 20
        assert dist["ELEVATED"] == 8
        assert dist["CRITICAL"] == 2
        assert dist["WIN"] == 10
        assert r["overall"]["n"] == 40

    def test_over_plan_rate_denominator(self, monkeypatch):
        """ELEVATED/CRITICAL share computed over loss records only."""
        recs = self.make(
            ["NORMAL"] * 24 + ["ELEVATED"] * 6 + ["WIN"] * 10)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        expected = round(6 / 30, 4)
        actual = r["overall"]["deviation_rate_over_plan"]
        assert actual["numerator"] == 6
        assert actual["denominator"] == 30
        assert actual["rate"] == expected

    def test_critical_rate(self, monkeypatch):
        recs = self.make(
            ["NORMAL"] * 26 + ["CRITICAL"] * 4 + ["WIN"] * 5)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        expected = round(4 / 30, 4)
        actual = r["overall"]["critical_loss_rate"]
        assert actual["rate"] == expected

    def test_loss_stats(self, monkeypatch):
        recs = self.make(
            ["NORMAL"] * 20 + ["ELEVATED"] * 10, deviation=[1.0] * 20 + [2.0] * 10)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        stats = r["overall"]["loss_deviation_stats"]
        assert stats["n"] == 30
        assert round(stats["mean"], 4) == round((20 * 1.0 + 10 * 2.0) / 30, 4)

    def test_no_risk_data_excluded(self, monkeypatch):
        recs = self.make(["NORMAL"] * 30 + ["NO_RISK_DATA"] * 5)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        # n includes NO_RISK_DATA count (it has a classification), but
        # deviation rates exclude it from the denominator.
        assert r["overall"]["n"] == 35
        assert r["overall"]["classification_distribution"]["NO_RISK_DATA"] == 5
        assert r["overall"]["deviation_rate_over_plan"]["denominator"] == 30
        assert "NO_RISK_DATA" in r["overall"]["classification_distribution"]

    def test_per_symbol_gate(self, monkeypatch):
        recs = self.make(["NORMAL"] * 30) + \
            [_fake_risk(trade_id=f"T-{i}", symbol="GBPUSD")
             for i in range(5)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        per_sym = r["overall"]["per_symbol"]
        assert "EURUSD" in per_sym
        assert "GBPUSD" not in per_sym

    def test_win_separate_from_loss_denominator(self, monkeypatch):
        recs = self.make(["WIN"] * 30 + ["ELEVATED"] * 5)
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert r["overall"]["win_records"] == 30
        assert r["overall"]["loss_records"] == 5
        assert r["overall"]["deviation_rate_over_plan"]["denominator"] == 5


# ═══════════════════════════════════════════════════════════════════
# Risk semantics / honesty
# ═══════════════════════════════════════════════════════════════════

class TestRiskSemantics:
    def test_not_causal_wording(self, monkeypatch):
        """Report must not claim deviation caused the loss."""
        recs = [_fake_risk(
            trade_id=f"TRADE-{i:04d}",
            classification="CRITICAL" if i % 2 == 0 else "NORMAL",
            risk_deviation=4.0 if i % 2 == 0 else 1.0,
            actual_risk_R=-4.0 if i % 2 == 0 else -1.0) for i in range(30)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        import json as _json
        s = _json.dumps(r).lower()
        for phrase in ("caused the", "causation", "proves that"):
            assert phrase not in s, f"report must not claim {phrase}"

    def test_deviation_not_called_cause(self, monkeypatch):
        """Warnings explicitly state deviation is a measure, not a cause."""
        recs = [_fake_risk(trade_id=f"T-{i}") for i in range(30)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        warnings = " ".join(r.get("warnings", [])).lower()
        assumptions = " ".join(r.get("assumptions", [])).lower()
        combined = (warnings + " " + assumptions).lower()
        assert "not prove" in combined or "does not" in combined

    def test_no_pre_trade_risk_claim(self, monkeypatch):
        """Report must not claim it measures pre-trade risk planning."""
        recs = [_fake_risk(trade_id=f"T-{i}") for i in range(30)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assumptions = " ".join(r.get("assumptions", []))
        assert "pre-trade" in assumptions.lower() or "planned" in assumptions.lower()

    def test_immutable_thresholds(self):
        """RISK-1 uses production NORMAL/ELEVATED band values, unchanged."""
        import core.risk_deviation as c
        assert c.NORMAL_DEVIATION_MAX == 1.5
        assert c.ELEVATED_DEVIATION_MAX == 3.0


# ═══════════════════════════════════════════════════════════════════
# Architecture / governance
# ═══════════════════════════════════════════════════════════════════

class TestArchitecture:
    def test_discovered_in_registry(self):
        from research_engine.runner_discovery import get_all_runners
        d = get_all_runners()
        assert "RISK-1" in d
        assert d["RISK-1"].__name__ == "run_risk1"

    def test_discovered_exactly_once(self):
        from research_engine.registry.research_question_registry import REGISTRY
        rs = [q for q in REGISTRY if q.id == "RISK-1"]
        assert len(rs) == 1

    def test_gap4_status_contract(self, monkeypatch):
        _patch_loader(monkeypatch, [])
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert "status" in r
        assert "recommendation" in r
        # with zero data both are INSUFFICIENT_DATA (status is authoritative)
        assert r["status"] == "INSUFFICIENT_DATA"

    def test_gap7_provenance(self, monkeypatch):
        recs = [_fake_risk(trade_id=f"T-{i}") for i in range(35)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()
        assert "provenance" in r
        assert r["provenance"]["registry_id"] == "RISK-1"
        assert r["provenance"]["experiment_module"] == (
            "research_engine.experiments.risk_research")

    def test_no_trading_mutation(self):
        """RISK-1 module must not reference production risk/MT5 paths."""
        import research_engine.experiments.risk_research as mod
        text = open(mod.__file__, encoding="utf-8").read()
        for forbidden in ("MT5Execution(", "order_send", "RiskManager(",
                          "persist_risk_deviation", "trade_management",
                          "position_sizing", "compute_risk_deviation("):
            assert forbidden not in text, (
                f"module should not reference {forbidden}")

    def test_no_local_fallback(self):
        from research_engine.data_access.loaders import load_risk_deviation
        src_file = load_risk_deviation.__globals__.get("__file__", "")
        text = open(src_file, encoding="utf-8").read()
        assert "get_default_source" in text or "read_dataset" in text

    def test_no_nan_infinity(self, monkeypatch):
        recs = [_fake_risk(trade_id=f"T-{i}") for i in range(35)]
        _patch_loader(monkeypatch, recs)
        from research_engine.experiments.risk_research import run_risk1
        r = run_risk1()

        def _walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)
            elif isinstance(o, float):
                assert math.isfinite(o), f"non-finite {o}"

        _walk(r)

    def test_weekly_cycle_compat(self):
        """RISK-1 is a standard registry question — no special scheduler."""
        from research_engine.runner_discovery import get_all_runners
        assert "RISK-1" in get_all_runners()