"""
Live S3 smoke test — canonical shadow ingestion from trading-bot-v10-data.

READ-ONLY. Requires an authenticated AWS session for the account that owns the
bucket (e.g. `aws sso login --profile trading-bot-new`, then set AWS_PROFILE
before running). Run from the repo root:

    AWS_PROFILE=trading-bot-new python _smoke_shadow_ingestion_live.py

Proves:
    - the canonical `shadow_runtime` dataset is readable on S3
    - >= 2 real nshadow_* CLOSE lifecycles normalize into the internal
      research shape with all required preserved fields
"""
import json
import sys
from collections import Counter

sys.path.insert(0, r"c:\Users\ikues\Trading-bot")

from research_engine.data_access.shadow_runtime_ingestion import (
    ingest_completed_shadow_trades,
    load_shadow_runtime_events,
)

events = load_shadow_runtime_events()
print(f"canonical S3 shadow_runtime events: {len(events)}")
print("event_type:", dict(Counter(e.get("event_type") for e in events)))
print("schema:    ", dict(Counter(e.get("schema_version") for e in events)))
assert events, "FAIL: canonical S3 source returned no events (collection gap)"

records = ingest_completed_shadow_trades()
nshadow = [r for r in records
           if r["identity"]["shadow_trade_id"].startswith("nshadow_")]
assert len(nshadow) >= 2, f"FAIL: only {len(nshadow)} nshadow_* lifecycles normalized"

required_id = ["shadow_trade_id", "plan_id", "observation_id",
               "canonical_opportunity_id", "symbol", "evaluated_horizon"]
for r in nshadow:
    ident, snap, out = r["identity"], r["decision_snapshot"], r["simulated_outcome"]
    assert r["schema_version"] == "shadow_trades_v1" and r["source"] == "shadow_runtime_ingestion"
    for k in required_id:
        assert k in ident, f"missing identity.{k}"
    for k in ("direction", "entry_intent_price", "stop_loss_intent", "take_profit_intent"):
        assert snap.get(k) not in ("", None), f"missing decision_snapshot.{k}"
    for k in ("pnl_r_multiple", "mfe_r", "mae_r", "exit_reason", "exit_timestamp"):
        assert k in out, f"missing simulated_outcome.{k}"

print("\nsample normalized lifecycles:")
for r in nshadow[:3]:
    print(json.dumps({
        "shadow_trade_id": r["identity"]["shadow_trade_id"],
        "symbol": r["identity"]["symbol"],
        "horizon": r["identity"]["evaluated_horizon"],
        "direction": r["decision_snapshot"]["direction"],
        "pnl_r_multiple": r["simulated_outcome"]["pnl_r_multiple"],
        "mfe_r": r["simulated_outcome"]["mfe_r"],
        "mae_r": r["simulated_outcome"]["mae_r"],
        "exit_reason": r["simulated_outcome"]["exit_reason"],
    }))
print(f"\nSMOKE OK — {len(nshadow)} real nshadow_* lifecycles normalized from live S3")
