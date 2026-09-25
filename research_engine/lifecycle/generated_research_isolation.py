"""
Frozen-70 Isolation Guard — mechanical proof that generated research cannot
contaminate the canonical research programme.

Stage ③ / Wave 0 foundation. The canonical 70-question registry is FROZEN
authority. Generated research is a SEPARATE governed namespace. This module
is the explicit, mechanical enforcement of that separation.

What it guarantees (each function raises on violation):

    1. The canonical registry still contains exactly 70 questions.
    2. Canonical IDs are unique.
    3. Canonical IDs still exactly equal `BASELINE_QUESTION_IDS`.
    4. Every canonical definition version is still 1.
    5. No generated research ID equals, or can be confused with, a canonical ID.
    6. Generated research is never returned by the canonical registry APIs.
    7. Canonical inventory/definition authority is otherwise unchanged.

READ-ONLY BY CONSTRUCTION
-------------------------
This module only imports and reads canonical authority. It never appends to
`REGISTRY`, never mutates `BASELINE_QUESTION_IDS`, never rebuilds or weakens
canonical definitions, and never weakens existing inventory guards. It adds no
Q71 and never makes generated research appear to be part of the canonical 70.
"""

from __future__ import annotations

from typing import Iterable

from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    GeneratedResearchNamespaceViolation,
    is_generated_research_id,
)
from research_engine.registry.baseline_manifest import (
    BASELINE_QUESTION_IDS,
    BASELINE_VERSION,
)
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.research_question_models import (
    QuestionCategory,
    QuestionPriority,
)
from research_engine.registry.research_question_registry import (
    REGISTRY,
    REGISTRY_BY_ID,
    get_question,
    get_questions_by_category,
    get_questions_by_priority,
)

#: The canonical programme is exactly this many questions. Frozen authority.
CANONICAL_QUESTION_COUNT = 70

#: The canonical definition version. Frozen authority (clean reset: always 1).
CANONICAL_DEFINITION_VERSION = 1

#: Canonical IDs as a frozen lookup set (built from the frozen V1 record).
CANONICAL_QUESTION_IDS: frozenset[str] = frozenset(BASELINE_QUESTION_IDS)

#: Case-folded canonical IDs, so a case-variant can never pass as generated.
_CANONICAL_IDS_CASEFOLDED: frozenset[str] = frozenset(
    question_id.casefold() for question_id in BASELINE_QUESTION_IDS
)


def canonical_inventory() -> tuple[str, ...]:
    """The live canonical IDs in registry order (the frozen authority order)."""
    return tuple(question.id for question in REGISTRY)


def assert_generated_research_id_isolated(generated_research_id: str) -> None:
    """
    Reject any ID that is not unambiguously inside the generated namespace.

    Raises `GeneratedResearchNamespaceViolation` when the presented ID:
      - is not a well-formed `GEN-<16 hex>` identity;
      - is a canonical question ID (case-insensitively);
      - does not carry the reserved prefix.
    """
    if not isinstance(generated_research_id, str) or not generated_research_id:
        raise GeneratedResearchNamespaceViolation(
            "generated research ID must be a non-empty string")
    if not is_generated_research_id(generated_research_id):
        raise GeneratedResearchNamespaceViolation(
            f"not a generated research ID (expected {GENERATED_RESEARCH_ID_PREFIX}"
            f"<16 hex>): {generated_research_id!r}")
    if generated_research_id in CANONICAL_QUESTION_IDS:
        raise GeneratedResearchNamespaceViolation(
            "generated research ID collides with a canonical question ID: "
            f"{generated_research_id!r}")
    if generated_research_id.casefold() in _CANONICAL_IDS_CASEFOLDED:
        raise GeneratedResearchNamespaceViolation(
            "generated research ID collides case-insensitively with a canonical ID: "
            f"{generated_research_id!r}")
    if not generated_research_id.startswith(GENERATED_RESEARCH_ID_PREFIX):
        raise GeneratedResearchNamespaceViolation(
            f"generated research ID escapes the reserved prefix: {generated_research_id!r}")


def assert_no_generated_canonical_collision(generated_ids: Iterable[str]) -> None:
    """Assert every generated ID is isolated from the frozen canonical 70."""
    for generated_research_id in generated_ids:
        assert_generated_research_id_isolated(generated_research_id)


