"""
V3 Shadow Outcome Linker — Full Dataset Refresh Audit.

Re-processes ALL execution assessments against the complete shadow trade history.
Reports before/after linkage rates and AR1 readiness status.
"""

import json
from pathlib import Path
from collections import Counter

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: LOCATE ALL DATA
# ═══════════════════════════════════════════════════════════════════════════════

base = Path("logs/v3_shadow")
exec_dir = base / "execution_assessment"
shadow_dir = Path("logs/shadow_trades")

# Count BEFORE state
before_linked = 0
before_unlinked = 0
exec_total = 0
ready_total = 0
ready_before_linked = 0

all_exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    all_exec_records.append(r)
                    exec_total += 1
                    if r.get("execution_state") == "READY_FOR_EXECUTION":
                        ready_total += 1
                    if r.get("_outcome_linked"):
                        before_linked += 1
                        if r.get("execution_state") == "READY_FOR_EXECUTION":
                            ready_before_linked += 1
                    else:
                        before_unlinked += 1
                except:
                    pass

# Shadow trades
shadow_count = 0
shadow_symbols = set()
if shadow_dir.exists():
    for d in shadow_dir.iterdir():
        if d.is_dir() and d.name != "UNKNOWN":
            shadow_symbols.add(d.name)
            for f in d.glob("*.jsonl"):
                with open(f) as fh:
                    shadow_count += sum(1 for line in fh if line.strip())

print("=" * 70)
print("V3 SHADOW OUTCOME LINKER — FULL DATASET REFRESH")
print("=" * 70)

print(f"\nSTEP 1: DATASET SCANNED")
print(f"  Total V3 execution assessments: {exec_total}")
print(f"  Total READY_FOR_EXECUTION: {ready_total}")
print(f"  Total shadow trades available: {shadow_count}")
print(f"  Shadow trade symbols: {sorted(shadow_symbols)}")

