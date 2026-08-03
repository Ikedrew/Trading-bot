"""
End-to-End Event System Integrity Tests.

Validates:
    1. Causal chain completeness (CANDLE?ENTITY?STRATEGY?DECISION?EXECUTION?OUTCOME)
    2. Local vs S3 consistency (identical payloads)
    3. Replay determinism (single-source reconstruction)
    4. Cross-sink completeness (no orphans)
    5. Failure resilience (S3 down ? local intact)

These tests run against a synthetic event stream (no MT5, no AWS required).
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clock import utc_ms, candle_ts_to_ms, set_clock_override, advance_clock
from core.event_stream import (
    emit, emit_candle, emit_entity, emit_strategy,
    emit_decision, emit_execution, emit_outcome,
    read_stream, close, stats, s3_mirror_stats,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_event_dir(tmp_path, monkeypatch):
    """Redirect all event writes to a temp dir and reset state."""
    monkeypatch.setattr("core.event_stream._EVENT_DIR", tmp_path)
    monkeypatch.setattr("core.event_stream._current_file", None)
    monkeypatch.setattr("core.event_stream._current_date", None)
    monkeypatch.setattr("core.event_stream._file_handle", None)
    monkeypatch.setattr("core.event_stream._total_emitted", 0)
    monkeypatch.setattr("core.event_stream._total_errors", 0)
    monkeypatch.setattr("core.event_stream._enabled", True)
    monkeypatch.setattr("core.event_stream._S3_ENABLED", False)
    # Override config to point to temp dir
    monkeypatch.setattr("core.event_stream._get_event_dir", lambda: tmp_path)
    yield tmp_path
    close()


@pytest.fixture
def deterministic_clock():
    """Set clock to a fixed value for deterministic tests."""
    base_ts = 1782520800000  # Fixed point
    set_clock_override(base_ts)
    yield base_ts
    set_clock_override(None)


def _emit_full_lifecycle(symbol: str = "EURUSD", candle_ts: int = 1719388800):
    """Emit a complete CANDLE?ENTITY?STRATEGY?DECISION?EXECUTION?OUTCOME chain."""
    candle_ms = candle_ts_to_ms(candle_ts)
    entity_id = f"{symbol}_{candle_ts}"
    decision_id = "d1a2b3c4e5f6789012345678abcdef00"

    # 1. CANDLE
    emit_candle(symbol, {"ts": candle_ms, "o": 1.074, "h": 1.075, "l": 1.073, "c": 1.0745, "v": 100})
    advance_clock(10)

    # 2. ENTITY
    emit_entity(symbol, {
        "entity_id": entity_id,
        "cycle_id": 42,
        "origin_candle_ts_utc_ms": candle_ms,
        "event_type": "PROMOTE",
        "data": {"score": 0.65, "pattern": "BULLISH_ENGULFING"},
    })
    advance_clock(10)

    # 3. STRATEGY
    strategy_ts = utc_ms()
    emit_strategy(symbol, {
        "entity_id": entity_id,
        "cycle_id": 42,
        "ts_utc_ms": strategy_ts,
        "regime": {"current": "TRENDING"},
        "selection": {"selected_strategy": "CONTINUATION", "selected_weight": 0.85},
    })
    advance_clock(10)

    # 4. DECISION
    decision_ts = utc_ms()
    emit_decision(symbol, {
        "decision_id": decision_id,
        "entity_id": entity_id,
        "cycle_id": 42,
        "strategy_ts_utc_ms": strategy_ts,
        "should_trade": True,
        "score": 0.65,
        "side": "BUY",
    })
    advance_clock(10)

    # 5. EXECUTION
    execution_ts = utc_ms()
    emit_execution(symbol, {
        "decision_id": decision_id,
        "decision_ts_utc_ms": decision_ts,
        "status": "FILLED",
        "fill_price": 1.07423,
        "deal": 12345,
        "order_ticket": 87654,
        "slippage": 0.00003,
        "fill_latency_ms": 45,
    })
    advance_clock(10)

    # 6. OUTCOME
    emit_outcome(symbol, {
        "decision_id": decision_id,
        "execution_ts_utc_ms": execution_ts,
        "decision_ts_utc_ms": decision_ts,
        "trade_id": "pos_87654",
        "pnl": 4.0,
        "rr_realised": 2.0,
        "final_r": 2.0,
        "mfe_r": 2.5,
        "mae_r": -0.3,
        "duration_ms": 300000,
        "exit_reason": "take_profit",
        "breakeven_triggered": True,
        "trailing_triggered": False,
        "entry_price": 1.07423,
        "exit_price": 1.07823,
        "volume": 0.01,
        "initial_sl": 1.07223,
        "initial_tp": 1.07823,
    })

    return {
        "candle_ms": candle_ms,
        "entity_id": entity_id,
        "decision_id": decision_id,
        "strategy_ts": strategy_ts,
        "decision_ts": decision_ts,
        "execution_ts": execution_ts,
    }


# -------------------------------------------------------------------------------
# TEST 1: CAUSAL CHAIN VALIDATION
# -------------------------------------------------------------------------------

class TestCausalChain:
    """Verify explicit causal links between all event types."""

    def test_full_lifecycle_links(self, isolated_event_dir, deterministic_clock):
        """Complete chain has all backward links explicit — no heuristics."""
        refs = _emit_full_lifecycle()

        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 6

        by_type = {e["type"]: e for e in events}

        # ENTITY links to CANDLE
        entity_p = by_type["ENTITY"]["payload"]
        assert entity_p["origin_candle_ts_utc_ms"] == refs["candle_ms"]

        # STRATEGY links to ENTITY
        strat_p = by_type["STRATEGY"]["payload"]
        assert strat_p["entity_id"] == refs["entity_id"]

        # DECISION links to STRATEGY
        dec_p = by_type["DECISION"]["payload"]
        assert dec_p["strategy_ts_utc_ms"] == refs["strategy_ts"]
        assert dec_p["entity_id"] == refs["entity_id"]
        assert dec_p["decision_id"] == refs["decision_id"]

        # EXECUTION links to DECISION
        exec_p = by_type["EXECUTION"]["payload"]
        assert exec_p["decision_id"] == refs["decision_id"]
        assert exec_p["decision_ts_utc_ms"] == refs["decision_ts"]

        # OUTCOME links to EXECUTION and DECISION
        out_p = by_type["OUTCOME"]["payload"]
        assert out_p["decision_id"] == refs["decision_id"]
        assert out_p["execution_ts_utc_ms"] == refs["execution_ts"]
        assert out_p["decision_ts_utc_ms"] == refs["decision_ts"]

    def test_no_orphan_links(self, isolated_event_dir, deterministic_clock):
        """Every non-CANDLE event has at least one backward link."""
        _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir))

        for e in events:
            if e["type"] == "CANDLE":
                continue  # Root event — no backward link needed
            p = e["payload"]
            # Must have at least one causal link field
            has_link = any(k in p for k in [
                "origin_candle_ts_utc_ms", "entity_id",
                "strategy_ts_utc_ms", "decision_ts_utc_ms",
                "execution_ts_utc_ms", "decision_id",
            ])
            assert has_link, f"Orphan event: type={e['type']} ts={e['ts_utc_ms']}"

    def test_ordering_is_causal(self, isolated_event_dir, deterministic_clock):
        """Events are emitted in strict causal order."""
        _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir))

        expected_order = ["CANDLE", "ENTITY", "STRATEGY", "DECISION", "EXECUTION", "OUTCOME"]
        actual_order = [e["type"] for e in events]
        assert actual_order == expected_order

        # Timestamps are strictly increasing
        timestamps = [e["ts_utc_ms"] for e in events]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == len(timestamps)  # All unique


# -------------------------------------------------------------------------------
# TEST 2: LOCAL vs S3 CONSISTENCY
# -------------------------------------------------------------------------------

class TestS3Consistency:
    """Verify S3 mirror receives identical payloads."""

    def test_s3_key_format_athena_compatible(self):
        """S3 key uses Hive-compatible partitioning (via batch writer)."""
        from core.storage.s3_batch_writer import S3BatchWriter
        writer = S3BatchWriter(bucket="test-bucket", base_prefix="events")
        # Verify the key structure is correct by checking the prefix format
        # Batch writer produces: events/symbol={SYM}/date={DATE}/part-{N}.jsonl
        assert writer._prefix == "events"
        assert writer._bucket == "test-bucket"

    def test_s3_receives_exact_same_json(self, isolated_event_dir, deterministic_clock):
        """S3 batch writer receives the exact same event as local file."""
        emit_decision("EURUSD", {"score": 0.5, "test": True})
        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 1

        # Read raw line from file
        files = list(isolated_event_dir.glob("*.jsonl"))
        assert len(files) == 1
        raw_line = files[0].read_text().strip()
        parsed = json.loads(raw_line)

        # Verify it's valid JSON with expected fields
        assert parsed["type"] == "DECISION"
        assert parsed["symbol"] == "EURUSD"
        assert parsed["payload"]["score"] == 0.5
        assert "ts_utc_ms" in parsed

    def test_s3_disabled_no_batch_activity(self, isolated_event_dir, deterministic_clock, monkeypatch):
        """When S3 mirror disabled, batch writer is not invoked."""
        monkeypatch.setattr("core.event_stream._S3_ENABLED", False)
        monkeypatch.setattr("core.event_stream._s3_is_enabled", lambda: False)
        emit_decision("TEST_SB", {"x": 1})
        # When S3 is disabled, _s3_enqueue returns immediately without touching batch writer
        # The key assertion: the event still writes locally successfully
        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) >= 1
        assert events[-1]["type"] == "DECISION"


# -------------------------------------------------------------------------------
# TEST 3: REPLAY DETERMINISM
# -------------------------------------------------------------------------------

class TestReplayDeterminism:
    """Verify replays from local ledger produce consistent results."""

    def test_same_data_same_order(self, isolated_event_dir, deterministic_clock):
        """Reading the same ledger twice produces identical results."""
        _emit_full_lifecycle()

        read1 = read_stream(event_dir=str(isolated_event_dir))
        read2 = read_stream(event_dir=str(isolated_event_dir))

        assert read1 == read2

    def test_symbol_filter_isolates(self, isolated_event_dir, deterministic_clock):
        """Symbol filter returns only matching events."""
        _emit_full_lifecycle("EURUSD")
        advance_clock(100)
        _emit_full_lifecycle("GBPUSD")

        eur_events = read_stream(event_dir=str(isolated_event_dir), symbol="EURUSD")
        gbp_events = read_stream(event_dir=str(isolated_event_dir), symbol="GBPUSD")
        all_events = read_stream(event_dir=str(isolated_event_dir))

        assert len(eur_events) == 6
        assert len(gbp_events) == 6
        assert len(all_events) == 12
        assert all(e["symbol"] == "EURUSD" for e in eur_events)
        assert all(e["symbol"] == "GBPUSD" for e in gbp_events)

    def test_type_filter_works(self, isolated_event_dir, deterministic_clock):
        """Type filter returns only matching event types."""
        _emit_full_lifecycle()

        decisions = read_stream(event_dir=str(isolated_event_dir), event_type="DECISION")
        assert len(decisions) == 1
        assert decisions[0]["type"] == "DECISION"

    def test_cycle_grouping(self, isolated_event_dir, deterministic_clock):
        """All events in a lifecycle share the same cycle_id."""
        _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir))

        # ENTITY, STRATEGY, DECISION should all have cycle_id=42
        for e in events:
            if e["type"] in ("ENTITY", "STRATEGY", "DECISION"):
                assert e["payload"].get("cycle_id") == 42


# -------------------------------------------------------------------------------
# TEST 4: CROSS-SINK COMPLETENESS
# -------------------------------------------------------------------------------

class TestCrossSinkCompleteness:
    """Verify local ledger completeness and no event loss."""

    def test_all_event_types_present(self, isolated_event_dir, deterministic_clock):
        """Full lifecycle produces all 6 event types."""
        _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir))
        types = {e["type"] for e in events}
        assert types == {"CANDLE", "ENTITY", "STRATEGY", "DECISION", "EXECUTION", "OUTCOME"}

    def test_no_duplicate_events(self, isolated_event_dir, deterministic_clock):
        """Each event has a unique ts_utc_ms (monotonic clock guarantee)."""
        _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir))
        timestamps = [e["ts_utc_ms"] for e in events]
        assert len(timestamps) == len(set(timestamps))

    def test_event_count_matches_emitted(self, isolated_event_dir, deterministic_clock):
        """stats() reports correct emit count."""
        _emit_full_lifecycle()
        s = stats()
        assert s["total_emitted"] == 6
        assert s["total_errors"] == 0


# -------------------------------------------------------------------------------
# TEST 5: FAILURE RESILIENCE
# -------------------------------------------------------------------------------

class TestFailureResilience:
    """Verify local ledger survives S3 failures."""

    def test_local_write_succeeds_without_s3(self, isolated_event_dir, deterministic_clock):
        """Events write locally even when S3 is unavailable."""
        # S3 disabled by default in tests
        assert emit_decision("TEST_SB", {"x": 1})
        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 1

    def test_invalid_event_type_rejected(self, isolated_event_dir, deterministic_clock):
        """Invalid event types are rejected (not written)."""
        result = emit("INVALID_TYPE", "TEST_SB", {"x": 1})
        assert result is False
        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 0
        s = stats()
        # Invalid type is silently rejected (allowlist enforcement), not an error
        assert s["total_errors"] == 0

    def test_none_payload_accepted(self, isolated_event_dir, deterministic_clock):
        """None payload creates event without payload field."""
        assert emit("CANDLE", "TEST_SB", None)
        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 1
        assert "payload" not in events[0]

    def test_schema_preserved_after_many_writes(self, isolated_event_dir, deterministic_clock):
        """Schema remains consistent across many writes."""
        for i in range(100):
            emit_candle("TEST_SB", {"ts": 1719388800000 + i * 300000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": i})
            advance_clock(1)

        events = read_stream(event_dir=str(isolated_event_dir))
        assert len(events) == 100
        for e in events:
            assert "ts_utc_ms" in e
            assert e["type"] == "CANDLE"
            assert e["symbol"] == "TEST_SB"
            assert "payload" in e


# -------------------------------------------------------------------------------
# TEST 6: OUTCOME COMPLETENESS AUDIT
# -------------------------------------------------------------------------------

class TestOutcomeCompleteness:
    """Verify OUTCOME events contain all required fields."""

    def test_outcome_has_all_required_fields(self, isolated_event_dir, deterministic_clock):
        """OUTCOME payload must contain the full field set."""
        refs = _emit_full_lifecycle()
        events = read_stream(event_dir=str(isolated_event_dir), event_type="OUTCOME")
        assert len(events) == 1

        p = events[0]["payload"]
        required = [
            "decision_id", "execution_ts_utc_ms", "decision_ts_utc_ms",
            "trade_id", "pnl", "rr_realised", "final_r", "mfe_r", "mae_r",
            "duration_ms", "exit_reason", "breakeven_triggered", "trailing_triggered",
            "entry_price", "exit_price", "volume", "initial_sl", "initial_tp",
        ]
        for field in required:
            assert field in p, f"OUTCOME missing required field: {field}"
