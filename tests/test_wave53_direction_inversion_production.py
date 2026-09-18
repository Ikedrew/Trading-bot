"""Wave 5.3 production direction_inversion suite (part 1/5)."""
import json
from pathlib import Path

import pytest

from core import optimisation_policy as policy
from core.optimisation_policy import (
    effective_identity,
    in_scope,
    normal_state,
    read_policy_file,
    validate_effective_state,
    write_policy_file,
)
from research_engine.lifecycle.treatment_provenance import canonical_spec


def make_spec(scope, treatment_id="historical-treatment"):
    return canonical_spec({
        "change_type": "direction_inversion", "declared": {},
        "scope": scope, "treatment_id": treatment_id,
    })


class TestAPolicyAuthority:
    def test_normal_representation(self):
        assert normal_state() == {"kind": "normal"}
        assert policy.canonical(normal_state()) == '{"kind":"normal"}'

    def test_inversion_representation(self):
        spec = make_spec({"symbols": ["EURUSD"], "patterns": None})
        st = policy.direction_inversion_state(
            treatment_id="historical-treatment", treatment_spec=spec,
            application_id="APP-X", candidate_id="C1")
        assert st["treatment_spec"] == spec
        assert validate_effective_state(st) == st

    def test_rejects_unsupported(self):
        with pytest.raises(ValueError):
            validate_effective_state({"kind": "geometry_modification"})
        with pytest.raises(ValueError):
            validate_effective_state({"kind": "normal", "extra": 1})
        with pytest.raises(ValueError):
            validate_effective_state({"kind": "direction_inversion"})
        bad = canonical_spec({"change_type": "geometry_modification",
                              "declared": {"stop_multiplier": 2.0},
                              "scope": {"symbols": None, "patterns": None},
                              "treatment_id": "t"})
        with pytest.raises(ValueError):
            validate_effective_state({"kind": "direction_inversion",
                                      "treatment_id": "t", "treatment_spec": bad})

    def test_missing_file_is_normal(self, tmp_path):
        assert read_policy_file(tmp_path / "absent.json") == {"kind": "normal"}

    def test_malformed_file_fails_closed(self, tmp_path):
        p = tmp_path / "pol.json"
        p.write_text("{nope", encoding="utf-8")
        with pytest.raises(ValueError):
            read_policy_file(p)

    def test_atomic_write_readback(self, tmp_path):
        p = tmp_path / "pol.json"
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        assert write_policy_file(st, p) == st
        assert read_policy_file(p) == st
        assert not list(tmp_path.glob("*.tmp"))

import inspect


class TestBAdapter:
    def test_exact_signatures(self):
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        assert list(inspect.signature(A.read_effective_state).parameters) == ["self"]
        assert list(inspect.signature(A.validate_intended_state).parameters) == ["self", "intended"]
        assert list(inspect.signature(A.apply_effective_state).parameters) == ["self", "intended"]
        assert list(inspect.signature(A.restore_effective_state).parameters) == ["self", "previous"]

    def test_roundtrip(self, tmp_path):
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        ad = A(tmp_path / "p.json")
        assert ad.read_effective_state() == {"kind": "normal"}
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        ad.validate_intended_state(st)
        ad.apply_effective_state(st)
        assert ad.read_effective_state() == st
        assert A(tmp_path / "p.json").read_effective_state() == st
        ad.restore_effective_state({"kind": "normal"})
        assert ad.read_effective_state() == {"kind": "normal"}

    def test_rejects_fake_and_bad(self, tmp_path):
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        ad = A(tmp_path / "p.json")
        with pytest.raises(ValueError):
            ad.validate_intended_state({"kind": "wave5_fake_policy"})
        with pytest.raises(ValueError):
            ad.validate_intended_state({"kind": "direction_inversion"})
        with pytest.raises(ValueError):
            ad.validate_intended_state({"kind": "normal", "extra": 1})

    def test_no_broker_text(self):
        for rel in ("research_engine/control_plane/direction_inversion_adapter.py",
                    "core/optimisation_policy.py"):
            src = Path(rel).read_text(encoding="utf-8").lower()
            assert "metatrader" not in src and "place_order" not in src
            assert "order_send" not in src and "auto_deploy" not in src

