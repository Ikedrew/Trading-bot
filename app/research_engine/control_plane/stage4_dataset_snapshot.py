"""STAGE 4 CANONICAL DATASET SNAPSHOT (IMMUTABLE POPULATION) AUTHORITY.

Refinement 2 of the Stage 4 governance sequence.  One authority, one policy,
fail closed.  This module separates two identities that were previously used
interchangeably:

    schema_version / schema_generation
        HOW records must be interpreted (structure / semantics contract).

    dataset_snapshot_id
        WHICH exact, immutable population of records an evidence set consumed.

The Stage 4 versioning overlay historically carried ``dataset_version`` values
that are schema-registry strings (for example ``shadow_runtime_v1``).  A schema
identity does NOT identify a population: the same schema describes a dataset
whose contents keep growing.  ``dataset_version`` therefore stays exactly as it
was (historical artifacts are never rewritten) but it is now explicitly
classified as a SCHEMA identifier, and every governed population gets its own
content-bound ``DSNAP-...`` identity in a separate namespace.

DESIGN DECISION - CONSOLIDATION, NOT A SECOND FINGERPRINT SYSTEM
---------------------------------------------------------------
* The BASE dataset/schema/generation authority stays
  ``core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY`` (read-only).
* The generation/producer/epoch overlay stays ``stage4_data_versioning``.
* The requirement/evidence-set identity stays ``stage4_identity``.
* The RECORD-level content digest is delegated to the repository's existing
  dataset fingerprinting utility
  (``research_engine.lifecycle.dataset_fingerprint.compute_content_hash``).
  When only a governed POPULATION DESCRIPTOR is available (the case for the
  already-persisted historical audit populations) the digest is computed over
  that descriptor and is labelled ``GOVERNED_POPULATION_DESCRIPTOR`` so a
  consumer can never mistake it for a record-byte digest.

WHAT THIS MODULE DOES NOT DO
----------------------------
No governed satisfaction decision, no re-entry authorization, no scientific
result lineage propagation, no invalidation/regression propagation.  It only
makes evidence identity structurally trustworthy: every governed evidence set
can name the exact immutable population(s) it used, under an explicit schema
interpretation and explicit producer lineage.

CONTROL-PLANE ONLY: no S3 write, no backfill, no historical mutation.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane import stage4_data_versioning as V
from research_engine.control_plane import stage4_identity as I


SNAPSHOT_POLICY_ID = "stage4_dataset_snapshot_policy_v1"
STAMP = "20260929"
STORE_SCHEMA = 1

ROOT = I.ROOT
AUDIT_PATH = (
    ROOT / "analysis" / "assurance" /
    "stage4_observation_dataset_audit_20260929.json")
VERSIONING_POLICY_PATH = (
    ROOT / "analysis" / "assurance" /
    "stage4_data_versioning_policy_20260929.json")

#: Durable authority (additive; no historical artifact is rewritten).
CANONICAL_STATE_PATH = (
    ROOT / "research_engine" / "control_plane" /
    "stage4_dataset_snapshot_state.json")
ARTIFACT_JSON_PATH = (
    ROOT / "analysis" / "assurance" /
    "stage4_dataset_snapshot_registry_20260929.json")
ARTIFACT_MD_PATH = (
    ROOT / "analysis" / "assurance" /
    "stage4_dataset_snapshot_registry_20260929.md")

# ── population state ───────────────────────────────────────────────────────
POPULATION_FROZEN = "FROZEN"
POPULATION_MUTABLE = "MUTABLE"
POPULATION_STATES = frozenset({POPULATION_FROZEN, POPULATION_MUTABLE})

# ── content digest scope ───────────────────────────────────────────────────
DIGEST_SCOPE_RECORD_BYTES = "RECORD_BYTES"
DIGEST_SCOPE_POPULATION_DESCRIPTOR = "GOVERNED_POPULATION_DESCRIPTOR"
DIGEST_SCOPES = frozenset({
    DIGEST_SCOPE_RECORD_BYTES, DIGEST_SCOPE_POPULATION_DESCRIPTOR,
})

# ── generation state ───────────────────────────────────────────────────────
GENERATION_CONFIRMED = "SINGLE_GENERATION_CONFIRMED"
GENERATION_UNASSERTED = "MIXED_OR_UNASSERTED_GENERATION"
GENERATION_STATES = frozenset({GENERATION_CONFIRMED, GENERATION_UNASSERTED})

# ── producer identity state ────────────────────────────────────────────────
PRODUCER_BOUND = "PRODUCER_BOUND"
PRODUCER_UNASSERTED = "PRODUCER_UNASSERTED"
PRODUCER_STATES = frozenset({PRODUCER_BOUND, PRODUCER_UNASSERTED})

# ── population classes ─────────────────────────────────────────────────────
AUDIT_BOUNDARY_POPULATION = "AUDIT_BOUNDARY_PERSISTED_POPULATION"
GOVERNED_REQUIREMENT_POPULATION = "GOVERNED_REQUIREMENT_POPULATION"
POPULATION_CLASSES = frozenset({
    AUDIT_BOUNDARY_POPULATION, GOVERNED_REQUIREMENT_POPULATION,
})

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# ── evidence-set / epoch snapshot identity states (shared via stage4_identity)
SNAPSHOT_BOUND = I.SNAPSHOT_IDENTITY_BOUND
SNAPSHOT_UNRESOLVED_LIVE = I.SNAPSHOT_IDENTITY_UNRESOLVED_LIVE
SNAPSHOT_UNRESOLVED_HISTORICAL = I.SNAPSHOT_IDENTITY_UNRESOLVED_HISTORICAL
SNAPSHOT_UNRESOLVED_NEVER_PERSISTED = I.SNAPSHOT_IDENTITY_UNRESOLVED_NEVER_PERSISTED

# ── read-side validation states ────────────────────────────────────────────
IDENTITY_VERIFIED = "VERIFIED"
IDENTITY_LEGACY_UNRESOLVED = I.LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY
SNAPSHOT_MISSING = "DATASET_SNAPSHOT_MISSING"
SNAPSHOT_UNKNOWN = "DATASET_SNAPSHOT_UNKNOWN"
SNAPSHOT_DATASET_MISMATCH = "EVIDENCE_SET_DATASET_MISMATCH"
SNAPSHOT_INCOMPATIBLE = "EVIDENCE_SET_REFERENCES_INCOMPATIBLE_SNAPSHOTS"
SCHEMA_MISMATCH = "SCHEMA_IDENTITY_MISMATCH"
GENERATION_MIXED = "SCHEMA_GENERATION_MIXED"
#: Read-side state: a required generation cannot be verified for a population
#: whose generation is deliberately UNASSERTED (distinct from the population's
#: own ``generation_state`` constants above).
REQUIRED_GENERATION_UNASSERTED = "SCHEMA_GENERATION_UNASSERTED"
GENERATION_MISMATCH = "SCHEMA_GENERATION_MISMATCH"
CONTENT_DIGEST_MISMATCH = "SNAPSHOT_CONTENT_DIGEST_MISMATCH"
MEMBER_DATASET_MISMATCH = "EVIDENCE_MEMBER_DATASET_MISMATCH"
MEMBER_SNAPSHOT_MISMATCH = "EVIDENCE_MEMBER_SNAPSHOT_MISMATCH"
REQUIRED_PRODUCER_UNASSERTED = "PRODUCER_LINEAGE_UNASSERTED"
PRODUCER_MISMATCH = "PRODUCER_LINEAGE_MISMATCH"

#: Epoch statuses whose population can no longer move.
FROZEN_EPOCH_STATUSES = frozenset({
    V.EvidenceEpoch.STATUS_FROZEN, V.EvidenceEpoch.STATUS_RETIRED,
})


class DatasetSnapshotError(RuntimeError):
    """A dataset population identity invariant was violated (fail closed)."""


def canonical_json(payload: Any) -> str:
    """Deterministic JSON rendering shared with the versioning authority."""
    return V.canonical_json(payload)


def fingerprint(payload: Any) -> str:
    """sha256 over canonical JSON (stable across processes and key order)."""
    return V.fingerprint(payload)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise DatasetSnapshotError(
            "SNAPSHOT_AUTHORITY_UNREADABLE:" + str(path)) from exc


def _iso_day(stamp: str) -> str:
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"


def record_content_digest(records: Sequence[Mapping[str, Any]]) -> str:
    """Record-byte content digest via the repository's existing utility.

    Delegation (not duplication):
    ``research_engine.lifecycle.dataset_fingerprint`` already owns canonical
    record serialisation and content hashing.
    """
    if not records:
        raise DatasetSnapshotError("EMPTY_POPULATION_CANNOT_BE_CONTENT_ADDRESSED")
    from research_engine.lifecycle.dataset_fingerprint import compute_content_hash
    return compute_content_hash(list(records))


def population_descriptor_digest(material: Mapping[str, Any]) -> str:
    """Digest of a GOVERNED POPULATION DESCRIPTOR (not of record bytes).

    Used only where the exact rows cannot be re-read (already-persisted
    historical populations).  The digest is always recorded together with
    ``content_digest_scope = GOVERNED_POPULATION_DESCRIPTOR`` so no consumer can
    mistake it for a record-byte digest.
    """
    return fingerprint({"population_descriptor": dict(material)})



# ═══════════════════════════════════════════════════════════════════════════
# THE CANONICAL IMMUTABLE POPULATION RECORD
# ═══════════════════════════════════════════════════════════════════════════

#: The immutable material a snapshot identity is derived from.
#:
#: Deliberately EXCLUDED: ``created_at``/``frozen_at`` (clock values are not
#: identity) and producer identity (lineage is verified separately and is not
#: allowed to stand in for population identity).
IDENTITY_MATERIAL_FIELDS: tuple[str, ...] = (
    "dataset_name",
    "schema_version",
    "schema_generation",
    "content_digest",
    "content_digest_scope",
    "record_count",
    "identity_grain",
    "source_boundaries",
    "population_filters",
    "temporal_bounds",
)


@dataclass(frozen=True)
class DatasetSnapshot:
    """One exact, immutable governed data population.

    ``dataset_name``          the dataset FAMILY (``shadow_runtime``)
    ``schema_version``        the interpretation contract (``shadow_runtime_v1``)
    ``schema_generation``     the governed structural generation, or None when
                              the persisted authority cannot separate generations
    ``content_digest``        content binding (scope recorded explicitly)
    ``record_count``          exact size of the population
    ``identity_grain``        what one record is
    ``producer_*``            lineage of the code that emitted this population
    ``source_boundaries``     where the population lives (prefix/objects/dates)
    ``population_filters``    the exact filters that produced this population
    ``temporal_bounds``       the population's own time window, when bounded
    """

    dataset_snapshot_id: str
    dataset_name: str
    schema_version: str
    schema_generation: int | None
    content_digest: str
    content_digest_scope: str
    record_count: int
    identity_grain: str
    identity_grain_evidence: str
    producer_version: str | None
    producer_fingerprint: str | None
    producer_identity_state: str
    source_boundaries: tuple[str, ...]
    population_filters: tuple[str, ...]
    temporal_bounds: tuple[str, str] | None
    population_class: str
    population_state: str
    generation_state: str
    generation_evidence: str
    observation_requirements: tuple[str, ...]
    created_at: str
    frozen_at: str
    evidence_citations: tuple[str, ...]
    audit_authority_fingerprint: str


    def __post_init__(self) -> None:
        try:
            name, schema, snapshot_id = I.validate_dataset_identity_separation(
                dataset_name=self.dataset_name,
                schema_version=self.schema_version,
                dataset_snapshot_id=self.dataset_snapshot_id,
            )
        except I.Stage4IdentityError as exc:
            raise DatasetSnapshotError(str(exc)) from exc
        object.__setattr__(self, "dataset_name", name)
        if schema is None:
            raise DatasetSnapshotError("SNAPSHOT_WITHOUT_SCHEMA_VERSION")
        if not I.is_schema_identifier(schema):
            raise DatasetSnapshotError(
                "MALFORMED_SNAPSHOT_SCHEMA_VERSION:" + schema)
        object.__setattr__(self, "schema_version", schema)
        object.__setattr__(self, "dataset_snapshot_id", str(snapshot_id))
        if self.population_state not in POPULATION_STATES:
            raise DatasetSnapshotError("UNKNOWN_POPULATION_STATE")
        if self.population_state != POPULATION_FROZEN:
            # A growing population has no stable identity.  It must never be
            # presented as immutable scientific evidence.
            raise DatasetSnapshotError(
                "MUTABLE_POPULATION_CANNOT_BE_DATASET_SNAPSHOT:"
                + str(self.dataset_name))
        if self.content_digest_scope not in DIGEST_SCOPES:
            raise DatasetSnapshotError("UNKNOWN_CONTENT_DIGEST_SCOPE")
        if self.generation_state not in GENERATION_STATES:
            raise DatasetSnapshotError("UNKNOWN_GENERATION_STATE")
        if self.producer_identity_state not in PRODUCER_STATES:
            raise DatasetSnapshotError("UNKNOWN_PRODUCER_IDENTITY_STATE")
        if self.producer_identity_state == PRODUCER_BOUND:
            if not self.producer_version or not self.producer_fingerprint:
                raise DatasetSnapshotError("PRODUCER_IDENTITY_INCOMPLETE")
        elif self.producer_version or self.producer_fingerprint:
            raise DatasetSnapshotError("UNASSERTED_PRODUCER_WITH_IDENTITY")
        if self.generation_state == GENERATION_CONFIRMED:
            if not isinstance(self.schema_generation, int) \
                    or self.schema_generation < 1:
                raise DatasetSnapshotError("CONFIRMED_GENERATION_REQUIRED")
        elif self.schema_generation is not None:
            # An unasserted generation must never carry a number: a number
            # would be an unproven claim about population content.
            raise DatasetSnapshotError("UNASSERTED_GENERATION_WITH_NUMBER")
        if int(self.record_count) <= 0:
            raise DatasetSnapshotError("EMPTY_POPULATION_CANNOT_BE_SNAPSHOT")
        if not _SHA256_PATTERN.fullmatch(str(self.content_digest or "")):
            raise DatasetSnapshotError("INVALID_SNAPSHOT_CONTENT_DIGEST")
        if not str(self.identity_grain or "").strip():
            raise DatasetSnapshotError("SNAPSHOT_WITHOUT_IDENTITY_GRAIN")
        if not self.source_boundaries or any(
                not str(value).strip() for value in self.source_boundaries):
            raise DatasetSnapshotError("SNAPSHOT_WITHOUT_SOURCE_BOUNDARIES")
        if not self.population_filters or any(
                not str(value).strip() for value in self.population_filters):
            raise DatasetSnapshotError("SNAPSHOT_WITHOUT_POPULATION_FILTERS")
        if self.population_class not in POPULATION_CLASSES:
            raise DatasetSnapshotError("UNKNOWN_POPULATION_CLASS")
        if self.temporal_bounds is not None:
            if len(self.temporal_bounds) != 2 or any(
                    not str(value).strip() for value in self.temporal_bounds):
                raise DatasetSnapshotError("INVALID_TEMPORAL_BOUNDS")
            if str(self.temporal_bounds[0]) > str(self.temporal_bounds[1]):
                raise DatasetSnapshotError("INVERTED_TEMPORAL_BOUNDS")
        if not str(self.frozen_at or "").strip():
            raise DatasetSnapshotError("SNAPSHOT_WITHOUT_FROZEN_AT")
        try:
            for rid in self.observation_requirements:
                I.validate_requirement_id(rid)
        except I.Stage4IdentityError as exc:
            raise DatasetSnapshotError(str(exc)) from exc
        object.__setattr__(self, "record_count", int(self.record_count))
        object.__setattr__(self, "source_boundaries",
                           tuple(str(x) for x in self.source_boundaries))
        object.__setattr__(self, "population_filters",
                           tuple(str(x) for x in self.population_filters))
        object.__setattr__(self, "evidence_citations",
                           tuple(str(x) for x in self.evidence_citations))
        object.__setattr__(self, "observation_requirements",
                           tuple(sorted(set(self.observation_requirements))))
        if derive_dataset_snapshot_id(self) != self.dataset_snapshot_id:
            # Fail closed: an identity that does not follow from the immutable
            # material would allow one ID to describe two populations.
            raise DatasetSnapshotError(
                "DATASET_SNAPSHOT_ID_NOT_DERIVED_FROM_POPULATION:"
                + str(self.dataset_snapshot_id))


    # -- identity derivation (content-bound, clock-free) -------------------

    def identity_material(self) -> dict[str, Any]:
        """The immutable material the snapshot identity is derived from."""
        return {
            "dataset_name": self.dataset_name,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "content_digest": self.content_digest,
            "content_digest_scope": self.content_digest_scope,
            "record_count": self.record_count,
            "identity_grain": self.identity_grain,
            "source_boundaries": list(self.source_boundaries),
            "population_filters": list(self.population_filters),
            "temporal_bounds": (None if self.temporal_bounds is None
                                else list(self.temporal_bounds)),
        }

    def population_digest(self) -> str:
        """Digest of this population's governed descriptor.

        A SECOND, self-consistency digest over the whole immutable record
        (``content_digest`` included).  ``content_digest`` binds the population
        to its content; this digest binds the RECORD to its own fields, so a
        tampered persisted record is detectable on reload.
        """
        return population_descriptor_digest(self.identity_material())

    def descriptor(self) -> dict[str, Any]:
        """The full immutable population record (JSON-ready)."""
        return self.to_dict()

    def content_axes(self) -> dict[str, Any]:
        """The axes on which two records claiming one ID must agree exactly."""
        return {
            "dataset_name": self.dataset_name,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "content_digest": self.content_digest,
            "content_digest_scope": self.content_digest_scope,
            "record_count": self.record_count,
            "identity_grain": self.identity_grain,
            "source_boundaries": list(self.source_boundaries),
            "population_filters": list(self.population_filters),
            "temporal_bounds": (None if self.temporal_bounds is None
                                else list(self.temporal_bounds)),
            "producer_version": self.producer_version,
            "producer_fingerprint": self.producer_fingerprint,
            "population_class": self.population_class,
            "observation_requirements": list(self.observation_requirements),
        }

    @property
    def generation_identity(self) -> str:
        """``schema_generation=<n>`` or the explicit UNASSERTED marker."""
        if self.schema_generation is None:
            return "schema_generation=" + V.AXIS_UNASSERTED
        return "schema_generation=" + str(self.schema_generation)

    def locator(self) -> str:
        return (f"schema_version={self.schema_version};"
                f"{self.generation_identity};"
                f"producer_version={self.producer_version or V.AXIS_UNASSERTED}")



    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "dataset_name": self.dataset_name,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "generation_state": self.generation_state,
            "generation_evidence": self.generation_evidence,
            "content_digest": self.content_digest,
            "content_digest_scope": self.content_digest_scope,
            "record_count": self.record_count,
            "identity_grain": self.identity_grain,
            "identity_grain_evidence": self.identity_grain_evidence,
            "producer_version": self.producer_version,
            "producer_fingerprint": self.producer_fingerprint,
            "producer_identity_state": self.producer_identity_state,
            "source_boundaries": list(self.source_boundaries),
            "population_filters": list(self.population_filters),
            "temporal_bounds": (None if self.temporal_bounds is None
                                else list(self.temporal_bounds)),
            "population_class": self.population_class,
            "population_state": self.population_state,
            "observation_requirements": list(self.observation_requirements),
            "created_at": self.created_at,
            "frozen_at": self.frozen_at,
            "evidence_citations": list(self.evidence_citations),
            "audit_authority_fingerprint": self.audit_authority_fingerprint,
            "population_descriptor_digest": self.population_digest(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSnapshot":
        """Reload a persisted snapshot, re-deriving and re-checking its ID."""
        if not isinstance(value, Mapping):
            raise DatasetSnapshotError("DATASET_SNAPSHOT_NOT_AN_OBJECT")
        bounds = value.get("temporal_bounds")
        claimed_descriptor = value.get("population_descriptor_digest")
        item = cls(
            dataset_snapshot_id=str(value.get("dataset_snapshot_id") or ""),
            dataset_name=str(value.get("dataset_name") or ""),
            schema_version=str(value.get("schema_version") or ""),
            schema_generation=value.get("schema_generation"),
            content_digest=str(value.get("content_digest") or ""),
            content_digest_scope=str(value.get("content_digest_scope") or ""),
            record_count=value.get("record_count"),
            identity_grain=str(value.get("identity_grain") or ""),
            identity_grain_evidence=str(value.get("identity_grain_evidence") or ""),
            producer_version=(None if value.get("producer_version") is None
                              else str(value["producer_version"])),
            producer_fingerprint=(None if value.get("producer_fingerprint") is None
                                  else str(value["producer_fingerprint"])),
            producer_identity_state=str(value.get("producer_identity_state") or ""),
            source_boundaries=tuple(value.get("source_boundaries") or ()),
            population_filters=tuple(value.get("population_filters") or ()),
            temporal_bounds=(None if not bounds
                             else (str(bounds[0]), str(bounds[1]))),
            population_class=str(value.get("population_class") or ""),
            population_state=str(value.get("population_state") or ""),
            generation_state=str(value.get("generation_state") or ""),
            generation_evidence=str(value.get("generation_evidence") or ""),
            observation_requirements=tuple(
                value.get("observation_requirements") or ()),
            created_at=str(value.get("created_at") or ""),
            frozen_at=str(value.get("frozen_at") or ""),
            evidence_citations=tuple(value.get("evidence_citations") or ()),
            audit_authority_fingerprint=str(
                value.get("audit_authority_fingerprint") or ""),
        )
        if claimed_descriptor is not None and str(
                claimed_descriptor) != item.population_digest():
            raise DatasetSnapshotError(
                "CONFLICTING_DATASET_SNAPSHOT_CONTENT:"
                + item.dataset_snapshot_id)
        return item


def derive_dataset_snapshot_id(snapshot: DatasetSnapshot) -> str:
    """Content-bound, clock-free population identity: ``DSNAP-<24 hex>``."""
    material = snapshot.identity_material()
    missing = [key for key in IDENTITY_MATERIAL_FIELDS if key not in material]
    if missing:
        raise DatasetSnapshotError(
            "INCOMPLETE_POPULATION_IDENTITY_MATERIAL:" + ",".join(missing))
    return I.DATASET_SNAPSHOT_ID_PREFIX + fingerprint(material)[:24].upper()


# ═══════════════════════════════════════════════════════════════════════════
# FREEZING A POPULATION  (mutable live family -> immutable governed snapshot)
# ═══════════════════════════════════════════════════════════════════════════

def assert_frozen_population(*, population_state: str, dataset_name: str) -> None:
    """A growing population may never be presented as immutable evidence."""
    if population_state != POPULATION_FROZEN:
        raise DatasetSnapshotError(
            "MUTABLE_POPULATION_CANNOT_BE_DATASET_SNAPSHOT:" + str(dataset_name))


def freeze_population(
    *,
    dataset_name: str,
    schema_version: str,
    identity_grain: str,
    source_boundaries: Sequence[str],
    population_filters: Sequence[str] = (
        "scope=ALL_PERSISTED_RECORDS_AT_AUDIT_BOUNDARY",),
    temporal_bounds: Sequence[str] | None = None,
    population_class: str = AUDIT_BOUNDARY_POPULATION,
    record_count: int | None = None,
    identity_grain_evidence: str = "",
    schema_generation: int | None = None,
    generation_state: str = GENERATION_UNASSERTED,
    generation_evidence: str = "",
    producer_version: str | None = None,
    producer_fingerprint: str | None = None,
    observation_requirements: Sequence[str] = (),
    records: Sequence[Mapping[str, Any]] | None = None,
    content_digest: str | None = None,
    content_digest_scope: str | None = None,
    frozen_at: str | None = None,
    created_at: str | None = None,
    evidence_citations: Sequence[str] = (),
    audit_authority_fingerprint: str = "",
    population_state: str = POPULATION_FROZEN,
) -> DatasetSnapshot:
    """Mint the canonical immutable population record for an exact population.

    FAIL CLOSED:
      * a non-frozen population state is refused outright;
      * an empty population is refused (it cannot be content-addressed);
      * record-byte digests REQUIRE the records themselves, and the declared
        ``record_count`` must match the records supplied;
      * without records an explicit ``content_digest`` is required, and the
        digest scope is recorded as ``GOVERNED_POPULATION_DESCRIPTOR`` so no
        consumer can mistake it for a record-byte digest.
    """
    assert_frozen_population(
        population_state=population_state, dataset_name=dataset_name)
    boundaries = tuple(str(x) for x in source_boundaries)
    filters = tuple(str(x) for x in population_filters)
    bounds = (None if not temporal_bounds
              else (str(temporal_bounds[0]), str(temporal_bounds[1])))
    stamp = str(frozen_at or _iso_day(STAMP))

    if records is not None:
        if content_digest is not None:
            raise DatasetSnapshotError("BOTH_RECORDS_AND_CONTENT_DIGEST_SUPPLIED")
        digest = record_content_digest(records)
        scope = DIGEST_SCOPE_RECORD_BYTES
        count = len(records)
    else:
        if not str(content_digest or "").strip():
            raise DatasetSnapshotError("MISSING_CONTENT_DIGEST")
        if content_digest_scope == DIGEST_SCOPE_RECORD_BYTES:
            raise DatasetSnapshotError(
                "RECORD_BYTE_DIGEST_REQUIRES_RECORDS")
        digest = str(content_digest)
        scope = str(content_digest_scope or DIGEST_SCOPE_POPULATION_DESCRIPTOR)
        if record_count is None:
            raise DatasetSnapshotError("MISSING_RECORD_COUNT")
        count = int(record_count)

    if record_count is not None and int(record_count) != count:
        raise DatasetSnapshotError("RECORD_COUNT_DOES_NOT_MATCH_POPULATION")
    if count <= 0:
        raise DatasetSnapshotError("EMPTY_POPULATION_CANNOT_BE_SNAPSHOT")
    if generation_state == GENERATION_CONFIRMED:
        if not isinstance(schema_generation, int) or schema_generation < 1:
            raise DatasetSnapshotError("CONFIRMED_GENERATION_REQUIRED")
    elif schema_generation is not None:
        raise DatasetSnapshotError("UNASSERTED_GENERATION_WITH_NUMBER")
    if producer_version or producer_fingerprint:
        if not (producer_version and producer_fingerprint):
            raise DatasetSnapshotError("PRODUCER_IDENTITY_INCOMPLETE")
        producer_state = PRODUCER_BOUND
    else:
        producer_state = PRODUCER_UNASSERTED

    payload: dict[str, Any] = {
        "dataset_name": dataset_name,
        "schema_version": schema_version,
        "schema_generation": schema_generation,
        "content_digest": digest,
        "content_digest_scope": scope,
        "record_count": count,
        "identity_grain": identity_grain,
        "identity_grain_evidence": identity_grain_evidence,
        "producer_version": producer_version,
        "producer_fingerprint": producer_fingerprint,
        "producer_identity_state": producer_state,
        "source_boundaries": list(boundaries),
        "population_filters": list(filters),
        "temporal_bounds": (None if bounds is None else list(bounds)),
        "population_class": population_class,
        "population_state": population_state,
        "generation_state": generation_state,
        "generation_evidence": generation_evidence,
        "observation_requirements": list(observation_requirements),
        "created_at": str(created_at or stamp),
        "frozen_at": stamp,
        "evidence_citations": list(evidence_citations),
        "audit_authority_fingerprint": str(audit_authority_fingerprint),
    }
    payload["dataset_snapshot_id"] = I.DATASET_SNAPSHOT_ID_PREFIX + fingerprint(
        {key: payload[key] for key in IDENTITY_MATERIAL_FIELDS})[:24].upper()
    return DatasetSnapshot(**payload)



# ═══════════════════════════════════════════════════════════════════════════
# MUTABLE LIVE POPULATION  (explicitly NOT a snapshot)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LiveDatasetPopulation:
    """A dataset FAMILY whose population is still growing, or never persisted.

    There is deliberately no dataset snapshot identity here: an actively
    growing population has no stable population identity, so any
    ``dataset_snapshot_id`` published for it would be a false claim.  A frozen
    VIEW of it becomes a :class:`DatasetSnapshot` through
    :func:`freeze_population` once its population is closed.
    """

    dataset_name: str
    schema_version: str
    schema_generation: int | None
    observed_record_count: int
    observed_at: str
    snapshot_identity_state: str = SNAPSHOT_UNRESOLVED_LIVE
    reason: str = (
        "The population is still growing (or was never persisted), so no "
        "immutable population identity exists yet."
    )

    def __post_init__(self) -> None:
        try:
            name, schema, _ = I.validate_dataset_identity_separation(
                dataset_name=self.dataset_name,
                schema_version=self.schema_version)
        except I.Stage4IdentityError as exc:
            raise DatasetSnapshotError(str(exc)) from exc
        if schema is None:
            raise DatasetSnapshotError("LIVE_POPULATION_WITHOUT_SCHEMA_VERSION")
        if not I.is_schema_identifier(schema):
            raise DatasetSnapshotError(
                "MALFORMED_LIVE_SCHEMA_VERSION:" + schema)
        if self.snapshot_identity_state not in {
                SNAPSHOT_UNRESOLVED_LIVE,
                SNAPSHOT_UNRESOLVED_NEVER_PERSISTED}:
            raise DatasetSnapshotError("INVALID_LIVE_SNAPSHOT_IDENTITY_STATE")
        if int(self.observed_record_count) < 0:
            raise DatasetSnapshotError("NEGATIVE_LIVE_POPULATION_COUNT")
        object.__setattr__(self, "dataset_name", name)
        object.__setattr__(self, "schema_version", schema)
        object.__setattr__(self, "observed_record_count",
                           int(self.observed_record_count))

    @property
    def dataset_snapshot_id(self) -> None:
        """Always ``None``: a live population has no snapshot identity."""
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "dataset_snapshot_id": None,
            "snapshot_identity_state": self.snapshot_identity_state,
            "population_state": POPULATION_MUTABLE,
            "observed_record_count": int(self.observed_record_count),
            "observed_at": self.observed_at,
            "reason": self.reason,
        }

    def freeze(self, **kwargs: Any) -> DatasetSnapshot:
        """Freeze an exact view of this population into a snapshot record.

        The caller MUST supply the frozen view's own immutable material
        (population filters, source boundaries, digest, record count).  A live
        population with no records cannot be frozen.
        """
        if int(self.observed_record_count) <= 0:
            raise DatasetSnapshotError(
                "EMPTY_POPULATION_CANNOT_BE_SNAPSHOT:" + self.dataset_name)
        params = {
            "dataset_name": self.dataset_name,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "generation_state": (GENERATION_CONFIRMED
                                 if self.schema_generation is not None
                                 else GENERATION_UNASSERTED),
        }
        params.update(kwargs)
        return freeze_population(**params)


def live_population(
    *, dataset_name: str, schema_version: str,
    schema_generation: int | None, observed_record_count: int,
    observed_at: str | None = None,
    snapshot_identity_state: str = SNAPSHOT_UNRESOLVED_LIVE,
    reason: str | None = None,
) -> LiveDatasetPopulation:
    """Describe a mutable dataset family that is NOT an evidence snapshot."""
    if snapshot_identity_state not in {
            SNAPSHOT_UNRESOLVED_LIVE,
            SNAPSHOT_UNRESOLVED_NEVER_PERSISTED}:
        raise DatasetSnapshotError("INVALID_LIVE_SNAPSHOT_IDENTITY_STATE")
    params: dict[str, Any] = {}
    if reason is not None:
        params["reason"] = reason
    return LiveDatasetPopulation(
        dataset_name=str(dataset_name),
        schema_version=str(schema_version),
        schema_generation=schema_generation,
        observed_record_count=int(observed_record_count),
        observed_at=str(observed_at or _iso_day(STAMP)),
        snapshot_identity_state=snapshot_identity_state,
        **params,
    )



# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY - the persisted population authority
# ═══════════════════════════════════════════════════════════════════════════

#: Axes compared when a persisted record claims an already-registered ID.
_CONFLICT_AXES: tuple[tuple[str, str], ...] = (
    ("content_digest", "CONFLICTING_DATASET_SNAPSHOT_CONTENT"),
    ("content_digest_scope", "CONFLICTING_DATASET_SNAPSHOT_CONTENT_SCOPE"),
    ("record_count", "CONFLICTING_DATASET_SNAPSHOT_RECORD_COUNT"),
    ("population_filters", "CONFLICTING_DATASET_SNAPSHOT_POPULATION"),
    ("source_boundaries", "CONFLICTING_DATASET_SNAPSHOT_POPULATION"),
    ("temporal_bounds", "CONFLICTING_DATASET_SNAPSHOT_POPULATION"),
    ("identity_grain", "CONFLICTING_DATASET_SNAPSHOT_POPULATION"),
    ("population_class", "CONFLICTING_DATASET_SNAPSHOT_POPULATION"),
    ("observation_requirements", "CONFLICTING_DATASET_SNAPSHOT_REQUIREMENTS"),
)


class DatasetSnapshotRegistry:
    """Collision-safe registry of immutable population identities.

    One ID may only ever describe one population.  Identity is content-bound:
    re-registering the same population is idempotent, while re-using an ID for
    different content fails closed.
    """

    SCHEMA = 1

    def __init__(self, snapshots: Iterable[DatasetSnapshot] = ()) -> None:
        self._snapshots: dict[str, DatasetSnapshot] = {}
        for snapshot in snapshots:
            self.add(snapshot)

    # -- registration ------------------------------------------------------

    def add(self, snapshot: DatasetSnapshot) -> DatasetSnapshot:
        if not isinstance(snapshot, DatasetSnapshot):
            raise DatasetSnapshotError("NOT_A_DATASET_SNAPSHOT")
        existing = self._snapshots.get(snapshot.dataset_snapshot_id)
        if existing is None:
            self._snapshots[snapshot.dataset_snapshot_id] = snapshot
            return snapshot
        if existing.content_axes() == snapshot.content_axes():
            return existing
        self._raise_conflict(existing, snapshot.content_axes())
        raise AssertionError("unreachable")

    def add_persisted(self, record: Mapping[str, Any]) -> DatasetSnapshot:
        """Adopt one persisted record, failing closed on a conflicting reuse."""
        if not isinstance(record, Mapping):
            raise DatasetSnapshotError("DATASET_SNAPSHOT_NOT_AN_OBJECT")
        # Parse every persisted record in full even when its ID is already
        # registered.  A partial duplicate must not bypass the immutable-record
        # checks merely because the registry has seen the identifier before.
        return self.add(DatasetSnapshot.from_dict(record))

    @staticmethod
    def _raise_conflict(existing: DatasetSnapshot, axes: Mapping[str, Any]) -> None:
        snapshot_id = existing.dataset_snapshot_id
        current = existing.content_axes()
        for key, code in _CONFLICT_AXES:
            if axes[key] != current[key]:
                raise DatasetSnapshotError(f"{code}:{snapshot_id}")
        if axes["schema_version"] != current["schema_version"] \
                or axes["schema_generation"] != current["schema_generation"]:
            raise DatasetSnapshotError(
                "CONFLICTING_DATASET_SNAPSHOT_SCHEMA:" + snapshot_id)
        if axes["producer_version"] != current["producer_version"] \
                or axes["producer_fingerprint"] != current["producer_fingerprint"]:
            raise DatasetSnapshotError(
                "CONFLICTING_DATASET_SNAPSHOT_PRODUCER:" + snapshot_id)
        raise DatasetSnapshotError(
            "CONFLICTING_DATASET_SNAPSHOT_DATASET:" + snapshot_id)


    # -- resolution --------------------------------------------------------

    def require(self, dataset_snapshot_id: str) -> DatasetSnapshot:
        """Resolve a snapshot identity, or fail closed."""
        resolved = I.validate_dataset_snapshot_id(
            dataset_snapshot_id, allow_none=False)
        try:
            return self._snapshots[str(resolved)]
        except KeyError:
            raise DatasetSnapshotError(
                "UNKNOWN_DATASET_SNAPSHOT_ID:" + str(resolved)) from None

    def resolve(self, dataset_snapshot_id: str) -> DatasetSnapshot:
        return self.require(dataset_snapshot_id)

    def is_registered(self, dataset_snapshot_id: str) -> bool:
        return str(dataset_snapshot_id) in self._snapshots

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._snapshots))

    def __len__(self) -> int:
        return len(self._snapshots)

    def dataset_names(self) -> tuple[str, ...]:
        return tuple(sorted({s.dataset_name for s in self._snapshots.values()}))

    def snapshots_for_dataset(
            self, dataset_name: str) -> tuple[DatasetSnapshot, ...]:
        name = str(dataset_name)
        return tuple(sorted(
            (s for s in self._snapshots.values() if s.dataset_name == name),
            key=lambda s: s.dataset_snapshot_id))

    def snapshots_for_requirement(
            self, requirement_id: str) -> tuple[DatasetSnapshot, ...]:
        rid = I.validate_requirement_id(requirement_id)
        return tuple(sorted(
            (s for s in self._snapshots.values()
             if rid in s.observation_requirements),
            key=lambda s: s.dataset_snapshot_id))

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "snapshots": [s.to_dict() for s in sorted(
                self._snapshots.values(), key=lambda s: s.dataset_snapshot_id)],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSnapshotRegistry":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise DatasetSnapshotError("UNKNOWN_DATASET_SNAPSHOT_REGISTRY_SCHEMA")
        rows = value.get("snapshots")
        if not isinstance(rows, list):
            raise DatasetSnapshotError("DATASET_SNAPSHOT_REGISTRY_MISSING_ROWS")
        return cls(DatasetSnapshot.from_dict(row) for row in rows)

    def save(self, path: Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        os.replace(temporary, target)

    @classmethod
    def load(cls, path: Path) -> "DatasetSnapshotRegistry":
        return cls.from_dict(_read_json(path))

    def registry_fingerprint(self) -> str:
        return fingerprint(self.to_dict())



# ═══════════════════════════════════════════════════════════════════════════
# BOOTSTRAP - the governed populations of the persisted Stage 4 audit
#
# Every fact below is ASSERTED against the persisted audit / matrix artifact at
# bootstrap time, so this table can never silently drift from the authority it
# transcribes.  Nothing here is inferred where the authority is silent: a value
# that cannot be evidenced is recorded as UNASSERTED rather than guessed.
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GovernedPopulationFact:
    """One transcribed, evidence-cited governed population fact."""

    population_key: str
    dataset_name: str
    schema_version: str
    schema_generation: int | None
    generation_state: str
    generation_evidence: str
    record_count: int
    record_count_requirement: str | None
    identity_grain: str
    identity_grain_evidence: str
    population_class: str
    population_filters: tuple[str, ...]
    producer_dataset: str | None
    producer_version: str | None
    requirement_source: str
    population_note: str

    @property
    def is_audit_boundary(self) -> bool:
        return self.population_class == AUDIT_BOUNDARY_POPULATION


#: The governed populations behind Stage 4 evidence.  ``shadow_runtime`` is the
#: canonical observation source (ROOT-01); ``decision_trace`` and
#: ``opportunities`` carry their own governed generations; ``market_context`` is
#: consulted as governed evidence for the lifecycle decision snapshot but has no
#: producer version in the Stage 4 overlay, so its lineage is recorded as
#: UNASSERTED instead of being invented.
GOVERNED_POPULATION_FACTS: tuple[GovernedPopulationFact, ...] = (
    GovernedPopulationFact(
        population_key="shadow_runtime:audit_boundary",
        dataset_name="shadow_runtime",
        schema_version="shadow_runtime_v1",
        schema_generation=1,
        generation_state=GENERATION_CONFIRMED,
        generation_evidence=(
            "No generation-2-only governed block is attested in the audited "
            "population: the generation-2 experiment-arm block is 0/66258 "
            "(experiment_arm, arm_assigned_at, arm_schema_version) and the "
            "audited shadow_runtime field inventory carries no decision_snapshot "
            "or lifecycle_m5_path path. Absence evidence, not a per-record "
            "generation stamp."),
        record_count=66258,
        record_count_requirement=None,
        identity_grain=(
            "one appended row per shadow lifecycle event; top-level "
            "canonical_opportunity_id, symbol and shadow_trade_id on all rows"),
        identity_grain_evidence=(
            "audit persisted_dataset_universe.shadow_runtime.coverage: "
            "canonical_opportunity_id 66258/66258 (top-level), symbol "
            "66258/66258, shadow_trade_id 66258/66258 (25028 nullish); the "
            "identity block is carried by the 20460 OPEN rows"),
        population_class=AUDIT_BOUNDARY_POPULATION,
        population_filters=("event_stream=ALL_PERSISTED_ROWS_AT_AUDIT_BOUNDARY",),
        producer_dataset="shadow_runtime",
        producer_version="shadow_runtime_producer_v1",
        requirement_source="MATRIX_AUTHORITATIVE_DATASET",
        population_note=(
            "The exact population the Stage 4 observation audit counted for "
            "shadow_runtime at the audit boundary."),
    ),
    GovernedPopulationFact(
        population_key="shadow_runtime:close_lifecycle_outcome",
        dataset_name="shadow_runtime",
        schema_version="shadow_runtime_v1",
        schema_generation=1,
        generation_state=GENERATION_CONFIRMED,
        generation_evidence=(
            "outcome is a generation-1 governed field "
            "(core/shadow observability generation-1 field inventory) and the "
            "generation-2 blocks are absent from the audited population."),
        record_count=20421,
        record_count_requirement="OR-01",
        identity_grain=(
            "one row per CLOSE lifecycle event keyed by (shadow_trade_id, "
            "canonical_opportunity_id, trade_horizon)"),
        identity_grain_evidence=(
            "matrix OR-01.required_grain 'one row per (shadow_trade_id, "
            "canonical_opportunity_id, trade_horizon)'; audit observed_coverage "
            "'20421/66258 (all 20421 CLOSE lifecycles)'"),
        population_class=GOVERNED_REQUIREMENT_POPULATION,
        population_filters=(
            "event_type=CLOSE",
            "outcome.pnl_r_multiple IS NOT NULL",
        ),
        producer_dataset="shadow_runtime",
        producer_version="shadow_runtime_producer_v1",
        requirement_source="EXPLICIT:OR-01",
        population_note=(
            "The exact CLOSE-lifecycle population that carries the OR-01 "
            "outcome R-multiple at the governed grain."),
    ),
    GovernedPopulationFact(
        population_key="decision_trace:audit_boundary",
        dataset_name="decision_trace",
        schema_version="decision_trace_v1",
        schema_generation=1,
        generation_state=GENERATION_CONFIRMED,
        generation_evidence=(
            "decision_trace generation 2 adds only the predicted_success block, "
            "which is absent/unusable on 25516/25516 audited rows; every audited "
            "row is therefore generation-1 content."),
        record_count=25516,
        record_count_requirement=None,
        identity_grain=(
            "one row per decision-trace event keyed by (observation_id, "
            "canonical_opportunity_id, decision_id)"),
        identity_grain_evidence=(
            "audit persisted_dataset_universe.decision_trace.coverage: "
            "canonical_opportunity_id 25516/25516, schema_version 25516/25516, "
            "timestamp_utc 25516/25516; the governed field inventory carries "
            "observation_id/decision_id/correlation_id"),
        population_class=AUDIT_BOUNDARY_POPULATION,
        population_filters=("event_stream=ALL_PERSISTED_ROWS_AT_AUDIT_BOUNDARY",),
        producer_dataset="decision_trace",
        producer_version="decision_trace_producer_v1",
        requirement_source="MATRIX_AUTHORITATIVE_DATASET",
        population_note=(
            "The exact population the Stage 4 observation audit counted for "
            "decision_trace at the audit boundary (generation-1 content)."),
    ),
    GovernedPopulationFact(
        population_key="opportunities:audit_boundary",
        dataset_name="opportunities",
        schema_version="opportunities_v1",
        schema_generation=1,
        generation_state=GENERATION_CONFIRMED,
        generation_evidence=(
            "opportunities has exactly one governed generation in the Stage 4 "
            "authority (generation 1, epoch STAGE4-EPOCH-OPPORTUNITIES-G1, "
            "RETIRED); no generation-2 boundary exists for this dataset."),
        record_count=100850,
        record_count_requirement=None,
        identity_grain=(
            "one row per opportunity record keyed by canonical_opportunity_id, "
            "with opportunity_id where the record carries one"),
        identity_grain_evidence=(
            "audit persisted_dataset_universe.opportunities.coverage: "
            "canonical_opportunity_id 100850/100850, symbol 100850/100850, "
            "opportunity_id 50521/100850"),
        population_class=AUDIT_BOUNDARY_POPULATION,
        population_filters=("event_stream=ALL_PERSISTED_ROWS_AT_AUDIT_BOUNDARY",),
        producer_dataset="opportunities",
        producer_version="opportunities_producer_v1",
        requirement_source="MATRIX_AUTHORITATIVE_DATASET",
        population_note=(
            "The exact population the Stage 4 observation audit counted for "
            "opportunities at the audit boundary."),
    ),
    GovernedPopulationFact(
        population_key="market_context:audit_boundary",
        dataset_name="market_context",
        schema_version="market_context_v1",
        schema_generation=None,
        generation_state=GENERATION_UNASSERTED,
        generation_evidence=(
            "market_context is outside the Stage 4 governed generation overlay: "
            "the version authority declares no schema generation for it, so the "
            "population's generation is recorded as UNASSERTED rather than "
            "invented."),
        record_count=1754,
        record_count_requirement=None,
        identity_grain="one row per (symbol, market bar) market-context observation",
        identity_grain_evidence=(
            "audit persisted_dataset_universe.market_context.coverage: "
            "bar_time 1754/1754, symbol 1754/1754, regime 1754/1754, "
            "h4.regime 1754/1754"),
        population_class=AUDIT_BOUNDARY_POPULATION,
        population_filters=("event_stream=ALL_PERSISTED_ROWS_AT_AUDIT_BOUNDARY",),
        producer_dataset=None,
        producer_version=None,
        requirement_source="NONE",
        population_note=(
            "Consulted as governed evidence for the lifecycle decision snapshot "
            "(OR-02..OR-05); no producer version is declared for it in the "
            "Stage 4 producer overlay, so its producer lineage is UNASSERTED."),
    ),
)




def _matrix_requirements() -> tuple[dict[str, Any], ...]:
    payload = _read_json(I.REQUIREMENT_MATRIX_PATH)
    rows = payload.get("observation_requirements")
    if not isinstance(rows, list):
        raise DatasetSnapshotError("REQUIREMENT_AUTHORITY_MISSING_REQUIREMENTS")
    return tuple(rows)


def requirements_for_fact(
    fact: GovernedPopulationFact,
    matrix: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str, ...]:
    """Governed observation requirements this population is evidence for."""
    if fact.requirement_source == "NONE":
        return ()
    if fact.requirement_source.startswith("EXPLICIT:"):
        return (I.validate_requirement_id(
            fact.requirement_source.split(":", 1)[1]),)
    if fact.requirement_source == "MATRIX_AUTHORITATIVE_DATASET":
        rows = tuple(matrix) if matrix is not None else _matrix_requirements()
        return tuple(sorted(str(row.get("id")) for row in rows
                            if fact.dataset_name in str(
                                row.get("authoritative_dataset", ""))))
    raise DatasetSnapshotError(
        "UNKNOWN_REQUIREMENT_SOURCE:" + str(fact.requirement_source))


def resolve_producer_identity(
    dataset: str, producer_version: str,
    versioning_policy: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Producer lineage, read from the existing versioning authority overlay."""
    payload = (dict(versioning_policy) if versioning_policy is not None
               else _read_json(VERSIONING_POLICY_PATH))
    registry = payload.get("version_registry") or {}
    for row in registry.get("producer_versions", ()):
        if str(row.get("dataset")) != str(dataset):
            continue
        if str(row.get("producer_version")) != str(producer_version):
            continue
        producer_fingerprint = str(row.get("producer_fingerprint") or "")
        if not producer_fingerprint:
            raise DatasetSnapshotError(
                "PRODUCER_IDENTITY_INCOMPLETE:" + str(producer_version))
        return str(producer_version), producer_fingerprint
    raise DatasetSnapshotError(
        f"PRODUCER_IDENTITY_UNRESOLVED:{dataset}:{producer_version}")


