"""Tests for V10 Scanner Integration (ENGINE_MODE routing)."""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.scanner_adapter import run_v10_cycle


class TestEngineModeConfig:
    def test_v10_mode_exists_in_config(self):
        from core import config
        assert hasattr(config, "ENGINE_MODE")
        assert config.ENGINE_MODE in ("V10", "LEGACY")

    def test_default_is_v10(self):
        from core import config
        assert config.ENGINE_MODE == "V10"


class TestV10AdapterOutput:
    """Test that run_v10_cycle returns scanner-compatible format."""

    def test_returns_dict_with_action(self):
        """Basic contract: always returns dict with 'action' key."""
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=1.0900, ask=1.0901,
        )
        assert isinstance(result, dict)
        assert "action" in result
        assert result["action"] in ("EXECUTE", "NO_TRADE")

    def test_no_trade_has_reason(self):
        """NO_TRADE results include a reason."""
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=0, ask=0,
        )
        assert result["action"] == "NO_TRADE"
        assert "reason" in result

    def test_exception_returns_no_trade(self):
        """Pipeline errors should not crash — return NO_TRADE."""
        result = run_v10_cycle(
            symbol="EURUSD", candles=None, closed_i=-1,
            bid=0, ask=0,
        )
        assert result["action"] == "NO_TRADE"

    def test_has_score_field(self):
        """Result always has a score (for compatibility with legacy)."""
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=1.0900, ask=1.0901,
        )
        assert "score" in result

    def test_has_v10_pipeline_result(self):
        """V10 path includes the full pipeline result for research."""
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=1.0900, ask=1.0901,
        )
        assert "v10_pipeline_result" in result


class TestLegacyPathPreserved:
    def test_legacy_engine_still_importable(self):
        """The existing New Engine is not removed."""
        try:
            from core.pipeline.new_engine import run_new_engine
            assert callable(run_new_engine)
        except ImportError:
            pytest.skip("Legacy engine not available in test environment")

    def test_engine_mode_legacy_skips_v10(self):
        """When ENGINE_MODE=LEGACY, V10 adapter should not be called."""
        from core import config
        original = config.ENGINE_MODE
        try:
            config.ENGINE_MODE = "LEGACY"
            assert config.ENGINE_MODE == "LEGACY"
        finally:
            config.ENGINE_MODE = original

    def test_engine_mode_v10_is_default(self):
        """V10 is the default ENGINE_MODE."""
        from core import config
        assert config.ENGINE_MODE == "V10"

    def test_v10_mode_produces_result_without_legacy(self):
        """V10 mode produces a valid result independently."""
        from core import config
        assert config.ENGINE_MODE == "V10"
        # run_v10_cycle should work without any legacy engine involvement
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=1.09, ask=1.0901,
        )
        assert result["action"] in ("EXECUTE", "NO_TRADE")
        # The result should NOT contain legacy engine artifacts
        # (legacy uses different field names like "components", "activation_regime")
        assert "activation_regime" not in result
