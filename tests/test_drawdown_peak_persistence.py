"""
Tests for E4: DrawdownGuard Peak Persistence.

Covers:
- Peak persists to disk on update
- Peak loads from disk on startup
- Peak survives restart (write → new instance → read)
- Corrupted file → reinitialises safely
- Missing file → initialises from current equity
- Peak never decreases (strict monotonic)
- Startup correction: stored peak < current → corrects upward
- Persist failure → falls back to in-memory (no crash)
- Reset persists new value
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.drawdown_guard import (
    DrawdownGuard,
    DrawdownResult,
    _load_persisted_peak,
    _persist_peak,
    _get_peak_path,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def use_temp_peak_file(tmp_path):
    """Redirect peak file to temp directory."""
    peak_file = tmp_path / "drawdown_peak.json"
    with patch("risk.drawdown_guard._get_peak_path", return_value=peak_file):
        yield peak_file


@pytest.fixture
def mock_config_enabled():
    """Config with drawdown guard enabled."""
    cfg = MagicMock()
    cfg.ENABLE_DRAWDOWN_GUARD = True
    cfg.MAX_DRAWDOWN_PERCENT = 10.0
    cfg.DRAWDOWN_PEAK_FILE = "logs/drawdown_peak.json"
    return cfg


# ─── TEST: PERSISTENCE WRITE ──────────────────────────────────────────────────

class TestPersistenceWrite:
    def test_persist_creates_file(self, use_temp_peak_file):
        """Persisting peak creates a JSON file."""
        result = _persist_peak(110000.0)
        assert result is True
        assert use_temp_peak_file.exists()

    def test_persist_writes_valid_json(self, use_temp_peak_file):
        """Persisted file contains valid JSON with peak_equity."""
        _persist_peak(105000.0)
        data = json.loads(use_temp_peak_file.read_text())
        assert data["peak_equity"] == 105000.0
        assert "last_updated" in data

    def test_persist_overwrites_atomically(self, use_temp_peak_file):
        """Multiple persists overwrite cleanly."""
        _persist_peak(100000.0)
        _persist_peak(110000.0)
        data = json.loads(use_temp_peak_file.read_text())
        assert data["peak_equity"] == 110000.0


# ─── TEST: PERSISTENCE READ ──────────────────────────────────────────────────

class TestPersistenceRead:
    def test_load_returns_stored_value(self, use_temp_peak_file):
        """Load reads persisted peak correctly."""
        _persist_peak(95000.0)
        result = _load_persisted_peak()
        assert result == 95000.0

    def test_load_missing_file_returns_none(self, use_temp_peak_file):
        """Missing file returns None (not an error)."""
        result = _load_persisted_peak()
        assert result is None

    def test_load_corrupted_file_returns_none(self, use_temp_peak_file):
        """Corrupted JSON returns None."""
        use_temp_peak_file.write_text("not valid json {{{")
        result = _load_persisted_peak()
        assert result is None

    def test_load_invalid_peak_returns_none(self, use_temp_peak_file):
        """File with invalid peak value returns None."""
        use_temp_peak_file.write_text(json.dumps({"peak_equity": -100}))
        result = _load_persisted_peak()
        assert result is None

    def test_load_zero_peak_returns_none(self, use_temp_peak_file):
        """Zero peak is treated as invalid."""
        use_temp_peak_file.write_text(json.dumps({"peak_equity": 0}))
        result = _load_persisted_peak()
        assert result is None


# ─── TEST: RESTART SURVIVAL ───────────────────────────────────────────────────

class TestRestartSurvival:
    def test_peak_survives_restart(self, use_temp_peak_file):
        """Peak persisted by one instance is loaded by the next."""
        # Instance 1: establishes peak
        _persist_peak(112000.0)

        # Instance 2: loads from disk
        guard = DrawdownGuard()
        assert guard.peak_equity == 112000.0

    def test_no_file_starts_at_zero(self, use_temp_peak_file):
        """No persisted file → starts at 0 (will init from first equity check)."""
        guard = DrawdownGuard()
        assert guard.peak_equity == 0.0


# ─── TEST: STRICT MONOTONIC ──────────────────────────────────────────────────

class TestStrictMonotonic:
    def test_peak_never_decreases(self, use_temp_peak_file):
        """Peak only increases, never decreases."""
        _persist_peak(110000.0)
        guard = DrawdownGuard()
        assert guard.peak_equity == 110000.0

        # Simulate check with lower equity — peak must NOT decrease
        with patch("risk.drawdown_guard.mt5_call") as mock_call, \
             patch("risk.drawdown_guard.config") as mock_cfg:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0

            acct = MagicMock()
            acct.equity = 105000.0  # Lower than peak
            mock_call.return_value = acct

            guard.check()
            assert guard.peak_equity == 110000.0  # Unchanged

    def test_peak_increases_on_new_high(self, use_temp_peak_file):
        """Peak updates when equity exceeds previous peak."""
        _persist_peak(100000.0)
        guard = DrawdownGuard()

        with patch("risk.drawdown_guard.mt5_call") as mock_call, \
             patch("risk.drawdown_guard.config") as mock_cfg:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0

            acct = MagicMock()
            acct.equity = 115000.0  # New high
            mock_call.return_value = acct

            guard.check()
            assert guard.peak_equity == 115000.0

            # Verify persisted to disk
            data = json.loads(use_temp_peak_file.read_text())
            assert data["peak_equity"] == 115000.0


# ─── TEST: STARTUP CORRECTION ─────────────────────────────────────────────────

class TestStartupCorrection:
    def test_stored_peak_below_current_gets_corrected(self, use_temp_peak_file):
        """If stored peak < current equity, peak is corrected upward on first check."""
        _persist_peak(90000.0)  # Stale/low peak
        guard = DrawdownGuard()
        assert guard.peak_equity == 90000.0

        with patch("risk.drawdown_guard.mt5_call") as mock_call, \
             patch("risk.drawdown_guard.config") as mock_cfg:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0

            acct = MagicMock()
            acct.equity = 100000.0  # Higher than stored peak
            mock_call.return_value = acct

            result = guard.check()
            assert guard.peak_equity == 100000.0  # Corrected
            assert result.allowed is True


# ─── TEST: PERSIST FAILURE FALLBACK ───────────────────────────────────────────

class TestPersistFailure:
    def test_persist_failure_does_not_crash(self, use_temp_peak_file):
        """If disk write fails, guard continues with in-memory peak."""
        guard = DrawdownGuard()

        with patch("risk.drawdown_guard._persist_peak", return_value=False), \
             patch("risk.drawdown_guard.mt5_call") as mock_call, \
             patch("risk.drawdown_guard.config") as mock_cfg:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0

            acct = MagicMock()
            acct.equity = 120000.0
            mock_call.return_value = acct

            # Should not raise
            result = guard.check()
            assert result.allowed is True
            assert guard.peak_equity == 120000.0  # Still tracked in memory


# ─── TEST: RESET PERSISTS ─────────────────────────────────────────────────────

class TestResetPersists:
    def test_reset_with_value_persists(self, use_temp_peak_file):
        """reset_peak with a value persists to disk."""
        guard = DrawdownGuard()
        guard.reset_peak(88000.0)
        assert guard.peak_equity == 88000.0

        data = json.loads(use_temp_peak_file.read_text())
        assert data["peak_equity"] == 88000.0

    def test_reset_to_zero_does_not_persist(self, use_temp_peak_file):
        """reset_peak(None) sets to 0 but doesn't persist 0."""
        _persist_peak(100000.0)
        guard = DrawdownGuard()
        guard.reset_peak(None)
        assert guard.peak_equity == 0.0
        # File still has old value (0 is not persisted)
        data = json.loads(use_temp_peak_file.read_text())
        assert data["peak_equity"] == 100000.0