def _source_boundaries(entry: Mapping[str, Any]) -> tuple[str, ...]:
    boundaries: list[str] = []
    prefix = str(entry.get("prefix") or "")
    if prefix:
        boundaries.append("s3_prefix=" + prefix)
    if entry.get("objects") is not None:
        boundaries.append("objects=" + str(int(entry.get("objects") or 0)))
    date_min = entry.get("date_min")
    date_max = entry.get("date_max")
    if date_min and date_max:
        boundaries.append(f"date_range={date_min}..{date_max}")
    if not boundaries:
        raise DatasetSnapshotError("AUDIT_POPULATION_WITHOUT_BOUNDARIES")
    return tuple(boundaries)


def _population_content_digest(
    fact: GovernedPopulationFact,
    entry: Mapping[str, Any],
) -> str:
    """Content binding for an already-persisted audited population.

    The exact rows can no longer be re-read, so the binding is taken over the
    audited population DESCRIPTOR.  The digest scope records that honestly.
    The audit's own fingerprint is deliberately EXCLUDED: it is provenance, and
    a re-derived audit must not re-identify an unchanged historical population.
    """
    return population_descriptor_digest({
        "population_key": fact.population_key,
        "dataset_name": fact.dataset_name,
        "schema_version": fact.schema_version,
        "record_count": fact.record_count,
        "identity_grain": fact.identity_grain,
        "population_filters": list(fact.population_filters),
        "source_boundaries": list(_source_boundaries(entry)),
        "audited_rows_field": str(entry.get("rows")),
    })


