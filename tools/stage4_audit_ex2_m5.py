"""Stage 4 audit: EX2 focused probe -- do authoritative M5 OHLC bars exist in
events_v1, and do they bind to shadow_runtime lifecycles?  READ ONLY."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.stage4_audit_s3_probe import BUCKET, client, walk  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "analysis", "assurance",
                   "_stage4_ex2_m5_probe_20260929.json")

EVENTS = "core/events/schema_version=events_v1/"


def _list(c, prefix, delim="/"):
    """Delimiter listing that returns prefixes (delim='/') or keys (delim='')."""
    out, tok = [], None
    kwargs = {"Bucket": BUCKET, "Prefix": prefix, "Delimiter": delim}
    while True:
        r = c.list_objects_v2(**kwargs)
        if delim:
            out.extend(p["Prefix"] for p in r.get("CommonPrefixes", []))
        else:
            out.extend(o["Key"] for o in r.get("Contents", []))
        if not r.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = r["NextContinuationToken"]
    return out


def main():
    c = client()
    types, s_tf, layouts = Counter(), Counter(), Counter()
    payload_keys = Counter()
    m5_dates, m5_by_symbol = set(), Counter()
    candle_total = m5_ohlc_complete = 0
    # Enumerate the (symbol, date) partition grid via delimiter listing, then
    # read exactly ONE canonical object per partition. Bounded and deterministic.
    grid = []
    for sym_prefix in _list(c, EVENTS):
        for date_prefix in _list(c, sym_prefix):
            keys = _list(c, date_prefix, delim="")
            if keys:
                grid.append((sym_prefix.split("symbol=")[1].split("/")[0],
                             date_prefix.split("date=")[1].split("/")[0],
                             sorted(keys)[0]))
    for sym, day, key in sorted(grid):
        body = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        for line in body.split(b"\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            types[str(rec.get("type"))] += 1
            s_tf[f"{rec.get('source')}:{rec.get('timeframe')}"] += 1
            layouts[str(rec.get("event_layout_version"))] += 1
            if str(rec.get("type")) == "CANDLE":
                candle_total += 1
                p = rec.get("payload") or {}
                for k in p:
                    payload_keys[k] += 1
                if rec.get("timeframe") == "M5":
                    m5_by_symbol[str(rec.get("symbol"))] += 1
                    m5_dates.add(day)
                    if all(k in p for k in ("o", "h", "l", "c", "ts")):
                        m5_ohlc_complete += 1
    objs = len(grid)
    res = {
        "events_prefix": EVENTS,
        "sampling": "one canonical part-000.jsonl object per (symbol,date) partition",
        "objects_scanned": objs,
        "event_types": dict(types),
        "source_timeframe": dict(s_tf),
        "event_layout_versions": dict(layouts),
        "candle_payload_keys": dict(payload_keys),
        "candle_rows": candle_total,
        "m5_rows": sum(m5_by_symbol.values()),
        "m5_ohlc_complete_rows": m5_ohlc_complete,
        "m5_date_partitions": sorted(x for x in m5_dates if x),
        "m5_date_partition_count": len([x for x in m5_dates if x]),
        "m5_by_symbol": dict(m5_by_symbol),
        "m5_lifecycle_binding_fields_present": False,
        "binding_note": (
            "events_v1 CANDLE rows carry no shadow_trade_id, "
            "canonical_opportunity_id or trade_horizon. Binding to a lifecycle "
            "is only possible post hoc by (symbol, ts) range join."
        ),
    }
    print(json.dumps(res, indent=1))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
