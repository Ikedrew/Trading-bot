"""
PipelineAuthority Tests — Decision ledger contract enforcement.

Verifies:
  - DecisionRecord schema correctness
  - PipelineAuthority interface (allow/reject/record)
  - Decision traceability
  - Reset behaviour
  - Integration with engine.py decision points
"""

from __future__ import annotations

import time

import pytest

from core.pipeline.pipeline_authority import (
    DecisionRecord,
    PipelineAuthority,
    VALID_STAGES,
)


class TestDecisionRecord:
    """DecisionRecord schema correctness."""

    def test_frozen_immutable(self):
        record = DecisionRecord(action="REJECT", stage="market_context", reason="closed")
        with pytest.raises(Exception):
            record.action = "ALLOW"  # type: ignore

    def test_default_metadata_empty(self):
        record = DecisionRecord(action="ALLOW", stage="complete")
        assert record.metadata == {}

    def test_timestamp_auto_populated(self):
        before = time.monotonic()
        record = DecisionRecord(action="ALLOW", stage="complete")
        after = time.monotonic()
        assert before <= record.timestamp <= after

    def test_all_fields_accessible(self):
        record = DecisionRecord(
            action="REJECT",
            stage="scoring_engine",
            reason="below_threshold",
            metadata={"score": 3.5},
        )
        assert record.action == "REJECT"
        assert record.stage == "scoring_engine"
        assert record.reason == "below_threshold"
        assert record.metadata == {"score": 3.5}


class TestPipelineAuthorityInterface:
    """PipelineAuthority allow/reject/record interface."""

    def test_reject_returns_record(self):
        auth = PipelineAuthority(symbol="TEST")
        record = auth.reject("market_context", "session_closed", {"hour": 22})
        assert record.action == "REJECT"
        assert record.stage == "market_context"
        assert record.reason == "session_closed"
        assert record.metadata == {"hour": 22}

    def test_allow_returns_record(self):
        auth = PipelineAuthority(symbol="TEST")
        record = auth.allow("complete", {"score": 7})
        assert record.action == "ALLOW"
        assert record.stage == "complete"
        assert record.reason is None
        assert record.metadata == {"score": 7}

    def test_record_returns_observe(self):
        auth = PipelineAuthority(symbol="TEST")
        record = auth.record("structure_scoring", "regime_updated", {"regime": "CONFIRMED"})
        assert record.action == "OBSERVE"
        assert record.stage == "structure_scoring"
        assert record.reason == "regime_updated"

    def test_symbol_stored(self):
        auth = PipelineAuthority(symbol="EURUSD")
        assert auth.symbol == "EURUSD"


class TestDecisionTraceability:
    """Full pipeline traceability — every decision is recorded."""

    def test_decision_trace_ordered(self):
        auth = PipelineAuthority(symbol="TEST")
        auth.record("market_context", "passed")
        auth.record("structure_analysis", "signal_found")
        auth.reject("scoring_engine", "below_threshold")

        trace = auth.decision_trace
        assert len(trace) == 3
        assert trace[0].stage == "market_context"
        assert trace[1].stage == "structure_analysis"
        assert trace[2].stage == "scoring_engine"

    def test_final_decision_is_last(self):
        auth = PipelineAuthority(symbol="TEST")
        auth.record("market_context", "passed")
        auth.reject("scoring_engine", "below_threshold")

        assert auth.final_decision is not None
        assert auth.final_decision.action == "REJECT"
        assert auth.final_decision.stage == "scoring_engine"

    def test_rejection_count(self):
        auth = PipelineAuthority(symbol="TEST")
        auth.reject("market_context", "closed")
        auth.record("structure", "observed")
        auth.reject("scoring_engine", "low")

        assert auth.rejection_count == 2

    def test_is_rejected(self):
        auth = PipelineAuthority(symbol="TEST")
        assert not auth.is_rejected
        auth.reject("market_context", "closed")
        assert auth.is_rejected

    def test_rejection_stages(self):
        auth = PipelineAuthority(symbol="TEST")
        auth.reject("market_context", "closed")
        auth.reject("scoring_engine", "low")
        assert auth.rejection_stages == ["market_context", "scoring_engine"]

    def test_summary_structure(self):
        auth = PipelineAuthority(symbol="GBPUSD")
        auth.record("market_context", "passed")
        auth.reject("scoring_engine", "below_threshold", {"score": 3})

        summary = auth.summary()
        assert summary["symbol"] == "GBPUSD"
        assert summary["total_decisions"] == 2
        assert summary["rejections"] == 1
        assert summary["observations"] == 1
        assert summary["final_action"] == "REJECT"
        assert summary["final_stage"] == "scoring_engine"
        assert summary["final_reason"] == "below_threshold"


class TestResetBehaviour:
    """Authority resets cleanly between cycles."""

    def test_reset_clears_all(self):
        auth = PipelineAuthority(symbol="TEST")
        auth.reject("market_context", "closed")
        auth.record("structure", "observed")

        assert len(auth.decision_trace) == 2
        assert auth.is_rejected

        auth.reset()

        assert len(auth.decision_trace) == 0
        assert not auth.is_rejected
        assert auth.final_decision is None
        assert auth.rejection_count == 0


class TestValidStages:
    """VALID_STAGES covers all pipeline stages."""

    def test_all_expected_stages_present(self):
        expected = {
            "market_context",
            "strategy_detection",
            "structure_analysis",
            "structure_scoring",
            "confirmations",
            "trade_quality_pre",
            "scoring_engine",
            "trade_quality_post",
            "htf_constraint",
            "execution_gate",
            "risk_engine",
            "intent_builder",
            "complete",
        }
        assert expected == VALID_STAGES


class TestNoRawDecisionLeakage:
    """Enforcement: engine.py uses PipelineAuthority for all decisions."""

    def test_engine_imports_pipeline_authority(self):
        """engine.py must import PipelineAuthority."""
        import pathlib
        engine_path = pathlib.Path(__file__).parent.parent / "core" / "engine.py"
        source = engine_path.read_text(encoding="utf-8")
        assert "PipelineAuthority" in source
        assert "from core.pipeline.pipeline_authority import PipelineAuthority" in source

    def test_engine_creates_authority_instance(self):
        """engine.py must instantiate PipelineAuthority per cycle."""
        import pathlib
        engine_path = pathlib.Path(__file__).parent.parent / "core" / "engine.py"
        source = engine_path.read_text(encoding="utf-8")
        assert "authority = PipelineAuthority(" in source

    def test_engine_records_rejections(self):
        """engine.py must call authority.reject() for early exits."""
        import pathlib
        engine_path = pathlib.Path(__file__).parent.parent / "core" / "engine.py"
        source = engine_path.read_text(encoding="utf-8")
        # Should have multiple reject calls for different stages
        reject_count = source.count("authority.reject(")
        assert reject_count >= 5, f"Expected =5 authority.reject() calls, found {reject_count}"

    def test_engine_records_allow(self):
        """engine.py must call authority.allow() for successful trades."""
        import pathlib
        engine_path = pathlib.Path(__file__).parent.parent / "core" / "engine.py"
        source = engine_path.read_text(encoding="utf-8")
        assert "authority.allow(" in source
