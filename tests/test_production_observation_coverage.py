"""Production observation space + governed coverage (autonomous Q71+ discovery).

These regressions prove that the *production* continuous-research path can
materialize a governed observation space from real frozen governed evidence,
build an immutable coverage snapshot, detect structural blind spots and hand the
result to the real Q71+ execution path - without manual question seeding and
without fabricating any observation cell, dataset binding or evaluator.

Evidence is the same real governed evidence the production path uses: a frozen
investigation snapshot produced by ``freeze_investigation_snapshot`` over real
``Production V1`` dataset objects.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_engine.experiments.q71_evidence_classes import (
    PRODUCTION_EVIDENCE_CLASSES,
    evidence_class_identity,
    structural_generated_question_families,
    unsupported_evidence_class_catalogue,
)
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchProposal,
)
from research_engine.lifecycle.generated_research_isolation import (
    canonical_inventory,
)
from research_engine.lifecycle.generated_research_store import (
    GeneratedResearchStore,
)
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.lifecycle.research_coverage import CoverageState
from research_engine.lifecycle.research_coverage_store import (
    ResearchCoverageStore,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore,
    GeneratedQuestionResultStore,
)
from research_engine.v10.continuous.production_coverage import (
    NO_PRODUCTION_OBSERVATION_SPACE,
    ProductionCoverageSnapshotStore,
    build_production_coverage,
    canonical_question_coverage_mapping,
    coverage_conservation,
    coverage_surface,
    materialize_production_coverage,
    production_coverage_snapshot_from_dict,
    q71_coverage_source_mapping,
)
from research_engine.control_plane.governed_m5_candle_authority import (
    M5_CANDLE_AUTHORITY_IDENTITY,
    M5CandleAuthorityStore,
    bind_m5_candle_authority,
    freeze_governed_m5_candle_authority,
)
from research_engine.v10.continuous.production_observation_space import (
    INVALID_SCHEMA,
    MISSING_DATASET_BINDING,
    MISSING_EVIDENCE,
    MISSING_EVALUATOR,
    MISSING_M5_CANDLE_AUTHORITY,
    STALE_FRONTIER,
    UNKNOWN_EVIDENCE_CLASS,
    ObservationSpaceSnapshot,
    ObservationSpaceSnapshotStore,
    ProductionObservationSpaceIdentityConflict,
    ProductionObservationSpaceNotMaterializable,
    bind_observation_cells,
    canonical_questions_for_evidence_class,
    governed_observation_cell_declarations,
    governed_observation_space,
    materialize_observation_space,
    production_observation_policy,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    GeneratedQuestionEvaluatorRegistry,
)
from research_engine.v10.continuous.q71_orchestration import (
    run_q71_orchestration,
)
from research_engine.v10.continuous.q71_production_registry import (
    write_production_evaluator_registry,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy,
    run_generated_question_worker,
)
from research_engine.v10.continuous.question_cycle_state import (
    QuestionCycleStore,
)
from research_engine.v10.continuous.research_lab import (
    build_lab_view,
    render_lab_terminal,
)

from tests.test_canonical_question_cycle import MemoryS3, _freeze
from tests.test_governed_scientific_result import _objects, _shadow_lifecycles


MEANINGFUL_R_MULTIPLES = (2.0, -1.0, 1.5, 0.5, -0.5, 3.0)
OTHER_R_MULTIPLES = (2.0, -1.0, 1.5, 0.5, -0.5, 4.5, 0.25)
CREATED_AT = "2026-10-08T00:00:00+00:00"


def _projection(*, statuses=None, result_snapshot: str = "") -> dict:
    """A canonical-70 projection shaped exactly like the persisted authority."""
    resolved = dict(statuses or {})
    questions = {}
    for question in REGISTRY:
        row: dict = {"question_id": question.id}
        status = resolved.get(question.id)
        if status:
            row["result"] = {
                "question_id": question.id,
                "result_id": "QRESULT-" + question.id,
                "status": status,
                "snapshot_id": result_snapshot,
                "evaluation_identity_digest": "a" * 64,
            }
        questions[question.id] = row
    return {"cycle_schema": "canonical_question_cycle_v1",
            "cycle_id": "QCYCLE-PRODUCTION", "questions": questions}


class Harness:
    """One isolated production observation-space/coverage environment."""

    def __init__(self, tmp_path: Path,
                 r_multiples=MEANINGFUL_R_MULTIPLES) -> None:
        self.root = Path(tmp_path)
        self.snapshot, self.manifests, self.client = self.freeze(
            r_multiples, name="evidence")
        # The governed source authority the frozen snapshot was created with; a
        # different bucket would (correctly) fail SOURCE_AUTHORITY_CHANGED.
        self.source = S3ResearchDataSource(
            bucket="question-cycle-test", client=self.client)
        self.registry = write_production_evaluator_registry(
            self.root / "evaluator_registry.json")

    def freeze(self, r_multiples, *, name: str):
        fake = MemoryS3(_objects(_shadow_lifecycles(list(r_multiples))))
        snapshot, manifests = _freeze(self.root / name, fake)
        return snapshot, manifests, fake

    def materialize(self, *, snapshot=None, manifests=None, statuses=None,
                    result_snapshot: str | None = None, registry="default",
                    projection=None, policy=None, name: str = "materialization",
                    candle_authority=None, candle_authority_binding=None,
                    created_at: str = CREATED_AT, observed_at: str | None = None):
        snapshot = snapshot or self.snapshot
        manifests = manifests or self.manifests
        resolved_registry = (self.registry if registry == "default"
                             else registry)
        if projection is None:
            projection = _projection(
                statuses=statuses,
                result_snapshot=(snapshot.snapshot_id if result_snapshot is None
                                 else result_snapshot))
        return materialize_production_coverage(
            snapshot_id=snapshot.snapshot_id, manifest_directory=manifests,
            observation_directory=self.root / name / "observation_space",
            coverage_directory=self.root / name / "production_coverage",
            coverage_store_path=self.root / name / "research_coverage.json",
            canonical_question_projection=projection,
            evaluator_registry=resolved_registry, policy=policy,
            candle_authority=candle_authority,
            candle_authority_binding=candle_authority_binding,
            created_at=created_at,
            observed_at=observed_at or created_at)

    def orchestrate(self, result, *, capacity: int = 10, registry="default",
                    name: str = "q71"):
        return self.orchestrate_coverage(
            result.coverage, store_path=result.coverage_store.path,
            capacity=capacity, registry=registry, name=name)

    def orchestrate_coverage(self, coverage, *, store_path, capacity: int = 10,
                             registry="default", name: str = "q71"):
        resolved_registry = (self.registry if registry == "default"
                             else registry)
        store = ResearchCoverageStore(store_path)
        store.register(coverage.coverage_snapshot)
        return run_q71_orchestration(
            snapshot_id=coverage.observation_space_snapshot.frontier_snapshot_id,
            capacity=capacity, coverage_store=store,
            generated_store=GeneratedResearchStore(
                self.root / name / "generated.json"),
            agenda_store=ResearchAgendaStore(self.root / name / "agenda.json"),
            state_path=self.root / name / "q71_state.json",
            evaluator_registry=resolved_registry)

    def execute(self, result, *, registry="default", name: str = "q71",
                max_questions: int = 8):
        resolved_registry = (self.registry if registry == "default"
                             else registry)
        return run_generated_question_worker(
            snapshot_id=result.observation_space.frontier_snapshot_id,
            evaluator_registry=resolved_registry,
            generated_store=GeneratedResearchStore(
                self.root / name / "generated.json"),
            result_store=GeneratedQuestionResultStore(self.root / name / "results"),
            execution_store=GeneratedQuestionExecutionStore(
                self.root / name / "execution.json"),
            q71_state_path=self.root / name / "q71_state.json",
            manifest_directory=self.manifests, source=self.source,
            policy=GeneratedExecutionPolicy(max_questions_per_run=max_questions),
            recorded_at=CREATED_AT)

    def q71_state(self, name: str = "q71") -> dict:
        return json.loads(
            (self.root / name / "q71_state.json").read_text(encoding="utf-8"))

    def decisions(self, result) -> dict:
        return {item.cell_identity: item for item in result.coverage.decisions}

# ── 0. governed M5 candle authority (candidate-capability evidence) ─────────
def governed_candle_authority_for(
    harness: Harness, *, snapshot_id: str | None = None,
    frontier_start: str | None = None, frontier_end: str | None = None,
    close: float = 1.105,
):
    """Freeze a governed M5 candle authority for one harness frontier.

    The rows are governed ``events_v1`` CANDLE/mt5_data/M5 records for exactly
    the frontier's own instrument population and inside its own window.  Nothing
    is invented: this is the same contract the production producer enforces.
    """
    from datetime import datetime, timezone

    from research_engine.v10.investigation_snapshot import (
        open_investigation_snapshot,
    )

    snapshot = harness.snapshot
    start = str(frontier_start or snapshot.start_date)
    end = str(frontier_end or snapshot.end_date)
    base_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    reader = open_investigation_snapshot(
        snapshot.snapshot_id, source=harness.source,
        manifest_directory=harness.manifests)
    shadow = reader.cycle_cached_dataset("shadow_runtime")
    symbols = sorted({
        str(row.get("symbol")).upper() for row in shadow if row.get("symbol")})
    rows = []
    for symbol in symbols:
        for index in range(6):
            ts = base_ms + index * 300_000
            rows.append({
                "ts_utc_ms": ts, "type": "CANDLE", "symbol": symbol,
                "timeframe": "M5", "source": "mt5_data",
                "schema_version": "events_v1",
                "payload": {
                    "ts": ts, "o": 1.100, "h": 1.110, "l": 1.090,
                    "c": close, "v": 100.0,
                    "timestamp_normalization_version": (
                        "mt5_broker_to_utc_once_v1"),
                },
            })
    authority = freeze_governed_m5_candle_authority(
        candle_rows=rows, shadow_runtime_rows=shadow,
        snapshot_id=str(snapshot_id or snapshot.snapshot_id),
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_start=start, frontier_end=end,
        snapshot_authority=snapshot.source_authority,
        produced_at=CREATED_AT)
    return authority, bind_m5_candle_authority(authority, bound_at=CREATED_AT)


def candidate_capable_cell(harness: Harness, result):
    declarations = {item.evidence_class: item
                    for item in PRODUCTION_EVIDENCE_CLASSES}
    for cell in result.coverage.decisions:
        declaration = declarations.get(cell.evidence_class)
        if declaration is not None and declaration.candidate_capable:
            return cell
    raise AssertionError("no candidate-capable cell was materialized")


def test_candidate_capable_cell_fails_closed_without_candle_authority(tmp_path):
    """8/20. Without the governed candle authority the cell says exactly why."""
    harness = Harness(tmp_path)
    result = harness.materialize(name="no_candles")
    row = candidate_capable_cell(harness, result)
    assert row.candidate_capable is True
    assert row.candidate_evidence_ready is False
    assert MISSING_M5_CANDLE_AUTHORITY in row.fail_closed_reasons
    assert row.candle_authority_id is None
    surface = result.surface
    assert surface["m5_candle_authority"]["present"] is False
    assert surface["m5_candle_authority"]["fail_closed_reason"] == (
        MISSING_M5_CANDLE_AUTHORITY)
    assert surface["candidate_capable_cells_evidence_ready"] == []
    blocked = surface["candidate_capable_cells_blocked"]
    assert [item["cell_identity"] for item in blocked] == [row.cell_identity]
    # The Lab states the precise reason; it is never hidden behind WAITING.
    view = build_lab_view(_lab_projection(observation_coverage=surface))
    rendered = render_lab_terminal(view)
    assert "M5 candle authority: MISSING" in rendered
    assert MISSING_M5_CANDLE_AUTHORITY in rendered



# ── 1. deterministic observation-cell identity ──────────────────────────────
def test_observation_cell_identity_is_deterministic(tmp_path):
    harness = Harness(tmp_path)
    first = harness.materialize(name="one")
    second = harness.materialize(name="two")
    assert (first.observation_space_snapshot_id
            == second.observation_space_snapshot_id)
    assert (first.observation_space.observation_space.observation_space_identity
            == second.observation_space.observation_space.observation_space_identity)
    assert ([cell.cell_identity
             for cell in first.observation_space.observation_space.cells]
            == [cell.cell_identity
                for cell in second.observation_space.observation_space.cells])
    assert len(set(cell.cell_identity
                   for cell in first.observation_space.observation_space.cells)) == 7


def test_cell_identity_is_stable_across_evidence_epochs(tmp_path):
    """New evidence must never churn cell identity (that would duplicate Q71)."""
    harness = Harness(tmp_path)
    first = harness.materialize(name="epoch-1")
    other_snapshot, other_manifests, _ = harness.freeze(
        OTHER_R_MULTIPLES, name="evidence-2")
    second = harness.materialize(
        snapshot=other_snapshot, manifests=other_manifests, name="epoch-2")
    assert (first.observation_space.observation_space.observation_space_identity
            == second.observation_space.observation_space.observation_space_identity)
    assert ([cell.cell_identity
             for cell in first.observation_space.observation_space.cells]
            == [cell.cell_identity
                for cell in second.observation_space.observation_space.cells])
    assert (first.observation_space_snapshot_id
            != second.observation_space_snapshot_id)
    assert (first.coverage.production_coverage_snapshot_id
            != second.coverage.production_coverage_snapshot_id)


# ── 2. real dataset binding ─────────────────────────────────────────────────
def test_cells_bind_to_real_governed_datasets(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    bindings = {item.cell_identity: item
                for item in result.observation_space.bindings}
    real = {item.dataset: item for item in harness.snapshot.datasets}
    for binding in bindings.values():
        assert binding.evidence_snapshot_id == harness.snapshot.snapshot_id
        assert (binding.evidence_fingerprint
                == harness.snapshot.snapshot_fingerprint)
        assert binding.evidence_producer_identity
        for dataset, schema in binding.dataset_schema_versions:
            assert real[dataset].schema_version == schema
        for dataset, rows in binding.dataset_row_counts:
            assert rows == real[dataset].source_row_count
        for dataset, identity in binding.dataset_evidence_identities:
            assert identity == real[dataset].dataset_snapshot_id
    by_class = {item.evidence_class: item for item in bindings.values()}
    shadow = by_class["CURRENT_COMPLETED_SHADOW_LIFECYCLES"]
    assert shadow.governed_datasets == ("shadow_runtime",)
    assert shadow.required_fields == tuple(sorted(
        next(question.required_fields for question in REGISTRY
             if question.id == "E1")))
    assert shadow.observed is True
    assert shadow.evidence_volume > 0


# ── 3. unknown dataset binding fails closed ─────────────────────────────────
def test_dataset_outside_the_evidence_contract_fails_closed():
    restricted = production_observation_policy(governed_datasets=("trade_truth",))
    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        governed_observation_space(restricted)
    assert excinfo.value.reason_code == MISSING_DATASET_BINDING


def test_unbound_dataset_fails_closed(tmp_path):
    harness = Harness(tmp_path)
    space = governed_observation_space()
    truncated = SimpleNamespace(
        datasets=tuple(item for item in harness.snapshot.datasets
                       if item.dataset != "shadow_runtime"),
        source_authority=harness.snapshot.source_authority,
        snapshot_id=harness.snapshot.snapshot_id,
        snapshot_fingerprint=harness.snapshot.snapshot_fingerprint,
        evidence_epoch=harness.snapshot.evidence_epoch,
        start_date=harness.snapshot.start_date,
        end_date=harness.snapshot.end_date)
    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        bind_observation_cells(
            space, truncated, observation_space_snapshot_id="POS-" + "0" * 16)
    assert excinfo.value.reason_code == MISSING_DATASET_BINDING



# ── 4/5. observation + coverage snapshots are immutable ─────────────────────
def test_observation_space_snapshot_is_immutable(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    store = ObservationSpaceSnapshotStore(
        harness.root / "materialization" / "observation_space")
    loaded = store.load(result.observation_space_snapshot_id)
    assert loaded is not None
    assert loaded.to_dict() == result.observation_space.to_dict()
    # Re-registration of the identical artifact is a no-op.
    assert (store.register(result.observation_space).to_dict()
            == result.observation_space.to_dict())
    # A tampered artifact can never be silently trusted.
    path = store.path_for(result.observation_space_snapshot_id)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["cell_count"] = 999
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ProductionObservationSpaceIdentityConflict):
        store.load(result.observation_space_snapshot_id)


def test_coverage_snapshot_is_immutable(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    store = ProductionCoverageSnapshotStore(
        harness.root / "materialization" / "production_coverage")
    loaded = store.load(result.production_coverage_snapshot_id)
    assert loaded is not None
    assert loaded.to_dict() == result.coverage.to_dict()
    assert (production_coverage_snapshot_from_dict(
        result.coverage.to_dict()).to_dict() == result.coverage.to_dict())
    path = store.path_for(result.production_coverage_snapshot_id)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["cell_count"] = 0
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ProductionObservationSpaceIdentityConflict):
        store.load(result.production_coverage_snapshot_id)


# ── 6/7. conservation and no duplicate cells ────────────────────────────────
def test_coverage_conservation_holds_on_real_evidence(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    report = result.coverage.conservation
    assert report["conserved"] is True
    total = report["total_governed_observation_cells"]
    # Seven governed cells: E1, X3, the four canonical exit-path questions and
    # the candidate-capable governed counterfactual family on EX1.  The count is
    # pinned exactly so no catch-all cell can ever appear unnoticed.
    assert total == result.coverage.cell_count == 7
    assert sorted(item.evidence_class for item in result.coverage.decisions) == [
        "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "GOVERNED_EXIT_POLICY_COUNTERFACTUAL",
        "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",
    ]
    assert sum(report["governed_partition"].values()) == total
    assert report["axis_coverage_state"]["conserved"] is True
    assert report["axis_evaluator_capability"]["conserved"] is True
    assert report["axis_structural_family"]["conserved"] is True
    assert report["axis_blind_spot_source"]["conserved"] is True
    assert report["duplicate_cell_identities"] == []
    assert report["duplicate_bindings"] == []
    assert report["unbound_cells"] == []
    assert report["unexplained_cells"] == []


def test_conservation_is_recomputed_and_enforced(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    space = result.observation_space.observation_space
    broken = coverage_conservation(
        result.coverage.coverage_snapshot, result.observation_space.bindings,
        result.coverage.decisions[:-1],
        result.observation_space.family_decisions)
    assert broken["conserved"] is False
    assert broken["total_governed_observation_cells"] == len(space.cells)
    from research_engine.v10.continuous.production_coverage import (
        assert_coverage_conservation,
    )
    with pytest.raises(Exception, match="COVERAGE_CONSERVATION_VIOLATED"):
        assert_coverage_conservation(broken)


def test_duplicate_cell_declarations_fail_closed():
    """One cell per governed (evidence class x canonical question): no catch-all."""
    declarations = governed_observation_cell_declarations()
    materials = [tuple(sorted(cell.semantic_material().items()))
                 for cell in declarations]
    assert len(set(map(repr, materials))) == len(materials)
    expected = 0
    for declaration in PRODUCTION_EVIDENCE_CLASSES:
        expected += len(canonical_questions_for_evidence_class(declaration))
    assert len(declarations) == expected == 7
    space = governed_observation_space()
    assert len(space.cells) == expected



# ── 8/9. canonical question structural mapping (accounting only) ────────────
def test_canonical_question_mapping_is_structural(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    mapping = {item.question_id: item
               for item in result.coverage.canonical_question_mapping}
    decisions = result.coverage.decisions
    e1_cells = tuple(sorted(
        item.cell_identity for item in decisions
        if item.evidence_class == "CURRENT_COMPLETED_SHADOW_LIFECYCLES"))
    exit_cells = tuple(sorted(
        item.cell_identity for item in decisions
        if item.evidence_class == "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH"))
    assert mapping["E1"].covered_cell_ids == e1_cells
    assert len(e1_cells) == 1
    # The exit-path evidence class covers exactly the four canonical questions
    # its own declaration resolves to, one governed cell each.
    assert exit_cells == tuple(sorted(
        item.covered_cell_ids[0] for item in
        (mapping[question_id] for question_id in ("EX1", "EX2", "EX3", "EX4"))))
    for question_id in ("EX2", "EX3", "EX4"):
        assert mapping[question_id].evidence_classes == (
            "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",)
        assert len(mapping[question_id].covered_cell_ids) == 1
    # EX1 is covered by BOTH governed families: the descriptive exit-path cell
    # and the candidate-capable governed counterfactual cell.  Two evidence
    # classes on one canonical question is two governed observation cells, not a
    # duplicated cell.
    counterfactual_cells = tuple(sorted(
        item.cell_identity for item in decisions
        if item.evidence_class == "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"))
    assert len(counterfactual_cells) == 1
    assert mapping["EX1"].evidence_classes == (
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
        "GOVERNED_EXIT_POLICY_COUNTERFACTUAL",
    )
    assert mapping["EX1"].covered_cell_ids == tuple(sorted(
        (counterfactual_cells[0], exit_cells[0])))
    counterfactual = next(
        item for item in decisions
        if item.evidence_class == "GOVERNED_EXIT_POLICY_COUNTERFACTUAL")
    assert counterfactual.candidate_capable is True
    assert counterfactual.scientific_finding_capable is True
    assert counterfactual.evaluator_key == "q71.counterfactual.governed_exit_policy"
    assert mapping["X3"].evidence_classes == (
        "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",)
    assert mapping["E2"].covered_cell_ids == ()
    assert mapping["E2"].evidence_classes == ()


def test_canonical_question_order_and_identity_are_unchanged(tmp_path):
    before_ids = tuple(question.id for question in REGISTRY)
    before_baseline = tuple(BASELINE_QUESTION_IDS)
    harness = Harness(tmp_path)
    result = harness.materialize()
    assert tuple(item.question_id
                 for item in result.coverage.canonical_question_mapping) == \
        tuple(canonical_inventory()) == before_ids
    assert tuple(BASELINE_QUESTION_IDS) == before_baseline
    assert len(before_baseline) == 70
    assert tuple(question.id for question in REGISTRY) == before_ids


def test_canonical_question_mapping_never_mutates_canonical_identity(tmp_path):
    """Coverage accounting must not reorder, rename or re-register the 70."""
    before = [(question.id, question.to_dict()) for question in REGISTRY]
    harness = Harness(tmp_path)
    result = harness.materialize()
    mapping = result.coverage.canonical_question_mapping
    assert len(mapping) == len(before) == 70
    assert [item.question_id for item in mapping] == [item[0] for item in before]
    assert [(question.id, question.to_dict()) for question in REGISTRY] == before
    # A cell can never be mapped to a question outside the frozen inventory.
    inventory = set(canonical_inventory())
    for item in mapping:
        assert item.question_id in inventory
        for cell_id in item.covered_cell_ids:
            assert cell_id in {cell.cell_identity
                               for cell in result.observation_space
                               .observation_space.cells}
    # Coverage states are governed CoverageState values, never free text.
    governed = {state.value for state in CoverageState}
    for item in mapping:
        assert set(item.coverage_states).issubset(governed)


# ── 10. existing Q71 source-cell reconciliation ─────────────────────────────
def test_q71_questions_are_bound_to_their_source_cells(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    orchestration = harness.orchestrate(result, capacity=10)
    assert len(orchestration["new_question_ids"]) == 7
    state = harness.q71_state()
    cells = {cell.cell_identity
             for cell in result.observation_space.observation_space.cells}
    for question_id, row in state["question_states"].items():
        assert row["source_kind"] == "COVERAGE_GAP"
        assert row["source_cell_id"] in cells
        assert row["last_evaluated_snapshot"] == \
            result.coverage.coverage_snapshot.coverage_snapshot_identity
    mapping = q71_coverage_source_mapping(
        result.coverage, state,
        generated_store=GeneratedResearchStore(
            harness.root / "q71" / "generated.json"))
    assert mapping["generated_question_count"] == 7
    assert mapping["production_coverage_snapshot_id"] == \
        result.production_coverage_snapshot_id
    for entry in mapping["entries"].values():
        assert entry["source_observation_cell_id"] in cells
        assert entry["source_coverage_snapshot_id"]
        assert entry["coverage_reason"]
        assert entry["blind_spot_identity"]
        assert entry["evidence_class"]
        assert entry["supersession_key"] == \
            "coverage_cell:" + entry["source_observation_cell_id"]



def _space_with_absent_evidence(result, dataset: str = "shadow_runtime"):
    """The same governed space with one governed dataset declared empty.

    This reproduces the real ``dataset_absent_treatment`` state the policy
    declares (a governed contract dataset carrying no rows in this frontier).
    No cell, population or evidence class is invented.
    """
    artifact = result.observation_space
    bindings = tuple(
        replace(item, observed=False,
                dataset_row_counts=tuple(
                    (name, 0) for name, _ in item.dataset_row_counts))
        if dataset in item.governed_datasets else item
        for item in artifact.bindings)
    return ObservationSpaceSnapshot.construct(
        observation_space=artifact.observation_space, policy=artifact.policy,
        bindings=bindings, family_decisions=artifact.family_decisions,
        frontier_snapshot_id=artifact.frontier_snapshot_id,
        frontier_fingerprint=artifact.frontier_fingerprint,
        investigation_epoch=artifact.investigation_epoch,
        frontier_start=artifact.frontier_start, frontier_end=artifact.frontier_end,
        evaluator_registry_identity=artifact.evaluator_registry_identity,
        created_at=artifact.created_at)


# ── 11-13. blind spots ──────────────────────────────────────────────────────
def test_real_blind_spots_are_detected_from_real_evidence(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    decisions = harness.decisions(result)
    assert len(decisions) == 7
    for decision in decisions.values():
        assert decision.coverage_state == "OBSERVED_NOT_RESEARCHED"
        assert decision.blind_spot is True
        assert decision.blind_spot_class == "DATA_WITHOUT_RESEARCH"
        assert decision.blind_spot_identity
        assert decision.question_minted is True
        assert decision.q71_eligible is True
        assert decision.evidence_volume > 0
        assert decision.evidence_freshness == "CURRENT_FRONTIER"
        assert decision.coverage_snapshot_identity == \
            result.coverage.coverage_snapshot.coverage_snapshot_identity


def test_covered_cell_is_not_a_blind_spot(tmp_path):
    harness = Harness(tmp_path)
    statuses = {question_id: "COMPLETE"
                for question_id in ("E1", "X3", "EX1", "EX2", "EX3", "EX4")}
    result = harness.materialize(statuses=statuses)
    decisions = harness.decisions(result)
    for decision in decisions.values():
        assert decision.coverage_state == "OBSERVED_AND_RESEARCHED"
        assert decision.blind_spot is False
        assert decision.blind_spot_class is None
        assert decision.question_minted is False
        assert decision.expected_question_status == "RETIRED"
    orchestration = harness.orchestrate(result)
    assert orchestration["new_question_ids"] == []
    assert orchestration["generated_questions"] == []



def test_waiting_for_data_cell_is_not_a_duplicate_blind_spot(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    waiting_space = _space_with_absent_evidence(result)
    waiting = build_production_coverage(
        waiting_space,
        canonical_question_projection=_projection(
            result_snapshot=waiting_space.frontier_snapshot_id),
        evaluator_registry=harness.registry,
        created_at=CREATED_AT, observed_at=CREATED_AT)
    decisions = {item.cell_identity: item for item in waiting.decisions}
    shadow_classes = {"CURRENT_COMPLETED_SHADOW_LIFECYCLES",
                      "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
                      "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"}
    for decision in decisions.values():
        if decision.evidence_class in shadow_classes:
            assert decision.coverage_state == "QUESTION_EXISTS_NO_EVIDENCE"
            assert decision.blind_spot_class == "QUESTION_WITHOUT_EVIDENCE"
            assert decision.expected_question_status == "WAITING_FOR_DATA"
            assert "MISSING_EVIDENCE" in decision.fail_closed_reasons
        else:
            assert decision.coverage_state == "OBSERVED_NOT_RESEARCHED"
    first = harness.orchestrate_coverage(
        waiting, store_path=harness.root / "waiting" / "coverage.json",
        name="waiting")
    assert len(first["new_question_ids"]) == 7
    statuses = {row["generated_question_id"]: row["status"]
                for row in first["generated_questions"]}
    assert sorted(set(statuses.values())) == ["QUEUED", "WAITING_FOR_DATA"]
    assert sum(1 for value in statuses.values() if value == "WAITING_FOR_DATA") == 6
    queued = {row["generated_question_id"] for row in first["queue"]}
    assert len(queued) == 1
    for question_id, status in statuses.items():
        assert (status == "QUEUED") == (question_id in queued)
    # Only the observed cell's question is queued; the waiting cells never are.
    observed_cell = next(
        decision.cell_identity for decision in decisions.values()
        if decision.evidence_class not in shadow_classes)
    observed_question = next(
        row["generated_question_id"] for row in first["generated_questions"]
        if row["source_opportunity"].split(":", 1)[1] == observed_cell)
    assert queued == {observed_question}
    # A second identical run cannot mint a duplicate for the same waiting cell.
    second = harness.orchestrate_coverage(
        waiting, store_path=harness.root / "waiting" / "coverage.json",
        name="waiting")
    assert second["new_question_ids"] == []
    assert sorted(statuses) == sorted(
        row["generated_question_id"] for row in second["generated_questions"])


# ── 14-15. honest capability reporting ──────────────────────────────────────
def test_missing_evaluator_is_reported_honestly(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize(registry=EMPTY_REGISTRY, name="no-evaluator")
    decisions = harness.decisions(result)
    for decision in decisions.values():
        assert decision.evaluator_verdict == MISSING_EVALUATOR
        assert decision.evaluator_key is None
        assert MISSING_EVALUATOR in decision.fail_closed_reasons
    orchestration = harness.orchestrate(
        result, registry=EMPTY_REGISTRY, name="q71-no-evaluator")
    assert len(orchestration["new_question_ids"]) == 7
    assert orchestration["queue"] == []
    for row in orchestration["generated_questions"]:
        assert row["status"] == "MISSING_EVALUATOR"
        assert row["lifecycle_status"] == MISSING_EVALUATOR
    execution = harness.execute(
        result, registry=EMPTY_REGISTRY, name="q71-no-evaluator")
    # No evaluator means no result; the worker never invents one.
    assert execution["result_ids"] == []


def test_scientifically_unsupported_families_are_represented(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    families = {item.structural_family: item
                for item in result.observation_space.family_decisions}
    assert set(families) == set(structural_generated_question_families())
    unsupported = unsupported_evidence_class_catalogue()
    assert len(unsupported) == 3
    for token, entry in unsupported.items():
        assert families[token].materialized is False
        assert families[token].reason_code == str(entry["reason"])
        assert families[token].cell_identities == ()
        assert families[token].provenance
    # The governed exit-policy counterfactual family is a real supported family
    # with a real production evaluator and real candidate capability.  It is a
    # different structural token from D5's deliberately unsupported
    # COUNTERFACTUAL_SHADOW_SIMULATED_OUTCOME, which stays unsupported and stays
    # in the denominator.
    assert "GOVERNED_EXIT_POLICY_COUNTERFACTUAL" not in unsupported
    assert "COUNTERFACTUAL_SHADOW_SIMULATED_OUTCOME" in unsupported
    counterfactual = families["GOVERNED_EXIT_POLICY_COUNTERFACTUAL"]
    assert counterfactual.materialized is True
    assert counterfactual.reason_code is None
    assert counterfactual.evaluator_available is True
    assert counterfactual.cell_identities
    surface = result.surface
    assert {item["structural_family"] for item in
            surface["scientifically_unsupported_families"]} == set(unsupported)
    assert surface["conservation"]["axis_structural_family"]["conserved"] is True



# ── 16-17. bounded, non-duplicating generation ──────────────────────────────
def test_one_blind_spot_produces_exactly_one_generated_question(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    blind = [item for item in result.coverage.decisions if item.blind_spot]
    orchestration = harness.orchestrate(result, capacity=50)
    assert len(orchestration["new_question_ids"]) == len(blind) == 7
    assert len(set(orchestration["new_question_ids"])) == 7
    state = harness.q71_state()
    source_cells = [row["source_cell_id"] for row in state["question_states"].values()]
    assert sorted(source_cells) == sorted(item.cell_identity for item in blind)
    assert all(question_id.startswith("GEN-")
               for question_id in orchestration["new_question_ids"])
    for question_id in orchestration["new_question_ids"]:
        assert question_id not in BASELINE_QUESTION_IDS


def test_unchanged_snapshot_rerun_generates_no_duplicate(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    first = harness.orchestrate(result, capacity=50)
    assert len(first["new_question_ids"]) == 7
    second = harness.orchestrate(result, capacity=50)
    assert second["new_question_ids"] == []
    assert second["superseded_questions"] == []
    assert sorted(row["generated_question_id"]
                  for row in second["generated_questions"]) == \
        sorted(first["new_question_ids"])
    assert len(harness.q71_state()["question_states"]) == 7


# ── 18-19. changed evidence changes coverage and re-enters ──────────────────
def test_changed_evidence_changes_the_coverage_snapshot(tmp_path):
    harness = Harness(tmp_path)
    first = harness.materialize(name="changed-1")
    other_snapshot, other_manifests, _ = harness.freeze(
        OTHER_R_MULTIPLES, name="evidence-other")
    second = harness.materialize(
        snapshot=other_snapshot, manifests=other_manifests, name="changed-2")
    assert (first.coverage.coverage_snapshot.coverage_snapshot_identity
            != second.coverage.coverage_snapshot.coverage_snapshot_identity)
    assert (first.coverage.observation_space_snapshot.frontier_fingerprint
            != second.coverage.observation_space_snapshot.frontier_fingerprint)
    first_identities = {item.cell_identity: item.evidence_volume
                        for item in first.coverage.decisions}
    second_identities = {item.cell_identity: item.evidence_volume
                         for item in second.coverage.decisions}
    assert first_identities != second_identities


def test_changed_evidence_re_enters_the_same_question(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    waiting = build_production_coverage(
        _space_with_absent_evidence(result),
        canonical_question_projection=_projection(
            result_snapshot=result.observation_space.frontier_snapshot_id),
        evaluator_registry=harness.registry,
        created_at=CREATED_AT, observed_at="2026-10-08T01:00:00+00:00")
    store_path = harness.root / "reentry" / "coverage.json"
    first = harness.orchestrate_coverage(
        waiting, store_path=store_path, name="reentry")
    waiting_ids = {row["generated_question_id"] for row in first["generated_questions"]
                   if row["status"] == "WAITING_FOR_DATA"}
    assert len(waiting_ids) == 6

    arrived = build_production_coverage(
        result.observation_space,
        canonical_question_projection=_projection(
            result_snapshot=result.observation_space.frontier_snapshot_id),
        evaluator_registry=harness.registry,
        created_at=CREATED_AT, observed_at="2026-10-08T02:00:00+00:00")
    second = harness.orchestrate_coverage(
        arrived, store_path=store_path, name="reentry")
    assert second["new_question_ids"] == []
    by_id = {row["generated_question_id"]: row
             for row in second["generated_questions"]}
    for question_id in waiting_ids:
        row = by_id[question_id]
        assert row["status"] == "QUEUED"
        assert row["reentry_count"] == 1
        assert row["reentry_reason"] == "MISSING_EVIDENCE_ARRIVED"
        assert row["reentry_authorized"] is True



# ── 20. evaluator arrival re-enters a MISSING_EVALUATOR question ────────────
def test_evaluator_arrival_re_enters_the_same_question(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize(registry=EMPTY_REGISTRY, name="arrival")
    store_path = harness.root / "arrival" / "coverage.json"
    first = harness.orchestrate_coverage(
        result.coverage, store_path=store_path, registry=EMPTY_REGISTRY,
        name="arrival")
    blocked = {row["generated_question_id"] for row in first["generated_questions"]
               if row["status"] == MISSING_EVALUATOR}
    assert len(blocked) == 7

    second = harness.orchestrate_coverage(
        result.coverage, store_path=store_path, registry=harness.registry,
        name="arrival")
    assert second["new_question_ids"] == []
    by_id = {row["generated_question_id"]: row
             for row in second["generated_questions"]}
    for question_id in blocked:
        row = by_id[question_id]
        assert row["status"] == "QUEUED"
        assert row["reentry_count"] == 1
        assert row["reentry_reason"] == "EVALUATOR_CAPABILITY_APPEARED"
        assert row["evaluator"] is not None

    # The now-executable questions really execute through the production worker.
    execution = harness.execute(result, registry=harness.registry, name="arrival")
    assert execution["result_ids"]


# ── 21-22. supersession and retirement never regenerate ─────────────────────
def _coverage_proposals(coverage):
    from research_engine.lifecycle.research_coverage import (
        admit_coverage_curiosity,
    )
    from research_engine.v10.continuous.q71_orchestration import (
        _coverage_proposal,
    )
    cells = {cell.cell_identity: cell
             for cell in coverage.observation_space_snapshot
             .observation_space.cells}
    return {
        signal.target_ref: _coverage_proposal(signal, cells[signal.target_ref])
        for signal in admit_coverage_curiosity(coverage.coverage_snapshot)}


def test_superseded_question_is_not_regenerated(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    store_path = harness.root / "supersede" / "coverage.json"
    first = harness.orchestrate_coverage(
        result.coverage, store_path=store_path, name="supersede")
    assert len(first["new_question_ids"]) == 7

    proposals = _coverage_proposals(result.coverage)
    target_cell = sorted(proposals)[0]
    base = proposals[target_cell]
    specification = dict(base.specification)
    specification["generation_version"] = 2
    newer = GeneratedResearchProposal(
        research_kind=base.research_kind, trigger_ref=base.trigger_ref,
        target_kind=base.target_kind, target_ref=base.target_ref,
        specification=specification, dimension_ref=base.dimension_ref,
        parent_refs=base.parent_refs)
    generated = GeneratedResearchStore(
        harness.root / "supersede" / "generated.json")
    older_id = next(record.generated_research_id for record in generated.all()
                    if record.target_ref == target_cell)
    newer_record = generated.register(newer)
    assert newer_record.generated_research_id != older_id

    second = harness.orchestrate_coverage(
        result.coverage, store_path=store_path, name="supersede")
    assert second["new_question_ids"] == []
    superseded = {item["generated_question_id"]: item["superseded_by"]
                  for item in second["superseded_questions"]}
    assert superseded.get(older_id) == newer_record.generated_research_id
    state = harness.q71_state("supersede")
    assert state["question_states"][older_id]["status"] == "SUPERSEDED"
    assert state["question_states"][older_id]["supersession_key"] == \
        "coverage_cell:" + target_cell
    assert state["question_states"][newer_record.generated_research_id][
        "status"] != "SUPERSEDED"

    third = harness.orchestrate_coverage(
        result.coverage, store_path=store_path, name="supersede")
    assert third["new_question_ids"] == []
    # Supersession is idempotent: the same winner, and no duplicated history.
    state = harness.q71_state("supersede")
    assert state["question_states"][older_id]["status"] == "SUPERSEDED"
    assert state["question_states"][older_id]["superseded_by"] == \
        newer_record.generated_research_id
    assert len(state["question_states"][older_id]["supersession_history"]) == 1
    assert set(third["superseded_questions"][index]["generated_question_id"]
               for index in range(len(third["superseded_questions"]))) <= {older_id}
    assert older_id not in {
        row["generated_question_id"] for row in third["queue"]}



def test_resolved_cell_retires_and_is_never_regenerated(tmp_path):
    harness = Harness(tmp_path)
    blind = harness.materialize(name="retire")
    store_path = harness.root / "retire" / "coverage.json"
    first = harness.orchestrate_coverage(
        blind.coverage, store_path=store_path, name="retire")
    assert len(first["new_question_ids"]) == 7

    statuses = {question_id: "COMPLETE"
                for question_id in ("E1", "X3", "EX1", "EX2", "EX3", "EX4")}
    resolved = build_production_coverage(
        blind.observation_space,
        canonical_question_projection=_projection(
            statuses=statuses,
            result_snapshot=blind.observation_space.frontier_snapshot_id),
        evaluator_registry=harness.registry, created_at=CREATED_AT,
        observed_at="2026-10-08T05:00:00+00:00")
    second = harness.orchestrate_coverage(
        resolved, store_path=store_path, name="retire")
    assert second["new_question_ids"] == []
    assert sorted(second["retired_question_ids"]) == sorted(first["new_question_ids"])
    assert {row["status"] for row in second["generated_questions"]} == {"RETIRED"}
    assert second["queue"] == []

    third = harness.orchestrate_coverage(
        resolved, store_path=store_path, name="retire")
    assert third["new_question_ids"] == []
    assert third["retired_question_ids"] == []
    assert len(harness.q71_state("retire")["question_states"]) == 7


def test_excluded_cell_is_not_materialized_and_not_regenerated(tmp_path):
    harness = Harness(tmp_path)
    policy = production_observation_policy(exclusions=(
        "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",))
    result = harness.materialize(policy=policy, name="excluded")
    excluded_cells = [item for item in result.coverage.decisions
                      if item.evidence_class
                      == "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH"]
    assert excluded_cells == []
    families = {item.structural_family: item
                for item in result.observation_space.family_decisions}
    assert families["CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH"].materialized is False
    assert (families["CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH"].reason_code
            == "EXCLUDED_BY_POLICY")
    orchestration = harness.orchestrate(result, capacity=50, name="excluded")
    assert len(orchestration["new_question_ids"]) == 3
    state = harness.q71_state("excluded")
    assert {row["evidence_requirements"]["evidence_class"]
            for row in state["question_states"].values()} == {
        "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
        "GOVERNED_EXIT_POLICY_COUNTERFACTUAL",
        "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE"}


# ── 23-25. fail-closed semantics ────────────────────────────────────────────
def test_unknown_evidence_class_fails_closed():
    policy = production_observation_policy(
        allowed_evidence_classes=("CURRENT_COMPLETED_SHADOW_LIFECYCLES",))
    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        governed_observation_space(policy)
    assert excinfo.value.reason_code == UNKNOWN_EVIDENCE_CLASS


def test_invalid_schema_fails_closed(tmp_path):
    harness = Harness(tmp_path)
    space = governed_observation_space()
    drifted = SimpleNamespace(
        datasets=tuple(
            replace(item, schema_version="shadow_runtime_v0")
            if item.dataset == "shadow_runtime" else item
            for item in harness.snapshot.datasets),
        source_authority=harness.snapshot.source_authority,
        snapshot_id=harness.snapshot.snapshot_id,
        snapshot_fingerprint=harness.snapshot.snapshot_fingerprint,
        evidence_epoch=harness.snapshot.evidence_epoch,
        start_date=harness.snapshot.start_date,
        end_date=harness.snapshot.end_date)
    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        bind_observation_cells(
            space, drifted, observation_space_snapshot_id="POS-" + "0" * 16)
    assert excinfo.value.reason_code == INVALID_SCHEMA


def test_missing_and_stale_frontier_fail_closed(tmp_path):
    harness = Harness(tmp_path)
    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        materialize_observation_space(
            snapshot_id="ISNAP-00000000000000000000",
            manifest_directory=harness.manifests,
            store=ObservationSpaceSnapshotStore(harness.root / "missing"))
    assert excinfo.value.reason_code == MISSING_EVIDENCE

    with pytest.raises(ProductionObservationSpaceNotMaterializable) as excinfo:
        materialize_observation_space(
            snapshot_id="FRONTIER-NOT-AN-ISNAP",
            manifest_directory=harness.manifests,
            store=ObservationSpaceSnapshotStore(harness.root / "missing"))
    assert excinfo.value.reason_code == MISSING_EVIDENCE

    # Research published against a different evidence frontier does not cover
    # the current one: the cell is reported STALE_FRONTIER, not resolved.
    stale = harness.materialize(
        statuses={"E1": "COMPLETE", "X3": "COMPLETE", "EX1": "COMPLETE",
                  "EX2": "COMPLETE", "EX3": "COMPLETE", "EX4": "COMPLETE"},
        result_snapshot="ISNAP-FFFFFFFFFFFFFFFFFFFF",
        name="stale")
    for decision in stale.coverage.decisions:
        assert decision.coverage_state == "OBSERVED_NOT_RESEARCHED"
        assert decision.blind_spot is True
        assert STALE_FRONTIER in decision.fail_closed_reasons



# ── 26-29. snapshot persistence and bound identities ────────────────────────
def test_source_cell_and_coverage_snapshot_are_persisted_on_the_question(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    orchestration = harness.orchestrate(result)
    state = harness.q71_state()
    for row in state["question_states"].values():
        assert row["source_cell_id"]
        assert row["last_evaluated_snapshot"] == \
            result.coverage.coverage_snapshot.coverage_snapshot_identity
        assert row["evidence_requirements"]["population_identity"] == \
            row["evidence_requirements"]["evidence_class"]
    for row in orchestration["generated_questions"]:
        assert row["source_lineage"] == []
        assert row["source_opportunity"].startswith("coverage_cell:")


def test_coverage_snapshot_is_persisted_and_registered(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    snapshot_path = (harness.root / "materialization" / "production_coverage"
                     / "snapshots"
                     / (result.production_coverage_snapshot_id + ".json"))
    assert snapshot_path.exists()
    pointer = json.loads(
        (harness.root / "materialization" / "production_coverage"
         / "latest_coverage.json").read_text(encoding="utf-8"))
    assert pointer["production_coverage_snapshot_id"] == \
        result.production_coverage_snapshot_id
    assert pointer["cell_count"] == 7
    # The governed store the orchestration reads carries the same snapshot.
    registered = result.coverage_store.get(
        result.coverage.coverage_snapshot.coverage_snapshot_identity)
    assert registered is not None
    assert registered.semantic_identity == \
        result.coverage.coverage_snapshot.semantic_identity
    store = ProductionCoverageSnapshotStore(
        harness.root / "materialization" / "production_coverage")
    assert store.snapshot_ids() == (result.production_coverage_snapshot_id,)


def test_evaluator_registry_identity_is_bound_to_the_snapshot(tmp_path):
    harness = Harness(tmp_path)
    first = harness.materialize(name="registry-1")
    assert (first.observation_space.evaluator_registry_identity
            == harness.registry.registry_identity)
    assert (first.coverage.evaluator_registry_identity
            == harness.registry.registry_identity)
    empty = GeneratedQuestionEvaluatorRegistry(registrations=())
    second = harness.materialize(registry=empty, name="registry-2")
    assert (second.observation_space.evaluator_registry_identity
            != first.observation_space.evaluator_registry_identity)
    assert (second.observation_space_snapshot_id
            != first.observation_space_snapshot_id)
    assert (second.coverage.production_coverage_snapshot_id
            != first.coverage.production_coverage_snapshot_id)


def test_evidence_catalogue_identity_is_bound_to_the_snapshot(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    assert result.observation_space.evidence_catalogue_identity == \
        evidence_class_identity()
    assert result.coverage.evidence_catalogue_identity == \
        evidence_class_identity()
    assert result.coverage.canonical_question_mapping_version == \
        "canonical_question_coverage_mapping_v1"
    assert result.coverage.q71_mapping_version == \
        "q71_coverage_source_mapping_v1"
    assert result.observation_space.producer_version
    assert result.observation_space.canonical_question_mapping_version == \
        "canonical_question_coverage_mapping_v1"



# ── 30-31. Research Lab truth ───────────────────────────────────────────────
def _lab_projection(*, observation_coverage=None) -> dict:
    rows = [{"question_id": question_id, "work_state": "FRESH",
             "execution_freshness": "CURRENT",
             "result": {"status": "COMPLETE"}}
            for question_id in BASELINE_QUESTION_IDS]
    material = {
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-LAB",
        "continuous_cycle_id": "CRCYCLE-LAB",
        "canonical_question_cycle_id": "QCYCLE-LAB",
        "data_frontier": {"snapshot_id": "ISNAP-LAB", "fingerprint": "FP",
                          "investigation_epoch": "EPOCH-LAB",
                          "frontier_start": "2026-09-25",
                          "frontier_end": "2026-10-08",
                          "changed_datasets": [],
                          "last_successful_research_cycle": "CRCYCLE-LAB"},
        "canonical_questions": rows,
        "generated_questions": [],
        "findings": [], "hypotheses": [], "candidates": [],
        "investigations_and_work_queues": {},
        "research_lag": {"pending_deep_jobs": 0, "running_deep_jobs": 0,
                         "lag_epochs": 0, "lag_seconds": 0.0},
        "what_changed": {},
        "predecessor_projection_version": None,
    }
    if observation_coverage is not None:
        material["observation_coverage"] = observation_coverage
    return material


def test_lab_reports_no_production_observation_space_truthfully(tmp_path):
    assert coverage_surface(None)["status"] == NO_PRODUCTION_OBSERVATION_SPACE
    assert coverage_surface(None)["cell_count"] == 0
    assert coverage_surface(None)["conserved"] is False
    view = build_lab_view(_lab_projection())
    surface = view["observation_coverage"]
    assert surface["status"] == NO_PRODUCTION_OBSERVATION_SPACE
    assert surface["observation_space_snapshot_id"] is None
    assert surface["production_coverage_snapshot_id"] is None
    assert surface["cell_count"] == 0
    assert surface["conserved"] is False
    assert NO_PRODUCTION_OBSERVATION_SPACE in render_lab_terminal(view)


def test_lab_reports_real_coverage_truthfully(tmp_path):
    harness = Harness(tmp_path)
    result = harness.materialize()
    orchestration = harness.orchestrate(result)
    surface = coverage_surface(result.coverage)
    view = build_lab_view(_lab_projection(observation_coverage=surface))
    reported = view["observation_coverage"]
    assert reported["status"] == "MATERIALIZED"
    assert reported["observation_space_snapshot_id"] == \
        result.observation_space_snapshot_id
    assert reported["production_coverage_snapshot_id"] == \
        result.production_coverage_snapshot_id
    assert reported["total_governed_observation_cells"] == 7
    assert reported["cell_count"] == 7
    assert reported["blind_spot_count"] == 7
    assert reported["conserved"] is True
    assert len(reported["blind_spots"]) == 7
    for row in reported["blind_spots"]:
        assert row["cell_identity"]
        assert row["blind_spot_class"] == "DATA_WITHOUT_RESEARCH"
        assert row["blind_spot_identity"]
        assert row["coverage_reason"] == "DATA_WITHOUT_RESEARCH"
        assert row["q71_eligible"] is True
    rendered = render_lab_terminal(view)
    assert result.observation_space_snapshot_id in rendered
    assert result.production_coverage_snapshot_id in rendered
    assert orchestration["new_question_ids"]



# ── 32-33. the continuous loop runs coverage before Q71 generation ──────────
def _loop_fixtures(tmp_path: Path, *, snapshot_id: str):
    state = tmp_path / "questions"
    QuestionCycleStore(state).save_projection({
        "cycle_schema": "canonical_question_cycle_v1",
        "cycle_id": "QCYCLE-LOOP", **_projection(),
    })
    question = SimpleNamespace(
        cycle_id="QCYCLE-LOOP", total_questions=70, cycle_status="COMPLETED",
        completed_at="2026-10-08T00:00:00Z")
    frontier = SimpleNamespace(
        status="NO_NEW_GOVERNED_EVIDENCE", verification_status="NOT_REQUIRED",
        snapshot_id=snapshot_id, fingerprint="FP", investigation_epoch="EPOCH",
        frontier_id="FR1", frontier_start="2026-09-25",
        frontier_end="2026-10-08", predecessor_snapshot_id=None,
        changed_datasets=(), stale_datasets=(), missing_optional_datasets=())
    bridge = SimpleNamespace(
        bridge_run_id="BR1", status="NO_SCIENTIFIC_STATE_CHANGE",
        validation_handoff=(), question_changes_processed=(),
        findings_created=(), findings_weakened=(), hypotheses_created=(),
        hypotheses_invalidated=(), candidates_created=(), review_required=())
    return state, question, frontier, bridge


def test_continuous_loop_invokes_coverage_before_q71_generation(tmp_path):
    from research_engine.v10.continuous.research_loop import (
        run_continuous_research_cycle,
    )
    harness = Harness(tmp_path)
    state, question, frontier, bridge = _loop_fixtures(
        tmp_path, snapshot_id=harness.snapshot.snapshot_id)
    order: list = []
    q71_kwargs: dict = {}

    def materializer(**kwargs):
        order.append(("observation_space", kwargs["snapshot_id"]))
        return materialize_production_coverage(**kwargs)

    def q71_runner(**kwargs):
        order.append(("q71", kwargs.get("snapshot_id")))
        q71_kwargs.update(kwargs)
        return run_q71_orchestration(**kwargs)

    result = run_continuous_research_cycle(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: frontier,
        question_runner=lambda value, **_: question,
        bridge_runner=lambda value, **_: bridge,
        q71_runner=q71_runner,
        question_kwargs={"state_directory": state},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": tmp_path / "registry"},
        q71_kwargs={"generated_store": GeneratedResearchStore(
                        tmp_path / "generated.json"),
                    "agenda_store": ResearchAgendaStore(
                        tmp_path / "agenda.json")},
        q71_capacity=1,
        observation_space_materializer=materializer,
        observation_space_manifest_directory=harness.manifests,
        observation_space_directory=tmp_path / "observation_space",
        production_coverage_directory=tmp_path / "production_coverage",
        production_coverage_store_path=tmp_path / "research_coverage.json",
        max_validation_jobs=0)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    assert result.stage_statuses["OBSERVATION_SPACE"] == "COMPLETED"
    assert order[0] == ("observation_space", harness.snapshot.snapshot_id)
    assert order[1] == ("q71", harness.snapshot.snapshot_id)
    # Q71 generation received exactly the materialized coverage authority.
    assert q71_kwargs["coverage_store"] is not None
    registered = q71_kwargs["coverage_store"].snapshots()
    assert len(registered) == 1
    assert registered[0].observation_space.observation_space_identity == \
        governed_observation_space().observation_space_identity
    # Bounded: capacity 1 can mint at most one question identity.
    q71_state = json.loads(
        (tmp_path / "continuous" / "q71_state.json").read_text(encoding="utf-8"))
    assert len(q71_state["question_states"]) == 1
    assert q71_state["question_states"][
        next(iter(q71_state["question_states"]))]["source_cell_id"]
    # The projection carries the materialized coverage surface truthfully.
    projection = json.loads(
        (tmp_path / "continuous" / "projection" / "latest.json")
        .read_text(encoding="utf-8"))
    surface = projection["observation_coverage"]
    assert surface["status"] == "MATERIALIZED"
    assert surface["cell_count"] == 7
    assert surface["conserved"] is True



def test_continuous_loop_fails_closed_without_a_governed_snapshot(tmp_path):
    from research_engine.v10.continuous.research_loop import (
        run_continuous_research_cycle,
    )
    state, question, frontier, bridge = _loop_fixtures(
        tmp_path, snapshot_id="S-NOT-A-GOVERNED-SNAPSHOT")
    result = run_continuous_research_cycle(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: frontier,
        question_runner=lambda value, **_: question,
        bridge_runner=lambda value, **_: bridge,
        question_kwargs={"state_directory": state},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": tmp_path / "registry"},
        q71_kwargs={"generated_store": GeneratedResearchStore(
                        tmp_path / "generated.json"),
                    "agenda_store": ResearchAgendaStore(
                        tmp_path / "agenda.json")},
        observation_space_directory=tmp_path / "observation_space",
        production_coverage_directory=tmp_path / "production_coverage",
        production_coverage_store_path=tmp_path / "research_coverage.json",
        max_validation_jobs=0)
    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    assert result.stage_statuses["OBSERVATION_SPACE"] == "FAILED_CLOSED"
    # Fail closed: no question identity is minted from an unmaterialized space.
    q71_state = json.loads(
        (tmp_path / "continuous" / "q71_state.json").read_text(encoding="utf-8"))
    assert q71_state["question_states"] == {}
    assert q71_state["signals"] == {}
    projection = json.loads(
        (tmp_path / "continuous" / "projection" / "latest.json")
        .read_text(encoding="utf-8"))
    assert projection["observation_coverage"]["status"] == \
        NO_PRODUCTION_OBSERVATION_SPACE
    assert result.stage_statuses["Q71_PLUS"] == "COMPLETED"


# ── 34-35. no generic/fuzzy generation; no live-trading mutation ────────────
def _module_source(name: str) -> str:
    import research_engine.v10.continuous as package
    return (Path(package.__file__).parent / name).read_text(
        encoding="utf-8", errors="ignore")


def _module_identifiers(name: str) -> set:
    """Every identifier, import and attribute name in one module's code."""
    import ast
    import research_engine.v10.continuous as package
    path = Path(package.__file__).parent / name
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            names.add(str(node.module or "").split(".")[0])
            names.add(str(node.module or "").split(".")[-1])
            for alias in node.names:
                names.add(alias.name)
    return names


