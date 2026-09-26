"""
Treatment Equivalence v1 -- mechanical equivalence between treatments.

Stage 3 / Wave 7. `treatment_memory` can say that two treatments share an
identity. This module can say more, and -- just as importantly -- can say LESS,
honestly.

THE DISTINCTION THIS MODULE EXISTS TO ENFORCE
=============================================
Similarity is not equivalence.

    EXACT_SEMANTIC_EQUIVALENCE
        Every governed treatment semantic matches. This MAY deduplicate.

    RELATED_BUT_DISTINCT
        Some governed structure is shared and something material differs. This
        MUST NOT deduplicate. Two treatments that are related are still two
        different treatments, and merging them is how a real finding disappears.

    NOT_EQUIVALENT
        The governed semantics differ. Not the same treatment.

    INDETERMINATE
        The available governed information is insufficient to establish
        equivalence.

INDETERMINATE NEVER COLLAPSES TO EQUIVALENT
===========================================
This is fail-closed and structural rather than a convention: `_decide` returns
EQUIVALENT only from a branch reached when every governed field is populated on
both sides and equal. Any absent or unspecified field routes to INDETERMINATE,
which no Wave 7 caller ever treats as equivalence.

WHY THERE IS NO SIMILARITY SCORE
================================
No embedding distance, no LLM judgement, no fuzzy string match, no token overlap
ratio and no arbitrary threshold appears anywhere in this module. Equivalence
must be mechanically reconstructable from governed fields, because a
reconstruction that needs a model is a reconstruction nobody can audit, and an
unauditable equivalence decision is indistinguishable from a guess.

A field is compared only if it is POPULATED on both sides. An unpopulated field
is not equal by default; it is missing evidence, and the assessment becomes
INDETERMINATE rather than silently permissive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from research_engine.lifecycle.treatment_memory import (
    TreatmentMemoryValidationError,
    TreatmentSignature,
)


class EquivalenceRelation(str, Enum):
    """
    The CLOSED vocabulary of equivalence between two treatments.

    A closed enum, so no caller can invent a fifth relation that quietly means
    close enough. No member's value suggests a fuzzy judgement.
    """

    EXACT_SEMANTIC_EQUIVALENCE = "EXACT_SEMANTIC_EQUIVALENCE"
    RELATED_BUT_DISTINCT = "RELATED_BUT_DISTINCT"
    NOT_EQUIVALENT = "NOT_EQUIVALENT"
    INDETERMINATE = "INDETERMINATE"

    @property
    def deduplicates(self) -> bool:
        """
        Whether this relation may mechanically collapse two treatments into one.

        True for EXACT_SEMANTIC_EQUIVALENCE only. Exposed as a governed property
        so every deduplication site consults one definition rather than
        re-deciding what counts as the same idea.
        """
        return self is EquivalenceRelation.EXACT_SEMANTIC_EQUIVALENCE


#: The governed treatment-semantic fields equivalence is computed over. This is
#: the whole basis of an equivalence claim: a field not listed here cannot make
#: two treatments equivalent or non-equivalent.
EQUIVALENCE_FIELDS: Tuple[str, ...] = (
    "subject_kind",
    "subject_ref",
    "component",
    "change_parameters",
    "direction",
    "baseline_semantics",
    "alternative_semantics",
    "treatment_class",
    "horizon",
    "dimension_identity",
    "interaction_identity",
    "slice_identity",
    "population_reference",
    "asset_family",
    "symbols",
)

#: Fields that describe a LINK between treatments rather than the treatment
#: itself. Two treatments raised from different proposals are still two
#: treatments; lineage is reported, never used to split equivalence.
LINEAGE_FIELDS: Tuple[str, ...] = (
    "parent_proposal_identity",
    "parent_protocol_identity",
)


class EquivalenceReason(str, Enum):
    """The CLOSED vocabulary of governed reasons for an equivalence verdict."""

    ALL_GOVERNED_FIELDS_EQUAL = "ALL_GOVERNED_FIELDS_EQUAL"
    SIGNATURE_IDENTITY_EQUAL = "SIGNATURE_IDENTITY_EQUAL"
    GOVERNED_FIELDS_DIFFER = "GOVERNED_FIELDS_DIFFER"
    GOVERNED_FIELD_UNPOPULATED = "GOVERNED_FIELD_UNPOPULATED"
    NO_GOVERNED_FIELDS_SHARED = "NO_GOVERNED_FIELDS_SHARED"
    PARTIAL_GOVERNED_STRUCTURE_SHARED = "PARTIAL_GOVERNED_STRUCTURE_SHARED"


@dataclass(frozen=True)
class EquivalenceAssessment:
    """
    A deterministic, fully inspectable equivalence verdict.

    Exposes WHICH governed fields matched and which did not. There is no score
    and no confidence value, because a score would invite a threshold, and a
    threshold would be exactly the fuzzy judgement this module refuses to make.
    """

    relation: EquivalenceRelation
    matched_fields: Tuple[str, ...]
    differing_fields: Tuple[str, ...]
    unpopulated_fields: Tuple[str, ...]
    reasons: Tuple[EquivalenceReason, ...]

    @property
    def deduplicates(self) -> bool:
        """Whether this assessment may collapse two treatments into one."""
        return self.relation.deduplicates

    def assert_not_equivalent(self) -> "EquivalenceAssessment":
        """
        Raise unless the two treatments are exactly equivalent.

        For a caller about to suppress work as a duplicate: if the relation is
        anything other than exact equivalence, including INDETERMINATE, this
        raises. Suppression is the dangerous direction, so it is the direction
        that must be proven rather than assumed.
        """
        if not self.deduplicates:
            raise TreatmentMemoryValidationError(
                f"treatments are {self.relation.value}, not "
                f"EXACT_SEMANTIC_EQUIVALENCE; duplicate suppression is not "
                f"permitted (differing fields: {list(self.differing_fields)}, "
                f"unpopulated fields: {list(self.unpopulated_fields)})")
        return self

    def to_dict(self) -> dict:
        return {
            "relation": self.relation.value,
            "matched_fields": list(self.matched_fields),
            "differing_fields": list(self.differing_fields),
            "unpopulated_fields": list(self.unpopulated_fields),
            "reasons": [reason.value for reason in self.reasons],
        }



def _is_populated(value: object) -> bool:
    """
    Whether a governed field carries actual information.

    Empty strings, empty collections and the UNSPECIFIED horizon carry no
    information, so they are treated as unpopulated rather than as a value that
    happens to compare equal. That distinction is the whole reason
    INDETERMINATE exists: an unknown scope is not a known-scope.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value) and value != "UNSPECIFIED"
    if isinstance(value, (tuple, list, set, frozenset, dict)):
        return bool(value)
    return True


