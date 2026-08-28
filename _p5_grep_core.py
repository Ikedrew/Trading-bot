import os

ROOT = r"C:\Users\ikues\Trading bot build"
patterns = ["persist_new_engine_decision_audit(", "build_decision_trace(", "persist_decision_trace(",
            "_decision_recorder.init_cycle(", "persist_decision_audit(", "build_v10_ledger_entry("]
for base in ("core",):
    root = os.path.join(ROOT, base)
    for dirpath, dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                for p in patterns:
                    if p in line:
                        print(f"{os.path.relpath(fp, ROOT)}:{i}: {line.rstrip()[:130]}")
                        break
print("DONE")