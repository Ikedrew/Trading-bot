"""
Gap 6 — Weekly scheduling + cycle-to-cycle change report tests.

Proves:
  - first cycle creates a baseline (no fake "everything is new")
  - evidence growth produces a deterministic semantic diff
  - unchanged state -> NO MATERIAL RESEARCH CHANGE
  - question status / confidence transitions use the Gap-4 contract
  - findings: new once, reconfirmed without duplication, gone = explicit
  - hypothesis / candidate transitions and candidate evidence accumulation
  - duplicate scheduler invocation is idempotent; overlap is locked out
  - failed cycles never advance the successful baseline; recovery compares
    against the last SUCCESSFUL cycle
  - S3 failure stays loud (no local production fallback)
  - scheduled research has no route to trading mutation

All tests are synthetic - production AWS is NEVER touched.
"""

from __future__ import annotations

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
from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleConfig,
    ResearchCycleRunner,
)


# ─── synthetic canonical evidence + question summaries ──────────────────────


def _install_evidence(monkeypatch, *, shadows=100, truths=10, traces=500):
    """Route canonical evidence counting to synthetic S3-backed data."""
    import research_engine.data_access.loaders as loaders
    import research_engine.data_access.shadow_runtime_ingestion as sri

    monkeypatch.setattr(sri, "ingest_completed_shadow_trades", lambda **k: [{"s": i} for i in range(shadows)])
    monkeypatch.setattr(loaders, "load_trade_truth", lambda *a, **k: [{"t": i} for i in range(truths)])
    monkeypatch.setattr(loaders, "load_decision_trace", lambda *a, **k: [{"d": i} for i in range(traces)])


def _install_run_all(monkeypatch, summaries: dict[str, dict[str, Any]]):
    import research_engine.experiments.research_runner as rr
    monkeypatch.setattr(rr, "run_all", lambda: summaries)


