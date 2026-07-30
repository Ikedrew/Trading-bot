"""V5.1 — Market Regime Intelligence Research.

Tests whether market environment classification (trend, volatility, location,
momentum) provides predictive information beyond V3/V4 reasoning.
"""
import json, math, random
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V5.1 — MARKET REGIME INTELLIGENCE RESEARCH")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

# Load market context (regime/volatility/location data)
ctx_dir = Path("logs/v3_shadow/market_context")
ctx_records = {}  # keyed by (symbol, timestamp)
if ctx_dir.exists():
    for f in ctx_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    ctx_records[key] = r
                except:
                    pass

# Load market understanding (HTF structure, ATR data)
mu_dir = Path("logs/v3_shadow/market_understanding")
mu_records = {}
if mu_dir.exists():
    for f in mu_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    key = (r.get("symbol", ""), int(r.get("timestamp_utc", 0)))
                    mu_records[key] = r
                except:
                    pass

# Load execution assessments (with outcomes)
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

# Load shadow trades for currency strength
PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
}
shadow_dir = Path("logs/shadow_trades")
trades_by_time = defaultdict(list)
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
                        sym = identity.get("symbol", "")
                        ts = snap.get("timestamp_decision_utc", 0)
                        direction = snap.get("direction", "")
                        key = (sym, int(ts))
                        if sym and ts and direction and key not in seen_trades:
                            seen_trades.add(key)
                            trades_by_time[int(ts)].append({
                                "symbol": sym, "direction": direction,
                            })
                    except:
                        pass

print(f"Market context records: {len(ctx_records)}")
print(f"Market understanding records: {len(mu_records)}")
print(f"Execution assessments (with outcome): {len(exec_records)}")

# ═══════════════════════════════════════════════════════════════
# BUILD ENRICHED DATASET (execution + market context + understanding)
# ═══════════════════════════════════════════════════════════════

enriched = []
for rec in exec_records:
    sym = rec.get("symbol", "")
    ts = int(rec.get("timestamp_utc", 0))
    key = (sym, ts)
    
    # Get market context
    ctx = ctx_records.get(key, {})
    mu = mu_records.get(key, {})
    
    # Extract regime features
    behaviour = ctx.get("behaviour", {})
    location = ctx.get("location", {})
    htf = ctx.get("htf_structure", {})
    
    # Market understanding features
    h4 = mu.get("h4", {})
    h1 = mu.get("h1", {})
    m5 = mu.get("m5", {})
    m15 = mu.get("m15", {})
    
    # Regime classification
    regime = behaviour.get("regime", "UNKNOWN")
    volatility = behaviour.get("volatility_state", "UNKNOWN")
    momentum_dir = behaviour.get("momentum_direction", "UNKNOWN")
    momentum_str = behaviour.get("momentum_strength", 0)
    expansion = behaviour.get("expansion_state", "UNKNOWN")
    
    # Location features
    loc_type = location.get("location_type", "UNKNOWN")
    inside_zone = location.get("inside_institutional_zone", False)
    prem_disc = location.get("premium_discount", "UNKNOWN")
    range_pos = location.get("range_position", 0.5)
    
    # HTF features
    macro_bias = htf.get("macro_bias", "UNKNOWN")
    bos_active = htf.get("bos_active", False)
    bos_dir = htf.get("bos_direction", "")
    structure_alignment = htf.get("structure_alignment", 0)
    
    # H4 trend
    h4_trend = h4.get("trend", "UNKNOWN")
    h4_volatility = h4.get("volatility_state", "UNKNOWN")
    
    # H1 structure
    h1_bos = h1.get("bos_confirmed", False)
    h1_trend = h1.get("dominant_trend", "UNKNOWN")
    structural_clarity = h1.get("structural_clarity", 0)
    
    # M5 features
    spread_atr = m5.get("spread_atr_ratio", 0)
    
    # Outcome
    outcome = rec["_outcome"]
    result_r = outcome["result_r"]
    mfe_r = outcome.get("mfe_r", 0)
    mae_r = outcome.get("mae_r", 0)
    
    enriched.append({
        "symbol": sym, "timestamp": ts,
        "direction": rec.get("direction", ""),
        "entry_state": rec.get("entry_state", ""),
        "opp_state": rec.get("opportunity_state", ""),
        "horizon": rec.get("horizon", ""),
        "result_r": result_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "win": result_r > 0,
        # Regime features
        "regime": regime,
        "volatility": volatility,
        "momentum_dir": momentum_dir,
        "momentum_str": momentum_str,
        "expansion": expansion,
        # Location features
        "loc_type": loc_type,
        "inside_zone": inside_zone,
        "prem_disc": prem_disc,
        "range_pos": range_pos,
        # HTF features
        "macro_bias": macro_bias,
        "bos_active": bos_active,
        "bos_dir": bos_dir,
        "structure_alignment": structure_alignment,
        "h4_trend": h4_trend,
        "h1_trend": h1_trend,
        "structural_clarity": structural_clarity,
        # Cost features
        "spread_atr": spread_atr,
        # Has context
        "has_ctx": bool(ctx),
        "has_mu": bool(mu),
    })

