"""Gap 8 proof: static config verification + full FakeS3 recovery demo.

Real S3 writes were not possible (SSO token expired — run
`aws sso login --profile trading-bot-new` to enable the live proof), so this
script (a) statically verifies the durability configuration against the
canonical contracts and (b) demonstrates the complete checkpoint → wipe →
restore → hash-compare cycle with a production-shaped lifecycle in a
sandboxed research-state directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")


def static_verification() -> None:
    from core.config import NEW_RUNTIME_S3_BUCKET
    from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, s3_base_prefix

    print("[static configuration verification]")
    print("  durability bucket  :", NEW_RUNTIME_S3_BUCKET, "(canonical research bucket)")
    print("  durability prefix  : research_state/ (separate ownership class)")
    # prefix separation: research_state must not collide with any evidence prefix
    evidence_prefixes = {entry.s3_base_prefix for entry in PRODUCTION_SCHEMA_REGISTRY.values()}
    assert all(not p.startswith("research_state") for p in evidence_prefixes)
    assert "research_state" not in PRODUCTION_SCHEMA_REGISTRY
    print("  prefix separation  : OK (research_state not a registered evidence dataset)")
    # credential model
    src = (ROOT / "research_engine" / "lifecycle" / "state_durability.py").read_text(encoding="utf-8")
    assert "trading-bot-new" not in src and "aws_access_key" not in src
    assert "RESEARCH_AWS_PROFILE" in src and "_build_session" in src
    print("  credential model   : RESEARCH_AWS_PROFILE locally / default chain on EC2")
    print("  old v10 publisher  : research_engine/v10/persistence/s3_publisher.py targets "
          "bucket 'v10-engine' (retired V10 artifact) - NOT used by Gap 8")


class FakeS3:
    def __init__(self):
        self.objects = {}
    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = bytes(Body)
    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                              "GetObject")
        import io
        return {"Body": io.BytesIO(self.objects[Key])}


def recovery_demo() -> None:
    from research_engine.lifecycle.finding_trigger import (
        EligibilityConfig, FindingTriggerEngine, stamp_provenance)
    from research_engine.lifecycle.registry import InvestigationRegistry
    from research_engine.lifecycle.research_cycle_runner import ResearchCycleRunner
    from research_engine.lifecycle import cycle_snapshot as cs
    from research_engine.lifecycle import state_durability as sd
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry
    from research_engine.v10.candidates.models import CandidateRecord

    with tempfile.TemporaryDirectory() as sandbox:
        os.chdir(sandbox)

        # ── production-shaped lifecycle ──────────────────────────────────
        engine = FindingTriggerEngine(config=EligibilityConfig(min_sample_size=30))
        trigger = engine.detect_from_pattern_performance(
            pattern="HAMMER", mean_r=-0.4, win_rate=0.05, sample_size=60)
        stamp_provenance([trigger], source_datasets=["shadow_runtime_v1(ingested)"],
                         dataset_fingerprint="fp-gap8", evidence_as_of="2026-09-06T12:00:00Z")
        from research_engine.lifecycle.hypothesis import (
            Hypothesis, HypothesisCategory, HypothesisStatus)
        hyp = Hypothesis(hypothesis_id="H-P", title="t", description="d", claim="c",
                         null_hypothesis="n", category=HypothesisCategory.PATTERN_SIGNAL,
                         source="research_cycle:TRG-x", source_finding_id=trigger.finding_id)
        hyp.transition(HypothesisStatus.REGISTERED, reason="demo")
        InvestigationRegistry().register(hyp)
        engine.mark_registered(trigger.trigger_id, "H-P")
        CandidateRegistry().create(CandidateRecord(
            candidate_id="OPT-P", hypothesis_id="H-P", status="SHADOW_TESTING"))
        Path("logs/research_lifecycle/cycles").mkdir(parents=True, exist_ok=True)
        Path("logs/research_lifecycle/cycles/latest_success.json").write_text(
            json.dumps({"cycle_id": "RC-BASE", "snapshot_file": "RC-BASE_snapshot.json"}))
        Path("logs/research_lifecycle/cycles/RC-BASE_snapshot.json").write_text(
            json.dumps({"cycle_id": "RC-BASE", "questions": {}}))

        # ── checkpoint ───────────────────────────────────────────────────
        fake = FakeS3()
        result = sd.ResearchStateDurability(bucket="b", client=fake).checkpoint(
            cycle_id="RC-BASE", dataset_fingerprint="fp-gap8")
        assert result.status == "durable"
        manifest = json.loads(fake.objects[result.manifest_key])
        print("\n[checkpoint] id=%s generation=%d artifacts=%d"
              % (result.checkpoint_id, result.generation, result.artifact_count))
        print("  artifacts:", sorted(a["path"] for a in manifest["artifacts"]))

        # ── hash comparison before loss ──────────────────────────────────
        def state_hashes() -> dict:
            hashes = {}
            for rel in ("logs/research_lifecycle/registry.json",
                        "data/research/candidates/candidates.jsonl",
                        "logs/research_lifecycle/finding_triggers.json",
                        "logs/research_lifecycle/cycles/latest_success.json"):
                p = Path(rel)
                hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
            return hashes
        before = state_hashes()

        # ── total local loss → restore ───────────────────────────────────
        import shutil
        for d in ("logs/research_lifecycle", "data/research", "analysis/summaries"):
            shutil.rmtree(d, ignore_errors=True)
        rec = sd.ResearchStateDurability(bucket="b", client=fake).restore_if_needed()
        assert rec.status == "recovered"
        after = state_hashes()
        print("\n[recovery] status=%s checkpoint=%s" % (rec.status, rec.checkpoint_id))
        for rel in before:
            match = "MATCH" if before[rel] == after[rel] else "DIFF"
            print(f"  {rel}: {match}")
            assert before[rel] == after[rel], rel

        # identity chain
        assert InvestigationRegistry().get("H-P").source_finding_id == trigger.finding_id
        assert CandidateRegistry().get("OPT-P").hypothesis_id == "H-P"
        assert CandidateRegistry().get("OPT-P").status == "SHADOW_TESTING"
        assert engine.get(trigger_id=trigger.trigger_id).evidence["dataset_fingerprint"] == "fp-gap8"
        assert cs.load_latest_successful_snapshot()["cycle_id"] == "RC-BASE"
        print("\n  identity chain: OPT-P -> H-P -> %s -> fingerprint fp-gap8 : OK"
              % trigger.finding_id)
        # leave the sandbox before cleanup (Windows cwd lock)
        os.chdir(ROOT)
        os.chdir(ROOT)


def main() -> None:
    print("=" * 72)
    print("GAP 8 PROOF")
    print("=" * 72)
    static_verification()
    recovery_demo()
    print("\nPROOF COMPLETE (FakeS3; real S3 blocked by expired SSO - "
          "run: aws sso login --profile trading-bot-new)")


if __name__ == "__main__":
    main()
