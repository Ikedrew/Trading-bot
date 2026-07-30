"""AR5 — Structural Risk Geometry Validation.

Validates whether AR4's positive EV comes from genuine structural quality
or simply from wider risk geometry reducing cost impact.
"""
import json, math
from pathlib import Path
from collections import Counter

# ═══════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════

exec_dir = Path("logs/v3_shadow/execution_assessment")
shadow_dir = Path("logs/shadow_trades")

# Load V3 execution assessments (WEAK+INTERESTING only, excl NOT_EXEC)
exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if (r.get("_outcome", {}).get("result_r") is not None and
                            r.get("execution_state") != "NOT_EXECUTABLE" and
                            r.get("entry_state") == "WEAK_ENTRY_CONFIRMATION" and
                            r.get("opportunity_state") == "INTERESTING_CONTEXT"):
                            exec_records.append(r)
                    except:
                        pass

# Load shadow trades with progression
shadow_by_key = {}
if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            r = json.loads(line)
                            if r.get("schema_version") == "shadow_trades_v2":
                                prog = r.get("simulated_outcome", {}).get("trade_state_progression", [])
                                if prog:
                                    eid = r.get("identity", {}).get("entity_id", "")
                                    sym = r.get("identity", {}).get("symbol", "")
                                    ts = r.get("decision_snapshot", {}).get("timestamp_decision_utc", 0)
                                    if eid:
                                        shadow_by_key[eid] = r
                                    if sym and ts:
                                        shadow_by_key[f"{sym}_{int(ts)}"] = r
                        except:
                            pass

# Match V3 records to shadow progressions
matched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = rec.get("timestamp_utc", 0)
    key = f"{sym}_{int(ts)}"
    trade = shadow_by_key.get(key)
    if not trade:
        for delta in [-300, 300]:
            trade = shadow_by_key.get(f"{sym}_{int(ts+delta)}")
            if trade: break
    if trade:
        prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
        risk_pips = trade.get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) or 0
        matched.append({"v3": rec, "progression": prog, "trade": trade, "risk_pips": risk_pips})

print("="*70)
print("AR5 — STRUCTURAL RISK GEOMETRY VALIDATION")
print("="*70)
print(f"\nTarget: WEAK + INTERESTING records")
print(f"V3 records: {len(exec_records)}")
print(f"Matched to progressions: {len(matched)}")

if len(matched) < 10:
    print("\nINSUFFICIENT DATA. Need more matched records.")
    exit()

# ═══════════════════════════════════════════════════════════════
# SIMULATION FUNCTION
# ═══════════════════════════════════════════════════════════════

def simulate(progression, max_bars, sl_r, tp_r=99.0):
    for b in progression[:max_bars]:
        r = float(b.get("r", 0))
        if r <= -sl_r: return -sl_r, "stop_loss"
        if r >= tp_r: return tp_r, "take_profit"
    if progression[:max_bars]:
        return float(progression[min(max_bars-1, len(progression)-1)].get("r", 0)), "timeout"
    return 0.0, "no_data"

def group_stats(items, max_bars=60, sl_r=0.5, tp_r=99.0):
    if not items: return None
    results = []
    exits = Counter()
    mfe_vals = []
    for item in items:
        r, reason = simulate(item["progression"], max_bars, sl_r, tp_r)
        results.append(r)
        exits[reason] += 1
        bar_rs = [float(b.get("r",0)) for b in item["progression"][:max_bars]]
        if bar_rs: mfe_vals.append(max(bar_rs))
    n = len(results)
    wins = sum(1 for r in results if r > 0)
    ev = sum(results)/n
    std = math.sqrt(sum((r-ev)**2 for r in results)/max(n-1,1))
    se = std/math.sqrt(n)
    avg_mfe = sum(mfe_vals)/len(mfe_vals) if mfe_vals else 0
    return {"n":n,"wr":wins/n,"ev":ev,"std":std,"ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "mfe":avg_mfe,"to_pct":exits.get("timeout",0)/n,"sl_pct":exits.get("stop_loss",0)/n,
            "tp_pct":exits.get("take_profit",0)/n}

def fmt(s, label, cost_r=0.0):
    if not s: print(f"  {label:40s} | n=0"); return
    net = s["ev"] - cost_r
    sig = " ***" if s["ci_low"] > cost_r else ""
    print(f"  {label:40s} | n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | net={net:+.4f} | MFE={s['mfe']:.3f} | SL={s['sl_pct']:.0%} TO={s['to_pct']:.0%}{sig}")

