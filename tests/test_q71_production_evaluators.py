"""Repair Block 2A — real production Q71+ evaluator families.

Repair Block 2 delivered the generated-question execution architecture with an
empty production evaluator registry, so every real generated question correctly
resolved to ``MISSING_EVALUATOR``.  These regressions prove the production
capability is now real:

* the production registry loads, is reproducible, and survives reload;
* every registered evaluator respects its declared scope and fails closed
  outside it;
* a real generated question in a supported structural class resolves to a
  production evaluator automatically, executes against governed frozen evidence,
  emits an immutable governed result and enters the shared scientific bridge;
* unsupported structural families stay ``MISSING_EVALUATOR``;
* a descriptive production evaluator can answer and still create no finding;
* insufficient evidence produces INSUFFICIENT_DATA;
* changed evidence re-enters with a new immutable result; unchanged evidence
  does not re-execute;
* no test fixture can leak into the production registry, no generic catch-all
  evaluator exists, and no candidate can be created without a governed
  intervention mapping.

The acceptance proofs use the *production* evaluator classes and the
*production* registry builder, not test-only evaluator fixtures.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments.q71_evidence_classes import (
    CURRENT_COMPLETED_SHADOW_LIFECYCLES,
    SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE,
    structural_generated_question_families,
    unsupported_evidence_class_catalogue,
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
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore, GeneratedQuestionResultStore,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    AMBIGUOUS_EVALUATOR_MATCH,
    EVALUATOR_KEY_UNKNOWN,
    EVALUATOR_OUT_OF_DECLARED_SCOPE,
    NO_REGISTERED_EVALUATOR,
    RESOLVED_UNIQUE_SCOPE_MATCH,
    GeneratedQuestionEvaluatorRegistration,
    GeneratedQuestionEvaluatorRegistry,
    GeneratedQuestionEvaluatorRequest,
    UNKNOWN,
)
from research_engine.v10.continuous.q71_orchestration import run_q71_orchestration
from research_engine.v10.continuous.q71_production_registry import (
    PRODUCTION_EVALUATOR_FAMILIES,
    ProductionEvaluatorRegistryError,
    build_production_evaluator_registry,
    load_production_evaluator_registry,
    production_capability_matrix,
    production_evaluator_registrations,
    production_registry_document,
    production_registry_identity,
    verify_production_registry,
    write_production_evaluator_registry,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy, run_generated_question_worker,
)
from research_engine.v10.continuous.research_lab import build_lab_view
from research_engine.v10.continuous.research_projection import (
    build_unified_research_projection,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    run_generated_scientific_bridge,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.validation_queue import ValidationQueueStore
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

from tests.test_canonical_question_cycle import MemoryS3, _freeze
from tests.test_governed_scientific_result import _objects, _shadow_lifecycles


DIMENSION = "DIM-AAAAAAAAAAAAAAAA"
SNAPSHOT_FRONTIER = "FRONTIER-1"
EXIT_PATH_EVIDENCE_CLASS = "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH"
UNSUPPORTED_EVIDENCE_CLASS = "EVENTS"
EVALUATOR_MODULE = "research_engine.experiments.q71_production_evaluators"

#: Six distinct completed lifecycles with non-zero variance: enough for E1's
#: one-sample t-test to be estimable, so the production expectancy evaluator can
#: declare a real scientific result.
MEANINGFUL_R_MULTIPLES = (2.0, -1.0, 1.5, 0.5, -0.5, 3.0)

#: Three lifecycles: below the canonical minimum of five, so the test is not
#: estimable and the evaluator must declare INSUFFICIENT_GOVERNED_EVIDENCE.
INSUFFICIENT_R_MULTIPLES = (1.0, -0.5, 0.25)

INSUFFICIENT_R_MULTIPLES = (1.0, -0.5, 0.25)


# ── Harness ──────────────────────────────────────────────────────────────────

def _space(count: int, evidence_class: str) -> ObservationSpace:
    policy = ObservationSpaceConstructionPolicy(
        allowed_subject_kinds=("CANONICAL_QUESTION",),
        allowed_dimensions=(DIMENSION,), allowed_populations=("shadow",),
        allowed_horizons=("SHORT",), allowed_evidence_classes=(evidence_class,),
        compatibility_rules=(CompatibilityRule(
            "q71_production", subject_kinds=("CANONICAL_QUESTION",),
            dimension_identities=(DIMENSION,)),),
    )
    declarations = tuple(ObservationCellDeclaration(
        subject_kind="CANONICAL_QUESTION",
        subject_identity=canonical_inventory()[index],
        population_identity="shadow", horizon="SHORT",
        evidence_class=evidence_class, dimension_identities=(DIMENSION,),
        evidence_capability=EvidenceCapability.OBSERVABLE,
    ) for index in range(count))
    return ObservationSpace.construct(policy, declarations)


def _coverage_snapshot(
    space: ObservationSpace, evidence_by_cell, *, epoch: int,
) -> CoverageSnapshot:
    inventory = ResearchInventoryBoundary(
        space.observation_space_identity,
        question_inventory=tuple(BASELINE_QUESTION_IDS),
        evidence_inventory=tuple(sorted({
            ref for evidence in evidence_by_cell.values()
            for ref in evidence.evidence_refs
        })),
        evidence_fingerprint=f"Q71-PROD-FP-{epoch}",
    )
    return CoverageSnapshot.construct(
        space, inventory, evidence_by_cell,
        observed_at=f"2026-10-08T00:0{epoch}:00+00:00")


class _Frontier:
    snapshot_id = "ISNAP-0000000000000000"
    fingerprint = "frontier-fingerprint"
    investigation_epoch = "EPOCH-1"


def _canonical_question_projection() -> dict:
    """A minimal, valid canonical-70 projection authority for Lab/projection truth.

    The canonical questions are deliberately inert here: these regressions prove
    generated-question truth, and the canonical 70 must remain untouched by it.
    """
    return {
        "questions": {
            question_id: {"question_id": question_id}
            for question_id in BASELINE_QUESTION_IDS
        },
    }

class Harness:
    """One isolated generated-question research environment."""

    def __init__(self, tmp_path: Path, *, evidence_class: str,
                 r_multiples=MEANINGFUL_R_MULTIPLES) -> None:
        self.root = tmp_path
        self.evidence_class = evidence_class
        self.space = _space(1, evidence_class)
        self.cells = self.space.cells
        self.r_multiples = tuple(r_multiples)
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
    def freeze_snapshot(self):
        fake = MemoryS3(_objects(_shadow_lifecycles(self.r_multiples)))
        snapshot, manifests = _freeze(self.root, fake)
        # The frozen snapshot binds the exact source authority it was created
        # with; a different bucket would (correctly) fail SOURCE_AUTHORITY_CHANGED.
        source = S3ResearchDataSource(bucket="question-cycle-test", client=fake)
        return snapshot.snapshot_id, manifests, source

    def register_coverage(self, evidence_by_cell, *, epoch: int) -> None:
        self.coverage.register(
            _coverage_snapshot(self.space, evidence_by_cell, epoch=epoch))

    # -- Pipeline --------------------------------------------------------
    def orchestrate(self, *, evaluator_registry=None, capacity: int = 10):
        return run_q71_orchestration(
            snapshot_id=SNAPSHOT_FRONTIER, capacity=capacity,
            coverage_store=self.coverage, generated_store=self.generated,
            agenda_store=self.agenda, state_path=self.q71_state,
            evaluator_registry=evaluator_registry)

    def execute(self, *, snapshot_id: str, manifests: Path,
                evaluator_registry: GeneratedQuestionEvaluatorRegistry,
                source: S3ResearchDataSource, max_questions: int = 4):
        return run_generated_question_worker(
            snapshot_id=snapshot_id, evaluator_registry=evaluator_registry,
            result_store=self.results, execution_store=self.execution,
            generated_store=self.generated, q71_state_path=self.q71_state,
            manifest_directory=manifests, source=source,
            policy=GeneratedExecutionPolicy(
                max_questions_per_run=max_questions, max_attempts=3),
            recorded_at="2026-10-08T00:10:00+00:00")

    def bridge(self, batch_id: str):
        return run_generated_scientific_bridge(
            batch_id, generated_result_store=self.results,
            scientific_store=self.scientific,
            optimisation_registry=self.registry,
            treatment_memory_path=self.memory_path,
            policy_catalog=CANDIDATE_POLICIES_V1)

    def projection(self, *, q71: dict, bridge=None):
        return build_unified_research_projection(
            continuous_cycle_id="CRCYCLE-PROD",
            frontier=_Frontier(),
            question_projection=_canonical_question_projection(),
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


def _production_registry(tmp_path: Path) -> GeneratedQuestionEvaluatorRegistry:
    """The real production registry, persisted at an isolated path."""
    return write_production_evaluator_registry(
        tmp_path / "production_registry.json")


def _run_full_cycle(
    tmp_path: Path, *,
    evidence_class: str = CURRENT_COMPLETED_SHADOW_LIFECYCLES,
    r_multiples=MEANINGFUL_R_MULTIPLES,
):
    harness = Harness(
        tmp_path, evidence_class=evidence_class, r_multiples=r_multiples)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _production_registry(tmp_path)
    orchestration = harness.orchestrate(evaluator_registry=registry)
    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    bridge = None
    if execution.get("batch_id"):
        bridge = harness.bridge(str(execution["batch_id"]))
    return harness, orchestration, execution, bridge, registry


# ── Step 1-3: production registry identity, scope and contamination ──────────

def test_production_registry_loads_and_persists(tmp_path):
    registry = _production_registry(tmp_path)
    path = tmp_path / "production_registry.json"
    assert path.exists()
    assert len(registry.all()) == len(PRODUCTION_EVALUATOR_FAMILIES) == 4
    loaded = load_production_evaluator_registry(path)
    assert loaded is not None
    assert [item.evaluator_key for item in loaded.all()] == [
        item.evaluator_key for item in registry.all()]
    verified, detail = verify_production_registry(path)
    assert verified is True, detail


def test_production_registry_identity_is_reproducible_and_survives_reload(tmp_path):
    first = production_registry_identity()
    second = production_registry_identity()
    assert first == second and len(first) == 64
    path = tmp_path / "production_registry.json"
    write_production_evaluator_registry(path)
    reloaded = GeneratedQuestionEvaluatorRegistry(path)
    assert reloaded.registry_identity == first
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["registry_identity"] == first
    # Re-writing the same declarations is idempotent, not a new registry.
    write_production_evaluator_registry(path)
    assert GeneratedQuestionEvaluatorRegistry(path).registry_identity == first
    from research_engine.control_plane.stage4_dataset_snapshot import canonical_json

    assert canonical_json(production_registry_document()) == canonical_json(document)


def test_tampered_production_registry_fails_verification(tmp_path):
    path = tmp_path / "production_registry.json"
    write_production_evaluator_registry(path)
    assert verify_production_registry(path) == (True, "VERIFIED")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["registrations"][0]["evaluator_version"] = "v2"
    path.write_text(json.dumps(document), encoding="utf-8")
    verified, detail = verify_production_registry(path)
    assert verified is False
    assert detail.startswith("PRODUCTION_REGISTRY_INVALID")
    with pytest.raises(Exception, match="EVALUATOR_IDENTITY_DIGEST_MISMATCH"):
        GeneratedQuestionEvaluatorRegistry(path)
    # A registry that silently widens scope is likewise refused.
    write_production_evaluator_registry(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["registrations"][0]["evidence_classes"] = ["UNKNOWN"]
    document["registrations"][0]["evaluator_identity_digest"] = (
        _recomputed_digest(document["registrations"][0]))
    path.write_text(json.dumps(document), encoding="utf-8")
    verified, detail = verify_production_registry(path)
    assert verified is False and detail == "PRODUCTION_REGISTRY_IDENTITY_MISMATCH"


def _recomputed_digest(row: dict) -> str:
    """Recompute a registration digest after tampering, to prove identity checks."""
    from research_engine.v10.continuous.q71_evaluator_registry import (
        GeneratedQuestionEvaluatorRegistration,
    )

    payload = {key: value for key, value in row.items()
               if key != "evaluator_identity_digest"}
    return GeneratedQuestionEvaluatorRegistration(**payload).evaluator_identity_digest


def test_no_test_evaluator_in_production_registry(tmp_path):
    registry = _production_registry(tmp_path)
    for registration in registry.all():
        assert registration.runner_module.startswith("research_engine."), (
            registration.runner_module)
        assert "tests" not in registration.runner_module.split(".")
    document = production_registry_document()
    modules = {row["runner_module"] for row in document["registrations"]}
    assert modules == {EVALUATOR_MODULE}


def test_production_registry_cannot_contain_a_test_runner(tmp_path):
    from research_engine.v10.continuous.q71_production_registry import (
        PRODUCTION_REGISTRY_MODULE_PREFIX,
    )

    assert PRODUCTION_REGISTRY_MODULE_PREFIX == "research_engine."
    # A registration naming a test module is refused by the loader contract.
    path = tmp_path / "contaminated.json"
    registry = GeneratedQuestionEvaluatorRegistry(path)
    registry.register(GeneratedQuestionEvaluatorRegistration(
        evaluator_key="tests.fixture.evaluator",
        runner_module="tests.q71_evaluator_fixtures",
        runner_function="meaningful_mean_r_multiple",
        evaluator_version="v1",
        capability_class="TEST_ONLY",
        question_types=("COVERAGE_GAP",),
        evidence_classes=("EVENTS",),
        metric_families=(UNKNOWN,), intervention_classes=(UNKNOWN,),
    ))
    registry.save()
    with pytest.raises(ProductionEvaluatorRegistryError,
                       match="PRODUCTION_REGISTRY_CONTAINS_NON_PRODUCTION_EVALUATOR"):
        load_production_evaluator_registry(path)


# ── Step 4-6: declared scope and fail-closed resolution ──────────────────────

def _request(
    *, evidence_class: str, question_id: str = "GEN-0000000000000000",
    cell: str = "OBC-0000000000000000", explicit: str | None = None,
) -> GeneratedQuestionEvaluatorRequest:
    return GeneratedQuestionEvaluatorRequest(
        generated_question_id=question_id, question_type="COVERAGE_GAP",
        evidence_class=evidence_class, observation_cell_identity=cell,
        explicit_evaluator_key=explicit)


def test_each_production_evaluator_resolves_inside_its_declared_scope(tmp_path):
    registry = _production_registry(tmp_path)
    for family in PRODUCTION_EVALUATOR_FAMILIES:
        resolution = registry.resolve(
            _request(evidence_class=str(family["evidence_class"])))
        assert resolution.resolved, family["evidence_class"]
        assert resolution.reason_code == RESOLVED_UNIQUE_SCOPE_MATCH
        assert resolution.evaluator_key == str(family["evaluator_key"])
        assert resolution.evaluator_identity_digest
        assert resolution.required_datasets


def test_out_of_scope_question_fails_closed(tmp_path):
    registry = _production_registry(tmp_path)
    # A structurally valid but unsupported evidence class resolves to nothing.
    assert registry.resolve(
        _request(evidence_class=UNSUPPORTED_EVIDENCE_CLASS)
    ).reason_code == NO_REGISTERED_EVALUATOR
    # An unknown explicit key is refused rather than approximated.
    assert registry.resolve(_request(
        evidence_class=CURRENT_COMPLETED_SHADOW_LIFECYCLES,
        explicit="q71.expectancy.does_not_exist",
    )).reason_code == EVALUATOR_KEY_UNKNOWN
    # An explicit key used outside its declared evidence class is refused.
    assert registry.resolve(_request(
        evidence_class=UNSUPPORTED_EVIDENCE_CLASS,
        explicit="q71.expectancy.completed_shadow_lifecycle",
    )).reason_code == EVALUATOR_OUT_OF_DECLARED_SCOPE
    # A question type outside the declared scope resolves to nothing.
    outside = GeneratedQuestionEvaluatorRequest(
        generated_question_id="GEN-1", question_type="INTERACTION_GAP",
        evidence_class=CURRENT_COMPLETED_SHADOW_LIFECYCLES,
        observation_cell_identity="OBC-0000000000000000")
    assert registry.resolve(outside).reason_code == NO_REGISTERED_EVALUATOR


def test_ambiguous_production_match_fails_closed(tmp_path):
    from research_engine.experiments.q71_evidence_classes import (
        PRODUCTION_EVIDENCE_CLASSES,
    )

    declaration = PRODUCTION_EVIDENCE_CLASSES[0]
    # Two *different* runners claiming the same evidence class with equal
    # specificity: the resolver must refuse rather than choose.
    def _synthetic(key: str, runner: tuple[str, str]):
        return GeneratedQuestionEvaluatorRegistration(
            evaluator_key=key, runner_module=runner[0], runner_function=runner[1],
            evaluator_version="v1", capability_class="SYNTHETIC_AMBIGUITY_PROBE",
            question_types=("COVERAGE_GAP",),
            evidence_classes=(declaration.evidence_class,),
            metric_families=(UNKNOWN,), intervention_classes=(UNKNOWN,),
            required_datasets=declaration.governed_datasets,
        )

    ambiguous = GeneratedQuestionEvaluatorRegistry(registrations=[
        _synthetic("q71.ambiguous.one", (
            EVALUATOR_MODULE, "completed_shadow_lifecycle_expectancy")),
        _synthetic("q71.ambiguous.two", (
            "research_engine.experiments.x3_session_quality", "build_x3_report")),
    ])
    resolution = ambiguous.resolve(
        _request(evidence_class=declaration.evidence_class))
    assert resolution.resolved is False
    assert resolution.reason_code == AMBIGUOUS_EVALUATOR_MATCH
    assert set(resolution.candidates) == {
        "q71.ambiguous.one", "q71.ambiguous.two"}

    # One runner can never be bound to two ambiguous keys either.
    duplicate = GeneratedQuestionEvaluatorRegistration(
        evaluator_key="q71.expectancy.duplicate",
        runner_module=EVALUATOR_MODULE,
        runner_function="completed_shadow_lifecycle_expectancy",
        evaluator_version="v1",
        capability_class="GENERATED_QUESTION_OUTCOME_EXPECTANCY_EVALUATOR",
        question_types=("COVERAGE_GAP",),
        evidence_classes=(declaration.evidence_class,),
        metric_families=(UNKNOWN,), intervention_classes=(UNKNOWN,),
        required_datasets=declaration.governed_datasets,
    )
    with pytest.raises(Exception, match="EVALUATOR_RUNNER_BOUND_TO_TWO_KEYS"):
        GeneratedQuestionEvaluatorRegistry(
            registrations=[*production_evaluator_registrations(), duplicate])


def test_no_generic_catch_all_evaluator_exists():
    registrations = production_evaluator_registrations()
    assert registrations, "production registry must not be empty"
    for registration in registrations:
        # Exactly one evidence class each: no evaluator claims a family it does
        # not own, and none declares the UNKNOWN evidence class.
        assert len(registration.evidence_classes) == 1
        assert UNKNOWN not in registration.evidence_classes
        # An undeclared scope dimension is impossible by construction.
        for name in ("question_types", "evidence_classes", "metric_families",
                     "intervention_classes"):
            assert getattr(registration, name)
        # Scope is declared, never wildcard: no registration pins every cell.
        assert registration.observation_cells == ()
    assert len({item.evidence_classes[0] for item in registrations}) == len(
        registrations)


def test_capability_matrix_is_honest_and_denominator_is_not_narrowed():
    matrix = production_capability_matrix()
    families = structural_generated_question_families()
    assert matrix["total_structural_families"] == len(families)
    assert matrix["supported_families"] == len(PRODUCTION_EVALUATOR_FAMILIES)
    assert matrix["unsupported_families"] == len(
        unsupported_evidence_class_catalogue())
    assert matrix["supported_families"] + matrix["unsupported_families"] == (
        matrix["total_structural_families"])
    assert 0.0 < matrix["supported_fraction"] < 1.0
    assert set(matrix["unsupported_family_reasons"]) == set(
        unsupported_evidence_class_catalogue())
    # Exactly one production family is candidate-capable, and it is the governed
    # HD09 exit-policy counterfactual authority.
    assert matrix["candidate_capable_families"] == [
        "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"]
    assert {row["structural_family"] for row in matrix["families"]} == set(families)
    for row in matrix["families"]:
        if not row["evaluator_available"]:
            assert row["unsupported_reason"]
            assert row["scientific_finding_capable"] is False
            assert row["candidate_capable"] is False
            assert row["governed_intervention_policy_id"] is None
            assert row["governed_intervention_authority"] is None
        elif row["candidate_capable"]:
            # A candidate-capable family must declare a governed intervention:
            # either a static governed policy id or a structural authority that
            # resolves one from the governed policy catalogue.
            assert (row["governed_intervention_policy_id"]
                    or row["governed_intervention_authority"])
            if row["governed_intervention_authority"]:
                assert row["governed_intervention_policy_catalogue"] == (
                    "research_engine.registry.exit_policy_adjudication."
                    "CANDIDATE_POLICIES_V1")
        else:
            assert row["governed_intervention_policy_id"] is None
            assert row["governed_intervention_authority"] is None
            assert row["governed_intervention_policy_catalogue"] is None



# ── Step 9A/9B: end-to-end governed pipeline on production evaluators ────────

def test_production_evaluator_creates_a_finding_and_falsifiable_hypothesis(tmp_path):
    """blind spot -> generated question -> PRODUCTION evaluator -> finding -> hypothesis."""
    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    question_id = orchestration["new_question_ids"][0]
    assert question_id.startswith("GEN-")
    row = orchestration["generated_questions"][0]
    assert row["status"] == "QUEUED" and row["lifecycle_status"] == "QUEUED"
    assert row["evaluator"] == "q71.expectancy.completed_shadow_lifecycle"

    assert len(execution["result_ids"]) == 1
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "COMPLETE"
    assert result.scientific_status == "SCIENTIFICALLY_MEANINGFUL"
    assert result.generated_question_id == question_id
    assert result.evaluator_key == "q71.expectancy.completed_shadow_lifecycle"
    assert result.evidence_datasets == ("shadow_runtime",)
    assert result.transport_result["runner"] == (
        EVALUATOR_MODULE + ".completed_shadow_lifecycle_expectancy")
    metrics = result.governed_scientific_metrics
    assert metrics["scientifically_meaningful"] is True
    assert metrics["population"]["canonical_authority_question_id"] == "E1"
    assert metrics["sample_size"] == len(MEANINGFUL_R_MULTIPLES)

    assert bridge is not None
    assert len(bridge.findings_created) == 1
    assert len(bridge.hypotheses_created) == 1
    assert bridge.candidates_created == ()
    hypothesis = harness.registry.get_hypothesis(bridge.hypotheses_created[0])
    assert hypothesis.status == "PROPOSED"
    assert hypothesis.falsification_criteria
    assert hypothesis.source_question == question_id


def test_production_evaluator_creates_no_candidate_without_governed_intervention(
    tmp_path,
):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    # The end-to-end cycle runs the E1 expectancy family, which names no governed
    # intervention: no candidate can be created and the pipeline must say why.
    assert bridge.candidates_created == ()
    assert any("CANDIDATE_DESIGN_REQUIRED" in item
               for item in bridge.review_required)
    assert harness.registry.list_candidates() == []
    matrix = production_capability_matrix()
    # Exactly one production family is candidate-capable: the governed HD09
    # exit-policy counterfactual authority.  No other family may claim it.
    assert matrix["candidate_capable_families"] == [
        "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"]
    for row in matrix["families"]:
        if row["structural_family"] == "GOVERNED_EXIT_POLICY_COUNTERFACTUAL":
            continue
        assert row["candidate_capable"] is False
        assert row["governed_intervention_policy_id"] is None
        assert row["governed_intervention_authority"] is None
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.governed_scientific_metrics["no_governed_intervention_reason"] == (
        "NO_INTERVENTION_MAPPING")
    assert result.governed_scientific_metrics.get("governed_policy_id") is None
    projection = harness.projection(q71=orchestration, bridge=bridge)


# ── Step 9C/9D/9E: fail-closed production behaviour ─────────────────────────

def test_insufficient_evidence_declares_insufficient_data(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(
        tmp_path, r_multiples=INSUFFICIENT_R_MULTIPLES)
    assert execution["result_ids"], execution
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "INSUFFICIENT_DATA"
    assert result.scientific_status == "NOT_SCIENTIFICALLY_RESOLVED"
    metrics = result.governed_scientific_metrics
    assert metrics["scientifically_meaningful"] is False
    assert metrics["scientific_not_meaningful_reason"] == "TEST_NOT_ESTIMABLE"
    assert bridge is None or bridge.findings_created == ()
    assert harness.registry.list_hypotheses() == []
    assert harness.registry.list_candidates() == []
    question_id = orchestration["new_question_ids"][0]
    assert question_id in set(harness.execution.states())


def test_descriptive_production_evaluator_is_complete_and_creates_no_finding(
    tmp_path,
):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(
        tmp_path, evidence_class=EXIT_PATH_EVIDENCE_CLASS)
    assert orchestration["generated_questions"][0]["evaluator"] == (
        "q71.descriptive.exit_path_distribution")
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.execution_status == "COMPLETE"
    assert result.scientific_status == "DECLARED_NOT_MEANINGFUL"
    metrics = result.governed_scientific_metrics
    assert metrics["scientifically_meaningful"] is False
    assert metrics["scientific_not_meaningful_reason"] == "DESCRIPTIVE_ONLY"
    assert result.transport_result["key_metrics"]["governed_rows"] == (
        len(MEANINGFUL_R_MULTIPLES))
    assert bridge is not None
    assert bridge.findings_created == ()
    assert bridge.hypotheses_created == ()
    assert bridge.candidates_created == ()
    assert harness.scientific.document["findings"] == {}
    projection = harness.projection(q71=orchestration, bridge=bridge)
    row = projection["generated_questions"][0]
    assert row["lifecycle_status"] == "COMPLETE"
    assert row["question_answered"] is True
    # Structurally COMPLETE is not scientifically actionable.
    assert row["question_scientifically_actionable"] is False
    assert row["linked_findings"] == []


def test_unsupported_family_stays_missing_evaluator(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(
        tmp_path, evidence_class=UNSUPPORTED_EVIDENCE_CLASS)
    row = orchestration["generated_questions"][0]
    assert row["status"] == "MISSING_EVALUATOR"
    assert row["lifecycle_status"] == "MISSING_EVALUATOR"
    assert execution["result_ids"] == []
    outcome = execution["outcomes"][0]
    assert outcome["execution_status"] == "MISSING_EVALUATOR"
    assert outcome["reason_code"] == NO_REGISTERED_EVALUATOR
    assert outcome["result_id"] is None
    assert bridge is None
    projection = harness.projection(q71=orchestration)
    assert projection["generated_questions"][0]["missing_evaluator_reason"] == (
        NO_REGISTERED_EVALUATOR)
    lab = build_lab_view(projection)
    assert lab["generated_questions_missing_evaluator"]
    assert lab["generated_questions_answered"] == []
    assert lab["generated_questions_scientifically_actionable"] == []

    assert projection["candidates"] == []
    lab = build_lab_view(projection)
    assert lab["candidates"] == []
    assert lab["generated_questions"][0]["linked_candidates"] == []



# ── Step 9F/9G: evidence re-entry semantics ─────────────────────────────────

def test_changed_evidence_re_executes_and_unchanged_evidence_does_not(tmp_path):
    harness = Harness(
        tmp_path, evidence_class=CURRENT_COMPLETED_SHADOW_LIFECYCLES)
    cell = harness.cells[0]
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1",)),
    }, epoch=1)
    snapshot_id, manifests, source = harness.freeze_snapshot()
    registry = _production_registry(tmp_path)
    orchestration = harness.orchestrate(evaluator_registry=registry)
    question_id = orchestration["new_question_ids"][0]
    execution = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert len(execution["result_ids"]) == 1
    first = execution["result_ids"][0]
    first_bytes = harness.results.result_path(first).read_bytes()
    bridge1 = harness.bridge(str(execution["batch_id"]))
    assert len(bridge1.findings_created) == 1

    # 9G — identical governed evidence: no duplicate execution, no new artifact.
    orchestration2 = harness.orchestrate(evaluator_registry=registry)
    assert orchestration2["generated_questions"][0]["reentry_count"] == 0
    execution2 = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert execution2["result_ids"] == []
    assert list(harness.results.result_ids(question_id)) == [first]
    assert harness.results.result_path(first).read_bytes() == first_bytes

    # 9F — changed governed evidence re-enters and mints a NEW immutable result.
    harness.register_coverage({
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-1", "EVD-2")),
    }, epoch=2)
    orchestration3 = harness.orchestrate(evaluator_registry=registry)
    assert orchestration3["generated_questions"][0]["reentry_count"] == 1
    assert orchestration3["generated_questions"][0]["reentry_reason"] == (
        "RELEVANT_EVIDENCE_CHANGED")
    execution3 = harness.execute(
        snapshot_id=snapshot_id, manifests=manifests,
        evaluator_registry=registry, source=source)
    assert len(execution3["result_ids"]) == 1
    second = execution3["result_ids"][0]
    assert second != first
    assert list(harness.results.result_ids(question_id)) == sorted([first, second])
    # The original artifact is byte-for-byte unchanged: history is immutable.
    assert harness.results.result_path(first).read_bytes() == first_bytes
    bridge3 = harness.bridge(str(execution3["batch_id"]))
    # The scientific proposition did not change, so no scientific churn occurs.
    assert bridge3.findings_created == ()
    assert len(harness.scientific.document["findings"]) == 1



# ── Step 11/12: transport truth, canonical isolation, Lab truth ─────────────

def test_worker_transports_the_evaluators_own_scientific_metadata(tmp_path):
    """The worker carries the evaluator's declaration; it invents no science."""
    from research_engine.data_access.shadow_runtime_ingestion import (
        reconstruct_completed_shadow_trades,
    )
    from research_engine.experiments.governed_scientific_result import (
        governed_scientific_metrics,
    )
    from research_engine.experiments.q71_production_evaluators import (
        completed_shadow_lifecycle_expectancy,
    )

    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    question_id = orchestration["new_question_ids"][0]
    result = harness.results.load_result(execution["result_ids"][0])

    # Independently re-run the SAME production evaluator on the SAME governed
    # rows and compare the transported scientific metrics exactly.
    raw = _shadow_lifecycles(MEANINGFUL_R_MULTIPLES)
    report = completed_shadow_lifecycle_expectancy(
        generated_question_id=question_id,
        datasets={
            "shadow_trades": reconstruct_completed_shadow_trades(raw),
            "shadow_runtime": raw,
        })
    expected = governed_scientific_metrics(report)
    assert result.governed_scientific_metrics == expected
    assert result.governed_scientific_metrics["scientifically_meaningful"] is True
    assert result.governed_scientific_metrics["population"]["question_id"] == (
        question_id)
    # The worker cannot originate a governed intervention or validation plan.
    for absent in ("governed_policy_id", "validation_criteria",
                   "governed_policy_parameters", "treatment_component"):
        assert absent not in result.governed_scientific_metrics
    assert "scientific_result" not in result.transport_result
    assert result.transport_result["key_metrics"]["governed_rows"] == (
        len(MEANINGFUL_R_MULTIPLES))
    assert result.transport_result["runner"] == (
        EVALUATOR_MODULE + ".completed_shadow_lifecycle_expectancy")
    assert result.transport_result["runner_version"] == "v1"


