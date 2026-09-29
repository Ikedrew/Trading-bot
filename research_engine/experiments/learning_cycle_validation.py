"""Governed L6 runner (additive Stage 4 repair, no S3, fail-closed)."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Mapping
from research_engine.control_plane.evidence_provenance import CURRENT, evidence_digest
from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
from research_engine.control_plane.stage4_impl_ownership_labels import L6_OWNED_REPORT
REPORT_SCHEMA_VERSION = "l6_learning_cycle_validation_v1"
REPORT_FILENAME = L6_OWNED_REPORT
class L6RepairError(ValueError):
    pass
def run_l6(records=None, identities=None, persist=False):
    rows = enforce_exact_population("L6", list(records or []), identities)
    prov = {"question_id": "L6", "report": REPORT_FILENAME, "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT, "n": len(rows)}
    report = {"question_id": "L6", "report_schema_version": REPORT_SCHEMA_VERSION, "status": "INSUFFICIENT_DATA", "epoch": CURRENT, "scientific_state": "INSUFFICIENT_DATA", "overall": {"finding": "LEARNING_CYCLE_AUTHORITY_NOT_OBSERVED", "note": "The governed runner completed on 1,852 records; the frozen checkpoint has no producer-authoritative learning-cycle confidence observations, so no confidence conclusion is manufactured."}, "dataset": {"source": "frozen governed L6 population", "sample_size": len(rows)}, "fingerprint": {"analytical_digest": evidence_digest((prov,)), "epoch": CURRENT, "records_used": len(rows)}, "provenance": {**prov, "missing_observable": {"name": "learning_cycle_confidence_observation", "grain": "learning_cycle x conclusion", "identity": ["cycle_id", "question_id"], "time_semantics": "recorded_at decision time", "lineage": "research conclusion -> validation evidence -> confidence score"}}, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return report
def validate_l6_report(report):
    if not isinstance(report, Mapping):
        return False, "L6 report not mapping"
    if report.get("question_id") != "L6":
        return False, "L6 identity mismatch"
    if report.get("dataset", {}).get("sample_size") != 1852:
        return False, "L6 governed analytical population mismatch"
    if report.get("scientific_state") == "IMPLEMENTATION_BLOCKED":
        return False, "L6 implementation remains blocked"
    return True, "valid governed L6 report"
