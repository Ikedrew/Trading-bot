"""Canonical report ownership contract (Repair Wave RW1).

Legacy IDs are compatibility/history identifiers.  They are not canonical
scientific ownership.  This module records the explicitly adjudicated canonical
ownership of shared report artifacts and makes report ownership fail closed:

* an adjudicated artifact may only ever satisfy its declared canonical owner;
* a scientifically distinct canonical question that historically shared the
  artifact's identity receives a fail-closed ownership state instead of
  inheriting the owner's validity, finding, or completion;
* report metadata can never silently contradict the adjudicated owner;
* artifacts that were never adjudicated keep the repository's already recorded
  compatibility behaviour, so unrelated legacy routing neither widens nor breaks.

The ownership map is derived from the frozen Wave-A5 ownership findings, so the
runtime contract cannot drift from the authoritative planning artifact.  This
module is read-only: it resolves ownership and never writes state.

OWNERSHIP CONTRACT (RW1)
------------------------
q1_component_reward.json -> D1  (never L3)
q5_pattern_degradation.json -> E2  (never L1)

OWNERSHIP CONTRACT (Repair 2B.1)
--------------------------------
e3_strategy_family_expectancy.json -> E3  (S1 is a superseded alias, not a co-owner)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from research_engine.registry.research_question_registry import REGISTRY
from research_engine.registry.wave_a5_definitions import WAVE_A5_OWNERSHIP


# Decision kinds -----------------------------------------------------------
OWNED = "OWNED_BY_CANONICAL_QUESTION"
NOT_ADJUDICATED = "NOT_ADJUDICATED"
OWNERSHIP_DENIED = "OWNERSHIP_DENIED"
OWNERSHIP_METADATA_CONFLICT = "OWNERSHIP_METADATA_CONFLICT"
ALLOWED_KINDS = frozenset({OWNED, NOT_ADJUDICATED})

# Repository-level fail-closed state used whenever ownership is not
# unambiguous.  It is surfaced as ReportValidity.MISSING (the requesting
# question owns no artifact) or ReportValidity.INVALIDATED (an artifact exists
# but its metadata contradicts the canonical owner).
FAIL_CLOSED_STATE = "AMBIGUOUS_REPORT_MAPPING"

_JSON_SUFFIX = ".json"


def _filename_key(report_filename: str) -> str:
    """Normalise an artifact reference to a comparable bare filename."""
    value = str(report_filename or "").strip().replace("\\", "/")
    return value.rsplit("/", 1)[-1].lower()


def _derive_adjudicated_owners() -> dict[str, str]:
    """Derive adjudicated report ownership from the frozen Wave-A5 findings."""
    owners: dict[str, str] = {}
    for relationship in WAVE_A5_OWNERSHIP.values():
        for artifact, owner in relationship.canonical_owners:
            filename = _filename_key(artifact)
            if not filename.endswith(_JSON_SUFFIX):
                continue
            existing = owners.get(filename)
            if existing is not None and existing != owner:
                raise RuntimeError(
                    f"conflicting adjudicated report owners for {filename}: "
                    f"{existing} and {owner}"
                )
            owners[filename] = owner
    return dict(sorted(owners.items()))


ADJUDICATED_REPORT_OWNERS: dict[str, str] = _derive_adjudicated_owners()

_BY_ID = {question.id: question for question in REGISTRY}

# Fail closed at import time if an adjudicated owner does not actually declare
# the artifact it was adjudicated.  Never silently accept an unverifiable map.
for _filename, _owner in ADJUDICATED_REPORT_OWNERS.items():
    _owner_question = _BY_ID.get(_owner)
    if _owner_question is None:
        raise RuntimeError(
            f"adjudicated report owner {_owner} for {_filename} is not a canonical question"
        )
    if _filename_key(_owner_question.report_filename) != _filename:
        raise RuntimeError(
            f"adjudicated report owner {_owner} does not declare {_filename} "
            "as its canonical report"
        )
del _filename, _owner, _owner_question

@dataclass(frozen=True)
class ReportOwnershipDecision:
    """Deterministic ownership verdict for one (artifact, question) pair."""

    report_filename: str
    canonical_owner: str | None
    kind: str
    allowed: bool
    reason: str

    @property
    def adjudicated(self) -> bool:
        return self.canonical_owner is not None

    @property
    def fail_closed_state(self) -> str:
        return "" if self.allowed else FAIL_CLOSED_STATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_filename": self.report_filename,
            "canonical_owner": self.canonical_owner,
            "kind": self.kind,
            "allowed": self.allowed,
            "fail_closed_state": self.fail_closed_state,
            "reason": self.reason,
        }


def canonical_report_owner(report_filename: str) -> str | None:
    """Return the adjudicated canonical owner of an artifact, if one exists."""
    return ADJUDICATED_REPORT_OWNERS.get(_filename_key(report_filename))


def is_adjudicated_report(report_filename: str) -> bool:
    return canonical_report_owner(report_filename) is not None


def declared_co_claimants(report_filename: str) -> tuple[str, ...]:
    """Canonical questions that declare the artifact but do not canonically own it."""
    filename = _filename_key(report_filename)
    owner = ADJUDICATED_REPORT_OWNERS.get(filename)
    if owner is None:
        return ()
    return tuple(sorted(
        question.id
        for question in REGISTRY
        if question.id != owner and _filename_key(question.report_filename) == filename
    ))


def report_ownership_contract() -> dict[str, str]:
    """Read-only view of the adjudicated ownership contract."""
    return dict(ADJUDICATED_REPORT_OWNERS)


def _metadata_conflict(
    filename: str,
    owner: str,
    report_metadata: Mapping[str, Any] | None,
) -> str:
    """Return a conflict description if artifact metadata contradicts ownership."""
    if not isinstance(report_metadata, Mapping):
        return ""
    declared = str(report_metadata.get("question_id", "") or "").strip().upper()
    if not declared:
        return ""
    owner_question = _BY_ID[owner]
    if declared == owner.upper():
        return ""
    if declared in {str(alias).strip().upper() for alias in owner_question.legacy_ids}:
        return ""
    return (
        f"{filename} declares question identity {declared!r}, which is neither the "
        f"canonical owner {owner} nor one of its declared compatibility IDs"
    )


def resolve_report_ownership(
    report_filename: str,
    question_id: str,
    *,
    report_metadata: Mapping[str, Any] | None = None,
) -> ReportOwnershipDecision:
    """Resolve artifact ownership for one canonical question, failing closed.

    Only an adjudicated artifact is strictly single-owner.  Every other artifact
    keeps the repository's existing compatibility behaviour and is reported as
    NOT_ADJUDICATED so that ownership is never implied silently.
    """
    filename = _filename_key(report_filename)
    owner = ADJUDICATED_REPORT_OWNERS.get(filename)
    if owner is None:
        return ReportOwnershipDecision(
            report_filename=filename,
            canonical_owner=None,
            kind=NOT_ADJUDICATED,
            allowed=True,
            reason=(
                "Artifact ownership is not adjudicated; declared compatibility routing applies."
            ),
        )

    requested = str(question_id or "").strip().upper()
    if requested != owner.upper():
        return ReportOwnershipDecision(
            report_filename=filename,
            canonical_owner=owner,
            kind=OWNERSHIP_DENIED,
            allowed=False,
            reason=(
                f"{FAIL_CLOSED_STATE}: {filename} is canonically owned by {owner}; "
                f"{requested or 'an unspecified question'} is not its canonical owner and "
                f"cannot inherit this artifact's validity, finding, or completion."
            ),
        )

    conflict = _metadata_conflict(filename, owner, report_metadata)
    if conflict:
        return ReportOwnershipDecision(
            report_filename=filename,
            canonical_owner=owner,
            kind=OWNERSHIP_METADATA_CONFLICT,
            allowed=False,
            reason=f"{FAIL_CLOSED_STATE}: {conflict}.",
        )

    return ReportOwnershipDecision(
        report_filename=filename,
        canonical_owner=owner,
        kind=OWNED,
        allowed=True,
        reason=f"{filename} is canonically owned by {owner}.",
    )
