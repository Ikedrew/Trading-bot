"""Governed production coverage snapshots for autonomous Q71+ research.

``production_observation_space`` materializes *what populations exist*.  This
module turns that into *what is already covered, what is waiting, what has no
evaluator, and what is a genuine structural blind spot*, and persists the result
as an immutable governed artifact that the continuous loop hands to
``run_q71_orchestration`` through the ordinary ``ResearchCoverageStore``.

Authority rules enforced here:

* Coverage evidence is derived from real governed research state only: the
  canonical question cycle's own persisted results (``QRESULT-*`` identities),
  the governed deep-work queue's own job identities, and the real dataset
  population identities of the frozen investigation snapshot.  Nothing is
  inferred from wall time and nothing is invented.
* Research performed against a *different* evidence frontier does not cover the
  current frontier.  The cell is reported as ``STALE_FRONTIER`` instead of being
  silently treated as resolved.
* A cell is a blind spot only when the governed coverage machinery says so
  (``CoverageSnapshot.blind_spots``) and the governed curiosity admission
  (``admit_coverage_curiosity``) would mint a question for it.  Wording is never
  compared, novelty is never scored and no LLM decides anything.
* Every declared structural family is accounted for exactly once, so the
  denominator of a coverage claim can never be narrowed to look better.
* Conservation is proven on explicit orthogonal axes; a violation raises instead
  of publishing an incomplete snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.q71_evidence_classes import (
    PRODUCTION_EVIDENCE_CLASSES,
    evidence_class_identity,
    structural_generated_question_families,
)
from research_engine.lifecycle.generated_research_isolation import (
    canonical_inventory,
)
from research_engine.lifecycle.research_coverage import (
    CoverageEvidence,
    CoverageSnapshot,
    CoverageState,
    ResearchInventoryBoundary,
    admit_coverage_curiosity,
)
from research_engine.lifecycle.research_coverage_store import (
    ResearchCoverageStore,
)
from research_engine.v10.investigation_snapshot import MANIFEST_DIRECTORY

from research_engine.v10.continuous.production_observation_space import (
    CANONICAL_QUESTION_MAPPING_VERSION,
    EXCLUDED_BY_POLICY,
    MISSING_EVIDENCE,
    MISSING_EVALUATOR,
    PRODUCER_VERSION,
    Q71_MAPPING_VERSION,
    STALE_FRONTIER,
    SUPERSEDED,
    ObservationSpaceSnapshot,
    ObservationSpaceSnapshotStore,
    ProductionCellBinding,
    ProductionObservationPolicy,
    ProductionObservationSpaceIdentityConflict,
    ProductionObservationSpaceValidationError,
    _atomic_json,
    _digest,
    _immutable_json,
    _is,
    _is_sha256,
    materialize_observation_space,
    production_observation_policy,
)


PRODUCTION_COVERAGE_SNAPSHOT_SCHEMA = "production_coverage_snapshot_v1"
PRODUCTION_COVERAGE_SNAPSHOT_ID_PREFIX = "PCVS-"
DEFAULT_PRODUCTION_COVERAGE_DIRECTORY = Path(
    "data/research/continuous/production_coverage")
#: The governed store the continuous loop hands to ``run_q71_orchestration``.
DEFAULT_PRODUCTION_COVERAGE_STORE_PATH = Path(
    "data/research/continuous/research_coverage.json")

NO_PRODUCTION_OBSERVATION_SPACE = "NO_PRODUCTION_OBSERVATION_SPACE"

#: Canonical question result statuses that resolve a cell's science.
RESOLVED_RESULT_STATUSES = frozenset({"COMPLETE", "NEGATIVE_RESULT"})
#: A superseded alias owns no independent science and never covers a cell.
SUPERSEDED_RESULT_STATUSES = frozenset({"ALIAS_OR_SUPERSEDED"})

EVALUATOR_RESOLVED = "EVALUATOR_RESOLVED"


def production_coverage_snapshot_identity_for(value: str) -> str:
    if not _is_sha256(value):
        raise ProductionObservationSpaceValidationError(
            "semantic identity must be sha256 hex")
    return PRODUCTION_COVERAGE_SNAPSHOT_ID_PREFIX + value[:16].upper()


def is_production_coverage_snapshot_identity(value: Any) -> bool:
    return _is(PRODUCTION_COVERAGE_SNAPSHOT_ID_PREFIX, value)


# ── Real governed research state ────────────────────────────────────────────
@dataclass(frozen=True)
class CanonicalResearchState:
    """The real governed research state of one canonical question."""

    question_id: str
    result_id: str = ""
    status: str = ""
    snapshot_id: str = ""
    evaluation_identity_digest: str = ""
    sample_n: int | None = None
    active_job_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id, "result_id": self.result_id,
            "status": self.status, "snapshot_id": self.snapshot_id,
            "evaluation_identity_digest": self.evaluation_identity_digest,
            "sample_n": self.sample_n,
            "active_job_ids": list(self.active_job_ids),
        }


def canonical_research_states(
    question_projection: Mapping[str, Any] | None,
    *, active_research_by_question: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, CanonicalResearchState]:
    """Read real persisted canonical research state; never infer it."""
    active = {
        str(key): tuple(sorted({str(item) for item in value}))
        for key, value in dict(active_research_by_question or {}).items()
    }
    rows = dict((question_projection or {}).get("questions") or {})
    states: dict[str, CanonicalResearchState] = {}
    for question_id in canonical_inventory():
        raw = rows.get(question_id)
        raw = dict(raw) if isinstance(raw, Mapping) else {}
        result = raw.get("result")
        result = dict(result) if isinstance(result, Mapping) else {}
        sample = result.get("sample_n")
        states[question_id] = CanonicalResearchState(
            question_id=question_id,
            result_id=str(result.get("result_id") or ""),
            status=str(result.get("status") or raw.get("status") or ""),
            snapshot_id=str(result.get("snapshot_id")
                            or raw.get("cycle_snapshot_id") or ""),
            evaluation_identity_digest=str(
                result.get("evaluation_identity_digest") or ""),
            sample_n=(None if sample is None else int(sample)),
            active_job_ids=tuple(active.get(question_id, ())),
        )
    return states


# ── Coverage evidence per cell ──────────────────────────────────────────────
@dataclass(frozen=True)
class ProductionCoverageDecision:
    """Why one governed cell is in the coverage state it is in."""

    cell_identity: str
    evidence_class: str
    canonical_question_ids: tuple[str, ...]
    coverage_state: str
    blind_spot: bool
    blind_spot_class: str | None
    blind_spot_identity: str | None
    question_minted: bool
    q71_eligible: bool
    coverage_reason: str
    evaluator_key: str | None
    evaluator_verdict: str
    expected_question_status: str
    expected_question_reason: str | None
    candidate_capable: bool
    scientific_finding_capable: bool
    hypothesis_capable: bool
    evidence_volume: int
    evidence_freshness: str
    fail_closed_reasons: tuple[str, ...]
    source_observation_cell_id: str
    coverage_snapshot_identity: str
    existing_generated_question_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_identity": self.cell_identity,
            "evidence_class": self.evidence_class,
            "canonical_question_ids": list(self.canonical_question_ids),
            "coverage_state": self.coverage_state,
            "blind_spot": self.blind_spot,
            "blind_spot_class": self.blind_spot_class,
            "blind_spot_identity": self.blind_spot_identity,
            "question_minted": self.question_minted,
            "q71_eligible": self.q71_eligible,
            "coverage_reason": self.coverage_reason,
            "evaluator_key": self.evaluator_key,
            "evaluator_verdict": self.evaluator_verdict,
            "expected_question_status": self.expected_question_status,
            "expected_question_reason": self.expected_question_reason,
            "candidate_capable": self.candidate_capable,
            "scientific_finding_capable": self.scientific_finding_capable,
            "hypothesis_capable": self.hypothesis_capable,
            "evidence_volume": self.evidence_volume,
            "evidence_freshness": self.evidence_freshness,
            "fail_closed_reasons": list(self.fail_closed_reasons),
            "source_observation_cell_id": self.source_observation_cell_id,
            "coverage_snapshot_identity": self.coverage_snapshot_identity,
            "existing_generated_question_id": self.existing_generated_question_id,
        }



def _declaration_for(evidence_class: str) -> Any:
    return next((item for item in PRODUCTION_EVIDENCE_CLASSES
                 if item.evidence_class == evidence_class), None)


def coverage_evidence_for_cell(
    binding: ProductionCellBinding,
    state: CanonicalResearchState | None,
    *, frontier_snapshot_id: str, policy: ProductionObservationPolicy,
    evaluator_key: str | None,
) -> tuple[CoverageEvidence, tuple[str, ...]]:
    """Derive one cell's governed coverage evidence from real state.

    Returns the governed evidence plus the fail-closed reason codes observed for
    the cell.  A cell whose population carries no rows is an evidence gap, which
    the policy declares explicitly; it is never turned into a fabricated
    observation.
    """
    reasons: list[str] = []
    evidence_refs: tuple[str, ...] = ()
    if binding.observed:
        evidence_refs = binding.evidence_identities
    else:
        reasons.append(MISSING_EVIDENCE)
    research_attempt_refs: tuple[str, ...] = ()
    conclusion_refs: tuple[str, ...] = ()
    blocked = False
    if state is not None and state.result_id:
        if state.snapshot_id and state.snapshot_id != frontier_snapshot_id:
            # Research against a different frontier does not cover this one.
            reasons.append(STALE_FRONTIER)
        elif state.status in SUPERSEDED_RESULT_STATUSES:
            reasons.append(SUPERSEDED)
        elif state.status in RESOLVED_RESULT_STATUSES:
            conclusion_refs = (state.result_id,)
        else:
            research_attempt_refs = (state.result_id,)
            blocked = state.status == "BLOCKED"
    excluded = bool(
        binding.evidence_class in set(policy.exclusions)
        or (not binding.observed and not policy.mint_question_for_absent_evidence)
        or (evaluator_key is None
            and not policy.mint_question_for_missing_evaluator))
    if excluded:
        reasons.append(EXCLUDED_BY_POLICY)
    return CoverageEvidence(
        evidence_refs=evidence_refs,
        question_refs=binding.canonical_question_ids,
        research_attempt_refs=research_attempt_refs,
        active_research_refs=tuple(state.active_job_ids) if state else (),
        conclusion_refs=conclusion_refs,
        blocked=blocked,
        structurally_formulable=True,
        curiosity_excluded=excluded,
    ), tuple(sorted(set(reasons)))


def _expected_question_status(
    coverage: Any, evaluator_key: str | None,
) -> tuple[str, str | None]:
    """Mirror the governed orchestration status derivation for reporting.

    The orchestration remains the single authority that *persists* a question
    status; this projection exists so the Lab can state the expected status
    before the question is minted.  Both read the same governed coverage row.
    """
    evidence = coverage.evidence
    if evidence.conclusion_refs or evidence.applicable_memory_refs:
        return "RETIRED", "RESEARCH_GAP_RESOLVED"
    if evidence.active_research_refs:
        return "ACTIVE", "RESEARCH_ALREADY_ACTIVE"
    if coverage.state.value in {"BLOCKED", "NOT_OBSERVABLE"}:
        return "WAITING_FOR_DATA", coverage.state.value
    if not evidence.evidence_refs:
        return "WAITING_FOR_DATA", "EVIDENCE_UNAVAILABLE"
    if evaluator_key is None:
        return "MISSING_EVALUATOR", "SNAPSHOT_BOUND_EVALUATOR_UNAVAILABLE"
    return "READY", None


def _evaluator_index(registry: Any) -> dict[str, str]:
    if registry is None:
        return {}
    capability = getattr(registry, "capability_index", None)
    if callable(capability):
        return dict(capability())
    if isinstance(registry, Mapping):
        return dict(registry)
    return {}


def _evaluator_key(
    index: Mapping[str, str], cell: Any, binding: ProductionCellBinding,
) -> str | None:
    for key in (cell.subject_identity, binding.cell_identity,
                binding.evidence_class):
        value = index.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None



# ── Canonical 70 coverage accounting (never an identity change) ─────────────
@dataclass(frozen=True)
class CanonicalQuestionCoverage:
    """One canonical question's structural coverage mapping.

    This is accounting only.  It never alters a canonical question's identity,
    definition, order or scientific meaning, and it never re-states a result.
    """

    question_id: str
    covered_cell_ids: tuple[str, ...]
    evidence_classes: tuple[str, ...]
    research_status: str
    result_is_current: bool
    latest_result_id: str
    coverage_states: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "covered_cell_ids": list(self.covered_cell_ids),
            "evidence_classes": list(self.evidence_classes),
            "research_status": self.research_status,
            "result_is_current": self.result_is_current,
            "latest_result_id": self.latest_result_id,
            "coverage_states": list(self.coverage_states),
        }


def canonical_question_coverage_mapping(
    coverage_snapshot: CoverageSnapshot,
    bindings: Sequence[ProductionCellBinding],
    states: Mapping[str, CanonicalResearchState],
    *, frontier_snapshot_id: str,
) -> tuple[CanonicalQuestionCoverage, ...]:
    """Map the canonical 70 onto the observation space in frozen registry order."""
    by_cell = {row.cell_identity: row for row in coverage_snapshot.classifications}
    cells_by_question: dict[str, list[ProductionCellBinding]] = {}
    for binding in bindings:
        for question_id in binding.canonical_question_ids:
            cells_by_question.setdefault(question_id, []).append(binding)
    out: list[CanonicalQuestionCoverage] = []
    for question_id in canonical_inventory():
        members = cells_by_question.get(question_id, [])
        state = states.get(question_id)
        current = bool(
            state and state.result_id and state.snapshot_id
            and state.snapshot_id == frontier_snapshot_id)
        out.append(CanonicalQuestionCoverage(
            question_id=question_id,
            covered_cell_ids=tuple(sorted(
                item.cell_identity for item in members)),
            evidence_classes=tuple(sorted({
                item.evidence_class for item in members})),
            research_status=str(state.status if state else ""),
            result_is_current=current,
            latest_result_id=str(state.result_id if state else ""),
            coverage_states=tuple(sorted({
                by_cell[item.cell_identity].state.value for item in members
                if item.cell_identity in by_cell})),
        ))
    if tuple(item.question_id for item in out) != tuple(canonical_inventory()):
        raise ProductionObservationSpaceValidationError(
            "canonical question mapping altered registry order")
    return tuple(out)



# ── Conservation / accounting (Step 5) ──────────────────────────────────────
def coverage_conservation(
    coverage_snapshot: CoverageSnapshot,
    bindings: Sequence[ProductionCellBinding],
    decisions: Sequence[ProductionCoverageDecision],
    family_decisions: Sequence[Any],
) -> dict[str, Any]:
    """Prove the coverage snapshot cannot silently lose or duplicate cells.

    Four orthogonal axes are proven independently.  Each axis is a partition of
    its own total, so a cell can never be dropped by moving between states, and
    a blind spot can never exist without a source observation cell.
    """
    rows = coverage_snapshot.classifications
    cells = coverage_snapshot.observation_space.cells
    total = len(cells)
    state_counts: dict[str, int] = {state.value: 0 for state in CoverageState}
    for row in rows:
        state_counts[row.state.value] += 1
    state_total = sum(state_counts.values())
    duplicate_cells = sorted(
        {cell.cell_identity for cell in cells
         if [item.cell_identity for item in cells].count(cell.cell_identity) > 1})
    binding_identities = [item.cell_identity for item in bindings]
    duplicate_bindings = sorted(
        {identity for identity in binding_identities
         if binding_identities.count(identity) > 1})
    unbound = sorted({cell.cell_identity for cell in cells}
                     - set(binding_identities))
    resolved = sum(1 for item in decisions
                   if item.evaluator_verdict == EVALUATOR_RESOLVED)
    missing = sum(1 for item in decisions
                  if item.evaluator_verdict == MISSING_EVALUATOR)
    blind = [item for item in decisions if item.blind_spot]
    unexplained = sorted(
        item.cell_identity for item in decisions
        if not item.coverage_state or not item.coverage_reason)
    family_total = len(family_decisions)
    families_materialized = sum(1 for item in family_decisions if item.materialized)
    families_unaccounted = sorted(
        item.structural_family for item in family_decisions
        if not item.materialized and not item.reason_code)
    axis_state = {
        "total": total, "partition_total": state_total,
        "states": dict(sorted(state_counts.items())),
        "conserved": state_total == total,
    }
    axis_evaluator = {
        "total": len(decisions), "resolved": resolved,
        "missing_evaluator": missing,
        "conserved": (resolved + missing) == len(decisions) == total,
    }
    axis_family = {
        "total": family_total, "materialized": families_materialized,
        "not_materialized": family_total - families_materialized,
        "conserved": (family_total == len(
            structural_generated_question_families())
            and not families_unaccounted),
    }
    axis_blind_spot = {
        "total": len(blind),
        "with_source_cell": sum(
            1 for item in blind
            if item.source_observation_cell_id in {c.cell_identity for c in cells}),
        "conserved": all(
            item.source_observation_cell_id in {c.cell_identity for c in cells}
            for item in blind),
    }
    conserved = bool(
        axis_state["conserved"] and axis_evaluator["conserved"]
        and axis_family["conserved"] and axis_blind_spot["conserved"]
        and not duplicate_cells and not duplicate_bindings and not unbound
        and not unexplained and not families_unaccounted)
    return {
        "total_governed_observation_cells": total,
        "governed_partition": dict(sorted(state_counts.items())),
        "axis_coverage_state": axis_state,
        "axis_evaluator_capability": axis_evaluator,
        "axis_structural_family": axis_family,
        "axis_blind_spot_source": axis_blind_spot,
        "duplicate_cell_identities": duplicate_cells,
        "duplicate_bindings": duplicate_bindings,
        "unbound_cells": unbound,
        "unexplained_cells": unexplained,
        "families_without_reason": families_unaccounted,
        "conserved": conserved,
    }


def assert_coverage_conservation(report: Mapping[str, Any]) -> None:
    """Raise unless every conservation axis holds."""
    if not report.get("conserved"):
        raise ProductionObservationSpaceValidationError(
            "COVERAGE_CONSERVATION_VIOLATED:"
            + canonical_json({
                key: value for key, value in report.items()
                if key.startswith("duplicate") or key.startswith("unbound")
                or key.startswith("unexplained")
                or key.startswith("families_without")
                or key.startswith("axis")}))



# ── Production coverage snapshot (immutable artifact) ───────────────────────
@dataclass(frozen=True)
class ProductionCoverageSnapshot:
    observation_space_snapshot: ObservationSpaceSnapshot
    coverage_snapshot: CoverageSnapshot
    decisions: tuple[ProductionCoverageDecision, ...]
    canonical_question_mapping: tuple[CanonicalQuestionCoverage, ...]
    conservation: Mapping[str, Any]
    evaluator_registry_identity: str
    evidence_catalogue_identity: str
    canonical_question_mapping_version: str
    q71_mapping_version: str
    producer_version: str
    created_at: str
    semantic_identity: str
    production_coverage_snapshot_id: str

    @classmethod
    def construct(
        cls, *, observation_space_snapshot: ObservationSpaceSnapshot,
        coverage_snapshot: CoverageSnapshot,
        decisions: Sequence[ProductionCoverageDecision],
        canonical_question_mapping: Sequence[CanonicalQuestionCoverage],
        conservation: Mapping[str, Any], created_at: str = "",
    ) -> "ProductionCoverageSnapshot":
        material = {
            "kind": "production_coverage_snapshot",
            "schema_version": PRODUCTION_COVERAGE_SNAPSHOT_SCHEMA,
            "producer_version": PRODUCER_VERSION,
            "observation_space_snapshot_id":
                observation_space_snapshot.observation_space_snapshot_id,
            "observation_space_identity":
                observation_space_snapshot.observation_space
                .observation_space_identity,
            "coverage_snapshot_identity":
                coverage_snapshot.coverage_snapshot_identity,
            "inventory_identity": coverage_snapshot.inventory.inventory_identity,
            "evidence_fingerprint":
                coverage_snapshot.inventory.evidence_fingerprint,
            "decisions": [item.to_dict() for item in decisions],
            "canonical_question_mapping": [
                item.to_dict() for item in canonical_question_mapping],
            "canonical_question_mapping_version":
                CANONICAL_QUESTION_MAPPING_VERSION,
            "q71_mapping_version": Q71_MAPPING_VERSION,
            "evaluator_registry_identity":
                observation_space_snapshot.evaluator_registry_identity,
            "evidence_catalogue_identity": evidence_class_identity(),
        }
        semantic = _digest(material)
        return cls(
            observation_space_snapshot=observation_space_snapshot,
            coverage_snapshot=coverage_snapshot, decisions=tuple(decisions),
            canonical_question_mapping=tuple(canonical_question_mapping),
            conservation=dict(conservation),
            evaluator_registry_identity=(
                observation_space_snapshot.evaluator_registry_identity),
            evidence_catalogue_identity=evidence_class_identity(),
            canonical_question_mapping_version=CANONICAL_QUESTION_MAPPING_VERSION,
            q71_mapping_version=Q71_MAPPING_VERSION,
            producer_version=PRODUCER_VERSION, created_at=str(created_at),
            semantic_identity=semantic,
            production_coverage_snapshot_id=(
                production_coverage_snapshot_identity_for(semantic)),
        )

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "production_coverage_snapshot",
            "schema_version": PRODUCTION_COVERAGE_SNAPSHOT_SCHEMA,
            "producer_version": self.producer_version,
            "observation_space_snapshot_id":
                self.observation_space_snapshot.observation_space_snapshot_id,
            "observation_space_identity":
                self.observation_space_snapshot.observation_space
                .observation_space_identity,
            "coverage_snapshot_identity":
                self.coverage_snapshot.coverage_snapshot_identity,
            "inventory_identity":
                self.coverage_snapshot.inventory.inventory_identity,
            "evidence_fingerprint":
                self.coverage_snapshot.inventory.evidence_fingerprint,
            "decisions": [item.to_dict() for item in self.decisions],
            "canonical_question_mapping": [
                item.to_dict() for item in self.canonical_question_mapping],
            "canonical_question_mapping_version":
                self.canonical_question_mapping_version,
            "q71_mapping_version": self.q71_mapping_version,
            "evaluator_registry_identity": self.evaluator_registry_identity,
            "evidence_catalogue_identity": self.evidence_catalogue_identity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "observation_space": self.observation_space_snapshot.to_dict(),
            "coverage": self.coverage_snapshot.to_dict(),
            "conservation": dict(self.conservation),
            "cell_count": len(self.coverage_snapshot.observation_space.cells),
            "frontier_snapshot_id":
                self.observation_space_snapshot.frontier_snapshot_id,
            "frontier_fingerprint":
                self.observation_space_snapshot.frontier_fingerprint,
            "investigation_epoch":
                self.observation_space_snapshot.investigation_epoch,
            "created_at": self.created_at,
            "digest": self.semantic_identity,
            "semantic_identity": self.semantic_identity,
            "production_coverage_snapshot_id":
                self.production_coverage_snapshot_id,
        }

    @property
    def cell_count(self) -> int:
        return len(self.coverage_snapshot.observation_space.cells)



def _decision_from_dict(value: Mapping[str, Any]) -> ProductionCoverageDecision:
    return ProductionCoverageDecision(
        cell_identity=str(value["cell_identity"]),
        evidence_class=str(value["evidence_class"]),
        canonical_question_ids=tuple(
            str(x) for x in value["canonical_question_ids"]),
        coverage_state=str(value["coverage_state"]),
        blind_spot=bool(value["blind_spot"]),
        blind_spot_class=(None if value.get("blind_spot_class") is None
                          else str(value["blind_spot_class"])),
        blind_spot_identity=(None if value.get("blind_spot_identity") is None
                             else str(value["blind_spot_identity"])),
        question_minted=bool(value["question_minted"]),
        q71_eligible=bool(value["q71_eligible"]),
        coverage_reason=str(value["coverage_reason"]),
        evaluator_key=(None if value.get("evaluator_key") is None
                       else str(value["evaluator_key"])),
        evaluator_verdict=str(value["evaluator_verdict"]),
        expected_question_status=str(value["expected_question_status"]),
        expected_question_reason=(
            None if value.get("expected_question_reason") is None
            else str(value["expected_question_reason"])),
        candidate_capable=bool(value["candidate_capable"]),
        scientific_finding_capable=bool(value["scientific_finding_capable"]),
        hypothesis_capable=bool(value["hypothesis_capable"]),
        evidence_volume=int(value["evidence_volume"]),
        evidence_freshness=str(value["evidence_freshness"]),
        fail_closed_reasons=tuple(
            str(x) for x in value["fail_closed_reasons"]),
        source_observation_cell_id=str(value["source_observation_cell_id"]),
        coverage_snapshot_identity=str(value["coverage_snapshot_identity"]),
        existing_generated_question_id=(
            None if value.get("existing_generated_question_id") is None
            else str(value["existing_generated_question_id"])),
    )


def _mapping_from_dict(value: Mapping[str, Any]) -> CanonicalQuestionCoverage:
    return CanonicalQuestionCoverage(
        question_id=str(value["question_id"]),
        covered_cell_ids=tuple(str(x) for x in value["covered_cell_ids"]),
        evidence_classes=tuple(str(x) for x in value["evidence_classes"]),
        research_status=str(value["research_status"]),
        result_is_current=bool(value["result_is_current"]),
        latest_result_id=str(value["latest_result_id"]),
        coverage_states=tuple(str(x) for x in value["coverage_states"]),
    )


def production_coverage_snapshot_from_dict(
    value: Mapping[str, Any],
) -> ProductionCoverageSnapshot:
    """Rebuild and re-verify a persisted production coverage artifact."""
    if not isinstance(value, Mapping):
        raise ProductionObservationSpaceValidationError(
            "production coverage artifact must be a mapping")
    space = ObservationSpaceSnapshot.from_dict(value["observation_space"])
    coverage = CoverageSnapshot.from_dict(value["coverage"])
    rebuilt = ProductionCoverageSnapshot.construct(
        observation_space_snapshot=space, coverage_snapshot=coverage,
        decisions=[_decision_from_dict(item) for item in value["decisions"]],
        canonical_question_mapping=[
            _mapping_from_dict(item)
            for item in value["canonical_question_mapping"]],
        conservation=dict(value["conservation"]),
        created_at=str(value["created_at"]),
    )
    if (value["semantic_identity"] != rebuilt.semantic_identity
            or value["production_coverage_snapshot_id"]
            != rebuilt.production_coverage_snapshot_id
            or int(value["cell_count"]) != rebuilt.cell_count):
        raise ProductionObservationSpaceIdentityConflict(
            "production coverage artifact identity mismatch")
    return rebuilt



class ProductionCoverageSnapshotStore:
    """Append-only store of immutable production coverage artifacts."""

    def __init__(
        self, directory: Path | str = DEFAULT_PRODUCTION_COVERAGE_DIRECTORY,
    ) -> None:
        self.directory = Path(directory)
        self.snapshots_directory = self.directory / "snapshots"
        self.latest_path = self.directory / "latest_coverage.json"

    def path_for(self, snapshot_id: str) -> Path:
        if not is_production_coverage_snapshot_identity(snapshot_id):
            raise ProductionObservationSpaceValidationError(
                "invalid PCVS identity")
        return self.snapshots_directory / (snapshot_id + ".json")

    def register(
        self, snapshot: ProductionCoverageSnapshot,
    ) -> ProductionCoverageSnapshot:
        if not isinstance(snapshot, ProductionCoverageSnapshot):
            raise ProductionObservationSpaceValidationError(
                "expected ProductionCoverageSnapshot")
        path = self.path_for(snapshot.production_coverage_snapshot_id)
        if path.exists():
            existing = self.load(snapshot.production_coverage_snapshot_id)
            if existing is None:  # pragma: no cover - exists() race guard
                raise ProductionObservationSpaceIdentityConflict(
                    "production coverage artifact disappeared")
            left, right = dict(existing.to_dict()), dict(snapshot.to_dict())
            left.pop("created_at", None)
            right.pop("created_at", None)
            if canonical_json(left) != canonical_json(right):
                raise ProductionObservationSpaceIdentityConflict(
                    "production coverage identity rebound to other semantics")
            return existing
        _immutable_json(path, snapshot.to_dict())
        _atomic_json(self.latest_path, {
            "schema": PRODUCTION_COVERAGE_SNAPSHOT_SCHEMA,
            "production_coverage_snapshot_id":
                snapshot.production_coverage_snapshot_id,
            "observation_space_snapshot_id":
                snapshot.observation_space_snapshot
                .observation_space_snapshot_id,
            "coverage_snapshot_identity":
                snapshot.coverage_snapshot.coverage_snapshot_identity,
            "semantic_identity": snapshot.semantic_identity,
            "cell_count": snapshot.cell_count,
            "created_at": snapshot.created_at,
        })
        return snapshot

    def load(self, snapshot_id: str) -> ProductionCoverageSnapshot | None:
        path = self.path_for(snapshot_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProductionObservationSpaceValidationError(
                "production coverage artifact unreadable") from exc
        snapshot = production_coverage_snapshot_from_dict(value)
        if snapshot.production_coverage_snapshot_id != snapshot_id:
            raise ProductionObservationSpaceIdentityConflict(
                "production coverage artifact id mismatch")
        return snapshot

    def latest_pointer(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        try:
            return json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProductionObservationSpaceValidationError(
                "latest production coverage pointer unreadable") from exc

    def load_latest(self) -> ProductionCoverageSnapshot | None:
        pointer = self.latest_pointer()
        if pointer is None:
            return None
        return self.load(str(pointer["production_coverage_snapshot_id"]))

    def snapshot_ids(self) -> tuple[str, ...]:
        if not self.snapshots_directory.exists():
            return ()
        return tuple(sorted(path.stem for path in
                            self.snapshots_directory.glob("PCVS-*.json")))



# ── Builder ─────────────────────────────────────────────────────────────────
def _existing_generated_questions(
    q71_state: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Map source observation cell -> the live generated question covering it."""
    from research_engine.v10.continuous.q71_orchestration import (
        TERMINAL_STATUSES,
    )
    out: dict[str, str] = {}
    states = dict((q71_state or {}).get("question_states") or {})
    for question_id in sorted(states):
        row = states[question_id]
        if not isinstance(row, Mapping):
            continue
        if str(row.get("source_kind") or "") != "COVERAGE_GAP":
            continue
        if str(row.get("status") or "") in TERMINAL_STATUSES:
            continue
        cell = str(row.get("source_cell_id") or "")
        if cell:
            out.setdefault(cell, str(question_id))
    return out


