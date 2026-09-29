"""Stage 4 implementation-gap repairs (additive, fail-closed, no V1 mutation)."""
from __future__ import annotations
import hashlib
import json
from typing import Any, Mapping, Sequence
GOVERNED_USABLE = {"R1": 635, "R2": 635, "L3": 95, "L6": 1852, "L7": 14046, "G2": 261, "G3": 1852, "EX2": 8760}
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
def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")).hexdigest()
def population_fingerprint(question_id: str, identities: Sequence[str]) -> str:
    qid = str(question_id or "").strip().upper()
    ids = sorted({str(i) for i in identities})
    return fingerprint({"question_id": qid, "identities": ids, "n": len(ids), "contract": REPAIR_MODULE_VERSION})
