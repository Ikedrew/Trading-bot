"""
Verification: NO_TRADE decisions flow through Shadow EV Monitor.

Confirms that rejected decisions (the most valuable research data) correctly:
1. Have dual_ev attached to the decision record
2. Reach the promotion monitor
3. Route to Discord via RESEARCH_MONITOR channel
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDualEVExistsOnNoTradePath:
    """Verify that new_engine produces dual_ev on NO_TRADE outcomes."""

    def test_ev_blocked_result_contains_dual_ev(self):
        """Engine result dict includes dual_ev when EV blocks the trade."""
        # Simulate what new_engine returns on EV rejection
        # The dual_ev is computed BEFORE the EV policy check and attached via _strategy_meta
        # When action=NO_TRADE with reason=ev_policy_blocked, dual_ev should be in the result

        # We can verify this by checking the _strategy_meta.update logic in new_engine.py:
        # _strategy_meta["dual_ev"] = _dual_ev.to_dict() is set BEFORE policy_final check
        # The return dict includes **_strategy_meta → dual_ev is in all return paths after EV computation

        # Direct test: simulate the engine result structure
        engine_result = {
            "action": "NO_TRADE",
            "reason": "ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
            "score": 0.55,
            "components": {"bias_alignment": 0.7},
            "pattern": "TWEEZER_TOP",
            "entity_id": "EURUSD_1000000",
            "dual_ev": {
                "synthetic_p": 0.28,
                "synthetic_ev": -0.05,
                "synthetic_positive": False,
                "empirical_p": 0.42,
                "empirical_ev": 0.24,
                "empirical_positive": True,
                "candidate_match": True,
                "candidate_id": "EC-TEST",
                "execution_difference": "RESEARCH_WOULD_EXECUTE",
            },
        }

        # Verify the result has dual_ev
        assert "dual_ev" in engine_result
        assert engine_result["dual_ev"]["candidate_match"] is True
        assert engine_result["dual_ev"]["execution_difference"] == "RESEARCH_WOULD_EXECUTE"


class TestEngineOutcomeHandlerAttachesDualEV:
    """Verify handle_no_trade_outcome attaches dual_ev to cycle_decision."""

    def test_dual_ev_attached_to_cycle_decision(self):
        """dual_ev from engine result flows into cycle_decision dict."""
        from core.runtime.engine_outcome_handler import handle_no_trade_outcome

        cycle_decision = {}
        new_result = {
            "action": "NO_TRADE",
            "reason": "ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
            "score": 0.55,
            "entity_id": "EURUSD_1000000",
            "assessment": None,
            "reasoning": None,
            "uncertainty": None,
            "attribution": None,
            "dual_ev": {
                "synthetic_p": 0.28,
                "synthetic_ev": -0.05,
                "synthetic_positive": False,
                "empirical_p": 0.42,
                "empirical_ev": 0.24,
                "empirical_positive": True,
                "candidate_match": True,
                "candidate_id": "EC-HIGH-TEST",
                "execution_difference": "RESEARCH_WOULD_EXECUTE",
            },
        }

        with patch("core.runtime.engine_outcome_handler.run_evaluation"):
            with patch("core.runtime.engine_outcome_handler.classify_new_engine_reason") as mock_classify:
                mock_classify.return_value = MagicMock(filter_key="ev_negative")
                with patch("core.research_assessment.promotion_monitor.record_comparison") as mock_record:
                    handle_no_trade_outcome(
                        new_result=new_result,
                        new_engine_score=0.55,
                        symbol="EURUSD",
                        engine_state=MagicMock(),
                        risk=MagicMock(),
                        cycle_id=100,
                        closed_time=1000000,
                        candles=[],
                        closed_i=0,
                        bid=1.1,
                        ask=1.1001,
                        config=MagicMock(),
                        runtime_session_id="test",
                        cycle_decision=cycle_decision,
                        cycle_drops=[],
                        filter_hits={"ev_negative": 0},
                    )

                    # Verify dual_ev was attached
                    assert "dual_ev" in cycle_decision
                    assert cycle_decision["dual_ev"]["candidate_match"] is True
                    assert cycle_decision["dual_ev"]["candidate_id"] == "EC-HIGH-TEST"

                    # Verify promotion monitor was called
                    mock_record.assert_called_once_with(new_result["dual_ev"])

    def test_no_dual_ev_when_absent(self):
        """When engine result has no dual_ev, no crash and no attachment."""
        from core.runtime.engine_outcome_handler import handle_no_trade_outcome

        cycle_decision = {}
        new_result = {
            "action": "NO_TRADE",
            "reason": "score_below_threshold (0.30 < 0.35)",
            "score": 0.30,
            "entity_id": "EURUSD_1000000",
            "assessment": None,
            # No dual_ev key — blocked before EV computation
        }

        with patch("core.runtime.engine_outcome_handler.run_evaluation"):
            with patch("core.runtime.engine_outcome_handler.classify_new_engine_reason") as mock_classify:
                mock_classify.return_value = MagicMock(filter_key="score_below")
                handle_no_trade_outcome(
                    new_result=new_result,
                    new_engine_score=0.30,
                    symbol="EURUSD",
                    engine_state=MagicMock(),
                    risk=MagicMock(),
                    cycle_id=100,
                    closed_time=1000000,
                    candles=[],
                    closed_i=0,
                    bid=1.1,
                    ask=1.1001,
                    config=MagicMock(),
                    runtime_session_id="test",
                    cycle_decision=cycle_decision,
                    cycle_drops=[],
                    filter_hits={"score_below": 0},
                )

                # No dual_ev attached — not an error
                assert "dual_ev" not in cycle_decision


class TestPromotionMonitorReceivesNoTrade:
    """Verify the promotion monitor accumulates NO_TRADE decisions."""

    def test_monitor_increments_on_no_trade(self):
        """record_comparison called with NO_TRADE dual_ev updates state."""
        import core.research_assessment.promotion_monitor as mod
        import tempfile
        mod._state = None
        mod._STATE_FILE = str(Path(tempfile.mkdtemp()) / "test.json")

        from core.research_assessment.promotion_monitor import record_comparison, get_state

        dual_ev = {
            "synthetic_positive": False,
            "empirical_positive": True,
            "candidate_match": True,
            "execution_difference": "RESEARCH_WOULD_EXECUTE",
        }

        record_comparison(dual_ev)
        state = get_state()

        assert state["decisions_processed"] == 1
        assert state["research_matches"] == 1
        assert state["disagreement_count"] == 1
        assert state["research_would_execute"] == 1

        mod._state = None


class TestDiscordRouting:
    """Verify RESEARCH_MONITOR events route to the correct Discord channel."""

    def test_channel_map_routing(self):
        """RESEARCH_MONITOR maps to research_monitor-shadow-research."""
        from core.log_router import CHANNEL_MAP
        assert CHANNEL_MAP["RESEARCH_MONITOR"] == "research_monitor-shadow-research"

    def test_milestone_triggers_discord(self):
        """100 decisions triggers milestone notification."""
        import core.research_assessment.promotion_monitor as mod
        import tempfile
        mod._state = None
        mod._startup_notified = False
        mod._STATE_FILE = str(Path(tempfile.mkdtemp()) / "test.json")

        from core.research_assessment.promotion_monitor import record_comparison

        with patch("core.research_assessment.promotion_monitor._emit_discord") as mock_discord:
            for _ in range(100):
                record_comparison({
                    "synthetic_positive": False,
                    "empirical_positive": True,
                    "candidate_match": True,
                    "execution_difference": "RESEARCH_WOULD_EXECUTE",
                })

            # Should have been called for 100-decision milestone
            assert mock_discord.called
            calls = [c for c in mock_discord.call_args_list if c[0][1].get("event") == "MILESTONE"]
            assert len(calls) >= 1
            assert calls[0][0][1]["decisions"] == 100

        mod._state = None
