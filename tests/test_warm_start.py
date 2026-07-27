"""
Tests for D1: Warm-Start Enablement + Completeness.

Covers:
- State restored correctly (all persisted fields)
- Cooldown persistence (last_successful_open_mono)
- Failed setup persistence (restore valid, discard expired)
- Structure persistence (structure_score, structure_regime)
- Regime validation (TREND_UP, TREND_DOWN accepted)
- Corrupt state handling (invalid file, invalid enum, invalid timestamps)
- Restart simulation (save ? shutdown ? restore ? verify)
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine_state import EngineState
from core.state_persistence import (
    save_engine_states,
    load_engine_state,
    _serialize_state,
    _deserialize_into_state,
    _PERSISTED_FIELDS,
    _FAILED_SETUP_TTL_SECONDS,
)
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture
def tmp_state_dir(tmp_path):
    """Provide temp directory for state files."""
    with patch("core.state_persistence._get_persist_dir", return_value=tmp_path), \
         patch("core.state_persistence.config") as mock_cfg:
        mock_cfg.ENGINE_STATE_WARM_START_ENABLED = True
        mock_cfg.ENGINE_STATE_PERSIST_DIR = str(tmp_path)
        mock_cfg.ENGINE_STATE_MAX_AGE_SECONDS = 86400
        yield tmp_path


# --- TEST: BASIC SAVE/RESTORE -------------------------------------------------

class TestBasicSaveRestore:
    def test_save_creates_file(self, tmp_state_dir):
        """Saving creates a JSON file for the symbol."""
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        state.current_bias = Side.BUY
        save_engine_states([("EURUSD", state)])

        filepath = tmp_state_dir / "EURUSD.json"
        assert filepath.exists()

    def test_restore_reads_saved_state(self, tmp_state_dir):
        """Saved state can be restored."""
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        state.current_bias = Side.BUY
        state.regime_state = "TRENDING"
        save_engine_states([("EURUSD", state)])

        restored = load_engine_state("EURUSD")
        assert restored is not None
        assert restored.bias_phase == "CONFIRMED"
        assert restored.current_bias == Side.BUY
        assert restored.regime_state == "TRENDING"

    def test_all_scalar_fields_roundtrip(self, tmp_state_dir):
        """All _PERSISTED_FIELDS survive save/restore."""
        state = EngineState()
        state.current_bias = Side.SELL
        state.bias_phase = "BUILDING"
        state.bias_strength = 75.5
        state.bias_age_seconds = 1200.0
        state.bias_confirmation_score = 4.0
        state.bias_confirmation_count = 3
        state.bias_contradiction_count = 1
        state.last_strong_impulse_direction = Side.SELL
        state.regime_state = "VOLATILE"
        state.volatility_filter = 0.8
        state.last_successful_open_mono = 1717400000.0
        state.structure_score = 3.5
        state.structure_regime = "BUILDING"

        save_engine_states([("TEST_SB", state)])
        restored = load_engine_state("TEST_SB")

        assert restored is not None
        assert restored.current_bias == Side.SELL
        assert restored.bias_phase == "BUILDING"
        assert restored.bias_strength == 75.5
        assert restored.bias_age_seconds == 1200.0
        assert restored.bias_confirmation_score == 4.0
        assert restored.bias_confirmation_count == 3
        assert restored.bias_contradiction_count == 1
        assert restored.last_strong_impulse_direction == Side.SELL
        assert restored.regime_state == "VOLATILE"
        assert restored.volatility_filter == 0.8
        assert restored.last_successful_open_mono == 1717400000.0
        assert restored.structure_score == 3.5
        assert restored.structure_regime == "BUILDING"


# --- TEST: COOLDOWN PERSISTENCE -----------------------------------------------

class TestCooldownPersistence:
    def test_last_successful_open_mono_persists(self, tmp_state_dir):
        """last_successful_open_mono survives restart."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        state.last_successful_open_mono = 1717401500.0

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        assert restored.last_successful_open_mono == 1717401500.0

    def test_none_cooldown_persists_as_none(self, tmp_state_dir):
        """None cooldown value is preserved."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        state.last_successful_open_mono = None

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        assert restored.last_successful_open_mono is None


# --- TEST: FAILED SETUP PERSISTENCE ------------------------------------------

class TestFailedSetupPersistence:
    def test_valid_failures_restored(self, tmp_state_dir):
        """Recent failed setups within TTL are restored."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        now = time.time()
        # Add recent failures (within TTL)
        state.last_failed_setups = deque([
            (1.1000, 1.1002, now - 100, "ENGULFING"),  # 100s ago — valid
            (1.0950, 1.0952, now - 500, "HAMMER"),     # 500s ago — valid
        ], maxlen=20)

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        assert len(restored.last_failed_setups) == 2

    def test_expired_failures_discarded(self, tmp_state_dir):
        """Expired failed setups (beyond TTL) are NOT restored."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        now = time.time()
        state.last_failed_setups = deque([
            (1.1000, 1.1002, now - 100, "ENGULFING"),       # Valid (100s)
            (1.0950, 1.0952, now - 5000, "HAMMER"),         # Expired (5000s > 1800s TTL)
        ], maxlen=20)

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        assert len(restored.last_failed_setups) == 1
        assert restored.last_failed_setups[0][3] == "ENGULFING"

    def test_empty_failures_handled(self, tmp_state_dir):
        """Empty last_failed_setups persists and restores cleanly."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        state.last_failed_setups = deque([], maxlen=20)

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        # Should have default empty deque
        assert len(restored.last_failed_setups) == 0


