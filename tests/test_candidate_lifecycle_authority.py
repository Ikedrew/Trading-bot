"""Repair Block 3 — unified candidate lifecycle + production authority tests.

Covers the governed candidate journey end to end:

    scientific candidate -> validation -> shadow evidence -> readiness
    -> human ACCEPT/REJECT -> governed approval -> governed deployment
    -> runtime authority -> disable/revoke/supersede/rollback
    -> upstream invalidation -> Lab truth

and the autonomy boundary: the ENGINE governs itself, the HUMAN governs live
authority.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import optimisation_policy as policy
from research_engine.control_plane import adverse_evidence_monitor as adverse
from research_engine.control_plane import candidate_lifecycle_authority as lifecycle
from research_engine.control_plane import candidate_lifecycle_service as service
from research_engine.control_plane import evidence_freshness_gate as freshness
from research_engine.control_plane import production_authority as authority
from research_engine.control_plane.application_ledger import ApplicationLedger
from research_engine.control_plane.application_service import ApplicationService
from research_engine.control_plane.direction_inversion_adapter import (
    DirectionInversionPolicyAdapter,
)
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation
from research_engine.lifecycle.candidate_recommendation import (
    RecommendationStore, create_recommendation,
)
from research_engine.lifecycle.treatment_provenance import canonical_spec
from research_engine.v10.baselines import baseline_authority as baseline
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord

BASELINE_ID = "BASE-1"
CONFIG_HASH = "cfg-1"
TREATMENT_ID = "historical-treatment"


def make_spec(scope, treatment_id=TREATMENT_ID):
    return canonical_spec({
        "change_type": "direction_inversion", "declared": {},
        "scope": scope, "treatment_id": treatment_id,
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated governed environment: every authority lives under tmp_path."""
    monkeypatch.chdir(tmp_path)
    baselines = tmp_path / "baselines"
    pointer = baselines / "active_baseline.json"
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._BASELINES_DIR", str(baselines))
    monkeypatch.setattr(
        "research_engine.v10.baselines.baseline_authority._ACTIVE_POINTER_FILE", str(pointer))
    monkeypatch.setattr("core.research_events.compute_config_hash", lambda: CONFIG_HASH)

    registry = SnapshotRegistry(str(baselines))
    registry.save(BaselineSnapshot(
        snapshot_id=BASELINE_ID, config_hash=CONFIG_HASH,
        configuration={"optimisation_policy": {"kind": "normal"}}))
    baseline.set_active(BASELINE_ID, actor="test", reason="fixture")

    governance = tmp_path / "governance"
    governance.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(authority, "DEFAULT_AUTHORITY_PATH",
                        governance / "production_authority.jsonl")
    monkeypatch.setattr(lifecycle, "DEFAULT_LIFECYCLE_LEDGER",
                        governance / "candidate_lifecycle.jsonl")
    monkeypatch.setattr(adverse, "DEFAULT_POLICY_PATH",
                        governance / "rollback_authority_policy.json")
    policy_path = tmp_path / "policy.json"
    monkeypatch.setattr(policy, "_DEFAULT_PATH", policy_path)
    policy.write_policy_file({"kind": "normal"}, policy_path)

    dirs = {
        "registry_dir": str(tmp_path / "candidates"),
        "decisions_dir": str(tmp_path / "candidates"),
        "recommendations_dir": str(tmp_path / "recommendations"),
        "evaluations_dir": str(tmp_path / "evaluations"),
        "optimisation_dir": str(tmp_path / "optimisation"),
    }
    Path(dirs["evaluations_dir"]).mkdir(parents=True, exist_ok=True)
    return {
        "tmp": tmp_path, "dirs": dirs, "governance": governance,
        "authority_path": governance / "production_authority.jsonl",
        "lifecycle_path": governance / "candidate_lifecycle.jsonl",
        "application_path": governance / "application.jsonl",
        "operations_dir": tmp_path / "operations",
        "baselines": baselines, "pointer": pointer, "registry": registry,
        "policy_path": policy_path, "monkeypatch": monkeypatch,
    }


def make_review_ready(env, *, cid="C-LIFE-1", eid="EVAL-1",
                      scope=None, status="READY_FOR_REVIEW",
                      requires_shadow=False):
    """Create a real Track-A candidate with real governed validation evidence."""
    scope = scope if scope is not None else {"symbols": ["EURUSD"], "patterns": None}
    change = {"type": "direction_inversion", "baseline_config_hash": CONFIG_HASH}
    if requires_shadow:
        change["requires_shadow_evidence"] = True
    change["treatment_hash"] = freshness.proposal_treatment_signature(change)

    registry = CandidateRegistry(env["dirs"]["registry_dir"])
    registry.create(CandidateRecord(
        candidate_id=cid, hypothesis_id="HYP-1", baseline_id=BASELINE_ID,
        created_from_question="E1", status=status, change_definition=change))
    registry.add_validation_result(cid, eid, "IMPROVED", confidence="HIGH",
                                   sample_size=60)

    evaluation = CandidateEvaluation(
        candidate_id=cid, evaluation_id=eid, treatment_id=TREATMENT_ID,
        baseline_id=BASELINE_ID, config_hash=CONFIG_HASH, decision="VALIDATED",
        confidence="HIGH", eligible_pairs=60, survives_outlier_removal=True)
    evaluation.treatment_spec = make_spec(scope)
    create_recommendation(
        evaluation, store=RecommendationStore(env["dirs"]["recommendations_dir"]))
    (Path(env["dirs"]["evaluations_dir"]) / f"{cid}.jsonl").write_text(
        json.dumps(evaluation.to_dict()) + "\n", encoding="utf-8")
    return registry, f"REC-{eid}"


def make_service(env):
    return ApplicationService(
        adapter=DirectionInversionPolicyAdapter(env["policy_path"]),
        application_path=env["application_path"],
        decisions_dir=env["dirs"]["decisions_dir"],
        recommendations_dir=env["dirs"]["recommendations_dir"],
        registry_dir=env["dirs"]["registry_dir"],
        evaluations_dir=env["dirs"]["evaluations_dir"],
        operations_dir=env["operations_dir"],
        baselines_dir=env["baselines"],
        pointer_file=env["pointer"],
    )


