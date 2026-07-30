"""Trace V3 identity keys through the pipeline to find linkage break."""
import json
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# V3 IDENTITY KEYS
# ═══════════════════════════════════════════════════════════════
v3_dir = Path("logs/v3_opportunities")
v3_records = []
if v3_dir.exists():
    for f in sorted(v3_dir.rglob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        v3_records.append(json.loads(line))
                    except:
                        pass

print("=" * 60)
print("V3 IDENTITY KEYS (first 5)")
print("=" * 60)
for r in v3_records[:5]:
    opp_id = r.get("opportunity_id", "")
    corr_id = r.get("correlation_id", "")
    sym = r.get("symbol", "")
    ts = r.get("timestamp_utc", 0)
    print(f"  opp_id:  {opp_id}")
    print(f"  corr_id: {corr_id}")
    print(f"  symbol:  {sym}")
    print(f"  ts:      {ts}")
    print()

# ═══════════════════════════════════════════════════════════════
# SHADOW TRADE IDENTITY KEYS
# ═══════════════════════════════════════════════════════════════
shadow_dir = Path("logs/shadow_trades")
shadow_records = []
if shadow_dir.exists():
    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        for f in sorted(sym_dir.glob("*.jsonl"))[:2]:
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            rec = json.loads(line)
                            if rec.get("schema_version") == "shadow_trades_v2":
                                shadow_records.append(rec)
                        except:
                            pass
                    if len(shadow_records) >= 20:
                        break
            if len(shadow_records) >= 20:
                break
        if len(shadow_records) >= 20:
            break

print("=" * 60)
print("SHADOW TRADE IDENTITY KEYS (first 5)")
print("=" * 60)
for r in shadow_records[:5]:
    identity = r.get("identity", {})
    snap = r.get("decision_snapshot", {})
    print(f"  entity_id:      {identity.get('entity_id', '')}")
    print(f"  correlation_id: {identity.get('correlation_id', '')}")
    print(f"  symbol:         {identity.get('symbol', '')}")
    print(f"  ts_decision:    {snap.get('timestamp_decision_utc', 0)}")
    print()

# ═══════════════════════════════════════════════════════════════
# LINKAGE ANALYSIS
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("LINKAGE ANALYSIS")
print("=" * 60)

# Build shadow trade lookup
entity_id_set = set()
corr_id_set = set()
shadow_by_symbol_time = {}  # (symbol, timestamp) → trade

for r in shadow_records:
    identity = r.get("identity", {})
    snap = r.get("decision_snapshot", {})
    eid = identity.get("entity_id", "")
    cid = identity.get("correlation_id", "")
    sym = identity.get("symbol", "")
    ts = snap.get("timestamp_decision_utc", 0)
    if eid:
        entity_id_set.add(eid)
    if cid:
        corr_id_set.add(cid)
    if sym and ts:
        shadow_by_symbol_time[(sym, ts)] = r

# Try matching V3 → shadow trades
match_entity = 0
match_corr = 0
match_time = 0
no_match = 0

for v3 in v3_records:
    corr = v3.get("correlation_id", "")
    sym = v3.get("symbol", "")
    ts = v3.get("timestamp_utc", 0)

    if corr in entity_id_set:
        match_entity += 1
    elif corr in corr_id_set:
        match_corr += 1
    elif (sym, ts) in shadow_by_symbol_time:
        match_time += 1
    else:
        # Try timestamp tolerance
        found = False
        for (s, t) in shadow_by_symbol_time:
            if s == sym and abs(t - ts) <= 300:
                match_time += 1
                found = True
                break
        if not found:
            no_match += 1

total = len(v3_records)
print(f"\nTotal V3 observations: {total}")
print(f"Match by entity_id:    {match_entity} ({match_entity/total*100:.1f}%)" if total else "")
print(f"Match by corr_id:      {match_corr} ({match_corr/total*100:.1f}%)" if total else "")
print(f"Match by time (±300s): {match_time} ({match_time/total*100:.1f}%)" if total else "")
print(f"No match:              {no_match} ({no_match/total*100:.1f}%)" if total else "")
print(f"Total linkable:        {match_entity + match_corr + match_time} ({(match_entity+match_corr+match_time)/total*100:.1f}%)" if total else "")

# Show V3 correlation_id format vs shadow entity_id format
print("\n" + "=" * 60)
print("KEY FORMAT COMPARISON")
print("=" * 60)
v3_corrs = [r.get("correlation_id", "") for r in v3_records[:10]]
shadow_eids = list(entity_id_set)[:10]
shadow_cids = list(corr_id_set)[:10]

print("\nV3 correlation_ids:")
for c in v3_corrs[:5]:
    print(f"  {c}")

print("\nShadow entity_ids:")
for e in sorted(shadow_eids)[:5]:
    print(f"  {e}")

print("\nShadow correlation_ids:")
for c in sorted(shadow_cids)[:5]:
    print(f"  {c}")

# Load ALL shadow trades for full linkage test
print("\n" + "=" * 60)
print("FULL LINKAGE TEST (all shadow trades)")
print("=" * 60)

all_shadows = []
if shadow_dir.exists():
    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            rec = json.loads(line)
                            if rec.get("schema_version") == "shadow_trades_v2":
                                all_shadows.append(rec)
                        except:
                            pass

print(f"Total shadow trades loaded: {len(all_shadows)}")

# Build complete lookup
all_entity_ids = set()
all_corr_ids = set()
all_sym_time = {}

for r in all_shadows:
    identity = r.get("identity", {})
    snap = r.get("decision_snapshot", {})
    eid = identity.get("entity_id", "")
    cid = identity.get("correlation_id", "")
    sym = identity.get("symbol", "")
    ts = snap.get("timestamp_decision_utc", 0)
    if eid:
        all_entity_ids.add(eid)
    if cid:
        all_corr_ids.add(cid)
    if sym and ts:
        key = f"{sym}_{int(ts)}"
        all_sym_time[key] = r

# Full linkage test
match_e = 0
match_c = 0
match_t = 0
no_m = 0

for v3 in v3_records:
    corr = v3.get("correlation_id", "")
    sym = v3.get("symbol", "")
    ts = v3.get("timestamp_utc", 0)

    if corr and corr in all_entity_ids:
        match_e += 1
    elif corr and corr in all_corr_ids:
        match_c += 1
    else:
        # Timestamp match
        key = f"{sym}_{int(ts)}"
        if key in all_sym_time:
            match_t += 1
        else:
            # Tolerance search
            found = False
            for delta in range(-300, 301, 300):
                k2 = f"{sym}_{int(ts + delta)}"
                if k2 in all_sym_time:
                    match_t += 1
                    found = True
                    break
            if not found:
                no_m += 1

total = len(v3_records)
print(f"\nV3 observations: {total}")
print(f"Match entity_id:  {match_e} ({match_e/total*100:.1f}%)" if total else "")
print(f"Match corr_id:    {match_c} ({match_c/total*100:.1f}%)" if total else "")
print(f"Match timestamp:  {match_t} ({match_t/total*100:.1f}%)" if total else "")
print(f"No match:         {no_m} ({no_m/total*100:.1f}%)" if total else "")
linkable = match_e + match_c + match_t
print(f"TOTAL LINKABLE:   {linkable} ({linkable/total*100:.1f}%)" if total else "")
