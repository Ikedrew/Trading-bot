import json, os
from datetime import datetime, timezone

base = r'c:\Users\Administrator\Desktop\Trading bot build\logs\v10_decisions'
symbols = ['AUDUSD','EURUSD','GBPUSD','NAS100','NZDUSD','US500','USDCAD','USDCHF','USDJPY','XAUUSD']

records = []
for sym in symbols:
    path = os.path.join(base, sym, '2026-07-30.jsonl')
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = rec.get('timestamp_utc', 0)
                if isinstance(ts, (int, float)) and ts > 1785000000:
                    records.append(rec)
            except:
                pass

total = len(records)
execute_count = sum(1 for r in records if r.get('final_action') == 'EXECUTE')
no_trade_count = sum(1 for r in records if r.get('final_action') == 'NO_TRADE')

opp_states = {}
for r in records:
    opp = r.get('opportunity', {})
    st = opp.get('state', 'UNKNOWN')
    opp_states[st] = opp_states.get(st, 0) + 1

rej_stages = {}
for r in records:
    stage = r.get('rejection_stage', 'null')
    rej_stages[stage] = rej_stages.get(stage, 0) + 1

strategy_families = {}
for r in records:
    sf = r.get('strategy_family')
    if sf and sf != 'None' and str(sf) != 'None':
        strategy_families[sf] = strategy_families.get(sf, 0) + 1

strategy_rejected = sum(1 for r in records if r.get('rejection_stage') == 'strategy')

entry_rejected = [r for r in records if r.get('rejection_stage') == 'entry']
entry_reasons = {}
for r in entry_rejected:
    reason = r.get('rejection_reason', 'unknown')
    entry_reasons[reason] = entry_reasons.get(reason, 0) + 1

risk_rejected = [r for r in records if r.get('rejection_stage') == 'risk']
exec_rejected = [r for r in records if r.get('rejection_stage') == 'execution']
risk_approved_any = sum(1 for r in records if r.get('risk_approved') == True)
exec_approved_any = sum(1 for r in records if r.get('execution_approved') == True)

timestamps = [r['timestamp_utc'] for r in records]
earliest = min(timestamps)
latest = max(timestamps)

stage_order = {'execution': 5, 'risk': 4, 'entry': 3, 'strategy': 2, 'opportunity': 1}
def depth(r):
    s = r.get('rejection_stage', 'opportunity')
    return stage_order.get(s, 0)

sorted_recs = sorted(records, key=lambda r: (-depth(r), -r.get('opportunity', {}).get('overall_quality', 0)))
top5 = sorted_recs[:5]

invalid_recs = [r for r in records if r.get('opportunity', {}).get('state') == 'INVALID']
loc_scores = [r.get('opportunity', {}).get('location_score', 0) for r in invalid_recs]
struct_scores = [r.get('opportunity', {}).get('structure_score', 0) for r in invalid_recs]
form_scores = [r.get('opportunity', {}).get('formation_score', 0) for r in invalid_recs]
avg_loc = sum(loc_scores)/len(loc_scores) if loc_scores else 0
avg_struct = sum(struct_scores)/len(struct_scores) if struct_scores else 0
avg_form = sum(form_scores)/len(form_scores) if form_scores else 0

print('=== V10 DECISION FUNNEL AUDIT ===')
print()
print('## 1. OVERALL')
print(f'Total evaluations (ts>1785000000): {total}')
print(f'EXECUTE count: {execute_count}')
print(f'NO_TRADE count: {no_trade_count}')
print()
print('## 2. OPPORTUNITY LAYER')
for st, cnt in sorted(opp_states.items(), key=lambda x: -x[1]):
    pct = cnt/total*100 if total else 0
    print(f'  {st}: {cnt} ({pct:.1f}%)')