def _summary(status="INSUFFICIENT_DATA", rec="WAIT", sample=0, confidence="LOW",
             source="report"):
    return {"status": status, "recommendation": rec, "sample": sample,
            "confidence": confidence, "status_source": source}


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """All research lifecycle state lands in a per-test sandbox."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# FIRST CYCLE / BASELINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestFirstCycle:
    def test_no_previous_snapshot_creates_baseline(self, monkeypatch):
        _install_evidence(monkeypatch)
        _install_run_all(monkeypatch, {"E3": _summary()})
        assert cs.load_latest_successful_snapshot() is None

        snapshot, report, kind = cs.record_cycle(cycle_id="RC-1", fingerprint="fp1")
        assert kind == "baseline"
        assert report["is_baseline"] is True
        assert report["material_change"] is False
        # persisted
        assert Path(snapshot["snapshot_file"]).exists()
        assert cs.load_latest_successful_snapshot()["cycle_id"] == "RC-1"
        # human report
        text = (cs._CYCLES_DIR / "RC-1_change_report.txt").read_text(encoding="utf-8")
        assert "BASELINE RESEARCH SNAPSHOT CREATED" in text
        assert "No previous successful research cycle exists" in text

    def test_baseline_snapshot_contains_contract_fields(self, monkeypatch):
        _install_evidence(monkeypatch, shadows=1253, truths=12, traces=1458)
        _install_run_all(monkeypatch, {"Q16": _summary("COMPLETE", "SHADOW_TRUSTED", 9)})
        snapshot, _, _ = cs.record_cycle(cycle_id="RC-B", fingerprint="fpB")
        assert snapshot["evidence"] == {
            "shadow_outcomes": 1253, "trade_truth": 12, "decision_trace": 1458,
            "dataset_fingerprint": "fpB",
        }
        assert snapshot["questions"]["Q16"]["status"] == "COMPLETE"
        assert snapshot["questions"]["Q16"]["sample"] == 9
        assert snapshot["schema"] == "research_cycle_snapshot_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# SECOND CYCLE / DETERMINISTIC DIFF
# ═══════════════════════════════════════════════════════════════════════════════


class TestChangeSemantics:
    def test_evidence_growth_produces_deterministic_diff(self, monkeypatch):
        _install_evidence(monkeypatch, shadows=1253, truths=12)
        _install_run_all(monkeypatch, {"E3": _summary()})
        prev, _, _ = cs.record_cycle(cycle_id="RC-1", fingerprint="fp1")

        _install_evidence(monkeypatch, shadows=1811, truths=37)
        curr = dict(prev)
        curr["cycle_id"] = "RC-2"
        curr["evidence"] = {**prev["evidence"], "shadow_outcomes": 1811, "trade_truth": 37}

        r1 = cs.diff_snapshots(prev, curr)
        r2 = cs.diff_snapshots(prev, curr)
        # deterministic (ignore volatile timestamp)
        r1.pop("generated_at"), r2.pop("generated_at")
        assert r1 == r2
        growth = {g["dataset"]: g for g in r1["evidence_growth"]}
        assert growth["shadow_outcomes"]["previous"] == 1253
        assert growth["shadow_outcomes"]["current"] == 1811
        assert growth["trade_truth"]["delta"] == 25
        assert r1["material_change"] is True

    def test_identical_state_is_no_material_change(self, monkeypatch):
        _install_evidence(monkeypatch)
        _install_run_all(monkeypatch, {"E3": _summary()})
        _, _, kind1 = cs.record_cycle(cycle_id="RC-1", fingerprint="fp1")
        assert kind1 == "baseline"
        _, _, kind2 = cs.record_cycle(cycle_id="RC-2", fingerprint="fp1")
        assert kind2 == "no_material_change"
        text = (cs._CYCLES_DIR / "RC-2_change_report.txt").read_text(encoding="utf-8")
        assert "NO MATERIAL RESEARCH CHANGE" in text

    def test_question_status_transition_insufficient_to_complete(self):
        prev = {"questions": {"R1": _summary("INSUFFICIENT_DATA", "WAIT", 0)}}
        curr = {"questions": {"R1": _summary("COMPLETE", "COMPLETE", 240)}}
        report = cs.diff_snapshots(prev, curr)
        q = report["question_changes"][0]
        changes = {c["field"]: (c["previous"], c["current"]) for c in q["changes"]}
        assert changes["status"] == ("INSUFFICIENT_DATA", "COMPLETE")
        assert changes["sample"] == (0, 240)

    def test_confidence_transition_low_to_medium(self):
        prev = {"questions": {"Q16": _summary("COMPLETE", "SHADOW_TRUSTED", 9, "LOW")}}
        curr = {"questions": {"Q16": _summary("COMPLETE", "SHADOW_TRUSTED", 24, "MEDIUM")}}
        report = cs.diff_snapshots(prev, curr)
        changes = {c["field"]: c["current"] for c in report["question_changes"][0]["changes"]}
        assert changes["confidence"] == "MEDIUM"
        assert changes["sample"] == 24

    def test_never_uses_recommendation_status_as_question_status(self):
        """Gap-4 regression: the recommendation label is compared as a
        recommendation, never treated as the question status."""
        prev = {"questions": {"E3": _summary("INSUFFICIENT_DATA", "COMPLETE", 0)}}
        curr = {"questions": {"E3": _summary("INSUFFICIENT_DATA", "COMPLETE", 0)}}
        report = cs.diff_snapshots(prev, curr)
        assert report["question_changes"] == []  # statuses agree -> no change
        assert prev["questions"]["E3"]["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# FINDINGS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindings:
    def test_new_finding_appears_once(self):
        prev = {"findings": {}}
        curr = {"findings": {"TRIG-1": {"status": "ELIGIBLE", "sample_size": 40}}}
        report = cs.diff_snapshots(prev, curr)
        assert len(report["findings_new"]) == 1
        assert report["findings_new"][0]["finding_id"] == "TRIG-1"

    def test_reconfirmed_finding_is_not_a_duplicate(self):
        prev = {"findings": {"TRIG-1": {"status": "ELIGIBLE", "sample_size": 40}}}
        curr = {"findings": {"TRIG-1": {"status": "ELIGIBLE", "sample_size": 55}}}
        report = cs.diff_snapshots(prev, curr)
        assert report["findings_new"] == []
        assert len(report["findings_reconfirmed"]) == 1
        assert report["findings_reconfirmed"][0]["sample_current"] == 55

    def test_finding_no_longer_supported_is_explicit_not_erased(self):
        prev = {"findings": {"TRIG-1": {"status": "ELIGIBLE", "sample_size": 40}}}
        curr = {"findings": {}}
        report = cs.diff_snapshots(prev, curr)
        assert len(report["findings_no_longer_supported"]) == 1
        assert report["material_change"] is True  # visible to the human


# ═══════════════════════════════════════════════════════════════════════════════
# HYPOTHESES / CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════


class TestHypothesesAndCandidates:
    def test_hypothesis_transition_reported(self):
        prev = {"hypotheses": {"H-1": {"status": "REGISTERED", "conclusion": ""}}}
        curr = {"hypotheses": {"H-1": {"status": "CONCLUDED", "conclusion": "VALIDATED"}}}
        report = cs.diff_snapshots(prev, curr)
        assert report["hypothesis_transitions"][0]["transition"] == "REGISTERED -> CONCLUDED"

    def test_candidate_transition_and_governance_notification(self):
        prev = {"candidates": {"OPT-1": {"status": "SHADOW_TESTING"}}}
        curr = {"candidates": {"OPT-1": {"status": "READY_FOR_REVIEW"}}}
        report = cs.diff_snapshots(prev, curr)
        assert report["candidate_transitions"][0]["transition"] == \
            "SHADOW_TESTING -> READY_FOR_REVIEW"
        assert report["governance_changes"][0]["candidate_id"] == "OPT-1"
        assert "HUMAN DECISION REQUIRED" in report["governance_changes"][0]["note"]

    def test_candidate_evidence_accumulation_without_duplicates(self):
        prev = {"candidates": {"OPT-1": {"status": "SHADOW_TESTING",
                                          "prospective_pairs": 18, "latest_verdict": ""}}}
        curr = {"candidates": {"OPT-1": {"status": "SHADOW_TESTING",
                                          "prospective_pairs": 37, "latest_verdict": ""}}}
        report = cs.diff_snapshots(prev, curr)
        growth = report["candidate_evidence_growth"]
        assert len(growth) == 1
        assert growth[0]["pairs_previous"] == 18
        assert growth[0]["pairs_current"] == 37
        # single candidate identity — no duplicates minted by accumulation
        assert len({c["candidate_id"] for c in growth}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# FULL CYCLE SAFETY (ResearchCycleRunner)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def runner(monkeypatch):
    """ResearchCycleRunner on offline synthetic evidence, no cooldown.
    Durability is routed to a fake in-memory S3 (Gap 8)."""
    _install_evidence(monkeypatch)
    _install_run_all(monkeypatch, {"E3": _summary()})
    import research_engine.lifecycle.state_durability as sdur
    real_cls = sdur.ResearchStateDurability
    monkeypatch.setattr(sdur, "ResearchStateDurability",
                        lambda: real_cls(bucket="b", client=_FakeS3ForGap6()))
    return ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))


class _FakeS3ForGap6:
    """Minimal in-memory S3 for Gap-6 cycle tests (no real AWS)."""
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


class TestCycleSafety:
    def test_full_cycle_produces_snapshot_and_change_report(self, runner):
        result = runner.run_cycle()
        assert result.status == "complete"
        assert result.change_kind == "baseline"
        assert Path(result.snapshot_path).exists()
        assert Path(result.change_report_path).exists()

    def test_duplicate_scheduler_invocation_is_idempotent(self, runner):
        r1 = runner.run_cycle()
        r2 = runner.run_cycle()
        assert r1.status == "complete" and r2.status == "complete"
        # second cycle: unchanged evidence -> explicit no-change, no duplicates
        assert r2.change_kind == "no_material_change"
        pointer = json.loads(cs._LATEST_SUCCESS_FILE.read_text(encoding="utf-8"))
        assert pointer["cycle_id"] == r2.cycle_id
        snapshots = list(cs._CYCLES_DIR.glob("*_snapshot.json"))
        assert len(snapshots) == 2  # one per cycle, not duplicating objects

    def test_overlapping_cycle_is_locked_out(self, runner):
        from research_engine.lifecycle import research_cycle_runner as rcr
        # Simulate another live cycle holding the lock (our own PID = alive)
        rcr._STATE_DIR.mkdir(parents=True, exist_ok=True)
        rcr._LOCK_FILE.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        result = runner.run_cycle()
        assert result.status == "locked"
        # baseline NOT created by the locked-out invocation
        assert cs.load_latest_successful_snapshot() is None
        rcr._LOCK_FILE.unlink(missing_ok=True)

    def test_stale_lock_is_recovered(self, runner):
        from research_engine.lifecycle import research_cycle_runner as rcr
        rcr._STATE_DIR.mkdir(parents=True, exist_ok=True)
        rcr._LOCK_FILE.write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
        result = runner.run_cycle()
        assert result.status == "complete"  # stale lock recovered, no deadlock

    def test_failed_cycle_never_advances_baseline(self, monkeypatch, runner):
        from research_engine.data_access.s3_source import ResearchDataSourceError
        import research_engine.data_access.loaders as loaders

        # cycle 1 succeeds -> baseline
        r1 = runner.run_cycle()
        assert r1.status == "complete"
        assert cs.load_latest_successful_snapshot()["cycle_id"] == r1.cycle_id

        # cycle 2: S3 breaks loudly (expired credentials)
        def _boom(*a, **k):
            raise ResearchDataSourceError("AWS failure: token expired (test)")
        monkeypatch.setattr(loaders, "load_trade_truth", _boom)
        r2 = runner.run_cycle()
        assert r2.status == "failed"
        # baseline pointer unchanged — failed cycle never becomes the baseline
        assert cs.load_latest_successful_snapshot()["cycle_id"] == r1.cycle_id
        # no change report produced for the failed cycle
        assert not (cs._CYCLES_DIR / f"{r2.cycle_id}_change_report.txt").exists()

    def test_recovery_compares_against_last_successful_cycle(self, monkeypatch, runner):
        from research_engine.data_access.s3_source import ResearchDataSourceError
        import research_engine.data_access.loaders as loaders

        r1 = runner.run_cycle()
        assert r1.status == "complete"

        def _boom(*a, **k):
            raise ResearchDataSourceError("AWS failure (test)")
        monkeypatch.setattr(loaders, "load_trade_truth", _boom)
        assert runner.run_cycle().status == "failed"

        # recovery: evidence grows, cycle succeeds again
        _install_evidence(monkeypatch, shadows=1400, truths=25)
        _install_run_all(monkeypatch, {"E3": _summary()})
        r3 = runner.run_cycle()
        assert r3.status == "complete"
        assert r3.change_kind == "material_change"
        report = json.loads(
            (cs._CYCLES_DIR / f"{r3.cycle_id}_change_report.json").read_text(encoding="utf-8"))
        # compared against the LAST SUCCESSFUL cycle, not the failed attempt
        assert report["previous_cycle_id"] == r1.cycle_id
        growth = {g["dataset"]: g for g in report["evidence_growth"]}
        assert growth["trade_truth"]["previous"] == 10  # baseline value
        assert growth["trade_truth"]["current"] == 25


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE / SCHEDULER CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernanceAndScheduler:
    def test_scheduled_research_has_no_trading_mutation_path(self):
        forbidden = (
            "MT5Execution(", "RiskManager(", "order_send", "from core.pipeline",
            "core.mt5_connection", "persist_trade_truth", "ShadowRuntime(",
            "ExecutionOrchestrator(",
        )
        for module in (
            ROOT / "research_engine" / "lifecycle" / "cycle_snapshot.py",
            ROOT / "research_engine" / "lifecycle" / "research_cycle_runner.py",
            ROOT / "scripts" / "run_research_cycle.py",
        ):
            src = module.read_text(encoding="utf-8")
            for f in forbidden:
                assert f not in src, f"{module.name} contains forbidden path: {f}"
            # no imports of the production/trading layer at all
            assert "from core" not in src and "import core" not in src

    def test_scheduler_entry_has_no_hardcoded_profile_or_keys(self):
        src = (ROOT / "scripts" / "run_research_cycle.py").read_text(encoding="utf-8")
        assert "trading-bot-new" not in src
        assert "aws_access_key" not in src
        assert ".pem" not in src
        # documents the Task Scheduler ownership
        assert "Task Scheduler" in src

    def test_scheduler_script_entry_point_loads(self):
        """The exact command a scheduler runs must at least parse/load."""
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_research_cycle.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0
        assert "--mode" in proc.stdout


