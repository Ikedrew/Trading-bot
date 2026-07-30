"""AR6 — Runner Prediction and Expansion Condition Analysis."""
import json, math
from pathlib import Path
from collections import Counter

# Load matched data (same as AR5)
exec_dir = Path("logs/v3_shadow/execution_assessment")
shadow_dir = Path("logs/shadow_trades")

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
                    except: pass

# Load shadow trades with progression
shadow_by_key = {}
if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN": continue
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
                                    if eid: shadow_by_key[eid] = r
                                    if sym and ts: shadow_by_key[f"{sym}_{int(ts)}"] = r
                        except: pass

# Match
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
        if prog:
            bar_rs = [float(b.get("r", 0)) for b in prog]
            mfe = max(bar_rs) if bar_rs else 0
            matched.append({"v3": rec, "progression": prog, "trade": trade, "mfe": mfe,
                           "result_r": rec["_outcome"]["result_r"]})

print("="*70)
print("AR6 — RUNNER PREDICTION AND EXPANSION CONDITION ANALYSIS")
print("="*70)
print(f"\nMatched records: {len(matched)}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: RUNNER DEFINITION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: RUNNER CATEGORIES")
print("─"*70)

cat_a = [m for m in matched if m["mfe"] < 0.5]     # No expansion
cat_b = [m for m in matched if 0.5 <= m["mfe"] < 2.0]  # Moderate
cat_c = [m for m in matched if m["mfe"] >= 2.0]    # Runner

n = len(matched)
print(f"\n  Category A (MFE < 0.5R — no expansion):   {len(cat_a):4d} ({len(cat_a)/n*100:.1f}%)")
print(f"  Category B (MFE 0.5-2.0R — moderate):     {len(cat_b):4d} ({len(cat_b)/n*100:.1f}%)")
print(f"  Category C (MFE >= 2.0R — runner):         {len(cat_c):4d} ({len(cat_c)/n*100:.1f}%)")

# EV contribution per category
for label, cat in [("A (no expansion)", cat_a), ("B (moderate)", cat_b), ("C (runner)", cat_c)]:
    if cat:
        results = [m["result_r"] for m in cat]
        ev = sum(results)/len(results)
        total_contrib = sum(results)
        overall_total = sum(m["result_r"] for m in matched)
        pct_contrib = total_contrib/overall_total*100 if overall_total != 0 else 0
        print(f"\n  Category {label}:")
        print(f"    n={len(cat)} | EV={ev:+.4f}R | Total contribution: {total_contrib:+.3f}R ({pct_contrib:.0f}% of total)")
        mfes = [m["mfe"] for m in cat]
        print(f"    Mean MFE: {sum(mfes)/len(mfes):.3f}R | Max MFE: {max(mfes):.3f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: PRE-ENTRY CHARACTERISTICS (Runner vs Non-Runner)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: PRE-ENTRY CHARACTERISTICS")
print("─"*70)

# Define runners as MFE >= 0.5R (includes cat B + C for larger sample)
runners = [m for m in matched if m["mfe"] >= 0.5]
non_runners = [m for m in matched if m["mfe"] < 0.5]

print(f"\n  Runners (MFE >= 0.5R): {len(runners)} ({len(runners)/n*100:.1f}%)")
print(f"  Non-runners (MFE < 0.5R): {len(non_runners)} ({len(non_runners)/n*100:.1f}%)")

# Compare features
features = [
    ("horizon", "horizon"),
    ("direction", "direction"),
    ("risk_state", "risk_state"),
]

print(f"\n  Feature comparison:")
for label, field in features:
    runner_dist = Counter(m["v3"].get(field, "") for m in runners)
    non_dist = Counter(m["v3"].get(field, "") for m in non_runners)
    print(f"\n    {label}:")
    all_vals = set(list(runner_dist.keys()) + list(non_dist.keys()))
    for val in sorted(all_vals):
        r_pct = runner_dist.get(val, 0)/len(runners)*100 if runners else 0
        nr_pct = non_dist.get(val, 0)/len(non_runners)*100 if non_runners else 0
        diff = r_pct - nr_pct
        marker = " <<<" if abs(diff) > 10 else ""
        print(f"      {val:25s}: runners={r_pct:5.1f}% | non-runners={nr_pct:5.1f}% | diff={diff:+.1f}%{marker}")

