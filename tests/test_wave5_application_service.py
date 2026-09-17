"""Wave 5.2: all execution and persistence isolated under tmp_path."""
import builtins
import inspect
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from research_engine.control_plane.application_ledger import ApplicationLedger
from research_engine.control_plane.application_service import ApplicationService
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation
from research_engine.lifecycle.candidate_recommendation import RecommendationStore, create_recommendation
from research_engine.v10.baselines import baseline_authority as authority
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.candidates.candidate_decision import record_human_decision
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord


class Crash(BaseException):
    pass


class PersistentFake:
    """Only adapter implementation: fixture-local files, never live services."""
    def __init__(self, path, mode="success", restore_mode="success"):
        self.path, self.mode, self.restore_mode = path, mode, restore_mode
        self.before_apply = lambda: None

    def data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def read_effective_state(self):
        return self.data()["state"]

    def validate_intended_state(self, intended):
        if not isinstance(intended, dict) or intended.get("kind") != "wave5_fake_policy":
            raise ValueError("unsupported fake policy")

    def apply_effective_state(self, intended):
        self.before_apply()
        data = self.data()
        data["applies"] += 1
        if self.mode in ("success", "crash"):
            data["state"] = intended
        elif self.mode == "partial":
            data["state"] = {"partial": True}
        self.write(data)
        if self.mode == "raise":
            raise RuntimeError("mutation exception")
        if self.mode == "crash":
            raise Crash("inside setter after write")

    def restore_effective_state(self, previous):
        data = self.data()
        data["restores"] += 1
        if self.restore_mode == "success":
            data["state"] = previous
        self.write(data)
        if self.restore_mode == "raise":
            raise RuntimeError("restore failed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dirs = {name: str(tmp_path / name) for name in (
        "registry_dir", "decisions_dir", "recommendations_dir",
    )}
    baselines = tmp_path / "baselines"
    pointer = baselines / "active_baseline.json"
    monkeypatch.setattr(authority, "_BASELINES_DIR", str(baselines))
    monkeypatch.setattr(authority, "_ACTIVE_POINTER_FILE", str(pointer))
    monkeypatch.setattr("core.research_events.compute_config_hash", lambda: "old-config")
    previous = {"kind": "wave5_fake_policy", "treatment_id": "incumbent"}
    reg = SnapshotRegistry(str(baselines))
    reg.save(BaselineSnapshot(snapshot_id="OLD", config_hash="old-config",
                             configuration={"wave5_fake_policy": previous}))
    authority.set_active("OLD", actor="test", reason="fixture")
    candidates = CandidateRegistry(dirs["registry_dir"])
    candidates.create(CandidateRecord(
        candidate_id="C1", baseline_id="OLD", created_from_question="E1",
        status="READY_FOR_REVIEW", change_definition={"baseline_config_hash": "old-config"},
    ))
    candidates.add_validation_result("C1", "E1", "IMPROVED", confidence="HIGH", sample_size=60)
    evaluation = CandidateEvaluation(
        candidate_id="C1", evaluation_id="E1", treatment_id="historical-treatment",
        baseline_id="OLD", config_hash="old-config", decision="VALIDATED",
        confidence="HIGH", eligible_pairs=60, survives_outlier_removal=True,
    )
    from research_engine.lifecycle.treatment_provenance import canonical_spec
    evaluation.treatment_spec = canonical_spec({
        "change_type": "direction_inversion", "declared": {},
        "scope": {"symbols": ["EURUSD"], "patterns": None},
        "treatment_id": evaluation.treatment_id,
    })
    create_recommendation(evaluation, store=RecommendationStore(dirs["recommendations_dir"]))
    evaluations = tmp_path / "evaluations"
    monkeypatch.setattr("research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR", evaluations)
    evaluations.mkdir()
    (evaluations / "C1.jsonl").write_text(json.dumps(evaluation.to_dict()) + "\n", encoding="utf-8")
    ledger = ApplicationLedger(tmp_path / "applications.jsonl")
    adapter_path = tmp_path / "fake.json"
    adapter_path.write_text(json.dumps({"state": previous, "applies": 0, "restores": 0}), encoding="utf-8")

    class Environment:
        def approve(self):
            record_human_decision("C1", "ACCEPT", "REC-E1", actor="human", reason="reviewed", **dirs)
            return ledger.create_application_from_approval("C1", "REC-E1", **dirs)

        def service(self, failpoint=None, mode="success", restore_mode="success"):
            return ApplicationService(
                adapter=PersistentFake(adapter_path, mode, restore_mode),
                application_path=tmp_path / "applications.jsonl", **dirs,
                evaluations_dir=evaluations, operations_dir=tmp_path / "operations",
                baselines_dir=baselines, pointer_file=pointer, failpoint=failpoint,
            )

    e = Environment()
    e.root, e.dirs, e.ledger, e.reg = tmp_path, dirs, ledger, reg
    e.previous, e.adapter_path = previous, adapter_path
    e.app_id = "APP-C1-REC-E1"
    return e