def _assert_fact_against_authority(
    fact: GovernedPopulationFact, audit: Mapping[str, Any],
    matrix: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Assert one transcribed fact against the persisted audit authority."""
    universe = audit.get("persisted_dataset_universe") or {}
    entry = universe.get(fact.dataset_name)
    if not isinstance(entry, Mapping):
        raise DatasetSnapshotError(
            "AUDIT_DATASET_ABSENT:" + fact.dataset_name)
    if entry.get("persisted") is not True:
        raise DatasetSnapshotError(
            "AUDIT_DATASET_NOT_PERSISTED:" + fact.dataset_name)
    if str(entry.get("schema")) != fact.schema_version:
        raise DatasetSnapshotError(
            "AUDIT_DATASET_SCHEMA_DRIFT:" + fact.dataset_name)
    if fact.is_audit_boundary:
        if int(entry.get("rows") or 0) != fact.record_count:
            raise DatasetSnapshotError(
                "AUDIT_POPULATION_SIZE_DRIFT:" + fact.dataset_name)
        return entry
    rows = {str(row.get("id")): row for row in matrix}
    requirement = rows.get(str(fact.record_count_requirement))
    if requirement is None:
        raise DatasetSnapshotError(
            "POPULATION_REQUIREMENT_ABSENT:" + fact.population_key)
    coverage = str(requirement.get("observed_coverage") or "")
    if str(fact.record_count) not in coverage:
        raise DatasetSnapshotError(
            "AUDIT_POPULATION_SIZE_DRIFT:" + fact.population_key)
    return entry



def bootstrap_registry(
    *,
    audit: Mapping[str, Any] | None = None,
    matrix: Sequence[Mapping[str, Any]] | None = None,
    versioning_policy: Mapping[str, Any] | None = None,
) -> DatasetSnapshotRegistry:
    """Build the canonical population registry from the persisted authority.

    Purely derived and deterministic: no clock, no S3 read, no historical
    mutation.  Every transcribed fact is asserted against the audit first.
    """
    resolved_audit = dict(audit) if audit is not None else _read_json(AUDIT_PATH)
    audit_fingerprint = str(resolved_audit.get("audit_fingerprint") or "")
    if not audit_fingerprint:
        raise DatasetSnapshotError("AUDIT_FINGERPRINT_MISSING")
    resolved_matrix = (tuple(matrix) if matrix is not None
                       else _matrix_requirements())
    from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY

    snapshots: list[DatasetSnapshot] = []
    for fact in GOVERNED_POPULATION_FACTS:
        entry = _assert_fact_against_authority(
            fact, resolved_audit, resolved_matrix)
        if fact.dataset_name in PRODUCTION_SCHEMA_REGISTRY:
            declared = PRODUCTION_SCHEMA_REGISTRY[fact.dataset_name].current
            if declared != fact.schema_version:
                raise DatasetSnapshotError(
                    "SCHEMA_VERSION_DRIFT:" + fact.dataset_name)
        producer_version: str | None = None
        producer_fingerprint: str | None = None
        if fact.producer_dataset is not None:
            producer_version, producer_fingerprint = resolve_producer_identity(
                fact.producer_dataset, str(fact.producer_version),
                versioning_policy)
        date_min = entry.get("date_min")
        date_max = entry.get("date_max")
        snapshots.append(freeze_population(
            dataset_name=fact.dataset_name,
            schema_version=fact.schema_version,
            schema_generation=fact.schema_generation,
            generation_state=fact.generation_state,
            generation_evidence=fact.generation_evidence,
            content_digest=_population_content_digest(fact, entry),
            content_digest_scope=DIGEST_SCOPE_POPULATION_DESCRIPTOR,
            record_count=fact.record_count,
            identity_grain=fact.identity_grain,
            identity_grain_evidence=fact.identity_grain_evidence,
            producer_version=producer_version,
            producer_fingerprint=producer_fingerprint,
            source_boundaries=_source_boundaries(entry),
            population_filters=fact.population_filters,
            temporal_bounds=((str(date_min), str(date_max))
                             if date_min and date_max else None),
            population_class=fact.population_class,
            observation_requirements=requirements_for_fact(fact, resolved_matrix),
            frozen_at=_iso_day(STAMP),
            evidence_citations=(
                f"audit persisted_dataset_universe.{fact.dataset_name}.rows",
                f"audit fingerprint {audit_fingerprint}",
                fact.identity_grain_evidence,
                fact.generation_evidence,
                fact.population_note,
            ),
            audit_authority_fingerprint=audit_fingerprint,
        ))
    return DatasetSnapshotRegistry(snapshots)


def population_key_index(
    registry: DatasetSnapshotRegistry | None = None,
) -> dict[str, str]:
    """Map each governed population key to its derived snapshot identity."""
    resolved = registry if registry is not None else bootstrap_registry()
    index: dict[str, str] = {}
    for fact in GOVERNED_POPULATION_FACTS:
        for snapshot in resolved.snapshots_for_dataset(fact.dataset_name):
            if snapshot.record_count == fact.record_count \
                    and snapshot.population_filters == fact.population_filters:
                index[fact.population_key] = snapshot.dataset_snapshot_id
                break
        else:
            raise DatasetSnapshotError(
                "GOVERNED_POPULATION_NOT_REGISTERED:" + fact.population_key)
    return index



#: Which governed population(s) each evidence epoch's window is drawn from.
#:
#: A COLLECTING/OPEN epoch deliberately maps to NO snapshot: its population is
#: still growing, so no immutable population identity exists yet.  Binding one
#: would claim a frozen population that does not exist.
EPOCH_POPULATION_KEYS: dict[str, tuple[str, ...]] = {
    "STAGE4-EPOCH-SHADOW-RUNTIME-G1": ("shadow_runtime:audit_boundary",),
    "STAGE4-EPOCH-SHADOW-RUNTIME-G2": (),
    "STAGE4-EPOCH-DECISION-TRACE-G1": ("decision_trace:audit_boundary",),
    "STAGE4-EPOCH-DECISION-TRACE-G2": (),
    "STAGE4-EPOCH-OPPORTUNITIES-G1": ("opportunities:audit_boundary",),
}


def epoch_population_bindings(
    registry: DatasetSnapshotRegistry | None = None,
) -> dict[str, tuple[str, ...]]:
    """Canonical ``epoch_id -> dataset_snapshot_id(s)`` binding table."""
    index = population_key_index(registry)
    return {epoch_id: tuple(index[key] for key in keys)
            for epoch_id, keys in EPOCH_POPULATION_KEYS.items()}


def snapshot_ids_for_epoch(
    epoch_id: str, registry: DatasetSnapshotRegistry | None = None,
) -> tuple[str, ...]:
    """Canonical population identity/binding for one evidence epoch."""
    bindings = epoch_population_bindings(registry)
    if str(epoch_id) not in bindings:
        raise DatasetSnapshotError("NO_EPOCH_POPULATION_BINDING:" + str(epoch_id))
    return bindings[str(epoch_id)]


def frozen_epoch_membership(
    registry: DatasetSnapshotRegistry | None = None,
) -> dict[str, list[str]]:
    """Persisted membership of every frozen epoch (used for tamper checks)."""
    bindings = epoch_population_bindings(registry)
    frozen = {"STAGE4-EPOCH-SHADOW-RUNTIME-G1",
              "STAGE4-EPOCH-DECISION-TRACE-G1",
              "STAGE4-EPOCH-OPPORTUNITIES-G1"}
    return {epoch_id: list(ids) for epoch_id, ids in bindings.items()
            if epoch_id in frozen}


def assert_frozen_epoch_membership(
    epoch: Any,
    frozen_membership: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """A frozen epoch may never silently change its population membership."""
    epoch_id = str(getattr(epoch, "epoch_id", ""))
    if not epoch_id:
        raise DatasetSnapshotError("EPOCH_WITHOUT_ID")
    declared = tuple(getattr(epoch, "dataset_snapshot_ids", ()))
    status = str(getattr(epoch, "status", ""))
    if status in FROZEN_EPOCH_STATUSES:
        if not declared:
            raise DatasetSnapshotError(
                "FROZEN_EPOCH_WITHOUT_DATASET_SNAPSHOT:" + epoch_id)
        persisted = dict(frozen_membership if frozen_membership is not None
                         else frozen_epoch_membership())
        recorded = persisted.get(epoch_id)
        if recorded is None:
            raise DatasetSnapshotError(
                "FROZEN_EPOCH_MEMBERSHIP_UNRECORDED:" + epoch_id)
        if tuple(recorded) != declared:
            raise DatasetSnapshotError(
                "FROZEN_EPOCH_SNAPSHOT_MEMBERSHIP_CHANGED:" + epoch_id)
        return
    # A live (OPEN/COLLECTING) epoch may not claim a frozen population, and may
    # never drop a membership that was already recorded.
    if declared:
        raise DatasetSnapshotError(
            "LIVE_POPULATION_CANNOT_HAVE_FROZEN_SNAPSHOT:" + epoch_id)
    persisted = dict(frozen_membership if frozen_membership is not None
                     else frozen_epoch_membership())
    if persisted.get(epoch_id):
        raise DatasetSnapshotError(
            "EPOCH_SNAPSHOT_MEMBERSHIP_REMOVED:" + epoch_id)



# ═══════════════════════════════════════════════════════════════════════════
# GOVERNED EVIDENCE SETS BOUND TO POPULATIONS
# ═══════════════════════════════════════════════════════════════════════════

EVIDENCE_SET_OR_01 = "ESET-OR-01-POPULATION"
EVIDENCE_SET_SHADOW_RUNTIME_AUDIT_BOUNDARY = "ESET-SHADOW-RUNTIME-AUDIT-BOUNDARY"
EVIDENCE_SET_AUDIT_BOUNDARY = "ESET-STAGE4-AUDIT-BOUNDARY-POPULATIONS"

#: Requirement-scoped governed evidence sets (population identity only).
REQUIREMENT_EVIDENCE_SET_KEYS: dict[str, tuple[str, ...]] = {
    EVIDENCE_SET_OR_01: ("shadow_runtime:close_lifecycle_outcome",),
    EVIDENCE_SET_SHADOW_RUNTIME_AUDIT_BOUNDARY:
        ("shadow_runtime:audit_boundary",),
}

#: The combined audit-boundary population set: ONE evidence set that combines
#: every governed population the Stage 4 observation audit drew evidence from.
AUDIT_BOUNDARY_POPULATION_KEYS: tuple[str, ...] = (
    "shadow_runtime:audit_boundary",
    "decision_trace:audit_boundary",
    "opportunities:audit_boundary",
    "market_context:audit_boundary",
)


def snapshot_member(
    snapshot: DatasetSnapshot, *,
    reference_type: str = "dataset_snapshot",
) -> I.EvidenceMemberReference:
    """One evidence member that claims the population it actually belongs to."""
    return I.EvidenceMemberReference(
        reference_type=reference_type,
        reference_id=snapshot.dataset_snapshot_id,
        dataset=snapshot.dataset_name,
        locator=snapshot.locator(),
        content_fingerprint=snapshot.content_digest,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
    )


def evidence_set_for_snapshots(
    *,
    evidence_set_id: str,
    observation_requirement_ids: Sequence[str],
    snapshots: Sequence[DatasetSnapshot],
) -> I.EvidenceSet:
    """Build a governed evidence set that explicitly names its populations."""
    if not snapshots:
        raise DatasetSnapshotError(
            "EVIDENCE_SET_WITHOUT_DATASET_SNAPSHOT:" + str(evidence_set_id))
    return I.EvidenceSet(
        evidence_set_id=str(evidence_set_id),
        observation_requirement_ids=tuple(observation_requirement_ids),
        members=tuple(snapshot_member(s) for s in snapshots),
        dataset_snapshot_ids=tuple(s.dataset_snapshot_id for s in snapshots),
    )


def governed_evidence_sets(
    registry: DatasetSnapshotRegistry | None = None,
    matrix: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[I.EvidenceSet, ...]:
    """Every governed evidence set whose population identity is resolvable."""
    resolved = registry if registry is not None else bootstrap_registry()
    index = population_key_index(resolved)
    resolved_matrix = (tuple(matrix) if matrix is not None
                       else _matrix_requirements())
    sets: list[I.EvidenceSet] = []
    for evidence_set_id, keys in sorted(REQUIREMENT_EVIDENCE_SET_KEYS.items()):
        facts = [fact for fact in GOVERNED_POPULATION_FACTS
                 if fact.population_key in keys]
        requirements = sorted({
            rid for fact in facts
            for rid in requirements_for_fact(fact, resolved_matrix)})
        sets.append(evidence_set_for_snapshots(
            evidence_set_id=evidence_set_id,
            observation_requirement_ids=requirements,
            snapshots=[resolved.require(index[key]) for key in keys],
        ))
    combined_requirements = sorted({
        rid for fact in GOVERNED_POPULATION_FACTS
        if fact.population_key in AUDIT_BOUNDARY_POPULATION_KEYS
        for rid in requirements_for_fact(fact, resolved_matrix)})
    sets.append(evidence_set_for_snapshots(
        evidence_set_id=EVIDENCE_SET_AUDIT_BOUNDARY,
        observation_requirement_ids=combined_requirements,
        snapshots=[resolved.require(index[key])
                   for key in AUDIT_BOUNDARY_POPULATION_KEYS],
    ))
    return tuple(sets)



# ═══════════════════════════════════════════════════════════════════════════
# RESEARCH READ-SIDE VALIDATION
#
# Governed consumers call these BEFORE treating evidence as usable.  Nothing
# here decides satisfaction: it decides whether the evidence's IDENTITY is
# structurally trustworthy (which population, which schema, which producer).
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class EvidenceIdentityValidation:
    """The outcome of a read-side evidence identity check."""

    evidence_set_id: str
    state: str
    reason: str
    dataset_snapshot_ids: tuple[str, ...]
    resolved_snapshots: tuple[dict[str, Any], ...]

    @property
    def valid(self) -> bool:
        return self.state == IDENTITY_VERIFIED

    def require_valid(self) -> "EvidenceIdentityValidation":
        if not self.valid:
            raise DatasetSnapshotError(
                f"{self.state}:{self.evidence_set_id}:{self.reason}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_set_id": self.evidence_set_id,
            "validation_state": self.state,
            "valid": self.valid,
            "reason": self.reason,
            "dataset_snapshot_ids": list(self.dataset_snapshot_ids),
            "resolved_snapshots": [dict(row) for row in self.resolved_snapshots],
        }


def _invalid(evidence_set_id: str, state: str, reason: str,
             snapshot_ids: Sequence[str] = (),
             resolved: Sequence[Mapping[str, Any]] = ()) -> EvidenceIdentityValidation:
    return EvidenceIdentityValidation(
        evidence_set_id=str(evidence_set_id), state=state, reason=reason,
        dataset_snapshot_ids=tuple(snapshot_ids),
        resolved_snapshots=tuple(dict(row) for row in resolved))


def _snapshot_view(snapshot: DatasetSnapshot) -> dict[str, Any]:
    return {
        "dataset_snapshot_id": snapshot.dataset_snapshot_id,
        "dataset_name": snapshot.dataset_name,
        "schema_version": snapshot.schema_version,
        "schema_generation": snapshot.schema_generation,
        "generation_state": snapshot.generation_state,
        "content_digest": snapshot.content_digest,
        "content_digest_scope": snapshot.content_digest_scope,
        "record_count": snapshot.record_count,
        "identity_grain": snapshot.identity_grain,
        "producer_version": snapshot.producer_version,
        "producer_fingerprint": snapshot.producer_fingerprint,
        "producer_identity_state": snapshot.producer_identity_state,
        "population_class": snapshot.population_class,
        "frozen_at": snapshot.frozen_at,
    }



def validate_evidence_identity(
    evidence_set: I.EvidenceSet, *,
    registry: DatasetSnapshotRegistry | None = None,
    require_dataset_snapshots: bool = True,
    require_producer: bool = False,
    required_schema_version: str | None = None,
    required_schema_generation: int | None = None,
    required_producers: Mapping[str, str] | None = None,
    require_uniform_schema: bool = True,
    require_uniform_generation: bool = True,
) -> EvidenceIdentityValidation:
    """Validate that governed evidence resolves to trustworthy populations."""
    resolved_registry = registry if registry is not None else bootstrap_registry()
    evidence_set_id = evidence_set.evidence_set_id
    snapshot_ids = tuple(evidence_set.dataset_snapshot_ids)
    if not snapshot_ids:
        if require_dataset_snapshots:
            return _invalid(
                evidence_set_id, SNAPSHOT_MISSING,
                "The evidence set declares no dataset snapshot identity, so the "
                "exact population it used cannot be resolved.")
        return _invalid(
            evidence_set_id, IDENTITY_LEGACY_UNRESOLVED,
            "No dataset snapshot identity is declared; the population is "
            "explicitly unresolved rather than assumed.")

    snapshots: list[DatasetSnapshot] = []
    for snapshot_id in snapshot_ids:
        if not resolved_registry.is_registered(snapshot_id):
            return _invalid(
                evidence_set_id, SNAPSHOT_UNKNOWN,
                f"dataset_snapshot_id {snapshot_id} is not registered.",
                snapshot_ids)
        snapshots.append(resolved_registry.require(snapshot_id))
    views = [_snapshot_view(s) for s in snapshots]

    # -- member membership: impossible membership is not tolerated ---------
    by_id = {s.dataset_snapshot_id: s for s in snapshots}
    for member in evidence_set.members:
        member_snapshot_id = member.dataset_snapshot_id
        if member_snapshot_id is None or member_snapshot_id not in by_id:
            return _invalid(
                evidence_set_id, MEMBER_SNAPSHOT_MISMATCH,
                f"Member {member.reference_id} does not belong to any declared "
                "dataset snapshot.", snapshot_ids, views)
        snapshot = by_id[member_snapshot_id]
        if member.dataset and str(member.dataset) != snapshot.dataset_name:
            return _invalid(
                evidence_set_id, MEMBER_DATASET_MISMATCH,
                f"Member {member.reference_id} claims dataset {member.dataset} "
                f"but its population belongs to {snapshot.dataset_name}.",
                snapshot_ids, views)
        if member.content_fingerprint is not None and str(
                member.content_fingerprint) != snapshot.content_digest:
            return _invalid(
                evidence_set_id, CONTENT_DIGEST_MISMATCH,
                f"Member {member.reference_id} declares a content fingerprint "
                "that does not match its dataset snapshot's content digest.",
                snapshot_ids, views)

    # -- schema identity vs population identity ----------------------------
    schema_versions = {s.schema_version for s in snapshots}
    schemas_by_dataset = {
        dataset_name: {s.schema_version for s in snapshots
                       if s.dataset_name == dataset_name}
        for dataset_name in {s.dataset_name for s in snapshots}}
    incompatible_schemas = {
        dataset_name: sorted(versions)
        for dataset_name, versions in schemas_by_dataset.items()
        if len(versions) > 1}
    if require_uniform_schema and incompatible_schemas:
        return _invalid(
            evidence_set_id, SNAPSHOT_INCOMPATIBLE,
            "The evidence set combines populations of the same dataset under "
            "different schema identities: "
            f"{incompatible_schemas}.", snapshot_ids, views)
    if required_schema_version is not None and schema_versions != {
            str(required_schema_version)}:
        return _invalid(
            evidence_set_id, SCHEMA_MISMATCH,
            f"Required schema identity {required_schema_version} does not match "
            f"the populations' schema identities {sorted(schema_versions)}.",
            snapshot_ids, views)

    asserted_generations = {
        s.schema_generation for s in snapshots if s.schema_generation is not None}
    generations_by_dataset = {
        dataset_name: {s.schema_generation for s in snapshots
                       if s.dataset_name == dataset_name
                       and s.schema_generation is not None}
        for dataset_name in {s.dataset_name for s in snapshots}}
    mixed_generations = {
        dataset_name: sorted(generations)
        for dataset_name, generations in generations_by_dataset.items()
        if len(generations) > 1}
    if require_uniform_generation and mixed_generations:
        return _invalid(
            evidence_set_id, GENERATION_MIXED,
            "The evidence set mixes populations of the same dataset from "
            f"different schema generations: {mixed_generations}.",
            snapshot_ids, views)
    if required_schema_generation is not None:
        if any(s.schema_generation is None for s in snapshots):
            return _invalid(
                evidence_set_id, REQUIRED_GENERATION_UNASSERTED,
                "A required schema generation cannot be verified: at least one "
                "population's generation is UNASSERTED.", snapshot_ids, views)
        if asserted_generations != {int(required_schema_generation)}:
            return _invalid(
                evidence_set_id, GENERATION_MISMATCH,
                f"Required schema generation {required_schema_generation} does "
                f"not match {sorted(asserted_generations)}.",
                snapshot_ids, views)

    # -- producer lineage (kept separate from population identity) ---------
    if require_producer:
        unasserted = sorted(s.dataset_name for s in snapshots
                            if s.producer_identity_state != PRODUCER_BOUND)
        if unasserted:
            return _invalid(
                evidence_set_id, REQUIRED_PRODUCER_UNASSERTED,
                "Producer lineage is required but unasserted for: "
                + ", ".join(unasserted), snapshot_ids, views)
    for dataset_name, expected in dict(required_producers or {}).items():
        matching = [snapshot for snapshot in snapshots
                    if snapshot.dataset_name == str(dataset_name)]
        if not matching:
            return _invalid(
                evidence_set_id, SNAPSHOT_DATASET_MISMATCH,
                f"Required producer dataset {dataset_name} is not present in "
                "the evidence set.", snapshot_ids, views)
        for snapshot in matching:
            if snapshot.producer_version != str(expected):
                return _invalid(
                    evidence_set_id, PRODUCER_MISMATCH,
                    f"Population {snapshot.dataset_snapshot_id} was emitted by "
                    f"producer {snapshot.producer_version}, not {expected}.",
                    snapshot_ids, views)

    return EvidenceIdentityValidation(
        evidence_set_id=evidence_set_id, state=IDENTITY_VERIFIED,
        reason=(
            "Every member belongs to a registered immutable population whose "
            "schema identity, generation and producer lineage are declared."),
        dataset_snapshot_ids=snapshot_ids,
        resolved_snapshots=tuple(views))


def require_evidence_identity(
    evidence_set: I.EvidenceSet, **kwargs: Any,
) -> EvidenceIdentityValidation:
    """Fail-closed read-side gate: raise unless the identity is trustworthy."""
    return validate_evidence_identity(
        evidence_set, **kwargs).require_valid()


def validate_epoch_evidence_identity(
    epoch: Any, *,
    registry: DatasetSnapshotRegistry | None = None,
    frozen_membership: Mapping[str, Sequence[str]] | None = None,
    require_dataset_snapshots: bool = True,
    **kwargs: Any,
) -> EvidenceIdentityValidation:
    """Read-side validation of ONE evidence epoch's population binding."""
    epoch_id = str(getattr(epoch, "epoch_id", ""))
    declared = tuple(getattr(epoch, "dataset_snapshot_ids", ()))
    if epoch_id in EPOCH_POPULATION_KEYS:
        expected = snapshot_ids_for_epoch(epoch_id, registry)
        if declared != expected:
            return _invalid(
                epoch_id, SNAPSHOT_UNKNOWN,
                "The epoch's dataset snapshot membership is not the canonical "
                "one for its governed window.", declared)
    if str(getattr(epoch, "status", "")) in FROZEN_EPOCH_STATUSES:
        assert_frozen_epoch_membership(epoch, frozen_membership)
    elif declared:
        raise DatasetSnapshotError(
            "LIVE_POPULATION_CANNOT_HAVE_FROZEN_SNAPSHOT:" + epoch_id)
    return validate_evidence_identity(
        epoch.evidence_set, registry=registry,
        require_dataset_snapshots=require_dataset_snapshots, **kwargs)



# ═══════════════════════════════════════════════════════════════════════════
# PERSISTENT AUTHORITY (additive; never rewrites historical artifacts)
# ═══════════════════════════════════════════════════════════════════════════

def identity_separation_report() -> dict[str, Any]:
    """The canonical statement of what each identity namespace means."""
    return {
        "schema_identity": {
            "fields": ["schema_version", "schema_generation"],
            "question": ("Under which structural/semantic contract is this "
                         "record interpreted?"),
            "example": "shadow_runtime_v1",
            "classification": "SCHEMA_IDENTIFIER",
            "derived_from": ("core.production_data_contract."
                             "PRODUCTION_SCHEMA_REGISTRY plus the Stage 4 "
                             "generation overlay"),
        },
        "population_identity": {
            "fields": ["dataset_snapshot_id"],
            "question": "Which exact immutable population of records was used?",
            "example": I.DATASET_SNAPSHOT_ID_PREFIX + "<24 hex>",
            "prefix": I.DATASET_SNAPSHOT_ID_PREFIX,
            "derived_from": list(IDENTITY_MATERIAL_FIELDS),
            "classification": "DATASET_POPULATION_IDENTIFIER",
        },
        "dataset_identity": {
            "fields": ["dataset_name"],
            "question": ("Which growing dataset family does this population "
                         "come from?"),
            "example": "shadow_runtime",
        },
        "producer_identity": {
            "fields": ["producer_version", "producer_fingerprint"],
            "question": "Which emitting code produced these records?",
            "note": ("Lineage only. It never substitutes for population or "
                     "schema identity."),
        },
        "legacy_dataset_version_field": {
            "field": "dataset_version",
            "meaning": (
                "Historical Stage 4 field. Its VALUE is a schema-registry "
                "string and is therefore a SCHEMA identity, not a population "
                "identity. It is preserved verbatim for historical "
                "compatibility and is never used as, derived into, or compared "
                "against a dataset_snapshot_id."),
            "compatibility_class": I.LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY,
        },
        "conflation_guards": [
            "SCHEMA_IDENTIFIER_USED_AS_DATASET_SNAPSHOT_ID",
            "SCHEMA_IDENTIFIER_USED_AS_DATASET_NAME",
            "DATASET_NAME_SCHEMA_CONFLATION",
            "SCHEMA_SNAPSHOT_CONFLATION",
            "DATASET_NAME_SNAPSHOT_CONFLATION",
            "MUTABLE_POPULATION_CANNOT_BE_DATASET_SNAPSHOT",
            "DATASET_SNAPSHOT_ID_NOT_DERIVED_FROM_POPULATION",
            "CONFLICTING_DATASET_SNAPSHOT_CONTENT",
        ],
    }


def _epoch_identity_states(
    bindings: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    """The snapshot identity state of every governed evidence epoch."""
    states: dict[str, str] = {}
    for epoch_id, ids in sorted(bindings.items()):
        if ids:
            states[epoch_id] = SNAPSHOT_BOUND
        elif epoch_id in EPOCH_POPULATION_KEYS:
            states[epoch_id] = SNAPSHOT_UNRESOLVED_LIVE
        else:
            states[epoch_id] = SNAPSHOT_UNRESOLVED_HISTORICAL
    return states


def build_store(
    *,
    registry: DatasetSnapshotRegistry | None = None,
    evidence_sets: Sequence[I.EvidenceSet] | None = None,
) -> dict[str, Any]:
    """Build the canonical Stage 4 dataset-snapshot authority store."""
    resolved_registry = registry if registry is not None else bootstrap_registry()
    resolved_sets = (tuple(evidence_sets) if evidence_sets is not None
                     else governed_evidence_sets(resolved_registry))
    matrix_payload = _read_json(I.REQUIREMENT_MATRIX_PATH)
    bindings = epoch_population_bindings(resolved_registry)
    membership = frozen_epoch_membership(resolved_registry)
    index = population_key_index(resolved_registry)
    validations = {item.evidence_set_id: validate_evidence_identity(
        item, registry=resolved_registry).to_dict() for item in resolved_sets}

    store: dict[str, Any] = {
        "schema": STORE_SCHEMA,
        "stamp": STAMP,
        "policy_id": SNAPSHOT_POLICY_ID,
        "registry_schema": DatasetSnapshotRegistry.SCHEMA,
        "identity_separation": identity_separation_report(),
        "base_authority": {
            "module": "core.production_data_contract",
            "registry": "PRODUCTION_SCHEMA_REGISTRY",
            "role": ("Canonical dataset identity and schema version. This "
                     "authority adds POPULATION (content-bound) identity; it "
                     "never duplicates the base contract."),
        },
        "dataset_snapshots": resolved_registry.to_dict()["snapshots"],
        "dataset_snapshot_count": len(resolved_registry),
        "registry_fingerprint": resolved_registry.registry_fingerprint(),
        "governed_populations": [
            {"population_key": fact.population_key,
             "dataset_name": fact.dataset_name,
             "schema_version": fact.schema_version,
             "schema_generation": fact.schema_generation,
             "generation_state": fact.generation_state,
             "record_count": fact.record_count,
             "population_class": fact.population_class,
             "population_filters": list(fact.population_filters),
             "producer_version": fact.producer_version,
             "dataset_snapshot_id": index[fact.population_key],
             "population_note": fact.population_note}
            for fact in GOVERNED_POPULATION_FACTS],
        "epoch_snapshot_bindings": {
            epoch_id: list(ids) for epoch_id, ids in sorted(bindings.items())},
        "frozen_epoch_membership": {
            epoch_id: list(ids) for epoch_id, ids in sorted(membership.items())},
        "epoch_snapshot_identity_states": _epoch_identity_states(bindings),
        "evidence_sets": [item.to_dict() for item in resolved_sets],
        "read_side_validation": validations,
        "authority_conservation": {
            "observation_requirements": len(
                matrix_payload.get("observation_requirements", ())),
            "governed_gaps": len(matrix_payload.get("governed_gaps", ())),
            "counts": dict(matrix_payload.get("counts", {})),
        },
        "mutation_ledger": {
            "s3_writes": 0,
            "historical_mutations": 0,
            "backfills_performed": 0,
            "research_reentry_events": 0,
            "satisfaction_decisions": 0,
            "reentry_authorizations": 0,
            "scientific_result_versions": 0,
            "invalidation_events": 0,
        },
        "limitations": [
            ("market_context carries no producer version in the Stage 4 "
             "producer overlay, so its producer lineage is recorded as "
             "UNASSERTED rather than invented."),
            ("Epochs whose population is still collecting (COLLECTING/OPEN) "
             "have no dataset snapshot identity: a growing population is not "
             "an immutable evidence snapshot."),
            ("Descriptor digests mark GOVERNED_POPULATION_DESCRIPTOR scope for "
             "populations whose exact rows cannot be re-read; they are never "
             "presented as record-byte digests."),
        ],
    }
    validate_store(store)
    store["store_fingerprint"] = fingerprint(
        {k: v for k, v in store.items() if k != "store_fingerprint"})
    return store


def validate_store(store: Mapping[str, Any]) -> None:
    """Fail closed on any identity, separation or conservation violation."""
    if not isinstance(store, Mapping):
        raise DatasetSnapshotError("SNAPSHOT_STORE_NOT_AN_OBJECT")
    if store.get("schema") != STORE_SCHEMA:
        raise DatasetSnapshotError("SNAPSHOT_STORE_SCHEMA_CHANGED")
    if store.get("stamp") != STAMP:
        raise DatasetSnapshotError("SNAPSHOT_STORE_STAMP_CHANGED")
    if store.get("policy_id") != SNAPSHOT_POLICY_ID:
        raise DatasetSnapshotError("SNAPSHOT_STORE_POLICY_CHANGED")
    if store.get("registry_schema") != DatasetSnapshotRegistry.SCHEMA:
        raise DatasetSnapshotError("SNAPSHOT_STORE_REGISTRY_SCHEMA_CHANGED")

    # 1. Every persisted population record is re-derived and re-checked.
    registry = DatasetSnapshotRegistry.from_dict({
        "schema": DatasetSnapshotRegistry.SCHEMA,
        "snapshots": store.get("dataset_snapshots"),
    })
    if int(store.get("dataset_snapshot_count", -1)) != len(registry):
        raise DatasetSnapshotError("DATASET_SNAPSHOT_COUNT_MISMATCH")
    if store.get("registry_fingerprint") != registry.registry_fingerprint():
        raise DatasetSnapshotError("DATASET_SNAPSHOT_REGISTRY_FINGERPRINT_CHANGED")
    for snapshot in (DatasetSnapshot.from_dict(row)
                     for row in store.get("dataset_snapshots", ())):
        I.validate_dataset_identity_separation(
            dataset_name=snapshot.dataset_name,
            schema_version=snapshot.schema_version,
            dataset_snapshot_id=snapshot.dataset_snapshot_id)
        I.validate_dataset_snapshot_id(
            snapshot.dataset_snapshot_id, allow_none=False)

    # 2. The population keys map to exactly the registered identities.
    index = population_key_index(registry)
    recorded_populations = {
        str(row.get("population_key")): str(row.get("dataset_snapshot_id"))
        for row in store.get("governed_populations", ())}
    if recorded_populations != index:
        raise DatasetSnapshotError("GOVERNED_POPULATION_INDEX_CHANGED")

    # 3. Epoch -> population bindings are canonical and resolvable.
    bindings = epoch_population_bindings(registry)
    recorded_bindings = {key: tuple(value) for key, value in
                         (store.get("epoch_snapshot_bindings") or {}).items()}
    if recorded_bindings != bindings:
        raise DatasetSnapshotError("EPOCH_SNAPSHOT_BINDING_RECORD_CHANGED")
    for epoch_id, ids in bindings.items():
        for snapshot_id in ids:
            registry.require(snapshot_id)
    membership = {key: tuple(value) for key, value in
                  frozen_epoch_membership(registry).items()}
    recorded_membership = {key: tuple(value) for key, value in
                           (store.get("frozen_epoch_membership") or {}).items()}
    if recorded_membership != membership:
        raise DatasetSnapshotError("FROZEN_EPOCH_MEMBERSHIP_RECORD_CHANGED")
    for epoch_id, ids in bindings.items():
        if epoch_id in membership and tuple(membership[epoch_id]) != tuple(ids):
            raise DatasetSnapshotError(
                "FROZEN_EPOCH_MEMBERSHIP_CHANGED:" + epoch_id)

    # 4. Every governed evidence set names registered populations, and the
    #    recorded read-side verdict is still the verdict today.
    evidence_sets = [I.EvidenceSet.from_dict(row)
                     for row in store.get("evidence_sets", ())]
    if not evidence_sets:
        raise DatasetSnapshotError("SNAPSHOT_STORE_WITHOUT_EVIDENCE_SETS")
    recorded_validation = dict(store.get("read_side_validation") or {})
    seen: set[str] = set()
    for evidence_set in evidence_sets:
        if evidence_set.evidence_set_id in seen:
            raise DatasetSnapshotError(
                "DUPLICATE_EVIDENCE_SET_ID:" + evidence_set.evidence_set_id)
        seen.add(evidence_set.evidence_set_id)
        evidence_set.require_dataset_snapshots()
        for snapshot_id in evidence_set.dataset_snapshot_ids:
            registry.require(snapshot_id)
        recomputed = validate_evidence_identity(
            evidence_set, registry=registry).to_dict()
        if recorded_validation.get(evidence_set.evidence_set_id) != recomputed:
            raise DatasetSnapshotError(
                "STALE_READ_SIDE_VALIDATION:" + evidence_set.evidence_set_id)

    # 5. Conservation: no requirement renumbered, no gap conversion lost.
    conservation = dict(store.get("authority_conservation") or {})
    counts = dict(conservation.get("counts") or {})
    if int(conservation.get("observation_requirements", -1)) != 15:
        raise DatasetSnapshotError("OBSERVATION_REQUIREMENT_COUNT_CHANGED")
    if int(conservation.get("governed_gaps", -1)) != 31:
        raise DatasetSnapshotError("GOVERNED_GAP_COUNT_CHANGED")
    if int(counts.get("UNIQUE_OBSERVATION_REQUIREMENTS", -1)) != 15:
        raise DatasetSnapshotError("MATRIX_REQUIREMENT_COUNT_CHANGED")
    if int(counts.get("GAP_COUNT_RAW", -1)) != 31:
        raise DatasetSnapshotError("MATRIX_GAP_COUNT_CHANGED")
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        I.validate_requirement_id(rid)

    # 6. This pass must not have performed governed-churn actions.
    ledger = dict(store.get("mutation_ledger") or {})
    if not ledger:
        raise DatasetSnapshotError("SNAPSHOT_STORE_WITHOUT_MUTATION_LEDGER")
    for key, value in ledger.items():
        if value not in (0, False):
            raise DatasetSnapshotError("SNAPSHOT_MUTATION_PERFORMED:" + key)

    claimed_fingerprint = store.get("store_fingerprint")
    if claimed_fingerprint is not None:
        actual_fingerprint = fingerprint({
            key: value for key, value in store.items()
            if key != "store_fingerprint"})
        if str(claimed_fingerprint) != actual_fingerprint:
            raise DatasetSnapshotError("SNAPSHOT_STORE_FINGERPRINT_CHANGED")




# ---------------------------------------------------------------------------
# Persistence (state authority + derived assurance artifacts).
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(temporary, target)


def save_store(store: Mapping[str, Any],
               path: Path = CANONICAL_STATE_PATH) -> None:
    validate_store(store)
    if not store.get("store_fingerprint"):
        raise DatasetSnapshotError("SNAPSHOT_STORE_FINGERPRINT_MISSING")
    _write_json(path, dict(store))


def load_canonical_state(
        path: Path = CANONICAL_STATE_PATH) -> dict[str, Any]:
    """Load the durable population authority and re-validate it."""
    state = _read_json(path)
    if not isinstance(state, Mapping):
        raise DatasetSnapshotError("SNAPSHOT_STORE_NOT_AN_OBJECT")
    if not state.get("store_fingerprint"):
        raise DatasetSnapshotError("SNAPSHOT_STORE_FINGERPRINT_MISSING")
    validate_store(state)
    return dict(state)


def render_markdown(store: Mapping[str, Any]) -> str:
    """Human-readable view of the immutable population registry."""
    lines = [
        "# Stage 4 dataset snapshot registry (immutable population identity)",
        "",
        f"Stamp: {store.get('stamp')}  ",
        f"Policy: `{store.get('policy_id')}`  ",
        f"Snapshots: {store.get('dataset_snapshot_count')}",
        "",
        "Dataset **population** identity (`DSNAP-...`) is separate from schema",
        "identity (`shadow_runtime_v1`) and from producer identity. A schema",
        "identity never identifies a population, and a growing dataset family is",
        "never claimed as an immutable evidence snapshot.",
        "",
        "| dataset_snapshot_id | dataset_name | schema_version | "
        "schema_generation | record_count | producer_version | "
        "population_class |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in store.get("dataset_snapshots", ()):
        lines.append(
            "| {id} | {name} | {schema} | {generation} | {count} | {producer} "
            "| {population_class} |".format(
                id=row.get("dataset_snapshot_id"),
                name=row.get("dataset_name"),
                schema=row.get("schema_version"),
                generation=row.get("schema_generation"),
                count=row.get("record_count"),
                producer=row.get("producer_version") or "UNASSERTED",
                population_class=row.get("population_class")))
    lines += ["", "## Evidence epoch -> population bindings", ""]
    for epoch_id, ids in sorted(
            (store.get("epoch_snapshot_bindings") or {}).items()):
        state = (store.get("epoch_snapshot_identity_states") or {}).get(epoch_id)
        rendered = ", ".join(f"`{value}`" for value in ids) if ids else \
            "(unresolved - population not frozen)"
        lines.append(f"* `{epoch_id}` -> {rendered} [{state}]")
    lines += ["", "## Governed evidence sets", ""]
    for item in store.get("evidence_sets", ()):
        lines.append(
            f"* `{item.get('evidence_set_id')}` -> "
            + ", ".join(f"`{value}`"
                        for value in item.get("dataset_snapshot_ids", ()))
            + " (requirements: "
            + ", ".join(item.get("observation_requirement_ids", ())) + ")")
    lines += ["", "## Known limitations", ""]
    lines += [f"* {value}" for value in store.get("limitations", ())]
    return "\n".join(lines) + "\n"


def write_artifacts(
    store: Mapping[str, Any],
    json_path: Path = ARTIFACT_JSON_PATH,
    md_path: Path = ARTIFACT_MD_PATH,
) -> None:
    """Write the derived assurance views (never the durable authority)."""
    _write_json(json_path, dict(store))
    target = Path(md_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(store))


def persist(store: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write the durable population authority and its assurance artifacts."""
    resolved = dict(store) if store is not None else build_store()
    save_store(resolved)
    write_artifacts(resolved)
    return resolved