with_ctx = [r for r in enriched if r["has_ctx"]]
print(f"\nEnriched records: {len(enriched)}")
print(f"  With market context: {len(with_ctx)}")
print(f"  Without context: {len(enriched) - len(with_ctx)}")

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
    # Movement probability
    move_05 = sum(1 for s in subset if s["mfe_r"] > 0.5) / n
    move_1 = sum(1 for s in subset if s["mfe_r"] > 1.0) / n
    move_2 = sum(1 for s in subset if s["mfe_r"] > 2.0) / n
    return {
        "n": n, "wr": wins / n, "ev": ev, "std": std,
        "ci_low": ev - 1.96 * se, "ci_high": ev + 1.96 * se,
        "mfe": sum(mfe_vals) / n, "mae": sum(mae_vals) / n,
        "move_05": move_05, "move_1": move_1, "move_2": move_2,
    }


def print_row(label, s, width=35):
    if s and s["n"] >= 5:
        print(f"  {label:<{width}s} | {s['n']:>4d} | {s['wr']:.1%} | "
              f"{s['ev']:>+7.4f} | {s['mfe']:.3f} | {s['mae']:.3f} | "
              f"{s['move_05']:.0%} | {s['move_1']:.0%}")


# ═══════════════════════════════════════════════════════════════
# ANALYSIS 1: REGIME DATA INVENTORY
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 1: AVAILABLE REGIME DATA")
print("─" * 70)

print(f"\n  Regime states: {Counter(r['regime'] for r in with_ctx).most_common()}")
print(f"  Volatility: {Counter(r['volatility'] for r in with_ctx).most_common()}")
print(f"  Momentum: {Counter(r['momentum_dir'] for r in with_ctx).most_common()}")
print(f"  Expansion: {Counter(r['expansion'] for r in with_ctx).most_common()}")
print(f"  Location type: {Counter(r['loc_type'] for r in with_ctx).most_common()}")
print(f"  Inside zone: {Counter(r['inside_zone'] for r in with_ctx).most_common()}")
print(f"  Premium/Discount: {Counter(r['prem_disc'] for r in with_ctx).most_common()}")
print(f"  Macro bias: {Counter(r['macro_bias'] for r in with_ctx).most_common()}")
print(f"  H4 trend: {Counter(r['h4_trend'] for r in with_ctx).most_common()}")
print(f"  H1 trend: {Counter(r['h1_trend'] for r in with_ctx).most_common()}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 2: REGIME PERFORMANCE SEPARATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 2: REGIME PERFORMANCE SEPARATION")
print("─" * 70)

header = f"  {'Category':<35s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'MFE':>5s} | {'MAE':>5s} | {'>0.5R':>4s} | {'>1R':>4s}"
sep = f"  {'-'*35}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-{'-'*4}-+-{'-'*4}"

# A) By Regime
print(f"\n  A) BY REGIME (trend environment):")
print(header)
print(sep)
for regime in ["TRENDING", "RANGING", "NEUTRAL", "UNKNOWN"]:
    subset = [r for r in with_ctx if r["regime"] == regime]
    print_row(f"Regime: {regime}", stats(subset))

# B) By Volatility
print(f"\n  B) BY VOLATILITY:")
print(header)
print(sep)
for vol in ["LOW", "NEUTRAL", "HIGH", "EXPANDING", "CONTRACTING", "UNKNOWN"]:
    subset = [r for r in with_ctx if r["volatility"] == vol]
    print_row(f"Volatility: {vol}", stats(subset))

