from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.decision_ledger import DecisionOutcome, DecisionLedgerWriter
from core.decision_trace import build_decision_trace, persist_decision_trace
from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id
from core.persistence.execution_attempts_writer import persist_execution_attempt
from core.persistence.execution_result_writer import persist_execution_result
from core.persistence.opportunity_writer import persist_opportunity


SYMBOL = "USDCAD"
BAR_TIME = 1_788_151_500
PATTERN = "MEAN_REVERSION"
OBSERVATION_ID = mint_observation_id(symbol=SYMBOL, bar_time=BAR_TIME, timeframe="M5")
CANONICAL_OPPORTUNITY_ID = make_canonical_opportunity_id(
    symbol=SYMBOL,
    bar_time=BAR_TIME,
    pattern=PATTERN,
)
DECISION_ID = "decision-runtime-001"
CORRELATION_ID = "COR-20260831-99-USDCAD-ABCD"
V10_HASH = "deadbeef00112233"


def _read_one(root: Path) -> dict:
    files = list(root.rglob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def _pipeline_result() -> SimpleNamespace:
    return SimpleNamespace(
        market_state=SimpleNamespace(symbol=SYMBOL, timestamp_utc=float(BAR_TIME)),
        opportunity=SimpleNamespace(observation_id=V10_HASH),
    )


def test_canonical_lineage_persists_through_new_lifecycle_records(tmp_path, monkeypatch):
    import core.decision_trace as decision_trace_mod
    import core.persistence.execution_attempts_writer as attempts_mod
    import core.persistence.execution_result_writer as results_mod
    import core.persistence.opportunity_writer as opportunity_mod

    monkeypatch.setattr(opportunity_mod, "_LOCAL_DIR", str(tmp_path / "opportunities"))
    monkeypatch.setattr(decision_trace_mod, "_LOCAL_DIR", str(tmp_path / "decision_trace"))
    monkeypatch.setattr(attempts_mod, "_LOCAL_DIR", str(tmp_path / "execution_attempts"))
    monkeypatch.setattr(results_mod, "_LOCAL_DIR", str(tmp_path / "execution_results"))

    assert persist_opportunity(
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
        observation_id=OBSERVATION_ID,
        trigger_observation_id=OBSERVATION_ID,
        symbol=SYMBOL,
        bar_time=float(BAR_TIME),
        pattern=PATTERN,
        opportunity_state="VALID",
        directional_bias="BUY",
        opportunity_type=PATTERN,
        quality_overall=0.72,
        cycle_id=99,
        entity_id=f"{SYMBOL}_{BAR_TIME}",
    )

    trace = build_decision_trace(
        engine_result={
            "action": "EXECUTE",
            "symbol": SYMBOL,
            "cycle_id": 99,
            "pattern": PATTERN,
            "strategy": PATTERN,
            "entity_id": f"{SYMBOL}_{BAR_TIME}",
            "observation_id": OBSERVATION_ID,
            "canonical_opportunity_id": CANONICAL_OPPORTUNITY_ID,
            "decision_id": DECISION_ID,
            "correlation_id": CORRELATION_ID,
        },
        v10_pipeline_result=_pipeline_result(),
    )
    persist_decision_trace(trace)

    ledger = DecisionLedgerWriter(
        local_dir=str(tmp_path / "decision_ledger"),
        flush_batch_size=1,
    )
    ledger.record(
        symbol=SYMBOL,
        cycle_id=99,
        decision=DecisionOutcome.EXECUTE,
        reason="all_guards_passed",
        signal_score=0.72,
        signal_type=PATTERN,
        pattern_state="detected",
        context_snapshot_id=CORRELATION_ID,
        correlation_id=CORRELATION_ID,
        decision_id=DECISION_ID,
        entity_id=f"{SYMBOL}_{BAR_TIME}",
        observation_id=OBSERVATION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
    )

    assert persist_execution_attempt(
        attempt_id="attempt-001",
        decision_id=DECISION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
        observation_id=OBSERVATION_ID,
        correlation_id=CORRELATION_ID,
        trade_id="",
        symbol=SYMBOL,
        cycle_id=99,
        action_type="ENTRY",
        side="BUY",
        volume=0.1,
        entry_reference=1.25,
        broker_ok=True,
        retcode=10009,
    )
    persist_execution_result(
        symbol=SYMBOL,
        cycle_id=99,
        result_ok=True,
        retcode=10009,
        deal=123,
        order=456,
        comment="done",
        side="BUY",
        volume=0.1,
        entry_reference=1.25,
        pattern=PATTERN,
        decision_id=DECISION_ID,
        correlation_id=CORRELATION_ID,
        entity_id=f"{SYMBOL}_{BAR_TIME}",
        observation_id=OBSERVATION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
    )

    records = {
        "opportunity": _read_one(tmp_path / "opportunities"),
        "decision_trace": _read_one(tmp_path / "decision_trace"),
        "decision_ledger": _read_one(tmp_path / "decision_ledger"),
        "execution_attempt": _read_one(tmp_path / "execution_attempts"),
        "execution_result": _read_one(tmp_path / "execution_results"),
    }

    for record in records.values():
        assert record["observation_id"] == OBSERVATION_ID
        assert record["observation_id"] != V10_HASH
        assert record["observation_id"] != CANONICAL_OPPORTUNITY_ID
        assert record["canonical_opportunity_id"] == CANONICAL_OPPORTUNITY_ID

    for name in ("decision_trace", "decision_ledger", "execution_attempt", "execution_result"):
        assert records[name]["decision_id"] == DECISION_ID
        assert records[name]["correlation_id"] == CORRELATION_ID

    assert records["decision_trace"]["v10_observation_id"] == V10_HASH
    assert records["decision_trace"]["v10_correlation_id"].startswith("v10_USDCAD_")
    assert records["execution_attempt"]["trade_id"] is None


def test_pre_opportunity_decision_does_not_fabricate_canonical_opportunity_id(tmp_path):
    ledger = DecisionLedgerWriter(
        local_dir=str(tmp_path / "decision_ledger"),
        flush_batch_size=1,
    )
    ledger.record(
        symbol=SYMBOL,
        cycle_id=100,
        decision=DecisionOutcome.NO_TRADE,
        reason="pre_engine_gate",
        context_snapshot_id=CORRELATION_ID,
        correlation_id=CORRELATION_ID,
        decision_id=DECISION_ID,
        observation_id=OBSERVATION_ID,
        canonical_opportunity_id="",
    )

    record = _read_one(tmp_path / "decision_ledger")
    assert record["observation_id"] == OBSERVATION_ID
    assert record["decision_id"] == DECISION_ID
    assert record["correlation_id"] == CORRELATION_ID
    assert record["canonical_opportunity_id"] == ""
