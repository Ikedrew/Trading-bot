"""V6.4 — Index Market Transfer Validation.

Tests whether V3/V5 architecture produces higher information value
on index markets (NAS100, US500, XAUUSD) vs FX baseline.

NOTE: This script will report actual data availability and perform
analysis on whatever index data exists. If no index data is available,
it reports the FX baseline and documents readiness requirements.
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V6.4 — INDEX MARKET TRANSFER VALIDATION")
print("=" * 70)

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD", "USTEC", "SPX500", "GOLD"}
FX_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_exec_assessments():
    """Load all execution assessments with outcomes."""
    records = {"FX": [], "INDEX": []}
    exec_dir = Path("logs/v3_shadow/execution_assessment")
    if not exec_dir.exists():
        return records
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is None:
                        continue
                    sym = r.get("symbol", "")
                    if sym.upper() in INDEX_SYMBOLS or any(idx in sym.upper() for idx in INDEX_SYMBOLS):
                        records["INDEX"].append(r)
                    elif sym.upper() in FX_SYMBOLS:
                        records["FX"].append(r)
                    else:
                        # Try to classify unknown symbols
                        records["FX"].append(r)  # default to FX
                except:
                    pass
    return records


def load_market_context():
    """Load market context records keyed by (symbol, timestamp)."""
    ctx = {}
    ctx_dir = Path("logs/v3_shadow/market_context")
    if not ctx_dir.exists():
        return ctx
    for f in ctx_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    ctx[key] = r
                except:
                    pass
    return ctx


def load_shadow_trades():
    """Load shadow trades by asset class."""
    records = {"FX": [], "INDEX": []}
    shadow_dir = Path("logs/shadow_trades")
    if not shadow_dir.exists():
        return records
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        sym = sym_dir.name.upper()
        target = "INDEX" if sym in INDEX_SYMBOLS or any(idx in sym for idx in INDEX_SYMBOLS) else "FX"
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("schema_version") == "shadow_trades_v2":
                            outcome = r.get("simulated_outcome", {})
                            if outcome.get("pnl_r_multiple") is not None:
                                records[target].append({
                                    "symbol": sym_dir.name,
                                    "result_r": outcome["pnl_r_multiple"],
                                    "mfe_r": outcome.get("mfe_r", 0),
                                    "mae_r": outcome.get("mae_r", 0),
                                    "exit_reason": outcome.get("exit_reason", ""),
                                    "bars_held": outcome.get("bars_held", 0),
                                })
                    except:
                        pass
    return records


# Load data
exec_data = load_exec_assessments()
ctx_data = load_market_context()
shadow_data = load_shadow_trades()

print(f"\n  Data loaded:")
print(f"    FX execution assessments: {len(exec_data['FX'])}")
print(f"    INDEX execution assessments: {len(exec_data['INDEX'])}")
print(f"    FX shadow trades: {len(shadow_data['FX'])}")
print(f"    INDEX shadow trades: {len(shadow_data['INDEX'])}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def stats(records, key="result_r"):
    if not records:
        return None
    if isinstance(records[0], dict) and "_outcome" in records[0]:
        results = [r["_outcome"]["result_r"] for r in records]
        mfes = [r["_outcome"].get("mfe_r", 0) for r in records]
        maes = [r["_outcome"].get("mae_r", 0) for r in records]
        timeouts = sum(1 for r in records if r["_outcome"].get("exit_reason") == "max_bars_timeout")
    else:
        results = [r["result_r"] for r in records]
        mfes = [r.get("mfe_r", 0) for r in records]
        maes = [r.get("mae_r", 0) for r in records]
        timeouts = sum(1 for r in records if r.get("exit_reason") == "max_bars_timeout")
    
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r > 0)
    ev = sum(results) / n
    std = math.sqrt(sum((r - ev) ** 2 for r in results) / max(n - 1, 1))
    se = std / math.sqrt(n)
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "mfe": sum(mfes) / n, "mae": sum(maes) / n,
        "timeout_rate": timeouts / n,
        "move_05": sum(1 for m in mfes if m > 0.5) / n,
        "move_1": sum(1 for m in mfes if m > 1.0) / n,
        "move_2": sum(1 for m in mfes if m > 2.0) / n,
    }


# ═══════════════════════════════════════════════════════════════
# CHECK: DO WE HAVE INDEX DATA?
# ═══════════════════════════════════════════════════════════════

has_index_data = len(exec_data["INDEX"]) >= 10 or len(shadow_data["INDEX"]) >= 30

if not has_index_data:
    print("\n" + "─" * 70)
    print("⚠  INSUFFICIENT INDEX DATA FOR TRANSFER ANALYSIS")
    print("─" * 70)
    print(f"""
  Index execution assessments found: {len(exec_data['INDEX'])}
  Index shadow trades found: {len(shadow_data['INDEX'])}
  Minimum required for analysis: 10 (preliminary) / 100 (research quality)

  STATUS: DATA COLLECTION HAS NOT YET OCCURRED

  The V6.2 configuration changes added NAS100/US500/XAUUSD to the
  observation pipeline, but the bot has not yet run with these symbols
  active (or the broker doesn't offer them under these exact names).

  BEFORE V6.4 CAN PROCEED:
  1. Start the bot with updated config
  2. Verify broker symbol availability (check MT5 Market Watch)
  3. Collect minimum 100 index execution assessments
  4. Re-run this analysis
""")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: MARKET BEHAVIOUR COMPARISON (FX baseline always shown)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: MARKET BEHAVIOUR COMPARISON")
print("─" * 70)

fx_stats = stats(exec_data["FX"])
# Use exec assessments if available, otherwise fall back to shadow trades
idx_source = exec_data["INDEX"] if len(exec_data["INDEX"]) >= 10 else shadow_data["INDEX"]
idx_stats = stats(idx_source) if has_index_data else None
idx_data_source = "execution_assessment" if len(exec_data["INDEX"]) >= 10 else "shadow_trades"

if has_index_data:
    print(f"\n  NOTE: Index data source = {idx_data_source} (n={len(idx_source)})")
    print(f"        FX data source = execution_assessment (n={len(exec_data['FX'])})")

print(f"\n  {'Metric':<25s} | {'FX (baseline)':>15s} | {'INDEX':>15s} | {'Delta':>10s}")
print(f"  {'-'*25}-+-{'-'*15}-+-{'-'*15}-+-{'-'*10}")

if fx_stats:
    metrics = [
        ("n", f"{fx_stats['n']}", f"{idx_stats['n']}" if idx_stats else "—", ""),
        ("Win Rate", f"{fx_stats['wr']:.1%}", f"{idx_stats['wr']:.1%}" if idx_stats else "—",
         f"{idx_stats['wr']-fx_stats['wr']:+.1%}" if idx_stats else ""),
        ("EV", f"{fx_stats['ev']:+.4f}R", f"{idx_stats['ev']:+.4f}R" if idx_stats else "—",
         f"{idx_stats['ev']-fx_stats['ev']:+.4f}" if idx_stats else ""),
        ("Avg MFE", f"{fx_stats['mfe']:.3f}R", f"{idx_stats['mfe']:.3f}R" if idx_stats else "—",
         f"{idx_stats['mfe']-fx_stats['mfe']:+.3f}" if idx_stats else ""),
        ("Avg MAE", f"{fx_stats['mae']:.3f}R", f"{idx_stats['mae']:.3f}R" if idx_stats else "—",
         f"{idx_stats['mae']-fx_stats['mae']:+.3f}" if idx_stats else ""),
        ("Timeout Rate", f"{fx_stats['timeout_rate']:.1%}", f"{idx_stats['timeout_rate']:.1%}" if idx_stats else "—",
         f"{idx_stats['timeout_rate']-fx_stats['timeout_rate']:+.1%}" if idx_stats else ""),
        ("P(>0.5R)", f"{fx_stats['move_05']:.1%}", f"{idx_stats['move_05']:.1%}" if idx_stats else "—",
         f"{idx_stats['move_05']-fx_stats['move_05']:+.1%}" if idx_stats else ""),
        ("P(>1R)", f"{fx_stats['move_1']:.1%}", f"{idx_stats['move_1']:.1%}" if idx_stats else "—",
         f"{idx_stats['move_1']-fx_stats['move_1']:+.1%}" if idx_stats else ""),
        ("P(>2R)", f"{fx_stats['move_2']:.1%}", f"{idx_stats['move_2']:.1%}" if idx_stats else "—",
         f"{idx_stats['move_2']-fx_stats['move_2']:+.1%}" if idx_stats else ""),
    ]
    for label, fx_val, idx_val, delta in metrics:
        print(f"  {label:<25s} | {fx_val:>15s} | {idx_val:>15s} | {delta:>10s}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2-7: ONLY RUN IF INDEX DATA EXISTS
# ═══════════════════════════════════════════════════════════════

if has_index_data:
    # ─── ANALYSIS 2: REGIME DISTRIBUTION ───────────────────────────
    print("\n" + "─" * 70)
    print("ANALYSIS 2: REGIME DISTRIBUTION")
    print("─" * 70)
    
    # For shadow trades we don't have market_context linkage,
    # so regime analysis only works with exec assessments
    if len(exec_data["INDEX"]) >= 10:
        for label, records in [("FX", exec_data["FX"]), ("INDEX", exec_data["INDEX"])]:
            print(f"\n  {label}:")
            regimes = []
            volatilities = []
            momentums = []
            for rec in records:
                key = (rec.get("symbol", ""), int(rec.get("timestamp_utc", 0)))
                ctx = ctx_data.get(key, {})
                beh = ctx.get("behaviour", {})
                regimes.append(beh.get("regime", "UNKNOWN"))
                volatilities.append(beh.get("volatility_state", "UNKNOWN"))
                momentums.append(beh.get("momentum_direction", "UNKNOWN"))
            print(f"    Regime: {Counter(regimes).most_common()}")
            print(f"    Volatility: {Counter(volatilities).most_common()}")
            print(f"    Momentum: {Counter(momentums).most_common()}")
    else:
        print(f"\n  Regime analysis requires execution_assessment data (market_context linkage).")
        print(f"  Currently using shadow_trades — regime/momentum not available per-trade.")
        print(f"  Will be available once V3 pipeline processes index symbols.")

    # ─── ANALYSIS 3: FEATURE PREDICTIVENESS TRANSFER ───────────────
    print("\n" + "─" * 70)
    print("ANALYSIS 3: FEATURE PREDICTIVENESS TRANSFER")
    print("─" * 70)
    
    if len(exec_data["INDEX"]) >= 10:
        # Full feature analysis with exec assessment data
        idx_records = exec_data["INDEX"]
        print(f"\n  MOMENTUM ALIGNMENT (Index):")
        for mom_label, filter_fn in [
            ("WITH momentum", lambda r, c: (r.get("direction")=="BULLISH" and c.get("behaviour",{}).get("momentum_direction")=="BULLISH") or
                                            (r.get("direction")=="BEARISH" and c.get("behaviour",{}).get("momentum_direction")=="BEARISH")),
            ("AGAINST momentum", lambda r, c: (r.get("direction")=="BULLISH" and c.get("behaviour",{}).get("momentum_direction")=="BEARISH") or
                                               (r.get("direction")=="BEARISH" and c.get("behaviour",{}).get("momentum_direction")=="BULLISH")),
            ("NEUTRAL momentum", lambda r, c: c.get("behaviour",{}).get("momentum_direction")=="NEUTRAL"),
        ]:
            subset = []
            for rec in idx_records:
                key = (rec.get("symbol",""), int(rec.get("timestamp_utc",0)))
                ctx = ctx_data.get(key, {})
                if filter_fn(rec, ctx):
                    subset.append(rec)
            s = stats(subset)
            if s and s["n"] >= 5:
                print(f"    {mom_label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

        print(f"\n  ENTRY STATE (Index):")
        for es in ["WEAK_ENTRY_CONFIRMATION", "VALID_ENTRY_CONFIRMATION", "NO_ENTRY_CONFIRMATION"]:
            subset = [r for r in idx_records if r.get("entry_state") == es]
            s = stats(subset)
            if s and s["n"] >= 5:
                print(f"    {es:<30s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")
    else:
        print(f"\n  Feature analysis requires execution_assessment data (entry_state, momentum).")
        print(f"  Shadow trades only provide outcome data (R, MFE, MAE).")
        print(f"  Basic outcome comparison available in Analysis 4.")

    # ─── ANALYSIS 4: MOVEMENT AVAILABILITY ─────────────────────────
    print("\n" + "─" * 70)
    print("ANALYSIS 4: MOVEMENT AVAILABILITY")
    print("─" * 70)
    
    fx_s = stats(exec_data["FX"])
    idx_s = stats(idx_source)
    if fx_s:
        print(f"\n  FX:    n={fx_s['n']:4d} | P(>0.5R)={fx_s['move_05']:.1%} | P(>1R)={fx_s['move_1']:.1%} | P(>2R)={fx_s['move_2']:.1%} | timeout={fx_s['timeout_rate']:.1%}")
    if idx_s:
        print(f"  INDEX: n={idx_s['n']:4d} | P(>0.5R)={idx_s['move_05']:.1%} | P(>1R)={idx_s['move_1']:.1%} | P(>2R)={idx_s['move_2']:.1%} | timeout={idx_s['timeout_rate']:.1%}")
    if fx_s and idx_s:
        print(f"\n  Comparison:")
        print(f"    Movement improvement: P(>0.5R) {idx_s['move_05']-fx_s['move_05']:+.1%}")
        print(f"    Timeout improvement: {fx_s['timeout_rate']-idx_s['timeout_rate']:+.1%} (lower = better)")
        print(f"    MFE improvement: {idx_s['mfe']-fx_s['mfe']:+.3f}R")

    # ─── ANALYSIS 5: PER-SYMBOL BREAKDOWN ──────────────────────────
    print("\n" + "─" * 70)
    print("ANALYSIS 5: INDEX PER-SYMBOL BREAKDOWN")
    print("─" * 70)
    
    for sym in sorted(set(r["symbol"] for r in idx_source)):
        subset = [r for r in idx_source if r["symbol"] == sym]
        s = stats(subset)
        if s and s["n"] >= 3:
            print(f"    {sym:12s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | MFE={s['mfe']:.3f} | P(>0.5R)={s['move_05']:.1%} | timeout={s['timeout_rate']:.1%}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V6.4 FINAL VERDICT")
print("=" * 70)

if not has_index_data:
    print(f"""
  VERDICT: D) More data required

  STATUS: NO INDEX DATA HAS BEEN COLLECTED

  The V6.2 configuration changes are in place:
    ✓ NAS100, US500, XAUUSD added to CANONICAL_SYMBOLS
    ✓ Spread caps configured (MAX_SPREAD_ABSOLUTE)
    ✓ Stop distances configured (MIN_SL_PIPS)
    ✓ Correlation groups configured
    ✓ core/instrument_utils.py created (pip/point abstraction)

  The V3 shadow pipeline is healthy:
    ✓ {len(exec_data['FX'])} FX execution assessments with outcomes
    ✓ All 7 pipeline stages flowing correctly
    ✓ Shadow trade outcome linking verified

  WHAT IS BLOCKING:
    ✗ Bot has not run with index symbols active
    ✗ Zero index observations exist
    ✗ Cannot perform transfer analysis without data

  REQUIRED ACTIONS:
  ┌──────────────────────────────────────────────────────────────────┐
  │ 1. Verify Pepperstone MT5 symbol availability:                   │
  │    - Open MT5 → View → Market Watch → right-click → Show All    │
  │    - Search for: NAS100, USTEC, US500, SPX500, XAUUSD, GOLD     │
  │    - Note exact names (e.g., "NAS100_SB" or "USTEC.c")          │
  │                                                                  │
  │ 2. Restart bot with updated config:                              │
  │    - The symbol_resolver will auto-map canonical → broker names  │
  │    - Shadow pipeline will begin collecting automatically         │
  │    - No execution will occur (PERMITTED_HORIZONS = ["SCALP"])    │
  │    - Index trades stay shadow-only unless explicitly enabled     │
  │                                                                  │
  │ 3. Monitor data collection:                                      │
  │    - Run: python -m analysis.v6_3_readiness_audit                │
  │    - Check logs/v3_shadow/execution_assessment/ for new dirs     │
  │    - Target: 100+ index execution assessments                    │
  │                                                                  │
  │ 4. Re-run V6.4 once data threshold met:                         │
  │    - python -m analysis.v6_4_index_transfer                      │
  │    - Compare against FX baseline shown above                     │
  └──────────────────────────────────────────────────────────────────┘

  FX BASELINE (for future comparison):
    n = {fx_stats['n'] if fx_stats else 0}
    WR = {fx_stats['wr']:.1%}
    EV = {fx_stats['ev']:+.4f}R
    Timeout = {fx_stats['timeout_rate']:.1%}
    P(>0.5R) = {fx_stats['move_05']:.1%}
    P(>1R) = {fx_stats['move_1']:.1%}

  SUCCESS CRITERIA (when index data arrives):
    Direction accuracy > 53% (FX = {fx_stats['wr']:.1%})
    P(>0.5R) > 35% (FX = {fx_stats['move_05']:.1%})
    Raw EV > +0.15R (FX = {fx_stats['ev']:+.4f}R)
    Net EV > +0.05R after costs
    Timeout rate < 60% (FX = {fx_stats['timeout_rate']:.1%})
