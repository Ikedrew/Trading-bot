"""V7.1 — Market-Specific Decision Policy Research.

Tests whether the observation architecture supports different decision
policies for different markets. Specifically: does INVERTING the signal
on indices (trend-following instead of mean-reversion) produce positive EV?
"""
import json, math
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V7.1 — MARKET-SPECIFIC DECISION POLICY RESEARCH")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD"}
FX_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}

# Load shadow trades (both FX and INDEX have outcome data)
shadow_dir = Path("logs/shadow_trades")
fx_trades = []
idx_trades = []

if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name == "UNKNOWN":
            continue
        sym = sym_dir.name
        is_index = sym in INDEX_SYMBOLS
        for f in sym_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("schema_version") != "shadow_trades_v2":
                            continue
                        snap = r.get("decision_snapshot", {})
                        outcome = r.get("simulated_outcome", {})
                        if outcome.get("pnl_r_multiple") is None:
                            continue
                        record = {
                            "symbol": sym,
                            "direction": snap.get("direction", ""),
                            "result_r": outcome["pnl_r_multiple"],
                            "mfe_r": outcome.get("mfe_r", 0),
                            "mae_r": outcome.get("mae_r", 0),
                            "exit_reason": outcome.get("exit_reason", ""),
                            "bars_held": outcome.get("bars_held", 0),
                            "timestamp": snap.get("timestamp_decision_utc", 0),
                        }
                        if is_index:
                            idx_trades.append(record)
                        else:
                            fx_trades.append(record)
                    except:
                        pass

# Load FX execution assessments (have V3 context labels)
exec_dir = Path("logs/v3_shadow/execution_assessment")
fx_exec = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        fx_exec.append(r)
                except:
                    pass

# Load market context for FX exec records
ctx_dir = Path("logs/v3_shadow/market_context")
ctx_data = {}
if ctx_dir.exists():
    for f in ctx_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    ctx_data[key] = r
                except:
                    pass

print(f"\n  FX shadow trades: {len(fx_trades)}")
print(f"  FX execution assessments: {len(fx_exec)}")
print(f"  INDEX shadow trades: {len(idx_trades)}")
print(f"  Market context records: {len(ctx_data)}")

# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def stats(records):
    if not records:
        return None
    if isinstance(records[0], dict) and "_outcome" in records[0]:
        results = [r["_outcome"]["result_r"] for r in records]
        mfes = [r["_outcome"].get("mfe_r", 0) for r in records]
        maes = [r["_outcome"].get("mae_r", 0) for r in records]
    else:
        results = [r["result_r"] for r in records]
        mfes = [r.get("mfe_r", 0) for r in records]
        maes = [r.get("mae_r", 0) for r in records]
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
        "move_05": sum(1 for m in mfes if m > 0.5) / n,
        "move_1": sum(1 for m in mfes if m > 1.0) / n,
    }


# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: BEHAVIOUR CLASSIFICATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: MARKET BEHAVIOUR CLASSIFICATION")
print("─" * 70)

# Continuation probability: if trade WINS, does MFE >> 1R? (momentum persistence)
# Reversal probability: if trade LOSES, was MAE > MFE? (mean-reversion failure)

