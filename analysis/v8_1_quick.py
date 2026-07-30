import json, math
from pathlib import Path

FX = ['EURUSD','GBPUSD','USDJPY','AUDUSD','NZDUSD','USDCHF','USDCAD']
IDX = ['NAS100','US500']
ALL = FX + IDX
shadow_dir = Path('logs/shadow_trades')

data = {}
for sym in ALL:
    d = shadow_dir / sym
    if not d.exists():
        data[sym] = []
        continue
    trades = []
    for f in d.glob('*.jsonl'):
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get('schema_version') != 'shadow_trades_v2':
                continue
            o = r.get('simulated_outcome', {})
            if o.get('pnl_r_multiple') is None:
                continue
            trades.append(o['pnl_r_multiple'])
    data[sym] = trades

print("=" * 70)
print("V8.1 — TRADING UNIVERSE AUDIT (QUICK)")
print("=" * 70)

print(f"\n{'Sym':<10s}|{'n':>5s}|{'RevWR':>6s}|{'RevEV':>8s}|{'TrdWR':>6s}|{'TrdEV':>8s}| Better  |{'Net':>7s}")
print("-" * 70)
FX_COST = 0.20
IDX_COST = 0.09

for sym in ALL:
    t = data[sym]
    n = len(t)
    if n < 10:
        print(f"{sym:<10s}|{n:>5d}| insufficient data")
        continue
    rev_wr = sum(1 for r in t if r > 0) / n
    rev_ev = sum(t) / n
    trd_wr = 1 - rev_wr
    trd_ev = -rev_ev
    better = 'REVERSION' if rev_ev > trd_ev else 'TREND'
    best_ev = max(rev_ev, trd_ev)
    cost = IDX_COST if sym in IDX else FX_COST
    net = best_ev - cost
    print(f"{sym:<10s}|{n:>5d}| {rev_wr:.1%}|{rev_ev:+.4f}| {trd_wr:.1%}|{trd_ev:+.4f}| {better:<8s}|{net:+.4f}")

print("\n" + "=" * 70)
print("CLASSIFICATION")
print("=" * 70)
for sym in ALL:
    t = data[sym]
    n = len(t)
    if n < 10:
        print(f"  {sym}: TIER 3 — insufficient data")
        continue
    rev_ev = sum(t) / n
    trd_ev = -rev_ev
    best_ev = max(rev_ev, trd_ev)
    cost = IDX_COST if sym in IDX else FX_COST
    net = best_ev - cost
    # Time stability
    half = n // 2
    h1_ev = sum(t[:half]) / half
    h2_ev = sum(t[half:]) / (n - half)
    better_policy = 'rev' if rev_ev > trd_ev else 'trend'
    if better_policy == 'trend':
        h1_ev = -h1_ev
        h2_ev = -h2_ev
    both_pos = h1_ev > 0 and h2_ev > 0

    if net > 0.05 and both_pos:
        tier = "TIER 1"
    elif net > 0 and both_pos:
        tier = "TIER 2"
    elif net > 0 and not both_pos:
        tier = "TIER 3"
    else:
        tier = "TIER 3"
    print(f"  {sym}: {tier} — net={net:+.4f}, H1={h1_ev:+.4f}, H2={h2_ev:+.4f}, stable={both_pos}")

print("\nDONE")
