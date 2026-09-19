"""
Post-sign-off hardening — Wave 4E-6 S3 checkpoint coverage.

Proves the research-state S3 checkpoint allowlist covers ALL canonical durable
lifecycle state required after TOTAL VM loss, including the fail-closed
Wave 4E-6 recovery authorities introduced after the original V1 allowlist, and
that:

  - every canonical Wave 4E-6 artifact is part of the checkpoint contract;
  - checkpoint then wipe (total VM loss) then restore reconstructs the full
    canonical chain byte-for-byte, and the persisted stores reload their
    original records through the REAL store load paths;
  - the Wave-6 verified-transition reconstruction still works on the restored
    Wave 5 truth (and fails closed when tampered with);
  - the Wave-5 frozen evaluation provenance authoritatively survives;
  - non-canonical / cache / temp / derived artifacts are NOT promoted;
  - existing V1 checkpoints (lacking the new artifacts) still restore.

Uses the proven FakeS3 harness (no real AWS).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_state_durability import FakeS3, _durability
import research_engine.lifecycle.state_durability as sd

from research_engine.control_plane.application_ledger import (
    ApplicationLedger,
    ApplicationState,
)
from research_engine.control_plane.application_service import canonical as op_canonical
from research_engine.control_plane.application_service import digest as op_digest
from research_engine.lifecycle.candidate_evidence_continuity import (
    build_candidate_evidence_continuity_state,
    CandidateEvidenceContinuityStore,
)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
    CandidateImpactHistoryStore,
    compute_impact_id,
    reconstruct_verified_transition,
)
from research_engine.lifecycle.candidate_recommendation import (
    CandidateRecommendation,
    RecommendationStore,
)
from research_engine.lifecycle.candidate_reconsideration_history import (
    CandidateReconsiderationRecord,
    CandidateReconsiderationHistoryStore,
)
from research_engine.lifecycle.treatment_provenance import (
    canonical_spec,
    validate_treatment_spec,
    validate_evaluation_spec,
)
from research_engine.v10.candidates.candidate_decision import (
    CandidateDecisionStore,
    HumanDecision,
)

# ── Canonical Wave 4E-6 durable state required after total VM loss ────────────
CANONICAL_WAVE_4E6_FILES = (
    "data/research/lifecycle/recommendations/recommendations.jsonl",
    "data/research/candidates/decisions.jsonl",
    "data/research/governance/application.jsonl",
    "data/research/lifecycle/impact_history/candidate_impact_history.jsonl",
    "data/research/lifecycle/evidence_continuity/candidate_evidence_continuity.jsonl",
    "data/research/lifecycle/reconsideration_history/candidate_reconsideration_history.jsonl",
)

CANONICAL_WAVE_4E6_GLOBS = (sd._EVALUATIONS_GLOB, sd._OPERATIONS_GLOB)

# Explicitly NOT canonical — these must never be promoted into the checkpoint.
NON_CANONICAL = (
    "data/research/governance/decisions.jsonl",            # control-plane presentation view
    "data/research/research_universe.jsonl",               # S3-authoritative dataset class
    "data/research/segments/instruments/EURUSD.jsonl",     # derived segment view
    "analysis/reports/q1.json",                            # derived report
    "data/baselines/active_baseline.json",                # production baseline authority (4C)
)
# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """Fresh workspace-local cwd; default store dirs resolve under it."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _wipe_canonical():
    for sub in ("data", "logs"):
        if Path(sub).exists():
            shutil.rmtree(sub)


def _collect_empty(before):
    """True when all pre-checkpoint canonical files have been wiped locally."""
    return all(not (Path(".") / rel).exists() for rel in before)


# ── Seed helpers ──────────────────────────────────────────────────────────────


def _treatment_spec():
    spec_dict = {
        "change_type": "direction_inversion",
        "declared": {},
        "scope": {"symbols": ["EURUSD"], "patterns": None},
        "treatment_id": "t1",
    }
    spec = canonical_spec(spec_dict)
    validate_treatment_spec(spec, "t1")  # raises if malformed
    return spec


