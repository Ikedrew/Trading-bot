"""READ-ONLY live trace of one completed trade (Step 4 audit) — pos_58220011.

Run: $env:RESEARCH_AWS_PROFILE="trading-bot-new"; python _probe_trace_trade.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.data_access.s3_source import S3ResearchDataSource  # noqa: E402

src = S3ResearchDataSource()
assert src.bucket == "trading-bot-v10-data"

TRADE = "pos_58220011"
OPP = "AUDUSD*1788495300*MEAN_REVERSION"

# trade_truth
truth = [r for r in src.read_dataset("trade_truth", symbol="AUDUSD")
         if (r.get("identity") or {}).get("trade_id") == TRADE]
print(f"trade_truth[{TRADE}]: n={len(truth)}")
for r in truth:
    print("  ", json.dumps(r, default=str)[:1200])

# trade_journal
tju = [r for r in src.read_dataset("trade_journal", symbol="AUDUSD")
       if r.get("trade_id") == TRADE or r.get("position_ticket") == 58220011]
print(f"trade_journal[{TRADE}]: n={len(tju)}")
for r in tju:
    print("  ", json.dumps(r, default=str)[:900])

# management_actions
mgmt = src.read_dataset("management_actions", symbol="AUDUSD")
mgmt_t = [r for r in mgmt if r.get("trade_id") == TRADE]
print(f"management_actions[{TRADE}]: n={len(mgmt_t)}")
for r in mgmt_t:
    print("  ", json.dumps(r, default=str)[:700])

# execution_attempts — join by trade_id where present, else deal/ticket
atts = src.read_dataset("execution_attempts", symbol="AUDUSD")
atts_t = [r for r in atts if r.get("trade_id") == TRADE]
print(f"execution_attempts[trade_id=={TRADE}]: n={len(atts_t)}")
# attempts referencing the deal via broker_result
atts_deal = [r for r in atts if (r.get("broker_result") or {}).get("deal") == 58220011]
print(f"execution_attempts[deal==58220011]: n={len(atts_deal)}")
for r in (atts_t or atts_deal)[:3]:
    print("  ", json.dumps(r, default=str)[:700])

# horizon_candidates / strategy_candidates for the same opportunity
hc = [r for r in src.read_dataset("horizon_candidates", symbol="AUDUSD")
      if r.get("canonical_opportunity_id") == OPP]
sc = [r for r in src.read_dataset("strategy_candidates", symbol="AUDUSD")
      if r.get("canonical_opportunity_id") == OPP]
print(f"horizon_candidates[{OPP}]: n={len(hc)}")
for r in hc:
    print("  ", json.dumps(r, default=str)[:520])
print(f"strategy_candidates[{OPP}]: n={len(sc)}")
for r in sc:
    print("  ", json.dumps(r, default=str)[:520])

# decision trace for the same entity
ent = "AUDUSD_1788495300"
dt = src.read_dataset("decision_trace", symbol="AUDUSD")
dt_e = [r for r in dt if r.get("entity_id") == ent]
print(f"decision_trace[entity_id=={ent}]: n={len(dt_e)}")
for r in dt_e[:2]:
    print("  ", json.dumps(r, default=str)[:800])