def build_production_coverage(
    observation_space_snapshot: ObservationSpaceSnapshot,
    *, canonical_question_projection: Mapping[str, Any] | None = None,
    active_research_by_question: Mapping[str, Sequence[str]] | None = None,
    evaluator_registry: Any | None = None,
    q71_state: Mapping[str, Any] | None = None,
    created_at: str = "", observed_at: str = "",
) -> ProductionCoverageSnapshot:
    """Build the immutable governed production coverage snapshot."""
    space = observation_space_snapshot.observation_space
    policy = observation_space_snapshot.policy
    bindings = observation_space_snapshot.bindings
    frontier_snapshot_id = observation_space_snapshot.frontier_snapshot_id
    index = _evaluator_index(evaluator_registry)
    states = canonical_research_states(
        canonical_question_projection,
        active_research_by_question=active_research_by_question)
    live_questions = _existing_generated_questions(q71_state)
    evidence_by_cell: dict[str, CoverageEvidence] = {}
    reasons_by_cell: dict[str, tuple[str, ...]] = {}
    evaluators: dict[str, str | None] = {}
    for cell in space.cells:
        binding = next(item for item in bindings
                       if item.cell_identity == cell.cell_identity)
        state = states.get(cell.subject_identity)
        evaluator = _evaluator_key(index, cell, binding)
        evaluators[cell.cell_identity] = evaluator
        evidence, reasons = coverage_evidence_for_cell(
            binding, state, frontier_snapshot_id=frontier_snapshot_id,
            policy=policy, evaluator_key=evaluator)
        evidence_by_cell[cell.cell_identity] = evidence
        reasons_by_cell[cell.cell_identity] = reasons
    evidence_inventory = tuple(sorted({
        ref for evidence in evidence_by_cell.values()
        for ref in evidence.evidence_refs}))
    inventory = ResearchInventoryBoundary(
        space.observation_space_identity,
        question_inventory=tuple(canonical_inventory()),
        evidence_inventory=evidence_inventory,
        evidence_fingerprint=observation_space_snapshot.frontier_fingerprint,
    )
    coverage = CoverageSnapshot.construct(
        space, inventory, evidence_by_cell, observed_at=observed_at)
    blind_spots = {item.cell_identity: item for item in coverage.blind_spots()}
    minted = {signal.target_ref for signal in admit_coverage_curiosity(coverage)}

    decisions: list[ProductionCoverageDecision] = []
    for cell in space.cells:
        row = coverage.coverage_for(cell.cell_identity)
        binding = next(item for item in bindings
                       if item.cell_identity == cell.cell_identity)
        declaration = _declaration_for(cell.evidence_class)
        blind = blind_spots.get(cell.cell_identity)
        evaluator = evaluators[cell.cell_identity]
        expected_status, expected_reason = _expected_question_status(
            row, evaluator)
        reasons = set(reasons_by_cell[cell.cell_identity])
        if evaluator is None:
            reasons.add(MISSING_EVALUATOR)
        decisions.append(ProductionCoverageDecision(
            cell_identity=cell.cell_identity,
            evidence_class=cell.evidence_class,
            canonical_question_ids=tuple(binding.canonical_question_ids),
            coverage_state=row.state.value,
            blind_spot=blind is not None,
            blind_spot_class=(None if blind is None
                              else blind.blind_spot_class.value),
            blind_spot_identity=(None if blind is None
                                 else blind.blind_spot_identity),
            question_minted=cell.cell_identity in minted,
            q71_eligible=cell.cell_identity in minted,
            coverage_reason=(blind.blind_spot_class.value if blind is not None
                             else row.state.value),
            evaluator_key=evaluator,
            evaluator_verdict=(EVALUATOR_RESOLVED if evaluator is not None
                               else MISSING_EVALUATOR),
            expected_question_status=expected_status,
            expected_question_reason=expected_reason,
            candidate_capable=bool(
                declaration is not None and declaration.candidate_capable),
            scientific_finding_capable=bool(
                declaration is not None
                and declaration.scientific_finding_capable),
            hypothesis_capable=bool(
                declaration is not None and declaration.hypothesis_capable),
            evidence_volume=binding.evidence_volume,
            evidence_freshness=(
                "CURRENT_FRONTIER" if binding.observed else "NO_EVIDENCE"),
            fail_closed_reasons=tuple(sorted(reasons)),
            source_observation_cell_id=cell.cell_identity,
            coverage_snapshot_identity=coverage.coverage_snapshot_identity,
            existing_generated_question_id=live_questions.get(
                cell.cell_identity),
        ))
    mapping = canonical_question_coverage_mapping(
        coverage, bindings, states, frontier_snapshot_id=frontier_snapshot_id)
    conservation = coverage_conservation(
        coverage, bindings, decisions,
        observation_space_snapshot.family_decisions)
    assert_coverage_conservation(conservation)
    return ProductionCoverageSnapshot.construct(
        observation_space_snapshot=observation_space_snapshot,
        coverage_snapshot=coverage, decisions=tuple(decisions),
        canonical_question_mapping=mapping, conservation=conservation,
        created_at=created_at)



