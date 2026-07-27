"""
Tests for G4: Configuration Profile Loader.

Covers:
- Profile loads successfully and overrides apply
- Invalid/unknown keys crash startup
- Type mismatches crash startup
- Missing OVERRIDES dict crashes startup
- Profile not found crashes startup
- Environment overrides work (final priority)
- No profile selected = base config only
- Active profile is logged/stored
- Base config remains untouched after profile switch
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_profile_loader import (
    ConfigProfileError,
    load_and_apply_profile,
    _validate_overrides,
    _coerce_env_value,
    _apply_overrides,
    _load_profile_module,
    PROFILE_ENV_VAR,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env():
    """Ensure TRADING_PROFILE is not set unless test sets it."""
    old = os.environ.pop(PROFILE_ENV_VAR, None)
    yield
    if old is not None:
        os.environ[PROFILE_ENV_VAR] = old
    else:
        os.environ.pop(PROFILE_ENV_VAR, None)


@pytest.fixture
def mock_config():
    """Create a mock config module with known attributes."""
    class _Config:
        MAX_DRAWDOWN_PERCENT = 10.0
        DAILY_LOSS_LIMIT_PERCENT = 4.0
        MAX_TOTAL_OPEN_POSITIONS = 3
        MAX_TOTAL_RISK_EXPOSURE_PCT = 3.0
        RISK_PER_TRADE_PERCENT = 1.0
        ENABLE_DRAWDOWN_GUARD = True
        ENABLE_DAILY_LOSS_LIMIT = True
        TRADING_HOURS_START_UTC = 7
        TRADING_HOURS_END_UTC = 21
        FIXED_LOT = 0.01
        BLOCK_FRIDAY_AFTER_HOUR = 20
        MAX_TRADES_PER_DAY_TOTAL = 20
        MAX_TRADES_PER_DAY_PER_SYMBOL = 5
    return _Config()


# ─── TEST: PROFILE LOADS SUCCESSFULLY ──────────────────────────────────────────

class TestProfileLoads:
    def test_retail_profile_loads(self):
        """Retail profile module loads and has valid OVERRIDES."""
        import profiles.retail as retail
        assert retail.PROFILE_NAME == "retail"
        assert isinstance(retail.OVERRIDES, dict)
        assert len(retail.OVERRIDES) > 0

    def test_prop_challenge_profile_loads(self):
        """Prop challenge profile loads correctly."""
        import profiles.prop_challenge as prop
        assert prop.PROFILE_NAME == "prop_challenge"
        assert isinstance(prop.OVERRIDES, dict)
        assert "DAILY_LOSS_LIMIT_PERCENT" in prop.OVERRIDES

    def test_prop_funded_profile_loads(self):
        """Prop funded profile loads correctly."""
        import profiles.prop_funded as prop
        assert prop.PROFILE_NAME == "prop_funded"
        assert isinstance(prop.OVERRIDES, dict)
        assert prop.OVERRIDES["MAX_DRAWDOWN_PERCENT"] == 5.0


# ─── TEST: OVERRIDES APPLY CORRECTLY ──────────────────────────────────────────

class TestOverridesApply:
    def test_overrides_modify_config(self, mock_config):
        """Profile overrides change config values."""
        overrides = {
            "MAX_DRAWDOWN_PERCENT": 15.0,
            "MAX_TOTAL_OPEN_POSITIONS": 5,
        }
        count = _apply_overrides(mock_config, overrides)

        assert count == 2
        assert mock_config.MAX_DRAWDOWN_PERCENT == 15.0
        assert mock_config.MAX_TOTAL_OPEN_POSITIONS == 5

    def test_full_profile_integration(self, clean_env):
        """Loading a profile via env var applies all overrides."""
        os.environ[PROFILE_ENV_VAR] = "retail"

        from core import config
        import profiles.retail as retail

        # Save all keys that will be overridden
        originals = {}
        for key in retail.OVERRIDES:
            if hasattr(config, key):
                originals[key] = getattr(config, key)

        try:
            result = load_and_apply_profile()

            assert result == "retail"
            # Retail sets MAX_DRAWDOWN_PERCENT = 15.0
            assert config.MAX_DRAWDOWN_PERCENT == 15.0
            assert config.ACTIVE_PROFILE == "retail"
        finally:
            # Restore all originals
            for key, val in originals.items():
                setattr(config, key, val)
            if hasattr(config, "ACTIVE_PROFILE"):
                delattr(config, "ACTIVE_PROFILE")

    def test_prop_funded_profile_applies(self, clean_env):
        """Prop funded profile sets conservative values."""
        os.environ[PROFILE_ENV_VAR] = "prop_funded"

        from core import config
        originals = {
            "MAX_DRAWDOWN_PERCENT": config.MAX_DRAWDOWN_PERCENT,
            "DAILY_LOSS_LIMIT_PERCENT": config.DAILY_LOSS_LIMIT_PERCENT,
            "MAX_TOTAL_OPEN_POSITIONS": config.MAX_TOTAL_OPEN_POSITIONS,
        }

        load_and_apply_profile()

        assert config.MAX_DRAWDOWN_PERCENT == 5.0
        assert config.DAILY_LOSS_LIMIT_PERCENT == 3.0
        assert config.MAX_TOTAL_OPEN_POSITIONS == 2

        # Restore
        for key, val in originals.items():
            setattr(config, key, val)
        if hasattr(config, "ACTIVE_PROFILE"):
            delattr(config, "ACTIVE_PROFILE")


# ─── TEST: UNKNOWN KEYS CRASH STARTUP ─────────────────────────────────────────

class TestUnknownKeys:
    def test_unknown_key_rejected(self, mock_config):
        """Unknown config key in profile causes validation failure."""
        overrides = {
            "MAX_DRAWDOWN_PERCENT": 10.0,
            "TOTALLY_FAKE_KEY": 42,
        }
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 1
        assert "TOTALLY_FAKE_KEY" in errors[0]
        assert "Unknown" in errors[0]

    def test_multiple_unknown_keys_all_reported(self, mock_config):
        """All unknown keys are reported."""
        overrides = {
            "FAKE_A": 1,
            "FAKE_B": 2,
            "MAX_DRAWDOWN_PERCENT": 5.0,
        }
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 2


# ─── TEST: TYPE MISMATCH CRASHES STARTUP ───────────────────────────────────────

class TestTypeMismatch:
    def test_string_where_float_expected(self, mock_config):
        """String value for float config key is rejected."""
        overrides = {"MAX_DRAWDOWN_PERCENT": "ten"}
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 1
        assert "Type mismatch" in errors[0]

    def test_int_for_float_allowed(self, mock_config):
        """Int value for float key is allowed (numeric interchangeable)."""
        overrides = {"MAX_DRAWDOWN_PERCENT": 10}
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 0

    def test_float_for_int_allowed(self, mock_config):
        """Float value for int key is allowed (numeric interchangeable)."""
        overrides = {"MAX_TOTAL_OPEN_POSITIONS": 3.0}
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 0

    def test_bool_for_int_rejected(self, mock_config):
        """Bool where int expected is rejected (even though bool is int subclass)."""
        overrides = {"MAX_TOTAL_OPEN_POSITIONS": True}
        errors = _validate_overrides(overrides, mock_config)
        assert len(errors) == 1
        assert "Type mismatch" in errors[0]


# ─── TEST: MISSING OVERRIDES DICT ─────────────────────────────────────────────

class TestMissingOverrides:
    def test_no_overrides_dict_crashes(self, clean_env):
        """Profile without OVERRIDES dict causes startup failure."""
        # Create a fake module without OVERRIDES
        fake_module = MagicMock(spec=[])  # No attributes

        with patch("core.config_profile_loader.importlib.import_module", return_value=fake_module):
            with pytest.raises(SystemExit) as exc_info:
                _load_profile_module("bad_profile")
            assert "OVERRIDES" in str(exc_info.value)


# ─── TEST: PROFILE NOT FOUND ──────────────────────────────────────────────────

class TestProfileNotFound:
    def test_nonexistent_profile_crashes(self, clean_env):
        """Loading a profile that doesn't exist causes startup failure."""
        with pytest.raises(SystemExit) as exc_info:
            _load_profile_module("nonexistent_profile_xyz")
        assert "not found" in str(exc_info.value)


