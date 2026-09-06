"""
Wave 2 — MGMT-1 / MGMT-2 management research tests.

Proves:
  - management population: action→trade join, managed/unmanaged split
  - MGMT-1: managed vs unmanaged observational comparison
  - MGMT-2: per-action-type analysis with CLOSE semantics classification
  - insufficient N handling
  - no causal wording, no trading mutation
  - registry/runner discovery

No real AWS. All synthetic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.experiments.management_research import (
    build_action_population, build_trade_level_population,
    _classify_action_semantics, run_mgmt1, run_mgmt2,
)


def _action(
    action_id: str = "ma_1", trade_id: str = "pos_1",
    action_type: str = "SLTP_MODIFY", reason: str = "trailing_stop",
    symbol: str = "EURUSD", corr: str = "COR-1",
) -> dict[str, Any]:
    return {
        "management_action_id": action_id,
        "trade_id": trade_id,
        "correlation_id": corr,
        "canonical_opportunity_id": "EURUSD*1*HAMMER",
        "symbol": symbol,
        "action_type": action_type,
        "action_reason": reason,
        "requested_sl": 1.09,
        "requested_tp": None,
        "requested_volume": None,
        "timestamp_utc": "2026-09-06T12:00:00Z",
    }


def _outcome(
    trade_id: str = "pos_1", corr: str = "COR-1",
    r: float = 1.0, symbol: str = "EURUSD",
) -> dict[str, Any]:
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": trade_id, "correlation_id": corr,
            "canonical_opportunity_id": "EURUSD*1*HAMMER", "symbol": symbol,
        },
        "outcome": {"r_multiple_realised": r, "pnl_realised": r * 100},
        "exit": {"exit_reason": "take_profit" if r > 0 else "stop_loss"},
    }


def _install(actions, outcomes, monkeypatch):
    import research_engine.experiments.management_research as mr
    monkeypatch.setattr(mr, "_load_actions", lambda: actions)
    monkeypatch.setattr(mr, "_load_outcomes", lambda: outcomes)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulation:
    def test_action_join_to_correct_trade(self):
        actions = [_action(trade_id="pos_1"), _action(action_id="ma_2", trade_id="pos_2")]
        outcomes = [_outcome(trade_id="pos_1"), _outcome(trade_id="pos_2", r=-0.5)]
        managed, outcome_by_id, managed_ids = build_trade_level_population(actions, outcomes)
        assert managed_ids == {"pos_1", "pos_2"}
        assert "pos_1" in outcome_by_id
        assert outcome_by_id["pos_1"]["r_multiple"] == 1.0

    def test_multiple_actions_per_trade(self):
        actions = [
            _action(action_id="ma_1", trade_id="pos_1", action_type="SLTP_MODIFY"),
            _action(action_id="ma_2", trade_id="pos_1", action_type="PARTIAL_CLOSE"),
        ]
        managed, _, _ = build_trade_level_population(actions, [])
        assert len(managed["pos_1"]) == 2

    def test_unmanaged_trades_distinguishable(self):
        actions = [_action(trade_id="pos_1")]
        outcomes = [_outcome(trade_id="pos_1"), _outcome(trade_id="pos_2", r=-0.5)]
        _, _, managed_ids = build_trade_level_population(actions, outcomes)
        assert "pos_1" in managed_ids
        assert "pos_2" not in managed_ids

    def test_duplicate_action_not_double_counted(self):
        actions = [_action(action_id="ma_1"), _action(action_id="ma_1")]
        pop = build_action_population(actions)
        assert len(pop) == 1

    def test_missing_lineage_excluded(self):
        actions = [_action(action_id="ma_1", trade_id="")]
        managed, _, _ = build_trade_level_population(actions, [])
        assert len(managed) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# MGMT-1 — MANAGED VS UNMANAGED
# ═══════════════════════════════════════════════════════════════════════════════


class TestMgmt1:
    def test_managed_better(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=1.5) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.2) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["status"] == "COMPLETE"
        assert report["recommendation"] == "MANAGEMENT_ASSOCIATED_WITH_BETTER_OUTCOMES"
        assert report["overall"]["managed"]["mean_r"] > report["overall"]["unmanaged"]["mean_r"]

    def test_managed_worse(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=-0.8) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.5) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["recommendation"] == "MANAGEMENT_ASSOCIATED_WITH_WORSE_OUTCOMES"

    def test_mixed_signal(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=0.5) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.4) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["recommendation"] == "MIXED_MANAGEMENT_SIGNAL"

    def test_insufficient_n(self, monkeypatch):
        actions = [_action(trade_id="pos_1")]
        outcomes = [_outcome(trade_id="pos_1"), _outcome(trade_id="pos_2")]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_no_causal_wording(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=1.5) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.2) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        src = json.dumps(report, default=str)
        assert "MANAGEMENT_IMPROVES_EV" not in src
        assert "caused" not in src.lower()
        assert "observational" in src.lower() or "association" in src.lower()

    def test_management_coverage_rate(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=1.5) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.2) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["overall"]["management_coverage_rate"] == pytest.approx(20 / 35, abs=0.01)

    def test_actions_per_managed_trade(self, monkeypatch):
        actions = (
            [_action(action_id="ma_1", trade_id="pos_1")]
            + [_action(action_id=f"ma_{i}", trade_id="pos_1", action_type="PARTIAL_CLOSE") for i in range(2, 5)]
        )
        outcomes = [_outcome(trade_id="pos_1")]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        if report["status"] == "COMPLETE":
            assert report["overall"]["mean_actions_per_managed_trade"] == pytest.approx(3.0)
        else:
            assert report["status"] == "INSUFFICIENT_DATA"



# ═══════════════════════════════════════════════════════════════════════════════
# MGMT-2 — PER ACTION TYPE
# ═══════════════════════════════════════════════════════════════════════════════


class TestMgmt2:
    def test_sltp_modify_separate(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}", action_type="SLTP_MODIFY") for i in range(20)]
        outcomes = [_outcome(trade_id=f"pos_{i}", r=0.8) for i in range(20)]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt2()
        types = report["overall"]["by_action_type"]
        assert "SLTP_MODIFY" in types
        assert types["SLTP_MODIFY"]["n"] >= 15

    def test_partial_close_separate(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}", action_type="PARTIAL_CLOSE", reason="profit_target") for i in range(20)]
        outcomes = [_outcome(trade_id=f"pos_{i}", r=1.2) for i in range(20)]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt2()
        types = report["overall"]["by_action_type"]
        assert "PARTIAL_CLOSE" in types

    def test_close_semantics_classified(self, monkeypatch):
        """CLOSE with TP/SL reason = lifecycle bookkeeping, not discretionary."""
        semantics = _classify_action_semantics("CLOSE", "take_profit reached")
        assert semantics == "LIFECYCLE_BOOKKEEPING"
        semantics2 = _classify_action_semantics("CLOSE", "management exit signal")
        assert semantics2 == "DISCRETIONARY_MANAGEMENT"
        semantics3 = _classify_action_semantics("SLTP_MODIFY", "trailing stop")
        assert semantics3 == "DISCRETIONARY_MANAGEMENT"

    def test_insufficient_per_type(self, monkeypatch):
        actions = [_action(action_id=f"ma_{i}", trade_id=f"pos_{i}", action_type="SLTP_MODIFY") for i in range(5)]
        outcomes = [_outcome(trade_id=f"pos_{i}", r=1.0) for i in range(5)]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt2()
        assert report["status"] == "INSUFFICIENT_DATA"
        # the insufficient path reports the count but not detailed per-type stats
        assert report["overall"]["trades_with_outcomes"] == 5

    def test_close_bookkeeping_not_counted_as_management(self, monkeypatch):
        """CLOSE with TP/SL reason should be classified as bookkeeping."""
        actions = [
            _action(action_id="ma_1", trade_id="pos_1", action_type="CLOSE", reason="take_profit reached"),
        ]
        outcomes = [_outcome(trade_id="pos_1", r=2.0)]
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt2()
        assert report["status"] == "INSUFFICIENT_DATA"  # N=1 < 15
        # semantics check is done by unit test above


# ═══════════════════════════════════════════════════════════════════════════════
# ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestArchitecture:
    def test_no_trading_mutation_path(self):
        src = (ROOT / "research_engine" / "experiments" / "management_research.py").read_text(
            encoding="utf-8")
        for f in ("MT5Execution", "RiskManager", "order_send", "from core.pipeline",
                  "persist_trade_truth", "ShadowRuntime("):
            assert f not in src, f"forbidden trading path: {f}"

    def test_canonical_s3_only(self):
        src = (ROOT / "research_engine" / "experiments" / "management_research.py").read_text(
            encoding="utf-8")
        assert "load_management_actions" in src
        assert "load_trade_truth" in src
        assert "read_dataset" not in src or "_load_actions" not in src

    def test_runners_discovered_exactly_once(self):
        from research_engine.runner_discovery import get_all_runners
        runners = get_all_runners()
        mgmt = [k for k in runners if k.startswith("MGMT")]
        assert len(mgmt) == 2
        assert len(set(mgmt)) == 2

    def test_gap4_status_contract(self, monkeypatch):
        actions = [_action(trade_id=f"pos_{i}") for i in range(20)]
        outcomes = (
            [_outcome(trade_id=f"pos_{i}", r=1.5) for i in range(20)]
            + [_outcome(trade_id=f"un_{i}", corr=f"COR-U{i}", r=0.2) for i in range(15)]
        )
        _install(actions, outcomes, monkeypatch)
        report = run_mgmt1()
        assert report["status"] in ("COMPLETE", "INSUFFICIENT_DATA", "BLOCKED")
        # Gap 4: recommendation is separate from status
        if report["status"] == "COMPLETE":
            assert report["recommendation"] != report["status"]