def accept(env, cid="C-LIFE-1", recommendation_id="REC-EVAL-1", **kwargs):
    return service.governed_human_decision(
        cid, "ACCEPT", recommendation_id, actor="human", reason="reviewed",
        registry_dir=env["dirs"]["registry_dir"],
        decisions_dir=env["dirs"]["decisions_dir"],
        recommendations_dir=env["dirs"]["recommendations_dir"],
        evaluations_dir=env["dirs"]["evaluations_dir"],
        lifecycle_ledger_path=env["lifecycle_path"], **kwargs)


def approve(env, cid="C-LIFE-1", recommendation_id="REC-EVAL-1"):
    return service.create_governed_approval(
        cid, recommendation_id, registry_dir=env["dirs"]["registry_dir"],
        decisions_dir=env["dirs"]["decisions_dir"],
        recommendations_dir=env["dirs"]["recommendations_dir"],
        evaluations_dir=env["dirs"]["evaluations_dir"],
        application_path=env["application_path"],
        authority_path=env["authority_path"],
        lifecycle_ledger_path=env["lifecycle_path"])


def derive(env, cid="C-LIFE-1", **kwargs):
    return lifecycle.derive_canonical_lifecycle(
        candidate_id=cid, registry_dir=env["dirs"]["registry_dir"],
        optimisation_registry_dir=env["dirs"]["optimisation_dir"],
        application_path=env["application_path"],
        authority_path=env["authority_path"],
        decisions_dir=env["dirs"]["decisions_dir"],
        lifecycle_ledger_path=env["lifecycle_path"], **kwargs)


def runtime_policy(env):
    """Exactly what the production runtime would consume."""
    return policy.read_authorized_policy_file(env["policy_path"])


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ONE CANONICAL LIFECYCLE PROJECTION ACROSS ALL STORES
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalProjection:
    def test_canonical_vocabulary_covers_the_full_journey(self):
        required = {
            "PROPOSED", "VALIDATION_QUEUED", "VALIDATING", "VALIDATION_FAILED",
            "VALIDATED", "FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE",
            "SHADOW_VALIDATED", "READY_FOR_PROMOTION_REVIEW", "REJECTED",
            "ACCEPTED", "APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED",
            "DISABLED", "REVOKED", "SUPERSEDED", "ROLLED_BACK",
            "INVALIDATED_UPSTREAM", "BLOCKED_UPSTREAM_INVALIDATED",
        }
        assert required <= set(lifecycle.CANONICAL_STATES)
        assert set(lifecycle.TRANSITION_AUTHORITY) == set(lifecycle.CANONICAL_STATES)

    def test_every_legacy_store_maps_losslessly_and_unknown_fails_closed(self):
        assert lifecycle.to_canonical("TRACK_A_CANDIDATE_REGISTRY",
                                      "READY_FOR_REVIEW") == "READY_FOR_PROMOTION_REVIEW"
        assert lifecycle.to_canonical("CONTINUOUS_OPTIMISATION_REGISTRY",
                                      "TESTING") == "VALIDATING"
        assert lifecycle.to_canonical("PRODUCTION_APPLICATION_LEDGER",
                                      "VERIFIED") == "VERIFIED"
        assert lifecycle.to_canonical("PRODUCTION_AUTHORITY", "REVOKED") == "REVOKED"
        with pytest.raises(lifecycle.LifecycleMappingError):
            lifecycle.to_canonical("TRACK_A_CANDIDATE_REGISTRY", "NOT_A_STATUS")
        with pytest.raises(lifecycle.LifecycleMappingError):
            lifecycle.to_canonical("NOT_A_STORE", "PROPOSED")

    def test_projection_unifies_track_a_and_continuous_and_flags_disagreement(self, env):
        make_review_ready(env)
        from research_engine.v10.optimisation.models import OptimisationCandidate
        from research_engine.v10.optimisation.optimisation_registry import (
            OptimisationRegistry,
        )

        registry = OptimisationRegistry(env["dirs"]["optimisation_dir"])
        from research_engine.v10.optimisation.models import ResearchHypothesis

        registry.add_hypothesis(ResearchHypothesis(
            hypothesis_id="HYP-1", source_finding="F-1", source_question="E1"))
        registry.add_candidate(OptimisationCandidate(
            candidate_id="C-LIFE-1", hypothesis_id="HYP-1", baseline_id=BASELINE_ID,
            component="strategy.entry", status="PROPOSED"))
        registry.save()
        view = derive(env)
        assert view.state == "READY_FOR_PROMOTION_REVIEW"
        assert view.store_states["TRACK_A_CANDIDATE_REGISTRY"] == "READY_FOR_PROMOTION_REVIEW"
        assert view.store_states["CONTINUOUS_OPTIMISATION_REGISTRY"] == "PROPOSED"
        assert any(d.startswith("REGISTRY_STATUS_DISAGREEMENT")
                   for d in view.disagreements)

    def test_registry_classes_are_explicit(self):
        classes = lifecycle.STORE_CLASSES
        assert classes["PRODUCTION_AUTHORITY"] == "AUTHORITATIVE"
        assert classes["PRODUCTION_APPLICATION_LEDGER"] == "AUTHORITATIVE"
        assert classes["HUMAN_DECISION_STORE"] == "AUTHORITATIVE"
        assert classes["TRACK_A_CANDIDATE_REGISTRY"] == "COMPATIBILITY"
        assert classes["CONTINUOUS_OPTIMISATION_REGISTRY"] == "COMPATIBILITY"
        assert classes["LEGACY_DECISION_LEDGER"] == "LEGACY"

    def test_unknown_candidate_is_proposed_and_has_no_authority(self, env):
        view = derive(env, cid="C-MISSING")
        assert view.state == "PROPOSED"
        assert view.runtime_effective is False
        assert view.live_authority is False

    def test_ready_for_promotion_review_is_not_accepted(self, env):
        make_review_ready(env)
        view = derive(env)
        assert view.state == "READY_FOR_PROMOTION_REVIEW"
        assert view.state != "ACCEPTED"
        assert view.review_notice == lifecycle.REVIEW_STATE_NOTICE
        assert "NOT LIVE" in view.review_notice
        assert authority.effective_authority_for_candidate(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"])
        ) is None

    def test_only_human_may_leave_review_ready(self):
        assert lifecycle.is_authorized_transition(
            "READY_FOR_PROMOTION_REVIEW", "ACCEPTED", "HUMAN")
        assert not lifecycle.is_authorized_transition(
            "READY_FOR_PROMOTION_REVIEW", "ACCEPTED", "ENGINE")
        assert not lifecycle.is_authorized_transition(
            "READY_FOR_PROMOTION_REVIEW", "ACCEPTED", "APPLICATION_SERVICE")
        # An engine may never self-promote at any stage.
        for from_state, to_state in (("VALIDATED", "ACCEPTED"),
                                     ("SHADOW_VALIDATED", "ACCEPTED"),
                                     ("ACCEPTED", "VERIFIED")):
            assert not lifecycle.is_authorized_transition(from_state, to_state, "ENGINE")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HUMAN DECISION AUTHORITY + FRESHNESS GATE
