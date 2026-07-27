"""Comprehensive performance audit of all live trades."""
import json
from pathlib import Path
from datetime import datetime, timezone

base = Path("logs")

# Collect from trade_journal (completed trades with outcomes)
journal = {}
for f in sorted((base / "trade_journal").rglob("*.jsonl")):
    for line in open(f, "r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            cid = rec.get("correlation_id", "")
            tid = rec.get("trade_id", "")
            pat = rec.get("pattern_name", "")
            # Skip test/recovered without correlation
            if not cid and pat == "RECOVERED":
                continue
            if not cid:
                continue
            journal[cid] = rec
        except Exception:
            pass

# Collect execution_results (for slippage/fill data)
exec_data = {}
for f in sorted((base / "execution_results").rglob("*.jsonl")):
    for line in open(f, "r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if rec.get("result_ok") and rec.get("correlation_id"):
                exec_data[rec["correlation_id"]] = rec
        except Exception:
            pass

# Build canonical trade list
trades = []
for cid, j in journal.items():
    e = exec_data.get(cid, {})
    sym = j["symbol"]
    pip = 0.01 if "JPY" in sym else 0.0001
    entry = j["entry_price"]
    exit_p = j["exit_price"]
    sl = j.get("initial_sl", 0)
    tp = j.get("initial_tp", 0)
    side = j["direction"]
    risk = abs(entry - sl) if sl else 0
    if risk > 0:
        r = ((exit_p - entry) / risk) if side == "BUY" else ((entry - exit_p) / risk)
    else:
        r = 0
    pnl = j.get("net_pnl", 0)
    result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BE")
    
    trades.append({
        "cid": cid,
        "sym": sym,
        "pattern": j.get("pattern_name", "?"),
        "side": side,
        "entry": entry,
        "exit": exit_p,
        "sl": sl,
        "tp": tp,
        "sl_pips": round(abs(entry - sl) / pip, 1) if sl else 0,
        "tp_pips": round(abs(entry - tp) / pip, 1) if tp else 0,
        "r": round(r, 2),
        "pnl": pnl,
        "result": result,
        "duration": j.get("duration_seconds", 0),
        "entry_time": j.get("entry_time", 0),
        "close_reason": j.get("close_reason", "?"),
        "mfe": j.get("max_favourable_price", 0),
        "slippage": e.get("slippage", 0),
    })

trades.sort(key=lambda x: x["entry_time"])

# ═══ OUTPUT ═══════════════════════════════════════════════════════════════════
print(f"TOTAL COMPLETED LIVE TRADES: {len(trades)}")
print()

# Trade-by-trade table
print("═══ TRADE-BY-TRADE TABLE ═══")
print(f"{'#':>2} {'Sym':<7} {'Pattern':<22} {'Side':<4} {'Entry':>9} {'Exit':>9} {'SL_p':>5} {'TP_p':>5} {'R':>6} {'P&L':>7} {'Dur(s)':>8} {'Result':<5}")
print("-" * 105)
for i, t in enumerate(trades, 1):
    print(f"{i:2d} {t['sym']:<7} {t['pattern']:<22} {t['side']:<4} {t['entry']:>9.5f} {t['exit']:>9.5f} {t['sl_pips']:>5.1f} {t['tp_pips']:>5.1f} {t['r']:>+6.2f} {t['pnl']:>+7.2f} {t['duration']:>8.1f} {t['result']:<5}")

# ═══ OVERALL PERFORMANCE ═══
wins = [t for t in trades if t["result"] == "WIN"]
losses = [t for t in trades if t["result"] == "LOSS"]
n = len(trades)
print()
print("═══ OVERALL PERFORMANCE ═══")
print(f"Total trades:      {n}")
print(f"Wins:              {len(wins)} ({100*len(wins)/n:.1f}%)")
print(f"Losses:            {len(losses)} ({100*len(losses)/n:.1f}%)")
print(f"Win rate:          {100*len(wins)/n:.1f}%")
r_vals = [t["r"] for t in trades]
print(f"Average R:         {sum(r_vals)/n:+.3f}")
print(f"Median R:          {sorted(r_vals)[n//2]:+.3f}")
if wins:
    print(f"Average winner:    {sum(t['r'] for t in wins)/len(wins):+.3f} R")
    print(f"Largest winner:    {max(t['r'] for t in wins):+.3f} R")
if losses:
    print(f"Average loser:     {sum(t['r'] for t in losses)/len(losses):+.3f} R")
    print(f"Largest loser:     {min(t['r'] for t in losses):+.3f} R")
expectancy = sum(r_vals) / n
print(f"Expectancy (R):    {expectancy:+.4f}")
gross_win = sum(t["pnl"] for t in wins)
gross_loss = abs(sum(t["pnl"] for t in losses))
print(f"Gross profit:      ${gross_win:+.2f}")
print(f"Gross loss:        ${gross_loss:.2f}")
print(f"Net profit:        ${sum(t['pnl'] for t in trades):+.2f}")
pf = gross_win / gross_loss if gross_loss > 0 else 0
print(f"Profit factor:     {pf:.3f}")
print(f"Avg duration:      {sum(t['duration'] for t in trades)/n:.0f}s ({sum(t['duration'] for t in trades)/n/60:.1f}m)")
print(f"Net R:             {sum(r_vals):+.2f}")

# Drawdown
equity = []
running = 0
for t in trades:
    running += t["r"]
    equity.append(running)
peak = 0
max_dd = 0
for e in equity:
    if e > peak:
        peak = e
    dd = peak - e
    if dd > max_dd:
        max_dd = dd
print(f"Max drawdown (R):  {max_dd:.2f}")

# Streaks
max_win_streak = max_loss_streak = cur_win = cur_loss = 0
for t in trades:
    if t["result"] == "WIN":
        cur_win += 1; cur_loss = 0
    else:
        cur_loss += 1; cur_win = 0
    max_win_streak = max(max_win_streak, cur_win)
    max_loss_streak = max(max_loss_streak, cur_loss)
print(f"Max win streak:    {max_win_streak}")
print(f"Max loss streak:   {max_loss_streak}")

# ═══ BY SYMBOL ═══
print()
print("═══ RESULTS BY SYMBOL ═══")
symbols = sorted(set(t["sym"] for t in trades))
sym_stats = []
for s in symbols:
    st = [t for t in trades if t["sym"] == s]
    w = [t for t in st if t["result"] == "WIN"]
    wr = len(w) / len(st) * 100
    avg_r = sum(t["r"] for t in st) / len(st)
    net_r = sum(t["r"] for t in st)
    sym_stats.append((s, len(st), wr, avg_r, net_r))
    print(f"  {s}: trades={len(st)} wins={len(w)} wr={wr:.0f}% avg_R={avg_r:+.3f} net_R={net_r:+.2f}")
print()

# ═══ BY PATTERN ═══
print("═══ RESULTS BY PATTERN ═══")
patterns = sorted(set(t["pattern"] for t in trades))
for p in patterns:
    pt = [t for t in trades if t["pattern"] == p]
    pw = [t for t in pt if t["result"] == "WIN"]
    wr = len(pw) / len(pt) * 100
    avg_r = sum(t["r"] for t in pt) / len(pt)
    net_r = sum(t["r"] for t in pt)
    print(f"  {p:<22} trades={len(pt)} wins={len(pw)} wr={wr:.0f}% avg_R={avg_r:+.3f} net_R={net_r:+.2f}")
print()

# ═══ BY DIRECTION ═══
print("═══ RESULTS BY DIRECTION ═══")
for d in ["BUY", "SELL"]:
    dt = [t for t in trades if t["side"] == d]
    if not dt:
        continue
    dw = [t for t in dt if t["result"] == "WIN"]
    wr = len(dw) / len(dt) * 100
    avg_r = sum(t["r"] for t in dt) / len(dt)
    net_r = sum(t["r"] for t in dt)
    print(f"  {d}: trades={len(dt)} wins={len(dw)} wr={wr:.0f}% avg_R={avg_r:+.3f} net_R={net_r:+.2f}")
print()

# ═══ BY SL SIZE ═══
print("═══ RESULTS BY STOP SIZE ═══")
tight = [t for t in trades if t["sl_pips"] < 3.0]
medium = [t for t in trades if 3.0 <= t["sl_pips"] < 5.0]
wide = [t for t in trades if t["sl_pips"] >= 5.0]
for label, group in [("< 3 pips", tight), ("3-5 pips", medium), (">= 5 pips", wide)]:
    if not group:
        continue
    gw = [t for t in group if t["result"] == "WIN"]
    wr = len(gw) / len(group) * 100
    avg_r = sum(t["r"] for t in group) / len(group)
    print(f"  SL {label:<10} trades={len(group)} wins={len(gw)} wr={wr:.0f}% avg_R={avg_r:+.3f}")
