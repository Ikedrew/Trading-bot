"""
Tests for Cohort Slicing Layer (Phase 3).

Validates:
- Confirmation strength slicing
- Entry timing slicing
- Wick ratio band slicing
- Body pct band slicing
- Interaction matrix construction
- CohortMetrics calculations (win rate, expectancy, variance)
- Report generation
- Empty data handling
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cohort_analysis.slicer import (
    CohortMetrics,
    slice_by_confirmation_strength,
    slice_by_entry_timing,
    slice_by_wick_ratio_band,
    slice_by_body_pct_band,
    build_interaction_matrix,
)
from tools.cohort_analysis.report import (
    format_cohort_table,
    format_interaction_matrix,
    run_full_report,
)


# --- TEST DATA FACTORIES -----------------------------------------------------

def _trade(
    strength="STRONG",
    timing="MID",
    body_pct=0.70,
    wick_ratio=0.30,
    close_location=0.85,
    win=True,
    pnl=10.0,
    rr=2.0,
) -> dict:
    """Create a synthetic enriched trade record."""
    return {
        "should_trade": True,
        "symbol": "EURUSD",
        "score": 6,
        "confirmation": {
            "strength": strength,
            "body_pct": body_pct,
            "wick_ratio": wick_ratio,
            "close_location": close_location,
            "reason": "test",
            "passed": True,
        },
        "entry_timing": timing,
        "outcome_pnl": pnl if win else -abs(pnl),
        "outcome_rr": rr,
        "outcome_win": win,
        "outcome_close_reason": "take_profit" if win else "stop_loss",
    }


def _sample_trades() -> list[dict]:
    """Generate a diverse sample of trades for testing."""
    return [
        # STRONG + EARLY (wins)
        _trade(strength="STRONG", timing="EARLY", body_pct=0.85, wick_ratio=0.15, win=True, rr=2.5),
        _trade(strength="STRONG", timing="EARLY", body_pct=0.80, wick_ratio=0.20, win=True, rr=2.0),
        _trade(strength="STRONG", timing="EARLY", body_pct=0.75, wick_ratio=0.25, win=False, rr=1.0),
        # STRONG + MID
        _trade(strength="STRONG", timing="MID", body_pct=0.65, wick_ratio=0.35, win=True, rr=2.0),
        _trade(strength="STRONG", timing="MID", body_pct=0.62, wick_ratio=0.38, win=False, rr=1.0),
        _trade(strength="STRONG", timing="MID", body_pct=0.60, wick_ratio=0.35, win=True, rr=1.8),
        # WEAK + MID
        _trade(strength="WEAK", timing="MID", body_pct=0.52, wick_ratio=0.42, win=False, rr=1.0),
        _trade(strength="WEAK", timing="MID", body_pct=0.55, wick_ratio=0.40, win=True, rr=2.0),
        _trade(strength="WEAK", timing="MID", body_pct=0.50, wick_ratio=0.45, win=False, rr=1.0),
        # WEAK + LATE
        _trade(strength="WEAK", timing="LATE", body_pct=0.48, wick_ratio=0.52, win=False, rr=1.0),
        _trade(strength="WEAK", timing="LATE", body_pct=0.45, wick_ratio=0.55, win=False, rr=1.0),
        _trade(strength="WEAK", timing="LATE", body_pct=0.50, wick_ratio=0.50, win=True, rr=1.5),
    ]


# -------------------------------------------------------------------------------
# COHORT METRICS UNIT TESTS
# -------------------------------------------------------------------------------

class TestCohortMetrics:

    def test_win_rate_calculation(self):
        """Win rate = wins / total."""
        m = CohortMetrics(label="test", trade_count=10, wins=6, losses=4)
        assert m.win_rate == pytest.approx(0.6)

    def test_win_rate_zero_trades(self):
        """Zero trades ? 0.0 win rate."""
        m = CohortMetrics(label="test", trade_count=0)
        assert m.win_rate == 0.0

    def test_expectancy_positive(self):
        """Positive total RR with trades ? positive expectancy."""
        m = CohortMetrics(label="test", trade_count=4, total_rr=3.0)
        assert m.expectancy == pytest.approx(0.75)

    def test_expectancy_negative(self):
        """Negative total RR ? negative expectancy."""
        m = CohortMetrics(label="test", trade_count=5, total_rr=-2.5)
        assert m.expectancy == pytest.approx(-0.5)

    def test_variance_calculation(self):
        """Variance computed from outcomes."""
        m = CohortMetrics(label="test", outcomes=[2.0, 2.0, -1.0, -1.0])
        # Mean = 0.5; variance = ((1.5² + 1.5² + 1.5² + 1.5²) / 4) = 2.25
        assert m.variance == pytest.approx(2.25, abs=0.01)

    def test_variance_single_outcome(self):
        """Single outcome ? 0 variance."""
        m = CohortMetrics(label="test", outcomes=[2.0])
        assert m.variance == 0.0

    def test_to_dict(self):
        """to_dict produces all expected fields."""
        m = CohortMetrics(label="test", trade_count=5, wins=3, losses=2, total_pnl=50.0)
        d = m.to_dict()
        assert d["label"] == "test"
        assert d["trade_count"] == 5
        assert d["wins"] == 3
        assert d["losses"] == 2
        assert "win_rate" in d
        assert "expectancy" in d
        assert "variance" in d


# -------------------------------------------------------------------------------
# SLICING FUNCTION TESTS
# -------------------------------------------------------------------------------

class TestSliceByConfirmationStrength:

    def test_groups_by_strength(self):
        """Trades are grouped by confirmation strength."""
        trades = _sample_trades()
        cohorts = slice_by_confirmation_strength(trades)

        assert "STRONG" in cohorts
        assert "WEAK" in cohorts
        assert cohorts["STRONG"].trade_count == 6
        assert cohorts["WEAK"].trade_count == 6

    def test_strong_has_higher_win_rate(self):
        """STRONG cohort has higher win rate than WEAK in sample data."""
        trades = _sample_trades()
        cohorts = slice_by_confirmation_strength(trades)

        assert cohorts["STRONG"].win_rate > cohorts["WEAK"].win_rate

    def test_empty_records(self):
        """Empty input produces empty cohorts."""
        cohorts = slice_by_confirmation_strength([])
        assert len(cohorts) == 0


class TestSliceByEntryTiming:

    def test_groups_by_timing(self):
        """Trades are grouped by entry timing."""
        trades = _sample_trades()
        cohorts = slice_by_entry_timing(trades)

        assert "EARLY" in cohorts
        assert "MID" in cohorts
        assert "LATE" in cohorts
        assert cohorts["EARLY"].trade_count == 3
        assert cohorts["MID"].trade_count == 6
        assert cohorts["LATE"].trade_count == 3

    def test_empty_records(self):
        """Empty input produces empty cohorts."""
        cohorts = slice_by_entry_timing([])
        assert len(cohorts) == 0


class TestSliceByWickRatio:

    def test_groups_by_wick_band(self):
        """Trades are grouped into wick ratio bands."""
        trades = _sample_trades()
        cohorts = slice_by_wick_ratio_band(trades)

        # All bands exist (pre-created)
        assert "clean_0.0-0.2" in cohorts
        assert "moderate_0.2-0.4" in cohorts
        assert "high_0.4-1.0" in cohorts

    def test_clean_wick_band_populated(self):
        """Trades with wick < 0.2 go into clean band."""
        trades = [_trade(wick_ratio=0.15), _trade(wick_ratio=0.10)]
        cohorts = slice_by_wick_ratio_band(trades)
        assert cohorts["clean_0.0-0.2"].trade_count == 2

    def test_high_wick_band_populated(self):
        """Trades with wick >= 0.4 go into high band."""
        trades = [_trade(wick_ratio=0.50), _trade(wick_ratio=0.55), _trade(wick_ratio=0.70)]
        cohorts = slice_by_wick_ratio_band(trades)
        assert cohorts["high_0.4-1.0"].trade_count == 3


class TestSliceByBodyPct:

    def test_groups_by_body_band(self):
        """Trades are grouped into body pct bands."""
        trades = [
            _trade(body_pct=0.45),  # low
            _trade(body_pct=0.60),  # moderate
            _trade(body_pct=0.80),  # high
        ]
        cohorts = slice_by_body_pct_band(trades)

        assert cohorts["low_0.0-0.55"].trade_count == 1
        assert cohorts["moderate_0.55-0.70"].trade_count == 1
        assert cohorts["high_0.70-1.0"].trade_count == 1


class TestInteractionMatrix:

    def test_matrix_structure(self):
        """Matrix has correct structure (3 strengths × 3 timings)."""
        trades = _sample_trades()
        matrix = build_interaction_matrix(trades)

        assert "STRONG" in matrix
        assert "WEAK" in matrix
        assert "INVALID" in matrix

        for strength in ("STRONG", "WEAK", "INVALID"):
            assert "EARLY" in matrix[strength]
            assert "MID" in matrix[strength]
            assert "LATE" in matrix[strength]

    def test_matrix_counts_correct(self):
        """Matrix cell trade counts match expectations."""
        trades = _sample_trades()
        matrix = build_interaction_matrix(trades)

        assert matrix["STRONG"]["EARLY"].trade_count == 3
        assert matrix["STRONG"]["MID"].trade_count == 3
        assert matrix["WEAK"]["MID"].trade_count == 3
        assert matrix["WEAK"]["LATE"].trade_count == 3
        assert matrix["INVALID"]["EARLY"].trade_count == 0  # No INVALID in sample

    def test_empty_matrix(self):
        """Empty records produce matrix with all-zero counts."""
        matrix = build_interaction_matrix([])

        for strength in ("STRONG", "WEAK", "INVALID"):
            for timing in ("EARLY", "MID", "LATE"):
                assert matrix[strength][timing].trade_count == 0


# -------------------------------------------------------------------------------
# REPORT GENERATION TESTS
# -------------------------------------------------------------------------------

class TestReportGeneration:

    def test_format_cohort_table_produces_string(self):
        """format_cohort_table returns non-empty string."""
        trades = _sample_trades()
        cohorts = slice_by_confirmation_strength(trades)
        output = format_cohort_table(cohorts, "Test Table")

        assert isinstance(output, str)
        assert "Test Table" in output
        assert "STRONG" in output or "strength=STRONG" in output

    def test_format_interaction_matrix_produces_string(self):
        """format_interaction_matrix returns formatted string."""
        trades = _sample_trades()
        matrix = build_interaction_matrix(trades)
        output = format_interaction_matrix(matrix)

        assert isinstance(output, str)
        assert "STRONG" in output
        assert "EARLY" in output

    def test_full_report_runs_without_error(self):
        """run_full_report completes without exception."""
        trades = _sample_trades()
        report = run_full_report(trades)

        assert isinstance(report, str)
        assert len(report) > 100  # Non-trivial output
        assert "COHORT ANALYSIS REPORT" in report

    def test_full_report_empty_data(self):
        """run_full_report handles empty data gracefully."""
        report = run_full_report([])

        assert isinstance(report, str)
        assert "Total trades analyzed: 0" in report

    def test_full_report_contains_all_sections(self):
        """Report contains all major analysis sections."""
        trades = _sample_trades()
        report = run_full_report(trades)

        assert "Confirmation Strength" in report
        assert "Entry Timing" in report
        assert "Wick Ratio" in report
        assert "Body Strength" in report
        assert "Interaction Matrix" in report
        assert "KEY INSIGHTS" in report
        assert "CONCLUSIONS" in report


# -------------------------------------------------------------------------------
# EDGE CASE TESTS
# -------------------------------------------------------------------------------

class TestEdgeCases:

    def test_missing_confirmation_field(self):
        """Records without confirmation field handled gracefully."""
        records = [{"should_trade": True, "entry_timing": "MID", "outcome_win": True, "outcome_rr": 2.0, "outcome_pnl": 10.0}]
        cohorts = slice_by_confirmation_strength(records)
        assert "UNKNOWN" in cohorts
        assert cohorts["UNKNOWN"].trade_count == 1

    def test_none_outcome_fields(self):
        """Records with None outcomes don't crash metrics."""
        records = [_trade()]
        records[0]["outcome_pnl"] = None
        records[0]["outcome_rr"] = None
        records[0]["outcome_win"] = None

        cohorts = slice_by_confirmation_strength(records)
        assert cohorts["STRONG"].trade_count == 1
        assert cohorts["STRONG"].wins == 0
        assert cohorts["STRONG"].losses == 0

    def test_single_trade_metrics(self):
        """Single trade produces valid metrics."""
        records = [_trade(win=True, rr=2.0)]
        cohorts = slice_by_confirmation_strength(records)

        assert cohorts["STRONG"].trade_count == 1
        assert cohorts["STRONG"].win_rate == 1.0
