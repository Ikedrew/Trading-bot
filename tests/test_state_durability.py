"""
Gap 8 — research-state durability tests (fake in-memory S3; no real AWS).

Proves:
  - durable-state allowlist: locks/temp/secrets/production evidence excluded
  - checkpoint creation with manifest correctness and last-advancing pointer
  - partial upload can never become latest; failures surface loudly
  - total local-state loss → full restore with identical identities
  - full candidate lifecycle chain survives (finding→trigger→hypothesis→
    candidate→SHADOW_TESTING→history; READY_FOR_REVIEW preserved)
  - conflict handling: local present = runtime authority; fresh machine +
    no checkpoint = honest fresh start; corrupt latest → complete fallback
  - S3 unavailable → loud DurabilityError (no local production fallback)
  - weekly-cycle integration: recovery at start, checkpoint on success
  - credential model: RESEARCH_AWS_PROFILE locally, default chain unset
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.lifecycle import cycle_snapshot as cs
from research_engine.lifecycle import state_durability as sd
from research_engine.lifecycle.finding_trigger import (
    EligibilityConfig,
    FindingTriggerEngine,
    stamp_provenance,
)
from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleConfig,
    ResearchCycleRunner,
)
from research_engine.lifecycle.registry import InvestigationRegistry
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord

try:
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    ClientError = None


def _no_such_key():
    return ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
        "GetObject",
    )


class FakeS3:
    """In-memory S3 client (fake — no real AWS)."""

    def __init__(self, fail_prefixes: tuple[str, ...] = (), fail_all: bool = False):
        self.objects: dict[str, bytes] = {}
        self._fail_prefixes = fail_prefixes
        self._fail_all = fail_all

    def put_object(self, Bucket: str, Key: str, Body: Any):
        if self._fail_all or any(Key.startswith(p) for p in self._fail_prefixes):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}},
                              "PutObject")
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket: str, Key: str):
        if Key not in self.objects:
            raise _no_such_key()
        return {"Body": io.BytesIO(self.objects[Key])}


def _durability(client: FakeS3) -> sd.ResearchStateDurability:
    return sd.ResearchStateDurability(bucket="test-bucket", client=client)


def _seed_local_state() -> dict[str, Any]:
    """Production-shaped lifecycle: finding→trigger→hypothesis→candidate."""
    engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=30))
    trigger = engine.detect_from_pattern_performance(
        pattern="HAMMER", mean_r=-0.4, win_rate=0.05, sample_size=60)
    stamp_provenance(
        [trigger],
        source_datasets=["shadow_runtime_v1(ingested)"],
        dataset_fingerprint="fp-gap8",
        evidence_as_of="2026-09-06T12:00:00+00:00",
    )

    orch_registry = InvestigationRegistry()
    from research_engine.lifecycle.hypothesis import (
        Hypothesis, HypothesisCategory, HypothesisStatus,
    )
    hyp = Hypothesis(
        hypothesis_id="H-GAP8", title="Edge: poor HAMMER performance",
        description="d", claim="c", null_hypothesis="n",
        category=HypothesisCategory.PATTERN_SIGNAL, source="research_cycle:TRG-x",
        source_finding_id=trigger.finding_id,
    )
    hyp.transition(HypothesisStatus.REGISTERED, reason="seed")
    orch_registry.register(hyp)
    engine.mark_registered(trigger.trigger_id, "H-GAP8")

    registry = CandidateRegistry()
    candidate = CandidateRecord(
        candidate_id="OPT-GAP8", hypothesis_id="H-GAP8",
        component="pattern", created_from_question="Q24",
        description="Invert HAMMER", status="SHADOW_TESTING",
    )
    from research_engine.v10.candidates.models import ValidationEntry
    candidate.validation_history.append(
        ValidationEntry(validation_id="V1", timestamp="2026-09-06T00:00:00Z",
                        decision="INCONCLUSIVE", confidence="LOW", sample_size=18))
    registry.create(candidate)

    # human governance decision record (governance gate store)
    Path("logs/research_lifecycle").mkdir(parents=True, exist_ok=True)
    Path("logs/research_lifecycle/governance_decisions.jsonl").write_text(
        json.dumps({"decision": "HUMAN_DECISION_REQUIRED", "candidate_id": "OPT-GAP8"}) + "\n",
        encoding="utf-8")
    # knowledge map (dedup depends on it)
    Path("analysis/summaries").mkdir(parents=True, exist_ok=True)
    Path("analysis/summaries/research_knowledge.json").write_text(
        json.dumps({"confirmed_facts": [], "rejected_hypotheses": []}), encoding="utf-8")
    # cycle state + Gap-6 baseline
    Path("logs/research_lifecycle/cycles").mkdir(parents=True, exist_ok=True)
    Path("logs/research_lifecycle/cycle_state.json").write_text(
        json.dumps({"last_cycle_id": "RC-1", "total_cycles": 1}), encoding="utf-8")
    Path("logs/research_lifecycle/cycles/latest_success.json").write_text(
        json.dumps({"cycle_id": "RC-1", "snapshot_file": "RC-1_snapshot.json"}), encoding="utf-8")
    Path("logs/research_lifecycle/cycles/RC-1_snapshot.json").write_text(
        json.dumps({"cycle_id": "RC-1", "questions": {}}), encoding="utf-8")

    return {"trigger_id": trigger.trigger_id, "finding_id": trigger.finding_id,
            "evidence": dict(trigger.evidence)}


def _wipe_local_state():
    """Simulate total local research-state loss (VM rebuilt)."""
    import shutil
    for d in ("logs/research_lifecycle", "data/research", "analysis/summaries"):
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# INVENTORY / CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestInventoryContract:
    def test_durable_allowlist_excludes_locks_temps_secrets(self):
        for rel in sd.CHECKPOINT_ARTIFACTS:
            assert not sd._is_excluded(rel), rel
        for excluded in ("logs/research_lifecycle/research_cycle.lock",
                         "logs/research_lifecycle/cycle_state.json.tmp",
                         "secrets.pem", ".env", ".aws/config"):
            assert sd._is_excluded(excluded), excluded

    def test_production_evidence_is_never_checkpointed(self):
        forbidden = ("trade_truth", "decision_trace", "shadow_runtime",
                     "shadow_trades", "market_context", "events")
        joined = " ".join(sd.CHECKPOINT_ARTIFACTS)
        for f in forbidden:
            assert f not in joined, f"canonical evidence path in checkpoint: {f}"

    def test_research_state_prefix_is_not_production_evidence(self):
        from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
        assert "research_state" not in PRODUCTION_SCHEMA_REGISTRY

    def test_no_hardcoded_profile_or_credentials(self):
        src = (ROOT / "research_engine" / "lifecycle" / "state_durability.py").read_text(encoding="utf-8")
        assert "trading-bot-new" not in src
        assert "aws_access_key" not in src
        assert "aws_secret" not in src
        # ".pem" appears only inside the exclusion guard, never as a path used
        assert src.count(".pem") == 1 and '"secrets.pem"' not in src

    def test_no_trading_mutation_path(self):
        src = (ROOT / "research_engine" / "lifecycle" / "state_durability.py").read_text(encoding="utf-8")
        for f in ("MT5Execution", "RiskManager", "order_send",
                  "persist_trade_truth", "ShadowRuntime("):
            assert f not in src, f"forbidden trading path: {f}"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckpoint:
    def test_complete_checkpoint_manifest_and_pointer(self):
        _seed_local_state()
        fake = FakeS3()
        result = _durability(fake).checkpoint(cycle_id="RC-1", dataset_fingerprint="fp-gap8")
        assert result.status == "durable"
        assert result.generation == 1
        manifest = json.loads(fake.objects[result.manifest_key])
        assert manifest["contract_version"] == "V1"
        assert manifest["status"] == "complete"
        assert manifest["checkpoint_id"] == result.checkpoint_id
        paths = {a["path"] for a in manifest["artifacts"]}
        assert "logs/research_lifecycle/registry.json" in paths
        assert "data/research/candidates/candidates.jsonl" in paths
        assert "logs/research_lifecycle/cycles/latest_success.json" in paths
        assert "logs/research_lifecycle/cycles/RC-1_snapshot.json" in paths
        assert not any("research_cycle.lock" in p for p in paths)
        # checksums valid
        import hashlib
        for a in manifest["artifacts"]:
            body = fake.objects[
                f"research_state/checkpoints/{result.checkpoint_id}/artifacts/{a['path']}"]
            assert hashlib.sha256(body).hexdigest() == a["sha256"]
        # pointer advanced LAST and references the complete manifest
        pointer = json.loads(fake.objects["research_state/latest_success.json"])
        assert pointer["checkpoint_id"] == result.checkpoint_id
        assert pointer["manifest_key"] == result.manifest_key
        assert pointer["artifact_count"] == len(manifest["artifacts"])

    def test_generation_chain_advances(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint(cycle_id="RC-1")
        r2 = d.checkpoint(cycle_id="RC-2")
        assert r1.generation == 1 and r2.generation == 2
        m2 = json.loads(fake.objects[r2.manifest_key])
        assert m2["previous_checkpoint_id"] == r1.checkpoint_id

    def test_checkpointing_identical_state_twice_is_safe(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint()
        r2 = d.checkpoint()
        assert r1.status == r2.status == "durable"
        assert r2.generation == r1.generation + 1
        for r in (r1, r2):
            m = json.loads(fake.objects[r.manifest_key])
            assert m["status"] == "complete"

    def test_upload_failure_surfaces_loudly(self):
        _seed_local_state()
        failing = FakeS3(fail_all=True)
        with pytest.raises(sd.DurabilityError):
            _durability(failing).checkpoint()


# ═══════════════════════════════════════════════════════════════════════════════
# RECOVERY (total local-state loss)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecovery:
    def test_total_local_state_loss_restores_everything(self):
        identity = _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint(cycle_id="RC-1", dataset_fingerprint="fp-gap8")
        assert r1.status == "durable"

        # capture pre-loss state for comparison
        candidate_before = CandidateRegistry().get("OPT-GAP8")
        pre_candidate = (candidate_before.candidate_id, candidate_before.status,
                         [v.decision for v in candidate_before.validation_history])
        pre_hypotheses = {h.hypothesis_id: h.status.value
                          for h in InvestigationRegistry().all()}
        pre_trigger = (identity["trigger_id"], "H-GAP8", identity["evidence"])

        # VM rebuilt: total local research-state loss
        _wipe_local_state()
        assert not Path("logs/research_lifecycle/registry.json").exists()

        result = d.restore_if_needed()
        assert result.status == "recovered"
        assert result.checkpoint_id == r1.checkpoint_id

        # same hypothesis IDs/statuses
        after = {h.hypothesis_id: h.status.value for h in InvestigationRegistry().all()}
        assert after == pre_hypotheses == {"H-GAP8": "REGISTERED"}
        # same candidate ID/status/validation history
        candidate_after = CandidateRegistry().get("OPT-GAP8")
        assert (candidate_after.candidate_id, candidate_after.status,
                [v.decision for v in candidate_after.validation_history]) == pre_candidate
        # same trigger identity + Gap-7 evidence provenance
        engine_after = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=30))
        trigger_after = engine_after.get(trigger_id=identity["trigger_id"])
        assert trigger_after.trigger_id == identity["trigger_id"]
        assert trigger_after.hypothesis_id == "H-GAP8"
        assert trigger_after.evidence["dataset_fingerprint"] == "fp-gap8"
        assert trigger_after.evidence["source_datasets"] == ["shadow_runtime_v1(ingested)"]
        # Gap-6 weekly baseline preserved
        assert cs.load_latest_successful_snapshot()["cycle_id"] == "RC-1"
        # locks not restored
        assert not Path("logs/research_lifecycle/research_cycle.lock").exists()

    def test_restore_twice_is_safe(self):
        _seed_local_state()
        d = _durability(FakeS3())
        d.checkpoint()
        _wipe_local_state()
        assert d.restore_if_needed().status == "recovered"
        # restart after successful restore: local present → skip
        assert d.restore_if_needed().status == "skipped"

    def test_local_state_present_means_runtime_authority(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        d.checkpoint()
        # mutate local AFTER checkpoint (local is now newer)
        Path("analysis/summaries/research_knowledge.json").write_text(
            json.dumps({"confirmed_facts": ["newer local fact"]}), encoding="utf-8")
        result = d.restore_if_needed()
        assert result.status == "skipped"   # never overwrite local with stale S3
        knowledge = json.loads(
            Path("analysis/summaries/research_knowledge.json").read_text(encoding="utf-8"))
        assert knowledge["confirmed_facts"] == ["newer local fact"]

    def test_fresh_machine_without_checkpoint_is_honest(self):
        result = _durability(FakeS3()).restore_if_needed()
        assert result.status == "skipped"
        assert "no durable checkpoint exists" in result.error

    def test_s3_unavailable_recovery_fails_loudly(self):
        class _DeadClient:
            def get_object(self, Bucket, Key):
                raise ClientError({"Error": {"Code": "ServiceUnavailable",
                                             "Message": "S3 down"}}, "GetObject")
            def put_object(self, Bucket, Key, Body):
                raise ClientError({"Error": {"Code": "ServiceUnavailable",
                                             "Message": "S3 down"}}, "PutObject")

        _seed_local_state()
        d = _durability(_DeadClient())
        _wipe_local_state()   # recovery is REQUIRED (local state absent)
        with pytest.raises(sd.DurabilityError, match="S3 unavailable"):
            d.restore_if_needed()

    def test_corrupt_latest_manifest_falls_back_to_previous_complete(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint(cycle_id="RC-1")
        r2 = d.checkpoint(cycle_id="RC-2")
        # corrupt the LATEST manifest (r2)
        fake.objects[r2.manifest_key] = b"{not json"
        _wipe_local_state()
        result = d.restore_if_needed()
        assert result.status == "recovered"
        assert result.checkpoint_id == r1.checkpoint_id  # previous complete

    def test_tampered_artifact_checksum_rejected(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint()
        # tamper with one artifact
        reg_key = (f"research_state/checkpoints/{r1.checkpoint_id}"
                   f"/artifacts/logs/research_lifecycle/registry.json")
        fake.objects[reg_key] = b'{"tampered": true}'
        _wipe_local_state()
        with pytest.raises(sd.DurabilityError, match="no complete durable checkpoint"):
            d.restore_if_needed()

    def test_incomplete_checkpoint_never_selected(self):
        _seed_local_state()
        fake = FakeS3()
        d = _durability(fake)
        r1 = d.checkpoint(cycle_id="RC-1")
        # simulate a TORN pointer: references a checkpoint whose manifest was
        # never completed; the pointer carries the previous complete id so
        # recovery can always retreat
        fake.objects["research_state/latest_success.json"] = json.dumps({
            "checkpoint_id": "ckpt-INCOMPLETE", "generation": 2,
            "previous_checkpoint_id": r1.checkpoint_id,
            "manifest_key": "research_state/checkpoints/ckpt-INCOMPLETE/manifest.json",
        }).encode()
        _wipe_local_state()
        result = d.restore_if_needed()
        # falls back to the complete r1 chain, never the incomplete generation
        assert result.status == "recovered"
        assert result.checkpoint_id == r1.checkpoint_id


# ═══════════════════════════════════════════════════════════════════════════════
# HUMAN GOVERNANCE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernanceRecovery:
    def test_ready_for_review_survives_without_promotion_or_reset(self):
        _seed_local_state()
        registry = CandidateRegistry()
        # SHADOW_TESTING → READY_FOR_REVIEW is the evaluator's governed outcome
        registry.update_status("OPT-GAP8", "READY_FOR_REVIEW")
        assert registry.get("OPT-GAP8").status == "READY_FOR_REVIEW"

        fake = FakeS3()
        _durability(fake).checkpoint()
        _wipe_local_state()
        result = _durability(fake).restore_if_needed()
        assert result.status == "recovered"

        candidate = CandidateRegistry().get("OPT-GAP8")
        # preserved exactly — NOT promoted, NOT reset, NOT reactivated
        assert candidate.status == "READY_FOR_REVIEW"
        assert [v.decision for v in candidate.validation_history] == ["INCONCLUSIVE"]
        # human decision record preserved
        decisions = Path("logs/research_lifecycle/governance_decisions.jsonl").read_text(encoding="utf-8")
        assert "HUMAN_DECISION_REQUIRED" in decisions
        # hypothesis chain intact
        assert InvestigationRegistry().get("H-GAP8").status.value == "REGISTERED"


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY-CYCLE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeeklyCycleIntegration:
    @pytest.fixture()
    def durability_module(self, monkeypatch):
        import research_engine.data_access.loaders as loaders
        import research_engine.data_access.shadow_runtime_ingestion as sri
        import research_engine.experiments.research_runner as rr
        import research_engine.lifecycle.candidate_pairing as pairing
        import research_engine.lifecycle.state_durability as sdur

        monkeypatch.setattr(sri, "ingest_completed_shadow_trades", lambda **k: [])
        monkeypatch.setattr(loaders, "load_trade_truth", lambda *a, **k: [])
        monkeypatch.setattr(loaders, "load_decision_trace", lambda *a, **k: [])
        monkeypatch.setattr(rr, "run_all", lambda: {})
        # Gap-1 prospective pairing reads canonical S3; unit tests stay offline
        monkeypatch.setattr(pairing, "count_prospective_pairs", lambda *a, **k: 0)
        return sdur

    def test_cycle_checkpoints_durable_after_success(self, durability_module, monkeypatch):
        fake = FakeS3()
        real_cls = sd.ResearchStateDurability
        monkeypatch.setattr(durability_module, "ResearchStateDurability",
                            lambda: real_cls(bucket="b", client=fake))
        runner = ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))
        result = runner.run_cycle()
        assert result.status == "complete"
        assert result.durability_status == "durable"
        pointer = json.loads(fake.objects["research_state/latest_success.json"])
        assert pointer["checkpoint_id"].startswith("ckpt-")

    def test_cycle_surfaces_checkpoint_failure_without_false_durable(
            self, durability_module, monkeypatch):
        # seed local state so recovery skips (runtime authority) and the cycle
        # reaches its end-of-cycle checkpoint, which then fails
        _seed_local_state()
        real_cls = sd.ResearchStateDurability
        monkeypatch.setattr(durability_module, "ResearchStateDurability",
                            lambda: real_cls(bucket="b", client=FakeS3(fail_all=True)))
        runner = ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))
        result = runner.run_cycle()
        # research completed, but durability is EXPLICITLY not durable
        assert result.status == "complete"
        assert result.durability_status == "checkpoint_failed"
        assert any("durability" in e for e in result.errors)

    def test_cycle_fails_loudly_when_local_state_missing_and_recovery_unavailable(
            self, durability_module, monkeypatch):
        class _DeadClient:
            def get_object(self, Bucket, Key):
                raise ClientError({"Error": {"Code": "ServiceUnavailable",
                                             "Message": "S3 down"}}, "GetObject")

        # total local loss + S3 down → must NOT run with empty lifecycle state
        real_cls = sd.ResearchStateDurability
        monkeypatch.setattr(durability_module, "ResearchStateDurability",
                            lambda: real_cls(bucket="b", client=_DeadClient()))
        runner = ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))
        result = runner.run_cycle()
        assert result.status == "failed"
        assert result.durability_status == "recovery_unavailable"
        assert any("recovery unavailable" in e for e in result.errors)

    def test_cycle_recovers_state_after_local_loss(self, durability_module, monkeypatch):
        _seed_local_state()
        fake = FakeS3()
        _durability(fake).checkpoint(cycle_id="RC-prev")
        _wipe_local_state()

        real_cls = sd.ResearchStateDurability
        monkeypatch.setattr(durability_module, "ResearchStateDurability",
                            lambda: real_cls(bucket="b", client=fake))
        runner = ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))
        result = runner.run_cycle()
        assert result.status == "complete"
        assert result.durability_status == "durable"
        # recovery preserved the Gap-6 baseline: this cycle's change report
        # compares against RC-1 (the seeded baseline recovered from the
        # durable checkpoint), never a fresh/empty baseline
        report = json.loads(
            (cs._CYCLES_DIR / f"{result.cycle_id}_change_report.json").read_text(encoding="utf-8"))
        assert report["previous_cycle_id"] == "RC-1"
        # no duplicate lifecycle objects
        assert len(InvestigationRegistry().all()) == 1
        assert len(CandidateRegistry().list_all()) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCredentials:
    def test_explicit_research_profile_used_locally(self, monkeypatch):
        seen = {}
        import core.config as cfg
        import research_engine.data_access.s3_source as s3s
        monkeypatch.setattr(cfg, "RESEARCH_AWS_PROFILE", "trading-bot-new")
        monkeypatch.setattr(
            s3s, "_build_session",
            lambda profile, region: (seen.update(profile=profile, region=region),
                                     type("S", (), {"client": lambda self, name: "client"})())[1])
        assert sd.ResearchStateDurability()._get_client() == "client"
        assert seen == {"profile": "trading-bot-new", "region": "eu-west-2"}

    def test_unset_profile_preserves_default_ec2_chain(self, monkeypatch):
        seen = {}
        import core.config as cfg
        import research_engine.data_access.s3_source as s3s
        monkeypatch.setattr(cfg, "RESEARCH_AWS_PROFILE", "")
        monkeypatch.setattr(
            s3s, "_build_session",
            lambda profile, region: (seen.update(profile=profile, region=region),
                                     type("S", (), {"client": lambda self, name: "client"})())[1])
        sd.ResearchStateDurability()._get_client()
        assert seen["profile"] is None   # standard boto3 chain / EC2 instance role