def test_legacy_provenance_cannot_execute(env):
    env.approve()
    service = env.service()
    for path in (service.application_path, service.decisions_dir / "decisions.jsonl",
                 service.recommendations_dir / "recommendations.jsonl", service.evaluations_dir / "C1.jsonl"):
        row = json.loads(path.read_text(encoding="utf-8"))
        row.pop("treatment_spec", None)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert env.ledger.list_all()[0].treatment_spec is None
    pointer = service.pointer_file.read_bytes()
    with pytest.raises(ValueError, match="treatment_spec"):
        service.execute(env.app_id)
    assert service.adapter.data()["applies"] == 0
    assert service.pointer_file.read_bytes() == pointer
    assert service.get_operation(env.app_id) is None


@pytest.mark.parametrize("source", ["application", "decision", "recommendation", "evaluation"])
def test_same_id_scope_substitution_blocks_execution(env, source):
    from research_engine.lifecycle.treatment_provenance import canonical_spec
    env.approve()
    service = env.service()
    paths = {"application": service.application_path,
             "decision": service.decisions_dir / "decisions.jsonl",
             "recommendation": service.recommendations_dir / "recommendations.jsonl",
             "evaluation": service.evaluations_dir / "C1.jsonl"}
    original = json.loads(paths[source].read_text(encoding="utf-8"))
    spec = json.loads(original["treatment_spec"])
    spec["scope"] = {"symbols": ["GBPUSD"], "patterns": None}
    rewrite_row(paths[source], treatment_spec=canonical_spec(spec))
    pointer = service.pointer_file.read_bytes()
    ledger = service.application_path.read_bytes()
    with pytest.raises(ValueError, match="treatment_spec"):
        service.execute(env.app_id)
    assert service.adapter.data()["applies"] == 0
    assert service.pointer_file.read_bytes() == pointer
    assert service.application_path.read_bytes() == ledger
    assert service.get_operation(env.app_id) is None


def test_candidate_mutation_does_not_supply_scope(env, monkeypatch):
    app = env.approve()
    service = env.service()
    candidate = CandidateRegistry(str(service.registry_dir)).get("C1")
    candidate.change_definition = {"type": "geometry_modification", "stop_multiplier": 9,
                                   "scope": {"symbols": ["GBPUSD"]}}
    monkeypatch.setattr(CandidateRegistry, "get", lambda self, cid: candidate)
    op = service.execute(env.app_id)
    assert op["intended_state"]["treatment_spec"] == app.treatment_spec
    assert json.loads(app.treatment_spec)["scope"]["symbols"] == ["EURUSD"]
    assert all(row.treatment_spec == app.treatment_spec for row in env.ledger.list_all())


@pytest.mark.parametrize("boundary", ["decision", "application"])
def test_recommendation_scope_substitution_blocks_boundary(env, boundary):
    from research_engine.lifecycle.treatment_provenance import canonical_spec
    if boundary == "application":
        env.approve()
    service = env.service()
    path = service.recommendations_dir / "recommendations.jsonl"
    rec = json.loads(path.read_text(encoding="utf-8"))
    spec = json.loads(rec["treatment_spec"])
    spec["scope"]["symbols"] = ["GBPUSD"]
    rewrite_row(path, treatment_spec=canonical_spec(spec))
    with pytest.raises(ValueError, match="treatment_spec"):
        if boundary == "application":
            env.ledger.create_application_from_approval("C1", "REC-E1", **env.dirs)
        else:
            env.approve()
    assert service.adapter.data()["applies"] == 0


