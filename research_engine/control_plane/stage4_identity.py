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


@dataclass(frozen=True, order=True)
class EvidenceMemberReference:
    """Stable reference to one exact member of a governed evidence set."""

    reference_type: str
    reference_id: str
    dataset: str | None = None
    locator: str | None = None
    content_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not str(self.reference_type or "").strip():
            raise Stage4IdentityError("EVIDENCE_MEMBER_MISSING_TYPE")
        if not str(self.reference_id or "").strip():
            raise Stage4IdentityError("EVIDENCE_MEMBER_MISSING_ID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "dataset": self.dataset,
            "locator": self.locator,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceMemberReference":
        return cls(
            reference_type=str(value.get("reference_type") or ""),
            reference_id=str(value.get("reference_id") or ""),
            dataset=(None if value.get("dataset") is None else str(value["dataset"])),
            locator=(None if value.get("locator") is None else str(value["locator"])),
            content_fingerprint=(None if value.get("content_fingerprint") is None
                                 else str(value["content_fingerprint"])),
        )


@dataclass(frozen=True)
class EvidenceSet:
    """An immutable, many-to-many governed collection of evidence references."""

    evidence_set_id: str
    observation_requirement_ids: tuple[str, ...]
    members: tuple[EvidenceMemberReference, ...]

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
        members = tuple(sorted(set(self.members), key=lambda item: canonical_json(
            item.to_dict())))
        if not members:
            raise Stage4IdentityError("EVIDENCE_SET_WITHOUT_MEMBERS")
        object.__setattr__(self, "evidence_set_id", evidence_id)
        object.__setattr__(self, "observation_requirement_ids", requirements)
        object.__setattr__(self, "members", members)

    @classmethod
    def deterministic(
        cls, *, observation_requirement_ids: Iterable[str],
        members: Iterable[EvidenceMemberReference],
    ) -> "EvidenceSet":
        requirements = tuple(sorted(set(observation_requirement_ids)))
        evidence_members = tuple(sorted(set(members), key=lambda item: canonical_json(
            item.to_dict())))
        material = {
            "observation_requirement_ids": requirements,
            "members": [member.to_dict() for member in evidence_members],
        }
        return cls("ESET-" + fingerprint(material)[:24].upper(),
                   requirements, evidence_members)

    @property
    def content_fingerprint(self) -> str:
        return fingerprint({
            "observation_requirement_ids": self.observation_requirement_ids,
            "members": [member.to_dict() for member in self.members],
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_set_id": self.evidence_set_id,
            "observation_requirement_ids": list(self.observation_requirement_ids),
            "evidence_member_references": [m.to_dict() for m in self.members],
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceSet":
        item = cls(
            evidence_set_id=str(value.get("evidence_set_id") or ""),
            observation_requirement_ids=tuple(
                str(item) for item in value.get("observation_requirement_ids", ())),
            members=tuple(EvidenceMemberReference.from_dict(item) for item in
                          value.get("evidence_member_references", ())),
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
    "CANONICAL_REQUIREMENT_IDS", "EvidenceMemberReference", "EvidenceSet",
    "EvidenceSetRegistry", "LEGACY_QUESTION_REQUIREMENTS",
    "REQUIREMENT_MATRIX_PATH", "RequirementAuthority", "Stage4IdentityError",
    "requirement_authority", "requirement_id_for_question",
    "resolve_legacy_transition_id", "validate_requirement_id",
]
