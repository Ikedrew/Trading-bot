"""Governed lifecycle vocabulary for generated (Q71+) research questions.

Repair Block 2 makes generated questions full participants in the autonomous
research loop.  That requires four *mechanically separate* axes that Audit 1
found collapsed together:

``generation_status``
    Did the governed question identity get minted and admitted to the agenda?
    (PROPOSED / READY / WAITING_FOR_DATA / MISSING_EVALUATOR / ACTIVE /
    RETIRED / INVALID - the vocabulary already persisted by
    ``q71_orchestration``.)

``execution_status``
    What did governed execution do with the question?  (GENERATED / QUEUED /
    RUNNING / COMPLETE / NEGATIVE_RESULT / WAITING_FOR_DATA /
    MISSING_EVALUATOR / IMPLEMENTATION_BLOCKED / BLOCKED / INSUFFICIENT_DATA /
    SUPERSEDED / RETIRED / INVALID.)

``scientific_status``
    What did the evaluator say, scientifically?  Derived only from the
    evaluator's own governed declaration, never from execution success.

``execution_freshness``
    Is the persisted result still current for the governed evidence epoch and
    the evaluator identity that produced it?

This module derives no thresholds, invents no reasons and performs no I/O.  It
is the single shared derivation used by the worker, the projection and the Lab
so that no consumer has to guess what a status means.
"""
from __future__ import annotations

from typing import Any


LIFECYCLE_SCHEMA = "generated_question_lifecycle_v1"

# -- Execution lifecycle vocabulary (closed) ---------------------------------
GENERATED = "GENERATED"
QUEUED = "QUEUED"
RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
NEGATIVE_RESULT = "NEGATIVE_RESULT"
WAITING_FOR_DATA = "WAITING_FOR_DATA"
MISSING_EVALUATOR = "MISSING_EVALUATOR"
IMPLEMENTATION_BLOCKED = "IMPLEMENTATION_BLOCKED"
BLOCKED = "BLOCKED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
SUPERSEDED = "SUPERSEDED"
RETIRED = "RETIRED"
INVALID = "INVALID"

LIFECYCLE_STATES = frozenset({
    GENERATED, QUEUED, RUNNING, COMPLETE, NEGATIVE_RESULT, WAITING_FOR_DATA,
    MISSING_EVALUATOR, IMPLEMENTATION_BLOCKED, BLOCKED, INSUFFICIENT_DATA,
    SUPERSEDED, RETIRED, INVALID,
})

#: No further work is attempted unless a governed authority explicitly
#: re-authorises the question (see ``reentry_authorized``).
TERMINAL_STATES = frozenset({SUPERSEDED, RETIRED, INVALID})

#: May be claimed by the generated-question worker.
CLAIMABLE_STATES = frozenset({QUEUED})

#: States that may legitimately re-enter when relevant evidence or evaluator
#: capability changes.  MISSING_EVALUATOR is deliberately *not* collapsed into
#: WAITING_FOR_DATA: they have different re-entry triggers.
REENTRY_STATES = frozenset({
    WAITING_FOR_DATA, MISSING_EVALUATOR, INSUFFICIENT_DATA, BLOCKED,
    IMPLEMENTATION_BLOCKED, INVALID,
})

#: Execution produced a scientific resolution (the evaluator's own semantics).
SCIENTIFICALLY_RESOLVED_STATES = frozenset({COMPLETE, NEGATIVE_RESULT})

#: Execution states that must never produce a scientific finding.
NON_SCIENTIFIC_STATES = frozenset({
    GENERATED, QUEUED, RUNNING, WAITING_FOR_DATA, MISSING_EVALUATOR,
    IMPLEMENTATION_BLOCKED, BLOCKED, INSUFFICIENT_DATA, SUPERSEDED, RETIRED,
    INVALID,
})

# -- Generation-status compatibility vocabulary ------------------------------
# ``q71_orchestration`` persists these names.  They are preserved verbatim on
# disk so historical state keeps loading; the mapping below is the one explicit
# translation into the lifecycle vocabulary.
GENERATION_PROPOSED = "PROPOSED"
GENERATION_READY = "READY"
GENERATION_ACTIVE = "ACTIVE"

