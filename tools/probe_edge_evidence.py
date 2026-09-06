"""READ-ONLY probe: decision_trace ↔ canonical shadow counterfactual join coverage.

Determines whether the edge-candidate surface can be answered from canonical V1
evidence (decision_trace conditions + shadow_runtime_v1 counterfactual outcomes
+ trade_truth realised outcomes) instead of local replay_data candles.
Writes NOTHING.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")


def main() -> None:
    from research_engine.data_access.loaders import load_decision_trace
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )
    from research_engine.data_access.s3_source import get_default_source

    traces = load_decision_trace()
    shadows = ingest_completed_shadow_trades()
    truths = get_default_source().read_dataset("trade_truth")

    print(f"decision_trace records: {len(traces)}")
    print(f"shadow outcomes:        {len(shadows)}")
    print(f"trade_truth records:    {len(truths)}")

    if traces:
        t0 = traces[0]
        print("\n[decision_trace sample keys]")
        print(sorted(t0.keys()))
        print("\n[decision_trace field population]")
        for key in ("entity_id", "canonical_opportunity_id", "correlation_id",
                    "pattern_detected", "pattern_name", "components",
                    "regime", "market_state", "volatility_state",
                    "timestamp_utc", "symbol", "score_neutral",
                    "selected_strategy"):
            n = sum(1 for t in traces if t.get(key) not in (None, "", {}, []))
            print(f"  {key}: {n}/{len(traces)}")

    def canon(rec: dict) -> str:
        return (
            rec.get("canonical_opportunity_id", "")
            or rec.get("identity", {}).get("canonical_opportunity_id", "")
        )

    trace_canon = {canon(t) for t in traces if canon(t)}
    shadow_canon = {s.get("identity", {}).get("canonical_opportunity_id", "") for s in shadows}
    shadow_canon.discard("")
    truth_canon = {canon(t) for t in truths if canon(t)}

    print("\n[join coverage]")
    print("  traces with canonical key:", sum(1 for t in traces if canon(t)), "/", len(traces))
    print("  trace AND shadow canonicals:", len(trace_canon & shadow_canon))
    print("  trace AND truth canonicals:", len(trace_canon & truth_canon))

    # actions distribution on shadow live_facts
    acts = Counter(s.get("identity", {}).get("v10_action", "") for s in shadows)
    print("\n[shadow live_facts.v10_action]", dict(acts))

    # how many decision traces are NO_TRADE/blocked vs EXECUTE
    if traces:
        dec = Counter(t.get("decision", t.get("final_decision", "?")) for t in traces)
        print("[decision_trace decision field]", dict(dec))

    print("\nPROBE COMPLETE (read-only)")


if __name__ == "__main__":
    main()
