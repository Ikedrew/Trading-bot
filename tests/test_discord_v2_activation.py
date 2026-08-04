"""Discord V2 Activation Tests — V2 active, legacy disabled.

Proves:
1. S3 persistence still happens when ENABLE_DISCORD_V2=True
2. Legacy webhook is NOT called when V2 is active
3. V2 renderer IS called when V2 is active
4. Legacy webhook IS called when V2 is disabled (backwards compat)
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestS3PersistsRegardless:
    """S3 persistence is always called regardless of Discord mode."""

    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.aws_uploader.upload_event")
    def test_s3_called_with_v2_enabled(self, mock_s3, mock_render):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = True
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("ORDER_FILLED", {"symbol": "EURUSD", "fill_price": 1.08})
            mock_s3.assert_called_once()
            payload = mock_s3.call_args[0][0]
            assert payload["event_type"] == "ORDER_FILLED"
            assert payload["symbol"] == "EURUSD"
        finally:
            cfg.ENABLE_DISCORD_V2 = original

    @patch("core.discord_notifier.send_discord")
    @patch("core.aws_uploader.upload_event")
    def test_s3_called_with_v2_disabled(self, mock_s3, mock_discord):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = False
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("ERROR", {"error_type": "TestError"})
            mock_s3.assert_called_once()
        finally:
            cfg.ENABLE_DISCORD_V2 = original


class TestLegacyDisabledWhenV2Active:
    """Legacy webhook is skipped when ENABLE_DISCORD_V2=True."""

    @patch("core.discord_notifier.send_discord")
    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.aws_uploader.upload_event")
    def test_legacy_webhook_not_called(self, mock_s3, mock_render, mock_legacy):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = True
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("ORDER_FILLED", {"symbol": "GBPUSD"})
            mock_legacy.assert_not_called()
        finally:
            cfg.ENABLE_DISCORD_V2 = original

    @patch("core.discord_notifier.send_discord")
    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.aws_uploader.upload_event")
    def test_legacy_webhook_called_when_v2_disabled(self, mock_s3, mock_render, mock_legacy):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = False
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("ORDER_FILLED", {"symbol": "GBPUSD"})
            # Legacy should fire (channel mapped)
            mock_legacy.assert_called()
        finally:
            cfg.ENABLE_DISCORD_V2 = original


class TestV2RendererReceivesEvents:
    """V2 renderer is called when ENABLE_DISCORD_V2=True."""

    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.aws_uploader.upload_event")
    def test_v2_renderer_called(self, mock_s3, mock_render):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = True
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("SYSTEM_STARTUP", {"mode": "TEST"})
            mock_render.assert_called_once_with("SYSTEM_STARTUP", {"mode": "TEST"})
        finally:
            cfg.ENABLE_DISCORD_V2 = original

    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.aws_uploader.upload_event")
    def test_v2_renderer_not_called_when_disabled(self, mock_s3, mock_render):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = False
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("SYSTEM_STARTUP", {"mode": "TEST"})
            mock_render.assert_not_called()
        finally:
            cfg.ENABLE_DISCORD_V2 = original


class TestCompleteFlow:
    """Full flow: S3 + V2, no legacy."""

    @patch("core.discord.renderer.DiscordRenderer.render")
    @patch("core.discord_notifier.send_discord")
    @patch("core.aws_uploader.upload_event")
    def test_full_flow_v2_active(self, mock_s3, mock_legacy, mock_v2):
        import core.config as cfg
        original = cfg.ENABLE_DISCORD_V2
        cfg.ENABLE_DISCORD_V2 = True
        try:
            from core.log_router import StructuredLogger
            logger = StructuredLogger()
            logger.event("RISK_BLOCK", {"symbol": "USDJPY", "guard": "spread"})

            # S3 always called
            mock_s3.assert_called_once()
            # Legacy NOT called
            mock_legacy.assert_not_called()
            # V2 called
            mock_v2.assert_called_once_with("RISK_BLOCK", {"symbol": "USDJPY", "guard": "spread"})
        finally:
            cfg.ENABLE_DISCORD_V2 = original
