"""Fix the TRADE_MANAGEMENT enum entry."""
from pathlib import Path
import py_compile

p = Path("research_engine/registry/research_question_models.py")
text = p.read_text(encoding="utf-8")

# The broken line is just "    TRADE_MANAGEMENT\n"
# Fix to: "    TRADE_MANAGEMENT = \"TRADE_MANAGEMENT\"\n"
old = '    TRADE_MANAGEMENT\n'
new = '    TRADE_MANAGEMENT = "TRADE_MANAGEMENT"  # MGMT: Trade management effectiveness\n'
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")

py_compile.compile(str(p), doraise=True)
print("TRADE_MANAGEMENT enum fixed + compiled OK")
