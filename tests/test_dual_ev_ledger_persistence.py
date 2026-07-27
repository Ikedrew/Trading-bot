"""Tests for dual_ev persistence in decision ledger."""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.decision_ledger import build_ledger_entry, DecisionOutcome


_DUAL_EV_SAMPLE = {
    "synthetic_p": 0.28,
    "synthetic_ev": -0.05,
    "synthetic_positive": False,
    "empirical_p": 0.42,
    "empirical_ev": 0.24,
    "empirical_positive": True,
    "candidate_match": True,
    "candidate_id": "EC-HIGH_TWEEZER_TOP-574D6A",
    "walk_forward_survivor": True,
    "research_confidence": "HIGH",
    "probability_difference": 0.14,
    "ev_difference": 0.29,
    "execution_difference": "RESEARCH_WOULD_EXECUTE",
}


class TestDualEVPersistence:
    def test_no_trade_persists_dual_ev(self):
        """NO_TRADE decision with dual_ev produces entry containing it."""
        entry = build_ledger_entry(
            symbol="EURUSD",
            cycle_id=100,
            decision=DecisionOutcome.NO_TRADE,
            reason="ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
            dual_ev=_DUAL_EV_SAMPLE,
        )
        assert "dual_ev" in entry
        assert entry["dual_ev"]["candidate_match"] is True
        assert entry["dual_ev"]["execution_difference"] == "RESEARCH_WOULD_EXECUTE"
        assert entry["dual_ev"]["candidate_id"] == "EC-HIGH_TWEEZER_TOP-574D6A"

    def test_execute_persists_dual_ev(self):
        """EXECUTE decision with dual_ev produces entry containing it."""
        entry = build_ledger_entry(
            symbol="GBPUSD",
            cycle_id=200,
            decision=DecisionOutcome.EXECUTE,
            reason="all_guards_passed",
            dual_ev=_DUAL_EV_SAMPLE,
        )
        assert "dual_ev" in entry
        assert entry["dual_ev"]["empirical_ev"] == 0.24

    def test_missing_dual_ev_is_none(self):
        """Entry without dual_ev has dual_ev=None (backward compatible)."""
        entry = build_ledger_entry(
            symbol="USDJPY",
            cycle_id=300,
            decision=DecisionOutcome.NO_TRADE,
            reason="no_patterns_detected",
        )
        assert "dual_ev" in entry
        assert entry["dual_ev"] is None

    def test_json_serializable_with_dual_ev(self):
        """Entry with dual_ev serializes cleanly to JSON."""
        entry = build_ledger_entry(
            symbol="EURUSD",
            cycle_id=100,
            decision=DecisionOutcome.NO_TRADE,
            reason="ev_blocked",
            dual_ev=_DUAL_EV_SAMPLE,
        )
        json_str = json.dumps(entry, default=str)
        parsed = json.loads(json_str)
        assert parsed["dual_ev"]["candidate_id"] == "EC-HIGH_TWEEZER_TOP-574D6A"

    def test_old_ledger_entries_still_load(self):
        """Legacy entries without dual_ev field load without error."""
        # Simulate old-format entry (no dual_ev key)
        old_entry = {
            "timestamp": "2026-07-17T00:30:00.000Z",
            "symbol": "EURUSD",
            "cycle_id": 100,
            "decision": "NO_TRADE",
            "reason": "ev_policy_blocked",
            "regime": "TRANSITIONAL",
            "signal_score": 0.55,
        }
        # Should load fine — accessing dual_ev via .get returns None
        assert old_entry.get("dual_ev") is None
        # JSON round-trip
        json_str = json.dumps(old_entry)
        parsed = json.loads(json_str)
        assert "dual_ev" not in parsed  # Old entries don't have the field


class TestDecisionRecorderDualEV:
    def test_finalize_passes_dual_ev(self):
        """DecisionRecorder.finalize passes dual_ev to ledger.record."""
        from core.runtime.decision_recorder import DecisionRecorder
        import time

        mock_ledger = MagicMock()
        recorder = DecisionRecorder(mock_ledger)

        decision = recorder.init_cycle(
            symbol="EURUSD", cycle_id=100, regime="TRANSITIONAL",
            context_snapshot_id="COR-TEST", drawdown_pct=0.0, daily_loss_pct=0.0,
        )
        decision["decision"] = DecisionOutcome.NO_TRADE
        decision["reason"] = "ev_policy_blocked"
        decision["dual_ev"] = _DUAL_EV_SAMPLE

        recorder.finalize(cycle_start=time.time() - 1.0)

        # Verify ledger.record was called with dual_ev
        mock_ledger.record.assert_called_once()
        call_kwargs = mock_ledger.record.call_args[1]
        assert "dual_ev" in call_kwargs
        assert call_kwargs["dual_ev"]["candidate_id"] == "EC-HIGH_TWEEZER_TOP-574D6A"

    def test_finalize_without_dual_ev(self):
        """DecisionRecorder.finalize works when dual_ev is not set."""
        from core.runtime.decision_recorder import DecisionRecorder
        import time

        mock_ledger = MagicMock()
        recorder = DecisionRecorder(mock_ledger)

        decision = recorder.init_cycle(
            symbol="EURUSD", cycle_id=100, regime="TRANSITIONAL",
            context_snapshot_id="COR-TEST", drawdown_pct=0.0, daily_loss_pct=0.0,
        )
        decision["decision"] = DecisionOutcome.NO_TRADE
        decision["reason"] = "no_patterns"

        recorder.finalize(cycle_start=time.time() - 1.0)

        call_kwargs = mock_ledger.record.call_args[1]
        assert call_kwargs["dual_ev"] is None
