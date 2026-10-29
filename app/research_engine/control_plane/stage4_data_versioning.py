"""STAGE 4 CANONICAL DATA / SCHEMA VERSIONING AUTHORITY.

One authority, one policy, fail closed.  This module is the single place that
answers, for any governed observation dataset: dataset name, dataset version,
schema generation, producer version, evidence epoch, predecessor, observation
requirements, questions served, schema fingerprint, producer fingerprint,
collection start and compatibility class.

DESIGN DECISION - WHY THIS EXTENDS RATHER THAN DUPLICATES
---------------------------------------------------------
``core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY`` is the pre-existing
canonical authority.  It already owns dataset -> (current schema, generation,
role, semantic_owner, population, partition_model) and its ``generation`` field
is documented as "future genuine revisions become 2, 3, ...".  This module does
NOT create a parallel version authority.  It:

  * READS dataset identity/schema/generation from that registry (single source),
  * ADDS the three dimensions the registry does not model - schema-generation
    boundaries, producer version fingerprints, and evidence epochs - as a
    governed OVERLAY keyed by the same dataset names,
  * VALIDATES the overlay against the registry so the two can never drift.

THE POLICY (canonical, applied unless a stronger compatible project rule exists)
-------------------------------------------------------------------------------
1. DATASET VERSION changes only on material incompatibility: canonical grain,
   canonical entity identity, primary-key semantics, fundamental event meaning,
   incompatible field semantics, record lifecycle semantics, or when backward
   compatibility is impossible without ambiguity.  Adding fields is NOT enough.

2. SCHEMA GENERATION changes when the dataset remains fundamentally the same
   but its governed record schema evolves (new/stronger fields, timestamp
   attestation, lifecycle snapshot, experiment-arm metadata, OHLC path block,
   optional->governed, explicit missing-state semantics).  Generation-1 records
   are never rewritten.

3. PRODUCER VERSION increments whenever emitting code changes materially: new
   field emission, new capture/assignment/path-binding logic, changed
   serialization.  A producer-only refactor with byte-identical output semantics
   may move the code fingerprint without a schema-generation change.

4. EVIDENCE EPOCH is opened whenever a new (schema generation, producer version)
   combination begins producing scientifically usable evidence.  It pins
   dataset, dataset version, schema generation, producer version, collection
   start, activated observation requirements, dependent questions, applicable
   canonical identities and evidence-contract versions.  Research re-entry must
   bind to the correct epoch.

5. LINKAGE - every schema-generation change records the observation requirement
   IDs, requesting question IDs, reason, fields added/changed, responsible
   producer, collection start and backfill/future-only status.  No orphans.

6. HISTORICAL IMMUTABILITY - no injecting new fields into old records, no
   claiming generation-1 rows satisfy generation-2 requirements, no inferring
   unavailable historical values.

7. ADDITIVE vs BREAKING - ``ADDITIVE_SCHEMA_EVOLUTION`` when meaning, grain and
   canonical identity are unchanged and new fields are additive;
   ``BREAKING_DATASET_EVOLUTION`` when meaning/grain/identity change or field
   reinterpretation creates ambiguity.  UNCERTAIN => FAIL CLOSED.

This module is CONTROL-PLANE ONLY.  It performs no S3 write, no backfill, no
historical mutation, no research re-entry and never starts Q71+.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane import stage4_identity as I

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY

POLICY_ID = "stage4_data_versioning_policy_v1"
POLICY_STAMP = "20260929"
STAMP = POLICY_STAMP

#: Compatibility classes (policy rule 7).
ADDITIVE_SCHEMA_EVOLUTION = "ADDITIVE_SCHEMA_EVOLUTION"
BREAKING_DATASET_EVOLUTION = "BREAKING_DATASET_EVOLUTION"

COMPATIBILITY_CLASSES = frozenset({
    ADDITIVE_SCHEMA_EVOLUTION, BREAKING_DATASET_EVOLUTION,
})

#: The material dimensions that force a DATASET version change (rule 1).  A
#: caller must assert each is UNCHANGED for an additive classification.
MATERIAL_INCOMPATIBILITY_AXES: tuple[str, ...] = (
    "canonical_grain",
    "canonical_entity_identity",
    "primary_key_semantics",
    "fundamental_event_meaning",
    "field_semantics",
    "record_lifecycle_semantics",
    "backward_compatibility_without_ambiguity",
)

#: Value used when an axis was neither asserted compatible nor listed changed.
AXIS_UNASSERTED = "UNASSERTED"


class DataVersioningError(RuntimeError):
    """A canonical versioning invariant was violated (fail closed)."""


# ---------------------------------------------------------------------------
# FINGERPRINTS
# ---------------------------------------------------------------------------

def canonical_json(payload: Any) -> str:
    """Deterministic JSON rendering used for every fingerprint here."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str,
    )


