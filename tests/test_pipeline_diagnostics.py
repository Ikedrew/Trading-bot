"""
Unit tests for pipeline_diagnostics — throttled diagnostic reporting.

Tests:
    - Diagnostics only emit at cycle % 50 == 0
    - Score pressure report prints correctly
    - Empty score tracker doesn't crash
    - Calibration report at cycle 100
    - Never raises regardless of input
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline.pipeline_diagnostics import emit_pipeline_diagnostics


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _empty_tracker():
    return {
        "scored_signals": [],
        "rejected_scores": [],
        "passed_scores": [],
    }


def _populated_tracker():
    return {
        "scored_signals": [
            ("EURUSD", 4.2, 4.6, {"base_score": 3.5, "regime_bonus": 0.7}),
            ("GBPUSD", 5.1, 4.6, {"base_score": 4.0, "regime_bonus": 1.1}),
        ],
        "rejected_scores": [
            ("EURUSD", 4.2, 4.6),
        ],
        "passed_scores": [
            ("GBPUSD", 5.1, 4.6),
        ],
    }


def _empty_filter_hits():
    return {"trades_executed": 0, "market_context": 2, "score_reject": 1}


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestThrottling:
    """Diagnostics only fire at correct intervals."""

    def test_no_output_at_cycle_7(self, capsys):
        funnel = MagicMock()
        emit_pipeline_diagnostics(
            cycle_id=7,
            decision_funnel=funnel,
            score_tracker=_empty_tracker(),
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        funnel.format_console.assert_not_called()

    def test_output_at_cycle_50(self, capsys):
        funnel = MagicMock()
        funnel.format_console.return_value = "[FUNNEL]"
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=funnel,
            score_tracker=_empty_tracker(),
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert "[FUNNEL]" in captured.out


class TestScorePressure:
    """Score pressure report prints when rejections exist."""

    def test_score_pressure_with_rejections(self, capsys):
        funnel = MagicMock()
        funnel.format_console.return_value = ""
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=funnel,
            score_tracker=_populated_tracker(),
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert "SCORE PRESSURE REPORT" in captured.out
        assert "Rejected by score" in captured.out

    def test_score_pressure_no_rejections(self, capsys):
        funnel = MagicMock()
        funnel.format_console.return_value = ""
        tracker = _empty_tracker()
        tracker["scored_signals"] = [("EURUSD", 5.0, 4.6, {})]
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=funnel,
            score_tracker=tracker,
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert "All passed scoring" in captured.out


class TestCalibrationReport:
    """Calibration report fires at cycle 100."""

    def test_calibration_at_cycle_100(self, capsys):
        funnel = MagicMock()
        funnel.format_console.return_value = ""
        emit_pipeline_diagnostics(
            cycle_id=100,
            decision_funnel=funnel,
            score_tracker=_populated_tracker(),
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert "CALIBRATION REPORT" in captured.out

    def test_no_calibration_at_cycle_50(self, capsys):
        funnel = MagicMock()
        funnel.format_console.return_value = ""
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=funnel,
            score_tracker=_populated_tracker(),
            filter_hits=_empty_filter_hits(),
        )
        captured = capsys.readouterr()
        assert "CALIBRATION REPORT" not in captured.out


class TestNeverRaises:
    """Diagnostics never raise regardless of input."""

    def test_none_funnel_no_raise(self):
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=None,
            score_tracker=_empty_tracker(),
            filter_hits=_empty_filter_hits(),
        )

    def test_broken_tracker_no_raise(self):
        emit_pipeline_diagnostics(
            cycle_id=50,
            decision_funnel=MagicMock(format_console=MagicMock(side_effect=RuntimeError)),
            score_tracker={"scored_signals": None, "rejected_scores": None, "passed_scores": None},
            filter_hits={},
        )
