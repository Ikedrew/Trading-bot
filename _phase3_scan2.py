#!/usr/bin/env python
"""Targeted Phase-3 audit: scan decision datasets and shadow data for canonical_opportunity_id."""
import json, os

ROOT = r"C:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")

# Check decision_audit 2026-08-27 files
print("\n=== DECISION_AUDIT 2026-08-27 ===")
da_dir = os.path.join(LOGS, "decision_audit")
for fn in sorted(os.listdir(da_dir)):
    if "2026-08-27" in fn and fn.endswith(".jsonl"):
        fpath = os.path.join(da_dir, fn)
        total = 0
        canonical_refs = 0
        populated = 0
        empty = 0
        no_field = 0
        cids = set()
        first_rec = None
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    rec = json.loads(line)
                except:
                    continue
                if first_rec is None:
                    first_rec = rec
                if "canonical_opportunity_id" in rec:
                    canonical_refs += 1
                    cid = rec["canonical_opportunity_id"]
                    if cid and cid != "" and cid != "null":
                        populated += 1
                        cids.add(cid)
                    elif cid == "" or cid == "null":
                        empty += 1
                else:
                    no_field += 1
        print(f"  {fn}: total={total}, refs={canonical_refs}, populated={populated}, empty={empty}, no_field={no_field}")
        if first_rec:
            print(f"    first: symbol={first_rec.get('symbol')}, cycle={first_rec.get('cycle_id')}, entity={first_rec.get('entity_id')}, pattern={first_rec.get('pattern')}, canonical={first_rec.get('canonical_opportunity_id','N/A')[:30]}, ts={first_rec.get('timestamp_utc','')[:30]}")

# Check decision_ledger 2026-08-27 files
print("\n=== DECISION_LEDGER 2026-08-27 ===")
dl_dir = os.path.join(LOGS, "decision_ledger")
for sdir in sorted(os.listdir(dl_dir)):
    spath = os.path.join(dl_dir, sdir)
    if not os.path.isdir(spath):
        continue
    for fn in sorted(os.listdir(spath)):
        if "2026-08-27" in fn and fn.endswith(".jsonl"):
            fpath = os.path.join(spath, fn)
            total = 0
            canonical_refs = 0
            populated = 0
            empty = 0
            no_field = 0
            first_rec = None
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        rec = json.loads(line)
                    except:
                        continue
                    if first_rec is None:
                        first_rec = rec
                    if "canonical_opportunity_id" in rec:
                        canonical_refs += 1
                        cid = rec["canonical_opportunity_id"]
                        if cid and cid != "" and cid != "null":
                            populated += 1
                        elif cid == "" or cid == "null":
                            empty += 1
                    else:
                        no_field += 1
            print(f"  {sdir}/{fn}: total={total}, refs={canonical_refs}, populated={populated}, empty={empty}, no_field={no_field}")
            if first_rec:
                print(f"    first: symbol={first_rec.get('symbol')}, cycle={first_rec.get('cycle_id')}, entity={first_rec.get('entity_id','')[:30]}, canonical={first_rec.get('canonical_opportunity_id','N/A')[:30]}, ts={str(first_rec.get('timestamp',''))[:30]}")

# Check decision_trace 2026-08-27 files
print("\n=== DECISION_TRACE 2026-08-27 ===")
dt_dir = os.path.join(LOGS, "decision_trace")
for sdir in sorted(os.listdir(dt_dir)):
    spath = os.path.join(dt_dir, sdir)
    if not os.path.isdir(spath):
        continue
    for fn in sorted(os.listdir(spath)):
        if "2026-08-27" in fn and fn.endswith(".jsonl"):
            fpath = os.path.join(spath, fn)
            total = 0
            canonical_refs = 0
            populated = 0
            empty = 0
            no_field = 0
            first_rec = None
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        rec = json.loads(line)
                    except:
                        continue
                    if first_rec is None:
                        first_rec = rec
                    if "canonical_opportunity_id" in rec:
                        canonical_refs += 1
                        cid = rec["canonical_opportunity_id"]
                        if cid and cid != "" and cid != "null":
                            populated += 1
                        elif cid == "" or cid == "null":
                            empty += 1
                    else:
                        no_field += 1
            print(f"  {sdir}/{fn}: total={total}, refs={canonical_refs}, populated={populated}, empty={empty}, no_field={no_field}")
            if first_rec:
                print(f"    first: symbol={first_rec.get('symbol')}, cycle={first_rec.get('cycle_id')}, entity={first_rec.get('entity_id','')[:30]}, canonical={first_rec.get('canonical_opportunity_id','N/A')[:30]}, obs_id={first_rec.get('observation_id','')[:20]}")

# Check execution_results 2026-08-26 files modified during fresh soak
print("\n=== EXECUTION_RESULTS (2026-08-26 files modified after 00:00 UTC Aug 27) ===")
er_dir = os.path.join(LOGS, "execution_results")
import time
for sdir in sorted(os.listdir(er_dir)):
    spath = os.path.join(er_dir, sdir)
    if not os.path.isdir(spath):
        continue
    for fn in sorted(os.listdir(spath)):
        if fn.endswith(".jsonl"):
            fpath = os.path.join(spath, fn)
            mtime = os.path.getmtime(fpath)
            if mtime > 1787788800:  # After 00:00 UTC Aug 27
                total = 0
                canonical_refs = 0
                populated = 0
                empty = 0
                no_field = 0
                first_rec = None
                last_rec = None
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        total += 1
                        try:
                            rec = json.loads(line)
                        except:
                            continue
                        if first_rec is None:
                            first_rec = rec
                        last_rec = rec
                        if "canonical_opportunity_id" in rec:
                            canonical_refs += 1
                            cid = rec["canonical_opportunity_id"]
                            if cid and cid != "" and cid != "null":
                                populated += 1
                            elif cid == "" or cid == "null":
                                empty += 1
                        else:
                            no_field += 1
                print(f"  {sdir}/{fn}: total={total}, refs={canonical_refs}, populated={populated}, empty={empty}, no_field={no_field}, mtime={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(mtime))}")
                if first_rec:
                    print(f"    first: ts={first_rec.get('timestamp_utc','')[:30]}, canonical={first_rec.get('canonical_opportunity_id','N/A')[:40]}")
                if last_rec and last_rec != first_rec:
                    print(f"    last: ts={last_rec.get('timestamp_utc','')[:30]}, canonical={last_rec.get('canonical_opportunity_id','N/A')[:40]}")

print("\n=== SCAN COMPLETE ===")
