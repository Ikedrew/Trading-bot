"""AR4 — Horizon and Risk Geometry Transfer Analysis.

Tests whether the WEAK+INTERESTING signal survives when expressed through
different holding durations and risk geometries using shadow trade progression data.
"""
import json, math
from pathlib import Path
from collections import Counter

# ═══════════════════════════════════════════════════════════════
# LOAD EXECUTION ASSESSMENTS + SHADOW TRADE PROGRESSIONS
# ═══════════════════════════════════════════════════════════════

exec_dir = Path("logs/v3_shadow/execution_assessment")
shadow_dir = Path("logs/shadow_trades")

# Load V3 execution assessments (linked)
exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("_outcome", {}).get("result_r") is not None:
                            if r.get("execution_state") != "NOT_EXECUTABLE":
                                exec_records.append(r)
                    except:
                        pass

# Load shadow trades with progression data
shadow_trades = []
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
                                if prog and len(prog) >= 3:
                                    shadow_trades.append(r)
                        except:
                            pass

# Index shadow trades by entity_id and timestamp
shadow_by_entity = {}
shadow_by_sym_time = {}
for t in shadow_trades:
    eid = t.get("identity", {}).get("entity_id", "")
    sym = t.get("identity", {}).get("symbol", "")
    ts = t.get("decision_snapshot", {}).get("timestamp_decision_utc", 0)
    if eid:
        shadow_by_entity[eid] = t
    if sym and ts:
        key = f"{sym}_{int(ts)}"
        shadow_by_sym_time[key] = t

# Match V3 WEAK+INTERESTING records to shadow trades with progressions
target_records = [r for r in exec_records
                  if r.get("entry_state") == "WEAK_ENTRY_CONFIRMATION"
                  and r.get("opportunity_state") == "INTERESTING_CONTEXT"
                  and r.get("horizon") == "SCALP"]

matched = []
for rec in target_records:
    sym = rec.get("symbol", "")
    ts = rec.get("timestamp_utc", 0)
    key = f"{sym}_{int(ts)}"
    trade = shadow_by_entity.get(key) or shadow_by_sym_time.get(key)
    if not trade:
        # Try timestamp proximity
        for delta in range(-300, 301, 300):
            k2 = f"{sym}_{int(ts + delta)}"
            trade = shadow_by_sym_time.get(k2)
            if trade:
                break
    if trade:
        prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
        if prog:
            matched.append({"v3": rec, "progression": prog, "trade": trade})

print("=" * 70)
print("AR4 — HORIZON AND RISK GEOMETRY TRANSFER ANALYSIS")
print("=" * 70)
print(f"\nTarget: SCALP + WEAK + INTERESTING records")
print(f"V3 records: {len(target_records)}")
print(f"Matched to shadow progressions: {len(matched)}")

if len(matched) < 10:
    print("\nINSUFFICIENT PROGRESSION DATA for simulation.")
    print("Falling back to outcome-based analysis only.")
    print()

# ═══════════════════════════════════════════════════════════════
# SIMULATION: RE-SIMULATE AT DIFFERENT GEOMETRIES
# ═══════════════════════════════════════════════════════════════

def simulate_exit(progression, max_bars, sl_r, tp_r):
    """Simulate trade with given SL/TP/duration on bar-by-bar data."""
    for bar_data in progression[:max_bars]:
        bar_r = float(bar_data.get("r", 0))
        if bar_r <= -sl_r:
            return -sl_r, "stop_loss"
        if bar_r >= tp_r:
            return tp_r, "take_profit"
    # Timeout at last bar
    if progression[:max_bars]:
        last_r = float(progression[min(max_bars-1, len(progression)-1)].get("r", 0))
        return last_r, "timeout"
    return 0.0, "no_data"

def run_geometry(records_with_prog, max_bars, sl_r, tp_r, label):
    """Run simulation across all matched records."""
    results = []
    exits = Counter()
    for item in records_with_prog:
        r, reason = simulate_exit(item["progression"], max_bars, sl_r, tp_r)
        results.append(r)
        exits[reason] += 1
    if not results:
        return None
    n = len(results)
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r-ev)**2 for r in results)/max(n-1,1))
    se = std/math.sqrt(n)
    mfe_vals = []
    for item in records_with_prog:
        prog = item["progression"][:max_bars]
        if prog:
            mfe_vals.append(max(float(b.get("r",0)) for b in prog))
    avg_mfe = sum(mfe_vals)/len(mfe_vals) if mfe_vals else 0
    return {"n":n,"wr":wins/n,"ev":ev,"std":std,"ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "avg_mfe":avg_mfe,"exits":dict(exits),"tp_pct":exits.get("take_profit",0)/n,
            "sl_pct":exits.get("stop_loss",0)/n,"to_pct":exits.get("timeout",0)/n}

