"""READ-ONLY live run of the Step-4 evidence consumers against real S3.

Run: $env:RESEARCH_AWS_PROFILE="trading-bot-new"; python tools\run_evidence_live.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.data_access.s3_source import S3ResearchDataSource  # noqa: E402
from research_engine.evidence.registry import run_dataset_evidence  # noqa: E402
from research_engine.dataset_disposition import coverage_report  # noqa: E402

src = S3ResearchDataSource()
assert src.bucket == "trading-bot-v10-data"
print(f"bucket={src.bucket} profile={src.research_profile}")

# ── consumers receive real records (date-bounded read for live proof) ─────
import research_engine.data_access.s3_source as _s3mod  # noqa: E402

_orig_read = _s3mod.S3ResearchDataSource.read_dataset

def _bounded_read(self, dataset, **kw):
    kw.setdefault("start_date", "2026-09-03")
    kw.setdefault("end_date", "2026-09-04")
    return _orig_read(self, dataset, **kw)

_s3mod.S3ResearchDataSource.read_dataset = _bounded_read

reports = run_dataset_evidence()
for ds, rep in reports.items():
    lin = rep["lineage_coverage"]
    best = lin["best_join_key"]
    best_share = lin["key_coverage"].get(best, {}).get("share")
    print(f"\n=== {ds}: record_count={rep['record_count']} "
          f"status={rep['disposition_status']} temporal={rep['temporal_availability']} "
          f"best_join_key={best} (share={best_share})")
    print("  analysis keys:", list(rep["analysis"].keys()))
    a = rep["analysis"]
    if ds == "horizon_candidates":
        print("  selection_status_distribution:", a["selection_status_distribution"])
        print("  selected_vs_rejected:", json.dumps(a["selected_vs_rejected"])[:300])
    elif ds == "strategy_candidates":
        print("  selected_vs_rejected:", json.dumps(a["selected_vs_rejected"])[:300])
        print("  confidence_gap:", json.dumps(a["winner_vs_best_alternative_confidence_gap"]))
    elif ds == "execution_attempts":
        print("  action_types:", a["action_type_distribution"])
        print("  retry_count:", a["retry_count"], "rejected:", a["broker_rejected_count"],
              "rate:", a["rejection_rate"])
        print("  retcodes:", a["retcode_distribution"])
    elif ds == "management_actions":
        print("  action_types:", a["action_type_distribution"])
        print("  action_reasons:", a["action_reason_distribution"])
        print("  actions_per_trade:", json.dumps(a["actions_per_trade"]))

# ── exclusions: prove events/position_excursion have no consumer by design ─
cov = coverage_report()
print("\n=== disposition coverage: covered =", cov["covered"],
      "uncovered =", cov["uncovered"])
for ds in ("events", "position_excursion"):
    d = cov["registry_external_documented"].get(ds) or cov["dispositions"].get(ds)
    print(f"  {ds}: status={d['status']} consumers={d['consumers']}")
    print(f"    reason: {d['reason'][:180]}...")

# ── events exclusion validation: confirm real records are pure telemetry ──
ev = src.read_dataset("events", symbol="AUDUSD")
types = {}
for r in ev:
    types[r.get("type", "?")] = types.get(r.get("type", "?"), 0) + 1
print(f"\nevents[AUDUSD] n={len(ev)} type_distribution={types}")
domain_keys = {"decision_id", "trade_id", "canonical_opportunity_id",
               "r_multiple", "pnl", "exit_reason"}
leaks = [k for r in ev for k in domain_keys if k in r]
print("domain-fact keys found in events:", set(leaks) or "NONE — exclusion rationale holds")