def _seed_full_wave4e6_chain():
    """Drive the REAL Wave 4E-6 append-only stores to seed canonical truth."""
    spec = _treatment_spec()
    verification = {"phase": "COMPLETED", "checks_passed": 1}
    ve_digest = op_digest(verification)
    op_id = "OP-1"

    # Wave 4E.1 recommendation (deterministic REC-{evaluation_id} authority).
    rec = CandidateRecommendation(
        recommendation_id="REC-EVAL-C1", evaluation_id="EVAL-C1",
        candidate_id="C1", treatment_id="t1", treatment_spec=spec,
        baseline_id="OLD", baseline_config_hash="oldcfg", actionable=True,
    )
    RecommendationStore().append(rec)

        # Wave 4E.2 human decision (canonical governance outcome).
    dec = HumanDecision(
        candidate_id="C1", decision="ACCEPT", actor="human", reason="good",
        timestamp="2026-01-01T00:00:00+00:00", evaluation_id="EVAL-C1",
        status_before="SHADOW_TESTING", status_after="ACCEPTED",
        treatment_id="t1", treatment_spec=spec, baseline_id="OLD",
        baseline_config_hash="oldcfg", outcome="COMPLETED",
                recommendation_id="REC-EVAL-C1",
    )
    CandidateDecisionStore()._append_row_atomic(dec)

    # Wave 4E.3/5 application ledger: APPROVED -> DEPLOYED -> VERIFIED.
    app = ApplicationLedger()
    approved = app.append(
        "C1", "Q24", ApplicationState.APPROVED_NOT_DEPLOYED,
        actor="human", reason="good", timestamp="2026-01-01T00:00:00+00:00",
        application_id="APP-C1", deployment_reference=op_id,
        verification_evidence="VE-0", recommendation_id="REC-EVAL-C1",
        evaluation_id="EVAL-C1", treatment_id="t1", treatment_spec=spec,
        baseline_id="OLD", baseline_config_hash="oldcfg",
        human_decision_outcome="ACCEPT",
    )
    app.append(
        "C1", "Q24", ApplicationState.DEPLOYED,
        actor="runtime", reason="deploy", timestamp="2026-01-02T00:00:00+00:00",
        application_id="APP-C1", deployment_reference=op_id,
        verification_evidence="VE-0", recommendation_id="REC-EVAL-C1",
        evaluation_id="EVAL-C1", treatment_id="t1", treatment_spec=spec,
        baseline_id="OLD", baseline_config_hash="oldcfg",
        human_decision_outcome="ACCEPT",
    )
    app.append(
        "C1", "Q24", ApplicationState.VERIFIED,
        actor="runtime", reason="verified", timestamp="2026-01-03T00:00:00+00:00",
        application_id="APP-C1", deployment_reference=op_id,
        verification_evidence=ve_digest, recommendation_id="REC-EVAL-C1",
        evaluation_id="EVAL-C1", treatment_id="t1", treatment_spec=spec,
        baseline_id="OLD", baseline_config_hash="oldcfg",
        human_decision_outcome="ACCEPT",
    )

    # Wave 5 fsynced VERIFIED-transition operation file (fail-closed truth).
    op = {
        "application": approved.to_dict(),
        "operation_id": op_id,
        "phase": "COMPLETED",
        "verification": verification,
        "old_snapshot": {"snapshot_id": "OLD", "config_hash": "oldcfg"},
        "snapshot": {"snapshot_id": "NEW", "config_hash": "newcfg"},
    }
    ops_dir = Path("data/research/governance/operations")
    ops_dir.mkdir(parents=True, exist_ok=True)
    (ops_dir / (op_digest("APP-C1") + ".json")).write_text(op_canonical(op))

    # Wave 5.3A frozen evaluation provenance — the ONLY durable source of
    # treatment_spec/baseline provenance (CandidateRecord does not carry them).
    ev_dir = Path("logs/research_lifecycle/evaluations")
    ev_dir.mkdir(parents=True, exist_ok=True)
    ev_row = {
        "evaluation_id": "EVAL-C1", "candidate_id": "C1",
        "treatment_id": "t1", "treatment_spec": spec,
        "baseline_id": "OLD", "config_hash": "oldcfg",
    }
    (ev_dir / "C1.jsonl").write_text(json.dumps(ev_row) + "\n")

    # Wave 6.1B append-only impact history (deterministic identity).
    impact_id = compute_impact_id(
        candidate_id="C1", candidate_baseline_id="OLD",
        candidate_baseline_config_hash="oldcfg", from_baseline_id="OLD",
        from_baseline_config_hash="oldcfg", to_baseline_id="NEW",
        to_baseline_config_hash="newcfg", application_id="APP-C1",
        candidate_treatment_id="t1", deployed_treatment_id="t1",
    )
    impact = CandidateBaselineImpactRecord(
        impact_id=impact_id, candidate_id="C1", candidate_baseline_id="OLD",
        candidate_baseline_config_hash="oldcfg", from_baseline_id="OLD",
        from_baseline_config_hash="oldcfg", to_baseline_id="NEW",
        to_baseline_config_hash="newcfg", application_id="APP-C1",
        candidate_treatment_id="t1", deployed_treatment_id="t1",
        classification="BASELINE_CHANGE_UNAFFECTED",
        reason_codes=("BASELINE_CHANGE_UNAFFECTED",),
        candidate_scope=None, deployed_scope=None,
    )
    CandidateImpactHistoryStore().append(impact)

    # Wave 6.2B append-only continuity history (built via the canonical builder).
    impact_store = CandidateImpactHistoryStore()
    continuity = build_candidate_evidence_continuity_state(
        impact_record=impact, decisions=(), impact_store=impact_store,
    )
    CandidateEvidenceContinuityStore().append(continuity)

    # Wave 6.3B append-only reconsideration history (deterministic identity).
    rec_id = "RCN-1"
    reconsideration = CandidateReconsiderationRecord(
        reconsideration_id=rec_id, historical_candidate_id="C1",
        historical_baseline_id="OLD", historical_baseline_config_hash="oldcfg",
        target_baseline_id="NEW", target_baseline_config_hash="newcfg",
        impact_id=impact_id, continuity_id=continuity.continuity_id,
        candidate_treatment_id="t1", application_id="APP-C1", operation_id=op_id,
        transition_candidate_id="C1-NEXT", historical_candidate_status="COMPLETED",
        historical_evaluation_outcome="PROMOTED", historical_human_decision="ACCEPT",
        reconsideration_status="ELIGIBLE_FOR_RECONSIDERATION",
        reason_codes=("BASELINE_CHANGE_UNAFFECTED",), fresh_evidence_required=False,
    )
    CandidateReconsiderationHistoryStore().append(reconsideration)

    return {
        "spec": spec, "impact": impact, "continuity": continuity,
        "continuity_id": continuity.continuity_id, "impact_id": impact_id,
        "rec_id": rec_id,
    }



