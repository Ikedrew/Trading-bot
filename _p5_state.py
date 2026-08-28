import io, re, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = r"C:\Users\ikues\Trading bot build"

# 1. Find all _canonical_opp_id usages in live_scanner
p = BASE + r"\core\runtime\live_scanner.py"
src = open(p, encoding="utf-8").read()
print("=== _canonical_opp_id usages in live_scanner.py ===")
for i, line in enumerate(src.splitlines(), 1):
    if "_canonical_opp_id" in line:
        print(f"  L{i}: {line.strip()[:150]}")

# 2. Check writer signatures accept canonical_opportunity_id
print("\n=== writer signature checks ===")
files_to_check = {
    "decision_trace.py": r"core\decision_trace.py",
    "decision_audit.py": r"core\decision_audit.py",
    "decision_ledger.py": r"core\decision_ledger.py",
    "decision_recorder.py": r"core\runtime\decision_recorder.py",
    "persistence/decision_trace_writer.py": r"core\persistence\decision_trace_writer.py",
}
for name, rel in files_to_check.items():
    fp = BASE + "\\" + rel
    try:
        s = open(fp, encoding="utf-8").read()
    except Exception as e:
        print(f"  {name}: ERROR {e}")
        continue
    hits = []
    for i, line in enumerate(s.splitlines(), 1):
        if "canonical_opportunity_id" in line:
            hits.append(f"    L{i}: {line.strip()[:140]}")
    print(f"  {name}: {len(hits)} refs")
    for h in hits[:12]:
        print(h)

# 3. Check market_context models/builder edits
print("\n=== market_context identity edits ===")
for rel in [r"core\market_context\models.py", r"core\market_context\builder.py"]:
    s = open(BASE + "\\" + rel, encoding="utf-8").read()
    for i, line in enumerate(s.splitlines(), 1):
        if "entity_id" in line or "correlation_id" in line or "cycle_id" in line or "bar_time" in line:
            print(f"  {rel} L{i}: {line.strip()[:140]}")

# 4. Verify compiled state
import py_compile
print("\n=== py_compile ===")
for rel in [r"core\runtime\live_scanner.py", r"core\market_context\models.py", r"core\market_context\builder.py",
            r"core\decision_trace.py", r"core\decision_audit.py", r"core\decision_ledger.py"]:
    try:
        py_compile.compile(BASE + "\\" + rel, doraise=True)
        print(f"  {rel}: OK")
    except Exception as e:
        print(f"  {rel}: FAIL {e}")