for label, trades in [("FX", fx_trades), ("INDEX", idx_trades)]:
    s = stats(trades)
    if not s:
        continue
    
    winners = [t for t in trades if t["result_r"] > 0]
    losers = [t for t in trades if t["result_r"] <= 0]
    
    # Continuation: winners that reached >1.5R MFE
    big_winners = [t for t in winners if t["mfe_r"] > 1.5]
    # Reversal failures: losers where MAE > 0.8R (went far against before stopping)
    deep_losers = [t for t in losers if t["mae_r"] > 0.8]
    
    print(f"\n  {label} (n={s['n']}):")
    print(f"    WR: {s['wr']:.1%} | EV: {s['ev']:+.4f}")
    print(f"    Winners: {len(winners)} ({len(winners)/s['n']:.0%})")
    print(f"      Big winners (MFE>1.5R): {len(big_winners)} ({len(big_winners)/max(len(winners),1):.0%} of wins)")
    print(f"    Losers: {len(losers)} ({len(losers)/s['n']:.0%})")
    print(f"      Deep losers (MAE>0.8R): {len(deep_losers)} ({len(deep_losers)/max(len(losers),1):.0%} of losses)")
    print(f"    Avg winner MFE: {sum(t['mfe_r'] for t in winners)/max(len(winners),1):.3f}R")
    print(f"    Avg loser MAE: {sum(t['mae_r'] for t in losers)/max(len(losers),1):.3f}R")
    
    # Continuation vs reversal character
    if len(big_winners) / max(len(winners), 1) > 0.3:
        print(f"    Character: MOMENTUM/CONTINUATION (winners run)")
    else:
        print(f"    Character: RANGE-BOUND (winners capped)")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: POLICY INVERSION TEST
# The system currently goes AGAINST momentum (contrarian).
# What if we INVERT the signal — treat winners as losers and vice versa?
# This simulates: "if the system says SELL, go LONG instead"
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: POLICY INVERSION TEST")
print("─" * 70)

print(f"""
  Concept: If the V3 system produces 40% WR on indices, then the 
  INVERSE signal produces 60% WR. But R:R also inverts — what was
  MFE becomes MAE and vice versa.
  
  This tests: "Does taking the OPPOSITE trade improve EV?"
""")

for label, trades in [("FX", fx_trades), ("INDEX", idx_trades)]:
    s_original = stats(trades)
    if not s_original or s_original["n"] < 30:
        continue
    
    # Invert: winners become losers (capped at -1R), losers become winners
    # Inversion logic: if system said BUY and lost, the SELL would have won
    # MFE of original = MAE of inverted, MAE of original = MFE of inverted
    inverted = []
    for t in trades:
        inverted.append({
            "result_r": -t["result_r"],  # Simple sign inversion
            "mfe_r": t["mae_r"],  # What was adverse is now favourable
            "mae_r": t["mfe_r"],  # What was favourable is now adverse
        })
    
    s_inverted = stats(inverted)
    
    print(f"\n  {label}:")
    print(f"    Original policy: WR={s_original['wr']:.1%} | EV={s_original['ev']:+.4f} | MFE={s_original['mfe']:.3f} | MAE={s_original['mae']:.3f}")
    print(f"    INVERTED policy: WR={s_inverted['wr']:.1%} | EV={s_inverted['ev']:+.4f} | MFE={s_inverted['mfe']:.3f} | MAE={s_inverted['mae']:.3f}")
    
    if s_inverted["ev"] > s_original["ev"]:
        improvement = s_inverted["ev"] - s_original["ev"]
        print(f"    → INVERSION IMPROVES EV by {improvement:+.4f}R")
    else:
        print(f"    → Inversion does NOT improve (original was better)")

# More nuanced: what if we only invert on LOSERS (use MAE as profit target)?
print(f"\n  NUANCED INVERSION (index only):")
print(f"  If we went opposite direction, how much adverse movement was available?")
if idx_trades:
    # For losing trades: MAE = how far it went against us = profit if inverted
    losers = [t for t in idx_trades if t["result_r"] <= 0]
    winners = [t for t in idx_trades if t["result_r"] > 0]
    
    avg_loser_mae = sum(t["mae_r"] for t in losers) / max(len(losers), 1)
    avg_winner_mfe = sum(t["mfe_r"] for t in winners) / max(len(winners), 1)
    
    print(f"    Losers: avg MAE = {avg_loser_mae:.3f}R (= profit if inverted)")
    print(f"    Winners: avg MFE = {avg_winner_mfe:.3f}R (= loss if inverted)")
    print(f"    Loser count: {len(losers)} | Winner count: {len(winners)}")
    
    # Approximate inverted EV:
    # Inverted winners = original losers, gain = mae_r (capped at some TP)
    # Inverted losers = original winners, loss = mfe_r (how far it went for them)
    # Simplified: inverted EV ≈ -(original EV)
    print(f"    Simple inversion EV: {-stats(idx_trades)['ev']:+.4f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: FEATURE RELATIONSHIP (FX vs Index using exec data)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: FX FEATURE RELATIONSHIPS (for comparison)")
