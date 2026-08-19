"""
Shadow Lineage + Coverage Validation Audit.
Comprehensive measurement of Shadow data quality, lineage, and research usability.
READ-ONLY — does not modify any data.
"""
import sys
import json
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population, Universe


def main():
    out = []
    
    # ═══════════════════════════════════════════════════════════════
    # BUILD SHADOW UNIVERSE
    # ═══════════════════════════════════════════════════════════════
    builder = ShadowOutcomeUniverseBuilder()
    raw_count = builder.load()
    records = builder.build()
    meta = builder.metadata
    
    out.append("=" * 70)
    out.append("SHADOW LINEAGE + COVERAGE VALIDATION AUDIT")
    out.append("=" * 70)
    out.append(f"\nRaw loaded: {raw_count}")
    out.append(f"After exclusion: {len(records)}")
    out.append(f"Excluded (test/invalid): {raw_count - len(records)}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 7: SHADOW TYPE INVENTORY
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("SHADOW TYPE INVENTORY")
    out.append("─" * 70)
    
    type_counts = Counter(r.get("shadow_type", "UNKNOWN") for r in records)
    for t, c in type_counts.most_common():
        out.append(f"  {t}: {c} ({c*100//len(records)}%)")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: SHADOW → DECISION LINEAGE
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("SHADOW → DECISION LINEAGE")
    out.append("─" * 70)
    
    # Load decision traces
    dt_dir = Path("logs/decision_trace")
    dt_entities = set()
    dt_by_entity = {}
    dt_count = 0
    if dt_dir.exists():
        for sym_dir in sorted(dt_dir.iterdir()):
            if not sym_dir.is_dir(): continue
            for f in sorted(sym_dir.glob("*.jsonl")):
                for line in open(f, encoding="utf-8"):
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                    except: continue
                    dt_count += 1
                    eid = rec.get("entity_id", "")
                    if eid:
                        dt_entities.add(eid)
                        if eid not in dt_by_entity:
                            dt_by_entity[eid] = {
                                "action": rec.get("action", ""),
                                "terminal_stage": rec.get("terminal_stage", ""),
                                "terminal_reason": rec.get("terminal_reason", ""),
                                "regime": (rec.get("v10_market_state", {}) or {}).get("regime", {}).get("regime", ""),
                                "strategy_family": (rec.get("v10_strategy", {}) or {}).get("family", ""),
                                "score": rec.get("score_strategy") or rec.get("score_neutral"),
                            }
    
    # Measure lineage by shadow type
    shadow_eids = set()
    lineage_by_type = defaultdict(lambda: {"total": 0, "matched": 0, "orphaned": 0, "no_eid": 0})
    
    for r in records:
        eid = r.get("entity_id", "")
        stype = r.get("shadow_type", "UNKNOWN")
        lineage_by_type[stype]["total"] += 1
        if not eid:
            lineage_by_type[stype]["no_eid"] += 1
        elif eid in dt_entities:
            lineage_by_type[stype]["matched"] += 1
            shadow_eids.add(eid)
        else:
            lineage_by_type[stype]["orphaned"] += 1
    
    out.append(f"Decision traces loaded: {dt_count}")
    out.append(f"Decision unique entities: {len(dt_entities)}")
    out.append(f"Shadow unique entities (with eid): {len(shadow_eids)}")
    out.append("")
    out.append(f"{'Type':<25} {'Total':>6} {'Matched':>8} {'Orphan':>7} {'No EID':>7} {'Match%':>7}")
    out.append(f"{'─'*25} {'─'*6} {'─'*8} {'─'*7} {'─'*7} {'─'*7}")
    for stype, stats in sorted(lineage_by_type.items()):
        total = stats["total"]
        matched = stats["matched"]
        pct = f"{matched*100//max(total,1)}%" if total else "N/A"
        out.append(f"  {stype:<23} {total:>6} {matched:>8} {stats['orphaned']:>7} {stats['no_eid']:>7} {pct:>7}")
    
    total_matched = sum(s["matched"] for s in lineage_by_type.values())
    total_no_eid = sum(s["no_eid"] for s in lineage_by_type.values())
    total_orphaned = sum(s["orphaned"] for s in lineage_by_type.values())
    out.append(f"  {'TOTAL':<23} {len(records):>6} {total_matched:>8} {total_orphaned:>7} {total_no_eid:>7} {total_matched*100//max(len(records),1)}%")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: SHADOW → MARKET LINEAGE
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("SHADOW → MARKET LINEAGE (via Decision join)")
    out.append("─" * 70)
    
    market_linked = 0
    for r in records:
        eid = r.get("entity_id", "")
        if eid and eid in dt_by_entity:
            dt_rec = dt_by_entity[eid]
            if dt_rec.get("regime"):
                market_linked += 1
    
    out.append(f"  Shadow with market context (via Decision regime): {market_linked}/{len(records)} ({market_linked*100//max(len(records),1)}%)")
    out.append(f"  Note: Market linkage is via entity_id → Decision → v10_market_state")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 6: SHADOW → STRATEGY LINEAGE
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("SHADOW → STRATEGY LINEAGE")
    out.append("─" * 70)
    
    # Strategy from shadow record itself
    shadow_strategy = sum(1 for r in records if r.get("strategy_id") and r["strategy_id"] not in ("", "None"))
    # Strategy from decision join
    decision_strategy = 0
    for r in records:
        eid = r.get("entity_id", "")
        if eid and eid in dt_by_entity:
            if dt_by_entity[eid].get("strategy_family"):
                decision_strategy += 1
    
    out.append(f"  From shadow record (strategy_id): {shadow_strategy}/{len(records)} ({shadow_strategy*100//max(len(records),1)}%)")
    out.append(f"  From Decision join (strategy_family): {decision_strategy}/{len(records)} ({decision_strategy*100//max(len(records),1)}%)")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 8: HORIZON GEOMETRY VALIDATION
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("HORIZON GEOMETRY VALIDATION")
    out.append("─" * 70)
    
    horizon_stats = defaultdict(lambda: {"count": 0, "valid_r": 0, "valid_entry": 0, "valid_sl": 0, "valid_tp": 0})
    for r in records:
        hz = r.get("evaluated_horizon") or r.get("trade_horizon") or "UNKNOWN"
        horizon_stats[hz]["count"] += 1
        if r.get("r_multiple") is not None:
            horizon_stats[hz]["valid_r"] += 1
        if r.get("entry_price") and r["entry_price"] > 0:
            horizon_stats[hz]["valid_entry"] += 1
        if r.get("stop_loss") and r["stop_loss"] > 0:
            horizon_stats[hz]["valid_sl"] += 1
        if r.get("take_profit") and r["take_profit"] > 0:
            horizon_stats[hz]["valid_tp"] += 1
    
    out.append(f"{'Horizon':<12} {'Count':>6} {'R Valid':>8} {'Entry':>6} {'SL':>6} {'TP':>6}")
    out.append(f"{'─'*12} {'─'*6} {'─'*8} {'─'*6} {'─'*6} {'─'*6}")
    for hz, stats in sorted(horizon_stats.items()):
        out.append(f"  {hz:<10} {stats['count']:>6} {stats['valid_r']:>8} {stats['valid_entry']:>6} {stats['valid_sl']:>6} {stats['valid_tp']:>6}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 9: SHADOW OUTCOME COMPLETENESS
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("SHADOW OUTCOME FIELD COMPLETENESS")
    out.append("─" * 70)
    
    fields_to_check = [
        "entity_id", "shadow_trade_id", "shadow_type", "trade_horizon",
        "r_multiple", "mfe_r", "mae_r", "exit_reason", "bars_held",
        "direction", "entry_price", "stop_loss", "take_profit",
        "symbol", "score", "pattern", "strategy_id",
        "evidence_source", "has_entity_id",
    ]
    
    for field in fields_to_check:
        valid = sum(1 for r in records if r.get(field) is not None and r.get(field) != "" and r.get(field) != 0)
        out.append(f"  {field:<25} {valid:>6}/{len(records)} ({valid*100//max(len(records),1)}%)")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 10: DUPLICATES AND CARDINALITY
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("DUPLICATES AND CARDINALITY")
    out.append("─" * 70)
    
    # shadow_trade_id uniqueness
    trade_ids = [r.get("shadow_trade_id", "") for r in records]
    unique_trade_ids = len(set(trade_ids))
    duplicates = len(trade_ids) - unique_trade_ids
    out.append(f"  shadow_trade_id unique: {unique_trade_ids}/{len(records)} (duplicates: {duplicates})")
    
    # entity_id cardinality
    eid_counts = Counter(r.get("entity_id", "") for r in records if r.get("entity_id"))
    cardinality_dist = Counter(eid_counts.values())
    out.append(f"  entity_id cardinality distribution:")
    for card, count in sorted(cardinality_dist.items()):
        out.append(f"    {card} shadow(s) per entity: {count} entities")
    
    # Check for identical (entity_id, horizon) duplicates
    eid_hz_pairs = [(r.get("entity_id", ""), r.get("evaluated_horizon") or r.get("trade_horizon", "")) for r in records if r.get("entity_id")]
    eid_hz_counter = Counter(eid_hz_pairs)
    eid_hz_dups = sum(1 for c in eid_hz_counter.values() if c > 1)
    out.append(f"  Duplicate (entity_id, horizon) pairs: {eid_hz_dups}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 11: ORPHANS AND LEGACY
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("ORPHAN AND LEGACY CLASSIFICATION")
    out.append("─" * 70)
    
    valid_current = 0
    valid_legacy = 0
    orphan = 0
    no_lineage = 0
    
    for r in records:
        eid = r.get("entity_id", "")
        has_contract = r.get("has_lineage_contract", False)
        if has_contract:
            valid_current += 1
        elif eid and eid in dt_entities:
            valid_legacy += 1
        elif eid and eid not in dt_entities:
            orphan += 1
        else:
            no_lineage += 1
    
    out.append(f"  VALID_CURRENT (new lineage contract): {valid_current}")
    out.append(f"  VALID_LEGACY (entity_id joins, no contract fields): {valid_legacy}")
    out.append(f"  ORPHAN (entity_id doesn't match any Decision): {orphan}")
    out.append(f"  NO_LINEAGE (empty entity_id): {no_lineage}")
    out.append(f"  Research-usable (VALID_CURRENT + VALID_LEGACY): {valid_current + valid_legacy}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 12: RESEARCH QUESTION COMPATIBILITY
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("RESEARCH QUESTION COMPATIBILITY")
    out.append("─" * 70)
    
    # SD-001: ALL_SHADOW_OUTCOMES
    pop_all = builder.get_population(Population.ALL_SHADOW_OUTCOMES)
    r_valid_all = sum(1 for r in pop_all if r.get("r_multiple") is not None)
    out.append(f"  SD-001 (ALL_SHADOW_OUTCOMES): pop={len(pop_all)}, r_valid={r_valid_all} → {'OK' if r_valid_all >= 30 else 'INSUFFICIENT'}")
    
    # SD-002: SHADOW_FROM_NO_TRADE
    pop_no_trade = builder.get_population(Population.SHADOW_FROM_NO_TRADE)
    r_valid_nt = sum(1 for r in pop_no_trade if r.get("r_multiple") is not None)
    out.append(f"  SD-002 (SHADOW_FROM_NO_TRADE): pop={len(pop_no_trade)}, r_valid={r_valid_nt} → {'OK' if r_valid_nt >= 20 else 'INSUFFICIENT'}")
    
    # SD-004: needs entity_id join to Decision
    pop_joinable = [r for r in pop_no_trade if r.get("entity_id") and r["entity_id"] in dt_entities]
    out.append(f"  SD-004 (joinable to Decision): pop={len(pop_joinable)} → {'OK' if len(pop_joinable) >= 20 else 'INSUFFICIENT'}")
    
    # SD-005: SHADOW_FROM_NO_TRADE with multiple horizons
    hz_in_pop = Counter(r.get("trade_horizon", "?") for r in pop_no_trade)
    multi_hz = sum(1 for c in hz_in_pop.values() if c >= 10)
    out.append(f"  SD-005 (horizon groups ≥10 obs): {multi_hz} groups → {'OK' if multi_hz >= 2 else 'INSUFFICIENT'}")
    out.append(f"         Horizons: {dict(hz_in_pop)}")
    
    # SD-006: strategy_id coverage
    strat_coverage = sum(1 for r in pop_all if r.get("strategy_id") and r["strategy_id"] not in ("", "None"))
    out.append(f"  SD-006 (strategy_id coverage): {strat_coverage}/{len(pop_all)} ({strat_coverage*100//max(len(pop_all),1)}%) → {'OK' if strat_coverage >= 10 else 'INSUFFICIENT'}")
    
    # SD-007: regime coverage (from shadow record itself)
    regime_coverage = sum(1 for r in pop_all if r.get("regime") and r["regime"] not in ("", "None"))
    out.append(f"  SD-007 (regime in shadow record): {regime_coverage}/{len(pop_all)} ({regime_coverage*100//max(len(pop_all),1)}%)")
    # Regime via join
    regime_join = sum(1 for r in pop_all if r.get("entity_id") and r["entity_id"] in dt_by_entity and dt_by_entity[r["entity_id"]].get("regime"))
    out.append(f"  SD-007 (regime via Decision join): {regime_join}/{len(pop_all)} ({regime_join*100//max(len(pop_all),1)}%) → {'OK' if regime_join >= 10 else 'INSUFFICIENT'}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 13: COVERAGE SCORECARD
    # ═══════════════════════════════════════════════════════════════
    out.append("─" * 70)
    out.append("FINAL COVERAGE SCORECARD")
    out.append("─" * 70)
    
    total = len(records)
    with_eid = sum(1 for r in records if r.get("has_entity_id"))
    with_r = sum(1 for r in records if r.get("r_multiple") is not None)
    with_decision = total_matched
    with_market = market_linked
    with_strategy_record = shadow_strategy
    with_strategy_join = decision_strategy
    non_legacy = valid_current
    research_usable = valid_current + valid_legacy
    
    out.append(f"  {'Metric':<35} {'Count':>6} {'%':>5}")
    out.append(f"  {'─'*35} {'─'*6} {'─'*5}")
    out.append(f"  {'Total Shadow observations':<35} {total:>6} {'100%':>5}")
    out.append(f"  {'Valid entity_id':<35} {with_eid:>6} {with_eid*100//total:>4}%")
    out.append(f"  {'Valid r_multiple':<35} {with_r:>6} {with_r*100//total:>4}%")
    out.append(f"  {'Decision lineage (matched)':<35} {with_decision:>6} {with_decision*100//total:>4}%")
    out.append(f"  {'Market context (via join)':<35} {with_market:>6} {with_market*100//total:>4}%")
    out.append(f"  {'Strategy (from record)':<35} {with_strategy_record:>6} {with_strategy_record*100//total:>4}%")
    out.append(f"  {'Strategy (from Decision join)':<35} {with_strategy_join:>6} {with_strategy_join*100//total:>4}%")
    out.append(f"  {'New lineage contract':<35} {non_legacy:>6} {non_legacy*100//max(total,1):>4}%")
    out.append(f"  {'Research-usable (current+legacy)':<35} {research_usable:>6} {research_usable*100//total:>4}%")
    out.append(f"  {'Orphaned':<35} {orphan:>6} {orphan*100//total:>4}%")
    out.append(f"  {'No lineage (empty entity_id)':<35} {no_lineage:>6} {no_lineage*100//total:>4}%")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # TRUST CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════
    out.append("=" * 70)
    out.append("TRUST CLASSIFICATION")
    out.append("=" * 70)
    
    # Decision criteria
    decision_match_pct = with_decision * 100 // total
    r_coverage = with_r * 100 // total
    orphan_pct = orphan * 100 // total
    
    if decision_match_pct >= 70 and r_coverage >= 95 and orphan_pct < 5:
        trust = "🟢 TRUSTED"
        reason = "High lineage coverage, complete R-multiples, minimal orphaning"
    elif decision_match_pct >= 50 and r_coverage >= 90 and orphan_pct < 10:
        trust = "🟠 TRUSTED WITH LIMITATIONS"
        reason = f"Decision match {decision_match_pct}% (legacy data lacks entity_id). R coverage {r_coverage}%. Orphans {orphan_pct}%."
    else:
        trust = "🔴 NOT TRUSTWORTHY"
        reason = f"Decision match only {decision_match_pct}%, R coverage {r_coverage}%, orphans {orphan_pct}%"
    
    out.append(f"\n  {trust}")
    out.append(f"  Reason: {reason}")
    out.append(f"\n  Decision match: {decision_match_pct}%")
    out.append(f"  R-multiple coverage: {r_coverage}%")
    out.append(f"  Orphan rate: {orphan_pct}%")
    out.append(f"  Research-usable: {research_usable*100//total}%")
    out.append("")
    out.append("=" * 70)
    
    output = "\n".join(out)
    Path("reports/architecture/shadow_lineage_validation_final.txt").write_text(output, encoding="utf-8")
    print(output[:3000])
    print("...")
    print(f"Full report: reports/architecture/shadow_lineage_validation_final.txt")


if __name__ == "__main__":
    main()