# __PART3__
SCOPE_CASES = [
    ({"symbols": ["EURUSD"], "patterns": None}, "EURUSD", "TBC", True),
    ({"symbols": ["EURUSD"], "patterns": None}, "GBPUSD", "TBC", False),
    ({"symbols": None, "patterns": ["TBC"]}, "EURUSD", "TBC", True),
    ({"symbols": None, "patterns": ["TBC"]}, "EURUSD", "OTHER", False),
    ({"symbols": ["EURUSD"], "patterns": ["TBC"]}, "EURUSD", "TBC", True),
    ({"symbols": ["EURUSD"], "patterns": ["TBC"]}, "EURUSD", "OTHER", False),
    ({"symbols": ["EURUSD"], "patterns": ["TBC"]}, "GBPUSD", "TBC", False),
    ({"symbols": None, "patterns": None}, "ANY", "ANY", True),
]


class TestDScope:
    @pytest.mark.parametrize("scope,symbol,pattern,expected", SCOPE_CASES)
    def test_matrix(self, scope, symbol, pattern, expected):
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec(scope)}
        assert in_scope(state=st, symbol=symbol, pattern=pattern)[0] is expected

    def test_case_sensitive(self):
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        assert in_scope(state=st, symbol="eurusd", pattern="TBC")[0] is False


class TestEReference:
    def test_reference_math(self):
        from research_engine.lifecycle.direction_inversion_geometry import (
            canonical_treatment_reference_entry as ref,
        )

        assert ref(1.10000, 1.10020) == pytest.approx(1.10010)

    def test_no_second_tick(self):
        assert "last_tick" not in Path("core/optimisation_policy.py").read_text()


class TestFRuntime:
    def _intent(self, **kw):
        from risk.models import OrderIntent
        from strategy.signals import Side

        base = dict(symbol="EURUSD", side=Side.BUY, volume=0.5,
                    entry_reference=1.10, sl=1.098, tp=1.106, pattern="TBC")
        base.update(kw)
        return OrderIntent(**base)

    def test_normal_passthrough(self):
        from core.optimisation_policy import apply_production_intent

        oi = self._intent()
        out, applied = apply_production_intent(
            intent=oi, symbol="EURUSD", treatment_reference_entry=1.10010,
            policy_state={"kind": "normal"})
        assert out is oi and applied is False

    def test_out_of_scope(self):
        from core.optimisation_policy import apply_production_intent

        oi = self._intent(symbol="GBPUSD")
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        out, applied = apply_production_intent(
            intent=oi, symbol="GBPUSD", treatment_reference_entry=1.10, policy_state=st)
        assert out is oi and applied is False

    def test_in_scope(self):
        from core.optimisation_policy import apply_production_intent
        from strategy.signals import Side

        oi = self._intent(risk_id="R1", metadata={"k": "v"})
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        out, applied = apply_production_intent(
            intent=oi, symbol="EURUSD", treatment_reference_entry=1.10010, policy_state=st)
        assert applied and out is not oi and out.side == Side.SELL
        assert out.volume == 0.5 and out.risk_id == "R1" and out.metadata == {"k": "v"}
        assert out.entry_reference == 1.10

    def test_missing_reference_fails(self):
        from core.optimisation_policy import apply_production_intent

        oi = self._intent()
        st = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
              "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        for bad in (None, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                apply_production_intent(intent=oi, symbol="EURUSD",
                                        treatment_reference_entry=bad, policy_state=st)


