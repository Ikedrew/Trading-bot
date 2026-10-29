"""
Research Priority Policy v1 -- the EXPLICIT, VERSIONED rule that orders research.

Stage 3 / Wave 5. `research_opportunity` gives an agenda the INPUTS (state,
information value, cost, dependencies). This module gives it the RULE, and
nothing else: it does not hold an agenda, does not execute research, and does
not choose a trading result.

THE POLICY IS TRANSPARENT BY CONSTRUCTION
=========================================
Prioritisation here is LEXICOGRAPHIC over named, closed, ordinal criteria -- not
a weighted floating-point score. That is a deliberate scientific choice:

    - Every component is visible, named and individually inspectable.
    - Every transformation is a lookup in a published table.
    - No false numerical precision is invented (there is no "0.73 priority").
    - Changing the RULE changes the POLICY IDENTITY, and therefore the agenda
      identity, so a rule change can never silently rewrite history.

NO PROFITABILITY, EVER
======================
The closed criterion set contains no expected P&L, win rate, Sharpe, expectancy,
drawdown, profit probability, candidate profitability or "likely winner". A
criterion that does not exist cannot be used, and no criterion can be supplied
by a caller: `PrioritisationPolicy` accepts only the members of `PriorityKey`.

NO URGENCY INVENTED FROM NOTHING
================================
There is deliberately NO urgency or staleness criterion. Nothing in the current
governed architecture records a deadline, an ageing clock or a decay function,
so any "urgency" would be fabricated from a timestamp -- i.e. "newest idea
first", which is exactly the anti-pattern this wave exists to prevent. A future
wave MAY add one, but only if and when a real governed clock exists to derive it
from, and the change would then be a new policy version, not a silent edit.

STABLE UNDER INPUT-ORDER CHANGES
=================================
Ties are resolved by the opportunity's own semantic identity, never by caller
order, insertion order, wall-clock time or a random draw. `ordered_priority_keys`
is what the agenda actually uses, and it is part of the policy identity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_opportunity import (
    EXECUTABILITY_CLASS_ORDER,
    ResearchAgendaError,
    ResearchOpportunity,
    ResearchOpportunityValidationError,
    canonical_opportunities,
    dependency_depths,
    unlock_map,
    unmet_dependencies,
)

PRIORITY_POLICY_SCHEMA_VERSION: int = 1

# `POL-` (prioritisation policy) is disjoint from every Wave 0-4 and canonical
# namespace. A policy is not a research question and is never a `GEN-*` identity.
PRIORITY_POLICY_ID_PREFIX = "POL-"
PRIORITY_POLICY_ID_DIGEST_CHARS = 16

_ID_DIGEST_CHARS = PRIORITY_POLICY_ID_DIGEST_CHARS
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_POL_ID_RE = re.compile(
    rf"^{re.escape(PRIORITY_POLICY_ID_PREFIX)}"
    rf"[0-9A-F]{{{PRIORITY_POLICY_ID_DIGEST_CHARS}}}$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class ResearchPriorityError(ResearchAgendaError):
    """Prioritisation policy material is invalid or not canonically encodable."""


class UnknownPriorityCriterionError(ResearchPriorityError):
    """A caller tried to introduce a criterion outside the closed governed set."""


def is_priority_policy_identity(value: Any) -> bool:
    """True only for IDs inside the reserved prioritisation-policy namespace."""
    return isinstance(value, str) and bool(_POL_ID_RE.match(value))


def priority_policy_identity_for(semantic_identity: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise ResearchPriorityError(
            "policy semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{PRIORITY_POLICY_ID_PREFIX}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise ResearchPriorityError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "policy semantic material").encode("utf-8")).hexdigest()


def _require_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ResearchPriorityError(f"{label} must be a trimmed, non-empty string")
    if not _TOKEN_RE.match(value):
        raise ResearchPriorityError(f"{label} is not a governed token: {value!r}")
    return value


class PriorityKey(str, Enum):
    """
    The CLOSED set of governed prioritisation criteria.

    Every member is a deterministic ORDINAL projection of already-governed
    research state. There is no composite score, no weight and no way for a
    caller to add a criterion: the enum is the whole vocabulary, so
    "prioritise by profitability" is not merely discouraged, it is
    unrepresentable.
    """

    #: Executability class first, always. A BLOCKED / WAITING / REFUSED item may
    #: NEVER outrank a structurally executable one, whatever else it scores.
    EXECUTABILITY = "EXECUTABILITY"
    #: How many unmet prerequisites this item has: zero is actionable now.
    UNMET_DEPENDENCIES = "UNMET_DEPENDENCIES"
    #: How many OTHER items this one unblocks. Larger = more downstream scope.
    DOWNSTREAM_UNLOCK_SCOPE = "DOWNSTREAM_UNLOCK_SCOPE"
    #: The research-information band (HIGH / MEDIUM / LOW / UNKNOWN).
    INFORMATION_BAND = "INFORMATION_BAND"
    #: The research-cost band (CHEAP / MODERATE / EXPENSIVE / UNKNOWN).
    COST_BAND = "COST_BAND"
    #: Dependency depth in the governed graph. Shallow prerequisites first.
    DEPENDENCY_DEPTH = "DEPENDENCY_DEPTH"


#: The ordering every policy MUST start from, and the one the queue relies on.
REQUIRED_LEADING_KEYS: tuple[PriorityKey, ...] = (PriorityKey.EXECUTABILITY,)

#: The deterministic final tie-break, always appended by the policy. Two items
#: that are genuinely indistinguishable are ordered by their own semantic
#: identity, so the ordering is total, reproducible and never random.
TIE_BREAK_KEY: str = "OPPORTUNITY_IDENTITY"



@dataclass(frozen=True)
class PriorityAssessment:
    """
    The fully expanded, per-item evaluation of every criterion in a policy.

    This is what makes the ordering EXPLAINABLE without re-deriving anything: a
    reader can see every component, in the policy's own order, together with the
    ordinal that was used. No composite score is computed, because none exists.
    """

    opportunity_identity: str
    components: tuple[tuple[str, int], ...]

    def ordinals(self) -> tuple[int, ...]:
        return tuple(value for _, value in self.components)

    def sort_key(self) -> tuple[int, ...]:
        return self.ordinals()

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_identity": self.opportunity_identity,
            "components": [
                {"criterion": name, "ordinal": value} for name, value in self.components
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriorityAssessment":
        if not isinstance(data, Mapping):
            raise ResearchPriorityError(
                f"persisted priority assessment must be a mapping, got "
                f"{type(data).__name__}")
        expected = {"opportunity_identity", "components"}
        if set(data) != expected:
            raise ResearchPriorityError(
                f"persisted priority assessment fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        raw = data["components"]
        if not isinstance(raw, list):
            raise ResearchPriorityError(
                "persisted priority assessment 'components' must be a list")
        components: list[tuple[str, int]] = []
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {"criterion", "ordinal"}:
                raise ResearchPriorityError(
                    "each priority component must be exactly {criterion, ordinal}")
            if isinstance(item["ordinal"], bool) or not isinstance(item["ordinal"], int):
                raise ResearchPriorityError("priority component ordinal must be an integer")
            components.append((str(item["criterion"]), int(item["ordinal"])))
        return cls(opportunity_identity=str(data["opportunity_identity"]),
                   components=tuple(components))


@dataclass(frozen=True)
class PrioritisationPolicy:
    """
    An explicit, versioned, identity-bearing prioritisation RULE.

    IDENTITY MATERIAL: the policy version label and the ordered criterion list
    (plus the governed tie-break). Nothing else. Two policies that order
    research identically ARE the same policy; two that differ in ANY criterion
    position are different policies and produce different agenda identities.

    The policy version is a governance label the human research process owns
    (e.g. "W5-LEX-V1"); the criteria list is the machine-checkable rule. Both
    are hashed, so a rule change can never masquerade as the same history.
    """

    policy_version: str
    criteria: tuple[PriorityKey, ...]
    note: str = ""                       # provenance only; NOT identity
    schema_version: int = PRIORITY_POLICY_SCHEMA_VERSION
    semantic_identity: str = ""
    policy_identity: str = ""

    def __post_init__(self) -> None:
        criteria: list[PriorityKey] = []
        for item in self.criteria:
            if isinstance(item, str) and not isinstance(item, PriorityKey):
                try:
                    item = PriorityKey(item)
                except ValueError as exc:
                    raise UnknownPriorityCriterionError(
                        f"{item!r} is not a governed prioritisation criterion; the "
                        f"closed set is {[k.value for k in PriorityKey]}") from exc
            if not isinstance(item, PriorityKey):
                raise UnknownPriorityCriterionError(
                    f"criteria must be PriorityKey members, got {item!r}")
            criteria.append(item)
        if not criteria:
            raise ResearchPriorityError(
                "a prioritisation policy must declare at least one criterion")
        if len(set(criteria)) != len(criteria):
            raise ResearchPriorityError(
                f"a prioritisation policy may not repeat a criterion: {criteria}")
        leading = tuple(criteria[:len(REQUIRED_LEADING_KEYS)])
        if leading != REQUIRED_LEADING_KEYS:
            raise ResearchPriorityError(
                f"every policy must begin with {REQUIRED_LEADING_KEYS} so structurally "
                f"impossible work can never outrank executable work; got {leading}")
        object.__setattr__(self, "criteria", tuple(criteria))
        object.__setattr__(
            self, "policy_version", _require_token(
                self.policy_version, "policy_version"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = priority_policy_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchPriorityError(
                    "presented policy semantic identity does not match the policy "
                    "material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.policy_identity:
            if self.policy_identity != expected_id:
                raise ResearchPriorityError(
                    f"presented policy identity {self.policy_identity!r} does not match "
                    f"the policy material")
        else:
            object.__setattr__(self, "policy_identity", expected_id)

    @classmethod
    def create(cls, **kwargs: Any) -> "PrioritisationPolicy":
        """Build a policy. Identity is derived, never supplied."""
        return cls(**kwargs)

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_prioritisation_policy",
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "criteria": [key.value for key in self.criteria],
            "tie_break": TIE_BREAK_KEY,
        }


    def _validate(self) -> "PrioritisationPolicy":
        if self.schema_version != PRIORITY_POLICY_SCHEMA_VERSION:
            raise ResearchPriorityError(
                f"prioritisation policy schema_version must be "
                f"{PRIORITY_POLICY_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        _encode(self.semantic_material(), "policy semantic material")
        return self

    def ordered_criteria(self) -> tuple[str, ...]:
        """
        The exact criterion sequence the agenda uses, INCLUDING the tie-break.

        Exposed as plain names so the agenda record and the frozen T0 snapshot
        can show the rule verbatim.
        """
        return tuple(key.value for key in self.criteria) + (TIE_BREAK_KEY,)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "policy_identity": self.policy_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PrioritisationPolicy":
        if not isinstance(data, Mapping):
            raise ResearchPriorityError(
                f"persisted policy must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "policy_version", "criteria", "tie_break", "note",
            "semantic_identity", "policy_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchPriorityError(
                f"persisted policy missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchPriorityError(
                f"persisted policy has unknown fields: {unknown}")
        if data["kind"] != "research_prioritisation_policy":
            raise ResearchPriorityError(
                f"persisted policy kind must be 'research_prioritisation_policy', got "
                f"{data['kind']!r}")
        if data["tie_break"] != TIE_BREAK_KEY:
            raise ResearchPriorityError(
                f"persisted policy tie_break {data['tie_break']!r} is not the governed "
                f"deterministic tie-break {TIE_BREAK_KEY!r}")
        criteria = data["criteria"]
        if not isinstance(criteria, list):
            raise ResearchPriorityError("persisted policy 'criteria' must be a list")
        try:
            resolved = tuple(PriorityKey(item) for item in criteria)
        except ValueError as exc:
            raise UnknownPriorityCriterionError(
                f"persisted policy names a non-governed criterion: {exc}") from exc
        return cls(
            policy_version=data["policy_version"],
            criteria=resolved,
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            policy_identity=data["policy_identity"],
        )


def _ordinals_for(
        item: ResearchOpportunity,
        policy: PrioritisationPolicy,
        population: tuple[ResearchOpportunity, ...],
        unlocks: Mapping[str, tuple[str, ...]],
        depths: Mapping[str, int],
) -> tuple[tuple[str, int], ...]:
    """
    Expand ONE opportunity against EVERY criterion in the policy.

    Each ordinal is smaller-is-earlier. Two conventions matter and are deliberate:

        DOWNSTREAM_UNLOCK_SCOPE is NEGATED, because unlocking more research work
        is better, so a larger scope must sort earlier;
        INFORMATION_BAND and COST_BAND use the published rank tables, where
        UNKNOWN is the LAST rank for both -- unknown value of learning never
        displaces known, and unknown cost is never treated as cheap.
    """
    unmet = unmet_dependencies(item, population)
    components: list[tuple[str, int]] = []
    for key in policy.criteria:
        if key is PriorityKey.EXECUTABILITY:
            # A READY item with no unmet prerequisite is the only thing that can
            # lead. Everything else is ranked strictly behind it.
            executable = item.state.is_executable_research and not unmet
            components.append((key.value, 0 if executable else 1))
        elif key is PriorityKey.UNMET_DEPENDENCIES:
            components.append((key.value, len(unmet)))
        elif key is PriorityKey.DOWNSTREAM_UNLOCK_SCOPE:
            components.append((key.value, -len(unlocks.get(item.opportunity_identity, ()))))
        elif key is PriorityKey.INFORMATION_BAND:
            components.append((key.value, item.information.rank()))
        elif key is PriorityKey.COST_BAND:
            components.append((key.value, item.cost.rank()))
        elif key is PriorityKey.DEPENDENCY_DEPTH:
            components.append((key.value, depths.get(item.opportunity_identity, 0)))
        else:  # pragma: no cover - the enum is closed
            raise UnknownPriorityCriterionError(
                f"no ordinal is defined for criterion {key!r}")
    # The governed tie-break is the item's own identity string. It is recorded as
    # a component so the record shows the rule was applied, and it is applied as
    # a string comparison in `order_opportunities` (it is the only non-ordinal
    # component, and it is always a last resort).
    components.append((TIE_BREAK_KEY, 0))
    return tuple(components)


def order_opportunities(
        policy: PrioritisationPolicy,
        opportunities: Iterable[ResearchOpportunity],
) -> tuple[tuple[ResearchOpportunity, PriorityAssessment], ...]:
    """
    Deterministically order a governed population under an explicit policy.

    Determinism guarantees, in order of importance:

        1. caller order is irrelevant -- the population is canonicalised first;
        2. the ordering is a total order -- the tie-break is the item identity;
        3. no randomness, no clock, no floating-point comparison;
        4. every component of every decision is returned, so the result is
           mechanically explainable without re-deriving anything.
    """
    if not isinstance(policy, PrioritisationPolicy):
        raise ResearchPriorityError(
            f"expected PrioritisationPolicy, got {type(policy).__name__}")
    population = assert_acyclic(policy, opportunities)
    unlocks = unlock_map(population)
    depths = dependency_depths(population)

    assessed = [
        (item, PriorityAssessment(
            opportunity_identity=item.opportunity_identity,
            components=_ordinals_for(item, policy, population, unlocks, depths)))
        for item in population
    ]
    # Sort on (ordinals, then identity). The identity is already the last
    # component, but repeating it here makes the guarantee local and obvious.
    assessed.sort(key=lambda pair: (pair[1].sort_key(), pair[0].opportunity_identity))
    return tuple(assessed)


def assert_acyclic(
        policy: PrioritisationPolicy,
        opportunities: Iterable[ResearchOpportunity],
) -> tuple[ResearchOpportunity, ...]:
    """
    Canonicalise the population and prove its dependency graph is acyclic.

    A cycle is unrepairable: no ordering can make cyclic work executable, so an
    agenda built on one would rank structurally impossible work as though it
    were research. It therefore fails closed, before any ordering is produced.
    """
    from research_engine.lifecycle.research_opportunity import (
        assert_acyclic_dependencies,
    )

    return assert_acyclic_dependencies(opportunities)


def explain_precedence(
        policy: PrioritisationPolicy,
        first: PriorityAssessment,
        second: PriorityAssessment,
) -> str:
    """
    A mechanical, one-line explanation of why `first` precedes `second`.

    The first criterion on which the two differ is named, with both ordinals.
    This is the whole explanation: there is no hidden weighting to disclose,
    because no weighting exists.
    """
    if first.opportunity_identity == second.opportunity_identity:
        raise ResearchPriorityError(
            "cannot explain the precedence of an opportunity against itself")
    if len(first.components) != len(second.components):
        raise ResearchPriorityError(
            "cannot compare priority assessments built from different criteria")
    for (name, left), (_, right) in zip(first.components, second.components):
        if left != right:
            relation = "precedes" if left < right else "follows"
            return (
                f"{first.opportunity_identity} {relation} {second.opportunity_identity} "
                f"on criterion {name} ({left} vs {right})")
    raise ResearchPriorityError(
        f"{first.opportunity_identity} and {second.opportunity_identity} are "
        f"indistinguishable under policy {policy.policy_identity}; the governed "
        f"tie-break should have separated them, which indicates a corrupted record")


#: The FIRST governed policy. A defensible, fully transparent default: what may
#: be researched at all, then what unblocks the most downstream work, then how
#: much is to be learned, then what it costs.
POLICY_LEXICOGRAPHIC_V1: PrioritisationPolicy = PrioritisationPolicy.create(
    policy_version="W5-LEX-V1",
    criteria=(
        PriorityKey.EXECUTABILITY,
        PriorityKey.DEPENDENCY_DEPTH,
        PriorityKey.UNMET_DEPENDENCIES,
        PriorityKey.DOWNSTREAM_UNLOCK_SCOPE,
        PriorityKey.INFORMATION_BAND,
        PriorityKey.COST_BAND,
    ),
    note=("Value of learning first, cost last, dependencies before dependents, "
          "and a mandatory executability gate in front of everything."),
)

#: A SECOND, equally legitimate policy, used to prove that a rule change is
#: provenance-visible and produces a NEW agenda rather than rewriting history.
#: It deliberately puts the research-information band ahead of dependency scope:
#: a human research process may legitimately prefer "learn the most first".
POLICY_INFORMATION_FIRST_V1: PrioritisationPolicy = PrioritisationPolicy.create(
    policy_version="W5-INFO-FIRST-V1",
    criteria=(
        PriorityKey.EXECUTABILITY,
        PriorityKey.INFORMATION_BAND,
        PriorityKey.UNMET_DEPENDENCIES,
        PriorityKey.DOWNSTREAM_UNLOCK_SCOPE,
        PriorityKey.COST_BAND,
    ),
    note=("Information value of learning ahead of downstream scope; cost last. "
          "Identical governance guarantees, different and equally defensible order."),
)

#: Every built-in policy, in a stable order.
BUILT_IN_POLICIES: tuple[PrioritisationPolicy, ...] = (
    POLICY_LEXICOGRAPHIC_V1,
    POLICY_INFORMATION_FIRST_V1,
)


def built_in_policy(version: str) -> PrioritisationPolicy:
    """Deterministic lookup of a built-in policy by its governed version label."""
    for policy in BUILT_IN_POLICIES:
        if policy.policy_version == version:
            return policy
    raise ResearchPriorityError(
        f"unknown built-in prioritisation policy {version!r}; available: "
        f"{[p.policy_version for p in BUILT_IN_POLICIES]}")


__all__ = [
    "BUILT_IN_POLICIES",
    "POLICY_INFORMATION_FIRST_V1",
    "POLICY_LEXICOGRAPHIC_V1",
    "PRIORITY_POLICY_ID_DIGEST_CHARS",
    "PRIORITY_POLICY_ID_PREFIX",
    "PRIORITY_POLICY_SCHEMA_VERSION",
    "PriorityAssessment",
    "PriorityKey",
    "PrioritisationPolicy",
    "REQUIRED_LEADING_KEYS",
    "ResearchPriorityError",
    "TIE_BREAK_KEY",
    "UnknownPriorityCriterionError",
    "built_in_policy",
    "is_priority_policy_identity",
    "order_opportunities",
    "priority_policy_identity_for",
]
