"""
Continuous Research Operation.

Orchestrates the complete research lifecycle as a repeatable,
governed, resumable cycle that composes existing Items 8-11.

Components:
    - orchestrator: ContinuousResearchOrchestrator
    - state: Persistent cycle state
    - trigger: Data readiness / change detection
"""

from research_engine.v10.continuous.orchestrator import ContinuousResearchOrchestrator
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierCycleResult,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.canonical_question_cycle import (
    CanonicalQuestionCycleError,
    run_canonical_question_cycle,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    ScientificStateBridgeError,
    ScientificStateBridgeResult,
    run_generated_scientific_bridge,
    run_scientific_state_bridge,
)
from research_engine.v10.continuous.generated_question_lifecycle import (
    LIFECYCLE_STATES,
)
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore,
    GeneratedQuestionResult,
    GeneratedQuestionResultStore,
    work_item_identity,
)
from research_engine.v10.continuous.q71_evaluator_registry import (
    GeneratedQuestionEvaluatorRegistration,
    GeneratedQuestionEvaluatorRegistry,
    GeneratedQuestionEvaluatorRegistryError,
    GeneratedQuestionEvaluatorRequest,
    GeneratedQuestionEvaluatorResolution,
)
from research_engine.v10.continuous.q71_worker import (
    GeneratedExecutionPolicy,
    GeneratedQuestionWorkerError,
    run_generated_question_worker,
)
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
from research_engine.v10.continuous.production_observation_space import (
    ObservationSpaceSnapshot,
    ObservationSpaceSnapshotStore,
    ProductionObservationPolicy,
    ProductionObservationSpaceError,
    governed_observation_space,
    materialize_observation_space,
    production_observation_policy,
)
from research_engine.v10.continuous.production_coverage import (
    NO_PRODUCTION_OBSERVATION_SPACE,
    ProductionCoverageMaterialization,
    ProductionCoverageSnapshot,
    ProductionCoverageSnapshotStore,
    coverage_surface,
    materialize_production_coverage,
    q71_coverage_source_mapping,
)
from research_engine.v10.continuous.state import CycleState, CycleStatus, TriggerStatus

__all__ = [
    "ContinuousResearchOrchestrator",
    "CanonicalQuestionCycleError",
    "GeneratedExecutionPolicy",
    "NO_PRODUCTION_OBSERVATION_SPACE",
    "ObservationSpaceSnapshot",
    "ObservationSpaceSnapshotStore",
    "ProductionCoverageMaterialization",
    "ProductionCoverageSnapshot",
    "ProductionCoverageSnapshotStore",
    "ProductionObservationPolicy",
    "ProductionObservationSpaceError",
    "coverage_surface",
    "governed_observation_space",
    "materialize_observation_space",
    "materialize_production_coverage",
    "production_observation_policy",
    "q71_coverage_source_mapping",
    "GeneratedQuestionEvaluatorRegistration",
    "GeneratedQuestionEvaluatorRegistry",
    "GeneratedQuestionEvaluatorRegistryError",
    "GeneratedQuestionEvaluatorRequest",
    "GeneratedQuestionEvaluatorResolution",
    "GeneratedQuestionExecutionStore",
    "GeneratedQuestionResult",
    "GeneratedQuestionResultStore",
    "GeneratedQuestionWorkerError",
    "LIFECYCLE_STATES",
    "PRODUCTION_EVALUATOR_FAMILIES",
    "ProductionEvaluatorRegistryError",
    "ScientificStateBridgeError",
    "ScientificStateBridgeResult",
    "FrontierCycleResult",
    "CycleState",
    "CycleStatus",
    "TriggerStatus",
    "build_production_evaluator_registry",
    "load_production_evaluator_registry",
    "production_capability_matrix",
    "production_evaluator_registrations",
    "production_registry_document",
    "production_registry_identity",
    "run_frontier_snapshot_cycle",
    "run_canonical_question_cycle",
    "run_generated_question_worker",
    "run_generated_scientific_bridge",
    "run_scientific_state_bridge",
    "verify_production_registry",
    "work_item_identity",
    "write_production_evaluator_registry",
]
