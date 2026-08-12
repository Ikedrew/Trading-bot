"""Shadow Lineage Audit — Data Quality Measurement. Outputs to file."""
import json
from pathlib import Path
from collections import defaultdict

def main():
    results = []
    shadow_dir = Path("logs/shadow_trades")
    
    total = 0
    by_type = defaultdict(int)
    by_eid_status = {"valid": 0, "empty": 0, "none_str": 0}
    by_horizon = defaultdict(int)
    by_exit = defaultdict(int)
    by_schema = defaultdict(int)
    by_symbol = defaultdict(int)
    entities = set()
    entity_counts = defaultdict(int)
    eid_empty_samples = []
    other_type_samples = []
    
    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir():
            continue
        for f in sorted(sym_dir.glob("*.jsonl")):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except:
                    continue
                total += 1
                
                identity = rec.get("identity", {})
                outcome = rec.get("simulated_outcome", {})
                
                eid = identity.get("entity_id", "") or ""
                tid = identity.get("trade_id", "") or ""
                schema = rec.get("schema_version", "NONE")
                sym = identity.get("symbol", "") or rec.get("symbol", "UNKNOWN")
                
                by_schema[schema] += 1
                by_symbol[sym] += 1
                
                # entity_id status
                if eid and eid != "None" and eid != "null":
                    by_eid_status["valid"] += 1
                    entities.add(eid)
                    entity_counts[eid] += 1
                elif eid == "None":
                    by_eid_status["none_str"] += 1
                    if len(eid_empty_samples) < 3:
                        eid_empty_samples.append({"tid": tid[:50], "schema": schema, "type": "None_str"})
                else:
                    by_eid_status["empty"] += 1
                    if len(eid_empty_samples) < 6:
                        eid_empty_samples.append({"tid": tid[:50], "schema": schema, "type": "empty"})
                
                # Shadow type
                if tid.startswith("hshadow_"):
                    by_type["horizon"] += 1
                    parts = tid.split("_")
                    if len(parts) >= 4:
                        by_horizon[parts[-1]] += 1
                elif tid.startswith("shadow_"):
                    by_type["primary"] += 1
                else:
                    by_type["other"] += 1
                    if len(other_type_samples) < 5:
                        other_type_samples.append({"tid": tid[:60], "eid": eid[:30], "schema": schema})
                
                # Exit reason
                exit_r = outcome.get("exit_reason", "NONE") or "NONE"
                by_exit[exit_r] += 1

    # Compute multi-shadow stats
    multi = sum(1 for c in entity_counts.values() if c > 1)
    max_per = max(entity_counts.values()) if entity_counts else 0
    
    # Distribution of shadows per entity
    dist = defaultdict(int)
    for c in entity_counts.values():
        dist[min(c, 10)] += 1  # bucket 10+
    
    results.append("=" * 70)
    results.append("SHADOW LINEAGE AUDIT — DATA MEASUREMENT")
    results.append("=" * 70)
    results.append(f"Total shadow records: {total}")
    results.append(f"Schema versions: {dict(by_schema)}")
    results.append(f"")
    results.append(f"--- ENTITY_ID STATUS ---")
    results.append(f"Valid entity_id: {by_eid_status['valid']} ({by_eid_status['valid']*100//max(total,1)}%)")
    results.append(f"Empty entity_id: {by_eid_status['empty']}")
    results.append(f"'None' string: {by_eid_status['none_str']}")
    results.append(f"Total missing: {by_eid_status['empty'] + by_eid_status['none_str']} ({(by_eid_status['empty']+by_eid_status['none_str'])*100//max(total,1)}%)")
    results.append(f"Unique valid entities: {len(entities)}")
    results.append(f"Entities with >1 shadow: {multi}")
    results.append(f"Max shadows per entity: {max_per}")
    results.append(f"Avg shadows per entity: {total/max(len(entities),1):.2f}")
    results.append(f"Distribution (shadows per entity): {dict(sorted(dist.items()))}")
    results.append(f"")
    results.append(f"--- SHADOW TYPES ---")
    results.append(f"Horizon (hshadow_*): {by_type['horizon']}")
    results.append(f"Primary (shadow_*): {by_type['primary']}")
    results.append(f"Other/legacy: {by_type['other']}")
    results.append(f"Horizons: {dict(by_horizon)}")
    results.append(f"")
    results.append(f"--- EXIT REASONS ---")
    results.append(f"Exit reasons: {dict(by_exit)}")
    results.append(f"")
    results.append(f"--- SYMBOLS ---")
    results.append(f"Symbols: {dict(by_symbol)}")
    results.append(f"")
    results.append(f"--- SAMPLES ---")
    results.append(f"Empty entity_id samples:")
    for s in eid_empty_samples:
        results.append(f"  {s}")
    results.append(f"Other-type samples:")
    for s in other_type_samples:
        results.append(f"  {s}")
    
    # Now measure JOIN to decision trace
    results.append(f"")
    results.append("=" * 70)
    results.append("JOIN COVERAGE")
    results.append("=" * 70)
    
    dt_dir = Path("logs/decision_trace")
    if dt_dir.exists():
        dt_entities = set()
        dt_total = 0
        dt_execute = 0
        dt_no_trade = 0
        for sym_dir in sorted(dt_dir.iterdir()):
            if not sym_dir.is_dir():
                continue
            for f in sorted(sym_dir.glob("*.jsonl")):
                for line in open(f, encoding="utf-8"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except:
                        continue
                    dt_total += 1
                    eid = rec.get("entity_id", "")
                    if eid:
                        dt_entities.add(eid)
                    action = rec.get("action", "")
                    if action == "EXECUTE":
                        dt_execute += 1
                    elif action == "NO_TRADE":
                        dt_no_trade += 1

        joined = entities & dt_entities
        shadow_orphan = entities - dt_entities
        decision_only = dt_entities - entities

        results.append(f"Decision trace records: {dt_total}")
        results.append(f"Decision unique entities: {len(dt_entities)}")
        results.append(f"Decision EXECUTE: {dt_execute}")
        results.append(f"Decision NO_TRADE: {dt_no_trade}")
        results.append(f"Shadow unique entities: {len(entities)}")
        results.append(f"JOINED (both): {len(joined)}")
        results.append(f"Shadow orphans: {len(shadow_orphan)}")
        results.append(f"Decision-only (no shadow): {len(decision_only)}")
        results.append(f"Join rate (shadow->decision): {len(joined)*100//max(len(entities),1)}%")
        results.append(f"Coverage (decision->shadow): {len(joined)*100//max(len(dt_entities),1)}%")
        
        # Show orphan sample
        if shadow_orphan:
            results.append(f"Shadow orphan entity_id samples: {list(shadow_orphan)[:5]}")
    else:
        results.append("WARNING: logs/decision_trace/ not found")
    
    results.append("")
    results.append("=" * 70)
    results.append("ENTITY_ID LOSS ANALYSIS")
    results.append("=" * 70)
    
    # Correlate: which TYPE of shadow has empty entity_id?
    eid_by_type = {"horizon_has": 0, "horizon_empty": 0, "primary_has": 0, "primary_empty": 0, "other_has": 0, "other_empty": 0}
    
    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir():
            continue
        for f in sorted(sym_dir.glob("*.jsonl")):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except:
                    continue
                identity = rec.get("identity", {})
                eid = identity.get("entity_id", "") or ""
                tid = identity.get("trade_id", "") or ""
                has_eid = bool(eid and eid != "None" and eid != "null")
                
                if tid.startswith("hshadow_"):
                    if has_eid:
                        eid_by_type["horizon_has"] += 1
                    else:
                        eid_by_type["horizon_empty"] += 1
                elif tid.startswith("shadow_"):
                    if has_eid:
                        eid_by_type["primary_has"] += 1
                    else:
                        eid_by_type["primary_empty"] += 1
                else:
                    if has_eid:
                        eid_by_type["other_has"] += 1
                    else:
                        eid_by_type["other_empty"] += 1
    
    results.append(f"Entity_ID presence by shadow type:")
    h_total = eid_by_type["horizon_has"] + eid_by_type["horizon_empty"]
    p_total = eid_by_type["primary_has"] + eid_by_type["primary_empty"]
    o_total = eid_by_type["other_has"] + eid_by_type["other_empty"]
    results.append(f"  Horizon: {eid_by_type['horizon_has']}/{h_total} have eid ({eid_by_type['horizon_has']*100//max(h_total,1)}%)")
    results.append(f"  Primary: {eid_by_type['primary_has']}/{p_total} have eid ({eid_by_type['primary_has']*100//max(p_total,1)}%)")
    results.append(f"  Other:   {eid_by_type['other_has']}/{o_total} have eid ({eid_by_type['other_has']*100//max(o_total,1)}%)")
    
    results.append("")
    results.append("DONE")
    
    output = "\n".join(results)
    Path("reports/architecture/shadow_lineage_data_measurement.txt").write_text(output, encoding="utf-8")
    print(output[:200])
    print("...")
    print(f"Full output: reports/architecture/shadow_lineage_data_measurement.txt")

if __name__ == "__main__":
    main()
