"""Read-only shadow investigation (timeout-safe). Does NOT modify logs/."""
import os, json
ROOT = r"C:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")

def iter_lines(d):
    if not os.path.isdir(d):
        return
    for r, _, fs in os.walk(d):
        for f in fs:
            if f.endswith(".jsonl"):
                p = os.path.join(r, f)
                with open(p, encoding="utf-8") as fh:
                    for line in fh:
                        s = line.strip()
                        if s:
                            yield os.path.relpath(p, LOGS), s

def count_and_sample(name, d, full_parse=False):
    print("\n=== %s (%s) ===" % (name, d if os.path.isdir(d) else "(absent)"))
    if not os.path.isdir(d):
        print("(absent)"); return None
    n = 0
    keys = set()
    events = {}
    tids = set(); pids = set(); canids = set()
    first = None
    for rel, s in iter_lines(d):
        n += 1
        if first is None:
            try:
                first = json.loads(s)
            except Exception:
                first = {"_raw_first_line": s[:300]}
        if full_parse:
            try:
                rec = json.loads(s)
                keys.update(rec.keys())
                et = rec.get("event_type")
                if et is not None: events[et] = events.get(et, 0) + 1
                for fk, bag in (("shadow_trade_id", tids), ("plan_id", pids),
                                ("canonical_opportunity_id", canids)):
                    v = rec.get(fk)
                    if v is not None: bag.add(v)
            except Exception:
                pass
    print("records:", n)
    if first is not None:
        print("first-record keys:", sorted(first.keys()))
        print("sample:", json.dumps(first, indent=2, ensure_ascii=False)[:1400])
    if full_parse:
        print("event_type distribution:", events)
        print("distinct shadow_trade_id:", len(tids), "plan_id:", len(pids),
              "canonical_opportunity_id:", len(canids))
        print("sample shadow_trade_id:", sorted(tids)[:5])
        print("sample canonical_opportunity_id:", sorted([str(c) for c in canids])[:5])
        return canids
    return None

print("########## SHADOW RUNTIME v1 (projector's ONLY shadow source) ##########")
sr = os.path.join(LOGS, "shadow_runtime_v1")
sr_canids = count_and_sample("shadow_runtime_v1", sr, full_parse=True)

print("\n########## SHADOW TRADES (legacy, disabled) ##########")
count_and_sample("shadow_trades", os.path.join(LOGS, "shadow_trades"), full_parse=False)

print("\n########## v3_shadow subdirs + file counts ##########")
v3 = os.path.join(LOGS, "v3_shadow")
if os.path.isdir(v3):
    for sub in sorted(os.listdir(v3)):
        sp = os.path.join(v3, sub)
        if os.path.isdir(sp):
            fc = sum(1 for _ in iter_lines(sp))
            print("  v3_shadow/%s : files=%d records=%d" % (sub, sum(1 for r,_,fs in os.walk(sp) for f in fs if f.endswith('.jsonl')), fc))
        else:
            print("  v3_shadow/%s : (file)" % sub)
print("\n=== v3_shadow/entry_assessment sample + keys ===")
count_and_sample("v3_shadow/entry_assessment", os.path.join(v3, "entry_assessment"), full_parse=False)

print("\n########## OPPORTUNITY <-> SHADOW LINKAGE ##########")
opp = os.path.join(LOGS, "opportunities")
opp_canids = set(); opp_count = 0
if os.path.isdir(opp):
    for rel, s in iter_lines(opp):
        opp_count += 1
        try:
            rec = json.loads(s)
            cid = rec.get("canonical_opportunity_id")
            if cid is not None:
                opp_canids.add(cid)
        except Exception:
            pass
print("LIVE opportunities: records=%d distinct canonical_opportunity_id=%d" % (opp_count, len(opp_canids)))
if sr_canids is not None:
    shared = opp_canids & sr_canids
    print("shadow canonical_opportunity_id in LIVE opportunities:", len(shared))
    print("shadow canonical_opportunity_id NOT in live opps (orphans):", len(sr_canids - opp_canids))
    print("sample shared:", sorted([str(c) for c in list(shared)[:5]]))

print("\n### also: execution_context / execution_results canonical_opportunity_id coverage")
# how many distinct canonical_opportunity_id appear in execution (to contrast w/ shadow)
for ds in ["execution_context", "execution_results", "trade_truth"]:
    cands = set(); cnt = 0
    d = os.path.join(LOGS, ds)
    if os.path.isdir(d):
        for rel, s in iter_lines(d):
            cnt += 1
            try:
                rec = json.loads(s); c = rec.get("canonical_opportunity_id")
                if c is not None: cands.add(c)
            except Exception:
                pass
    print("  %-22s records=%d distinct canonical_opportunity_id=%d" % (ds, cnt, len(cands)))

print("\n=== DONE ===")
