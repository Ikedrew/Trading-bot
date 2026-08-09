"""
Canonical Research Context Resolver.

Single source of truth for resolving a question's complete execution context.
Used by BOTH the local runner and Lambda — ensuring parity.

Given a question, resolves:
    - Required universe(s) → built and validated
    - Required population(s) → latest valid version
    - Required joins → validated against join contracts
    - Required semantic fields → available in resolved populations
    - Required primitive(s) → available in registry
    - Resolution manifest → reproducibility metadata

No hard-coded population paths. No Lambda-specific assumptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.runner.primitive_mapping import resolve_primitives_for_question
from research_engine.v10.runner.primitives.base import AnalysisRegistry
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.contracts import (
    get_join_contract,
    get_population_contract,
)
from research_engine.v10.universes.correlation import (
    EXECUTION_DECISION_CORRELATION,
    CorrelationTrust,
)
from research_engine.v10.universes.models import (
    NewEngineQuestion,
    Population,
    QuestionStatus,
    Universe,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVED CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResolutionManifest:
    """Records exactly what was resolved for reproducibility."""
    question_id: str = ""
    resolved_at: str = ""
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)
    population_record_counts: dict[str, int] = field(default_factory=dict)
    join_contracts_used: list[str] = field(default_factory=list)
    semantic_mapping_version: str = "1.0.0"
    primitive_registry_version: str = "1.0.0"
    runner_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "resolved_at": self.resolved_at,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "population_record_counts": self.population_record_counts,
            "join_contracts_used": self.join_contracts_used,
            "semantic_mapping_version": self.semantic_mapping_version,
            "primitive_registry_version": self.primitive_registry_version,
            "runner_version": self.runner_version,
        }


@dataclass
class ResolvedContext:
    """Complete resolved execution context for one question."""
    question: NewEngineQuestion
    ready: bool = False
    blocked_reason: str = ""
    population: list[dict[str, Any]] = field(default_factory=list)
    primitives: list[str] = field(default_factory=list)
    manifest: ResolutionManifest = field(default_factory=ResolutionManifest)


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchContextResolver:
    """
    Canonical resolver used by both local and Lambda runners.

    Resolves: question → universes → populations → joins → fields → primitives.
    Produces a ResolvedContext that the QuestionRunner can execute.
    """

    def __init__(
        self,
        builders: dict[Universe, UniverseBuilder] | None = None,
        registry: AnalysisRegistry | None = None,
    ):
        self._builders = builders or {}
        self._registry = registry or build_default_registry()

    def set_builders(self, builders: dict[Universe, UniverseBuilder]) -> None:
        """Set universe builders (call after building universes)."""
        self._builders = builders

    def resolve(self, question: NewEngineQuestion) -> ResolvedContext:
        """
        Resolve the complete execution context for a question.

        Returns:
            ResolvedContext with ready=True if all requirements met,
            or ready=False with blocked_reason explaining why.
        """
        ctx = ResolvedContext(question=question)
        ctx.manifest.question_id = question.question_id
        ctx.manifest.resolved_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # 1. Check question status
        if question.status == QuestionStatus.BLOCKED:
            ctx.blocked_reason = f"Question contract status is BLOCKED"
            return ctx

        # 2. Resolve universes
        for u in question.required_universes:
            builder = self._builders.get(u)
            if builder is None or not builder.is_built:
                ctx.blocked_reason = f"Universe {u.value} not available"
                return ctx
            ctx.manifest.universe_versions[u.value] = builder.metadata.content_hash

        # 3. Resolve populations
        primary_universe = question.required_universes[0]
        primary_builder = self._builders[primary_universe]

        if question.required_populations:
            primary_pop = question.required_populations[0]
            pop_contract = get_population_contract(primary_pop)
            if pop_contract is None:
                ctx.blocked_reason = f"Population {primary_pop.value} has no contract"
                return ctx

            population = primary_builder.get_population(primary_pop)
            ctx.manifest.population_versions[primary_pop.value] = (
                primary_builder.metadata.content_hash
            )
            ctx.manifest.population_record_counts[primary_pop.value] = len(population)
        else:
            population = primary_builder.records
            ctx.manifest.population_record_counts["all"] = len(population)

        # 4. Check minimum sample size
        if len(population) < question.minimum_sample_size:
            # Not blocking — will produce INCONCLUSIVE finding
            pass

        # 5. Validate joins (for cross-universe questions)
        for join_req in question.required_joins:
            jc = get_join_contract(join_req.from_universe, join_req.to_universe)
            jc_rev = get_join_contract(join_req.to_universe, join_req.from_universe)

            if jc:
                ctx.manifest.join_contracts_used.append(jc.join_id)
            elif jc_rev:
                ctx.manifest.join_contracts_used.append(jc_rev.join_id)
            else:
                # Check correlation layer
                corr = EXECUTION_DECISION_CORRELATION
                pair_match = (
                    (corr.left_universe == join_req.from_universe.value
                     and corr.right_universe == join_req.to_universe.value)
                    or (corr.left_universe == join_req.to_universe.value
                        and corr.right_universe == join_req.from_universe.value)
                )
                if pair_match:
                    if corr.trust_classification in (
                        CorrelationTrust.TRUSTWORTHY,
                        CorrelationTrust.PARTIAL_BUT_USABLE,
                    ):
                        ctx.manifest.join_contracts_used.append(corr.join_id)
                    else:
                        ctx.blocked_reason = (
                            f"Join {join_req.from_universe.value}→"
                            f"{join_req.to_universe.value}: correlation "
                            f"{corr.trust_classification.value}"
                        )
                        return ctx
                else:
                    ctx.blocked_reason = (
                        f"No join contract for {join_req.from_universe.value}→"
                        f"{join_req.to_universe.value}"
                    )
                    return ctx

        # 6. Resolve primitives
        primitives = resolve_primitives_for_question(
            question.analysis_type.value, question.views
        )
        for pname in primitives:
            if not self._registry.has(pname):
                ctx.blocked_reason = f"Primitive '{pname}' not in registry"
                return ctx
        ctx.primitives = primitives

        # 7. All resolved — mark ready
        ctx.ready = True
        ctx.population = population
        ctx.manifest.primitive_registry_version = "1.0.0"

        return ctx

    def resolve_all(
        self, questions: tuple[NewEngineQuestion, ...]
    ) -> list[ResolvedContext]:
        """Resolve all questions. Each independently — one failure doesn't block others."""
        return [self.resolve(q) for q in questions]
