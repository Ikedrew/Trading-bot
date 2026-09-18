"""Wave 5.4 — baseline-aware repeat-cycle proof: N → N+1 → N+2.

Proves the system can improve AGAIN after N+1 becomes the incumbent baseline:

    Baseline N
      → finding/candidate/evaluation/recommendation/persisted ACCEPT
      → explicit governed deployment (ApplicationService.execute)
    → Baseline N+1
      → RESTART (process reconstruction from durable stores only)
      → NEW baseline-aware cycle 2 (same conceptual direction_inversion
        treatment, new epoch)
      → new evaluation/recommendation/ACCEPT/application, all bound to N+1
      → explicit governed deployment
    → Baseline N+2

with N != N+1 != N+2, exact baseline/config provenance at every boundary,
stale N-era authority rejected, same-epoch idempotency, no automatic
deployment anywhere, and historical N records preserved untouched.

Isolated tmp stores only. No MT5, no broker, no live AWS/S3, no real
runtime policy file.
"""
import ast
import builtins
import hashlib
import json
from pathlib import Path

import pytest

from core.optimisation_policy import (
    read_policy_file,
    write_policy_file,
)
from research_engine.lifecycle.treatment_provenance import canonical_spec

TID = "historical-treatment"
EUR_SCOPE = {"symbols": ["EURUSD"], "patterns": None}   # cycle 1 scope
GBP_SCOPE = {"symbols": ["GBPUSD"], "patterns": None}   # cycle 2 scope
BL_N = "BL-N"
H_N = "cfg-N-normal"


def make_spec(scope, tid=TID):
    return canonical_spec({
        "change_type": "direction_inversion", "declared": {},
        "scope": scope, "treatment_id": tid,
    })


def inversion_state(scope):
    return {"kind": "direction_inversion", "treatment_id": TID,
            "treatment_spec": make_spec(scope)}


STATE_N = {"kind": "normal"}
STATE_1 = inversion_state(EUR_SCOPE)
STATE_2 = inversion_state(GBP_SCOPE)


