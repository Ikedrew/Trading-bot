"""
Tests for Trade Management Counterfactual Simulator (Phase 4A).

Validates:
- Break-even simulation: converts reversals to 0R
- Trailing stop simulation: captures extended moves
- Partial take-profit simulation: weighted R calculation
- Trades that never reach +1R remain unchanged
- MFE/MAE consistency
- Batch simulation on multiple trades
- No execution logic affected (observational only)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cohort_analysis.trade_management_simulator import (
    simulate_break_even,
    simulate_trailing_stop,
    simulate_partial_take_profit,
    simulate_all,
    simulate_batch,
    SimulationConfig,
    DEFAULT_CONFIG,
    BreakEvenResult,
    TrailingStopResult,
    PartialTakeProfitResult,
    CounterfactualResult,
)


# -------------------------------------------------------------------------------
# BREAK-EVEN SIMULATION TESTS
# -------------------------------------------------------------------------------


class TestBreakEvenSimulation:

    def test_trade_reaches_1r_then_reverses_to_sl(self):
        """Core scenario: Trade hits +1R then reverses to -1R.
        BE converts this from -1R loss ? 0R (breakeven)."""
        result = simulate_break_even(
            mfe_r=1.5,          # Reached +1.5R (beyond trigger)
            mae_r=-0.3,         # Had minor adverse before move
            actual_outcome_r=-1.0,  # Reversed all the way to SL
        )

        assert result.triggered is True
        assert result.outcome_r == 0.0  # BE caps at 0R
        assert result.improvement_r == 1.0  # Saved 1R

    def test_trade_never_reaches_trigger(self):
        """Trade never hits +1R — BE not triggered, outcome unchanged."""
        result = simulate_break_even(
            mfe_r=0.8,          # Only reached +0.8R
            mae_r=-0.5,
            actual_outcome_r=-1.0,  # Full stop loss
        )

        assert result.triggered is False
        assert result.outcome_r == -1.0  # Unchanged
        assert result.improvement_r == 0.0

    def test_winning_trade_unaffected(self):
        """Trade that wins: BE triggered but outcome already positive — no change."""
        result = simulate_break_even(
            mfe_r=2.5,
            mae_r=-0.2,
            actual_outcome_r=2.0,  # Won at +2R
        )

        assert result.triggered is True
        assert result.outcome_r == 2.0  # Still +2R (BE doesn't cap winners)
        assert result.improvement_r == 0.0

    def test_trade_exactly_at_trigger(self):
        """Trade reaches exactly +1R (boundary) — BE triggers."""
        result = simulate_break_even(
            mfe_r=1.0,          # Exactly at trigger
            mae_r=-0.4,
            actual_outcome_r=-1.0,
        )

        assert result.triggered is True
        assert result.outcome_r == 0.0

    def test_be_with_buffer(self):
        """BE with +0.1R buffer: stop moves to +0.1R instead of 0R."""
        config = SimulationConfig(be_trigger_r=1.0, be_buffer_r=0.1)
        result = simulate_break_even(
            mfe_r=1.5,
            mae_r=-0.2,
            actual_outcome_r=-1.0,
            config=config,
        )

        assert result.triggered is True
        assert result.outcome_r == 0.1  # Buffer captured
        assert result.improvement_r == pytest.approx(1.1)

    def test_trade_hits_5r_then_reverses_past_be(self):
        """Trade hits +5R then reverses to -1R. BE saves it at 0R."""
        result = simulate_break_even(
            mfe_r=5.0,
            mae_r=-0.1,
            actual_outcome_r=-1.0,
        )

        assert result.triggered is True
        assert result.outcome_r == 0.0
        assert result.improvement_r == 1.0


# -------------------------------------------------------------------------------
# TRAILING STOP SIMULATION TESTS
# -------------------------------------------------------------------------------


class TestTrailingStopSimulation:

    def test_trade_hits_5r_trail_captures_4r(self):
        """Trade reaches +5R. Trail at 1R distance captures +4R on reversal."""
        result = simulate_trailing_stop(
            mfe_r=5.0,
            mae_r=-0.1,
            actual_outcome_r=2.0,  # Actual exit at +2R (TP)
        )

        assert result.activated is True
        assert result.exit_r == 4.0  # MFE(5) - trail(1) = 4R
        assert result.max_locked_r == 4.0
        # Trail exit (4R) > actual (2R) ? improvement
        assert result.improvement_r == pytest.approx(2.0)

    def test_trade_never_activates_trail(self):
        """Trade never reaches +1R — trailing not activated, outcome unchanged."""
        result = simulate_trailing_stop(
            mfe_r=0.7,
            mae_r=-0.5,
            actual_outcome_r=-1.0,
        )

        assert result.activated is False
        assert result.exit_r == -1.0  # Unchanged
        assert result.improvement_r == 0.0

    def test_trail_on_moderate_winner(self):
        """Trade reaches +2R, trail locks +1R. Actual exit was +2R (TP hit first)."""
        result = simulate_trailing_stop(
            mfe_r=2.0,
            mae_r=-0.2,
            actual_outcome_r=2.0,  # Actual TP hit
        )

        assert result.activated is True
        # Trail exit = MFE(2) - distance(1) = 1R
        # But actual (2R) > trail_exit (1R), so we use actual (trade hit TP before trail)
        assert result.exit_r == 2.0
        assert result.improvement_r == 0.0  # TP was better than trail

    def test_trail_prevents_full_reversal(self):
        """Trade reaches +3R then reverses to -1R. Trail captures +2R."""
        result = simulate_trailing_stop(
            mfe_r=3.0,
            mae_r=-0.2,
            actual_outcome_r=-1.0,  # Full reversal to SL
        )

        assert result.activated is True
        assert result.exit_r == 2.0  # MFE(3) - trail(1) = 2R
        assert result.improvement_r == pytest.approx(3.0)  # Saved from -1R to +2R

    def test_custom_trail_distance(self):
        """Trail with 0.5R distance captures more."""
        config = SimulationConfig(trail_activation_r=1.0, trail_distance_r=0.5)
        result = simulate_trailing_stop(
            mfe_r=3.0,
            mae_r=-0.2,
            actual_outcome_r=-1.0,
            config=config,
        )

        assert result.activated is True
        assert result.exit_r == 2.5  # MFE(3) - trail(0.5) = 2.5R


# -------------------------------------------------------------------------------
# PARTIAL TAKE-PROFIT SIMULATION TESTS
# -------------------------------------------------------------------------------


class TestPartialTakeProfitSimulation:

    def test_tp1_hit_then_reversal(self):
        """Trade hits +1R (TP1), closes 50%. Remainder reverses to -1R.
        Combined: 50% × 1R + 50% × (-1R) = 0R."""
        result = simulate_partial_take_profit(
            mfe_r=1.5,
            mae_r=-0.2,
            actual_outcome_r=-1.0,  # Full position would have lost
        )

        assert result.tp1_hit is True
        # 0.5 × 1.0 + 0.5 × (-1.0) = 0.5 - 0.5 = 0.0
        assert result.combined_r == pytest.approx(0.0)
        assert result.improvement_r == pytest.approx(1.0)  # Saved from -1R to 0R

    def test_tp1_hit_and_winner(self):
        """Trade hits +1R (TP1), closes 50%. Remainder exits at +2R.
        Combined: 50% × 1R + 50% × 2R = 1.5R."""
        result = simulate_partial_take_profit(
            mfe_r=2.5,
            mae_r=-0.1,
            actual_outcome_r=2.0,  # Full position won at 2R
        )

        assert result.tp1_hit is True
        # 0.5 × 1.0 + 0.5 × 2.0 = 0.5 + 1.0 = 1.5
        assert result.combined_r == pytest.approx(1.5)
        # Partial TP reduced winner: 1.5 < 2.0 ? negative improvement
        assert result.improvement_r == pytest.approx(-0.5)

    def test_tp1_never_hit(self):
        """Trade never reaches +1R — no partial, outcome unchanged."""
        result = simulate_partial_take_profit(
            mfe_r=0.7,
            mae_r=-0.5,
            actual_outcome_r=-1.0,
        )

        assert result.tp1_hit is False
        assert result.combined_r == -1.0  # Unchanged
        assert result.improvement_r == 0.0

    def test_custom_tp1_level_and_fraction(self):
        """TP1 at +1.5R, 30% position closed."""
        config = SimulationConfig(tp1_level_r=1.5, tp1_fraction=0.3)
        result = simulate_partial_take_profit(
            mfe_r=2.0,
            mae_r=-0.1,
            actual_outcome_r=-1.0,
            config=config,
        )

        assert result.tp1_hit is True
        # 0.3 × 1.5 + 0.7 × (-1.0) = 0.45 - 0.70 = -0.25
        assert result.combined_r == pytest.approx(-0.25)

    def test_consistency_partial_vs_full_loss(self):
        """Partial TP always improves outcome when TP1 hit and remainder loses."""
        result = simulate_partial_take_profit(
            mfe_r=1.2,
            mae_r=-0.3,
            actual_outcome_r=-1.0,
        )

        # If TP1 hit and remainder goes to SL, combined should be better than full loss
        assert result.tp1_hit is True
        assert result.combined_r > result.actual_outcome_r


# -------------------------------------------------------------------------------
# COMBINED SIMULATION TESTS
# -------------------------------------------------------------------------------


class TestSimulateAll:

    def test_all_simulations_run(self):
        """simulate_all produces all three results."""
        result = simulate_all(
            mfe_r=3.0,
            mae_r=-0.5,
            actual_outcome_r=-1.0,
        )

        assert isinstance(result, CounterfactualResult)
        assert isinstance(result.break_even, BreakEvenResult)
        assert isinstance(result.trailing_stop, TrailingStopResult)
        assert isinstance(result.partial_tp, PartialTakeProfitResult)
        assert result.mfe_r == 3.0
        assert result.mae_r == -0.5
        assert result.actual_outcome_r == -1.0

    def test_to_dict_serializable(self):
        """CounterfactualResult.to_dict() produces JSON-safe output."""
        import json

        result = simulate_all(mfe_r=2.0, mae_r=-0.3, actual_outcome_r=2.0)
        d = result.to_dict()

        # Should not raise
        json_str = json.dumps(d)
        assert "break_even" in json_str
        assert "trailing_stop" in json_str
        assert "partial_tp" in json_str

    def test_trade_never_reaches_1r(self):
        """Trade that never hits +1R: all simulations return actual outcome."""
        result = simulate_all(
            mfe_r=0.5,
            mae_r=-0.8,
            actual_outcome_r=-1.0,
        )

        assert result.break_even.triggered is False
        assert result.break_even.outcome_r == -1.0
        assert result.trailing_stop.activated is False
        assert result.trailing_stop.exit_r == -1.0
        assert result.partial_tp.tp1_hit is False
        assert result.partial_tp.combined_r == -1.0

    def test_big_winner_all_strategies(self):
        """Trade that wins big (+5R): compare all strategies."""
        result = simulate_all(
            mfe_r=5.0,
            mae_r=-0.2,
            actual_outcome_r=2.0,  # Actual TP at 2R
        )

        # BE: doesn't help (already winning) ? 2R
        assert result.break_even.outcome_r == 2.0
        # Trail: captures 4R (MFE 5 - distance 1) > actual 2R
        assert result.trailing_stop.exit_r == 4.0
        # Partial: 0.5×1 + 0.5×2 = 1.5R (hurts the winner)
        assert result.partial_tp.combined_r == pytest.approx(1.5)


# -------------------------------------------------------------------------------
# BATCH SIMULATION TESTS
# -------------------------------------------------------------------------------


class TestBatchSimulation:

    def test_batch_enriches_records(self):
        """simulate_batch adds counterfactual field to each record."""
        trades = [
            {"outcome_rr": 2.0, "mfe_r": 2.5, "mae_r": -0.3},
            {"outcome_rr": -1.0, "mfe_r": 1.5, "mae_r": -1.0},
            {"outcome_rr": -1.0, "mfe_r": 0.5, "mae_r": -1.0},
        ]

        results = simulate_batch(trades)

        assert len(results) == 3
        for r in results:
            assert "counterfactual" in r
            assert "break_even" in r["counterfactual"]
            assert "trailing_stop" in r["counterfactual"]
            assert "partial_tp" in r["counterfactual"]

    def test_batch_handles_missing_mfe(self):
        """Records without mfe_r are handled with estimation."""
        trades = [
            {"outcome_rr": 2.0},  # No mfe_r
            {"outcome_rr": -1.0},  # No mfe_r
        ]

        results = simulate_batch(trades)

        assert len(results) == 2
        assert "counterfactual" in results[0]
        assert "counterfactual" in results[1]

    def test_batch_handles_none_outcome(self):
        """Records with None outcome_rr don't crash."""
        trades = [{"outcome_rr": None}]

        results = simulate_batch(trades)

        assert len(results) == 1
        assert "counterfactual" in results[0]

    def test_batch_preserves_original_fields(self):
        """Original trade record fields are preserved."""
        trades = [
            {"symbol": "EURUSD", "outcome_rr": 2.0, "mfe_r": 2.5, "mae_r": -0.3, "score": 6},
        ]

        results = simulate_batch(trades)

        assert results[0]["symbol"] == "EURUSD"
        assert results[0]["score"] == 6
        assert results[0]["outcome_rr"] == 2.0


# -------------------------------------------------------------------------------
# MFE/MAE CONSISTENCY TESTS
# -------------------------------------------------------------------------------


class TestMfeAaeConsistency:

    def test_mfe_always_gte_outcome_for_winners(self):
        """For winners, MFE should be >= actual outcome."""
        # This is a data integrity assumption
        result = simulate_all(mfe_r=2.5, mae_r=-0.2, actual_outcome_r=2.0)
        assert result.mfe_r >= result.actual_outcome_r

    def test_zero_mfe_means_immediate_loss(self):
        """MFE=0 means trade went negative immediately."""
        result = simulate_all(mfe_r=0.0, mae_r=-1.0, actual_outcome_r=-1.0)

        assert result.break_even.triggered is False
        assert result.trailing_stop.activated is False
        assert result.partial_tp.tp1_hit is False
