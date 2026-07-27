"""Evidence Readiness Audit — inspect all persistence layers."""
import json
from pathlib import Path

base = Path("logs")

# 1. Count records in each layer
print("PERSISTENCE LAYER INVENTORY")
print("=" * 60)
for d in ['trade_truth','trade_journal','execution_results','decision_trace',
          'decision_audit','decision_ledger','execution_context','shadow_trades','market_context']:
    p = base / d
    if not p.exists():
        print(f"  {d:25s} DOES NOT EXIST")
        continue
    files = list(p.rglob("*.jsonl"))
    recs = sum(1 for f in files for line in open(f,'r',encoding='utf-8') if line.strip())
    print(f"  {d:25s} files={len(files):3d}  records={recs:5d}")

# 2. Audit trade_journal fields
print("\nTRADE JOURNAL COMPLETENESS")
print("=" * 60)
tj = base / "trade_journal"
real_trades = []
if tj.exists():
    for f in tj.rglob("*.jsonl"):
        for line in open(f,'r',encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                if rec.get("pattern_name") == "RECOVERED" and not rec.get("correlation_id"):
                    continue
                real_trades.append(rec)
            except: pass

print(f"  Real completed trades: {len(real_trades)}")
if real_trades:
    # Check key fields
    checks = {
        "correlation_id": 0, "entry_price": 0, "exit_price": 0,
        "initial_sl": 0, "initial_tp": 0, "net_pnl": 0,
        "duration_seconds": 0, "max_favourable_price": 0,
        "pattern_name": 0, "direction": 0, "close_reason": 0,
    }
    for rec in real_trades:
        for field in checks:
            val = rec.get(field)
            if val is not None and val != "" and val != 0:
                checks[field] += 1
    print("  Field coverage (non-empty/non-zero):")
    for field, count in checks.items():
        pct = count * 100 // len(real_trades)
        status = "OK" if pct >= 80 else "PARTIAL" if pct >= 50 else "MISSING"
        print(f"    {field:25s} {count:2d}/{len(real_trades)} ({pct}%) [{status}]")

# 3. Decision audit EV fields
print("\nDECISION AUDIT EV COVERAGE")
print("=" * 60)
da = base / "decision_audit"
if da.exists():
    total = 0; has_ev = 0; has_p = 0; has_policy = 0; has_experiment = 0
    has_score = 0; has_pattern = 0; execute_count = 0
    for f in da.rglob("*.jsonl"):
        for line in open(f,'r',encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                if "MagicMock" in str(rec.get("engine_state", {})): continue
                total += 1
                if rec.get("ev") is not None: has_ev += 1
                if rec.get("p_success") is not None: has_p += 1
                if rec.get("policy_trade_allowed") is not None: has_policy += 1
                if rec.get("ev_experiment_mode") is not None: has_experiment += 1
                if rec.get("score_neutral") and rec["score_neutral"] > 0: has_score += 1
                if rec.get("pattern"): has_pattern += 1
                if rec.get("should_trade"): execute_count += 1
            except: pass
    print(f"  Total decision_audit records: {total}")
    print(f"  With EV value:               {has_ev} ({has_ev*100//max(total,1)}%)")
    print(f"  With p_success:              {has_p} ({has_p*100//max(total,1)}%)")
    print(f"  With policy_trade_allowed:   {has_policy} ({has_policy*100//max(total,1)}%)")
    print(f"  With ev_experiment_mode:     {has_experiment} ({has_experiment*100//max(total,1)}%)")
    print(f"  With score > 0:             {has_score} ({has_score*100//max(total,1)}%)")
    print(f"  With pattern:               {has_pattern} ({has_pattern*100//max(total,1)}%)")
    print(f"  EXECUTE decisions:           {execute_count}")

# 4. Trade truth real records
print("\nTRADE TRUTH REAL RECORDS")
print("=" * 60)
tt = base / "trade_truth"
real_truth = []
if tt.exists():
    for f in tt.rglob("*.jsonl"):
        for line in open(f,'r',encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                if rec.get("schema_version") != "trade_truth_v3": continue
                cid = rec.get("identity",{}).get("correlation_id","")
                if any(x in cid for x in ["RECOVERED","TEST","AUDIT","ROBUST","DEAD"]): continue
                if not cid.startswith("COR-"): continue
                real_truth.append(rec)
            except: pass
print(f"  Real trade_truth records: {len(real_truth)}")
if real_truth:
    # Check for completeness issues
    neg_duration = sum(1 for r in real_truth if r.get("timestamps",{}).get("duration_seconds",0) < 0)
    margin_call = sum(1 for r in real_truth if r.get("exit",{}).get("exit_reason") == "margin_call")
    zero_r = sum(1 for r in real_truth if r.get("outcome",{}).get("r_multiple_realised",0) == 0)
    print(f"  Negative duration:        {neg_duration}")
    print(f"  Exit reason 'margin_call': {margin_call} (should be stop_loss/take_profit)")
    print(f"  Zero R-multiple:          {zero_r}")

# 5. Cross-reference: can we join decision → execution → outcome?
print("\nCROSS-REFERENCE AUDIT")
print("=" * 60)
# Get all correlation_ids from execution_results (successful)
exec_cids = set()
er = base / "execution_results"
if er.exists():
    for f in er.rglob("*.jsonl"):
        for line in open(f,'r',encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                if rec.get("result_ok") and rec.get("correlation_id"):
                    exec_cids.add(rec["correlation_id"])
            except: pass

# Get all from trade_journal
journal_cids = set(r.get("correlation_id","") for r in real_trades if r.get("correlation_id"))

# Get all from trade_truth
truth_cids = set(r.get("identity",{}).get("correlation_id","") for r in real_truth)

# Get all from decision_trace (EXECUTE only)
trace_cids = set()
dt = base / "decision_trace"
if dt.exists():
    for f in dt.rglob("*.jsonl"):
        for line in open(f,'r',encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                if rec.get("action") == "EXECUTE":
                    # extract correlation from entity_id or other field
                    trace_cids.add(rec.get("entity_id",""))
            except: pass

print(f"  Successful executions (by CID):    {len(exec_cids)}")
print(f"  Trade journal records (by CID):    {len(journal_cids)}")
print(f"  Trade truth records (by CID):      {len(truth_cids)}")
print(f"  Decision trace EXECUTE (entity):   {len(trace_cids)}")
print()

# Find gaps
exec_without_journal = exec_cids - journal_cids
exec_without_truth = exec_cids - truth_cids
journal_without_truth = journal_cids - truth_cids

print(f"  Executions WITHOUT journal:        {len(exec_without_journal)}")
if exec_without_journal:
    for cid in sorted(exec_without_journal)[:5]:
        print(f"    {cid}")
print(f"  Executions WITHOUT trade_truth:    {len(exec_without_truth)}")
if exec_without_truth:
    for cid in sorted(exec_without_truth)[:5]:
        print(f"    {cid}")
print(f"  Journal WITHOUT trade_truth:       {len(journal_without_truth)}")
if journal_without_truth:
    for cid in sorted(journal_without_truth)[:5]:
        print(f"    {cid}")

# 6. Missing data summary
print("\n" + "=" * 60)
print("EVIDENCE GAPS SUMMARY")
print("=" * 60)
print("""
CRITICAL GAPS:
  1. Exit reason classification: 'margin_call' used instead of 'stop_loss_hit'/'take_profit_hit'
     - Affects: exit reason analysis, strategy evaluation
  2. Spread/commission not captured in trade_truth
     - Affects: true execution cost analysis
  3. MFE/MAE only in journal (max_favourable_price), not in trade_truth
     - Affects: trade management optimization
  4. No session field in trade records
     - Affects: session-based performance analysis (must be derived from timestamp)
  5. Some executions have no journal/truth (broker-side close before fix deployed)
     - Affects: complete lifecycle reconstruction for early trades
""")
