import json, os
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
SHADOW_DIR = os.path.join(ROOT, "logs", "shadow_runtime_v1")

events = []
for root, _, files in os.walk(SHADOW_DIR):
    for f in files:
        if f == "2026-08-28.jsonl":
            symbol = os.path.basename(root)
            with open(os.path.join(root, f)) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        rec["_symbol"] = symbol
                        events.append(rec)

plans = [e for e in events if e["event_type"] == "PLAN"]
opens = [e for e in events if e["event_type"] == "OPEN"]
closes = [e for e in events if e["event_type"] == "CLOSE"]

# ---- LINEAGE STABILITY ----
print("=== LINEAGE STABILITY PLAN->OPEN->CLOSE ===")
opens_by_root = {}
for o in opens:
    opens_by_root.setdefault(o["canonical_opportunity_id"], []).append(o)
closes_by_tid = {}
for c in closes:
    closes_by_tid.setdefault(c["shadow_trade_id"], []).append(c)

n_plan = n_open = n_close = 0
mismatch = []
for p in plans:
    root = p["canonical_opportunity_id"]
    o_list = opens_by_root.get(root, [])
    if len(o_list) == 1:
        n_plan += 1
        o = o_list[0]
        if p["plan_id"] != o["plan_id"] or p["entity_id"] != o["identity"]["entity_id"] or p["cycle_id"] != o["identity"]["cycle_id"] or p["symbol"] != o["symbol"] or p["direction"] != o["construction"]["direction"]:
            mismatch.append(("PLAN!=OPEN", root))
        t = o["shadow_trade_id"]
        c_list = closes_by_tid.get(t, [])
        if c_list:
            n_close += 1
            c = c_list[0]
            if c["canonical_opportunity_id"] != root or c["plan_id"] != o["plan_id"] or c["symbol"] != o["symbol"]:
                mismatch.append(("OPEN!=CLOSE", root))
print(f"PLAN with exactly 1 OPEN: {n_plan}/46")
print(f"OPENs with a CLOSE in-window: {n_close}/46")
print(f"Lineage mismatches: {len(mismatch)}")
for m in mismatch[:5]:
    print(f"  {m}")

# duplicate check
roots_dup = [r for r, c in Counter(o["canonical_opportunity_id"] for o in opens).items() if c > 1]
tids_dup = [t for t, c in Counter(o["shadow_trade_id"] for o in opens).items() if c > 1]
print(f"Duplicate canonical roots among OPENs: {len(roots_dup)}")
print(f"Duplicate shadow_trade_ids among OPENs: {len(tids_dup)}")

# ---- NESTED KEY INVENTORY (OPEN / CLOSE) ----
print()
print("=== NESTED KEYS: OPEN.identity ===")
print(sorted(opens[0]["identity"].keys()))
print("=== NESTED KEYS: OPEN.live_facts ===")
print(sorted(opens[0]["live_facts"].keys()))
print("=== NESTED KEYS: OPEN.construction ===")
print(sorted(opens[0]["construction"].keys()))
print("=== NESTED KEYS: OPEN.construction.structure_inputs ===")
print(sorted(opens[0]["construction"]["structure_inputs"].keys()))
print("=== NESTED KEYS: OPEN.market_entry_facts ===")
print(sorted(opens[0]["market_entry_facts"].keys()))
print("=== NESTED KEYS: OPEN.simulation_assumptions ===")
print(sorted(opens[0]["simulation_assumptions"].keys()))
print("=== NESTED KEYS: OPEN.lifecycle_initial ===")
print(sorted(opens[0]["lifecycle_initial"].keys()))
if closes:
    print("=== NESTED KEYS: CLOSE.outcome ===")
    print(sorted(closes[0]["outcome"].keys()))
    print("=== NESTED KEYS: CLOSE.final_lifecycle ===")
    print(sorted(closes[0]["final_lifecycle"].keys()))
    prog_lens = Counter(len(c.get("trade_state_progression", [])) for c in closes)
    print(f"trade_state_progression lengths: {dict(prog_lens)}")
    er = Counter(c["exit_reason"] for c in closes)
    print(f"exit_reasons: {dict(er)}")

# shadow_type distribution
st = Counter(o["identity"]["shadow_type"] for o in opens)
print(f"shadow_type: {dict(st)}")
hz = Counter(o["identity"]["trade_horizon"] for o in opens)
print(f"trade_horizon: {dict(hz)}")
va = Counter(o["live_facts"]["v10_action"] for o in opens)
print(f"live_facts.v10_action: {dict(va)}")

# PLAN horizons states
states = Counter()
for p in plans:
    for h in p["horizons"]:
        states[(h["horizon"], h["state"])] += 1
print(f"PLAN horizon states: {dict(states)}")
