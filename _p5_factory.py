import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
for path in [
    r"C:\Users\ikues\Trading bot build\core\opportunity\factory.py",
]:
    print(f"=== {path.split(chr(92))[-1]} ===")
    src = open(path, encoding='utf-8').read().splitlines()
    pat = re.compile(r"make_canonical_opportunity_id|bar_time|def create_opportunity|pattern")
    for i, line in enumerate(src, 1):
        if pat.search(line):
            print(f"{i}: {line.strip()[:170]}")
    print()
# Where do _raw_patterns come from? find their class
import subprocess