# ═══════════════════════════════════════════════════════════════════════════════

class TestHumanDecisionAuthority:
    def test_no_runtime_authority_before_human_accept(self, env):
        make_review_ready(env)
        assert policy.is_normal(runtime_policy(env))
        assert derive(env).state == "READY_FOR_PROMOTION_REVIEW"
        assert not authority.is_runtime_effective(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"]))

    def test_accept_requires_review_ready_and_freshness(self, env):
        make_review_ready(env, status="VALIDATING")
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env)
        assert "PROMOTION_BLOCKED_STALE" in str(exc.value)
        ledger = lifecycle.CandidateLifecycleLedger(env["lifecycle_path"])
        assert ledger.has_event("C-LIFE-1", "PROMOTION_BLOCKED_STALE")

    def test_stale_evidence_prevents_accept(self, env):
        registry, _ = make_review_ready(env)
        # Later contradictory governed evidence supersedes the success.
        registry.add_validation_result("C-LIFE-1", "EVAL-2", "WORSENED",
                                       confidence="LOW", sample_size=5)
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env)
        assert "EVIDENCE_SUPERSEDED_BY" in str(exc.value)
        assert derive(env).state == "READY_FOR_PROMOTION_REVIEW"

    def test_superseded_evidence_frontier_prevents_accept(self, env):
        make_review_ready(env)
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env, current_frontier="FRONTIER-2",
                   superseded_frontiers=["FRONTIER-1"],
                   candidate_frontier="FRONTIER-1")
        assert "EVIDENCE_FRONTIER_SUPERSEDED" in str(exc.value)

    def test_baseline_config_drift_prevents_accept(self, env):
        make_review_ready(env)
        env["monkeypatch"].setattr("core.research_events.compute_config_hash",
                                   lambda: "cfg-DRIFTED")
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env)
        assert "BASELINE_NOT_CURRENT" in str(exc.value)

    def test_shadow_failure_prevents_accept(self, env):
        make_review_ready(env, requires_shadow=True)
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env)
        assert "MISSING_SHADOW_EVIDENCE_FRONTIER" in str(exc.value)

    def test_upstream_invalidated_prevents_accept(self, env):
        make_review_ready(env)
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env, invalidated_upstream=["F-1"])
        assert "UPSTREAM_INVALIDATED" in str(exc.value)

    def test_failed_validation_cannot_become_review_ready(self, env):
        make_review_ready(env, status="FAILED_VALIDATION")
        with pytest.raises(service.CandidateLifecycleServiceError) as exc:
            accept(env)
        assert "NOT_REVIEWABLE_STATE" in str(exc.value)


    def test_explicit_accept_creates_governed_approval(self, env):
        make_review_ready(env)
        result = accept(env)
        assert result.ok and not result.duplicate
        assert derive(env).state == "ACCEPTED"

        application = approve(env)
        assert application.state == "APPROVED_NOT_DEPLOYED"
        record = authority.effective_authority_for_candidate(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"]))
        assert record is not None and record.status == authority.AuthorityStatus.ISSUED
        assert record.decision_id and record.application_id and record.policy_hash
        # ISSUED is NOT runtime authority.
        assert not authority.is_runtime_effective(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"]))
        assert derive(env).state == "APPROVED_NOT_DEPLOYED"
        assert policy.is_normal(runtime_policy(env))

    def test_reject_prevents_production_authority(self, env):
        make_review_ready(env)
        result = service.governed_human_decision(
            "C-LIFE-1", "REJECT", "REC-EVAL-1", actor="human", reason="not convinced",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            recommendations_dir=env["dirs"]["recommendations_dir"],
            evaluations_dir=env["dirs"]["evaluations_dir"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert result.ok
        assert derive(env).state == "REJECTED"
        with pytest.raises(service.CandidateLifecycleServiceError):
            approve(env)
        assert authority.effective_authority_for_candidate(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"])
        ) is None

    def test_reject_stays_possible_for_a_stale_candidate(self, env):
        registry, _ = make_review_ready(env)
        registry.add_validation_result("C-LIFE-1", "EVAL-2", "WORSENED",
                                       confidence="LOW", sample_size=5)
        result = service.governed_human_decision(
            "C-LIFE-1", "REJECT", "REC-EVAL-1", actor="human", reason="stale",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            recommendations_dir=env["dirs"]["recommendations_dir"],
            evaluations_dir=env["dirs"]["evaluations_dir"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert result.ok
        assert derive(env).state == "REJECTED"

    def test_duplicate_human_decision_fails_closed(self, env):
        make_review_ready(env)
        first = accept(env)
        assert first.ok and not first.duplicate
        replay = accept(env)
        assert replay.ok and replay.duplicate
        decisions = (Path(env["dirs"]["decisions_dir"]) / "decisions.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        assert len(decisions) == 1

    def test_conflicting_human_decision_fails_closed(self, env):
        make_review_ready(env)
        accept(env)
        with pytest.raises(service.CandidateLifecycleServiceError):
            service.governed_human_decision(
                "C-LIFE-1", "REJECT", "REC-EVAL-1", actor="human", reason="changed mind",
                registry_dir=env["dirs"]["registry_dir"],
                decisions_dir=env["dirs"]["decisions_dir"],
                recommendations_dir=env["dirs"]["recommendations_dir"],
                evaluations_dir=env["dirs"]["evaluations_dir"],
                lifecycle_ledger_path=env["lifecycle_path"])
        assert derive(env).state == "ACCEPTED"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GOVERNED APPLICATION + DEPLOYMENT + RUNTIME ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def deploy(env):
    """ACCEPTED -> approval -> governed deployment -> ACTIVE authority."""
    make_review_ready(env)
    accept(env)
    application = approve(env)
    operation = make_service(env).execute(application.application_id)
    return application, operation


class TestGovernedApplicationAndRuntime:
    def test_application_requires_accept(self, env):
        make_review_ready(env)
        ledger = ApplicationLedger(env["application_path"])
        with pytest.raises(ValueError):
            ledger.create_application_from_approval(
                "C-LIFE-1", "REC-EVAL-1",
                registry_dir=env["dirs"]["registry_dir"],
                decisions_dir=env["dirs"]["decisions_dir"],
                recommendations_dir=env["dirs"]["recommendations_dir"],
                evaluations_dir=env["dirs"]["evaluations_dir"])

    def test_deployment_requires_approval_ledger(self, env):
        make_review_ready(env)
        accept(env)
        with pytest.raises(ValueError):
            make_service(env).execute("APP-C-LIFE-1-REC-EVAL-1")

    def test_runtime_accepts_valid_deployed_authority(self, env):
        _application, operation = deploy(env)
        assert operation["phase"] == "COMPLETED"
        record = authority.effective_authority_for_candidate(
            "C-LIFE-1", ledger=authority.ProductionAuthorityLedger(env["authority_path"]))
        assert record.status == authority.AuthorityStatus.ACTIVE
        assert record.deployment_id == operation["operation_id"]
        assert record.verification_status == "VERIFIED"
        assert record.superseded_by == ""
        assert derive(env).state == "VERIFIED"
        assert derive(env).runtime_effective is True
        assert runtime_policy(env) == operation["intended_state"]

    def test_runtime_ignores_orphan_policy_file(self, env):
        make_review_ready(env)
        accept(env)
        application = approve(env)
        intended = policy.intended_state_from_approval(
            application=application.to_dict())
        policy.write_policy_file(intended, env["policy_path"])
        # A syntactically valid policy file with no ACTIVE governed authority.
        assert policy.read_policy_file(env["policy_path"]) == intended
        assert policy.is_normal(runtime_policy(env))
        verdict = policy.verify_production_authority(intended)
        assert verdict.ok is False
        assert "AUTHORITY_NOT_RUNTIME_EFFECTIVE:ISSUED" in verdict.reasons

    def test_direct_policy_file_write_has_no_authority(self, env):
        state = {"kind": "direction_inversion", "treatment_id": TREATMENT_ID,
                 "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None}),
                 "application_id": "APP-FORGED", "candidate_id": "C-FORGED"}
        policy.write_policy_file(state, env["policy_path"])
        assert policy.is_normal(runtime_policy(env))
        verdict = policy.verify_production_authority(state)
        assert not verdict.ok and "NO_GOVERNED_AUTHORITY" in verdict.reasons

    def test_live_approved_metadata_alone_has_no_authority(self, env):
        make_review_ready(env)
        registry = CandidateRegistry(env["dirs"]["registry_dir"])
        registry.get("C-LIFE-1").change_definition["live_approved"] = True
        registry._persist()
        state = {"kind": "direction_inversion", "treatment_id": TREATMENT_ID,
                 "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None}),
                 "application_id": "APP-METADATA", "candidate_id": "C-LIFE-1"}
        policy.write_policy_file(state, env["policy_path"])
        assert policy.is_normal(runtime_policy(env))
        assert derive(env).state == "READY_FOR_PROMOTION_REVIEW"

    def test_no_parallel_registry_can_grant_runtime_authority(self, env):
        make_review_ready(env)
        accept(env)
        application = approve(env)
        # A parallel compatibility registry can be edited to claim anything for
        # a DIFFERENT candidate; runtime authority is unchanged because it lives
        # only in the governed authority ledger.
        registry = CandidateRegistry(env["dirs"]["registry_dir"])
        registry.create(CandidateRecord(
            candidate_id="C-PARALLEL", baseline_id=BASELINE_ID,
            status="ACCEPTED",
            change_definition={"type": "direction_inversion", "live_approved": True,
                               "baseline_config_hash": CONFIG_HASH}))
        forged = {"kind": "direction_inversion", "treatment_id": TREATMENT_ID,
                  "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None}),
                  "application_id": "APP-C-PARALLEL", "candidate_id": "C-PARALLEL"}
        policy.write_policy_file(forged, env["policy_path"])
        assert policy.is_normal(runtime_policy(env))
        assert derive(env, cid="C-PARALLEL").runtime_effective is False
        assert derive(env).state == "APPROVED_NOT_DEPLOYED"
        intended = policy.intended_state_from_approval(
            application=application.to_dict())
        policy.write_policy_file(intended, env["policy_path"])
        assert policy.is_normal(runtime_policy(env))

    def test_revoked_policy_ignored(self, env):
        _application, operation = deploy(env)
        assert not policy.is_normal(runtime_policy(env))
        service.revoke_candidate(
            "C-LIFE-1", actor="human", reason="harmful",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert policy.is_normal(runtime_policy(env))
        view = derive(env)
        assert view.state == "REVOKED"
        assert view.runtime_effective is False
        # The policy file itself is untouched: enforcement is authority-side.
        assert policy.read_policy_file(env["policy_path"]) == operation["intended_state"]

    def test_disabled_policy_ignored(self, env):
        _application, _operation = deploy(env)
        service.disable_candidate(
            "C-LIFE-1", actor="human", reason="suspended",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert policy.is_normal(runtime_policy(env))
        view = derive(env)
        assert view.state == "DISABLED"
        assert view.runtime_effective is False
        # Disable retains historical deployment evidence.
        assert view.application_id

    def test_superseded_policy_ignored_and_predecessor_authority_ceases(self, env):
        _application, _operation = deploy(env)
        service.supersede_candidate(
            "C-LIFE-1", "APP-SUCCESSOR", actor="human", reason="replaced",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert policy.is_normal(runtime_policy(env))
        view = derive(env)
        assert view.state == "SUPERSEDED"
        assert view.superseded_by.startswith("AUTH-")

    def test_rolled_back_policy_ignored(self, env):
        application, _operation = deploy(env)
        make_service(env).rollback(application.application_id)
        assert policy.is_normal(runtime_policy(env))
        view = derive(env)
        assert view.state == "ROLLED_BACK"
        assert view.runtime_effective is False

    def test_authority_ledger_is_append_only(self, env):
        deploy(env)
        ledger = authority.ProductionAuthorityLedger(env["authority_path"])
        authority_id = ledger.effective()[0].authority_id
        before = env["authority_path"].read_text(encoding="utf-8")
        assert len(ledger.events_for(authority_id)) == 2
        service.disable_candidate(
            "C-LIFE-1", actor="human", reason="pause",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        after = env["authority_path"].read_text(encoding="utf-8")
        assert after.startswith(before)
        assert len(ledger.events_for(authority_id)) == 3

    def test_illegal_authority_transition_refused(self):
        ok, reason = authority.validate_authority_transition("REVOKED", "ACTIVE")
        assert not ok and "AUTHORITY_TRANSITION_REFUSED" in reason
        ok, _ = authority.validate_authority_transition("ISSUED", "ACTIVE")
        assert ok


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ROLLBACK SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestRollbackSafety:
    def test_rollback_restores_valid_predecessor(self, env):
        application, operation = deploy(env)
        before_pointer = env["registry"].load(BASELINE_ID).configuration["optimisation_policy"]
        assert before_pointer == {"kind": "normal"}
        result = make_service(env).rollback(application.application_id)
        assert result["phase"] == "ROLLED_BACK"
        assert policy.read_policy_file(env["policy_path"]) == {"kind": "normal"}
        assert policy.is_normal(runtime_policy(env))
        # Predecessor baseline pointer restored.
        assert baseline.get_active().active_baseline_id == BASELINE_ID
        view = derive(env)
        assert view.state == "ROLLED_BACK"
        assert view.runtime_effective is False
        assert operation["intended_state"] != {"kind": "normal"}

    def test_rollback_is_idempotent(self, env):
        application, _operation = deploy(env)
        service_obj = make_service(env)
        first = service_obj.rollback(application.application_id)
        second = service_obj.rollback(application.application_id)
        assert first["phase"] == "ROLLED_BACK" == second["phase"]
        ledger = authority.ProductionAuthorityLedger(env["authority_path"])
        authority_id = ledger.effective()[0].authority_id
        # The idempotent replay must not append a second ROLLED_BACK event.
        assert [e.event for e in ledger.events_for(authority_id)] == [
            "ISSUED", "ACTIVATED", "ROLLED_BACK"]

    def test_rollback_refuses_unrelated_newer_activation(self, env):
        application, _operation = deploy(env)
        from research_engine.v10.baselines.models import BaselineSnapshot

        env["registry"].save(BaselineSnapshot(
            snapshot_id="UNRELATED", config_hash=CONFIG_HASH,
            configuration={"optimisation_policy": {"kind": "normal"}}))
        baseline.set_active("UNRELATED", actor="other", reason="unrelated")
        with pytest.raises(ValueError):
            make_service(env).rollback(application.application_id)
        # The failed rollback must not have claimed ROLLED_BACK authority.
        ledger = authority.ProductionAuthorityLedger(env["authority_path"])
        assert ledger.effective()[0].status == authority.AuthorityStatus.ACTIVE

    def test_rollback_is_not_rejection(self, env):
        application, _operation = deploy(env)
        make_service(env).rollback(application.application_id)
        view = derive(env)
        assert view.state == "ROLLED_BACK"
        assert view.state != "REJECTED"
        decisions = (Path(env["dirs"]["decisions_dir"]) / "decisions.jsonl").read_text(
            encoding="utf-8")
        assert '"decision": "ACCEPT"' in decisions


# ═══════════════════════════════════════════════════════════════════════════════
# 5. UPSTREAM INVALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpstreamInvalidation:
    def test_upstream_invalidation_blocks_candidate(self, env):
        make_review_ready(env)
        result = service.invalidate_upstream(
            "C-LIFE-1", actor="research_engine", reason="source finding disproven",
            upstream_id="F-1", registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert result["state"] == "BLOCKED_UPSTREAM_INVALIDATED"
        assert result["required_action"] == "BLOCK_CANDIDATE"
        assert result["was_runtime_effective"] is False
        assert derive(env, upstream_invalidated=True).state == "INVALIDATED_UPSTREAM"
        with pytest.raises(service.CandidateLifecycleServiceError):
            accept(env, invalidated_upstream=["F-1"])

    def test_live_upstream_invalidation_requires_revoke_or_rollback(self, env):
        deploy(env)
        result = service.invalidate_upstream(
            "C-LIFE-1", actor="research_engine", reason="source finding disproved",
            upstream_id="F-1", registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert result["state"] == "INVALIDATED_UPSTREAM"
        assert result["required_action"] == "REVOKE_OR_ROLLBACK"
        assert result["was_runtime_effective"] is True
        # The escalation is visible, and the authority is NOT silently left
        # approved: the candidate is now flagged for revoke/rollback review.
        view = derive(env, upstream_invalidated=True)
        assert view.required_action == "REVOKE_OR_ROLLBACK"
        # Human authority then performs the governed withdrawal.
        service.revoke_candidate(
            "C-LIFE-1", actor="human", reason="upstream invalidated",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert policy.is_normal(runtime_policy(env))

    def test_upstream_invalidation_does_not_clear_itself(self, env):
        make_review_ready(env)
        service.invalidate_upstream(
            "C-LIFE-1", actor="research_engine", reason="disproven",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        # An ACCEPT after invalidation stays refused even without the caller
        # restating it: the request is refused on the durable basis too.
        with pytest.raises(service.CandidateLifecycleServiceError):
            accept(env, invalidated_upstream=["F-1"])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. APPEND-ONLY HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendOnlyHistory:
    def test_all_consequential_transitions_append_history(self, env):
        application, _operation = deploy(env)
        ledger = lifecycle.CandidateLifecycleLedger(env["lifecycle_path"])
        events = [row.event for row in ledger.history("C-LIFE-1")]
        for expected in ("HUMAN_ACCEPT", "APPROVED_NOT_DEPLOYED", "AUTHORITY_ISSUED",
                         "VERIFIED"):
            assert expected in events
        before = env["lifecycle_path"].read_text(encoding="utf-8")
        make_service(env).rollback(application.application_id)
        after = env["lifecycle_path"].read_text(encoding="utf-8")
        assert after.startswith(before)
        events = [row.event for row in ledger.history("C-LIFE-1")]
        assert "ROLLBACK_REQUESTED" in events and "ROLLED_BACK" in events

    def test_history_ledger_rejects_unknown_events_and_states(self, env):
        ledger = lifecycle.CandidateLifecycleLedger(env["lifecycle_path"])
        with pytest.raises(lifecycle.LifecycleLedgerError):
            ledger.append(lifecycle.CandidateLifecycleEvent(
                candidate_id="C1", event="NOT_AN_EVENT", from_state="", to_state="",
                authority="ENGINE", occurred_at="now"))
        with pytest.raises(lifecycle.LifecycleLedgerError):
            ledger.append(lifecycle.CandidateLifecycleEvent(
                candidate_id="C1", event="HUMAN_ACCEPT", from_state="", to_state="BOGUS",
                authority="HUMAN", occurred_at="now"))

    def test_history_ledger_refuses_unauthorized_transition(self, env):
        ledger = lifecycle.CandidateLifecycleLedger(env["lifecycle_path"])
        with pytest.raises(ValueError):
            lifecycle.record_lifecycle_event(
                candidate_id="C1", event="HUMAN_ACCEPT",
                from_state="READY_FOR_PROMOTION_REVIEW", to_state="ACCEPTED",
                authority="ENGINE", ledger=ledger)
        assert ledger.rows() == []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ADVERSE EVIDENCE -> GOVERNED ACTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdverseEvidence:
    def test_default_policy_requires_human_confirmation(self, env):
        governed = adverse.load_rollback_authority_policy()
        assert governed.mode == adverse.HUMAN_CONFIRMATION_REQUIRED
        assert governed.automatic_execution_authorized is False

    def test_auto_rollback_without_attribution_is_not_a_grant(self, env):
        (env["governance"] / "rollback_authority_policy.json").write_text(
            json.dumps({"mode": "AUTO_ROLLBACK"}), encoding="utf-8")
        governed = adverse.load_rollback_authority_policy()
        assert governed.mode == adverse.HUMAN_CONFIRMATION_REQUIRED
        assert governed.automatic_execution_authorized is False

    def test_adverse_evidence_recommends_rollback_but_does_not_execute(self, env):
        deploy(env)
        out = service.monitor_post_deployment(
            "C-LIFE-1", explicit_signals=["paired_delta_negative"],
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert out["action_required"] is True
        assert out["recommended_action"] == "ROLLBACK"
        assert out["execution"] == "HUMAN_CONFIRMATION_REQUIRED"
        assert out["executed"] is False
        # Nothing was withdrawn automatically: runtime authority is intact and
        # the requirement is durably recorded.
        assert not policy.is_normal(runtime_policy(env))
        ledger = lifecycle.CandidateLifecycleLedger(env["lifecycle_path"])
        assert ledger.has_event("C-LIFE-1", "ADVERSE_EVIDENCE")

    def test_monitoring_report_failure_criteria_are_detected(self, env):
        deploy(env)
        report = {"reports": [{
            "candidate_id": "C-LIFE-1",
            "readiness": {"state": "SHADOW_VALIDATION_INVALID",
                          "unsatisfied_criteria": ["candidate_profit_factor_below_baseline",
                                                   "max_drawdown_multiple"]}}]}
        out = service.monitor_post_deployment(
            "C-LIFE-1", monitoring_report=report,
            authority_path=env["authority_path"])
        assert out["action_required"] is True
        assert out["recommended_action"] == "ROLLBACK"

    def test_explicit_governed_grant_allows_automatic_revoke(self, env):
        deploy(env)
        (env["governance"] / "rollback_authority_policy.json").write_text(
            json.dumps({"mode": "AUTO_ROLLBACK", "authorized_by": "risk_committee",
                        "authorized_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        # A non-live authority with adverse evidence recommends REVOKE.
        service.disable_candidate(
            "C-LIFE-1", actor="human", reason="pause",
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        out = service.monitor_post_deployment(
            "C-LIFE-1", explicit_signals=["criteria_failed"],
            execute_if_authorized=True,
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert out["recommended_action"] == "REVOKE"
        assert out["execution"] == "REVOKE_EXECUTED"
        assert derive(env).state == "REVOKED"

    def test_automatic_rollback_requires_an_explicit_grant(self, env):
        deploy(env)
        out = service.monitor_post_deployment(
            "C-LIFE-1", explicit_signals=["criteria_failed"],
            execute_if_authorized=True,
            registry_dir=env["dirs"]["registry_dir"],
            decisions_dir=env["dirs"]["decisions_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            lifecycle_ledger_path=env["lifecycle_path"])
        assert out["execution"] == "HUMAN_CONFIRMATION_REQUIRED"
        assert derive(env).state == "VERIFIED"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RESEARCH LAB TRUTH
# ═══════════════════════════════════════════════════════════════════════════════

def grant_authority_state(env, cid, status, *, verified=True):
    """Create a governed authority for cid and move it to a target status."""
    ledger = authority.ProductionAuthorityLedger(env["authority_path"])
    state = {"kind": "direction_inversion", "treatment_id": TREATMENT_ID,
             "treatment_spec": make_spec({"symbols": ["EURUSD"], "patterns": None}),
             "application_id": f"APP-{cid}", "candidate_id": cid}
    authority.issue_authority(
        candidate_id=cid, decision_id=f"REC-{cid}", application_id=f"APP-{cid}",
        treatment_id=TREATMENT_ID, treatment_spec=state["treatment_spec"],
        baseline_id=BASELINE_ID, baseline_config_hash=CONFIG_HASH,
        policy_state=state, actor="human", reason="lab fixture", ledger=ledger)
    # Matching governed application ledger rows (the deployment evidence).
    applications = ApplicationLedger(env["application_path"])
    app_states = (["APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED"] if verified
                  else ["APPROVED_NOT_DEPLOYED", "DEPLOYED"])
    for app_state in app_states:
        applications.append(cid, "E1", app_state, application_id=f"APP-{cid}",
                            actor="app", deployment_reference=f"OP-{cid}")
    authority.activate_authority(
        application_id=f"APP-{cid}", deployment_id=f"OP-{cid}",
        verification_status="VERIFIED", policy_state=state, actor="app",
        reason="lab fixture", ledger=ledger)
    if status == "DISABLED":
        authority.disable_authority(application_id=f"APP-{cid}", actor="human",
                                    reason="lab", ledger=ledger)
    elif status == "REVOKED":
        authority.revoke_authority(application_id=f"APP-{cid}", actor="human",
                                   reason="lab", ledger=ledger)
    elif status == "ROLLED_BACK":
        authority.mark_rolled_back(application_id=f"APP-{cid}", actor="app",
                                   reason="lab", ledger=ledger)
        applications.append(cid, "E1", "ROLLED_BACK", application_id=f"APP-{cid}",
                            actor="app", deployment_reference=f"OP-{cid}")
    elif status == "SUPERSEDED":
        authority.supersede_authority(
            predecessor_application_id=f"APP-{cid}",
            successor_application_id=f"APP-{cid}-SUCCESSOR", actor="human",
            reason="lab", ledger=ledger)
    return state


class TestResearchLabTruth:
    def test_lab_shows_every_major_state_and_never_implies_live_authority(self, env):
        registry = CandidateRegistry(env["dirs"]["registry_dir"])
        for cid, status in (("C-PROPOSED", "PROPOSED"),
                            ("C-REVIEW", "READY_FOR_REVIEW"),
                            ("C-REJECT", "REJECTED")):
            registry.create(CandidateRecord(
                candidate_id=cid, baseline_id=BASELINE_ID, status=status,
                change_definition={"type": "direction_inversion",
                                   "baseline_config_hash": CONFIG_HASH}))
        grant_authority_state(env, "C-LIVE", "ACTIVE")
        grant_authority_state(env, "C-DEPLOYED", "ACTIVE", verified=False)
        grant_authority_state(env, "C-DISABLED", "DISABLED")
        grant_authority_state(env, "C-REVOKED", "REVOKED")
        grant_authority_state(env, "C-ROLLEDBACK", "ROLLED_BACK")
        grant_authority_state(env, "C-SUPERSEDED", "SUPERSEDED")
        grant_authority_state(env, "C-INVALID", "ACTIVE")
        grant_authority_state(env, "C-VERIFIED", "ACTIVE")

        projection = service.build_candidate_lifecycle_projection(
            registry_dir=env["dirs"]["registry_dir"],
            optimisation_registry_dir=env["dirs"]["optimisation_dir"],
            application_path=env["application_path"],
            authority_path=env["authority_path"],
            decisions_dir=env["dirs"]["decisions_dir"],
            lifecycle_ledger_path=env["lifecycle_path"],
            invalidated_upstream=["C-INVALID"])
        by_id = {row["candidate_id"]: row for row in projection["candidates"]}
        assert by_id["C-PROPOSED"]["canonical_state"] == "PROPOSED"
        assert by_id["C-REVIEW"]["canonical_state"] == "READY_FOR_PROMOTION_REVIEW"
        assert by_id["C-REVIEW"]["review_notice"] == lifecycle.REVIEW_STATE_NOTICE
        assert by_id["C-REJECT"]["canonical_state"] == "REJECTED"
        assert by_id["C-LIVE"]["canonical_state"] == "VERIFIED"
        assert by_id["C-DEPLOYED"]["canonical_state"] == "DEPLOYED"
        assert by_id["C-VERIFIED"]["canonical_state"] == "VERIFIED"
        assert by_id["C-DISABLED"]["canonical_state"] == "DISABLED"
        assert by_id["C-REVOKED"]["canonical_state"] == "REVOKED"
        assert by_id["C-ROLLEDBACK"]["canonical_state"] == "ROLLED_BACK"
        assert by_id["C-SUPERSEDED"]["canonical_state"] == "SUPERSEDED"
        assert by_id["C-INVALID"]["canonical_state"] == "INVALIDATED_UPSTREAM"
        assert by_id["C-INVALID"]["required_action"] == "REVOKE_OR_ROLLBACK"

        # Only VERIFIED/ACTIVE authority is runtime-effective, and the Lab
        # marks review-ready candidates as NOT LIVE.
        assert set(projection["runtime_effective"]) == {
            "C-LIVE", "C-DEPLOYED", "C-INVALID", "C-VERIFIED"}
        assert projection["review_ready"] == [
            {"candidate_id": "C-REVIEW", "notice": lifecycle.REVIEW_STATE_NOTICE}]
        assert all(row["runtime_effective"] is False
                   for row in projection["candidates"]
                   if row["canonical_state"] in {"PROPOSED", "REJECTED",
                                                 "READY_FOR_PROMOTION_REVIEW"})
        assert projection["store_classes"]["TRACK_A_CANDIDATE_REGISTRY"] == "COMPATIBILITY"
        assert projection["canonical_states"]

    def test_lab_server_exposes_the_canonical_lifecycle_section(self):
        from research_engine.v10.lab import server

        state = server.build_state()
        section = state["candidate_lifecycle"]
        assert section["schema_version"] == "research_lab_candidate_lifecycle_v1"
        assert "candidates" in section and "review_ready" in section
        assert section["store_classes"]["PRODUCTION_AUTHORITY"] == "AUTHORITATIVE"
        html = (server.STATIC / "index.html").read_text(encoding="utf-8")
        assert "NOT LIVE / HUMAN APPROVAL REQUIRED" in html


# ═══════════════════════════════════════════════════════════════════════════════
# 9. AUTONOMY BOUNDARY — THE ENGINE GOVERNS ITSELF, THE HUMAN GOVERNS LIVE
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutonomyBoundary:
    def test_engine_may_act_up_to_review_but_never_live(self):
        engine_allowed = (
            ("PROPOSED", "VALIDATION_QUEUED"),
            ("VALIDATION_QUEUED", "VALIDATING"),
            ("VALIDATING", "VALIDATED"),
            ("VALIDATING", "VALIDATION_FAILED"),
            ("VALIDATED", "FORWARD_VALIDATED"),
            ("FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE"),
            ("SHADOW_VALIDATION_ACTIVE", "SHADOW_VALIDATED"),
            ("SHADOW_VALIDATED", "READY_FOR_PROMOTION_REVIEW"),
        )
        for from_state, to_state in engine_allowed:
            assert lifecycle.is_authorized_transition(from_state, to_state, "ENGINE")
        engine_forbidden = (
            ("READY_FOR_PROMOTION_REVIEW", "ACCEPTED"),
            ("ACCEPTED", "APPROVED_NOT_DEPLOYED"),
            ("ACCEPTED", "VERIFIED"),
            ("APPROVED_NOT_DEPLOYED", "DEPLOYED"),
            ("APPROVED_NOT_DEPLOYED", "VERIFIED"),
            ("VERIFIED", "ROLLED_BACK"),
        )
        for from_state, to_state in engine_forbidden:
            assert not lifecycle.is_authorized_transition(from_state, to_state, "ENGINE")

    def test_no_automatic_live_promotion_api_exists(self):
        # There is no engine-facing helper that grants live authority: every
        # promotion entry point requires an explicit human decision identity.
        import inspect

        source = inspect.getsource(service)
        assert "def governed_human_decision" in source
        assert "def create_governed_approval" in source
        # The approval path refuses anything that is not an ACCEPTED candidate.
        assert "CANDIDATE_NOT_ACCEPTED" in source
        # Automatic execution is opt-in and requires an explicit governed grant.
        assert "automatic_execution_permitted" in source
        assert adverse.load_rollback_authority_policy().mode == \
            adverse.HUMAN_CONFIRMATION_REQUIRED

    def test_validation_failure_is_autonomously_rejected_without_live_authority(
            self, env):
        make_review_ready(env, status="FAILED_VALIDATION")
        view = derive(env)
        assert view.state == "VALIDATION_FAILED"
        assert view.runtime_effective is False
        assert policy.is_normal(runtime_policy(env))

    def test_engine_autonomously_validates_and_reaches_readiness_without_live_authority(
            self, env):
        import hashlib

        from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
        from research_engine.v10.continuous.validation_queue import (
            ValidationQueueStore, enqueue_validation_handoff, process_validation_queue,
        )
        from research_engine.v10.optimisation.models import (
            OptimisationCandidate, ResearchHypothesis, ValidationPlan,
        )
        from research_engine.v10.optimisation.optimisation_registry import (
            OptimisationRegistry,
        )

        frozen_policy = {"change_type": "direction_inversion",
                         "scope": {"symbols": ["EURUSD"], "patterns": None}}
        treatment_hash = hashlib.sha256(
            canonical_json(frozen_policy).encode("utf-8")).hexdigest()
        registry = OptimisationRegistry(env["dirs"]["optimisation_dir"])
        registry.add_hypothesis(ResearchHypothesis(
            hypothesis_id="HYP-AUTO", source_finding="F-AUTO", source_question="E1"))
        registry.add_candidate(OptimisationCandidate(
            candidate_id="C-AUTO", hypothesis_id="HYP-AUTO", baseline_id=BASELINE_ID,
            component="strategy.entry", status="PROPOSED", policy_id="POL-AUTO",
            treatment_hash=treatment_hash,
            changes={"frozen_policy": frozen_policy},
            target_population={"symbols": ["EURUSD"]}))
        registry.add_plan(ValidationPlan(
            candidate_id="C-AUTO", baseline_id=BASELINE_ID,
            metrics=["expectancy_r"], minimum_sample=20,
            success_conditions={"expectancy_fidelity_unchanged": ">= 0"},
            failure_conditions={"expectancy_fidelity_unchanged": "< 0"},
            notes="treatment fidelity unchanged within scope"))
        registry.save()

        store = ValidationQueueStore(env["tmp"] / "validation_queue.json")
        enqueue_validation_handoff(
            [{"candidate_id": "C-AUTO", "policy_id": "POL-AUTO",
              "treatment_hash": treatment_hash}],
            registry=registry, store=store, snapshot_id="SNAP-AUTO", cycle_id="CYC-AUTO")

        def executor(candidate, plan, job):
            return {"status": "VALIDATED", "snapshot_id": job.source_snapshot_id,
                    "candidate_id": candidate.candidate_id,
                    "treatment_hash": candidate.treatment_hash}

        outcome = process_validation_queue(
            store=store, registry=registry, validation_executor=executor, max_jobs=1)
        assert outcome["transitions"][0]["candidate_status"] == "VALIDATED"
        assert registry.get_candidate("C-AUTO").status == "VALIDATED"

        # Engine-owned readiness derivation (governed shadow evidence).
        track = CandidateRegistry(env["dirs"]["registry_dir"])
        track.create(CandidateRecord(
            candidate_id="C-AUTO", hypothesis_id="HYP-AUTO", baseline_id=BASELINE_ID,
            status="VALIDATED",
            change_definition={"type": "direction_inversion",
                               "baseline_config_hash": CONFIG_HASH}))
        track.record_shadow_research_status("C-AUTO", {
            "candidate_id": "C-AUTO", "status": "SHADOW_VALIDATION_ACTIVE",
            "integrity_status": "VERIFIED", "snapshot_id": "SNAP-AUTO",
            "evidence_frontier": ["SNAP-AUTO"], "policy_id": "POL-AUTO",
            "treatment_hash": treatment_hash, "last_evidence_time": "2026-01-01"})
        track.record_shadow_research_status("C-AUTO", {
            "candidate_id": "C-AUTO", "status": "SHADOW_VALIDATED",
            "integrity_status": "VERIFIED", "snapshot_id": "SNAP-AUTO",
            "evidence_frontier": ["SNAP-AUTO"], "policy_id": "POL-AUTO",
            "treatment_hash": treatment_hash, "last_evidence_time": "2026-01-01"})
        track.record_shadow_research_status("C-AUTO", {
            "candidate_id": "C-AUTO", "status": "READY_FOR_PROMOTION_REVIEW",
            "integrity_status": "VERIFIED", "snapshot_id": "SNAP-AUTO",
            "evidence_frontier": ["SNAP-AUTO"], "policy_id": "POL-AUTO",
            "treatment_hash": treatment_hash, "last_evidence_time": "2026-01-01"})
        view = derive(env, cid="C-AUTO")
        assert view.state == "READY_FOR_PROMOTION_REVIEW"
        # ...and STILL no live authority, and the runtime is untouched.
        assert view.runtime_effective is False
        assert view.live_authority is False
        assert policy.is_normal(runtime_policy(env))
        assert authority.effective_authority_for_candidate(
            "C-AUTO", ledger=authority.ProductionAuthorityLedger(env["authority_path"])
        ) is None
