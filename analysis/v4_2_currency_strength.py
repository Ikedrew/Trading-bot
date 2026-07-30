"""V4.2 — Currency Strength Information Value Analysis.

Derives broad currency strength from 7 existing FX pairs and tests whether
alignment between individual pair direction and broader currency trends
improves prediction beyond the 50.7% price-action baseline.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

# ═══════════════════════════════════════════════════════════════
# LOAD ALL SHADOW TRADES (for cross-pair strength calculation)
# ═══════════════════════════════════════════════════════════════

shadow_dir = Path("logs/shadow_trades")

# Load ALL shadow trades with timestamps, grouped by timestamp
trades_by_time = defaultdict(list)  # {timestamp: [trade, ...]}
all_trades = []

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
                                identity = r.get("identity", {})
                                snap = r.get("decision_snapshot", {})
                                outcome = r.get("simulated_outcome", {})
                                sym = identity.get("symbol", "")
                                ts = snap.get("timestamp_decision_utc", 0)
                                result_r = outcome.get("pnl_r_multiple")
                                direction = snap.get("direction", "")
                                if sym and ts and result_r is not None and direction:
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

# Deduplicate by entity (keep first per symbol+timestamp)
seen = set()
unique_trades = []
for t in all_trades:
    key = (t["symbol"], int(t["timestamp"]))
    if key not in seen:
        seen.add(key)
        unique_trades.append(t)

print("="*70)
print("V4.2 — CURRENCY STRENGTH INFORMATION VALUE ANALYSIS")
print("="*70)
print(f"\nTotal unique shadow trades: {len(unique_trades)}")
print(f"Unique timestamps: {len(trades_by_time)}")
print(f"Symbols: {Counter(t['symbol'] for t in unique_trades).most_common()}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: CURRENCY STRENGTH CONSTRUCTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 1: CURRENCY STRENGTH DERIVATION")
print("─"*70)

# For each pair, determine which currencies are involved
# Convention: for "XXXYYY", XXX is base, YYY is quote
# If pair goes UP: base strengthens, quote weakens
PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
}

def get_usd_direction(trade):
    """Determine if this trade implies USD strength or weakness."""
    sym = trade["symbol"]
    direction = trade["direction"]
    if sym not in PAIR_CURRENCIES:
        return None
    base, quote = PAIR_CURRENCIES[sym]
    if quote == "USD":
        # e.g., EURUSD BUY = EUR strong = USD weak
        return "USD_WEAK" if direction == "BUY" else "USD_STRONG"
    elif base == "USD":
        # e.g., USDJPY BUY = USD strong
        return "USD_STRONG" if direction == "BUY" else "USD_WEAK"
    return None

# For each timestamp, determine broad USD direction from OTHER pairs
def compute_usd_strength_at_time(target_symbol, target_time, window=300):
    """Compute USD direction from other pairs at the same time (±window seconds)."""
    other_trades = []
    for ts in range(int(target_time) - window, int(target_time) + window + 1):
        for t in trades_by_time.get(ts, []):
            if t["symbol"] != target_symbol:
                usd_dir = get_usd_direction(t)
                if usd_dir:
                    # Use outcome as proxy for "what USD actually did"
                    other_trades.append({
                        "symbol": t["symbol"],
                        "usd_direction": usd_dir,
                        "result_r": t["result_r"],
                    })
    return other_trades

# For a more tractable approach: compute per-timestamp "consensus"
# Group trades by timestamp, determine if there's broad USD agreement

# Since we can't compute real-time strength without OHLC of each bar,
# we use a simpler approach: for each trade, check if OTHER trades
# at the same time agree on USD direction

print("\n  Deriving USD context from concurrent cross-pair trades...")

# Build alignment dataset
alignment_data = []
for trade in unique_trades:
    sym = trade["symbol"]
    ts = trade["timestamp"]
    direction = trade["direction"]
    
    # What does THIS trade imply about USD?
    my_usd = get_usd_direction(trade)
    if not my_usd:
        continue
    
    # What do OTHER concurrent trades say about USD?
    concurrent = [t for t in trades_by_time.get(int(ts), []) if t["symbol"] != sym]
    if not concurrent:
        continue
    
    # Count USD strong vs weak from other pairs
    usd_strong_count = sum(1 for t in concurrent if get_usd_direction(t) == "USD_STRONG")
    usd_weak_count = sum(1 for t in concurrent if get_usd_direction(t) == "USD_WEAK")
    total_others = usd_strong_count + usd_weak_count
    
    if total_others == 0:
        continue
    
    # Determine alignment
    if my_usd == "USD_STRONG":
        aligned = usd_strong_count > usd_weak_count
    else:
        aligned = usd_weak_count > usd_strong_count
    
    # Strength of agreement
    agreement_ratio = max(usd_strong_count, usd_weak_count) / total_others if total_others > 0 else 0.5
    
    alignment_data.append({
        "trade": trade,
        "aligned": aligned,
        "agreement_ratio": agreement_ratio,
        "concurrent_pairs": total_others,
        "usd_strong": usd_strong_count,
        "usd_weak": usd_weak_count,
    })

print(f"  Trades with cross-pair context: {len(alignment_data)}")
print(f"  Aligned with USD trend: {sum(1 for a in alignment_data if a['aligned'])}")
print(f"  Opposing USD trend: {sum(1 for a in alignment_data if not a['aligned'])}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: ALIGNMENT TEST
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 2: ALIGNMENT TEST")
print("─"*70)

def stats(trades):
    if not trades: return None
    results = [t["trade"]["result_r"] for t in trades]
    n = len(results)
    wins = sum(1 for r in results if r > 0)
    ev = sum(results)/n
    std = math.sqrt(sum((r-ev)**2 for r in results)/max(n-1,1))
    se = std/math.sqrt(n)
    mfe = [t["trade"]["mfe_r"] for t in trades]
    mae = [t["trade"]["mae_r"] for t in trades]
    return {"n":n,"wr":wins/n,"ev":ev,"std":std,"ci_low":ev-1.96*se,"ci_high":ev+1.96*se,
            "mfe":sum(mfe)/len(mfe) if mfe else 0,"mae":sum(mae)/len(mae) if mae else 0}

aligned = [a for a in alignment_data if a["aligned"]]
opposing = [a for a in alignment_data if not a["aligned"]]

s_all = stats(alignment_data)
s_aligned = stats(aligned)
s_opposing = stats(opposing)

print(f"\n  {'Group':<25s} | {'n':>5s} | {'WR':>5s} | {'EV':>8s} | {'CI':>18s} | {'MFE':>5s} | {'MAE':>5s}")
print(f"  {'-'*25}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}-+-{'-'*18}-+-{'-'*5}-+-{'-'*5}")

for label, s in [("All (baseline)", s_all), ("USD ALIGNED", s_aligned), ("USD OPPOSING", s_opposing)]:
    if s:
        print(f"  {label:<25s} | {s['n']:>5d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | [{s['ci_low']:+.3f},{s['ci_high']:+.3f}] | {s['mfe']:.3f} | {s['mae']:.3f}")

if s_aligned and s_opposing:
    delta = s_aligned["ev"] - s_opposing["ev"]
    wr_delta = s_aligned["wr"] - s_opposing["wr"]
    print(f"\n  Alignment effect:")
    print(f"    EV improvement: {delta:+.4f}R")
    print(f"    WR improvement: {wr_delta:+.1%}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: STRENGTH DIFFERENTIAL
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 3: AGREEMENT STRENGTH")
print("─"*70)

# Split by agreement ratio (how strongly other pairs agree)
strong_agree = [a for a in alignment_data if a["agreement_ratio"] >= 0.8 and a["aligned"]]
moderate_agree = [a for a in alignment_data if 0.6 <= a["agreement_ratio"] < 0.8 and a["aligned"]]
weak_agree = [a for a in alignment_data if a["agreement_ratio"] < 0.6]
strong_disagree = [a for a in alignment_data if a["agreement_ratio"] >= 0.8 and not a["aligned"]]

for label, subset in [
    ("Strong agreement (>80%) + aligned", strong_agree),
    ("Moderate agreement (60-80%) + aligned", moderate_agree),
    ("Weak/mixed (<60%)", weak_agree),
    ("Strong disagreement (>80%) opposing", strong_disagree),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"  {label:<45s} | n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: BROAD AGREEMENT TEST
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 4: BROAD AGREEMENT (3+ pairs concur)")
print("─"*70)

broad_agree = [a for a in alignment_data if a["concurrent_pairs"] >= 3 and a["aligned"] and a["agreement_ratio"] >= 0.67]
broad_oppose = [a for a in alignment_data if a["concurrent_pairs"] >= 3 and not a["aligned"] and a["agreement_ratio"] >= 0.67]
no_consensus = [a for a in alignment_data if a["concurrent_pairs"] >= 3 and a["agreement_ratio"] < 0.67]

for label, subset in [
    ("3+ pairs agree WITH trade", broad_agree),
    ("3+ pairs OPPOSE trade", broad_oppose),
    ("No clear consensus", no_consensus),
]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"  {label:<35s} | n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | CI=[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: BY SYMBOL
# ═══════════════════════════════════════════════════════════════
print("\n" + "─"*70)
print("ANALYSIS 5: SYMBOL STABILITY")
print("─"*70)

for sym in sorted(set(a["trade"]["symbol"] for a in alignment_data)):
    sym_aligned = [a for a in aligned if a["trade"]["symbol"] == sym]
    sym_opposing = [a for a in opposing if a["trade"]["symbol"] == sym]
    sa = stats(sym_aligned)
    so = stats(sym_opposing)
    if sa and so and sa["n"] >= 5 and so["n"] >= 5:
        delta = sa["ev"] - so["ev"]
        print(f"  {sym:10s}: aligned n={sa['n']:3d} EV={sa['ev']:+.4f} | opposing n={so['n']:3d} EV={so['ev']:+.4f} | delta={delta:+.4f}")

# ═══════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("V4.2 VERDICT")
print("="*70)

if s_aligned and s_opposing:
    delta_ev = s_aligned["ev"] - s_opposing["ev"]
    delta_wr = s_aligned["wr"] - s_opposing["wr"]
    baseline_wr = s_all["wr"] if s_all else 0.5
    aligned_wr = s_aligned["wr"]

    print(f"\n  Baseline WR: {baseline_wr:.1%}")
    print(f"  Aligned WR: {aligned_wr:.1%}")
    print(f"  WR improvement from alignment: {delta_wr:+.1%}")
    print(f"  EV improvement (aligned vs opposing): {delta_ev:+.4f}R")

    if delta_wr > 0.05 and s_aligned["ci_low"] > 0:
        print("\n  A) Currency strength materially improves V3 prediction")
    elif delta_wr > 0.02 or delta_ev > 0.05:
        print("\n  B) Currency strength provides weak improvement — needs more data")
        print(f"     {delta_wr:+.1%} WR improvement is {'meaningful' if delta_wr > 0.03 else 'marginal'}")
    elif abs(delta_wr) < 0.02 and abs(delta_ev) < 0.03:
        print("\n  C) Currency strength adds no meaningful information")
        print(f"     Aligned WR ({aligned_wr:.1%}) ≈ baseline ({baseline_wr:.1%})")
    else:
        print("\n  D) Insufficient data or inconclusive")

print()
