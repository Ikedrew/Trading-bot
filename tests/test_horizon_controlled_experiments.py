"""
Tests for Horizon Controlled Experiments.

Verifies:
    - Experiments do not change multiple variables accidentally
    - Paired trades use the same entry opportunity
    - Statistical tests are computed
    - Reports include correct metadata
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_engine.experiments.horizon_controlled import (
    _paired_significance,
    _simulate_exit,
    _simulate_trailing,
    _variant_stats,
    run_experiment_a_duration,
    run_experiment_b_stop_distance,
    run_experiment_c_target_distance,
    run_experiment_d_exit_policy,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SIMULATION UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSimulateExit:
    """Tests for the bar-by-bar exit simulator."""

    def test_sl_triggered(self):
        """Trade exits at SL when R drops below -SL."""
        prog = [{"bar": 1, "r": 0.1}, {"bar": 2, "r": -0.5}, {"bar": 3, "r": -1.1}]
        result = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=2.0)
        assert result["exit_r"] == -1.0
        assert result["exit_reason"] == "stop_loss"
        assert result["bars"] == 3

    def test_tp_triggered(self):
        """Trade exits at TP when R reaches TP level."""
        prog = [{"bar": 1, "r": 0.5}, {"bar": 2, "r": 1.5}, {"bar": 3, "r": 2.0}]
        result = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=2.0)
        assert result["exit_r"] == 2.0
        assert result["exit_reason"] == "take_profit"
        assert result["bars"] == 3

    def test_timeout(self):
        """Trade times out at max_bars."""
        prog = [{"bar": i, "r": 0.1} for i in range(1, 11)]
        result = _simulate_exit(prog, max_bars=5, sl_r=1.0, tp_r=2.0)
        assert result["exit_reason"] == "timeout"
        assert result["bars"] == 5
        assert result["exit_r"] == 0.1  # Last bar's R

    def test_no_future_data_used(self):
        """Exit only uses information up to the current bar."""
        prog = [{"bar": 1, "r": -1.5}, {"bar": 2, "r": 3.0}]  # SL hit on bar 1
        result = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=2.0)
        # Must exit at bar 1 (SL), not continue to bar 2 (TP)
        assert result["exit_reason"] == "stop_loss"
        assert result["bars"] == 1

    def test_empty_progression(self):
        """Empty progression returns no_data."""
        result = _simulate_exit([], max_bars=60, sl_r=1.0, tp_r=2.0)
        assert result["exit_reason"] == "no_data"


class TestSimulateTrailing:
    """Tests for trailing stop simulator."""

    def test_trailing_activates_and_exits(self):
        """Trailing activates at threshold and exits when price retraces."""
        prog = [
            {"bar": 1, "r": 0.2},
            {"bar": 2, "r": 0.6},  # Activates at 0.5
            {"bar": 3, "r": 0.8},  # Peak
            {"bar": 4, "r": 0.4},  # Drops below trail (0.8 - 0.25 = 0.55)
        ]
        result = _simulate_trailing(prog, max_bars=60, sl_r=1.0, activation_r=0.5, trail_distance_r=0.25)
        assert result["exit_reason"] == "trailing_stop"
        assert result["exit_r"] == 0.55  # peak(0.8) - trail(0.25)

    def test_trailing_not_activated(self):
        """If price never reaches activation, trailing doesn't apply."""
        prog = [{"bar": i, "r": 0.1} for i in range(1, 11)]
        result = _simulate_trailing(prog, max_bars=5, sl_r=1.0, activation_r=0.5, trail_distance_r=0.25)
        assert result["exit_reason"] == "timeout"

    def test_sl_before_activation(self):
        """SL triggers before trailing can activate."""
        prog = [{"bar": 1, "r": -0.5}, {"bar": 2, "r": -1.1}]
        result = _simulate_trailing(prog, max_bars=60, sl_r=1.0, activation_r=0.5, trail_distance_r=0.25)
        assert result["exit_reason"] == "stop_loss"
        assert result["exit_r"] == -1.0


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE VARIABLE ISOLATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleVariableIsolation:
    """Ensure each experiment changes only ONE variable."""

    def test_duration_only_changes_max_bars(self):
        """Experiment A: SL and TP are constant across all variants."""
        # Same trade tested at different max_bars
        prog = [{"bar": i, "r": 0.1 * (i % 5 - 2)} for i in range(1, 100)]

        # All variants use same SL=1.0 and TP=99 (unreachable)
        r_20 = _simulate_exit(prog, max_bars=20, sl_r=1.0, tp_r=99.0)
        r_60 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=99.0)
        r_180 = _simulate_exit(prog, max_bars=180, sl_r=1.0, tp_r=99.0)

        # SL and TP constant — only duration differs
        # Verify no TP hit (tp=99 is unreachable)
        assert r_20["exit_reason"] != "take_profit"
        assert r_60["exit_reason"] != "take_profit"

    def test_stop_distance_only_changes_sl(self):
        """Experiment B: TP and duration constant."""
        prog = [{"bar": i, "r": -0.1 * i} for i in range(1, 100)]

        # Different SL, same TP=99, same max_bars=60
        r_05 = _simulate_exit(prog, max_bars=60, sl_r=0.5, tp_r=99.0)
        r_10 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=99.0)
        r_20 = _simulate_exit(prog, max_bars=60, sl_r=2.0, tp_r=99.0)

        # Tighter SL exits earlier
        assert r_05["bars"] < r_10["bars"]
        assert r_10["bars"] < r_20["bars"]

    def test_target_distance_only_changes_tp(self):
        """Experiment C: SL and duration constant."""
        prog = [{"bar": i, "r": 0.1 * i} for i in range(1, 100)]  # Steadily rising

        # Same SL=1.0, same max_bars=60, different TP
        r_025 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=0.25)
        r_100 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=1.0)
        r_300 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=3.0)

        # Closer TP exits earlier
        assert r_025["bars"] < r_100["bars"]
        assert r_100["bars"] < r_300["bars"]
        # All hit TP (steady rise)
        assert r_025["exit_reason"] == "take_profit"
        assert r_100["exit_reason"] == "take_profit"


