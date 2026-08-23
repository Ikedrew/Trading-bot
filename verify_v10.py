import os, json

print("=== DECISION LEDGER (all symbols, today's file) ===")
symbols = ['AUDUSD','EURUSD','GBPUSD','NAS100','NZDUSD','US500','USDCAD','USDCHF','USDJPY','XAUUSD']
for sym in symbols:
    d = f'logs/decision_ledger/{sym}'
    if not os.path.exists(d):
        print(f'{sym}: NO DIR')
        continue
    files = sorted([f for f in os.listdir(d) if f.endswith('.jsonl')])
    if not files:
        print(f'{sym}: NO FILES')
        continue
    latest = files[-1]
    path = os.path.join(d, latest)
    lines = open(path).readlines()
    last = json.loads(lines[-1])
    has_v10 = 'v10' in last
    dual_ev = last.get('dual_ev')
    print(f'{sym} {latest}: {len(lines)} entries, last_decision={last.get("decision")}, has_v10={has_v10}, dual_ev={dual_ev}')

print()
print("=== V10 DECISIONS (all symbols) ===")
d = 'logs/v10_decisions'
for sym in sorted(os.listdir(d)):
    symdir = os.path.join(d, sym)
    if not os.path.isdir(symdir):
        continue
    files = sorted([f for f in os.listdir(symdir) if f.endswith('.jsonl')])
    if files:
        latest = files[-1]
        path = os.path.join(symdir, latest)
        lines = open(path).readlines()
        last = json.loads(lines[-1])
        print(f'{sym}: v10_decisions {latest} - {len(lines)} entries, ts_utc={last.get("timestamp_utc")}, corr_id={last.get("correlation_id")}')
    else:
        print(f'{sym}: v10_decisions - empty')

print()
print("=== VISIBILITY TRACE (last 5 entries) ===")
with open('logs/visibility_trace.jsonl', 'r') as f:
    lines = f.readlines()
print(f'Total lines: {len(lines)}')
for i, line in enumerate(lines[-5:]):
    rec = json.loads(line)
    print(f'Line {len(lines)-5+i+1}: ts={rec.get("timestamp")}, sym={rec.get("symbol")}, obs_id={rec.get("observation_id","N/A")}, decision={rec.get("decision","N/A")}, engine={rec.get("engine_version","N/A")}')
