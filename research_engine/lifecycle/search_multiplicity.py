"""
Multiplicity Governance v1 -- the derived Bonferroni denominator and the governed
bridge into the EXISTING validation machinery.

Stage 3 / Wave 4. `search_provenance` records WHAT was searched. This module turns
that record into the one number the repository's conclusion logic already knows
how to consume: `ValidationSpec.bonferroni_tests`.

THE RULE (no guessing)
======================
The denominator is DERIVED, once, from the recorded alternatives under the
family's declared `MultiplicityRule`. There is no second place to enter it, and
a value a caller hands to `ValidationSpec` is CHECKED against the derived one
rather than trusted.

    STATISTICALLY_EVALUATED_ALTERNATIVES
        count every alternative whose recorded evaluation says a statistical test
        actually ran (including the winner).
    ALL_CONSIDERED_ALTERNATIVES
        count EVERY recorded alternative, including pre-test BLOCKED,
        WAITING_DATA and REFUSE alternatives.

There is deliberately no rule that counts only PERMIT alternatives: if a test
ran before an eligibility classification, that tested loser is part of the same
multiple-comparison problem, and the `evaluation` field -- not the Wave 2 state --
is what records that.

WHAT THIS IS NOT
----------------
It does not rewrite the statistical system, does not choose a test, does not
compute a p-value, does not invent a profit probability or a confidence score,
and does not decide which search matters more. It derives a count and refuses to
let a caller contradict it.

BACKWARD COMPATIBILITY
======================
`ValidationSpec` is NOT modified. Historical experiments that were not created
through a Stage ③ governed search keep whatever `bonferroni_tests` they already
carried; nothing here rewrites them. The governed bridge is opt-in: a caller must
explicitly hand this module a `SearchRecord`.

NO PRODUCTION AUTHORITY
-----------------------
No runner, no orchestrator, no `GovernanceGate`, no execution, risk, sizing,
production configuration, baseline activation or candidate promotion. No I/O and
no import-time writes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.search_provenance import (
    MULTIPLICITY_ID_PREFIX,
    MultiplicityRule,
    SearchProvenanceError,
    SearchProvenanceValidationError,
    SearchRecord,
    SelectionFreeze,
)

MULTIPLICITY_SCHEMA_VERSION: int = 1

#: The correction method this bridge implements. The repository's existing
#: conclusion logic applies Bonferroni; nothing else is claimed here.
CORRECTION_METHOD_BONFERRONI = "BONFERRONI"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class MultiplicityGovernanceError(SearchProvenanceError):
    """Multiplicity material is invalid, incomplete or contradictory."""


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise SearchProvenanceValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "multiplicity semantic material").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MultiplicityRecord:
    """
    The DERIVED multiplicity family: how many hypotheses this search produced.

    Its identity is bound to the search record it was derived from, so a derived
    denominator can never be detached from the provenance that produced it and
    re-used to correct an unrelated validation.
    """

    search_identity: str
    family_identity: str
    multiplicity_rule: MultiplicityRule
    alternatives_considered: int
    multiplicity_eligible: int
    family_size: int
    correction_method: str = CORRECTION_METHOD_BONFERRONI
    base_alpha: float = 0.05
    note: str = ""                 # provenance only; NOT identity
    schema_version: int = MULTIPLICITY_SCHEMA_VERSION
    semantic_identity: str = ""
    multiplicity_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.multiplicity_rule, str) and not isinstance(
                self.multiplicity_rule, MultiplicityRule):
            object.__setattr__(
                self, "multiplicity_rule", MultiplicityRule(self.multiplicity_rule))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = f"{MULTIPLICITY_ID_PREFIX}{expected[:16].upper()}"
        if self.semantic_identity:
            if (not _HEX64_RE.match(self.semantic_identity)
                    or self.semantic_identity != expected):
                raise MultiplicityGovernanceError(
                    "presented multiplicity semantic identity does not match the "
                    "derived family material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.multiplicity_identity:
            if self.multiplicity_identity != expected_id:
                raise MultiplicityGovernanceError(
                    f"presented multiplicity identity {self.multiplicity_identity!r} "
                    "does not match the derived family material")
        else:
            object.__setattr__(self, "multiplicity_identity", expected_id)

    # -- Views --------------------------------------------------------------
    @property
    def corrected_alpha(self) -> float:
        """The Bonferroni-corrected significance threshold for this family."""
        return self.base_alpha / self.family_size

    @property
    def bonferroni_tests(self) -> int:
        """The integer the existing `ValidationSpec` API requires."""
        return self.family_size

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "multiplicity_record",
            "schema_version": self.schema_version,
            "search_identity": self.search_identity,
            "family_identity": self.family_identity,
            "multiplicity_rule": self.multiplicity_rule.value,
            "alternatives_considered": self.alternatives_considered,
            "multiplicity_eligible": self.multiplicity_eligible,
            "family_size": self.family_size,
            "correction_method": self.correction_method,
            "base_alpha": self.base_alpha,
        }

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "MultiplicityRecord":
        if self.schema_version != MULTIPLICITY_SCHEMA_VERSION:
            raise MultiplicityGovernanceError(
                f"multiplicity record schema_version must be "
                f"{MULTIPLICITY_SCHEMA_VERSION} (clean reset), got {self.schema_version!r}")
        if self.alternatives_considered < 1:
            raise MultiplicityGovernanceError(
                "a multiplicity record must cover at least one alternative")
        if self.multiplicity_eligible < 1:
            raise MultiplicityGovernanceError(
                "a multiplicity record must have at least one eligible alternative")
        if self.multiplicity_eligible > self.alternatives_considered:
            raise MultiplicityGovernanceError(
                f"{self.multiplicity_eligible} eligible alternatives cannot exceed "
                f"{self.alternatives_considered} considered alternatives")
        if self.family_size != self.multiplicity_eligible:
            raise MultiplicityGovernanceError(
                f"derived family size {self.family_size} contradicts the "
                f"{self.multiplicity_eligible} eligible alternatives it was derived from")
        if isinstance(self.base_alpha, bool) or not isinstance(self.base_alpha, float):
            raise MultiplicityGovernanceError(
                f"base_alpha must be a probability, got {self.base_alpha!r}")
        if not 0.0 < self.base_alpha < 1.0:
            raise MultiplicityGovernanceError(
                f"base_alpha must be a probability in (0, 1), got {self.base_alpha!r}")
        _encode(self.semantic_material(), "multiplicity record semantic material")
        return self

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "multiplicity_identity": self.multiplicity_identity,
            "semantic_identity": self.semantic_identity,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MultiplicityRecord":
        if not isinstance(data, Mapping):
            raise MultiplicityGovernanceError(
                "persisted multiplicity record must be a mapping")
        expected = {
            "kind", "schema_version", "search_identity", "family_identity",
            "multiplicity_rule", "alternatives_considered", "multiplicity_eligible",
            "family_size", "correction_method", "base_alpha", "multiplicity_identity",
            "semantic_identity", "note",
        }
        if expected - set(data) or set(data) - expected:
            raise MultiplicityGovernanceError(
                "persisted multiplicity record fields are not exact")
        if data["kind"] != "multiplicity_record":
            raise MultiplicityGovernanceError(
                f"persisted multiplicity kind must be 'multiplicity_record', got "
                f"{data['kind']!r}")
        return cls(
            search_identity=data["search_identity"],
            family_identity=data["family_identity"],
            multiplicity_rule=MultiplicityRule(data["multiplicity_rule"]),
            alternatives_considered=data["alternatives_considered"],
            multiplicity_eligible=data["multiplicity_eligible"],
            family_size=data["family_size"],
            correction_method=data["correction_method"],
            base_alpha=data["base_alpha"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            multiplicity_identity=data["multiplicity_identity"],
        )


# -- The authoritative derivation --------------------------------------------


def derive_multiplicity(
    record: SearchRecord,
    *,
    base_alpha: float = 0.05,
) -> MultiplicityRecord:
    """
    The single authoritative derivation of the multiplicity family size.

    There is no `bonferroni_tests=` parameter here on purpose: the caller does
    not get to state the denominator. It comes from the recorded alternatives and
    the family-declared rule, and nothing else.
    """
    if not isinstance(record, SearchRecord):
        raise MultiplicityGovernanceError(
            f"expected SearchRecord, got {type(record).__name__}")
    return MultiplicityRecord(
        search_identity=record.search_identity,
        family_identity=record.family_identity,
        multiplicity_rule=record.family.multiplicity_rule,
        alternatives_considered=record.alternatives_considered,
        multiplicity_eligible=len(record.multiplicity_eligible),
        family_size=record.derived_family_size,
        correction_method=CORRECTION_METHOD_BONFERRONI,
        base_alpha=float(base_alpha),
    )


def assert_consistent_with(
    multiplicity: MultiplicityRecord,
    record: SearchRecord,
) -> MultiplicityRecord:
    """A derived denominator may only be used against the record it came from."""
    if not isinstance(multiplicity, MultiplicityRecord):
        raise MultiplicityGovernanceError(
            f"expected MultiplicityRecord, got {type(multiplicity).__name__}")
    if not isinstance(record, SearchRecord):
        raise MultiplicityGovernanceError(
            f"expected SearchRecord, got {type(record).__name__}")
    if multiplicity.search_identity != record.search_identity:
        raise MultiplicityGovernanceError(
            "the multiplicity record was derived from a different search and may not "
            "be used to correct this one")
    if multiplicity.family_size != record.derived_family_size:
        raise MultiplicityGovernanceError(
            f"the multiplicity family size {multiplicity.family_size} contradicts the "
            f"{record.derived_family_size} derived from the recorded alternatives")
    return multiplicity


def governed_bonferroni_tests(
    record: SearchRecord,
    *,
    requested: int | None = None,
    base_alpha: float = 0.05,
) -> int:
    """
    The `bonferroni_tests` value a Wave 4 governed validation must use.

    `requested` exists ONLY so a contradictory hand-entered value can be detected
    and refused. It can never override the derived value: passing a different
    number raises rather than silently replacing the governed denominator.
    """
    multiplicity = derive_multiplicity(record, base_alpha=base_alpha)
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise MultiplicityGovernanceError(
                f"bonferroni_tests must be an integer, got {requested!r}")
        if requested != multiplicity.family_size:
            raise MultiplicityGovernanceError(
                f"requested bonferroni_tests={requested} contradicts the "
                f"{multiplicity.family_size} derived from the "
                f"{multiplicity.alternatives_considered} recorded alternatives in "
                f"{multiplicity.search_identity}; a governed search may not correct "
                "itself with a hand-entered denominator")
    return multiplicity.family_size


def apply_to_validation_spec(
    record: SearchRecord,
    validation,
    *,
    base_alpha: float = 0.05,
):
    """
    Write the derived family size into an EXISTING `ValidationSpec` instance.

    The repository's `ValidationSpec` and the conclusion logic that reads
    `bonferroni_tests` are NOT modified. This only refuses to let a
    Wave 4-governed validation carry a denominator that its own search provenance
    contradicts; a validation that is not governed by a Wave 4 search record is
    never passed here and is therefore left exactly as it was.
    """
    expected = governed_bonferroni_tests(record, base_alpha=base_alpha)
    current = getattr(validation, "bonferroni_tests", None)
    if current is None:
        raise MultiplicityGovernanceError(
            f"{type(validation).__name__} does not expose a bonferroni_tests field; "
            "the existing validation contract cannot consume the governed family size")
    if current != 1 and current != expected:
        raise MultiplicityGovernanceError(
            f"validation carries bonferroni_tests={current} but the governed search "
            f"derives {expected}; a manual denominator may never override recorded "
            "search provenance")
    validation.bonferroni_tests = expected
    return validation


def multiplicity_from_freeze(
    freeze: SelectionFreeze,
    *,
    base_alpha: float = 0.05,
) -> MultiplicityRecord:
    """
    Rebuild the derived multiplicity record from a frozen T0 selection.

    A frozen selection already binds the derived family size, so the count is
    reproduced from the freeze without re-opening the search.
    """
    if not isinstance(freeze, SelectionFreeze):
        raise MultiplicityGovernanceError(
            f"expected SelectionFreeze, got {type(freeze).__name__}")
    return MultiplicityRecord(
        search_identity=freeze.search_identity,
        family_identity=freeze.family_identity,
        multiplicity_rule=MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES,
        alternatives_considered=freeze.alternatives_considered,
        multiplicity_eligible=freeze.multiplicity_family_size,
        family_size=freeze.multiplicity_family_size,
        correction_method=freeze.correction_method,
        base_alpha=float(base_alpha),
    )


__all__ = [
    "CORRECTION_METHOD_BONFERRONI",
    "MULTIPLICITY_SCHEMA_VERSION",
    "MultiplicityGovernanceError",
    "MultiplicityRecord",
    "apply_to_validation_spec",
    "assert_consistent_with",
    "derive_multiplicity",
    "governed_bonferroni_tests",
    "multiplicity_from_freeze",
]