def test_no_generic_or_fuzzy_question_generation():
    banned = {"difflib", "SequenceMatcher", "fuzzywuzzy", "rapidfuzz",
              "levenshtein", "openai", "anthropic", "embedding", "SentenceTransformer"}
    for name in ("production_observation_space.py", "production_coverage.py"):
        names = _module_identifiers(name)
        assert not (names & banned), (name, sorted(names & banned))
    # Generation is driven by governed coverage admission only: one signal per
    # governed blind spot, resolved by structural identity.
    from research_engine.lifecycle.research_coverage import (
        admit_coverage_curiosity,
    )
    assert "admit_coverage_curiosity" in _module_identifiers(
        "production_coverage.py")
    declarations = governed_observation_cell_declarations()
    assert len(declarations) == 7
    for declaration in declarations:
        assert declaration.subject_kind == "CANONICAL_QUESTION"
        assert declaration.subject_identity in canonical_inventory()
        assert declaration.horizon == "UNKNOWN"
        assert declaration.dimension_identities == ()
        assert declaration.interaction_identity == ""
        assert declaration.slice_identity == ""
    assert callable(admit_coverage_curiosity)


def test_no_live_trading_mutation_from_the_observation_stage(tmp_path):
    banned = {"MetaTrader5", "mt5", "order_send", "place_order",
              "live_approved", "position_sizing", "PERMITTED_HORIZONS"}
    for name in ("production_observation_space.py", "production_coverage.py"):
        names = _module_identifiers(name)
        assert not (names & banned), (name, sorted(names & banned))
    loop_source = _module_source("research_loop.py")
    assert "materialize_production_coverage" in loop_source
    # The observation stage never writes scientific state, candidates or the
    # canonical question cycle.
    stage = loop_source.split(
        'progress("enter_stage", "OBSERVATION_SPACE")')[1].split(
        'progress("enter_stage", "Q71_PLUS")')[0]
    for forbidden in ("OptimisationRegistry", "add_candidate(", "add_hypothesis(",
                      "run_scientific_state_bridge", "question_runner(",
                      "run_canonical_question_cycle", "live_approved"):
        assert forbidden not in stage, forbidden
    harness = Harness(tmp_path)
    result = harness.materialize()
    assert result.coverage.conservation["conserved"] is True
    # No candidate, hypothesis or finding authority is reachable from the
    # materialization path.
    surface = result.surface
    assert "candidates" not in surface
    assert "hypotheses" not in surface
    assert "findings" not in surface

