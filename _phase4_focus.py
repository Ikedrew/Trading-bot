#!/usr/bin/env python
"""Focused: session-attributed new-process records + v3_shadow fresh-file counts. READ-ONLY."""
import json, os, datetime

ROOT = r"C:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")
SESS = "c7bc9645c653"
FRESH = 1787788800.0

def _fmt(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t else "n/a"

print("=" * 100)
print("A) Records of NEW session c7bc9645c653 by file (opportunities / decision_audit / decision_trace)")
print("=" * 100)
for label in ("opportunities", "decision_audit", "decision_trace"):
    d = os.path.join(LOGS, label)
    if not os.path.exists(d):
        continue
    per_file = {}
    tot_new = 0
    can_pop = 0
    can_empty = 0
    nf = 0
    for _r, _dd, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl"):
                continue
            if "2026-08-26" not in fn and "2026-08-27" not in fn:
                continue
            fp = os.path.join(_r, fn)
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("runtime_session_id") != SESS:
                        continue
                    tot_new += 1
                    per_file[fn] = per_file.get(fn, 0) + 1
                    cid = rec.get("canonical_opportunity_id")
                    if cid is None:
                        nf += 1
                    elif cid:
                        can_pop += 1
                    else:
                        can_empty += 1
    print(f"  {label}: total_new_session={tot_new} canonical_populated={can_pop} canonical_empty={can_empty} no_field={nf}")
    for k, v in sorted(per_file.items()):
        print(f"      {k}: {v}")

print()
print("=" * 100)
print("B) Execution_context NEW-window records (session absent; use ts>=fresh)")
print("=" * 100)
d = os.path.join(LOGS, "execution_context")
per_file = {}
tot = 0
empty_can = 0
pop_can = 0
sym_can = {}
for _r, _dd, fs in os.walk(d):
    for fn in sorted(fs):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(_r, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("event_market_time") or rec.get("timestamp_unix")
                # execution_context uses event_market_time (bar) - classify by file date
                if "2026-08-27" not in os.path.join(_r, fn):
                    continue
                tot += 1
                cid = rec.get("canonical_opportunity_id")
                if cid:
                    pop_can += 1
                    sym_can[rec.get("symbol","?")] = sym_can.get(rec.get("symbol","?"),0) + 1
                else:
                    empty_can += 1
                per_file[fn] = per_file.get(fn, 0) + 1
print(f"  execution_context 2026-08-27 totals: {tot}  canonical_pop={pop_can}  canonical_empty={empty_can}")
for k, v in sorted(per_file.items()):
    print(f"      {k}: {v}")

print()
print("=" * 100)
print("C) v3_shadow 2026-08-27 files - record counts and timestamp range")
print("=" * 100)
for sub in ("market_understanding", "market_context", "opportunity_assessment",
            "horizon_assessment", "risk_assessment", "entry_assessment", "execution_assessment"):
    d = os.path.join(LOGS, "v3_shadow", sub)
    if not os.path.exists(d):
        continue
    tot = 0
    mn = mx = None
    common_keys = {}
    for _r, _dd, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl") or "2026-08-27" not in fn:
                continue
            with open(os.path.join(_r, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    tot += 1
                    ts = rec.get("timestamp_utc")
                    if isinstance(ts, (int, float)) and ts > 0:
                        if mn is None or ts < mn:
                            mn = ts
                        if mx is None or ts > mx:
                            mx = ts
                    for k in ("entity_id", "canonical_opportunity_id", "observation_id", "cycle_id", "correlation_id"):
                        if k in rec:
                            common_keys[k] = common_keys.get(k, 0) + 1
    print(f"  {sub}: records_2026-08-27={tot} first_ts={_fmt(mn)} last_ts={_fmt(mx)}")
    if common_keys:
        print(f"      identity-field presence: {common_keys}")

print()
print("=" * 100)
print("D) events/2026-08-27.jsonl tail timestamp + heartbeat/state last writes")
print("=" * 100)
ev = os.path.join(ROOT, "events", "2026-08-27.jsonl")
if os.path.exists(ev):
    mx = 0
    with open(ev, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            v = rec.get("ts_utc_ms") or 0
            if v > mx:
                mx = v
    print(f"  events/2026-08-27.jsonl last ts_utc_ms={mx} -> {_fmt(mx/1000.0)}")
print(f"  heartbeat.json (logs): {_fmt(os.path.getmtime(os.path.join(LOGS,'heartbeat.json')))} mtime")
print("  (content read earlier: cycle 544 alive ts=00:03:09Z)")
print(f"  heartbeat.json (runtime): {_fmt(os.path.getmtime(os.path.join(ROOT,'runtime','heartbeat.json')))} mtime ->" )
with open(os.path.join(ROOT, "runtime", "heartbeat.json"), encoding="utf-8") as f:
    print("    " + f.read().strip())
print("\n=== FOCUS SCAN COMPLETE ===")