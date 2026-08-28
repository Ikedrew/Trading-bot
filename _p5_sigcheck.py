#!/usr/bin/env python
"""READ-ONLY: verify writer signatures accept canonical_opportunity_id + find all call sites."""
import io, sys, re, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\Users\ikues\Trading bot build\core"

targets = [
    "persist_decision_audit", "persist_risk_rejection", "persist_decision_trace",
    "handle_live_opportunity_shadow", "prepare_execution",
]

def scan(root):
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                src = open(p, encoding="utf-8").read()
            except Exception:
                continue
            rel = os.path.relpath(p, r"C:\Users\ikues\Trading bot build")
            for m in re.finditer(r"def (\w+)\(([^)]{0,1500})", src, re.S):
                name, params = m.group(1), m.group(2)
                if name in targets:
                    has = "canonical_opportunity_id" in params
                    line = src[: m.start()].count("\n") + 1
                    print(f"DEF  {rel}:{line}: {name}(...) canonical={'YES' if has else 'NO'}")
            for t in targets:
                for m in re.finditer(re.escape(t) + r"\(", src):
                    line = src[: m.start()].count("\n") + 1
                    # skip the def line itself
                    pre = src[max(0, m.start() - 4): m.start()]
                    if pre.endswith("def "):
                        continue
                    print(f"CALL {rel}:{line}: {t}(")

scan(ROOT)

# Also check decision trace/ledger writer signatures
extra = os.path.join(ROOT, "persistence")
for f in os.listdir(extra) if os.path.isdir(extra) else []:
    pass

# TradeIdentity
ti = os.path.join(r"C:\Users\ikues\Trading bot build", "core", "trade_identity.py")
if os.path.exists(ti):
    src = open(ti, encoding="utf-8").read()
    print("TradeIdentity canonical:", "canonical_opportunity_id" in src)

# decision recorder
dr = os.path.join(ROOT, "runtime", "decision_recorder.py")
src = open(dr, encoding="utf-8").read()
print("decision_recorder canonical:", "canonical_opportunity_id" in src)