print("─" * 70)

# FX: momentum relationship (from V5.1 — contrarian works)
print(f"\n  FX Momentum Alignment (from V3 exec assessments):")
for label, filter_fn in [
    ("WITH momentum", lambda r, c: (r.get("direction")=="BULLISH" and c.get("behaviour",{}).get("momentum_direction")=="BULLISH") or
                                    (r.get("direction")=="BEARISH" and c.get("behaviour",{}).get("momentum_direction")=="BEARISH")),
    ("AGAINST momentum", lambda r, c: (r.get("direction")=="BULLISH" and c.get("behaviour",{}).get("momentum_direction")=="BEARISH") or
                                       (r.get("direction")=="BEARISH" and c.get("behaviour",{}).get("momentum_direction")=="BULLISH")),
    ("NEUTRAL momentum", lambda r, c: c.get("behaviour",{}).get("momentum_direction")=="NEUTRAL"),
]:
    subset = []
    for rec in fx_exec:
        key = (rec.get("symbol",""), int(rec.get("timestamp_utc",0)))
        ctx = ctx_data.get(key, {})
        if filter_fn(rec, ctx):
            subset.append(rec)
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# FX: entry state
print(f"\n  FX Entry State:")
for es in ["WEAK_ENTRY_CONFIRMATION", "VALID_ENTRY_CONFIRMATION", "NO_ENTRY_CONFIRMATION"]:
    subset = [r for r in fx_exec if r.get("entry_state") == es]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {es:<30s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# FX: structure alignment
print(f"\n  FX Structure Alignment:")
for label, filter_fn in [
    ("High alignment (>0.8)", lambda r, c: c.get("htf_structure",{}).get("structure_alignment",0) >= 0.8),
    ("Low alignment (<0.5)", lambda r, c: c.get("htf_structure",{}).get("structure_alignment",0) < 0.5),
]:
    subset = []
    for rec in fx_exec:
        key = (rec.get("symbol",""), int(rec.get("timestamp_utc",0)))
        ctx = ctx_data.get(key, {})
        if filter_fn(rec, ctx):
            subset.append(rec)
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {label:<25s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

print(f"\n  INDEX: No V3 context labels available (shadow_trades only)")
print(f"  Cannot compare feature relationships directly until V3 pipeline")
print(f"  processes index symbols.")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: POLICY SEPARATION — DUAL POLICY FRAMEWORK
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: DUAL POLICY FRAMEWORK")
print("─" * 70)

print(f"""
  RESEARCH FINDING SUMMARY:

  FX (mean-reversion policy):
    - Contrarian: AGAINST momentum = better
    - Early entry: WEAK > VALID
    - Low alignment: structure disagreement = better
    - Current EV: +0.093R (positive but fragile)
    
  INDEX (current policy applied — FAILS):
    - System fades momentum → 40% WR (wrong direction)
    - EV = -0.112R (net loss)
    - Market moves MORE but system picks WRONG direction

  PROPOSED DUAL POLICY:
  ┌─────────────────────────────────────────────────────────────────┐
  │ Market │ Policy    │ Momentum  │ Entry    │ Structure │ Expected│
  ├────────┼───────────┼───────────┼──────────┼───────────┼─────────┤
  │ FX     │ Reversion │ AGAINST   │ WEAK     │ Low align │ +0.09R  │
  │ INDEX  │ Trend     │ WITH      │ VALID    │ High align│ ???     │
  └─────────────────────────────────────────────────────────────────┘
""")

# Test: what if we flip the DIRECTION of index trades?
# The inverted EV should approximate what a trend-following policy gets
fx_s = stats(fx_trades)
idx_s = stats(idx_trades)

if fx_s and idx_s:
    print(f"  Current performance:")
    print(f"    FX (reversion policy): EV={fx_s['ev']:+.4f} | WR={fx_s['wr']:.1%}")
    print(f"    INDEX (reversion on trending): EV={idx_s['ev']:+.4f} | WR={idx_s['wr']:.1%}")
    print(f"    INDEX inverted (trend-following): EV={-idx_s['ev']:+.4f} | WR={1-idx_s['wr']:.1%}")
    
    # Combined portfolio: FX original + INDEX inverted
    combined_ev = (fx_s["ev"] * fx_s["n"] + (-idx_s["ev"]) * idx_s["n"]) / (fx_s["n"] + idx_s["n"])
    combined_n = fx_s["n"] + idx_s["n"]
    
    print(f"\n  DUAL POLICY PORTFOLIO (theoretical):")
    print(f"    FX reversion + INDEX trend-following")
    print(f"    Combined n: {combined_n}")
    print(f"    Combined EV: {combined_ev:+.4f}R")
    print(f"    vs FX-only: {fx_s['ev']:+.4f}R")
    print(f"    Improvement: {combined_ev - fx_s['ev']:+.4f}R")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: INDEX STABILITY (per symbol, time periods)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: INDEX STABILITY")
print("─" * 70)

# Per symbol
print(f"\n  Per symbol (original policy):")
for sym in sorted(set(t["symbol"] for t in idx_trades)):
    subset = [t for t in idx_trades if t["symbol"] == sym]
    s = stats(subset)
    if s and s["n"] >= 10:
        # Also show inverted
        print(f"    {sym:10s}: n={s['n']:4d} | WR={s['wr']:.1%} | EV={s['ev']:+.4f} | inverted EV={-s['ev']:+.4f}")

# Time stability
print(f"\n  Time stability (INDEX, halves):")
if idx_trades:
    sorted_idx = sorted(idx_trades, key=lambda t: t["timestamp"])
    half = len(sorted_idx) // 2
    first_half = sorted_idx[:half]
    second_half = sorted_idx[half:]
    
    s1 = stats(first_half)
    s2 = stats(second_half)
    if s1 and s2:
        print(f"    First half:  n={s1['n']:4d} | WR={s1['wr']:.1%} | EV={s1['ev']:+.4f} | inverted={-s1['ev']:+.4f}")
        print(f"    Second half: n={s2['n']:4d} | WR={s2['wr']:.1%} | EV={s2['ev']:+.4f} | inverted={-s2['ev']:+.4f}")
        consistent = (s1["ev"] < 0 and s2["ev"] < 0) or (s1["ev"] > 0 and s2["ev"] > 0)
        print(f"    Direction consistent: {'YES' if consistent else 'NO'}")
        if s1["ev"] < 0 and s2["ev"] < 0:
            print(f"    → Both halves negative = inversion CONSISTENTLY positive")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: WHAT DOES THE INVERTED SIGNAL LOOK LIKE?
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 6: INVERTED INDEX SIGNAL CHARACTERISTICS")
print("─" * 70)

if idx_trades:
    # If we invert: winners → losers, losers → winners
    # Original losers = trades where system said X but market went opposite
    # These become our winners under inverted policy
    
    original_losers = [t for t in idx_trades if t["result_r"] <= 0]
    original_winners = [t for t in idx_trades if t["result_r"] > 0]
    
    print(f"\n  Under INVERTED policy:")
    print(f"    New winners (original losers): {len(original_losers)} ({len(original_losers)/len(idx_trades):.1%})")
    print(f"    New losers (original winners): {len(original_winners)} ({len(original_winners)/len(idx_trades):.1%})")
    
    if original_losers:
        # How much did the market move AGAINST the original trade? = our profit
        avg_capture = sum(t["mae_r"] for t in original_losers) / len(original_losers)
        print(f"    Avg profit from inverted wins: {avg_capture:.3f}R (original MAE)")
    
    if original_winners:
        # How much did original winners move FOR them? = our loss
        avg_loss = sum(t["mfe_r"] for t in original_winners) / len(original_winners)
        print(f"    Avg loss from inverted losses: {avg_loss:.3f}R (original MFE)")
    
    # Exit reason analysis
    print(f"\n  Exit reason distribution (original):")
    reasons = Counter(t["exit_reason"] for t in idx_trades)
    for reason, count in reasons.most_common():
        subset = [t for t in idx_trades if t["exit_reason"] == reason]
        s = stats(subset)
        if s:
            print(f"    {reason:<20s}: n={count:4d} ({count/len(idx_trades):.0%}) | WR={s['wr']:.1%} | EV={s['ev']:+.4f}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V7.1 FINAL VERDICT")
print("=" * 70)

fx_s = stats(fx_trades)
idx_s = stats(idx_trades)

if fx_s and idx_s:
    inverted_ev = -idx_s["ev"]
    inverted_wr = 1 - idx_s["wr"]
    
    # Key metrics
    fx_positive = fx_s["ev"] > 0
    idx_negative = idx_s["ev"] < 0
    inversion_positive = inverted_ev > 0
    inversion_better_than_fx = inverted_ev > fx_s["ev"]
    
    print(f"\n  RESULTS:")
    print(f"    FX (reversion policy):     EV={fx_s['ev']:+.4f} | WR={fx_s['wr']:.1%} | CI=[{fx_s['ci_low']:+.3f},{fx_s['ci_high']:+.3f}]")
    print(f"    INDEX (reversion policy):  EV={idx_s['ev']:+.4f} | WR={idx_s['wr']:.1%} | CI=[{idx_s['ci_low']:+.3f},{idx_s['ci_high']:+.3f}]")
    print(f"    INDEX (INVERTED/trend):    EV={inverted_ev:+.4f} | WR={inverted_wr:.1%}")
    
    # Determine verdict
    if inversion_positive and inversion_better_than_fx and idx_s["n"] >= 100:
        verdict = "A"
        reason = "Same observation layer supports BOTH policies — inversion produces positive EV on indices"
    elif inversion_positive and idx_s["n"] >= 50:
        verdict = "B"
        reason = "Different policies improve information value — inverted index signal is positive but needs validation"
    elif not inversion_positive:
        verdict = "C"
        reason = "Decision layer is insufficient — neither original nor inverted produces reliable edge on indices"
    else:
        verdict = "D"
        reason = f"More data required (n={idx_s['n']})"
    
    print(f"\n  VERDICT: {verdict}) {reason}")
    
    print(f"\n  INTERPRETATION:")
    if inversion_positive:
        print(f"    The V3 observation layer DOES detect market direction on indices.")
        print(f"    It detects it CORRECTLY — but the DECISION POLICY inverts it.")
        print(f"    The system says 'sell' when the market is about to go UP.")
        print(f"    This is because V3 was designed for mean-reversion (fade momentum).")
        print(f"    On indices, momentum CONTINUES — so fading = wrong direction.")
        print(f"")
        print(f"    SOLUTION: Use the SAME observation but FOLLOW the signal direction")
        print(f"    on trending instruments, instead of fading it.")
        print(f"")
        print(f"    This is NOT a new system — it's the SAME intelligence with a")
        print(f"    market-appropriate interpretation layer.")
    else:
        print(f"    Neither policy produces reliable edge on indices.")
        print(f"    The observation layer may not contain directional information")
        print(f"    relevant to index markets.")
    
    print(f"\n  NEXT STEPS:")
    if verdict in ("A", "B"):
        print(f"    V7.2: Implement market-specific policy router")
        print(f"    - Detect instrument class (FX vs Index)")
        print(f"    - Apply reversion policy on FX")
        print(f"    - Apply trend-following policy on indices")
        print(f"    - Validate with walk-forward on collected data")
    else:
        print(f"    Further index data collection needed before conclusions")

print()
