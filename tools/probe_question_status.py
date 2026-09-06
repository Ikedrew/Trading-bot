"""Probe suspicious question statuses (E3/S1 COMPLETE with n=0)."""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.runner_discovery import get_all_runners
runners = get_all_runners()
for qid in ("E3", "S1", "X4", "D6", "E5"):
    fn = runners.get(qid)
    if not fn:
        print(f"{qid}: NO RUNNER")
        continue
    rep = fn()
    ds = rep.get("dataset", {})
    rec = rep.get("recommendation", {})
    print(f"{qid}: status={rep.get('status')} sample={ds.get('sample_size')} "
          f"r_used={ds.get('r_multiples_used')} rec={rec if not isinstance(rec, dict) else rec.get('status')} "
          f"finding={json.dumps(rep.get('overall', {}).get('finding', ''))[:120]}")