EMPTY_REGISTRY = GeneratedQuestionEvaluatorRegistry(registrations=())


def test_candidate_capable_cell_becomes_evidence_ready_with_candles(tmp_path):
    """21/22/23. A bound governed candle authority makes the cell evidence-ready."""
    harness = Harness(tmp_path)
    authority, binding = governed_candle_authority_for(harness)
    result = harness.materialize(
        name="with_candles", candle_authority=authority,
        candle_authority_binding=binding)
    row = candidate_capable_cell(harness, result)
    assert row.candidate_capable is True
    assert row.candidate_evidence_ready is True
    assert MISSING_M5_CANDLE_AUTHORITY not in row.fail_closed_reasons
    assert row.candle_authority_id == authority.authority_id
    assert row.candle_authority_digest == authority.content_digest
    surface = result.surface
    assert surface["m5_candle_authority"]["present"] is True
    assert surface["m5_candle_authority"]["authority_identity"] == (
        M5_CANDLE_AUTHORITY_IDENTITY)
    assert surface["m5_candle_authority"]["authority_id"] == (
        authority.authority_id)
    assert surface["m5_candle_authority"]["content_digest"] == (
        authority.content_digest)
    assert surface["candidate_capable_cells_evidence_ready"] == [
        row.cell_identity]
    assert surface["candidate_capable_cells_blocked"] == []
    # Coverage conservation remains valid on every axis.
    assert surface["conserved"] is True
    view = build_lab_view(_lab_projection(observation_coverage=surface))
    rendered = render_lab_terminal(view)
    assert "M5 candle authority: " + authority.authority_id in rendered
    assert "Candidate-capable cells: evidence_ready=1  blocked=0" in rendered


