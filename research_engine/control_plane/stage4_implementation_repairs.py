"""Stage 4 implementation-gap repairs (additive, fail-closed, no V1 mutation)."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence
GOVERNED_USABLE = {"R1": 635, "R2": 635, "L3": 95, "L6": 1852, "L7": 14046, "G2": 261, "G3": 1852, "EX2": 8760}
HISTORICAL_GOVERNED_USABLE = dict(GOVERNED_USABLE)
FORBIDDEN_RUNNER_N = {"R1": 10803, "R2": 10803, "G2": 22521, "EX2": 9045}
REPAIR_MODULE_VERSION = "stage4_impl_repairs_v2"
L7_LABEL_CONTRACT_VERSION = "l7_label_contract_v2"
L7_VALID_LABELS = frozenset({"CONTROL", "CANDIDATE"})
L3_OWNED_REPORT = "l3_architecture_assumption_validity.json"
D1_OWNED_REPORT = "q1_component_reward.json"
L6_OWNED_REPORT = "l6_learning_cycle_validation.json"
L7_OWNED_REPORT = "l7_adaptation_evidence.json"
L6_RUNNER_MODULE = "research_engine.experiments.learning_cycle_validation"
L6_RUNNER_FUNCTION = "run_l6"
class Stage4RepairError(ValueError):
    pass


@dataclass(frozen=True)
class CurrentPopulationAuthority:
    """Canonical-cycle authority for one current snapshot population."""

    question_id: str
    snapshot_id: str
    evaluation_identity_digest: str
    expected_count: int
    population_digest: str


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")).hexdigest()
def population_fingerprint(question_id: str, identities: Sequence[str]) -> str:
    qid = str(question_id or "").strip().upper()
    ids = sorted({str(i) for i in identities})
    return fingerprint({"question_id": qid, "identities": ids, "n": len(ids), "contract": REPAIR_MODULE_VERSION})


def build_current_population_authority(
    question_id: str, records: Sequence[Mapping[str, Any]], *, snapshot_id: str,
    evaluation_identity_digest: str,
) -> CurrentPopulationAuthority:
    qid = str(question_id or "").strip().upper()
    if not qid or not snapshot_id or not evaluation_identity_digest:
        raise Stage4RepairError("CURRENT_POPULATION_AUTHORITY_INCOMPLETE:" + qid)
    rows = list(records)
    return CurrentPopulationAuthority(
        question_id=qid,
        snapshot_id=str(snapshot_id),
        evaluation_identity_digest=str(evaluation_identity_digest),
        expected_count=len(rows),
        population_digest=fingerprint(rows),
    )


def enforce_current_population(
    question_id: str, records: Sequence[Mapping[str, Any]],
    authority: CurrentPopulationAuthority,
) -> list[dict[str, Any]]:
    qid = str(question_id or "").strip().upper()
    rows = list(records)
    if not isinstance(authority, CurrentPopulationAuthority):
        raise Stage4RepairError("CURRENT_POPULATION_AUTHORITY_INVALID:" + qid)
    if authority.question_id != qid or not authority.snapshot_id or not authority.evaluation_identity_digest:
        raise Stage4RepairError("CURRENT_POPULATION_AUTHORITY_MISMATCH:" + qid)
    if len(rows) != authority.expected_count:
        raise Stage4RepairError(
            f"POPULATION_MISMATCH:{qid}:got={len(rows)}:expected={authority.expected_count}")
    if fingerprint(rows) != authority.population_digest:
        raise Stage4RepairError("POPULATION_MEMBERSHIP_MISMATCH:" + qid)
    return [dict(row) if isinstance(row, Mapping) else {} for row in rows]
