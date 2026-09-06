"""READ-ONLY forensic probe: Q16/X4 shadow-vs-live lineage field population.

Verifies, against the real S3 populations:
  - trade_truth_v1: identity.correlation_id / identity.canonical_opportunity_id population
  - normalized shadow_runtime_v1 research records: identity lineage population
  - actual key overlap between the two populations (correlation_id vs canonical_opportunity_id)

Writes NOTHING. Never modifies any dataset.
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
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )
    from research_engine.data_access.s3_source import get_default_source

    source = get_default_source()

    shadows = ingest_completed_shadow_trades()
    truths = source.read_dataset("trade_truth")

    print("=" * 72)
    print(f"shadow normalized records: {len(shadows)}")
    print(f"trade_truth records:       {len(truths)}")
    print("=" * 72)

    # ── trade_truth field population ──────────────────────────────────────
    tt_corr = [t.get("identity", {}).get("correlation_id", "") for t in truths]
    tt_canon = [t.get("identity", {}).get("canonical_opportunity_id", "") for t in truths]
    tt_r = [t.get("outcome", {}).get("r_multiple_realised") for t in truths]
    print("\n[trade_truth]")
    print("  identity.correlation_id populated:", sum(1 for c in tt_corr if c), "/", len(truths))
    print("  identity.canonical_opportunity_id populated:", sum(1 for c in tt_canon if c), "/", len(truths))
    print("  outcome.r_multiple_realised populated:", sum(1 for r in tt_r if r is not None), "/", len(truths))
    print("  trade_id populated:", sum(1 for t in truths if t.get("identity", {}).get("trade_id")))
    print("  symbols:", Counter(t.get("identity", {}).get("symbol", "") for t in truths))

    # ── normalized shadow field population ────────────────────────────────
    sh_canon = [s.get("identity", {}).get("canonical_opportunity_id", "") for s in shadows]
    sh_corr = [s.get("identity", {}).get("correlation_id", "") for s in shadows]
    sh_flat_corr = [s.get("correlation_id", "") for s in shadows]
    sh_types = Counter(s.get("identity", {}).get("shadow_type", "") for s in shadows)
    sh_horizons = Counter(s.get("identity", {}).get("evaluated_horizon", "") for s in shadows)
    sh_r = [s.get("simulated_outcome", {}).get("pnl_r_multiple") for s in shadows]
    print("\n[shadow normalized]")
    print("  identity.canonical_opportunity_id populated:", sum(1 for c in sh_canon if c), "/", len(shadows))
    print("  identity.correlation_id populated:", sum(1 for c in sh_corr if c))
    print("  flat correlation_id populated:", sum(1 for c in sh_flat_corr if c))
    print("  simulated_outcome.pnl_r_multiple populated:", sum(1 for r in sh_r if r is not None), "/", len(shadows))
    print("  shadow_type:", dict(sh_types))
    print("  evaluated_horizon:", dict(sh_horizons))

    # ── overlap ───────────────────────────────────────────────────────────
    print("\n[overlap]")
    canon_truth = {c for c in tt_canon if c}
    canon_shadow = {c for c in sh_canon if c}
    inter = canon_truth & canon_shadow
    print("  canonical_opportunity_id: live=", len(canon_truth), " shadow=", len(canon_shadow),
          " intersection=", len(inter))
    corr_truth = {c for c in tt_corr if c}
    corr_shadow = {c for c in sh_corr if c} | {c for c in sh_flat_corr if c}
    print("  correlation_id: live=", len(corr_truth), " shadow=", len(corr_shadow),
          " intersection=", len(corr_truth & corr_shadow))

    # canonical examples
    if canon_truth:
        print("\n  live canonical examples:", sorted(canon_truth)[:3])
    # horizon multiplicity on intersecting canonicals
    if inter:
        by_canon = Counter(
            s.get("identity", {}).get("canonical_opportunity_id", "")
            for s in shadows
            if s.get("identity", {}).get("canonical_opportunity_id", "") in inter
        )
        print("  shadows per intersecting canonical:", dict(by_canon))
        tt_by_canon = Counter(
            t.get("identity", {}).get("canonical_opportunity_id", "")
            for t in truths
            if t.get("identity", {}).get("canonical_opportunity_id", "") in inter
        )
        print("  live trades per intersecting canonical:", dict(tt_by_canon))

        primaries = {
            s.get("identity", {}).get("canonical_opportunity_id", ""): s
            for s in shadows
            if s.get("identity", {}).get("shadow_type", "") == "PRIMARY_HORIZON_SIMULATION"
            and s.get("identity", {}).get("canonical_opportunity_id", "") in inter
        }
        print("  primary-horizon shadows on intersecting canonicals:", len(primaries))

    # sample matched pairs
    shown = 0
    for t in truths:
        c = t.get("identity", {}).get("canonical_opportunity_id", "")
        if c and c in canon_shadow and shown < 2:
            shadow_hits = [
                s for s in shadows
                if s.get("identity", {}).get("canonical_opportunity_id", "") == c
            ]
            print("\n[matched pair example]")
            print("  canonical:", c)
            print("  live: trade_id=", t.get("identity", {}).get("trade_id"),
                  " correlation_id=", t.get("identity", {}).get("correlation_id"),
                  " r=", t.get("outcome", {}).get("r_multiple_realised"),
                  " exit=", t.get("exit", {}).get("exit_reason"))
            for s in shadow_hits:
                print("  shadow: trade_id=", s.get("identity", {}).get("shadow_trade_id"),
                      " horizon=", s.get("identity", {}).get("evaluated_horizon"),
                      " type=", s.get("identity", {}).get("shadow_type"),
                      " r=", s.get("simulated_outcome", {}).get("pnl_r_multiple"),
                      " exit=", s.get("simulated_outcome", {}).get("exit_reason"))
            shown += 1

    print("\nPROBE COMPLETE (read-only)")


if __name__ == "__main__":
    main()
