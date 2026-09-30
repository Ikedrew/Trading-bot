"""Canonical Stage 4 observation-requirement and evidence-set identity.

The persisted observation requirement matrix remains the semantic authority.
This module validates and exposes it; it does not create a second registry.
Historical ``OG-*`` values are transition identifiers and can only be resolved
through the explicit compatibility map below.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT_MATRIX_PATH = (
    ROOT / "analysis" / "assurance" /
    "stage4_observation_requirement_matrix_20260929.json"
)
CANONICAL_REQUIREMENT_IDS = tuple(f"OR-{number:02d}" for number in range(1, 16))
LEGACY_QUESTION_REQUIREMENTS = {"EX2": "OR-14", "L7": "OR-15"}
_OR_PATTERN = re.compile(r"^OR-[0-9]{2}$")
_EVIDENCE_SET_PATTERN = re.compile(r"^ESET-[A-Z0-9][A-Z0-9._:-]{0,127}$")
_LEGACY_TRANSITION_PATTERN = re.compile(r"^OG-(EX2|L7)-[A-Za-z0-9]+$")

#: Canonical shape of an immutable dataset POPULATION identity.
#:
#: A dataset snapshot identity is deliberately a DIFFERENT namespace from a
#: schema identity: ``shadow_runtime_v1`` is an interpretation contract, never a
#: population.  ``DSNAP-...`` is the population.
_DATASET_SNAPSHOT_PATTERN = re.compile(r"^DSNAP-[A-Z0-9][A-Z0-9._:-]{0,127}$")

#: Namespace prefix that makes a population identity visually unmistakable.
DATASET_SNAPSHOT_ID_PREFIX = "DSNAP-"

#: What a schema/dataset-version identifier looks like in this repository
#: (``shadow_runtime_v1``, ``decision_trace_v1``, ``opportunities_v1``, ...).
_SCHEMA_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*_v[0-9]+$")


class Stage4IdentityError(RuntimeError):
    """A canonical Stage 4 identity invariant was violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class RequirementAuthority:
    """Validated view over the existing persisted requirement matrix."""

    def __init__(self, requirements: Sequence[Mapping[str, Any]]) -> None:
        self._requirements: dict[str, dict[str, Any]] = {}
        self._semantic_fingerprints: dict[str, str] = {}
        for raw in requirements:
            if not isinstance(raw, Mapping):
                raise Stage4IdentityError("REQUIREMENT_NOT_AN_OBJECT")
            rid = str(raw.get("observation_requirement_id") or raw.get("id") or "")
            if not rid:
                raise Stage4IdentityError("MISSING_OBSERVATION_REQUIREMENT_ID")
            if not _OR_PATTERN.fullmatch(rid):
                raise Stage4IdentityError("MALFORMED_OBSERVATION_REQUIREMENT_ID:" + rid)
            normalized = dict(raw)
            normalized["id"] = rid  # historical matrix field
            normalized["observation_requirement_id"] = rid
            semantic_fp = fingerprint({
                key: value for key, value in normalized.items()
                if key != "observation_requirement_id"
            })
            if rid in self._requirements:
                if self._semantic_fingerprints[rid] != semantic_fp:
                    raise Stage4IdentityError(
                        "CONFLICTING_OBSERVATION_REQUIREMENT_ID:" + rid)
                raise Stage4IdentityError("DUPLICATE_OBSERVATION_REQUIREMENT_ID:" + rid)
            self._requirements[rid] = normalized
            self._semantic_fingerprints[rid] = semantic_fp

        actual = set(self._requirements)
        expected = set(CANONICAL_REQUIREMENT_IDS)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise Stage4IdentityError(
                "CANONICAL_REQUIREMENT_SET_MISMATCH:missing="
                + ",".join(missing) + ":unknown=" + ",".join(unknown))

    @classmethod
    def load(cls, path: Path = REQUIREMENT_MATRIX_PATH) -> "RequirementAuthority":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise Stage4IdentityError("REQUIREMENT_AUTHORITY_UNREADABLE") from exc
        requirements = payload.get("observation_requirements")
        if not isinstance(requirements, list):
            raise Stage4IdentityError("REQUIREMENT_AUTHORITY_MISSING_REQUIREMENTS")
        return cls(requirements)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._requirements))

    def require(self, requirement_id: str) -> dict[str, Any]:
        rid = validate_requirement_id(requirement_id, authority=self)
        return dict(self._requirements[rid])

    def semantic_fingerprint(self, requirement_id: str) -> str:
        rid = validate_requirement_id(requirement_id, authority=self)
        return self._semantic_fingerprints[rid]