print("\n" + "─"*70)
print("ANALYSIS 1: HORIZON TRANSFER (duration variation)")
print("─"*70)

if matched:
    # Test different holding periods with same SL (1R) and no TP (unreachable)
    durations = [20, 40, 60, 120, 180, 300]
    print(f"\n  Fixed SL=1.0R, No TP, varying duration:")
    print(f"  {'Duration':<12s} | {'n':>4s} | {'WR':>5s} | {'EV':>7s} | {'MFE':>5s} | {'SL%':>4s} | {'TO%':>4s} | {'CI':>16s}")
    print(f"  {'-'*12}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*5}-+-{'-'*4}-+-{'-'*4}-+-{'-'*16}")
    for dur in durations:
        s = run_geometry(matched, dur, 1.0, 99.0, f"{dur} bars")
        if s:
            print(f"  {dur:>4d} bars    | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+6.4f} | {s['avg_mfe']:.3f} | {s['sl_pct']:.0%} | {s['to_pct']:.0%} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

    print(f"\n" + "─"*70)
    print("ANALYSIS 2: RISK GEOMETRY VARIATION")
    print("─"*70)

    # Test different SL/TP combinations at 60 bars
    geometries = [
        ("Current (SL=1R, no TP)", 60, 1.0, 99.0),
        ("Tight SL (0.5R, no TP)", 60, 0.5, 99.0),
        ("Wide SL (1.5R, no TP)", 60, 1.5, 99.0),
        ("Wide SL (2.0R, no TP)", 60, 2.0, 99.0),
        ("TP 0.5R, SL 1R", 60, 1.0, 0.5),
        ("TP 1.0R, SL 1R", 60, 1.0, 1.0),
        ("TP 1.5R, SL 1R", 60, 1.0, 1.5),
        ("TP 2.0R, SL 1R", 60, 1.0, 2.0),
        ("TP 0.5R, SL 0.5R (1:1)", 60, 0.5, 0.5),
        ("TP 1.0R, SL 2.0R (wide)", 120, 2.0, 1.0),
        ("INTRA: SL 1R, TP 3R, 120 bars", 120, 1.0, 3.0),
        ("INTRA: SL 1.5R, TP 3R, 180 bars", 180, 1.5, 3.0),
    ]

    print(f"\n  {'Geometry':<35s} | {'n':>4s} | {'WR':>5s} | {'EV':>7s} | {'TP%':>4s} | {'SL%':>4s} | {'TO%':>4s} | {'CI':>16s}")
    print(f"  {'-'*35}-+-{'-'*4}-+-{'-'*5}-+-{'-'*7}-+-{'-'*4}-+-{'-'*4}-+-{'-'*4}-+-{'-'*16}")

    for label, dur, sl, tp in geometries:
        s = run_geometry(matched, dur, sl, tp, label)
        if s:
            print(f"  {label:<35s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+6.4f} | {s['tp_pct']:.0%} | {s['sl_pct']:.0%} | {s['to_pct']:.0%} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

    print(f"\n" + "─"*70)
    print("ANALYSIS 3: MOVE CAPTURE (how far does price go?)")
    print("─"*70)

    # Analyse MFE distribution within matched set
    all_mfe = []
    all_mae = []
    for item in matched:
        prog = item["progression"]
        if prog:
            bar_rs = [float(b.get("r",0)) for b in prog]
            all_mfe.append(max(bar_rs) if bar_rs else 0)
            all_mae.append(min(bar_rs) if bar_rs else 0)

    if all_mfe:
        print(f"\n  MFE distribution (SCALP+WEAK+INTERESTING, n={len(all_mfe)}):")
        mfe_buckets = [
            ("< 0.25R", sum(1 for m in all_mfe if m < 0.25)),
            ("0.25-0.5R", sum(1 for m in all_mfe if 0.25 <= m < 0.5)),
            ("0.5-1.0R", sum(1 for m in all_mfe if 0.5 <= m < 1.0)),
            ("1.0-2.0R", sum(1 for m in all_mfe if 1.0 <= m < 2.0)),
            (">= 2.0R", sum(1 for m in all_mfe if m >= 2.0)),
        ]
        for label, count in mfe_buckets:
            print(f"    {label:15s}: {count:4d} ({count/len(all_mfe)*100:.1f}%)")
        print(f"    Mean MFE: {sum(all_mfe)/len(all_mfe):.3f}R")
        print(f"    Median MFE: {sorted(all_mfe)[len(all_mfe)//2]:.3f}R")

    if all_mae:
        print(f"\n  MAE distribution:")
        mae_buckets = [
            ("> -0.25R (small)", sum(1 for m in all_mae if m > -0.25)),
            ("-0.25 to -0.5R", sum(1 for m in all_mae if -0.5 <= m < -0.25)),
            ("-0.5 to -1.0R", sum(1 for m in all_mae if -1.0 <= m < -0.5)),
            ("<= -1.0R (large)", sum(1 for m in all_mae if m <= -1.0)),
        ]
        for label, count in mae_buckets:
            print(f"    {label:20s}: {count:4d} ({count/len(all_mae)*100:.1f}%)")
        print(f"    Mean MAE: {sum(all_mae)/len(all_mae):.3f}R")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 4: COST-ADJUSTED GEOMETRY SEARCH
    # ═══════════════════════════════════════════════════════════════
    print(f"\n" + "─"*70)
    print("ANALYSIS 4: COST-ADJUSTED GEOMETRY (best configurations)")
    print("─"*70)

    # For each geometry, calculate cost-adjusted EV at different stop sizes
    TOTAL_COST_PIPS = 1.2
    print(f"\n  Spread+slippage: {TOTAL_COST_PIPS} pips")
    print(f"\n  {'Geometry':<35s} | {'EV':>7s} | {'@3.5p':>7s} | {'@10p':>7s} | {'@15p':>7s} | {'@20p':>7s} | {'Best?':>5s}")
    print(f"  {'-'*35}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*5}")

    best_net = -999
    best_label = ""
    for label, dur, sl, tp in geometries:
        s = run_geometry(matched, dur, sl, tp, label)
        if s and s["n"] >= 10:
            ev = s["ev"]
            nets = []
            for stop_pips in [3.5, 10.0, 15.0, 20.0]:
                cost_r = TOTAL_COST_PIPS / stop_pips
                net = ev - cost_r
                nets.append(net)
                if net > best_net:
                    best_net = net
                    best_label = f"{label} @{stop_pips}p"
            print(f"  {label:<35s} | {ev:>+6.4f} | {nets[0]:>+6.4f} | {nets[1]:>+6.4f} | {nets[2]:>+6.4f} | {nets[3]:>+6.4f} | {'***' if max(nets)>0 else ''}")

    print(f"\n  Best configuration: {best_label} → net EV = {best_net:+.4f}R")

else:
    # Fallback: use outcome data only
    print("\n  No progression data matched. Using outcome R values only.")
    print("  Cannot simulate alternative geometries without bar-by-bar data.")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: COST SENSITIVITY
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "─"*70)
print("ANALYSIS 5: COST SENSITIVITY — BREAKEVEN REQUIREMENTS")
print("─"*70)

# What cost level makes the best raw signal break even?
best_raw_ev = 0.043  # From AR3: SCALP+WEAK+INTERESTING
print(f"\n  Best raw EV: +{best_raw_ev:.4f}R")
print(f"\n  Breakeven spread/risk ratios:")
print(f"    At {best_raw_ev:.4f}R raw EV, breakeven when spread/risk <= {best_raw_ev:.1%}")
print(f"    With 1.2 pip total cost: need stop >= {TOTAL_COST_PIPS/best_raw_ev:.1f} pips")
print(f"    With 0.8 pip total cost (ECN): need stop >= {0.8/best_raw_ev:.1f} pips")
print(f"    With 0.5 pip total cost (institutional): need stop >= {0.5/best_raw_ev:.1f} pips")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "="*70)
print("AR4 VERDICT")
print("="*70)

if matched and best_net > 0:
    print(f"\n  A) Higher timeframe expression preserves the edge")
    print(f"     Best: {best_label} → net EV = {best_net:+.4f}R")
elif matched and best_net > -0.02:
    print(f"\n  B) Edge exists but only under specific geometry")
    print(f"     Closest to breakeven: {best_label} → {best_net:+.4f}R")
    print(f"     Gap to breakeven: {abs(best_net):.4f}R")
elif not matched or len(matched) < 10:
    print(f"\n  D) More data required")
    print(f"     Only {len(matched)} records with progression data matched.")
    print(f"     Need shadow trades from V3 active period with trade_state_progression.")
else:
    print(f"\n  C) Signal too weak for any tested execution model")
    print(f"     Best net: {best_net:+.4f}R")
    print(f"     Required for breakeven: > 0.00R")

print(f"\n  V3 opportunity detection should remain the foundation:")
print(f"    - Directional information exists (+0.04R raw)")
print(f"    - Timing decay identified (WEAK > VALID)")
print(f"    - Cost structure is the binding constraint")
print(f"    - Need: either stronger signal, lower costs, or different market")
print()