# ── Q71 source mapping (Step 7) ─────────────────────────────────────────────
def q71_coverage_source_mapping(
    coverage: ProductionCoverageSnapshot,
    q71_state: Mapping[str, Any] | None,
    *, generated_store: Any | None = None,
) -> dict[str, Any]:
    """Bind every generated coverage question back to its source cell.

    The mapping is derived from persisted identities only: the question's own
    ``source_cell_id``, the coverage snapshot it was last evaluated against, and
    the governed ``supersession_key``/``generation_version`` the generated
    research authority persists in its own record.  No wording is ever compared.
    """
    by_cell = {item.cell_identity: item for item in coverage.decisions}
    entries: dict[str, Any] = {}
    for question_id in sorted(dict((q71_state or {}).get(
            "question_states") or {})):
        row = dict((q71_state or {})["question_states"][question_id])
        if str(row.get("source_kind") or "") != "COVERAGE_GAP":
            continue
        cell_id = str(row.get("source_cell_id") or "")
        decision = by_cell.get(cell_id)
        requirements = dict(row.get("evidence_requirements") or {})
        specification: dict[str, Any] = {}
        if generated_store is not None:
            record = generated_store.get(question_id)
            if record is not None:
                specification = dict(record.specification)
        entries[question_id] = {
            "generated_question_id": question_id,
            "source_observation_cell_id": cell_id,
            "source_coverage_snapshot_id": str(
                row.get("last_evaluated_snapshot") or ""),
            "coverage_reason": (row.get("reason")
                                or (decision.coverage_reason
                                    if decision is not None else None)),
            "question_status_reason": row.get("reason"),
            "blind_spot_identity": (
                decision.blind_spot_identity if decision is not None else None),
            "coverage_state": (
                decision.coverage_state if decision is not None else None),
            "supersession_key": specification.get("supersession_key"),
            "generation_version": specification.get("generation_version"),
            "evidence_class": (requirements.get("evidence_class")
                               or specification.get("evidence_class")),
            "population_identity": requirements.get("population_identity"),
            "horizon": requirements.get("horizon"),
            "dimension_identities": list(
                requirements.get("dimension_identities") or ()),
            "evidence_datasets": list(
                specification.get("evidence_datasets") or ()),
            "evaluator_key": row.get("evaluator"),
            "status": row.get("status"),
            "reentry_count": row.get("reentry_count", 0),
            "superseded_by": row.get("superseded_by"),
        }
    return {
        "mapping_schema": Q71_MAPPING_VERSION,
        "observation_space_snapshot_id":
            coverage.observation_space_snapshot.observation_space_snapshot_id,
        "production_coverage_snapshot_id":
            coverage.production_coverage_snapshot_id,
        "coverage_snapshot_identity":
            coverage.coverage_snapshot.coverage_snapshot_identity,
        "generated_question_count": len(entries),
        "entries": entries,
    }



