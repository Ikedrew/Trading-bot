import os

ROOT = os.path.dirname(os.path.abspath(__file__))
PATTERNS = ["canonical_opportunity_id"]
SKIP_DIRS = {".git", "__pycache__", ".hypothesis", "node_modules", "logs", "events",
             "replay_data", "tmp_s3_shadows", "reports", "MagicMock", "build", "replay_data"}

out_lines = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(base, fn)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if PATTERNS[0] in line:
                        rel = os.path.relpath(p, ROOT)
                        out_lines.append(f"{rel}:{i}: {line.strip()}")
        except Exception:
            pass

with open("_canonical_usage.txt", "w", encoding="utf-8") as f:
    for h in out_lines:
        f.write(h + "\n")
    f.write(f"\nTOTAL HIT LINES: {len(out_lines)}\n")
print("wrote", len(out_lines))