# ── Tests: checkpoint contract coverage ───────────────────────────────────────


class TestWave4E6CheckpointContract:
    def test_all_canonical_wave4e6_files_are_in_allowlist(self):
        missing = [a for a in CANONICAL_WAVE_4E6_FILES if a not in sd.CHECKPOINT_ARTIFACTS]
        assert missing == [], (
            f"canonical artifacts missing from allowlist: {missing}")

    def test_canonical_wave4e6_globs_are_registered(self):
        for g in CANONICAL_WAVE_4E6_GLOBS:
            assert g in sd._CHECKPOINT_GLOBS, f"canonical glob missing from collection: {g}"

    def test_new_artifacts_pass_exclusion_guard(self):
        for path in CANONICAL_WAVE_4E6_FILES:
            assert not sd._is_excluded(path), f"canonical artifact wrongly excluded: {path}"

    @pytest.mark.parametrize("path", CANONICAL_WAVE_4E6_FILES)
    def test_each_canonical_file_is_individually_checkpointed(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"canonical-body")
        try:
            collected = sd._collect_local_artifacts()
            assert path in collected
            assert collected[path] == b"canonical-body"
        finally:
            Path(path).unlink(missing_ok=True)


class TestNonCanonicalNotPromoted:
    def test_non_canonical_paths_are_not_in_allowlist(self):
        joined = " ".join(sd.CHECKPOINT_ARTIFACTS) + " " + " ".join(sd._CHECKPOINT_GLOBS)
        for path in NON_CANONICAL:
            assert path not in sd.CHECKPOINT_ARTIFACTS
            assert path not in sd._CHECKPOINT_GLOBS

    def test_non_canonical_files_are_never_collected(self):
        _seed_full_wave4e6_chain()
        # seed non-canonical (presentation view, derived views, temp, lock)
        Path("data/research/governance/decisions.jsonl").parent.mkdir(parents=True, exist_ok=True)
        Path("data/research/governance/decisions.jsonl").write_text("{}")
        Path("data/research/research_universe.jsonl").write_text("{}")
        Path("data/research/segments/instruments/EURUSD.jsonl").parent.mkdir(parents=True, exist_ok=True)
        Path("data/research/segments/instruments/EURUSD.jsonl").write_text("{}")
        Path("analysis/reports/q1.json").parent.mkdir(parents=True, exist_ok=True)
        Path("analysis/reports/q1.json").write_text("{}")
        Path("logs/research_lifecycle/evaluations/C1.jsonl.tmp").write_text("tmp")
        Path("data/research/governance/decisions.jsonl.lock").write_text("lock")

        collected = sd._collect_local_artifacts()
        for non_canonical in (
            "data/research/governance/decisions.jsonl",
            "data/research/research_universe.jsonl",
            "data/research/segments/instruments/EURUSD.jsonl",
            "analysis/reports/q1.json",
            "logs/research_lifecycle/evaluations/C1.jsonl.tmp",
            "data/research/governance/decisions.jsonl.lock",
        ):
            assert non_canonical not in collected, (
                f"non-canonical artifact was promoted into checkpoint: {non_canonical}")

