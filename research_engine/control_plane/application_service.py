"""Explicit, injected Wave 5.2 fake-policy executor. Never called by live startup.

Operation files are fsynced atomic JSON replacements. INTENT is durable before
APPLYING; an interrupted APPLYING phase is ambiguous and is never retried.
Pre-deployment failures belong here, not in an illegal ledger FAILED transition.
Stores are trusted local governance authorities, not cryptographic attestations.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from research_engine.control_plane.application_ledger import ApplicationLedger
from research_engine.control_plane.production_adapter import PolicyAdapter
from research_engine.v10.baselines import baseline_authority as authority
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.candidates.candidate_registry import CandidateRegistry

IDENTITIES = ("candidate_id", "recommendation_id", "evaluation_id", "treatment_id",
              "baseline_id", "baseline_config_hash")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(path):
    if not Path(path).exists():
        return []
    # Unlike forgiving projection readers, never silently skip corrupt evidence.
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def exact(matches, label):
    require(len(matches) == 1, f"Missing or ambiguous {label}")
    return matches[0]


class ApplicationService:
    """Explicit execute(application_id), get_operation(id), rollback(id).

    Every path and adapter must be injected. Only the wave5_fake_policy envelope
    is supported; this class cannot translate any real runtime treatment.
    """

    def __init__(self, *, adapter: PolicyAdapter, application_path, decisions_dir,
                 recommendations_dir, registry_dir, evaluations_dir, operations_dir,
                 baselines_dir, pointer_file, failpoint=None):
        self.adapter = adapter
        self.application_path = Path(application_path)
        self.decisions_dir = Path(decisions_dir)
        self.recommendations_dir = Path(recommendations_dir)
        self.registry_dir = Path(registry_dir)
        self.evaluations_dir = Path(evaluations_dir)
        self.operations_dir = Path(operations_dir)
        self.registry = SnapshotRegistry(str(baselines_dir))
        self.pointer_file = Path(pointer_file)
        self.failpoint = failpoint or (lambda name: None)

    def _path(self, application_id):
        require(isinstance(application_id, str) and application_id.strip(), "Application ID required")
        return self.operations_dir / (digest(application_id) + ".json")

    def get_operation(self, application_id):
        path = self._path(application_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save(self, op):
        path = self._path(op["application"]["application_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(canonical(op))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)

    def _authorize(self, application_id, op):
        ledger_rows = rows(self.application_path)
        apps = [r for r in ledger_rows if r.get("application_id") == application_id]
        app = exact([r for r in apps if r.get("state") == "APPROVED_NOT_DEPLOYED"], "approved application")
        require(application_id == f"APP-{app['candidate_id']}-{app['recommendation_id']}", "Noncanonical application ID")
        for name in IDENTITIES:
            require(isinstance(app.get(name), str) and app[name].strip(), f"Missing {name}")
        decision = exact([d for d in rows(self.decisions_dir / "decisions.jsonl")
                          if d.get("candidate_id") == app["candidate_id"]
                          and d.get("outcome") == "COMPLETED"], "effective human decision")
        require(decision.get("decision") == "ACCEPT", "Human ACCEPT required")
        require(app.get("human_decision_outcome") == "COMPLETED", "Approval not completed")
        require(all(app.get(k) == decision.get(k) for k in (*IDENTITIES, "actor", "reason")), "Approval provenance mismatch")
        rec = exact([r for r in rows(self.recommendations_dir / "recommendations.jsonl")
                     if r.get("recommendation_id") == app["recommendation_id"]], "recommendation")
        require(all(rec.get(k) == app[k] for k in IDENTITIES), "Recommendation provenance mismatch")
        cid = app["candidate_id"]
        require(Path(cid).name == cid and "/" not in cid and "\\" not in cid, "Unsafe candidate ID")
        evaluation = exact([e for e in rows(self.evaluations_dir / f"{cid}.jsonl")
                            if e.get("evaluation_id") == app["evaluation_id"]], "evaluation")
        require(all(evaluation.get("config_hash" if k == "baseline_config_hash" else k) == app[k]
                    for k in IDENTITIES if k != "recommendation_id"), "Evaluation provenance mismatch")
        candidate = CandidateRegistry(str(self.registry_dir)).get(cid)
        require(candidate is not None and candidate.baseline_id == app["baseline_id"]
                and candidate.status == "ACCEPTED", "Candidate not accepted against exact baseline")
        require(any(v.validation_id == app["evaluation_id"] and v.decision == "IMPROVED"
                    for v in candidate.validation_history), "Missing candidate evidence")
        proof = {"decision": decision, "recommendation": rec, "evaluation": evaluation}
        if op:
            require(op["application"] == app and op["approval"] == proof, "Persisted authorization changed")
        else:
            require(apps[-1] == app, "Application is not APPROVED_NOT_DEPLOYED")
        require(all(all(r.get(k) == app[k] for k in IDENTITIES) for r in apps), "Application history conflict")
        require([r for r in ledger_rows if r.get("candidate_id") == cid][-1]["application_id"] == application_id,
                "Another application owns candidate ledger")
        return app, proof

    def _active(self):
        state = authority.get_active(registry=self.registry, pointer_file=self.pointer_file)
        require(state is not None, "Missing active baseline")
        return state.to_dict()

    def _old(self, app):
        old = self.registry.load(app["baseline_id"])
        require(old is not None and old.config_hash == app["baseline_config_hash"], "Stale baseline config")
        require("wave5_fake_policy" in old.configuration, "No captured starting fake state")
        return old

    def _transition(self, op, state):
        app = op["application"]
        ledger = ApplicationLedger(self.application_path)
        current = ledger.get_latest_for_candidate(app["candidate_id"])
        if current.state == state or (state == "DEPLOYED" and current.state == "VERIFIED"):
            require(current.deployment_reference == op["operation_id"], "Foreign deployment ledger")
            return
        fields = {k: v for k, v in app.items() if k not in ("state", "occurred_at")}
        fields.update(deployment_reference=op["operation_id"],
                      verification_evidence=digest(op.get("verification", {})))
        ledger.append(state=state, **fields)
        with self.application_path.open("r+b") as stream:
            os.fsync(stream.fileno())

    def execute(self, application_id):
        op = self.get_operation(application_id)
        app, proof = self._authorize(application_id, op)
        old = self._old(app)
        if op is None:
            active = self._active()
            require(active["active_baseline_id"] == app["baseline_id"], "Stale active baseline")
            previous = json.loads(canonical(self.adapter.read_effective_state()))
            require(previous == old.configuration["wave5_fake_policy"], "Starting state drift")
            intended = {"kind": "wave5_fake_policy", "treatment_id": app["treatment_id"],
                        "application_id": application_id}
            self.adapter.validate_intended_state(json.loads(canonical(intended)))
            op = {"schema_version": 1, "operation_id": "OP-" + digest(application_id),
                  "application": app, "approval": proof, "old_pointer": active,
                  "old_snapshot": old.to_dict(), "previous_state": previous,
                  "intended_state": intended, "phase": "INTENT"}
            self._save(op)
        require(old.to_dict() == op["old_snapshot"], "Old baseline content changed")
        if op["phase"] == "COMPLETED":
            return op
        require(op["phase"] in ("INTENT", "APPLIED", "VERIFIED", "SNAPSHOT_SAVED"),
                "Ambiguous/failed operation: do not repeat mutation")
        if op["phase"] == "INTENT":
            self.failpoint("before_mutation")
            self._authorize(application_id, op)
            require(self._old(app).to_dict() == op["old_snapshot"], "Old baseline content changed")
            require(self._active() == op["old_pointer"], "Stale baseline before mutation")
            require(self.adapter.read_effective_state() == op["previous_state"], "Starting state drift")
            self.adapter.validate_intended_state(json.loads(canonical(op["intended_state"])))
            op["phase"] = "APPLYING"
            self._save(op)
            try:
                self.adapter.apply_effective_state(json.loads(canonical(op["intended_state"])))
            except Exception as exc:
                op.update(phase="APPLY_FAILED", error=str(exc))
                self._save(op)
                raise
            op["phase"] = "APPLIED"
            self._save(op)
            self.failpoint("after_mutation")
        if op["phase"] == "APPLIED":
            actual = self.adapter.read_effective_state()
            if canonical(actual) != canonical(op["intended_state"]):
                op.update(phase="VERIFY_FAILED", readback=actual)
                self._save(op)
                raise ValueError("Incorrect readback; not deployed or verified")
            op["verification"] = {"actual": actual, "intended_hash": digest(op["intended_state"]),
                                  "actual_hash": digest(actual)}
            op["phase"] = "VERIFIED"
            self._save(op)
            self.failpoint("after_verification")
        return self._finalize(op)

    def _finalize(self, op):
        from research_engine.v10.baselines.models import BaselineSnapshot
        from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder

        require(op["verification"]["actual"] == op["intended_state"], "Invalid verification evidence")
        require(op["verification"]["actual_hash"] == digest(op["verification"]["actual"]),
                "Invalid verification actual hash")
        require(op["verification"]["intended_hash"] == digest(op["intended_state"]),
                "Invalid verification intended hash")
        require(self.adapter.read_effective_state() == op["intended_state"], "Verified state drift")
        require(op["verification"]["actual"] == op["intended_state"], "Invalid verification evidence")
        self._transition(op, "DEPLOYED")
        self._transition(op, "VERIFIED")
        if "snapshot" not in op:
            snapshot = SnapshotBuilder.from_verified_fake_state(
                BaselineSnapshot.from_dict(op["old_snapshot"]),
                op["verification"]["actual"], op["operation_id"])
            op["snapshot"] = snapshot.to_dict()
            self._save(op)  # Freeze exact content before writing the registry.
        snapshot = BaselineSnapshot.from_dict(op["snapshot"])
        if self.registry.exists(snapshot.snapshot_id):
            loaded = self.registry.load(snapshot.snapshot_id)
            require(loaded is not None and loaded.to_dict() == op["snapshot"], "Snapshot content conflict")
        else:
            self.registry.save(snapshot)
        op["phase"] = "SNAPSHOT_SAVED"
        self._save(op)
        self.failpoint("after_snapshot_save")
        require(self.registry.load(snapshot.snapshot_id).to_dict() == op["snapshot"], "Snapshot reload conflict")
        active = self._active()
        if active != op["old_pointer"]:
            require(active["active_baseline_id"] == snapshot.snapshot_id
                    and active["previous_baseline_id"] == op["application"]["baseline_id"]
                    and active["reason"] == op["operation_id"]
                    and active["actor"] == "wave5:fake_executor", "Independent baseline transition")
        else:
            authority.set_active(snapshot.snapshot_id, actor="wave5:fake_executor",
                                 reason=op["operation_id"], registry=self.registry,
                                 pointer_file=self.pointer_file)
        self.failpoint("after_activation")
        op["activated_pointer"] = self._active()
        op["phase"] = "COMPLETED"
        self._save(op)
        return op

    def rollback(self, application_id):
        """Restore a completed fake deployment, never an unrelated active policy.

        An interrupted setter is deliberately not replayed automatically. Once
        restoration evidence is durable, pointer/ledger completion is resumable.
        """
        op = self.get_operation(application_id)
        require(op is not None, "No operation to roll back")
        app, _ = self._authorize(application_id, op)
        require(self._old(app).to_dict() == op["old_snapshot"], "Old baseline changed")
        if op["phase"] == "ROLLED_BACK":
            return op
        require(op["phase"] in ("COMPLETED", "RESTORED"), "Rollback requires completed deployment or verified restoration")
        if op["phase"] == "COMPLETED":
            require(self._active() == op["activated_pointer"], "Independent baseline transition")
            require(self.adapter.read_effective_state() == op["intended_state"], "Rollback starting state drift")
            op["phase"] = "RESTORING"
            self._save(op)
            try:
                self.adapter.restore_effective_state(json.loads(canonical(op["previous_state"])))
                actual = json.loads(canonical(self.adapter.read_effective_state()))
                require(canonical(actual) == canonical(op["previous_state"]), "Restoration readback mismatch")
            except Exception as exc:
                op.update(phase="ROLLBACK_FAILED", error=str(exc))
                self._save(op)
                raise
            op["restoration"] = {"actual": actual, "actual_hash": digest(actual)}
            op["phase"] = "RESTORED"
            self._save(op)
        require(self.adapter.read_effective_state() == op["previous_state"], "Restored state drift")
        active = self._active()
        reason = "rollback:" + op["operation_id"]
        if active == op["activated_pointer"]:
            authority.set_active(app["baseline_id"], actor="wave5:fake_executor", reason=reason,
                                 registry=self.registry, pointer_file=self.pointer_file)
        else:
            require(active["active_baseline_id"] == app["baseline_id"]
                    and active["previous_baseline_id"] == op["snapshot"]["snapshot_id"]
                    and active["actor"] == "wave5:fake_executor" and active["reason"] == reason,
                    "Independent baseline transition during restoration")
        self._transition(op, "ROLLED_BACK")
        op["phase"] = "ROLLED_BACK"
        self._save(op)
        return op
