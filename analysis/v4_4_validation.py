"""V4.4 — Currency Strength Validation and Sample Expansion.

Validates whether the V4.3 finding (WEAK+INTERESTING+3agree = +0.105R, n=25)
represents a stable predictive relationship or small-sample artefact.

Approach:
1. Load ALL V3 opportunity + execution assessments (not just execution_assessment)
2. Link to shadow trade outcomes via timestamp+symbol matching
3. Derive currency strength from ALL concurrent shadow trades at each timestamp
4. Reproduce V4.3 result and test with expanded sample
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict
import statistics as st

print("=" * 70)
print("V4.4 — CURRENCY STRENGTH VALIDATION AND SAMPLE EXPANSION")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# LOAD ALL SHADOW TRADES (cross-pair strength source)
# ═══════════════════════════════════════════════════════════════

PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
}

shadow_dir = Path("logs/shadow_trades")
trades_by_time = defaultdict(list)
all_trades = []
seen_trades = set()

if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("schema_version") != "shadow_trades_v2":
                            continue
                        identity = r.get("identity", {})
                        snap = r.get("decision_snapshot", {})
                        outcome = r.get("simulated_outcome", {})
                        sym = identity.get("symbol", "")
                        ts = snap.get("timestamp_decision_utc", 0)
                        result_r = outcome.get("pnl_r_multiple")
                        direction = snap.get("direction", "")
                        key = (sym, int(ts))
                        if sym and ts and result_r is not None and direction and key not in seen_trades:
                            seen_trades.add(key)
                            entry = {
                                "symbol": sym, "timestamp": ts,
                                "direction": direction, "result_r": result_r,
                                "mfe_r": outcome.get("mfe_r", 0),
                                "mae_r": outcome.get("mae_r", 0),
                            }
                            all_trades.append(entry)
                            trades_by_time[int(ts)].append(entry)
                    except:
                        pass

print(f"\nShadow trades loaded: {len(all_trades)}")
print(f"Unique timestamps: {len(trades_by_time)}")

# ═══════════════════════════════════════════════════════════════
# LOAD V3 PIPELINE DATA (ALL stages for maximum sample)
# ═══════════════════════════════════════════════════════════════

# Load execution assessments (primary: has entry_state + opportunity_state + _outcome)
exec_dir = Path("logs/v3_shadow/execution_assessment")
exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        exec_records.append(r)
                except:
                    pass

# Load opportunity assessments (for records not in execution)
opp_dir = Path("logs/v3_shadow/opportunity_assessment")
opp_records = []
if opp_dir.exists():
    for f in opp_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    opp_records.append(r)
                except:
                    pass

# Load entry assessments (for linking entry_state to opportunities)
entry_dir = Path("logs/v3_shadow/entry_assessment")
entry_records = []
if entry_dir.exists():
    for f in entry_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    entry_records.append(r)
                except:
                    pass

print(f"Execution assessments: {len(exec_records)}")
print(f"Opportunity assessments: {len(opp_records)}")
print(f"Entry assessments: {len(entry_records)}")

# ═══════════════════════════════════════════════════════════════
# BUILD EXPANDED DATASET
# Link opportunity+entry states to shadow trade outcomes
# ═══════════════════════════════════════════════════════════════

# Index opportunity assessments by (symbol, timestamp)
opp_index = {}
for rec in opp_records:
    key = (rec.get("symbol", ""), int(rec.get("timestamp_utc", 0)))
    opp_index[key] = rec

# Index entry assessments by (symbol, timestamp)
entry_index = {}
for rec in entry_records:
    key = (rec.get("symbol", ""), int(rec.get("timestamp_utc", 0)))
    entry_index[key] = rec

# Build combined dataset:
# Primary: execution_assessment records (already have everything)
# Secondary: shadow trades linked to opp+entry assessments by (symbol, timestamp)

combined_records = []
seen_combined = set()

# 1. Execution assessment records (already complete)
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = int(rec.get("timestamp_utc", 0))
    key = (sym, ts)
    if key in seen_combined:
        continue
    seen_combined.add(key)
    combined_records.append({
        "symbol": sym,
        "timestamp": ts,
        "direction": rec.get("direction", ""),
        "entry_state": rec.get("entry_state", ""),
        "opportunity_state": rec.get("opportunity_state", ""),
        "horizon": rec.get("horizon", ""),
        "result_r": rec["_outcome"]["result_r"],
        "mfe_r": rec["_outcome"].get("mfe_r", 0),
        "mae_r": rec["_outcome"].get("mae_r", 0),
        "source": "execution_assessment",
    })

# 2. Link shadow trades to opp+entry assessments (expand sample)
for trade in all_trades:
    sym = trade["symbol"]
    ts = int(trade["timestamp"])
    key = (sym, ts)
    if key in seen_combined:
        continue
    
    # Find matching opportunity assessment (exact or ±300s)
    opp = opp_index.get(key)
    entry = entry_index.get(key)
    
    if not opp:
        for delta in [300, -300, 600, -600]:
            opp = opp_index.get((sym, ts + delta))
            if opp:
                break
    if not entry:
        for delta in [300, -300, 600, -600]:
            entry = entry_index.get((sym, ts + delta))
            if entry:
                break
    
    opp_state = opp.get("assessment_state", "") if opp else ""
    entry_state = entry.get("entry_state", "") if entry else ""
    direction = trade["direction"]
    
    # Map to standard labels
    if "INTERESTING" in opp_state:
        opp_state = "INTERESTING_CONTEXT"
    elif "HIGH" in opp_state:
        opp_state = "HIGH_QUALITY_CONTEXT"
    
    if "WEAK" in entry_state:
        entry_state = "WEAK_ENTRY_CONFIRMATION"
    elif "VALID" in entry_state:
        entry_state = "VALID_ENTRY_CONFIRMATION"
    
    seen_combined.add(key)
    combined_records.append({
        "symbol": sym,
        "timestamp": ts,
        "direction": direction,
        "entry_state": entry_state,
        "opportunity_state": opp_state,
        "horizon": entry.get("horizon", "") if entry else "",
        "result_r": trade["result_r"],
        "mfe_r": trade["mfe_r"],
        "mae_r": trade["mae_r"],
        "source": "shadow_linked",
    })

print(f"\nCombined dataset: {len(combined_records)} records")
print(f"  From execution_assessment: {sum(1 for r in combined_records if r['source']=='execution_assessment')}")
print(f"  From shadow_linked: {sum(1 for r in combined_records if r['source']=='shadow_linked')}")

# ═══════════════════════════════════════════════════════════════
# COMPUTE CURRENCY STRENGTH FOR EACH RECORD
# ═══════════════════════════════════════════════════════════════

def get_usd_direction(sym, direction):
    if sym not in PAIR_CURRENCIES:
        return None
    base, quote = PAIR_CURRENCIES[sym]
    # Normalize direction
    d = direction.upper()
    if d in ("BUY", "BULLISH", "LONG"):
        d = "BUY"
    elif d in ("SELL", "BEARISH", "SHORT"):
        d = "SELL"
    else:
        return None
    
    if quote == "USD":
        return "USD_WEAK" if d == "BUY" else "USD_STRONG"
    elif base == "USD":
        return "USD_STRONG" if d == "BUY" else "USD_WEAK"
    return None


def compute_currency_strength(record, window=300):
    """Compute cross-pair USD agreement for a record."""
    sym = record["symbol"]
    ts = record["timestamp"]
    direction = record["direction"]
    
    my_usd = get_usd_direction(sym, direction)
    if not my_usd:
        return None, 0, 0
    
    # Gather concurrent trades from OTHER pairs
    others = []
    for delta in range(-window, window + 1, 300):
        check_ts = int(ts) + delta
        for t in trades_by_time.get(check_ts, []):
            if t["symbol"] != sym:
                others.append(t)
    
    # Deduplicate by symbol (keep closest timestamp)
    seen_syms = set()
    unique_others = []
    for t in sorted(others, key=lambda x: abs(x["timestamp"] - ts)):
        if t["symbol"] not in seen_syms:
            seen_syms.add(t["symbol"])
            unique_others.append(t)
    
    if not unique_others:
        return None, 0, 0
    
    usd_strong = sum(1 for t in unique_others
                     if get_usd_direction(t["symbol"], t["direction"]) == "USD_STRONG")
    usd_weak = sum(1 for t in unique_others
                   if get_usd_direction(t["symbol"], t["direction"]) == "USD_WEAK")
    total = usd_strong + usd_weak
    if total == 0:
        return None, 0, 0
    
    if my_usd == "USD_STRONG":
        aligned = usd_strong > usd_weak
        agree_count = usd_strong
    else:
        aligned = usd_weak > usd_strong
        agree_count = usd_weak
    
    return aligned, agree_count, total


# Compute strength for all combined records
enriched = []
for rec in combined_records:
    aligned, agree_count, total_pairs = compute_currency_strength(rec)
    if aligned is not None:
        enriched.append({
            **rec,
            "aligned": aligned,
            "agree_count": agree_count,
            "total_pairs": total_pairs,
        })

print(f"Records with currency context: {len(enriched)}")
print(f"  Aligned: {sum(1 for r in enriched if r['aligned'])}")
print(f"  Opposing: {sum(1 for r in enriched if not r['aligned'])}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def stats(subset):
    if not subset:
        return None
    results = [s["result_r"] for s in subset]
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    mfe_vals = [s["mfe_r"] for s in subset]
    mae_vals = [s["mae_r"] for s in subset]
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "mfe": sum(mfe_vals) / n, "mae": sum(mae_vals) / n,
    }


def print_stats_row(label, s, cost=0.12):
    if s and s["n"] >= 3:
        net = s["ev"] - cost
        print(f"  {label:<45s} | {s['n']:>4d} | {s['wr']:.1%} | "
              f"{s['ev']:>+7.4f} | {net:>+7.4f} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")


# Cost at different stop distances
COST_10P = 1.2 / 10.0  # 0.12R (SCALP)
COST_15P = 1.2 / 15.0  # 0.08R
COST_20P = 1.2 / 20.0  # 0.06R

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: EXPANDED SAMPLE SIZE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: EXPANDED SAMPLE SIZE")
print("─" * 70)

# Target: WEAK + INTERESTING + 3+ agree
target_full = [r for r in enriched
               if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
               and r["opportunity_state"] == "INTERESTING_CONTEXT"
               and r["aligned"] and r["agree_count"] >= 3]

target_weak_int = [r for r in enriched
                   if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
                   and r["opportunity_state"] == "INTERESTING_CONTEXT"]

target_weak = [r for r in enriched
               if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]

print(f"\n  Total enriched records: {len(enriched)}")
print(f"  With WEAK entry: {len(target_weak)}")
print(f"  With WEAK + INTERESTING: {len(target_weak_int)}")
print(f"  With WEAK + INTERESTING + 3+ agree: {len(target_full)}")
print(f"  V4.3 original n: 25")
print(f"  Expansion: {len(target_full)} ({'growth' if len(target_full) > 25 else 'same/smaller'})")

# Show breakdown
print(f"\n  Source breakdown (WEAK+INTERESTING+3+agree):")
from_exec = sum(1 for r in target_full if r["source"] == "execution_assessment")
from_shadow = sum(1 for r in target_full if r["source"] == "shadow_linked")
print(f"    From execution_assessment: {from_exec}")
print(f"    From shadow_linked: {from_shadow}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: REPRODUCE V4.3 EXACTLY + EXPANDED
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: REPRODUCE V4.3 + EXPANDED RESULT")
print("─" * 70)

# Reproduce V4.3 (execution_assessment only)
v43_subset = [r for r in enriched
              if r["source"] == "execution_assessment"
              and r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
              and r["opportunity_state"] == "INTERESTING_CONTEXT"
              and r["aligned"] and r["agree_count"] >= 3]

s_v43 = stats(v43_subset)
s_full = stats(target_full)
s_weak_int = stats(target_weak_int)
s_all = stats(enriched)

print(f"\n  {'Configuration':<45s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@15p':>8s} | {'CI':>18s}")
print(f"  {'-'*45}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*18}")

print_stats_row("All records (baseline)", s_all, COST_15P)
print_stats_row("WEAK + INTERESTING (no filter)", s_weak_int, COST_15P)
print_stats_row("V4.3 reproduction (exec only, 3+agree)", s_v43, COST_15P)
print_stats_row("EXPANDED (all sources, 3+agree)", s_full, COST_15P)

if s_v43 and s_full:
    print(f"\n  V4.3 original:  n={s_v43['n']:3d} | EV={s_v43['ev']:+.4f}")
    print(f"  V4.4 expanded:  n={s_full['n']:3d} | EV={s_full['ev']:+.4f}")
    if s_full["n"] > s_v43["n"]:
        print(f"  Sample growth: +{s_full['n'] - s_v43['n']} records ({s_full['n']/max(s_v43['n'],1):.1f}x)")
    ev_change = s_full["ev"] - s_v43["ev"]
    print(f"  EV change: {ev_change:+.4f}R ({'STABLE' if abs(ev_change) < 0.05 else 'SHIFT'})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: AGREEMENT THRESHOLD VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: AGREEMENT THRESHOLD (WEAK+INTERESTING subset)")
print("─" * 70)

print(f"\n  {'Threshold':<25s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@15p':>8s} | {'@20p':>8s}")
print(f"  {'-'*25}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

for min_agree in [0, 1, 2, 3, 4, 5]:
    if min_agree == 0:
        subset = target_weak_int  # no filter
        label = "No filter (all)"
    else:
        subset = [r for r in target_weak_int if r["aligned"] and r["agree_count"] >= min_agree]
        label = f"{min_agree}+ agreeing pairs"
    
    s = stats(subset)
    if s and s["n"] >= 3:
        net15 = s["ev"] - COST_15P
        net20 = s["ev"] - COST_20P
        print(f"  {label:<25s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net15:>+7.4f} | {net20:>+7.4f}")

# Also show opposing thresholds
print(f"\n  OPPOSING trades (WEAK+INTERESTING):")
for min_oppose in [1, 2, 3]:
    subset = [r for r in target_weak_int if not r["aligned"] and r["agree_count"] >= min_oppose]
    s = stats(subset)
    if s and s["n"] >= 3:
        print(f"    {min_oppose}+ opposing: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: TIME STABILITY (thirds)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: TIME STABILITY")
print("─" * 70)

# Test the full target (WEAK+INTERESTING+aligned) across time thirds
test_set = [r for r in enriched
            if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
            and r["opportunity_state"] == "INTERESTING_CONTEXT"
            and r["aligned"]]

if test_set:
    sorted_set = sorted(test_set, key=lambda r: r["timestamp"])
    third = len(sorted_set) // 3
    
    periods = [
        ("Early period", sorted_set[:third]),
        ("Middle period", sorted_set[third:2*third]),
        ("Recent period", sorted_set[2*third:]),
    ]
    
    print(f"\n  WEAK + INTERESTING + ALIGNED (time thirds):")
    for label, subset in periods:
        s = stats(subset)
        if s and s["n"] >= 3:
            print(f"    {label:<15s}: n={s['n']:3d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# Also test 3+ agree across halves
if target_full:
    sorted_target = sorted(target_full, key=lambda r: r["timestamp"])
    half = len(sorted_target) // 2
    
    print(f"\n  WEAK + INTERESTING + 3+ AGREE (halves):")
    s1 = stats(sorted_target[:half])
    s2 = stats(sorted_target[half:])
    if s1 and s1["n"] >= 3:
        print(f"    First half:  n={s1['n']:3d} | WR={s1['wr']:.1%} | EV={s1['ev']:+.4f}")
    if s2 and s2["n"] >= 3:
        print(f"    Second half: n={s2['n']:3d} | WR={s2['wr']:.1%} | EV={s2['ev']:+.4f}")
    if s1 and s2 and s1["n"] >= 3 and s2["n"] >= 3:
        print(f"    Stability: {'STABLE' if abs(s1['ev']-s2['ev']) < 0.08 else 'VARIABLE'}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: SYMBOL STABILITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: SYMBOL STABILITY")
print("─" * 70)

# Test WEAK+INTERESTING+aligned per symbol
print(f"\n  WEAK + INTERESTING + ALIGNED per symbol:")
print(f"  {'Symbol':<10s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | Effect vs baseline")
print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*20}")

symbols_positive = 0
symbols_total = 0
for sym in sorted(set(r["symbol"] for r in enriched)):
    sym_aligned = [r for r in enriched
                   if r["symbol"] == sym
                   and r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
                   and r["opportunity_state"] == "INTERESTING_CONTEXT"
                   and r["aligned"]]
    sym_baseline = [r for r in enriched
                    if r["symbol"] == sym
                    and r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"
                    and r["opportunity_state"] == "INTERESTING_CONTEXT"]
    
    sa = stats(sym_aligned)
    sb = stats(sym_baseline)
    if sa and sa["n"] >= 3 and sb and sb["n"] >= 3:
        symbols_total += 1
        delta = sa["ev"] - sb["ev"]
        if delta > 0:
            symbols_positive += 1
        print(f"  {sym:<10s} | {sa['n']:>4d} | {sa['wr']:.1%} | {sa['ev']:>+7.4f} | {delta:>+.4f} {'✓' if delta > 0 else '✗'}")

if symbols_total > 0:
    print(f"\n  Symbols with positive alignment effect: {symbols_positive}/{symbols_total}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: TRADE REMOVAL ANALYSIS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 6: WHAT DOES CURRENCY STRENGTH REMOVE?")
print("─" * 70)

# Compare: WEAK+INTERESTING (all) vs WEAK+INTERESTING+aligned
removed = [r for r in target_weak_int if not r["aligned"]]
kept = [r for r in target_weak_int if r["aligned"]]

if removed and target_weak_int:
    removed_results = [r["result_r"] for r in removed]
    kept_results = [r["result_r"] for r in kept]
    
    removed_wins = sum(1 for r in removed_results if r > 0)
    removed_losses = sum(1 for r in removed_results if r <= 0)
    kept_wins = sum(1 for r in kept_results if r > 0)
    kept_losses = sum(1 for r in kept_results if r <= 0)
    
    print(f"\n  WEAK+INTERESTING baseline: {len(target_weak_int)} trades")
    print(f"  After alignment filter: {len(kept)} trades")
    print(f"  Removed: {len(removed)} trades ({len(removed)/len(target_weak_int)*100:.0f}%)")
    print(f"\n  REMOVED trades breakdown:")
    print(f"    Winners removed: {removed_wins} ({removed_wins/max(len(removed),1)*100:.0f}%)")
    print(f"    Losers removed: {removed_losses} ({removed_losses/max(len(removed),1)*100:.0f}%)")
    print(f"    Avg R removed: {sum(removed_results)/max(len(removed_results),1):+.4f}")
    print(f"\n  KEPT trades breakdown:")
    print(f"    Winners kept: {kept_wins} ({kept_wins/max(len(kept),1)*100:.0f}%)")
    print(f"    Losers kept: {kept_losses} ({kept_losses/max(len(kept),1)*100:.0f}%)")
    print(f"    Avg R kept: {sum(kept_results)/max(len(kept_results),1):+.4f}")
    
    # Selectivity ratio
    baseline_wr = (removed_wins + kept_wins) / max(len(target_weak_int), 1)
    kept_wr = kept_wins / max(len(kept), 1)
    removed_wr = removed_wins / max(len(removed), 1)
    
    print(f"\n  Filter selectivity:")
    print(f"    Baseline WR: {baseline_wr:.1%}")
    print(f"    Kept WR: {kept_wr:.1%}")
    print(f"    Removed WR: {removed_wr:.1%}")
    print(f"    WR improvement: {kept_wr - baseline_wr:+.1%}")
    
    # Determine: improves winners (A), removes losers (B), reduces frequency (C), shifts regime (D)
    removes_more_losers = removed_losses > removed_wins
    improves_ev = (sum(kept_results)/max(len(kept_results),1)) > (sum(removed_results + kept_results)/max(len(removed_results)+len(kept_results),1))
    
    print(f"\n  Filter mechanism:")
    if removes_more_losers and improves_ev:
        print(f"    B) Primarily REMOVES LOSING trades (loser removal ratio: {removed_losses}/{len(removed)})")
    elif not removes_more_losers:
        print(f"    C) Reduces frequency without clear loser targeting")
    else:
        print(f"    A/D) Mixed effect")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 7: CONFIDENCE ASSESSMENT
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 7: STATISTICAL CONFIDENCE")
print("─" * 70)

s_target = stats(target_full)
if s_target:
    ev = s_target["ev"]
    n = s_target["n"]
    std = s_target["std"]
    
    # Required sample for 95% CI excluding zero
    if ev > 0 and std > 0:
        n_required = math.ceil((1.96 * std / ev) ** 2)
    else:
        n_required = float('inf')
    
    # T-test (is EV significantly different from 0?)
    t_stat = ev / (std / math.sqrt(n)) if std > 0 and n > 1 else 0
    
    # Monte Carlo: bootstrap probability of profit
    import random
    random.seed(42)
    results = [r["result_r"] for r in target_full]
    bootstrap_positive = 0
    n_boot = 10000
    for _ in range(n_boot):
        sample = random.choices(results, k=n)
        if sum(sample) / n > 0:
            bootstrap_positive += 1
    profit_prob = bootstrap_positive / n_boot
    
    # Bootstrap CI for EV
    boot_evs = []
    for _ in range(n_boot):
        sample = random.choices(results, k=n)
        boot_evs.append(sum(sample) / n)
    boot_evs.sort()
    boot_ci_low = boot_evs[int(0.025 * n_boot)]
    boot_ci_high = boot_evs[int(0.975 * n_boot)]
    
    print(f"\n  Target: WEAK + INTERESTING + 3+ AGREE")
    print(f"  Sample size: n={n}")
    print(f"  EV: {ev:+.4f}R")
    print(f"  Std: {std:.4f}")
    print(f"  T-statistic: {t_stat:.3f}")
    print(f"  Parametric CI: [{s_target['ci_low']:+.4f}, {s_target['ci_high']:+.4f}]")
    print(f"  Bootstrap CI: [{boot_ci_low:+.4f}, {boot_ci_high:+.4f}]")
    print(f"  Bootstrap profit probability: {profit_prob:.1%}")
    print(f"  Required n for significance: {n_required}")
    print(f"  Current n / required: {n}/{n_required} ({n/max(n_required,1)*100:.0f}%)")
    
    # Net EV at different geometries
    print(f"\n  Net EV at different stop distances:")
    for label, cost in [("10p (SCALP)", COST_10P), ("15p", COST_15P), ("20p", COST_20P)]:
        net = ev - cost
        print(f"    {label}: {net:+.4f}R {'POSITIVE' if net > 0 else 'negative'}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS: BROADER ALIGNMENT TEST (ALL trades, not just WEAK+INT)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("BROADER TEST: All enriched trades by agreement level")
print("─" * 70)

print(f"\n  {'Agreement':<35s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@15p':>8s}")
print(f"  {'-'*35}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}")

for label, subset in [
    ("All enriched (baseline)", enriched),
    ("Aligned (any)", [r for r in enriched if r["aligned"]]),
    ("Opposing (any)", [r for r in enriched if not r["aligned"]]),
    ("3+ agree", [r for r in enriched if r["aligned"] and r["agree_count"] >= 3]),
    ("3+ oppose", [r for r in enriched if not r["aligned"] and r["agree_count"] >= 3]),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        net = s["ev"] - COST_15P
        print(f"  {label:<35s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net:>+7.4f}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V4.4 FINAL VERDICT")
print("=" * 70)

s_baseline = stats(enriched)
s_target = stats(target_full)
s_weak_int_all = stats(target_weak_int)

if s_target and s_baseline and s_weak_int_all:
    # Key metrics
    n_original = 25
    n_expanded = s_target["n"]
    ev_original = 0.105  # V4.3 result
    ev_expanded = s_target["ev"]
    
    print(f"\n  Original V4.3: n={n_original} | EV=+0.105R")
    print(f"  Expanded V4.4: n={n_expanded} | EV={ev_expanded:+.4f}R")
    print(f"  Baseline (all): n={s_baseline['n']} | EV={s_baseline['ev']:+.4f}R")
    
    # Improvement over baseline
    improvement = ev_expanded - s_baseline["ev"]
    print(f"\n  Improvement over baseline: {improvement:+.4f}R")
    print(f"  CI includes zero: {'YES' if s_target['ci_low'] <= 0 <= s_target['ci_high'] else 'NO'}")
    
    # Determine verdict
    if n_expanded >= 50 and s_target["ci_low"] > 0:
        verdict = "A"
        reason = "Currency strength validated — CI excludes zero with sufficient sample"
    elif n_expanded >= 30 and ev_expanded > 0.05 and improvement > 0.03:
        verdict = "B"
        reason = f"Promising (EV={ev_expanded:+.4f}) but CI still includes zero — needs more data"
    elif n_expanded < 30 and ev_expanded > 0:
        verdict = "B"
        reason = f"Effect persists (EV={ev_expanded:+.4f}) but n={n_expanded} insufficient for validation"
    elif ev_expanded <= 0 or improvement < 0.01:
        verdict = "C"
        reason = "Effect disappears or collapses with expanded sample"
    else:
        verdict = "D"
        reason = "Insufficient evidence to determine"
    
    print(f"\n  VERDICT: {verdict}) {reason}")
    
    # Recommendations
    print(f"\n  Implications:")
    if verdict in ("A", "B"):
        print(f"    - Currency strength IS a genuine information layer")
        print(f"    - Primary value: REJECTION of opposing trades")
        print(f"    - Net viability depends on trade geometry (15-20p stops)")
        if n_expanded < 50:
            print(f"    - Need {50 - n_expanded} more observations for statistical power")
        print(f"\n  Recommended V4.5:")
        print(f"    - Implement currency_strength.py in core/market_intelligence/")
        print(f"    - Run as live observer feature alongside V3 shadow")
        print(f"    - Collect data at 15-20p stop geometry specifically")
    elif verdict == "C":
        print(f"    - Currency strength was a small-sample artefact")
        print(f"    - V3+candlestick data remains insufficient")
        print(f"    - Consider: different market, different timescale, or accept null")
    else:
        print(f"    - Continue collecting data")
        print(f"    - Do not implement until n>=50 achieved")

print()