def test_stale_candle_authority_is_refused_for_this_frontier(tmp_path):
    """14/15. A candle authority from another frontier is refused, not reused."""
    harness = Harness(tmp_path)
    authority, binding = governed_candle_authority_for(
        harness, snapshot_id="ISNAP-OTHERFRONTIER000000000002")
    result = harness.materialize(
        name="stale_candles", candle_authority=authority,
        candle_authority_binding=binding)
    row = candidate_capable_cell(harness, result)
    assert row.candidate_evidence_ready is False
    assert STALE_FRONTIER in row.fail_closed_reasons
    assert result.surface["m5_candle_authority"]["present"] is False
    assert result.surface["conserved"] is True


def test_candle_authority_without_membership_is_refused(tmp_path):
    """A governed authority is admitted only through its pinned membership."""
    harness = Harness(tmp_path)
    authority, _ = governed_candle_authority_for(harness)
    result = harness.materialize(
        name="unbound_candles", candle_authority=authority)
    row = candidate_capable_cell(harness, result)
    assert row.candidate_evidence_ready is False
    assert MISSING_M5_CANDLE_AUTHORITY in row.fail_closed_reasons
    assert result.surface["m5_candle_authority"]["present"] is False



# ── The loop admits candle authority and freezes counterfactual evidence ────
def _events_objects_for(harness: Harness) -> dict:
    """Governed ``events_v1`` M5 CANDLE objects for the harness frontier."""
    from datetime import datetime, timezone

    from core.production_data_contract import s3_base_prefix
    from research_engine.v10.investigation_snapshot import (
        open_investigation_snapshot,
    )

    snapshot = harness.snapshot
    base_ms = int(datetime.strptime(snapshot.start_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    reader = open_investigation_snapshot(
        snapshot.snapshot_id, source=harness.source,
        manifest_directory=harness.manifests)
    shadow = reader.cycle_cached_dataset("shadow_runtime")
    symbols = sorted({
        str(row.get("symbol")).upper() for row in shadow if row.get("symbol")})
    objects: dict = {}
    for symbol in symbols:
        rows = []
        for index in range(8):
            ts = base_ms + index * 300_000
            rows.append({
                "ts_utc_ms": ts, "type": "CANDLE", "symbol": symbol,
                "timeframe": "M5", "source": "mt5_data",
                "schema_version": "events_v1",
                "payload": {
                    "ts": ts, "o": 1.100, "h": 1.110, "l": 1.090,
                    "c": 1.105, "v": 100.0,
                    "timestamp_normalization_version": (
                        "mt5_broker_to_utc_once_v1"),
                },
            })
        key = (
            f"{s3_base_prefix('events')}/schema_version=events_v1"
            f"/symbol={symbol}/date={snapshot.start_date}/part-000.jsonl")
        objects[key] = "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in rows)
    return objects




def _candle_loop_kwargs(tmp_path, harness, state, question, frontier, bridge):
    return dict(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: frontier,
        question_runner=lambda value, **_: question,
        bridge_runner=lambda value, **_: bridge,
        question_kwargs={"state_directory": state},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": tmp_path / "registry"},
        q71_kwargs={"generated_store": GeneratedResearchStore(
                        tmp_path / "generated.json"),
                    "agenda_store": ResearchAgendaStore(
                        tmp_path / "agenda.json")},
        q71_capacity=1,
        q71_evaluator_registry=harness.registry,
        q71_execution_policy=GeneratedExecutionPolicy(max_questions_per_run=1),
        observation_space_manifest_directory=harness.manifests,
        observation_space_directory=tmp_path / "observation_space",
        production_coverage_directory=tmp_path / "production_coverage",
        production_coverage_store_path=tmp_path / "research_coverage.json",
        q71_evidence_source=harness.source,
        candle_authority_directory=tmp_path / "candle_authority",
        counterfactual_evidence_directory=(
            tmp_path / "counterfactual_evidence"),
        max_validation_jobs=0)


def test_continuous_loop_admits_candle_authority_for_the_frontier(tmp_path):
    """A/B/C/D/E/12. The real loop admits candle authority and freezes evidence."""
    from research_engine.v10.continuous.research_loop import (
        run_continuous_research_cycle,
    )
    from research_engine.v10.continuous.research_projection import (
        ResearchProjectionStore,
    )

    harness = Harness(tmp_path)
    harness.client.objects.update(_events_objects_for(harness))
    state, question, frontier, bridge = _loop_fixtures(
        tmp_path, snapshot_id=harness.snapshot.snapshot_id)
    result = run_continuous_research_cycle(
        **_candle_loop_kwargs(
            tmp_path, harness, state, question, frontier, bridge))

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    assert result.stage_statuses["OBSERVATION_SPACE"] == "COMPLETED"
    projection = ResearchProjectionStore(
        tmp_path / "continuous" / "projection").load_latest()
    coverage = projection["observation_coverage"]
    candle = coverage["m5_candle_authority"]
    assert candle["present"] is True
    assert candle["authority_identity"] == M5_CANDLE_AUTHORITY_IDENTITY
    assert candle["authority_id"]
    assert candle["content_digest"]
    assert candle["bar_count"] > 0
    assert candle["snapshot_id"] == harness.snapshot.snapshot_id
    assert coverage["candidate_capable_cells_evidence_ready"]
    assert coverage["candidate_capable_cells_blocked"] == []
    # The authority is immutably registered against this exact frontier.
    store = M5CandleAuthorityStore(tmp_path / "candle_authority")
    pinned = store.for_snapshot(harness.snapshot.snapshot_id)
    assert pinned is not None
    assert pinned[0].authority_id == candle["authority_id"]
    assert pinned[1].content_digest == candle["content_digest"]
    # The governed counterfactual evidence was frozen upstream of the worker.
    counterfactual = coverage["counterfactual_evidence"]
    assert counterfactual["present"] is True
    assert counterfactual["dataset_id"].startswith("CFE-")
    assert counterfactual["candle_authority_id"] == candle["authority_id"]
    assert counterfactual["content_digest"]
    # Nothing was fabricated: the fixture's candle window does not cover its
    # shadow lifecycles, so the artifact is legitimately non-analysable.
    assert counterfactual["scientifically_analysable"] is False
    assert "MISSING_COUNTERFACTUAL_EVIDENCE" in counterfactual["reason_codes"]
    assert "MISSING_M5_CANDLE_AUTHORITY" not in counterfactual["reason_codes"]
    # Re-entry is deterministic: a second cycle reuses the same authority.
    second = run_continuous_research_cycle(
        **_candle_loop_kwargs(
            tmp_path, harness, state, question, frontier, bridge))
    assert second.cycle_outcome in {"COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"}
    assert store.authority_ids() == (candle["authority_id"],)
