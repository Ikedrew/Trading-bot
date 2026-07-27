"""Unit tests for regime normalization and validation."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine_state import normalize_regime, _VALID_REGIME_STATES


class TestNormalizeRegime:
    def test_trend_up(self):
        assert normalize_regime("TREND_UP") == ("TRENDING", "UP")

    def test_trend_down(self):
        assert normalize_regime("TREND_DOWN") == ("TRENDING", "DOWN")

    def test_trending(self):
        assert normalize_regime("TRENDING") == ("TRENDING", None)

    def test_ranging(self):
        assert normalize_regime("RANGING") == ("RANGING", None)

    def test_volatile(self):
        assert normalize_regime("VOLATILE") == ("VOLATILE", None)

    def test_choppy(self):
        assert normalize_regime("CHOPPY") == ("CHOPPY", None)

    def test_unknown_defaults_to_ranging(self):
        assert normalize_regime("UNKNOWN_STATE") == ("RANGING", None)

    def test_empty_string_defaults_to_ranging(self):
        assert normalize_regime("") == ("RANGING", None)


class TestValidRegimeStates:
    def test_trend_up_is_valid(self):
        assert "TREND_UP" in _VALID_REGIME_STATES

    def test_trend_down_is_valid(self):
        assert "TREND_DOWN" in _VALID_REGIME_STATES

    def test_structural_states_valid(self):
        for state in ("RANGING", "TRENDING", "VOLATILE", "CHOPPY"):
            assert state in _VALID_REGIME_STATES

    def test_invalid_state_not_in_set(self):
        assert "SIDEWAYS" not in _VALID_REGIME_STATES
