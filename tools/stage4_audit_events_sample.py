"""Stage 4 audit: inspect one events_v1 CANDLE object in detail. READ ONLY."""
from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.stage4_audit_s3_probe import BUCKET, client  # noqa: E402

KEY = ("core/events/schema_version=events_v1/symbol=EURUSD/date=2026-09-03/"
       "migration-s3-candle-timeframe-v1-20260908T172515Z-0e089aa3fb37-part-0006.jsonl")


def main():
    c = client()
    body = c.get_object(Bucket=BUCKET, Key=KEY)["Body"].read()
    rows = [json.loads(l) for l in body.split(b"\n") if l.strip()]
    types = collections.Counter(r.get("type") for r in rows)
    s_tf = collections.Counter(f"{r.get('source')}:{r.get('timeframe')}" for r in rows)
    print("rows", len(rows))
    print("types", dict(types))
    print("source_timeframe", dict(s_tf))
    for r in rows:
        if r.get("type") == "CANDLE":
            print("CANDLE payload keys:", sorted((r.get("payload") or {}).keys()))
            break
    for r in rows:
        if r.get("type") == "CANDLE" and r.get("timeframe") == "M5":
            print("M5 ROW:", json.dumps(r)[:800])
            break
    for r in rows:
        if r.get("type") == "CANDLE" and r.get("timeframe") == "M5":
            p = r.get("payload") or {}
            print("M5 lifecycle binding fields present:",
                  {k: (k in r) for k in ("shadow_trade_id", "canonical_opportunity_id",
                                         "trade_horizon", "entity_id", "observation_id")})
            print("M5 payload full:", json.dumps(p)[:400])
            break


if __name__ == "__main__":
    main()
