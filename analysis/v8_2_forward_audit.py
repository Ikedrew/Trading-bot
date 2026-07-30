"""V8.2 — Forward Validation Audit.

Determines whether the V8.1 policies survive on forward (unseen) data.
Uses a 70/30 in-sample/out-of-sample split on available shadow trades
as a proxy for true forward validation.

Policies:
  TREND: NAS100, US500, USDJPY, AUDUSD, NZDUSD (invert shadow result)
  REVERSION: EURUSD (keep shadow result as-is)
"""
import json, math
from pathlib import Path

print("=" * 70)
print("V8.2 — FORWARD VALIDATION AUDIT")
print("=" * 70)

TREND_SYMS = ["NAS100", "US500", "USDJPY", "AUDUSD", "NZDUSD"]
REVERSION_SYMS = ["EURUSD"]
ALL_ACTIVE = TREND_SYMS + REVERSION_SYMS

# Load data
shadow_dir = Path("logs/shadow_trades")
data = {}
for sym in ALL_ACTIVE:
    d = shadow_dir / sym
    if not d.exists():
        data[sym] = []
        continue
    trades = []
    for f in d.glob("*.jsonl"):
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("schema_version") != "shadow_trades_v2":
                continue
            o = r.get("simulated_outcome", {})
            s = r.get("decision_snapshot", {})
            if o.get("pnl_r_multiple") is None:
                continue
            trades.append({
                "result_r": o["pnl_r_multiple"],
                "mfe_r": o.get("mfe_r", 0),
                "mae_r": o.get("mae_r", 0),
                "exit_reason": o.get("exit_reason", ""),
                "timestamp": s.get("timestamp_decision_utc", 0),
            })
    data[sym] = sorted(trades, key=lambda t: t["timestamp"])

def apply_policy(trades, policy):
    """Return results under the given policy."""
    if policy == "TREND":
        return [-t["result_r"] for t in trades]
    else:
        return [t["result_r"] for t in trades]

def stats(results):
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    win_r = [r for r in results if r > 0]
    loss_r = [r for r in results if r < 0]
    avg_win = sum(win_r) / len(win_r) if win_r else 0
    avg_loss = sum(loss_r) / len(loss_r) if loss_r else 0
    pf = sum(win_r) / abs(sum(loss_r)) if loss_r else float('inf')
    # Max losing streak
    streak = 0; max_streak = 0
    # Max drawdown
    equity = 0; peak = 0; max_dd = 0
    for r in results:
        equity += r
        if equity > peak: peak = equity
        dd = peak - equity
        if dd > max_dd: max_dd = dd
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n": n, "wr": wins/n, "ev": ev, "std": std,
        "ci_low": ev - 1.96*se, "ci_high": ev + 1.96*se,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "pf": pf, "max_streak": max_streak, "max_dd": max_dd,
    }

# ═══════════════════════════════════════════════════════════════
# SECTION 1: FORWARD DATASET INTEGRITY
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 1: DATASET INTEGRITY")
print("-" * 70)

total_all = 0
for sym in ALL_ACTIVE:
    n = len(data[sym])
    total_all += n
    ts_set = set(t["timestamp"] for t in data[sym])
    dupes = n - len(ts_set)
    has_ts = all(t["timestamp"] > 0 for t in data[sym])
    print(f"  {sym:10s}: n={n:5d} | timestamps valid: {has_ts} | duplicates: {dupes}")

print(f"\n  Total observations: {total_all}")
print(f"  NOTE: No true 'forward' data exists (bot has not generated new trades")
print(f"  since discovery). Using 70/30 IS/OOS split as proxy.")

# ═══════════════════════════════════════════════════════════════
# SECTION 2: INSTRUMENT PERFORMANCE (full + 70/30 split)
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 2: INSTRUMENT PERFORMANCE")
print("-" * 70)