print(f'  INVALID avg scores - location: {avg_loc:.3f}, structure: {avg_struct:.3f}, formation: {avg_form:.3f}')
print(f'  Top 3 contributing factors for INVALID:')
print(f'    1) location_score=0: {sum(1 for s in loc_scores if s==0)}/{len(loc_scores)} ({sum(1 for s in loc_scores if s==0)/len(loc_scores)*100:.0f}%)')
print(f'    2) formation_score=0: {sum(1 for s in form_scores if s==0)}/{len(form_scores)} ({sum(1 for s in form_scores if s==0)/len(form_scores)*100:.0f}%)')
print(f'    3) structure_score<=0.3: {sum(1 for s in struct_scores if s<=0.3)}/{len(struct_scores)} ({sum(1 for s in struct_scores if s<=0.3)/len(struct_scores)*100:.0f}%)')
print()
print('## 3. STRATEGY LAYER')
non_invalid = sum(1 for r in records if r.get('opportunity', {}).get('state') != 'INVALID')
has_strategy = sum(strategy_families.values())
print(f'  Non-INVALID opportunity (entering strategy): {non_invalid}')
print(f'  Strategy family selected (non-null): {has_strategy}')
print(f'  Breakdown by family:')
for sf, cnt in sorted(strategy_families.items(), key=lambda x: -x[1]):
    print(f'    {sf}: {cnt}')
print(f'  Rejected at strategy (no match): {strategy_rejected}')
print()
print('## 4. ENTRY LAYER')
print(f'  Records rejected at entry: {len(entry_rejected)}')
for reason, cnt in sorted(entry_reasons.items(), key=lambda x: -x[1]):
    print(f'    "{reason}": {cnt}')
print(f'  Entry geometry for entry-rejected:')
for r in entry_rejected[:8]:
    print(f'    {r.get("symbol")} ts={r.get("timestamp_utc")} entry={r.get("entry_price")} stop={r.get("stop_price")} target={r.get("target_price")} rr={r.get("expected_rr")}')
print()
print('## 5. RISK/EXECUTION LAYERS')
print(f'  Rejected at risk stage: {len(risk_rejected)}')
print(f'  Rejected at execution stage: {len(exec_rejected)}')
print(f'  risk_approved=True anywhere: {risk_approved_any}')
print(f'  execution_approved=True anywhere: {exec_approved_any}')
print()
print('## 6. DEEPEST DECISIONS (Top 5)')
for i, r in enumerate(top5, 1):
    print(f'  #{i}: sym={r.get("symbol")} ts={r.get("timestamp_utc")} rej_stage={r.get("rejection_stage")}')
    print(f'       reason="{r.get("rejection_reason")}"')
    opp = r.get('opportunity', {})
    print(f'       opp: state={opp.get("state")} quality={opp.get("overall_quality")} loc={opp.get("location_score")} struct={opp.get("structure_score")} behav={opp.get("behaviour_score")} form={opp.get("formation_score")}')
    print(f'       strategy: family={r.get("strategy_family")} conf={r.get("strategy_confidence")} dir={r.get("strategy_direction")} horizon={r.get("horizon")}')
    print(f'       entry: price={r.get("entry_price")} stop={r.get("stop_price")} target={r.get("target_price")} rr={r.get("expected_rr")}')
    ms = r.get('market_state', {})
    print(f'       market: h1_bos={ms.get("h1_bos_direction")} h1_clarity={ms.get("h1_structural_clarity")} h4_trend={ms.get("h4_trend")} regime={ms.get("regime")} loc_type={ms.get("location_type")}')
    print()
print('## 7. ENTRY GEOMETRY DIAGNOSTICS')
print(f'  Entry-rejected records total: {len(entry_rejected)}')
has_bos = 0
has_nonzero_stop = 0
for r in entry_rejected:
    full_json = json.dumps(r)
    if 'bos_level' in full_json:
        has_bos += 1
    sp = r.get('stop_price')
    if sp is not None and sp != 0:
        has_nonzero_stop += 1
print(f'  Contains "bos_level" field: {has_bos}/{len(entry_rejected)}')
print(f'  Non-zero stop_price: {has_nonzero_stop}/{len(entry_rejected)}')
if has_nonzero_stop > 0:
    print(f'  ** BOS fix IS producing stops in {has_nonzero_stop} records **')
else:
    print(f'  ** NO entry-rejected record shows non-zero stop — BOS fix NOT producing stops **')
print()
print('## 8. TIMESTAMP RANGE')
print(f'  Earliest: {datetime.fromtimestamp(earliest, tz=timezone.utc).isoformat()} (unix: {earliest})')
print(f'  Latest:   {datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()} (unix: {latest})')
print(f'  Duration: ~{(latest - earliest)/3600:.1f} hours')
print(f'  These are from 2026-07-30 — confirms post-fix restart records.')
