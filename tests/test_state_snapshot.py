"""
Unit tests for StateSnapshot and StateDelta.

Validates:
- StateSnapshot immutability
- StateSnapshot.from_state correctness
- StateDelta collection
- apply_delta correctness
- No behavioral change (structural refactor only)
"""

from __future__ import annotations

import pytest

from core.engine_state import EngineState
from core.state.snapshot import StateSnapshot
from core.state.delta import StateDelta, apply_delta
from strategy.signals import Side


class TestStateSnapshot:
    def test_from_state_captures_all_fields(self):
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        state.current_bias = Side.BUY
        state.bias_strength = 72.0
        state.bias_age_seconds = 300.0
        state.regime_state = "TREND_UP"
        state.last_sweep_high = 1.0900
        state.last_sweep_low = 1.0800
        state.last_strong_impulse_direction = Side.BUY
        state.last_trade_side = "BUY"
        state.last_trade_bar = 42

        snap = StateSnapshot.from_state(state)

        assert snap.bias_phase == "CONFIRMED"
        assert snap.current_bias == Side.BUY
        assert snap.bias_strength == 72.0
        assert snap.bias_age_seconds == 300.0
        assert snap.regime_state == "TREND_UP"
        assert snap.last_sweep_high == 1.0900
        assert snap.last_sweep_low == 1.0800
        assert snap.last_strong_impulse_direction == Side.BUY
        assert snap.last_trade_side == "BUY"
        assert snap.last_trade_bar == 42
        assert snap.can_trade_bias is True

    def test_frozen_cannot_mutate(self):
        state = EngineState()
        snap = StateSnapshot.from_state(state)
        with pytest.raises(Exception):
            snap.bias_phase = "BUILDING"  # type: ignore[misc]

    def test_can_trade_bias_false_when_not_confirmed(self):
        state = EngineState()
        state.bias_phase = "BUILDING"
        snap = StateSnapshot.from_state(state)
        assert snap.can_trade_bias is False

    def test_can_trade_bias_true_when_confirmed(self):
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        snap = StateSnapshot.from_state(state)
        assert snap.can_trade_bias is True

    def test_bias_flip_bars_count(self):
        state = EngineState()
        state.bias_flip_bars.append(10)
        state.bias_flip_bars.append(15)
        state.bias_flip_bars.append(20)
        snap = StateSnapshot.from_state(state)
        assert snap.bias_flip_bars_count == 3

    def test_default_state_snapshot(self):
        state = EngineState()
        snap = StateSnapshot.from_state(state)
        assert snap.bias_phase == "EXPIRED"
        assert snap.current_bias is None
        assert snap.bias_strength == 0.0
        assert snap.can_trade_bias is False


class TestStateDelta:
    def test_default_delta_is_empty(self):
        delta = StateDelta()
        assert delta.volatility_filter is None
        assert delta.last_trade_side is None
        assert delta.last_trade_bar is None
        assert delta.bias_age_increment == 0
        assert delta.failed_setup is None
        assert delta.last_rejection_zone is None

    def test_apply_delta_volatility(self):
        state = EngineState()
        state.volatility_filter = 0.0
        delta = StateDelta(volatility_filter=-2.0)
        apply_delta(state, delta)
        assert state.volatility_filter == -2.0

    def test_apply_delta_trade_fields(self):
        state = EngineState()
        delta = StateDelta(last_trade_side="SELL", last_trade_bar=55, bias_age_increment=1)
        apply_delta(state, delta)
        assert state.last_trade_side == "SELL"
        assert state.last_trade_bar == 55
        assert state.bias_age == 1

    def test_apply_delta_failed_setup(self):
        state = EngineState()
        delta = StateDelta(
            failed_setup=(1000.0, 1.0800, 1.0850, "risk_reject"),
            last_rejection_zone=(1.0800, 1.0850),
        )
        apply_delta(state, delta)
        assert len(state.last_failed_setups) == 1
        assert state.last_rejection_zone == (1.0800, 1.0850)

    def test_apply_empty_delta_no_change(self):
        state = EngineState()
        state.volatility_filter = -1.5
        state.last_trade_side = "BUY"
        delta = StateDelta()  # empty
        apply_delta(state, delta)
        assert state.volatility_filter == -1.5
        assert state.last_trade_side == "BUY"

    def test_apply_delta_does_not_touch_fsm_fields(self):
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        state.current_bias = Side.BUY
        state.bias_strength = 80.0
        delta = StateDelta(volatility_filter=-1.0)
        apply_delta(state, delta)
        # FSM fields unchanged
        assert state.bias_phase == "CONFIRMED"
        assert state.current_bias == Side.BUY
        assert state.bias_strength == 80.0
