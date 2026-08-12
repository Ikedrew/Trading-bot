"""
Shadow Data Quality Measurement Script (read-only).
Streams shadow_trades JSONL files and produces statistics.
Does NOT modify any data.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

def main():
    shadow_dir = Path("logs/shadow_trades")
    if not shadow_dir.exists():
        print("ERROR: logs/shadow_trades/ not found")
        sys.exit(1)

    total = 0
    valid_eid = 0
    empty_eid = 0
    valid_r = 0
    null_r = 0
    hshadow = 0
    primary = 0
    other_type = 0
    entities = set()
    entity_counts = defaultdict(int)
    horizons = defaultdict(int)
    exits = defaultdict(int)
    symbols = defaultdict(int)
    files_processed = 0

    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir():
            continue
        for f in sorted(sym_dir.glob("*.jsonl")):
            files_processed += 1
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    total += 1

                    identity = rec.get("identity", {})
                    outcome = rec.get("simulated_outcome", {})

                    eid = identity.get("entity_id", "")
                    tid = identity.get("trade_id", "")
                    sym = identity.get("symbol", "UNKNOWN")
                    r = outcome.get("pnl_r_multiple")

                    if eid:
                        valid_eid += 1
                        entities.add(eid)
                        entity_counts[eid] += 1
                    else:
                        empty_eid += 1

                    if r is not None:
                        valid_r += 1
                    else:
                        null_r += 1

                    if tid.startswith("hshadow_"):
                        hshadow += 1
                        parts = tid.split("_")
                        h = parts[-1] if len(parts) >= 4 else "UNKNOWN"
                        horizons[h] += 1
                    elif tid.startswith("shadow_"):
                        primary += 1
                    else:
                        other_type += 1

                    symbols[sym] += 1
                    exits[outcome.get("exit_reason", "NONE")] += 1

    multi = sum(1 for c in entity_counts.values() if c > 1)
    max_per = max(entity_counts.values()) if entity_counts else 0

    print("=" * 60)
    print("SHADOW DATA QUALITY MEASUREMENT")
    print("=" * 60)
    print(f"Files processed:        {files_processed}")
    print(f"Total records:          {total}")
    print(f"Valid entity_id:        {valid_eid} ({valid_eid*100//max(total,1)}%)")
    print(f"Empty entity_id:        {empty_eid} ({empty_eid*100//max(total,1)}%)")
    print(f"Valid R-multiple:       {valid_r} ({valid_r*100//max(total,1)}%)")
    print(f"Null R-multiple:        {null_r}")
    print(f"Unique entity_ids:      {len(entities)}")
    print(f"Entities with >1 shadow:{multi}")
    print(f"Max shadows per entity: {max_per}")
    print(f"Avg shadows per entity: {total/max(len(entities),1):.2f}")
    print(f"Horizon shadows:        {hshadow}")
    print(f"Primary shadows:        {primary}")
    print(f"Other type:             {other_type}")
    print(f"Horizons:               {dict(horizons)}")
    print(f"Symbols:                {dict(symbols)}")
    print(f"Exit reasons:           {dict(exits)}")
    print("=" * 60)

    # Now measure join coverage against decision_trace
    dt_dir = Path("logs/decision_trace")
    if dt_dir.exists():
        dt_entities = set()
        dt_total = 0
        dt_execute = 0
        dt_no_trade = 0
        for sym_dir in sorted(dt_dir.iterdir()):
            if not sym_dir.is_dir():
                continue
            for f in sorted(sym_dir.glob("*.jsonl")):
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        dt_total += 1
                        eid = rec.get("entity_id", "")
                        if eid:
                            dt_entities.add(eid)
                        action = rec.get("action", "")
                        if action == "EXECUTE":
                            dt_execute += 1
                        elif action == "NO_TRADE":
                            dt_no_trade += 1

        joined = entities & dt_entities
        shadow_only = entities - dt_entities
        decision_only = dt_entities - entities

        print()
        print("=" * 60)
        print("JOIN COVERAGE: Shadow <-> Decision")
        print("=" * 60)
        print(f"Decision records:       {dt_total}")
        print(f"Decision unique eids:   {len(dt_entities)}")
        print(f"Decision EXECUTE:       {dt_execute}")
        print(f"Decision NO_TRADE:      {dt_no_trade}")
        print(f"Shadow unique eids:     {len(entities)}")
        print(f"Joined (both):          {len(joined)}")
        print(f"Shadow-only (orphan):   {len(shadow_only)}")
        print(f"Decision-only (no shd): {len(decision_only)}")
        print(f"Join rate (shadow->dec):{len(joined)*100//max(len(entities),1)}%")
        print(f"Coverage (dec->shadow): {len(joined)*100//max(len(dt_entities),1)}%")
        print("=" * 60)
    else:
        print("\nWARNING: logs/decision_trace/ not found — join coverage not measured")

if __name__ == "__main__":
    main()
