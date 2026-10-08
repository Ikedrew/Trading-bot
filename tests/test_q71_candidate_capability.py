"""Candidate-capability activation: production Q71 -> governed candidate.

These regressions prove the production-generated Q71 path is genuinely
candidate-capable end to end:

    frozen governed evidence
        -> governed counterfactual evidence producer (upstream)
        -> immutable, content-addressed frozen artifact
        -> snapshot-pinned governed evidence membership
        -> production Q71 evaluator (frozen evidence only)
        -> governed scientific result (Repair Block 1 contract)
        -> finding -> falsifiable hypothesis
        -> governed intervention mapping -> candidate
        -> unified Repair Block 3 candidate lifecycle

Nothing here injects ``scientifically_meaningful``, ``governed_policy_id``,
``falsification_criteria`` or ``validation_criteria``: every scientific value
originates in the canonical HD09 evaluator the production evaluator reuses, and
every candidate originates in the shared scientific-state bridge.

The evidence used is real governed evidence in the shape the production path
freezes: ``shadow_runtime_v1`` lifecycle events (the snapshot-bound dataset) plus
the governed ``events_v1`` M5 candle stream the common investigation snapshot
deliberately does not bind.  The producer reads them upstream; the worker and
the evaluator never read anything.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_engine.control_plane.governed_counterfactual_evidence import (
    COUNTERFACTUAL_EVIDENCE_CLASS,
    GOVERNED_POLICY_CATALOGUE,
    INCOMPLETE_REPLAY,
    INVALID_COUNTERFACTUAL_SCHEMA,
    LEAKAGE_GUARD_FAILED,
    MISSING_BASELINE,
    MISSING_COUNTERFACTUAL_EVIDENCE,
    MISSING_M5_CANDLE_AUTHORITY,
    REASON_CODES,
    STALE_FRONTIER,
    SUPERSEDED_EVIDENCE,
    TREATMENT_SIGNATURE_MISMATCH,
    UNKNOWN_GOVERNED_POLICY,
    CounterfactualEvidenceError,
    CounterfactualEvidenceStore,
    _m5_guard,
    bind_counterfactual_evidence,
    build_governed_counterfactual_evidence,
    rebuild_governed_exit_evidence,
    treatment_component,
    treatment_signature,
    validate_governed_counterfactual_evidence,
    verify_counterfactual_rows,
    verify_governed_counterfactual_binding,
)
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments.governed_scientific_result import (
    AMBIGUOUS_GOVERNED_INTERVENTION,
    INSUFFICIENT_GOVERNED_EVIDENCE,
    NO_INTERVENTION_MAPPING,
    governed_scientific_metrics,
    governed_scientific_result,
)
from research_engine.experiments.q71_evidence_classes import (
    PRODUCTION_EVIDENCE_CLASSES,
    ProductionEvidenceClass,
    ProductionEvidenceClassError,
    resolve_governed_intervention_authority,
    structural_generated_question_families,
    unsupported_evidence_class_catalogue,
)
from research_engine.lifecycle.research_coverage import CoverageEvidence
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.continuous.q71_production_registry import (
    production_capability_matrix,
    write_production_evaluator_registry,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy,
    run_generated_question_worker,
)
from research_engine.v10.continuous.research_lab import (
    build_lab_view,
    render_lab_terminal,
)

from tests.test_canonical_question_cycle import MemoryS3, _freeze
from tests.test_exit_candidate_replay import FLAT, _repeat, _reproduced_source
from tests.test_governed_scientific_result import _hd09_lifecycle, _objects
from tests.test_q71_production_evaluators import (
    _canonical_question_projection, Harness,
)


COUNT = 200
GOVERNED_POLICY_ID = "REDUCED_TP_0_50R_V1"
EVIDENCE_CLASS = COUNTERFACTUAL_EVIDENCE_CLASS
EVALUATOR_KEY = "q71.counterfactual.governed_exit_policy"
PRODUCED_AT = "2026-10-08T00:05:00+00:00"


# ── Governed evidence fixtures ───────────────────────────────────────────────

def rows_for(bars=None):
    """Real governed shadow_runtime rows plus their governed M5 candles.

    ``bars`` selects an alternative governed population: the default fixture is
    a population whose baseline times out and therefore benefits from a reduced
    target; the ``tp_bars`` population reaches the original target and therefore
    loses under every governed candidate policy.
    """
    opens, closes, candles = [], [], []
    for index in range(COUNT):
        if bars is None:
            source = _hd09_lifecycle(index)
        else:
            base = _reproduced_source(index=index, bars=bars(index))
            trade_id = f"nshadow_{index:016x}"
            base.open_event["shadow_trade_id"] = trade_id
            base.close_event["shadow_trade_id"] = trade_id
            base.open_event["identity"] = {
                "entity_id": "EURUSD_1", "cycle_id": index,
                "trade_horizon": "SCALP", "evaluated_horizon": "SCALP",
                "shadow_type": "HORIZON_ALTERNATIVE",
            }
            source = base
        opens.append(dict(source.open_event))
        closes.append(dict(source.close_event))
        candles.extend(dict(item) for item in source.candle_events)
    return opens + closes, candles


def tp_bars(index):
    """A governed population that reaches the original target on its last bar."""
    return _repeat(FLAT, 8) + ((100.0, 104.5, 99.0, 104.2),)


class CandidateHarness(Harness):
    """The production generated-question harness on the counterfactual family."""

    def __init__(self, tmp_path, rows) -> None:
        super().__init__(tmp_path, evidence_class=EVIDENCE_CLASS)
        self.shadow_rows, self.candles = rows

    def freeze_at(self, root, rows):
        fake = MemoryS3(_objects(rows[0]))
        snapshot, manifests = _freeze(root, fake)
        source = S3ResearchDataSource(
            bucket="question-cycle-test", client=fake)
        return snapshot, manifests, source


def artifact_for(rows, snapshot, *, produced_at: str = PRODUCED_AT):
    """Freeze the governed counterfactual evidence for one governed population."""
    artifact = build_governed_counterfactual_evidence(
        shadow_runtime_rows=rows[0], candle_rows=rows[1],
        snapshot_id=snapshot.snapshot_id,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_start=snapshot.start_date, frontier_end=snapshot.end_date,
        produced_at=produced_at,
        source_dataset_identities={
            "shadow_runtime": "DSNAP-TEST-SHADOW",
            "events": "GOVERNED_M5_CANDLE_STREAM",
        })
    return artifact, bind_counterfactual_evidence(
        artifact, bound_at=produced_at)


def run_worker(harness, *, snapshot, manifests, source, registry,
               evidence=None, binding=None, recorded_at=PRODUCED_AT,
               max_questions: int = 4):
    return run_generated_question_worker(
        snapshot_id=snapshot.snapshot_id, evaluator_registry=registry,
        result_store=harness.results, execution_store=harness.execution,
        generated_store=harness.generated, q71_state_path=harness.q71_state,
        manifest_directory=manifests, source=source,
        policy=GeneratedExecutionPolicy(
            max_questions_per_run=max_questions, max_attempts=3),
        recorded_at=recorded_at,
        governed_counterfactual_evidence=evidence,
        governed_counterfactual_binding=binding)


class Activated:
    """One fully activated candidate-capable production Q71 environment."""

    def __init__(self, tmp_path: Path, rows=None) -> None:
        self.rows = rows or rows_for()
        self.harness = CandidateHarness(tmp_path, self.rows)
        self.cell = self.harness.cells[0]
        self.harness.register_coverage(
            {self.cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",))},
            epoch=1)
        self.snapshot, self.manifests, self.source = self.harness.freeze_at(
            self.harness.root, self.rows)
        self.artifact, self.binding = artifact_for(self.rows, self.snapshot)
        self.registry = write_production_evaluator_registry(
            tmp_path / "prod_registry.json")

    def orchestrate(self):
        return self.harness.orchestrate(evaluator_registry=self.registry)

    def execute(self, **kwargs):
        kwargs.setdefault("evidence", self.artifact)
        kwargs.setdefault("binding", self.binding)
        return run_worker(
            self.harness, snapshot=self.snapshot, manifests=self.manifests,
            source=self.source, registry=self.registry, **kwargs)


@pytest.fixture
def activated(tmp_path):
    return Activated(tmp_path)


# ── Part A: governed counterfactual evidence contract ───────────────────────

def test_candidate_capable_evidence_schema_round_trips_strictly(tmp_path):
    """1. The frozen artifact parses strictly and round-trips its own identity."""
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    artifact, _ = artifact_for(rows, snapshot)
    assert artifact.schema == "governed_counterfactual_exit_policy_evidence_v1"
    assert artifact.dataset_id.startswith("CFE-")
    assert artifact.evidence_class == EVIDENCE_CLASS
    assert artifact.scientifically_analysable is True
    assert len(artifact.rows) == COUNT * len(CANDIDATE_POLICIES_V1)
    payload = json.loads(json.dumps(artifact.to_dict()))
    restored = validate_governed_counterfactual_evidence(payload)
    assert restored.content_digest == artifact.content_digest
    assert [row.row_digest for row in restored.rows] == [
        row.row_digest for row in artifact.rows]
    # A tampered payload never round-trips.
    payload["rows"][0]["counterfactual_r"] = 999.0
    with pytest.raises(CounterfactualEvidenceError,
                       match="INVALID_COUNTERFACTUAL_SCHEMA"):
        validate_governed_counterfactual_evidence(payload)
    payload = json.loads(json.dumps(artifact.to_dict()))
    payload["content_digest"] = "0" * 64
    with pytest.raises(CounterfactualEvidenceError, match="content_digest"):
        validate_governed_counterfactual_evidence(payload)


def test_evidence_identity_is_deterministic(tmp_path):
    """2. Identical governed evidence produces an identical artifact identity."""
    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    first, _, _ = harness.freeze_at(tmp_path / "one", rows)
    second, _, _ = harness.freeze_at(tmp_path / "two", rows)
    a, _ = artifact_for(rows, first, produced_at="2026-10-08T00:00:00+00:00")
    b, _ = artifact_for(rows, second, produced_at="2026-10-08T01:00:00+00:00")
    assert first.snapshot_id == second.snapshot_id
    assert a.dataset_id == b.dataset_id
    assert a.content_digest == b.content_digest
    other_rows = rows_for(bars=tp_bars)
    other, _, _ = harness.freeze_at(tmp_path / "three", other_rows)
    c, _ = artifact_for(other_rows, other)
    assert c.dataset_id != a.dataset_id
    assert c.content_digest != a.content_digest


def test_source_lineage_is_complete(tmp_path):
    """3. Every provenance field the contract requires is present and pinned."""
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    artifact, binding = artifact_for(rows, snapshot)
    assert artifact.producer_identity == (
        "research_engine.control_plane.governed_counterfactual_evidence")
    assert artifact.producer_version
    assert artifact.replay_method and artifact.replay_version
    assert artifact.m5_authority == "events_v1:CANDLE:mt5_data:M5"
    assert artifact.baseline_policy_id == "SHADOW_BASELINE_V1"
    assert artifact.governed_policy_catalogue == GOVERNED_POLICY_CATALOGUE
    assert artifact.governed_policy_ids == tuple(
        item["policy_id"] for item in CANDIDATE_POLICIES_V1)
    assert artifact.population_identity == EVIDENCE_CLASS
    assert artifact.snapshot_id == snapshot.snapshot_id
    assert artifact.snapshot_fingerprint == snapshot.snapshot_fingerprint
    assert artifact.investigation_epoch == snapshot.evidence_epoch
    assert artifact.frontier_start and artifact.frontier_end
    assert artifact.produced_at
    assert dict(artifact.source_dataset_identities) == {
        "events": "GOVERNED_M5_CANDLE_STREAM",
        "shadow_runtime": "DSNAP-TEST-SHADOW",
    }
    assert artifact.source_shadow_runtime_digest
    assert artifact.source_candle_digest
    assert artifact.candle_rows
    row = artifact.rows[0]
    assert row.source_evidence_lineage["hd09_adjudication_version"]
    assert row.source_evidence_lineage["path_analytical_digest"]
    assert row.source_evidence_lineage["baseline_reproduction_digest"]
    assert row.source_evidence_lineage["candidate_replay_digest"]
    assert row.required_bar_availability["authority"] == (
        "events_v1:CANDLE:mt5_data:M5")
    assert row.leakage_guard["entry_bar_evaluated"] is False
    assert row.replay_method == artifact.replay_method
    # The membership pins the artifact to this snapshot identity exactly.
    assert binding.snapshot_id == artifact.snapshot_id
    assert binding.content_digest == artifact.content_digest
    assert binding.governed_policy_catalogue == GOVERNED_POLICY_CATALOGUE


def test_missing_m5_candle_authority_fails_closed_without_rows(tmp_path):
    """A snapshot-only population with no governed candle stream is a real gap."""
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    artifact, _ = artifact_for((rows[0], ()), snapshot)
    assert artifact.scientifically_analysable is False
    assert artifact.rows == ()
    assert set(artifact.reason_codes) >= {
        MISSING_M5_CANDLE_AUTHORITY, MISSING_COUNTERFACTUAL_EVIDENCE}
    assert artifact.reason_codes and all(
        code in REASON_CODES for code in artifact.reason_codes)


class _FakeSnapshot:
    """A deterministic snapshot identity for evidence-contract tests."""

    snapshot_id = "ISNAP-0000000000000000"
    snapshot_fingerprint = "f" * 64
    evidence_epoch = "INVESTIGATION-EPOCH-CONTRACT"
    start_date = "2026-04-01"
    end_date = "2026-04-30"


def test_leakage_guard_accepts_a_governed_path_and_rejects_a_pre_entry_bar():
    """9. The leakage guard is re-asserted on the frozen evidence, fail-closed."""
    rows = rows_for()
    artifact, _ = artifact_for(rows, _FakeSnapshot())
    path_record = rebuild_governed_exit_evidence(
        artifact, rows[0]).path.records[0]
    availability, failure = _m5_guard(path_record)
    assert failure is None
    assert availability["post_entry_only"] is True
    assert availability["strictly_ascending"] is True
    assert availability["exit_aligned"] is True
    assert availability["supplied_m5_bars"] == availability["expected_m5_bars"]

    class _Bar:
        def __init__(self, stamp):
            self.timestamp_utc_ms = stamp

    class _Record:
        entry_utc_epoch_s = 1_000
        exit_utc_epoch_s = 1_900
        ordered_m5_bars = (_Bar(1_000_000), _Bar(1_600_000))

    _, rejected = _m5_guard(_Record())
    assert rejected == LEAKAGE_GUARD_FAILED


def test_invalid_and_incomplete_replay_fail_closed(tmp_path):
    """7/8. A tampered or incomplete governed row never reaches analysis."""
    from dataclasses import replace as _replace

    from research_engine.control_plane.governed_counterfactual_evidence import (
        CounterfactualEvidenceExclusion,
    )

    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    artifact, _ = artifact_for(rows, snapshot)
    rebuilt = rebuild_governed_exit_evidence(artifact, rows[0])
    verify_counterfactual_rows(artifact, rebuilt)

    broken = _replace(artifact, rows=(
        _replace(artifact.rows[0], source_evidence_lineage={
            **dict(artifact.rows[0].source_evidence_lineage),
            "candidate_replay_digest": "0" * 64}),
    ) + artifact.rows[1:])
    with pytest.raises(CounterfactualEvidenceError, match=INCOMPLETE_REPLAY):
        verify_counterfactual_rows(broken, rebuilt)

    broken_baseline = _replace(artifact, rows=(
        _replace(artifact.rows[0],
                 baseline_r=artifact.rows[0].baseline_r + 1.0),
    ) + artifact.rows[1:])
    with pytest.raises(CounterfactualEvidenceError, match=MISSING_BASELINE):
        verify_counterfactual_rows(broken_baseline, rebuilt)

    with pytest.raises(CounterfactualEvidenceError,
                       match="INVALID_COUNTERFACTUAL_SCHEMA"):
        CounterfactualEvidenceExclusion(
            lifecycle_identity=("a", "b", "c"), canonical_symbol="EURUSD",
            trade_horizon="SCALP", governed_policy_id=None,
            reason="FREE_TEXT_REASON")


def test_exact_governed_policy_lookup_and_signature_determinism():
    """10/11/12/13. Only governed catalogue policies are representable."""
    policy = next(item for item in CANDIDATE_POLICIES_V1
                  if item["policy_id"] == GOVERNED_POLICY_ID)
    signature = treatment_signature(policy)
    assert signature == treatment_signature(dict(policy))
    assert len(signature) == 64
    assert treatment_component(policy) == "TARGET_GEOMETRY"
    with pytest.raises(CounterfactualEvidenceError,
                       match=UNKNOWN_GOVERNED_POLICY):
        treatment_signature(
            {"policy_id": "INVENTED_POLICY", "policy_type": "TRAILING"})
    with pytest.raises(CounterfactualEvidenceError,
                       match=TREATMENT_SIGNATURE_MISMATCH):
        treatment_signature({
            "policy_id": GOVERNED_POLICY_ID, "policy_type": "REDUCED_TP",
            "target_cap_r": 9.99,
        })
    with pytest.raises(CounterfactualEvidenceError,
                       match=UNKNOWN_GOVERNED_POLICY):
        treatment_component({"policy_id": "X", "policy_type": "MADE_UP"})


def test_binding_membership_is_snapshot_pinned(tmp_path):
    """A membership from another frontier or epoch can never be admitted."""
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    _, binding = artifact_for(rows, snapshot)
    assert verify_governed_counterfactual_binding(
        binding, snapshot_id=snapshot.snapshot_id,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch).dataset_id == binding.dataset_id
    with pytest.raises(CounterfactualEvidenceError, match=STALE_FRONTIER):
        verify_governed_counterfactual_binding(
            binding, snapshot_id="ISNAP-OTHER",
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            investigation_epoch=snapshot.evidence_epoch)
    with pytest.raises(CounterfactualEvidenceError, match=SUPERSEDED_EVIDENCE):
        verify_governed_counterfactual_binding(
            binding, snapshot_id=snapshot.snapshot_id,
            snapshot_fingerprint="a" * 64,
            investigation_epoch=snapshot.evidence_epoch)
    with pytest.raises(CounterfactualEvidenceError, match=SUPERSEDED_EVIDENCE):
        verify_governed_counterfactual_binding(
            binding, snapshot_id=snapshot.snapshot_id,
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            investigation_epoch="INVESTIGATION-EPOCH-OTHER")
    with pytest.raises(CounterfactualEvidenceError,
                       match=MISSING_COUNTERFACTUAL_EVIDENCE):
        verify_governed_counterfactual_binding(
            None, snapshot_id=snapshot.snapshot_id,
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            investigation_epoch=snapshot.evidence_epoch)


def test_artifact_store_is_write_once_and_content_addressed(tmp_path):
    """A frozen artifact is immutable: a conflicting rewrite is refused."""
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    artifact, _ = artifact_for(rows, snapshot)
    store = CounterfactualEvidenceStore(tmp_path / "evidence")
    store.register(artifact, bound_at=PRODUCED_AT)
    assert store.dataset_ids() == (artifact.dataset_id,)
    assert store.load(artifact.dataset_id).content_digest == artifact.content_digest
    assert store.load_latest().dataset_id == artifact.dataset_id
    assert store.binding_for(artifact.dataset_id).dataset_id == artifact.dataset_id
    store.register(artifact, bound_at=PRODUCED_AT)
    tampered = json.loads(json.dumps(artifact.to_dict()))
    tampered["rows"][0]["counterfactual_r"] = 999.0
    store.path_for(artifact.dataset_id).write_text(
        json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CounterfactualEvidenceError):
        store.load(artifact.dataset_id)
    # A conflicting rewrite through the store is refused as well.
    store.path_for(artifact.dataset_id).write_text(
        json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CounterfactualEvidenceError, match="identity_collision"):
        store.register(artifact, bound_at=PRODUCED_AT)


# ── Part B: the production Q71 evaluator ────────────────────────────────────

def _evaluate(harness, artifact, binding, *, shadow=None, question_id="GEN-TEST"):
    from research_engine.experiments.q71_production_evaluators import (
        governed_exit_policy_counterfactual,
    )

    return governed_exit_policy_counterfactual(
        generated_question_id=question_id,
        datasets={"shadow_runtime": shadow if shadow is not None
                  else harness.shadow_rows},
        governed_counterfactual_evidence=artifact,
        governed_counterfactual_binding=binding)


def test_production_evaluator_emits_the_rb1_contract_with_an_intervention(
    tmp_path,
):
    """14/21. The production evaluator declares RB1 science and a real policy."""
    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    artifact, binding = artifact_for(rows, _FakeSnapshot())
    report = _evaluate(harness, artifact, binding, question_id="GEN-CF-1")
    assert report["status"] == "COMPLETE"
    declared = governed_scientific_result(report)
    assert declared is not None
    assert declared.scientifically_meaningful is True
    assert declared.question_id == "GEN-CF-1"
    assert declared.signal.signal_type == EVIDENCE_CLASS
    assert declared.signal.sample_size == COUNT
    assert declared.signal.significance_method.startswith("opportunity_clustered")
    assert declared.signal.population["canonical_authority_question_id"] == "EX1"
    assert declared.falsification is not None and declared.falsification.criteria
    design = declared.candidate_design
    assert design is not None
    assert design.governed_policy_id == GOVERNED_POLICY_ID
    assert design.treatment_component == "TARGET_GEOMETRY"
    assert dict(design.treatment_parameters) == next(
        item for item in CANDIDATE_POLICIES_V1
        if item["policy_id"] == GOVERNED_POLICY_ID)
    assert design.validation_criteria["required_sample"] == 200
    assert design.success_conditions and design.failure_conditions
    assert design.intervention_rationale
    metrics = governed_scientific_metrics(report)
    assert metrics["scientifically_meaningful"] is True
    assert metrics["governed_policy_id"] == GOVERNED_POLICY_ID
    assert report["provenance"]["canonical_authority"] == (
        "research_engine.experiments.exit_policy_governed")
    assert report["provenance"]["governed_counterfactual_evidence"][
        "dataset_id"] == artifact.dataset_id


def test_production_evaluator_declines_without_a_governed_intervention(
    tmp_path,
):
    """22. A meaningful result with no supported policy creates no candidate."""
    rows = rows_for(bars=tp_bars)
    harness = CandidateHarness(tmp_path, rows)
    artifact, binding = artifact_for(rows, _FakeSnapshot())
    report = _evaluate(harness, artifact, binding)
    declared = governed_scientific_result(report)
    assert declared is not None
    assert declared.scientifically_meaningful is True
    assert declared.candidate_design is None
    assert declared.no_intervention_reason in {
        NO_INTERVENTION_MAPPING, AMBIGUOUS_GOVERNED_INTERVENTION}
    metrics = governed_scientific_metrics(report)
    assert metrics.get("governed_policy_id") is None
    assert metrics["no_governed_intervention_reason"] in {
        NO_INTERVENTION_MAPPING, AMBIGUOUS_GOVERNED_INTERVENTION}


def test_production_evaluator_fails_closed_without_admitted_evidence(tmp_path):
    """17/18/23. No artifact, no analysis, no finding, no candidate."""
    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    report = _evaluate(harness, None, None)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["provenance"]["fail_closed_reason"] == (
        MISSING_COUNTERFACTUAL_EVIDENCE)
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == INSUFFICIENT_GOVERNED_EVIDENCE
    assert declared.candidate_design is None


def test_production_evaluator_fails_closed_on_an_unanalysable_artifact(tmp_path):
    """A frozen artifact with no admissible rows is never analysed."""
    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    artifact, binding = artifact_for((rows[0], ()), _FakeSnapshot())
    report = _evaluate(harness, artifact, binding)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["provenance"]["fail_closed_reason"] == (
        MISSING_M5_CANDLE_AUTHORITY)
    assert governed_scientific_result(report).scientifically_meaningful is False


def test_production_evaluator_refuses_evidence_from_another_population(tmp_path):
    """Stale/superseded evidence fails closed instead of being re-analysed."""
    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    artifact, binding = artifact_for(rows, _FakeSnapshot())
    other = rows_for(bars=tp_bars)
    report = _evaluate(harness, artifact, binding, shadow=other[0])
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["provenance"]["fail_closed_reason"] == STALE_FRONTIER
    assert governed_scientific_result(report).scientifically_meaningful is False


def test_production_evaluator_uses_no_filesystem_and_no_live_s3(tmp_path):
    """4/5/6. The evaluator runs inside the snapshot-only runner environment."""
    from research_engine.v10.continuous.canonical_question_cycle import (
        SnapshotEscapeError, _snapshot_only_runner_environment,
    )
    from research_engine.v10.investigation_snapshot import (
        open_investigation_snapshot,
    )

    rows = rows_for()
    harness = CandidateHarness(tmp_path, rows)
    snapshot, manifests, source = harness.freeze_at(tmp_path, rows)
    artifact, binding = artifact_for(rows, snapshot)
    reader = open_investigation_snapshot(
        snapshot.snapshot_id, source=source, manifest_directory=manifests)
    with _snapshot_only_runner_environment(reader):
        report = _evaluate(harness, artifact, binding)
        assert report["status"] == "COMPLETE"
        assert governed_scientific_result(report).scientifically_meaningful is True
        with pytest.raises(SnapshotEscapeError, match="FILESYSTEM"):
            open("C:/evidence/shadow_runtime_v1/part-000.jsonl", "r")
        with pytest.raises(SnapshotEscapeError, match="FILESYSTEM"):
            Path("C:/evidence/events_v1/part-000.jsonl").open("r")
        with pytest.raises(SnapshotEscapeError, match="LIVE_S3"):
            S3ResearchDataSource(bucket="trading-bot-v10-data")._get_client()


def test_complete_does_not_imply_scientific(tmp_path):
    """15. A structurally COMPLETE evaluator can still be non-scientific."""
    from research_engine.experiments.q71_production_evaluators import (
        governed_exit_path_distribution,
    )
    from tests.test_governed_scientific_result import _shadow_lifecycles

    raw = _shadow_lifecycles((2.0, -1.0, 1.5, 0.5, -0.5, 3.0))
    report = governed_exit_path_distribution(
        generated_question_id="GEN-DESC", datasets={"shadow_runtime": raw})
    assert report["status"] == "COMPLETE"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == "DESCRIPTIVE_ONLY"


# ── Part C: worker membership, candidate lifecycle, coverage, Lab ───────────

def test_worker_refuses_ungoverned_or_stale_counterfactual_evidence(tmp_path):
    """The worker admits frozen evidence only through a verified membership."""
    activated = Activated(tmp_path)
    question_id = activated.orchestrate()["new_question_ids"][0]

    # An artifact without a governed membership is never admitted.
    execution = activated.execute(evidence=activated.artifact, binding=None)
    assert execution["outcomes"][0]["execution_status"] == "BLOCKED"
    assert execution["outcomes"][0]["reason_code"] == (
        MISSING_COUNTERFACTUAL_EVIDENCE)
    assert execution["result_ids"] == []

    # A membership from another frontier is refused, not repaired.
    stale = json.loads(json.dumps(activated.binding.to_dict()))
    stale["snapshot_id"] = "ISNAP-OTHER-FRONTIER"
    execution = activated.execute(evidence=activated.artifact, binding=stale)
    assert execution["outcomes"][0]["execution_status"] == "BLOCKED"
    assert execution["outcomes"][0]["reason_code"] == STALE_FRONTIER
    assert execution["result_ids"] == []

    # With the verified membership the same question executes normally.
    execution = activated.execute()
    assert execution["outcomes"][0]["execution_status"] == "COMPLETE"
    assert execution["result_ids"]
    assert execution["outcomes"][0]["generated_question_id"] == question_id


def test_generated_q71_creates_a_candidate_through_the_shared_bridge(tmp_path):
    """18-21/27/28. generated Q71 -> finding -> hypothesis -> candidate."""
    activated = Activated(tmp_path)
    orchestration = activated.orchestrate()
    question_id = orchestration["new_question_ids"][0]
    row = orchestration["generated_questions"][0]
    assert row["evaluator"] == EVALUATOR_KEY
    assert row["evidence_requirements"]["evidence_class"] == EVIDENCE_CLASS

    execution = activated.execute()
    assert execution["result_ids"]
    result = activated.harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "COMPLETE"
    assert result.scientific_status == "SCIENTIFICALLY_MEANINGFUL"
    assert result.generated_question_id == question_id
    assert result.evaluator_key == EVALUATOR_KEY
    assert result.evidence_datasets == ("shadow_runtime",)
    assert result.governed_scientific_metrics["governed_policy_id"] == (
        GOVERNED_POLICY_ID)
    assert result.governed_scientific_metrics["scientifically_meaningful"] is True

    bridge = activated.harness.bridge(execution["batch_id"])
    assert bridge.source_kind == "GENERATED_QUESTION_EXECUTION_BATCH"
    assert len(bridge.findings_created) == 1
    assert len(bridge.hypotheses_created) == 1
    assert len(bridge.candidates_created) == 1
    assert bridge.validation_handoff

    hypothesis = activated.harness.registry.get_hypothesis(
        bridge.hypotheses_created[0])
    assert hypothesis.source_question == question_id
    assert hypothesis.falsification_criteria
    assert hypothesis.status == "PROPOSED"
    assert hypothesis.evidence_lineage["snapshot_id"] == (
        activated.snapshot.snapshot_id)

    candidate_id = bridge.candidates_created[0]
    candidate = activated.harness.registry.get_candidate(candidate_id)
    assert candidate.status == "PROPOSED"
    assert candidate.policy_id == GOVERNED_POLICY_ID
    assert candidate.treatment_hash
    # The candidate's question-result lineage is the governed transport result,
    # and the immutable generated result identity is carried in provenance.
    assert candidate.source_question_results == [
        result.transport_result["result_id"]]
    assert candidate.provenance["generated_question_result_id"] == result.result_id
    assert candidate.validation_requirements["required_sample"] == 200
    assert candidate.target_population["question_id"] == question_id
    assert candidate.target_population["canonical_authority_question_id"] == "EX1"
    assert candidate.provenance["treatment_signature"]["subject_ref"] == question_id
    plan = activated.harness.registry.get_plan(candidate_id)
    assert plan is not None
    assert plan.minimum_sample == 200
    assert plan.target_questions == [question_id]
    assert plan.success_conditions and plan.failure_conditions

    # The candidate carries the full generated-research lineage.
    handoff = bridge.validation_handoff[0]
    assert handoff["candidate_id"] == candidate_id
    assert handoff["status"] == "PROPOSED"
    assert handoff["source_question_result_id"] == (
        result.transport_result["result_id"])
    assert handoff["source_hypothesis_id"] == bridge.hypotheses_created[0]


def test_candidate_never_gains_runtime_authority_or_deploys(tmp_path):
    """29/30/39. A Q71 candidate is a proposal, never a live policy."""
    activated = Activated(tmp_path)
    activated.orchestrate()
    execution = activated.execute()
    bridge = activated.harness.bridge(execution["batch_id"])
    candidate_id = bridge.candidates_created[0]
    candidate = activated.harness.registry.get_candidate(candidate_id)
    assert candidate.shadow_binding.get("live_approved", False) is False
    assert candidate.shadow_binding == {}
    assert candidate.status not in {
        "VALIDATED", "FORWARD_VALIDATED", "READY_FOR_PROMOTION_REVIEW",
        "ACCEPTED", "LIVE_APPROVED",
    }
    assert "Block 3 proposal only" in candidate.notes
    projection = activated.harness.projection(
        q71=activated.orchestrate(), bridge=bridge)
    row = next(item for item in projection["candidates"]
               if item["candidate_id"] == candidate_id)
    assert row["live_approved"] is False
    assert row["runtime_authority"] == "NOT_LIVE"
    assert row["human_approval_required"] is True
    assert row["promotion_action"] is None


def test_unchanged_rerun_creates_no_duplicate_result_or_candidate(tmp_path):
    """24/25. The same governed science never mints a second candidate."""
    activated = Activated(tmp_path)
    activated.orchestrate()
    first = activated.execute()
    assert first["result_ids"]
    bridge = activated.harness.bridge(first["batch_id"])
    assert len(bridge.candidates_created) == 1
    candidate_id = bridge.candidates_created[0]

    # Reconciling the identical immutable batch is idempotent: it returns the
    # same receipt and creates nothing new.
    again = activated.harness.bridge(first["batch_id"])
    assert again.to_dict() == bridge.to_dict()
    assert [item.candidate_id
            for item in activated.harness.registry.list_candidates()] == [
        candidate_id]

    # New evidence epoch, identical science: the worker mints a new immutable
    # result for the re-entry, but the reconciliation is a NO_FINDING_CHANGE and
    # therefore creates no duplicate candidate.
    activated.harness.register_coverage(
        {activated.cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-2",))},
        epoch=2)
    reentry = activated.orchestrate()
    assert reentry["new_question_ids"] == []
    assert reentry["generated_questions"][0]["reentry_reason"] == (
        "RELEVANT_EVIDENCE_CHANGED")
    second = activated.execute(recorded_at="2026-10-08T02:00:00+00:00")
    assert second["result_ids"] and second["result_ids"][0] != first["result_ids"][0]
    third = activated.harness.bridge(second["batch_id"])
    assert third.candidates_created == ()
    assert third.findings_created == ()
    assert [item.candidate_id
            for item in activated.harness.registry.list_candidates()] == [
        candidate_id]


def test_new_evidence_re_enters_and_invalidates_the_candidate_upstream(tmp_path):
    """26/37/38/39. New governed evidence re-enters, then invalidates upstream."""
    activated = Activated(tmp_path)
    activated.orchestrate()
    first = activated.execute()
    bridge_a = activated.harness.bridge(first["batch_id"])
    candidate_id = bridge_a.candidates_created[0]
    assert activated.harness.registry.get_candidate(candidate_id).status == (
        "PROPOSED")

    # A second governed population whose baseline reaches the original target:
    # every governed policy now loses, so the finding is invalidated upstream.
    other_rows = rows_for(bars=tp_bars)
    snapshot_b, manifests_b, source_b = activated.harness.freeze_at(
        activated.harness.root / "second", other_rows)
    artifact_b, binding_b = artifact_for(
        other_rows, snapshot_b, produced_at="2026-10-08T00:20:00+00:00")
    activated.harness.register_coverage(
        {activated.cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-2",))},
        epoch=2)
    reentry = activated.orchestrate()
    assert reentry["new_question_ids"] == []
    row = reentry["generated_questions"][0]
    assert row["status"] == "QUEUED"
    assert row["reentry_reason"] == "RELEVANT_EVIDENCE_CHANGED"
    assert row["reentry_authorized"] is True

    second = run_worker(
        activated.harness, snapshot=snapshot_b, manifests=manifests_b,
        source=source_b, registry=activated.registry,
        evidence=artifact_b, binding=binding_b,
        recorded_at="2026-10-08T00:30:00+00:00")
    assert second["result_ids"]
    assert second["result_ids"][0] != first["result_ids"][0]
    result_b = activated.harness.results.load_result(second["result_ids"][0])
    execution_state = activated.harness.execution.states()[
        result_b.generated_question_id]
    assert execution_state["predecessor_result_id"] == first["result_ids"][0]
    assert len(execution_state["result_history"]) == 2
    assert result_b.governed_scientific_metrics["scientifically_meaningful"] is True
    assert result_b.governed_scientific_metrics.get("governed_policy_id") is None
    assert result_b.governed_scientific_metrics[
        "no_governed_intervention_reason"] in {
        NO_INTERVENTION_MAPPING, AMBIGUOUS_GOVERNED_INTERVENTION}

    bridge_b = activated.harness.bridge(second["batch_id"])
    assert bridge_b.findings_invalidated
    assert bridge_b.candidates_created == ()
    assert bridge_b.candidates_updated == (candidate_id,)
    assert activated.harness.registry.get_candidate(candidate_id).status == (
        "BLOCKED_UPSTREAM_INVALIDATED")
    assert bridge_b.governance_signals == ()
    assert len(activated.harness.registry.list_candidates()) == 1


def test_candidate_capable_family_is_declared_honestly():
    """31/33/34. Exactly one candidate-capable family, and no catch-all."""
    declarations = {item.evidence_class: item for item in PRODUCTION_EVIDENCE_CLASSES}
    assert EVIDENCE_CLASS in declarations
    declaration = declarations[EVIDENCE_CLASS]
    assert declaration.candidate_capable is True
    assert declaration.scientific_finding_capable is True
    assert declaration.hypothesis_capable is True
    assert declaration.governed_intervention_policy_id is None
    assert declaration.governed_intervention_policy_catalogue == (
        GOVERNED_POLICY_CATALOGUE)
    assert resolve_governed_intervention_authority(declaration) is (
        treatment_signature)
    assert declaration.governed_datasets == ("shadow_runtime",)
    assert set(declaration.governed_datasets) <= set(
        __import__("research_engine.v10.investigation_snapshot",
                   fromlist=["BOUND_DATASETS"]).BOUND_DATASETS)

    # Every other declared family is explicitly not candidate-capable.
    for other in PRODUCTION_EVIDENCE_CLASSES:
        if other.evidence_class == EVIDENCE_CLASS:
            continue
        assert other.candidate_capable is False
        assert other.governed_intervention_policy_id is None
        assert other.governed_intervention_authority is None

    matrix = production_capability_matrix()
    assert matrix["candidate_capable_families"] == [EVIDENCE_CLASS]
    assert matrix["total_structural_families"] == 7
    assert matrix["supported_families"] == 4
    assert matrix["unsupported_families"] == 3

    # Unsupported structural families stay unsupported and stay in the
    # denominator, so coverage can never be improved by narrowing it.
    unsupported = unsupported_evidence_class_catalogue()
    assert set(unsupported) == {
        "EVENTS", "COUNTERFACTUAL_SHADOW_SIMULATED_OUTCOME",
        "SHADOW_CANDIDATE_PROSPECTIVE"}
    assert set(structural_generated_question_families()) == (
        set(declarations) | set(unsupported))
    assert EVIDENCE_CLASS not in unsupported


def test_declaration_rejects_a_fabricated_intervention_mapping():
    """43/44. No generic or free-text intervention mapping can be declared."""
    base = dict(
        evidence_class="SYNTHETIC_PROBE", canonical_question_id="E1",
        canonical_evaluator_module="research_engine.experiments.expected_value",
        governed_datasets=("shadow_runtime",), observation_grain="probe",
        estimand="probe", statistical_method="probe",
        minimum_evidence={"minimum": 1}, scientific_finding_capable=True,
        hypothesis_capable=True, capability_class="PROBE",
        declared_scope_note="probe",
    )
    # Candidate capability without any governed intervention is refused.
    with pytest.raises(ProductionEvidenceClassError,
                       match="CANDIDATE_REQUIRES_GOVERNED_POLICY"):
        ProductionEvidenceClass(candidate_capable=True,
                                governed_intervention_policy_id=None, **base)
    # A policy outside the governed catalogue is refused.
    with pytest.raises(ProductionEvidenceClassError,
                       match="INTERVENTION_POLICY_UNKNOWN"):
        ProductionEvidenceClass(
            candidate_capable=True,
            governed_intervention_policy_id="FREE_TEXT_POLICY", **base)
    # A free-text authority is refused: it must be an importable callable and it
    # must name the governed policy catalogue.
    with pytest.raises(ProductionEvidenceClassError,
                       match="INTERVENTION_AUTHORITY_INVALID"):
        ProductionEvidenceClass(
            candidate_capable=True, governed_intervention_policy_id=None,
            governed_intervention_authority="whichever policy looks best",
            governed_intervention_policy_catalogue=GOVERNED_POLICY_CATALOGUE,
            **base)
    with pytest.raises(ProductionEvidenceClassError,
                       match="INTERVENTION_CATALOGUE_UNKNOWN"):
        ProductionEvidenceClass(
            candidate_capable=True, governed_intervention_policy_id=None,
            governed_intervention_authority=(
                "research_engine.control_plane.governed_counterfactual_evidence."
                "treatment_signature"),
            governed_intervention_policy_catalogue="some.other.catalogue",
            **base)
    # Declaring an intervention mapping for a non-candidate family is refused.
    with pytest.raises(ProductionEvidenceClassError,
                       match="AUTHORITY_WITHOUT_CANDIDATE"):
        ProductionEvidenceClass(
            candidate_capable=False, governed_intervention_policy_id=None,
            governed_intervention_authority=(
                "research_engine.control_plane.governed_counterfactual_evidence."
                "treatment_signature"),
            governed_intervention_policy_catalogue=GOVERNED_POLICY_CATALOGUE,
            **base)


def test_candidate_capable_cell_materializes_and_coverage_conserves(tmp_path):
    """31/32. The new family materializes a real cell and coverage conserves."""
    from research_engine.v10.continuous.production_observation_space import (
        ProductionCellBinding, bind_observation_cells,
        canonical_questions_for_evidence_class,
        governed_observation_cell_declarations, governed_observation_space,
    )
    from research_engine.v10.investigation_snapshot import BOUND_DATASETS

    declaration = next(item for item in PRODUCTION_EVIDENCE_CLASSES
                       if item.evidence_class == EVIDENCE_CLASS)
    assert canonical_questions_for_evidence_class(declaration) == ("EX1",)

    declarations = governed_observation_cell_declarations()
    cells = tuple(item for item in declarations
                  if item.evidence_class == EVIDENCE_CLASS)
    assert len(cells) == 1
    assert cells[0].subject_identity == "EX1"
    assert cells[0].population_identity == EVIDENCE_CLASS
    space = governed_observation_space()
    assert len([cell for cell in space.cells
                if cell.evidence_class == EVIDENCE_CLASS]) == 1

    # The cell binds to a real frozen governed evidence population.
    rows = rows_for()
    snapshot, _, _ = CandidateHarness(tmp_path, rows).freeze_at(tmp_path, rows)
    bindings = bind_observation_cells(
        space, snapshot, observation_space_snapshot_id="POS-" + "A" * 16)
    binding = next(item for item in bindings
                   if item.evidence_class == EVIDENCE_CLASS)
    assert isinstance(binding, ProductionCellBinding)
    assert binding.observed is True
    assert binding.evidence_volume > 0
    assert binding.evidence_snapshot_id == snapshot.snapshot_id
    assert binding.canonical_question_ids == ("EX1",)
    assert set(binding.governed_datasets) <= set(BOUND_DATASETS)

    matrix = production_capability_matrix()
    assert matrix["supported_families"] + matrix["unsupported_families"] == (
        matrix["total_structural_families"])


def test_lab_shows_candidate_lineage_and_never_claims_live(tmp_path):
    """35/36. The Lab states the whole lineage and that nothing is live."""
    activated = Activated(tmp_path)
    orchestration = activated.orchestrate()
    question_id = orchestration["new_question_ids"][0]
    execution = activated.execute()
    bridge = activated.harness.bridge(execution["batch_id"])
    candidate_id = bridge.candidates_created[0]

    projection = activated.harness.projection(
        q71=activated.orchestrate(), bridge=bridge)
    row = next(item for item in projection["generated_questions"]
               if item["generated_question_id"] == question_id)
    assert row["candidate_capable"] is True
    assert row["evidence_class"] == EVIDENCE_CLASS
    assert row["governed_intervention"]["policy_id"] is None
    assert row["governed_intervention"]["catalogue"] == GOVERNED_POLICY_CATALOGUE
    assert row["governed_policy_id"] == GOVERNED_POLICY_ID
    assert row["linked_findings"] == [
        ref.split(":", 1)[0] for ref in bridge.findings_created]
    assert row["linked_hypotheses"] == list(bridge.hypotheses_created)
    assert row["linked_candidates"] == [candidate_id]
    assert row["scientific_status"] == "SCIENTIFICALLY_MEANINGFUL"

    view = build_lab_view(projection)
    assert view["candidate_capable_generated_questions"] == [question_id]
    assert view["generated_questions_with_governed_policy"] == [question_id]
    assert view["candidates_human_approval_required"] == [candidate_id]
    assert view["candidates_not_live"] == [candidate_id]
    rendered = render_lab_terminal(view)
    assert "NOT LIVE / HUMAN APPROVAL REQUIRED" in rendered
    assert "Candidate-capable generated research: " + question_id in rendered
    for candidate in view["candidates"]:
        assert candidate["live_approved"] is False
        assert candidate["runtime_authority"] == "NOT_LIVE"


def test_lab_reports_no_intervention_mapping_without_a_candidate(tmp_path):
    """14. A meaningful result with no mapping is shown as such, not as a gap."""
    activated = Activated(tmp_path, rows=rows_for(bars=tp_bars))
    activated.orchestrate()
    execution = activated.execute()
    bridge = activated.harness.bridge(execution["batch_id"])
    assert bridge.candidates_created == ()
    projection = activated.harness.projection(
        q71=activated.orchestrate(), bridge=bridge)
    row = projection["generated_questions"][0]
    assert row["candidate_capable"] is True
    assert row["scientific_status"] == "SCIENTIFICALLY_MEANINGFUL"
    assert row["no_governed_intervention_reason"] in {
        NO_INTERVENTION_MAPPING, AMBIGUOUS_GOVERNED_INTERVENTION}
    assert row["governed_policy_id"] is None
    assert row["linked_candidates"] == []
    view = build_lab_view(projection)
    assert view["generated_questions_without_intervention"] == [
        row["generated_question_id"]]
    assert view["candidates"] == []


def test_canonical_question_order_and_identity_are_unchanged():
    """41/42. The canonical 70 and generated-question identity are untouched."""
    ids = tuple(question.id for question in REGISTRY)
    assert ids == tuple(BASELINE_QUESTION_IDS)
    assert len(ids) == 70
    assert tuple(item.evidence_class for item in PRODUCTION_EVIDENCE_CLASSES) == (
        "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
        "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        EVIDENCE_CLASS,
    )
    # The new declaration resolves to the EXISTING canonical question identity.
    declaration = next(item for item in PRODUCTION_EVIDENCE_CLASSES
                       if item.evidence_class == EVIDENCE_CLASS)
    assert declaration.canonical_question_id == "EX1"
    assert declaration.canonical_question_id in ids


def test_generated_question_identity_is_preserved_end_to_end(tmp_path):
    """42. One governed question identity flows through result/finding/hypothesis."""
    activated = Activated(tmp_path)
    orchestration = activated.orchestrate()
    question_id = orchestration["new_question_ids"][0]
    assert question_id.startswith("GEN-")
    assert question_id not in BASELINE_QUESTION_IDS
    execution = activated.execute()
    result = activated.harness.results.load_result(execution["result_ids"][0])
    bridge = activated.harness.bridge(execution["batch_id"])
    hypothesis = activated.harness.registry.get_hypothesis(
        bridge.hypotheses_created[0])
    candidate = activated.harness.registry.get_candidate(
        bridge.candidates_created[0])
    assert result.generated_question_id == question_id
    assert hypothesis.source_question == question_id
    assert candidate.source_question_results == [
        result.transport_result["result_id"]]
    assert candidate.provenance["question_id"] == question_id
    assert candidate.provenance["generated_question_result_id"] == (
        result.result_id)
    finding = next(item for item in activated.harness.scientific.document[
        "findings"].values())[-1]
    assert finding["lineage"]["generated_question_result_id"] == result.result_id


def test_canonical_and_q71_candidates_share_one_lifecycle_authority(tmp_path):
    """40. The Q71 candidate is an ordinary Block-3 candidate."""
    from research_engine.control_plane.candidate_lifecycle_authority import (
        to_canonical,
    )

    activated = Activated(tmp_path)
    activated.orchestrate()
    execution = activated.execute()
    bridge = activated.harness.bridge(execution["batch_id"])
    candidate_id = bridge.candidates_created[0]
    candidate = activated.harness.registry.get_candidate(candidate_id)
    # The generated candidate is mapped through the SAME governed lifecycle
    # authority as every canonical candidate; no Q71-specific lifecycle exists.
    assert to_canonical("CONTINUOUS_OPTIMISATION_REGISTRY", candidate.status) == (
        "PROPOSED")
    # And the validation handoff maps into the same canonical vocabulary.
    assert to_canonical("VALIDATION_QUEUE", "QUEUED") == "VALIDATION_QUEUED"
    assert activated.harness.registry.get_plan(candidate_id) is not None
    assert activated.harness.registry.get_candidate(candidate_id) is candidate
    assert candidate.hypothesis_id in {
        item.hypothesis_id for item in activated.harness.registry.list_hypotheses()}


def test_no_module_in_the_activation_chain_mutates_live_trading():
    """45. No part of the activation path can touch live trading."""
    live_fragments = (
        "core.runtime", "core.shadow.candidate_runtime",
        "core.shadow.opt_dp1_002", "deployment", "live_scanner",
    )
    # The producer, evaluator catalogue and registry must never reach a
    # candidate/finding writer either.
    writer_fragments = (
        "optimisation_registry", "scientific_state_store",
        "scientific_state_bridge", "research_projection",
    )
    modules = (
        "research_engine/control_plane/governed_counterfactual_evidence.py",
        "research_engine/experiments/q71_production_evaluators.py",
        "research_engine/experiments/q71_evidence_classes.py",
        "research_engine/v10/continuous/q71_production_registry.py",
    )
    forbidden_calls = {
        "create_candidate", "add_candidate", "register_candidate",
        "save_candidate", "create_finding", "add_finding", "write_finding",
        "register_opt_dp1_002", "place_order", "send_order", "modify_order",
        "close_position", "execute_trade",
    }
    for name in modules + ("research_engine/v10/continuous/q71_worker.py",):
        tree = ast.parse(Path(name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            fragments = live_fragments
            if name in modules:
                fragments = fragments + writer_fragments
            if isinstance(node, ast.ImportFrom):
                target = str(node.module or "")
                assert not any(fragment in target
                               for fragment in fragments), (name, target)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(fragment in alias.name
                                   for fragment in fragments), (name, alias.name)
            if isinstance(node, ast.Call):
                func = node.func
                label = getattr(func, "attr", None) or getattr(func, "id", None)
                assert label not in forbidden_calls, (name, label)
    # The producer never reaches a candidate registry or the worker's stores.
    source = Path(
        "research_engine/control_plane/governed_counterfactual_evidence.py"
    ).read_text(encoding="utf-8")
    assert "OptimisationRegistry" not in source
    assert "generated_question_result" not in source


def test_q71_worker_never_reaches_storage_for_candles():
    """18/19. The worker is snapshot/frozen-evidence only, for candles too."""
    source = Path(
        "research_engine/v10/continuous/q71_worker.py").read_text(
        encoding="utf-8")
    # No storage client, no live S3, no filesystem evidence scan.
    for forbidden in ("boto3", "S3ResearchDataSource", "s3_source",
                      "discover_dataset_objects", "read_bound_objects",
                      "read_dataset(", "glob(", "iterdir", "listdir",
                      "os.listdir", "_exit_path_audit"):
        assert forbidden not in source, forbidden
    # The only channel for candle evidence is the admitted frozen artifact.
    assert "governed_counterfactual_evidence" in source
    assert "governed_counterfactual_binding" in source
    assert "governed_m5_candle_authority" not in source


def test_reason_code_vocabulary_is_closed_and_complete():
    """17. Every mandated fail-closed reason code exists and no other may."""
    assert REASON_CODES == frozenset({
        "MISSING_COUNTERFACTUAL_EVIDENCE", "INVALID_COUNTERFACTUAL_SCHEMA",
        "INCOMPLETE_REPLAY", "LEAKAGE_GUARD_FAILED", "MISSING_BASELINE",
        "MISSING_TREATMENT_POLICY", "UNKNOWN_GOVERNED_POLICY",
        "TREATMENT_SIGNATURE_MISMATCH", "INSUFFICIENT_SAMPLE",
        "SCIENTIFICALLY_NOT_MEANINGFUL", "NO_INTERVENTION_MAPPING",
        "VALIDATION_CRITERIA_INCOMPLETE", "STALE_FRONTIER",
        "SUPERSEDED_EVIDENCE", "DUPLICATE_CANDIDATE", "UPSTREAM_INVALIDATED",
        "MISSING_M5_CANDLE_AUTHORITY", "MISSING_SHADOW_LIFECYCLE_POPULATION",
    })
