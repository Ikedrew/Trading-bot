#!/usr/bin/env python
"""READ-ONLY forensic scan of fresh (2026-08-27) runtime data for Phase-3 canonical_opportunity_id validation."""
import json, os, glob

ROOT = r"C:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")
TODAY = "2026-08-27"
YESTERDAY = "2026-08-26"

# Fresh boundary: new process started ~2026-08-27T00:03:09Z
# Data files with timestamp_utc >= 2026-08-27T00:00:00 are considered FRESH

datasets = [
    "opportunities",
    "execution_context",
    "execution_results",
    "risk_deviation",
    "strategy_observations",
    "decision_audit",
    "decision_ledger",
    "decision_trace",
    "market_context",
    "assessments",
]

def scan_dir(dirpath, date_filter=TODAY):
    """Scan all JSONL files in a directory that match the date filter."""
    results = {}
    if not os.path.exists(dirpath):
        return results
    for root, dirs, files in os.walk(dirpath):
        for fn in files:
            if fn.endswith(".jsonl"):
                if date_filter not in fn and not fn.endswith(date_filter + ".jsonl"):
                    # Also check for files like "AUDUSD_2026-08-27.jsonl"
                    if date_filter not in fn:
                        continue
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    total = len(lines)
                    canonical_refs = 0
                    populated = 0
                    empty = 0
                    no_field = 0
                    last_ts = ""
                    canonical_examples = []
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        ts = rec.get("timestamp_utc") or rec.get("timestamp_unix") or rec.get("detected_at_utc", "") or rec.get("recorded_at_utc", "") or rec.get("timestamp", "")
                        if ts:
                            last_ts = str(ts)
                        if "canonical_opportunity_id" in rec:
                            canonical_refs += 1
                            cid = rec["canonical_opportunity_id"]
                            if cid and cid != "" and cid != "null":
                                populated += 1
                                if len(canonical_examples) < 3:
                                    canonical_examples.append({"cid": cid, "entity_id": rec.get("entity_id",""), "ts": last_ts, "cycle": rec.get("cycle_id","")})
                            elif cid == "" or cid == "null":
                                empty += 1
                        else:
                            no_field += 1
                    results[fpath] = {
                        "total": total,
                        "canonical_refs": canonical_refs,
                        "populated": populated,
                        "empty": empty,
                        "no_field": no_field,
                        "last_ts": last_ts,
                        "examples": canonical_examples,
                    }
                except Exception as e:
                    results[fpath] = {"error": str(e)}
    return results

print("=" * 80)
print("PHASE 3 FRESH DATA AUDIT SCAN")
print("=" * 80)

for ds in datasets:
    dirpath = os.path.join(LOGS, ds)
    print(f"\n--- {ds} (fresh 2026-08-27 files) ---")
    results = scan_dir(dirpath, TODAY)
    if not results:
        print("  NO 2026-08-27 FILES FOUND")
        # Check if 2026-08-26 files were modified today
        results2 = scan_dir(dirpath, YESTERDAY)
        if results2:
            print(f"  (Found {YESTERDAY} files - checking if modified during fresh soak)")
    for fpath, info in sorted(results.items()):
        rel = os.path.relpath(fpath, ROOT)
        if "error" in info:
            print(f"  {rel}: ERROR - {info['error']}")
        else:
            print(f"  {rel}: total={info['total']}, canonical_refs={info['canonical_refs']}, populated={info['populated']}, empty={info['empty']}, no_field={info['no_field']}, last_ts={info['last_ts'][:50]}")
            for ex in info.get("examples", []):
                print(f"    example: cid={ex['cid'][:60]}, entity={ex['entity_id'][:40]}, ts={ex['ts'][:30]}, cycle={ex['cycle']}")

