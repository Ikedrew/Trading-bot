"""Governed L3 architecture-assumption evidence audit."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from research_engine.control_plane.evidence_provenance import CURRENT, evidence_digest
from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
from research_engine.control_plane.stage4_impl_ownership_labels import L3_OWNED_REPORT

REPORT_SCHEMA_VERSION = "l3_architecture_assumption_validity_v2"
REPORT_FILENAME = L3_OWNED_REPORT


def run_l3(*, records=None, identities=None, persist: bool = False) -> dict[str, Any]:
    """Audit the exact L3 population; never consume D1's component report."""
    rows = enforce_exact_population("L3", list(records or ()), identities)
    required = ("components", "strategy", "regime", "r_multiple")
    coverage = {
        field: sum(row.get(field) not in (None, "", [], {}) for row in rows) / len(rows)
        for field in required
    }
    complete = all(value == 1.0 for value in coverage.values())
    state = "INSUFFICIENT_DATA" if not complete else "NO_EFFECT"
    material = {
        "question_id": "L3", "report": REPORT_FILENAME,
        "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT,
        "analytical_population": len(rows), "field_coverage": coverage,
    }
    return {
        "question_id": "L3",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "COMPLETE" if state == "NO_EFFECT" else state,
        "scientific_state": state,
        "epoch": CURRENT,
        "overall": {
            "finding": (
                "ARCHITECTURE_ASSUMPTION_OBSERVABLES_INCOMPLETE" if not complete
                else "NO_SUPPORTED_ARCHITECTURE_ASSUMPTION_EFFECT"
            ),
            "field_coverage": coverage,
        },
        "dataset": {"source": "frozen governed L3 population", "sample_size": len(rows)},
        "fingerprint": {"epoch": CURRENT, "records_used": len(rows),
                        "analytical_digest": evidence_digest((material,))},
        "provenance": {**material, "scientific_owner": "L3", "d1_report_consumed": False},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def validate_l3_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "L3" or report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "L3 identity/schema mismatch"
    if report.get("provenance", {}).get("d1_report_consumed") is not False:
        return False, "L3 may not consume D1 report authority"
    if report.get("dataset", {}).get("sample_size") != 95:
        return False, "L3 governed analytical population mismatch"
    return True, "valid independently-owned L3 report"
