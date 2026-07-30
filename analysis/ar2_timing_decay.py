"""AR2 — Opportunity Timing Decay Analysis."""
import json, math
from pathlib import Path
from collections import Counter

base = Path("logs/v3_shadow/execution_assessment")
all_records = []
if base.exists():
    for f in base.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("_outcome", {}).get("result_r") is not None:
                            all_records.append(r)
                    except:
                        pass

# EXCLUDE NOT_EXECUTABLE (composition artefact from AR1)
records = [r for r in all_records if r.get("execution_state") != "NOT_EXECUTABLE"]

def stats(subset):
    if not subset: return None
    outcomes = [r["_outcome"]["result_r"] for r in subset]
    n = len(outcomes)
    if n == 0: return None
    wins = sum(1 for o in outcomes if o > 0)
    wr = wins/n
    ev = sum(outcomes)/n
    std = math.sqrt(sum((o-ev)**2 for o in outcomes)/max(n-1,1))
    se = std/math.sqrt(n)
    median = sorted(outcomes)[n//2]
    mfe = [r["_outcome"].get("mfe_r",0) or 0 for r in subset]
    mae = [r["_outcome"].get("mae_r",0) or 0 for r in subset]
    exits = Counter(r["_outcome"].get("exit_reason","") for r in subset)
    timeout_pct = exits.get("max_bars_timeout",0)/n
    return {"n":n,"wr":wr,"ev":ev,"median":median,"std":std,
            "ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "mfe":sum(mfe)/len(mfe),"mae":sum(mae)/len(mae),
            "timeout_pct":timeout_pct}

def fmt(s, label):
    if not s: print(f"  {label:45s} | n=0"); return
    print(f"  {label:45s} | n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f} | MAE={s['mae']:.3f} | TO={s['timeout_pct']:.0%} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

print("="*70)
print("AR2 — OPPORTUNITY TIMING DECAY ANALYSIS")
print("="*70)
print(f"\nDataset: {len(records)} records (excl. NOT_EXECUTABLE artefact)")
print(f"Excluded: {len(all_records) - len(records)} NOT_EXECUTABLE records")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: PIPELINE STAGE PERFORMANCE (cleaned)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: PIPELINE STAGE PERFORMANCE (cleaned baseline)")
print("─"*70)

fmt(stats(records), "BASELINE (excl NOT_EXEC)")
print()

# Progressive filtering
stage_1 = records  # All active pipeline records
stage_2_hi = [r for r in records if r.get("opportunity_state") in ("HIGH_QUALITY_CONTEXT","INTERESTING_CONTEXT")]
stage_2_mix = [r for r in records if r.get("opportunity_state") == "MIXED_CONTEXT"]
stage_3_hor = [r for r in records if r.get("horizon") in ("SCALP","INTRADAY","EXTENDED")]
stage_4_entry = [r for r in records if r.get("entry_state") in ("VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION")]
stage_4b_valid = [r for r in records if r.get("entry_state") == "VALID_ENTRY_CONFIRMATION"]
stage_4c_weak = [r for r in records if r.get("entry_state") == "WEAK_ENTRY_CONFIRMATION"]
stage_5_constrained = [r for r in records if r.get("execution_state") == "EXECUTION_CONSTRAINED"]
stage_6_ready = [r for r in records if r.get("execution_state") == "READY_FOR_EXECUTION"]

print("  Progressive filtering (cumulative):")
fmt(stats(stage_1), "Stage 1: All active pipeline")
fmt(stats(stage_2_hi), "Stage 2: HIGH+INTERESTING opp")
fmt(stats(stage_3_hor), "Stage 3: Horizon selected (SCALP/INTRA)")
fmt(stats(stage_4_entry), "Stage 4: Entry confirmed (VALID+WEAK)")
fmt(stats(stage_4c_weak), "Stage 4b: WEAK confirmation only")
fmt(stats(stage_4b_valid), "Stage 4c: VALID confirmation only")
fmt(stats(stage_5_constrained), "Stage 5: CONSTRAINED execution")
fmt(stats(stage_6_ready), "Stage 6: READY_FOR_EXECUTION")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: MFE/MAE DECAY — WHERE DOES CAPTURE FAIL?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: MFE/MAE TIMING ANALYSIS")
print("─"*70)

print("\n  Movement characteristics by pipeline stage:")
print(f"  {'Stage':<35s} | {'MFE':>5s} | {'MAE':>5s} | {'R':>6s} | {'MFE-MAE':>7s} | {'Captured':>8s}")
print(f"  {'-'*35}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}")

for label, subset in [
    ("All active", stage_1),
    ("HIGH+INTERESTING", stage_2_hi),
    ("Horizon selected", stage_3_hor),
    ("WEAK entry", stage_4c_weak),
    ("VALID entry", stage_4b_valid),
    ("CONSTRAINED", stage_5_constrained),
    ("READY", stage_6_ready),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        balance = s["mfe"] - s["mae"]
        captured = s["ev"]/s["mfe"]*100 if s["mfe"] > 0 else 0
        print(f"  {label:<35s} | {s['mfe']:.3f} | {s['mae']:.3f} | {s['ev']:+.4f} | {balance:+.4f} | {captured:+.0f}%")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: ENTRY STATE VALUE (within active pipeline)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: ENTRY STATE AS PREDICTOR")
print("─"*70)

# Only within records that have horizon (exclude NO_HORIZON/INSUFFICIENT)
with_horizon = [r for r in records if r.get("horizon") in ("SCALP","INTRADAY","EXTENDED")]
print(f"\n  Within horizon-selected records (n={len(with_horizon)}):")

for entry_state in ["VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION","NO_ENTRY_CONFIRMATION"]:
    subset = [r for r in with_horizon if r.get("entry_state") == entry_state]
    fmt(stats(subset), f"  {entry_state}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: SIMULATED ENTRY TIMING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: ENTRY TIMING SIMULATION")
print("─"*70)

print("\n  Hypothetical entry checkpoints (within horizon-selected):")
print("  If we entered AT each pipeline stage rather than waiting for READY:")
print()

# Entry A: At opportunity detection (HIGH+INTERESTING, any entry state)
entry_a = [r for r in with_horizon if r.get("opportunity_state") in ("HIGH_QUALITY_CONTEXT","INTERESTING_CONTEXT")]
# Entry B: After horizon (has horizon, regardless of entry)
entry_b = with_horizon
# Entry C: After weak+ confirmation
entry_c = [r for r in with_horizon if r.get("entry_state") in ("VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION")]
# Entry D: READY only
entry_d = [r for r in with_horizon if r.get("execution_state") == "READY_FOR_EXECUTION"]

fmt(stats(entry_a), "Entry A: At opportunity (HIGH+INT+horizon)")
fmt(stats(entry_b), "Entry B: After horizon selection")
fmt(stats(entry_c), "Entry C: After WEAK+ confirmation")
fmt(stats(entry_d), "Entry D: READY only")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: HORIZON COMPARISON (SCALP vs INTRADAY)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: HORIZON-SPECIFIC PERFORMANCE")
print("─"*70)

for h in ["SCALP","INTRADAY"]:
    subset = [r for r in records if r.get("horizon") == h]
    fmt(stats(subset), f"  {h} (all)")
    # Within each horizon, by entry state
    for es in ["WEAK_ENTRY_CONFIRMATION","VALID_ENTRY_CONFIRMATION","NO_ENTRY_CONFIRMATION"]:
        sub2 = [r for r in subset if r.get("entry_state") == es]
        if sub2:
            fmt(stats(sub2), f"    + {es}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: MFE DISTRIBUTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: MFE DISTRIBUTION (do trades move AT ALL?)")
print("─"*70)

active_mfe = [r["_outcome"].get("mfe_r",0) or 0 for r in records]
if active_mfe:
    buckets = [
        ("MFE < 0.25R (no movement)", sum(1 for m in active_mfe if m < 0.25)),
        ("MFE 0.25-0.5R (small)", sum(1 for m in active_mfe if 0.25 <= m < 0.5)),
        ("MFE 0.5-1.0R (moderate)", sum(1 for m in active_mfe if 0.5 <= m < 1.0)),
        ("MFE 1.0-2.0R (good)", sum(1 for m in active_mfe if 1.0 <= m < 2.0)),
        ("MFE >= 2.0R (strong)", sum(1 for m in active_mfe if m >= 2.0)),
    ]
    n_total = len(active_mfe)
    for label, count in buckets:
        print(f"  {label:<35s}: {count:4d} ({count/n_total*100:.1f}%)")
    print(f"\n  Mean MFE: {sum(active_mfe)/len(active_mfe):.3f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 7: PREDICTIVE RANKING (cleaned)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 7: PREDICTIVE VALUE RANKING (cleaned)")
print("─"*70)

# Only compare within active pipeline (no NOT_EXEC contamination)
comparisons = [
    ("WEAK entry vs NO entry", stage_4c_weak, [r for r in with_horizon if r.get("entry_state")=="NO_ENTRY_CONFIRMATION"]),
    ("VALID entry vs WEAK entry", stage_4b_valid, stage_4c_weak),
    ("CONSTRAINED vs SIMULATED", stage_5_constrained, [r for r in records if r.get("execution_state")=="SIMULATED_ONLY"]),
    ("READY vs CONSTRAINED", stage_6_ready, stage_5_constrained),
    ("SCALP vs INTRADAY", [r for r in records if r.get("horizon")=="SCALP"], [r for r in records if r.get("horizon")=="INTRADAY"]),
    ("HIGH opp vs INTERESTING opp", [r for r in records if r.get("opportunity_state")=="HIGH_QUALITY_CONTEXT"], [r for r in records if r.get("opportunity_state")=="INTERESTING_CONTEXT"]),
]

print(f"\n  {'Comparison':<40s} | {'n_A':>4s} | {'EV_A':>7s} | {'n_B':>4s} | {'EV_B':>7s} | {'Delta':>7s}")
print(f"  {'-'*40}-+-{'-'*4}-+-{'-'*7}-+-{'-'*4}-+-{'-'*7}-+-{'-'*7}")

for label, set_a, set_b in comparisons:
    sa = stats(set_a)
    sb = stats(set_b)
    if sa and sb and sa["n"] >= 5 and sb["n"] >= 5:
        delta = sa["ev"] - sb["ev"]
        print(f"  {label:<40s} | {sa['n']:>4d} | {sa['ev']:>+6.4f} | {sb['n']:>4d} | {sb['ev']:>+6.4f} | {delta:>+6.4f}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR2 VERDICT")
print("="*70)

# Compare entry stages
s_all = stats(records)
s_weak = stats(stage_4c_weak)
s_valid = stats(stage_4b_valid)
s_ready = stats(stage_6_ready)
s_constrained = stats(stage_5_constrained)

if s_all and s_weak and s_valid and s_constrained:
    print(f"\n  Cleaned baseline (excl NOT_EXEC): EV={s_all['ev']:+.4f}R (n={s_all['n']})")
    print(f"  WEAK entry:                       EV={s_weak['ev']:+.4f}R (n={s_weak['n']})")
    if s_valid:
        print(f"  VALID entry:                      EV={s_valid['ev']:+.4f}R (n={s_valid['n']})")
    print(f"  CONSTRAINED:                      EV={s_constrained['ev']:+.4f}R (n={s_constrained['n']})")
    if s_ready:
        print(f"  READY:                            EV={s_ready['ev']:+.4f}R (n={s_ready['n']})")

    # Where does decay begin?
    if s_weak and s_all:
        if s_weak["ev"] > s_all["ev"]:
            print("\n  Decay begins AFTER WEAK confirmation (VALID adds negative value)")
        elif s_weak["ev"] < s_all["ev"]:
            print("\n  Decay begins AT entry confirmation (WEAK already worse than unfiltered)")
        else:
            print("\n  No clear decay point — all stages similar")

    # Final classification
    if s_constrained and s_ready and s_constrained["ev"] > s_ready["ev"]:
        if s_constrained["ev"] > 0:
            print("\n  VERDICT: B) Opportunity detection valuable, confirmation destroys expectancy")
        else:
            print("\n  VERDICT: A) Predictive value exists before confirmation")
    elif s_all and s_all["ev"] <= 0 and (not s_ready or s_ready["ev"] <= 0):
        print("\n  VERDICT: C) No meaningful predictive value found (all stages negative or near-zero)")
    else:
        print("\n  VERDICT: D) More data required for definitive conclusion")

print()
