"""Check what shadow records have empty entity_id and what type they are."""
import json
from pathlib import Path

shadow_dir = Path("logs/shadow_trades/EURUSD")
empty_eid = []
has_eid = []

for f in sorted(shadow_dir.glob("*.jsonl")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        eid = rec.get("identity", {}).get("entity_id", "") or ""
        tid = rec.get("identity", {}).get("trade_id", "") or ""
        sv = rec.get("schema_version", "")
        if not eid or eid == "None":
            empty_eid.append({"tid": tid[:50], "sv": sv})
        else:
            has_eid.append({"tid": tid[:50], "eid": eid[:30]})

print(f"EURUSD: {len(empty_eid)} empty, {len(has_eid)} with eid")
print(f"\nFirst 5 EMPTY entity_id records:")
for r in empty_eid[:5]:
    print(f"  trade_id={r['tid']}  schema={r['sv']}")
print(f"\nFirst 5 WITH entity_id records:")
for r in has_eid[:5]:
    print(f"  trade_id={r['tid']}  entity_id={r['eid']}")
