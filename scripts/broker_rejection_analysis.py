"""
BROKER REJECTION ANALYSIS

Investigates why 54 guard-passed opportunities were broker-rejected with +0.39R
counterfactual expectancy. 

Root cause hypothesis: The SPREAD GUARD inside mt5_execution.py blocks when
spread/risk_distance > 0.30 or spread > absolute cap.

This script:
1. Loads execution_results to find ALL rejected vs accepted orders
2. Matches rejections to V10_PRIMARY shadows via correlation_id
3. Analyzes correlations: spread, risk_distance, symbol, session, time, regime
4. Determines whether tight-stop setups are being systematically filtered

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, ".")


def load_execution_results():
    """Load all execution result records."""
    records = []
    base = Path("logs/execution_results")
    for sym_dir in base.iterdir():
        if not sym_dir.is_dir():
            continue
        for f in sym_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def load_decision_ledger():
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
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def load_shadow_primary():
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def main():
    out = []
    out.append("=" * 80)
    out.append("BROKER REJECTION DEEP ANALYSIS")
    out.append("=" * 80)
    out.append("")

    # Load all data
    exec_results = load_execution_results()
    shadows = load_shadow_primary()
    ledger = load_decision_ledger()

    out.append(f"Execution results loaded: {len(exec_results)}")
    out.append(f"V10_PRIMARY shadows: {len(shadows)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: CLASSIFY EXECUTION RESULTS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 1: EXECUTION RESULTS CLASSIFICATION")
    out.append("━" * 80)
    out.append("")

    # Separate by result
    successful = [r for r in exec_results if r.get("result_ok")]
    failed = [r for r in exec_results if not r.get("result_ok")]

    out.append(f"  Total execution attempts: {len(exec_results)}")
    out.append(f"  Successful (result_ok=True): {len(successful)}")
    out.append(f"  Failed (result_ok=False): {len(failed)}")
    out.append("")

    # Classify failures by comment/reason
    fail_reasons = Counter()
    for r in failed:
        comment = r.get("comment", "")
        fail_reasons[comment] += 1

    out.append("  Failure reasons:")
    for reason, count in fail_reasons.most_common():
        out.append(f"    {reason}: {count}")
    out.append("")

    # Identify spread-guard blocked specifically
    spread_blocked = [r for r in failed if "SPREAD" in r.get("comment", "").upper()]
    other_blocked = [r for r in failed if "SPREAD" not in r.get("comment", "").upper()]

    out.append(f"  Spread-guard blocked: {len(spread_blocked)}")
    out.append(f"  Other failures: {len(other_blocked)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: EXAMINE EXECUTION RESULT FIELDS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 2: EXECUTION RESULT FIELD INSPECTION")
    out.append("━" * 80)
    out.append("")

    if exec_results:
        sample = exec_results[0]
        out.append(f"  Available fields: {sorted(sample.keys())}")
        out.append("")

    # Show samples of rejected
    if failed:
        out.append("  Sample REJECTED execution results:")
        for r in failed[:3]:
            out.append(f"    symbol={r.get('symbol')}, comment={r.get('comment')}, "
                       f"retcode={r.get('retcode')}, side={r.get('side')}, "
                       f"sl={r.get('sl')}, tp={r.get('tp')}, volume={r.get('volume')}, "
                       f"correlation_id={r.get('correlation_id', '')[:30]}, "
                       f"entity_id={r.get('entity_id', '')}")
            out.append("")

    # Show samples of accepted
    if successful:
        out.append("  Sample ACCEPTED execution results:")
        for r in successful[:3]:
            out.append(f"    symbol={r.get('symbol')}, comment={r.get('comment')}, "
                       f"retcode={r.get('retcode')}, side={r.get('side')}, "
                       f"sl={r.get('sl')}, tp={r.get('tp')}, volume={r.get('volume')}, "
                       f"fill_price={r.get('fill_price')}, slippage={r.get('slippage')}, "
                       f"correlation_id={r.get('correlation_id', '')[:30]}")
            out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: MATCH REJECTIONS TO SHADOWS VIA CORRELATION_ID
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 3: MATCH REJECTIONS TO V10_PRIMARY SHADOWS")
    out.append("━" * 80)
    out.append("")

    # Build shadow lookup by correlation_id
    shadow_by_corr = {}
    for s in shadows:
        cid = s.get("correlation_id", "")
        if cid:
            shadow_by_corr[cid] = s

    # Match failed executions to shadows
    reject_matched = []
    reject_unmatched = 0
    for r in failed:
        cid = r.get("correlation_id", "")
        if cid and cid in shadow_by_corr:
            s = shadow_by_corr[cid]
            reject_matched.append({
                "exec": r,
                "shadow": s,
            })
        else:
            reject_unmatched += 1

    # Match successful executions to shadows
    accept_matched = []
    accept_unmatched = 0
    for r in successful:
        cid = r.get("correlation_id", "")
        if cid and cid in shadow_by_corr:
            s = shadow_by_corr[cid]
            accept_matched.append({
                "exec": r,
                "shadow": s,
            })
        else:
            accept_unmatched += 1

    out.append(f"  Rejected matched to shadow: {len(reject_matched)}")
    out.append(f"  Rejected unmatched: {reject_unmatched}")
    out.append(f"  Accepted matched to shadow: {len(accept_matched)}")
    out.append(f"  Accepted unmatched: {accept_unmatched}")
    out.append("")

    # Counterfactual R
    reject_r = [m["shadow"].get("r_multiple") for m in reject_matched if m["shadow"].get("r_multiple") is not None]
    accept_r = [m["shadow"].get("r_multiple") for m in accept_matched if m["shadow"].get("r_multiple") is not None]

    out.append(f"  REJECTED counterfactual R: N={len(reject_r)}, "
               f"Mean={statistics.mean(reject_r):+.4f}, "
               f"Median={statistics.median(reject_r):+.4f}, "
               f"WR={sum(1 for r in reject_r if r > 0)*100/len(reject_r):.1f}%" if reject_r else "  REJECTED: no shadow R data")
    out.append(f"  ACCEPTED counterfactual R: N={len(accept_r)}, "
               f"Mean={statistics.mean(accept_r):+.4f}, "
               f"Median={statistics.median(accept_r):+.4f}, "
               f"WR={sum(1 for r in accept_r if r > 0)*100/len(accept_r):.1f}%" if accept_r else "  ACCEPTED: no shadow R data")
    out.append("")

    if reject_r and accept_r:
        delta = statistics.mean(reject_r) - statistics.mean(accept_r)
        out.append(f"  Δ (rejected - accepted): {delta:+.4f}R")
        if delta > 0.1:
            out.append(f"  ⚠️ BROKER REJECTION IS QUALITY-DESTROYING: rejected has {delta:+.4f}R better expectancy")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: RISK DISTANCE ANALYSIS (THE KEY MECHANISM)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 4: RISK DISTANCE / SPREAD RATIO ANALYSIS")
    out.append("━" * 80)
    out.append("")

    # The spread guard blocks when: spread / risk_distance > 0.30
    # Risk distance = |entry_reference - sl|
    # Smaller risk_distance = tighter stop = MORE LIKELY TO BE BLOCKED
    # Hypothesis: tight-stop setups have BETTER R (higher RR) but fail the spread guard

    reject_risk_dist = []
    accept_risk_dist = []

    for m in reject_matched:
        r = m["exec"]
        sl = r.get("sl", 0)
        entry = r.get("entry_reference", 0)
        if sl and entry:
            rd = abs(entry - sl)
            if rd > 0:
                reject_risk_dist.append(rd)

    for m in accept_matched:
        r = m["exec"]
        sl = r.get("sl", 0)
        entry = r.get("entry_reference", 0)
        if sl and entry:
            rd = abs(entry - sl)
            if rd > 0:
                accept_risk_dist.append(rd)

    # Also get from all execution results
    all_reject_rd = []
    all_accept_rd = []
    for r in failed:
        sl = r.get("sl", 0)
        entry = r.get("entry_reference", 0)
        if sl and entry:
            rd = abs(entry - sl)
            if rd > 0:
                all_reject_rd.append({"rd": rd, "symbol": r.get("symbol", ""), "comment": r.get("comment", "")})
    for r in successful:
        sl = r.get("sl", 0)
        entry = r.get("entry_reference", 0)
        if sl and entry:
            rd = abs(entry - sl)
            if rd > 0:
                all_accept_rd.append({"rd": rd, "symbol": r.get("symbol", "")})

    if all_reject_rd:
        reject_rds = [x["rd"] for x in all_reject_rd]
        out.append(f"  REJECTED risk_distance (pips equivalent):")
        out.append(f"    N={len(reject_rds)}, Mean={statistics.mean(reject_rds):.6f}, "
                   f"Median={statistics.median(reject_rds):.6f}")
    if all_accept_rd:
        accept_rds = [x["rd"] for x in all_accept_rd]
        out.append(f"  ACCEPTED risk_distance:")
        out.append(f"    N={len(accept_rds)}, Mean={statistics.mean(accept_rds):.6f}, "
                   f"Median={statistics.median(accept_rds):.6f}")
    out.append("")

    if all_reject_rd and all_accept_rd:
        reject_mean_rd = statistics.mean([x["rd"] for x in all_reject_rd])
        accept_mean_rd = statistics.mean([x["rd"] for x in all_accept_rd])
        out.append(f"  Risk distance ratio (rejected/accepted): {reject_mean_rd/accept_mean_rd:.3f}")
        if reject_mean_rd < accept_mean_rd:
            out.append("  ✓ CONFIRMED: Rejected trades have TIGHTER stops (smaller risk_distance)")
            out.append("    This means spread/risk_distance ratio exceeds threshold more easily")
    out.append("")

    # Shadow RR ratio comparison
    reject_rr = [m["shadow"].get("reward_risk_ratio", 0) for m in reject_matched if m["shadow"].get("reward_risk_ratio")]
    accept_rr = [m["shadow"].get("reward_risk_ratio", 0) for m in accept_matched if m["shadow"].get("reward_risk_ratio")]
    if reject_rr:
        out.append(f"  REJECTED shadow RR ratio: Mean={statistics.mean(reject_rr):.3f}, Median={statistics.median(reject_rr):.3f}")
    if accept_rr:
        out.append(f"  ACCEPTED shadow RR ratio: Mean={statistics.mean(accept_rr):.3f}, Median={statistics.median(accept_rr):.3f}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: SYMBOL DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 5: SYMBOL DISTRIBUTION")
    out.append("━" * 80)
    out.append("")

    reject_symbols = Counter(r.get("symbol", "") for r in failed)
    accept_symbols = Counter(r.get("symbol", "") for r in successful)

    out.append(f"  REJECTED by symbol:")
    for sym, count in reject_symbols.most_common():
        total = reject_symbols[sym] + accept_symbols.get(sym, 0)
        rej_rate = count * 100 / total if total > 0 else 0
        out.append(f"    {sym}: {count} rejected / {total} total ({rej_rate:.0f}% rejection rate)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6: TEMPORAL / SESSION ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 6: TEMPORAL / SESSION ANALYSIS")
    out.append("━" * 80)
    out.append("")

    def get_session(ts_unix):
        if not ts_unix:
            return "UNKNOWN"
        hour = _dt.fromtimestamp(ts_unix, tz=_tz.utc).hour
        if 7 <= hour < 12:
            return "LONDON"
        elif 12 <= hour < 17:
            return "NY"
        elif 0 <= hour < 7:
            return "ASIA"
        else:
            return "OFF_SESSION"

    reject_sessions = Counter()
    accept_sessions = Counter()
    for r in failed:
        ts = r.get("timestamp_unix", 0)
        reject_sessions[get_session(ts)] += 1
    for r in successful:
        ts = r.get("timestamp_unix", 0)
        accept_sessions[get_session(ts)] += 1

    out.append("  Session distribution:")
    all_sessions = set(list(reject_sessions.keys()) + list(accept_sessions.keys()))
    for session in sorted(all_sessions):
        rej = reject_sessions.get(session, 0)
        acc = accept_sessions.get(session, 0)
        total = rej + acc
        rej_rate = rej * 100 / total if total > 0 else 0
        out.append(f"    {session}: {rej} rejected / {total} total ({rej_rate:.0f}% rejection rate)")
    out.append("")

    # Hour-by-hour
    reject_hours = Counter()
    accept_hours = Counter()
    for r in failed:
        ts = r.get("timestamp_unix", 0)
        if ts:
            reject_hours[_dt.fromtimestamp(ts, tz=_tz.utc).hour] += 1
    for r in successful:
        ts = r.get("timestamp_unix", 0)
        if ts:
            accept_hours[_dt.fromtimestamp(ts, tz=_tz.utc).hour] += 1

    out.append("  Hour distribution (UTC):")
    for hour in sorted(set(list(reject_hours.keys()) + list(accept_hours.keys()))):
        rej = reject_hours.get(hour, 0)
        acc = accept_hours.get(hour, 0)
        total = rej + acc
        rej_rate = rej * 100 / total if total > 0 else 0
        if total > 0:
            out.append(f"    {hour:02d}:00 — {rej} rejected / {total} total ({rej_rate:.0f}%)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 7: PATTERN ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 7: PATTERN ANALYSIS")
    out.append("━" * 80)
    out.append("")

    reject_patterns = Counter(r.get("pattern", "?") for r in failed)
    accept_patterns = Counter(r.get("pattern", "?") for r in successful)

    out.append("  Pattern distribution (rejected vs accepted):")
    all_patterns = set(list(reject_patterns.keys()) + list(accept_patterns.keys()))
    for pat in sorted(all_patterns, key=lambda p: reject_patterns.get(p, 0), reverse=True):
        rej = reject_patterns.get(pat, 0)
        acc = accept_patterns.get(pat, 0)
        total = rej + acc
        rej_rate = rej * 100 / total if total > 0 else 0
        out.append(f"    {pat}: {rej} rejected / {total} total ({rej_rate:.0f}%)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 8: SHADOW EXIT REASON COMPARISON
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 8: SHADOW EXIT REASON COMPARISON")
    out.append("━" * 80)
    out.append("")

    reject_exits = Counter(m["shadow"].get("exit_reason", "?") for m in reject_matched)
    accept_exits = Counter(m["shadow"].get("exit_reason", "?") for m in accept_matched)

    out.append("  REJECTED shadow exit distribution:")
    for ex, count in reject_exits.most_common():
        out.append(f"    {ex}: {count}")
    out.append("")
    out.append("  ACCEPTED shadow exit distribution:")
    for ex, count in accept_exits.most_common():
        out.append(f"    {ex}: {count}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 9: THE MECHANISM — WHY TIGHT STOPS PRODUCE BETTER R
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 9: MECHANISM ANALYSIS — TIGHT STOPS + HIGH RR")
    out.append("━" * 80)
    out.append("")

    # The spread guard formula: spread / risk_distance > 0.30 blocks
    # A smaller risk_distance (tighter stop) makes the ratio larger
    # Tighter stop = higher RR ratio = MORE profit when TP is hit
    # But also more likely to be spread-blocked

    # Group shadows by risk_distance quartile and check R
    all_matched = reject_matched + accept_matched
    if all_matched:
        sorted_by_rd = sorted(all_matched, key=lambda m: m["shadow"].get("risk_distance", 999))
        n = len(sorted_by_rd)
        q1 = sorted_by_rd[:n//4]
        q2 = sorted_by_rd[n//4:n//2]
        q3 = sorted_by_rd[n//2:3*n//4]
        q4 = sorted_by_rd[3*n//4:]

        out.append("  Shadow R by risk_distance quartile (tightest → widest stop):")
        for label, group in [("Q1 (tightest)", q1), ("Q2", q2), ("Q3", q3), ("Q4 (widest)", q4)]:
            r_vals = [m["shadow"].get("r_multiple") for m in group if m["shadow"].get("r_multiple") is not None]
            rd_vals = [m["shadow"].get("risk_distance", 0) for m in group if m["shadow"].get("risk_distance")]
            rej_count = sum(1 for m in group if not m["exec"].get("result_ok"))
            if r_vals:
                out.append(f"    {label}: N={len(r_vals)}, Mean R={statistics.mean(r_vals):+.4f}, "
                           f"Mean RD={statistics.mean(rd_vals):.5f}, "
                           f"Rejected={rej_count}/{len(group)} ({rej_count*100//len(group)}%)")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 10: DRY_RUN vs LIVE — Check if rejections are DRY_RUN artifacts
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("STEP 10: DRY_RUN vs LIVE EXECUTION MODE")
    out.append("━" * 80)
    out.append("")

    dry_run_results = [r for r in exec_results if r.get("comment", "") == "dry_run"]
    live_results = [r for r in exec_results if r.get("comment", "") != "dry_run"]

    out.append(f"  DRY_RUN results: {len(dry_run_results)}")
    out.append(f"  LIVE results: {len(live_results)}")
    out.append("")

    live_failed = [r for r in live_results if not r.get("result_ok")]
    live_success = [r for r in live_results if r.get("result_ok")]

    out.append(f"  LIVE failed: {len(live_failed)}")
    out.append(f"  LIVE success: {len(live_success)}")
    out.append("")

    if live_failed:
        live_fail_reasons = Counter(r.get("comment", "") for r in live_failed)
        out.append("  LIVE failure reasons:")
        for reason, count in live_fail_reasons.most_common():
            out.append(f"    {reason}: {count}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CONCLUSIONS")
    out.append("=" * 80)
    out.append("")

    # Determine primary rejection mechanism
    spread_block_count = sum(1 for r in failed if "SPREAD" in r.get("comment", "").upper())
    execution_disabled_count = sum(1 for r in failed if "EXECUTION_DISABLED" in r.get("comment", "").upper())
    duplicate_count = sum(1 for r in failed if "DUPLICATE" in r.get("comment", "").upper())
    other_count = len(failed) - spread_block_count - execution_disabled_count - duplicate_count

    out.append(f"  PRIMARY REJECTION MECHANISMS:")
    out.append(f"    SPREAD_GUARD: {spread_block_count}")
    out.append(f"    EXECUTION_DISABLED: {execution_disabled_count}")
    out.append(f"    DUPLICATE_INTENT: {duplicate_count}")
    out.append(f"    OTHER: {other_count}")
    out.append("")

    if reject_r and accept_r:
        out.append(f"  QUALITY IMPACT:")
        out.append(f"    Rejected shadow R: {statistics.mean(reject_r):+.4f}")
        out.append(f"    Accepted shadow R: {statistics.mean(accept_r):+.4f}")
        out.append(f"    Δ = {statistics.mean(reject_r) - statistics.mean(accept_r):+.4f}")
        out.append("")

    out.append("  ROOT CAUSE HYPOTHESIS:")
    if spread_block_count > len(failed) * 0.5:
        out.append("    The SPREAD GUARD in mt5_execution.py is the dominant rejection mechanism.")
        out.append("    It blocks when: spread / risk_distance > MAX_SPREAD_ATR_RATIO (0.30)")
        out.append("    Trades with TIGHTER stops (smaller risk_distance) are more likely blocked.")
        out.append("    Tight-stop trades have HIGHER reward:risk → BETTER counterfactual R.")
        out.append("    The spread guard is therefore systematically filtering HIGH-QUALITY setups.")
    elif execution_disabled_count > len(failed) * 0.5:
        out.append("    EXECUTION_DISABLED config flag is the dominant cause.")
        out.append("    These rejections are from periods when live execution was turned off.")
        out.append("    The shadows still run but trades cannot fill.")
    else:
        out.append("    Mixed rejection causes — see breakdown above.")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/broker_rejection_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