# Also check shadow data
print("\n--- shadow_runtime_v1 (2026-08-26 files, modified during soak) ---")
sr_dir = os.path.join(LOGS, "shadow_runtime_v1")
if os.path.exists(sr_dir):
    for root, dirs, files in os.walk(sr_dir):
        for fn in sorted(files):
            if fn.endswith(".jsonl"):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    events = {}
                    canonical_count = 0
                    shadow_trade_ids = set()
                    plan_ids = set()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        et = rec.get("event_type", "?")
                        events[et] = events.get(et, 0) + 1
                        cid = rec.get("canonical_opportunity_id", "")
                        if cid:
                            canonical_count += 1
                        tid = rec.get("shadow_trade_id", "")
                        if tid:
                            shadow_trade_ids.add(tid)
                        pid = rec.get("plan_id", "")
                        if pid:
                            plan_ids.add(pid)
                    rel = os.path.relpath(fpath, ROOT)
                    print(f"  {rel}: total={len(lines)}, events={events}, canonical_populated={canonical_count}, shadow_ids={len(shadow_trade_ids)}, plans={len(plan_ids)}")
                except Exception as e:
                    print(f"  {fpath}: ERROR - {e}")

# Check v3_shadow
print("\n--- v3_shadow (2026-08-27 files) ---")
v3_dir = os.path.join(LOGS, "v3_shadow")
if os.path.exists(v3_dir):
    for root, dirs, files in os.walk(v3_dir):
        for fn in sorted(files):
            if "2026-08-27" in fn and fn.endswith(".jsonl"):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    canonical_count = 0
                    shadow_trade_ids = set()
                    plan_ids = set()
                    event_types = {}
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        et = rec.get("event_type", "")
                        if et:
                            event_types[et] = event_types.get(et, 0) + 1
                        cid = rec.get("canonical_opportunity_id", "")
                        if cid:
                            canonical_count += 1
                        tid = rec.get("shadow_trade_id", "")
                        if tid:
                            shadow_trade_ids.add(tid)
                        pid = rec.get("plan_id", "")
                        if pid:
                            plan_ids.add(pid)
                    rel = os.path.relpath(fpath, ROOT)
                    first_chars = rel[:60]
                    print(f"  {first_chars}...: total={len(lines)}, canonical_populated={canonical_count}, shadow_ids={len(shadow_trade_ids)}, plans={len(plan_ids)}, events={event_types}")
                except Exception as e:
                    print(f"  {fpath}: ERROR - {e}")

# Check shadow_trades
print("\n--- shadow_trades ---")
st_dir = os.path.join(LOGS, "shadow_trades")
if os.path.exists(st_dir):
    for root, dirs, files in os.walk(st_dir):
        for fn in sorted(files):
            if fn.endswith(".jsonl"):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    events = {}
                    canonical_count = 0
                    shadow_trade_ids = set()
                    plan_ids = set()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        et = rec.get("event_type", rec.get("shadow_event_type", rec.get("type", "")))
                        if et:
                            events[et] = events.get(et, 0) + 1
                        for k in ["canonical_opportunity_id", "canonical_opp_id", "canonical_id"]:
                            cid = rec.get(k, "")
                            if cid:
                                canonical_count += 1
                                break
                        tid = rec.get("shadow_trade_id", rec.get("shadow_id", ""))
                        if tid:
                            shadow_trade_ids.add(tid)
                        pid = rec.get("plan_id", "")
                        if pid:
                            plan_ids.add(pid)
                    rel = os.path.relpath(fpath, ROOT)
                    print(f"  {rel}: total={len(lines)}, events={events}, canonical={canonical_count}, shadow_ids={len(shadow_trade_ids)}, plans={len(plan_ids)}")
                except Exception as e:
                    print(f"  {fpath}: ERROR - {e}")

# Check execution_results 2026-08-27
print("\n--- execution_results (checking for 2026-08-27 files) ---")
er_dir = os.path.join(LOGS, "execution_results")
if os.path.exists(er_dir):
    for root, dirs, files in os.walk(er_dir):
        for fn in sorted(files):
            if "2026-08-27" in fn and fn.endswith(".jsonl"):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    canonical_count = 0
                    populated = 0
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        cid = rec.get("canonical_opportunity_id", "")
                        if cid:
                            populated += 1
                        if "canonical_opportunity_id" in rec:
                            canonical_count += 1
                    rel = os.path.relpath(fpath, ROOT)
                    print(f"  {rel}: total={len(lines)}, canonical_refs={canonical_count}, populated={populated}")
                except Exception as e:
                    print(f"  {fpath}: ERROR - {e}")

print("\n=== SCAN COMPLETE ===")