def test_atomic_operation_roundtrip(tmp_path):
    service = ApplicationService(
        adapter=PersistentFake(tmp_path / "fake.json"),
        application_path=tmp_path / "applications.jsonl",
        decisions_dir=tmp_path / "decisions", recommendations_dir=tmp_path / "recommendations",
        registry_dir=tmp_path / "candidates", evaluations_dir=tmp_path / "evaluations",
        operations_dir=tmp_path / "operations", baselines_dir=tmp_path / "baselines",
        pointer_file=tmp_path / "baselines" / "active_baseline.json",
    )
    op = {"application": {"application_id": "APP-test"}, "phase": "INTENT",
          "previous_state": {"nested": [1, 2]}, "intended_state": {"nested": [3]}}
    service._save(op)
    assert service.get_operation("APP-test") == op
    op["phase"] = "APPLYING"
    service._save(op)
    assert service.get_operation("APP-test") == op
    assert len(list((tmp_path / "operations").glob("*.json"))) == 1
    assert not list((tmp_path / "operations").glob("*.tmp"))


def test_valid_execution_and_idempotency(env):
    env.approve()
    op = env.service().execute(env.app_id)
    assert op["phase"] == "COMPLETED"
    assert op["previous_state"] == env.previous
    assert op["verification"]["actual"] == op["intended_state"]
    assert authority.get_active().active_baseline_id == op["snapshot"]["snapshot_id"]
    assert env.reg.load(op["snapshot"]["snapshot_id"]).configuration["wave5_fake_policy"] == op["intended_state"]
    assert env.service().execute(env.app_id) == op
    assert env.service().adapter.data()["applies"] == 1
    assert [r.state for r in env.ledger.list_all()] == ["APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED"]


def test_no_approval_no_mutation(env):
    with pytest.raises(ValueError):
        env.service().execute(env.app_id)
    assert env.service().adapter.data()["applies"] == 0


@pytest.mark.parametrize("point", ["before_mutation", "after_mutation", "after_verification", "after_snapshot_save", "after_activation"])
def test_restart(env, point):
    env.approve()
    def fail(name):
        if name == point:
            raise Crash(name)
    with pytest.raises(Crash):
        env.service(failpoint=fail).execute(env.app_id)
    saved = env.service().get_operation(env.app_id)
    op = env.service().execute(env.app_id)
    assert op["operation_id"] == saved["operation_id"]
    assert op["previous_state"] == env.previous
    assert op["phase"] == "COMPLETED"
    assert env.service().adapter.data()["applies"] == 1


def test_rollback(env):
    env.approve()
    env.service().execute(env.app_id)
    op = env.service().rollback(env.app_id)
    assert op["phase"] == "ROLLED_BACK"
    assert op["restoration"]["actual"] == env.previous
    assert env.service().adapter.read_effective_state() == env.previous
    assert authority.get_active().active_baseline_id == "OLD"
    assert env.ledger.list_all()[-1].state == "ROLLED_BACK"
    pointer = env.service().pointer_file.read_bytes()
    ledger = env.service().application_path.read_bytes()
    assert env.service().rollback(env.app_id) == op
    assert env.service().adapter.data()["restores"] == 1
    assert env.service().pointer_file.read_bytes() == pointer
    assert env.service().application_path.read_bytes() == ledger


@pytest.fixture
def activation_spy(monkeypatch):
    spy = Mock(wraps=authority.set_active)
    monkeypatch.setattr(authority, "set_active", spy)
    return spy


def rewrite_row(path, **changes):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    records[0].update(changes)
    path.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")


def assert_no_mutation(service, spy, pointer):
    assert service.adapter.data()["applies"] == 0
    assert service.adapter.data()["restores"] == 0
    assert service.pointer_file.read_bytes() == pointer
    spy.assert_not_called()


