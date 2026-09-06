"""READ-ONLY live probe of the six unconsumed V1 datasets (Step 4 audit) — capped.

Run: $env:RESEARCH_AWS_PROFILE="trading-bot-new"; python _probe_six_datasets.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.production_data_contract import canonical_s3_schema_prefix  # noqa: E402
from research_engine.data_access.s3_source import S3ResearchDataSource  # noqa: E402

src = S3ResearchDataSource()
assert src.bucket == "trading-bot-v10-data"


def _list(prefix: str, limit: int = 12):
    client = src._get_client()
    keys = []
    token = None
    while len(keys) < limit:
        kw = {"Bucket": src.bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        keys.extend(c.get("Key") for c in resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys[:limit]


def _get_first_body(key: str):
    client = src._get_client()
    obj = client.get_object(Bucket=src.bucket, Key=key)
    return obj["Body"].read().decode("utf-8")


# Cap object counts; only the head of each prefix.
for ds in ("events", "horizon_candidates", "strategy_candidates",
           "execution_attempts", "management_actions"):
    prefix = canonical_s3_schema_prefix(ds)
    keys = _list(prefix, limit=12)
    print(f"{ds}: keys[0..{min(12, len(keys))}) prefix={prefix}")

    # Read the first actual record file (skip pure dir keys).
    rec_lines = []
    for k in keys:
        if k.endswith(".jsonl"):
            print(f"   GET {k}")
            body = _get_first_body(k)
            rec_lines = [ln for ln in body.splitlines() if ln.strip()][:2]
            for ln in rec_lines:
                print("   SAMPLE:", ln[:800])
            if rec_lines:
                break

print("\nposition_excursion keys:", _list("runtime_state/position_excursion/", limit=5))
for k in _list("runtime_state/position_excursion/", limit=2):
    print("EXCURSION:", k)
    print("   BODY:", _get_first_body(k)[:700])