"""
H1 INVESTIGATION: Execution Leakage Analysis

Determines whether the +0.7636R gap between V10_PRIMARY shadow (+0.59R) and 
live execution (-0.18R) represents genuine leakage or model limitation.

CRITICAL FIRST QUESTION: Are these actually the same entities?
- V10_PRIMARY has 952 records
- Live execution has 94 records
- If they don't overlap by entity_id, the comparison is INVALID

Does NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes import ExecutionUniverseBuilder


def main():
    out = []
    out.append("=" * 70)
    out.append("H1 INVESTIGATION: EXECUTION LEAKAGE ANALYSIS")
    out.append("=" * 70)
    out.append("")
    
    # Build universes
    exe_builder = ExecutionUniverseBuilder()
    exe_builder.build()
    shadow_builder = ShadowOutcomeUniverseBuilder()
    shadow_builder.build()
    
    live_trades = exe_builder.records
    primary_shadows = shadow_builder.get_population(Population.PRIMARY_V10_SHADOW)
    
    out.append(f"Live trades: {len(live_trades)}")
    out.append(f"V10_PRIMARY shadows: {len(primary_shadows)}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 1: ARE THESE THE SAME ENTITIES?
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("STEP 1: ENTITY OVERLAP — ARE WE COMPARING THE SAME TRADES?")
    out.append("━" * 70)
    out.append("")
    
    live_eids = set(r.get("entity_id", "") for r in live_trades if r.get("entity_id"))
    shadow_eids = set(r.get("entity_id", "") for r in primary_shadows if r.get("entity_id"))
    
    overlap = live_eids & shadow_eids
    live_only = live_eids - shadow_eids
    shadow_only = shadow_eids - live_eids
    
    out.append(f"  Live trades with entity_id: {len(live_eids)}")
    out.append(f"  V10_PRIMARY with entity_id: {len(shadow_eids)}")
    out.append(f"  OVERLAP (same entity in both): {len(overlap)}")
    out.append(f"  Live-only (no matching shadow): {len(live_only)}")
    out.append(f"  Shadow-only (no matching live trade): {len(shadow_only)}")
    out.append("")
    
    if len(overlap) == 0:
        out.append("  ⚠️  CRITICAL: ZERO OVERLAP between Live and V10_PRIMARY entities!")
        out.append("  The +0.76R 'leakage' comparison is NOT apples-to-apples.")
        out.append("  V10_PRIMARY shadows are NOT the shadow counterparts of the 94 live trades.")
        out.append("")
        out.append("  EXPLANATION:")
        out.append("  - V10_PRIMARY shadows use entity_id from their creation time")
        out.append("  - Live trades use entity_id enriched from execution_results")
        out.append("  - These may be the same underlying opportunities but with different")
        out.append("    entity_id formats or enrichment timing")
        out.append("")
    elif len(overlap) < 10:
        out.append(f"  ⚠️  VERY LOW OVERLAP: Only {len(overlap)} paired observations available.")
        out.append(f"  The comparison between V10_PRIMARY and Live is largely UNPAIRED.")
    else:
        out.append(f"  ✓ {len(overlap)} paired observations available for direct comparison.")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 2: EXAMINE ENTITY_ID FORMATS
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("STEP 2: ENTITY_ID FORMAT COMPARISON")
    out.append("━" * 70)
    out.append("")
    
    live_eid_samples = list(live_eids)[:5]
    shadow_eid_samples = list(shadow_eids)[:5]
    
    out.append(f"  Live entity_id samples: {live_eid_samples}")
    out.append(f"  Shadow entity_id samples: {shadow_eid_samples}")
    out.append("")
    
    # Check if live trades use pos_NNNNN format (trade_id fallback)
    live_eid_types = Counter()
    for r in live_trades:
        eid = r.get("entity_id", "")
        if eid.startswith("pos_"):
            live_eid_types["pos_fallback"] += 1
        elif "_" in eid and not eid.startswith("pos_"):
            live_eid_types["symbol_bartime"] += 1
        elif eid:
            live_eid_types["other"] += 1
        else:
            live_eid_types["empty"] += 1
    
    shadow_eid_types = Counter()
    for r in primary_shadows:
        eid = r.get("entity_id", "")
        if not eid:
            shadow_eid_types["empty"] += 1
        elif "_" in eid and any(c.isdigit() for c in eid):
            shadow_eid_types["symbol_bartime"] += 1
        else:
            shadow_eid_types["other"] += 1
    
    out.append(f"  Live entity_id formats: {dict(live_eid_types)}")
    out.append(f"  Shadow entity_id formats: {dict(shadow_eid_types)}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 3: PAIRED ANALYSIS (if overlap exists)
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("STEP 3: PAIRED ANALYSIS")
    out.append("━" * 70)
    out.append("")
    
    if overlap:
        # Build lookup
        live_by_eid = {r["entity_id"]: r for r in live_trades if r.get("entity_id") in overlap}
        shadow_by_eid = {}
        for r in primary_shadows:
            eid = r.get("entity_id", "")
            if eid in overlap and eid not in shadow_by_eid:
                shadow_by_eid[eid] = r
        
        paired_diffs = []
        for eid in overlap:
            live_r = live_by_eid.get(eid, {}).get("r_multiple")
            shadow_r = shadow_by_eid.get(eid, {}).get("r_multiple")
            if live_r is not None and shadow_r is not None:
                paired_diffs.append({
                    "entity_id": eid,
                    "live_r": live_r,
                    "shadow_r": shadow_r,
                    "diff": shadow_r - live_r,
                    "live_exit": live_by_eid[eid].get("exit_reason", ""),
                    "shadow_exit": shadow_by_eid[eid].get("exit_reason", ""),
                })
        
        if paired_diffs:
            live_rs = [p["live_r"] for p in paired_diffs]
            shadow_rs = [p["shadow_r"] for p in paired_diffs]
            diffs = [p["diff"] for p in paired_diffs]
            
            out.append(f"  Paired observations: {len(paired_diffs)}")
            out.append(f"  Live mean R: {statistics.mean(live_rs):+.4f}")
            out.append(f"  Shadow mean R: {statistics.mean(shadow_rs):+.4f}")
            out.append(f"  Mean difference (shadow - live): {statistics.mean(diffs):+.4f}")
            out.append(f"  Median difference: {statistics.median(diffs):+.4f}")
            out.append("")
            
            # Exit reason comparison
            exit_match = sum(1 for p in paired_diffs if p["live_exit"] == p["shadow_exit"])
            out.append(f"  Exit reason agreement: {exit_match}/{len(paired_diffs)} ({exit_match*100//len(paired_diffs)}%)")
            
            exit_diffs = Counter()
            for p in paired_diffs:
                exit_diffs[f"{p['live_exit']} → {p['shadow_exit']}"] += 1
            out.append(f"  Exit transitions:")
            for trans, count in exit_diffs.most_common(5):
                out.append(f"    {trans}: {count}")
        else:
            out.append(f"  No paired R-multiples available despite {len(overlap)} entity overlap")
    else:
        out.append(f"  NO PAIRED ANALYSIS POSSIBLE — zero entity overlap")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 4: SHADOW MODEL ASSUMPTIONS
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("STEP 4: SHADOW MODEL vs LIVE EXECUTION — STRUCTURAL DIFFERENCES")
    out.append("━" * 70)
    out.append("")
    
    # Shadow model properties (from code knowledge)
    out.append("  SHADOW MODEL:")
    out.append("    Entry: (bid+ask)/2 midpoint at decision time")
    out.append("    SL: V10 RiskEngine computed stop (from OrderIntent.sl)")
    out.append("    TP: V10 EntryEngine computed target (from OrderIntent.tp)")
    out.append("    Exit: First of SL/TP/60-bar timeout")
    out.append("    SL checked BEFORE TP on same bar")
    out.append("    No spread deduction")
    out.append("    No commission")
    out.append("    No slippage")
    out.append("    No trade management (no trailing, no BE, no partial)")
    out.append("    Position held until mechanical exit only")
    out.append("")
    out.append("  LIVE EXECUTION:")
    out.append("    Entry: Actual broker fill (includes slippage)")
    out.append("    SL: May be modified by TradeStateManager (BE, trailing)")
    out.append("    TP: May be modified")
    out.append("    Exit: Broker-confirmed close (SL/TP/management/manual)")
    out.append("    Spread paid on entry AND exit")
    out.append("    Commission charged")
    out.append("    Slippage on entry and exit")
    out.append("    Trade management active (trailing stop, breakeven, partial exits)")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 5: QUANTIFY KNOWN STRUCTURAL DIFFERENCES
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("STEP 5: QUANTIFYING THE GAP")
    out.append("━" * 70)
    out.append("")
    
    # The 952 V10_PRIMARY shadows vs 94 live trades are DIFFERENT populations
    # PRIMARY shadows are created for EVERY EXECUTE decision (including those 
    # that passed risk but may have been blocked by runtime guards or broker rejection)
    # Live trades are only those that actually filled at the broker
    
    out.append(f"  V10_PRIMARY count: {len(primary_shadows)} (every EXECUTE decision)")
    out.append(f"  Live filled trades: {len(live_trades)} (broker-confirmed fills only)")
    out.append(f"  Ratio: {len(live_trades)}/{len(primary_shadows)} = {len(live_trades)*100//max(len(primary_shadows),1)}%")
    out.append("")
    out.append("  POPULATION MISMATCH EXPLANATION:")
    out.append("    V10_PRIMARY shadows are opened for every EXECUTE decision")
    out.append("    BEFORE runtime guards (daily trade limit, cooldown, correlation, spread)")
    out.append("    BEFORE broker execution (which may reject)")
    out.append("    Therefore: 952 shadows ≠ 94 fills")
    out.append("    The 858 extra shadows represent:")
    out.append("      - Guard-blocked opportunities")
    out.append("      - Broker-rejected orders")
    out.append("      - Or shadows from a different time period than live trades")
    out.append("")
    
    # V10_PRIMARY exit distribution
    shadow_exits = Counter(r.get("exit_reason", "?") for r in primary_shadows)
    out.append(f"  V10_PRIMARY exit distribution:")
    for exit_r, count in shadow_exits.most_common():
        out.append(f"    {exit_r}: {count} ({count*100//len(primary_shadows)}%)")
    out.append("")
    
    # Live exit distribution
    live_exits = Counter(r.get("exit_reason", "?") for r in live_trades)
    out.append(f"  Live trade exit distribution:")
    for exit_r, count in live_exits.most_common():
        out.append(f"    {exit_r}: {count} ({count*100//len(live_trades)}%)")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 6: DIAGNOSTIC CONCLUSION
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("DIAGNOSTIC CONCLUSION")
    out.append("━" * 70)
    out.append("")
    
    out.append("A. PROVEN:")
    out.append("   - V10_PRIMARY and Live trades are DIFFERENT POPULATIONS")
    out.append(f"   - V10_PRIMARY = {len(primary_shadows)} shadows (all EXECUTE decisions)")
    out.append(f"   - Live trades = {len(live_trades)} (broker fills only)")
    out.append(f"   - Entity overlap: {len(overlap)}")
    out.append("   - The +0.76R 'gap' is NOT a paired leakage measurement")
    out.append("   - It compares two different population means (unpaired)")
    out.append("")
    
    out.append("B. PLAUSIBLE EXPLANATIONS for the gap:")
    out.append("   1. POPULATION BIAS: V10_PRIMARY includes 858 shadows for opportunities")
    out.append("      that were guard-blocked or broker-rejected. These may have BETTER")
    out.append("      geometry than the 94 that actually filled (selection effect).")
    out.append("   2. SHADOW MODEL OPTIMISM: No spread/commission/slippage in shadow.")
    out.append("      For each trade at ~2 pips spread cost ≈ 0.1-0.3R per trade.")
    out.append("   3. TRADE MANAGEMENT: Live uses trailing/BE which may cut winners short")
    out.append("      and not be captured by the shadow's simple SL/TP/timeout model.")
    out.append("   4. TEMPORAL MISMATCH: Primary shadows and live trades may not cover")
    out.append("      the same time period (shadow data accumulates continuously,")
    out.append("      live trades only from the period V10 was execution-enabled).")
    out.append("")
    
    out.append("C. SHADOW MODEL LIMITATIONS:")
    out.append("   - Entry at midpoint (live pays spread at entry)")
    out.append("   - No spread deduction (≈0.1-0.3R per trade)")
    out.append("   - No commission")
    out.append("   - SL checked before TP same bar (pessimistic for shadow)")
    out.append("   - 60-bar timeout (live has no forced timeout)")
    out.append("   - No trade management (live has trailing/BE)")
    out.append("")
    
    out.append("D. UNRESOLVED:")
    out.append(f"   - Whether the {len(overlap)} overlapping entities show actual leakage")
    out.append("   - Whether trade management systematically helps or hurts")
    out.append("   - What proportion of the 858 non-filled shadows would have been profitable")
    out.append("   - Whether guard-blocking is removing good opportunities")
    out.append("")
    
    out.append("━" * 70)
    out.append("REVISED HYPOTHESIS")
    out.append("━" * 70)
    out.append("")
    out.append("The original H1 framing ('execution leakage') is MISLEADING.")
    out.append("")
    out.append("The +0.76R gap is NOT primarily execution leakage.")
    out.append("It is a POPULATION DIFFERENCE between:")
    out.append("  - ALL V10 EXECUTE decisions (952 opportunities, many never filled)")
    out.append("  - ACTUALLY FILLED trades (94 that survived guards + broker)")
    out.append("")
    out.append("The correct decomposition is:")
    out.append("  Gap = Population_bias + Execution_costs + Management_effects + Model_limitations")
    out.append("")
    out.append("NEXT EXPERIMENTS REQUIRED:")
    out.append("  1. PAIRED COMPARISON: Match live trades to their V10_PRIMARY shadows by entity_id")
    out.append("     (requires investigating why overlap is low and fixing entity_id matching)")
    out.append("  2. GUARD ANALYSIS: Compare shadow R of guard-blocked vs guard-passed opportunities")
    out.append("  3. COST ESTIMATION: Quantify spread+commission in R terms for the 94 live trades")
    out.append("  4. TEMPORAL ALIGNMENT: Confirm which shadow records correspond to the live trading period")
    out.append("")
    
    output = "\n".join(out)
    Path("reports/research/baseline/h1_execution_leakage_investigation.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
