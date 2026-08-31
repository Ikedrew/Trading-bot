import json, os
from collections import Counter
from datetime import datetime, timezone

ROOT = r"c:\Users\ikues\Trading bot build"

def load(pattern_dir, date="2026-08-28", flat=False):
    recs = []
    d = os.path.join(ROOT, "logs", pattern_dir)
    if not os.path.isdir(d):
        return recs
    for root, _, files in os.walk(d):
        for f in files:
            if date in f:
                try:
                    with open(os.path.join(root, f)) as fh:
                        for line in fh:
                            line = line.strip()
                            if line:
                                recs.append(json.loads(line))
                except Exception:
                    pass
    return recs

# 1. USDJPY HANGING_MAN decision_ledger record
dl = load("decision_ledger")
for r in dl:
    if "HANGING_MAN" in str(r.get("canonical_opportunity_id", "")):
        print("=== USDJPY HANGING_MAN decision_ledger ===")
        print(json.dumps({k: r.get(k) for k in ["canonical_opportunity_id","decision","pattern_state","last_stage","signal_type","entity_id","cycle_id","reason"]}, indent=1))
        v10 = r.get("v10", {})
        if isinstance(v10, dict):
            print("v10 sub-record:", json.dumps({k: v10.get(k) for k in list(v10.keys())[:15]}, indent=1, default=str))
        print()

# 2. v10_decisions for the same
v10d = load("v10_decisions")
exec_recs = [r for r in v10d if "HANGING_MAN" in str(r.get("canonical_opportunity_id", "")) or str(r.get("action",""))=="EXECUTE"]
print(f"v10_decisions total: {len(v10d)}, EXECUTE-ish/HANGING_MAN matches: {len(exec_recs)}")
if v10d:
    print("v10_decisions sample keys:", sorted(v10d[0].keys())[:20])
for r in exec_recs[:2]:
    print(json.dumps({k: r.get(k) for k in ["canonical_opportunity_id","action","side","pattern","decision"] if k in r}, default=str))

# 3. Shadow events: PROGRESS / DATA_GAP / duplicates / watermark
sh = load("shadow_runtime_v1")
types = Counter(e["event_type"] for e in sh)
print("\n=== SHADOW EVENT TYPES ===", dict(types))

# Duplicate (shadow_trade_id, bar) evaluations
open_ids = [e.get("shadow_trade_id") for e in sh if e["event_type"]=="OPEN"]
dup_opens = [k for k,v in Counter(open_ids).items() if v>1]
plan_roots = [e.get("canonical_opportunity_id") for e in sh if e["event_type"]=="PLAN"]
dup_plans = [k for k,v in Counter(plan_roots).items() if v>1]
print(f"Duplicate PLANs per canonical root: {len(dup_plans)}")
print(f"Duplicate OPENs per shadow_trade_id: {len(dup_opens)}")

# data gaps in closes
closes = [e for e in sh if e["event_type"]=="CLOSE"]
gap_events = sum(len(e.get("data_gaps") or []) for e in closes)
print(f"DATA_GAP entries recorded in closes: {gap_events}")

# bar progression sanity: exit bar times
print("\n=== EXIT REASONS ===", dict(Counter(e.get("exit_reason") for e in closes)))
print("=== BARS HELD distribution ===", dict(Counter(e.get("bars_held") for e in closes)))

# 4. run window from opportunities + market data
opps = load("opportunities")
opp_times = sorted(set(r.get("detected_at_bar_time", 0) for r in opps if r.get("detected_at_bar_time")))
if opp_times:
    print(f"\n=== OPPORTUNITY WINDOW === first bar={datetime.fromtimestamp(opp_times[0], tz=timezone.utc).isoformat()} last={datetime.fromtimestamp(opp_times[-1], tz=timezone.utc).isoformat()} bars={len(opp_times)}")

# cycles per symbol (cycle_id max)
cycles = Counter()
for r in opps:
    cycles[(r.get("_symbol") or r.get("symbol"),)] = max(cycles.get((r.get("_symbol") or r.get("symbol"),),0), r.get("cycle_id",0) or 0)
print("max cycle_id per symbol:", {k[0]: v for k,v in cycles.items()})

# 5. outcomes summary
pnl = Counter()
for e in closes:
    o = e.get("outcome", {})
    r = o.get("pnl_r_multiple")
    if r is None: pnl["missing"] += 1
    elif r > 0: pnl["win"] += 1
    elif r < 0: pnl["loss"] += 1
    else: pnl["flat"] += 1
print("\n=== OUTCOME SUMMARY ===", dict(pnl))
wins = [e for e in closes if (e.get("outcome") or {}).get("pnl_r_multiple",0) > 0]
for w in wins:
    o = w["outcome"]
    print(f"  WIN {w.get('canonical_opportunity_id','?')[:50]} dir={None} pnl_r={o.get('pnl_r_multiple')} mfe={o.get('mfe_r')} exit={w.get('exit_reason')} bars={w.get('bars_held')}")
