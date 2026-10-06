"""Governed L6 runner (additive Stage 4 repair, no S3, fail-closed)."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Mapping
from research_engine.control_plane.evidence_provenance import CURRENT, evidence_digest
from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
from research_engine.control_plane.stage4_impl_ownership_labels import L6_OWNED_REPORT
from research_engine.control_plane.stage4_implementation_repairs import (
    enforce_current_population,
)
from research_engine.registry import learning_adaptation_adjudication as A
REPORT_SCHEMA_VERSION = "l6_learning_cycle_validation_v1"
REPORT_FILENAME = L6_OWNED_REPORT

# Governed evaluator semantic identity; see component_reward for the contract.
# L6 is the sole HD12 target (per-cycle pre/post learning-cycle confidence), so
# its evaluator binds the HD12 and full-learning governance contract versions.
EVALUATOR_SEMANTIC_VERSIONS = {
    "run_l6": "l6_hd12_learning_cycle_confidence_v1",
}
EVALUATOR_REPORT_SCHEMA_VERSIONS = {
    "run_l6": {"REPORT_SCHEMA_VERSION": REPORT_SCHEMA_VERSION},
}
EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS = {
    "run_l6": {
        "HD12_VERSION": A.HD12_VERSION,
        "LEARNING_VERSION": A.LEARNING_VERSION,
        "CURRENT_POPULATION_AUTHORITY": "current_snapshot_population_v1",
    },
}

class L6RepairError(ValueError):
    pass
def run_l6(records=None, identities=None, persist=False, population_authority=None):
    supplied = list(records or [])
    if population_authority is None:
        rows = enforce_exact_population("L6", supplied, identities)
        authority = {"kind": "HISTORICAL_CHECKPOINT", "expected_count": 1852}
        source = "frozen governed L6 population"
    else:
        rows = enforce_current_population("L6", supplied, population_authority)
        authority = {
            "kind": "CURRENT_SNAPSHOT",
            "snapshot_id": population_authority.snapshot_id,
            "evaluation_identity_digest": population_authority.evaluation_identity_digest,
            "expected_count": population_authority.expected_count,
            "population_digest": population_authority.population_digest,
        }
        source = "governed current-snapshot L6 population"
    prov = {"question_id": "L6", "report": REPORT_FILENAME, "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT, "n": len(rows), "population_authority": authority}
    report = {"question_id": "L6", "report_schema_version": REPORT_SCHEMA_VERSION, "status": "INSUFFICIENT_DATA", "epoch": CURRENT, "scientific_state": "INSUFFICIENT_DATA", "overall": {"finding": "LEARNING_CYCLE_AUTHORITY_NOT_OBSERVED", "note": f"The governed runner completed on {len(rows):,} records; the bound population has no producer-authoritative learning-cycle confidence observations, so no confidence conclusion is manufactured."}, "dataset": {"source": source, "sample_size": len(rows)}, "fingerprint": {"analytical_digest": evidence_digest((prov,)), "epoch": CURRENT, "records_used": len(rows)}, "provenance": {**prov, "missing_observable": {"name": "learning_cycle_confidence_observation", "grain": "learning_cycle x conclusion", "identity": ["cycle_id", "question_id"], "time_semantics": "recorded_at decision time", "lineage": "research conclusion -> validation evidence -> confidence score"}}, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return report
def validate_l6_report(report):
    if not isinstance(report, Mapping):
        return False, "L6 report not mapping"
    if report.get("question_id") != "L6":
        return False, "L6 identity mismatch"
    authority = report.get("provenance", {}).get("population_authority", {})
    expected = authority.get("expected_count", 1852)
    if report.get("dataset", {}).get("sample_size") != expected:
        return False, "L6 governed analytical population mismatch"
    if authority.get("kind") == "CURRENT_SNAPSHOT" and not all(
            authority.get(name) for name in (
                "snapshot_id", "evaluation_identity_digest", "population_digest")):
        return False, "L6 current population authority is incomplete"
    if report.get("scientific_state") == "IMPLEMENTATION_BLOCKED":
        return False, "L6 implementation remains blocked"
    return True, "valid governed L6 report"