# ── Tests: total VM-loss recovery ────────────────────────────────────────────


class TestWave4E6CheckpointRestore:
    def test_checkpoint_then_vm_loss_restore_reconstructs_full_chain(self):
        seed = _seed_full_wave4e6_chain()
        before = sd._collect_local_artifacts()
        assert before, "expected canonical state present locally"
        # every canonical Wave 4E-6 artifact class is represented in the upload
        for f in CANONICAL_WAVE_4E6_FILES:
            assert f in before, f"canonical file not collected pre-checkpoint: {f}"
        assert "logs/research_lifecycle/evaluations/C1.jsonl" in before
        ops_name = next(
            p for p in before
            if p.startswith("data/research/governance/operations/"))

        fake = FakeS3()
        dur = _durability(fake)
        result = dur.checkpoint(cycle_id="RC-wave6")
        assert result.status == "durable", result.error
        assert result.artifact_count == len(before)

        # manifest (completion marker) must enumerate every canonical artifact
        manifest_key = dur._key("checkpoints", result.checkpoint_id, "manifest.json")
        manifest = json.loads(fake.objects[manifest_key])
        assert manifest["status"] == "complete"
        manifest_paths = {a["path"] for a in manifest["artifacts"]}
        for f in before:
            assert f in manifest_paths, f"{f} missing from manifest"

        # total VM loss: wipe every canonical local artifact
        _wipe_canonical()
        assert _collect_empty(before), "local state not fully wiped"

        restored = dur.restore_if_needed()
        assert restored.status == "recovered", restored.error
        after = sd._collect_local_artifacts()

        # byte-for-byte reconstruction of ALL canonical state
        assert after == before, "restored canonical state differs from checkpoint"

        # ── REAL store load paths confirm the archive is structurally valid
        #    recoverable Research Engine state (not just loose bytes). ───────

        # Wave 4E.2 canonical human decisions
        decisions = CandidateDecisionStore()
        assert decisions.get_decision("C1").outcome == "COMPLETED"
        assert decisions.get_decision("C1").decision == "ACCEPT"

        # Wave 4E.1 canonical recommendations
        assert RecommendationStore().get_by_recommendation_id("REC-EVAL-C1") is not None

        # Wave 4E.3/5 application ledger (full APPROVED->DEPLOYED->VERIFIED)
        assert [r.state for r in ApplicationLedger().list_all()] == [
            "APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED"]

        # Wave 6.1B impact history
        impact_store = CandidateImpactHistoryStore()
        restored_impact = impact_store.get(seed["impact_id"])
        assert restored_impact.to_dict() == seed["impact"].to_dict()

        # Wave 6.2B continuity history
        continuity = CandidateEvidenceContinuityStore().get(seed["continuity_id"])
        assert continuity.to_dict() == seed["continuity"].to_dict()

        # Wave 6.3B reconsideration history
        restored_rec = CandidateReconsiderationHistoryStore().get(seed["rec_id"])
        assert restored_rec is not None
        assert restored_rec.impact_id == seed["impact_id"]

        # Wave 5.3A frozen evaluation provenance authoritatively reloads
        validate_evaluation_spec(
            SimpleNamespace(candidate_id="C1", evaluation_id="EVAL-C1",
                            treatment_id="t1", treatment_spec=seed["spec"]),
            evaluations_dir="logs/research_lifecycle/evaluations",
        )

        # Wave 6 verified-transition reconstruction succeeds on the restored
        # Wave 5 truth (operations file + application ledger).
        transition = reconstruct_verified_transition(
            "APP-C1",
            application_path="data/research/governance/application.jsonl",
            operations_dir="data/research/governance/operations",
        )
        assert transition.to_baseline_id == "NEW"
        assert transition.deployed_treatment_id == "t1"
        assert transition.deployed_treatment_spec == seed["spec"]

        # Tamper: drop the restored operation file -> reconstruction FAILS
        # CLOSED (proving it is the canonical transition proof, not a cache).
        Path(ops_name).unlink()
        with pytest.raises(Exception):
            reconstruct_verified_transition(
                "APP-C1",
                application_path="data/research/governance/application.jsonl",
                operations_dir="data/research/governance/operations",
            )



