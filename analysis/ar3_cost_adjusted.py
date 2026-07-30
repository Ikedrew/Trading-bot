"""AR3 — Early Signal Cost-Adjusted Expectancy Analysis."""
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

# Exclude NOT_EXECUTABLE artefact
records = [r for r in all_records if r.get("execution_state") != "NOT_EXECUTABLE"]

# Cost assumptions (research priors from V2/V3 research)
SPREAD_PIPS = 1.0  # Typical major FX spread
COMMISSION_PIPS = 0.0  # Included in spread for most brokers
SLIPPAGE_PIPS = 0.2  # Conservative estimate
TOTAL_COST_PIPS = SPREAD_PIPS + SLIPPAGE_PIPS

# Risk geometry assumptions
SCALP_STOP_PIPS = 3.5   # (2+5)/2 from profile
INTRADAY_STOP_PIPS = 10.0  # (5+15)/2 from profile

SCALP_SPREAD_RISK = TOTAL_COST_PIPS / SCALP_STOP_PIPS  # 1.2/3.5 = 0.343
INTRADAY_SPREAD_RISK = TOTAL_COST_PIPS / INTRADAY_STOP_PIPS  # 1.2/10 = 0.12

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
    mfe = [r["_outcome"].get("mfe_r",0) or 0 for r in subset]
    mae = [r["_outcome"].get("mae_r",0) or 0 for r in subset]
    return {"n":n,"wr":wr,"ev":ev,"std":std,"se":se,
            "ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "mfe":sum(mfe)/len(mfe),"mae":sum(mae)/len(mae)}

print("="*70)
print("AR3 — EARLY SIGNAL COST-ADJUSTED EXPECTANCY ANALYSIS")
print("="*70)
print(f"\nDataset: {len(records)} records (excl NOT_EXECUTABLE)")
print(f"\nCost assumptions:")
print(f"  Spread: {SPREAD_PIPS} pips")
print(f"  Slippage: {SLIPPAGE_PIPS} pips")
print(f"  Total cost: {TOTAL_COST_PIPS} pips")
print(f"  SCALP stop: {SCALP_STOP_PIPS} pips → spread/risk = {SCALP_SPREAD_RISK:.1%}")
print(f"  INTRADAY stop: {INTRADAY_STOP_PIPS} pips → spread/risk = {INTRADAY_SPREAD_RISK:.1%}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: COST-ADJUSTED PERFORMANCE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: COST-ADJUSTED PERFORMANCE")
print("─"*70)

groups = {
    "WEAK (all)": [r for r in records if r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"],
    "VALID (all)": [r for r in records if r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"],
    "SCALP + WEAK": [r for r in records if r.get("horizon")=="SCALP" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"],
    "INTRADAY + WEAK": [r for r in records if r.get("horizon")=="INTRADAY" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"],
    "SCALP + VALID": [r for r in records if r.get("horizon")=="SCALP" and r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"],
    "INTRADAY + VALID": [r for r in records if r.get("horizon")=="INTRADAY" and r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"],
}

print(f"\n  {'Group':<25s} | {'n':>4s} | {'WR':>5s} | {'Gross EV':>8s} | {'SCALP cost':>10s} | {'INTRA cost':>10s} | {'SCALP net':>9s} | {'INTRA net':>9s}")
print(f"  {'-'*25}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*9}-+-{'-'*9}")

for label, subset in groups.items():
    s = stats(subset)
    if s and s["n"] >= 5:
        scalp_net = s["ev"] - SCALP_SPREAD_RISK
        intra_net = s["ev"] - INTRADAY_SPREAD_RISK
        print(f"  {label:<25s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {SCALP_SPREAD_RISK:>+9.4f} | {INTRADAY_SPREAD_RISK:>+9.4f} | {scalp_net:>+8.4f} | {intra_net:>+8.4f}")
    elif s:
        print(f"  {label:<25s} | {s['n']:>4d} | (insufficient sample)")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: RISK GEOMETRY COMPARISON
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: RISK GEOMETRY COMPARISON")
print("─"*70)

print(f"\n  {'Geometry':<15s} | {'Stop':>6s} | {'Spread/Risk':>11s} | {'Breakeven EV':>12s} | {'WEAK net':>8s} | {'Viable?':>7s}")
print(f"  {'-'*15}-+-{'-'*6}-+-{'-'*11}-+-{'-'*12}-+-{'-'*8}-+-{'-'*7}")

weak_ev = stats(groups["WEAK (all)"])["ev"] if stats(groups["WEAK (all)"]) else 0

geometries = [
    ("SCALP", SCALP_STOP_PIPS, SCALP_SPREAD_RISK),
    ("INTRADAY", INTRADAY_STOP_PIPS, INTRADAY_SPREAD_RISK),
    ("WIDE_INTRA (15p)", 15.0, TOTAL_COST_PIPS/15.0),
    ("STRUCTURE (20p)", 20.0, TOTAL_COST_PIPS/20.0),
]

for name, stop, sr in geometries:
    net = weak_ev - sr
    viable = "YES" if net > 0 else "NO"
    print(f"  {name:<15s} | {stop:>5.1f} | {sr:>10.1%} | {sr:>+11.4f} | {net:>+7.4f} | {viable:>7s}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: TIMING CAPTURE EFFICIENCY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: TIMING CAPTURE EFFICIENCY")
print("─"*70)

print(f"\n  {'Group':<25s} | {'MFE':>5s} | {'MAE':>5s} | {'R':>6s} | {'MFE-MAE':>7s} | {'Captured':>8s} | {'Entry quality':>12s}")
print(f"  {'-'*25}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*12}")

for label, subset in groups.items():
    s = stats(subset)
    if s and s["n"] >= 5:
        balance = s["mfe"] - s["mae"]
        captured = s["ev"]/s["mfe"]*100 if s["mfe"] > 0 else 0
        timing = "EARLY" if captured > 0 else "LATE" if captured < -20 else "NEUTRAL"
        print(f"  {label:<25s} | {s['mfe']:.3f} | {s['mae']:.3f} | {s['ev']:+.4f} | {balance:+.4f} | {captured:+.0f}% | {timing}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: ENVIRONMENT FILTERING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: ENVIRONMENT FILTERING (WEAK entries only)")
print("─"*70)

weak_all = groups["WEAK (all)"]

# By horizon
print("\n  By Horizon:")
for h in ["SCALP", "INTRADAY"]:
    subset = [r for r in weak_all if r.get("horizon") == h]
    s = stats(subset)
    if s and s["n"] >= 5:
        net_intra = s["ev"] - INTRADAY_SPREAD_RISK
        print(f"    {h:15s}: n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | INTRA net={net_intra:+.4f}")

# By opportunity quality
print("\n  By Opportunity Quality:")
for opp in ["HIGH_QUALITY_CONTEXT", "INTERESTING_CONTEXT", "MIXED_CONTEXT"]:
    subset = [r for r in weak_all if r.get("opportunity_state") == opp]
    s = stats(subset)
    if s and s["n"] >= 5:
        net_intra = s["ev"] - INTRADAY_SPREAD_RISK
        print(f"    {opp:30s}: n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | INTRA net={net_intra:+.4f}")

# By direction
print("\n  By Direction:")
for d in ["BULLISH", "BEARISH", "NEUTRAL"]:
    subset = [r for r in weak_all if r.get("direction") == d]
    s = stats(subset)
    if s and s["n"] >= 5:
        net_intra = s["ev"] - INTRADAY_SPREAD_RISK
        print(f"    {d:15s}: n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | INTRA net={net_intra:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: OPPORTUNITY vs ENTRY SEPARATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: OPPORTUNITY QUALITY x ENTRY STATE")
print("─"*70)

print(f"\n  {'Combination':<45s} | {'n':>4s} | {'WR':>5s} | {'EV':>7s} | {'INTRA net':>9s}")
print(f"  {'-'*45}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*9}")

combos = [
    ("HIGH opp + WEAK entry", [r for r in records if r.get("opportunity_state")=="HIGH_QUALITY_CONTEXT" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"]),
    ("HIGH opp + VALID entry", [r for r in records if r.get("opportunity_state")=="HIGH_QUALITY_CONTEXT" and r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"]),
    ("INTERESTING opp + WEAK entry", [r for r in records if r.get("opportunity_state")=="INTERESTING_CONTEXT" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"]),
    ("INTERESTING opp + VALID entry", [r for r in records if r.get("opportunity_state")=="INTERESTING_CONTEXT" and r.get("entry_state")=="VALID_ENTRY_CONFIRMATION"]),
    ("INTERESTING opp + NO entry", [r for r in records if r.get("opportunity_state")=="INTERESTING_CONTEXT" and r.get("entry_state")=="NO_ENTRY_CONFIRMATION"]),
    ("MIXED opp + WEAK entry", [r for r in records if r.get("opportunity_state")=="MIXED_CONTEXT" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION"]),
]

for label, subset in combos:
    s = stats(subset)
    if s and s["n"] >= 3:
        net = s["ev"] - INTRADAY_SPREAD_RISK
        print(f"  {label:<45s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+6.4f} | {net:>+8.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: BEST CONFIGURATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: BEST CONFIGURATION SEARCH")
print("─"*70)

# Test all meaningful combinations with INTRADAY cost
configs = [
    ("SCALP + WEAK", groups["SCALP + WEAK"]),
    ("INTRADAY + WEAK", groups["INTRADAY + WEAK"]),
    ("SCALP + WEAK + INTERESTING", [r for r in records if r.get("horizon")=="SCALP" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION" and r.get("opportunity_state")=="INTERESTING_CONTEXT"]),
    ("INTRADAY + WEAK + INTERESTING", [r for r in records if r.get("horizon")=="INTRADAY" and r.get("entry_state")=="WEAK_ENTRY_CONFIRMATION" and r.get("opportunity_state")=="INTERESTING_CONTEXT"]),
    ("Any horizon + WEAK + BULLISH", [r for r in weak_all if r.get("direction")=="BULLISH"]),
    ("Any horizon + WEAK + BEARISH", [r for r in weak_all if r.get("direction")=="BEARISH"]),
]

print(f"\n  {'Configuration':<40s} | {'n':>4s} | {'WR':>5s} | {'Gross':>7s} | {'@SCALP':>7s} | {'@INTRA':>7s} | {'@WIDE':>7s}")
print(f"  {'-'*40}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}")

for label, subset in configs:
    s = stats(subset)
    if s and s["n"] >= 5:
        scalp_n = s["ev"] - SCALP_SPREAD_RISK
        intra_n = s["ev"] - INTRADAY_SPREAD_RISK
        wide_n = s["ev"] - TOTAL_COST_PIPS/15.0
        print(f"  {label:<40s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+6.4f} | {scalp_n:>+6.4f} | {intra_n:>+6.4f} | {wide_n:>+6.4f}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR3 VERDICT")
print("="*70)

best_weak = stats(groups["WEAK (all)"])
scalp_weak = stats(groups["SCALP + WEAK"])
intra_weak = stats(groups["INTRADAY + WEAK"])

if best_weak:
    print(f"\n  WEAK confirmation raw EV: {best_weak['ev']:+.4f}R (n={best_weak['n']})")
    print(f"  95% CI: [{best_weak['ci_low']:+.4f}, {best_weak['ci_high']:+.4f}]")
    print()
    print(f"  Cost-adjusted at SCALP geometry (34% spread/risk):")
    print(f"    Net EV = {best_weak['ev']:+.4f} - {SCALP_SPREAD_RISK:.4f} = {best_weak['ev']-SCALP_SPREAD_RISK:+.4f}R  ← NEGATIVE")
    print()
    print(f"  Cost-adjusted at INTRADAY geometry (12% spread/risk):")
    print(f"    Net EV = {best_weak['ev']:+.4f} - {INTRADAY_SPREAD_RISK:.4f} = {best_weak['ev']-INTRADAY_SPREAD_RISK:+.4f}R  ← {'NEGATIVE' if best_weak['ev']-INTRADAY_SPREAD_RISK < 0 else 'POSITIVE'}")
    print()

    intra_net = best_weak["ev"] - INTRADAY_SPREAD_RISK
    wide_net = best_weak["ev"] - TOTAL_COST_PIPS/15.0

    if intra_net > 0:
        print("  VERDICT: A) Early signal survives costs at INTRADAY geometry")
    elif wide_net > 0:
        print("  VERDICT: B) Signal exists but only with wider geometry (15+ pip stops)")
    elif best_weak["ev"] > 0 and best_weak["ci_high"] > INTRADAY_SPREAD_RISK:
        print("  VERDICT: B) Signal exists but only in specific conditions")
        print(f"    CI upper ({best_weak['ci_high']:+.4f}) exceeds INTRADAY cost ({INTRADAY_SPREAD_RISK:.4f})")
        print("    Signal MAY be viable — but current point estimate is below breakeven")
    elif best_weak["ev"] <= 0:
        print("  VERDICT: C) Predictive value disappears after realistic costs")
    else:
        print("  VERDICT: D) More data required")

    if scalp_weak:
        print(f"\n  Best raw signal: SCALP+WEAK EV={scalp_weak['ev']:+.4f} (n={scalp_weak['n']})")
        print(f"    At INTRADAY geometry: {scalp_weak['ev']-INTRADAY_SPREAD_RISK:+.4f}R")
        print(f"    At WIDE geometry (15p): {scalp_weak['ev']-TOTAL_COST_PIPS/15.0:+.4f}R")

print()
