import json, os
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")
RUN_DAY = "2026-08-28"

out = []
def p(s): out.append(s)

datasets = sorted(d for d in os.listdir(LOGS) if os.path.isdir(os.path.join(LOGS, d)))
p(f"{'DATASET':34} {'FILES':>6} {'RUNDAY_F':>9} {'RUNDAY_LINES':>12}  SAMPLE_KEYS")
for d in datasets:
    dpath = os.path.join(LOGS, d)
    files = []
    for r, _, fs in os.walk(dpath):
        for f in fs:
            files.append(os.path.join(r, f))
    run_files = [f for f in files if RUN_DAY in os.path.basename(f)]
    run_lines = 0
    sample_keys = ""
    for f in run_files[:1]:
        try:
            with open(f, encoding="utf-8") as fh:
                lines = fh.readlines()
            run_lines += len(lines)
            if lines:
                rec = json.loads(lines[0])
                sample_keys = ",".join(sorted(rec.keys()))[:180]
        except Exception as e:
            sample_keys = f"ERR:{e}"
    # for flat files w/o date in name
    if not run_files:
        dated = [f for f in files if RUN_DAY in f]
        run_files = dated
    p(f"{d:34} {len(files):>6} {len(run_files):>9} {run_lines:>12}  {sample_keys}")

# research_data tree
p("\n=== research_data tree ===")
rd = os.path.join(ROOT, "research_data")
for r, dirs, fs in os.walk(rd):
    rel = os.path.relpath(r, ROOT)
    p(f"{rel}\\  files={len(fs)}")
    for f in fs[:5]:
        p(f"   {f}  ({os.path.getsize(os.path.join(r,f))} bytes)")

print("\n".join(out))
