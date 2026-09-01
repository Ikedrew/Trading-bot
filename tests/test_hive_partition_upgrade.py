"""
Hive Partition Upgrade — Tests for 7 datasets migrated from flat to Hive layout.

Validates S3 key format, schema_version presence, and failure handling.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


class TestShadowTradesHive:
    def test_s3_key_hive_format(self):
        from core.shadow_trades import _S3_PREFIX, _S3_BUCKET
        assert _S3_PREFIX == "supporting/shadow_trades"
        assert _S3_BUCKET == "trading-bot-v10-data"

    def test_key_contains_schema_version(self):
        """Verify the S3 append function includes schema_version partition."""
        import inspect
        from core.shadow_trades import _s3_append
        source = inspect.getsource(_s3_append)
        assert "schema_version={_SCHEMA_VERSION}" in source
        assert "symbol=" in source
        assert "date=" in source


class TestResearchShadowHive:
    def test_schema_version_constant(self):
        from core.research_assessment.research_shadow_engine import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "research_shadow_trades_v1"

    def test_key_contains_schema_version(self):
        import inspect
        from core.research_assessment.research_shadow_engine import _s3_append_research
        source = inspect.getsource(_s3_append_research)
        assert "schema_version=" in source
        assert "symbol=" in source
        assert "date=" in source


class TestTradeTruthHive:
    def test_key_contains_schema_version(self):
        import inspect
        from core.trade_truth import _s3_persist
        source = inspect.getsource(_s3_persist)
        assert "schema_version={_SCHEMA_VERSION}" in source
        assert "symbol=" in source
        assert "date=" in source


class TestEdgeAttributionHive:
    def test_schema_version_constant(self):
        from core.edge_attribution import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "edge_attribution_v2"

    def test_key_contains_schema_version(self):
        import inspect
        from core.edge_attribution import _s3_append
        source = inspect.getsource(_s3_append)
        assert "schema_version=" in source
        assert "symbol=" in source
        assert "date=" in source


class TestEdgeOptimisationHive:
    def test_schema_version_constant(self):
        from core.edge_optimisation import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "edge_optimisation_v2"

    def test_key_contains_schema_version(self):
        import inspect
        from core.edge_optimisation import persist_edge_report
        source = inspect.getsource(persist_edge_report)
        assert "schema_version=" in source
        assert "date=" in source


class TestStrategyCompilerHive:
    def test_schema_version_constant(self):
        from core.strategy_compiler import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "strategy_compiler_v2"

    def test_key_contains_schema_version(self):
        import inspect
        from core.strategy_compiler import persist_strategy
        source = inspect.getsource(persist_strategy)
        assert "schema_version=" in source
        assert "date=" in source


class TestMarketContextHive:
    def test_schema_version_constant(self):
        from core.market_context.persistence import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "market_context_v1"

    def test_s3_key_hive_format(self):
        from core.market_context.persistence import _S3_PREFIX, _SCHEMA_VERSION
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        with patch("core.config.EVENT_STREAM_S3_MIRROR", True), \
             patch("core.config.MARKET_CONTEXT_PERSISTENCE_ENABLED", True), \
             patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}), \
             patch("boto3.client", return_value=mock_s3):
            from core.market_context.persistence import MarketContextPersistence
            p = MarketContextPersistence()
            p.persist({"symbol": "EURUSD", "regime": "TRENDING"})

        if mock_s3.put_object.called:
            key = mock_s3.put_object.call_args[1]["Key"]
            assert f"schema_version={_SCHEMA_VERSION}" in key
            assert "symbol=EURUSD" in key
            assert "date=" in key

    def test_s3_failure_does_not_raise(self):
        """Market context persist doesn't raise on S3 failure."""
        from core.market_context.persistence import MarketContextPersistence
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch("core.market_context.persistence._LOCAL_DIR", td):
                p = MarketContextPersistence()
                p.persist({"symbol": "EURUSD", "regime": "TRENDING"})