@pytest.mark.parametrize("evidence", [
    "raw", "REJECT", "BASELINE_BLOCKED", "RECOMMENDATION_BLOCKED", "STATUS_FAILED",
])
def test_application_row_cannot_replace_effective_accept(env, activation_spy, evidence):
    service = env.service()
    if evidence == "REJECT":
        record_human_decision("C1", "REJECT", "REC-E1", actor="human", reason="reviewed", **env.dirs)
    elif evidence != "raw":
        env.approve()
        rewrite_row(service.decisions_dir / "decisions.jsonl", outcome=evidence)
    if evidence in ("raw", "REJECT"):
        # Even a fully populated low-level application row is not authorization.
        env.ledger.append("C1", "", "APPROVED_NOT_DEPLOYED", application_id=env.app_id,
                          actor="human", reason="reviewed", recommendation_id="REC-E1",
                          evaluation_id="E1", treatment_id="historical-treatment",
                          baseline_id="OLD", baseline_config_hash="old-config",
                          human_decision_outcome="COMPLETED")
    pointer = service.pointer_file.read_bytes()
    with pytest.raises(ValueError, match="effective human decision|Human ACCEPT required"):
        service.execute(env.app_id)
    assert_no_mutation(service, activation_spy, pointer)
    assert service.get_operation(env.app_id) is None


@pytest.mark.parametrize("source, field", [
    ("application", "recommendation_id"),
    ("application", "evaluation_id"),
    ("application", "treatment_id"),
    ("application", "baseline_id"),
    ("application", "baseline_config_hash"),
    ("recommendation", "recommendation_id"),
    ("recommendation", "evaluation_id"),
    ("recommendation", "treatment_id"),
    ("recommendation", "baseline_id"),
    ("recommendation", "baseline_config_hash"),
    ("evaluation", "evaluation_id"),
    ("evaluation", "treatment_id"),
    ("evaluation", "baseline_id"),
    ("evaluation", "config_hash"),
])
def test_substituted_provenance_never_mutates(env, activation_spy, source, field):
    env.approve()
    service = env.service()
    paths = {"application": service.application_path,
             "recommendation": service.recommendations_dir / "recommendations.jsonl",
             "evaluation": service.evaluations_dir / "C1.jsonl"}
    rewrite_row(paths[source], **{field: "substituted"})
    pointer = service.pointer_file.read_bytes()
    with pytest.raises(ValueError, match="provenance mismatch|Noncanonical|Missing or ambiguous"):
        service.execute(env.app_id)
    assert_no_mutation(service, activation_spy, pointer)
    assert service.get_operation(env.app_id) is None


@pytest.mark.parametrize("drift", ["active", "config_hash", "starting_state"])
@pytest.mark.parametrize("timing", ["before_execute", "before_mutation"])
def test_staleness_fails_before_setter(env, activation_spy, drift, timing):
    env.approve()
    service = env.service()
    def change():
        if drift == "active":
            env.reg.save(BaselineSnapshot(snapshot_id="NEWER", config_hash="other"))
            authority.set_active("NEWER", actor="independent", reason="new baseline")
            activation_spy.reset_mock()
        elif drift == "config_hash":
            old = env.reg.load("OLD")
            old.config_hash = "drifted"
            env.reg.save(old)
        else:
            data = service.adapter.data()
            data["state"] = {"kind": "wave5_fake_policy", "treatment_id": "drifted"}
            service.adapter.write(data)
    if timing == "before_execute":
        change()
    else:
        service.failpoint = lambda point: change() if point == "before_mutation" else None
    with pytest.raises(ValueError, match="Stale|Starting state drift|Old baseline"):
        service.execute(env.app_id)
    assert service.adapter.data()["applies"] == 0
    assert service.adapter.data()["restores"] == 0
    activation_spy.assert_not_called()
    assert authority.get_active().active_baseline_id == ("NEWER" if drift == "active" else "OLD")


@pytest.mark.parametrize("mode, phase, error", [
    ("noop", "VERIFY_FAILED", "Incorrect readback"),
    ("partial", "VERIFY_FAILED", "Incorrect readback"),
    ("raise", "APPLY_FAILED", "mutation exception"),
])
def test_false_success_and_mutation_exception_never_activate(env, activation_spy, mode, phase, error):
    env.approve()
    service = env.service(mode=mode)
    pointer = service.pointer_file.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match=error):
        service.execute(env.app_id)
    op = service.get_operation(env.app_id)
    assert op["phase"] == phase
    assert "verification" not in op
    assert "snapshot" not in op
    assert [r.state for r in env.ledger.list_all()] == ["APPROVED_NOT_DEPLOYED"]
    assert env.reg.list_snapshots() == ["OLD"]
    assert service.pointer_file.read_bytes() == pointer
    activation_spy.assert_not_called()
    with pytest.raises(ValueError, match="Ambiguous/failed"):
        env.service().execute(env.app_id)
    assert service.adapter.data()["applies"] == 1
    activation_spy.assert_not_called()