results_table = {}
for sym in ALL_ACTIVE:
    trades = data[sym]
    n = len(trades)
    if n < 10:
        print(f"  {sym:10s}: INSUFFICIENT DATA (n={n})")
        results_table[sym] = None
        continue
    
    policy = "TREND" if sym in TREND_SYMS else "REVERSION"
    all_results = apply_policy(trades, policy)
    
    # 70/30 split
    cutoff = int(n * 0.70)
    discovery_results = apply_policy(trades[:cutoff], policy)
    forward_results = apply_policy(trades[cutoff:], policy)
    
    s_all = stats(all_results)
    s_disc = stats(discovery_results)
    s_fwd = stats(forward_results)
    
    results_table[sym] = {"all": s_all, "disc": s_disc, "fwd": s_fwd, "policy": policy}
    
    print(f"\n  {sym} ({policy}):")
    print(f"    {'Set':<12s}|{'n':>5s}|{'WR':>6s}|{'EV':>8s}|{'AvgW':>6s}|{'AvgL':>6s}|{'PF':>5s}|{'MxLS':>4s}|{'MxDD':>5s}|{'CI':>20s}")
    print(f"    {'-'*12}+{'-'*5}+{'-'*6}+{'-'*8}+{'-'*6}+{'-'*6}+{'-'*5}+{'-'*4}+{'-'*5}+{'-'*20}")
    for label, s in [("Discovery", s_disc), ("FORWARD", s_fwd), ("Combined", s_all)]:
        if s:
            print(f"    {label:<12s}|{s['n']:>5d}|{s['wr']:.1%}|{s['ev']:>+7.4f}|{s['avg_win']:>5.3f}|{s['avg_loss']:>+5.3f}|{s['pf']:>4.2f}|{s['max_streak']:>4d}|{s['max_dd']:>4.1f}R|[{s['ci_low']:+.3f},{s['ci_high']:+.3f}]")

# ═══════════════════════════════════════════════════════════════
# SECTION 3: STABILITY ANALYSIS
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 3: STABILITY (Discovery vs Forward)")
print("-" * 70)

print(f"\n  {'Symbol':<10s}|{'Disc EV':>8s}|{'Fwd EV':>8s}|{'Delta':>7s}| Assessment")
print(f"  {'-'*10}+{'-'*8}+{'-'*8}+{'-'*7}+{'-'*20}")

for sym in ALL_ACTIVE:
    r = results_table.get(sym)
    if not r or not r["fwd"]:
        print(f"  {sym:<10s}| {'—':>7s}| {'—':>7s}| {'—':>6s}| NO DATA")
        continue
    d_ev = r["disc"]["ev"]
    f_ev = r["fwd"]["ev"]
    delta = f_ev - d_ev
    
    if f_ev > 0 and abs(delta) < 0.10:
        assessment = "STABLE"
    elif f_ev > 0 and f_ev > d_ev:
        assessment = "IMPROVED"
    elif f_ev > 0 and delta < -0.10:
        assessment = "WEAKENED"
    elif f_ev <= 0:
        assessment = "DEGRADED"
    else:
        assessment = "INCONCLUSIVE"
    
    print(f"  {sym:<10s}|{d_ev:>+7.4f}|{f_ev:>+7.4f}|{delta:>+6.4f}| {assessment}")

# ═══════════════════════════════════════════════════════════════
# SECTION 4: REGIME PROXY (exit reason as movement indicator)
# No V3 regime labels for indices; use exit_reason as proxy
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 4: REGIME PROXY (exit reason distribution)")
print("-" * 70)

# Exit reason tells us about market movement:
# take_profit = market moved strongly in signal direction (trending)
# stop_loss = market moved against signal (wrong direction or reversal)
# max_bars_timeout = market didn't move enough (ranging)

for sym in ALL_ACTIVE:
    trades = data[sym]
    if len(trades) < 20:
        continue
    policy = "TREND" if sym in TREND_SYMS else "REVERSION"
    
    tp = [t for t in trades if t["exit_reason"] == "take_profit"]
    sl = [t for t in trades if t["exit_reason"] == "stop_loss"]
    to = [t for t in trades if t["exit_reason"] == "max_bars_timeout"]
    
    n = len(trades)
    # Under inverted policy: tp/sl interpretation flips for TREND instruments
    if policy == "TREND":
        # Original TP = our SL (market went their way = against us under original signal)
        # Original SL = our TP (market went against them = for us when inverted)
        trending_pct = len(sl) / n  # Original SL = strong move against original = FOR us
        ranging_pct = len(to) / n
        reversal_pct = len(tp) / n  # Original TP = strong move FOR original = AGAINST us
    else:
        trending_pct = len(tp) / n
        ranging_pct = len(to) / n
        reversal_pct = len(sl) / n
    
    print(f"  {sym:10s}: wins(trend)={trending_pct:.0%} | timeout(range)={ranging_pct:.0%} | losses(reversal)={reversal_pct:.0%}")

