"""READ-ONLY probe: real candidate evidence for the prospective-pairing path."""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.lifecycle.candidate_pairing import load_pairing_populations

reg = CandidateRegistry()
all_c = reg.list_all() if hasattr(reg, "list_all") else []
from collections import Counter
print("live candidates:", len(all_c), dict(Counter(c.status for c in all_c)))
st = [c for c in all_c if c.status == "SHADOW_TESTING"]
print("SHADOW_TESTING candidates:", len(st))

cand_records, inc_records = load_pairing_populations()
def _st(r):
    return (r.get("identity") or {}).get("shadow_type") or ""
cand_types = Counter(_st(r) for r in cand_records)
print(f"shadow_trades dataset: n={len(cand_records)} shadow_type_distribution={dict(cand_types)}")
cand_shadows = [r for r in cand_records if _st(r).startswith("CANDIDATE_")]
print(f"candidate shadows on S3: {len(cand_shadows)}")
truth_with_cor = sum(1 for r in inc_records if (r.get('identity') or {}).get('correlation_id'))
print(f"trade_truth: n={len(inc_records)} with_correlation_id={truth_with_cor}")
