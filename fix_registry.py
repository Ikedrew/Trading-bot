#!/usr/bin/env python3
"""Fix the question_registry.py to normalize statuses."""
import re

with open('c:\\Users\\ikues\\Trading bot build\\research_engine\\question_registry.py', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Helper: find and replace a specific ResearchQuestion block
def replace_q_block(content, qid, replacements):
    """Replace fields in a ResearchQuestion block.
    replacements is a dict of field_name -> new_value
    """
    # Find the block for this QID
    pattern = rf'(ResearchQuestion\(\s*id="{re.escape(qid)}".*?\)'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print(f"ERROR: Could not find ResearchQuestion for {qid}")
        return content
    
    block = match.group(0)
    print(f"\n=== Found block for {qid} ===")
    print(block[:200])
    
    new_block = block
    for field, new_val in replacements.items():
        # Find the field in the block
        if isinstance(new_val, str):
            new_val_repr = repr(new_val)
        else:
            new_val_repr = str(new_val)
        
        # Try to find field = ... pattern
        field_pattern = rf'(\s*){field}=\s*[^,\n]+'
        m = re.search(field_pattern, new_block)
        if m:
            old_field = m.group(0)
            indent = m.group(1)
            new_field = f'{indent}{field}={new_val_repr}'
            new_block = new_block.replace(old_field, new_field, 1)
            print(f"  Replaced {field}: {old_field[:30]}... -> {new_field[:30]}...")
        else:
            print(f"  WARNING: Could not find field {field} in {qid} block")
    
    content = content.replace(block, new_block, 1)
    return content

# Q4: not_implemented -> PARTIAL
content = replace_q_block(content, "Q4", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.score_calibration"',
    "notes": '"Report exists (q4_confidence_calibration.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q5: not_implemented -> PARTIAL
content = replace_q_block(content, "Q5", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.research_runner"',
    "notes": '"Report exists (q5_pattern_degradation.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q6: not_implemented -> PARTIAL
content = replace_q_block(content, "Q6", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.research_runner"',
    "notes": '"Report exists (q6_regime_accuracy.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q7: not_implemented -> PARTIAL
content = replace_q_block(content, "Q7", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.research_runner"',
    "notes": '"Report exists (q7_session_edge.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q8: not_implemented -> PARTIAL
content = replace_q_block(content, "Q8", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.research_runner"',
    "notes": '"Report exists (q8_htf_alignment_value.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q9: not_implemented -> PARTIAL
content = replace_q_block(content, "Q9", {
    "status": "Status.PARTIAL",
    "runner": '"experiments.research_runner"',
    "notes": '"Report exists (q9_spread_fill_quality.json, 2026-07-21). Uses ALL-epoch data; needs CURRENT-epoch re-validation."',
})

# Q19: ready -> COMPLETE (already has runner, but update notes)
content = replace_q_block(content, "Q19", {
    "status": "Status.COMPLETE",
    "notes": '"Re-run 2026-09-06 on CURRENT epoch after data collection fix: EV=-0.047R, n=1253, epoch=CURRENT. Valid CURRENT-epoch evidence."',
})

# Q20: ready -> COMPLETE
content = replace_q_block(content, "Q20", {
    "status": "Status.COMPLETE",
    "notes": '"Report exists (q20_score_calibration.json) + calibration curve artifact. Score->probability mapping exists but not yet wired to runtime."',
})

# R3/R4/R5 are not in the question_registry.py (they're in v10_research_registry or referenced via legacy_ids)
# R3 is referenced by V10_R1 with legacy_ids=("R3",)
# R4 is referenced by V10_R2 with legacy_ids=("R4",) 
# R5 is referenced by V10_R3 with legacy_ids=("R5",)
# These are V10 questions that carry the legacy R3/R4/R5 IDs

# The question_registry.py uses Q1-Q25 IDs. R3/R4/R5 are legacy IDs from the old engine.
# The status_model handles the INVALIDATED status for those.

# Write the updated file
with open('c:\\Users\\ikues\\Trading bot build\\research_engine\\question_registry.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n\n=== DONE ===")
print("Updated question_registry.py")

# Verify
with open('c:\\Users\\ikues\\Trading bot build\\research_engine\\question_registry.py', 'r', encoding='utf-8', errors='ignore') as f:
    new_content = f.read()

for qid in ["Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q19", "Q20"]:
    pattern = rf'(ResearchQuestion\(\s*id="{re.escape(qid)}".*?\)'
    match = re.search(pattern, new_content, re.DOTALL)
    if match:
        block = match.group(0)
        # Find status line
        status_match = re.search(r'status=([^,\n]+)', block)
        if status_match:
            print(f"{qid}: status={status_match.group(1)}")