print(f"\n  BEFORE REFRESH:")
print(f"    Linked outcomes: {before_linked}")
print(f"    Unlinked: {before_unlinked}")
print(f"    Linkage rate: {before_linked/exec_total*100:.1f}%" if exec_total else "    N/A")
print(f"    READY linked: {ready_before_linked}/{ready_total}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: RUN FULL REFRESH
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\nSTEP 2: RUNNING LINKER...")

from core.v3_shadow.outcome_linker import link_v3_shadow_outcomes

report = link_v3_shadow_outcomes(persist=True)

print(f"  Linker complete.")
print(f"  Matched: {report.matched}")
print(f"  Unmatched: {report.unmatched}")
print(f"  Match rate: {report.match_rate:.1%}")
print(f"  By correlation: {report.match_by_correlation}")
print(f"  By timestamp: {report.match_by_timestamp}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: VALIDATE LINKAGE KEYS
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\nSTEP 3: LINKAGE KEY ANALYSIS")

# Examine match methods
match_methods = Counter()
for r in report.linked_records:
    outcome = r.get("_outcome", {})
    if outcome.get("result_r") is not None:
        match_methods[outcome.get("match_method", "unknown")] += 1

print(f"  Match methods used: {dict(match_methods)}")

# Check for missing identifiers
missing_symbol = sum(1 for r in report.linked_records if not r.get("symbol"))
missing_ts = sum(1 for r in report.linked_records if not r.get("timestamp_utc"))
print(f"  Missing symbol: {missing_symbol}")
print(f"  Missing timestamp: {missing_ts}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: AFTER REFRESH ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\nSTEP 4: AFTER REFRESH RESULTS")

# Reload and count
after_linked = 0
after_ready_linked = 0
exec_states_linked = Counter()
horizons_linked = Counter()
opp_states_linked = Counter()
entry_states_linked = Counter()
risk_states_linked = Counter()
outcomes_by_state = {}

for r in report.linked_records:
    outcome = r.get("_outcome", {})
    is_linked = outcome.get("result_r") is not None
    exec_state = r.get("execution_state", "")
    
    if is_linked:
        after_linked += 1
        exec_states_linked[exec_state] += 1
        horizons_linked[r.get("horizon", "")] += 1
        opp_states_linked[r.get("opportunity_state", "")] += 1
        entry_states_linked[r.get("entry_state", "")] += 1
        risk_states_linked[r.get("risk_state", "")] += 1
        
        if exec_state == "READY_FOR_EXECUTION":
            after_ready_linked += 1
        
        if exec_state not in outcomes_by_state:
            outcomes_by_state[exec_state] = []
        outcomes_by_state[exec_state].append(outcome["result_r"])

after_unlinked = len(report.linked_records) - after_linked

print(f"  AFTER REFRESH:")
print(f"    Total records: {len(report.linked_records)}")
print(f"    Linked outcomes: {after_linked}")
print(f"    Unlinked: {after_unlinked}")
print(f"    Linkage rate: {after_linked/len(report.linked_records)*100:.1f}%" if report.linked_records else "N/A")
print(f"    READY linked: {after_ready_linked}/{ready_total}")

print(f"\n  Linked by execution state:")
for k, v in exec_states_linked.most_common():
    outcomes = outcomes_by_state.get(k, [])
    if outcomes:
        wr = sum(1 for o in outcomes if o > 0) / len(outcomes)
        ev = sum(outcomes) / len(outcomes)
        print(f"    {k:30s}: {v:4d} linked | WR={wr:.1%} | EV={ev:+.4f}R")
    else:
        print(f"    {k:30s}: {v:4d} linked")

print(f"\n  Linked by horizon:")
for k, v in horizons_linked.most_common():
    print(f"    {k:15s}: {v}")

print(f"\n  Linked by opportunity state:")
for k, v in opp_states_linked.most_common():
    print(f"    {k:30s}: {v}")

print(f"\n  Linked by entry state:")
for k, v in entry_states_linked.most_common():
    print(f"    {k:30s}: {v}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: BEFORE/AFTER COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*70}")
print("STEP 5: BEFORE/AFTER COMPARISON")
print(f"{'='*70}")

print(f"\n  {'Metric':<35s} | {'Before':>8s} | {'After':>8s} | {'Change':>8s}")
print(f"  {'-'*35}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
print(f"  {'Total assessments':<35s} | {exec_total:>8d} | {len(report.linked_records):>8d} | {'—':>8s}")
print(f"  {'Linked outcomes':<35s} | {before_linked:>8d} | {after_linked:>8d} | {after_linked-before_linked:>+8d}")
print(f"  {'Unlinked':<35s} | {before_unlinked:>8d} | {after_unlinked:>8d} | {after_unlinked-before_unlinked:>+8d}")
print(f"  {'Linkage rate':<35s} | {before_linked/exec_total*100 if exec_total else 0:>7.1f}% | {after_linked/len(report.linked_records)*100 if report.linked_records else 0:>7.1f}% | {'—':>8s}")
print(f"  {'READY linked':<35s} | {ready_before_linked:>8d} | {after_ready_linked:>8d} | {after_ready_linked-ready_before_linked:>+8d}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: AR1 READINESS CHECK
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*70}")
print("STEP 6: AR1 READINESS")
print(f"{'='*70}")

print(f"\n  Linked outcomes: {after_linked} / 50 required")
print(f"  READY linked: {after_ready_linked} / 10 required")

ar1_ready = after_linked >= 50 and after_ready_linked >= 10

if ar1_ready:
    print(f"\n  AR1 CRITERIA: MET")
else:
    print(f"\n  AR1 CRITERIA: NOT MET")
    if after_linked < 50:
        print(f"    Need {50 - after_linked} more linked outcomes")
    if after_ready_linked < 10:
        print(f"    Need {10 - after_ready_linked} more READY linked outcomes")
        if ready_total > after_ready_linked:
            unlinked_ready = ready_total - after_ready_linked
            print(f"    ({unlinked_ready} READY events exist without linked outcomes)")
            print(f"    Likely cause: shadow trades at those timestamps not yet closed or not matched")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7: FINAL VERDICT
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*70}")
print("FINAL VERDICT")
print(f"{'='*70}")

if ar1_ready:
    print("\n  READY_FOR_AR1_RESEARCH")
elif after_ready_linked > 0 and after_linked >= 30:
    print("\n  CONTINUE_COLLECTION")
    print("  Close to threshold. Continue running + periodic re-linking.")
elif report.match_rate < 0.5 and exec_total > 100:
    print("\n  LINKER_REPAIR_REQUIRED")
    print("  Match rate too low despite available data.")
else:
    print("\n  CONTINUE_COLLECTION")
    print("  Pipeline operational. Need more data + time for outcomes to resolve.")
print()