def assert_generated_not_in_canonical_apis(generated_ids: Iterable[str]) -> None:
    """
    Assert no generated identity is observable through the canonical APIs.

    `get_question`, `REGISTRY_BY_ID` and the category/priority projections must
    not surface generated research, and no generated ID may be readable as a
    canonical question.
    """
    for generated_research_id in generated_ids:
        assert_generated_research_id_isolated(generated_research_id)
        if generated_research_id in REGISTRY_BY_ID or get_question(generated_research_id):
            raise GeneratedResearchNamespaceViolation(
                f"generated research {generated_research_id!r} is visible through the "
                "canonical registry APIs")
        if generated_research_id.casefold() in _CANONICAL_IDS_CASEFOLDED:
            raise GeneratedResearchNamespaceViolation(
                f"generated research {generated_research_id!r} collides with a "
                "canonical question ID")


def all_canonical_questions() -> list:
    """Every canonical question reachable through the live canonical APIs."""
    collected: dict[str, object] = {question.id: question for question in REGISTRY}
    for category in QuestionCategory:
        for question in get_questions_by_category(category):
            collected[question.id] = question
    for priority in QuestionPriority:
        for question in get_questions_by_priority(priority):
            collected[question.id] = question
    return list(collected.values())


def canonical_identity_snapshot() -> dict[str, object]:
    """
    Capture the canonical authority state for before/after comparison.

    Any mutation of the frozen 70 — count, IDs, definition content or
    definition versions — changes this snapshot.
    """
    definitions = build_definitions_from_registry(REGISTRY)
    return {
        "baseline_version": BASELINE_VERSION,
        "count": len(REGISTRY),
        "ids": canonical_inventory(),
        "definition_versions": {
            question_id: definition.definition_version
            for question_id, definition in sorted(definitions.items())
        },
        "definitions": {
            question_id: definition.to_dict()
            for question_id, definition in sorted(definitions.items())
        },
    }


def assert_canonical_70_intact() -> None:
    """
    The Frozen-70 guard. Raises on any deviation from canonical authority.

    Checks, in order: exact cardinality, ID uniqueness, exact ID equality with
    `BASELINE_QUESTION_IDS`, and definition version 1 for every canonical
    definition. Canonical inventory authority is consulted read-only.
    """
    from research_engine.registry.inventory_guard import (
        QuestionInventoryViolation,
        canonical_questions,
    )

    inventory = canonical_inventory()

    if len(inventory) != CANONICAL_QUESTION_COUNT:
        raise QuestionInventoryViolation(
            f"canonical registry must contain exactly {CANONICAL_QUESTION_COUNT} "
            f"questions, found {len(inventory)}")
    if len(set(inventory)) != len(inventory):
        duplicates = sorted({qid for qid in inventory if inventory.count(qid) > 1})
        raise QuestionInventoryViolation(
            f"duplicate canonical question IDs: {duplicates}")
    if inventory != tuple(BASELINE_QUESTION_IDS):
        raise QuestionInventoryViolation(
            "canonical registry IDs no longer exactly match BASELINE_QUESTION_IDS")
    if BASELINE_VERSION != 1:
        raise QuestionInventoryViolation(
            f"canonical baseline version must remain 1, found {BASELINE_VERSION}")
    if tuple(sorted(canonical_questions())) != tuple(sorted(inventory)):
        raise QuestionInventoryViolation(
            "canonical inventory guard disagrees with the live canonical registry")

    definitions = build_definitions_from_registry(REGISTRY)
    if set(definitions) != set(inventory):
        raise QuestionInventoryViolation(
            "canonical definitions do not cover exactly the canonical question IDs")
    bad_versions = sorted(
        question_id for question_id, definition in definitions.items()
        if definition.definition_version != CANONICAL_DEFINITION_VERSION
    )
    if bad_versions:
        raise QuestionInventoryViolation(
            f"canonical definition versions must all remain {CANONICAL_DEFINITION_VERSION}: "
            f"{bad_versions}")


def assert_generated_research_isolated(generated_ids: Iterable[str]) -> None:
    """
    The full Frozen-70 isolation guard for a set of generated identities.

    Proves both halves: generated research cannot contaminate the canonical
    programme, and the canonical programme is unchanged while it exists.
    """
    ids = [generated_research_id for generated_research_id in generated_ids]
    assert_canonical_70_intact()
    assert_no_generated_canonical_collision(ids)
    assert_generated_not_in_canonical_apis(ids)


__all__ = [
    "CANONICAL_DEFINITION_VERSION",
    "CANONICAL_QUESTION_COUNT",
    "CANONICAL_QUESTION_IDS",
    "all_canonical_questions",
    "assert_canonical_70_intact",
    "assert_generated_not_in_canonical_apis",
    "assert_generated_research_id_isolated",
    "assert_generated_research_isolated",
    "assert_no_generated_canonical_collision",
    "canonical_identity_snapshot",
    "canonical_inventory",
]
