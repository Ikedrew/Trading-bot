"""Stage 4 audit: full-population coverage scan. READ ONLY."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.stage4_audit_s3_probe import BUCKET, client, walk  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "analysis", "assurance",
                   "_stage4_s3_coverage_20260929.json")

PREFIX = {
    "shadow_runtime": "supporting/shadow_runtime/schema_version=shadow_runtime_v1/",
    "events": "core/events/schema_version=events_v1/",
    "market_context": "core/market_context/schema_version=market_context_v1/",
    "decision_trace": "supporting/decision_trace/schema_version=decision_trace_v1/",
    "horizon_candidates":
        "supporting/horizon_candidates/schema_version=horizon_candidates_v1/",
    "strategy_candidates":
        "supporting/strategy_candidates/schema_version=strategy_candidates_v1/",
    "portfolio_rankings":
        "supporting/portfolio_rankings/schema_version=portfolio_ranking_v1/",
    "strategy_observations":
        "supporting/strategy_observations/schema_version=strategy_observation_v1/",
    "opportunities": "core/opportunities/schema_version=opportunities_v1/",
}

SHADOW_PROBES = [
    "shadow_trade_id", "canonical_opportunity_id", "identity.shadow_type",
    "identity.trade_horizon", "identity.evaluated_horizon",
    "identity.canonical_opportunity_id",
    "symbol", "horizon", "schema_version", "recorded_at_utc",
    "entry_market_time", "entry_market_time_utc_epoch_s",
    "entry_market_time_utc_iso8601",
    "exit_market_time", "exit_market_time_utc_epoch_s",
    "exit_market_time_utc_iso8601",
    "market_timestamp_semantics", "market_timestamp_normalization_version",
    "broker_offset_seconds",
    "outcome.pnl_r_multiple", "outcome.mae_r", "outcome.mfe_r",
    "simulated_outcome.pnl_r_multiple",
    "live_facts.market_phase", "live_facts.h4_regime", "live_facts.pattern",
    "live_facts.strategy", "live_facts.regime",
    "live_facts.v10_selected_horizon", "live_facts.horizon_selection_status",
    "trade_state_progression",
    "experiment_arm", "arm_assigned_at", "arm_schema_version",
]

EVENT_PROBES = [
    "type", "source", "schema_version", "timeframe", "symbol", "ts_utc_ms",
    "payload.ts", "payload.open", "payload.high", "payload.low", "payload.close",
    "payload.o", "payload.h", "payload.l", "payload.c",
    "event_layout_version", "feature_version",
]

PROBES = {
    "shadow_runtime": SHADOW_PROBES,
    "events": EVENT_PROBES,
    "market_context": ["regime", "phase", "symbol", "bar_time", "timestamp_utc",
                       "schema_version", "h4.regime", "h1.direction"],
    "decision_trace": ["canonical_opportunity_id", "trade_horizon", "symbol",
                       "p_success", "v10_entry", "v10_market_state.regime",
                       "v10_market_state.h4.market_phase",
                       "v10_market_state.h1.dominant_trend",
                       "timestamp_utc", "schema_version"],
    "horizon_candidates": ["canonical_opportunity_id", "horizon", "selection_status",
                           "confidence", "symbol", "bar_time", "eligible"],
    "strategy_candidates": ["canonical_opportunity_id", "confidence", "rank", "selected",
                            "symbol", "bar_time", "rank_position", "selection_status"],
    "portfolio_rankings": ["candidates.selection_status", "candidates.rank_position",
                           "symbol", "cycle_id", "timestamp_utc", "schema_version"],
    "strategy_observations": ["canonical_opportunity_id", "market_phase", "h4_regime",
                              "strategy_family", "detected_pattern", "symbol",
                              "bar_time", "authority", "record_role", "timestamp_utc"],
    "opportunities": ["canonical_opportunity_id", "opportunity_id", "state",
                      "overall_score", "h4_regime", "bias_phase", "symbol", "bar_time"],
}


def dig(rec, dotted):
    cur = rec
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def scan(c, name, prefix, probes):
    total = objs = 0
    hit, nullish, ev_types, tfs, dates = Counter(), Counter(), Counter(), Counter(), set()
    for key, _size, _m in walk(c, prefix):
        objs += 1
        if "date=" in key:
            dates.add(key.split("date=")[1].split("/")[0])
        body = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        for line in body.split(b"\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            total += 1
            for p in probes:
                if dig(rec, p):
                    hit[p] += 1
                    v = rec
                    for part in p.split("."):
                        v = v[part]
                    if v is None or v == "" or v == []:
                        nullish[p] += 1
            if name == "events":
                ev_types[str(rec.get("type"))] += 1
                tfs[f"{rec.get('source')}:{rec.get('timeframe')}"] += 1
    res = {
        "dataset": name, "prefix": prefix, "objects": objs, "rows": total,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "probe_coverage": {p: f"{hit[p]}/{total}" for p in probes},
        "probe_nullish": {p: nullish[p] for p in probes if nullish[p]},
    }
    if name == "events":
        res["event_types"] = dict(ev_types)
        res["source_timeframe"] = dict(tfs)
    return res


def main():
    c = client()
    only = sys.argv[1:] or list(PROBES)
    out = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            out = json.load(fh)
    for name in only:
        if name in out:
            print(f"### {name}: already scanned, skipping", flush=True)
            continue
        r = scan(c, name, PREFIX[name], PROBES[name])
        out[name] = r
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(json.dumps(r, indent=1)[:5500], flush=True)
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
