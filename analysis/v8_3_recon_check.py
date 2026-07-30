"""V8.3 check — examine data available for trade reconstruction."""
import json
from pathlib import Path

# 1. Check shadow trade schema for NAS100
print("=== NAS100 SHADOW TRADE SCHEMA ===")
d = Path("logs/shadow_trades/NAS100")
if d.exists():
    for f in d.glob("*.jsonl"):
        line = open(f).readline()
        r = json.loads(line)
        snap = r.get("decision_snapshot", {})
        out = r.get("simulated_outcome", {})
        print(f"Snap keys: {sorted(snap.keys())}")
        print(f"Outcome keys: {sorted(out.keys())}")
        print(f"Direction: {snap.get('direction')}")
        print(f"Entry: {snap.get('entry_price')}")
        print(f"Stop: {snap.get('stop_loss')}")
        print(f"Target: {snap.get('take_profit')}")
        print(f"Stop pips: {snap.get('stop_distance_pips')}")
        print(f"Timestamp: {snap.get('timestamp_decision_utc')}")
        break

# 2. Check market context for NAS100
print("\n=== NAS100 MARKET CONTEXT ===")
ctx_dir = Path("logs/v3_shadow/market_context/NAS100")
if ctx_dir.exists():
    for f in ctx_dir.glob("*.jsonl"):
        line = open(f).readline()
        r = json.loads(line)
        loc = r.get("location", {})
        print(f"Location keys: {sorted(loc.keys())}")
        print(f"Zone quality: {loc.get('zone_quality')}")
        print(f"Nearest liq above: {loc.get('nearest_liquidity_direction')}")
        print(f"Supply zones: {loc.get('supply_zones_nearby')}")
        print(f"Demand zones: {loc.get('demand_zones_nearby')}")
        break
else:
    print("  NO NAS100 market_context directory")

# 3. Check market understanding for structural levels
print("\n=== NAS100 MARKET UNDERSTANDING ===")
mu_dir = Path("logs/v3_shadow/market_understanding/NAS100")
if mu_dir.exists():
    for f in mu_dir.glob("*.jsonl"):
        line = open(f).readline()
        r = json.loads(line)
        h1 = r.get("h1", {})
        m15 = r.get("m15", {})
        print(f"H1 keys: {sorted(h1.keys())}")
        print(f"H1 swing_high: {h1.get('swing_high')}")
        print(f"H1 swing_low: {h1.get('swing_low')}")
        print(f"M15 swing_high: {m15.get('swing_high')}")
        print(f"M15 swing_low: {m15.get('swing_low')}")
        break
else:
    print("  NO NAS100 market_understanding directory")

# 4. Check candle data availability
print("\n=== CANDLE DATA ===")
for sym in ["NAS100", "US500"]:
    cd = Path(f"replay_data/{sym}/5")
    if cd.exists():
        files = list(cd.glob("*.jsonl"))
        total_lines = 0
        for f in files[:2]:
            total_lines += sum(1 for _ in open(f))
        print(f"  {sym}: {len(files)} files, ~{total_lines} candles in first 2")
    else:
        print(f"  {sym}: no M5 data")

print("\nDONE")