class TestGFidelity:
    def test_example(self):
        from core.optimisation_policy import apply_direction_inversion
        from research_engine.lifecycle.candidate_shadow_hook import resolve_candidate_treatment
        from research_engine.lifecycle.direction_inversion_geometry import (
            canonical_direction_inversion as geo,
        )

        g = geo(incumbent_direction="BUY", reference_entry=1.10010, incumbent_stop=1.09800)
        assert g.risk_distance == pytest.approx(0.00210)
        assert (g.inverted_direction, g.stop, g.target) == (
            "SELL", pytest.approx(1.10220), pytest.approx(1.09380))
        res, _ = resolve_candidate_treatment(
            change_definition={"type": "direction_inversion"}, direction="BUY",
            entry_price=1.10010, stop_loss=1.09800, take_profit=1.10,
            risk_distance=0.00210, symbol="EURUSD", pattern="TBC")
        prod = apply_direction_inversion(
            policy_state={"kind": "direction_inversion", "treatment_id": "historical-treatment",
                          "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})},
            symbol="EURUSD", pattern="TBC", incumbent_direction="BUY",
            incumbent_stop=1.09800, treatment_reference_entry=1.10010)
        assert prod["direction"] == res.params["direction"] == "SELL"
        assert prod["stop_loss"] == pytest.approx(res.params["stop_loss"])
        assert prod["take_profit"] == pytest.approx(res.params["take_profit"])

# __PART4__
@pytest.fixture
def rdirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = {n: str(tmp_path / n) for n in ("registry_dir", "decisions_dir", "recommendations_dir")}
    baselines = tmp_path / "baselines"
    pointer = baselines / "active_baseline.json"
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._BASELINES_DIR", str(baselines))
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._ACTIVE_POINTER_FILE", str(pointer))
    monkeypatch.setattr("core.research_events.compute_config_hash", lambda: "old-config")
    from research_engine.v10.baselines import baseline_authority as auth
    from research_engine.v10.baselines.models import BaselineSnapshot as BS
    from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry as SR

    reg = SR(str(baselines))
    normal = {"kind": "normal"}
    reg.save(BS(snapshot_id="OLD", config_hash="old-config",
                configuration={"optimisation_policy": normal}))
    auth.set_active("OLD", actor="test", reason="fixture")
    ppath = tmp_path / "policy.json"
    write_policy_file(normal, ppath)
    return {"tmp": tmp_path, "dirs": d, "baselines": baselines, "pointer": pointer,
            "reg": reg, "policy_path": ppath}


def approve(rdirs, scope, cid="C1", eid="E1", tid="historical-treatment"):
    from research_engine.control_plane.application_ledger import ApplicationLedger as AL
    from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation as CE
    from research_engine.lifecycle.candidate_recommendation import (
        RecommendationStore as RS,
        create_recommendation as cr,
    )
    from research_engine.v10.candidates.candidate_decision import record_human_decision as rh
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry as CR
    from research_engine.v10.candidates.models import CandidateRecord as Rec

    d, tmp = rdirs["dirs"], rdirs["tmp"]
    cands = CR(d["registry_dir"])
    cands.create(Rec(candidate_id=cid, baseline_id="OLD", created_from_question="E1",
                     status="READY_FOR_REVIEW",
                     change_definition={"baseline_config_hash": "old-config"}))
    cands.add_validation_result(cid, eid, "IMPROVED", confidence="HIGH", sample_size=60)
    ev = CE(candidate_id=cid, evaluation_id=eid, treatment_id=tid,
            baseline_id="OLD", config_hash="old-config", decision="VALIDATED",
            confidence="HIGH", eligible_pairs=60, survives_outlier_removal=True)
    ev.treatment_spec = make_spec(scope, tid)
    cr(ev, store=RS(d["recommendations_dir"]))
    ed = tmp / "evaluations"
    ed.mkdir(exist_ok=True)
    (ed / f"{cid}.jsonl").write_text(json.dumps(ev.to_dict()) + "\n", encoding="utf-8")
    ledger = AL(tmp / "applications.jsonl")
    rh(cid, "ACCEPT", f"REC-{eid}", actor="human", reason="reviewed",
       registry_dir=d["registry_dir"], decisions_dir=d["decisions_dir"],
       recommendations_dir=d["recommendations_dir"], evaluations_dir=str(ed))
    return ledger, ledger.create_application_from_approval(
        cid, f"REC-{eid}", registry_dir=d["registry_dir"],
        decisions_dir=d["decisions_dir"], recommendations_dir=d["recommendations_dir"],
        evaluations_dir=str(ed))


def rservice(rdirs):
    from research_engine.control_plane.application_service import ApplicationService as AS
    from research_engine.control_plane.direction_inversion_adapter import (
        DirectionInversionPolicyAdapter as A,
    )

    tmp = rdirs["tmp"]
    return AS(adapter=A(rdirs["policy_path"]),
              application_path=tmp / "applications.jsonl", **rdirs["dirs"],
              evaluations_dir=tmp / "evaluations", operations_dir=tmp / "operations",
              baselines_dir=rdirs["baselines"], pointer_file=rdirs["pointer"])


class TestCTranslation:
    def test_frozen_authority(self, rdirs):
        from core.optimisation_policy import intended_state_from_approval

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        intended = intended_state_from_approval(application={
            "treatment_id": app.treatment_id, "treatment_spec": app.treatment_spec,
            "application_id": app.application_id, "candidate_id": app.candidate_id})
        assert intended["treatment_spec"] == app.treatment_spec

    def test_mutable_candidate_ignored(self, rdirs, monkeypatch):
        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry as CR

        cand = CR(str(svc.registry_dir)).get("C1")
        cand.change_definition = {"type": "geometry_modification",
                                  "stop_multiplier": 9.0, "scope": {"symbols": ["GBPUSD"]}}
        monkeypatch.setattr(CR, "get", lambda self, cid: cand)
        op = svc.execute(app.application_id)
        assert op["intended_state"]["treatment_spec"] == app.treatment_spec

# __PART5__
class TestHBaselineIdentity:
    def test_splits(self, rdirs):
        from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder as SB

        old = rdirs["reg"].load("OLD")
        eur = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
               "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})}
        gbp = {"kind": "direction_inversion", "treatment_id": "historical-treatment",
               "treatment_spec": make_spec({"symbols": ["GBPUSD"], "patterns": None})}
        n1 = SB.from_verified_real_state(old, {"kind": "normal"}, "OP-N")
        se = SB.from_verified_real_state(old, eur, "OP-E")
        sg = SB.from_verified_real_state(old, gbp, "OP-G")
        assert se.configuration["optimisation_policy"] == eur
        assert n1.identity_hash != se.identity_hash != sg.identity_hash
        assert n1.config_hash != se.config_hash


class TestIEndToEnd:
    def test_verified_n1(self, rdirs):
        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        assert read_policy_file(rdirs["policy_path"]) == {"kind": "normal"}
        op = svc.execute(app.application_id)
        assert op["phase"] == "COMPLETED"
        assert read_policy_file(rdirs["policy_path"]) == op["intended_state"]
        assert op["verification"]["actual"] == op["intended_state"]
        assert rdirs["reg"].load(
            op["snapshot"]["snapshot_id"]).configuration["optimisation_policy"] == op["intended_state"]
        from research_engine.v10.baselines import baseline_authority as auth

        assert auth.get_active().active_baseline_id == op["snapshot"]["snapshot_id"]

    def test_creation_zero_mutation(self, rdirs):
        before = Path(rdirs["policy_path"]).read_bytes()
        approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        assert Path(rdirs["policy_path"]).read_bytes() == before

# __PART6__
class TestJRestart:
    def test_restart(self, rdirs):
        from core.optimisation_policy import apply_production_intent, effective_identity
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )
        from risk.models import OrderIntent
        from strategy.signals import Side

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        intended = rservice(rdirs).execute(app.application_id)["intended_state"]
        assert A(rdirs["policy_path"]).read_effective_state() == intended
        assert effective_identity(A(rdirs["policy_path"]).read_effective_state()) == \
            effective_identity(intended)
        oi_in = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.5,
                            entry_reference=1.10, sl=1.098, tp=1.106, pattern="TBC")
        oi_out = OrderIntent(symbol="GBPUSD", side=Side.BUY, volume=0.5,
                             entry_reference=1.10, sl=1.098, tp=1.106, pattern="TBC")
        a, fa = apply_production_intent(intent=oi_in, symbol="EURUSD",
                                        treatment_reference_entry=1.10010,
                                        policy_path=rdirs["policy_path"])
        b, fb = apply_production_intent(intent=oi_out, symbol="GBPUSD",
                                        treatment_reference_entry=1.10,
                                        policy_path=rdirs["policy_path"])
        assert fa and a.side == Side.SELL
        assert (not fb) and b is oi_out


