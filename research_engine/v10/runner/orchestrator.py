"""
Research Execution Orchestrator.

The top-level coordinator that:
    1. Validates all 45 questions against contracts
    2. Resolves required populations from built universes
    3. Executes READY questions via the generic QuestionRunner
    4. Saves findings through QuestionProductManager
    5. Produces an immutable run manifest
    6. Updates the Control Plane

Does NOT:
    - Perform analysis (primitives do that)
    - Modify trading logic
    - Auto-create questions
    - Trigger optimisation
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.control_plane.finding_schema import ResearchFinding
from research_engine.v10.control_plane.models import ResearchRunManifest
from research_engine.v10.control_plane.question_products import QuestionProductManager
from research_engine.v10.runner.primitive_mapping import build_full_mapping
from research_engine.v10.runner.primitives.base import AnalysisRegistry
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.runner.question_runner import (
    QuestionRunner,
    RunContext,
    QuestionExecutionResult,
)
from research_engine.v10.universes.models import (
    NewEngineQuestion,
    Population,
    QuestionStatus,
    Universe,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION READINESS CLASSIFICATION (for execution)
# ═══════════════════════════════════════════════════════════════════════════════


class ExecutionStatus:
    COMPLETE = "COMPLETE"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class QuestionExecutionOutcome:
    """Final execution outcome for one question."""
    question_id: str
    status: str  # COMPLETE, INCONCLUSIVE, BLOCKED, ERROR
    reason: str = ""
    finding: ResearchFinding | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchExecutionOrchestrator:
    """
    Coordinates the execution of the entire 45-question research bank.

    Usage:
        orch = ResearchExecutionOrchestrator()
        manifest = orch.execute_all()
    """

    def __init__(
        self,
        questions_dir: Path | str | None = None,
        registry: AnalysisRegistry | None = None,
    ):
        self._questions_dir = Path(questions_dir) if questions_dir else Path("reports/research/questions")
        self._registry = registry or build_default_registry()
        self._product_mgr = QuestionProductManager(base_dir=self._questions_dir)
        self._mapping = build_full_mapping(self._get_question_bank())
        self._runner = QuestionRunner(self._registry, self._mapping)

    def execute_all(
        self,
        universe_builders: dict[Universe, Any] | None = None,
    ) -> tuple[ResearchRunManifest, list[QuestionExecutionOutcome]]:
        """
        Execute all 45 questions against latest valid populations.

        Args:
            universe_builders: Pre-built universe builders (if None, builds from defaults).

        Returns:
            (manifest, outcomes) — the run manifest and per-question outcomes.
        """
        start_time = time.time()
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        questions = self._get_question_bank()

        # Build universes
        builders = universe_builders or self._build_universes()

        # Build run context
        context = RunContext(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            engine_version="1.0.0",
            question_bank_version="1.0.0",
            universe_versions={
                u.value: b.metadata.content_hash
                for u, b in builders.items() if b.is_built
            },
            population_versions={},
            primitive_versions=self._registry.versions(),
        )

        # Execute each question (isolated)
        outcomes: list[QuestionExecutionOutcome] = []
        completed = 0
        blocked = 0
        failed = 0
        inconclusive = 0

        for q in questions:
            outcome = self._execute_one(q, builders, context)
            outcomes.append(outcome)

            if outcome.status == ExecutionStatus.COMPLETE:
                completed += 1
            elif outcome.status == ExecutionStatus.INCONCLUSIVE:
                inconclusive += 1
            elif outcome.status == ExecutionStatus.BLOCKED:
                blocked += 1
            elif outcome.status == ExecutionStatus.ERROR:
                failed += 1

        duration = time.time() - start_time

        # Build manifest
        manifest = ResearchRunManifest(
            run_id=run_id,
            timestamp=context.timestamp,
            engine_version=context.engine_version,
            question_bank_version=context.question_bank_version,
            questions_requested=len(questions),
            questions_executed=completed + inconclusive,
            questions_blocked=blocked,
            questions_failed=failed,
            questions_inconclusive=inconclusive,
            findings_generated=completed + inconclusive,
            anomalies_detected=sum(
                1 for o in outcomes
                if o.finding and o.finding.anomaly_view
            ),
            exceptional_views=sum(
                1 for o in outcomes
                if o.finding and o.finding.exceptional_view
            ),
            candidate_questions_generated=sum(
                len(o.finding.research_gaps) for o in outcomes
                if o.finding and o.finding.research_gaps
            ),
            population_versions=context.universe_versions,
            universe_versions=context.universe_versions,
            duration_seconds=round(duration, 2),
            executed_question_ids=[
                o.question_id for o in outcomes
                if o.status in (ExecutionStatus.COMPLETE, ExecutionStatus.INCONCLUSIVE)
            ],
            blocked_question_ids=[
                o.question_id for o in outcomes if o.status == ExecutionStatus.BLOCKED
            ],
            failed_question_ids=[
                o.question_id for o in outcomes if o.status == ExecutionStatus.ERROR
            ],
        )

        # Save manifest
        self._save_manifest(manifest)

        logger.info(
            f"[ORCHESTRATOR] Run {run_id} complete: "
            f"{completed} complete, {inconclusive} inconclusive, "
            f"{blocked} blocked, {failed} failed in {duration:.1f}s"
        )

        return manifest, outcomes

    def _execute_one(
        self,
        question: NewEngineQuestion,
        builders: dict[Universe, Any],
        context: RunContext,
    ) -> QuestionExecutionOutcome:
        """Execute a single question with full isolation."""
        qid = question.question_id

        try:
            # 1. Check if question is BLOCKED by contract
            if question.status == QuestionStatus.BLOCKED:
                return QuestionExecutionOutcome(
                    question_id=qid, status=ExecutionStatus.BLOCKED,
                    reason="Question status is BLOCKED in contract",
                )

            # 2. Resolve population
            population = self._resolve_population(question, builders)
            if population is None:
                return QuestionExecutionOutcome(
                    question_id=qid, status=ExecutionStatus.BLOCKED,
                    reason="Required universe not built or population unavailable",
                )

            # 3. Check minimum sample
            if len(population) < question.minimum_sample_size:
                # Still execute but will be INCONCLUSIVE
                pass

            # 4. Execute via runner
            result = self._runner.run_question(question, population, context)

            if not result.success or result.finding is None:
                return QuestionExecutionOutcome(
                    question_id=qid, status=ExecutionStatus.ERROR,
                    reason=result.error or "Runner returned no finding",
                )

            # 5. Classify outcome
            finding = result.finding
            if finding.confidence == "INSUFFICIENT" or finding.outcome == "INCONCLUSIVE":
                status = ExecutionStatus.INCONCLUSIVE
            elif finding.outcome == "ANALYSIS_FAILED":
                status = ExecutionStatus.ERROR
            else:
                status = ExecutionStatus.COMPLETE

            # 6. Save product
            self._product_mgr.save_finding(finding)

            return QuestionExecutionOutcome(
                question_id=qid, status=status, finding=finding,
            )

        except Exception as exc:
            logger.warning(f"[ORCHESTRATOR] {qid} failed: {exc}")
            return QuestionExecutionOutcome(
                question_id=qid, status=ExecutionStatus.ERROR,
                reason=f"{type(exc).__name__}: {exc}",
            )

    def _resolve_population(
        self,
        question: NewEngineQuestion,
        builders: dict[Universe, Any],
    ) -> list[dict[str, Any]] | None:
        """
        Resolve the appropriate population for a question.

        For single-universe questions: use the primary universe's ALL population.
        For cross-universe questions: use the primary (first) universe's records.
        """
        if not question.required_universes:
            return None

        primary_universe = question.required_universes[0]
        builder = builders.get(primary_universe)
        if builder is None or not builder.is_built:
            return None

        # Use the first declared population
        if question.required_populations:
            pop = question.required_populations[0]
            return builder.get_population(pop)

        # Fallback to all records
        return builder.records

    def _build_universes(self) -> dict[Universe, Any]:
        """Build all six universes from default paths, then enrich with outcomes."""
        from research_engine.v10.universes import (
            ExecutionUniverseBuilder,
            DecisionUniverseBuilder,
            MarketUniverseBuilder,
            StrategyUniverseBuilder,
            RiskUniverseBuilder,
            OutcomeUniverseBuilder,
        )
        from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment

        builders: dict[Universe, Any] = {}
        for UClass, utype in [
            (ExecutionUniverseBuilder, Universe.EXECUTION),
            (DecisionUniverseBuilder, Universe.DECISION),
            (MarketUniverseBuilder, Universe.MARKET),
            (StrategyUniverseBuilder, Universe.STRATEGY),
            (RiskUniverseBuilder, Universe.RISK),
        ]:
            try:
                b = UClass()
                b.build()
                builders[utype] = b
            except Exception as e:
                logger.warning(f"[ORCHESTRATOR] Failed to build {utype.value}: {e}")

        # Outcome enrichment: join r_multiple from Execution into other universes
        exe_builder = builders.get(Universe.EXECUTION)
        if exe_builder and exe_builder.is_built:
            enrichment = OutcomeEnrichment(exe_builder)
            enrichment.enrich_all(builders)

            # Build Outcome universe from completed executions
            try:
                outcome_builder = OutcomeUniverseBuilder(execution_builder=exe_builder)
                outcome_builder.build()
                builders[Universe.OUTCOME] = outcome_builder
            except Exception as e:
                logger.warning(f"[ORCHESTRATOR] Failed to build OUTCOME: {e}")

        return builders

    def _get_question_bank(self) -> tuple[NewEngineQuestion, ...]:
        from research_engine.v10.universes.question_bank import QUESTION_BANK
        return QUESTION_BANK

    def _save_manifest(self, manifest: ResearchRunManifest) -> None:
        """Save the run manifest to disk."""
        import json
        manifest_dir = Path("reports/research/runs")
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / f"{manifest.run_id}.json"
        path.write_text(
            json.dumps(manifest.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
