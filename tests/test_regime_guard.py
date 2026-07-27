"""
Tests for I2: Market Regime Guard.

Covers:
- VOLATILE regime blocks execution
- CHOPPY regime blocks execution
- TRENDING regime allows execution
- RANGING regime allows execution
- HTF context used as primary source
- M5 fallback classification
- Disabled guard always allows
- Structured rejection format
- Confidence scoring
- Config-driven blocked regimes list
- Production integration (ordering in pipeline)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.regime_guard import (
    check_regime,
    classify_regime,
    RegimeGuardResult,
    RegimeAssessment,
    REJECT_REGIME_BLOCKED,
    _normalize_h4_regime,
    _classify_from_m5_signals,
)


# --- HELPERS ------------------------------------------------------------------

class _FakeRegimeSnapshot:
    def __init__(self, classification_value: str, confidence: float = 0.7, atr_ratio: float = 1.0):
        self.classification = MagicMock(value=classification_value)
        self.confidence = confidence
        self.atr_ratio = atr_ratio
        self.ema_slope = 0.0
        self.bar_time = 0


class _FakeHTFContext:
    def __init__(self, regime_snap=None, structure_snap=None):
        self.regime = regime_snap
        self.bias = None
        self.structure = structure_snap


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config():
    """Set known config defaults."""
    with patch("risk.regime_guard._is_enabled", return_value=True), \
         patch("risk.regime_guard._get_blocked_regimes", return_value=["VOLATILE", "CHOPPY"]):
        yield


# --- TEST: VOLATILE REGIME BLOCKS ----------------------------------------------

class TestVolatileBlocks:
    def test_htf_volatile_blocks(self, default_config):
        """H4 VOLATILE regime blocks execution."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("VOLATILE", 0.85))

        result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is False
        assert result.reason == REJECT_REGIME_BLOCKED
        assert result.regime == "VOLATILE"
        assert result.confidence == 0.85
        assert result.source == "HTF_H4"

    def test_m5_high_atr_volatile_blocks(self, default_config):
        """High ATR ratio without HTF ? classified as VOLATILE ? blocked."""
        result = check_regime(
            htf_context=None,
            m5_regime_state="RANGING",
            atr_ratio=1.8,
            symbol="GBPUSD",
        )

        assert result.allowed is False
        assert result.regime == "VOLATILE"


# --- TEST: CHOPPY REGIME BLOCKS -----------------------------------------------

class TestChoppyBlocks:
    def test_ranging_low_structure_is_choppy(self, default_config):
        """RANGING + low structure quality ? CHOPPY ? blocked."""
        htf = _FakeHTFContext(
            regime_snap=_FakeRegimeSnapshot("RANGING", 0.6),
        )

        result = check_regime(
            htf_context=htf,
            structure_score=0.2,  # Below 0.3 threshold
            symbol="EURUSD",
        )

        assert result.allowed is False
        assert result.regime == "CHOPPY"

    def test_m5_ranging_poor_structure_choppy(self, default_config):
        """M5 RANGING + poor structure ? CHOPPY ? blocked."""
        result = check_regime(
            htf_context=None,
            m5_regime_state="RANGING",
            atr_ratio=0.8,
            structure_score=0.2,
            symbol="USDJPY",
        )

        assert result.allowed is False
        assert result.regime == "CHOPPY"


# --- TEST: TRENDING ALLOWS ----------------------------------------------------

class TestTrendingAllows:
    def test_htf_trending_bullish_allows(self, default_config):
        """H4 TRENDING_BULLISH ? TRENDING ? allowed."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("TRENDING_BULLISH", 0.8))

        result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is True
        assert result.regime == "TRENDING"

    def test_htf_trending_bearish_allows(self, default_config):
        """H4 TRENDING_BEARISH ? TRENDING ? allowed."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("TRENDING_BEARISH", 0.75))

        result = check_regime(htf_context=htf, symbol="GBPUSD")

        assert result.allowed is True
        assert result.regime == "TRENDING"

    def test_m5_trend_up_allows(self, default_config):
        """M5 TREND_UP ? TRENDING ? allowed."""
        result = check_regime(
            htf_context=None,
            m5_regime_state="TREND_UP",
            atr_ratio=1.0,
            symbol="EURUSD",
        )

        assert result.allowed is True
        assert result.regime == "TRENDING"


# --- TEST: RANGING ALLOWS -----------------------------------------------------