def fingerprint(payload: Any) -> str:
    """sha256 over canonical JSON: stable across processes and key order."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def schema_fingerprint(governed_fields: Iterable[str]) -> str:
    """Fingerprint of a governed record schema.

    Only governed field NAMES take part: the schema is the set of fields a
    consumer may rely on, not the values a record happened to carry.
    """
    return fingerprint(
        {"governed_fields": sorted({str(f) for f in governed_fields})})


def producer_fingerprint(modules: Iterable[str],
                         emitted_fields: Iterable[str]) -> str:
    """Fingerprint of the emitting code plus the fields it emits.

    The module list is the code surface; the emitted-field list makes a change
    in what is emitted visible even when the module list is unchanged.
    """
    return fingerprint({
        "modules": sorted({str(m) for m in modules}),
        "emitted_fields": sorted({str(f) for f in emitted_fields}),
    })


# ---------------------------------------------------------------------------
# CHANGE CLASSIFICATION (policy rule 7)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvolutionAssessment:
    """Outcome of classifying one proposed dataset/schema change."""

    compatibility_class: str
    dataset_version_change_required: bool
    schema_generation_change_required: bool
    producer_version_change_required: bool
    breaking_axes: tuple[str, ...]
    unasserted_axes: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatibility_class": self.compatibility_class,
            "dataset_version_change_required": self.dataset_version_change_required,
            "schema_generation_change_required": self.schema_generation_change_required,
            "producer_version_change_required": self.producer_version_change_required,
            "breaking_axes": list(self.breaking_axes),
            "unasserted_axes": list(self.unasserted_axes),
            "reason": self.reason,
        }


def classify_evolution(
    *,
    changed_axes: Sequence[str] = (),
    new_fields: Sequence[str] = (),
    changed_fields: Sequence[str] = (),
    removed_fields: Sequence[str] = (),
    field_reinterpretations: Sequence[str] = (),
    producer_code_changed: bool = False,
    axes_asserted: Sequence[str] = MATERIAL_INCOMPATIBILITY_AXES,
    axes_compatible: Mapping[str, bool] | None = None,
) -> EvolutionAssessment:
    """Classify a proposed change under the canonical policy (rule 7).

    ``axes_compatible`` maps an axis name to "this axis is UNCHANGED".  An axis
    that is neither asserted compatible nor listed as changed is UNASSERTED.

    FAIL CLOSED: any unasserted material axis, any field reinterpretation, or
    any removed field makes the evolution BREAKING, i.e. a dataset version
    change.  An uncertain classification is never silently treated as additive.

    A self-contradictory call (an axis declared BOTH changed and unchanged) is
    itself treated as BREAKING: the caller is uncertain, and uncertainty fails
    closed.
    """
    asserted = {str(a) for a in axes_asserted}
    compatible = dict(axes_compatible or {})

    changed = {str(a) for a in changed_axes}
    unasserted = tuple(sorted(
        axis for axis in asserted
        if axis not in changed and axis not in compatible
    ))
    # An axis the caller declared CHANGED is incompatible by that declaration.
    # Declaring it simultaneously "unchanged" is a contradiction, not a nuance.
    contradictions = tuple(sorted(
        axis for axis in changed if compatible.get(axis) is True
    ))
    incompatible = tuple(sorted(
        axis for axis in changed if compatible.get(axis) is not True
    ))
    breaking = tuple(sorted(
        set(incompatible) | set(contradictions)
        | {str(f) for f in field_reinterpretations}
    ))

    hard_breaking = bool(breaking) or bool(unasserted) or bool(removed_fields)

    if hard_breaking:
        return EvolutionAssessment(
            compatibility_class=BREAKING_DATASET_EVOLUTION,
            dataset_version_change_required=True,
            # A dataset version bump supersedes a schema-generation bump; the
            # new dataset version starts its own generation sequence at 1.
            schema_generation_change_required=False,
            producer_version_change_required=True,
            breaking_axes=breaking,
            unasserted_axes=unasserted,
            reason=(
                "Material incompatibility asserted (breaking_axes="
                f"{list(breaking)}) or a material axis was left unasserted "
                f"({list(unasserted)}) or fields were removed "
                f"({list(removed_fields)}). Policy rule 7 fails closed to a "
                "dataset version change."
            ),
        )

    return EvolutionAssessment(
        compatibility_class=ADDITIVE_SCHEMA_EVOLUTION,
        dataset_version_change_required=False,
        # Additive / optional-to-governed evolution moves the schema generation;
        # a pure producer-only change (identical governed contract) does not.
        schema_generation_change_required=bool(new_fields or changed_fields),
        producer_version_change_required=bool(producer_code_changed),
        breaking_axes=(),
        unasserted_axes=(),
        reason=(
            "Dataset meaning, grain and canonical identity unchanged; change is "
            f"additive (new_fields={len(tuple(new_fields))}, "
            f"changed_fields={len(tuple(changed_fields))}). Classified "
            "ADDITIVE_SCHEMA_EVOLUTION."
        ),
    )


# ---------------------------------------------------------------------------
# GOVERNED RECORDS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaGeneration:
    """One governed schema generation of a dataset (policy rule 2).

    Generation 1 is the historical baseline.  A generation is immutable once
    declared: it describes what records of that generation may contain, never
    licensing a rewrite of records already written.
    """

    dataset: str
    dataset_version: str
    generation: int
    predecessor_generation: int | None
    compatibility_class: str
    governed_fields: tuple[str, ...]
    added_fields: tuple[str, ...]
    changed_fields: tuple[str, ...]
    reason: str
    responsible_producer: str
    observation_requirements: tuple[str, ...]
    questions: tuple[str, ...]
    collection_start: str
    backfill_status: str
    immutable_history: bool = True

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise DataVersioningError("SCHEMA_GENERATION_MUST_BE_POSITIVE")
        if self.compatibility_class not in COMPATIBILITY_CLASSES:
            raise DataVersioningError("UNKNOWN_COMPATIBILITY_CLASS")
        # A generation must never be orphaned: it names at least one
        # observation requirement and the producer responsible for it.
        if not self.observation_requirements:
            raise DataVersioningError("ORPHAN_SCHEMA_GENERATION_NO_REQUIREMENT")
        if not self.responsible_producer:
            raise DataVersioningError("SCHEMA_GENERATION_WITHOUT_PRODUCER")
        if self.generation > 1 and self.predecessor_generation is None:
            raise DataVersioningError("SCHEMA_GENERATION_MISSING_PREDECESSOR")
        try:
            for rid in self.observation_requirements:
                I.validate_requirement_id(rid)
        except I.Stage4IdentityError as exc:
            raise DataVersioningError(str(exc)) from exc

    @property
    def schema_fingerprint(self) -> str:
        return schema_fingerprint(self.governed_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "schema_generation": self.generation,
            "predecessor_generation": self.predecessor_generation,
            "compatibility_class": self.compatibility_class,
            "governed_fields": list(self.governed_fields),
            "added_fields": list(self.added_fields),
            "changed_fields": list(self.changed_fields),
            "schema_fingerprint": self.schema_fingerprint,
            "reason": self.reason,
            "responsible_producer": self.responsible_producer,
            "observation_requirements": list(self.observation_requirements),
            "questions": list(self.questions),
            "collection_start": self.collection_start,
            "backfill_status": self.backfill_status,
            "historical_immutability_enforced": self.immutable_history,
        }


@dataclass(frozen=True)
class ProducerVersion:
    """One governed producer version (policy rule 3)."""

    dataset: str
    producer_version: str
    producer_fingerprint: str
    modules: tuple[str, ...]
    emitted_fields: tuple[str, ...]
    schema_generation: int
    predecessor_producer_version: str | None
    change_kind: str
    output_semantics_unchanged: bool

    def __post_init__(self) -> None:
        if not self.modules:
            raise DataVersioningError("PRODUCER_VERSION_WITHOUT_MODULES")
        if not self.emitted_fields:
            raise DataVersioningError("PRODUCER_VERSION_WITHOUT_EMITTED_FIELDS")
        if self.schema_generation < 1:
            raise DataVersioningError("PRODUCER_VERSION_BAD_GENERATION")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "producer_version": self.producer_version,
            "producer_fingerprint": self.producer_fingerprint,
            "modules": list(self.modules),
            "emitted_fields": list(self.emitted_fields),
            "schema_generation": self.schema_generation,
            "predecessor_producer_version": self.predecessor_producer_version,
            "change_kind": self.change_kind,
            "output_semantics_unchanged": self.output_semantics_unchanged,
        }


@dataclass(frozen=True)
class EvidenceEpoch:
    """A governed evidence epoch (policy rule 4).

    An epoch opens when a new (schema generation, producer version) combination
    begins producing scientifically usable evidence.  Research re-entry must
    bind to the correct epoch; evidence produced under a different generation
    may never be presented as satisfying this one.
    """

    STATUS_OPEN = "OPEN"
    STATUS_COLLECTING = "COLLECTING"
    STATUS_FROZEN = "FROZEN"
    STATUS_RETIRED = "RETIRED"
    ALLOWED_STATUSES = frozenset({
        STATUS_OPEN, STATUS_COLLECTING, STATUS_FROZEN, STATUS_RETIRED,
    })

    epoch_id: str
    dataset: str
    dataset_version: str
    schema_generation: int
    producer_version: str
    producer_fingerprint: str
    collection_start: str
    observation_requirements: tuple[str, ...]
    questions: tuple[str, ...]
    canonical_identities: tuple[str, ...]
    evidence_contract_versions: Mapping[str, str]
    predecessor_epoch_id: str | None
    status: str
    research_reentry_events: int = 0
    #: Immutable dataset POPULATION(S) this epoch's window is drawn from
    #: (Stage 4 Refinement 2).  Empty while the population is still growing.
    dataset_snapshot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in self.ALLOWED_STATUSES:
            raise DataVersioningError("UNKNOWN_EVIDENCE_EPOCH_STATUS")
        if not self.observation_requirements:
            raise DataVersioningError("EVIDENCE_EPOCH_WITHOUT_REQUIREMENTS")
        if not self.collection_start:
            raise DataVersioningError("EVIDENCE_EPOCH_WITHOUT_COLLECTION_START")
        if not self.evidence_contract_versions:
            raise DataVersioningError("EVIDENCE_EPOCH_WITHOUT_CONTRACT_VERSIONS")
        if self.research_reentry_events != 0:
            raise DataVersioningError("UNEXPECTED_RESEARCH_REENTRY_IN_EPOCH")
        snapshots = tuple(sorted(set(self.dataset_snapshot_ids)))
        for snapshot_id in snapshots:
            try:
                I.validate_dataset_snapshot_id(snapshot_id, allow_none=False)
            except I.Stage4IdentityError as exc:
                raise DataVersioningError(str(exc)) from exc
        if snapshots and self.status in (self.STATUS_OPEN, self.STATUS_COLLECTING):
            # A growing population cannot be content-addressed: claiming a
            # frozen snapshot here would assert an immutable population that
            # does not exist yet.
            raise DataVersioningError(
                "LIVE_POPULATION_CANNOT_HAVE_FROZEN_SNAPSHOT:" + self.epoch_id)
        object.__setattr__(self, "dataset_snapshot_ids", snapshots)
        try:
            for rid in self.observation_requirements:
                I.validate_requirement_id(rid)
        except I.Stage4IdentityError as exc:
            raise DataVersioningError(str(exc)) from exc

    @property
    def schema_version(self) -> str:
        """The schema/interpretation contract this epoch is read under.

        ``dataset_version`` is the historical field name and its value is a
        schema-registry string (``shadow_runtime_v1``): it is a SCHEMA
        identity, never a population identity.  The population identity of
        this epoch is ``dataset_snapshot_ids``.
        """
        return self.dataset_version

    @property
    def snapshot_identity_state(self) -> str:
        """Whether this epoch's population identity is resolved or not."""
        if self.dataset_snapshot_ids:
            return I.SNAPSHOT_IDENTITY_BOUND
        if self.status in (self.STATUS_OPEN, self.STATUS_COLLECTING):
            return I.SNAPSHOT_IDENTITY_UNRESOLVED_LIVE
        return I.SNAPSHOT_IDENTITY_UNRESOLVED_HISTORICAL

    @property
    def evidence_set(self) -> I.EvidenceSet:
        """Canonical identity for the governed dataset slice opened by this epoch.

        When the epoch's population is frozen, each member claims one immutable
        dataset snapshot.  The population authority owns that snapshot's content
        digest, so the epoch never duplicates one here.  While the population is
        still collecting the member stays a dataset-slice reference and the
        evidence set declares NO snapshot identity: a growing population is not
        an immutable evidence snapshot.
        """
        if self.dataset_snapshot_ids:
            members = tuple(I.EvidenceMemberReference(
                reference_type="dataset_snapshot",
                reference_id=snapshot_id,
                dataset=self.dataset,
                locator=(f"schema_version={self.dataset_version};"
                         f"schema_generation={self.schema_generation};"
                         f"producer_version={self.producer_version}"),
                content_fingerprint=None,
                dataset_snapshot_id=snapshot_id,
            ) for snapshot_id in self.dataset_snapshot_ids)
            snapshot_ids = self.dataset_snapshot_ids
        else:
            members = (I.EvidenceMemberReference(
                reference_type="governed_dataset_slice",
                reference_id=self.epoch_id,
                dataset=self.dataset,
                locator=(f"schema_generation={self.schema_generation};"
                         f"producer_version={self.producer_version}"),
                content_fingerprint=self.schema_fingerprint,
            ),)
            snapshot_ids = ()
        # The ID is derived from immutable epoch identity, not clock time or a
        # mutable filename. Registry collision checks bind it to membership.
        return I.EvidenceSet(
            evidence_set_id="ESET-" + self.epoch_id,
            observation_requirement_ids=self.observation_requirements,
            members=members,
            dataset_snapshot_ids=snapshot_ids,
        )

    @property
    def schema_fingerprint(self) -> str:
        return fingerprint({
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "schema_generation": self.schema_generation,
        })

    def to_dict(self) -> dict[str, Any]:
        evidence_set = self.evidence_set
        return {
            "epoch_id": self.epoch_id,
            "evidence_set_id": evidence_set.evidence_set_id,
            "evidence_member_references": [
                member.to_dict() for member in evidence_set.members],
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "producer_version": self.producer_version,
            "producer_fingerprint": self.producer_fingerprint,
            "schema_fingerprint": self.schema_fingerprint,
            "collection_start": self.collection_start,
            "observation_requirements": list(self.observation_requirements),
            "questions": list(self.questions),
            "canonical_identities": list(self.canonical_identities),
            "evidence_contract_versions": dict(self.evidence_contract_versions),
            "predecessor_epoch_id": self.predecessor_epoch_id,
            "status": self.status,
            "research_reentry_events": self.research_reentry_events,
            "dataset_snapshot_ids": list(self.dataset_snapshot_ids),
            "snapshot_identity_state": self.snapshot_identity_state,
        }