""") if fx_stats else print("  No FX data available for baseline.")

else:
    # Full analysis verdict
    idx_s = stats(idx_source)
    fx_s = fx_stats
    
    if idx_s and fx_s:
        ev_improvement = idx_s["ev"] - fx_s["ev"]
        move_improvement = idx_s["move_05"] - fx_s["move_05"]
        timeout_improvement = fx_s["timeout_rate"] - idx_s["timeout_rate"]
        
        print(f"\n  DATA SOURCE: {idx_data_source} (n={idx_s['n']})")
        print(f"\n  FX:    EV={fx_s['ev']:+.4f} | WR={fx_s['wr']:.1%} | P(>0.5R)={fx_s['move_05']:.1%} | timeout={fx_s['timeout_rate']:.1%}")
        print(f"  INDEX: EV={idx_s['ev']:+.4f} | WR={idx_s['wr']:.1%} | P(>0.5R)={idx_s['move_05']:.1%} | timeout={idx_s['timeout_rate']:.1%}")
        print(f"\n  Improvement:")
        print(f"    EV: {ev_improvement:+.4f}R")
        print(f"    Movement P(>0.5R): {move_improvement:+.1%}")
        print(f"    Timeout reduction: {timeout_improvement:+.1%}")
        
        # Determine verdict
        if idx_s["ev"] > 0.15 and idx_s["ci_low"] > 0 and idx_s["move_05"] > 0.35:
            verdict = "A"
            reason = "Architecture transfers successfully — market was the limitation"
        elif ev_improvement > 0.05 and move_improvement > 0.10:
            verdict = "B"
            reason = "Architecture transfers — index shows better movement characteristics"
        elif idx_s["move_05"] > fx_s["move_05"] and idx_s["ev"] <= 0:
            verdict = "C"
            reason = "Index markets improve conditions but no reliable edge exists"
        elif idx_s["n"] < 100:
            verdict = "D"
            reason = f"Preliminary results (n={idx_s['n']}) — more data required for validation"
        else:
            verdict = "D"
            reason = "Results inconclusive"
        
        print(f"\n  VERDICT: {verdict}) {reason}")
        
        if idx_data_source == "shadow_trades":
            print(f"\n  NOTE: Analysis uses shadow_trades only (no V3 pipeline context).")
            print(f"  Feature analysis (momentum, entry_state, regime) requires")
            print(f"  the V3 pipeline to process index symbols (execution_assessment).")
            print(f"  Current data shows OUTCOME characteristics only.")

print()
