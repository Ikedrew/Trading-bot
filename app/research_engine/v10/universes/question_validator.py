"""
Question Validator.

Checks each of the 45 new-engine questions against the formal contracts
to determine readiness status: READY, BLOCKED, or INVALID.

A question is:
    READY   — all required universes, populations, fields, and joins are available
    BLOCKED — a required population/field/join genuinely does not exist or is empty
    INVALID — the question definition conflicts with the data contract
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.universes.contracts import (
    POPULATION_CONTRACTS,
    UNIVERSE_CONTRACTS,
    JOIN_CONTRACTS,
    SEMANTIC_FIELDS_BY_NAME,
    get_join_contract,
    get_population_contract,
)
from research_engine.v10.universes.correlation import (
    EXECUTION_DECISION_CORRELATION,
    CorrelationContract,
    CorrelationTrust,
)
from research_engine.v10.universes.models import (
    NewEngineQuestion,
    Population,
    Universe,
)
from research_engine.v10.universes.resolver import PopulationResolver, ResolutionResult


@dataclass
class QuestionReadiness:
    """Readiness assessment for a single question."""
    question_id: str
    title: str
    status: str  # READY, BLOCKED, INVALID
    reasons: list[str] = field(default_factory=list)
    universe_status: dict[str, str] = field(default_factory=dict)
    population_status: dict[str, str] = field(default_factory=dict)
    field_status: dict[str, str] = field(default_factory=dict)
    join_status: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "status": self.status,
            "reasons": self.reasons,
            "universe_status": self.universe_status,
            "population_status": self.population_status,
            "field_status": self.field_status,
            "join_status": self.join_status,
        }


def validate_question(
    question: NewEngineQuestion,
    resolver: PopulationResolver | None = None,
) -> QuestionReadiness:
    """
    Validate a single question against the formal contracts.

    Args:
        question: The question to validate.
        resolver: Optional resolver with registered population versions.

    Returns:
        QuestionReadiness with status and detailed breakdown.
    """
    readiness = QuestionReadiness(
        question_id=question.question_id,
        title=question.title,
        status="READY",
        reasons=[],
    )

    # 1. Check required universes have contracts
    for u in question.required_universes:
        if u in UNIVERSE_CONTRACTS:
            readiness.universe_status[u.value] = "AVAILABLE"
        else:
            readiness.universe_status[u.value] = "MISSING"
            readiness.reasons.append(f"Universe {u.value} has no contract")
            readiness.status = "INVALID"

    # 2. Check required populations have contracts
    for pop in question.required_populations:
        contract = get_population_contract(pop)
        if contract:
            readiness.population_status[pop.value] = "CONTRACTED"
        else:
            readiness.population_status[pop.value] = "NO_CONTRACT"
            readiness.reasons.append(f"Population {pop.value} has no contract")
            if readiness.status != "INVALID":
                readiness.status = "BLOCKED"

    # 3. Check required populations can be resolved (if resolver provided)
    if resolver:
        for pop in question.required_populations:
            result = resolver.resolve(
                population=pop,
                universe=_pop_universe(pop, question.required_universes),
                minimum_sample_size=question.minimum_sample_size,
            )
            if not result.resolved:
                if readiness.population_status.get(pop.value) == "CONTRACTED":
                    readiness.population_status[pop.value] = f"BLOCKED: {result.reason}"
                readiness.reasons.append(
                    f"Population {pop.value}: {result.reason}"
                )
                if readiness.status == "READY":
                    readiness.status = "BLOCKED"

    # 4. Check semantic fields have mappings
    for ar in question.angle_requirements:
        for field_name in ar.required_fields:
            mappings = SEMANTIC_FIELDS_BY_NAME.get(field_name, [])
            universe_mappings = [m for m in mappings if m.universe_id == ar.universe]
            if universe_mappings:
                readiness.field_status[f"{ar.universe.value}.{field_name}"] = "MAPPED"
            elif mappings:
                # Field exists but in a different universe — check if it's in
                # another required universe (cross-angle questions)
                other_u = [m.universe_id for m in mappings]
                if any(u in question.required_universes for u in other_u):
                    readiness.field_status[f"{ar.universe.value}.{field_name}"] = "CROSS_MAPPED"
                else:
                    readiness.field_status[f"{ar.universe.value}.{field_name}"] = "WRONG_UNIVERSE"
                    readiness.reasons.append(
                        f"Field '{field_name}' not mapped in {ar.universe.value} "
                        f"(available in {[u.value for u in other_u]})"
                    )
            else:
                readiness.field_status[f"{ar.universe.value}.{field_name}"] = "UNMAPPED"
                # Not necessarily blocking — some fields are derived/computed
                # Only block if it's a core measurement field
                if field_name in ("r_multiple", "entity_id", "action"):
                    readiness.reasons.append(
                        f"Critical field '{field_name}' has no mapping in any universe"
                    )
                    if readiness.status == "READY":
                        readiness.status = "BLOCKED"

    # 5. Check joins have contracts (including correlation layer)
    for join_req in question.required_joins:
        jc = get_join_contract(join_req.from_universe, join_req.to_universe)
        if jc:
            readiness.join_status[
                f"{join_req.from_universe.value}→{join_req.to_universe.value}"
            ] = f"VALID ({jc.cardinality.value})"
        else:
            # Check reverse direction
            jc_rev = get_join_contract(join_req.to_universe, join_req.from_universe)
            if jc_rev:
                readiness.join_status[
                    f"{join_req.from_universe.value}→{join_req.to_universe.value}"
                ] = f"VALID_REVERSE ({jc_rev.cardinality.value})"
            else:
                # Check correlation layer (optional joins)
                corr = _check_correlation_contract(
                    join_req.from_universe, join_req.to_universe
                )
                if corr:
                    trust = corr.trust_classification
                    if trust in (CorrelationTrust.TRUSTWORTHY, CorrelationTrust.PARTIAL_BUT_USABLE):
                        readiness.join_status[
                            f"{join_req.from_universe.value}→{join_req.to_universe.value}"
                        ] = f"CORRELATION ({trust.value}, {corr.historical_coverage:.0%})"
                    else:
                        readiness.join_status[
                            f"{join_req.from_universe.value}→{join_req.to_universe.value}"
                        ] = f"CORRELATION_INSUFFICIENT ({trust.value})"
                        readiness.reasons.append(
                            f"Join {join_req.from_universe.value}→"
                            f"{join_req.to_universe.value}: correlation is "
                            f"{trust.value} ({corr.historical_coverage:.0%} coverage)"
                        )
                        if readiness.status == "READY":
                            readiness.status = "BLOCKED"
                else:
                    readiness.join_status[
                        f"{join_req.from_universe.value}→{join_req.to_universe.value}"
                    ] = "NO_CONTRACT"
                    readiness.reasons.append(
                        f"Join {join_req.from_universe.value}→"
                        f"{join_req.to_universe.value} has no contract"
                    )
                    if readiness.status == "READY":
                        readiness.status = "BLOCKED"

    return readiness


def validate_all_questions(
    questions: tuple[NewEngineQuestion, ...],
    resolver: PopulationResolver | None = None,
) -> list[QuestionReadiness]:
    """Validate all questions and return readiness assessments."""
    return [validate_question(q, resolver) for q in questions]


def _pop_universe(pop: Population, universes: tuple[Universe, ...]) -> Universe:
    """Determine which universe a population belongs to."""
    contract = get_population_contract(pop)
    if contract:
        uid = contract.universe_id
        if isinstance(uid, Universe):
            return uid
        return Universe(uid)
    # Fallback to first required universe
    return universes[0] if universes else Universe.EXECUTION


def _check_correlation_contract(
    from_u: Universe, to_u: Universe
) -> CorrelationContract | None:
    """Check if a correlation contract exists for this universe pair."""
    corr = EXECUTION_DECISION_CORRELATION
    # Check both directions
    if (
        (corr.left_universe == from_u.value and corr.right_universe == to_u.value)
        or (corr.left_universe == to_u.value and corr.right_universe == from_u.value)
    ):
        return corr
    return None
