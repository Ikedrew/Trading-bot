"""
Discord Multi-Channel Webhook Notifier — Production trading bot alerting.

Sends structured messages to Discord channels via per-channel webhook URLs.
Synchronous. Fail-safe. No external dependencies beyond requests.
"""

import requests

from core.config import DISCORD_WEBHOOKS


def send_discord(channel: str, message: str) -> None:
    """
    Send a message to a Discord channel via its configured webhook.

    Args:
        channel: Channel name key (must exist in DISCORD_WEBHOOKS).
        message: Text content to send.

    Behaviour:
        - If ALERTING_ENABLED is False: silently returns.
        - If LEGACY_DISCORD_ENABLED is False: silently returns (V2 active).
        - If channel key is missing or URL is empty: silently returns.
        - If HTTP request fails: silently returns (prints error to console).
        - Never raises. Never blocks trading logic beyond the timeout.
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "ALERTING_ENABLED", True):
            return
        if not getattr(_cfg, "LEGACY_DISCORD_ENABLED", True):
            return
    except ImportError:
        pass

    url = DISCORD_WEBHOOKS.get(channel)
    if not url:
        return

    try:
        requests.post(url, json={"content": message}, timeout=2)
    except Exception as e:
        print(f"[DISCORD_ERROR] channel={channel} error={type(e).__name__}: {e}")