# C) By Momentum
print(f"\n  C) BY MOMENTUM DIRECTION:")
print(header)
print(sep)
for mom in ["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]:
    subset = [r for r in with_ctx if r["momentum_dir"] == mom]
    print_row(f"Momentum: {mom}", stats(subset))

# D) By Expansion State
print(f"\n  D) BY EXPANSION STATE:")
print(header)
print(sep)
for exp in ["EXPANDING", "CONTRACTING", "NEUTRAL", "UNKNOWN"]:
    subset = [r for r in with_ctx if r["expansion"] == exp]
    print_row(f"Expansion: {exp}", stats(subset))

# E) By Location
print(f"\n  E) BY LOCATION TYPE:")
print(header)
print(sep)
for loc in set(r["loc_type"] for r in with_ctx):
    subset = [r for r in with_ctx if r["loc_type"] == loc]
    print_row(f"Location: {loc}", stats(subset))

# F) Inside Zone
print(f"\n  F) INSIDE INSTITUTIONAL ZONE:")
print(header)
print(sep)
for iz in [True, False]:
    subset = [r for r in with_ctx if r["inside_zone"] == iz]
    print_row(f"Inside zone: {iz}", stats(subset))

# G) Premium/Discount
print(f"\n  G) PREMIUM vs DISCOUNT:")
print(header)
print(sep)
for pd in ["PREMIUM", "DISCOUNT", "EQUILIBRIUM", "UNKNOWN"]:
    subset = [r for r in with_ctx if r["prem_disc"] == pd]
    print_row(f"P/D: {pd}", stats(subset))

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 3: REGIME × V3 SIGNAL INTERACTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 3: REGIME × V3 TIMING INTERACTION")
print("─" * 70)

# Does WEAK timing work differently by regime?
weak_records = [r for r in with_ctx if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]

print(f"\n  WEAK entries by regime:")
print(header)
print(sep)
for regime in ["TRENDING", "RANGING", "NEUTRAL"]:
    subset = [r for r in weak_records if r["regime"] == regime]
    print_row(f"WEAK + {regime}", stats(subset))

print(f"\n  WEAK entries by volatility:")
print(header)
print(sep)
for vol in ["LOW", "NEUTRAL", "HIGH"]:
    subset = [r for r in weak_records if r["volatility"] == vol]
    print_row(f"WEAK + vol={vol}", stats(subset))

# HTF alignment with trade direction
print(f"\n  WEAK entries by HTF alignment:")
print(header)
print(sep)
weak_htf_aligned = [r for r in weak_records
                    if (r["direction"] == "BULLISH" and r["macro_bias"] == "BULLISH") or
                       (r["direction"] == "BEARISH" and r["macro_bias"] == "BEARISH")]
weak_htf_counter = [r for r in weak_records
                    if (r["direction"] == "BULLISH" and r["macro_bias"] == "BEARISH") or
                       (r["direction"] == "BEARISH" and r["macro_bias"] == "BULLISH")]
weak_htf_neutral = [r for r in weak_records
                    if r["macro_bias"] == "NEUTRAL"]

print_row("WEAK + HTF ALIGNED", stats(weak_htf_aligned))
print_row("WEAK + HTF COUNTER", stats(weak_htf_counter))
print_row("WEAK + HTF NEUTRAL", stats(weak_htf_neutral))

# Structure alignment score
print(f"\n  WEAK entries by structure alignment score:")
print(header)
print(sep)
for threshold, label in [(0.8, "High (>0.8)"), (0.5, "Medium (0.5-0.8)"), (0.0, "Low (<0.5)")]:
    if threshold == 0.8:
        subset = [r for r in weak_records if r["structure_alignment"] >= 0.8]
    elif threshold == 0.5:
        subset = [r for r in weak_records if 0.5 <= r["structure_alignment"] < 0.8]
    else:
        subset = [r for r in weak_records if r["structure_alignment"] < 0.5]
    print_row(f"WEAK + struct_align {label}", stats(subset))

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 4: MOVEMENT AVAILABILITY BY REGIME
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 4: MOVEMENT AVAILABILITY (the 92.5% problem)")
print("─" * 70)

print(f"\n  Baseline movement probability:")
s_base = stats(with_ctx)
if s_base:
    print(f"    All records: P(>0.5R)={s_base['move_05']:.1%} | P(>1R)={s_base['move_1']:.1%} | P(>2R)={s_base['move_2']:.1%}")

print(f"\n  Movement by REGIME:")
for regime in ["TRENDING", "RANGING", "NEUTRAL"]:
    subset = [r for r in with_ctx if r["regime"] == regime]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {regime:<12s}: P(>0.5R)={s['move_05']:.1%} | P(>1R)={s['move_1']:.1%} | P(>2R)={s['move_2']:.1%} (n={s['n']})")

print(f"\n  Movement by VOLATILITY:")
for vol in ["LOW", "NEUTRAL", "HIGH"]:
    subset = [r for r in with_ctx if r["volatility"] == vol]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {vol:<12s}: P(>0.5R)={s['move_05']:.1%} | P(>1R)={s['move_1']:.1%} | P(>2R)={s['move_2']:.1%} (n={s['n']})")

print(f"\n  Movement by EXPANSION STATE:")
for exp in ["EXPANDING", "CONTRACTING", "NEUTRAL"]:
    subset = [r for r in with_ctx if r["expansion"] == exp]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {exp:<12s}: P(>0.5R)={s['move_05']:.1%} | P(>1R)={s['move_1']:.1%} | P(>2R)={s['move_2']:.1%} (n={s['n']})")

print(f"\n  Movement by MOMENTUM:")
for mom in ["BULLISH", "BEARISH", "NEUTRAL"]:
    subset = [r for r in with_ctx if r["momentum_dir"] == mom]
    s = stats(subset)
    if s and s["n"] >= 10:
        print(f"    {mom:<12s}: P(>0.5R)={s['move_05']:.1%} | P(>1R)={s['move_1']:.1%} | P(>2R)={s['move_2']:.1%} (n={s['n']})")

# Momentum aligned with trade direction
print(f"\n  Movement by MOMENTUM ALIGNMENT:")
mom_aligned = [r for r in with_ctx
               if (r["direction"] == "BULLISH" and r["momentum_dir"] == "BULLISH") or
                  (r["direction"] == "BEARISH" and r["momentum_dir"] == "BEARISH")]
mom_counter = [r for r in with_ctx
               if (r["direction"] == "BULLISH" and r["momentum_dir"] == "BEARISH") or
                  (r["direction"] == "BEARISH" and r["momentum_dir"] == "BULLISH")]
mom_neutral = [r for r in with_ctx if r["momentum_dir"] == "NEUTRAL"]

for label, subset in [("Momentum WITH trade", mom_aligned),
                      ("Momentum AGAINST trade", mom_counter),
                      ("Momentum NEUTRAL", mom_neutral)]:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"    {label:<25s}: P(>0.5R)={s['move_05']:.1%} | P(>1R)={s['move_1']:.1%} | EV={s['ev']:+.4f} (n={s['n']})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 5: CURRENCY STRENGTH + REGIME INTERACTION
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 5: CURRENCY STRENGTH + REGIME COMBINATION")
print("─" * 70)

# Add currency strength to enriched records
def get_usd_direction(sym, direction):
    if sym not in PAIR_CURRENCIES:
        return None
    base, quote = PAIR_CURRENCIES[sym]
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

for rec in with_ctx:
    sym = rec["symbol"]
    ts = rec["timestamp"]
    direction = rec["direction"]
    my_usd = get_usd_direction(sym, direction)
    
    concurrent = [t for t in trades_by_time.get(int(ts), []) if t["symbol"] != sym]
    if not concurrent or not my_usd:
        rec["cs_aligned"] = None
        rec["cs_agree"] = 0
        continue
    
    usd_strong = sum(1 for t in concurrent if get_usd_direction(t["symbol"], t["direction"]) == "USD_STRONG")
    usd_weak = sum(1 for t in concurrent if get_usd_direction(t["symbol"], t["direction"]) == "USD_WEAK")
    total = usd_strong + usd_weak
    if total == 0:
        rec["cs_aligned"] = None
        rec["cs_agree"] = 0
        continue
    
    if my_usd == "USD_STRONG":
        rec["cs_aligned"] = usd_strong > usd_weak
        rec["cs_agree"] = usd_strong
    else:
        rec["cs_aligned"] = usd_weak > usd_strong
        rec["cs_agree"] = usd_weak

# Combined: regime + currency
cs_available = [r for r in with_ctx if r.get("cs_aligned") is not None]
print(f"\n  Records with both regime + currency context: {len(cs_available)}")

combos = [
    ("TRENDING + CS aligned", [r for r in cs_available if r["regime"] == "TRENDING" and r["cs_aligned"]]),
    ("TRENDING + CS opposed", [r for r in cs_available if r["regime"] == "TRENDING" and not r["cs_aligned"]]),
    ("RANGING + CS aligned", [r for r in cs_available if r["regime"] == "RANGING" and r["cs_aligned"]]),
    ("RANGING + CS opposed", [r for r in cs_available if r["regime"] == "RANGING" and not r["cs_aligned"]]),
    ("NEUTRAL + CS aligned", [r for r in cs_available if r["regime"] == "NEUTRAL" and r["cs_aligned"]]),
    ("NEUTRAL + CS opposed", [r for r in cs_available if r["regime"] == "NEUTRAL" and not r["cs_aligned"]]),
]

print(f"\n  {'Combination':<30s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'MFE':>5s} | {'>0.5R':>4s} | {'>1R':>4s}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*4}-+-{'-'*4}")
for label, subset in combos:
    s = stats(subset)
    if s and s["n"] >= 5:
        print(f"  {label:<30s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {s['mfe']:.3f} | {s['move_05']:.0%} | {s['move_1']:.0%}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 6: FEATURE IMPORTANCE RANKING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 6: FEATURE IMPORTANCE (separation power)")
print("─" * 70)

def separation_power(feature_name, groups):
    """Calculate how much a feature separates good from bad trades."""
    group_stats = []
    for label, subset in groups:
        s = stats(subset)
        if s and s["n"] >= 10:
            group_stats.append((label, s))
    
    if len(group_stats) < 2:
        return None
    
    evs = [s["ev"] for _, s in group_stats]
    best = max(evs)
    worst = min(evs)
    spread = best - worst
    return spread, group_stats

features = [
    ("REGIME", [
        ("TRENDING", [r for r in with_ctx if r["regime"] == "TRENDING"]),
        ("RANGING", [r for r in with_ctx if r["regime"] == "RANGING"]),
        ("NEUTRAL", [r for r in with_ctx if r["regime"] == "NEUTRAL"]),
    ]),
    ("VOLATILITY", [
        ("LOW", [r for r in with_ctx if r["volatility"] == "LOW"]),
        ("NEUTRAL", [r for r in with_ctx if r["volatility"] == "NEUTRAL"]),
        ("HIGH", [r for r in with_ctx if r["volatility"] == "HIGH"]),
    ]),
    ("MOMENTUM_ALIGN", [
        ("WITH", mom_aligned),
        ("AGAINST", mom_counter),
        ("NEUTRAL", mom_neutral),
    ]),
    ("HTF_ALIGNMENT", [
        ("ALIGNED", weak_htf_aligned),
        ("COUNTER", weak_htf_counter),
        ("NEUTRAL", weak_htf_neutral),
    ]),
    ("LOCATION_TYPE", [
        (loc, [r for r in with_ctx if r["loc_type"] == loc])
        for loc in set(r["loc_type"] for r in with_ctx)
    ]),
    ("INSIDE_ZONE", [
        ("IN", [r for r in with_ctx if r["inside_zone"]]),
        ("OUT", [r for r in with_ctx if not r["inside_zone"]]),
    ]),
    ("PREM_DISC", [
        ("PREMIUM", [r for r in with_ctx if r["prem_disc"] == "PREMIUM"]),
        ("DISCOUNT", [r for r in with_ctx if r["prem_disc"] == "DISCOUNT"]),
        ("EQUILIBRIUM", [r for r in with_ctx if r["prem_disc"] == "EQUILIBRIUM"]),
    ]),
    ("ENTRY_STATE", [
        ("WEAK", [r for r in with_ctx if r["entry_state"] == "WEAK_ENTRY_CONFIRMATION"]),
        ("VALID", [r for r in with_ctx if r["entry_state"] == "VALID_ENTRY_CONFIRMATION"]),
        ("NONE", [r for r in with_ctx if r["entry_state"] == "NO_ENTRY_CONFIRMATION"]),
    ]),
    ("CURRENCY_STRENGTH", [
        ("ALIGNED", [r for r in cs_available if r["cs_aligned"]]),
        ("OPPOSED", [r for r in cs_available if not r["cs_aligned"]]),
    ]),
]

print(f"\n  {'Feature':<25s} | {'Spread':>7s} | Best group | Worst group")
print(f"  {'-'*25}-+-{'-'*7}-+-{'-'*30}-+-{'-'*30}")

ranked = []
for feature_name, groups in features:
    result = separation_power(feature_name, groups)
    if result:
        spread, group_stats = result
        best_label = max(group_stats, key=lambda x: x[1]["ev"])[0]
        worst_label = min(group_stats, key=lambda x: x[1]["ev"])[0]
        best_ev = max(s["ev"] for _, s in group_stats)
        worst_ev = min(s["ev"] for _, s in group_stats)
        ranked.append((spread, feature_name, best_label, worst_label, best_ev, worst_ev))

for spread, name, best, worst, best_ev, worst_ev in sorted(ranked, reverse=True):
    print(f"  {name:<25s} | {spread:>+6.4f} | {best} ({best_ev:+.4f}) | {worst} ({worst_ev:+.4f})")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS 7: STABILITY TESTING
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS 7: STABILITY TESTING")
print("─" * 70)

# Time stability — top 2 features by separation
# Sort by timestamp, split into thirds
sorted_ctx = sorted(with_ctx, key=lambda r: r["timestamp"])
third = len(sorted_ctx) // 3
time_periods = [
    ("Early", sorted_ctx[:third]),
    ("Middle", sorted_ctx[third:2*third]),
    ("Recent", sorted_ctx[2*third:]),
]

# Test regime separation across time
print(f"\n  REGIME effect across time:")
for period_label, period_data in time_periods:
    trending = [r for r in period_data if r["regime"] == "TRENDING"]
    ranging = [r for r in period_data if r["regime"] == "RANGING"]
    st = stats(trending)
    sr = stats(ranging)
    if st and sr and st["n"] >= 5 and sr["n"] >= 5:
        delta = st["ev"] - sr["ev"]
        print(f"    {period_label:<8s}: TRENDING EV={st['ev']:+.4f}(n={st['n']}) vs RANGING EV={sr['ev']:+.4f}(n={sr['n']}) Δ={delta:+.4f}")

# Test by symbol
print(f"\n  REGIME effect by symbol:")
for sym in sorted(set(r["symbol"] for r in with_ctx)):
    sym_trending = [r for r in with_ctx if r["symbol"] == sym and r["regime"] == "TRENDING"]
    sym_ranging = [r for r in with_ctx if r["symbol"] == sym and r["regime"] == "RANGING"]
    st = stats(sym_trending)
    sr = stats(sym_ranging)
    if st and sr and st["n"] >= 5 and sr["n"] >= 5:
        delta = st["ev"] - sr["ev"]
        print(f"    {sym:10s}: TREND={st['ev']:+.4f}(n={st['n']}) RANGE={sr['ev']:+.4f}(n={sr['n']}) Δ={delta:+.4f}")

# Test momentum alignment across time
print(f"\n  MOMENTUM ALIGNMENT across time:")
for period_label, period_data in time_periods:
    with_mom = [r for r in period_data
                if (r["direction"] == "BULLISH" and r["momentum_dir"] == "BULLISH") or
                   (r["direction"] == "BEARISH" and r["momentum_dir"] == "BEARISH")]
    against_mom = [r for r in period_data
                   if (r["direction"] == "BULLISH" and r["momentum_dir"] == "BEARISH") or
                      (r["direction"] == "BEARISH" and r["momentum_dir"] == "BULLISH")]
    sw = stats(with_mom)
    sa = stats(against_mom)
    if sw and sa and sw["n"] >= 5 and sa["n"] >= 5:
        delta = sw["ev"] - sa["ev"]
        print(f"    {period_label:<8s}: WITH={sw['ev']:+.4f}(n={sw['n']}) AGAINST={sa['ev']:+.4f}(n={sa['n']}) Δ={delta:+.4f}")

# ═══════════════════════════════════════════════════════════════
# ANALYSIS: BEST COMBINED REGIME CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("ANALYSIS: BEST COMBINED CONFIGURATIONS")
print("─" * 70)

# Test promising combinations from the separation analysis
combos_test = [
    ("Baseline (all)", with_ctx),
    ("WEAK only", weak_records),
    ("WEAK + TRENDING", [r for r in weak_records if r["regime"] == "TRENDING"]),
    ("WEAK + RANGING", [r for r in weak_records if r["regime"] == "RANGING"]),
    ("WEAK + HTF aligned", weak_htf_aligned),
    ("WEAK + momentum WITH", [r for r in weak_records
        if (r["direction"]=="BULLISH" and r["momentum_dir"]=="BULLISH") or
           (r["direction"]=="BEARISH" and r["momentum_dir"]=="BEARISH")]),
    ("WEAK + inside zone", [r for r in weak_records if r["inside_zone"]]),
    ("WEAK + TRENDING + HTF aligned", [r for r in weak_records
        if r["regime"]=="TRENDING" and
           ((r["direction"]=="BULLISH" and r["macro_bias"]=="BULLISH") or
            (r["direction"]=="BEARISH" and r["macro_bias"]=="BEARISH"))]),
    ("WEAK + RANGING + inside zone", [r for r in weak_records
        if r["regime"]=="RANGING" and r["inside_zone"]]),
    ("WEAK + momentum WITH + HTF aligned", [r for r in weak_records
        if ((r["direction"]=="BULLISH" and r["momentum_dir"]=="BULLISH") or
            (r["direction"]=="BEARISH" and r["momentum_dir"]=="BEARISH")) and
           ((r["direction"]=="BULLISH" and r["macro_bias"]=="BULLISH") or
            (r["direction"]=="BEARISH" and r["macro_bias"]=="BEARISH"))]),
]

COST_15P = 1.2 / 15.0  # 0.08R

print(f"\n  {'Configuration':<45s} | {'n':>4s} | {'WR':>5s} | {'EV':>8s} | {'@15p':>7s} | {'>0.5R':>4s} | {'>1R':>4s}")
print(f"  {'-'*45}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}-+-{'-'*4}-+-{'-'*4}")
for label, subset in combos_test:
    s = stats(subset)
    if s and s["n"] >= 3:
        net = s["ev"] - COST_15P
        print(f"  {label:<45s} | {s['n']:>4d} | {s['wr']:.1%} | {s['ev']:>+7.4f} | {net:>+6.4f} | {s['move_05']:.0%} | {s['move_1']:.0%}")

# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V5.1 FINAL VERDICT")
print("=" * 70)

# Compute the key metrics
s_baseline = stats(with_ctx)
s_best_regime = None
best_label = ""
best_ev = -999

for label, subset in combos_test:
    s = stats(subset)
    if s and s["n"] >= 10 and s["ev"] > best_ev:
        best_ev = s["ev"]
        best_label = label
        s_best_regime = s

print(f"\n  Baseline: n={s_baseline['n']} | WR={s_baseline['wr']:.1%} | EV={s_baseline['ev']:+.4f}")
if s_best_regime:
    print(f"  Best config: {best_label}")
    print(f"    n={s_best_regime['n']} | WR={s_best_regime['wr']:.1%} | EV={s_best_regime['ev']:+.4f}")
    print(f"    CI: [{s_best_regime['ci_low']:+.4f}, {s_best_regime['ci_high']:+.4f}]")
    print(f"    Net @15p: {s_best_regime['ev'] - COST_15P:+.4f}R")
    print(f"    Movement: P(>0.5R)={s_best_regime['move_05']:.1%} | P(>1R)={s_best_regime['move_1']:.1%}")
    
    improvement = s_best_regime["ev"] - s_baseline["ev"]
    print(f"    Improvement over baseline: {improvement:+.4f}R")
    
    ci_excludes_zero = s_best_regime["ci_low"] > 0
    net_positive = s_best_regime["ev"] > COST_15P
    
    if ci_excludes_zero and net_positive:
        print(f"\n  VERDICT: A) Regime intelligence provides meaningful improvement")
    elif improvement > 0.05 and s_best_regime["n"] >= 20:
        print(f"\n  VERDICT: B) Regime information improves filtering — needs validation")
    elif improvement < 0.02:
        print(f"\n  VERDICT: C) Regime classification adds no meaningful information")
    else:
        print(f"\n  VERDICT: D) Insufficient data or marginal effect")

# Key observation about the movement problem
print(f"\n  MOVEMENT PROBLEM STATUS:")
print(f"    Baseline P(>0.5R): {s_baseline['move_05']:.1%}")
print(f"    Baseline P(>1R): {s_baseline['move_1']:.1%}")
if s_best_regime:
    print(f"    Best regime P(>0.5R): {s_best_regime['move_05']:.1%}")
    print(f"    Best regime P(>1R): {s_best_regime['move_1']:.1%}")
    print(f"    Movement improvement: {s_best_regime['move_05'] - s_baseline['move_05']:+.1%}")

print()
