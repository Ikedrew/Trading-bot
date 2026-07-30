"""
AR1 — Incremental Predictive Value Analysis.

Tests whether each V3 reasoning layer contributes measurable value.
Compares cumulative EV after each pipeline stage.
"""

import json
import math
from pathlib import Path
from collections import Counter

# ═══════════════════════════════════════════════════════════════════════════════
# LOAD ALL V3 SHADOW DATA (linked execution assessments)
# ═══════════════════════════════════════════════════════════════════════════════

base = Path("logs/v3_shadow")

# Load all layers for cross-reference
def load_layer(layer_name):
    d = base / layer_name
    records = []
    if d.exists():
        for f in d.rglob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except:
                            pass
    return records

exec_records = load_layer("execution_assessment")
opp_records = load_layer("opportunity_assessment")
horizon_records = load_layer("horizon_assessment")
entry_records = load_layer("entry_assessment")
risk_records = load_layer("risk_assessment")

# Build index by (symbol, timestamp) for cross-reference
def index_by_key(records):
    idx = {}
    for r in records:
        key = (r.get("symbol", ""), round(r.get("timestamp_utc", 0), 0))
        idx[key] = r
    return idx

opp_idx = index_by_key(opp_records)
horizon_idx = index_by_key(horizon_records)
entry_idx = index_by_key(entry_records)
risk_idx = index_by_key(risk_records)

# Filter to linked outcomes only
linked = [r for r in exec_records if r.get("_outcome_linked") and r.get("_outcome", {}).get("result_r") is not None]

print("=" * 70)
print("AR1 — INCREMENTAL PREDICTIVE VALUE ANALYSIS")
print("=" * 70)
print(f"\nTotal execution assessments: {len(exec_records)}")
print(f"With linked outcomes: {len(linked)}")
print()

if len(linked) < 10:
    print("INSUFFICIENT DATA — need minimum 10 linked outcomes for any analysis.")
    print(f"Currently have: {len(linked)}")
    print("\nAction: Continue collecting V3 shadow data and re-link outcomes.")
    exit()


def compute_stats(subset, label=""):
    outcomes = [r["_outcome"]["result_r"] for r in subset]
    if not outcomes:
        return None
    n = len(outcomes)
    wins = sum(1 for o in outcomes if o > 0)
    wr = wins / n
    ev = sum(outcomes) / n
    std = math.sqrt(sum((o - ev)**2 for o in outcomes) / max(n-1, 1))
    se = std / math.sqrt(n)
    ci_low = ev - 1.96 * se
    ci_high = ev + 1.96 * se
    mfe = [r["_outcome"].get("mfe_r", 0) or 0 for r in subset]
    mae = [r["_outcome"].get("mae_r", 0) or 0 for r in subset]
    avg_mfe = sum(mfe) / len(mfe) if mfe else 0
    avg_mae = sum(mae) / len(mae) if mae else 0
    return {
        "n": n, "wr": wr, "ev": ev, "std": std,
        "ci_low": ci_low, "ci_high": ci_high,
        "mfe": avg_mfe, "mae": avg_mae,
    }


def fmt(s, label):
    if s is None or s["n"] == 0:
        print(f"  {label:45s} | n=0")
        return
    sig = " ***" if s["ci_low"] > 0 else ""
    print(f"  {label:45s} | n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}R | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}] | MFE={s['mfe']:.3f} MAE={s['mae']:.3f}{sig}")


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 0: BASELINE (all linked outcomes, no filtering)
# ═══════════════════════════════════════════════════════════════════════════════

print("─── STAGE 0: BASELINE (all linked V3 shadow observations) ───")
fmt(compute_stats(linked), "All linked outcomes")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1: MARKET CONTEXT (filter by opportunity state)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n─── STAGE 1: + OPPORTUNITY ASSESSMENT ───")
print("  Does quality classification separate outcomes?")
print()

# Cross-ref opportunity state
for r in linked:
    key = (r.get("symbol", ""), round(r.get("timestamp_utc", 0), 0))
    opp = opp_idx.get(key, {})
    r["_opp_state"] = opp.get("assessment_state", r.get("opportunity_state", ""))

high = [r for r in linked if r["_opp_state"] == "HIGH_QUALITY_CONTEXT"]
interesting = [r for r in linked if r["_opp_state"] == "INTERESTING_CONTEXT"]
mixed = [r for r in linked if r["_opp_state"] == "MIXED_CONTEXT"]
low = [r for r in linked if r["_opp_state"] == "LOW_QUALITY_CONTEXT"]