def _comparable(left: TreatmentSignature, right: TreatmentSignature,
                name: str) -> object:
    """Read a governed field from a signature by its governed name."""
    return getattr(left, name, None) if left is not None else getattr(
        right, name, None)


def _field_values(signature: TreatmentSignature, name: str) -> object:
    if name == "subject_kind":
        return signature.subject_kind.value
    if name == "component":
        return signature.component.value
    if name == "direction":
        return signature.direction.value
    if name == "treatment_class":
        return signature.treatment_class.value
    if name == "horizon":
        return signature.horizon.value
    if name == "change_parameters":
        return tuple(signature.change_parameters)
    if name == "symbols":
        return tuple(signature.symbols)
    return getattr(signature, name, None)


def _decide(matched: Tuple[str, ...], differing: Tuple[str, ...],
            unpopulated: Tuple[str, ...],
            identity_equal: bool) -> EquivalenceAssessment:
    """
    The single place a relation is chosen. Every route is explicit.

    The ordering matters and encodes the fail-closed priority:

      1. identical signature identity is conclusive equivalence;
      2. any ASYMMETRICALLY populated governed field is INDETERMINATE, never
         equivalence;
      3. any differing field with nothing else shared is NOT_EQUIVALENT;
      4. any differing field alongside shared structure is RELATED_BUT_DISTINCT;
      5. everything equal is EXACT_SEMANTIC_EQUIVALENCE.

    Step 2 sits ABOVE step 5 on purpose. A treatment scoped to a population and
    a treatment that never declared one must not compare equal, because
    collapsing them would silently widen a bounded finding into an unbounded
    one -- amnesia in the disguise of deduplication.
    """
    if identity_equal:
        return EquivalenceAssessment(
            EquivalenceRelation.EXACT_SEMANTIC_EQUIVALENCE,
            matched, differing, unpopulated,
            (EquivalenceReason.SIGNATURE_IDENTITY_EQUAL,
             EquivalenceReason.ALL_GOVERNED_FIELDS_EQUAL))
    if unpopulated:
        return EquivalenceAssessment(
            EquivalenceRelation.INDETERMINATE, matched, differing, unpopulated,
            (EquivalenceReason.GOVERNED_FIELD_UNPOPULATED,))
    if differing:
        if matched:
            return EquivalenceAssessment(
                EquivalenceRelation.RELATED_BUT_DISTINCT, matched, differing,
                unpopulated,
                (EquivalenceReason.GOVERNED_FIELDS_DIFFER,
                 EquivalenceReason.PARTIAL_GOVERNED_STRUCTURE_SHARED))
        return EquivalenceAssessment(
            EquivalenceRelation.NOT_EQUIVALENT, matched, differing, unpopulated,
            (EquivalenceReason.GOVERNED_FIELDS_DIFFER,
             EquivalenceReason.NO_GOVERNED_FIELDS_SHARED))
    return EquivalenceAssessment(
        EquivalenceRelation.EXACT_SEMANTIC_EQUIVALENCE, matched, differing,
        unpopulated, (EquivalenceReason.ALL_GOVERNED_FIELDS_EQUAL,))



