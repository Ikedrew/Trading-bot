import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
path = r"C:\Users\ikues\Trading bot build\core\runtime\live_scanner.py"
src = open(path, encoding='utf-8').read().splitlines()
pat = re.compile(r"persist_risk_rejection|prepare_execution|canonical_opportunity_id\s*=")
for i, line in enumerate(src, 1):
    if pat.search(line):
        print(f"{i}: {line.strip()[:160]}")
