"""
Manual diagnostic: Test ALL Discord webhook channels.
Run directly: python -m core.test_all_channels

Sends one test message to EVERY configured channel.
This verifies webhook health, routing, and permissions.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import DISCORD_WEBHOOKS
from core.discord_notifier import send_discord

if __name__ == "__main__":
    print(f"\n[CHANNEL HEALTH CHECK] Testing {len(DISCORD_WEBHOOKS)} channels...\n")

    results = {}
    for channel, url in DISCORD_WEBHOOKS.items():
        if not url:
            print(f"  ⬜ {channel:20s} — SKIPPED (no URL)")
            results[channel] = "SKIPPED"
            continue

        print(f"  🔄 {channel:20s} — sending...", end=" ")
        try:
            import requests
            resp = requests.post(url, json={"content": f"🧪 **CHANNEL TEST** | `{channel}` | routing verified"}, timeout=5)
            if resp.status_code < 300:
                print(f"✅ OK (HTTP {resp.status_code})")
                results[channel] = "OK"
            else:
                print(f"❌ FAILED (HTTP {resp.status_code}: {resp.text[:80]})")
                results[channel] = f"HTTP_{resp.status_code}"
        except Exception as e:
            print(f"❌ ERROR ({type(e).__name__}: {e})")
            results[channel] = f"ERROR:{type(e).__name__}"

    print(f"\n{'='*60}")
    print(f"[SUMMARY]")
    ok = sum(1 for v in results.values() if v == "OK")
    fail = sum(1 for v in results.values() if v not in ("OK", "SKIPPED"))
    skip = sum(1 for v in results.values() if v == "SKIPPED")
    print(f"  ✅ OK:      {ok}")
    print(f"  ❌ FAILED:  {fail}")
    print(f"  ⬜ SKIPPED: {skip}")
    print(f"{'='*60}\n")

    if fail > 0:
        print("[FAILURES]")
        for ch, status in results.items():
            if status not in ("OK", "SKIPPED"):
                print(f"  {ch} → {status}")
        print()
