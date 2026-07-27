"""
Shared pytest fixtures for the trading system test suite.

Provides:
- Synthetic candle generation
- Mock MT5 client
- Risk state reset
- Pattern registry loading
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side, Signal


# ─── CANDLE FIXTURES ──────────────────────────────────────────────────────────

@pytest.fixture
def make_candle():
    """Factory fixture for creating synthetic candles."""
    def _make(t: int, o: float, h: float, l: float, c: float, tv: int = 0) -> Candle:
        return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=tv)
    return _make


@pytest.fixture
def bullish_candles(make_candle):
    """Three progressive bullish candles (three white soldiers shape)."""
    return [
        make_candle(1, 1.00, 1.02, 1.00, 1.02),
        make_candle(2, 1.02, 1.04, 1.02, 1.04),
        make_candle(3, 1.04, 1.06, 1.04, 1.06),
    ]


@pytest.fixture
def bearish_candles(make_candle):
    """Three progressive bearish candles (three black crows shape)."""
    return [
        make_candle(1, 1.06, 1.06, 1.04, 1.04),
        make_candle(2, 1.04, 1.04, 1.02, 1.02),
        make_candle(3, 1.02, 1.02, 1.00, 1.00),
    ]


@pytest.fixture
def engulfing_bullish(make_candle):
    """Two candles forming a bullish engulfing pattern."""
    return [
        make_candle(1, 1.10, 1.10, 1.08, 1.08),  # bearish
        make_candle(2, 1.07, 1.12, 1.07, 1.12),  # bullish engulfs
    ]


@pytest.fixture
def flat_candles(make_candle):
    """Zero-range candles (no pattern should fire)."""
    return [
        make_candle(1, 1.10, 1.10, 1.10, 1.10),
        make_candle(2, 1.10, 1.10, 1.10, 1.10),
        make_candle(3, 1.10, 1.10, 1.10, 1.10),
    ]


# ─── PATTERN REGISTRY ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def load_patterns():
    """Ensure pattern registry is loaded for all tests."""
    from patterns.registry import load_all_patterns
    load_all_patterns()


# ─── MOCK MT5 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_mt5():
    """Provide a mock MT5 module for testing without live connection."""
    with patch("risk.guards.mt5") as m:
        yield m


@pytest.fixture
def mock_mt5_account():
    """Mock MT5 with valid account info."""
    with patch("risk.guards.mt5") as m:
        acct = MagicMock()
        acct.balance = 10000.0
        acct.equity = 10000.0
        m.account_info.return_value = acct
        yield m


# ─── RISK STATE RESET ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_risk_metrics():
    """Reset risk metrics between tests to prevent state leakage."""
    yield
    try:
        from risk.metrics import risk_metrics
        risk_metrics.reset()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_rejection_metrics():
    """Reset rejection counters between tests."""
    yield
    try:
        from risk.manager import reset_rejection_metrics
        reset_rejection_metrics()
    except Exception:
        pass


# ─── S3 ISOLATION (prevent test contamination of production storage) ──────────

@pytest.fixture(autouse=True)
def disable_s3_mirror_in_tests(monkeypatch):
    """
    Prevent ALL tests from writing to production S3.

    Tests must never interact with the production S3 bucket.
    Local persistence to temp directories is unaffected.
    """
    monkeypatch.setattr("core.config.EVENT_STREAM_S3_MIRROR", False)
