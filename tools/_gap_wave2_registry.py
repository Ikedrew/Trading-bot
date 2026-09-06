"""One-shot: add MGMT-1 and MGMT-2 to the canonical registry + QuestionCategory."""
from pathlib import Path
import py_compile

# ─── 1. Add TRADE_MANAGEMENT category to the models enum ─────────────────────
models_p = Path("research_engine/registry/research_question_models.py")
models_src = models_p.read_text(encoding="utf-8")

if "TRADE_MANAGEMENT" not in models_src:
    # Find the EXIT_MANAGEMENT entry and add TRADE_MANAGEMENT after it
    anchor = "    EXIT_MANAGEMENT"
    # find the end of that line
    idx = models_src.index(anchor)
    line_end = models_src.index("\n", idx)
    insert = "\n    TRADE_MANAGEMENT"
    models_src = models_src[:line_end] + insert + models_src[line_end:]
    models_p.write_text(models_src, encoding="utf-8")
    print("added TRADE_MANAGEMENT to QuestionCategory")
else:
    print("TRADE_MANAGEMENT already in QuestionCategory")

# ─── 2. Add MGMT-1 and MGMT-2 to the canonical registry ──────────────────────
registry_p = Path("research_engine/registry/research_question_registry.py")
reg_src = registry_p.read_text(encoding="utf-8")

if "MGMT-1" not in reg_src:
    # Find where to insert — after the last EX question, before the next category
    # Find the pattern that marks the end of the EXIT_MANAGEMENT section
    # We'll insert after the last EX definition
    anchor = 'EX10 = ResearchQuestion('
    idx = reg_src.find(anchor)
    if idx < 0:
        # fallback: find the last question definition and insert after it
        idx = reg_src.rfind(" = ResearchQuestion(")
        if idx < 0:
            raise RuntimeError("cannot find insertion point in registry")
        # find the end of that definition
        paren_depth = 0
        pos = idx
        while pos < len(reg_src):
            if reg_src[pos] == "(":
                paren_depth += 1
            elif reg_src[pos] == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    break
            pos += 1
        idx = pos  # at the closing paren
    
    # find the end of this definition block
    paren_depth = 0
    pos = idx
    while pos < len(reg_src):
        if reg_src[pos] == "(":
            paren_depth += 1
        elif reg_src[pos] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                break
        pos += 1
    # pos is at the closing paren of the last question definition
    # find the next newline after it
    next_nl = reg_src.find("\n", pos)
    if next_nl < 0:
        next_nl = len(reg_src)
    
    mgmt_block = '''

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY — TRADE MANAGEMENT (MGMT)
# ═══════════════════════════════════════════════════════════════════════════════

MGMT1 = ResearchQuestion(
    id="MGMT-1",
    category=QuestionCategory.TRADE_MANAGEMENT,
    title="Does trade management appear to help or harm outcomes?",
    description="Observational association between management actions and realised outcomes. NOT causal.",
    required_fields=("action_type", "trade_id", "r_multiple_realised"),
    data_sources=(DataSource.MANAGEMENT_ACTIONS, DataSource.TRADE_TRUTH),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required for managed/unmanaged comparison"),
        ValidationRule("sample_size", ">=", 30, "Minimum for managed-vs-unmanaged comparison"),
    ),
    runner_module="research_engine.experiments.management_research",
    runner_function="run_mgmt1",
    report_filename="mgmt1_management_effectiveness.json",
)

MGMT2 = ResearchQuestion(
    id="MGMT-2",
    category=QuestionCategory.TRADE_MANAGEMENT,
    title="Which management action types appear helpful, harmful, or neutral?",
    description="Per-action-type outcome analysis (SLTP_MODIFY, PARTIAL_CLOSE, CLOSE). OBSERVATIONAL.",
    required_fields=("action_type", "action_reason", "trade_id", "r_multiple_realised"),
    data_sources=(DataSource.MANAGEMENT_ACTIONS, DataSource.TRADE_TRUTH),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required for action-type analysis"),
        ValidationRule("sample_size", ">=", 15, "Minimum per action type"),
    ),
    runner_module="research_engine.experiments.management_research",
    runner_function="run_mgmt2",
    report_filename="mgmt2_action_type_analysis.json",
)
'''
    reg_src = reg_src[:next_nl] + mgmt_block + reg_src[next_nl:]
    registry_p.write_text(reg_src, encoding="utf-8")
    print("added MGMT-1 and MGMT-2 to canonical registry")
else:
    print("MGMT-1/MGMT-2 already in registry")

# ─── 3. Verify ────────────────────────────────────────────────────────────────
py_compile.compile(str(models_p), doraise=True)
py_compile.compile(str(registry_p), doraise=True)
print("py_compile OK for both files")
