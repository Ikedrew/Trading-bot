import json, os, re
from collections import Counter
from datetime import datetime, timezone

ROOT = r"c:\Users\ikues\Trading bot build"
WIN_START = 1787957700
WIN_END   = 1787959800

def load(d_, date="2026-08-28"):
    recs = []
    d = os.path.join(ROOT, "logs", d_)
    if not os.path.isdir(d):
        return recs
    for root, _, files in os.walk(d):
        for f in files:
            if date in f:
                try:
                    with open(os.path.join(root, f)) as fh:
                        for line in fh:
                            line = line.strip()
                            if line:
                                recs.append(json.loads(line))
                except Exception:
                    pass
    return recs

# Refined error scan: only records with an explicit error level/status
print("=== REFINED ERROR SCAN (explicit error level/status) ===")
hits = []
logs_dir = os.path.join(ROOT, "logs")
for root, _, files in os.walk(logs_dir):
    for f in files:
        if "2026-08-28" in f and f.endswith(".jsonl"):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, logs_dir)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        lvl = str(rec.get("level", "")).upper()
                        status = str(rec.get("status", "")).upper()
                        evt = str(rec.get("event_type", rec.get("event", ""))).upper()
                        if lvl in ("ERROR","FATAL","CRITICAL") or "ERROR" in status or "FATAL" in status or "ERROR" in evt or "RECOVERY" in evt:
                            hits.append((rel, i, lvl, status, evt, line[:130]))
            except Exception:
                pass
print(f"explicit error/fatal/recovery records: {len(hits)}")
for h in hits[:20]:
    print(f"  {h[0]}:{h[1]} lvl={h[2]} status={h[3]} evt={h[4]} :: {h[5][:110]}")

# DATA_GAP / gap events anywhere
print("\n=== DATA_GAP SCAN ===")
gap_hits = 0
for root, _, files in os.walk(logs_dir):
    for f in files:
        if "2026-08-28" in f and f.endswith(".jsonl"):
            p = os.path.join(root, f)
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "DATA_GAP" in line or "data_gap" in line:
                        gap_hits += 1
print(f"lines mentioning data gaps: {gap_hits}")

# Assessments in window
asses = load("assessments")
def get_ts(r):
    for k in ("timestamp_unix","ts_utc_ms","timestamp","bar_time"):
        v = r.get(k)
        if isinstance(v,(int,float)):
            return v/1000.0 if v > 10**12 else v
    return None
a_win = [r for r in asses if (get_ts(r) and WIN_START <= get_ts(r) <= WIN_END) or (r.get("detected_at_bar_time") and WIN_START <= r["detected_at_bar_time"] <= WIN_END)]
print(f"\n=== ASSESSMENTS === day total={len(asses)}, in-window={len(a_win)}")
if asses:
    print("assessment keys:", sorted(asses[0].keys())[:18])
    print("assessment canonical populated:", sum(1 for r in asses if r.get("canonical_opportunity_id")), "/", len(asses))

# Live trades today (execution_results 2 records) - details
ex = load("execution_results")
print(f"\n=== EXECUTION_RESULTS (day): {len(ex)} ===")
for r in ex:
    print(f"  {json.dumps({k: r.get(k) for k in list(r.keys())[:12]}, default=str)[:250]}")

# decision_trace for the window
dt = load("decision_trace")
print(f"\ndecision_trace day total: {len(dt)}")

# Check bot runtime window via file mtimes
print("\n=== RUN WINDOW (file mtimes) ===")
p_eur = os.path.join(ROOT, "logs", "shadow_runtime_v1", "EURUSD", "2026-08-28.jsonl")
print(f"shadow EURUSD file mtime: {datetime.fromtimestamp(os.path.getmtime(p_eur)).isoformat()}")
p_opp = os.path.join(ROOT, "logs", "opportunities", "EURUSD", "2026-08-28.jsonl")
print(f"opportunities EURUSD file mtime: {datetime.fromtimestamp(os.path.getmtime(p_opp)).isoformat()}")