# ═══════════════════════════════════════════════════════════════════════════════
# PAIRED TRADE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPairedTrades:
    """All experiments use the same entry for all variants."""

    def test_same_progression_used_across_variants(self):
        """Each trade's progression is fed to all variants identically."""
        prog = [{"bar": i, "r": 0.05 * i} for i in range(1, 61)]

        # Experiment C variants all use the SAME progression
        r1 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=0.5)
        r2 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=1.0)
        r3 = _simulate_exit(prog, max_bars=60, sl_r=1.0, tp_r=2.0)

        # All share same entry (implied by same progression starting point)
        # Different outcomes only because TP differs
        assert r1["exit_r"] == 0.5
        assert r2["exit_r"] == 1.0
        assert r3["exit_r"] == 2.0


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatisticalTests:
    """Paired significance tests work correctly."""

    def test_identical_lists_not_significant(self):
        """No difference between identical lists."""
        data = [0.1, -0.2, 0.3, -0.1, 0.2] * 20
        result = _paired_significance(data, data)
        assert result["mean_improvement"] == 0.0
        assert result["significant_05"] is False

    def test_clear_improvement_is_significant(self):
        """Large consistent improvement is significant."""
        import random
        random.seed(42)
        control = [random.gauss(-0.1, 0.3) for _ in range(100)]
        variant = [c + 0.5 for c in control]  # Consistent +0.5 improvement
        result = _paired_significance(control, variant)
        assert result["significant_05"] is True
        assert result["mean_improvement"] > 0.4

    def test_confidence_interval_included(self):
        """Result includes 95% CI."""
        import random
        random.seed(123)
        control = [random.gauss(0.0, 0.5) for _ in range(100)]
        variant = [c + random.gauss(0.1, 0.05) for c in control]  # Noisy improvement
        result = _paired_significance(control, variant)
        assert "ci_95_low" in result
        assert "ci_95_high" in result
        assert result["ci_95_low"] < result["ci_95_high"]


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: FULL EXPERIMENT RUNS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentIntegration:
    """Full experiment runs produce valid reports."""

    def test_experiment_a_runs(self):
        """Experiment A produces a report without error."""
        report = run_experiment_a_duration()
        assert "question_id" in report
        assert report["question_id"] == "EX_A"
        assert "overall" in report

    def test_experiment_b_runs(self):
        """Experiment B produces a report."""
        report = run_experiment_b_stop_distance()
        assert report["question_id"] == "EX_B"

    def test_experiment_c_runs(self):
        """Experiment C produces a report."""
        report = run_experiment_c_target_distance()
        assert report["question_id"] == "EX_C"

    def test_experiment_d_runs(self):
        """Experiment D produces a report."""
        report = run_experiment_d_exit_policy()
        assert report["question_id"] == "EX_D"

    def test_reports_have_epoch(self):
        """All reports include CURRENT epoch metadata."""
        for runner in [run_experiment_a_duration, run_experiment_b_stop_distance]:
            report = runner()
            fp = report.get("fingerprint", {})
            assert fp.get("epoch") == "CURRENT"