_AUTHORITY: RequirementAuthority | None = None


def requirement_authority() -> RequirementAuthority:
    global _AUTHORITY
    if _AUTHORITY is None:
        _AUTHORITY = RequirementAuthority.load()
    return _AUTHORITY


def validate_requirement_id(
    requirement_id: str, *, authority: RequirementAuthority | None = None,
) -> str:
    rid = str(requirement_id or "").strip()
    if not rid:
        raise Stage4IdentityError("MISSING_OBSERVATION_REQUIREMENT_ID")
    if rid.startswith("OG-"):
        raise Stage4IdentityError("LEGACY_TRANSITION_ID_NOT_CANONICAL:" + rid)
    if not _OR_PATTERN.fullmatch(rid):
        raise Stage4IdentityError("MALFORMED_OBSERVATION_REQUIREMENT_ID:" + rid)
    known = set((authority or requirement_authority()).ids)
    if rid not in known:
        raise Stage4IdentityError("UNKNOWN_OBSERVATION_REQUIREMENT_ID:" + rid)
    return rid


def resolve_legacy_transition_id(transition_id: str) -> str:
    """Resolve a historical OG transition without treating it as canonical."""
    value = str(transition_id or "").strip()
    match = _LEGACY_TRANSITION_PATTERN.fullmatch(value)
    if not match:
        raise Stage4IdentityError("UNKNOWN_LEGACY_TRANSITION_ID:" + value)
    return validate_requirement_id(LEGACY_QUESTION_REQUIREMENTS[match.group(1)])


def requirement_id_for_question(question_id: str) -> str:
    qid = str(question_id or "").strip().upper()
    try:
        return validate_requirement_id(LEGACY_QUESTION_REQUIREMENTS[qid])
    except KeyError:
        raise Stage4IdentityError("NO_CANONICAL_REQUIREMENT_FOR_QUESTION:" + qid) from None


# ---------------------------------------------------------------------------
# SCHEMA IDENTITY vs DATASET POPULATION IDENTITY (Stage 4 Refinement 2)
#
# These two namespaces must never be substituted for one another:
#
#   schema_version / schema_generation  -> how records are INTERPRETED
#   dataset_snapshot_id                 -> WHICH exact records were consumed
#
# A schema identity does not identify a population: the same schema describes a
# dataset whose contents keep growing.  ``shadow_runtime_v1`` therefore fails
# closed the moment it is used as a dataset snapshot identity.
# ---------------------------------------------------------------------------

def is_schema_identifier(value: str) -> bool:
    """True when ``value`` names a governed schema (``*_v<N>``), not a population."""
    return bool(_SCHEMA_IDENTIFIER_PATTERN.fullmatch(str(value or "").strip()))


def validate_dataset_snapshot_id(
    value: str | None, *, allow_none: bool = True,
) -> str | None:
    """Validate an immutable dataset POPULATION identity.

    Fail closed.  A missing value is only tolerated for historical records when
    the caller explicitly opts in; a schema-registry string is ALWAYS rejected,
    because a schema identity can never stand in for a record population.
    """
    if value is None or not str(value).strip():
        if allow_none:
            return None
        raise Stage4IdentityError("MISSING_DATASET_SNAPSHOT_ID")
    resolved = str(value).strip()
    if resolved.startswith("DSNAP-") or not is_schema_identifier(resolved):
        if _DATASET_SNAPSHOT_PATTERN.fullmatch(resolved):
            return resolved
        raise Stage4IdentityError("MALFORMED_DATASET_SNAPSHOT_ID:" + resolved)
    raise Stage4IdentityError(
        "SCHEMA_IDENTIFIER_USED_AS_DATASET_SNAPSHOT_ID:" + resolved)


