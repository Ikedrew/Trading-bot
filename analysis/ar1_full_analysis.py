"""AR1 — V3 Incremental Predictive Value Analysis (full)."""
import json, math
from pathlib import Path
from collections import Counter

base = Path("logs/v3_shadow/execution_assessment")
records = []
if base.exists():
    for f in base.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("_outcome", {}).get("result_r") is not None:
                            records.append(r)
                    except:
                        pass

def stats(subset):
    if not subset:
        return None
    outcomes = [r["_outcome"]["result_r"] for r in subset]
    n = len(outcomes)
    wins = sum(1 for o in outcomes if o > 0)
    wr = wins / n
    ev = sum(outcomes) / n
    std = math.sqrt(sum((o-ev)**2 for o in outcomes)/max(n-1,1))
    se = std/math.sqrt(n)
    median = sorted(outcomes)[n//2]
    mfe = [r["_outcome"].get("mfe_r",0) or 0 for r in subset]
    mae = [r["_outcome"].get("mae_r",0) or 0 for r in subset]
    gross_w = sum(o for o in outcomes if o > 0)
    gross_l = abs(sum(o for o in outcomes if o < 0))
    pf = gross_w/gross_l if gross_l > 0 else 99.0
    return {"n":n,"wr":wr,"ev":ev,"median":median,"std":std,"ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "mfe":sum(mfe)/len(mfe) if mfe else 0,"mae":sum(mae)/len(mae) if mae else 0,"pf":pf}

def fmt(s, label):
    if not s or s["n"]==0:
        print(f"  {label:40s} | n=0")
        return
    sig = " ***" if s["ci_low"]>0 else " (neg)" if s["ci_high"]<0 else ""
    print(f"  {label:40s} | n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | med={s['median']:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}] | PF={s['pf']:.2f}{sig}")

print("="*70)
print("AR1 — V3 INCREMENTAL PREDICTIVE VALUE ANALYSIS")
print("="*70)
print(f"\nDataset: {len(records)} linked execution assessments")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: EXECUTION STATE PERFORMANCE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: EXECUTION STATE PERFORMANCE")
print("─"*70)

for state in ["READY_FOR_EXECUTION","EXECUTION_CONSTRAINED","SIMULATED_ONLY","NOT_EXECUTABLE"]:
    subset = [r for r in records if r.get("execution_state")==state]
    fmt(stats(subset), state)

baseline = stats(records)
fmt(baseline, "BASELINE (all)")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: LAYER INCREMENTAL VALUE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: LAYER INCREMENTAL VALUE")
print("─"*70)

# Opportunity quality
print("\n  --- Opportunity Assessment ---")
high_int = [r for r in records if r.get("opportunity_state") in ("HIGH_QUALITY_CONTEXT","INTERESTING_CONTEXT")]
low_mix = [r for r in records if r.get("opportunity_state") in ("LOW_QUALITY_CONTEXT","MIXED_CONTEXT")]
fmt(stats(high_int), "HIGH + INTERESTING")
fmt(stats(low_mix), "LOW + MIXED")

# By individual state
for state in ["HIGH_QUALITY_CONTEXT","INTERESTING_CONTEXT","MIXED_CONTEXT","LOW_QUALITY_CONTEXT"]:
    subset = [r for r in records if r.get("opportunity_state")==state]
    fmt(stats(subset), f"  {state}")

# Horizon
print("\n  --- Horizon Assessment ---")
for h in ["SCALP","INTRADAY","EXTENDED","NO_HORIZON"]:
    subset = [r for r in records if r.get("horizon")==h]
    fmt(stats(subset), f"  {h}")

# Entry
print("\n  --- Entry Assessment ---")
for e in ["VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION","NO_ENTRY_CONFIRMATION","INSUFFICIENT_ENTRY_DATA"]:
    subset = [r for r in records if r.get("entry_state")==e]
    fmt(stats(subset), f"  {e}")

# Risk
print("\n  --- Risk Assessment ---")
for rs in ["ACCEPTABLE_RISK","MARGINAL_RISK","POOR_RISK","INSUFFICIENT_RISK_DATA"]:
    subset = [r for r in records if r.get("risk_state")==rs]
    fmt(stats(subset), f"  {rs}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: READY DEEP DIVE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: READY_FOR_EXECUTION DEEP DIVE (n=38)")
print("─"*70)

ready = [r for r in records if r.get("execution_state")=="READY_FOR_EXECUTION"]
ready_wins = [r for r in ready if r["_outcome"]["result_r"] > 0]
ready_losses = [r for r in ready if r["_outcome"]["result_r"] <= 0]

print(f"\n  Winners: {len(ready_wins)}, Losers: {len(ready_losses)}")

# By horizon within READY
print("\n  READY by horizon:")
for h in ["SCALP","INTRADAY"]:
    subset = [r for r in ready if r.get("horizon")==h]
    fmt(stats(subset), f"    READY + {h}")

# By direction
print("\n  READY by direction:")
for d in ["BULLISH","BEARISH"]:
    subset = [r for r in ready if r.get("direction")==d]
    fmt(stats(subset), f"    READY + {d}")

# Exit reasons
exits = Counter(r["_outcome"].get("exit_reason","") for r in ready)
print(f"\n  Exit reasons: {dict(exits)}")

# MFE analysis
ready_mfe = [r["_outcome"].get("mfe_r",0) or 0 for r in ready]
if ready_mfe:
    print(f"\n  MFE distribution (READY):")
    print(f"    Mean MFE: {sum(ready_mfe)/len(ready_mfe):.3f}R")
    mfe_above_1 = sum(1 for m in ready_mfe if m >= 1.0)
    mfe_above_2 = sum(1 for m in ready_mfe if m >= 2.0)
    print(f"    MFE >= 1R: {mfe_above_1}/{len(ready_mfe)} ({mfe_above_1/len(ready_mfe)*100:.0f}%)")
    print(f"    MFE >= 2R: {mfe_above_2}/{len(ready_mfe)} ({mfe_above_2/len(ready_mfe)*100:.0f}%)")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: LATE ENTRY HYPOTHESIS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: LATE ENTRY / TIMING INVESTIGATION")
print("─"*70)

# Compare MFE between states (if high MFE but negative EV = entry timing problem)
for state in ["READY_FOR_EXECUTION","EXECUTION_CONSTRAINED","SIMULATED_ONLY"]:
    subset = [r for r in records if r.get("execution_state")==state]
    if subset:
        mfes = [r["_outcome"].get("mfe_r",0) or 0 for r in subset]
        maes = [r["_outcome"].get("mae_r",0) or 0 for r in subset]
        avg_mfe = sum(mfes)/len(mfes) if mfes else 0
        avg_mae = sum(maes)/len(maes) if maes else 0
        outcomes = [r["_outcome"]["result_r"] for r in subset]
        avg_r = sum(outcomes)/len(outcomes)
        captured = avg_r / avg_mfe if avg_mfe > 0 else 0
        print(f"  {state:30s} | MFE={avg_mfe:.3f} | MAE={avg_mae:.3f} | Result={avg_r:+.3f} | Captured={captured:.0%} of MFE")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: NOT_EXECUTABLE INVESTIGATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: NOT_EXECUTABLE ANOMALY INVESTIGATION")
print("─"*70)

not_exec = [r for r in records if r.get("execution_state")=="NOT_EXECUTABLE"]
if not_exec:
    # Check what opportunity states these have
    ne_opp = Counter(r.get("opportunity_state","") for r in not_exec)
    ne_hor = Counter(r.get("horizon","") for r in not_exec)
    print(f"  NOT_EXECUTABLE opportunity states: {dict(ne_opp)}")
    print(f"  NOT_EXECUTABLE horizons: {dict(ne_hor)}")
    print(f"  These are likely early records before pipeline had HTF data.")
    
    # Check timestamp distribution
    ne_ts = [r.get("timestamp_utc",0) for r in not_exec]
    from datetime import datetime, timezone
    if ne_ts:
        earliest = min(t for t in ne_ts if t > 0)
        latest = max(ne_ts)
        print(f"  Timestamp range: {datetime.fromtimestamp(earliest,tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(latest,tz=timezone.utc).strftime('%Y-%m-%d')}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: FEATURE RANKING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: PREDICTIVE RANKING")
print("─"*70)

rankings = []

# Compare each binary split
comparisons = [
    ("Opp: HIGH+INT vs LOW+MIX", high_int, low_mix),
    ("Horizon: SCALP vs rest", [r for r in records if r.get("horizon")=="SCALP"],
     [r for r in records if r.get("horizon") not in ("SCALP","")]),
    ("Horizon: INTRADAY vs rest", [r for r in records if r.get("horizon")=="INTRADAY"],
     [r for r in records if r.get("horizon") not in ("INTRADAY","")]),
    ("Entry: VALID vs rest", [r for r in records if r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"],
     [r for r in records if r.get("entry_state")!="VALID_ENTRY_CONFIRMATION"]),
    ("Entry: has confirmation vs none", [r for r in records if r.get("entry_state") in ("VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION")],
     [r for r in records if r.get("entry_state") not in ("VALID_ENTRY_CONFIRMATION","WEAK_ENTRY_CONFIRMATION")]),
    ("Risk: ACCEPTABLE vs rest", [r for r in records if r.get("risk_state")=="ACCEPTABLE_RISK"],
     [r for r in records if r.get("risk_state")!="ACCEPTABLE_RISK"]),
]

print(f"\n  {'Comparison':<45s} | {'n_yes':>5s} | {'EV_yes':>7s} | {'n_no':>5s} | {'EV_no':>7s} | {'Delta':>7s}")
print(f"  {'-'*45}-+-{'-'*5}-+-{'-'*7}-+-{'-'*5}-+-{'-'*7}-+-{'-'*7}")

for label, yes_set, no_set in comparisons:
    s_yes = stats(yes_set)
    s_no = stats(no_set)
    if s_yes and s_no and s_yes["n"]>=5 and s_no["n"]>=5:
        delta = s_yes["ev"] - s_no["ev"]
        print(f"  {label:<45s} | {s_yes['n']:>5d} | {s_yes['ev']:>+6.4f} | {s_no['n']:>5d} | {s_no['ev']:>+6.4f} | {delta:>+6.4f}")
        rankings.append((label, delta, s_yes["n"]))

rankings.sort(key=lambda x: x[1], reverse=True)
print(f"\n  Ranked by EV improvement:")
for i, (label, delta, n) in enumerate(rankings, 1):
    direction = "+" if delta > 0 else "-"
    print(f"    {i}. {label:45s} | {delta:>+.4f}R | n={n}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR1 VERDICT")
print("="*70)

ready_s = stats(ready)
constrained_s = stats([r for r in records if r.get("execution_state")=="EXECUTION_CONSTRAINED"])

if ready_s and constrained_s:
    ready_vs_constrained = ready_s["ev"] - constrained_s["ev"]
    print(f"\n  READY vs CONSTRAINED: {ready_vs_constrained:+.4f}R")
    print(f"  READY vs BASELINE: {ready_s['ev'] - baseline['ev']:+.4f}R")

    if ready_s["ev"] > constrained_s["ev"] and ready_s["ci_low"] > 0:
        print("\n  A) V3 reasoning layers demonstrate predictive value")
    elif ready_s["ev"] < constrained_s["ev"]:
        print("\n  C) Current approval logic is selecting inferior opportunities")
        print(f"     READY EV={ready_s['ev']:+.4f} < CONSTRAINED EV={constrained_s['ev']:+.4f}")
        print(f"     The stricter the filter, the WORSE the outcome.")
        print(f"     This suggests the final approval gates (VALID entry + ACCEPTABLE risk)")
        print(f"     are correlated with conditions that produce worse outcomes.")
    else:
        print("\n  D) Insufficient evidence — results within noise")

print()
