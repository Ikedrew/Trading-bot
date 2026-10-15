"""Finalize the already committed Milestone 4 Cycle 2 publication transaction.

This entry point never invokes frontier discovery, snapshot readers, or evaluators.
The archived evaluator fingerprint is checked as provenance only.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

HOST = Path(__file__).resolve().parent
REPO = HOST.parent / "Trading-bot-stage-a-restored"
sys.path.insert(0, str(REPO))

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint
from research_engine.v10.investigation_snapshot import (
    authenticate_snapshot_inputs, load_investigation_snapshot_id,
)
from research_engine.v10.continuous.continuous_projection_bridge import (
    _verify_projection, sync_latest_completed_cycle,
)
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleLease, ContinuousCycleStore, ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.frontier_coordinator import FrontierStateStore
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_loop import _attempt_cycle_id, _cycle_id
from research_engine.v10.continuous.research_projection import ResearchProjectionStore
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore

BASELINE = "ISNAP-5FB0DD9FC4D808D6C23789CD"
SUCCESSOR = "ISNAP-5DA156EB1082C659C56EF4B7"
QUESTION = "QCYCLE-EB2CA76B313ED76A6D67791B06C7D7D5"
BRIDGE = "SBRIDGE-BF801904CD51DE154679ED81D655A02A"
CYCLE = "CRCYCLE-D2273157F055F6BB2614E33AF0DD37D4"
FAILED = "CRCYCLE-1D0A5BB524343EB75C89DC1174BBA519"
VERSION = "RPROJ-869A144143FD34B1639C52F314443D7E"
ARCHIVED_IMPLEMENTATION = "f6870d933cabe11ec070792db52262db331b026269bf526fe0bbc3aca639aff2"
IMMUTABLE_SHA256 = "e80bafd8b0022516d3efdd81d50162b22e3617b79caa0331711d21326e5df820"
BASELINE_POINTER_SHA256 = "a589d92c65f1d1daf4283753d1b6f06d98f677a65ace2f64f8ffe981862e3b65"
EMPTY_QUEUE_DIGEST = hashlib.sha256(canonical_json([]).encode("utf-8")).hexdigest()
ROOT = HOST / "milestone4_autonomy_sequence"
GATE = HOST / "milestone4_integrity_gate"


class RecoveryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"DOCUMENT_NOT_OBJECT:{path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Plan:
    root: Path
    projection: dict
    completed: ContinuousResearchCycleResult
    source_hashes: dict[str, str]
    existing_completion: bool


def validate_small_records(root: Path, failed: dict, question: object,
                           bridge: dict, replay: dict, qprojection: dict) -> None:
    """Validate archived authorities without claiming they match current code."""
    require(failed["continuous_cycle_id"] == FAILED and failed["cycle_outcome"] == "FAILED"
            and failed["failure_stage"] == "PROJECTION", "ORIGINAL_ATTEMPT_INVALID")
    expected_stages = {"FRONTIER": "NEW_VERIFIED_IMMUTABLE_SNAPSHOT_READY",
                       "QUESTIONS": "COMPLETED", "SCIENTIFIC_STATE": "NO_SCIENTIFIC_STATE_CHANGE",
                       "OBSERVATION_SPACE": "COMPLETED", "Q71_PLUS": "COMPLETED",
                       "Q71_EXECUTION": "SKIPPED_NO_EVALUATOR_REGISTRY",
                       "VALIDATION_QUEUE": "COMPLETED", "PROJECTION": "FAILED"}
    require(failed["stage_statuses"] == expected_stages, "RECORDED_STAGES_INVALID")
    require(failed["frontier_snapshot_id"] == SUCCESSOR
            and failed["question_cycle_id"] == QUESTION
            and failed["bridge_run_id"] == BRIDGE, "ATTEMPT_LINEAGE_INVALID")
    require(question.cycle_id == QUESTION and question.snapshot_id == SUCCESSOR
            and question.cycle_status == "COMPLETED" and question.total_questions == 70
            and question.evaluated_count == 11 and question.retained_count == 58
            and question.alias_count == 1 and question.complete_count == 14
            and question.insufficient_count == 37 and question.blocked_count == 10
            and question.waiting_count == 8 and len(question.result_ids) == 70,
            "QUESTION_ACCOUNTING_INVALID")
    require(bridge.get("bridge_run_id") == BRIDGE
            and bridge.get("source_cycle_id") == QUESTION
            and bridge.get("snapshot_id") == SUCCESSOR
            and bridge.get("source_kind") == "CANONICAL_QUESTION_CYCLE"
            and bridge.get("status") == "NO_SCIENTIFIC_STATE_CHANGE"
            and not bridge.get("findings_created") and not bridge.get("hypotheses_created")
            and not bridge.get("candidates_created") and not bridge.get("validation_handoff"),
            "SCIENTIFIC_BRIDGE_INVALID")
    require(qprojection.get("replay_implementation_identity") == ARCHIVED_IMPLEMENTATION
            and qprojection.get("replay_scope_digest") == replay["authority"]["scope_digest"],
            "ARCHIVED_IMPLEMENTATION_PROVENANCE_INVALID")
    require(set(qprojection["questions"]) == set(question.result_ids),
            "QUESTION_PROJECTION_MEMBERSHIP_INVALID")
    require(not (root / "continuous/validation_queue.json").exists(),
            "VALIDATION_QUEUE_NOT_EMPTY")
    material = {"snapshot": SUCCESSOR, "question": QUESTION, "bridge": BRIDGE,
                "agenda": failed["q71_agenda_id"],
                "validation_queue_version": EMPTY_QUEUE_DIGEST,
                "predecessor": failed["predecessor_cycle_id"]}
    require(_attempt_cycle_id(failed["cycle_attempt_id"], _cycle_id(material)) == CYCLE,
            "CONTINUOUS_ID_DERIVATION_INVALID")


def validate_pointers(root: Path, completed: ContinuousResearchCycleResult) -> bool:
    source = root / "continuous"
    lab = root / "lab_publication"
    cycles = ContinuousCycleStore(source / "cycles")
    prior = cycles.load_latest_success()
    require(prior is not None, "PRIOR_COMPLETION_MISSING")
    require(prior.continuous_cycle_id in {completed.predecessor_cycle_id, CYCLE},
            "UNEXPECTED_COMPLETED_CYCLE")
    existing = cycles.history_directory / (CYCLE + ".json")
    if existing.exists():
        require(canonical_json(read(existing)) == canonical_json(completed.to_dict()),
                "CONFLICTING_COMPLETION_RECEIPT")
    require(prior.continuous_cycle_id != CYCLE or existing.exists(),
            "SUCCESS_POINTER_WITHOUT_HISTORY")
    for store_root in (source, lab):
        pointer = store_root / "projection/latest.json"
        if pointer.exists():
            pointer_hash = sha256(pointer)
            require(pointer_hash in {BASELINE_POINTER_SHA256, IMMUTABLE_SHA256},
                    f"UNEXPECTED_CURRENT_PROJECTION:{pointer}")
        cycle_pointer = store_root / "cycles/latest_completed.json"
        if cycle_pointer.exists():
            row = read(cycle_pointer)
            require(row.get("continuous_cycle_id") in {
                completed.predecessor_cycle_id, CYCLE},
                f"UNEXPECTED_COMPLETED_POINTER:{cycle_pointer}")
    baseline_history = source / "projection/history" / (
        "RPROJ-F2941A19B4BA4CED60EE200ADD92BD97.json")
    require(sha256(baseline_history) == BASELINE_POINTER_SHA256,
            "BASELINE_HISTORY_POINTER_INCONSISTENT")
    lab_latest = lab / "projection/latest.json"
    lab_cycle = read(lab / "cycles/latest_completed.json")
    require((lab_cycle["continuous_cycle_id"] == completed.predecessor_cycle_id
             and sha256(lab_latest) == BASELINE_POINTER_SHA256)
            or (lab_cycle["continuous_cycle_id"] == CYCLE
                and sha256(lab_latest) == IMMUTABLE_SHA256),
            "LAB_POINTER_LINEAGE_INCONSISTENT")
    return existing.exists()


def validate_projection(projection: dict, question_result_ids: dict[str, str]) -> None:
    _verify_projection(projection, expected_version=VERSION)
    require(projection.get("continuous_cycle_id") == CYCLE
            and projection.get("canonical_question_cycle_id") == QUESTION
            and projection.get("predecessor_projection_version")
            == "RPROJ-F2941A19B4BA4CED60EE200ADD92BD97"
            and len(projection["canonical_questions"]) == 70
            and all(row.get("cycle_snapshot_id") == SUCCESSOR
                    for row in projection["canonical_questions"]),
            "UNIFIED_PROJECTION_LINEAGE_INVALID")
    ids = {row["question_id"]: row["result"]["result_id"]
           for row in projection["canonical_questions"]}
    require(ids == question_result_ids, "UNIFIED_PROJECTION_QUESTION_IDS_INVALID")


def preflight(root: Path = ROOT, gate_dir: Path = GATE) -> Plan:
    root, gate_dir = Path(root), Path(gate_dir)
    require(not (root / "continuous/active_cycle.lock").exists(), "ACTIVE_CYCLE_LEASE_PRESENT")
    gate = read(gate_dir / "gate_result.json")
    require(gate.get("status") == "PASS" and gate.get("baseline_id") == BASELINE
            and gate.get("successor_id") == SUCCESSOR
            and gate.get("changed_datasets") == ["execution_context"],
            "SOURCE_GATE_INVALID")
    increment = gate_dir / "simulated_execution_context.jsonl"
    require(sha256(increment) == gate["simulated_sha256"], "INCREMENT_BYTES_CHANGED")
    baseline = load_investigation_snapshot_id(BASELINE, manifest_directory=root / "manifests")
    successor = load_investigation_snapshot_id(SUCCESSOR, manifest_directory=root / "manifests")
    require(successor.snapshot_id != baseline.snapshot_id, "SUCCESSOR_NOT_DISTINCT")
    # Authentication checks the actual production source-authority contract and
    # current dataset schemas. It makes no LIST/HEAD/GET requests.
    from m4_integrity_gate import LocalEvidenceSource
    index = read(root / "local_cache_path_index.json")
    require(index.get("baseline_id") == BASELINE
            and len(index.get("paths", {})) == 44938,
            "LOCAL_EVIDENCE_INDEX_INVALID")
    membership = {binding.dataset: [item.to_dict() for item in binding.objects]
                  for binding in successor.datasets}
    source = LocalEvidenceSource(
        {key: Path(value) for key, value in index["paths"].items()},
        membership, increment, gate["simulated_key"])
    authenticate_snapshot_inputs(successor, source)
    require(source.local_lists == 0 and source.local_client.heads == 0
            and source.local_client.gets == 0,
            "PREFLIGHT_SOURCE_IO_OCCURRED")
    original = {binding.dataset: {item.identifier: item.to_dict()
                                  for item in binding.objects}
                for binding in baseline.datasets}
    evolved = {binding.dataset: {item.identifier: item.to_dict()
                                 for item in binding.objects}
               for binding in successor.datasets}
    require(all(evolved[name].get(key) == value for name, rows in original.items()
                for key, value in rows.items())
            and sum(len(rows) for rows in evolved.values()) == 44939
            and sum(len(rows) for rows in original.values()) == 44938,
            "SUCCESSOR_OBJECT_LINEAGE_INVALID")
    frontier = FrontierStateStore(root / "frontier_state").load_latest_success()
    require(frontier is not None
            and frontier["last_successful_snapshot_id"] == SUCCESSOR
            and frontier["last_successful_snapshot_fingerprint"] == successor.snapshot_fingerprint
            and frontier["predecessor_snapshot_id"] == BASELINE
            and frontier["delta"]["changed_datasets"] == ["execution_context"],
            "FRONTIER_LINEAGE_INVALID")

    cycles = ContinuousCycleStore(root / "continuous/cycles")
    failed = read(cycles.history_directory / (FAILED + ".json"))
    qstore = QuestionCycleStore(root / "questions")
    replay_document = read(root / "questions/replay" / (QUESTION + ".json"))
    require(fingerprint(replay_document["payload"]) == replay_document["sha256"],
            "QUESTION_REPLAY_RECEIPT_DIGEST_INVALID")
    authority = replay_document["payload"]["authority"]
    archived = qstore.load_replay(QUESTION, authority)
    require(archived is not None, "QUESTION_REPLAY_RECEIPT_MISSING")
    question, qprojection = archived
    scientific = ScientificStateStore(root / "science")
    bridge = scientific.load_run(BRIDGE)
    require(bridge is not None, "SCIENTIFIC_BRIDGE_RECEIPT_MISSING")
    validate_small_records(root, failed, question, bridge,
                           replay_document["payload"], qprojection)
    del qprojection, archived
    gc.collect()

    immutable = root / "continuous/projection/history" / (VERSION + ".json")
    require(sha256(immutable) == IMMUTABLE_SHA256, "IMMUTABLE_PROJECTION_SHA256_INVALID")
    projection = read(immutable)
    validate_projection(projection, question.result_ids)
    stages = dict(failed["stage_statuses"])
    stages["PROJECTION"] = "COMPLETED"
    completed = ContinuousResearchCycleResult(
        continuous_cycle_id=CYCLE, cycle_outcome="COMPLETED",
        frontier_snapshot_id=SUCCESSOR, question_cycle_id=QUESTION,
        bridge_run_id=BRIDGE, q71_agenda_id=failed["q71_agenda_id"],
        q71_queue_id=failed["q71_queue_id"],
        validation_queue_version=EMPTY_QUEUE_DIGEST,
        projection_version=VERSION,
        predecessor_cycle_id=failed["predecessor_cycle_id"],
        started_at=failed["started_at"], completed_at=question.completed_at,
        stage_statuses=stages, validation_transitions=(),
        shadow_eligibility=(), projection_path=str(immutable),
        cycle_attempt_id=failed["cycle_attempt_id"], evidence_identity=SUCCESSOR)
    already = validate_pointers(root, completed)
    source_latest = root / "continuous/projection/latest.json"
    lab_latest = root / "lab_publication/projection/latest.json"
    lab_history = root / "lab_publication/projection/history" / (VERSION + ".json")
    pending_large_writes = (int(not source_latest.exists() or
                                sha256(source_latest) != IMMUTABLE_SHA256)
                            + int(not lab_history.exists())
                            + int(not lab_latest.exists() or
                                  sha256(lab_latest) != IMMUTABLE_SHA256))
    required_free = immutable.stat().st_size * pending_large_writes + 512 * 1024 * 1024
    require(shutil.disk_usage(root).free >= required_free,
            f"INSUFFICIENT_DISK_FOR_ATOMIC_PUBLICATION:{required_free}")
    sources = [root / "questions/cycles" / (QUESTION + ".json"),
               root / "questions/replay" / (QUESTION + ".json"),
               root / "questions/projections" / (QUESTION + ".json"),
               root / "science/runs" / (BRIDGE + ".json"),
               root / "science/state.json", immutable,
               root / "continuous/cycles/history" / (FAILED + ".json")]
    hashes = {str(path.relative_to(root)): sha256(path) for path in sources}
    return Plan(root, projection, completed, hashes, already)


def publish(plan: Plan, *, stop_after: str | None = None) -> dict:
    root = plan.root
    with ContinuousCycleLease(root / "continuous/active_cycle.lock",
                              lease_id="M4-PUBLICATION-" + uuid.uuid4().hex):
        store = ResearchProjectionStore(root / "continuous/projection")
        current = store.latest_path
        if current.exists():
            require(sha256(current) in {BASELINE_POINTER_SHA256, IMMUTABLE_SHA256},
                    "POINTER_CHANGED_AFTER_PREFLIGHT")
        history = store.save(plan.projection)
        require(sha256(history) == IMMUTABLE_SHA256
                and sha256(current) == IMMUTABLE_SHA256
                and store.load_latest()["projection_version"] == VERSION,
                "POINTER_READBACK_INVALID")
        print(json.dumps({"stage": "PROJECTION_POINTER_VERIFIED",
                          "sha256": IMMUTABLE_SHA256}), flush=True)
        if stop_after == "pointer":
            raise RecoveryError("INJECTED_INTERRUPTION_AFTER_POINTER")

        cycles = ContinuousCycleStore(root / "continuous/cycles")
        history = cycles.history_directory / (CYCLE + ".json")
        if history.exists():
            require(canonical_json(read(history)) == canonical_json(plan.completed.to_dict()),
                    "CONFLICTING_COMPLETION_RECEIPT")
        cycles.save(plan.completed, successful=True)
        require(cycles.load_latest_success() == plan.completed,
                "COMPLETION_READBACK_INVALID")
        print(json.dumps({"stage": "CONTINUOUS_RECEIPT_VERIFIED",
                          "cycle_id": CYCLE}), flush=True)
        audit_path = root / "continuous/cycles/recovery" / (CYCLE + ".json")
        if not audit_path.exists():
            atomic_json(audit_path, {
                "recovery_schema": "m4_publication_recovery_v1",
                "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
                "continuous_cycle_id": CYCLE,
                "original_failed_cycle_id": FAILED,
                "original_attempt_id": plan.completed.cycle_attempt_id,
                "source_artifact_sha256": plan.source_hashes,
            })
        require(read(audit_path)["source_artifact_sha256"] == plan.source_hashes,
                "RECOVERY_AUDIT_CONFLICT")
        if stop_after == "receipt":
            raise RecoveryError("INJECTED_INTERRUPTION_AFTER_RECEIPT")

        lab = sync_latest_completed_cycle(root / "lab_publication",
                                          source=root / "continuous")
        require(lab.status in {"IMPORTED", "UNCHANGED"}
                and lab.imported_cycle_id == CYCLE
                and lab.imported_projection_version == VERSION,
                "LAB_PUBLICATION_INCOMPLETE")
        lab_latest = root / "lab_publication/projection/latest.json"
        require(sha256(lab_latest) == IMMUTABLE_SHA256
                and sha256(current) == sha256(lab_latest),
                "LAB_PROJECTION_PARITY_INVALID")
        lab_pointer = read(root / "lab_publication/cycles/latest_completed.json")
        require(lab_pointer["continuous_cycle_id"] == CYCLE
                and lab_pointer["projection_version"] == VERSION,
                "LAB_COMPLETION_POINTER_INVALID")
        marker = read(root / "lab_publication/continuous_bridge_state.json")
        require(marker["imported_cycle_id"] == CYCLE
                and marker["imported_projection_version"] == VERSION,
                "LAB_PUBLICATION_MARKER_INVALID")
        return {"status": "PASS", "cycle_id": CYCLE, "projection_version": VERSION,
                "lab_bridge_status": lab.status, "projection_sha256": IMMUTABLE_SHA256,
                "recovery_audit": str(audit_path), "lab_marker": str(
                    root / "lab_publication/continuous_bridge_state.json")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    report = ROOT / "m4_publication_recovery_report.json"
    try:
        plan = preflight()
        preflight_row = {"status": "PASS", "cycle_id": CYCLE,
                         "question_cycle_id": QUESTION, "scientific_bridge_id": BRIDGE,
                         "snapshot_id": SUCCESSOR, "projection_version": VERSION,
                         "archived_implementation": ARCHIVED_IMPLEMENTATION,
                         "existing_completion": plan.existing_completion,
                         "source_artifact_sha256": plan.source_hashes,
                         "historical_body_loads": 0, "question_evaluations": 0}
        atomic_json(ROOT / "m4_publication_preflight.json", preflight_row)
        print(json.dumps({"preflight": preflight_row}, sort_keys=True), flush=True)
        if args.preflight_only:
            return 0
        result = publish(plan)
        atomic_json(report, {"preflight": preflight_row, "publication": result})
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        atomic_json(report, {"status": "FAIL", "error": f"{type(exc).__name__}:{exc}"})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
