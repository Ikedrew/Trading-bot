"""
Lambda Research Adapter.

Provides the Lambda-compatible interface to the canonical research engine.
Uses the same ResearchContextResolver as the local runner — ensuring parity.

Supported actions:
    run_canonical_question  — Execute one question via canonical resolver
    run_canonical_bank      — Execute all 45 questions
    resolve_question        — Resolve context without executing (dry run)

This adapter does NOT use the old ExperimentRunner or 12-question registry.
It uses the new-engine 45-question bank + canonical contracts.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from research_engine.v10.runner.context_resolver import (
    ResearchContextResolver,
    ResolvedContext,
)
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext
from research_engine.v10.runner.primitive_mapping import build_full_mapping
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Universe, NewEngineQuestion

logger = logging.getLogger(__name__)


class LambdaResearchAdapter:
    """
    Lambda-compatible adapter for the canonical research engine.

    Uses the same resolution path as the local ResearchExecutionOrchestrator:
        Question → Contracts → Populations → Primitives → Finding
    """

    def __init__(self, builders: dict[Universe, UniverseBuilder] | None = None):
        self._registry = build_default_registry()
        self._resolver = ResearchContextResolver(
            builders=builders or {},
            registry=self._registry,
        )
        self._mapping = build_full_mapping(self._get_question_bank())
        self._runner = QuestionRunner(self._registry, self._mapping)
        self._builders = builders

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Handle a Lambda research event using canonical resolution.

        Args:
            event: {"action": "run_canonical_question", "question_id": "E-001", ...}

        Returns:
            JSON-serialisable result dict.
        """
        action = event.get("action", "")
        start = time.time()

        try:
            if action == "run_canonical_question":
                result = self._run_one(event)
            elif action == "run_canonical_bank":
                result = self._run_all(event)
            elif action == "resolve_question":
                result = self._resolve_only(event)
            else:
                result = {"error": f"Unknown canonical action: '{action}'"}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}

        result["_action"] = action
        result["_duration_seconds"] = round(time.time() - start, 2)
        return result

    def _run_one(self, event: dict) -> dict:
        """Execute one canonical question."""
        qid = event.get("question_id", "")
        question = self._find_question(qid)
        if question is None:
            return {"error": f"Question '{qid}' not found in canonical bank"}

        # Ensure universes are built
        self._ensure_builders()

        # Resolve
        resolved = self._resolver.resolve(question)
        if not resolved.ready:
            return {
                "question_id": qid,
                "status": "BLOCKED",
                "reason": resolved.blocked_reason,
                "manifest": resolved.manifest.to_dict(),
            }

        # Execute
        ctx = RunContext(
            engine_version="1.0.0",
            universe_versions=resolved.manifest.universe_versions,
            population_versions=resolved.manifest.population_versions,
            primitive_versions=self._registry.versions(),
        )
        result = self._runner.run_question(question, resolved.population, ctx)

        if not result.success or result.finding is None:
            return {
                "question_id": qid,
                "status": "ERROR",
                "error": result.error,
                "manifest": resolved.manifest.to_dict(),
            }

        return {
            "question_id": qid,
            "status": "COMPLETE" if result.finding.confidence != "INSUFFICIENT" else "INCONCLUSIVE",
            "finding": result.finding.to_dict(),
            "manifest": resolved.manifest.to_dict(),
        }

    def _run_all(self, event: dict) -> dict:
        """Execute all canonical questions."""
        self._ensure_builders()
        questions = self._get_question_bank()

        results = []
        completed = 0
        blocked = 0
        failed = 0

        for q in questions:
            resolved = self._resolver.resolve(q)
            if not resolved.ready:
                results.append({
                    "question_id": q.question_id,
                    "status": "BLOCKED",
                    "reason": resolved.blocked_reason,
                })
                blocked += 1
                continue

            ctx = RunContext(
                universe_versions=resolved.manifest.universe_versions,
                population_versions=resolved.manifest.population_versions,
                primitive_versions=self._registry.versions(),
            )
            r = self._runner.run_question(q, resolved.population, ctx)

            if r.success and r.finding:
                status = "COMPLETE" if r.finding.confidence != "INSUFFICIENT" else "INCONCLUSIVE"
                results.append({
                    "question_id": q.question_id,
                    "status": status,
                    "outcome": r.finding.outcome,
                    "confidence": r.finding.confidence,
                })
                completed += 1
            else:
                results.append({
                    "question_id": q.question_id,
                    "status": "ERROR",
                    "error": r.error,
                })
                failed += 1

        return {
            "total": len(questions),
            "completed": completed,
            "blocked": blocked,
            "failed": failed,
            "results": results,
        }

    def _resolve_only(self, event: dict) -> dict:
        """Resolve context without executing (dry run)."""
        qid = event.get("question_id", "")
        question = self._find_question(qid)
        if question is None:
            return {"error": f"Question '{qid}' not found"}

        self._ensure_builders()
        resolved = self._resolver.resolve(question)

        return {
            "question_id": qid,
            "ready": resolved.ready,
            "blocked_reason": resolved.blocked_reason,
            "population_size": len(resolved.population),
            "primitives": resolved.primitives,
            "manifest": resolved.manifest.to_dict(),
        }

    def _ensure_builders(self) -> None:
        """Build universes if not already built."""
        if self._builders and all(
            b.is_built for b in self._builders.values()
        ):
            return

        from research_engine.v10.universes import (
            ExecutionUniverseBuilder,
            DecisionUniverseBuilder,
            MarketUniverseBuilder,
            StrategyUniverseBuilder,
        )
        builders: dict[Universe, UniverseBuilder] = {}
        for UClass, utype in [
            (ExecutionUniverseBuilder, Universe.EXECUTION),
            (DecisionUniverseBuilder, Universe.DECISION),
            (MarketUniverseBuilder, Universe.MARKET),
            (StrategyUniverseBuilder, Universe.STRATEGY),
        ]:
            try:
                b = UClass()
                b.build()
                builders[utype] = b
            except Exception as e:
                logger.warning(f"[LAMBDA_ADAPTER] Failed to build {utype.value}: {e}")

        self._builders = builders
        self._resolver.set_builders(builders)

    def _find_question(self, question_id: str) -> NewEngineQuestion | None:
        from research_engine.v10.universes.question_bank import get_question
        return get_question(question_id)

    def _get_question_bank(self) -> tuple[NewEngineQuestion, ...]:
        from research_engine.v10.universes.question_bank import QUESTION_BANK
        return QUESTION_BANK
