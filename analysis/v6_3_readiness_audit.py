"""V6.3 — Index Shadow Data Readiness Audit.

Checks whether NAS100/US500/XAUUSD data exists in the shadow pipeline
and reports on coverage, integrity, and readiness for research.
"""
import json
from pathlib import Path
from collections import Counter, defaultdict

print("=" * 70)
print("V6.3 — INDEX SHADOW DATA READINESS AUDIT")
print("=" * 70)

INDEX_SYMBOLS = {"NAS100", "US500", "XAUUSD"}
FX_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}

# ═══════════════════════════════════════════════════════════════
# 1. INSTRUMENT COVERAGE
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("1. INSTRUMENT COVERAGE")
print("─" * 70)

# Check all V3 shadow pipeline directories
stages = [
    "market_understanding", "market_context", "opportunity_assessment",
    "horizon_assessment", "entry_assessment", "risk_assessment",
    "execution_assessment",
]

stage_counts = {}
for stage in stages:
    stage_dir = Path(f"logs/v3_shadow/{stage}")
    counts = defaultdict(int)
    if stage_dir.exists():
        for sym_dir in stage_dir.iterdir():
            if sym_dir.is_dir():
                for f in sym_dir.glob("*.jsonl"):
                    with open(f) as fh:
                        for line in fh:
                            if line.strip():
                                counts[sym_dir.name] += 1
    stage_counts[stage] = dict(counts)

# Check shadow trades
shadow_dir = Path("logs/shadow_trades")
shadow_counts = defaultdict(int)
if shadow_dir.exists():
    for sym_dir in shadow_dir.iterdir():
        if sym_dir.is_dir() and sym_dir.name != "UNKNOWN":
            for f in sym_dir.glob("*.jsonl"):
                with open(f) as fh:
                    for line in fh:
                        if line.strip():
                            shadow_counts[sym_dir.name] += 1

# Report
all_symbols = set()
for counts in stage_counts.values():
    all_symbols.update(counts.keys())
all_symbols.update(shadow_counts.keys())

print(f"\n  Symbols found across all pipeline stages:")
print(f"  {'Symbol':<12s} | {'shadow_trades':>13s} | {'mkt_ctx':>7s} | {'opp':>5s} | {'entry':>5s} | {'exec':>5s}")
print(f"  {'-'*12}-+-{'-'*13}-+-{'-'*7}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}")

for sym in sorted(all_symbols):
    st = shadow_counts.get(sym, 0)
    mc = stage_counts.get("market_context", {}).get(sym, 0)
    opp = stage_counts.get("opportunity_assessment", {}).get(sym, 0)
    ent = stage_counts.get("entry_assessment", {}).get(sym, 0)
    exc = stage_counts.get("execution_assessment", {}).get(sym, 0)
    marker = " ← INDEX" if sym in INDEX_SYMBOLS else ""
    print(f"  {sym:<12s} | {st:>13d} | {mc:>7d} | {opp:>5d} | {ent:>5d} | {exc:>5d}{marker}")

# Index-specific summary
print(f"\n  INDEX INSTRUMENTS SUMMARY:")
index_total = 0
for sym in INDEX_SYMBOLS:
    total = shadow_counts.get(sym, 0)
    for stage_data in stage_counts.values():
        total += stage_data.get(sym, 0)
    index_total += total
    if total > 0:
        print(f"    {sym}: {total} total records across all stages")
    else:
        print(f"    {sym}: NO DATA FOUND")

if index_total == 0:
    print(f"\n  ⚠ NO INDEX DATA EXISTS IN THE SHADOW PIPELINE")
    print(f"    The system has not yet collected any NAS100/US500/XAUUSD observations.")
    print(f"    This is expected if the bot has not been running with index symbols enabled.")

# ═══════════════════════════════════════════════════════════════
# 2. FX BASELINE COMPARISON (what we have to compare against)
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("2. FX BASELINE (existing data)")
print("─" * 70)

fx_shadow_total = sum(v for k, v in shadow_counts.items() if k in FX_SYMBOLS)
fx_exec_total = sum(stage_counts.get("execution_assessment", {}).get(s, 0) for s in FX_SYMBOLS)

print(f"\n  FX shadow trades: {fx_shadow_total}")
print(f"  FX execution assessments: {fx_exec_total}")
print(f"  FX symbols active: {sum(1 for s in FX_SYMBOLS if shadow_counts.get(s, 0) > 0)}/7")

# Load a sample of FX execution assessments for baseline metrics
exec_dir = Path("logs/v3_shadow/execution_assessment")
fx_exec_records = []
if exec_dir.exists():
    for f in exec_dir.rglob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("_outcome", {}).get("result_r") is not None:
                        fx_exec_records.append(r)
                except:
                    pass

