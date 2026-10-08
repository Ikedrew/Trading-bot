"""Production observation-space materialization for autonomous Q71+ research.

Repair Block 2 gave the engine a governed Q71+ execution path and Repair Block 3
gave it a governed candidate lifecycle, but in production nothing ever
*materialized* the governed observation space.  ``run_q71_orchestration`` reads a
``ResearchCoverageStore``; that store was never populated from live evidence, so
production Q71+ generation had no eyes and could not run without a manually
supplied coverage snapshot.

This module is that materialization path.  It is deliberately narrow:

* The observation space is built from *governed declarations only* - the closed
  production evidence-class catalogue
  (``research_engine.experiments.q71_evidence_classes``) resolved against the
  frozen canonical question registry.  No cell is invented, no Cartesian product
  is generated, and no dimension is fabricated.
* The cell set is **independent of the evidence epoch**.  Evidence availability
  changes the *coverage* of a cell, never its identity, so a new frontier
  re-enters the same governed question instead of minting a semantically
  duplicate one.
* Every cell carries a structural binding record to the real governed dataset
  population (dataset, schema version, presence, row count, content digest,
  Stage 4 dataset-snapshot identity, evidence frontier identity).  A dataset is
  never bound merely because its name looks similar.
* Unknown dimensions stay explicitly ``UNKNOWN`` / ``NOT_APPLICABLE``.
* Anything that cannot be structurally resolved fails closed with a reason code
  instead of becoming a silent success.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import (
    canonical_json, fingerprint,
)
from research_engine.experiments.q71_evidence_classes import (
    PRODUCTION_EVIDENCE_CLASSES,
    ProductionEvidenceClass,
    evidence_class_identity,
    evidence_class_vocabulary,
    structural_generated_question_families,
    unsupported_evidence_class_catalogue,
)
from research_engine.lifecycle.generated_research_isolation import (
    canonical_inventory,
)
from research_engine.lifecycle.research_observation_space import (
    CompatibilityRule,
    EvidenceCapability,
    ObservationCell,
    ObservationCellDeclaration,
    ObservationSpace,
    ObservationSpaceConstructionPolicy,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    MANIFEST_DIRECTORY,
    InvestigationSnapshot,
    load_investigation_snapshot_id,
)


PRODUCTION_OBSERVATION_SPACE_SCHEMA = "production_observation_space_v1"
PRODUCER_VERSION = "production_observation_space_producer_v1"
CANONICAL_QUESTION_MAPPING_VERSION = "canonical_question_coverage_mapping_v1"
Q71_MAPPING_VERSION = "q71_coverage_source_mapping_v1"

OBSERVATION_SPACE_SNAPSHOT_ID_PREFIX = "POS-"
CELL_BINDING_ID_PREFIX = "PCB-"

DEFAULT_OBSERVATION_SPACE_DIRECTORY = Path(
    "data/research/continuous/observation_space")

#: Explicit "the governed authority does not declare this dimension" markers.
#: A dimension is never guessed, and it is never silently omitted either.
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Policy for a governed dataset that is part of the evidence contract but
#: carries no rows in the current frontier.  The policy *explicitly* declares
#: this an evidence gap; absence of evidence is never treated as a research gap
#: without this declaration.
GOVERNED_EVIDENCE_GAP = "GOVERNED_EVIDENCE_GAP"
UNKNOWN_EVIDENCE_CLASS_FAIL_CLOSED = "FAIL_CLOSED"

# -- Fail-closed reason codes (Step 13).  None of these may become a silent
# -- success, and every one of them is reported on the coverage snapshot.
MISSING_DATASET_BINDING = "MISSING_DATASET_BINDING"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
UNKNOWN_EVIDENCE_CLASS = "UNKNOWN_EVIDENCE_CLASS"
MISSING_EVALUATOR = "MISSING_EVALUATOR"
INVALID_SCHEMA = "INVALID_SCHEMA"
UNRESOLVED_POPULATION = "UNRESOLVED_POPULATION"
STALE_FRONTIER = "STALE_FRONTIER"
DUPLICATE_CELL = "DUPLICATE_CELL"
DUPLICATE_QUESTION = "DUPLICATE_QUESTION"
SUPERSEDED = "SUPERSEDED"
EXCLUDED_BY_POLICY = "EXCLUDED_BY_POLICY"
NOT_SCIENTIFICALLY_SUPPORTED = "NOT_SCIENTIFICALLY_SUPPORTED"

FAIL_CLOSED_REASON_CODES: tuple[str, ...] = (
    MISSING_DATASET_BINDING, MISSING_EVIDENCE, UNKNOWN_EVIDENCE_CLASS,
    MISSING_EVALUATOR, INVALID_SCHEMA, UNRESOLVED_POPULATION, STALE_FRONTIER,
    DUPLICATE_CELL, DUPLICATE_QUESTION, SUPERSEDED, EXCLUDED_BY_POLICY,
    NOT_SCIENTIFICALLY_SUPPORTED,
)

_HEX = frozenset("0123456789abcdef")


class ProductionObservationSpaceError(RuntimeError):
    """The production observation space could not be built or trusted."""


class ProductionObservationSpaceValidationError(ProductionObservationSpaceError):
    """A structural declaration or artifact is malformed."""


class ProductionObservationSpaceIdentityConflict(ProductionObservationSpaceError):
    """Two different artifacts claim the same governed identity."""



def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _HEX)


def _identity(prefix: str, value: str) -> str:
    if not _is_sha256(value):
        raise ProductionObservationSpaceValidationError(
            "semantic identity must be sha256 hex")
    return prefix + value[:16].upper()


def _is(prefix: str, value: Any) -> bool:
    return (isinstance(value, str) and value.startswith(prefix)
            and len(value) == len(prefix) + 16
            and all(character in "0123456789ABCDEF"
                    for character in value[len(prefix):]))


def observation_space_snapshot_identity_for(value: str) -> str:
    return _identity(OBSERVATION_SPACE_SNAPSHOT_ID_PREFIX, value)


def cell_binding_identity_for(value: str) -> str:
    return _identity(CELL_BINDING_ID_PREFIX, value)


def is_observation_space_snapshot_identity(value: Any) -> bool:
    return _is(OBSERVATION_SPACE_SNAPSHOT_ID_PREFIX, value)


def is_cell_binding_identity(value: Any) -> bool:
    return _is(CELL_BINDING_ID_PREFIX, value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProductionObservationSpaceValidationError(
            name + " must be non-empty trimmed text")
    return value


def _tokens(values: Any, name: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ProductionObservationSpaceValidationError(
            name + " must be a sequence")
    out = tuple(sorted({_text(item, name) for item in values}))
    if required and not out:
        raise ProductionObservationSpaceValidationError(
            name + " must be non-empty")
    return out


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


# â”€â”€ Governed production observation policy â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@dataclass(frozen=True)
class ProductionObservationPolicy:
    """The governed policy that admits the production observation universe.

    The policy is a *declaration*, not a generator: it names the closed
    vocabularies a cell may draw from and the treatment of every failure mode.
    It never enumerates combinations and never invents a dimension.
    """

    policy_version: int = 1
    allowed_subject_kinds: tuple[str, ...] = ("CANONICAL_QUESTION",)
    allowed_evidence_classes: tuple[str, ...] = field(
        default_factory=evidence_class_vocabulary)
    allowed_populations: tuple[str, ...] = field(
        default_factory=evidence_class_vocabulary)
    #: No governed authority currently declares a horizon for a production
    #: evidence class, so the governed horizon vocabulary of this policy is the
    #: explicit UNKNOWN token.  A future horizon dimension requires a policy
    #: version bump, which is exactly the intended governance.
    allowed_horizons: tuple[str, ...] = (UNKNOWN,)
    exclusions: tuple[str, ...] = ()
    #: The physical datasets the governed evidence contract binds.  A cell whose
    #: evidence class names a dataset outside this boundary can never be
    #: satisfied by the Q71 worker and fails closed with
    #: ``MISSING_DATASET_BINDING``.
    governed_datasets: tuple[str, ...] = BOUND_DATASETS
    dataset_absent_treatment: str = GOVERNED_EVIDENCE_GAP
    unknown_evidence_class_treatment: str = UNKNOWN_EVIDENCE_CLASS_FAIL_CLOSED
    #: Generation policy.  A governed blind spot whose population carries no
    #: rows yet, or which has no production evaluator, may still be *minted* as
    #: a question identity (it stays WAITING_FOR_DATA / MISSING_EVALUATOR and
    #: never enters the bounded agenda queue).  Disabling either flag suppresses
    #: the question identity through the governed ``curiosity_excluded`` flag,
    #: so the policy and the generation stage can never disagree.
    mint_question_for_absent_evidence: bool = True
    mint_question_for_missing_evaluator: bool = True
    label: str = "production_observation_universe_v1"
    note: str = ""
    created_at: str = ""
    semantic_identity: str = field(init=False)
    policy_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.policy_version) is not int or self.policy_version < 1:
            raise ProductionObservationSpaceValidationError(
                "policy_version must be a positive int")
        for name in ("allowed_subject_kinds", "allowed_evidence_classes",
                     "allowed_populations", "allowed_horizons"):
            object.__setattr__(self, name, _tokens(getattr(self, name), name,
                                                   required=True))
        object.__setattr__(self, "exclusions",
                           _tokens(self.exclusions, "exclusions"))
        governed = _tokens(self.governed_datasets, "governed_datasets",
                           required=True)
        outside = sorted(set(governed) - set(BOUND_DATASETS))
        if outside:
            raise ProductionObservationSpaceNotMaterializable(
                MISSING_DATASET_BINDING, ",".join(outside))
        object.__setattr__(self, "governed_datasets", governed)
        if self.dataset_absent_treatment != GOVERNED_EVIDENCE_GAP:
            raise ProductionObservationSpaceValidationError(
                "dataset_absent_treatment must be " + GOVERNED_EVIDENCE_GAP)
        if (self.unknown_evidence_class_treatment
                != UNKNOWN_EVIDENCE_CLASS_FAIL_CLOSED):
            raise ProductionObservationSpaceValidationError(
                "unknown_evidence_class_treatment must fail closed")
        for name in ("mint_question_for_absent_evidence",
                     "mint_question_for_missing_evaluator"):
            if type(getattr(self, name)) is not bool:
                raise ProductionObservationSpaceValidationError(
                    name + " must be bool")
        semantic = _digest(self.semantic_material())
        object.__setattr__(self, "semantic_identity", semantic)
        object.__setattr__(self, "policy_identity",
                           "POSPOL-" + semantic[:16].upper())

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "production_observation_policy",
            "schema_version": PRODUCTION_OBSERVATION_SPACE_SCHEMA,
            "policy_version": self.policy_version,
            "allowed_subject_kinds": list(self.allowed_subject_kinds),
            "allowed_evidence_classes": list(self.allowed_evidence_classes),
            "allowed_populations": list(self.allowed_populations),
            "allowed_horizons": list(self.allowed_horizons),
            "exclusions": list(self.exclusions),
            "governed_datasets": list(self.governed_datasets),
            "dataset_absent_treatment": self.dataset_absent_treatment,
            "unknown_evidence_class_treatment":
                self.unknown_evidence_class_treatment,
            "mint_question_for_absent_evidence":
                self.mint_question_for_absent_evidence,
            "mint_question_for_missing_evaluator":
                self.mint_question_for_missing_evaluator,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_material(), "label": self.label,
                "note": self.note, "created_at": self.created_at,
                "semantic_identity": self.semantic_identity,
                "policy_identity": self.policy_identity}

    def space_policy(self) -> ObservationSpaceConstructionPolicy:
        """Project this policy onto the governed observation-space policy."""
        return ObservationSpaceConstructionPolicy(
            allowed_subject_kinds=tuple(self.allowed_subject_kinds),
            allowed_dimensions=(),
            allowed_populations=tuple(self.allowed_populations),
            allowed_horizons=tuple(self.allowed_horizons),
            allowed_evidence_classes=tuple(self.allowed_evidence_classes),
            compatibility_rules=(CompatibilityRule(
                rule_token="production_observation_universe_v1",
                subject_kinds=tuple(self.allowed_subject_kinds)),),
            exclusions=tuple(self.exclusions),
            label=self.label, note=self.note, created_at=self.created_at,
        )


def production_observation_policy(**kwargs: Any) -> ProductionObservationPolicy:
    """The single governed production observation policy."""
    return ProductionObservationPolicy(**kwargs)



def _immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write-once persistence: a repeated identical write is a no-op."""
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProductionObservationSpaceIdentityConflict(
                "IMMUTABLE_ARTIFACT_UNREADABLE:" + str(path)) from exc
        if canonical_json(existing) != canonical_json(payload):
            raise ProductionObservationSpaceIdentityConflict(
                "IMMUTABLE_ARTIFACT_IDENTITY_COLLISION:" + str(path))
        return
    _atomic_json(path, payload)


