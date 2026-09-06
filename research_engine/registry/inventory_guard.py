"""
Question Inventory Guard — single-source-of-truth enforcement (Gap 9).

Proves, structurally, that the V1 Research Engine has exactly ONE active
question inventory:

    research_engine.registry.research_question_registry  (REGISTRY)

and that the legacy NEW-ENGINE question bank
(research_engine.v10.universes.legacy_question_bank) is a read-only
research-coverage audit input that:

    - defines no runners and cannot inject into runner discovery;
    - only aliases into canonical registry IDs via source_intent
      (except the four documented UNMIGRATED intents);
    - contains no duplicate IDs.

These functions raise `QuestionInventoryViolation` on any violation so the
regression tests (and any future audit tooling) fail loudly instead of
silently allowing a second canonical inventory to grow.
"""

from __future__ import annotations

from typing import Any

from research_engine.registry.research_question_registry import REGISTRY

# The four documented UNMIGRATED research intents (conceptually valid, no
# canonical runner). Anything else in the legacy bank MUST alias into the
# canonical registry — this list may only grow through an explicit decision.
DOCUMENTED_UNMIGRATED_INTENTS = frozenset({
    "D-005",   # Opportunity Quality Predictive Value
    "D-006",   # Opportunity Failure Characterisation
    "SD-001",  # Shadow Counterfactual Expectancy
    "SD-007",  # Shadow Regime Expectancy
})


class QuestionInventoryViolation(AssertionError):
    """The question inventory violates the single-source-of-truth contract."""


def canonical_questions() -> dict[str, Any]:
    """The canonical inventory keyed by question ID (dupes => violation)."""
    out: dict[str, Any] = {}
    for question in REGISTRY:
        if question.id in out:
            raise QuestionInventoryViolation(
                f"duplicate canonical question id: {question.id}")
        out[question.id] = question
    return out


def executable_canonical_questions() -> dict[str, Any]:
    """Canonical questions that declare a runner."""
    return {qid: q for qid, q in canonical_questions().items()
            if getattr(q, "runner_module", "") and getattr(q, "runner_function", "")}


def assert_runners_match_registry(discovered_runners: dict[str, Any]) -> None:
    """Runner discovery and the canonical registry must agree exactly."""
    canonical = executable_canonical_questions()
    unregistered = sorted(set(discovered_runners) - set(canonical))
    missing = sorted(set(canonical) - set(discovered_runners))
    if unregistered:
        raise QuestionInventoryViolation(
            f"executable runners without a canonical question: {unregistered}")
    if missing:
        raise QuestionInventoryViolation(
            f"canonical executable questions without a runner: {missing}")


def assert_legacy_bank_is_read_only_coverage(bank_questions: list[Any],
                                             retired_questions: list[Any] = ()) -> None:
    """
    The legacy NEW-ENGINE bank must remain a read-only coverage input:

    - no runner fields on its question model (cannot execute);
    - unique question IDs;
    - every non-retired, non-unmigrated question aliases into the canonical
      registry via source_intent.
    """
    model = type(bank_questions[0]) if bank_questions else None
    if model is not None and any(
            hasattr(model, f) for f in ("runner_module", "runner_function", "runner")):
        raise QuestionInventoryViolation(
            "legacy bank question model gained runner fields - it must stay "
            "a non-executable coverage input")

    ids: set[str] = set()
    for question in bank_questions:
        qid = question.question_id
        if qid in ids:
            raise QuestionInventoryViolation(f"legacy bank duplicate id: {qid}")
        ids.add(qid)

    canonical = canonical_questions()
    unaliased: list[str] = []
    for question in bank_questions:
        if question.question_id in DOCUMENTED_UNMIGRATED_INTENTS:
            continue
        aliases = set(getattr(question, "source_intent", ()) or ())
        if not (aliases & set(canonical)):
            unaliased.append(question.question_id)
    if unaliased:
        raise QuestionInventoryViolation(
            f"legacy bank questions with no canonical alias and no documented "
            f"unmigrated status: {sorted(unaliased)}")

    for question in retired_questions:
        if question.question_id in canonical:
            raise QuestionInventoryViolation(
                f"retired question {question.question_id} collides with the "
                f"canonical registry")
