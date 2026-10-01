"""Stage 4 OBSERVATION/DATASET AUDIT -- READ-ONLY S3 discovery helper.

READ ONLY. Never writes, deletes, copies, or mutates S3.
Used by analysis/assurance/stage4_observation_dataset_audit_20260929.*
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import boto3

BUCKET = os.environ.get("STAGE4_AUDIT_BUCKET", "trading-bot-v10-data")
PROFILE = os.environ.get("STAGE4_AUDIT_PROFILE", "trading-bot-new")


def client():
    return boto3.Session(profile_name=PROFILE).client("s3")


def prefixes(c, prefix, delim="/"):
    out = []
    token = None
    kwargs = {"Bucket": BUCKET, "Prefix": prefix, "Delimiter": delim}
    while True:
        r = c.list_objects_v2(**kwargs)
        out.extend(p["Prefix"] for p in r.get("CommonPrefixes", []))
        if not r.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = r["NextContinuationToken"]
    return out


def walk(c, prefix):
    """Yield (key, size, last_modified) for every object under prefix."""
    token = None
    kwargs = {"Bucket": BUCKET, "Prefix": prefix}
    while True:
        r = c.list_objects_v2(**kwargs)
        for o in r.get("Contents", []):
            yield o["Key"], o["Size"], o["LastModified"]
        if not r.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = r["NextContinuationToken"]


def cmd_tree():
    c = client()
    for root in prefixes(c, ""):
        print(f"## {root}")
        for p in prefixes(c, root):
            n = sum(1 for _ in walk(c, p))
            print(f"   {p}  objects={n}")


def cmd_count():
    c = client()
    rows = []
    for root in prefixes(c, ""):
        for p in prefixes(c, root):
            keys = [k for k, _, _ in walk(c, p)]
            size = sum(s for _, s, _ in walk(c, p))
            dates = sorted({k.split("date=")[1].split("/")[0] for k in keys if "date=" in k})
            rows.append({
                "prefix": p,
                "objects": len(keys),
                "bytes": size,
                "date_min": dates[0] if dates else None,
                "date_max": dates[-1] if dates else None,
                "date_partitions": len(dates),
            })
    print(json.dumps(rows, indent=2, default=str))


def cmd_census():
    """Targeted census: partitions, object counts, size, date span, sample key."""
    c = client()
    targets = sys.argv[2:] or [
        "core/assessments/", "core/decision_ledger/", "core/events/",
        "core/execution_results/", "core/market_context/", "core/opportunities/",
        "core/trade_truth/", "projections/portfolio_shadow/",
        "projections/trade_journal/", "supporting/decision_trace/",
        "supporting/execution_attempts/", "supporting/execution_context/",
        "supporting/horizon_candidates/", "supporting/management_actions/",
        "supporting/portfolio_rankings/", "supporting/protection_audit/",
        "supporting/risk_deviation/", "supporting/shadow_runtime/",
        "supporting/strategy_candidates/", "supporting/strategy_observations/",
        "migration_staging/", "quarantine/", "research_state/checkpoints/",
        "runtime_state/position_excursion/",
    ]
    out = []
    for p in targets:
        keys, size = [], 0
        for k, s, _ in walk(c, p):
            keys.append(k)
            size += s
        dates = sorted({k.split("date=")[1].split("/")[0] for k in keys if "date=" in k})
        out.append({
            "prefix": p,
            "objects": len(keys),
            "bytes": size,
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "date_partitions": len(dates),
            "sample_key": keys[0] if keys else None,
        })
        print(json.dumps(out[-1], default=str), flush=True)
    with open(os.path.join(os.path.dirname(__file__), "..", "analysis", "assurance",
                           "_stage4_s3_census_20260929.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)


def cmd_fields():
    """Sample actual persisted field structure. READ ONLY (range GET)."""
    c = client()
    spec = json.loads(sys.argv[2])
    out = {}
    for name, key in spec.items():
        body = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        lines = [l for l in body.split(b"\n") if l.strip()]
        rec = json.loads(lines[0])
        def shape(v):
            if isinstance(v, dict):
                return {k: shape(x) for k, x in v.items()}
            if isinstance(v, list):
                return [shape(v[0])] if v else []
            return type(v).__name__
        out[name] = {
            "key": key,
            "rows_in_object": len(lines),
            "top_level_fields": sorted(rec.keys()),
            "shape": shape(rec),
        }
        print(f"===== {name} :: {key} ({len(lines)} rows) =====", flush=True)
        print(json.dumps(shape(rec), indent=1)[:6000], flush=True)
    with open(os.path.join(os.path.dirname(__file__), "..", "analysis", "assurance",
                           "_stage4_s3_fields_20260929.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)


def cmd_keys(prefix):
    c = client()
    for k, s, m in walk(c, prefix):
        print(f"{s}\t{m}\t{k}")


def cmd_get(key, out):
    c = client()
    c.download_file(BUCKET, key, out)
    print(f"downloaded {key} -> {out}")


if __name__ == "__main__":
    globals()["cmd_" + sys.argv[1]](*sys.argv[2:])