# ─── TEST: ENVIRONMENT OVERRIDES ──────────────────────────────────────────────

class TestEnvironmentOverrides:
    def test_env_var_overrides_config(self, clean_env):
        """Environment variable takes final priority over profile."""
        os.environ[PROFILE_ENV_VAR] = "retail"
        # Set an env var that overrides the profile value
        os.environ["MAX_DRAWDOWN_PERCENT"] = "99.0"

        from core import config
        original = config.MAX_DRAWDOWN_PERCENT

        try:
            load_and_apply_profile()
            # Retail sets 15.0, but env var overrides to 99.0
            assert config.MAX_DRAWDOWN_PERCENT == 99.0
        finally:
            config.MAX_DRAWDOWN_PERCENT = original
            os.environ.pop("MAX_DRAWDOWN_PERCENT", None)
            if hasattr(config, "ACTIVE_PROFILE"):
                delattr(config, "ACTIVE_PROFILE")

    def test_env_bool_coercion(self):
        """Bool env var values are correctly coerced."""
        assert _coerce_env_value("true", True) is True
        assert _coerce_env_value("false", True) is False
        assert _coerce_env_value("1", True) is True
        assert _coerce_env_value("0", True) is False

    def test_env_int_coercion(self):
        """Int env var values are correctly coerced."""
        assert _coerce_env_value("42", 0) == 42
        assert _coerce_env_value("0", 1) == 0

    def test_env_float_coercion(self):
        """Float env var values are correctly coerced."""
        assert _coerce_env_value("3.14", 0.0) == pytest.approx(3.14)
        assert _coerce_env_value("0.5", 1.0) == pytest.approx(0.5)


# ─── TEST: NO PROFILE SELECTED ────────────────────────────────────────────────

class TestNoProfile:
    def test_no_env_var_returns_none(self, clean_env):
        """Without TRADING_PROFILE env var, no profile is applied."""
        result = load_and_apply_profile()
        assert result is None

    def test_empty_env_var_returns_none(self, clean_env):
        """Empty TRADING_PROFILE env var = no profile."""
        os.environ[PROFILE_ENV_VAR] = ""
        result = load_and_apply_profile()
        assert result is None


# ─── TEST: ACTIVE PROFILE STORED ──────────────────────────────────────────────

class TestActiveProfileStored:
    def test_active_profile_on_config(self, clean_env):
        """After profile load, config.ACTIVE_PROFILE is set."""
        os.environ[PROFILE_ENV_VAR] = "prop_challenge"

        from core import config
        originals = {}
        import profiles.prop_challenge as pc
        for key in pc.OVERRIDES:
            if hasattr(config, key):
                originals[key] = getattr(config, key)

        try:
            load_and_apply_profile()
            assert config.ACTIVE_PROFILE == "prop_challenge"
        finally:
            for key, val in originals.items():
                setattr(config, key, val)
            if hasattr(config, "ACTIVE_PROFILE"):
                delattr(config, "ACTIVE_PROFILE")
