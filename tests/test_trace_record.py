"""
TraceRecord Tests — Observational trace model contract.

Verifies:
  - TraceRecord is frozen and immutable
  - TraceCollector accumulates records without side effects
  - TraceRecord does not influence pipeline decisions
  - Removing TraceRecord would not affect execution
"""

from __future__ import annotations

import time

import pytest

from core.pipeline.trace_record import TraceRecord, TraceCollector


class TestTraceRecordSchema:
    """TraceRecord model correctness."""

    def test_frozen_immutable(self):
        record = TraceRecord(symbol="EURUSD", timeframe="M5", stage="market_context", result="PASS")
        with pytest.raises(Exception):
            record.result = "REJECT"  # type: ignore

    def test_all_fields_populated(self):
        record = TraceRecord(
            symbol="GBPUSD",
            timeframe="M5",
            stage="scoring_engine",
            result="REJECT",
            reason="below_threshold",
        )
        assert record.symbol == "GBPUSD"
        assert record.timeframe == "M5"
        assert record.stage == "scoring_engine"
        assert record.result == "REJECT"
        assert record.reason == "below_threshold"

    def test_timestamp_auto_populated(self):
        before = time.monotonic()
        record = TraceRecord(symbol="X", timeframe="M5", stage="test", result="PASS")
        after = time.monotonic()
        assert before <= record.timestamp <= after

    def test_reason_optional(self):
        record = TraceRecord(symbol="X", timeframe="M5", stage="test", result="PASS")
        assert record.reason is None


class TestTraceCollector:
    """TraceCollector accumulation and reset."""

    def test_accumulates_records(self):
        tc = TraceCollector(symbol="EURUSD", timeframe="M5")
        tc.trace("market_context", "PASS")
        tc.trace("scoring_engine", "REJECT", "below_threshold")
        assert len(tc.records) == 2

    def test_records_are_ordered(self):
        tc = TraceCollector(symbol="X", timeframe="M5")
        tc.trace("stage_a", "PASS")
        tc.trace("stage_b", "REJECT", "reason_b")
        tc.trace("stage_c", "ALLOW")
        records = tc.records
        assert records[0].stage == "stage_a"
        assert records[1].stage == "stage_b"
        assert records[2].stage == "stage_c"

    def test_reset_clears(self):
        tc = TraceCollector(symbol="X", timeframe="M5")
        tc.trace("a", "PASS")
        tc.trace("b", "REJECT")
        assert len(tc.records) == 2
        tc.reset()
        assert len(tc.records) == 0

    def test_records_returns_copy(self):
        tc = TraceCollector(symbol="X", timeframe="M5")
        tc.trace("a", "PASS")
        records = tc.records
        records.append(TraceRecord(symbol="X", timeframe="M5", stage="fake", result="PASS"))
        # Internal state unchanged
        assert len(tc.records) == 1


class TestTraceRecordIsolation:
    """TraceRecord must not influence pipeline decisions."""

    def test_no_decision_imports(self):
        """trace_record.py must not import decision modules."""
        import pathlib
        source_path = pathlib.Path(__file__).parent.parent / "core" / "pipeline" / "trace_record.py"
        source = source_path.read_text(encoding="utf-8")

        forbidden = [
            "from core.pipeline.decision_engine",
            "from core.pipeline.scoring_engine",
            "from core.voters",
            "from core.engine_state",
            "FinishParams",
            "UnifiedDecision",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"trace_record.py imports decision module: {pattern}"
            )

    def test_no_consumers_in_pipeline(self):
        """No pipeline module should import TraceRecord for decision logic."""
        import pathlib
        root = pathlib.Path(__file__).parent.parent

        # These files must NOT import TraceRecord/TraceCollector
        forbidden_consumers = [
            "core/pipeline/scoring_engine.py",
            "core/pipeline/trade_quality.py",
            "core/pipeline/intent_builder.py",
            "core/voters/confluence_engine.py",
            "core/voters/execution_gate.py",
            "core/voters/risk_engine.py",
        ]

        for filepath in forbidden_consumers:
            full_path = root / filepath
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8")
            assert "TraceRecord" not in source, (
                f"{filepath} imports TraceRecord (forbidden consumer)"
            )
            assert "TraceCollector" not in source, (
                f"{filepath} imports TraceCollector (forbidden consumer)"
            )

    def test_engine_uses_trace_observationally(self):
        """engine.py uses TraceCollector but only for observation."""
        import pathlib
        engine_path = pathlib.Path(__file__).parent.parent / "core" / "engine.py"
        source = engine_path.read_text(encoding="utf-8")

        # Must import
        assert "TraceCollector" in source

        # Must NOT use trace results in conditionals
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "_trace." in stripped and ("if " in stripped or "elif " in stripped):
                pytest.fail(f"engine.py uses _trace in conditional at line {i}: {stripped}")