def test_descriptive_transport_carries_no_invented_scientific_meaning(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(
        tmp_path, evidence_class=EXIT_PATH_EVIDENCE_CLASS)
    result = harness.results.load_result(execution["result_ids"][0])
    metrics = result.governed_scientific_metrics
    assert metrics["scientifically_meaningful"] is False
    assert metrics["scientific_not_meaningful_reason"] == "DESCRIPTIVE_ONLY"
    for absent in ("scientific_signal_type", "estimate", "population",
                   "falsification_criteria", "effect_direction"):
        assert absent not in metrics


def test_canonical_70_are_untouched_by_production_generated_research(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    assert len(BASELINE_QUESTION_IDS) == 70
    canonical = set(BASELINE_QUESTION_IDS)
    for registration in production_evaluator_registrations():
        assert registration.evaluator_key not in canonical
        assert registration.runner_module != (
            "research_engine.registry.research_question_registry")
    projection = harness.projection(q71=orchestration, bridge=bridge)
    assert len(projection["canonical_questions"]) == 70
    assert {row["question_id"] for row in projection["canonical_questions"]} == (
        canonical)
    generated_ids = {
        row["generated_question_id"] for row in projection["generated_questions"]}
    assert generated_ids and not (generated_ids & canonical)
    # Generated findings never attach to a canonical question.
    for finding in projection["findings"]:
        assert not (set(finding["source_question_ids"]) & canonical)



def test_projection_and_lab_report_production_evaluator_truth(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    question_id = orchestration["new_question_ids"][0]
    projection = harness.projection(q71=orchestration, bridge=bridge)
    row = projection["generated_questions"][0]
    assert row["generated_question_id"] == question_id
    assert row["evaluator_available"] is True
    assert row["evaluator_key"] == "q71.expectancy.completed_shadow_lifecycle"
    assert row["evaluator_version"] == "v1"
    assert row["evaluator_identity_digest"]
    assert row["lifecycle_status"] == "COMPLETE"
    assert row["question_executable"] is True
    assert row["question_answered"] is True
    assert row["question_scientifically_actionable"] is True
    assert row["scientific_status"] == "SCIENTIFICALLY_MEANINGFUL"
    assert row["execution_freshness"] == "CURRENT"
    assert row["latest_result_id"] == execution["result_ids"][0]
    assert row["linked_findings"] == [
        str(item).split(":v")[0] for item in bridge.findings_created]
    assert row["linked_hypotheses"] == list(bridge.hypotheses_created)
    assert row["linked_candidates"] == []

    lab = build_lab_view(projection)
    assert lab["generated_question_count"] == 1
    assert lab["generated_questions_executable"] == [question_id]
    assert lab["generated_questions_answered"] == [question_id]
    assert lab["generated_questions_scientifically_actionable"] == [question_id]
    assert lab["generated_questions_missing_evaluator"] == []
    assert lab["generated_questions_waiting"] == []
    lab_row = lab["generated_questions"][0]
    assert lab_row["evaluator_key"] == row["evaluator_key"]
    assert lab_row["evaluator_version"] == "v1"
    assert lab_row["execution_freshness"] == "CURRENT"
    assert lab_row["linked_findings"] == row["linked_findings"]
    assert lab_row["linked_hypotheses"] == row["linked_hypotheses"]
    assert lab["generated_question_lifecycle_counts"] == {"COMPLETE": 1}




def test_production_registry_cannot_be_widened_by_a_registration():
    """Scope is one evidence class per family; the catalogue is closed."""
    from research_engine.experiments.q71_evidence_classes import (
        ProductionEvidenceClass, ProductionEvidenceClassError,
        evidence_class_catalogue,
    )

    catalogue = evidence_class_catalogue()
    assert set(catalogue) == {
        row["evidence_class"] for row in PRODUCTION_EVALUATOR_FAMILIES}
    assert len(catalogue) == len(PRODUCTION_EVALUATOR_FAMILIES)
    # A declaration naming a dataset outside the common snapshot is refused.
    with pytest.raises(ProductionEvidenceClassError,
                       match="EVIDENCE_CLASS_DATASET_NOT_IN_SNAPSHOT"):
        ProductionEvidenceClass(
            evidence_class="INVENTED", canonical_question_id="X",
            canonical_evaluator_module="research_engine.experiments.x",
            governed_datasets=("not_a_bound_dataset",),
            observation_grain="g", estimand="e", statistical_method="m",
            minimum_evidence={"n": 1}, scientific_finding_capable=True,
            hypothesis_capable=True, candidate_capable=False,
            governed_intervention_policy_id=None, capability_class="C",
            declared_scope_note="note")
    # A declaration claiming candidate capability without a governed policy is
    # refused outright.
    with pytest.raises(
            ProductionEvidenceClassError,
            match="EVIDENCE_CLASS_CANDIDATE_REQUIRES_GOVERNED_POLICY"):
        ProductionEvidenceClass(
            evidence_class="INVENTED", canonical_question_id="X",
            canonical_evaluator_module="research_engine.experiments.x",
            governed_datasets=("shadow_runtime",),
            observation_grain="g", estimand="e", statistical_method="m",
            minimum_evidence={"n": 1}, scientific_finding_capable=True,
            hypothesis_capable=True, candidate_capable=True,
            governed_intervention_policy_id=None, capability_class="C",
            declared_scope_note="note")


def test_worker_and_orchestrator_cannot_fabricate_a_scientific_result():
    """Only an evaluator may originate RB1 scientific semantics."""
    for module in (
        Path("research_engine/v10/continuous/q71_worker.py"),
        Path("research_engine/v10/continuous/q71_orchestration.py"),
    ):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                assert name not in {"meaningful", "not_meaningful", "attach"}, (
                    module, name)


def test_production_registry_path_is_never_contaminated_by_tests(tmp_path):
    """This test module must not write to the deployed production registry."""
    from research_engine.v10.continuous.q71_evaluator_registry import (
        DEFAULT_REGISTRY_PATH,
    )

    assert DEFAULT_REGISTRY_PATH == Path(
        "data/research/continuous/q71_evaluator_registry.json")
    # Every registry this module builds lives under the test's tmp_path.
    registry = _production_registry(tmp_path)
    assert registry.path.parent == tmp_path
    assert registry.path != DEFAULT_REGISTRY_PATH
    if DEFAULT_REGISTRY_PATH.exists():
        deployed = load_production_evaluator_registry(DEFAULT_REGISTRY_PATH)
        assert deployed is not None
        assert deployed.registry_identity == production_registry_identity()
        for registration in deployed.all():
            assert registration.runner_module.startswith("research_engine.")
        # The production path never resolves an evidence class it does not own.
        assert deployed.resolve(_request(
            evidence_class=UNSUPPORTED_EVIDENCE_CLASS
        )).reason_code == NO_REGISTERED_EVALUATOR


def test_production_evaluator_functions_respect_their_declared_scope_directly():
    """Each production evaluator answers its own evidence class and fails closed."""
    from research_engine.experiments import q71_production_evaluators as evaluators
    from research_engine.experiments.governed_scientific_result import (
        governed_scientific_result,
    )

    raw = _shadow_lifecycles(MEANINGFUL_R_MULTIPLES)
    datasets = {"shadow_runtime": raw}

    expectancy = governed_scientific_result(
        evaluators.completed_shadow_lifecycle_expectancy(
            generated_question_id="GEN-TEST", datasets=datasets))
    assert expectancy is not None and expectancy.scientifically_meaningful
    assert expectancy.question_id == "GEN-TEST"

    descriptive = governed_scientific_result(
        evaluators.governed_exit_path_distribution(
            generated_question_id="GEN-TEST", datasets=datasets))
    assert descriptive is not None
    assert descriptive.scientifically_meaningful is False
    assert descriptive.not_meaningful_reason == "DESCRIPTIVE_ONLY"

    # The execution-quality family fails closed when its governed evidence is
    # absent rather than inventing a result.
    slippage = governed_scientific_result(
        evaluators.session_conditioned_execution_slippage(
            generated_question_id="GEN-TEST", datasets={}))
    assert slippage is not None
    assert slippage.scientifically_meaningful is False
    assert slippage.not_meaningful_reason == "INSUFFICIENT_GOVERNED_EVIDENCE"

    # An empty governed population is not estimable, never a positive finding.
    empty = governed_scientific_result(
        evaluators.completed_shadow_lifecycle_expectancy(
            generated_question_id="GEN-TEST", datasets={}))
    assert empty is not None and empty.scientifically_meaningful is False
    assert empty.not_meaningful_reason == "TEST_NOT_ESTIMABLE"

    # The candidate-capable counterfactual family fails closed when no frozen
    # governed counterfactual evidence is admitted, and never names a policy.
    counterfactual = governed_scientific_result(
        evaluators.governed_exit_policy_counterfactual(
            generated_question_id="GEN-TEST", datasets=datasets))
    assert counterfactual is not None
    assert counterfactual.scientifically_meaningful is False
    assert counterfactual.not_meaningful_reason == "INSUFFICIENT_GOVERNED_EVIDENCE"
    assert counterfactual.candidate_design is None
    assert counterfactual.no_intervention_reason is None
    report = evaluators.governed_exit_policy_counterfactual(
        generated_question_id="GEN-TEST", datasets=datasets)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["provenance"]["fail_closed_reason"] == (
        "MISSING_COUNTERFACTUAL_EVIDENCE")

def test_no_live_approval_anywhere_in_the_production_pipeline(tmp_path):
    harness, orchestration, execution, bridge, registry = _run_full_cycle(tmp_path)
    projection = harness.projection(q71=orchestration, bridge=bridge)
    for row in projection["candidates"]:
        assert row["live_approved"] is False
        assert row["promotion_action"] in (None, "HUMAN_REVIEW_REQUIRED")
    for candidate in harness.registry.list_candidates():
        assert candidate.shadow_binding.get("live_approved", False) is False
    lab = build_lab_view(projection)
    assert all(row.get("live_approved") is False for row in lab["candidates"])


# ── Step 12: no coverage theatre, no bypass, no test contamination ──────────

def test_production_modules_never_write_candidates_or_findings_directly():
    """No production Q71 module may reach a candidate/finding writer."""
    modules = (
        Path("research_engine/experiments/q71_production_evaluators.py"),
        Path("research_engine/experiments/q71_evidence_classes.py"),
        Path("research_engine/v10/continuous/q71_production_registry.py"),
        Path("research_engine/control_plane/governed_counterfactual_evidence.py"),
    )
    forbidden_import_fragments = (
        "optimisation_registry", "scientific_state_store", "scientific_state_bridge",
        "generated_question_result", "research_projection",
    )
    forbidden_calls = {
        "create_candidate", "add_candidate", "register_candidate", "save_candidate",
        "create_finding", "add_finding", "write_finding",
    }
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = str(node.module or "")
                assert not any(fragment in target
                               for fragment in forbidden_import_fragments), (
                    module, target)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(fragment in alias.name
                                   for fragment in forbidden_import_fragments), (
                        module, alias.name)
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                assert name not in forbidden_calls, (module, name)



def test_execution_quality_family_resolves_and_fails_closed_in_the_pipeline(
    tmp_path,
):
    """The X3 family resolves and executes automatically, and fails closed.

    The frozen fixture carries only generic execution rows, so the governed
    X3 evidence attestation cannot be built and the evaluator must declare
    INSUFFICIENT_GOVERNED_EVIDENCE rather than invent a slippage result.
    """
    harness, orchestration, execution, bridge, registry = _run_full_cycle(
        tmp_path,
        evidence_class=(
            SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE))
    row = orchestration["generated_questions"][0]
    assert row["evaluator"] == "q71.execution.session_conditioned_slippage"
    assert row["status"] == "QUEUED"
    assert execution["outcomes"][0]["execution_status"] == "INSUFFICIENT_DATA"
    result = harness.results.load_result(execution["result_ids"][0])
    assert result.evidence_datasets == ("execution_context", "execution_results")
    assert result.scientific_status == "NOT_SCIENTIFICALLY_RESOLVED"
    assert result.governed_scientific_metrics[
        "scientific_not_meaningful_reason"] == "INSUFFICIENT_GOVERNED_EVIDENCE"
    assert bridge is None or bridge.findings_created == ()
    assert harness.registry.list_hypotheses() == []