def crash_at(point):
    def fail(name):
        if name == point:
            raise Crash(name)
    return fail


@pytest.mark.parametrize("damage", ["missing", "null", "actual", "actual_hash", "intended_hash"])
def test_invalid_durable_verification_never_activates(env, activation_spy, damage):
    env.approve()
    service = env.service(failpoint=crash_at("after_verification"))
    with pytest.raises(Crash):
        service.execute(env.app_id)
    op = service.get_operation(env.app_id)
    if damage == "missing":
        del op["verification"]
    elif damage == "null":
        op["verification"] = None
    else:
        op["verification"][damage] = {} if damage == "actual" else "invalid-hash"
    service._save(op)
    pointer = service.pointer_file.read_bytes()
    with pytest.raises((ValueError, KeyError, TypeError)):
        env.service().execute(env.app_id)
    activation_spy.assert_not_called()
    assert service.pointer_file.read_bytes() == pointer
    assert env.reg.list_snapshots() == ["OLD"]
    assert [r.state for r in env.ledger.list_all()] == ["APPROVED_NOT_DEPLOYED"]
    assert service.adapter.data()["applies"] == 1


def test_activation_observes_durable_verification_and_exact_snapshot(env, activation_spy):
    from research_engine.control_plane.application_service import digest
    from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder

    env.approve()
    service = env.service()
    real_set_active = activation_spy._mock_wraps
    def guarded_activation(snapshot_id, **kwargs):
        op = service.get_operation(env.app_id)
        assert op["phase"] == "SNAPSHOT_SAVED"
        assert op["verification"]["actual"] == service.adapter.read_effective_state()
        assert op["verification"]["actual_hash"] == digest(op["verification"]["actual"])
        assert env.ledger.list_all()[-1].state == "VERIFIED"
        expected = SnapshotBuilder.from_verified_fake_state(
            BaselineSnapshot.from_dict(op["old_snapshot"]),
            op["verification"]["actual"], op["operation_id"])
        assert env.reg.load(snapshot_id).to_dict() == expected.to_dict() == op["snapshot"]
        return real_set_active(snapshot_id, **kwargs)
    activation_spy.side_effect = guarded_activation
    def observe(point):
        if point in ("before_mutation", "after_mutation", "after_verification", "after_snapshot_save"):
            activation_spy.assert_not_called()
            assert authority.get_active().active_baseline_id == "OLD"
    service.failpoint = observe
    op = service.execute(env.app_id)
    activation_spy.assert_called_once()
    assert env.service().execute(env.app_id)["snapshot"] == op["snapshot"]
    activation_spy.assert_called_once()


def test_verified_policy_changes_snapshot_identity(env):
    from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder

    env.approve()
    op = env.service().execute(env.app_id)
    old = env.reg.load("OLD")
    exact = SnapshotBuilder.from_verified_fake_state(old, op["verification"]["actual"], op["operation_id"])
    changed = dict(op["verification"]["actual"], treatment_id="different-fake-policy")
    different = SnapshotBuilder.from_verified_fake_state(old, changed, op["operation_id"])
    assert exact.to_dict() == op["snapshot"]
    assert different.snapshot_id != exact.snapshot_id
    assert different.identity_hash != exact.identity_hash
    assert different.config_hash != exact.config_hash
    assert old.configuration["wave5_fake_policy"] == env.previous


def test_snapshot_identity_collision_preserves_existing_content(env, activation_spy):
    env.approve()
    service = env.service(failpoint=crash_at("after_snapshot_save"))
    with pytest.raises(Crash):
        service.execute(env.app_id)
    op = service.get_operation(env.app_id)
    snapshot = env.reg.load(op["snapshot"]["snapshot_id"])
    snapshot.configuration["wave5_fake_policy"] = {"conflicting": True}
    env.reg.save(snapshot)
    conflict = snapshot.to_dict()
    pointer = service.pointer_file.read_bytes()
    with pytest.raises(ValueError, match="Snapshot content conflict"):
        env.service().execute(env.app_id)
    assert env.reg.load(snapshot.snapshot_id).to_dict() == conflict
    assert service.pointer_file.read_bytes() == pointer
    activation_spy.assert_not_called()
    assert service.adapter.data()["applies"] == 1


