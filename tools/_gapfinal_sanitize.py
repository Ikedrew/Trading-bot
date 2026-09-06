"""One-shot: ASCII-sanitize the final ingestion audit tool (idempotent)."""
from pathlib import Path
import py_compile

p = Path("tools/final_ingestion_audit.py")
src = p.read_text(encoding="utf-8")

repl = {
    "\u2550": "=",
    "\u2192": "->",
    "\u2713": "[OK]",
    "\u2705": "[OK]",
    "\u26a0": "[WARN]",
    "\u274c": "[X]",
    "\u2014": "-",
    "\u2013": "-",
    "\u2190": "<-",
}
for k, v in repl.items():
    src = src.replace(k, v)

# ensure stdout never crashes on any remaining non-ASCII
if "reconfigure" not in src:
    anchor = "import json\n"
    gate = (
        "import sys as _sys\n"
        "if hasattr(_sys.stdout, 'reconfigure'):\n"
        "    _sys.stdout.reconfigure(errors='replace')\n"
        "    _sys.stderr.reconfigure(errors='replace')\n"
    )
    src = src.replace(anchor, anchor + gate, 1)

p.write_text(src, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("ASCII-sanitized + compiled OK")