# ── Tests: V1 compatibility ──────────────────────────────────────────────────


class TestV1Compatibility:
    def test_existing_v1_checkpoint_without_new_artifacts_still_restores(self):
        """A pre-Wave-4E-6 V1 manifest must still restore (additive contract)."""
        legacy = {"logs/research_lifecycle/registry.json": b"{}\n"}
        fake = FakeS3()
        dur = _durability(fake)
        ckpt_id = "legacy-v1"
        key = dur._key
        entries = []
        for rel, body in legacy.items():
            fake.put_object(Bucket="test-bucket",
                            Key=key("checkpoints", ckpt_id, "artifacts", rel),
                            Body=body)
            entries.append({"path": rel, "size": len(body),
                            "sha256": sd._sha256_bytes(body)})
        manifest = {
            "schema": "research_state_checkpoint_v1",
            "contract_version": sd._CONTRACT_VERSION,
            "checkpoint_id": ckpt_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "generation": 1, "previous_checkpoint_id": "",
            "cycle_id": "RC-legacy", "dataset_fingerprint": "",
            "status": "complete", "artifacts": entries,
        }
        fake.put_object(Bucket="test-bucket",
                        Key=key("checkpoints", ckpt_id, "manifest.json"),
                        Body=json.dumps(manifest).encode())
        fake.put_object(Bucket="test-bucket", Key=key("latest_success.json"),
                        Body=json.dumps({"checkpoint_id": ckpt_id, "generation": 1,
                                         "previous_checkpoint_id": "",
                                         "manifest_key": key("checkpoints", ckpt_id,
                                                             "manifest.json"),
                                         "completed_at": "2026-01-01T00:00:00+00:00",
                                         "artifact_count": len(entries),
                                         "contract_version": sd._CONTRACT_VERSION
                                         }).encode())

        _wipe_canonical()
        restored = dur.restore_if_needed()
        assert restored.status == "recovered"
        assert (Path("logs/research_lifecycle/registry.json").read_bytes() == b"{}\n")
