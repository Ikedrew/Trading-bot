"""
Phase 6: Historical Data Classification Report.

Runs the updated ShadowOutcomeUniverseBuilder against existing data and
produces a before/after comparison showing how records are classified
under the new shadow lineage contract.

NO DATA IS MODIFIED OR DELETED.
This script is READ-ONLY against persisted shadow_trades data.
"""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population

def main():
    out = []
    out.append("=" * 70)
    out.append("PHASE 6: HISTORICAL DATA CLASSIFICATION REPORT")
    out.append("=" * 70)
    out.append("")

    # Build the universe
    builder = ShadowOutcomeUniverseBuilder()
    raw_count = builder.load()
    records = builder.build()
    meta = builder.metadata

    out.append(f"Source records loaded: {raw_count}")
    out.append(f"Records after exclusion: {len(records)}")
    out.append(f"Exclusions: {meta.exclusions}")
    out.append("")

    # ─── CLASSIFICATION BY shadow_type ─────────────────────────
    out.append("--- SHADOW TYPE CLASSIFICATION ---")
    type_counts = defaultdict(int)
    for r in records:
        type_counts[r.get("shadow_type", "MISSING")] += 1
    for k, v in sorted(type_counts.items()):
        out.append(f"  {k}: {v}")
    out.append("")

    # ─── CLASSIFICATION BY horizon_selection_status ─────────────
    out.append("--- HORIZON SELECTION STATUS ---")
    status_counts = defaultdict(int)
    for r in records:
        status_counts[r.get("horizon_selection_status", "MISSING")] += 1
    for k, v in sorted(status_counts.items()):
        out.append(f"  {k}: {v}")
    out.append("")

    # ─── CLASSIFICATION BY data_quality ────────────────────────
    out.append("--- DATA QUALITY CLASSIFICATION ---")
    quality_counts = defaultdict(int)
    for r in records:
        quality_counts[r.get("data_quality", "MISSING")] += 1
    for k, v in sorted(quality_counts.items()):
        out.append(f"  {k}: {v}")
    out.append("")

    # ─── CLASSIFICATION BY v10_action ──────────────────────────
    out.append("--- V10 ACTION ---")
    action_counts = defaultdict(int)
    for r in records:
        action_counts[r.get("v10_action", "") or "LEGACY_UNKNOWN"] += 1
    for k, v in sorted(action_counts.items()):
        out.append(f"  {k}: {v}")
    out.append("")

    # ─── CLASSIFICATION BY has_lineage_contract ────────────────
    out.append("--- LINEAGE CONTRACT PRESENCE ---")
    has_contract = sum(1 for r in records if r.get("has_lineage_contract"))
    no_contract = len(records) - has_contract
    out.append(f"  Has lineage contract (new format): {has_contract}")
    out.append(f"  Legacy (no lineage contract): {no_contract}")
    out.append("")

    # ─── POPULATION SIZES ──────────────────────────────────────
    out.append("--- POPULATION SIZES ---")
    populations_to_check = [
        Population.ALL_SHADOW_OUTCOMES,
        Population.SHADOW_WINS,
        Population.SHADOW_LOSSES,
        Population.PRIMARY_V10_SHADOW,
        Population.HORIZON_SCALP,
        Population.HORIZON_INTRADAY,
        Population.HORIZON_EXTENDED,
        Population.SHADOW_FROM_EXECUTE,
        Population.SHADOW_FROM_NO_TRADE,
        Population.SHADOW_TP_HIT,
        Population.SHADOW_SL_HIT,
        Population.SHADOW_TIMEOUT,
    ]
    for pop in populations_to_check:
        pop_records = builder.get_population(pop)
        out.append(f"  {pop.value}: {len(pop_records)}")
    out.append("")

    # ─── ENTITY_ID COVERAGE ────────────────────────────────────
    out.append("--- ENTITY_ID COVERAGE ---")
    has_eid = sum(1 for r in records if r.get("has_entity_id"))
    out.append(f"  With entity_id: {has_eid} ({has_eid*100//max(len(records),1)}%)")
    out.append(f"  Without entity_id: {len(records) - has_eid}")
    out.append("")

    # ─── CROSS-REFERENCE: shadow_type x data_quality ───────────
    out.append("--- SHADOW_TYPE x DATA_QUALITY ---")
    cross = defaultdict(int)
    for r in records:
        key = f"{r.get('shadow_type', '?')} | {r.get('data_quality', '?')}"
        cross[key] += 1
    for k, v in sorted(cross.items()):
        out.append(f"  {k}: {v}")
    out.append("")

    # ─── QUARANTINE SUMMARY ────────────────────────────────────
    out.append("--- QUARANTINE CLASSIFICATION ---")
    out.append(f"  VALID (has lineage contract): {has_contract}")
    out.append(f"  CONDITIONAL (legacy, usable with limitation): {no_contract}")
    out.append(f"  INVALID (test contamination, excluded by builder): {raw_count - len(records)}")
    out.append(f"  DELETED: 0 (no data deleted)")
    out.append("")

    out.append("--- INVARIANT CHECK ---")
    # Every record must have r_multiple
    all_have_r = all(r.get("r_multiple") is not None for r in records)
    out.append(f"  All records have r_multiple: {all_have_r}")
    # Every record must have shadow_trade_id
    all_have_id = all(r.get("shadow_trade_id") for r in records)
    out.append(f"  All records have shadow_trade_id: {all_have_id}")
    # Every record must have evidence_source = COUNTERFACTUAL
    all_counterfactual = all(r.get("evidence_source") == "COUNTERFACTUAL" for r in records)
    out.append(f"  All records labelled COUNTERFACTUAL: {all_counterfactual}")
    out.append("")

    out.append("PHASE 6 COMPLETE — No data modified or deleted.")

    output = "\n".join(out)
    Path("reports/architecture/phase6_historical_classification.txt").write_text(output, encoding="utf-8")
    print(output[:500])
    print("...")
    print(f"Full report: reports/architecture/phase6_historical_classification.txt")

if __name__ == "__main__":
    main()
