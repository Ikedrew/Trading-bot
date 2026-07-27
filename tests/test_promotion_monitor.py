"""Tests for Shadow EV Promotion Monitor."""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_assessment.promotion_monitor import (
    record_comparison,
    get_state,
    reset_state,
    emit_startup_notification,
    _evaluate_promotion,
    PromotionState,
    _MIN_DECISIONS_FOR_PROMOTION,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset monitor state before each test."""
    import core.research_assessment.promotion_monitor as mod
    mod._state = None
    mod._startup_notified = False
    mod._last_persist_time = 0.0
    # Redirect state file to temp
    mod._STATE_FILE = str(Path(tempfile.mkdtemp()) / "test_state.json")
    yield
    mod._state = None
    mod._startup_notified = False
    mod._last_persist_time = 0.0


def _dual_ev(match=True, exec_diff="RESEARCH_WOULD_EXECUTE", synth_pos=False, emp_pos=True):
    return {
        "synthetic_p": 0.28,
        "synthetic_ev": -0.05,
        "synthetic_positive": synth_pos,
        "empirical_p": 0.42,
        "empirical_ev": 0.24,
        "empirical_positive": emp_pos,
        "candidate_match": match,
        "candidate_id": "EC-TEST",
        "walk_forward_survivor": True,
        "research_confidence": "HIGH",
        "probability_difference": 0.14,
        "ev_difference": 0.29,
        "execution_difference": exec_diff,
    }


class TestAccumulator:
    def test_records_decision(self):
        record_comparison(_dual_ev())
        state = get_state()
        assert state["decisions_processed"] == 1
        assert state["research_matches"] == 1

    def test_counts_disagreement(self):
        record_comparison(_dual_ev(exec_diff="RESEARCH_WOULD_EXECUTE"))
        state = get_state()
        assert state["disagreement_count"] == 1
        assert state["research_would_execute"] == 1

    def test_counts_agreement(self):
        record_comparison(_dual_ev(exec_diff="AGREE", synth_pos=True, emp_pos=True))
        state = get_state()
        assert state["agreement_count"] == 1
        assert state["disagreement_count"] == 0

    def test_no_match_not_counted(self):
        record_comparison(_dual_ev(match=False))
        state = get_state()
        assert state["research_matches"] == 0

    def test_none_input_safe(self):
        record_comparison(None)
        state = get_state()
        assert state["decisions_processed"] == 0

    def test_multiple_records(self):
        for _ in range(10):
            record_comparison(_dual_ev())
        state = get_state()
        assert state["decisions_processed"] == 10


class TestPersistence:
    def test_state_persists_on_time_interval(self):
        """State persists after time interval elapses."""
        import core.research_assessment.promotion_monitor as mod
        # Set last persist time far in the past to trigger immediate persist
        mod._last_persist_time = 0.0

        record_comparison(_dual_ev())

        path = Path(mod._STATE_FILE)
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data["decisions_processed"] == 1

    def test_state_does_not_persist_before_interval(self):
        """State does not persist if interval has not elapsed."""
        import core.research_assessment.promotion_monitor as mod
        import time as _time

        # Set last persist time to now (interval not yet elapsed)
        mod._last_persist_time = _time.time()

        record_comparison(_dual_ev())

        path = Path(mod._STATE_FILE)
        # File should NOT exist (no persist triggered)
        # Unless milestone or status change triggered it
        # With 1 decision, no milestone reached, so no persist
        assert not path.exists()

    def test_milestone_forces_persistence(self):
        """Reaching 100 decisions persists immediately regardless of time."""
        import core.research_assessment.promotion_monitor as mod
        import time as _time

        # Set persist time to now (interval not elapsed)
        mod._last_persist_time = _time.time()

        for _ in range(100):
            record_comparison(_dual_ev())

        # Should persist due to milestone at 100
        path = Path(mod._STATE_FILE)
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data["decisions_processed"] == 100

    def test_restart_recovery(self):
        """State loads correctly after restart."""
        import core.research_assessment.promotion_monitor as mod
        # Force persist after each decision by keeping _last_persist_time at 0
        for _ in range(5):
            mod._last_persist_time = 0.0  # Force persist each time
            record_comparison(_dual_ev())
        # Simulate restart
        mod._state = None
        mod._last_persist_time = 0.0
        state = get_state()
        assert state["decisions_processed"] == 5


class TestPromotionCriteria:
    def test_below_min_decisions(self):
        state = PromotionState(decisions_processed=100, research_matches=50, research_would_execute=30)
        assert _evaluate_promotion(state) == "COLLECTING"

    def test_low_match_rate(self):
        state = PromotionState(decisions_processed=600, research_matches=10, research_would_execute=5, research_would_reject=3)
        assert _evaluate_promotion(state) == "NOT_READY"

    def test_candidate_when_criteria_met(self):
        state = PromotionState(
            decisions_processed=600,
            research_matches=100,
            research_would_execute=50,
            research_would_reject=10,
        )
        assert _evaluate_promotion(state) == "CANDIDATE"

    def test_not_ready_when_research_worse(self):
        state = PromotionState(
            decisions_processed=600,
            research_matches=100,
            research_would_execute=10,
            research_would_reject=50,
        )
        assert _evaluate_promotion(state) == "NOT_READY"


class TestDiscordNotifications:
    @patch("core.research_assessment.promotion_monitor._emit_discord")
    def test_milestone_notification(self, mock_discord):
        for _ in range(100):
            record_comparison(_dual_ev())
        mock_discord.assert_called()
        call_args = mock_discord.call_args[0]
        assert call_args[0] == "RESEARCH_MONITOR"
        assert call_args[1]["event"] == "MILESTONE"

    @patch("core.research_assessment.promotion_monitor._emit_discord")
    def test_startup_notification(self, mock_discord):
        emit_startup_notification()
        mock_discord.assert_called_once()
        call_args = mock_discord.call_args[0]
        assert call_args[1]["event"] == "STARTUP"
        assert call_args[1]["empirical_execution"] == "DISABLED"

    @patch("core.research_assessment.promotion_monitor._emit_discord")
    def test_no_duplicate_startup(self, mock_discord):
        emit_startup_notification()
        emit_startup_notification()
        assert mock_discord.call_count == 1


class TestProductionSafety:
    def test_no_execution_imports(self):
        import core.research_assessment.promotion_monitor as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        import_lines = [l for l in source.split("\n") if l.strip().startswith(("import ", "from "))]
        for line in import_lines:
            assert "from execution" not in line
            assert "mt5" not in line.lower()
            assert "order_send" not in line

    def test_exception_never_propagates(self):
        """Even with broken state, record_comparison never raises."""
        import core.research_assessment.promotion_monitor as mod
        mod._state = "BROKEN"  # Force bad state
        # Should not raise
        record_comparison(_dual_ev())
        # Reset
        mod._state = None
