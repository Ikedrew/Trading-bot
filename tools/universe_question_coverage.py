"""Universe & question coverage audit — read-only evidence extraction.

Dumps structured data about:
  - all 61 canonical questions (with all metadata)
  - all universe builders and their data sources
  - all experiment runners and their actual evidence usage
  - all evidence consumers

Output: JSON file for the coverage analysis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = {}


def main() -> None:
    # ─── 1. canonical registry dump ────────────────────────────────────────
    from research_engine.registry.research_question_registry import REGISTRY
    questions = []
    for q in REGISTRY:
        entry = {
            "id": q.id,
            "title": q.title,
            "category": q.category.value if hasattr(q.category, "value") else str(q.category),
            "description": getattr(q, "description", ""),
            "runner_module": getattr(q, "runner_module", "") or None,
            "runner_function": getattr(q, "runner_function", "") or None,
            "has_runner": bool(getattr(q, "runner_module", "")),
            "legacy_ids": list(getattr(q, "legacy_ids", ()) or ()),
            "depends_on": list(getattr(q, "depends_on", ()) or ()),
            "required_fields": list(getattr(q, "required_fields", ()) or ()),
            "validation_rules": [
                {"field": vr.field, "op": vr.operator, "threshold": vr.threshold,
                 "description": vr.description}
                for vr in (getattr(q, "validation_rules", ()) or ())
            ],
            "priority": getattr(q, "priority", ""),
            "report_filename": getattr(q, "report_filename", "") or None,
        }
        # dump source datasets from the registry
        if hasattr(q, "required_datasets") and q.required_datasets:
            entry["required_datasets"] = list(q.required_datasets)
        if hasattr(q, "data_sources") and q.data_sources:
            entry["data_sources"] = list(q.data_sources)
        questions.append(entry)
    OUT["canonical_questions"] = questions

    # ─── 2. experiment runner source inspection ───────────────────────────
    runners = {}
    exp_dir = ROOT / "research_engine" / "experiments"
    for f in sorted(exp_dir.glob("*.py")):
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        mod_name = f"research_engine.experiments.{f.stem}"
        # extract all load_* and read_dataset calls
        loaders = set()
        for line in src.splitlines():
            for fn in ("load_decision_trace", "load_trade_truth", "load_trade_journal",
                       "load_shadow_trades", "load_decision_ledger", "load_opportunities",
                       "load_assessments", "load_portfolio_rankings", "load_shadow_comparisons",
                       "load_execution_results", "load_execution_context", "load_protection_audit",
                       "load_risk_deviation", "load_horizon_candidates", "load_strategy_candidates",
                       "load_execution_attempts", "load_management_actions",
                       "load_market_context", "ingest_completed_shadow_trades",
                       "read_dataset", "load_edge_evidence"):
                if fn in line:
                    loaders.add(fn)
        # extract analysis dimensions actually used
        dims = set()
        for dim in ("pattern", "regime", "session", "symbol", "strategy", "horizon",
                    "h4_regime", "h1_bias", "market_phase", "score_bin", "direction",
                    "terminal_stage", "ev_positive", "spread", "slippage", "risk",
                    "protection", "management", "portfolio", "confidence", "calibration",
                    "drawdown", "position_sizing", "time", "temporal", "degradation",
                    "shadow", "edge", "filtering", "threshold", "distribution",
                    "variance", "recovery", "opportunity"):
            if dim in src.lower():
                dims.add(dim)
        runners[mod_name] = {
            "file": str(f.relative_to(ROOT)).replace("\\", "/"),
            "loaders_used": sorted(loaders),
            "analysis_dimensions": sorted(dims),
            "line_count": len(src.splitlines()),
        }
    OUT["runners"] = runners

    # ─── 3. universe builders ─────────────────────────────────────────────
    uni_dir = ROOT / "research_engine" / "v10" / "universes"
    universes = {}
    for f in sorted(uni_dir.glob("*.py")):
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        # find builder classes
        for line in src.splitlines():
            if line.strip().startswith("class ") and "Builder" in line:
                cls = line.strip().split("(")[0].replace("class ", "").rstrip(":")
                # extract loaders used
                loaders = set()
                for fn in ("load_decision_trace", "load_trade_truth", "load_trade_journal",
                           "load_shadow_trades", "load_decision_ledger", "load_opportunities",
                           "load_assessments", "load_portfolio_rankings", "load_shadow_comparisons",
                           "load_execution_results", "load_execution_context", "load_protection_audit",
                           "load_risk_deviation", "load_horizon_candidates", "load_strategy_candidates",
                           "load_execution_attempts", "load_management_actions",
                           "ingest_completed_shadow_trades"):
                    if fn in src:
                        loaders.add(fn)
                universes[cls] = {
                    "file": str(f.relative_to(ROOT)).replace("\\", "/"),
                    "loaders_used": sorted(loaders),
                }
    OUT["universe_builders"] = universes

    # ─── 4. evidence consumers ────────────────────────────────────────────
    ev_dir = ROOT / "research_engine" / "evidence"
    evidence = {}
    for f in sorted(ev_dir.glob("*.py")):
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        loaders = set()
        for fn in ("load_horizon_candidates", "load_strategy_candidates",
                   "load_execution_attempts", "load_management_actions"):
            if fn in src:
                loaders.add(fn)
        evidence[f.stem] = {"loaders_used": sorted(loaders)}
    OUT["evidence_consumers"] = evidence

    # ─── 5. shadow populations ────────────────────────────────────────────
    OUT["shadow_ingestion"] = {
        "canonical_source": "shadow_runtime_v1",
        "ingestion_module": "research_engine/data_access/shadow_runtime_ingestion.py",
        "normalized_shape": ["identity", "decision_snapshot", "simulated_outcome"],
        "primary_horizon_tag": "PRIMARY_HORIZON_SIMULATION",
        "horizon_alternative_tag": "HORIZON_ALTERNATIVE",
    }

    out_path = ROOT / "tools" / "universe_question_coverage_data.json"
    out_path.write_text(json.dumps(OUT, indent=2, default=str), encoding="utf-8")
    print(f"dumped {len(json.dumps(OUT))} bytes to {out_path}")


if __name__ == "__main__":
    main()
