import json, os
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"

# Collect shadow_runtime_v1 events from 2026-08-28
SHADOW_DIR = os.path.join(ROOT, "logs", "shadow_runtime_v1")
shadow_events = []
for root, _, files in os.walk(SHADOW_DIR):
    for f in files:
        if f == "2026-08-28.jsonl":
            fpath = os.path.join(root, f)
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            shadow_events.append(rec)
                        except:
                            pass

# Collect decision_ledger records from 2026-08-28
DL_DIR = os.path.join(ROOT, "logs", "decision_ledger")
dl_records = []
for root, _, files in os.walk(DL_DIR):
    for f in files:
        if f == "2026-08-28.jsonl":
            fpath = os.path.join(root, f)
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            dl_records.append(rec)
                        except:
                            pass

# Collect opportunity records
OPP_DIR = os.path.join(ROOT, "logs", "opportunities")
opp_records = []
for root, _, files in os.walk(OPP_DIR):
    for f in files:
        if f == "2026-08-28.jsonl":
            fpath = os.path.join(root, f)
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            opp_records.append(rec)
                        except:
                            pass

shadow_canonicals = set(e.get("canonical_opportunity_id", "") for e in shadow_events if e.get("canonical_opportunity_id"))
dl_canonicals = set(d.get("canonical_opportunity_id", "") for d in dl_records if d.get("canonical_opportunity_id"))
opp_canonicals = set(o.get("canonical_opportunity_id", "") for o in opp_records if o.get("canonical_opportunity_id"))

# Shadow canonicals that match decision_ledger
dl_by_canon = {d.get("canonical_opportunity_id", ""): d for d in dl_records if d.get("canonical_opportunity_id")}

print("=== RUNTIME FUNNEL (2026-08-28) ===")
print(f"Opportunities recorded: {len(opp_records)} ({len(opp_canonicals)} unique canonical)")
print(f"Decision_ledger records: {len(dl_records)} ({len(dl_canonicals)} unique canonical)")
print(f"Shadow events: {len(shadow_events)}")
print(f"Shadow unique canonical: {len(shadow_canonicals)}")
print(f"Shadow ∩ Decision_ledger: {len(shadow_canonicals & dl_canonicals)}")
print(f"Shadow ∩ Opportunity: {len(shadow_canonicals & opp_canonicals)}")
print()

# Decision breakdown
dl_decisions = Counter(d.get("decision", "MISSING") for d in dl_records)
print(f"Decision_ledger decisions: {dict(dl_decisions)}")
print()

# Of the shadowed canonicals, what were their live decisions?
print("=== LIVE DECISIONS FOR SHADOWED OPPORTUNITIES ===")
shadowed_decisions = Counter()
for cid in shadow_canonicals:
    dl_rec = dl_by_canon.get(cid, {})
    decision = dl_rec.get("decision", "NOT_FOUND")
    shadowed_decisions[decision] += 1
print(f"Decision breakdown of shadowed opportunities: {dict(shadowed_decisions)}")
print()

# Detailed NO_TRADE proof
print("=== NO_TRADE COUNTERFACTUAL PROOF ===")
opens = [e for e in shadow_events if e["event_type"] == "OPEN"]
no_trade_shadows = [o for o in opens if o.get("live_facts", {}).get("v10_action") == "NO_TRADE"]
execute_shadows = [o for o in opens if o.get("live_facts", {}).get("v10_action") == "EXECUTE"]

print(f"NO_TRADE shadowed: {len(no_trade_shadows)}")
print(f"EXECUTE shadowed: {len(execute_shadows)}")
print()

# Show 3 NO_TRADE examples with full evidence
print("=== 3 NO_TRADE COUNTERFACTUAL CASES ===")
for o in no_trade_shadows[:3]:
    cid = o.get("canonical_opportunity_id", "")
    lf = o.get("live_facts", {})
    cons = o.get("construction", {})
    dl = dl_by_canon.get(cid, {})
    
    print(f"1. canonical: {cid}")
    print(f"   LIVE:    decision={dl.get('decision')} | pattern_state={dl.get('pattern_state')}")
    print(f"   SHADOW:  live_facts.v10_action={lf.get('v10_action')} | pattern={lf.get('pattern')} | strategy={lf.get('strategy')}")
    print(f"   SHADOW:  construction.direction={cons.get('direction')} | entry={cons.get('entry_price')} | SL={cons.get('stop_loss')} | TP={cons.get('take_profit')}")
    print(f"   SHADOW:  horizon={o.get('identity',{}).get('trade_horizon','?')} | shadow_trade_id={o.get('shadow_trade_id','?')}")
    print()

# Show EXECUTE example
if execute_shadows:
    o = execute_shadows[0]
    cid = o.get("canonical_opportunity_id", "")
    lf = o.get("live_facts", {})
    cons = o.get("construction", {})
    dl = dl_by_canon.get(cid, {})
    print(f"EXECUTE CASE:")
    print(f"   canonical: {cid}")
    print(f"   LIVE:    decision={dl.get('decision')}")
    print(f"   SHADOW:  live_facts.v10_action={lf.get('v10_action')} | construction.direction={cons.get('direction')}")
    print()

# Horizon breakdown in shadow OPENs
print("=== HORIZON BREAKDOWN IN SHADOW OPENs ===")
hz_counts = Counter(o.get("identity", {}).get("trade_horizon", "?") for o in opens)
print(dict(hz_counts))
print()

# Event lifecycle for one NO_TRADE
print("=== FULL LIFECYCLE: NO_TRADE EXAMPLE ===")
if no_trade_shadows:
    o = no_trade_shadows[0]
    tid = o.get("shadow_trade_id", "")
    cid = o.get("canonical_opportunity_id", "")
    trade_events = [e for e in shadow_events if e.get("shadow_trade_id", "") == tid]
    for te in trade_events:
        print(f"  {te['event_type']} @ UTC={te.get('event_market_time_utc_iso8601','?')} | market_time={te.get('event_market_time','?')}")
    
    # Find the matching decision
    dl = dl_by_canon.get(cid, {})
    print(f"\n  LIVE DECISION: {dl.get('decision')} (side from decision_ledger: {'not applicable - NO_TRADE')}")
    print(f"  Canonical ID: {cid}")
    print(f"  Pattern: {o.get('live_facts',{}).get('pattern')}")
    print(f"  Shadow direction: {o.get('construction',{}).get('direction')}")

# Progression events
progress = [e for e in shadow_events if e["event_type"] == "PROGRESS"]
print(f"\n=== PROGRESS EVENTS TODAY: {len(progress)} ===")
# Note: SCALP timeout=9 < checkpoint=12, so most SCALP close before checkpoint
closes = [e for e in shadow_events if e["event_type"] == "CLOSE"]
print(f"CLOSE events: {len(closes)}")

# Data gaps
gaps = [e for e in shadow_events if e["event_type"] == "CLOSE" and e.get("data_gaps")]
print(f"CLOSES with data_gaps: {len(gaps)}")
for g in gaps:
    print(f"  gaps: {g.get('data_gaps')}")
