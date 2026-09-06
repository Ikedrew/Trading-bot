"""Fix DataSource enum: add MANAGEMENT_ACTIONS."""
from pathlib import Path
import py_compile

p = Path("research_engine/registry/research_question_models.py")
text = p.read_text(encoding="utf-8")

if "MANAGEMENT_ACTIONS" not in text:
    anchor = '    TRADE_TRUTH = "trade_truth"\n'
    insert = '    MANAGEMENT_ACTIONS = "management_actions"\n'
    text = text.replace(anchor, anchor + insert)
    p.write_text(text, encoding="utf-8")
    print("added MANAGEMENT_ACTIONS to DataSource")
else:
    print("already present")

py_compile.compile(str(p), doraise=True)
print("py_compile OK")
