#!/usr/bin/env python
"""DEFINITIVE Phase-4 record-level scan. READ-ONLY. No writes to repo data."""
import json, os, datetime

ROOT = r"C:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")
FRESH_MIN_TS = 1787788800.0          # 2026-08-27T00:00:00Z
SESSION_NEW = "c7bc9645c653"

def _iso(s):
    try:
        s = str(s).replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def pick_ts(rec):
    for key in ("ts_utc_ms", "recorded_at_utc_ms", "closed_at_utc_ms"):
        v = rec.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return v / 1000.0
    for key in ("timestamp_unix", "event_market_time_utc_epoch_s"):
        v = rec.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    for key in ("timestamp_utc", "recorded_at_utc", "closed_at_utc", "timestamp"):
        v = rec.get(key)
        if isinstance(v, str) and v:
            t = _iso(v)
            if t:
                return t
    return None

def _fmt(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t else "n/a"

def scan_live_dir(label):
    dirpath = os.path.join(LOGS, label)
    print("\n" + "=" * 100)
    print("LIVE DATASET: " + label)
    print("=" * 100)
    if not os.path.exists(dirpath):
        print("  (missing)"); return
    total = parse_err = new_s = new_t = prev_s = prev_t = nots = 0
    min_ts = max_ts = None
    min_rec = max_rec = None
    sessions = {}
    for _root, _dirs, files in os.walk(dirpath):
        for fn in sorted(files):
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(_root, fn)
            # only consider recent date partitions (boundary window)
            if "2026-08-26" not in fp and "2026-08-27" not in fp:
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        total += 1
                        try:
                            rec = json.loads(line)
                        except Exception:
                            parse_err += 1
                            continue
                        sess = rec.get("runtime_session_id", "")
                        ts = pick_ts(rec)
                        if sess == SESSION_NEW:
                            new_s += 1
                        elif ts is not None and ts >= FRESH_MIN_TS:
                            new_t += 1
                        elif sess:
                            prev_s += 1
                        elif ts is not None:
                            prev_t += 1
                        else:
                            nots += 1
                        if ts is not None:
                            if min_ts is None or ts < min_ts:
                                min_ts = ts; min_rec = (fn, rec.get("symbol", ""), ts, sess)
                            if max_ts is None or ts > max_ts:
                                max_ts = ts; max_rec = (fn, rec.get("symbol", ""), ts, sess)
                        if sess:
                            sessions[sess] = sessions.get(sess, 0) + 1
            except Exception as e:
                print("  ERR", fp, e)
    print(f"  total={total} parse_err={parse_err}")
    print(f"  NEW session({SESSION_NEW})={new_s}  NEW ts>=fresh={new_t}")
    print(f"  PREV session={prev_s}  PREV ts<fresh={prev_t}  no_ts/sess={nots}")
    print(f"  min={_fmt(min_ts)} {min_rec}")
    print(f"  max={_fmt(max_ts)} {max_rec}")
    if sessions:
        print(f"  sessions={sorted(sessions.items())[:15]}")

# 1. LIVE datasets
for label in ("opportunities", "decision_audit", "decision_ledger", "decision_trace",
              "execution_context", "execution_results", "strategy_observations", "risk_deviation"):
    scan_live_dir(label)

# 2. v3_shadow
print("\n" + "=" * 100)
print("v3_shadow (assessment research layer - ALWAYS-ON observer #10)")
print("=" * 100)
for sub in ("market_understanding", "market_context", "opportunity_assessment",
            "horizon_assessment", "risk_assessment", "entry_assessment", "execution_assessment"):
    d = os.path.join(LOGS, "v3_shadow", sub)
    if not os.path.exists(d):
        continue
    tot = 0
    mn = mx = None
    has_can = can_pop = 0
    for _r, _dd, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(_r, fn)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        tot += 1
                        if "canonical_opportunity_id" in rec:
                            has_can += 1
                            if rec["canonical_opportunity_id"]:
                                can_pop += 1
                        ts = pick_ts(rec)
                        if ts:
                            if mn is None or ts < mn:
                                mn = ts
                            if mx is None or ts > mx:
                                mx = ts
            except Exception as e:
                print("  ERR", fp, e)
    print(f"  {sub}: records={tot} canonical_field={has_can} canonical_pop={can_pop} min={_fmt(mn)} max={_fmt(mx)}")

# 3. shadow_runtime_v1
print("\n" + "=" * 100)
print("shadow_runtime_v1 (NEW runtime event stream; gated OFF in fresh proc)")
print("=" * 100)
d = os.path.join(LOGS, "shadow_runtime_v1")
if os.path.exists(d):
    for _root, _dd, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(_root, fn)
            evt = {}
            mn = mx = None
            can = 0
            stids = set()
            planids = set()
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        et = rec.get("event_type", "?")
                        evt[et] = evt.get(et, 0) + 1
                        if rec.get("canonical_opportunity_id"):
                            can += 1
                        if rec.get("shadow_trade_id"):
                            stids.add(rec["shadow_trade_id"])
                        if rec.get("plan_id"):
                            planids.add(rec["plan_id"])
                        ts = pick_ts(rec)
                        if ts:
                            if mn is None or ts < mn:
                                mn = ts
                            if mx is None or ts > mx:
                                mx = ts
            except Exception as e:
                print("  ERR", fp, e)
            print(f"  {os.path.relpath(fp, LOGS)}: records={sum(evt.values())} events={evt} "
                  f"canonical={can} shadow_ids={len(stids)} plans={len(planids)} "
                  f"min={_fmt(mn)} max={_fmt(mx)}")

# 4) shadow_trades (legacy) - only look for 2026-08-26/27 files
print("\n" + "=" * 100)
print("shadow_trades (LEGACY pipeline; gate OFF)")
print("=" * 100)
summ = {"records": 0, "fresh": 0, "canon": 0}
d = os.path.join(LOGS, "shadow_trades")
if os.path.exists(d):
    for _root, _dd, fs in os.walk(d):
        for fn in sorted(fs):
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(_root, fn)
            if "2026-08-26" not in fp and "2026-08-27" not in fp:
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        summ["records"] += 1
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        ts = pick_ts(rec)
                        if ts is not None and ts >= FRESH_MIN_TS:
                            summ["fresh"] += 1
                        for k in ("canonical_opportunity_id", "canonical_opp_id"):
                            if rec.get(k):
                                summ["canon"] += 1
                                break
            except Exception:
                pass
print(f"  total records={summ['records']} fresh(ts>=fresh)={summ['fresh']} with_canonical={summ['canon']}")

print("\n=== SCAN COMPLETE ===")