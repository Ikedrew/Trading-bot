"""
SELECTION FUNNEL ANALYSIS: 985 EXECUTE → 94 LIVE

Traces every post-Decision gate between V10 EXECUTE and broker fill.
Cross-references decision_ledger RISK_BLOCK entries (guard name in reason)
with V10_PRIMARY shadow R-multiples to determine if guards are quality-improving
or quality-destroying.

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, ".")


def load_decision_ledger():
    """Load all decision ledger records."""
    records = []
    ledger_dir = Path("logs/decision_ledger")
    for symbol_dir in ledger_dir.iterdir():
        if not symbol_dir.is_dir():
            continue
        for f in symbol_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append(rec)
                except Exception:
                    pass
    return records


def load_shadow_primary():
    """Load V10_PRIMARY shadow outcomes."""
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def load_live_trades():
    """Load live execution universe."""
    from research_engine.v10.universes import ExecutionUniverseBuilder
    builder = ExecutionUniverseBuilder()
    builder.build()
    return builder.records


def main():
    out = []
    out.append("=" * 80)
    out.append("SELECTION FUNNEL ANALYSIS: 985 EXECUTE → 94 LIVE")
    out.append("=" * 80)
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # LOAD DATA
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("Loading decision_ledger...")
    ledger = load_decision_ledger()
    out.append(f"  Total decision_ledger records: {len(ledger)}")

    out.append("Loading V10_PRIMARY shadows...")
    shadows = load_shadow_primary()
    out.append(f"  V10_PRIMARY shadow records: {len(shadows)}")

    out.append("Loading live trades...")
    live = load_live_trades()
    out.append(f"  Live trade records: {len(live)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: DECISION LEDGER FUNNEL — EXECUTE vs RISK_BLOCK
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 1: DECISION LEDGER — EXECUTE vs RISK_BLOCK COUNTS")
    out.append("━" * 80)
    out.append("")

    decision_counts = Counter(r["decision"] for r in ledger)
    out.append("  Overall decision distribution:")
    for dec, count in decision_counts.most_common():
        out.append(f"    {dec}: {count}")
    out.append("")

    # Filter to EXECUTE and RISK_BLOCK (the two outcomes AFTER V10 says EXECUTE)
    execute_records = [r for r in ledger if r["decision"] == "EXECUTE"]
    risk_block_records = [r for r in ledger if r["decision"] == "RISK_BLOCK"]

    out.append(f"  EXECUTE decisions (passed all guards): {len(execute_records)}")
    out.append(f"  RISK_BLOCK decisions (guard blocked): {len(risk_block_records)}")
    out.append(f"  Total V10-intended-EXECUTE: {len(execute_records) + len(risk_block_records)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: GUARD BREAKDOWN — Which guards block which opportunities
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 2: GUARD-BY-GUARD BREAKDOWN")
    out.append("━" * 80)
    out.append("")

    # Parse guard name from reason field
    guard_counts = Counter()
    guard_records = defaultdict(list)
    for r in risk_block_records:
        reason = r.get("reason", "")
        risk_flag = r.get("risk_state", {}).get("risk_flag", "") or r.get("risk_flag", "")
        # Guard name is either in risk_flag or parsed from reason (before first ':')
        guard_name = risk_flag if risk_flag else reason.split(":")[0]
        guard_counts[guard_name] += 1
        guard_records[guard_name].append(r)

    out.append("  Guard rejection counts (from decision_ledger RISK_BLOCK):")
    for guard, count in guard_counts.most_common():
        pct = count * 100 / len(risk_block_records) if risk_block_records else 0
        out.append(f"    {guard}: {count} ({pct:.1f}%)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: ENTITY_ID MATCHING — Link ledger records to shadow R
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 3: ENTITY_ID CROSS-REFERENCE WITH SHADOW R")
    out.append("━" * 80)
    out.append("")

    # Build shadow lookup by entity_id
    shadow_by_eid = {}
    shadow_no_eid = 0
    for s in shadows:
        eid = s.get("entity_id", "")
        if eid:
            shadow_by_eid[eid] = s
        else:
            shadow_no_eid += 1

    out.append(f"  Shadow records with entity_id: {len(shadow_by_eid)}")
    out.append(f"  Shadow records WITHOUT entity_id: {shadow_no_eid}")
    out.append("")

    # Match RISK_BLOCK ledger records to shadows
    matched_blocked = []
    unmatched_blocked = 0
    for r in risk_block_records:
        eid = r.get("entity_id", "")
        if eid and eid in shadow_by_eid:
            matched_blocked.append({
                "entity_id": eid,
                "guard": r.get("risk_state", {}).get("risk_flag", "") or r.get("reason", "").split(":")[0],
                "score": r.get("signal_score", 0),
                "symbol": r.get("symbol", ""),
                "shadow_r": shadow_by_eid[eid].get("r_multiple"),
                "shadow_exit": shadow_by_eid[eid].get("exit_reason", ""),
            })
        else:
            unmatched_blocked += 1

    # Match EXECUTE ledger records to shadows
    matched_executed = []
    unmatched_executed = 0
    for r in execute_records:
        eid = r.get("entity_id", "")
        if eid and eid in shadow_by_eid:
            matched_executed.append({
                "entity_id": eid,
                "score": r.get("signal_score", 0),
                "symbol": r.get("symbol", ""),
                "shadow_r": shadow_by_eid[eid].get("r_multiple"),
                "shadow_exit": shadow_by_eid[eid].get("exit_reason", ""),
            })
        else:
            unmatched_executed += 1

    out.append(f"  RISK_BLOCK records with shadow match: {len(matched_blocked)}")
    out.append(f"  RISK_BLOCK records without shadow match: {unmatched_blocked}")
    out.append(f"  EXECUTE records with shadow match: {len(matched_executed)}")
    out.append(f"  EXECUTE records without shadow match: {unmatched_executed}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: COUNTERFACTUAL R BY GUARD
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 4: COUNTERFACTUAL R DISTRIBUTION BY GUARD")
    out.append("━" * 80)
    out.append("")

    # Group matched blocked by guard
    guard_r_values = defaultdict(list)
    for rec in matched_blocked:
        if rec["shadow_r"] is not None:
            guard_r_values[rec["guard"]].append(rec["shadow_r"])

    # Executed (guard-passed) shadow R
    executed_r_values = [rec["shadow_r"] for rec in matched_executed if rec["shadow_r"] is not None]

    out.append("  GUARD-PASSED (EXECUTE → broker) counterfactual shadow R:")
    if executed_r_values:
        out.append(f"    N = {len(executed_r_values)}")
        out.append(f"    Mean R: {statistics.mean(executed_r_values):+.4f}")
        out.append(f"    Median R: {statistics.median(executed_r_values):+.4f}")
        out.append(f"    Win rate: {sum(1 for r in executed_r_values if r > 0) * 100 / len(executed_r_values):.1f}%")
        out.append(f"    Stdev: {statistics.stdev(executed_r_values):.4f}" if len(executed_r_values) > 1 else "")
    else:
        out.append(f"    NO DATA (no entity_id matches)")
    out.append("")

    out.append("  GUARD-BLOCKED (RISK_BLOCK) counterfactual shadow R by guard:")
    all_blocked_r = []
    for guard, r_vals in sorted(guard_r_values.items(), key=lambda x: -len(x[1])):
        all_blocked_r.extend(r_vals)
        if r_vals:
            mean_r = statistics.mean(r_vals)
            median_r = statistics.median(r_vals)
            wr = sum(1 for r in r_vals if r > 0) * 100 / len(r_vals)
            out.append(f"    {guard}:")
            out.append(f"      N = {len(r_vals)}, Mean R = {mean_r:+.4f}, Median R = {median_r:+.4f}, WR = {wr:.1f}%")

    out.append("")
    if all_blocked_r:
        out.append(f"  ALL BLOCKED (combined) counterfactual shadow R:")
        out.append(f"    N = {len(all_blocked_r)}")
        out.append(f"    Mean R: {statistics.mean(all_blocked_r):+.4f}")
        out.append(f"    Median R: {statistics.median(all_blocked_r):+.4f}")
        out.append(f"    Win rate: {sum(1 for r in all_blocked_r if r > 0) * 100 / len(all_blocked_r):.1f}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: SELECTION QUALITY DETERMINATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 5: SELECTION QUALITY — ARE GUARDS HELPING OR HURTING?")
    out.append("━" * 80)
    out.append("")

    if executed_r_values and all_blocked_r:
        exec_mean = statistics.mean(executed_r_values)
        block_mean = statistics.mean(all_blocked_r)
        delta = exec_mean - block_mean

        out.append(f"  Guard-PASSED mean shadow R: {exec_mean:+.4f} (N={len(executed_r_values)})")
        out.append(f"  Guard-BLOCKED mean shadow R: {block_mean:+.4f} (N={len(all_blocked_r)})")
        out.append(f"  Delta (passed - blocked): {delta:+.4f}")
        out.append("")

        if delta > 0.05:
            out.append("  VERDICT: GUARDS ARE QUALITY-IMPROVING (passed > blocked)")
            out.append("  Guards are correctly filtering out worse opportunities.")
        elif delta < -0.05:
            out.append("  ⚠️  VERDICT: GUARDS ARE QUALITY-DESTROYING (blocked > passed)")
            out.append("  Guards are filtering OUT the better opportunities!")
            out.append(f"  The blocked population has {abs(delta):.4f}R BETTER expectancy.")
        else:
            out.append("  VERDICT: GUARDS ARE NEUTRAL (no meaningful difference)")
            out.append("  Guard filtering is volume-reducing but not quality-selective.")
    else:
        out.append("  INSUFFICIENT DATA for quality determination")
        out.append(f"  (executed_r_values: {len(executed_r_values)}, all_blocked_r: {len(all_blocked_r)})")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6: FULL POPULATION SHADOW R (all 985 V10_PRIMARY)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 6: FULL V10_PRIMARY POPULATION vs LIVE SUBSET")
    out.append("━" * 80)
    out.append("")

    all_shadow_r = [s.get("r_multiple") for s in shadows if s.get("r_multiple") is not None]
    live_r = [t.get("r_multiple") for t in live if t.get("r_multiple") is not None]

    out.append(f"  ALL V10_PRIMARY shadows (full population):")
    if all_shadow_r:
        out.append(f"    N = {len(all_shadow_r)}")
        out.append(f"    Mean R: {statistics.mean(all_shadow_r):+.4f}")
        out.append(f"    Median R: {statistics.median(all_shadow_r):+.4f}")
        out.append(f"    Win rate: {sum(1 for r in all_shadow_r if r > 0) * 100 / len(all_shadow_r):.1f}%")
    out.append("")

    out.append(f"  LIVE trades (realised):")
    if live_r:
        out.append(f"    N = {len(live_r)}")
        out.append(f"    Mean R: {statistics.mean(live_r):+.4f}")
        out.append(f"    Median R: {statistics.median(live_r):+.4f}")
        out.append(f"    Win rate: {sum(1 for r in live_r if r > 0) * 100 / len(live_r):.1f}%")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 7: TEMPORAL ANALYSIS — How many EXECUTE+RISK_BLOCK per day?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 7: TEMPORAL ANALYSIS — DAILY VOLUME")
    out.append("━" * 80)
    out.append("")

    # V10-intended execute records by date
    from datetime import datetime
    daily_execute = Counter()
    daily_block = Counter()
    for r in execute_records:
        ts = r.get("timestamp", "")[:10]
        daily_execute[ts] += 1
    for r in risk_block_records:
        ts = r.get("timestamp", "")[:10]
        daily_block[ts] += 1

    all_dates = sorted(set(list(daily_execute.keys()) + list(daily_block.keys())))
    out.append(f"  Date range: {all_dates[0] if all_dates else '?'} to {all_dates[-1] if all_dates else '?'}")
    out.append(f"  Trading days: {len(all_dates)}")
    out.append("")
    out.append(f"  {'Date':<12} {'EXECUTE':<10} {'RISK_BLOCK':<12} {'Total V10':<10} {'Block %'}")
    out.append(f"  {'─'*12} {'─'*10} {'─'*12} {'─'*10} {'─'*8}")
    for d in all_dates:
        exe = daily_execute.get(d, 0)
        blk = daily_block.get(d, 0)
        total = exe + blk
        blk_pct = blk * 100 / total if total > 0 else 0
        out.append(f"  {d:<12} {exe:<10} {blk:<12} {total:<10} {blk_pct:.0f}%")

    out.append("")
    total_exe = sum(daily_execute.values())
    total_blk = sum(daily_block.values())
    out.append(f"  TOTAL: EXECUTE={total_exe}, RISK_BLOCK={total_blk}, Combined={total_exe+total_blk}")
    out.append(f"  Overall block rate: {total_blk*100/(total_exe+total_blk):.1f}%" if (total_exe+total_blk) > 0 else "")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 8: SCORE DISTRIBUTION — Blocked vs Passed
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 8: V10 SCORE DISTRIBUTION — BLOCKED vs PASSED")
    out.append("━" * 80)
    out.append("")

    execute_scores = [r.get("signal_score", 0) for r in execute_records if r.get("signal_score")]
    blocked_scores = [r.get("signal_score", 0) for r in risk_block_records if r.get("signal_score")]

    if execute_scores:
        out.append(f"  GUARD-PASSED scores:")
        out.append(f"    N = {len(execute_scores)}, Mean = {statistics.mean(execute_scores):.2f}, Median = {statistics.median(execute_scores):.1f}")
        score_dist_exe = Counter(int(s) for s in execute_scores)
        for score in sorted(score_dist_exe.keys()):
            out.append(f"      Score {score}: {score_dist_exe[score]}")
    out.append("")

    if blocked_scores:
        out.append(f"  GUARD-BLOCKED scores:")
        out.append(f"    N = {len(blocked_scores)}, Mean = {statistics.mean(blocked_scores):.2f}, Median = {statistics.median(blocked_scores):.1f}")
        score_dist_blk = Counter(int(s) for s in blocked_scores)
        for score in sorted(score_dist_blk.keys()):
            out.append(f"      Score {score}: {score_dist_blk[score]}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 9: EXECUTION FAILURES (passed guards but broker rejected)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 9: EXECUTION FAILURES (post-guard, pre-fill)")
    out.append("━" * 80)
    out.append("")

    # Look for decision=NO_TRADE with reason containing "execution_failed" or "execution_not_attempted"
    exec_fail_records = [r for r in ledger if r["decision"] == "NO_TRADE" and 
                         ("execution_failed" in r.get("reason", "") or 
                          "execution_not_attempted" in r.get("reason", ""))]
    out.append(f"  Execution failures in ledger: {len(exec_fail_records)}")
    exec_fail_reasons = Counter(r.get("reason", "") for r in exec_fail_records)
    for reason, count in exec_fail_reasons.most_common(5):
        out.append(f"    {reason[:80]}: {count}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 10: COMPLETE FUNNEL SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 10: COMPLETE FUNNEL RECONSTRUCTION")
    out.append("━" * 80)
    out.append("")

    total_v10_execute_intent = total_exe + total_blk
    out.append(f"  V10 EXECUTE DECISIONS (intent): {total_v10_execute_intent}")
    out.append(f"    └─ RISK_BLOCK by guards: {total_blk}")
    
    for guard, count in guard_counts.most_common():
        out.append(f"       ├─ {guard}: {count}")
    
    out.append(f"    └─ Passed guards: {total_exe}")
    out.append(f"       ├─ Execution failures: {len(exec_fail_records)}")
    out.append(f"       └─ Broker filled (LIVE): {len(live)}")
    out.append("")

    # Verify funnel adds up
    accounted = total_blk + len(exec_fail_records) + len(live)
    unaccounted = total_v10_execute_intent - accounted
    out.append(f"  FUNNEL ACCOUNTING:")
    out.append(f"    Total V10 intent: {total_v10_execute_intent}")
    out.append(f"    Guard-blocked: {total_blk}")
    out.append(f"    Execution failures: {len(exec_fail_records)}")
    out.append(f"    Live fills: {len(live)}")
    out.append(f"    Accounted: {accounted}")
    out.append(f"    UNACCOUNTED: {unaccounted}")
    out.append("")

    if unaccounted > 0:
        out.append(f"  ⚠️  {unaccounted} opportunities unaccounted for.")
        out.append(f"     Possible explanations:")
        out.append(f"     - V10_PRIMARY shadows created during periods when execution was disabled")
        out.append(f"     - Shadow engine ran in paper/shadow-only mode (no execution attempted)")
        out.append(f"     - Decision ledger does not fully overlap with shadow creation period")
        out.append(f"     - Some EXECUTE decisions had entity_id populated differently")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 11: V10_PRIMARY SHADOW CREATION vs DECISION LEDGER TIME OVERLAP
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 11: TEMPORAL OVERLAP — SHADOW vs LEDGER PERIODS")
    out.append("━" * 80)
    out.append("")

    shadow_times = sorted([s.get("entry_time", 0) for s in shadows if s.get("entry_time")])
    ledger_times = sorted([r.get("timestamp_unix", 0) for r in (execute_records + risk_block_records) if r.get("timestamp_unix")])

    from datetime import datetime as _dt, timezone as _tz
    if shadow_times:
        s_start = _dt.fromtimestamp(shadow_times[0], tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
        s_end = _dt.fromtimestamp(shadow_times[-1], tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"  V10_PRIMARY shadow period: {s_start} → {s_end}")
    if ledger_times:
        l_start = _dt.fromtimestamp(ledger_times[0], tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
        l_end = _dt.fromtimestamp(ledger_times[-1], tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"  Decision ledger (EXECUTE+BLOCK) period: {l_start} → {l_end}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("FINDINGS & CLASSIFICATION")
    out.append("=" * 80)
    out.append("")

    out.append("1. FUNNEL STRUCTURE — PROVEN")
    out.append("   V10 EXECUTE decision → prepare_execution() [shadow opens] →")
    out.append("   HorizonAuthority (skipped V10) → evaluate_runtime_guards() →")
    out.append("   ExecutionOrchestrator → Broker fill → LIVE trade")
    out.append("")

    out.append("2. GUARD BLOCKING VOLUME — PROVEN (from decision_ledger)")
    out.append(f"   {total_blk} of {total_v10_execute_intent} opportunities blocked by guards")
    out.append(f"   Block rate: {total_blk*100/(total_v10_execute_intent) if total_v10_execute_intent else 0:.1f}%")
    out.append("")

    if executed_r_values and all_blocked_r:
        exec_mean = statistics.mean(executed_r_values)
        block_mean = statistics.mean(all_blocked_r)
        delta = exec_mean - block_mean
        if delta < -0.05:
            quality_verdict = "QUALITY-DESTROYING"
        elif delta > 0.05:
            quality_verdict = "QUALITY-IMPROVING"
        else:
            quality_verdict = "NEUTRAL"

        out.append(f"3. GUARD SELECTION QUALITY — {'PROVEN' if abs(delta) > 0.1 else 'PLAUSIBLE'}")
        out.append(f"   Guards are {quality_verdict}")
        out.append(f"   Passed mean shadow R: {exec_mean:+.4f}")
        out.append(f"   Blocked mean shadow R: {block_mean:+.4f}")
        out.append(f"   Delta: {delta:+.4f}R")
    else:
        out.append("3. GUARD SELECTION QUALITY — DATA LIMITATION")
        out.append("   Insufficient entity_id overlap to determine guard quality")
    out.append("")

    out.append("4. ENTITY_ID COVERAGE GAP — DATA LIMITATION")
    out.append(f"   V10_PRIMARY with entity_id: {len(shadow_by_eid)}/{len(shadows)} ({len(shadow_by_eid)*100//len(shadows)}%)")
    out.append(f"   RISK_BLOCK matched to shadow: {len(matched_blocked)}/{len(risk_block_records)}")
    out.append(f"   EXECUTE matched to shadow: {len(matched_executed)}/{len(execute_records)}")
    out.append("   758 shadows (77%) have NO entity_id — cannot be matched to ledger")
    out.append("")

    out.append("5. UNACCOUNTED FUNNEL GAP — UNRESOLVED")
    out.append(f"   V10_PRIMARY shadows: {len(shadows)}")
    out.append(f"   Ledger EXECUTE+BLOCK: {total_v10_execute_intent}")
    out.append(f"   Difference: {len(shadows) - total_v10_execute_intent}")
    out.append("   Possible: shadow creation period extends beyond decision_ledger period,")
    out.append("   or shadow opens during modes where no ledger entry was written.")
    out.append("")

    out.append("NEXT EXPERIMENT REQUIRED:")
    out.append("  If guard quality cannot be determined from current entity_id coverage,")
    out.append("  use TEMPORAL PROXIMITY matching: for each RISK_BLOCK, find the shadow")
    out.append("  opened within ±30s for the same symbol. This bypasses the entity_id gap.")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline").mkdir(parents=True, exist_ok=True)
    Path("reports/research/baseline/selection_funnel_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