fmt(compute_stats(high), "HIGH_QUALITY_CONTEXT")
fmt(compute_stats(interesting), "INTERESTING_CONTEXT")
fmt(compute_stats(mixed), "MIXED_CONTEXT")
fmt(compute_stats(low), "LOW_QUALITY_CONTEXT")
# Combined: only pass HIGH + INTERESTING
passed_opp = [r for r in linked if r["_opp_state"] in ("HIGH_QUALITY_CONTEXT", "INTERESTING_CONTEXT")]
fmt(compute_stats(passed_opp), "Cumulative: HIGH + INTERESTING only")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2: + HORIZON (filter by horizon selection)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n─── STAGE 2: + HORIZON ASSESSMENT ───")
print("  Does horizon selection improve over unfiltered?")
print()

# Add horizon info
for r in linked:
    key = (r.get("symbol", ""), round(r.get("timestamp_utc", 0), 0))
    hor = horizon_idx.get(key, {})
    r["_horizon"] = hor.get("selected_horizon", r.get("horizon", ""))

scalp = [r for r in linked if r["_horizon"] == "SCALP"]
intraday = [r for r in linked if r["_horizon"] == "INTRADAY"]
extended = [r for r in linked if r["_horizon"] == "EXTENDED"]
no_horizon = [r for r in linked if r["_horizon"] == "NO_HORIZON"]

fmt(compute_stats(scalp), "SCALP horizon")
fmt(compute_stats(intraday), "INTRADAY horizon")
fmt(compute_stats(extended), "EXTENDED horizon")
fmt(compute_stats(no_horizon), "NO_HORIZON (rejected)")
# Cumulative: has a horizon
has_horizon = [r for r in linked if r["_horizon"] in ("SCALP", "INTRADAY", "EXTENDED")]
fmt(compute_stats(has_horizon), "Cumulative: any horizon selected")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3: + ENTRY BEHAVIOUR
# ═══════════════════════════════════════════════════════════════════════════════

print("\n─── STAGE 3: + ENTRY BEHAVIOUR ───")
print("  Does entry confirmation improve outcomes?")
print()

for r in linked:
    key = (r.get("symbol", ""), round(r.get("timestamp_utc", 0), 0))
    ent = entry_idx.get(key, {})
    r["_entry_state"] = ent.get("entry_state", r.get("entry_state", ""))
    r["_entry_behaviour"] = ent.get("entry_behaviour_type", "")

valid_entry = [r for r in linked if r["_entry_state"] == "VALID_ENTRY_CONFIRMATION"]
weak_entry = [r for r in linked if r["_entry_state"] == "WEAK_ENTRY_CONFIRMATION"]
no_entry = [r for r in linked if r["_entry_state"] == "NO_ENTRY_CONFIRMATION"]
insuf_entry = [r for r in linked if r["_entry_state"] == "INSUFFICIENT_ENTRY_DATA"]

fmt(compute_stats(valid_entry), "VALID_ENTRY_CONFIRMATION")
fmt(compute_stats(weak_entry), "WEAK_ENTRY_CONFIRMATION")
fmt(compute_stats(no_entry), "NO_ENTRY_CONFIRMATION")
fmt(compute_stats(insuf_entry), "INSUFFICIENT_ENTRY_DATA")
# Cumulative: valid or weak
has_entry = [r for r in linked if r["_entry_state"] in ("VALID_ENTRY_CONFIRMATION", "WEAK_ENTRY_CONFIRMATION")]
fmt(compute_stats(has_entry), "Cumulative: VALID + WEAK entry")

# Entry behaviour types
print("\n  By behaviour type:")
for btype in ["STRUCTURE_ALIGNMENT", "RETEST_BEHAVIOUR", "REJECTION_BEHAVIOUR", "MOMENTUM_TRANSITION", "UNKNOWN"]:
    subset = [r for r in linked if r["_entry_behaviour"] == btype]
    if subset:
        fmt(compute_stats(subset), f"  {btype}")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 4: + RISK ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════

print("\n─── STAGE 4: + RISK ASSESSMENT ───")
print("  Does risk geometry filtering improve outcomes?")
print()

for r in linked:
    key = (r.get("symbol", ""), round(r.get("timestamp_utc", 0), 0))
    rsk = risk_idx.get(key, {})
    r["_risk_state"] = rsk.get("risk_state", r.get("risk_state", ""))

