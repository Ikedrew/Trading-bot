# Consolidated forensic evidence: dataset inventory + run-day counts
import os, json
from collections import Counter

ROOT = r"c:\Users\ikues\Trading bot build"
LOGS = os.path.join(ROOT, "logs")

out = []

def scan(dirpath, label):
    if not os.path.isdir(dirpath):
        out.append(f"{label}: MISSING")
        return
    total = 0
    files = 0
    today = 0
    for r, _, fs in os.walk(dirpath):
        for f in fs:
            files += 1
            p = os.path.join(r, f)
            try:
                sz = os.path.getsize(p)
                total += sz
            except OSError:
                pass
            if "2026-08-28" in f:
                today += 1
    out.append(f"{label}: files={files} bytes={total} files_with_20260828={today}")

out.append("=== LOGS ROOT ===")
out.append("top-level dirs: " + ", ".join(sorted(d for d in os.listdir(LOGS) if os.path.isdir(os.path.join(LOGS, d)))))
out.append("top-level files: " + ", ".join(sorted(d for d in os.listdir(LOGS) if os.path.isfile(os.path.join(LOGS, d)))[:20]))
out.append("")
out.append("=== PER-DATASET INVENTORY (logs/*) ===")
for d in sorted(os.listdir(LOGS)):
    p = os.path.join(LOGS, d)
    if os.path.isdir(p):
        scan(p, d)
