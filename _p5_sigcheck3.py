#!/usr/bin/env python
"""Verify remaining signatures + trace writer canonical plumbing."""
import io, sys, ast, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"C:\Users\ikues\Trading bot build"

def sigs(path, names):
    full = os.path.join(ROOT, path)
    src = open(full, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            print(f"{path} :: {node.name} (L{node.lineno}) canonical={'canonical_opportunity_id' in args}")

sigs(r"core\decision_audit.py", {"persist_risk_rejection"})
sigs(r"core\runtime\engine_execution_handler.py", {"prepare_execution"})

# Trace writer: how is the trace persisted and where does canonical come from?
full = os.path.join(ROOT, r"core\persistence\decision_trace_writer.py")
src = open(full, encoding="utf-8", errors="replace").read()
print("\n--- decision_trace_writer.py (full) ---")
print(src)

# Where does the trace dict get built in decision_trace.py? does it copy canonical from new_result?
full2 = os.path.join(ROOT, r"core\decision_trace.py")
src2 = open(full2, encoding="utf-8", errors="replace").read()
print("\n--- canonical mentions in decision_trace.py ---")
for i, ln in enumerate(src2.splitlines(), 1):
    if "canonical" in ln.lower():
        print(f"  L{i}: {ln.strip()[:140]}")

# does live_scanner pass new_result into the trace build?
full3 = os.path.join(ROOT, r"core\runtime\live_scanner.py")
src3 = open(full3, encoding="utf-8", errors="replace").read()
lines3 = src3.splitlines()
print("\n--- live_scanner: trace build call sites ---")
for i, ln in enumerate(lines3, 1):
    if re.search(r"record_decision_trace|build_trace|decision_trace", ln) and "import" not in ln:
        print(f"  L{i}: {ln.strip()[:140]}")