# ═══════════════════════════════════════════════════════════════
# SECTION 5: EXECUTION REALITY (cost estimates)
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 5: EXECUTION COST ESTIMATE")
print("-" * 70)

COST_MODEL = {
    "NAS100": 0.10, "US500": 0.08,
    "USDJPY": 0.20, "AUDUSD": 0.20, "NZDUSD": 0.20,
    "EURUSD": 0.20,
}

print(f"\n  {'Symbol':<10s}|{'Gross EV':>9s}|{'Cost/R':>7s}|{'Net EV':>8s}| Viable?")
print(f"  {'-'*10}+{'-'*9}+{'-'*7}+{'-'*8}+{'-'*10}")

for sym in ALL_ACTIVE:
    r = results_table.get(sym)
    if not r:
        continue
    s = r["all"]
    if not s:
        continue
    cost = COST_MODEL.get(sym, 0.20)
    net = s["ev"] - cost
    viable = "YES" if net > 0 else "NO"
    print(f"  {sym:<10s}|{s['ev']:>+8.4f}|{cost:>6.2f}|{net:>+7.4f}| {viable}")

# ═══════════════════════════════════════════════════════════════
# SECTION 6: POLICY VALIDATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 6: POLICY STILL CORRECT?")
print("-" * 70)

for sym in ALL_ACTIVE:
    r = results_table.get(sym)
    if not r or not r["fwd"]:
        print(f"  {sym:10s}: INSUFFICIENT DATA")
        continue
    
    fwd_ev = r["fwd"]["ev"]
    fwd_wr = r["fwd"]["wr"]
    fwd_n = r["fwd"]["n"]
    
    if fwd_ev > 0 and fwd_wr > 0.50 and fwd_n >= 30:
        answer = "YES"
        evidence = f"Forward EV={fwd_ev:+.4f}, WR={fwd_wr:.1%}, n={fwd_n}"
    elif fwd_ev > 0 and fwd_n >= 20:
        answer = "YES (marginal)"
        evidence = f"Forward EV={fwd_ev:+.4f}, WR={fwd_wr:.1%}, n={fwd_n}"
    elif fwd_ev > 0 and fwd_n < 20:
        answer = "INSUFFICIENT DATA"
        evidence = f"Forward EV={fwd_ev:+.4f} but n={fwd_n} too small"
    elif fwd_ev <= 0:
        answer = "CONCERN"
        evidence = f"Forward EV={fwd_ev:+.4f} — edge may have degraded"
    else:
        answer = "INSUFFICIENT DATA"
        evidence = ""
    
    print(f"  {sym:10s}: {answer}")
    print(f"    Evidence: {evidence}")

# ═══════════════════════════════════════════════════════════════
# SECTION 7: INVALIDATION THRESHOLDS
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 7: INVALIDATION THRESHOLDS")
print("-" * 70)

print(f"""
  Proposed invalidation criteria (per instrument):

  SUSPEND trading if ANY of:
    1. Rolling-50 EV < 0 for 2 consecutive checks
    2. Rolling-50 WR < 40% (TREND) or < 35% (REVERSION)
    3. 15+ consecutive losses
    4. Drawdown exceeds 12R from equity peak
    5. Forward n≥100 with CI upper bound < 0

  HALT ALL trading if:
    1. Portfolio rolling-100 EV < 0
    2. Combined max DD exceeds 15R
    3. 3+ instruments simultaneously in SUSPEND state
""")

# ═══════════════════════════════════════════════════════════════
# SECTION 8: CONFIDENCE ASSESSMENT
# ═══════════════════════════════════════════════════════════════
print("\n" + "-" * 70)
print("SECTION 8: CONFIDENCE ASSESSMENT")
print("-" * 70)

print(f"\n  {'Symbol':<10s}|{'Policy':<10s}|{'n':>5s}|{'Fwd EV':>7s}|{'Stable?':>7s}|{'Net+?':>5s}| Confidence")
print(f"  {'-'*10}+{'-'*10}+{'-'*5}+{'-'*7}+{'-'*7}+{'-'*5}+{'-'*12}")

