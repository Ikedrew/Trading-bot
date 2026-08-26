"""
Phase 1 data-capture regression — canonical lineage on the EXECUTION boundary.

Implemented gap (LIVE_SHADOW_DATA_CAPTURE_CONTRACT.md §3/G1):
    ExecutionOrchestrator.execute_trade() -> persist_execution_result()
    previously persisted the PRIMARY execution-result row WITHOUT
    canonical_opportunity_id (the protection-verification secondary row
    already carried it). This suite pins the completed propagation.

Proven here (mock broker; temporary directory sink; never touches logs/):
      - Case D (persistence): execute_trade forwards canonical_opportunity_id
        into the real writer -> JSONL row in a tmp dir contains EXACTLY the
        originating canonical root (ID_A vs ID_B kept distinct).
      - Schema safety: calling without the new param still writes a valid row
        (empty lineage) — historical/compat callers unchanged.
      - No fabrication: when the caller passes an empty root (lineage not
        established), the row records an empty string — never a stale or
        invented ID.
      - Static AST guards pin both ends of the propagation.

No bot start, no MT5 connection, no orders placed (broker module is fully
mocked; order_send is never reached).
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


def _make_intent():
    intent = MagicMock()
    intent.side = SimpleNamespace(name="BUY")
    intent.volume = 0.01
    intent.entry_reference = 1.2300
    intent.sl = 1.2250
    intent.tp = 1.2400
    intent.pattern = "TWEEZER_TOP"
    return intent


def _make_config():
    cfg = MagicMock()
    cfg.discord_logger = None
    cfg._discord_logger = None
    cfg.EXECUTION_ENABLED = True
    return cfg


class SimpleResult:
    def __init__(self, *, ok, retcode, deal, order, comment, fill_price):
        self.ok = ok
        self.retcode = retcode
        self.deal = deal
        self.order = order
        self.comment = comment
        self.fill_price = fill_price
        self.price = fill_price
        self.volume = 0.01


def _make_exec_ok():
    """Mock MT5Execution returning a successful fill — no broker touched."""
    ex = MagicMock()
    ex.execute.return_value = SimpleResult(
        ok=True, retcode=10009, deal=111, order=222,
        comment="done", fill_price=1.2305,
    )
    return ex


def _read_rows(local_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(local_dir.rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


@pytest.fixture
def isolated_writer(monkeypatch, tmp_path):
    """Redirect the REAL execution-result writer to a tmp dir; S3 disabled."""
    import core.persistence.execution_result_writer as w

    local_dir = tmp_path / "exec_results"
    monkeypatch.setattr(w, "_LOCAL_DIR", str(local_dir))
    monkeypatch.setattr(w, "_write_s3", lambda *a, **k: None)
    return local_dir


class TestCaseD_ExecutionResultCarriesCanonicalRoot:
    def test_primary_row_persists_the_originating_root(self, isolated_writer):
        from execution.execution_orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator(_make_exec_ok(), _make_config())
        outcome = orch.execute_trade(
            intent=_make_intent(),
            symbol="EURUSD",
            cycle_id=42,
            decision_id="DEC-1",
            correlation_id="COR-TEST-42-EURUSD",
            entity_id="EURUSD_1784800000",
            observation_id="",
            canonical_opportunity_id=ID_A,
            mt5_state="CONNECTED",
        )
        assert outcome.executed and outcome.ok

        rows = _read_rows(isolated_writer)
        assert len(rows) == 1
        assert rows[0]["canonical_opportunity_id"] == ID_A     # Case D
        assert rows[0]["decision_id"] == "DEC-1"
        assert rows[0]["entity_id"] == "EURUSD_1784800000"

    def test_consecutive_executions_keep_roots_distinct(self, isolated_writer):
        """Case B at the execution boundary: ID_A and ID_B never blend."""
        from execution.execution_orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator(_make_exec_ok(), _make_config())
        orch.execute_trade(
            intent=_make_intent(), symbol="EURUSD", cycle_id=1,
            decision_id="D1", correlation_id="C1", entity_id="E1",
            canonical_opportunity_id=ID_A, mt5_state="CONNECTED",
        )
        orch.execute_trade(
            intent=_make_intent(), symbol="GBPUSD", cycle_id=2,
            decision_id="D2", correlation_id="C2", entity_id="E2",
            canonical_opportunity_id=ID_B, mt5_state="CONNECTED",
        )
        roots = [r["canonical_opportunity_id"] for r in _read_rows(isolated_writer)]
        assert roots == [ID_A, ID_B]
        assert len(set(roots)) == 2


class TestSchemaAndSafetyPreservation:
    def test_missing_param_still_writes_valid_row_empty_lineage(
        self, isolated_writer,
    ):
        """Backward compatibility: legacy callers omit the new kwarg."""
        from execution.execution_orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator(_make_exec_ok(), _make_config())
        orch.execute_trade(
            intent=_make_intent(), symbol="EURUSD", cycle_id=9,
            decision_id="D9", correlation_id="C9", entity_id="E9",
            mt5_state="CONNECTED",
        )
        row = _read_rows(isolated_writer)[0]
        assert row["canonical_opportunity_id"] == ""
        assert row["schema_version"] == "execution_results_v1"

    def test_no_fabrication_when_lineage_not_established(self, isolated_writer):
        """
        Case E analogue at the LIVE boundary: an empty root must be recorded
        as empty — never inherited, never invented.
        """
        from execution.execution_orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator(_make_exec_ok(), _make_config())
        orch.execute_trade(
            intent=_make_intent(), symbol="EURUSD", cycle_id=10,
            decision_id="D10", correlation_id="C10", entity_id="E10",
            canonical_opportunity_id="",   # pre-pattern gate territory
            mt5_state="CONNECTED",
        )
        assert _read_rows(isolated_writer)[0]["canonical_opportunity_id"] == ""

    def test_failed_broker_call_persists_nothing_new_here(self, isolated_writer):
        """execute() raising => ExecutionOutcome(executed=False); no row is
        written by this path (writer step only runs after a broker call)."""
        from execution.execution_orchestrator import ExecutionOrchestrator

        ex = MagicMock()
        ex.execute.side_effect = RuntimeError("boom")
        orch = ExecutionOrchestrator(ex, _make_config())
        outcome = orch.execute_trade(
            intent=_make_intent(), symbol="EURUSD", cycle_id=11,
            decision_id="D11", correlation_id="C11", entity_id="E11",
            canonical_opportunity_id=ID_A, mt5_state="CONNECTED",
        )
        assert not outcome.executed
        assert _read_rows(isolated_writer) == []


class TestStaticPropagationGuard:
    def test_live_scanner_forwards_canonical_to_execute_trade(self):
        """AST guard: live_scanner's execute_trade call passes the kwarg."""
        import ast

        src = Path("core/runtime/live_scanner.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(
                node.func, "attr", ""
            ) == "execute_trade":
                kws = {k.arg for k in node.keywords}
                assert "canonical_opportunity_id" in kws, (
                    "live_scanner must forward canonical_opportunity_id "
                    "to execute_trade (Phase 1 data capture)"
                )
                found = True
        assert found, "execute_trade call site missing?"

    def test_orchestrator_signature_and_forwarding_intact(self):
        src = Path("execution/execution_orchestrator.py").read_text(encoding="utf-8-sig")
        assert "canonical_opportunity_id: str =" in src.replace("\r\n", "\n") or \
               "canonical_opportunity_id: str" in src
        # forwarded into the primary persistence call, not dropped
        body = src.split("persist_execution_result(")[1]
        assert "canonical_opportunity_id=canonical_opportunity_id" in body
