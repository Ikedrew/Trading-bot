"""Add MGMT1/MGMT2 to the REGISTRY tuple."""
from pathlib import Path
import py_compile

p = Path("research_engine/registry/research_question_registry.py")
text = p.read_text(encoding="utf-8")

old = (
    "    # Exit Management\n"
    "    EX1, EX2, EX3, EX4, EX5, EX6, EX7, EX8, EX9, EX10,\n"
    ")"
)
new = (
    "    # Exit Management\n"
    "    EX1, EX2, EX3, EX4, EX5, EX6, EX7, EX8, EX9, EX10,\n"
    "    # Trade Management\n"
    "    MGMT1, MGMT2,\n"
    ")"
)
assert old in text, "REGISTRY tuple exit section not found"
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("MGMT1/MGMT2 added to REGISTRY tuple + compiled OK")