class TestKRollback:
    def test_rollback(self, rdirs):
        from core.optimisation_policy import apply_production_intent
        from risk.models import OrderIntent
        from strategy.signals import Side

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        svc.execute(app.application_id)
        assert svc.rollback(app.application_id)["phase"] == "ROLLED_BACK"
        assert read_policy_file(rdirs["policy_path"]) == {"kind": "normal"}
        from research_engine.v10.baselines import baseline_authority as auth

        assert auth.get_active().active_baseline_id == "OLD"
        oi = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.5,
                         entry_reference=1.10, sl=1.098, tp=1.106, pattern="TBC")
        out, applied = apply_production_intent(
            intent=oi, symbol="EURUSD", treatment_reference_entry=1.10010,
            policy_path=rdirs["policy_path"])
        assert out is oi and not applied
        assert svc.rollback(app.application_id)["phase"] == "ROLLED_BACK"

    def test_newer_protected(self, rdirs):
        from research_engine.v10.baselines.models import BaselineSnapshot as BS

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        svc.execute(app.application_id)
        rdirs["reg"].save(BS(snapshot_id="NEWER", config_hash="independent"))
        from research_engine.v10.baselines import baseline_authority as auth

        auth.set_active("NEWER", actor="independent", reason="unrelated activation")
        with pytest.raises(ValueError, match="Independent baseline transition"):
            svc.rollback(app.application_id)
        assert auth.get_active().active_baseline_id == "NEWER"