def _cfg(parent, state):
    # Exact SnapshotBuilder.from_verified_real_state config_hash derivation:
    # parent baseline config identity + the deployed effective policy.
    return hashlib.sha256(json.dumps(
        {"parent_config": parent, "optimisation_policy": state},
        sort_keys=True).encode()).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated two-cycle environment: tmp stores only, no MT5/AWS/policy file."""
    monkeypatch.chdir(tmp_path)
    d = {n: str(tmp_path / n)
         for n in ("registry_dir", "decisions_dir", "recommendations_dir")}
    baselines = tmp_path / "baselines"
    pointer = baselines / "active_baseline.json"
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._BASELINES_DIR",
        str(baselines))
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._ACTIVE_POINTER_FILE",
        str(pointer))

    import research_engine.lifecycle.finding_trigger as ft
    trig = tmp_path / "triggers"
    monkeypatch.setattr(ft, "_TRIGGER_DIR", trig)
    monkeypatch.setattr(ft, "_TRIGGER_FILE", trig / "finding_triggers.json")

    import research_engine.lifecycle.registry as reg_mod
    inv = tmp_path / "investigations"
    monkeypatch.setattr(reg_mod, "_REGISTRY_DIR", inv)
    monkeypatch.setattr(reg_mod, "_REGISTRY_FILE", inv / "registry.json")
    monkeypatch.setattr(reg_mod, "_AUDIT_LOG", inv / "audit_log.jsonl")

    # Orchestrator-side CandidateRegistry uses the module default dir.
    monkeypatch.setattr(
        "research_engine.v10.candidates.candidate_registry._STORAGE_DIR",
        d["registry_dir"])

    ppath = tmp_path / "policy.json"
    write_policy_file(STATE_N, ppath)

    import core.research_events as events_mod

    # Production semantics (SnapshotBuilder.from_verified_real_state): after
    # each VERIFIED deployment the current production config identity IS the
    # new snapshot's config_hash (parent hash + deployed effective policy).
    # The test mirrors that chain explicitly; no wall clock anywhere.
    cfg = {"current": H_N}

    def fake_config_hash():
        st = read_policy_file(ppath)
        if st.get("kind") == "normal":
            return H_N
        return cfg["current"]

    monkeypatch.setattr(events_mod, "compute_config_hash", fake_config_hash)

    from research_engine.v10.baselines import baseline_authority as auth
    from research_engine.v10.baselines.models import BaselineSnapshot as BS
    from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry as SR
    reg = SR(str(baselines))
    reg.save(BS(snapshot_id=BL_N, config_hash=H_N,
                configuration={"optimisation_policy": STATE_N}))
    auth.set_active(BL_N, actor="test", reason="seed baseline N")
    return {"tmp": tmp_path, "dirs": d, "baselines": baselines,
            "pointer": pointer, "reg": reg, "policy_path": ppath, "cfg": cfg}


# ─── Repeat-cycle harness helpers ────────────────────────────────────────────


def make_service(env):
    from research_engine.control_plane.application_service import (
        ApplicationService as AS)
    from research_engine.control_plane.direction_inversion_adapter import (
        DirectionInversionPolicyAdapter)
    tmp = env["tmp"]
    return AS(
        adapter=DirectionInversionPolicyAdapter(env["policy_path"]),
        application_path=tmp / "applications.jsonl",
        **env["dirs"],
        evaluations_dir=tmp / "evaluations",
        operations_dir=tmp / "operations",
        baselines_dir=env["baselines"],
        pointer_file=env["pointer"],
    )


def validated_hypothesis(orch, finding_id="F-100"):
    from research_engine.lifecycle.hypothesis import (
        ConclusionType, HypothesisCategory, HypothesisStatus)
    h = orch.detect_and_register(
        title=f"TBC reversal hypothesis {finding_id}",
        description="Detected direction asymmetry in TBC performance.",
        claim="Inverting TBC direction produces positive R",
        null_hypothesis="TBC direction has no systematic effect on outcome",
        category=HypothesisCategory.DIRECTION_INVERSION,
        source="wave54_repeat_cycle",
        source_finding_id=finding_id)
    h.transition(HypothesisStatus.TESTING, reason="experiment started")
    assert h.conclude(ConclusionType.VALIDATED, reason="validated evidence",
                      confidence="HIGH", evidence_ref="EXP-1")
    return h


def validated_result(h):
    from research_engine.lifecycle.experiment_protocol import ExperimentResult
    return ExperimentResult(
        experiment_id=f"EXP-{h.hypothesis_id}", hypothesis_id=h.hypothesis_id,
        status="complete", n=80, mean_r=0.32, win_rate=0.58,
        ci_lower=0.10, ci_upper=0.50, oos_n=40, oos_mean_r=0.21,
        symbols_positive=4, symbols_total=5, periods_positive=3,
        periods_total=4, survives_top20_removal=True,
        evidence_maturity="GREEN")


def make_candidate(orch, h, result):
    cand = orch.create_optimisation_candidate(h, result)
    assert cand is not None, "candidate creation failed"
    return cand


def move_to_ready(env, cid):
    from research_engine.v10.candidates.candidate_registry import (
        CandidateRegistry as CR)
    cands = CR(env["dirs"]["registry_dir"])
    for status in ("VALIDATING", "VALIDATED", "READY_FOR_REVIEW"):
        cands.update_status(cid, status)


def stage_evaluation(env, cid, eid, scope, baseline_id, config_hash):
    """Evaluation + recommendation + durable evidence files (no decision)."""
    from research_engine.lifecycle.candidate_evaluator import (
        CandidateEvaluation as CE)
    from research_engine.lifecycle.candidate_recommendation import (
        RecommendationStore as RS, create_recommendation as cr)
    d, tmp = env["dirs"], env["tmp"]
    move_to_ready(env, cid)
    from research_engine.v10.candidates.candidate_registry import (
        CandidateRegistry as CR)
    CR(d["registry_dir"]).add_validation_result(
        cid, eid, "IMPROVED", confidence="HIGH", sample_size=60)
    ev = CE(candidate_id=cid, evaluation_id=eid, treatment_id=TID,
            baseline_id=baseline_id, config_hash=config_hash,
            decision="VALIDATED", confidence="HIGH", eligible_pairs=60,
            survives_outlier_removal=True)
    ev.treatment_spec = make_spec(scope)
    cr(ev, store=RS(d["recommendations_dir"]))
    ed = tmp / "evaluations"
    ed.mkdir(exist_ok=True)
    with (ed / f"{cid}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev.to_dict()) + "\n")
    return ed


def accept_and_apply(env, cid, eid, ed):
    """Persisted human ACCEPT + governed ApplicationRecord."""
    from research_engine.v10.candidates.candidate_decision import (
        record_human_decision as rh)
    from research_engine.control_plane.application_ledger import (
        ApplicationLedger as AL)
    d, tmp = env["dirs"], env["tmp"]
    rh(cid, "ACCEPT", f"REC-{eid}", actor="human", reason="reviewed",
       registry_dir=d["registry_dir"], decisions_dir=d["decisions_dir"],
       recommendations_dir=d["recommendations_dir"], evaluations_dir=str(ed))
    ledger = AL(tmp / "applications.jsonl")
    app = ledger.create_application_from_approval(
        cid, f"REC-{eid}", registry_dir=d["registry_dir"],
        decisions_dir=d["decisions_dir"],
        recommendations_dir=d["recommendations_dir"],
        evaluations_dir=str(ed))
    return ledger, app


def govern_and_apply(env, cand, eid, scope, baseline_id, config_hash):
    ed = stage_evaluation(env, cand["candidate_id"], eid, scope,
                          baseline_id, config_hash)
    return accept_and_apply(env, cand["candidate_id"], eid, ed)


def deploy_cycle1(env):
    """Cycle 1: candidate bound to N → ACCEPT → explicit deploy → N+1."""
    from research_engine.lifecycle.orchestrator import ResearchOrchestrator
    orch = ResearchOrchestrator()
    h = validated_hypothesis(orch)
    result = validated_result(h)
    cand1 = make_candidate(orch, h, result)
    assert cand1["baseline_id"] == BL_N
    _ledger, app1 = govern_and_apply(env, cand1, "E1", EUR_SCOPE, BL_N, H_N)
    op1 = make_service(env).execute(app1.application_id)
    assert op1["phase"] == "COMPLETED"
    # a verified deployment advances the production config identity chain
    env["cfg"]["current"] = env["reg"].load(
        op1["snapshot"]["snapshot_id"]).config_hash
    return orch, h, result, cand1, app1, op1, op1["snapshot"]["snapshot_id"]


def decisions_rows(env):
    path = Path(env["dirs"]["decisions_dir"]) / "decisions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def n1_config_hash(env):
    """Config identity of the N+1 baseline (derived from the cycle-1 verified
    deployment state, exactly as from_verified_real_state does)."""
    from core.optimisation_policy import validate_effective_state as ves
    return _cfg(H_N, ves(read_policy_file(env["policy_path"])))


class TestABaselineEpoch:
    def test_epoch_is_active_baseline_identity(self, env):
        from research_engine.v10.baselines import baseline_authority as auth
        from research_engine.v10.baselines.baseline_authority import research_epoch
        # A1: N is the initial active baseline
        assert auth.get_active().active_baseline_id == BL_N
        assert research_epoch() == BL_N

    def test_epoch_derived_from_authority_not_wallclock(self, env):
        from research_engine.v10.baselines.baseline_authority import research_epoch
        assert research_epoch() == research_epoch() == BL_N
        # no pointer file → pre-bootstrap eras collapse to one empty epoch
        (env["tmp"] / "baselines" / "active_baseline.json").unlink()
        assert research_epoch() == ""


class TestBFindingReopening:
    def _detect(self, engine, sample=80):
        return engine.detect_from_pattern_performance(
            "TBC", mean_r=-0.40, win_rate=0.10, sample_size=sample,
            source="wave54")

    def test_same_epoch_no_duplicate_new_epoch_reopens_history_preserved(self, env):
        from research_engine.lifecycle.finding_trigger import (
            FindingTriggerEngine, TriggerStatus)
        from research_engine.v10.baselines import baseline_authority as auth
        from research_engine.v10.baselines.baseline_authority import research_epoch
        from research_engine.v10.baselines.models import BaselineSnapshot as BS

        engine = FindingTriggerEngine()
        t1 = self._detect(engine)
        assert t1 is not None and t1.status == TriggerStatus.ELIGIBLE
        assert t1.baseline_epoch == BL_N
        # investigation under epoch N completes
        engine.mark_investigating(t1.trigger_id)
        engine.mark_completed(t1.trigger_id)

        # 5/31: same finding + same baseline epoch → idempotent, no duplicate
        assert self._detect(engine) is None
        assert not [t for t in engine.eligible() if t.finding_id == t1.finding_id]

        # baseline epoch advances (N+1 becomes incumbent)
        env["reg"].save(BS(snapshot_id="BL-M", config_hash=H_N,
                           configuration={"optimisation_policy": STATE_N}))
        auth.set_active("BL-M", actor="test", reason="epoch advance")
        assert research_epoch() == "BL-M"

        # 6: same finding + NEW baseline epoch → eligible again
        engine2 = FindingTriggerEngine()  # restart-safe: reloads persisted state
        t2 = self._detect(engine2, sample=90)
        assert t2 is not None and t2.status == TriggerStatus.ELIGIBLE
        assert t2.baseline_epoch == "BL-M"
        assert t2.trigger_id != t1.trigger_id

        # 7: historical N finding preserved untouched
        old = engine2.get(t1.trigger_id)
        assert old.status == TriggerStatus.COMPLETED
        assert old.baseline_epoch == BL_N
        assert old.resolved_at

        # 31: same-epoch (N+1) retry → suppressed again
        assert self._detect(engine2, sample=90) is None


class TestCCandidateIdentityAcrossBaselines:
    def test_same_hypothesis_distinct_candidate_per_baseline(self, env):
        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.v10.baselines import baseline_authority as auth
        from research_engine.v10.baselines.models import BaselineSnapshot as BS
        from research_engine.v10.candidates.candidate_registry import (
            CandidateRegistry as CR)

        orch = ResearchOrchestrator()
        h = validated_hypothesis(orch)
        result = validated_result(h)

        # 8: same hypothesis + baseline N → deterministic candidate, idempotent
        cand_n = make_candidate(orch, h, result)
        base = f"OPT-{h.hypothesis_id[-8:]}"
        assert cand_n["candidate_id"] == base
        assert cand_n["baseline_id"] == BL_N
        assert cand_n["change_definition"]["baseline_config_hash"] == H_N
        cid_n = cand_n["candidate_id"]

        # same-epoch replay → identical candidate (persisted truth)
        replay = make_candidate(orch, h, result)
        assert replay["candidate_id"] == cid_n
        assert replay["created_at"] == cand_n["created_at"]

        # baseline epoch advances (N+1 becomes incumbent)
        env["reg"].save(BS(snapshot_id="BL-M", config_hash=H_N,
                           configuration={"optimisation_policy": STATE_N}))
        auth.set_active("BL-M", actor="test", reason="epoch advance")

        # 9: same hypothesis + new baseline epoch → DISTINCT new candidate
        cand_m = make_candidate(orch, h, result)
        assert cand_m["candidate_id"] != cid_n
        assert cand_m["candidate_id"] == (
            f"{base}-{ResearchOrchestrator._epoch_suffix('BL-M')}")
        assert cand_m["baseline_id"] == "BL-M"

        # same-epoch replay idempotent, single record
        cand_m2 = make_candidate(orch, h, result)
        assert cand_m2["candidate_id"] == cand_m["candidate_id"]
        assert cand_m2["created_at"] == cand_m["created_at"]

        cands = CR(env["dirs"]["registry_dir"])
        assert len([c for c in cands.list_all()
                    if c.candidate_id == cand_m["candidate_id"]]) == 1

        # 10: historical N candidate unchanged (never mutated or rebound)
        old = cands.get(cid_n)
        assert old.baseline_id == BL_N
        assert old.created_at == cand_n["created_at"]
        assert old.change_definition == cand_n["change_definition"]
        assert old.status == "PROPOSED"

        # returning to epoch N resolves the historical candidate (idempotent)
        auth.set_active(BL_N, actor="test", reason="epoch return")
        again = make_candidate(orch, h, result)
        assert again["candidate_id"] == cid_n


class TestDFullRepeatCycle:
    def test_n_to_n_plus_1_restart_then_n_plus_2(self, env):
        """The N → N+1 → N+2 invariant: two governed cycles, restart between."""
        from research_engine.lifecycle.finding_trigger import (
            FindingTriggerEngine, TriggerStatus)
        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.v10.baselines import baseline_authority as auth
        from research_engine.v10.baselines.baseline_authority import research_epoch
        from research_engine.v10.candidates.candidate_decision import (
            get_human_decision, record_human_decision as rh)
        from research_engine.v10.candidates.candidate_registry import (
            CandidateRegistry as CR)
        from research_engine.lifecycle.candidate_recommendation import (
            RecommendationStore as RS)

        d, tmp = env["dirs"], env["tmp"]

        # ── A1: N is the initial active baseline ────────────────────────
        assert auth.get_active().active_baseline_id == BL_N
        assert research_epoch() == BL_N

        # ── Cycle 1: candidate binds N (A2/C8) ──────────────────────────
        orch = ResearchOrchestrator()
        h = validated_hypothesis(orch)
        result = validated_result(h)
        cand1 = make_candidate(orch, h, result)
        cid1 = cand1["candidate_id"]
        assert cid1 == f"OPT-{h.hypothesis_id[-8:]}"
        assert cand1["baseline_id"] == BL_N
        assert cand1["change_definition"]["baseline_config_hash"] == H_N
        cand1_before = dict(cand1)

        # 32: same hypothesis + same epoch → idempotent
        cand1_replay = make_candidate(orch, h, result)
        assert cand1_replay["candidate_id"] == cid1
        assert cand1_replay["created_at"] == cand1["created_at"]

        # ── Finding trigger investigated under epoch N ──────────────────
        engine = FindingTriggerEngine()
        t1 = engine.detect_from_pattern_performance(
            "TBC", mean_r=-0.40, win_rate=0.10, sample_size=80,
            source="wave54")
        assert t1 is not None and t1.baseline_epoch == BL_N
        engine.mark_investigating(t1.trigger_id)
        engine.mark_completed(t1.trigger_id)
        # 5: same finding + same epoch → suppressed
        assert engine.detect_from_pattern_performance(
            "TBC", mean_r=-0.40, win_rate=0.10, sample_size=80,
            source="wave54") is None

        # ── Cycle-1 governance (approval alone mutates nothing) ─────────
        policy_normal_bytes = Path(env["policy_path"]).read_bytes()
        _ledger, app1 = govern_and_apply(env, cand1, "E1", EUR_SCOPE, BL_N, H_N)
        assert Path(env["policy_path"]).read_bytes() == policy_normal_bytes
        assert app1.baseline_id == BL_N
        assert app1.baseline_config_hash == H_N
        assert app1.state == "APPROVED_NOT_DEPLOYED"

        # ── Prepare N-bound authority that will become STALE after N+1 ──
        h2 = validated_hypothesis(orch, finding_id="F-200")
        cand1s = make_candidate(orch, h2, validated_result(h2))
        assert cand1s["baseline_id"] == BL_N
        _l2, app1s = govern_and_apply(env, cand1s, "E1S", EUR_SCOPE, BL_N, H_N)
        h3 = validated_hypothesis(orch, finding_id="F-300")
        cand_stale = make_candidate(orch, h3, validated_result(h3))
        stage_evaluation(env, cand_stale["candidate_id"], "E-S", EUR_SCOPE,
                         BL_N, H_N)  # READY_FOR_REVIEW, never ACCEPTed under N

        # ── A3/A4: explicit deployment 1 → N+1 active ───────────────────
        op1 = make_service(env).execute(app1.application_id)
        assert op1["phase"] == "COMPLETED"
        assert op1["application"]["baseline_id"] == BL_N
        assert op1["old_pointer"]["active_baseline_id"] == BL_N
        assert op1["previous_state"] == STATE_N
        assert op1["verification"]["actual"] == op1["intended_state"]
        eff1 = read_policy_file(env["policy_path"])
        assert eff1["treatment_spec"] == make_spec(EUR_SCOPE)
        n1_id = op1["snapshot"]["snapshot_id"]
        assert n1_id != BL_N
        assert auth.get_active().active_baseline_id == n1_id
        assert research_epoch() == n1_id
        snap1 = env["reg"].load(n1_id)
        # the verified deployment advances the production config identity
        from core.optimisation_policy import validate_effective_state as ves
        h1_actual = _cfg(H_N, ves(eff1))
        assert snap1.config_hash == h1_actual
        assert snap1.configuration["optimisation_policy"] == eff1
        env["cfg"]["current"] = h1_actual


        # ── G27: stale N-bound application cannot mutate after N+1 ──────
        with pytest.raises(ValueError, match="Stale active baseline"):
            make_service(env).execute(app1s.application_id)
        assert read_policy_file(env["policy_path"]) == eff1
        assert auth.get_active().active_baseline_id == n1_id

        # ── G27b: stale N-bound ACCEPT is baseline-blocked after N+1 ────
        d = env["dirs"]
        with pytest.raises(ValueError):
            rh(cand_stale["candidate_id"], "ACCEPT", "REC-E-S", actor="human",
               reason="late accept of N-era candidate",
               registry_dir=d["registry_dir"], decisions_dir=d["decisions_dir"],
               recommendations_dir=d["recommendations_dir"],
               evaluations_dir=str(tmp / "evaluations"))
        assert get_human_decision(cand_stale["candidate_id"],
                                  decisions_dir=d["decisions_dir"]) is None
        blocked_rows = [r for r in decisions_rows(env)
                        if r["outcome"] not in ("COMPLETED", "STATUS_FAILED")]
        assert any("BASELINE" in r["outcome"] or "BLOCKED" in r["outcome"]
                   for r in blocked_rows)

        # ── D: RESTART — reconstruct everything from durable stores ─────
        svc_r = make_service(env)                # new ApplicationService+adapter
        eng_r = FindingTriggerEngine()           # reloads persisted triggers
        orch_r = ResearchOrchestrator()          # reloads investigation registry
        cands_r = CR(d["registry_dir"])          # reloads candidate store
        # 13: active baseline still N+1
        assert auth.get_active().active_baseline_id == n1_id
        # 14: effective production policy still N+1
        eff = svc_r.adapter.read_effective_state()
        assert eff == eff1
        # 15: cycle-2 candidate will bind N+1 (proven below)

        # ── B6: same finding + N+1 → NEW investigation eligible ─────────
        t2 = eng_r.detect_from_pattern_performance(
            "TBC", mean_r=-0.45, win_rate=0.08, sample_size=90,
            source="wave54")
        assert t2 is not None and t2.status == TriggerStatus.ELIGIBLE
        assert t2.baseline_epoch == n1_id
        assert t2.trigger_id != t1.trigger_id
        # 7: old finding history preserved
        old_t = eng_r.get(t1.trigger_id)
        assert old_t.status == TriggerStatus.COMPLETED
        assert old_t.baseline_epoch == BL_N
        # 31: same-epoch retry idempotent
        assert eng_r.detect_from_pattern_performance(
            "TBC", mean_r=-0.45, win_rate=0.08, sample_size=90,
            source="wave54") is None

        # ── C9/C11: cycle-2 candidate — same hypothesis, NEW epoch ──────
        cand2 = make_candidate(orch_r, h, result)   # SAME hypothesis object
        cid2 = cand2["candidate_id"]
        assert cid2 != cid1
        assert cid2 == (f"OPT-{h.hypothesis_id[-8:]}-"
                        f"{ResearchOrchestrator._epoch_suffix(n1_id)}")
        assert cand2["baseline_id"] == n1_id
        assert cand2["change_definition"]["baseline_config_hash"] == n1_config_hash(env)
        # 10: old N candidate remains unchanged
        old_c = cands_r.get(cid1)
        assert old_c.baseline_id == BL_N
        assert old_c.change_definition == cand1_before["change_definition"]
        assert old_c.status == "ACCEPTED"
        # 32: same-epoch replay idempotent
        cand2_replay = make_candidate(orch_r, h, result)
        assert cand2_replay["candidate_id"] == cid2
        assert cand2_replay["created_at"] == cand2["created_at"]


        # ── Cycle-2 governance: every record binds N+1 (E16-19) ─────────
        policy_n1_bytes = Path(env["policy_path"]).read_bytes()
        h1 = n1_config_hash(env)
        ledger2, app2 = govern_and_apply(env, cand2, "E2", GBP_SCOPE, n1_id, h1)
        assert Path(env["policy_path"]).read_bytes() == policy_n1_bytes
        assert auth.get_active().active_baseline_id == n1_id  # no auto-deploy
        # 16: evaluation binds N+1
        eval_rows = [json.loads(line) for line in
                     (tmp / "evaluations" / f"{cid2}.jsonl")
                     .read_text(encoding="utf-8").splitlines() if line.strip()]
        assert eval_rows and all(r["baseline_id"] == n1_id
                                 and r["config_hash"] == h1
                                 for r in eval_rows)
        # 17: recommendation binds N+1
        rec2 = RS(d["recommendations_dir"]).get_by_recommendation_id("REC-E2")
        assert rec2.baseline_id == n1_id
        assert rec2.baseline_config_hash == h1
        # 18: ACCEPT binds N+1
        dec2 = get_human_decision(cid2, decisions_dir=d["decisions_dir"])
        assert dec2.decision == "ACCEPT"
        assert dec2.baseline_id == n1_id
        assert dec2.baseline_config_hash == h1
        # 19: application binds N+1
        assert app2.baseline_id == n1_id
        assert app2.baseline_config_hash == h1
        assert app2.state == "APPROVED_NOT_DEPLOYED"

        # ── F21-26: explicit execute application 2 → N+2 ────────────────
        op2 = make_service(env).execute(app2.application_id)
        assert op2["phase"] == "COMPLETED"
        # 22: previous state == exact N+1 effective state
        assert op2["previous_state"] == eff1
        # authorization was bound to N+1, and N+1 was the active baseline
        assert op2["application"]["baseline_id"] == n1_id
        assert op2["old_pointer"]["active_baseline_id"] == n1_id
        assert op2["intended_state"]["treatment_spec"] == make_spec(GBP_SCOPE)
        assert op2["verification"]["actual"] == op2["intended_state"]
        n2_id = op2["snapshot"]["snapshot_id"]
        # 24: N+2 active
        assert auth.get_active().active_baseline_id == n2_id
        eff2 = read_policy_file(env["policy_path"])
        assert eff2["treatment_spec"] == make_spec(GBP_SCOPE)
        snap2 = env["reg"].load(n2_id)
        # the second verified deployment advances the chain from N+1, not N
        from core.optimisation_policy import validate_effective_state as ves
        assert snap2.config_hash == _cfg(snap1.config_hash, ves(eff2))
        # 25: N, N+1, N+2 are all distinct
        assert len({BL_N, n1_id, n2_id}) == 3
        assert len({H_N, snap1.config_hash, snap2.config_hash}) == 3
        # 26: N+2 derives from N+1, not from N
        assert op2["old_snapshot"]["snapshot_id"] == n1_id
        assert snap2.configuration["optimisation_policy"] == eff2

        # ── H33/34: execute replay does not create another baseline ─────
        before_count = len(env["reg"].list_snapshots())
        op2_replay = make_service(env).execute(app2.application_id)
        assert op2_replay["operation_id"] == op2["operation_id"]
        assert op2_replay["phase"] == "COMPLETED"
        op1_replay = make_service(env).execute(app1.application_id)
        assert op1_replay["phase"] == "COMPLETED"
        assert auth.get_active().active_baseline_id == n2_id  # no N+3
        assert len(env["reg"].list_snapshots()) == before_count

        # ── H: recommendation/application replay idempotent ──────────────
        res = rh(cid2, "ACCEPT", "REC-E2", actor="human", reason="reviewed",
                 registry_dir=d["registry_dir"], decisions_dir=d["decisions_dir"],
                 recommendations_dir=d["recommendations_dir"],
                 evaluations_dir=str(tmp / "evaluations"))
        assert res.duplicate is True
        app2_again = ledger2.create_application_from_approval(
            cid2, "REC-E2", registry_dir=d["registry_dir"],
            decisions_dir=d["decisions_dir"],
            recommendations_dir=d["recommendations_dir"],
            evaluations_dir=str(tmp / "evaluations"))
        assert app2_again.application_id == app2.application_id

        # ── 22: historical records preserved (N-era untouched) ──────────
        assert cands_r.get(cid1).baseline_id == BL_N
        assert cands_r.get(cid1).change_definition == cand1_before["change_definition"]
        final_t = FindingTriggerEngine().get(t1.trigger_id)
        assert final_t.baseline_epoch == BL_N
        assert final_t.status == TriggerStatus.COMPLETED

        # ── I35: BOTH transitions required persisted ACCEPT ─────────────
        from research_engine.control_plane.application_ledger import (
            ApplicationLedger as AL)
        completed_accepts = [r for r in decisions_rows(env)
                             if r["decision"] == "ACCEPT"
                             and r["outcome"] == "COMPLETED"]
        accept_cids = {r["candidate_id"] for r in completed_accepts}
        assert cid1 in accept_cids and cid2 in accept_cids
        # every completed ACCEPT has a matching governed application
        governed = {a.candidate_id
                    for a in AL(tmp / "applications.jsonl").list_all()
                    if a.state in ("APPROVED_NOT_DEPLOYED", "VERIFIED")}
        assert accept_cids <= governed


class TestGStaleAuthority:
    def test_cycle1_recommendation_and_decision_cannot_substitute_cycle2(self, env):
        orch, h, result, cand1, app1, op1, n1_id = deploy_cycle1(env)
        cand2 = make_candidate(orch, h, result)
        assert cand2["baseline_id"] == n1_id
        d, tmp = env["dirs"], env["tmp"]

        from research_engine.v10.candidates.candidate_decision import (
            get_human_decision, record_human_decision as rh)
        # 28: cycle-1 recommendation substituted into a cycle-2 decision
        with pytest.raises(ValueError):
            rh(cand2["candidate_id"], "ACCEPT", "REC-E1", actor="human",
               reason="substitute cycle 1", registry_dir=d["registry_dir"],
               decisions_dir=d["decisions_dir"],
               recommendations_dir=d["recommendations_dir"],
               evaluations_dir=str(tmp / "evaluations"))
        dec = get_human_decision(cand2["candidate_id"],
                                 decisions_dir=d["decisions_dir"])
        assert dec is None or dec.outcome != "COMPLETED"

        # 29: cycle-1 approval substituted into a cycle-2 application
        from research_engine.control_plane.application_ledger import (
            ApplicationLedger as AL)
        ledger = AL(tmp / "applications.jsonl")
        with pytest.raises((ValueError, RuntimeError)):
            ledger.create_application_from_approval(
                cand2["candidate_id"], "REC-E1",
                registry_dir=d["registry_dir"],
                decisions_dir=d["decisions_dir"],
                recommendations_dir=d["recommendations_dir"],
                evaluations_dir=str(tmp / "evaluations"))
        assert ledger.get_latest_for_candidate(cand2["candidate_id"]) is None

        # 30: no substitutable application exists for the cycle-2 candidate
        with pytest.raises((ValueError, IndexError, KeyError)):
            make_service(env).execute(f"APP-{cand2['candidate_id']}-REC-E1")
        assert (read_policy_file(env["policy_path"])["treatment_spec"]
                == make_spec(EUR_SCOPE))

    def test_cycle1_application_cannot_substitute_cycle2(self, env):
        orch, h, result, cand1, app1, op1, n1_id = deploy_cycle1(env)
        cand2 = make_candidate(orch, h, result)
        ledger, app2 = govern_and_apply(env, cand2, "E2", GBP_SCOPE, n1_id,
                                        n1_config_hash(env))
        op2 = make_service(env).execute(app2.application_id)
        n2_id = op2["snapshot"]["snapshot_id"]
        assert op2["phase"] == "COMPLETED"

        # substitution attack: forge a cycle-1-provenance application for the
        # cycle-2 candidate AFTER VERIFIED — the append transition guard
        # refuses it and the historical ledger is unchanged.
        from research_engine.control_plane.application_ledger import (
            ApplicationLedger as AL)
        rows_before = len(AL(env["tmp"] / "applications.jsonl").list_all())
        forged = AL(env["tmp"] / "applications.jsonl")
        with pytest.raises(ValueError):
            forged.append(
                cand2["candidate_id"], "", "APPROVED_NOT_DEPLOYED",
                application_id=f"APP-{cand2['candidate_id']}-REC-E1",
                actor="attacker", reason="substitute cycle 1",
                recommendation_id="REC-E1", evaluation_id="E1",
                treatment_id=TID, treatment_spec=None,
                baseline_id=BL_N, baseline_config_hash=H_N,
                human_decision_outcome="COMPLETED")
        assert len(AL(env["tmp"] / "applications.jsonl").list_all()) == rows_before
        # executing the substituted application id fails closed
        with pytest.raises(Exception):
            make_service(env).execute(f"APP-{cand2['candidate_id']}-REC-E1")
        # production remains at N+2, unchanged by the attack
        from research_engine.v10.baselines import baseline_authority as auth
        assert auth.get_active().active_baseline_id == n2_id
        assert (read_policy_file(env["policy_path"])["treatment_spec"]
                == make_spec(GBP_SCOPE))


class TestIHumanGovernance:
    def test_no_automatic_deployment_path_in_repeat_cycle_code(self):
        """AST proof: repeat-cycle modules contain no production mutation path."""
        banned = {"apply_effective_state", "write_policy_file", "set_active",
                  "place_order", "order_send"}
        repo = Path(__file__).resolve().parents[1]
        for rel in ("research_engine/lifecycle/orchestrator.py",
                    "research_engine/lifecycle/finding_trigger.py"):
            tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
            names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            for b in banned:
                assert b not in names, f"{rel} references '{b}'"

    def test_persisted_accept_required_before_application_and_execute(self, env):
        orch, h, result, cand1, app1, op1, n1_id = deploy_cycle1(env)
        cand2 = make_candidate(orch, h, result)
        assert cand2["baseline_id"] == n1_id
        from research_engine.control_plane.application_ledger import (
            ApplicationLedger as AL)
        tmp = env["tmp"]
        # 36: without a persisted ACCEPT no application can be created
        with pytest.raises((ValueError, RuntimeError)):
            AL(tmp / "applications.jsonl").create_application_from_approval(
                cand2["candidate_id"], "REC-E2",
                registry_dir=env["dirs"]["registry_dir"],
                decisions_dir=env["dirs"]["decisions_dir"],
                recommendations_dir=env["dirs"]["recommendations_dir"],
                evaluations_dir=str(tmp / "evaluations"))
        # and explicit execute still requires a real governed application
        with pytest.raises((ValueError, IndexError, KeyError)):
            make_service(env).execute("APP-nonexistent")
        assert auth_active(env) == n1_id
        assert (read_policy_file(env["policy_path"])["treatment_spec"]
                == make_spec(EUR_SCOPE))


def auth_active(env):
    from research_engine.v10.baselines import baseline_authority as auth
    return auth.get_active().active_baseline_id


class TestJIsolation:
    def test_full_cycle_no_broker_cloud_or_repo_policy_mutation(self, env):
        repo = Path(__file__).resolve().parents[1]
        real_policy = repo / "data" / "production" / "optimisation_policy.json"
        real_before = real_policy.read_bytes() if real_policy.exists() else None

        class _Guard:
            FORBIDDEN = ("mt5", "metatrader", "broker", "boto3")

            def __init__(self):
                self._orig = builtins.__import__

            def __enter__(self):
                orig = self._orig

                def guarded(name, *a, **k):
                    low = name.lower()
                    if any(f in low for f in self.FORBIDDEN):
                        raise AssertionError(
                            f"forbidden import attempted: {name}")
                    return orig(name, *a, **k)

                builtins.__import__ = guarded
                return self

            def __exit__(self, *exc):
                builtins.__import__ = self._orig
                return False

        orch, h, result, cand1, app1, op1, n1_id = deploy_cycle1(env)
        cand2 = make_candidate(orch, h, result)
        _ledger, app2 = govern_and_apply(env, cand2, "E2", GBP_SCOPE, n1_id,
                                         n1_config_hash(env))
        with _Guard():
            make_service(env).execute(app1.application_id)   # replay (no-op)
            op2 = make_service(env).execute(app2.application_id)
        assert op2["phase"] == "COMPLETED"
        assert (read_policy_file(env["policy_path"])["treatment_spec"]
                == make_spec(GBP_SCOPE))
        assert auth_active(env) == op2["snapshot"]["snapshot_id"]
        # nothing fell back to default production paths; repo policy untouched
        assert not (env["tmp"] / "data" / "production").exists()
        real_after = real_policy.read_bytes() if real_policy.exists() else None
        assert real_after == real_before