import json, os
from collections import Counter
from datetime import datetime, timezone

ROOT = r"c:\Users\ikues\Trading bot build"

def load(d_, date="2026-08-28"):
    recs = []
    d = os.path.join(ROOT, "logs", d_)
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

sh = load("shadow_runtime_v1")
dl = load("decision_ledger")
opps = load("opportunities")
asses = load("assessments")

shadow_canons = set(e["canonical_opportunity_id"] for e in sh if e.get("canonical_opportunity_id"))
dl_canon = {}
for r in dl:
    c = r.get("canonical_opportunity_id", "")
    if c:
        dl_canon.setdefault(c, r)

opp_by_canon = {}
for r in opps:
    c = r.get("canonical_opportunity_id", "")
    if c:
        opp_by_canon.setdefault(c, r)

print(f"shadow canonicals: {len(shadow_canons)}")
print(f"decision_ledger canonicals: {len(dl_canon)}")
print(f"opportunity canonicals: {len(opp_by_canon)}")
print()

# Loss funnel: DL canonicals NOT shadowed
not_shadowed = set(dl_canon) - shadow_canons
print(f"=== LOSS FUNNEL: DL canonicals NOT shadowed: {len(not_shadowed)} ===")
reasons = Counter()
for c in not_shadowed:
    d = dl_canon[c]
    o = opp_by_canon.get(c, {})
    reasons[(d.get("decision"), o.get("direction"), o.get("state"))] += 1
for k, v in reasons.most_common():
    print(f"  decision={k[0]} | opp_direction={k[1]} | opp_state={k[2]} -> {v}")

# Sample some non-shadowed with direction available
print("\n=== SAMPLE NON-SHADOWED (with direction) ===")
n = 0
for c in sorted(not_shadowed):
    d = dl_canon[c]
    o = opp_by_canon.get(c, {})
    if o.get("direction") in ("BUY","SELL") and n < 8:
        print(f"  {c[:55]} | decision={d.get('decision')} | reason={str(d.get('reason'))[:60]} | opp_dir={o.get('direction')} | state={o.get('state')} | reject={o.get('rejection_reason')}/{o.get('rejection_stage')}")
        n += 1

# Sample non-shadowed without direction
print("\n=== SAMPLE NON-SHADOWED (no direction) ===")
n = 0
for c in sorted(not_shadowed):
    o = opp_by_canon.get(c, {})
    if o.get("direction") not in ("BUY","SELL") and n < 5:
        d = dl_canon[c]
        print(f"  {c[:55]} | decision={d.get('decision')} | opp_dir={o.get('direction')} | state={o.get('state')}")
        n += 1

# Check shadowed canonicals' opportunity state
print("\n=== SHADOWED canonicals: opportunity state ===")
states = Counter(opp_by_canon.get(c, {}).get("state", "NO_OPP_RECORD") for c in shadow_canons)
print(dict(states))

# Check cycle_id in shadow events
plan_cycles = Counter(e.get("cycle_id") for e in sh if e["event_type"]=="PLAN")
print(f"\n=== SHADOW plan cycle_ids === {dict(plan_cycles)}")

# Shadow window vs opportunity window this run
sh_times = sorted(set(e.get("event_market_time") for e in sh if e.get("event_market_time")))
print(f"\nshadow bars: {len(sh_times)} first={datetime.fromtimestamp(sh_times[0],tz=timezone.utc).isoformat()} last={datetime.fromtimestamp(sh_times[-1],tz=timezone.utc).isoformat()}")

# opportunities in the same window
opp_in_window = [r for r in opps if r.get("detected_at_bar_time") and sh_times[0] <= r["detected_at_bar_time"] <= sh_times[-1]]
print(f"opportunity records in shadow window: {len(opp_in_window)}")
opp_in_win_canons = set(r.get("canonical_opportunity_id") for r in opp_in_window if r.get("canonical_opportunity_id"))
print(f"unique canonicals in window: {len(opp_in_win_canons)}")
print(f"shadowed of in-window canonicals: {len(opp_in_win_canons & shadow_canons)}")

# dl decisions in window
dl_in_win = [r for r in dl if r.get("timestamp_unix") and sh_times[0] <= r["timestamp_unix"] <= sh_times[-1]+300]
print(f"decision_ledger records in window: {len(dl_in_win)} decisions={dict(Counter(r.get('decision') for r in dl_in_win))}")

# assessments count
print(f"\nassessments records (2026-08-28): {len(asses)}")

# direction distribution of shadowed
opens = [e for e in sh if e["event_type"]=="OPEN"]
dirs = Counter(e.get("construction",{}).get("direction") for e in opens)
print(f"shadow directions: {dict(dirs)}")

# non-shadowed window canonicals - reason breakdown for in-window only
not_sh_win = opp_in_win_canons - shadow_canons
print(f"\n=== IN-WINDOW canonicals NOT shadowed: {len(not_sh_win)} ===")
for c in sorted(not_sh_win):
    o = opp_by_canon.get(c, {})
    d = dl_canon.get(c, {})
    print(f"  {c[:55]} | opp_state={o.get('state')} | opp_dir={o.get('direction')} | dl_decision={d.get('decision')} | dl_reason={str(d.get('reason'))[:50]}")