# __PART7__
class TestLFailures:
    def test_stale_blocks(self, rdirs):
        from research_engine.v10.baselines.models import BaselineSnapshot as BS

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        rdirs["reg"].save(BS(snapshot_id="NEWER2", config_hash="old-config",
                             configuration={"optimisation_policy": {"kind": "normal"}}))
        from research_engine.v10.baselines import baseline_authority as auth

        auth.set_active("NEWER2", actor="independent", reason="drift")
        with pytest.raises(ValueError):
            svc.execute(app.application_id)
        assert read_policy_file(rdirs["policy_path"]) == {"kind": "normal"}

    def test_substitution_blocks(self, rdirs):
        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        row = json.loads(svc.application_path.read_text(encoding="utf-8"))
        spec = json.loads(row["treatment_spec"])
        spec["scope"] = {"symbols": ["GBPUSD"], "patterns": None}
        row["treatment_spec"] = make_spec(spec["scope"])
        svc.application_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="treatment_spec"):
            svc.execute(app.application_id)
        assert read_policy_file(rdirs["policy_path"]) == {"kind": "normal"}

    def test_wrong_readback(self, rdirs, monkeypatch):
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        orig = A.apply_effective_state

        def bad(self, intended):
            orig(self, intended)
            Path(self.policy_path).write_text(
                '{"schema_version":1,"state":{"kind":"normal"}}', encoding="utf-8")

        monkeypatch.setattr(A, "apply_effective_state", bad)
        with pytest.raises(ValueError, match="Incorrect readback"):
            svc.execute(app.application_id)
        assert svc.get_operation(app.application_id)["phase"] == "VERIFY_FAILED"


