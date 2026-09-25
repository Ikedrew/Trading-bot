"""
Generated Research Identity v1 — governed identity for NON-canonical research.

Stage ③ / Wave 0 foundation. This module establishes ONE thing: a separate,
immutable, deterministic identity namespace for generated research that lives
OUTSIDE the frozen canonical 70-question programme.

WHAT THIS IS
------------
A generated research record is a *scientific identity*, not a question. It
describes a research intent that a later wave may turn into a governed
specification. Wave 0 deliberately stops there:

    - it does NOT generate questions;
    - it does NOT execute research, experiments, hypotheses or candidates;
    - it does NOT implement dimensions, interactions, slices or cells;
    - it does NOT prioritise, schedule or size multiplicity families;
    - it has ZERO production authority.

WHAT THIS IS NOT
----------------
It is NOT a Q71. It is NOT a member of `REGISTRY`. It never appends to
`research_engine.registry.research_question_registry.REGISTRY`, never touches
`baseline_manifest.BASELINE_QUESTION_IDS`, and never alters canonical
definitions. The canonical 70 remain the only canonical research inventory;
`research_engine.lifecycle.generated_research_isolation` mechanically proves it.

NAMESPACE
---------
Canonical IDs are short alphabetic programme codes (``E1``, ``M4``, ``X6``,
``PORT-1``). ``X001`` from the Stage ③ audit would therefore be *visually
inside* the Execution ``X`` programme. This module reserves the prefix::

    GEN-<16 hex characters of the semantic identity digest>

``GEN-`` is disjoint from every canonical programme prefix, and the collision
guard rejects any presented ID that is a canonical ID.

DETERMINISM
-----------
The scientific identity is a SHA-256 digest of the canonical JSON encoding of
the frozen semantic material (see `semantic_material`). It never depends on
insertion order, wall-clock time, a counter, or a random UUID. Two equivalent
proposals — even proposed months apart, with different creation timestamps —
resolve to the same `semantic_identity` and therefore to the same
`generated_research_id`.

TIMESTAMPS
----------
`created_at` is provenance, never semantic identity. It is deliberately absent
from `semantic_material()`.

VERSIONING
----------
Clean-reset architecture: `GENERATED_RESEARCH_SCHEMA_VERSION` and
`GENERATED_RESEARCH_SEMANTIC_VERSION` both start at 1 and must never exceed 1.

EXTENSION POINT FOR WAVE 1+
---------------------------
`dimension_ref` is part of the frozen semantic material and is `None` in Wave 0
— "explicitly not governed yet" rather than invented data. Wave 1 governed
dimension/interaction semantics populate that same field; records written
before then stay valid because their `None` is part of their own digest.

No lifecycle state is stored: none of the existing lifecycle enums can be
reused without implying execution or promotion authority, and Wave 0 executes
nothing. A later wave adds state as a separate, non-semantic field.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

# ─── Versions (clean reset: both start at 1, never > 1) ─────────────────────

GENERATED_RESEARCH_SCHEMA_VERSION: int = 1
GENERATED_RESEARCH_SEMANTIC_VERSION: int = 1

# ─── Namespace ──────────────────────────────────────────────────────────────

GENERATED_RESEARCH_ID_PREFIX = "GEN-"
GENERATED_RESEARCH_ID_DIGEST_CHARS = 16

# A governed reference token, e.g. "finding:FT-9", "hypothesis:H-3",
# "candidate:CAND-1", "generated_research:GEN-0123456789ABCDEF".
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_GEN_ID_RE = re.compile(
    rf"^{re.escape(GENERATED_RESEARCH_ID_PREFIX)}"
    rf"[0-9A-F]{{{GENERATED_RESEARCH_ID_DIGEST_CHARS}}}$"
)


# ─── Errors (fail closed, never silently repaired) ──────────────────────────


class GeneratedResearchError(RuntimeError):
    """Base failure for the generated-research identity layer."""


class GeneratedResearchValidationError(GeneratedResearchError):
    """Proposal/record material is invalid or incomplete."""


class GeneratedResearchNamespaceViolation(GeneratedResearchError):
    """A generated ID collided with the frozen canonical 70 namespace."""


class GeneratedResearchIdentityConflict(GeneratedResearchError):
    """A presented generated ID already exists with different immutable semantics."""


# ─── Research kind (identity taxonomy only — no Wave 0 behaviour) ───────────


class GeneratedResearchKind(str, Enum):
    """
    Identity label for the research kind / curiosity mode of a proposal.

    IDENTITY TAXONOMY ONLY. Wave 0 attaches no generation, screening,
    prioritisation, agenda or execution behaviour to any member: registering a
    record with any of these kinds creates identity and nothing else. The
    members exist so equivalent proposals can be distinguished deterministically
    today, without prejudging how a later wave implements them.
    """

    UNCLASSIFIED = "UNCLASSIFIED"
    EXPANSION = "EXPANSION"
    INTERACTION = "INTERACTION"
    DESTRUCTIVE = "DESTRUCTIVE"


# ─── Canonical encoding ─────────────────────────────────────────────────────


def _json_value(value: Any) -> Any:
    """Reduce a value to a deterministic, JSON-native form (fail closed)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise GeneratedResearchValidationError(
                "non-finite float is not representable in canonical identity material")
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise GeneratedResearchValidationError(
                    "canonical identity material requires string keys, "
                    f"got {type(key).__name__}")
            out[key] = _json_value(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise GeneratedResearchValidationError(
        f"unsupported value in canonical identity material: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Deterministic, order-independent JSON encoding used for identity."""
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeneratedResearchValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise GeneratedResearchValidationError(f"{label} must not have surrounding whitespace")
    return value


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise GeneratedResearchValidationError(
            f"{label} is not a valid identity token: {text!r}")
    return text


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise GeneratedResearchValidationError(
            f"{label} must be a governed '<kind>:<id>' reference, got {text!r}")
    return text


def _normalise_parents(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise GeneratedResearchValidationError(
            "parent_refs must be a sequence of references")
    refs = [_require_reference(item, "parent_ref") for item in values]
    if len(set(refs)) != len(refs):
        raise GeneratedResearchValidationError("parent_refs must not contain duplicates")
    return tuple(sorted(refs))


def validate_version(value: Any, label: str) -> int:
    """Clean-reset version guard: only version 1 is ever valid here."""
    if value != GENERATED_RESEARCH_SCHEMA_VERSION:
        raise GeneratedResearchValidationError(
            f"{label} must be {GENERATED_RESEARCH_SCHEMA_VERSION} (clean reset), "
            f"got {value!r}")
    return int(value)


def is_generated_research_id(value: Any) -> bool:
    """True only for IDs inside the reserved generated-research namespace."""
    return isinstance(value, str) and bool(_GEN_ID_RE.match(value))


def generated_research_id_for(semantic_identity: str) -> str:
    """Derive the governed display ID deterministically from a semantic identity."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise GeneratedResearchValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return (
        f"{GENERATED_RESEARCH_ID_PREFIX}"
        f"{semantic_identity[:GENERATED_RESEARCH_ID_DIGEST_CHARS].upper()}"
    )


# ─── Proposal (input) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class GeneratedResearchProposal:
    """
    An explicit proposal for a generated research identity.

    A proposal carries semantic material ONLY. It carries no timestamp, no
    counter and no identifier: identity is derived from these fields alone.
    """

    research_kind: GeneratedResearchKind
    trigger_ref: str                       # why this research exists (source provenance)
    target_kind: str                       # what class of thing is researched
    target_ref: str                        # the research subject itself
    specification: Mapping[str, Any] = field(default_factory=dict)
    dimension_ref: str | None = None       # governed dimension identity; None in Wave 0
    parent_refs: tuple[str, ...] = ()      # parent research/finding/hypothesis/candidate

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen semantic material that defines scientific identity.

        `created_at` is intentionally absent: a timestamp is provenance, not
        identity, so the same proposal discovered later still deduplicates.
        """
        return {
            "schema_version": GENERATED_RESEARCH_SCHEMA_VERSION,
            "semantic_version": GENERATED_RESEARCH_SEMANTIC_VERSION,
            "research_kind": self.research_kind.value,
            "trigger_ref": self.trigger_ref,
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "dimension_ref": self.dimension_ref,
            # Canonical (sorted, de-duplicated) order: the caller's argument order
            # must never leak into scientific identity.
            "parent_refs": list(_normalise_parents(self.parent_refs)),
            "specification": _json_value(self.specification),
        }


def _validate_proposal(proposal: GeneratedResearchProposal) -> GeneratedResearchProposal:
    if not isinstance(proposal, GeneratedResearchProposal):
        raise GeneratedResearchValidationError(
            f"expected GeneratedResearchProposal, got {type(proposal).__name__}")
    try:
        kind = GeneratedResearchKind(proposal.research_kind)
    except ValueError as exc:
        raise GeneratedResearchValidationError(
            f"unknown research kind: {proposal.research_kind!r}") from exc
    if kind is not proposal.research_kind:
        raise GeneratedResearchValidationError(
            "research_kind must be a GeneratedResearchKind member")

    _require_reference(proposal.trigger_ref, "trigger_ref")
    _require_token(proposal.target_kind, "target_kind")
    _require_text(proposal.target_ref, "target_ref")
    if proposal.dimension_ref is not None:
        _require_token(proposal.dimension_ref, "dimension_ref")
    _normalise_parents(proposal.parent_refs)
    if not isinstance(proposal.specification, Mapping):
        raise GeneratedResearchValidationError("specification must be a mapping")
    # Fail closed on material that cannot be canonically encoded.
    canonical_json(proposal.specification)
    return proposal


def semantic_identity_for(proposal: GeneratedResearchProposal) -> str:
    """Deterministic SHA-256 scientific identity of an equivalent proposal."""
    _validate_proposal(proposal)
    material = proposal.semantic_material()
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


# ─── Record (persisted, immutable) ──────────────────────────────────────────


@dataclass(frozen=True)
class GeneratedResearchRecord:
    """
    A persisted generated research identity.

    Immutable: every semantic field is frozen, validated, and re-verified
    against the digest on load. `created_at` is provenance only and is never
    rewritten by re-registration of an equivalent proposal.
    """

    generated_research_id: str
    semantic_identity: str
    schema_version: int
    semantic_version: int
    created_at: str
    research_kind: GeneratedResearchKind
    trigger_ref: str
    target_kind: str
    target_ref: str
    specification_json: str
    dimension_ref: str | None = None
    parent_refs: tuple[str, ...] = ()
    provenance_json: str = "{}"

    # ─── Derived views ───────────────────────────────────────────────────

    @property
    def specification(self) -> dict[str, Any]:
        return json.loads(self.specification_json)

    @property
    def provenance(self) -> dict[str, Any]:
        return json.loads(self.provenance_json)

    def semantic_material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "semantic_version": self.semantic_version,
            "research_kind": self.research_kind.value,
            "trigger_ref": self.trigger_ref,
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "dimension_ref": self.dimension_ref,
            "parent_refs": list(self.parent_refs),
            "specification": self.specification,
        }

    # ─── Construction ────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        proposal: GeneratedResearchProposal,
        *,
        created_at: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> "GeneratedResearchRecord":
        """Build a new record for a proposal. Identity is derived, never supplied."""
        _validate_proposal(proposal)
        material = proposal.semantic_material()
        identity = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
        stamp = created_at if created_at is not None else datetime.now(timezone.utc).isoformat()
        _require_text(stamp, "created_at")
        record = cls(
            generated_research_id=generated_research_id_for(identity),
            semantic_identity=identity,
            schema_version=GENERATED_RESEARCH_SCHEMA_VERSION,
            semantic_version=GENERATED_RESEARCH_SEMANTIC_VERSION,
            created_at=stamp,
            research_kind=proposal.research_kind,
            trigger_ref=proposal.trigger_ref,
            target_kind=proposal.target_kind,
            target_ref=proposal.target_ref,
            specification_json=canonical_json(material["specification"]),
            dimension_ref=proposal.dimension_ref,
            parent_refs=_normalise_parents(proposal.parent_refs),
            provenance_json=canonical_json(provenance or {}),
        )
        return record.validate()

    # ─── Validation / serialisation ──────────────────────────────────────

    def validate(self) -> "GeneratedResearchRecord":
        """Re-derive identity from persisted material; fail closed on any drift."""
        validate_version(self.schema_version, "schema_version")
        validate_version(self.semantic_version, "semantic_version")
        if not isinstance(self.semantic_identity, str) or not _HEX64_RE.match(self.semantic_identity):
            raise GeneratedResearchValidationError(
                "semantic_identity is not a lowercase sha256 hex digest")
        if not is_generated_research_id(self.generated_research_id):
            raise GeneratedResearchValidationError(
                "generated_research_id is outside the reserved namespace: "
                f"{self.generated_research_id!r}")
        _require_text(self.created_at, "created_at")
        _require_reference(self.trigger_ref, "trigger_ref")
        _require_token(self.target_kind, "target_kind")
        _require_text(self.target_ref, "target_ref")
        if self.dimension_ref is not None:
            _require_token(self.dimension_ref, "dimension_ref")
        _normalise_parents(self.parent_refs)
        if tuple(sorted(self.parent_refs)) != tuple(self.parent_refs):
            raise GeneratedResearchValidationError(
                "parent_refs must be stored in canonical (sorted) order")

        try:
            specification = json.loads(self.specification_json)
            provenance = json.loads(self.provenance_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise GeneratedResearchValidationError(
                "persisted payload is not valid JSON") from exc
        if not isinstance(specification, Mapping):
            raise GeneratedResearchValidationError("specification must decode to a mapping")
        if not isinstance(provenance, Mapping):
            raise GeneratedResearchValidationError("provenance must decode to a mapping")
        if canonical_json(specification) != self.specification_json:
            raise GeneratedResearchValidationError(
                "specification_json is not canonically encoded")
        if canonical_json(provenance) != self.provenance_json:
            raise GeneratedResearchValidationError(
                "provenance_json is not canonically encoded")

        try:
            kind = GeneratedResearchKind(self.research_kind)
        except ValueError as exc:
            raise GeneratedResearchValidationError(
                f"unknown research kind: {self.research_kind!r}") from exc

        material = {
            "schema_version": self.schema_version,
            "semantic_version": self.semantic_version,
            "research_kind": kind.value,
            "trigger_ref": self.trigger_ref,
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "dimension_ref": self.dimension_ref,
            "parent_refs": list(self.parent_refs),
            "specification": specification,
        }
        expected = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
        if expected != self.semantic_identity:
            raise GeneratedResearchIdentityConflict(
                "persisted semantic material does not match identity "
                f"{self.semantic_identity}")
        if generated_research_id_for(self.semantic_identity) != self.generated_research_id:
            raise GeneratedResearchIdentityConflict(
                f"generated_research_id {self.generated_research_id} does not match "
                f"its semantic identity {self.semantic_identity}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_research_id": self.generated_research_id,
            "semantic_identity": self.semantic_identity,
            "schema_version": self.schema_version,
            "semantic_version": self.semantic_version,
            "created_at": self.created_at,
            "research_kind": self.research_kind.value,
            "trigger_ref": self.trigger_ref,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "dimension_ref": self.dimension_ref,
            "parent_refs": list(self.parent_refs),
            "specification_json": self.specification_json,
            "provenance_json": self.provenance_json,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GeneratedResearchRecord":
        """Strict deserialisation. Unknown/missing/corrupt content fails closed."""
        if not isinstance(data, Mapping):
            raise GeneratedResearchValidationError(
                f"persisted record must be a mapping, got {type(data).__name__}")
        expected = {
            "generated_research_id", "semantic_identity", "schema_version", "semantic_version",
            "created_at", "research_kind", "trigger_ref", "target_kind", "target_ref",
            "dimension_ref", "parent_refs", "specification_json", "provenance_json",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise GeneratedResearchValidationError(f"persisted record missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise GeneratedResearchValidationError(
                f"persisted record has unknown fields: {unknown}")

        parents = data["parent_refs"]
        if not isinstance(parents, list):
            raise GeneratedResearchValidationError("parent_refs must be a list in persistence")
        try:
            record = cls(
                generated_research_id=data["generated_research_id"],
                semantic_identity=data["semantic_identity"],
                schema_version=data["schema_version"],
                semantic_version=data["semantic_version"],
                created_at=data["created_at"],
                research_kind=GeneratedResearchKind(data["research_kind"]),
                trigger_ref=data["trigger_ref"],
                target_kind=data["target_kind"],
                target_ref=data["target_ref"],
                specification_json=data["specification_json"],
                dimension_ref=data["dimension_ref"],
                parent_refs=tuple(parents),
                provenance_json=data["provenance_json"],
            )
        except (GeneratedResearchError, TypeError, ValueError) as exc:
            raise GeneratedResearchValidationError(
                f"persisted record is malformed: {exc}") from exc
        return record.validate()


__all__ = [
    "GENERATED_RESEARCH_ID_DIGEST_CHARS",
    "GENERATED_RESEARCH_ID_PREFIX",
    "GENERATED_RESEARCH_SCHEMA_VERSION",
    "GENERATED_RESEARCH_SEMANTIC_VERSION",
    "GeneratedResearchError",
    "GeneratedResearchIdentityConflict",
    "GeneratedResearchKind",
    "GeneratedResearchNamespaceViolation",
    "GeneratedResearchProposal",
    "GeneratedResearchRecord",
    "GeneratedResearchValidationError",
    "canonical_json",
    "generated_research_id_for",
    "is_generated_research_id",
    "semantic_identity_for",
    "validate_version",
]