if fx_exec_records:
    results = [r["_outcome"]["result_r"] for r in fx_exec_records]
    mfes = [r["_outcome"].get("mfe_r", 0) for r in fx_exec_records]
    timeouts = sum(1 for r in fx_exec_records if r["_outcome"].get("exit_reason") == "max_bars_timeout")
    n = len(results)
    
    print(f"\n  FX Execution Assessment Baseline:")
    print(f"    n = {n}")
    print(f"    WR = {sum(1 for r in results if r > 0)/n:.1%}")
    print(f"    EV = {sum(results)/n:+.4f}R")
    print(f"    Avg MFE = {sum(mfes)/n:.3f}R")
    print(f"    Timeout rate = {timeouts/n:.1%}")
    print(f"    P(>0.5R) = {sum(1 for m in mfes if m > 0.5)/n:.1%}")
    print(f"    P(>1.0R) = {sum(1 for m in mfes if m > 1.0)/n:.1%}")

# ═══════════════════════════════════════════════════════════════
# 3. PIPELINE INTEGRITY CHECK
# ═══════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("3. PIPELINE INTEGRITY")
print("─" * 70)

# Check if V3 pipeline stages have consistent record counts
print(f"\n  V3 Pipeline stage record counts (all symbols):")
for stage in stages:
    total = sum(stage_counts.get(stage, {}).values())
    print(f"    {stage:<25s}: {total:>5d}")

# Check for index-specific directories that exist but are empty
print(f"\n  Index directory existence check:")
for sym in INDEX_SYMBOLS:
    for stage in stages:
        stage_dir = Path(f"logs/v3_shadow/{stage}/{sym}")
        if stage_dir.exists():
            files = list(stage_dir.glob("*.jsonl"))
            records = stage_counts.get(stage, {}).get(sym, 0)
            print(f"    {stage}/{sym}: EXISTS ({len(files)} files, {records} records)")
        # Don't print "missing" for every combo — too verbose

    shadow_sym_dir = Path(f"logs/shadow_trades/{sym}")
    if shadow_sym_dir.exists():
        files = list(shadow_sym_dir.glob("*.jsonl"))
        print(f"    shadow_trades/{sym}: EXISTS ({len(files)} files, {shadow_counts.get(sym, 0)} records)")

# ═══════════════════════════════════════════════════════════════
# 4. READINESS VERDICT
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("V6.3 READINESS VERDICT")
print("=" * 70)

index_shadow = sum(shadow_counts.get(s, 0) for s in INDEX_SYMBOLS)
index_exec = sum(stage_counts.get("execution_assessment", {}).get(s, 0) for s in INDEX_SYMBOLS)

if index_exec >= 100:
    print(f"""
  VERDICT: A) Enough clean index data exists for V6 research
  
  Index execution assessments: {index_exec}
  Minimum required for initial analysis: 100
  Status: READY FOR RESEARCH
""")
elif index_exec >= 30:
    print(f"""
  VERDICT: C) More collection required
  
  Index execution assessments: {index_exec}
  Minimum required: 100 (have {index_exec})
  Estimated additional collection time: {(100 - index_exec) // 5} days
  Status: PRELIMINARY ANALYSIS POSSIBLE, FULL RESEARCH NEEDS MORE DATA
""")
elif index_shadow > 0 or index_exec > 0:
    print(f"""
  VERDICT: C) More collection required
  
  Index shadow trades: {index_shadow}
  Index execution assessments: {index_exec}
  Status: PIPELINE IS WORKING but insufficient data for research
  
  Action required:
  - Continue running bot with index symbols enabled
  - Wait for 100+ execution assessments before V6.4 analysis
  - Estimated time: 2-4 weeks depending on opportunity frequency
""")
else:
    print(f"""
  VERDICT: C) No index data collected yet — collection has not started
  
  Index shadow trades: 0
  Index execution assessments: 0
  
  ROOT CAUSE: The bot has not yet run with NAS100/US500/XAUUSD enabled.
  
  The V6.2 configuration changes added these symbols to CANONICAL_SYMBOLS,
  but the live scanner needs to actually run and observe these instruments
  before data will appear.
  
  REQUIRED ACTIONS:
  1. Verify broker (Pepperstone MT5) offers NAS100/US500/XAUUSD
     - Check MT5 Market Watch for available symbols
     - Symbol names may be: NAS100, USTEC, US500, SPX500, XAUUSD, GOLD
     
  2. Start the bot (or run replay) with new symbols enabled
     - The shadow pipeline will automatically begin collecting observations
     - No execution will occur (shadow-only mode for new instruments)
     
  3. Wait for data collection
     - Minimum: 100 execution assessments for initial analysis
     - Optimal: 300+ for statistical validation
     - Estimated time: 2-4 weeks of live observation
     
  4. Re-run this audit (v6_3_readiness_audit.py) to check progress
  
  IMPORTANT:
  - DO NOT modify V3 logic for indices
  - DO NOT create index-specific rules
  - Let the existing architecture observe and collect
  - Research begins AFTER data collection, not before
""")

print()