COST_20P = 1.2 / 20.0  # 0.06R at 20-pip stops

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: STRUCTURAL STOP REQUIREMENT (by risk distance)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: BY STRUCTURAL RISK DISTANCE")
print("─"*70)

# Group by risk_pips from the shadow trade
groups_by_dist = {
    "<5 pips": [m for m in matched if m["risk_pips"] > 0 and m["risk_pips"] < 5],
    "5-10 pips": [m for m in matched if 5 <= m["risk_pips"] < 10],
    "10-15 pips": [m for m in matched if 10 <= m["risk_pips"] < 15],
    "15-20 pips": [m for m in matched if 15 <= m["risk_pips"] < 20],
    "20-30 pips": [m for m in matched if 20 <= m["risk_pips"] < 30],
    "30+ pips": [m for m in matched if m["risk_pips"] >= 30],
    "Unknown (0)": [m for m in matched if m["risk_pips"] == 0],
}

print(f"\n  At SL=0.5R, no TP, 60 bars (AR4 best config):")
print(f"  Cost at 20p: {COST_20P:.4f}R")
for label, items in groups_by_dist.items():
    if items:
        s = group_stats(items, 60, 0.5)
        # Cost varies by actual risk distance
        if items[0]["risk_pips"] > 0:
            actual_cost = 1.2 / max(items[0]["risk_pips"], 1)
        else:
            actual_cost = COST_20P
        fmt(s, label, actual_cost)

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: ALL MATCHED (reference)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: OVERALL (all matched, SL=0.5R)")
print("─"*70)

s_all = group_stats(matched, 60, 0.5)
fmt(s_all, "All matched (SL=0.5R, no TP)", COST_20P)

# Also test different SL levels
for sl in [0.25, 0.5, 0.75, 1.0, 1.5]:
    s = group_stats(matched, 60, sl)
    fmt(s, f"SL={sl}R, no TP, 60 bars", COST_20P)

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: OPPORTUNITY QUALITY x GEOMETRY INTERACTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: V3 HORIZON INTERACTION")
print("─"*70)

scalp_matched = [m for m in matched if m["v3"].get("horizon") == "SCALP"]
intra_matched = [m for m in matched if m["v3"].get("horizon") == "INTRADAY"]

print(f"\n  By V3 horizon (SL=0.5R, no TP):")
fmt(group_stats(scalp_matched, 60, 0.5), "SCALP horizon", COST_20P)
fmt(group_stats(intra_matched, 60, 0.5), "INTRADAY horizon", COST_20P)

# Within SCALP, vary geometry
print(f"\n  SCALP horizon with different SL:")
for sl in [0.25, 0.5, 1.0]:
    fmt(group_stats(scalp_matched, 60, sl), f"  SCALP + SL={sl}R", COST_20P)

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: STOP PLACEMENT COMPARISON
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: STOP PLACEMENT COMPARISON")
print("─"*70)