@pytest.mark.parametrize("mode", ["raise", "noop"])
def test_rollback_failure_is_not_rolled_back(env, activation_spy, mode):
    env.approve()
    service = env.service(restore_mode=mode)
    deployed = service.execute(env.app_id)
    activation_spy.reset_mock()
    pointer = service.pointer_file.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match="restore failed|Restoration readback mismatch"):
        service.rollback(env.app_id)
    op = service.get_operation(env.app_id)
    assert op["phase"] == "ROLLBACK_FAILED"
    assert "restoration" not in op
    assert env.ledger.list_all()[-1].state == "VERIFIED"
    assert service.pointer_file.read_bytes() == pointer
    assert authority.get_active().active_baseline_id == deployed["snapshot"]["snapshot_id"]
    activation_spy.assert_not_called()
    with pytest.raises(ValueError, match="Rollback requires"):
        env.service().rollback(env.app_id)
    assert service.adapter.data()["restores"] == 1
    activation_spy.assert_not_called()


@pytest.mark.parametrize("timing", ["before_rollback", "during_restoration"])
def test_rollback_never_overwrites_independent_baseline(env, activation_spy, monkeypatch, timing):
    env.approve()
    service = env.service()
    service.execute(env.app_id)
    def independent_activation():
        env.reg.save(BaselineSnapshot(snapshot_id="NEWER", config_hash="independent"))
        authority.set_active("NEWER", actor="independent", reason="unrelated activation")
        activation_spy.reset_mock()
    if timing == "before_rollback":
        independent_activation()
    else:
        restore = service.adapter.restore_effective_state
        def intervening_restore(previous):
            restore(previous)
            independent_activation()
        monkeypatch.setattr(service.adapter, "restore_effective_state", intervening_restore)
    with pytest.raises(ValueError, match="Independent baseline transition"):
        service.rollback(env.app_id)
    pointer = service.pointer_file.read_bytes()
    assert authority.get_active().active_baseline_id == "NEWER"
    assert service.get_operation(env.app_id)["phase"] != "ROLLED_BACK"
    assert env.ledger.list_all()[-1].state == "VERIFIED"
    assert service.adapter.data()["restores"] == (0 if timing == "before_rollback" else 1)
    activation_spy.assert_not_called()
    with pytest.raises(ValueError, match="Independent baseline transition"):
        env.service().rollback(env.app_id)
    assert service.pointer_file.read_bytes() == pointer
    activation_spy.assert_not_called()


def test_injected_fake_only_and_no_live_collection(env, monkeypatch):
    from research_engine.control_plane import production_adapter
    from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder

    assert inspect.signature(ApplicationService).parameters["adapter"].default is inspect.Parameter.empty
    assert production_adapter.PolicyAdapter._is_protocol
    classes = [value for value in vars(production_adapter).values()
               if inspect.isclass(value) and value.__module__ == production_adapter.__name__]
    assert classes == [production_adapter.PolicyAdapter]
    with pytest.raises(TypeError, match="Protocols cannot be instantiated"):
        production_adapter.PolicyAdapter()
    env.approve()
    service = env.service()
    assert type(service.adapter) is PersistentFake
    for path in (service.application_path, service.decisions_dir, service.recommendations_dir,
                 service.registry_dir, service.evaluations_dir, service.operations_dir,
                 service.pointer_file, service.registry._dir, service.adapter.path):
        assert path.is_relative_to(env.root)
    forbidden = Mock(side_effect=AssertionError("live collection forbidden"))
    monkeypatch.setattr(SnapshotBuilder, "build", forbidden)
    for name in vars(SnapshotBuilder):
        if name.startswith("_collect_") or name == "_load_universe":
            monkeypatch.setattr(SnapshotBuilder, name, forbidden)
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert not any(token in name.lower() for token in ("metatrader", "mt5", "broker", "execution", "core.config"))
        assert not (name == "core" and "config" in kwargs.get("fromlist", ()))
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    service.execute(env.app_id)
    service.rollback(env.app_id)
    forbidden.assert_not_called()

