from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id


SYMBOL = "USDCAD"
BAR_TIME = 1_788_151_500
PATTERN = "MEAN_REVERSION"
OBSERVATION_ID = mint_observation_id(symbol=SYMBOL, bar_time=BAR_TIME, timeframe="M5")
CANONICAL_OPPORTUNITY_ID = make_canonical_opportunity_id(
    symbol=SYMBOL,
    bar_time=BAR_TIME,
    pattern=PATTERN,
)
DECISION_ID = "93eab925eec800000000000000000000"
CORRELATION_ID = "COR-20260831-1-USDCAD-AE4D"
V10_OBSERVATION_ID = "be7b62eabad8cd9a"


def _rows(path: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted(path.rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _pipeline_result() -> SimpleNamespace:
    return SimpleNamespace(
        market_state=SimpleNamespace(
            symbol=SYMBOL,
            timestamp_utc=float(BAR_TIME),
            h4=SimpleNamespace(
                trend="", trend_strength=0.0, market_phase="", structure_type="",
                swing_high=0.0, swing_low=0.0, last_bos_direction="",
                atr=0.0, volatility_state="",
            ),
            h1=SimpleNamespace(
                dominant_trend="", structural_clarity=0.0, bos_confirmed=False,
                bos_direction="", choch_detected=False, choch_direction="",
                swing_high=0.0, swing_low=0.0,
            ),
            m15=SimpleNamespace(
                pullback_active=False, displacement_present=False,
                displacement_direction="", range_position="", internal_bos=False,
                internal_bos_direction="",
            ),
            m5=SimpleNamespace(
                pattern=PATTERN, pattern_confidence=0.8, micro_structure="",
                momentum_direction="SELL", momentum_strength=0.0,
            ),
            regime=SimpleNamespace(regime="", confidence=0.0, session=""),
            location=SimpleNamespace(location_type="", distance_to_key_level_pips=0.0),
            htf_alignment=SimpleNamespace(macro_bias="", alignment_score=0.0),
        ),
        opportunity=SimpleNamespace(
            observation_id=V10_OBSERVATION_ID,
            opportunity_state="VALID",
            opportunity_type=PATTERN,
            quality_score=0.7,
            location_score=0.7,
            structure_score=0.7,
            behaviour_score=0.7,
            formation_score=0.7,
            reasoning=[],
        ),
        strategy=SimpleNamespace(
            strategy_family="MEAN_REVERSION",
            confidence=0.7,
            direction="SELL",
            reasoning=[],
        ),
        horizon=SimpleNamespace(
            horizon_type="SCALP",
            max_adverse_excursion_pips=10.0,
            max_favourable_excursion_pips=20.0,
            duration_minutes=30.0,
            reasoning=[],
        ),
        entry=SimpleNamespace(
            entry_method="MARKET",
            entry_price=1.3888,
            stop_loss=1.3898,
            target_price=1.3868,
            expected_rr=2.0,
            reasoning=[],
        ),
        risk=SimpleNamespace(position_size=0.01, risk_percentage=1.0, reasoning=[]),
        execution=SimpleNamespace(
            executable=True,
            reason="",
            order_type="MARKET",
            volume=0.01,
            reasoning=[],
        ),
        account_snapshot=None,
        broker_snapshot=None,
    )


def test_canonical_lifecycle_lineage_persists_distinct_ids(monkeypatch, tmp_path):
    import core.decision_trace as trace_writer
    import core.persistence.execution_attempts_writer as attempts_writer
    import core.persistence.execution_result_writer as results_writer
    import core.persistence.opportunity_writer as opportunity_writer

    monkeypatch.setattr(opportunity_writer, "_LOCAL_DIR", str(tmp_path / "opportunities"))
    monkeypatch.setattr(trace_writer, "_LOCAL_DIR", str(tmp_path / "decision_trace"))
    monkeypatch.setattr(attempts_writer, "_LOCAL_DIR", str(tmp_path / "execution_attempts"))
    monkeypatch.setattr(results_writer, "_LOCAL_DIR", str(tmp_path / "execution_results"))
    monkeypatch.setattr(opportunity_writer, "_write_s3", lambda *a, **k: None)
    monkeypatch.setattr(trace_writer, "_write_s3", lambda *a, **k: None)
    monkeypatch.setattr(attempts_writer, "_write_s3", lambda *a, **k: None)
    monkeypatch.setattr(results_writer, "_write_s3", lambda *a, **k: None)

    assert opportunity_writer.persist_opportunity(
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
        observation_id=OBSERVATION_ID,
        trigger_observation_id=OBSERVATION_ID,
        symbol=SYMBOL,
        bar_time=float(BAR_TIME),
        pattern=PATTERN,
    )

    from core.decision_trace import build_decision_trace, persist_decision_trace

    trace = build_decision_trace(
        engine_result={
            "symbol": SYMBOL,
            "cycle_id": 1,
            "action": "EXECUTE",
            "pattern": PATTERN,
            "canonical_opportunity_id": CANONICAL_OPPORTUNITY_ID,
            "observation_id": OBSERVATION_ID,
            "decision_id": DECISION_ID,
            "correlation_id": CORRELATION_ID,
        },
        v10_pipeline_result=_pipeline_result(),
        observation_id=OBSERVATION_ID,
        decision_id=DECISION_ID,
        correlation_id=CORRELATION_ID,
    )
    persist_decision_trace(trace)

    from core.decision_ledger import DecisionOutcome, build_ledger_entry

    ledger = build_ledger_entry(
        symbol=SYMBOL,
        cycle_id=1,
        decision=DecisionOutcome.EXECUTE,
        reason="approved",
        context_snapshot_id=CORRELATION_ID,
        correlation_id=CORRELATION_ID,
        decision_id=DECISION_ID,
        observation_id=OBSERVATION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
    )

    assert attempts_writer.persist_execution_attempt(
        attempt_id="attempt-1",
        decision_id=DECISION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
        observation_id=OBSERVATION_ID,
        correlation_id=CORRELATION_ID,
        trade_id="",
        symbol=SYMBOL,
        cycle_id=1,
        action_type="ENTRY",
    )
    results_writer.persist_execution_result(
        symbol=SYMBOL,
        cycle_id=1,
        result_ok=True,
        retcode=10009,
        deal=123,
        order=456,
        comment="done",
        decision_id=DECISION_ID,
        correlation_id=CORRELATION_ID,
        observation_id=OBSERVATION_ID,
        canonical_opportunity_id=CANONICAL_OPPORTUNITY_ID,
    )

    records = [
        _rows(tmp_path / "opportunities")[0],
        _rows(tmp_path / "decision_trace")[0],
        ledger,
        _rows(tmp_path / "execution_attempts")[0],
        _rows(tmp_path / "execution_results")[0],
    ]

    for rec in records:
        assert rec["observation_id"] == OBSERVATION_ID
        assert rec["observation_id"] != CANONICAL_OPPORTUNITY_ID
        assert rec["canonical_opportunity_id"] == CANONICAL_OPPORTUNITY_ID

    for rec in records[1:]:
        assert rec["decision_id"] == DECISION_ID
        assert rec["correlation_id"] == CORRELATION_ID

    assert records[1]["v10_observation_id"] == V10_OBSERVATION_ID
    assert records[1]["v10_observation_id"] != records[1]["observation_id"]
    assert records[3]["trade_id"] is None


def test_shadow_recovery_keeps_open_observation_id_into_progress_and_close(tmp_path):
    from core.shadow.persistence import ShadowEventWriter, load_events
    from core.shadow.runtime import ShadowRuntime

    writer = ShadowEventWriter(base_dir=str(tmp_path))
    rt = ShadowRuntime(writer=writer)
    ctx = {
        "canonical_opportunity_id": CANONICAL_OPPORTUNITY_ID,
        "observation_id": OBSERVATION_ID,
        "entity_id": f"{SYMBOL}_{BAR_TIME}",
        "symbol": SYMBOL,
        "cycle_id": 1,
        "bar_time_raw": BAR_TIME,
        "direction": "SELL",
        "pattern": PATTERN,
        "strategy": "MEAN_REVERSION",
        "score": 0.7,
        "bid": 1.3888,
        "ask": 1.3889,
        "eligible_horizons": ["INTRADAY"],
        "structure": {
            "m5_candle_high": 1.3890,
            "m5_candle_low": 1.3880,
            "m15_nearest_resistance": 1.3898,
            "m15_nearest_support": None,
        },
        "horizon_assessments": [],
    }
    rt.handle_opportunity(ctx)

    rt2 = ShadowRuntime(writer=writer)
    tid = rt2.active_ids()[0]
    assert rt2.snapshot(tid)["observation_id"] == OBSERVATION_ID
    open_event = [e for e in load_events(str(tmp_path)) if e["event_type"] == "OPEN"][0]
    construction = open_event["construction"]

    for i in range(1, 13):
        rt2.evaluate_bar(
            symbol=SYMBOL,
            bar_time=BAR_TIME + i * 300,
            bar_high=1.3889,
            bar_low=1.3884,
            bar_close=1.3886,
        )
    rt2.evaluate_bar(
        symbol=SYMBOL,
        bar_time=BAR_TIME + 13 * 300,
        bar_high=float(construction["entry_price"]),
        bar_low=float(construction["take_profit"]) - 0.001,
        bar_close=float(construction["take_profit"]),
    )

    events = load_events(str(tmp_path))
    for event_type in ("OPEN", "PROGRESS", "CLOSE"):
        row = [e for e in events if e["event_type"] == event_type][-1]
        assert row["observation_id"] == OBSERVATION_ID
        assert row["canonical_opportunity_id"] == CANONICAL_OPPORTUNITY_ID
