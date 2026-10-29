"""
V10 Research Registry Migration Report.

Run this module to produce the full migration summary.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_engine.registry.v10_research_registry import (
    V10_REGISTRY, V10Status, MigrationSource,
)


# ═══════════════════════════════════════════════════════════════
# MIGRATION TABLE (Old ID → V10 ID → Classification)
# ═══════════════════════════════════════════════════════════════

MIGRATION_TABLE = [
    # System Edge
    {"old_id": "E1", "v10_id": "V10-E1", "type": "CARRY_OVER", "reason": "Expectancy question unchanged for V10"},
    {"old_id": "E2", "v10_id": "V10-E2", "type": "CARRY_OVER", "reason": "Pattern expectancy still relevant"},
    {"old_id": "E3", "v10_id": "V10-E3", "type": "MODIFY", "reason": "Strategy families changed from 3 to 6 in V10"},
    {"old_id": "E4", "v10_id": "V10-E3", "type": "MODIFY", "reason": "Merged into V10-E3 (strategy × pattern now within family)"},
    {"old_id": "E5", "v10_id": "V10-E4", "type": "CARRY_OVER", "reason": "Walk-forward still required"},

    # Market Context
    {"old_id": "M1", "v10_id": "V10-M1", "type": "CARRY_OVER", "reason": "Regime prediction unchanged"},
    {"old_id": "M2", "v10_id": "V10-SC2", "type": "MODIFY", "reason": "Moved to strategy classification (regime×strategy)"},
    {"old_id": "M3", "v10_id": None, "type": "ARCHIVE", "reason": "Market phase classification changed in V10 — replaced by V10-M3"},
    {"old_id": "M4", "v10_id": None, "type": "ARCHIVE", "reason": "3-way interaction too complex for current sample size"},
    {"old_id": "M5", "v10_id": None, "type": "ARCHIVE", "reason": "Phase transition prediction requires temporal dataset not yet available"},
    {"old_id": "M6", "v10_id": None, "type": "ARCHIVE", "reason": "V10 uses different phase taxonomy — old phases not applicable"},
    {"old_id": "M7", "v10_id": None, "type": "ARCHIVE", "reason": "Merged into V10-M3 (regime+volatility replaces regime+phase)"},
    {"old_id": "M8", "v10_id": None, "type": "ARCHIVE", "reason": "Temporal phase data not available"},
    {"old_id": "M9", "v10_id": None, "type": "ARCHIVE", "reason": "Phase-appropriate patterns replaced by V10 opportunity quality model"},
    {"old_id": "M10", "v10_id": None, "type": "ARCHIVE", "reason": "Strategy per phase replaced by V10 strategy engine conditions"},
    {"old_id": "M11", "v10_id": None, "type": "ARCHIVE", "reason": "Context vs pattern: V10 already weights location(35%)+structure(30%)+behaviour(15%)+formation(20%)"},

    # Decision Quality
    {"old_id": "D1", "v10_id": "V10-D1", "type": "CARRY_OVER", "reason": "Component prediction still critical"},
    {"old_id": "D2", "v10_id": "V10-D2", "type": "CARRY_OVER", "reason": "Calibration check still needed"},
    {"old_id": "D3", "v10_id": "V10-D2", "type": "MODIFY", "reason": "Merged EV filtering into calibration question"},
    {"old_id": "D4", "v10_id": None, "type": "ARCHIVE", "reason": "Context-dependent thresholds: V10 uses 4-dimension quality instead of single threshold"},
    {"old_id": "D5", "v10_id": None, "type": "ARCHIVE", "reason": "Missed opportunity: requires shadow dataset not yet available"},
    {"old_id": "D6", "v10_id": "V10-OQ2", "type": "MODIFY", "reason": "Renamed to opportunity ranking accuracy (V10 has ranking system)"},

    # Strategy & Horizon
    {"old_id": "S1", "v10_id": "V10-SC1", "type": "MODIFY", "reason": "V10 has 6 strategy families vs old 3"},
    {"old_id": "S2", "v10_id": "V10-EX3", "type": "MODIFY", "reason": "Horizon impact on exit is the actionable question"},
    {"old_id": "S3", "v10_id": "V10-SC2", "type": "MODIFY", "reason": "Merged into strategy×regime interaction"},
    {"old_id": "S4", "v10_id": None, "type": "ARCHIVE", "reason": "Phase-specialised strategies: V10 strategy engine already has phase-aware conditions"},
    {"old_id": "S5", "v10_id": "V10-SC1", "type": "MODIFY", "reason": "Merged into V10-SC1"},
    {"old_id": "S6", "v10_id": "V10-EX3", "type": "MODIFY", "reason": "Horizon expectancy → exit policy question"},
    {"old_id": "S7", "v10_id": "V10-SC2", "type": "MODIFY", "reason": "Merged into strategy×regime"},

    # Execution
    {"old_id": "X1", "v10_id": "V10-X1", "type": "CARRY_OVER", "reason": "Slippage model still needed"},
    {"old_id": "X2", "v10_id": "V10-X1", "type": "MODIFY", "reason": "Broker failures merged into execution quality"},
    {"old_id": "X3", "v10_id": "V10-X1", "type": "MODIFY", "reason": "Session quality merged"},
    {"old_id": "X4", "v10_id": None, "type": "ARCHIVE", "reason": "Edge lost in execution: requires shadow+live comparison not yet available"},
    {"old_id": "X5", "v10_id": None, "type": "ARCHIVE", "reason": "Same as X4"},
    {"old_id": "X6", "v10_id": "V10-X1", "type": "MODIFY", "reason": "Merged into single execution quality question"},

    # Risk Management
    {"old_id": "R1", "v10_id": "V10-R1", "type": "CARRY_OVER", "reason": "Guard effectiveness still needed"},
    {"old_id": "R2", "v10_id": "V10-R1", "type": "MODIFY", "reason": "Merged into single risk model question"},
    {"old_id": "R3", "v10_id": "V10-R2", "type": "CARRY_OVER", "reason": "Ruin probability critical"},
    {"old_id": "R4", "v10_id": "V10-R2", "type": "MODIFY", "reason": "Drawdown threshold merged into ruin analysis"},
    {"old_id": "R5", "v10_id": "V10-R3", "type": "MODIFY", "reason": "Position sizing now quality-aware in V10"},

    # System Learning
    {"old_id": "L1", "v10_id": "V10-L1", "type": "CARRY_OVER", "reason": "Degradation detection still needed"},
    {"old_id": "L2", "v10_id": "V10-L2", "type": "CARRY_OVER", "reason": "Improvement tracking needed for V10 migration validation"},
    {"old_id": "L3", "v10_id": "V10-D1", "type": "MODIFY", "reason": "Architecture assumptions merged into scoring component analysis"},
    {"old_id": "L4", "v10_id": "V10-L1", "type": "MODIFY", "reason": "Market drift merged into pattern degradation"},
    {"old_id": "L5", "v10_id": "V10-L1", "type": "MODIFY", "reason": "Model drift merged into degradation"},
    {"old_id": "L6", "v10_id": None, "type": "ARCHIVE", "reason": "Research confidence: handled by data governance layer (validated_trade_dataset)"},
    {"old_id": "L7", "v10_id": None, "type": "ARCHIVE", "reason": "Shadow A/B: not yet applicable (no candidate version to test)"},

    # Data Governance
    {"old_id": "G1", "v10_id": None, "type": "ARCHIVE", "reason": "Replaced by validated_trade_dataset pipeline + research_ready_dataset"},
    {"old_id": "G2", "v10_id": None, "type": "ARCHIVE", "reason": "Lineage coverage now computed automatically by validation pipeline"},
    {"old_id": "G3", "v10_id": None, "type": "ARCHIVE", "reason": "Research validity now computed by data_quality_score in validated dataset"},

    # Promotion Intelligence
    {"old_id": "P1", "v10_id": None, "type": "ARCHIVE", "reason": "Promotion impact: depends on E1+E5+R3 which are blocked. Defer."},

    # Exit Management
    {"old_id": "EX1", "v10_id": "V10-EX1", "type": "MODIFY", "reason": "Simplified to exit distribution analysis (actionable)"},
    {"old_id": "EX2", "v10_id": "V10-EX2", "type": "CARRY_OVER", "reason": "Trailing stop still needs validation"},
    {"old_id": "EX3", "v10_id": None, "type": "ARCHIVE", "reason": "Optimal TP: requires MFE data not available"},
    {"old_id": "EX4", "v10_id": None, "type": "ARCHIVE", "reason": "Optimal SL: requires MAE data not available"},
    {"old_id": "EX5", "v10_id": "V10-EX3", "type": "MODIFY", "reason": "Horizon exit policy — kept as V10 has horizon engine"},
    {"old_id": "EX6", "v10_id": None, "type": "ARCHIVE", "reason": "Strategy exit policy: blocked by strategy field coverage"},
    {"old_id": "EX7", "v10_id": None, "type": "ARCHIVE", "reason": "Regime exit policy: deferred until regime analysis (V10-M1) complete"},
    {"old_id": "EX8", "v10_id": None, "type": "ARCHIVE", "reason": "Pattern exit: requires MFE/MAE not available"},
    {"old_id": "EX9", "v10_id": None, "type": "ARCHIVE", "reason": "Timeout reduction: requires bar-by-bar data not available"},
    {"old_id": "EX10", "v10_id": None, "type": "ARCHIVE", "reason": "Walk-forward exit: requires 200+ trades"},
]


def generate_report() -> dict:
    """Generate the complete migration report."""
    carry_over = [m for m in MIGRATION_TABLE if m["type"] == "CARRY_OVER"]
    modify = [m for m in MIGRATION_TABLE if m["type"] == "MODIFY"]
    archive = [m for m in MIGRATION_TABLE if m["type"] == "ARCHIVE"]

    # Count NEW_V10 (questions in V10 registry with no old_ids)
    new_v10 = [q for q in V10_REGISTRY if q.source == MigrationSource.NEW_V10]

    # Status breakdown
    ready = [q for q in V10_REGISTRY if q.status == V10Status.READY]
    partial = [q for q in V10_REGISTRY if q.status == V10Status.PARTIAL]
    blocked = [q for q in V10_REGISTRY if q.status == V10Status.BLOCKED]

    return {
        "old_registry_location": "research_engine/registry/research_registry_v1_old_engine.py",
        "new_registry_location": "research_engine/registry/v10_research_registry.py",
        "old_registry_questions": 55,
        "v10_registry_questions": len(V10_REGISTRY),
        "migration_summary": {
            "CARRY_OVER": len(carry_over),
            "MODIFIED": len(modify),
            "ARCHIVED": len(archive),
            "NEW_V10": len(new_v10),
        },
        "v10_status_summary": {
            "READY": len(ready),
            "PARTIAL": len(partial),
            "BLOCKED": len(blocked),
        },
        "ready_questions": [q.research_id + ": " + q.title for q in ready],
        "migration_table": MIGRATION_TABLE,
    }


if __name__ == "__main__":
    report = generate_report()
    print(json.dumps(report, indent=2))
