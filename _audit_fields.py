import json, os
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
SHADOW_DIR = os.path.join(ROOT, "logs", "shadow_runtime_v1")

events = []
files_seen = []
for root, _, files in os.walk(SHADOW_DIR):
    for f in files:
        fpath = os.path.join(root, f)
        if "2026-08-28" in f:
            files_seen.append(fpath)
            with open(fpath, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            rec["_file"] = fpath
                            events.append(rec)
                        except Exception:
                            pass

print(f"FILES SEEN: {len(files_seen)}")
for fp in sorted(files_seen):
    rel = os.path.relpath(fp, ROOT)
    print(f"  {rel}")
print(f"TOTAL EVENTS 2026-08-28: {len(events)}")

et = Counter(e["event_type"] for e in events)
print(f"BREAKDOWN: {dict(et)}")
print()

# ---- Full field inventory per event type ----
def walk_keys(obj, prefix=""):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}{k}"
            keys.add(full)
            keys |= walk_keys(v, full + ".")
    elif isinstance(obj, list) and obj:
        # union of item keys (first few)
        for item in obj[:3]:
            keys |= walk_keys(item, prefix[:-1] + "[]")
    return keys

for etype in ["PLAN", "OPEN", "CLOSE", "PROGRESS", "DATA_GAP"]:
    recs = [e for e in events if e["event_type"] == etype]
    if not recs:
        print(f"== {etype}: 0 records ==")
        continue
    print(f"== {etype}: {len(recs)} records ==")
    # top-level keys union + presence
    top_keys = set()
    for r in recs:
        top_keys |= set(r.keys())
    for k in sorted(top_keys - {"_file"}):
        present = sum(1 for r in recs if k in r)
        # emptiness: empty string, None, missing, empty dict/list
        empty = 0
        for r in recs:
            v = r.get(k, "\x00MISSING")
            if v == "\x00MISSING" or v is None or v == "" or v == [] or v == {}:
                empty += 1
        flag = f" [EMPTY:{empty}/{len(recs)}]" if empty else ""
        print(f"  {k} (present {present}/{len(recs)}){flag}")
    print()