class TestMExplicit:
    def test_no_mutation_on_construct(self, rdirs):
        from research_engine.control_plane.application_service import ApplicationService as AS
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        before = Path(rdirs["policy_path"]).read_bytes()
        A(rdirs["policy_path"])
        AS(adapter=A(rdirs["policy_path"]), application_path=rdirs["tmp"] / "applications.jsonl",
           **rdirs["dirs"], evaluations_dir=rdirs["tmp"] / "evaluations",
           operations_dir=rdirs["tmp"] / "operations",
           baselines_dir=rdirs["baselines"], pointer_file=rdirs["pointer"])
        assert Path(rdirs["policy_path"]).read_bytes() == before

    def test_explicit_adapter_required(self):
        import inspect as _insp

        from research_engine.control_plane.application_service import ApplicationService as AS
        from research_engine.control_plane import production_adapter as _pa

        assert _insp.signature(AS).parameters["adapter"].default is _insp.Parameter.empty
        assert _pa.PolicyAdapter._is_protocol

    def test_no_startup_or_broker(self):
        import ast as _ast

        for rel in ("core/optimisation_policy.py",
                    "research_engine/control_plane/direction_inversion_adapter.py"):
            src = Path(rel).read_text(encoding="utf-8").lower()
            assert "metatrader" not in src and "place_order" not in src
            assert "order_send" not in src and "auto_deploy" not in src
        tree = _ast.parse(Path("core/runtime/startup_recovery.py").read_text(encoding="utf-8"))
        names = [n.attr for n in _ast.walk(tree) if isinstance(n, _ast.Attribute)]
        assert "apply_effective_state" not in names and "write_policy_file" not in names

    def test_execute_places_no_orders(self, rdirs, monkeypatch):
        import builtins as _bi

        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        svc = rservice(rdirs)
        orig = _bi.__import__

        def guarded(name, *args, **kwargs):
            assert not any(t in name.lower() for t in ("metatrader", "mt5", "broker"))
            return orig(name, *args, **kwargs)

        monkeypatch.setattr(_bi, "__import__", guarded)
        svc.execute(app.application_id)
        svc.rollback(app.application_id)

# __PART8__
class TestNLiveConsumption:
    def _prep_kwargs(self, pattern="TBC"):
        from types import SimpleNamespace

        from risk.models import OrderIntent
        from strategy.signals import Side

        oi = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.5,
                         entry_reference=1.10, sl=1.09800, tp=1.106, pattern=pattern)
        return dict(new_result={"intent": oi, "entity_id": "E1", "strategy": "S"},
                    new_engine_score=0.5, new_engine_htf=None,
                    sym_state=SimpleNamespace(symbol="EURUSD", engine_state=None),
                    cycle_id=1, closed_time=1000, bid=1.10000, ask=1.10020)

    def test_live_normal_keeps_incumbent(self, tmp_path, monkeypatch):
        import core.optimisation_policy as pol

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(pol, "_DEFAULT_PATH", tmp_path / "policy.json")
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch

        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0):
            prep = h.prepare_execution(**self._prep_kwargs())
        assert prep.intent.side.name == "BUY" and prep.intent.sl == 1.09800
        assert prep.treatment_reference_entry == pytest.approx(1.10010)

    def test_live_inverts(self, tmp_path, monkeypatch):
        import core.optimisation_policy as pol

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(pol, "_DEFAULT_PATH", tmp_path / "policy.json")
        pol.write_policy_file(
            {"kind": "direction_inversion", "treatment_id": "historical-treatment",
             "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})},
            tmp_path / "policy.json")
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch

        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0) as hook:
            prep = h.prepare_execution(**self._prep_kwargs())
        assert prep.intent.side.name == "SELL"
        assert prep.intent.sl == pytest.approx(1.10220)
        assert prep.intent.tp == pytest.approx(1.09380)
        assert prep.intent.entry_reference == 1.10
        _, kwargs = hook.call_args
        assert kwargs["entry_price"] == pytest.approx(1.10010)