class TestRangingAllows:
    def test_htf_ranging_good_structure_allows(self, default_config):
        """H4 RANGING with good structure ? RANGING ? allowed."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("RANGING", 0.6))

        result = check_regime(
            htf_context=htf,
            structure_score=0.7,  # Good structure
            symbol="EURUSD",
        )

        assert result.allowed is True
        assert result.regime == "RANGING"

    def test_m5_ranging_decent_structure_allows(self, default_config):
        """M5 RANGING + decent structure ? RANGING ? allowed."""
        result = check_regime(
            htf_context=None,
            m5_regime_state="RANGING",
            atr_ratio=0.8,
            structure_score=0.5,  # Above 0.3 threshold
            symbol="AUDUSD",
        )

        assert result.allowed is True
        assert result.regime == "RANGING"


# --- TEST: DISABLED GUARD -----------------------------------------------------

class TestDisabledGuard:
    def test_disabled_always_allows(self, default_config):
        """When disabled, volatile regime still allowed."""
        with patch("risk.regime_guard._is_enabled", return_value=False):
            htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("VOLATILE", 0.9))
            result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is True
        assert result.reason == "REGIME_GUARD_DISABLED"

    def test_empty_blocked_list_allows(self, default_config):
        """Empty BLOCKED_REGIMES list ? nothing blocked."""
        with patch("risk.regime_guard._get_blocked_regimes", return_value=[]):
            htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("VOLATILE", 0.9))
            result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is True


# --- TEST: STRUCTURED REJECTION ------------------------------------------------

class TestStructuredRejection:
    def test_rejection_has_all_fields(self, default_config):
        """Rejection result contains all required fields."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("VOLATILE", 0.82))

        result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is False
        assert result.reason == "REGIME_BLOCKED"
        assert result.regime == "VOLATILE"
        assert result.confidence == pytest.approx(0.82)
        assert result.source == "HTF_H4"

    def test_allowed_result_has_regime_info(self, default_config):
        """Even allowed results report current regime."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("TRENDING_BULLISH", 0.7))

        result = check_regime(htf_context=htf, symbol="EURUSD")

        assert result.allowed is True
        assert result.regime == "TRENDING"
        assert result.confidence == 0.7


# --- TEST: CLASSIFICATION LOGIC -----------------------------------------------

class TestClassification:
    def test_htf_takes_priority_over_m5(self, default_config):
        """When HTF is available, M5 regime state is ignored."""
        htf = _FakeHTFContext(regime_snap=_FakeRegimeSnapshot("VOLATILE", 0.9))

        assessment = classify_regime(
            htf_context=htf,
            m5_regime_state="TREND_UP",  # M5 says trending, HTF says volatile
            atr_ratio=0.5,
        )

        # HTF wins
        assert assessment.regime == "VOLATILE"
        assert assessment.source == "HTF_H4"

    def test_no_htf_uses_m5(self, default_config):
        """Without HTF, M5 signals are used."""
        assessment = classify_regime(
            htf_context=None,
            m5_regime_state="TREND_DOWN",
            atr_ratio=1.0,
        )

        assert assessment.regime == "TRENDING"
        assert assessment.source == "M5_COMPOSITE"

    def test_transitional_high_atr_becomes_volatile(self, default_config):
        """H4 TRANSITIONAL + high ATR ? VOLATILE."""
        regime = _normalize_h4_regime("TRANSITIONAL", atr_ratio=1.5, structure_score=0.5)
        assert regime == "VOLATILE"

    def test_transitional_low_structure_becomes_choppy(self, default_config):
        """H4 TRANSITIONAL + low structure ? CHOPPY."""
        regime = _normalize_h4_regime("TRANSITIONAL", atr_ratio=0.8, structure_score=0.2)
        assert regime == "CHOPPY"


# --- TEST: CUSTOM BLOCKED REGIMES ---------------------------------------------

class TestCustomBlockedRegimes:
    def test_only_volatile_blocked(self, default_config):
        """If only VOLATILE is blocked, CHOPPY is allowed."""
        with patch("risk.regime_guard._get_blocked_regimes", return_value=["VOLATILE"]):
            result = check_regime(
                htf_context=None,
                m5_regime_state="RANGING",
                atr_ratio=0.8,
                structure_score=0.2,
                symbol="EURUSD",
            )
            # This classifies as CHOPPY, but CHOPPY not in blocked list
            assert result.allowed is True

    def test_all_blocked(self, default_config):
        """Block everything except TRENDING."""
        with patch("risk.regime_guard._get_blocked_regimes",
                   return_value=["VOLATILE", "CHOPPY", "RANGING", "TRANSITIONAL"]):
            result = check_regime(
                htf_context=None,
                m5_regime_state="RANGING",
                atr_ratio=0.8,
                structure_score=0.5,
                symbol="EURUSD",
            )
            assert result.allowed is False
            assert result.regime == "RANGING"


# --- TEST: PRODUCTION INTEGRATION ---------------------------------------------

class TestProductionIntegration:
    def test_regime_guard_before_execution(self):
        """Regime guard appears in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        regime_pos = source.find("check_regime")

        assert regime_pos > 0, "Regime guard not found in runtime guard chain"

    def test_regime_guard_after_a5(self):
        """Regime guard appears AFTER portfolio exposure guard."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        a5_pos = source.find("check_portfolio_exposure")
        regime_pos = source.find("check_regime")

        assert a5_pos > 0
        assert regime_pos > 0
        assert a5_pos < regime_pos, "Regime guard must appear AFTER A5"
