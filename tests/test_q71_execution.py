"""Repair Block 2 - generated questions as full research participants.

These regressions prove that a generated Q71+ question can autonomously travel
from a governed blind spot through real evaluator execution, persisted governed
results, the shared scientific bridge, findings, falsifiable hypotheses and - only
where a legitimate governed intervention exists - a governed candidate, with
re-entry, supersession, projection and Lab truth.

No test injects ``scientifically_meaningful``, ``falsification_criteria``,
``validation_criteria`` or ``governed_policy_id`` into the pipeline: those values
originate in the registered evaluator under test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments.governed_scientific_result import (
    SCIENTIFIC_RESULT_SCHEMA,
)
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchProposal,
)
from research_engine.lifecycle.generated_research_isolation import canonical_inventory
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.lifecycle.research_coverage import (
    CoverageEvidence, CoverageSnapshot, ResearchInventoryBoundary,
)
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore
from research_engine.lifecycle.research_observation_space import (
    CompatibilityRule, EvidenceCapability, ObservationCellDeclaration,
    ObservationSpace, ObservationSpaceConstructionPolicy,
)
from research_engine.registry.baseline_manifest import (
    BASELINE_QUESTION_IDS, BASELINE_QUESTION_SET,
)
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore, GeneratedQuestionResultStore,
    work_item_identity,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    AMBIGUOUS_EVALUATOR_MATCH,
    EVALUATOR_KEY_UNKNOWN,
    EVALUATOR_OUT_OF_DECLARED_SCOPE,
    NO_REGISTERED_EVALUATOR,
    RESOLVED_EXPLICIT_KEY,
    RESOLVED_UNIQUE_SCOPE_MATCH,
    GeneratedQuestionEvaluatorRegistration,
    GeneratedQuestionEvaluatorRegistry,
    GeneratedQuestionEvaluatorRequest,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy, run_generated_question_worker,
)
from research_engine.v10.continuous.q71_orchestration import run_q71_orchestration
from research_engine.v10.continuous.research_projection import (
    build_unified_research_projection,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    run_generated_scientific_bridge,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.validation_queue import ValidationQueueStore
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

from tests.test_canonical_question_cycle import MemoryS3, _freeze, _shadow_events
from tests.test_governed_scientific_result import _objects

import tests.q71_evaluator_fixtures as evaluators


DIMENSION = "DIM-AAAAAAAAAAAAAAAA"
EVIDENCE_CLASS = "EVENTS"
SNAPSHOT_FRONTIER = "FRONTIER-1"
EVALUATOR_MODULE = "tests.q71_evaluator_fixtures"


# -- Harness ------------------------------------------------------------------


def _space(count: int = 1, *, evidence_class: str = EVIDENCE_CLASS) -> ObservationSpace:
    policy = ObservationSpaceConstructionPolicy(
        allowed_subject_kinds=("CANONICAL_QUESTION",),
        allowed_dimensions=(DIMENSION,), allowed_populations=("shadow",),
        allowed_horizons=("SHORT",), allowed_evidence_classes=(evidence_class,),
        compatibility_rules=(CompatibilityRule(
            "q71_execution", subject_kinds=("CANONICAL_QUESTION",),
            dimension_identities=(DIMENSION,)),),
    )
    declarations = tuple(ObservationCellDeclaration(
        subject_kind="CANONICAL_QUESTION",
        subject_identity=canonical_inventory()[index],
        population_identity="shadow", horizon="SHORT", evidence_class=evidence_class,
        dimension_identities=(DIMENSION,),
        evidence_capability=EvidenceCapability.OBSERVABLE,
    ) for index in range(count))
    return ObservationSpace.construct(policy, declarations)


def _snapshot(space: ObservationSpace, evidence_by_cell, *, epoch: int) -> CoverageSnapshot:
    inventory = ResearchInventoryBoundary(
        space.observation_space_identity,
        question_inventory=tuple(BASELINE_QUESTION_IDS),
        evidence_inventory=tuple(sorted({
            ref for evidence in evidence_by_cell.values()
            for ref in evidence.evidence_refs
        })),
        evidence_fingerprint=f"Q71-EXEC-FP-{epoch}",
    )
    return CoverageSnapshot.construct(
        space, inventory, evidence_by_cell,
        observed_at=f"2026-10-08T00:0{epoch}:00+00:00")


def registration(
    function: str, *, evidence_class: str = EVIDENCE_CLASS,
    required_datasets: tuple[str, ...] = ("trade_truth",),
    evaluator_key: str | None = None,
    question_types: tuple[str, ...] = ("COVERAGE_GAP",),
    metric_families: tuple[str, ...] = ("UNKNOWN",),
    intervention_classes: tuple[str, ...] = ("UNKNOWN",),
    observation_cells: tuple[str, ...] = (),
    evaluator_version: str = "v1",
) -> GeneratedQuestionEvaluatorRegistration:
    return GeneratedQuestionEvaluatorRegistration(
        evaluator_key=evaluator_key or f"{EVALUATOR_MODULE}.{function}",
        runner_module=EVALUATOR_MODULE, runner_function=function,
        evaluator_version=evaluator_version,
        capability_class="GENERATED_QUESTION_COVERAGE_EVALUATOR",
        question_types=question_types, evidence_classes=(evidence_class,),
        metric_families=metric_families,
        intervention_classes=intervention_classes,
        required_datasets=required_datasets,
        observation_cells=observation_cells,
        report_schema_version="generated_question_report_v1",
        declared_scope_note="test double; declares its own governed semantics",
    )


def _registry(*registrations, path: Path | str | None = None) -> GeneratedQuestionEvaluatorRegistry:
    """Build a registry.  A path is supplied whenever the test persists it."""
    return GeneratedQuestionEvaluatorRegistry(
        path, registrations=list(registrations))


class Harness:
    """One isolated generated-question research environment."""

    def __init__(self, tmp_path: Path, *, space: ObservationSpace) -> None:
        self.root = tmp_path
        self.space = space
        self.cells = space.cells
        self.coverage = ResearchCoverageStore(tmp_path / "coverage.json")
        self.generated = GeneratedResearchStore(tmp_path / "generated.json")
        self.agenda = ResearchAgendaStore(tmp_path / "agenda.json")
        self.q71_state = tmp_path / "q71_state.json"
        self.execution = GeneratedQuestionExecutionStore(
            tmp_path / "execution_state.json")
        self.results = GeneratedQuestionResultStore(tmp_path / "results")
        self.scientific = ScientificStateStore(tmp_path / "scientific")
        self.registry = OptimisationRegistry(str(tmp_path / "optimisation"))
        self.validation = ValidationQueueStore(tmp_path / "validation_queue.json")
        self.memory_path = tmp_path / "memory.json"

    # -- Evidence --------------------------------------------------------
    def freeze_snapshot(self) -> tuple[str, Path, S3ResearchDataSource]:
        fake = MemoryS3(_objects(list(_shadow_events())))
        snapshot, manifests = _freeze(self.root, fake)
        source = S3ResearchDataSource(bucket="question-cycle-test", client=fake)
        return snapshot.snapshot_id, manifests, source

    def register_coverage(self, evidence_by_cell, *, epoch: int) -> None:
        self.coverage.register(_snapshot(self.space, evidence_by_cell, epoch=epoch))

    # -- Pipeline --------------------------------------------------------
    def orchestrate(self, *, evaluator_registry=None, capacity: int = 10,
                    snapshot_id: str = SNAPSHOT_FRONTIER):
        return run_q71_orchestration(
            snapshot_id=snapshot_id, capacity=capacity,
            coverage_store=self.coverage, generated_store=self.generated,
            agenda_store=self.agenda, state_path=self.q71_state,
            evaluator_registry=evaluator_registry)

    def execute(self, *, snapshot_id: str, manifests: Path,
                evaluator_registry: GeneratedQuestionEvaluatorRegistry,
                source: S3ResearchDataSource, max_questions: int = 4,
                max_attempts: int = 3):
        return run_generated_question_worker(
            snapshot_id=snapshot_id, evaluator_registry=evaluator_registry,
            result_store=self.results, execution_store=self.execution,
            generated_store=self.generated, q71_state_path=self.q71_state,
            manifest_directory=manifests, source=source,
            policy=GeneratedExecutionPolicy(
                max_questions_per_run=max_questions, max_attempts=max_attempts),
            recorded_at="2026-10-08T00:10:00+00:00")

    def bridge(self, batch_id: str):
        return run_generated_scientific_bridge(
            batch_id, generated_result_store=self.results,
            scientific_store=self.scientific, optimisation_registry=self.registry,
            treatment_memory_path=self.memory_path,
            policy_catalog=CANDIDATE_POLICIES_V1)

    def run_cycle(self, *, evaluator_registry, snapshot_id: str, manifests: Path,
                  source: S3ResearchDataSource, capacity: int = 10):
        orchestration = self.orchestrate(
            evaluator_registry=evaluator_registry, capacity=capacity)
        execution = self.execute(
            snapshot_id=snapshot_id, manifests=manifests,
            evaluator_registry=evaluator_registry, source=source)
        bridge = None
        if execution.get("batch_id"):
            bridge = self.bridge(str(execution["batch_id"]))
        return orchestration, execution, bridge

    def projection(self, *, q71: dict, bridge=None, question_projection=None):
        return build_unified_research_projection(
            continuous_cycle_id="CRCYCLE-TEST",
            frontier=_Frontier(),
            question_projection=question_projection,
            bridge=bridge,
            scientific_store=self.scientific,
            optimisation_registry=self.registry,
            validation_store=self.validation,
            q71=q71, generated_execution_store=self.execution,
            generated_result_store=self.results,
            projection_generated_at="2026-10-08T00:20:00+00:00")

    def generated_questions(self) -> dict[str, dict]:
        state = json.loads(self.q71_state.read_text(encoding="utf-8"))
        return state["question_states"]


class _Frontier:
    snapshot_id = "ISNAP-0000000000000000"
    fingerprint = "frontier-fingerprint"
    investigation_epoch = "EPOCH-1"



# -- Part B: real generated-question execution --------------------------------


def test_generated_question_traverses_the_governed_pipeline(tmp_path):
    """blind spot -> generated question -> evaluator -> result -> finding -> hypothesis."""
    harness = _harness(tmp_path)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        path=tmp_path / "registry.json")

    orchestration, execution, bridge = harness.run_cycle(
        evaluator_registry=registry, snapshot_id=snapshot_id,
        manifests=manifests, source=source)

    question_id = orchestration["new_question_ids"][0]
    assert question_id.startswith("GEN-")
    assert orchestration["generated_questions"][0]["status"] == "QUEUED"
    assert orchestration["generated_questions"][0]["lifecycle_status"] == "QUEUED"

    assert execution["result_ids"] and len(execution["result_ids"]) == 1
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "COMPLETE"
    assert result.scientific_status == "SCIENTIFICALLY_MEANINGFUL"
    assert result.generated_question_id == question_id
    assert result.evaluator_key == registry.all()[0].evaluator_key
    assert result.evidence_datasets == ("trade_truth",)
    assert result.evidence_references
    assert result.transport_result["runner"] == (
        "tests.q71_evaluator_fixtures.meaningful_mean_r_multiple")

    assert bridge is not None
    assert len(bridge.findings_created) == 1
    assert len(bridge.hypotheses_created) == 1
    assert bridge.candidates_created == ()
    assert bridge.source_kind == "GENERATED_QUESTION_EXECUTION_BATCH"
    hypothesis = harness.registry.get_hypothesis(bridge.hypotheses_created[0])
    assert hypothesis.status == "PROPOSED"
    assert hypothesis.falsification_criteria == [
        "a governed replication measures a non-positive mean r-multiple"]
    assert hypothesis.source_question == question_id
    assert any("CANDIDATE_DESIGN_REQUIRED" in item for item in bridge.review_required)


def test_generated_governed_intervention_creates_a_proposed_candidate(tmp_path):
    harness = _harness(tmp_path)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration("meaningful_mean_r_multiple_with_governed_intervention"),
        path=tmp_path / "registry.json")

    orchestration, execution, bridge = harness.run_cycle(
        evaluator_registry=registry, snapshot_id=snapshot_id,
        manifests=manifests, source=source)
    question_id = orchestration["new_question_ids"][0]

    assert len(bridge.findings_created) == 1
    assert len(bridge.hypotheses_created) == 1
    assert len(bridge.candidates_created) == 1
    candidate_id = bridge.candidates_created[0]
    candidate = harness.registry.get_candidate(candidate_id)
    assert candidate.status == "PROPOSED"
    assert candidate.policy_id == evaluators.GOVERNED_POLICY_ID
    assert candidate.shadow_binding.get("live_approved", False) is False
    assert len(candidate.source_question_results) == 1
    assert candidate.provenance["question_id"] == question_id
    assert candidate.provenance["generated_question_result_id"] == (
        execution["result_ids"][0])
    plan = harness.registry.get_plan(candidate_id)
    assert plan is not None and plan.minimum_sample == 100
    assert plan.target_questions == [question_id]
    assert bridge.validation_handoff

    projection = harness.projection(q71=orchestration, bridge=bridge)
    row = projection["generated_questions"][0]
    assert row["lifecycle_status"] == "COMPLETE"
    assert row["question_answered"] is True
    assert row["question_scientifically_actionable"] is True
    assert row["linked_findings"] == [
        str(item).split(":v")[0] for item in bridge.findings_created]
    assert row["linked_hypotheses"] == list(bridge.hypotheses_created)
    assert row["linked_candidates"] == list(bridge.candidates_created)
    assert row["latest_result_id"] == execution["result_ids"][0]
    assert row["evaluator_identity_digest"] == registry.all()[0].evaluator_identity_digest
    assert row["execution_freshness"] == "CURRENT"
    assert row["governed_scientific_metrics"]["governed_policy_id"] == (
        evaluators.GOVERNED_POLICY_ID)
    candidate_rows = {c["candidate_id"]: c for c in projection["candidates"]}
    assert candidate_rows[candidate_id]["live_approved"] is False



def _request(*, question_id: str = "GEN-0000000000000000",
             evidence_class: str = EVIDENCE_CLASS,
             explicit: str | None = None,
             cell: str = "OBS-CELL-1") -> GeneratedQuestionEvaluatorRequest:
    return GeneratedQuestionEvaluatorRequest(
        generated_question_id=question_id, question_type="COVERAGE_GAP",
        evidence_class=evidence_class, observation_cell_identity=cell,
        explicit_evaluator_key=explicit)


def test_evaluator_resolution_is_deterministic_and_persisted(tmp_path):
    entry = registration("meaningful_mean_r_multiple")
    registry_path = tmp_path / "registry.json"
    registry = _registry(entry, path=registry_path)
    first = registry.resolve(_request())
    second = registry.resolve(_request())
    assert first == second
    assert first.resolved and first.reason_code == RESOLVED_UNIQUE_SCOPE_MATCH
    assert first.evaluator_key == entry.evaluator_key
    assert first.evaluator_identity_digest == entry.evaluator_identity_digest

    registry.save()
    assert registry_path.exists()
    reloaded = GeneratedQuestionEvaluatorRegistry(registry_path)
    assert reloaded.registry_identity == registry.registry_identity
    assert reloaded.resolve(_request()) == first

    # A silently edited registration cannot masquerade as the original.
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    document["registrations"][0]["evaluator_version"] = "v2"
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(Exception, match="EVALUATOR_IDENTITY_DIGEST_MISMATCH"):
        GeneratedQuestionEvaluatorRegistry(registry_path)


def test_evaluator_scope_is_declared_and_never_generic():
    with pytest.raises(Exception, match="EVALUATOR_SCOPE_DIMENSION_UNDECLARED"):
        registration("meaningful_mean_r_multiple", metric_families=())
    registry = _registry(registration("meaningful_mean_r_multiple"))
    # A question outside the declared evidence class resolves to nothing.
    assert registry.resolve(
        _request(evidence_class="TRADES")).reason_code == NO_REGISTERED_EVALUATOR
    # A question outside the declared question type resolves to nothing.
    outside = GeneratedQuestionEvaluatorRequest(
        generated_question_id="GEN-1", question_type="INTERACTION_GAP",
        evidence_class=EVIDENCE_CLASS, observation_cell_identity="OBS-CELL-1")
    assert registry.resolve(outside).reason_code == NO_REGISTERED_EVALUATOR


def test_unknown_and_out_of_scope_explicit_keys_fail_closed():
    entry = registration("meaningful_mean_r_multiple", evidence_class=EVIDENCE_CLASS)
    registry = _registry(entry)
    assert registry.resolve(
        _request(explicit="tests.missing.evaluator")).reason_code == (
            EVALUATOR_KEY_UNKNOWN)
    out_of_scope = registry.resolve(
        _request(explicit=entry.evaluator_key, evidence_class="TRADES"))
    assert out_of_scope.reason_code == EVALUATOR_OUT_OF_DECLARED_SCOPE
    assert not out_of_scope.resolved
    in_scope = registry.resolve(_request(explicit=entry.evaluator_key))
    assert in_scope.reason_code == RESOLVED_EXPLICIT_KEY


def test_ambiguous_evaluator_match_fails_closed():
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        registration("descriptive_r_multiple_profile"))
    resolution = registry.resolve(_request())
    assert resolution.resolved is False
    assert resolution.reason_code == AMBIGUOUS_EVALUATOR_MATCH
    assert len(resolution.candidates) == 2


def test_observation_cell_specificity_breaks_a_tie_deterministically():
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        registration("descriptive_r_multiple_profile", observation_cells=("OBS-CELL-1",)))
    resolution = registry.resolve(_request())
    assert resolution.resolved
    assert resolution.evaluator_key.endswith("descriptive_r_multiple_profile")


def test_registry_rejects_a_runner_bound_to_two_keys():
    registry = _registry(registration("meaningful_mean_r_multiple"))
    with pytest.raises(Exception, match="EVALUATOR_RUNNER_BOUND_TO_TWO_KEYS"):
        registry.register(registration(
            "meaningful_mean_r_multiple", evaluator_key="other.key"))


def test_capability_index_matches_resolution_authority():
    entry = registration("meaningful_mean_r_multiple")
    registry = _registry(entry)
    index = registry.capability_index()
    assert index[EVIDENCE_CLASS] == entry.evaluator_key
    assert index["COVERAGE_GAP"] == entry.evaluator_key
    assert entry.evaluator_identity_digest
    assert SCIENTIFIC_RESULT_SCHEMA == entry.scientific_result_schema

    frontier_start = "2026-09-25"
    frontier_end = "2026-09-25"
    membership_closed_at = "2026-09-25T13:00:00+00:00"
    predecessor_snapshot_id = None
    changed_datasets: tuple[str, ...] = ()
    stale_datasets: tuple[str, ...] = ()
    missing_optional_datasets: tuple[str, ...] = ()
    status = "FROZEN"


def _harness(tmp_path: Path, *, evidence_class: str = EVIDENCE_CLASS,
             count: int = 1) -> Harness:
    return Harness(tmp_path, space=_space(count, evidence_class=evidence_class))


# -- Part C: the bridge applies the canonical rules to generated results -------


def _run_with(tmp_path: Path, function: str, *, evidence_class: str = EVIDENCE_CLASS,
              required_datasets: tuple[str, ...] = ("trade_truth",),
              name: str = "h"):
    harness = _harness(tmp_path / name, evidence_class=evidence_class)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration(function, evidence_class=evidence_class,
                     required_datasets=required_datasets),
        path=tmp_path / name / "registry.json")
    orchestration, execution, bridge = harness.run_cycle(
        evaluator_registry=registry, snapshot_id=snapshot_id,
        manifests=manifests, source=source)
    return harness, orchestration, execution, bridge


def test_descriptive_complete_generated_result_creates_no_finding(tmp_path):
    harness, _, execution, bridge = _run_with(
        tmp_path, "descriptive_r_multiple_profile")
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "COMPLETE"
    assert result.scientific_status == "DECLARED_NOT_MEANINGFUL"
    assert bridge.findings_created == ()
    assert bridge.hypotheses_created == ()
    assert bridge.candidates_created == ()
    assert bridge.status == "NO_SCIENTIFIC_STATE_CHANGE"
    assert any("FINDING_DERIVATION:RUNNER_DECLARED_NOT_MEANINGFUL" in item
               for item in bridge.review_required)


@pytest.mark.parametrize("function,expected", [
    ("waiting_for_data_declaration", "WAITING_FOR_DATA"),
    ("insufficient_evidence_declaration", "INSUFFICIENT_DATA"),
])
def test_non_resolved_generated_results_create_no_finding(tmp_path, function, expected):
    harness, _, execution, bridge = _run_with(tmp_path, function)
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == expected
    assert result.scientific_status == "NOT_SCIENTIFICALLY_RESOLVED"
    assert bridge.findings_created == ()
    assert bridge.hypotheses_created == ()
    assert bridge.candidates_created == ()




def test_missing_evaluator_is_distinct_and_visible_without_a_finding(tmp_path):
    harness = _harness(tmp_path)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    empty = GeneratedQuestionEvaluatorRegistry(registrations=[])

    orchestration = harness.orchestrate(evaluator_registry=empty)
    question_id = orchestration["new_question_ids"][0]
    assert orchestration["generated_questions"][0]["status"] == "MISSING_EVALUATOR"
    assert orchestration["generated_questions"][0]["evidence_availability"] is True
    assert orchestration["queue"] == []

    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=empty, source=source)
    assert execution["result_ids"] == []
    state = harness.execution.state(question_id)
    assert state["execution_status"] == "MISSING_EVALUATOR"
    assert state["reason_code"] == NO_REGISTERED_EVALUATOR
    assert state["latest_result_id"] is None

    projection = harness.projection(q71=orchestration)
    row = projection["generated_questions"][0]
    assert row["lifecycle_status"] == "MISSING_EVALUATOR"
    assert row["missing_evaluator_reason"] == NO_REGISTERED_EVALUATOR
    assert row["question_executable"] is False
    assert row["question_answered"] is False
    assert row["question_scientifically_actionable"] is False
    assert projection["findings"] == []
    assert projection["investigations_and_work_queues"][
        "missing_evaluator_investigations"] == [question_id]


def test_negative_generated_result_is_a_legitimate_scientific_finding(tmp_path):
    harness, _, execution, bridge = _run_with(
        tmp_path, "negative_result_mean_r_multiple")
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "NEGATIVE_RESULT"
    assert result.scientific_status == "NEGATIVE_SCIENTIFIC_RESULT"
    assert len(bridge.findings_created) == 1
    assert len(bridge.hypotheses_created) == 1
    assert bridge.candidates_created == ()


def test_missing_falsification_stops_at_the_finding(tmp_path):
    harness, _, _, bridge = _run_with(tmp_path, "meaningful_without_falsification")
    assert len(bridge.findings_created) == 1
    assert bridge.hypotheses_created == ()
    assert any(item.endswith(":FALSIFICATION_CRITERIA_REQUIRED")
               for item in bridge.review_required)


@pytest.mark.parametrize("function,expected_reason", [
    ("malformed_governed_result", "EVALUATOR_OUTPUT_MALFORMED"),
    ("non_mapping_report", "EVALUATOR_OUTPUT_MALFORMED"),
    ("raising_evaluator", "EVALUATOR_EXECUTION_FAILED"),
])
def test_malformed_or_failing_evaluator_fails_closed(tmp_path, function, expected_reason):
    harness, orchestration, execution, bridge = _run_with(tmp_path, function)
    question_id = orchestration["new_question_ids"][0]
    assert execution["result_ids"] == []
    state = harness.execution.state(question_id)
    assert state["execution_status"] == "IMPLEMENTATION_BLOCKED"
    assert state["reason_code"] == expected_reason
    assert bridge is None
    assert harness.scientific.document["findings"] == {}


def test_absent_declared_evidence_dataset_waits_for_data(tmp_path):
    harness, orchestration, execution, bridge = _run_with(
        tmp_path, "required_evidence_evaluator",
        required_datasets=("equity_curve",))
    question_id = orchestration["new_question_ids"][0]
    assert execution["result_ids"] == []
    state = harness.execution.state(question_id)
    assert state["execution_status"] == "WAITING_FOR_DATA"
    assert state["reason_code"].startswith("EVIDENCE_DATASET_ABSENT")
    assert bridge is None



# -- Part D: re-entry on changed evidence and changed capability --------------


def _executed_harness(tmp_path: Path, *, name: str = "reentry"):
    harness = _harness(tmp_path / name)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        path=tmp_path / name / "registry.json")
    orchestration, execution, bridge = harness.run_cycle(
        evaluator_registry=registry, snapshot_id=snapshot_id,
        manifests=manifests, source=source)
    return harness, cell, snapshot_id, manifests, source, registry, orchestration


def test_unchanged_relevant_evidence_does_not_re_execute(tmp_path):
    harness, _, snapshot_id, manifests, source, registry, orchestration = (
        _executed_harness(tmp_path))
    question_id = orchestration["new_question_ids"][0]
    first = harness.results.result_ids(question_id)
    assert len(first) == 1
    before = harness.results.result_path(first[0]).read_bytes()
    # A second cycle over the identical governed evidence epoch.
    orchestration2 = harness.orchestrate(evaluator_registry=registry)
    execution2 = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert execution2["result_ids"] == []
    assert list(harness.results.result_ids(question_id)) == list(first)
    assert harness.results.result_path(first[0]).read_bytes() == before
    assert orchestration2["generated_questions"][0]["reentry_count"] == 0


def test_changed_relevant_evidence_re_enters_with_a_new_immutable_result(tmp_path):
    harness, cell, snapshot_id, manifests, source, registry, orchestration = (
        _executed_harness(tmp_path))
    question_id = orchestration["new_question_ids"][0]
    first = harness.results.result_ids(question_id)[0]
    first_bytes = harness.results.result_path(first).read_bytes()

    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1", "EVD-2")),
    }, epoch=2)
    orchestration2 = harness.orchestrate(evaluator_registry=registry)
    assert orchestration2["generated_questions"][0]["reentry_count"] == 1
    assert orchestration2["generated_questions"][0]["reentry_reason"] == (
        "RELEVANT_EVIDENCE_CHANGED")

    execution2 = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert len(execution2["result_ids"]) == 1
    second = execution2["result_ids"][0]
    assert second != first
    assert list(harness.results.result_ids(question_id)) == sorted([first, second])
    # History is immutable: the original artifact is byte-for-byte unchanged.
    assert harness.results.result_path(first).read_bytes() == first_bytes
    bridge2 = harness.bridge(str(execution2["batch_id"]))

    # The scientific proposition did not change, so no scientific churn occurs.
    assert bridge2.findings_created == ()
    assert bridge2.hypotheses_created == ()
    assert len(harness.scientific.document["findings"]) == 1


def test_missing_evidence_arriving_later_re_enters(tmp_path):
    harness = _harness(tmp_path)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(question_refs=("Q-GAP",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        path=tmp_path / "registry.json")

    waiting = harness.orchestrate(evaluator_registry=registry)
    question_id = waiting["new_question_ids"][0]
    assert waiting["generated_questions"][0]["status"] == "WAITING_FOR_DATA"
    first_execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert first_execution["result_ids"] == []
    assert harness.execution.state(question_id)["execution_status"] == "WAITING_FOR_DATA"

    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-ARRIVED",)),
    }, epoch=2)
    ready = harness.orchestrate(evaluator_registry=registry)
    assert ready["new_question_ids"] == []
    assert ready["generated_questions"][0]["status"] == "QUEUED"
    assert ready["generated_questions"][0]["reentry_reason"] == (
        "MISSING_EVIDENCE_ARRIVED")
    second_execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert len(second_execution["result_ids"]) == 1
    assert harness.execution.state(question_id)["execution_status"] == "COMPLETE"


def test_evaluator_registration_arriving_later_re_enters(tmp_path):
    harness = _harness(tmp_path)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    empty = GeneratedQuestionEvaluatorRegistry(registrations=[])

    blocked = harness.orchestrate(evaluator_registry=empty)
    question_id = blocked["new_question_ids"][0]
    assert blocked["generated_questions"][0]["status"] == "MISSING_EVALUATOR"
    harness.execute(snapshot_id=snapshot_id, manifests=manifests,
                    evaluator_registry=empty, source=source)
    assert harness.execution.state(question_id)["execution_status"] == (
        "MISSING_EVALUATOR")

    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        path=tmp_path / "registry.json")
    ready = harness.orchestrate(evaluator_registry=registry)
    assert ready["generated_questions"][0]["status"] == "QUEUED"
    assert ready["generated_questions"][0]["reentry_reason"] == (
        "EVALUATOR_CAPABILITY_APPEARED")
    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert len(execution["result_ids"]) == 1


# -- Part E: autonomous supersession and retirement ---------------------------


def _mint_newer_generation(harness: Harness, question_id: str, version: int):
    record = harness.generated.get(question_id)
    specification = dict(record.specification)
    specification["generation_version"] = version
    proposal = GeneratedResearchProposal(
        research_kind=record.research_kind, trigger_ref=record.trigger_ref,
        target_kind=record.target_kind, target_ref=record.target_ref,
        specification=specification, dimension_ref=record.dimension_ref,
        parent_refs=record.parent_refs)
    return harness.generated.register(
        proposal, provenance={"origin": "TEST_NEWER_GENERATION"})


def test_structurally_superseded_question_is_retained_and_not_executed(tmp_path):
    harness, _, snapshot_id, manifests, source, registry, orchestration = (
        _executed_harness(tmp_path, name="supersession"))
    old_id = orchestration["new_question_ids"][0]
    first_result_id = harness.results.result_ids(old_id)[0]
    newer = _mint_newer_generation(harness, old_id, 2)

    second = harness.orchestrate(evaluator_registry=registry)
    superseded = {row["generated_question_id"]: row
                  for row in second["superseded_questions"]}
    assert old_id in superseded
    assert superseded[old_id]["superseded_by"] == newer.generated_research_id
    assert superseded[old_id]["reason_code"] == "SUPERSEDED_BY_NEWER_GENERATION"

    states = harness.generated_questions()
    assert states[old_id]["status"] == "SUPERSEDED"
    assert states[old_id]["superseded_by"] == newer.generated_research_id
    assert states[old_id]["reentry_authorized"] is False
    # Historical retention: the superseded question identity still exists.
    assert harness.generated.get(old_id) is not None
    assert len(states[old_id]["supersession_history"]) == 1

    # Exactly one active question and one queue entry for the observation cell.
    key = harness.generated.get(old_id).specification["supersession_key"]
    active = sorted(
        qid for qid, row in states.items()
        if harness.generated.get(qid).specification.get("supersession_key") == key
        and row["status"] not in {"SUPERSEDED", "RETIRED", "INVALID"})
    assert active == [newer.generated_research_id]
    assert [row["generated_question_id"] for row in second["queue"]] == [
        newer.generated_research_id]

    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert execution["executed_question_ids"] == [newer.generated_research_id]
    assert list(harness.results.result_ids(old_id)) == [first_result_id]


def test_retired_question_is_not_re_executed(tmp_path):
    harness, cell, snapshot_id, manifests, source, registry, orchestration = (
        _executed_harness(tmp_path, name="retired"))
    question_id = orchestration["new_question_ids"][0]

    # The blind spot is resolved by governed research, so the question retires.
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(
            evidence_refs=("EVD-1",), research_attempt_refs=("TRY-1",),
            conclusion_refs=("CON-1",)),
    }, epoch=2)
    retired = harness.orchestrate(evaluator_registry=registry)
    assert retired["retired_question_ids"] == [question_id]
    assert retired["generated_questions"][0]["status"] == "RETIRED"
    assert retired["queue"] == []

    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert execution["executed_question_ids"] == []
    assert len(harness.results.result_ids(question_id)) == 1


# -- Part F: bounded autonomy / backpressure ----------------------------------


def test_generated_queue_stays_bounded_across_repeated_cycles(tmp_path):
    harness = _harness(tmp_path, count=3)
    cells = harness.cells
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=(f"EVD-{index}",))
        for index, cell in enumerate(cells)
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _registry(
        registration("meaningful_mean_r_multiple"),
        path=tmp_path / "registry.json")

    observed = []
    for cycle in range(4):
        orchestration = harness.orchestrate(
            evaluator_registry=registry, capacity=2)
        observed.append(len(orchestration["queue"]))
        execution = harness.execute(
            snapshot_id=snapshot_id, manifests=manifests,
            evaluator_registry=registry, source=source, max_questions=1)
        if execution.get("batch_id"):
            harness.bridge(str(execution["batch_id"]))

    assert max(observed) <= 2
    states = harness.generated_questions()
    # One stable question per governed observation cell, never more.
    per_cell: dict[str, int] = {}
    for question_id, row in states.items():
        record = harness.generated.get(question_id)
        key = record.specification.get("supersession_key")
        if row["status"] not in {"SUPERSEDED", "RETIRED", "INVALID"}:
            per_cell[key] = per_cell.get(key, 0) + 1
    assert set(per_cell.values()) <= {1}
    metrics = harness.execution.metrics()
    assert metrics["running_work_items"] == 0
    assert metrics["executed_question_count"] >= 1




# -- Part G: authority, projection, Lab and immutability ----------------------


def test_worker_transports_the_governed_contract_without_fabricating_fields(tmp_path):
    harness, _, execution, _ = _run_with(
        tmp_path, "meaningful_mean_r_multiple_with_governed_intervention",
        name="transport")
    result = harness.results.load_result(execution["result_ids"][0])
    governed = result.governed_scientific_metrics
    assert governed["scientifically_meaningful"] is True
    assert governed["governed_policy_id"] == evaluators.GOVERNED_POLICY_ID
    assert governed["validation_criteria"]["required_sample"] == 100
    assert governed["falsification_criteria"]
    assert governed["population"]["question_id"] == result.generated_question_id
    assert result.transport_result["key_metrics"]["scientifically_meaningful"] is True

    # A descriptive evaluator transports no intervention and no falsification:
    # the worker adds nothing of its own.
    other, _, other_execution, _ = _run_with(
        tmp_path, "descriptive_r_multiple_profile", name="transport-descriptive")
    descriptive = other.results.load_result(other_execution["result_ids"][0])
    assert "governed_policy_id" not in descriptive.governed_scientific_metrics
    assert "falsification_criteria" not in descriptive.governed_scientific_metrics
    assert descriptive.governed_scientific_metrics[
        "scientifically_meaningful"] is False
    assert descriptive.governed_scientific_metrics[
        "scientific_not_meaningful_reason"] == "DESCRIPTIVE_ONLY"
    assert descriptive.transport_result["key_metrics"][
        "scientifically_meaningful"] is False


def test_no_parallel_generated_writer_bypasses_bridge_authority():
    import research_engine

    root = Path(research_engine.__file__).resolve().parent
    generated_bridge_callers = set()
    q71_modules = (
        "q71_worker.py", "generated_question_result.py",
        "q71_evaluator_registry.py", "generated_question_lifecycle.py",
        "q71_orchestration.py",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = str(path.relative_to(root)).replace("\\", "/")
        if "run_generated_scientific_bridge" in text:
            generated_bridge_callers.add(relative)
    assert generated_bridge_callers == {
        "v10/continuous/__init__.py",
        "v10/continuous/research_loop.py",
        "v10/continuous/scientific_state_bridge.py",
    }, sorted(generated_bridge_callers)
    for name in q71_modules:
        text = (root / "v10" / "continuous" / name).read_text(
            encoding="utf-8", errors="ignore")
        assert "OptimisationRegistry" not in text, name
        assert "add_candidate(" not in text, name
        assert "add_hypothesis(" not in text, name
        assert "run_scientific_state_bridge" not in text, name
    bridge_text = (
        root / "v10" / "continuous" / "scientific_state_bridge.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert bridge_text.count("registry.add_hypothesis(") == 1
    assert bridge_text.count("registry.add_candidate(") == 1


def test_canonical_70_are_untouched_and_generated_questions_stay_separate(tmp_path):
    harness, orchestration, execution, _ = _run_with(
        tmp_path, "meaningful_mean_r_multiple", name="separation")
    question_id = orchestration["new_question_ids"][0]
    assert len(BASELINE_QUESTION_IDS) == 70
    assert question_id.startswith("GEN-")
    assert question_id not in BASELINE_QUESTION_SET
    assert all(not item.startswith("GEN-") for item in BASELINE_QUESTION_IDS)
    # The canonical question cycle state is never written by the Q71 path.
    assert not (harness.root / "questions").exists()
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.transport_result["question_id"] == question_id



def test_result_identity_is_deterministic_and_history_immutable(tmp_path):
    harness, _, execution, _ = _run_with(
        tmp_path, "meaningful_mean_r_multiple", name="immutability")
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.result_id == result.derived_result_id
    assert result.work_item_id.startswith("GQW-")
    assert result.work_item_id == work_item_identity(
        generated_question_id=result.generated_question_id,
        evidence_epoch=harness.execution.state(
            result.generated_question_id)["result_evidence_identity"],
        evaluator_identity_digest=result.evaluator_identity_digest)

    # Re-persisting the identical artifact is a no-op.
    harness.results.save_result(result)
    assert harness.results.load_result(result.result_id).to_dict() == result.to_dict()

    # A different payload under the same identity is refused: history is immutable.
    path = harness.results.result_path(result.result_id)
    tampered = result.to_dict()
    tampered["reason"] = "tampered"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(Exception, match="GENERATED_RESULT_IDENTITY_COLLISION"):
        harness.results.save_result(result)


def test_projection_and_lab_report_generated_lifecycle_and_science(tmp_path, monkeypatch):
    from research_engine.v10.continuous.research_projection import (
        ResearchProjectionStore,
    )
    from research_engine.v10.lab import server

    harness, orchestration, execution, bridge = _run_with(
        tmp_path, "meaningful_mean_r_multiple_with_governed_intervention",
        name="lab")
    question_id = orchestration["new_question_ids"][0]
    projection = harness.projection(q71=orchestration, bridge=bridge)
    state_root = tmp_path / "continuous"
    ResearchProjectionStore(state_root / "projection").save(projection)

    monkeypatch.setattr(server, "STATE_ROOT", state_root)
    monkeypatch.setattr(server, "PROGRESS_PATH", state_root / "cycle_progress.json")
    monkeypatch.setattr(server, "LAST_RUN_PATH", state_root / "lab_last_run.json")
    monkeypatch.setattr(server, "_run", {"status": "IDLE"})
    state = server.build_state()

    assert state["source"] == "UNIFIED_PROJECTION"
    q71 = state["q71"]
    assert q71["lifecycle_counts"] == {"COMPLETE": 1}
    assert q71["execution_counts"] == {"COMPLETE": 1}
    assert q71["freshness_counts"] == {"CURRENT": 1}
    assert q71["answered"] == [question_id]
    assert q71["scientifically_actionable"] == [question_id]
    assert q71["missing_evaluator"] == []
    assert q71["superseded"] == []
    row = q71["generated"][0]
    assert row["lifecycle_status"] == "COMPLETE"
    assert row["question_answered"] is True
    assert row["question_scientifically_actionable"] is True
    assert row["evaluator_identity_digest"]
    assert row["linked_findings"]
    assert state["invariants"]["live_approved_any"] is False


def test_continuous_loop_exposes_a_governed_generated_execution_stage():
    import inspect

    from research_engine.v10.continuous.research_loop import (
        STAGES, run_continuous_research_cycle,
    )

    assert "Q71_EXECUTION" in STAGES
    parameters = inspect.signature(run_continuous_research_cycle).parameters
    for name in (
        "q71_evaluator_registry", "q71_execution_policy", "q71_manifest_directory",
        "q71_evidence_source", "generated_execution_store",
        "generated_result_store", "generated_bridge_runner",
    ):
        assert name in parameters, name
