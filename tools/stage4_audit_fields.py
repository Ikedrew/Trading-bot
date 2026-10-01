"""Stage 4 audit: sample actual persisted fields. READ ONLY."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.stage4_audit_s3_probe import BUCKET, client, walk  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "analysis", "assurance",
                   "_stage4_s3_fields_20260929.json")

# Direct keys discovered by census (avoids slow full-prefix walks).
SAMPLE_KEYS = {
    "events": "core/events/schema_version=events_v1/symbol=EURUSD/date=2026-09-03/migration-s3-candle-timeframe-v1-20260908T172515Z-0e089aa3fb37-part-0006.jsonl",
    "events_live": "core/events/schema_version=events_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "market_context": "core/market_context/schema_version=market_context_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "opportunities": "core/opportunities/schema_version=opportunities_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "assessments": "core/assessments/schema_version=assessments_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "decision_ledger": "core/decision_ledger/schema_version=decision_ledger_v1/symbol=EURUSD/date=2026-09-07/part-000.jsonl",
    "trade_truth": "core/trade_truth/schema_version=trade_truth_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "execution_results": "core/execution_results/schema_version=execution_results_v1/symbol=EURUSD/date=2026-09-22/part-000.jsonl",
    "decision_trace": "supporting/decision_trace/schema_version=decision_trace_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "shadow_runtime": "supporting/shadow_runtime/schema_version=shadow_runtime_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "horizon_candidates": "supporting/horizon_candidates/schema_version=horizon_candidates_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "strategy_candidates": "supporting/strategy_candidates/schema_version=strategy_candidates_v1/symbol=EURUSD/date=2026-09-22/part-000.jsonl",
    "portfolio_rankings": "supporting/portfolio_rankings/schema_version=portfolio_ranking_v1/date=2026-09-24/part-000.jsonl",
    "execution_context": "supporting/execution_context/schema_version=execution_context_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
    "management_actions": "supporting/management_actions/schema_version=management_actions_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "risk_deviation": "supporting/risk_deviation/schema_version=risk_deviation_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "protection_audit": "supporting/protection_audit/schema_version=protection_audit_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "execution_attempts": "supporting/execution_attempts/schema_version=execution_attempts_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "trade_journal": "projections/trade_journal/schema_version=trade_journal_v1/symbol=EURUSD/date=2026-09-23/part-000.jsonl",
    "portfolio_shadow": "projections/portfolio_shadow/schema_version=portfolio_shadow_v1/date=2026-09-28/part-000.jsonl",
    "strategy_observations": "supporting/strategy_observations/schema_version=strategy_observation_v1/symbol=EURUSD/date=2026-09-24/part-000.jsonl",
}


def latest_key(c, prefix):
    best = None
    for k, _, m in walk(c, prefix):
        if best is None or k > best[0]:
            best = (k, m)
    return best[0] if best else None


def shape(v):
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return type(v).__name__


def sample(c, key, n=400):
    body = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return [json.loads(l) for l in body.split(b"\n") if l.strip()][:n]


def main():
    c = client()
    out = {}
    for name, key in SAMPLE_KEYS.items():
        try:
            rows = sample(c, key)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"key": key, "present": False, "error": type(exc).__name__}
            print(f"### {name}: ABSENT/ERROR {key} {exc}", flush=True)
            continue
        fields = {}
        for r in rows:
            for k, v in r.items():
                fields.setdefault(k, {"present_in": 0, "types": set(), "sample": v})
                fields[k]["present_in"] += 1
                fields[k]["types"].add(type(v).__name__)
        out[name] = {
            "present": True,
            "sample_key": key,
            "rows_sampled": len(rows),
            "schema_partition": key.split("/")[1] if "schema_version" in key else None,
            "fields": {
                k: {"coverage": f"{v['present_in']}/{len(rows)}",
                    "types": sorted(v["types"]),
                    "shape": shape(v["sample"])}
                for k, v in sorted(fields.items())
            },
        }
        print(f"### {name} :: {key} ({len(rows)} rows)", flush=True)
        print(json.dumps({k: f"{v['present_in']}/{len(rows)} {sorted(v['types'])}"
                          for k, v in sorted(fields.items())}, indent=1), flush=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print("wrote", os.path.abspath(OUT))



if __name__ == "__main__":
    main()
