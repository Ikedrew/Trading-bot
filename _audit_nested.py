import json, os
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
SHADOW_DIR = os.path.join(ROOT, "logs", "shadow_runtime_v1")

events = []
for root, _, files in os.walk(SHADOW_DIR):
    for f in files:
        if "2026-08-28" in f:
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass

def empty_val(v):
    return v is None or v == "" or v == [] or v == {}

# ---- Nested field inventory ----
def nested_report(recs, prefix_filter=None):
    paths = {}
    for r in recs:
        stack = [("", r)]
        while stack:
            p, obj = stack.pop()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full = f"{p}{k}"
                    if isinstance(v, (dict,)):
                        stack.append((full + ".", v))
                    elif isinstance(v, list) and v and isinstance(v[0], dict):
                        paths.setdefault(full + "[]", []).extend(v)
                    else:
                        paths.setdefault(full, []).append(v)
    return paths

for etype in ["PLAN", "OPEN", "CLOSE"]:
    recs = [e for e in events if e["event_type"] == etype]
    print(f"===== {etype} NESTED FIELDS ({len(recs)} records) =====")
    paths = nested_report(recs)
    for k in sorted(paths):
        vals = paths[k]
        if k.endswith("[]"):
            # list-of-dicts: report keys of items
            item_keys = set()
            for v in vals:
                item_keys |= set(v.keys())
            print(f"  {k}  (items={len(vals)}) item_keys={sorted(item_keys)}")
            continue
        empties = sum(1 for v in vals if empty_val(v))
        distinct = len(set(str(v) for v in vals))
        flag = f"  <<EMPTY {empties}/{len(vals)}>>" if empties else ""
        print(f"  {k}  n={len(vals)} distinct={distinct}{flag}")
    print()

# ---- Lineage stability across lifecycle ----
print("===== LINEAGE STABILITY (trades with CLOSE) =====")
opens = {e["shadow_trade_id"]: e for e in events if e["event_type"] == "OPEN"}
closes = {e["shadow_trade_id"]: e for e in events if e["event_type"] == "CLOSE"}
plans_by_canon = {e["canonical_opportunity_id"]: e for e in events if e["event_type"] == "PLAN"}

mismatch = 0
checked = 0
for tid, o in opens.items():
    canon = o["canonical_opportunity_id"]
    p = plans_by_canon.get(canon)
    c = closes.get(tid)
    if not c:
        continue
    checked += 1
    issues = []
    if p and p["plan_id"] != o["plan_id"]:
        issues.append("plan_id differs PLAN vs OPEN")
    if o["symbol"] != c["symbol"]:
        issues.append("symbol differs OPEN vs CLOSE")
    if o["entity_id"] != (c.get("identity", {}) or {}).get("entity_id", o["entity_id"]) and False:
        pass
    if p and p["entity_id"] != o["identity"]["entity_id"]:
        issues.append("entity_id differs PLAN vs OPEN")
    if p and p["cycle_id"] != o["identity"]["cycle_id"]:
        issues.append("cycle_id differs PLAN vs OPEN")
    if p and p["direction"] != o["construction"]["direction"]:
        issues.append("direction differs PLAN vs OPEN")
    if issues:
        mismatch += 1
        print(f"  {tid}: {issues}")
print(f"  checked={checked} lifecycle-complete trades; mismatches={mismatch}")

# entity/cycle present on CLOSE?
c0 = next(iter(closes.values()))
print(f"  CLOSE top-level identity fields: entity_id present={'entity_id' in c0}, cycle_id present={'cycle_id' in c0}")

# ---- Duplicate / conflicting fields ----
print()
print("===== DUPLICATE/CONFLICT FIELD CHECKS =====")
# PLAN.direction vs OPEN.construction.direction
for tid, o in list(opens.items())[:]:
    pass
conflict_dir = sum(1 for o in opens.values()
                   if plans_by_canon.get(o["canonical_opportunity_id"])
                   and plans_by_canon[o["canonical_opportunity_id"]]["direction"] != o["construction"]["direction"])
print(f"  PLAN.direction vs OPEN.construction.direction conflicts: {conflict_dir}/{len(opens)}")

# shadow_trade_id empty on PLAN (by design?)
plan_tid_empty = sum(1 for e in events if e["event_type"] == "PLAN" and not e["shadow_trade_id"])
print(f"  PLAN.shadow_trade_id empty: {plan_tid_empty}/46 (per-trade id minted at OPEN)")

# live_facts empties on OPEN
lfs = [o["live_facts"] for o in opens.values()]
for k in sorted(lfs[0].keys()):
    empt = sum(1 for lf in lfs if empty_val(lf.get(k)))
    if empt:
        print(f"  OPEN.live_facts.{k}: EMPTY {empt}/{len(lfs)}")

# market_entry_facts spread
spreads = [o["market_entry_facts"].get("spread_at_entry") for o in opens.values()]
zero_spread = sum(1 for s in spreads if s == 0)
print(f"  OPEN.market_entry_facts.spread_at_entry == 0: {zero_spread}/{len(spreads)}")

# lifecycle_initial.state_log_tail empties
slt = sum(1 for o in opens.values() if empty_val(o["lifecycle_initial"].get("state_log_tail")))
print(f"  OPEN.lifecycle_initial.state_log_tail empty: {slt}/{len(opens)} (expected at open)")

# data_gaps empty on CLOSE
dg = sum(1 for c in closes.values() if not c["data_gaps"])
print(f"  CLOSE.data_gaps empty: {dg}/{len(closes)}")

# trade_state_progression lengths vs bars_held
bad = sum(1 for c in closes.values() if len(c["trade_state_progression"]) != c["bars_held"])
print(f"  CLOSE.trade_state_progression length != bars_held: {bad}/{len(closes)}")

# exit_reason distribution
print(f"  CLOSE exit_reason: {dict(Counter(c['exit_reason'] for c in closes.values()))}")

# outcome field set
o0 = next(iter(closes.values()))["outcome"]
print(f"  CLOSE.outcome keys: {sorted(o0.keys())}")

# recorded_at vs event_market_time sanity (latency)
lats = []
for e in events:
    if e.get("recorded_at_utc_ms") and e.get("event_market_time_utc_epoch_s"):
        lats.append(e["recorded_at_utc_ms"] / 1000 - e["event_market_time_utc_epoch_s"])
if lats:
    lats.sort()
    print(f"  recorded_at minus market_time (s): min={lats[0]:.0f} median={lats[len(lats)//2]:.0f} max={lats[-1]:.0f}")
