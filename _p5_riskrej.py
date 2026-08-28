#!/usr/bin/env python
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\Users\ikues\Trading bot build"
src = open(ROOT + r"\core\runtime\live_scanner.py", encoding="utf-8", errors="replace").read()
lines = src.splitlines()
for i, ln in enumerate(lines, 1):
    if "persist_risk_rejection" in ln and "import" not in ln:
        # print surrounding context
        for j in range(max(0, i - 12), min(len(lines), i + 18)):
            print(f"L{j+1}: {lines[j][:150]}")
        print("---")
