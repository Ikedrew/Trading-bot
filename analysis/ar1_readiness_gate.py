"""AR1 Research Readiness Gate — Check whether sufficient data exists."""
import json
from pathlib import Path
from collections import Counter

base = Path("logs/v3_shadow")

layers = ["market_understanding", "market_context", "opportunity_assessment",
          "horizon_assessment", "entry_assessment", "risk_assessment", "execution_assessment"]

layer_counts = {}
for layer in layers:
    d = base / layer
    count = 0
    if d.exists():
        for f in d.rglob("*.jsonl"):
            with open(f) as fh:
                count += sum(1 for line in fh if line.strip())
    layer_counts[layer] = count

# Execution details
exec_dir = base / "execution_assessment"
exec_states = Counter()
horizons = Counter()
opp_states = Counter()
entry_states = Counter()
risk_states = Counter()
linked_count = 0
ready_linked = 0
total_exec = 0

if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    total_exec += 1
                    exec_states[r.get("execution_state", "")] += 1
                    horizons[r.get("horizon", "")] += 1
                    opp_states[r.get("opportunity_state", "")] += 1
                    entry_states[r.get("entry_state", "")] += 1
                    risk_states[r.get("risk_state", "")] += 1
                    if r.get("_outcome_linked"):
                        linked_count += 1
                        if r.get("execution_state") == "READY_FOR_EXECUTION":
                            ready_linked += 1
                except:
                    pass

# Criteria checks
all_layers_populated = all(v > 0 for v in layer_counts.values())
non_low_opp = sum(v for k, v in opp_states.items() if k not in ("LOW_QUALITY_CONTEXT", "INSUFFICIENT_CONTEXT", ""))
mtf_available = non_low_opp > 10
has_scalp = horizons.get("SCALP", 0) > 0
has_intraday = horizons.get("INTRADAY", 0) > 0
horizon_diverse = has_scalp and has_intraday
valid_entries = entry_states.get("VALID_ENTRY_CONFIRMATION", 0)
weak_entries = entry_states.get("WEAK_ENTRY_CONFIRMATION", 0)
entry_present = (valid_entries + weak_entries) > 0
risk_exec_linked = linked_count > 0

# Report
print("=" * 60)
print("AR1 — RESEARCH READINESS GATE")
print("=" * 60)

criteria = [
    (">=50 linked V3 outcomes", linked_count >= 50, f"{linked_count}/50"),
    (">=10 READY_FOR_EXECUTION linked", ready_linked >= 10, f"{ready_linked}/10"),
    ("All 7 layers populated", all_layers_populated, f"{sum(1 for v in layer_counts.values() if v > 0)}/7"),
    ("MTF context available (non-LOW opp >10)", mtf_available, f"{non_low_opp} non-LOW"),
    ("Horizon diversity (SCALP + INTRADAY)", horizon_diverse, f"SCALP={horizons.get('SCALP',0)} INTRADAY={horizons.get('INTRADAY',0)}"),
    ("Entry behaviour observations present", entry_present, f"valid={valid_entries} weak={weak_entries}"),
    ("Risk + Execution linked to outcomes", risk_exec_linked, f"{linked_count} linked"),
]

all_pass = True
print()
for label, passed, detail in criteria:
    status = "PASS" if passed else "FAIL"
    marker = "[+]" if passed else "[X]"
    if not passed:
        all_pass = False
    print(f"  {marker} {label:50s} | {detail}")

print()
print("-" * 60)

if all_pass:
    print("  AR1 STATUS: READY")
    print()
    print("  All readiness criteria satisfied.")
    print("  Proceed with Incremental Predictive Value Analysis.")
else:
    print("  AR1 STATUS: NOT READY")
    print()
    print("  Failed criteria:")
    for label, passed, detail in criteria:
        if not passed:
            print(f"    - {label}: {detail}")
    print()
    print("  Recommendation:")
    if linked_count < 50:
        print(f"    Continue V3 shadow collection. Need {50 - linked_count} more linked outcomes.")
    if ready_linked < 10:
        print(f"    Need {10 - ready_linked} more READY_FOR_EXECUTION events with outcomes.")
        total_ready = exec_states.get("READY_FOR_EXECUTION", 0)
        if total_ready > 0:
            print(f"    ({total_ready} READY events exist but {total_ready - ready_linked} not yet linked)")
        else:
            print("    Ensure bot runs with MTF_ENABLED during active sessions.")
    if not mtf_available:
        print("    Enable MTF_ENABLED=True in config for HTF context.")
    if not entry_present:
        print("    Entry triggers not firing — check BOS/rejection detection upstream.")

print()
print("  Data summary:")
print(f"    Total execution assessments: {total_exec}")
print(f"    Linked outcomes: {linked_count}")
print(f"    READY events (total): {exec_states.get('READY_FOR_EXECUTION', 0)}")
print(f"    READY events (linked): {ready_linked}")
print()
print("  Layer volumes:")
for layer, count in layer_counts.items():
    print(f"    {layer:30s}: {count}")
print()
print("  Execution state distribution:")
for k, v in exec_states.most_common():
    print(f"    {k:30s}: {v}")
print()
print("  Horizon distribution:")
for k, v in horizons.most_common():
    print(f"    {k:15s}: {v}")
print()