def validate_dataset_identity_separation(
    *, dataset_name: str, schema_version: str | None = None,
    dataset_snapshot_id: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Reject any accidental conflation of name / schema / population identity.

    Returns the validated ``(dataset_name, schema_version, snapshot_id)`` tuple
    so callers can use it as a normalisation step.
    """
    name = str(dataset_name or "").strip()
    if not name:
        raise Stage4IdentityError("MISSING_DATASET_NAME")
    schema = (None if schema_version is None
              else str(schema_version).strip() or None)
    snapshot = validate_dataset_snapshot_id(dataset_snapshot_id)
    if schema is not None:
        if is_schema_identifier(name):
            # A registry string was passed where a dataset FAMILY belongs.
            raise Stage4IdentityError(
                "SCHEMA_IDENTIFIER_USED_AS_DATASET_NAME:" + name)
        if schema == name:
            raise Stage4IdentityError(
                "DATASET_NAME_SCHEMA_CONFLATION:" + name)
        if snapshot is not None and snapshot in (schema, name):
            raise Stage4IdentityError(
                "SCHEMA_SNAPSHOT_CONFLATION:" + snapshot)
    elif snapshot is not None and snapshot == name:
        raise Stage4IdentityError("DATASET_NAME_SNAPSHOT_CONFLATION:" + name)
    return name, schema, snapshot


#: Legacy classification for records that only ever carried a schema string.
LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY = "LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY"

#: Shared epoch/evidence-set snapshot identity states.  They live here because
#: both the versioning overlay and the snapshot authority import this module,
#: and neither may import the other.
SNAPSHOT_IDENTITY_BOUND = "BOUND_TO_DATASET_SNAPSHOT"
SNAPSHOT_IDENTITY_UNRESOLVED_LIVE = "UNRESOLVED_POPULATION_NOT_FROZEN"
SNAPSHOT_IDENTITY_UNRESOLVED_HISTORICAL = "UNRESOLVED_HISTORICAL_WINDOW"
SNAPSHOT_IDENTITY_UNRESOLVED_NEVER_PERSISTED = "UNRESOLVED_NEVER_PERSISTED"
SNAPSHOT_IDENTITY_STATES = frozenset({
    SNAPSHOT_IDENTITY_BOUND, SNAPSHOT_IDENTITY_UNRESOLVED_LIVE,
    SNAPSHOT_IDENTITY_UNRESOLVED_HISTORICAL,
    SNAPSHOT_IDENTITY_UNRESOLVED_NEVER_PERSISTED,
})


def normalize_legacy_dataset_reference(value: str) -> dict[str, Any]:
    """Classify a historical dataset reference WITHOUT inventing a population.

    Older Stage 4 records carry only something like
    ``dataset_version = shadow_runtime_v1``.  That value is a SCHEMA identity.
    It is never reinterpreted as a dataset snapshot: the honest answer is that
    the exact historical population is unresolved.
    """
    resolved = str(value or "").strip()
    if not resolved:
        raise Stage4IdentityError("MISSING_LEGACY_DATASET_REFERENCE")
    if not is_schema_identifier(resolved):
        raise Stage4IdentityError("NOT_A_LEGACY_SCHEMA_REFERENCE:" + resolved)
    return {
        "legacy_dataset_version": resolved,
        "interpretation": "SCHEMA_IDENTIFIER",
        "is_dataset_snapshot_id": False,
        "compatibility_class": LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY,
        "dataset_snapshot_id": None,
        "reason": (
            "A schema identity does not identify an immutable record "
            "population. The exact historical population cannot be "
            "reconstructed, so no dataset_snapshot_id is synthesized."
        ),
    }


@dataclass(frozen=True, order=True)
class EvidenceMemberReference:
    """Stable reference to one exact member of a governed evidence set.

    ``dataset`` names the dataset FAMILY.  ``dataset_snapshot_id`` names the
    exact immutable POPULATION the member belongs to and is validated by shape
    here (registry resolution happens on the governed read side).  The two are
    never interchangeable.
    """

    reference_type: str
    reference_id: str
    dataset: str | None = None
    locator: str | None = None
    content_fingerprint: str | None = None
    dataset_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if not str(self.reference_type or "").strip():
            raise Stage4IdentityError("EVIDENCE_MEMBER_MISSING_TYPE")
        if not str(self.reference_id or "").strip():
            raise Stage4IdentityError("EVIDENCE_MEMBER_MISSING_ID")
        validate_dataset_snapshot_id(self.dataset_snapshot_id)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "dataset": self.dataset,
            "locator": self.locator,
            "content_fingerprint": self.content_fingerprint,
        }
        # Preserve the byte-level identity of Refinement-1-era members.  The
        # population field is additive and only serialised when it is bound.
        if self.dataset_snapshot_id is not None:
            value["dataset_snapshot_id"] = self.dataset_snapshot_id
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceMemberReference":
        return cls(
            reference_type=str(value.get("reference_type") or ""),
            reference_id=str(value.get("reference_id") or ""),
            dataset=(None if value.get("dataset") is None else str(value["dataset"])),
            locator=(None if value.get("locator") is None else str(value["locator"])),
            content_fingerprint=(None if value.get("content_fingerprint") is None
                                 else str(value["content_fingerprint"])),
            dataset_snapshot_id=(
                None if value.get("dataset_snapshot_id") is None
                else str(value["dataset_snapshot_id"])),
        )


@dataclass(frozen=True)
class EvidenceSet:
    """An immutable, many-to-many governed collection of evidence references.

    The evidence set is the governed COLLECTION identity.  The immutable
    dataset POPULATION(S) underneath it are named by ``dataset_snapshot_ids``
    (Stage 4 Refinement 2).  The two are never collapsed into one identifier:
    an evidence set may combine several governed snapshots, and one snapshot
    may back several evidence sets.

    ``dataset_snapshot_ids`` stays optional so that Refinement-1-era collections
    and not-yet-frozen live populations remain readable; a governed consumer
    must call :meth:`require_dataset_snapshots` (or the read-side validator)
    before treating the set as population-resolved evidence.
    """

    evidence_set_id: str
    observation_requirement_ids: tuple[str, ...]
    members: tuple[EvidenceMemberReference, ...]
    dataset_snapshot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        evidence_id = str(self.evidence_set_id or "").strip()
        if not evidence_id:
            raise Stage4IdentityError("MISSING_EVIDENCE_SET_ID")
        if not _EVIDENCE_SET_PATTERN.fullmatch(evidence_id):
            raise Stage4IdentityError("MALFORMED_EVIDENCE_SET_ID:" + evidence_id)
        requirements = tuple(sorted(set(self.observation_requirement_ids)))
        if not requirements:
            raise Stage4IdentityError("EVIDENCE_SET_WITHOUT_REQUIREMENTS")
        for rid in requirements:
            validate_requirement_id(rid)
        snapshots = tuple(sorted({
            str(validate_dataset_snapshot_id(item, allow_none=False))
            for item in self.dataset_snapshot_ids}))
        members = tuple(sorted(set(self.members), key=lambda item: canonical_json(
            item.to_dict())))
        if not members:
            raise Stage4IdentityError("EVIDENCE_SET_WITHOUT_MEMBERS")
        if snapshots:
            # A population-bound evidence set may not contain a member whose
            # population is unknown: that would be unverifiable evidence.
            declared = set(snapshots)
            for member in members:
                if member.dataset_snapshot_id is None:
                    raise Stage4IdentityError(
                        "EVIDENCE_MEMBER_WITHOUT_DATASET_SNAPSHOT:"
                        + member.reference_id)
                if member.dataset_snapshot_id not in declared:
                    raise Stage4IdentityError(
                        "EVIDENCE_MEMBER_SNAPSHOT_NOT_IN_SET:"
                        + member.reference_id)
            represented = {
                str(member.dataset_snapshot_id) for member in members}
            missing_membership = sorted(declared - represented)
            if missing_membership:
                raise Stage4IdentityError(
                    "DATASET_SNAPSHOT_WITHOUT_EVIDENCE_MEMBER:"
                    + ",".join(missing_membership))
        object.__setattr__(self, "evidence_set_id", evidence_id)
        object.__setattr__(self, "observation_requirement_ids", requirements)
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "dataset_snapshot_ids", snapshots)

    @classmethod
    def deterministic(
        cls, *, observation_requirement_ids: Iterable[str],
        members: Iterable[EvidenceMemberReference],
        dataset_snapshot_ids: Iterable[str] = (),
    ) -> "EvidenceSet":
        requirements = tuple(sorted(set(observation_requirement_ids)))
        evidence_members = tuple(sorted(set(members), key=lambda item: canonical_json(
            item.to_dict())))
        snapshots = tuple(sorted(set(dataset_snapshot_ids)))
        material = {
            "observation_requirement_ids": requirements,
            "members": [member.to_dict() for member in evidence_members],
        }
        if snapshots:
            material["dataset_snapshot_ids"] = list(snapshots)
        return cls("ESET-" + fingerprint(material)[:24].upper(),
                   requirements, evidence_members, snapshots)

    @property
    def content_fingerprint(self) -> str:
        material = {
            "observation_requirement_ids": self.observation_requirement_ids,
            "members": [member.to_dict() for member in self.members],
        }
        if self.dataset_snapshot_ids:
            material["dataset_snapshot_ids"] = list(self.dataset_snapshot_ids)
        return fingerprint(material)

    @property
    def dataset_snapshot_id(self) -> str | None:
        """The single bound snapshot, or ``None`` when 0 or >1 are bound."""
        return (self.dataset_snapshot_ids[0]
                if len(self.dataset_snapshot_ids) == 1 else None)

    def require_dataset_snapshots(self) -> tuple[str, ...]:
        """Fail closed unless this governed set resolves to a population."""
        if not self.dataset_snapshot_ids:
            raise Stage4IdentityError(
                "EVIDENCE_SET_WITHOUT_DATASET_SNAPSHOT:" + self.evidence_set_id)
        return self.dataset_snapshot_ids

    def dataset_names(self) -> tuple[str, ...]:
        """Dataset FAMILIES the members belong to (never snapshot identities)."""
        return tuple(sorted({str(member.dataset) for member in self.members
                             if member.dataset}))

    def to_dict(self) -> dict[str, Any]:
        value = {
            "evidence_set_id": self.evidence_set_id,
            "observation_requirement_ids": list(self.observation_requirement_ids),
            "evidence_member_references": [m.to_dict() for m in self.members],
            "content_fingerprint": self.content_fingerprint,
        }
        if self.dataset_snapshot_ids:
            value["dataset_snapshot_ids"] = list(self.dataset_snapshot_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceSet":
        item = cls(
            evidence_set_id=str(value.get("evidence_set_id") or ""),
            observation_requirement_ids=tuple(
                str(item) for item in value.get("observation_requirement_ids", ())),
            members=tuple(EvidenceMemberReference.from_dict(item) for item in
                          value.get("evidence_member_references", ())),
            dataset_snapshot_ids=tuple(
                str(item) for item in value.get("dataset_snapshot_ids", ())),
        )
        claimed = value.get("content_fingerprint")
        if claimed is not None and str(claimed) != item.content_fingerprint:
            raise Stage4IdentityError("EVIDENCE_SET_FINGERPRINT_MISMATCH")
        return item


class EvidenceSetRegistry:
    """Collision-safe persistent registry and bidirectional relationship map."""

    SCHEMA = 1

    def __init__(self, evidence_sets: Iterable[EvidenceSet] = ()) -> None:
        self._sets: dict[str, EvidenceSet] = {}
        for evidence_set in evidence_sets:
            self.add(evidence_set)

    def add(self, evidence_set: EvidenceSet) -> EvidenceSet:
        existing = self._sets.get(evidence_set.evidence_set_id)
        if existing is not None:
            if existing.content_fingerprint != evidence_set.content_fingerprint:
                raise Stage4IdentityError(
                    "CONFLICTING_EVIDENCE_SET_ID:" + evidence_set.evidence_set_id)
            return existing
        self._sets[evidence_set.evidence_set_id] = evidence_set
        return evidence_set

    def require(self, evidence_set_id: str) -> EvidenceSet:
        try:
            return self._sets[str(evidence_set_id)]
        except KeyError:
            raise Stage4IdentityError(
                "UNKNOWN_EVIDENCE_SET_ID:" + str(evidence_set_id)) from None

    def evidence_sets_for_requirement(self, requirement_id: str) -> tuple[str, ...]:
        rid = validate_requirement_id(requirement_id)
        return tuple(sorted(item.evidence_set_id for item in self._sets.values()
                            if rid in item.observation_requirement_ids))

    def requirements_for_evidence_set(self, evidence_set_id: str) -> tuple[str, ...]:
        return self.require(evidence_set_id).observation_requirement_ids

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, "evidence_sets": [
            item.to_dict() for item in sorted(
                self._sets.values(), key=lambda item: item.evidence_set_id)]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceSetRegistry":
        if value.get("schema") != cls.SCHEMA:
            raise Stage4IdentityError("UNKNOWN_EVIDENCE_SET_REGISTRY_SCHEMA")
        rows = value.get("evidence_sets")
        if not isinstance(rows, list):
            raise Stage4IdentityError("EVIDENCE_SET_REGISTRY_MISSING_ROWS")
        return cls(EvidenceSet.from_dict(row) for row in rows)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True)
                             + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> "EvidenceSetRegistry":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise Stage4IdentityError("EVIDENCE_SET_REGISTRY_UNREADABLE") from exc
        return cls.from_dict(value)


__all__ = [
    "CANONICAL_REQUIREMENT_IDS", "DATASET_SNAPSHOT_ID_PREFIX",
    "EvidenceMemberReference", "EvidenceSet",
    "EvidenceSetRegistry", "LEGACY_QUESTION_REQUIREMENTS",
    "LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY",
    "REQUIREMENT_MATRIX_PATH", "RequirementAuthority", "Stage4IdentityError",
    "is_schema_identifier", "normalize_legacy_dataset_reference",
    "requirement_authority", "requirement_id_for_question",
    "resolve_legacy_transition_id", "validate_dataset_identity_separation",
    "validate_dataset_snapshot_id", "validate_requirement_id",
]
