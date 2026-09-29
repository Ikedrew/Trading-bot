"""Governed L7 adaptation-evidence runner (additive Stage 4 repair).

Deterministic control/candidate arm assignment from the governed, versioned
label contract.  Unknown or unmapped labels fail closed.  No S3 / live reads,
no chronological-halves fallback, no always-COMPLETE behaviour.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Mapping
from research_engine.control_plane.evidence_provenance import CURRENT, evidence_digest
from research_engine.control_plane.stage4_impl_ownership_labels import (
    L7_LABEL_CONTRACT_VERSION,
    L7_OWNED_REPORT,
    assign_l7_labels,
    govern_l7_label,
)
from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
from research_engine.registry.learning_adaptation_adjudication import L7_MIN
REPORT_SCHEMA_VERSION = "l7_adaptation_evidence_v1"
REPORT_FILENAME = L7_OWNED_REPORT
class L7RepairError(ValueError):
    """Structural L7 label-contract or sufficiency failure."""
def label_contract() -> dict[str, Any]:
    """Versioned, deterministic label contract bound to the L7 evidence contract."""
    return {
        "contract_version": L7_LABEL_CONTRACT_VERSION,
        "valid_labels": ["CONTROL", "CANDIDATE"],
        "assignment_rule": (
            "producer-issued pre-outcome experiment_arm block; the arm value "
            "is exactly CONTROL or CANDIDATE. schema_version is the "
            "record-structure identity and is NEVER an arm."
        ),
        "unknown_label_behaviour": "FAIL_CLOSED",
        "free_text_interpretation": False,
        "runtime_inference": False,
        "minimums": {"control": int(L7_MIN["control"]), "candidate": int(L7_MIN["candidate"]), "cell": int(L7_MIN["cell"])},
    }
def arm_counts(rows) -> dict[str, int]:
    counts = {"CONTROL": 0, "CANDIDATE": 0}
    for row in rows:
        counts[govern_l7_label(row.get("governed_arm"))] += 1
    return counts
def run_l7(records=None, identities=None, persist=False):
    rows = enforce_exact_population("L7", list(records or []), identities)
    # Exhaustive frozen-evidence adjudication established that the V2 label
    # contract is correct but was never historically emitted.  Preserve the
    # fail-closed label rule and publish a scientific absence result rather
    # than manufacturing arms from chronology or other proxies.
    if rows and all(str(row.get("schema_version", "")) == "shadow_trades_v1" for row in rows):
        from research_engine.control_plane.stage4_ex2_l7_blocker_adjudication import adjudicate_l7
        audit = adjudicate_l7()
        prov = {
            "question_id": "L7", "report": REPORT_FILENAME,
            "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT, "n": len(rows),
            "runner_analytical_population": len(rows),
            "label_contract": label_contract(),
            "observation_gap_adjudication": audit,
        }
        digest = evidence_digest((prov,))
        return {
            "question_id": "L7", "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "BLOCKED", "epoch": CURRENT,
            "scientific_state": "HISTORICALLY_UNANSWERABLE",
            "overall": {
                "finding": "Producer-authoritative CONTROL/CANDIDATE assignment was not historically recorded",
                "sample_size": len(rows),
                "observation_gap": "producer-authoritative CONTROL/CANDIDATE assignment",
            },
            "dataset": {"source": "frozen governed L7 population", "sample_size": len(rows)},
            "fingerprint": {"analytical_digest": digest, "epoch": CURRENT},
            "provenance": prov,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    labelled = assign_l7_labels(rows)
    counts = arm_counts(labelled)
    contract = label_contract()
    sufficient = (counts["CONTROL"] >= contract["minimums"]["control"] and counts["CANDIDATE"] >= contract["minimums"]["candidate"])
    prov = {"question_id": "L7", "report": REPORT_FILENAME, "schema": REPORT_SCHEMA_VERSION, "epoch": CURRENT, "n": len(labelled), "label_contract": contract, "arm_counts": counts}
    report = {"question_id": "L7", "report_schema_version": REPORT_SCHEMA_VERSION, "status": "WAITING_DATA" if sufficient else "INSUFFICIENT_DATA", "epoch": CURRENT, "scientific_state": "WAITING_DATA" if sufficient else "INSUFFICIENT_DATA", "overall": {"finding": "L7_GOVERNED_LABELS_BOUND", "arm_counts": counts, "minimums": contract["minimums"], "note": "Deterministic governed arms bound; statistical comparison requires intervention-gated candidate/control pairing absent from the 20260927 checkpoint."}, "dataset": {"source": "frozen governed L7 population", "sample_size": len(labelled)}, "fingerprint": {"analytical_digest": evidence_digest((prov,)), "epoch": CURRENT}, "provenance": prov, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return report
def validate_l7_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    if not isinstance(report, Mapping):
        return False, "L7 report not a mapping"
    if report.get("question_id") != "L7":
        return False, "L7 identity mismatch"
    if report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "L7 schema mismatch"
    prov = report.get("provenance")
    if not isinstance(prov, Mapping):
        return False, "L7 provenance missing"
    contract = prov.get("label_contract")
    if not isinstance(contract, Mapping) or contract.get("contract_version") != L7_LABEL_CONTRACT_VERSION:
        return False, "L7 label contract identity invalid"
    if contract.get("unknown_label_behaviour") != "FAIL_CLOSED":
        return False, "L7 unknown labels must fail closed"
    audit = prov.get("observation_gap_adjudication")
    if audit is not None:
        if report.get("status") != "BLOCKED" or report.get("scientific_state") != "HISTORICALLY_UNANSWERABLE":
            return False, "L7 observation-gap state invalid"
        if not isinstance(audit, Mapping) or audit.get("classification") != "HISTORICAL_OBSERVATION_GAP":
            return False, "L7 observation-gap adjudication invalid"
        keys = ("producer_authoritative_CONTROL", "producer_authoritative_CANDIDATE",
                "other_authoritative_assignment", "unlabeled_historically", "ambiguous", "unexplained")
        if int(audit.get("governed_population", -1)) != sum(int(audit.get(key, -1)) for key in keys):
            return False, "L7 observation-gap accounting does not conserve"
        if audit.get("governed_schema_versions") != {"shadow_trades_v1": 14046}:
            return False, "L7 historical producer schema accounting invalid"
        return True, "valid governed L7 historical observation-gap report"
    return True, "valid governed L7 label-bound report"
