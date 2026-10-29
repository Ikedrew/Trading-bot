"""L3/L6/L7/G3 fail-closed repairs (additive, no V1 mutation)."""
from __future__ import annotations
from typing import Any, Mapping, Sequence
from research_engine.control_plane.stage4_implementation_repairs import Stage4RepairError
L7_LABEL_CONTRACT_VERSION = "l7_label_contract_v2"
L7_VALID_LABELS = frozenset({"CONTROL", "CANDIDATE"})
L3_OWNED_REPORT = "l3_architecture_assumption_validity.json"
D1_OWNED_REPORT = "q1_component_reward.json"
L6_OWNED_REPORT = "l6_learning_cycle_validation.json"
L7_OWNED_REPORT = "l7_adaptation_evidence.json"
L6_RUNNER_MODULE = "research_engine.experiments.learning_cycle_validation"
L6_RUNNER_FUNCTION = "run_l6"
def resolve_l3_ownership(question_id: str, report_filename: str, report_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    qid = str(question_id or "").strip().upper()
    base = str(report_filename or "").strip().split("/")[-1].lower()
    if qid == "L3":
        if base != L3_OWNED_REPORT:
            return {"allowed": False, "owner": "L3", "reason": "AMBIGUOUS_REPORT_MAPPING:L3 must own " + L3_OWNED_REPORT + ", got " + base}
        declared = ""
        if isinstance(report_metadata, Mapping):
            declared = str(report_metadata.get("question_id", "") or "").strip().upper()
        if declared and declared != "L3":
            return {"allowed": False, "owner": "L3", "reason": "AMBIGUOUS_REPORT_MAPPING:metadata declares " + declared}
        return {"allowed": True, "owner": "L3", "reason": L3_OWNED_REPORT + " canonically owned by L3"}
    if qid == "D1":
        if base != D1_OWNED_REPORT:
            return {"allowed": False, "owner": "D1", "reason": "AMBIGUOUS_REPORT_MAPPING:D1 must own " + D1_OWNED_REPORT}
        return {"allowed": True, "owner": "D1", "reason": D1_OWNED_REPORT + " canonically owned by D1"}
    if base == D1_OWNED_REPORT and qid != "D1":
        return {"allowed": False, "owner": "D1", "reason": "AMBIGUOUS_REPORT_MAPPING:" + base + " owned by D1, not " + qid}
    if base == L3_OWNED_REPORT and qid != "L3":
        return {"allowed": False, "owner": "L3", "reason": "AMBIGUOUS_REPORT_MAPPING:" + base + " owned by L3, not " + qid}
    return {"allowed": False, "owner": "", "reason": "AMBIGUOUS_REPORT_MAPPING:unresolved " + qid + "/" + base}
def govern_l7_label(raw: Any) -> str:
    val = str(raw if raw is not None else "").strip().upper()
    if val not in L7_VALID_LABELS:
        raise Stage4RepairError("L7_UNKNOWN_LABEL_FAIL_CLOSED:" + repr(raw))
    return val
def assign_l7_labels(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bind the governed arm label from the PRODUCER-ISSUED arm block.

    The arm is read from ``experiment_arm`` inside the ``experiment_arm`` block
    that the shadow runtime issues at OPEN.  It is deliberately NOT read from
    ``schema_version``: that field carries the record-structure identity
    (``shadow_runtime_v1``), and reading an arm from it is the exact semantic
    collision ROOT-05 exists to remove.

    A dataset/version-shaped token, a missing block, or an unrecognised label
    fails closed.  There is no chronology, free-text or proxy inference.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise Stage4RepairError("L7_UNKNOWN_LABEL_FAIL_CLOSED:non-mapping row")

        block = row.get("experiment_arm")
        if not isinstance(block, Mapping):
            raise Stage4RepairError("L7_ARM_BLOCK_MISSING_FAIL_CLOSED")
        src = block.get("experiment_arm")

        # A dataset/schema identity presented as an arm is the precise
        # confusion this contract forbids, so it is named explicitly.
        text = str(src or "").strip().upper()
        if text.endswith("_V1") or text.endswith("_V2"):
            raise Stage4RepairError(
                "L7_SCHEMA_IDENTITY_PRESENTED_AS_ARM:" + repr(src))
        label = govern_l7_label(src)

        item = dict(row)
        item["governed_arm"] = label
        item["label_provenance"] = {
            "contract": L7_LABEL_CONTRACT_VERSION,
            "source_field": "experiment_arm.experiment_arm",
            "source_block": "experiment_arm",
            "assignment_id": block.get("arm_assignment_id"),
            "assigned_at_event": block.get("arm_assigned_at_event"),
            "pre_outcome": (
                block.get("arm_pre_outcome_attestation") or {}
            ).get("outcome_knowledge_at_assignment") == "NONE",
            "label": label,
        }
        out.append(item)
    return out


def l6_runner_identity() -> dict[str, str]:
    return {"module": L6_RUNNER_MODULE, "function": L6_RUNNER_FUNCTION, "report": L6_OWNED_REPORT, "live_s3_fallback": "FORBIDDEN"}
def gate_g3_on_l6(l6_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(l6_state, Mapping):
        return {"allowed": False, "reason": "G3_BLOCKED:L6 dependency unsatisfied (no L6 state)"}
    epoch = str(l6_state.get("epoch", "") or l6_state.get("evidence_epoch", "") or "").upper()
    sci = str(l6_state.get("scientific_state", "") or l6_state.get("status", "") or "").upper()
    valid_states = {"COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA", "WAITING_DATA", "HISTORICALLY_UNANSWERABLE"}
    if sci == "IMPLEMENTATION_BLOCKED" or not sci:
        return {"allowed": False, "reason": "G3_BLOCKED:L6 still IMPLEMENTATION_BLOCKED"}
    if epoch != "CURRENT":
        return {"allowed": False, "reason": "G3_BLOCKED:L6 epoch not CURRENT (" + epoch + ")"}
    if sci not in valid_states:
        return {"allowed": False, "reason": "G3_BLOCKED:L6 state " + sci + " not consumable"}
    return {"allowed": True, "reason": "G3_UNLOCKED:L6 " + sci + " CURRENT"}
