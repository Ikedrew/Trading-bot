#!/usr/bin/env python
"""Read-only check of writer signatures and canonical plumbing."""
import io, sys, re, ast, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"C:\Users\ikues\Trading bot build"

def show_sigs(path, names):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print(f"MISSING FILE: {path}")
        return
    src = open(full, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in names:
                found.add(node.name)
                args = [a.arg for a in node.args.args]
                kwonly = [a.arg for a in node.args.kwonlyargs]
                defaults = len(node.args.defaults)
                print(f"{path} :: def {node.name} (line {node.lineno})")
                print(f"   positional: {args}")
                if kwonly:
                    print(f"   kwonly: {kwonly}")
                has_canon = any("canonical" in a for a in args + kwonly)
                print(f"   accepts canonical_opportunity_id: {has_canon}")
    for n in names - found:
        print(f"{path} :: NOT FOUND: {n}")

print("=" * 70)
print("WRITER SIGNATURE AUDIT")
print("=" * 70)

# decision_audit writer
show_sigs(r"core\decision_audit.py", {"persist_decision_audit", "write_decision_audit", "record_decision_audit", "DecisionAuditWriter"})

# decision_trace writer
show_sigs(r"core\persistence\decision_trace_writer.py", {"persist_decision_trace", "write_decision_trace", "record_decision_trace", "append_decision_trace"})

# decision_ledger
show_sigs(r"core\decision_ledger.py", {"persist_decision_ledger", "write_decision_ledger", "record_decision", "DecisionLedger"})

# decision_recorder
show_sigs(r"core\runtime\decision_recorder.py", {"init_cycle", "finalize", "record"})

print("\n" + "=" * 70)
print("CALL SITES OF persist_decision_audit / trace writers in live_scanner.py")
print("=" * 70)
src = open(os.path.join(ROOT, r"core\runtime\live_scanner.py"), encoding="utf-8", errors="replace").read()
lines = src.splitlines()
for i, ln in enumerate(lines, 1):
    if re.search(r"persist_decision_audit|write_decision_trace|persist_decision_trace|record_decision_audit", ln):
        print(f"  L{i}: {ln.strip()[:150]}")

print("\n" + "=" * 70)
print("canonical_opportunity_id occurrences in live_scanner.py")
print("=" * 70)
for i, ln in enumerate(lines, 1):
    if "canonical_opportunity_id" in ln or "_canonical_opp_id" in ln:
        print(f"  L{i}: {ln.strip()[:140]}")

print("\nDONE")