# Shadow trade characteristics
print(f"\n  Shadow trade characteristics:")
runner_risk = [m["trade"].get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) for m in runners]
non_risk = [m["trade"].get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) for m in non_runners]
runner_risk_valid = [r for r in runner_risk if r > 0]
non_risk_valid = [r for r in non_risk if r > 0]

if runner_risk_valid and non_risk_valid:
    print(f"    Avg risk distance: runners={sum(runner_risk_valid)/len(runner_risk_valid):.1f}p | non-runners={sum(non_risk_valid)/len(non_risk_valid):.1f}p")

# Symbol distribution
runner_syms = Counter(m["v3"].get("symbol", "") for m in runners)
non_syms = Counter(m["v3"].get("symbol", "") for m in non_runners)
print(f"\n    Symbol distribution (runners): {dict(runner_syms.most_common(5))}")
print(f"    Symbol distribution (non-runners): {dict(non_syms.most_common(5))}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: RUNNER RATE BY CONDITION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: RUNNER RATE BY CONDITION")
print("─"*70)

baseline_rate = len(runners) / n * 100

def runner_rate(subset):
    if not subset: return 0, 0
    r = sum(1 for m in subset if m["mfe"] >= 0.5)
    return r, r/len(subset)*100

conditions = [
    ("SCALP horizon", [m for m in matched if m["v3"].get("horizon") == "SCALP"]),
    ("INTRADAY horizon", [m for m in matched if m["v3"].get("horizon") == "INTRADAY"]),
    ("BULLISH direction", [m for m in matched if m["v3"].get("direction") == "BULLISH"]),
    ("BEARISH direction", [m for m in matched if m["v3"].get("direction") == "BEARISH"]),
    ("Risk <5 pips", [m for m in matched if 0 < m["trade"].get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) < 5]),
    ("Risk 5-10 pips", [m for m in matched if 5 <= m["trade"].get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) < 10]),
    ("Risk >=10 pips", [m for m in matched if m["trade"].get("decision_snapshot", {}).get("risk_config_snapshot", {}).get("risk_pips", 0) >= 10]),
]

# Add symbol conditions
for sym in ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]:
    subset = [m for m in matched if m["v3"].get("symbol") == sym]
    if len(subset) >= 5:
        conditions.append((f"Symbol: {sym}", subset))

print(f"\n  Baseline runner rate: {baseline_rate:.1f}% (n={n})")
print(f"\n  {'Condition':<30s} | {'n':>4s} | {'Runners':>7s} | {'Rate':>6s} | {'vs base':>7s}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}")

for label, subset in conditions:
    if subset:
        count, rate = runner_rate(subset)
        diff = rate - baseline_rate
        marker = " **" if diff > 5 else ""
        print(f"  {label:<30s} | {len(subset):>4d} | {count:>7d} | {rate:>5.1f}% | {diff:>+5.1f}%{marker}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: RUNNER PREDICTIVE RANKING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: FEATURE RANKING BY RUNNER RATE IMPROVEMENT")
print("─"*70)

rankings = []
for label, subset in conditions:
    if subset and len(subset) >= 5:
        count, rate = runner_rate(subset)
        diff = rate - baseline_rate
        rankings.append((label, len(subset), count, rate, diff))

rankings.sort(key=lambda x: x[4], reverse=True)
print(f"\n  {'Feature':<30s} | {'n':>4s} | {'#Run':>4s} | {'Rate':>6s} | {'Lift':>6s} | {'Useful?':>7s}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*4}-+-{'-'*6}-+-{'-'*6}-+-{'-'*7}")
for label, n_sub, count, rate, diff in rankings:
    useful = "YES" if diff > 3 and count >= 3 else "MAYBE" if diff > 0 else "NO"
    print(f"  {label:<30s} | {n_sub:>4d} | {count:>4d} | {rate:>5.1f}% | {diff:>+5.1f}% | {useful}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: FALSE RUNNER CONDITIONS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: FALSE EXPANSION (high MFE but negative result)")
