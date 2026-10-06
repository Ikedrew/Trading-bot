"""Pass D Repair 1 — snapshot-bound runner injection for broken preset-70 questions.

These tests exercise the real runner/evaluator boundary: the governed canonical
question cycle must inject snapshot-bound evidence (the reconstructed
``shadow_trades`` population for M1/M2 and the governed exit foundations for
EX1/EX2/EX5/EX6/EX7/EX9) rather than letting the runners reopen storage.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from research_engine.control_plane.governed_exit_evidence import (
    build_governed_exit_evidence,
)
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments import exit_policy_governed, exit_policy_heterogeneity
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.continuous.canonical_question_cycle import (
    run_canonical_question_cycle,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS

from tests.test_canonical_question_cycle import MemoryS3, _freeze, _objects

EIGHT = ("M1", "M2", "EX1", "EX2", "EX5", "EX6", "EX7", "EX9")
EXIT_SIX = ("EX1", "EX2", "EX5", "EX6", "EX7", "EX9")


def _run_real(tmp_path: Path, fake: MemoryS3):
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id,
         "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "cycles",
    )
    projection = json.loads(
        (tmp_path / "cycles" / "current_projection.json").read_text())
    return snapshot, projection


def test_eight_questions_reach_governed_runners(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    for qid in EIGHT:
        entry = projection["questions"][qid]
        assert entry["result"]["runner"], qid
        assert entry["result"]["implementation_status"] == "IMPLEMENTED", qid
        assert entry["result"]["snapshot_id"] == snapshot.snapshot_id, qid
        assert entry["runner_failed"] is False, qid


def test_exit_questions_surface_governed_m5_observation_gap(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    for qid in EXIT_SIX:
        result = projection["questions"][qid]["result"]
        assert result["status"] == "INSUFFICIENT_DATA", qid
        assert any(
            "M5 OHLC candle paths" in str(item)
            for item in result["missing_evidence"]
        ), (qid, result["missing_evidence"])


def test_m1_m2_use_reconstructed_snapshot_population(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    m1 = projection["questions"]["M1"]["result"]
    m2 = projection["questions"]["M2"]["result"]
    # The fixture binds one completed shadow lifecycle.  M1 (which only needs
    # h4_regime) sees that one CURRENT analytical row; M2 (which also needs a
    # clean strategy field) reaches its runner but finds no segmentable rows.
    assert m1["sample_n"] == 1
    assert m1["status"] in {"WAITING_FOR_DATA", "INSUFFICIENT_DATA"}
    assert m2["status"] == "INSUFFICIENT_DATA"


def test_exit_runner_uses_injected_evidence_not_filesystem(monkeypatch):
    evidence = build_governed_exit_evidence([
        {
            "event_type": "OPEN",
            "shadow_trade_id": "nshadow_1",
            "canonical_opportunity_id": "nopp_1",
            "horizon": "SCALP",
            "schema_version": "shadow_runtime_v1",
        },
        {
            "event_type": "CLOSE",
            "shadow_trade_id": "nshadow_1",
            "canonical_opportunity_id": "nopp_1",
            "horizon": "SCALP",
            "schema_version": "shadow_runtime_v1",
        },
    ], ())

    def forbidden(*args, **kwargs):
        raise AssertionError("runner reopened storage")

    monkeypatch.setattr(exit_policy_governed, "load_governed_foundations", forbidden)
    monkeypatch.setattr(exit_policy_heterogeneity, "load_governed_foundations", forbidden)

    report = exit_policy_governed.run_ex1(governed_exit_evidence=evidence)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["question_id"] == "EX1"
    assert any("M5 OHLC" in item for item in report["missing_evidence"])

    report9 = exit_policy_governed.run_ex9(governed_exit_evidence=evidence)
    assert report9["status"] == "INSUFFICIENT_DATA"

    report5 = exit_policy_heterogeneity.run_ex5(governed_exit_evidence=evidence)
    assert report5["status"] == "INSUFFICIENT_DATA"
    assert report5["question_id"] == "EX5"


def test_missing_injected_evidence_fails_closed():
    evidence = build_governed_exit_evidence([], ())
    assert evidence.missing_evidence
    report = exit_policy_governed._governed_exit_result("EX1", evidence)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["missing_evidence"]



def test_governed_exit_evidence_is_derived_from_its_snapshot_events():
    from tests.test_canonical_question_cycle import _shadow_events

    opened, closed = _shadow_events()
    one = build_governed_exit_evidence([opened, closed], ())

    opened2, closed2 = deepcopy(_shadow_events())
    opened2["shadow_trade_id"] = closed2["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    opened2["canonical_opportunity_id"] = closed2["canonical_opportunity_id"] = "nopp_2"
    two = build_governed_exit_evidence([opened, closed, opened2, closed2], ())

    # The evidence reflects only its own bound events, never another snapshot's.
    assert one.completed_lifecycles == 1
    assert two.completed_lifecycles == 2


def test_runner_signature_injection_boundary(tmp_path):
    """The shared runner context injects governed exit evidence by name."""
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    captured = {}

    def capture_ex1(shadow_trades=None, governed_exit_evidence=None, **kwargs):
        captured["shadow_trades"] = shadow_trades
        captured["governed_exit_evidence"] = governed_exit_evidence
        return {"status": "COMPLETE", "sample_n": 1, "conclusion": "captured"}

    runners = {q.id: (lambda **kw: {"status": "COMPLETE", "sample_n": 1})
               for q in REGISTRY}
    runners["EX1"] = capture_ex1

    run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id,
         "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "inj",
        runners=runners,
    )
    assert captured["shadow_trades"], "shadow_trades population not injected"
    assert captured["governed_exit_evidence"] is not None
    assert captured["governed_exit_evidence"].missing_evidence



def test_m1_m2_do_not_use_legacy_loader_during_cycle(tmp_path, monkeypatch):
    import research_engine.experiments.market_prediction_rw2 as mp
    import research_engine.experiments.market_research as mr

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy loader called during governed cycle")

    monkeypatch.setattr(mp, "load_shadow_trades", forbidden)
    monkeypatch.setattr(mr, "load_shadow_trades", forbidden)

    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    assert projection["questions"]["M1"]["result"]["status"] in {
        "WAITING_FOR_DATA", "INSUFFICIENT_DATA"}
    assert projection["questions"]["M2"]["result"]["status"] == "INSUFFICIENT_DATA"