_GENERATION_TO_LIFECYCLE = {
    GENERATION_PROPOSED: GENERATED,
    GENERATION_READY: QUEUED,
    # ACTIVE means governed research is already running elsewhere for this cell;
    # it is an execution-eligibility statement, not a scientific one.
    GENERATION_ACTIVE: QUEUED,
    WAITING_FOR_DATA: WAITING_FOR_DATA,
    MISSING_EVALUATOR: MISSING_EVALUATOR,
    RETIRED: RETIRED,
    INVALID: INVALID,
}


# -- Scientific-status vocabulary (closed) -----------------------------------
SCIENTIFICALLY_MEANINGFUL = "SCIENTIFICALLY_MEANINGFUL"
NEGATIVE_SCIENTIFIC_RESULT = "NEGATIVE_SCIENTIFIC_RESULT"
DECLARED_NOT_MEANINGFUL = "DECLARED_NOT_MEANINGFUL"
NOT_SCIENTIFICALLY_RESOLVED = "NOT_SCIENTIFICALLY_RESOLVED"

SCIENTIFIC_STATUS_VALUES = frozenset({
    SCIENTIFICALLY_MEANINGFUL, NEGATIVE_SCIENTIFIC_RESULT,
    DECLARED_NOT_MEANINGFUL, NOT_SCIENTIFICALLY_RESOLVED,
})

# -- Execution-freshness vocabulary (closed, independent of status) ----------
NEVER_EXECUTED = "NEVER_EXECUTED"
EXECUTING = "EXECUTING"
CURRENT = "CURRENT"
STALE_EVIDENCE = "STALE_EVIDENCE"
STALE_EVALUATOR = "STALE_EVALUATOR"
NOT_APPLICABLE = "NOT_APPLICABLE"

FRESHNESS_VALUES = frozenset({
    NEVER_EXECUTED, EXECUTING, CURRENT, STALE_EVIDENCE, STALE_EVALUATOR,
    NOT_APPLICABLE,
})

# -- Machine-readable re-entry reasons (closed) ------------------------------
REENTRY_NONE = "NO_REENTRY_REQUIRED"
REENTRY_EVIDENCE_CHANGED = "RELEVANT_EVIDENCE_CHANGED"
REENTRY_EVIDENCE_ARRIVED = "MISSING_EVIDENCE_ARRIVED"
REENTRY_EVALUATOR_REGISTERED = "EVALUATOR_CAPABILITY_APPEARED"
REENTRY_EVALUATOR_CHANGED = "EVALUATOR_IDENTITY_CHANGED"
REENTRY_IMPLEMENTATION_FIXED = "IMPLEMENTATION_STATE_CHANGED"
REENTRY_REAUTHORISED = "EXPLICITLY_REAUTHORISED"
REENTRY_BLOCKED_TERMINAL = "TERMINAL_STATE_NOT_REAUTHORISED"

REENTRY_REASONS = frozenset({
    REENTRY_NONE, REENTRY_EVIDENCE_CHANGED, REENTRY_EVIDENCE_ARRIVED,
    REENTRY_EVALUATOR_REGISTERED, REENTRY_EVALUATOR_CHANGED,
    REENTRY_IMPLEMENTATION_FIXED, REENTRY_REAUTHORISED,
    REENTRY_BLOCKED_TERMINAL,
})

# -- Supersession / retirement reasons (closed) ------------------------------
SUPERSEDED_BY_NEWER_GENERATION = "SUPERSEDED_BY_NEWER_GENERATION"
SUPERSEDED_BY_EQUIVALENT_CANONICAL_QUESTION = (
    "SUPERSEDED_BY_EQUIVALENT_CANONICAL_QUESTION")
SUPERSEDED_BY_EQUIVALENT_GENERATED_QUESTION = (
    "SUPERSEDED_BY_EQUIVALENT_GENERATED_QUESTION")
RETIRED_BLIND_SPOT_RESOLVED = "RESEARCH_GAP_RESOLVED"
RETIRED_CELL_REMOVED = "OBSERVATION_CELL_REMOVED"
RETIRED_CURIOSITY_NOT_ADMISSIBLE = "CURIOSITY_NO_LONGER_ADMISSIBLE"

SUPERSESSION_REASONS = frozenset({
    SUPERSEDED_BY_NEWER_GENERATION,
    SUPERSEDED_BY_EQUIVALENT_CANONICAL_QUESTION,
    SUPERSEDED_BY_EQUIVALENT_GENERATED_QUESTION,
    RETIRED_BLIND_SPOT_RESOLVED,
    RETIRED_CELL_REMOVED,
    RETIRED_CURIOSITY_NOT_ADMISSIBLE,
})