print("─"*70)

# Trades with decent MFE but negative final R
false_runners = [m for m in matched if m["mfe"] >= 0.25 and m["result_r"] < 0]
true_runners = [m for m in matched if m["mfe"] >= 0.5 and m["result_r"] > 0]

print(f"\n  False runners (MFE>=0.25 but R<0): {len(false_runners)} ({len(false_runners)/n*100:.1f}%)")
print(f"  True runners (MFE>=0.5 and R>0): {len(true_runners)} ({len(true_runners)/n*100:.1f}%)")

if false_runners and true_runners:
    # Compare characteristics
    fr_syms = Counter(m["v3"].get("symbol","") for m in false_runners)
    tr_syms = Counter(m["v3"].get("symbol","") for m in true_runners)
    print(f"\n  False runner symbols: {dict(fr_syms.most_common(3))}")
    print(f"  True runner symbols: {dict(tr_syms.most_common(3))}")

    fr_hor = Counter(m["v3"].get("horizon","") for m in false_runners)
    tr_hor = Counter(m["v3"].get("horizon","") for m in true_runners)
    print(f"  False runner horizons: {dict(fr_hor)}")
    print(f"  True runner horizons: {dict(tr_hor)}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: ROBUSTNESS — EV WITH/WITHOUT RUNNERS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 6: ROBUSTNESS TEST")
print("─"*70)

all_results = [m["result_r"] for m in matched]
without_runners = [m["result_r"] for m in non_runners]
runner_results = [m["result_r"] for m in runners]

print(f"\n  All trades: n={len(all_results)} EV={sum(all_results)/len(all_results):+.4f}R")
print(f"  Without runners (MFE<0.5R): n={len(without_runners)} EV={sum(without_runners)/len(without_runners):+.4f}R")
if runner_results:
    print(f"  Runners only (MFE>=0.5R): n={len(runner_results)} EV={sum(runner_results)/len(runner_results):+.4f}R")

# Can we improve runner concentration?
if rankings:
    best_cond_label = rankings[0][0]
    best_subset = None
    for label, subset in conditions:
        if label == best_cond_label:
            best_subset = subset
            break
    if best_subset:
        best_results = [m["result_r"] for m in best_subset]
        best_runners = sum(1 for m in best_subset if m["mfe"] >= 0.5)
        print(f"\n  Best runner condition ({best_cond_label}):")
        print(f"    n={len(best_subset)} | Runner rate: {best_runners/len(best_subset)*100:.1f}% | EV={sum(best_results)/len(best_results):+.4f}R")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("AR6 VERDICT")
print("="*70)

# Can any condition reliably increase runner rate?
best_lift = rankings[0][4] if rankings else 0
best_rate = rankings[0][3] if rankings else 0

if best_lift > 10 and rankings[0][2] >= 5:
    print(f"\n  A) V3 identifies expansion conditions")
    print(f"     Best: {rankings[0][0]} → runner rate {best_rate:.1f}% (baseline {baseline_rate:.1f}%)")
elif best_lift > 5:
    print(f"\n  B) Runner conditions exist but require more data")
    print(f"     Best candidate: {rankings[0][0]} → +{best_lift:.1f}% lift")
    print(f"     Need larger sample to confirm")
elif len(matched) < 50:
    print(f"\n  D) More data required (only {len(matched)} matched records)")
else:
    print(f"\n  C) Runners cannot be predicted from current V3 features")
    print(f"     Best lift: {best_lift:+.1f}% (insufficient)")
    print(f"     Runner rate appears random across all tested conditions")

# Summary
print(f"\n  Runner rate: {baseline_rate:.1f}% (baseline)")
if rankings:
    print(f"  Best predictor: {rankings[0][0]} ({rankings[0][3]:.1f}%)")
    print(f"  Worst predictor: {rankings[-1][0]} ({rankings[-1][3]:.1f}%)")

without_ev = sum(without_runners)/len(without_runners) if without_runners else 0
print(f"\n  EV without runners: {without_ev:+.4f}R")
print(f"  System viability WITHOUT runner prediction: {'MARGINAL' if without_ev > -0.05 else 'NOT VIABLE'}")
print()