class ProductionObservationSpaceNotMaterializable(ProductionObservationSpaceError):
    """A structural fact the materialization requires is absent.

    Raised instead of fabricating the missing fact; the message always starts
    with a governed fail-closed reason code.
    """

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        if self.reason_code not in FAIL_CLOSED_REASON_CODES:
            raise ProductionObservationSpaceValidationError(
                "UNKNOWN_FAIL_CLOSED_REASON:" + self.reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code + (":" + self.detail if self.detail else ""))


# â”€â”€ Governed cell set: structural, evidence-independent â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def canonical_questions_for_evidence_class(
    declaration: ProductionEvidenceClass,
    *, inventory: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Resolve the governed canonical questions one evidence class is equivalent to.

    Resolution is structural only and uses two governed authorities:

    * an exact canonical question identity, or
    * a governed contiguous canonical *group* (``EX1-EX4``) whose two declared
      endpoints are themselves canonical identities, expanded in frozen registry
      order.

    Free text is never compared, no similarity is scored, and an unresolvable
    reference fails closed with ``UNRESOLVED_POPULATION``.
    """
    order = tuple(inventory) if inventory is not None else tuple(
        canonical_inventory())
    token = str(getattr(declaration, "canonical_question_id", "") or "").strip()
    if token in order:
        return (token,)
    parts = token.split("-")
    if (len(parts) == 2 and parts[0] in order and parts[1] in order):
        start, end = order.index(parts[0]), order.index(parts[1])
        if start <= end:
            return tuple(order[start:end + 1])
    raise ProductionObservationSpaceNotMaterializable(
        UNRESOLVED_POPULATION, token or "<missing>")


def _declaration_datasets(declaration: ProductionEvidenceClass) -> tuple[str, ...]:
    datasets = tuple(str(item) for item in declaration.governed_datasets)
    if not datasets:
        raise ProductionObservationSpaceNotMaterializable(
            MISSING_DATASET_BINDING, declaration.evidence_class)
    return datasets


def governed_observation_cell_declarations(
    policy: ProductionObservationPolicy | None = None,
    *, inventory: Sequence[str] | None = None,
) -> tuple[ObservationCellDeclaration, ...]:
    """The deterministic governed cell set of the production observation space.

    The set is a pure function of the governed evidence-class catalogue, the
    frozen canonical registry and the policy.  It does **not** depend on which
    datasets happen to be present, so a new evidence epoch can never churn cell
    identity (and therefore can never mint a duplicate question).
    """
    resolved = policy or production_observation_policy()
    order = tuple(inventory) if inventory is not None else tuple(
        canonical_inventory())
    allowed_classes = set(resolved.allowed_evidence_classes)
    governed_datasets = set(resolved.governed_datasets)
    exclusions = set(resolved.exclusions)
    declarations: list[ObservationCellDeclaration] = []
    for declaration in PRODUCTION_EVIDENCE_CLASSES:
        evidence_class = declaration.evidence_class
        if evidence_class not in allowed_classes:
            raise ProductionObservationSpaceNotMaterializable(
                UNKNOWN_EVIDENCE_CLASS, evidence_class)
        datasets = _declaration_datasets(declaration)
        outside = sorted(set(datasets) - governed_datasets)
        if outside:
            raise ProductionObservationSpaceNotMaterializable(
                MISSING_DATASET_BINDING,
                evidence_class + ":" + ",".join(outside))
        for question_id in canonical_questions_for_evidence_class(
                declaration, inventory=order):
            if question_id in exclusions or evidence_class in exclusions:
                continue
            declarations.append(ObservationCellDeclaration(
                subject_kind="CANONICAL_QUESTION",
                subject_identity=question_id,
                population_identity=evidence_class,
                horizon=UNKNOWN,
                evidence_class=evidence_class,
                evidence_capability=EvidenceCapability.OBSERVABLE,
            ))
    if not declarations:
        raise ProductionObservationSpaceNotMaterializable(
            NOT_SCIENTIFICALLY_SUPPORTED, "no governed cell declared")
    return tuple(declarations)


def governed_observation_space(
    policy: ProductionObservationPolicy | None = None,
    *, inventory: Sequence[str] | None = None, label: str = "",
    created_at: str = "",
) -> ObservationSpace:
    """Construct the governed observation space (duplicate cells fail closed)."""
    resolved = policy or production_observation_policy()
    declarations = governed_observation_cell_declarations(
        resolved, inventory=inventory)
    materials = [canonical_json(item.semantic_material())
                 for item in declarations]
    if len(set(materials)) != len(materials):
        raise ProductionObservationSpaceNotMaterializable(
            DUPLICATE_CELL, "governed cell declarations are not unique")
    return ObservationSpace.construct(
        resolved.space_policy(), declarations,
        label=label or resolved.label, created_at=created_at)



# â”€â”€ Structural binding of a cell to real governed evidence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _canonical_question(question_id: str) -> Any:
    from research_engine.registry.research_question_registry import (
        get_question,
    )
    return get_question(question_id)


@dataclass(frozen=True)
class ProductionCellBinding:
    """One observation cell bound to the real governed evidence population.

    The binding is descriptive provenance: it records which physical datasets,
    schema versions, object populations and producer identity the cell's
    population actually comes from.  It grants nothing and computes nothing.
    """

    observation_space_snapshot_id: str
    cell_identity: str
    evidence_class: str
    canonical_question_ids: tuple[str, ...]
    governed_datasets: tuple[str, ...]
    dataset_schema_versions: tuple[tuple[str, str], ...]
    dataset_presence: tuple[tuple[str, str], ...]
    dataset_row_counts: tuple[tuple[str, int], ...]
    dataset_evidence_identities: tuple[tuple[str, str], ...]
    required_fields: tuple[str, ...]
    evidence_producer_identity: str
    evidence_snapshot_id: str
    evidence_epoch: str
    evidence_frontier_start: str
    evidence_frontier_end: str
    evidence_fingerprint: str
    generation_version: int
    observed: bool
    market_dimension: str = NOT_APPLICABLE
    instrument_class_dimension: str = NOT_APPLICABLE
    session_dimension: str = NOT_APPLICABLE
    regime_dimension: str = NOT_APPLICABLE
    treatment_dimension: str = NOT_APPLICABLE
    outcome_metric_family: str = NOT_APPLICABLE
    semantic_identity: str = field(init=False)
    binding_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not is_observation_space_snapshot_identity(
                self.observation_space_snapshot_id):
            raise ProductionObservationSpaceValidationError(
                "binding requires a POS observation-space identity")
        if type(self.generation_version) is not int or self.generation_version < 1:
            raise ProductionObservationSpaceValidationError(
                "generation_version must be a positive int")
        if type(self.observed) is not bool:
            raise ProductionObservationSpaceValidationError(
                "observed must be bool")
        semantic = _digest(self.semantic_material())
        object.__setattr__(self, "semantic_identity", semantic)
        object.__setattr__(self, "binding_identity",
                           cell_binding_identity_for(semantic))

    def semantic_material(self) -> dict[str, Any]:
        # The observation-space snapshot identity is a cross-reference, not part
        # of the binding's own structural identity: the artifact identity is
        # itself derived from the bindings, so including it would be circular.
        return {
            "kind": "production_cell_binding",
            "schema_version": PRODUCTION_OBSERVATION_SPACE_SCHEMA,
            "cell_identity": self.cell_identity,
            "evidence_class": self.evidence_class,
            "canonical_question_ids": list(self.canonical_question_ids),
            "governed_datasets": list(self.governed_datasets),
            "dataset_schema_versions": [list(item) for item in
                                        self.dataset_schema_versions],
            "dataset_presence": [list(item) for item in self.dataset_presence],
            "dataset_row_counts": [list(item) for item in
                                   self.dataset_row_counts],
            "dataset_evidence_identities": [
                list(item) for item in self.dataset_evidence_identities],
            "required_fields": list(self.required_fields),
            "evidence_producer_identity": self.evidence_producer_identity,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_epoch": self.evidence_epoch,
            "evidence_frontier_start": self.evidence_frontier_start,
            "evidence_frontier_end": self.evidence_frontier_end,
            "evidence_fingerprint": self.evidence_fingerprint,
            "generation_version": self.generation_version,
            "observed": self.observed,
            "market_dimension": self.market_dimension,
            "instrument_class_dimension": self.instrument_class_dimension,
            "session_dimension": self.session_dimension,
            "regime_dimension": self.regime_dimension,
            "treatment_dimension": self.treatment_dimension,
            "outcome_metric_family": self.outcome_metric_family,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_material(),
                "observation_space_snapshot_id": self.observation_space_snapshot_id,
                "semantic_identity": self.semantic_identity,
                "binding_identity": self.binding_identity}

    @property
    def evidence_identities(self) -> tuple[str, ...]:
        return tuple(sorted(identity for _, identity in
                            self.dataset_evidence_identities))

    @property
    def evidence_volume(self) -> int:
        return sum(count for _, count in self.dataset_row_counts)




def _producer_identity(snapshot: InvestigationSnapshot) -> str:
    authority = dict(snapshot.source_authority or {})
    return fingerprint({
        "canonical_authority": authority.get("canonical_authority"),
        "data_contract": authority.get("data_contract"),
        "source": authority.get("source"),
        "bucket": authority.get("bucket"),
        "region": authority.get("region"),
        "research_profile": authority.get("research_profile"),
        "stage4_snapshot_policy_id": authority.get("stage4_snapshot_policy_id"),
    })


def bind_observation_cells(
    space: ObservationSpace,
    snapshot: InvestigationSnapshot,
    *, observation_space_snapshot_id: str,
    generation_version: int = 1,
) -> tuple[ProductionCellBinding, ...]:
    """Bind every governed cell to the real governed evidence population.

    A cell whose evidence class names a dataset the snapshot does not bind, or
    whose schema authority is not the governed one, fails closed.  Nothing is
    bound because a name looks similar.
    """
    from core.production_data_contract import current_schema

    bindings = {item.dataset: item for item in snapshot.datasets}
    producer = _producer_identity(snapshot)
    out: list[ProductionCellBinding] = []
    for cell in space.cells:
        declaration = next(
            (item for item in PRODUCTION_EVIDENCE_CLASSES
             if item.evidence_class == cell.evidence_class), None)
        if declaration is None:
            raise ProductionObservationSpaceNotMaterializable(
                UNKNOWN_EVIDENCE_CLASS, cell.evidence_class)
        datasets = _declaration_datasets(declaration)
        schemas: list[tuple[str, str]] = []
        presence: list[tuple[str, str]] = []
        counts: list[tuple[str, int]] = []
        identities: list[tuple[str, str]] = []
        observed = True
        for dataset in datasets:
            binding = bindings.get(dataset)
            if binding is None:
                raise ProductionObservationSpaceNotMaterializable(
                    MISSING_DATASET_BINDING,
                    cell.evidence_class + ":" + dataset)
            governed_schema = str(current_schema(dataset) or "")
            if not governed_schema or binding.schema_version != governed_schema:
                raise ProductionObservationSpaceNotMaterializable(
                    INVALID_SCHEMA,
                    dataset + ":" + str(binding.schema_version) + "!="
                    + governed_schema)
            schemas.append((dataset, binding.schema_version))
            presence.append((dataset, binding.presence))
            counts.append((dataset, int(binding.source_row_count)))
            identities.append((dataset, (
                str(binding.dataset_snapshot_id)
                if binding.dataset_snapshot_id is not None
                else "EVIDENCE:" + dataset + ":" + binding.content_digest)))
            if binding.presence != "PRESENT" or binding.source_row_count <= 0:
                observed = False
        question = _canonical_question(cell.subject_identity)
        if question is None:
            raise ProductionObservationSpaceNotMaterializable(
                UNRESOLVED_POPULATION, cell.subject_identity)
        out.append(ProductionCellBinding(
            observation_space_snapshot_id=observation_space_snapshot_id,
            cell_identity=cell.cell_identity,
            evidence_class=cell.evidence_class,
            canonical_question_ids=(cell.subject_identity,),
            governed_datasets=tuple(datasets),
            dataset_schema_versions=tuple(sorted(schemas)),
            dataset_presence=tuple(sorted(presence)),
            dataset_row_counts=tuple(sorted(counts)),
            dataset_evidence_identities=tuple(sorted(identities)),
            required_fields=tuple(sorted(
                str(field) for field in question.required_fields)),
            evidence_producer_identity=producer,
            evidence_snapshot_id=str(snapshot.snapshot_id),
            evidence_epoch=str(snapshot.evidence_epoch),
            evidence_frontier_start=str(snapshot.start_date),
            evidence_frontier_end=str(snapshot.end_date),
            evidence_fingerprint=str(snapshot.snapshot_fingerprint),
            generation_version=int(generation_version),
            observed=observed,
        ))
    identities_seen = [item.cell_identity for item in out]
    if len(set(identities_seen)) != len(identities_seen):
        raise ProductionObservationSpaceNotMaterializable(
            DUPLICATE_CELL, "duplicate cell identity in binding set")
    return tuple(out)


# â”€â”€ Structural family ledger â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@dataclass(frozen=True)
class StructuralFamilyDecision:
    """Why a governed structural family did or did not become observation cells.

    The denominator of every coverage claim is the *real* structural family set
    (``structural_generated_question_families``); it is never narrowed to make
    coverage look better.
    """

    structural_family: str
    materialized: bool
    reason_code: str | None
    cell_identities: tuple[str, ...]
    evaluator_available: bool
    provenance: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "structural_family": self.structural_family,
            "materialized": self.materialized,
            "reason_code": self.reason_code,
            "cell_identities": list(self.cell_identities),
            "evaluator_available": self.evaluator_available,
            "provenance": self.provenance,
            "detail": self.detail,
        }


def structural_family_decisions(
    space: ObservationSpace,
    policy: ProductionObservationPolicy | None = None,
) -> tuple[StructuralFamilyDecision, ...]:
    """Account for every governed structural family exactly once."""
    resolved = policy or production_observation_policy()
    unsupported = unsupported_evidence_class_catalogue()
    declared = {item.evidence_class: item for item in PRODUCTION_EVIDENCE_CLASSES}
    governed_datasets = set(resolved.governed_datasets)
    exclusions = set(resolved.exclusions)
    out: list[StructuralFamilyDecision] = []
    for family in structural_generated_question_families():
        cells = tuple(sorted(cell.cell_identity for cell in space.cells
                             if cell.evidence_class == family))
        if cells:
            out.append(StructuralFamilyDecision(
                structural_family=family, materialized=True, reason_code=None,
                cell_identities=cells, evaluator_available=family in declared))
            continue
        if family in unsupported:
            entry = unsupported[family]
            out.append(StructuralFamilyDecision(
                structural_family=family, materialized=False,
                reason_code=str(entry["reason"]), cell_identities=(),
                evaluator_available=False,
                provenance=str(entry["provenance"]),
                detail=str(entry["detail"])))
            continue
        if family in exclusions:
            reason, detail = EXCLUDED_BY_POLICY, "family excluded by policy"
        elif family not in set(resolved.allowed_evidence_classes):
            reason, detail = (UNKNOWN_EVIDENCE_CLASS,
                              "family is not in the governed evidence-class vocabulary")
        elif family not in declared:
            reason, detail = (NOT_SCIENTIFICALLY_SUPPORTED,
                              "no governed declaration exists for this family")
        elif sorted(set(declared[family].governed_datasets)
                    - governed_datasets):
            reason, detail = (MISSING_DATASET_BINDING,
                              "declared datasets are outside the evidence contract")
        else:
            reason, detail = (UNRESOLVED_POPULATION,
                              "no governed canonical question could be resolved")
        out.append(StructuralFamilyDecision(
            structural_family=family, materialized=False, reason_code=reason,
            cell_identities=(), evaluator_available=False,
            provenance=str(declared[family].provenance.get("authority", ""))
            if family in declared else "", detail=detail))
    if len({item.structural_family for item in out}) != len(out):
        raise ProductionObservationSpaceNotMaterializable(
            DUPLICATE_CELL, "structural family ledger is not unique")
    return tuple(out)

    return tuple(out)



# â”€â”€ Production observation-space snapshot (immutable artifact) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@dataclass(frozen=True)
class ObservationSpaceSnapshot:
    observation_space: ObservationSpace
    policy: ProductionObservationPolicy
    bindings: tuple[ProductionCellBinding, ...]
    family_decisions: tuple[StructuralFamilyDecision, ...]
    frontier_snapshot_id: str
    frontier_fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    evaluator_registry_identity: str
    evidence_catalogue_identity: str
    canonical_question_mapping_version: str
    q71_mapping_version: str
    producer_version: str
    created_at: str
    semantic_identity: str
    observation_space_snapshot_id: str

    @classmethod
    def construct(
        cls, *, observation_space: ObservationSpace,
        policy: ProductionObservationPolicy,
        bindings: Sequence[ProductionCellBinding],
        family_decisions: Sequence[StructuralFamilyDecision],
        frontier_snapshot_id: str, frontier_fingerprint: str,
        investigation_epoch: str, frontier_start: str, frontier_end: str,
        evaluator_registry_identity: str, created_at: str = "",
    ) -> "ObservationSpaceSnapshot":
        bound = tuple(bindings)
        if len(bound) != len(observation_space.cells):
            raise ProductionObservationSpaceValidationError(
                "every governed cell requires exactly one binding")
        if {item.cell_identity for item in bound} != {
                cell.cell_identity for cell in observation_space.cells}:
            raise ProductionObservationSpaceValidationError(
                "binding set does not match the governed cell set")
        material = {
            "kind": "production_observation_space_snapshot",
            "schema_version": PRODUCTION_OBSERVATION_SPACE_SCHEMA,
            "producer_version": PRODUCER_VERSION,
            "observation_space_identity":
                observation_space.observation_space_identity,
            "policy_identity": policy.policy_identity,
            "bindings": [item.semantic_identity for item in bound],
            "family_decisions": [item.to_dict() for item in family_decisions],
            "frontier_snapshot_id": str(frontier_snapshot_id),
            "frontier_fingerprint": str(frontier_fingerprint),
            "investigation_epoch": str(investigation_epoch),
            "evaluator_registry_identity": str(evaluator_registry_identity),
            "evidence_catalogue_identity": evidence_class_identity(),
            "canonical_question_mapping_version":
                CANONICAL_QUESTION_MAPPING_VERSION,
            "q71_mapping_version": Q71_MAPPING_VERSION,
        }
        semantic = _digest(material)
        return cls(
            observation_space=observation_space, policy=policy, bindings=bound,
            family_decisions=tuple(family_decisions),
            frontier_snapshot_id=str(frontier_snapshot_id),
            frontier_fingerprint=str(frontier_fingerprint),
            investigation_epoch=str(investigation_epoch),
            frontier_start=str(frontier_start), frontier_end=str(frontier_end),
            evaluator_registry_identity=str(evaluator_registry_identity),
            evidence_catalogue_identity=evidence_class_identity(),
            canonical_question_mapping_version=CANONICAL_QUESTION_MAPPING_VERSION,
            q71_mapping_version=Q71_MAPPING_VERSION,
            producer_version=PRODUCER_VERSION, created_at=str(created_at),
            semantic_identity=semantic,
            observation_space_snapshot_id=observation_space_snapshot_identity_for(
                semantic),
        )

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "production_observation_space_snapshot",
            "schema_version": PRODUCTION_OBSERVATION_SPACE_SCHEMA,
            "producer_version": self.producer_version,
            "observation_space_identity":
                self.observation_space.observation_space_identity,
            "policy_identity": self.policy.policy_identity,
            "bindings": [item.semantic_identity for item in self.bindings],
            "family_decisions": [item.to_dict() for item in self.family_decisions],
            "frontier_snapshot_id": self.frontier_snapshot_id,
            "frontier_fingerprint": self.frontier_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "evaluator_registry_identity": self.evaluator_registry_identity,
            "evidence_catalogue_identity": self.evidence_catalogue_identity,
            "canonical_question_mapping_version":
                self.canonical_question_mapping_version,
            "q71_mapping_version": self.q71_mapping_version,
        }

    @property
    def cell_count(self) -> int:
        return len(self.observation_space.cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "observation_space": self.observation_space.to_dict(),
            "policy": self.policy.to_dict(),
            "bindings": [item.to_dict() for item in self.bindings],
            "cell_count": self.cell_count,
            "frontier_start": self.frontier_start,
            "frontier_end": self.frontier_end,
            "created_at": self.created_at,
            "digest": self.semantic_identity,
            "semantic_identity": self.semantic_identity,
            "observation_space_snapshot_id": self.observation_space_snapshot_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationSpaceSnapshot":
        if not isinstance(value, Mapping):
            raise ProductionObservationSpaceValidationError(
                "observation-space artifact must be a mapping")
        space = ObservationSpace.from_dict(value["observation_space"])
        allowed = {
            "policy_version", "allowed_subject_kinds",
            "allowed_evidence_classes", "allowed_populations",
            "allowed_horizons", "exclusions", "governed_datasets",
            "dataset_absent_treatment", "unknown_evidence_class_treatment",
            "mint_question_for_absent_evidence",
            "mint_question_for_missing_evaluator", "label", "note",
            "created_at",
        }
        policy = production_observation_policy(**{
            key: item for key, item in dict(value["policy"]).items()
            if key in allowed})
        bindings = tuple(_binding_from_dict(item) for item in value["bindings"])
        families = tuple(
            StructuralFamilyDecision(
                structural_family=str(item["structural_family"]),
                materialized=bool(item["materialized"]),
                reason_code=(None if item.get("reason_code") is None
                             else str(item["reason_code"])),
                cell_identities=tuple(str(x) for x in item["cell_identities"]),
                evaluator_available=bool(item["evaluator_available"]),
                provenance=str(item.get("provenance") or ""),
                detail=str(item.get("detail") or ""))
            for item in value["family_decisions"])
        rebuilt = cls.construct(
            observation_space=space, policy=policy, bindings=bindings,
            family_decisions=families,
            frontier_snapshot_id=str(value["frontier_snapshot_id"]),
            frontier_fingerprint=str(value["frontier_fingerprint"]),
            investigation_epoch=str(value["investigation_epoch"]),
            frontier_start=str(value["frontier_start"]),
            frontier_end=str(value["frontier_end"]),
            evaluator_registry_identity=str(
                value["evaluator_registry_identity"]),
            created_at=str(value["created_at"]),
        )
        if (value["semantic_identity"] != rebuilt.semantic_identity
                or value["observation_space_snapshot_id"]
                != rebuilt.observation_space_snapshot_id
                or int(value["cell_count"]) != rebuilt.cell_count):
            raise ProductionObservationSpaceIdentityConflict(
                "observation-space artifact identity mismatch")
        return rebuilt


def _binding_from_dict(value: Mapping[str, Any]) -> ProductionCellBinding:
    return ProductionCellBinding(
        observation_space_snapshot_id=str(
            value["observation_space_snapshot_id"]),
        cell_identity=str(value["cell_identity"]),
        evidence_class=str(value["evidence_class"]),
        canonical_question_ids=tuple(
            str(x) for x in value["canonical_question_ids"]),
        governed_datasets=tuple(str(x) for x in value["governed_datasets"]),
        dataset_schema_versions=tuple(
            (str(a), str(b)) for a, b in value["dataset_schema_versions"]),
        dataset_presence=tuple(
            (str(a), str(b)) for a, b in value["dataset_presence"]),
        dataset_row_counts=tuple(
            (str(a), int(b)) for a, b in value["dataset_row_counts"]),
        dataset_evidence_identities=tuple(
            (str(a), str(b)) for a, b in value["dataset_evidence_identities"]),
        required_fields=tuple(str(x) for x in value["required_fields"]),
        evidence_producer_identity=str(value["evidence_producer_identity"]),
        evidence_snapshot_id=str(value["evidence_snapshot_id"]),
        evidence_epoch=str(value["evidence_epoch"]),
        evidence_frontier_start=str(value["evidence_frontier_start"]),
        evidence_frontier_end=str(value["evidence_frontier_end"]),
        evidence_fingerprint=str(value["evidence_fingerprint"]),
        generation_version=int(value["generation_version"]),
        observed=bool(value["observed"]),
        market_dimension=str(value["market_dimension"]),
        instrument_class_dimension=str(value["instrument_class_dimension"]),
        session_dimension=str(value["session_dimension"]),
        regime_dimension=str(value["regime_dimension"]),
        treatment_dimension=str(value["treatment_dimension"]),
        outcome_metric_family=str(value["outcome_metric_family"]),
    )


# â”€â”€ Immutable persistence with a mutable latest pointer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ObservationSpaceSnapshotStore:
    """Append-only store of immutable production observation-space artifacts."""

    def __init__(
        self, directory: Path | str = DEFAULT_OBSERVATION_SPACE_DIRECTORY,
    ) -> None:
        self.directory = Path(directory)
        self.spaces_directory = self.directory / "spaces"
        self.latest_path = self.directory / "latest_space.json"

    def path_for(self, snapshot_id: str) -> Path:
        if not is_observation_space_snapshot_identity(snapshot_id):
            raise ProductionObservationSpaceValidationError(
                "invalid POS identity")
        return self.spaces_directory / (snapshot_id + ".json")

    def register(
        self, snapshot: ObservationSpaceSnapshot,
    ) -> ObservationSpaceSnapshot:
        if not isinstance(snapshot, ObservationSpaceSnapshot):
            raise ProductionObservationSpaceValidationError(
                "expected ObservationSpaceSnapshot")
        path = self.path_for(snapshot.observation_space_snapshot_id)
        if path.exists():
            existing = self.load(snapshot.observation_space_snapshot_id)
            if existing is None:  # pragma: no cover - exists() race guard
                raise ProductionObservationSpaceIdentityConflict(
                    "observation-space artifact disappeared")
            left, right = dict(existing.to_dict()), dict(snapshot.to_dict())
            left.pop("created_at", None)
            right.pop("created_at", None)
            if canonical_json(left) != canonical_json(right):
                raise ProductionObservationSpaceIdentityConflict(
                    "observation-space identity rebound to other semantics")
            return existing
        _immutable_json(path, snapshot.to_dict())
        _atomic_json(self.latest_path, {
            "schema": PRODUCTION_OBSERVATION_SPACE_SCHEMA,
            "observation_space_snapshot_id":
                snapshot.observation_space_snapshot_id,
            "semantic_identity": snapshot.semantic_identity,
            "frontier_snapshot_id": snapshot.frontier_snapshot_id,
            "frontier_fingerprint": snapshot.frontier_fingerprint,
            "investigation_epoch": snapshot.investigation_epoch,
            "cell_count": snapshot.cell_count,
            "created_at": snapshot.created_at,
        })
        return snapshot

    def load(self, snapshot_id: str) -> ObservationSpaceSnapshot | None:
        path = self.path_for(snapshot_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProductionObservationSpaceValidationError(
                "observation-space artifact unreadable") from exc
        snapshot = ObservationSpaceSnapshot.from_dict(value)
        if snapshot.observation_space_snapshot_id != snapshot_id:
            raise ProductionObservationSpaceIdentityConflict(
                "observation-space artifact id mismatch")
        return snapshot

    def latest_pointer(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        try:
            return json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProductionObservationSpaceValidationError(
                "latest observation-space pointer unreadable") from exc

    def load_latest(self) -> ObservationSpaceSnapshot | None:
        pointer = self.latest_pointer()
        if pointer is None:
            return None
        return self.load(str(pointer["observation_space_snapshot_id"]))

    def snapshot_ids(self) -> tuple[str, ...]:
        if not self.spaces_directory.exists():
            return ()
        return tuple(sorted(path.stem for path in
                            self.spaces_directory.glob("POS-*.json")))


# â”€â”€ Materialization entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def materialize_observation_space(
    *, snapshot_id: str, manifest_directory: Path | str = MANIFEST_DIRECTORY,
    store: ObservationSpaceSnapshotStore | None = None,
    policy: ProductionObservationPolicy | None = None,
    evaluator_registry_identity: str = "", created_at: str = "",
    generation_version: int = 1,
) -> ObservationSpaceSnapshot:
    """Materialize the production observation space from real governed evidence.

    The real governed evidence is the frozen investigation snapshot: the same
    boundary the canonical question cycle and the Q71 worker read.  If that
    snapshot is absent or unreadable the materialization fails closed with
    ``MISSING_EVIDENCE``; nothing is fabricated to make the space appear.
    """
    resolved = policy or production_observation_policy()
    resolved_id = str(snapshot_id or "").strip()
    if not resolved_id.startswith("ISNAP-"):
        raise ProductionObservationSpaceNotMaterializable(
            MISSING_EVIDENCE, "no governed investigation snapshot:"
            + (resolved_id or "<empty>"))
    try:
        snapshot = load_investigation_snapshot_id(
            resolved_id, manifest_directory=Path(manifest_directory))
    except Exception as exc:
        raise ProductionObservationSpaceNotMaterializable(
            MISSING_EVIDENCE,
            resolved_id + ":" + f"{type(exc).__name__}:{exc}") from exc
    space = governed_observation_space(resolved, created_at=created_at)
    families = structural_family_decisions(space, resolved)
    bindings = bind_observation_cells(
        space, snapshot, observation_space_snapshot_id="POS-" + ("0" * 16),
        generation_version=generation_version)
    artifact = ObservationSpaceSnapshot.construct(
        observation_space=space, policy=resolved, bindings=bindings,
        family_decisions=families, frontier_snapshot_id=snapshot.snapshot_id,
        frontier_fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_start=snapshot.start_date, frontier_end=snapshot.end_date,
        evaluator_registry_identity=evaluator_registry_identity,
        created_at=created_at,
    )
    rebound = tuple(
        replace(item,
                observation_space_snapshot_id=(
                    artifact.observation_space_snapshot_id))
        for item in bindings)
    artifact = ObservationSpaceSnapshot.construct(
        observation_space=space, policy=resolved, bindings=rebound,
        family_decisions=families, frontier_snapshot_id=snapshot.snapshot_id,
        frontier_fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_start=snapshot.start_date, frontier_end=snapshot.end_date,
        evaluator_registry_identity=evaluator_registry_identity,
        created_at=created_at,
    )
    resolved_store = store or ObservationSpaceSnapshotStore()
    return resolved_store.register(artifact)


def load_latest_observation_space(
    directory: Path | str = DEFAULT_OBSERVATION_SPACE_DIRECTORY,
) -> ObservationSpaceSnapshot | None:
    """Latest materialized observation space, or ``None`` when none exists."""
    return ObservationSpaceSnapshotStore(directory).load_latest()


__all__ = [
    "CANONICAL_QUESTION_MAPPING_VERSION",
    "DEFAULT_OBSERVATION_SPACE_DIRECTORY",
    "DUPLICATE_CELL",
    "DUPLICATE_QUESTION",
    "EXCLUDED_BY_POLICY",
    "FAIL_CLOSED_REASON_CODES",
    "GOVERNED_EVIDENCE_GAP",
    "INVALID_SCHEMA",
    "MISSING_DATASET_BINDING",
    "MISSING_EVIDENCE",
    "MISSING_EVALUATOR",
    "NOT_APPLICABLE",
    "NOT_SCIENTIFICALLY_SUPPORTED",
    "OBSERVATION_SPACE_SNAPSHOT_ID_PREFIX",
    "ObservationSpaceSnapshot",
    "ObservationSpaceSnapshotStore",
    "PRODUCER_VERSION",
    "PRODUCTION_OBSERVATION_SPACE_SCHEMA",
    "ProductionCellBinding",
    "ProductionObservationPolicy",
    "ProductionObservationSpaceError",
    "ProductionObservationSpaceIdentityConflict",
    "ProductionObservationSpaceNotMaterializable",
    "ProductionObservationSpaceValidationError",
    "Q71_MAPPING_VERSION",
    "STALE_FRONTIER",
    "SUPERSEDED",
    "StructuralFamilyDecision",
    "UNKNOWN",
    "UNKNOWN_EVIDENCE_CLASS",
    "UNRESOLVED_POPULATION",
    "bind_observation_cells",
    "canonical_questions_for_evidence_class",
    "cell_binding_identity_for",
    "governed_observation_cell_declarations",
    "governed_observation_space",
    "is_cell_binding_identity",
    "is_observation_space_snapshot_identity",
    "load_latest_observation_space",
    "materialize_observation_space",
    "observation_space_snapshot_identity_for",
    "production_observation_policy",
    "structural_family_decisions",
]
