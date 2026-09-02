"""
Regression tests for the offline causal-replay migration off the retired
`decision_audit` dataset onto the retained V1 authorities:

    - decision_ledger  → authoritative terminal decision (action/reason/lineage)
    - decision_trace   → diagnostic reasoning (score/pattern/structure/stage)

Proves replay works with `logs/decision_audit/` completely ABSENT, that the
terminal decision comes from the ledger (not inferred from trace), that
diagnostic reasoning comes from the trace, and that canonical lineage survives
the replay projection unchanged.

Read-only offline consumer test — no trading logic is exercised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.causal.replay import CausalReplayEngine


# ─── Known lineage ────────────────────────────────────────────────────────────
CANONICAL = "EURUSD*1785205500*ENGULFING_BULLISH"
CORRELATION = "COR-20260101-7-EURUSD-A1B2"
OBSERVATION = "obs_EURUSD_1785205500_M5"
DECISION_ID = "deadbeefcafe"
ENTITY = "EURUSD_1785205500"
CYCLE = 7
SYMBOL = "EURUSD"
TRADE_ID = "hshadow_7_EURUSD_INTRADAY"


class _StubCausalAPI:
    """Minimal causal API — the graph is not under test here, only that decision
    facts/lineage are read from the retained datasets, not decision_audit."""

    def lineage(self, node_id):        return {"paths": [], "shortest_path": [], "path_count": 0}
    def risk_surface(self, node_id):   return {"total_at_risk": 0, "hard_failures": []}
    def what_if(self, node_id):        return {"hard_failures": [], "degraded": []}
    def backward(self, node_id):       return {"cause_count": 0, "causal_ancestors": [], "by_domain": {}}
    def find(self, **filters):         return {"nodes": []}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _seed_retained_v1(root: Path, *, ledger_decision: str, ledger_reason: str,
                      trace_pattern: str, trace_structure_ok: bool,
                      trace_terminal_stage: str) -> None:
    """Stage A: persist the retained V1 records a real runtime writes.

    Deliberately does NOT create logs/decision_audit/.
    """
    # Shadow record provides the lineage the replay starts from.
    _write_jsonl(root / "logs" / "shadow_trades" / SYMBOL / "2026-07-23.jsonl", [{
        "schema_version": "shadow_trades_v1",
        "identity": {
            "trade_id": TRADE_ID,
            "correlation_id": CORRELATION,
            "canonical_opportunity_id": CANONICAL,
            "entity_id": ENTITY,
            "cycle_id": CYCLE,
            "symbol": SYMBOL,
        },
        "decision_snapshot": {"timestamp_decision_utc": 1785205500, "pattern": trace_pattern,
                              "direction": "BUY", "score": 0.61},
        "simulated_outcome": {"pnl_r_multiple": 1.4, "exit_reason": "take_profit"},
    }])

    # decision_ledger = AUTHORITATIVE terminal decision.
    _write_jsonl(root / "logs" / "decision_ledger" / SYMBOL / "2026-07-23.jsonl", [{
        "schema_version": "decision_ledger_v1",
        "symbol": SYMBOL,
        "cycle_id": CYCLE,
        "decision": ledger_decision,
        "reason": ledger_reason,
        "signal_score": 0.61,
        "execution_intent": {"side": "BUY", "volume": 0.1, "sl": 1.094, "tp": 1.101},
        "correlation_id": CORRELATION,
        "decision_id": DECISION_ID,
        "entity_id": ENTITY,
        "canonical_opportunity_id": CANONICAL,
        "observation_id": OBSERVATION,
    }])

    # decision_trace = DIAGNOSTIC reasoning (deliberately carries a DIFFERENT,
    # non-authoritative action to prove the terminal decision comes from ledger).
    _write_jsonl(root / "logs" / "decision_trace" / SYMBOL / "2026-07-23.jsonl", [{
        "schema_version": "decision_trace_v1",
        "symbol": SYMBOL,
        "cycle_id": CYCLE,
        "action": "NO_TRADE",  # NON-authoritative — must NOT override ledger
        "terminal_stage": trace_terminal_stage,
        "pattern_name": trace_pattern,
        "structure_ok": trace_structure_ok,
        "selected_strategy": "CONTINUATION",
        "trade_horizon": "INTRADAY",
        "components": {"htf_alignment": 0.7, "formation": 0.5},
        "score_strategy": 0.61,
        "correlation_id": CORRELATION,
        "decision_id": DECISION_ID,
        "entity_id": ENTITY,
        "canonical_opportunity_id": CANONICAL,
        "observation_id": OBSERVATION,
    }])


@pytest.fixture
def replay_env(tmp_path):
    _seed_retained_v1(
        tmp_path,
        ledger_decision="EXECUTE", ledger_reason="all_gates_passed",
        trace_pattern="ENGULFING_BULLISH", trace_structure_ok=True,
        trace_terminal_stage="execution",
    )
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(old)


def _engine() -> CausalReplayEngine:
    return CausalReplayEngine(_StubCausalAPI())


# ─── Test A — replay works with decision_audit absent ─────────────────────────

def test_replay_without_decision_audit_directory(replay_env):
    assert not (replay_env / "logs" / "decision_audit").exists()
    result = _engine().replay_trade(TRADE_ID)
    assert "error" not in result
    assert result["trade_id"] == TRADE_ID
    assert result["decision"] is not None  # sourced from retained authorities


# ─── Test B — authoritative terminal decision comes from the ledger ───────────

def test_terminal_decision_comes_from_ledger_not_trace(replay_env):
    result = _engine().replay_trade(TRADE_ID)
    decision = result["decision"]
    # Ledger says EXECUTE; trace deliberately says NO_TRADE. Ledger wins.
    assert decision["decision"] == "EXECUTE"
    assert decision["should_trade"] is True
    assert decision["reason"] == "all_gates_passed"
    assert decision["_sources"]["ledger"] is True


# ─── Test C — diagnostic reasoning comes from the trace ───────────────────────

def test_reasoning_comes_from_trace(replay_env):
    result = _engine().reconstruct_decision(TRADE_ID)
    audit_state = result["audit_state"]
    assert audit_state["should_trade"] is True                 # ledger authority
    assert audit_state["structure_ok"] is True                 # trace diagnostic
    assert audit_state["last_stage"] == "execution"            # trace terminal stage
    assert audit_state["patterns"] == ["ENGULFING_BULLISH"]    # trace pattern
    # engine_state has no retained V1 owner → explicitly unavailable, not fabricated.
    assert audit_state["engine_state"] == {}


# ─── Test D — lineage join survives the replay projection ─────────────────────

def test_lineage_join_survives_projection(replay_env):
    result = _engine().replay_trade(TRADE_ID)
    decision = result["decision"]
    assert decision["canonical_opportunity_id"] == CANONICAL
    assert decision["decision_id"] == DECISION_ID
    assert decision["observation_id"] == OBSERVATION
    assert decision["correlation_id"] == CORRELATION
    # The replay projection's top-level correlation/lineage is the same original.
    assert result["correlation_id"] == CORRELATION


# ─── Test E — no dependency on logs/decision_audit ────────────────────────────

def test_no_decision_audit_dependency(replay_env):
    # Absent directory is normal; replay + reconstruct both succeed.
    assert not (replay_env / "logs" / "decision_audit").exists()
    assert "error" not in _engine().replay_trade(TRADE_ID)
    assert "error" not in _engine().reconstruct_decision(TRADE_ID)


def test_load_decision_audit_symbol_no_longer_exists():
    """The retired reader must be gone from the research loaders module."""
    import research_engine.data_access.loaders as loaders
    assert not hasattr(loaders, "load_decision_audit")


def test_replay_module_has_no_decision_audit_reader():
    """core.causal.replay must not expose a decision_audit loader."""
    import core.causal.replay as replay
    assert not hasattr(replay, "_load_decision_audit")
    # The retained-authority loaders exist instead.
    assert hasattr(replay, "_load_decision_ledger_record")
    assert hasattr(replay, "_load_decision_trace_record")