def assess_treatment_equivalence(
        left: TreatmentSignature,
        right: TreatmentSignature) -> EquivalenceAssessment:
    """
    The mechanical equivalence verdict between two governed treatments.

    Compares every field in `EQUIVALENCE_FIELDS`. A field is:

      - MATCHED when both sides carry the same value, INCLUDING when both sides
        are empty (two treatments that both decline to scope themselves agree);
      - DIFFERING when both sides are populated and the values differ;
      - UNPOPULATED when exactly one side is populated. That asymmetry is the
        genuine insufficient-information case, and it is the ONLY thing that
        produces INDETERMINATE -- an unpopulated field can never be silently
        read as a matching one, and never as a differing one either.
    """
    for value, label in ((left, "left"), (right, "right")):
        if not isinstance(value, TreatmentSignature):
            raise TreatmentMemoryValidationError(
                f"assess_treatment_equivalence {label} argument must be a "
                f"governed TreatmentSignature, got {type(value).__name__}")

    matched = []
    differing = []
    unpopulated = []
    for name in EQUIVALENCE_FIELDS:
        left_value = _field_values(left, name)
        right_value = _field_values(right, name)
        left_populated = _is_populated(left_value)
        right_populated = _is_populated(right_value)
        if left_populated != right_populated:
            unpopulated.append(name)
        elif left_value == right_value:
            matched.append(name)
        else:
            differing.append(name)

    return _decide(tuple(matched), tuple(differing), tuple(unpopulated),
                   left.semantic_identity == right.semantic_identity)


def shared_lineage(
        left: TreatmentSignature,
        right: TreatmentSignature) -> Tuple[str, ...]:
    """
    The lineage fields two treatments share.

    Reported, never used to establish equivalence. Two treatments raised from
    different proposals are still comparable treatments; lineage is provenance
    about how they were raised, not part of what they are.
    """
    for value, label in ((left, "left"), (right, "right")):
        if not isinstance(value, TreatmentSignature):
            raise TreatmentMemoryValidationError(
                f"shared_lineage {label} argument must be a governed "
                f"TreatmentSignature, got {type(value).__name__}")
    return tuple(name for name in LINEAGE_FIELDS
                 if getattr(left, name) and getattr(left, name) == getattr(
                     right, name))


def duplicate_candidates(
        candidate: TreatmentSignature,
        known: Tuple[TreatmentSignature, ...]) -> Tuple[TreatmentSignature, ...]:
    """
    Which known treatments are EXACTLY equivalent to `candidate`.

    The deduplication filter, and the ONLY place in Wave 7 where two treatments
    are collapsed. Anything that is merely related is returned as not a
    duplicate, and an INDETERMINATE comparison is likewise not a duplicate:
    uncertainty is never grounds for suppression.
    """
    matches = []
    for other in known:
        if not isinstance(other, TreatmentSignature):
            raise TreatmentMemoryValidationError(
                f"duplicate_candidates expects TreatmentSignature objects, got "
                f"{type(other).__name__}")
        if assess_treatment_equivalence(candidate, other).deduplicates:
            matches.append(other)
    return tuple(matches)


__all__ = [
    "EQUIVALENCE_FIELDS",
    "LINEAGE_FIELDS",
    "EquivalenceAssessment",
    "EquivalenceReason",
    "EquivalenceRelation",
    "assess_treatment_equivalence",
    "duplicate_candidates",
    "shared_lineage",
]

