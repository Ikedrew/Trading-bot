#!/usr/bin/env python
"""Quick final count: strategy_observations + market_context boundary files. READ-ONLY."""
import json, os

LOGS = r"C:\Users\ikues\Trading bot build\logs"
FRESH = 1787788800.0

for label in ("strategy_observations", "market_context"):
    d = os.path.join(LOGS, label)
    if not os.path.exists(d):
        print(label, ": MISSING")
        continue
    print("\n" + label)
    for _r, _d, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl"):
                continue
            if "2026-08-27" not in fn and "2026-08-26" not in fn:
                continue
            fp = os.path.join(_r, fn)
            tot = ent = can = can_empty = fresh = noent = 0
            mn = mx = None
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    tot += 1
                    if rec.get("entity_id"):
                        ent += 1
                    else:
                        noent += 1
                    if "canonical_opportunity_id" in rec:
                        if rec["canonical_opportunity_id"]:
                            can += 1
                        else:
                            can_empty += 1
                    ts = rec.get("timestamp_utc")
                    if isinstance(ts, (int, float)):
                        if ts >= FRESH:
                            fresh += 1
                        if mn is None or ts < mn:
                            mn = ts
                        if mx is None or ts > mx:
                            mx = ts
            print(f"  {os.path.relpath(fp, LOGS)}: tot={tot} entity={ent} noent={noent} "
                  f"canon_pop={can} canon_empty={can_empty} fresh_ts={fresh} min={mn} max={mx}")

print("\nDONE")