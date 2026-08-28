import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
files = [
    r"C:\Users\ikues\Trading bot build\core\runtime\decision_recorder.py",
    r"C:\Users\ikues\Trading bot build\core\runtime\live_scanner.py",
]
for path in files:
    print(f"=== {path.split(chr(92))[-1]} ===")
    src = open(path, encoding='utf-8').read().splitlines()
    pat = re.compile(r"canonical_opportunity_id|set_canonical|_cycle_decision\[")
    for i, line in enumerate(src, 1):
        if pat.search(line):
            print(f"{i}: {line.strip()[:170]}")
    print()