# --- TEST: STRUCTURE PERSISTENCE ----------------------------------------------

class TestStructurePersistence:
    def test_structure_score_persists(self, tmp_state_dir):
        """structure_score survives restart."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        state.structure_score = 4.2
        state.structure_regime = "CONFIRMED"

        save_engine_states([("EURUSD", state)])
        restored = load_engine_state("EURUSD")

        assert restored is not None
        assert restored.structure_score == 4.2
        assert restored.structure_regime == "CONFIRMED"


# --- TEST: REGIME VALIDATION --------------------------------------------------

class TestRegimeValidation:
    def test_trend_up_accepted(self, tmp_state_dir):
        """TREND_UP is a valid regime that can be restored."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "TREND_UP"
        save_engine_states([("EURUSD", state)])

        restored = load_engine_state("EURUSD")
        assert restored is not None
        assert restored.regime_state == "TREND_UP"

    def test_trend_down_accepted(self, tmp_state_dir):
        """TREND_DOWN is a valid regime that can be restored."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "TREND_DOWN"
        save_engine_states([("EURUSD", state)])

        restored = load_engine_state("EURUSD")
        assert restored is not None
        assert restored.regime_state == "TREND_DOWN"

    def test_invalid_regime_rejected(self, tmp_state_dir):
        """Invalid regime causes restore to be skipped."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        save_engine_states([("EURUSD", state)])

        # Corrupt the file
        filepath = tmp_state_dir / "EURUSD.json"
        data = json.loads(filepath.read_text())
        data["regime_state"] = "INVALID_REGIME"
        filepath.write_text(json.dumps(data))

        restored = load_engine_state("EURUSD")
        assert restored is None


# --- TEST: CORRUPT STATE HANDLING ---------------------------------------------

class TestCorruptStateHandling:
    def test_invalid_json_returns_none(self, tmp_state_dir):
        """Corrupted JSON file ? returns None (no crash)."""
        filepath = tmp_state_dir / "EURUSD.json"
        filepath.write_text("{{not valid json")

        restored = load_engine_state("EURUSD")
        assert restored is None

    def test_invalid_bias_phase_rejected(self, tmp_state_dir):
        """Invalid bias_phase ? restore skipped."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        save_engine_states([("EURUSD", state)])

        filepath = tmp_state_dir / "EURUSD.json"
        data = json.loads(filepath.read_text())
        data["bias_phase"] = "INVALID_PHASE"
        filepath.write_text(json.dumps(data))

        restored = load_engine_state("EURUSD")
        assert restored is None

    def test_stale_snapshot_rejected(self, tmp_state_dir):
        """Snapshot older than max age ? restore skipped."""
        state = EngineState()
        state.bias_phase = "EXPIRED"
        state.regime_state = "RANGING"
        save_engine_states([("EURUSD", state)])

        # Backdate the timestamp
        filepath = tmp_state_dir / "EURUSD.json"
        data = json.loads(filepath.read_text())
        data["_meta"]["saved_at_unix"] = time.time() - 200000  # Way beyond max age
        filepath.write_text(json.dumps(data))

        restored = load_engine_state("EURUSD")
        assert restored is None

    def test_missing_file_returns_none(self, tmp_state_dir):
        """No file for symbol ? returns None."""
        restored = load_engine_state("NONEXISTENT_SB")
        assert restored is None


# --- TEST: RESTART SIMULATION -------------------------------------------------

class TestRestartSimulation:
    def test_full_restart_cycle(self, tmp_state_dir):
        """Full save ? shutdown ? restore cycle preserves state."""
        # Before restart
        state = EngineState()
        state.bias_phase = "CONFIRMED"
        state.current_bias = Side.BUY
        state.regime_state = "TREND_UP"
        state.bias_strength = 80.0
        state.bias_age_seconds = 600.0
        state.last_successful_open_mono = 1717400500.0
        state.structure_score = 3.8
        state.structure_regime = "CONFIRMED"

        now = time.time()
        state.last_failed_setups = deque([
            (1.1050, 1.1052, now - 300, "THREE_WHITE_SOLDIERS"),
        ], maxlen=20)

        # Save (shutdown)
        save_engine_states([("EURUSD", state)])

        # Restore (startup)
        restored = load_engine_state("EURUSD")

        # Verify all critical state preserved
        assert restored is not None
        assert restored.bias_phase == "CONFIRMED"
        assert restored.current_bias == Side.BUY
        assert restored.regime_state == "TREND_UP"
        assert restored.bias_strength == 80.0
        assert restored.last_successful_open_mono == 1717400500.0
        assert restored.structure_score == 3.8
        assert restored.structure_regime == "CONFIRMED"
        assert len(restored.last_failed_setups) == 1
        assert restored.last_failed_setups[0][3] == "THREE_WHITE_SOLDIERS"