# __PART9__
class TestNLiveOutOfScope:
    def _prep_kwargs(self):
        from types import SimpleNamespace

        from risk.models import OrderIntent
        from strategy.signals import Side

        oi = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.5,
                         entry_reference=1.10, sl=1.09800, tp=1.106, pattern="TBC")
        return dict(new_result={"intent": oi, "entity_id": "E1", "strategy": "S"},
                    new_engine_score=0.5, new_engine_htf=None,
                    sym_state=SimpleNamespace(symbol="EURUSD", engine_state=None),
                    cycle_id=1, closed_time=1000, bid=1.10000, ask=1.10020)

    def test_live_out_of_scope(self, tmp_path, monkeypatch):
        import core.optimisation_policy as pol

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(pol, "_DEFAULT_PATH", tmp_path / "policy.json")
        pol.write_policy_file(
            {"kind": "direction_inversion", "treatment_id": "historical-treatment",
             "treatment_spec": make_spec({"symbols": ["GBPUSD"], "patterns": None})},
            tmp_path / "policy.json")
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch

        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0):
            prep = h.prepare_execution(**self._prep_kwargs())
        assert prep.intent.side.name == "BUY" and prep.intent.sl == 1.09800

    def test_live_corrupt_fails_closed(self, tmp_path, monkeypatch):
        import core.optimisation_policy as pol

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(pol, "_DEFAULT_PATH", tmp_path / "policy.json")
        (tmp_path / "policy.json").write_text("{corrupt", encoding="utf-8")
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch

        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0):
            with pytest.raises(ValueError):
                h.prepare_execution(**self._prep_kwargs())

    def test_live_missing_reference_fails_closed(self, tmp_path, monkeypatch):
        import core.optimisation_policy as pol

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(pol, "_DEFAULT_PATH", tmp_path / "policy.json")
        pol.write_policy_file(
            {"kind": "direction_inversion", "treatment_id": "historical-treatment",
             "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None})},
            tmp_path / "policy.json")
        import core.runtime.engine_execution_handler as h
        from unittest.mock import patch as _patch

        kw = self._prep_kwargs()
        kw.update(bid=float("nan"), ask=float("nan"))
        with _patch("core.decision_audit.persist_new_engine_decision_audit", return_value=""), \
             _patch("core.research_events.persist_config_snapshot", return_value=None), \
             _patch("research_engine.lifecycle.candidate_shadow_hook.open_candidate_shadows",
                    return_value=0):
            with pytest.raises(ValueError):
                h.prepare_execution(**kw)

# __PART10__
class TestOSafety:
    def test_import_and_construct_no_mutation(self, tmp_path, monkeypatch):
        import sys as _sys

        monkeypatch.chdir(tmp_path)
        for mod in ("core.optimisation_policy",
                    "research_engine.control_plane.direction_inversion_adapter"):
            _sys.modules.pop(mod, None)
        import core.optimisation_policy as pol

        assert not (tmp_path / "data" / "production" / "optimisation_policy.json").exists()
        from research_engine.control_plane.direction_inversion_adapter import (
            DirectionInversionPolicyAdapter as A,
        )

        before = pol.read_policy_file(tmp_path / "p.json")
        A(tmp_path / "p.json")
        assert pol.read_policy_file(tmp_path / "p.json") == before
        assert not (tmp_path / "data" / "production" / "optimisation_policy.json").exists()

    def test_only_execute_mutates(self, rdirs):
        before = Path(rdirs["policy_path"]).read_bytes()
        _ledger, app = approve(rdirs, {"symbols": ["EURUSD"], "patterns": None})
        assert Path(rdirs["policy_path"]).read_bytes() == before
        rservice(rdirs).execute(app.application_id)
        assert Path(rdirs["policy_path"]).read_bytes() != before










