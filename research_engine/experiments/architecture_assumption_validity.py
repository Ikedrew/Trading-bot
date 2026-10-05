"""Governed L3 architecture-assumption evidence audit."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from research_engine.control_plane.evidence_provenance import CURRENT, evidence_digest
from research_engine.control_plane.stage4_impl_ownership_labels import L3_OWNED_REPORT
from research_engine.registry import learning_adaptation_adjudication as A

# Governed evaluator semantic identity; see component_reward for the contract.
REPORT_SCHEMA_VERSION = "l3_architecture_assumption_validity_v3"
EVALUATOR_SEMANTIC_VERSIONS = {
    "run_l3": "l3_hd11_architecture_assumption_validity_v3",
}
EVALUATOR_REPORT_SCHEMA_VERSIONS = {
    "run_l3": {"REPORT_SCHEMA_VERSION": REPORT_SCHEMA_VERSION},
}
EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS = {
    "run_l3": {
        "HD11_VERSION": A.HD11_VERSION,
        "LEARNING_VERSION": A.LEARNING_VERSION,
    },
}
REPORT_FILENAME = L3_OWNED_REPORT


def _identity(row: Mapping[str, Any]) -> str:
    identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    return str(row.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id") or "")


def run_l3(*, records=None, identities=None, persist: bool = False) -> dict[str, Any]:
    """Evaluate CURRENT L3 under HD11's independent three-subtest contract.

    Stage-4's historical n=95 is assurance lineage only.  It is deliberately
    neither a selector nor a completion gate for CURRENT L3.
    """
    rows = [dict(row) for row in (records or ()) if isinstance(row, Mapping)]
    distinct = len({_identity(row) for row in rows if _identity(row)})
    blockers = ("L3_WEIGHT_PROFILE_ABSENT", "L3_MAPPING_EVIDENCE_BLOCKER")
    subtests = {
        "weight": {
            "status": "BLOCKED", "sample_n": distinct,
            "minimum_n": A.L3_WEIGHT_MIN["distinct_paired_opportunities"],
            "blocker": blockers[0],
        },
        "regime": {
            "status": "NOT_EVALUATED_DUE_BLOCKING_PRECEDENCE",
            "sample_n": distinct,
            "minimum_n": A.L3_REGIME_MIN["distinct_grouped_opportunities"],
            "per_cell_minimum": A.L3_REGIME_MIN["per_cell"],
        },
        "mapping": {
            "status": "BLOCKED", "sample_n": distinct,
            "minimum_n": A.L3_MAPPING_MIN["distinct_grouped_opportunities"],
            "per_cell_minimum": A.L3_MAPPING_MIN["per_cell"],
            "blocker": blockers[1],
        },
    }
    material = {
        "question_id": "L3", "report": REPORT_FILENAME,
        "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT,
        "contract_version": A.HD11_VERSION,
        "current_population": len(rows), "distinct_opportunities": distinct,
        "subtests": subtests,
    }
    return {
        "question_id": "L3",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "BLOCKED",
        "scientific_state": "BLOCKED",
        "failure_reason": ";".join(blockers),
        "missing_evidence": list(blockers),
        "confidence": "INSUFFICIENT_DATA",
        "epoch": CURRENT,
        "overall": {
            "finding": "HD11_L3_BLOCKED_ON_MISSING_HISTORICAL_AUTHORITIES",
            "subtests": subtests,
        },
        "key_metrics": {
            "current_population": len(rows),
            "distinct_canonical_opportunities": distinct,
            "historical_stage4_assurance_n": 95,
            "historical_count_enforced_for_current": False,
        },
        "dataset": {"source": "CURRENT snapshot under HD11", "sample_size": distinct},
        "fingerprint": {"epoch": CURRENT, "records_used": len(rows),
                        "analytical_digest": evidence_digest((material,))},
        "provenance": {**material, "scientific_owner": "L3",
                       "population_authority": A.HD11_VERSION,
                       "d1_report_consumed": False},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def validate_l3_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "L3" or report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "L3 identity/schema mismatch"
    provenance = report.get("provenance", {})
    if provenance.get("d1_report_consumed") is not False:
        return False, "L3 may not consume D1 report authority"
    if provenance.get("contract_version") != A.HD11_VERSION:
        return False, "L3 HD11 contract identity mismatch"
    subtests = report.get("overall", {}).get("subtests", {})
    if tuple(subtests) != A.L3_HOLM_ORDER:
        return False, "L3 independent subtest accounting mismatch"
    if report.get("status") == "BLOCKED":
        missing = set(report.get("missing_evidence", ()))
        if not {"L3_WEIGHT_PROFILE_ABSENT", "L3_MAPPING_EVIDENCE_BLOCKER"} <= missing:
            return False, "L3 governed blockers are incomplete"
    sample = report.get("dataset", {}).get("sample_size")
    if not isinstance(sample, int) or sample < 0:
        return False, "L3 CURRENT analytical population is invalid"
    return True, "valid independently-owned HD11 L3 report"
