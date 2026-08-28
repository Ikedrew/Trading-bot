import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

path = r"C:\Users\ikues\Trading bot build\core\runtime\live_scanner.py"
src = open(path, encoding="utf-8").read()
lines = src.split("\n")

targets = [
    "_canonical_opp_id",
    "_raw_patterns",
    "_observation_id_cycle",
    "persist_decision_trace",
    "persist_decision_audit",
    "record_decision",
    "persist_decision_ledger",
    "canonical_opportunity_id",
]
print("=== occurrences with line numbers ===")
for t in targets:
    print(f"\n--- {t} ---")
    for i, ln in enumerate(lines, 1):
        if t in ln:
            print(f"  {i}: {ln.strip()[:160]}")
