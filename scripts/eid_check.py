import json
from pathlib import Path

out = []
shadow_dir = Path("logs/shadow_trades/EURUSD")
empty = 0
has = 0
empty_tids = []
has_tids = []

for f in sorted(shadow_dir.glob("*.jsonl")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        rec = json.loads(line)
        eid = rec.get("identity", {}).get("entity_id", "") or ""
        tid = rec.get("identity", {}).get("trade_id", "") or ""
        if not eid or eid == "None":
            empty += 1
            if len(empty_tids) < 5:
                empty_tids.append(tid[:60])
        else:
            has += 1
            if len(has_tids) < 3:
                has_tids.append(f"{tid[:40]}|{eid[:25]}")

out.append(f"EURUSD: empty={empty}, has_eid={has}")
out.append(f"Empty samples: {empty_tids}")
out.append(f"Has samples: {has_tids}")

Path("scripts/eid_result.txt").write_text("\n".join(out), encoding="utf-8")
print("DONE")