acceptable = [r for r in linked if r["_risk_state"] == "ACCEPTABLE_RISK"]
marginal = [r for r in linked if r["_risk_state"] == "MARGINAL_RISK"]
poor = [r for r in linked if r["_risk_state"] == "POOR_RISK"]
insuf_risk = [r for r in linked if r["_risk_state"] == "INSUFFICIENT_RISK_DATA"]

fmt(compute_stats(acceptable), "ACCEPTABLE_RISK")
fmt(compute_stats(marginal), "MARGINAL_RISK")
fmt(compute_stats(poor), "POOR_RISK")
fmt(compute_stats(insuf_risk), "INSUFFICIENT_RISK_DATA")
# Cumulative: acceptable or marginal
has_risk = [r for r in linked if r["_risk_state"] in ("ACCEPTABLE_RISK", "MARGINAL_RISK")]
fmt(compute_stats(has_risk), "Cumulative: ACCEPTABLE + MARGINAL risk")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 5: + EXECUTION STATE (final pipeline)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n─── STAGE 5: + EXECUTION ASSESSMENT (full pipeline) ───")
print("  Does the complete V3 pipeline separate outcomes?")
print()

ready = [r for r in linked if r.get("execution_state") == "READY_FOR_EXECUTION"]
constrained = [r for r in linked if r.get("execution_state") == "EXECUTION_CONSTRAINED"]
simulated = [r for r in linked if r.get("execution_state") == "SIMULATED_ONLY"]
not_exec = [r for r in linked if r.get("execution_state") == "NOT_EXECUTABLE"]

fmt(compute_stats(ready), "READY_FOR_EXECUTION")
fmt(compute_stats(constrained), "EXECUTION_CONSTRAINED")
fmt(compute_stats(simulated), "SIMULATED_ONLY")
fmt(compute_stats(not_exec), "NOT_EXECUTABLE")

# ═══════════════════════════════════════════════════════════════════════════════
# INCREMENTAL VALUE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("INCREMENTAL VALUE SUMMARY")
print("=" * 70)

stages = [
    ("Baseline (all)", linked),
    ("+ Opp (HIGH+INTERESTING)", passed_opp),
    ("+ Horizon (any selected)", has_horizon),
    ("+ Entry (VALID+WEAK)", has_entry),
    ("+ Risk (ACCEPTABLE+MARGINAL)", has_risk),
    ("+ Execution (READY)", ready),
]

print(f"\n  {'Stage':<40s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'Δ EV':>7s} | {'Reject%':>7s}")
print(f"  {'-'*40}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}-+-{'-'*7}")

prev_ev = None
total_n = len(linked)
for label, subset in stages:
    s = compute_stats(subset)
    if s and s["n"] > 0:
        delta = f"{s['ev'] - prev_ev:+.4f}" if prev_ev is not None else "  —"
        reject_pct = f"{(1 - s['n']/total_n)*100:.0f}%" if total_n > 0 else "—"
        print(f"  {label:<40s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {delta:>7s} | {reject_pct:>7s}")
        prev_ev = s["ev"]
    else:
        print(f"  {label:<40s} | n=0")
        prev_ev = None

# ═══════════════════════════════════════════════════════════════════════════════
# CONCLUSION
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("CONCLUSION")
print("=" * 70)

baseline_s = compute_stats(linked)
ready_s = compute_stats(ready)

if ready_s and ready_s["n"] >= 3 and baseline_s:
    delta = ready_s["ev"] - baseline_s["ev"]
    print(f"\n  Full pipeline vs baseline: {delta:+.4f}R improvement")
    if delta > 0 and ready_s["n"] >= 10:
        print("  POSITIVE SIGNAL: Full V3 pipeline appears to improve EV")
    elif delta > 0:
        print("  EARLY POSITIVE: Direction correct but n too small for confidence")
    else:
        print("  NO IMPROVEMENT: Full pipeline does not outperform baseline")
elif ready_s is None or (ready_s and ready_s["n"] < 3):
    print(f"\n  INSUFFICIENT READY_FOR_EXECUTION events (n={ready_s['n'] if ready_s else 0})")
    print("  Cannot determine full pipeline value yet.")
    print("  Need minimum 10 READY events with linked outcomes.")
else:
    print("\n  Unable to compute comparison.")

print(f"\n  Sample size: {len(linked)} linked outcomes")
print(f"  Minimum for reliable analysis: 50+ per stage")
print(f"  Current status: {'SUFFICIENT' if len(linked) >= 50 else 'INSUFFICIENT'} for preliminary conclusions")
print()
