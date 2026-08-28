import re, sys, os
ROOT = r"C:\Users\ikues\Trading bot build"
TARGETS = [
    r"core\runtime\live_scanner.py",
    r"core\runtime\decision_recorder.py",
    r"core\persistence\decision_trace_writer.py",
    r"core\runtime\engine_outcome_handler.py",
    r"core\decision_audit.py",
    r"core\decision_trace.py",
    r"core\decision_ledger.py",
]
PATTERNS = [
    r"_canonical_opp_id",
    r"canonical_opportunity_id",
    r"persist_decision_audit|record_decision_trace|persist_decision_trace|DecisionRecorder|record_cycle|persist_ledger|write_trace|persist\(",
]
for t in TARGETS:
    p = os.path.join(ROOT, t)
    if not os.path.exists(p):
        print(f"MISSING: {t}")
        continue
    print(f"\n===== {t} =====")
    with open(p, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            for pat in PATTERNS:
                if re.search(pat, line):
                    print(f"{i}: {line.rstrip()[:160]}")
                    break
