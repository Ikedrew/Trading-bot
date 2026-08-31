import os, json
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")
DAY = "2026-08-28"

def iter_day_files(base):
    if not os.path.isdir(base):
        return
    for root, _, files in os.walk(base):
        for f in files:
            if DAY in f:
                yield os.path.join(root, f)

print("=" * 80)
print("RUN-DAY (2026-08-28) RECORD COUNTS PER logs/ DATASET")
print("=" * 80)
for d in sorted(os.listdir(LOGS)):
    p = os.path.join(LOGS, d)
    if not os.path.isdir(p):
        print(f"logs/{d}: (file)")
        continue
    total = 0
    files_n = 0
    samples = []
    for fp in iter_day_files(p):
        files_n += 1
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                lines = [l for l in fh if l.strip()]
            total += len(lines)
            if lines and len(samples) < 1:
                try:
                    samples.append(json.loads(lines[0]))
                except Exception:
                    samples.append({"_unparsed": lines[0][:120]})
        except Exception as e:
            pass
    print(f"logs/{d}: files={files_n} records={total}")

print()
print("=" * 80)
print("research_data/ INVENTORY")
print("=" * 80)
rd = os.path.join(ROOT, "research_data")
if os.path.isdir(rd):
    for root, dirs, files in os.walk(rd):
        rel = os.path.relpath(root, rd)
        n = len(files)
        if n or rel != ".":
            print(f"research_data/{rel}: files={n}")
else:
    print("research_data/ does not exist")

print()
print("=" * 80)
print("research_projection/ INVENTORY")
print("=" * 80)
rp = os.path.join(ROOT, "research_projection")
if os.path.isdir(rp):
    for f in sorted(os.listdir(rp)):
        print(f"research_projection/{f}")