# ── Research Lab / projection surface (Step 14) ─────────────────────────────
def coverage_surface(
    coverage: ProductionCoverageSnapshot | Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The truthful observation/coverage surface for the Lab and projection.

    With no production snapshot the surface says so explicitly instead of
    showing an empty-looking success state.
    """
    if coverage is None:
        return {
            "status": NO_PRODUCTION_OBSERVATION_SPACE,
            "observation_space_snapshot_id": None,
            "production_coverage_snapshot_id": None,
            "total_governed_observation_cells": 0,
            "cell_count": 0,
            "conserved": False,
            "reason": "no production observation space has been materialized",
        }
    document = (coverage.to_dict()
                if isinstance(coverage, ProductionCoverageSnapshot)
                else dict(coverage))
    decisions = list(document.get("decisions") or [])
    mapping = list(document.get("canonical_question_mapping") or [])
    conservation = dict(document.get("conservation") or {})
    families = list(
        (document.get("observation_space") or {}).get("family_decisions") or [])
    states: dict[str, int] = {}
    for row in decisions:
        key = str(row.get("coverage_state") or "UNKNOWN")
        states[key] = states.get(key, 0) + 1
    blind = [row for row in decisions if row.get("blind_spot")]
    generated = [row for row in decisions
                 if row.get("existing_generated_question_id")]
    return {
        "status": "MATERIALIZED",
        "observation_space_snapshot_id": document.get(
            "observation_space_snapshot_id"),
        "production_coverage_snapshot_id": document.get(
            "production_coverage_snapshot_id"),
        "coverage_snapshot_identity": document.get("coverage_snapshot_identity"),
        "inventory_identity": document.get("inventory_identity"),
        "evidence_fingerprint": document.get("evidence_fingerprint"),
        "frontier_snapshot_id": document.get("frontier_snapshot_id"),
        "investigation_epoch": document.get("investigation_epoch"),
        "cell_count": int(document.get("cell_count") or 0),
        "total_governed_observation_cells": int(
            document.get("cell_count") or 0),
        "coverage_states": dict(sorted(states.items())),
        "researched_cells": states.get(
            CoverageState.OBSERVED_AND_RESEARCHED.value, 0),
        "unresearched_cells": states.get(
            CoverageState.OBSERVED_NOT_RESEARCHED.value, 0),
        "active_question_cells": sum(
            1 for row in decisions
            if str(row.get("expected_question_status")) == "ACTIVE"),
        "waiting_cells": sum(
            1 for row in decisions
            if str(row.get("expected_question_status")) == "WAITING_FOR_DATA"),
        "blind_spot_count": len(blind),
        "blind_spots": [
            {
                "cell_identity": row.get("cell_identity"),
                "evidence_class": row.get("evidence_class"),
                "canonical_question_ids": row.get("canonical_question_ids"),
                "blind_spot_class": row.get("blind_spot_class"),
                "blind_spot_identity": row.get("blind_spot_identity"),
                "coverage_reason": row.get("coverage_reason"),
                "q71_eligible": row.get("q71_eligible"),
                "existing_generated_question_id": row.get(
                    "existing_generated_question_id"),
                "evidence_volume": row.get("evidence_volume"),
                "fail_closed_reasons": row.get("fail_closed_reasons"),
            }
            for row in blind],
        "missing_evaluator_cells": [
            row.get("cell_identity") for row in decisions
            if str(row.get("evaluator_verdict")) == MISSING_EVALUATOR],
        "candidate_capable_cells": [
            row.get("cell_identity") for row in decisions
            if row.get("candidate_capable")],
        "scientifically_unsupported_families": [
            {
                "structural_family": row.get("structural_family"),
                "reason_code": row.get("reason_code"),
                "detail": row.get("detail"),
            }
            for row in families if not row.get("materialized")],
        "generated_question_count": len(generated),
        "generated_questions": [
            {
                "generated_question_id": row.get(
                    "existing_generated_question_id"),
                "source_observation_cell_id": row.get("cell_identity"),
                "coverage_reason": row.get("coverage_reason"),
                "evaluator_key": row.get("evaluator_key"),
                "expected_question_status": row.get("expected_question_status"),
            }
            for row in generated],
        "canonical_question_mapping": [
            row for row in mapping if row.get("covered_cell_ids")],
        "canonical_question_mapping_version": document.get(
            "canonical_question_mapping_version"),
        "q71_mapping_version": document.get("q71_mapping_version"),
        "evaluator_registry_identity": document.get(
            "evaluator_registry_identity"),
        "evidence_catalogue_identity": document.get(
            "evidence_catalogue_identity"),
        "conservation": conservation,
        "conserved": bool(conservation.get("conserved")),
    }



# ── Production entry point ──────────────────────────────────────────────────
@dataclass(frozen=True)
class ProductionCoverageMaterialization:
    """Everything the continuous loop needs from one materialization."""

    observation_space: ObservationSpaceSnapshot
    coverage: ProductionCoverageSnapshot
    coverage_store: ResearchCoverageStore
    surface: Mapping[str, Any]

    @property
    def observation_space_snapshot_id(self) -> str:
        return self.observation_space.observation_space_snapshot_id

    @property
    def production_coverage_snapshot_id(self) -> str:
        return self.coverage.production_coverage_snapshot_id


def materialize_production_coverage(
    *, snapshot_id: str, manifest_directory: Path | str = MANIFEST_DIRECTORY,
    observation_directory: Path | str = None,
    coverage_directory: Path | str = DEFAULT_PRODUCTION_COVERAGE_DIRECTORY,
    coverage_store_path: Path | str = DEFAULT_PRODUCTION_COVERAGE_STORE_PATH,
    canonical_question_projection: Mapping[str, Any] | None = None,
    active_research_by_question: Mapping[str, Sequence[str]] | None = None,
    evaluator_registry: Any | None = None,
    q71_state: Mapping[str, Any] | None = None,
    policy: ProductionObservationPolicy | None = None,
    evaluator_registry_identity: str = "", created_at: str = "",
    observed_at: str = "",
) -> ProductionCoverageMaterialization:
    """Materialize, persist and register the production coverage snapshot.

    This is the production seam the continuous research loop calls before Q71+
    generation.  It performs exactly the governed flow:

    live governed evidence -> observation-space materialization -> governed
    coverage snapshot -> structural blind-spot detection -> registration in the
    store ``run_q71_orchestration`` reads.
    """
    from research_engine.v10.continuous.production_observation_space import (
        DEFAULT_OBSERVATION_SPACE_DIRECTORY as _DEFAULT_OBSERVATION_DIRECTORY,
    )
    resolved_policy = policy or production_observation_policy()
    resolved_registry_identity = str(evaluator_registry_identity or "")
    if not resolved_registry_identity:
        identity = getattr(evaluator_registry, "registry_identity", None)
        if isinstance(identity, str):
            resolved_registry_identity = identity
    observation_space = materialize_observation_space(
        snapshot_id=snapshot_id, manifest_directory=manifest_directory,
        store=ObservationSpaceSnapshotStore(
            observation_directory or _DEFAULT_OBSERVATION_DIRECTORY),
        policy=resolved_policy,
        evaluator_registry_identity=resolved_registry_identity,
        created_at=created_at)
    coverage = build_production_coverage(
        observation_space,
        canonical_question_projection=canonical_question_projection,
        active_research_by_question=active_research_by_question,
        evaluator_registry=evaluator_registry, q71_state=q71_state,
        created_at=created_at, observed_at=observed_at)
    persisted = ProductionCoverageSnapshotStore(
        coverage_directory).register(coverage)
    store = ResearchCoverageStore(coverage_store_path)
    store.register(persisted.coverage_snapshot)
    return ProductionCoverageMaterialization(
        observation_space=observation_space, coverage=persisted,
        coverage_store=store, surface=coverage_surface(persisted))


def write_q71_coverage_source_mapping(
    coverage: ProductionCoverageSnapshot,
    q71_state: Mapping[str, Any] | None,
    *, directory: Path | str = DEFAULT_PRODUCTION_COVERAGE_DIRECTORY,
    generated_store: Any | None = None,
) -> Path:
    """Persist the mutable Q71 -> source-cell mapping document.

    The document is a pointer: it names the immutable coverage artifact it was
    derived from, so a stale mapping can never be mistaken for current coverage.
    """
    document = q71_coverage_source_mapping(
        coverage, q71_state, generated_store=generated_store)
    path = Path(directory) / "q71_source_mapping.json"
    _atomic_json(path, document)
    return path


def load_latest_production_coverage(
    directory: Path | str = DEFAULT_PRODUCTION_COVERAGE_DIRECTORY,
) -> ProductionCoverageSnapshot | None:
    """Latest persisted production coverage snapshot, or ``None``."""
    return ProductionCoverageSnapshotStore(directory).load_latest()


__all__ = [
    "CanonicalQuestionCoverage",
    "CanonicalResearchState",
    "DEFAULT_PRODUCTION_COVERAGE_DIRECTORY",
    "DEFAULT_PRODUCTION_COVERAGE_STORE_PATH",
    "EVALUATOR_RESOLVED",
    "NO_PRODUCTION_OBSERVATION_SPACE",
    "PRODUCTION_COVERAGE_SNAPSHOT_ID_PREFIX",
    "PRODUCTION_COVERAGE_SNAPSHOT_SCHEMA",
    "ProductionCoverageDecision",
    "ProductionCoverageMaterialization",
    "ProductionCoverageSnapshot",
    "ProductionCoverageSnapshotStore",
    "RESOLVED_RESULT_STATUSES",
    "SUPERSEDED_RESULT_STATUSES",
    "assert_coverage_conservation",
    "build_production_coverage",
    "canonical_question_coverage_mapping",
    "canonical_research_states",
    "coverage_conservation",
    "coverage_evidence_for_cell",
    "coverage_surface",
    "is_production_coverage_snapshot_identity",
    "load_latest_production_coverage",
    "materialize_production_coverage",
    "production_coverage_snapshot_from_dict",
    "production_coverage_snapshot_identity_for",
    "q71_coverage_source_mapping",
    "write_q71_coverage_source_mapping",
]