def generation_to_lifecycle(value: Any) -> str:
    """Translate a persisted generation status into the lifecycle vocabulary.

    Unknown generation names fail closed to INVALID rather than being silently
    treated as runnable.
    """
    raw = str(value or "").strip().upper()
    if raw in LIFECYCLE_STATES:
        return raw
    return _GENERATION_TO_LIFECYCLE.get(raw, INVALID)


def execution_status_from_report_status(value: Any) -> str:
    """Map an evaluator-declared report status into the execution vocabulary.

    The worker never guesses: an unrecognised or absent report status is INVALID
    (fail closed), and only the evaluator's own names are accepted.
    """
    raw = str(value or "").strip().upper()
    aliases = {
        "WAITING_DATA": WAITING_FOR_DATA,
        "WAITING": WAITING_FOR_DATA,
        "ERROR": INVALID,
        "MALFORMED_REPORT": INVALID,
        "NO_EFFECT": NEGATIVE_RESULT,
    }
    status = aliases.get(raw, raw)
    return status if status in LIFECYCLE_STATES else INVALID


def scientific_status(
    execution_status: str, scientifically_meaningful: Any = None,
) -> str:
    """Derive the scientific status from the evaluator's own declaration.

    A structurally COMPLETE execution is *not* a scientific result.  Only an
    explicit evaluator declaration can raise it to a scientific resolution, and
    a negative result stays a legitimate scientific resolution.
    """
    status = str(execution_status or "").upper()
    if status not in SCIENTIFICALLY_RESOLVED_STATES:
        return NOT_SCIENTIFICALLY_RESOLVED
    if scientifically_meaningful is True:
        return (NEGATIVE_SCIENTIFIC_RESULT
                if status == NEGATIVE_RESULT else SCIENTIFICALLY_MEANINGFUL)
    if scientifically_meaningful is False:
        return DECLARED_NOT_MEANINGFUL
    return NOT_SCIENTIFICALLY_RESOLVED


def execution_freshness(
    *, execution_status: Any, result_evidence_epoch: Any,
    current_evidence_epoch: Any, result_evaluator_digest: Any,
    current_evaluator_digest: Any,
) -> str:
    """Freshness is evidence/evaluator currency, never scientific meaning."""
    status = str(execution_status or "").upper()
    if status == RUNNING:
        return EXECUTING
    if not status or status in {GENERATED, QUEUED, WAITING_FOR_DATA,
                                MISSING_EVALUATOR}:
        return NEVER_EXECUTED
    if status in TERMINAL_STATES:
        return NOT_APPLICABLE
    if not result_evidence_epoch:
        return NEVER_EXECUTED
    current_digest = str(current_evaluator_digest or "")
    if (current_digest and result_evaluator_digest
            and current_digest != str(result_evaluator_digest)):
        return STALE_EVALUATOR
    if str(result_evidence_epoch) != str(current_evidence_epoch or ""):
        return STALE_EVIDENCE
    return CURRENT


def reentry_reason(
    *, previous_status: Any, next_status: Any, evaluator_was_missing: bool,
    evidence_arrived: bool, evaluator_changed: bool, reauthorised: bool,
) -> str:
    """Return the machine-readable reason a generated question re-entered."""
    previous = str(previous_status or "").upper()
    following = str(next_status or "").upper()
    if following in TERMINAL_STATES and not reauthorised:
        return REENTRY_BLOCKED_TERMINAL
    if previous == MISSING_EVALUATOR and not evaluator_was_missing:
        return REENTRY_EVALUATOR_REGISTERED
    if evidence_arrived:
        return REENTRY_EVIDENCE_ARRIVED
    if evaluator_changed:
        return REENTRY_EVALUATOR_CHANGED
    if previous == IMPLEMENTATION_BLOCKED and following not in {
            IMPLEMENTATION_BLOCKED, INVALID}:
        return REENTRY_IMPLEMENTATION_FIXED
    if previous in REENTRY_STATES and following in CLAIMABLE_STATES:
        return REENTRY_EVIDENCE_CHANGED
    if reauthorised:
        return REENTRY_REAUTHORISED
    return REENTRY_NONE


def effective_status(generation_status: Any, execution_status: Any) -> str:
    """The single lifecycle status a consumer should display.

    Governed execution is authoritative once it has happened; otherwise the
    generation/eligibility state is the truth.  Terminal execution states always
    win so a superseded question cannot look runnable.
    """
    execution = str(execution_status or "").strip().upper()
    if execution in TERMINAL_STATES:
        return execution
    if execution == RUNNING:
        return RUNNING
    if execution and execution not in {GENERATED, QUEUED}:
        return execution
    generation = generation_to_lifecycle(generation_status)
    if execution == QUEUED and generation in {GENERATED, QUEUED}:
        return QUEUED
    return generation


