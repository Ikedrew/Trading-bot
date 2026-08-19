"""
Manual Discord pipeline test only.
Run this file directly to verify webhook connectivity.
Do NOT import or auto-run from any runtime module.
"""

from core.discord_notifier import send_discord

if __name__ == "__main__":
    send_discord("system-status", "🔥 TEST: Discord pipeline working")
    print("Test message sent (check Discord)")