# ---------------------------------------------------------------------------
# REGISTRY - the authority overlay
# ---------------------------------------------------------------------------

class VersionRegistry:
    """Governed overlay answering every canonical versioning question.

    Base dataset identity/schema/generation is READ from
    ``PRODUCTION_SCHEMA_REGISTRY`` (never duplicated); this class only adds
    the generation boundaries, producer versions and evidence epochs.
    """

    def __init__(
        self,
        *,
        generations: Sequence[SchemaGeneration],
        producer_versions: Sequence[ProducerVersion],
        epochs: Sequence[EvidenceEpoch],
    ) -> None:
        self._generations: dict[tuple[str, int], SchemaGeneration] = {}
        for gen in generations:
            key = (gen.dataset, gen.generation)
            if key in self._generations:
                raise DataVersioningError("DUPLICATE_SCHEMA_GENERATION")
            self._generations[key] = gen

        self._producers: dict[tuple[str, str], ProducerVersion] = {}
        for pv in producer_versions:
            key = (pv.dataset, pv.producer_version)
            if key in self._producers:
                raise DataVersioningError("DUPLICATE_PRODUCER_VERSION")
            self._producers[key] = pv

        self._epochs: dict[str, EvidenceEpoch] = {}
        for epoch in epochs:
            if epoch.epoch_id in self._epochs:
                raise DataVersioningError("DUPLICATE_EVIDENCE_EPOCH")
            self._epochs[epoch.epoch_id] = epoch

        self._validate()

    # -- base authority (read-only, never duplicated) ---------------------

    @staticmethod
    def base_dataset_version(dataset: str) -> str:
        """Canonical dataset VERSION string from the production contract."""
        if dataset not in PRODUCTION_SCHEMA_REGISTRY:
            raise DataVersioningError("UNKNOWN_DATASET:" + str(dataset))
        return PRODUCTION_SCHEMA_REGISTRY[dataset].current

    @staticmethod
    def is_declared_dataset(dataset: str) -> bool:
        return dataset in PRODUCTION_SCHEMA_REGISTRY

    # -- queries -----------------------------------------------------------

    def generation(self, dataset: str, generation: int) -> SchemaGeneration:
        try:
            return self._generations[(dataset, int(generation))]
        except KeyError:
            raise DataVersioningError(
                f"NO_SCHEMA_GENERATION:{dataset}:{generation}") from None

    def current_generation(self, dataset: str) -> SchemaGeneration:
        candidates = [
            gen for (ds, _), gen in self._generations.items() if ds == dataset
        ]
        if not candidates:
            raise DataVersioningError("NO_SCHEMA_GENERATION_FOR:" + str(dataset))
        return max(candidates, key=lambda g: g.generation)

    def producer_version(self, dataset: str, version: str) -> ProducerVersion:
        try:
            return self._producers[(dataset, str(version))]
        except KeyError:
            raise DataVersioningError(
                f"NO_PRODUCER_VERSION:{dataset}:{version}") from None

    def epoch(self, epoch_id: str) -> EvidenceEpoch:
        try:
            return self._epochs[str(epoch_id)]
        except KeyError:
            raise DataVersioningError("NO_EVIDENCE_EPOCH:" + str(epoch_id)) from None

    def epochs(self) -> tuple[EvidenceEpoch, ...]:
        """Every governed evidence epoch, in canonical epoch-id order."""
        return tuple(self._epochs[epoch_id] for epoch_id in sorted(self._epochs))

    def epochs_for(self, dataset: str) -> tuple[EvidenceEpoch, ...]:
        return tuple(e for e in self._epochs.values() if e.dataset == dataset)

    def _epoch_for_generation(
        self, dataset: str, generation: int, dataset_version: str,
    ) -> EvidenceEpoch | None:
        for epoch in sorted(self.epochs_for(dataset), key=lambda e: e.epoch_id):
            if (epoch.schema_generation == generation
                    and epoch.dataset_version == dataset_version):
                return epoch
        return None

    def producer_for_generation(self, dataset: str,
                               generation: int) -> ProducerVersion:
        """The producer version bound to one schema generation of a dataset."""
        candidates = [
            pv for (ds, _), pv in self._producers.items()
            if ds == dataset and pv.schema_generation == int(generation)
        ]
        if not candidates:
            raise DataVersioningError(
                f"NO_PRODUCER_FOR_GENERATION:{dataset}:{generation}")
        return sorted(candidates, key=lambda p: p.producer_version)[0]

    def authority(self, dataset: str, generation: int | None = None) -> dict[str, Any]:
        """The complete canonical answer for one dataset (all 12 fields)."""
        gen = (self.current_generation(dataset) if generation is None
               else self.generation(dataset, generation))
        producer = self.producer_for_generation(dataset, gen.generation)
        epoch = self._epoch_for_generation(
            dataset, gen.generation, gen.dataset_version)
        return {
            "dataset": dataset,
            "dataset_name": dataset,
            "dataset_version": gen.dataset_version,
            "schema_version": gen.dataset_version,
            "schema_generation": gen.generation,
            "producer_version": producer.producer_version,
            "evidence_epoch": epoch.epoch_id if epoch else None,
            "evidence_set_id": (
                epoch.evidence_set.evidence_set_id if epoch else None),
            "dataset_snapshot_ids": list(
                epoch.dataset_snapshot_ids) if epoch else [],
            "dataset_snapshot_id": (
                epoch.dataset_snapshot_ids[0]
                if epoch and len(epoch.dataset_snapshot_ids) == 1 else None),
            "snapshot_identity_state": (
                epoch.snapshot_identity_state if epoch
                else I.SNAPSHOT_IDENTITY_UNRESOLVED_HISTORICAL),
            "predecessor": (
                None if gen.predecessor_generation is None else {
                    "schema_generation": gen.predecessor_generation,
                    "producer_version": producer.predecessor_producer_version,
                    "evidence_epoch": (
                        epoch.predecessor_epoch_id if epoch else None),
                }
            ),
            "observation_requirements": list(gen.observation_requirements),
            "questions_served": list(gen.questions),
            "schema_fingerprint": gen.schema_fingerprint,
            "producer_fingerprint": producer.producer_fingerprint,
            "collection_start": gen.collection_start,
            "compatibility_class": gen.compatibility_class,
        }


    # -- validation (fail closed) ------------------------------------------

    def _validate(self) -> None:
        for (dataset, generation), gen in self._generations.items():
            # Drift guard: the overlay may not contradict the base contract.
            if self.is_declared_dataset(dataset):
                expected = self.base_dataset_version(dataset)
                if gen.dataset_version != expected:
                    raise DataVersioningError(
                        f"SCHEMA_VERSION_DRIFT:{dataset}:{gen.dataset_version}"
                        f"!={expected}")
            # Predecessor must exist within the same dataset.
            if gen.predecessor_generation is not None:
                if (dataset, gen.predecessor_generation) not in self._generations:
                    raise DataVersioningError(
                        f"MISSING_PREDECESSOR_GENERATION:{dataset}:"
                        f"{gen.predecessor_generation}")
            # An additive generation must be a strict superset of its
            # predecessor's governed fields; a shrinking schema would be a
            # breaking change and must have been classified as one.
            if gen.predecessor_generation is not None:
                pred = self._generations[
                    (dataset, gen.predecessor_generation)]
                if gen.compatibility_class == ADDITIVE_SCHEMA_EVOLUTION:
                    lost = set(pred.governed_fields) - set(gen.governed_fields)
                    if lost:
                        raise DataVersioningError(
                            "ADDITIVE_GENERATION_DROPPED_FIELDS:"
                            + ",".join(sorted(lost)))
                if gen.schema_fingerprint == pred.schema_fingerprint:
                    raise DataVersioningError(
                        "SCHEMA_GENERATION_WITHOUT_SCHEMA_CHANGE:" + str(dataset))

        for (dataset, version), pv in self._producers.items():
            if (dataset, pv.schema_generation) not in self._generations:
                raise DataVersioningError(
                    f"PRODUCER_VERSION_UNKNOWN_GENERATION:{dataset}:"
                    f"{pv.schema_generation}")

        evidence_sets = I.EvidenceSetRegistry()
        for epoch in self._epochs.values():
            evidence_sets.add(epoch.evidence_set)
            if (epoch.dataset, epoch.schema_generation) not in self._generations:
                raise DataVersioningError(
                    "EVIDENCE_EPOCH_UNKNOWN_GENERATION:" + epoch.epoch_id)
            pv = self.producer_version(epoch.dataset, epoch.producer_version)
            if pv.schema_generation != epoch.schema_generation:
                raise DataVersioningError(
                    "EVIDENCE_EPOCH_PRODUCER_GENERATION_MISMATCH:"
                    + epoch.epoch_id)
            if pv.producer_fingerprint != epoch.producer_fingerprint:
                raise DataVersioningError(
                    "EVIDENCE_EPOCH_PRODUCER_FINGERPRINT_MISMATCH:"
                    + epoch.epoch_id)
            # An epoch may never claim requirements its generation does not
            # govern (no orphan and no over-claiming linkage).
            gen = self.generation(epoch.dataset, epoch.schema_generation)
            extra = set(epoch.observation_requirements) - set(
                gen.observation_requirements)
            if extra:
                raise DataVersioningError(
                    "EVIDENCE_EPOCH_UNGOVERNED_REQUIREMENTS:" + epoch.epoch_id)
            if (epoch.predecessor_epoch_id is not None
                    and epoch.predecessor_epoch_id not in self._epochs):
                raise DataVersioningError(
                    "EVIDENCE_EPOCH_MISSING_PREDECESSOR:" + epoch.epoch_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": POLICY_ID,
            "schema_generations": [
                g.to_dict() for g in sorted(
                    self._generations.values(),
                    key=lambda g: (g.dataset, g.generation))
            ],
            "producer_versions": [
                p.to_dict() for p in sorted(
                    self._producers.values(),
                    key=lambda p: (p.dataset, p.producer_version))
            ],
            "evidence_epochs": [
                e.to_dict() for e in sorted(
                    self._epochs.values(), key=lambda e: e.epoch_id)
            ],
        }


__all__ = [
    "ADDITIVE_SCHEMA_EVOLUTION", "AXIS_UNASSERTED",
    "BREAKING_DATASET_EVOLUTION", "COMPATIBILITY_CLASSES",
    "DataVersioningError", "EvidenceEpoch", "EvolutionAssessment",
    "MATERIAL_INCOMPATIBILITY_AXES", "POLICY_ID", "POLICY_STAMP",
    "ProducerVersion", "SchemaGeneration", "STAMP", "VersionRegistry",
    "canonical_json", "classify_evolution", "fingerprint",
    "producer_fingerprint", "schema_fingerprint",
]