print(f"\n  Fixed SL values (all matched, 60 bars, no TP):")
print(f"  {'SL Level':<15s} | {'Raw EV':>7s} | {'@10p net':>8s} | {'@15p net':>8s} | {'@20p net':>8s} | {'@30p net':>8s}")
print(f"  {'-'*15}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

for sl in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
    s = group_stats(matched, 60, sl)
    if s:
        nets = [s["ev"] - 1.2/stop for stop in [10, 15, 20, 30]]
        print(f"  SL={sl:<5.2f}R     | {s['ev']:>+6.4f} | {nets[0]:>+7.4f} | {nets[1]:>+7.4f} | {nets[2]:>+7.4f} | {nets[3]:>+7.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: EXIT DISTRIBUTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: EXIT DISTRIBUTION (SL=0.5R, no TP, 60 bars)")
print("─"*70)

all_results = []
for item in matched:
    r, _ = simulate(item["progression"], 60, 0.5)
    all_results.append(r)

if all_results:
    buckets = [
        ("Full SL (-0.5R)", sum(1 for r in all_results if r <= -0.49)),
        ("Small loss (-0.5 to -0.1R)", sum(1 for r in all_results if -0.49 < r < -0.1)),
        ("Near zero (-0.1 to +0.1R)", sum(1 for r in all_results if -0.1 <= r <= 0.1)),
        ("Small win (+0.1 to +0.5R)", sum(1 for r in all_results if 0.1 < r <= 0.5)),
        ("Good win (+0.5 to +1.0R)", sum(1 for r in all_results if 0.5 < r <= 1.0)),
        ("Runner (>+1.0R)", sum(1 for r in all_results if r > 1.0)),
    ]
    n = len(all_results)
    print(f"\n  Outcome distribution (n={n}):")
    for label, count in buckets:
        print(f"    {label:35s}: {count:4d} ({count/n*100:.1f}%)")

    # Is expectancy from high WR or asymmetric winners?
    avg_win = sum(r for r in all_results if r > 0) / max(sum(1 for r in all_results if r > 0), 1)
    avg_loss = sum(r for r in all_results if r < 0) / max(sum(1 for r in all_results if r < 0), 1)
    print(f"\n  Avg win: {avg_win:+.4f}R")
    print(f"  Avg loss: {avg_loss:+.4f}R")
    print(f"  Win/Loss ratio: {abs(avg_win/avg_loss) if avg_loss != 0 else 0:.2f}")

    runners = [r for r in all_results if r > 0.5]
    if runners:
        print(f"\n  Runners (>0.5R): {len(runners)} ({len(runners)/n*100:.1f}%)")
        print(f"  Runner contribution: {sum(runners):+.3f}R total ({sum(runners)/sum(all_results)*100:.0f}% of total EV)" if sum(all_results) != 0 else "")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR5 VERDICT")
print("="*70)

# Compare: does structural distance matter?
small_geom = [m for m in matched if 0 < m["risk_pips"] < 10]
large_geom = [m for m in matched if m["risk_pips"] >= 10]
unknown_geom = [m for m in matched if m["risk_pips"] == 0]

s_small = group_stats(small_geom, 60, 0.5) if small_geom else None
s_large = group_stats(large_geom, 60, 0.5) if large_geom else None
s_unknown = group_stats(unknown_geom, 60, 0.5) if unknown_geom else None

print(f"\n  Small geometry (<10p): ", end="")
if s_small: print(f"n={s_small['n']} EV={s_small['ev']:+.4f}")
else: print("no data")

print(f"  Large geometry (>=10p): ", end="")
if s_large: print(f"n={s_large['n']} EV={s_large['ev']:+.4f}")
else: print("no data")

print(f"  Unknown geometry (0p): ", end="")
if s_unknown: print(f"n={s_unknown['n']} EV={s_unknown['ev']:+.4f}")
else: print("no data")

# Determine source of edge
if s_all:
    runners_count = sum(1 for r in all_results if r > 0.5)
    runner_pct = runners_count / len(all_results) if all_results else 0

    if runner_pct > 0.05 and avg_win > abs(avg_loss) * 1.5:
        print("\n  Edge source: ASYMMETRIC WINNERS (rare runners dominate)")
        print(f"  {runner_pct:.1%} of trades produce >0.5R, contributing majority of EV")
    elif s_all["wr"] > 0.55:
        print("\n  Edge source: HIGH WIN RATE (consistent small wins)")
    else:
        print("\n  Edge source: MIXED (moderate WR + moderate asymmetry)")

    # Final classification
    if s_large and s_small:
        if s_large["ev"] > s_small["ev"] + 0.02:
            print("\n  VERDICT: A) Structural geometry IS the missing component")
            print(f"    Large geometry EV ({s_large['ev']:+.4f}) > Small ({s_small['ev']:+.4f})")
        elif abs(s_large["ev"] - s_small["ev"]) < 0.02:
            print("\n  VERDICT: C) Positive EV from wider risk-distance only (cost reduction)")
            print("    Geometry size doesn't change raw EV — only cost impact changes")
        else:
            print("\n  VERDICT: B) Edge exists but requires specific conditions")
    elif s_unknown and s_unknown["n"] > 50:
        # Most records have unknown risk_pips (0) — likely pre-configured shadow trades
        print(f"\n  Most records have risk_pips=0 (n={s_unknown['n']})")
        print("  Cannot separate structural quality from geometry — all use same shadow trade SL")
        if s_all["ev"] > 0 and s_all["ci_high"] > COST_20P:
            print("\n  VERDICT: B) Edge exists but signal operates through cost reduction")
            print(f"    Raw EV +{s_all['ev']:.4f} > cost at 20p ({COST_20P:.4f})")
            print("    The edge IS the combination: correct direction + affordable cost structure")
        else:
            print("\n  VERDICT: D) More data required")
            print("    Need trades with explicit structural stop distances to validate")

print()