for sym in ALL_ACTIVE:
    r = results_table.get(sym)
    if not r:
        print(f"  {sym:<10s}| {'—':<9s}| {'—':>4s}| {'—':>6s}| {'—':>6s}| {'—':>4s}| NO DATA")
        continue
    
    policy = r["policy"]
    s_all = r["all"]
    s_fwd = r["fwd"]
    
    if not s_all:
        continue
    
    n = s_all["n"]
    fwd_ev = s_fwd["ev"] if s_fwd else 0
    fwd_positive = fwd_ev > 0
    
    # Stability: both halves positive?
    stable = s_fwd and r["disc"]["ev"] > 0 and s_fwd["ev"] > 0
    
    # Net positive after costs?
    cost = COST_MODEL.get(sym, 0.20)
    net_pos = s_all["ev"] - cost > 0
    
    # Confidence
    if n >= 500 and fwd_positive and stable and net_pos and s_all["ci_low"] > 0:
        confidence = "HIGH"
    elif n >= 200 and fwd_positive and stable and net_pos:
        confidence = "MEDIUM"
    elif n >= 100 and fwd_positive and net_pos:
        confidence = "LOW-MEDIUM"
    elif fwd_positive:
        confidence = "LOW"
    else:
        confidence = "VERY LOW"
    
    print(f"  {sym:<10s}|{policy:<10s}|{n:>5d}|{fwd_ev:>+6.4f}|{'YES' if stable else 'NO':>7s}|{'YES' if net_pos else 'NO':>5s}| {confidence}")

# ═══════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V8.2 FINAL REPORT")
print("=" * 70)

# Categorise
validated = []
need_data = []
degraded = []

for sym in ALL_ACTIVE:
    r = results_table.get(sym)
    if not r or not r["fwd"]:
        need_data.append(sym)
        continue
    fwd_ev = r["fwd"]["ev"]
    disc_ev = r["disc"]["ev"]
    cost = COST_MODEL.get(sym, 0.20)
    net = r["all"]["ev"] - cost
    
    if fwd_ev > 0 and net > 0:
        validated.append(sym)
    elif fwd_ev <= 0 and disc_ev > 0:
        degraded.append(sym)
    else:
        need_data.append(sym)

print(f"""
  A) INSTRUMENTS THAT REMAIN VALIDATED:
     {', '.join(validated) if validated else 'None'}

  B) INSTRUMENTS REQUIRING MORE FORWARD DATA:
     {', '.join(need_data) if need_data else 'None'}

  C) INSTRUMENTS SHOWING SIGNS OF DEGRADATION:
     {', '.join(degraded) if degraded else 'None'}
""")

# Recommendation
all_fwd_positive = all(
    results_table.get(sym, {}).get("fwd", {}) and results_table[sym]["fwd"]["ev"] > 0
    for sym in ALL_ACTIVE if results_table.get(sym)
)
any_degraded = len(degraded) > 0
total_forward_n = sum(
    results_table[sym]["fwd"]["n"]
    for sym in ALL_ACTIVE
    if results_table.get(sym) and results_table[sym].get("fwd")
)

print(f"  D) IMPLEMENTATION RECOMMENDATION:")
if all_fwd_positive and total_forward_n >= 500:
    print(f"     → PROCEED TO PAPER TRADING")
    print(f"     Evidence: All instruments forward-positive, total forward n={total_forward_n}")
elif all_fwd_positive and total_forward_n < 500:
    print(f"     → CONTINUE SHADOW VALIDATION")
    print(f"     Evidence: Forward positive but n={total_forward_n} (need 500+)")
    print(f"     NOTE: This is a PSEUDO-FORWARD split (70/30 on existing data).")
    print(f"     TRUE forward validation requires NEW data generated after V8.1.")
    print(f"     The system has not collected new observations since discovery.")
elif any_degraded:
    print(f"     → CONTINUE SHADOW VALIDATION (monitor degraded instruments)")
    print(f"     Degraded: {', '.join(degraded)}")
else:
    print(f"     → CONTINUE SHADOW VALIDATION")
    print(f"     Reason: Insufficient evidence for progression")

print(f"""
  CRITICAL NOTE:
  This analysis uses a 70/30 split on EXISTING data as a proxy.
  No genuinely new (post-discovery) trades have been collected.
  True forward validation requires the bot to RUN and COLLECT
  new shadow observations. Until then, all results are in-sample.
""")