def lifecycle_flags(
    *, generation_status: Any, execution_status: Any, evaluator_available: bool,
    evidence_available: bool, scientific: Any,
) -> dict[str, Any]:
    """The five Lab distinctions, derived from authority only.

    ``QUESTION_EXISTS`` / ``QUESTION_EXECUTABLE`` / ``QUESTION_RUNNING`` /
    ``QUESTION_ANSWERED`` / ``QUESTION_SCIENTIFICALLY_ACTIONABLE``.  Scientific
    actionability is never inferred from a COMPLETE execution status.
    """
    status = effective_status(generation_status, execution_status)
    exists = bool(generation_status)
    executable = bool(
        exists and evaluator_available and evidence_available
        and status not in TERMINAL_STATES
    )
    running = status == RUNNING
    answered = status in SCIENTIFICALLY_RESOLVED_STATES
    actionable = bool(
        answered and str(scientific or "") in {
            SCIENTIFICALLY_MEANINGFUL, NEGATIVE_SCIENTIFIC_RESULT}
    )
    return {
        "lifecycle_status": status,
        "question_exists": exists,
        "question_executable": executable,
        "question_running": running,
        "question_answered": answered,
        "question_scientifically_actionable": actionable,
    }


def supersession_key(cell_identity: Any) -> str:
    """Structural supersession identity: the governed observation cell.

    Derived from the observation-cell identity only.  Free-text question wording
    is deliberately never used, so semantic equivalence can never be guessed.
    """
    value = str(cell_identity or "").strip()
    return "coverage_cell:" + value if value else ""


__all__ = [
    "BLOCKED",
    "CLAIMABLE_STATES",
    "COMPLETE",
    "CURRENT",
    "DECLARED_NOT_MEANINGFUL",
    "EXECUTING",
    "FRESHNESS_VALUES",
    "GENERATED",
    "GENERATION_ACTIVE",
    "GENERATION_PROPOSED",
    "GENERATION_READY",
    "IMPLEMENTATION_BLOCKED",
    "INSUFFICIENT_DATA",
    "INVALID",
    "LIFECYCLE_SCHEMA",
    "LIFECYCLE_STATES",
    "MISSING_EVALUATOR",
    "NEGATIVE_RESULT",
    "NEGATIVE_SCIENTIFIC_RESULT",
    "NEVER_EXECUTED",
    "NON_SCIENTIFIC_STATES",
    "NOT_APPLICABLE",
    "NOT_SCIENTIFICALLY_RESOLVED",
    "QUEUED",
    "REENTRY_BLOCKED_TERMINAL",
    "REENTRY_EVIDENCE_ARRIVED",
    "REENTRY_EVIDENCE_CHANGED",
    "REENTRY_EVALUATOR_CHANGED",
    "REENTRY_EVALUATOR_REGISTERED",
    "REENTRY_IMPLEMENTATION_FIXED",
    "REENTRY_NONE",
    "REENTRY_REASONS",
    "REENTRY_REAUTHORISED",
    "REENTRY_STATES",
    "RETIRED",
    "RETIRED_BLIND_SPOT_RESOLVED",
    "RETIRED_CELL_REMOVED",
    "RETIRED_CURIOSITY_NOT_ADMISSIBLE",
    "RUNNING",
    "SCIENTIFICALLY_MEANINGFUL",
    "SCIENTIFICALLY_RESOLVED_STATES",
    "SCIENTIFIC_STATUS_VALUES",
    "STALE_EVIDENCE",
    "STALE_EVALUATOR",
    "SUPERSEDED",
    "SUPERSEDED_BY_EQUIVALENT_CANONICAL_QUESTION",
    "SUPERSEDED_BY_EQUIVALENT_GENERATED_QUESTION",
    "SUPERSEDED_BY_NEWER_GENERATION",
    "SUPERSESSION_REASONS",
    "TERMINAL_STATES",
    "WAITING_FOR_DATA",
    "effective_status",
    "execution_freshness",
    "execution_status_from_report_status",
    "generation_to_lifecycle",
    "lifecycle_flags",
    "reentry_reason",
    "scientific_status",
    "supersession_key",
]
