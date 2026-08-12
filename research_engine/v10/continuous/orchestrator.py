"""
Continuous Research Orchestrator.

Composes existing Items 8-11 into a repeatable, governed, resumable
research cycle.

NEVER modifies the trading bot. Stops at HUMAN_GOVERNANCE_REQUIRED.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.continuous.state import (
    CycleState,
    CycleStateStore,
    CycleStatus,
    TriggerStatus,
)

logger = logging.getLogger(__name__)

_QUESTIONS_DIR = Path("reports/research/questions")
_RUNS_DIR = Path("reports/research/runs")
_MIN_NEW_RECORDS = 5  # Provisional policy: minimum new records to trigger


class ContinuousResearchOrchestrator:
    """
    Orchestrates the continuous research lifecycle.

    Lifecycle:
        detect → research → feedback → knowledge → proposals →
        experiments → validation → promotion gate → STOP

    Read-only with respect to the trading system.
    """

    def __init__(self, state_dir: Path | str | None = None):
        self._store = CycleStateStore(state_dir)

    # ─── PLAN (read-only) ─────────────────────────────────────────────────────

    def plan(self) -> CycleState:
        """
        Determine what would happen next WITHOUT executing it.

        Read-only. Does not mutate research state.
        """
        state = CycleState(
            cycle_id=f"plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # Detect data readiness
        trigger_status, trigger_reason, pop_sizes = self._detect_data_changes()
        state.trigger_status = trigger_status
        state.trigger_reason = trigger_reason
        state.current_population_sizes = pop_sizes

        if trigger_status == TriggerStatus.NO_NEW_DATA.value:
            state.status = CycleStatus.NO_ACTION.value
        elif trigger_status == TriggerStatus.NEW_DATA_BELOW_THRESHOLD.value:
            state.status = CycleStatus.NO_ACTION.value
        elif trigger_status == TriggerStatus.BLOCKED.value:
            state.status = CycleStatus.BLOCKED.value
            state.blocked_reason = trigger_reason
        else:
            state.status = CycleStatus.DETECTED.value

        return state

    # ─── RUN CYCLE ────────────────────────────────────────────────────────────

    def run_cycle(self, force: bool = False) -> CycleState:
        """
        Execute the next continuous research cycle.

        Stages:
            1. Data detection
            2. Research execution
            3. Feedback generation
            4. Knowledge update
            5. Proposal discovery
            6. Experiment/validation (where executable)
            7. Promotion gate

        Each stage is isolated. A failure preserves previous stage results.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = CycleState(
            cycle_id=f"cycle_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}",
            started_at=now,
        )

        try:
            # Stage 1: Data detection
            trigger_status, trigger_reason, pop_sizes = self._detect_data_changes()
            state.trigger_status = trigger_status if not force else TriggerStatus.FORCE_RUN.value
            state.trigger_reason = trigger_reason if not force else "Forced by user"
            state.current_population_sizes = pop_sizes

            if not force and trigger_status in (TriggerStatus.NO_NEW_DATA.value, TriggerStatus.NEW_DATA_BELOW_THRESHOLD.value):
                state.status = CycleStatus.NO_ACTION.value
                state.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._store.save(state)
                return state

            if trigger_status == TriggerStatus.BLOCKED.value and not force:
                state.status = CycleStatus.BLOCKED.value
                state.blocked_reason = trigger_reason
                state.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._store.save(state)
                return state

            # Stage 2: Research run
            state.status = CycleStatus.RESEARCHING.value
            run_id = self._run_research()
            state.research_run_id = run_id
            state.stages_completed.append("RESEARCH")

            # Stage 3: Feedback
            state.status = CycleStatus.ANALYSING.value
            findings = self._load_all_findings()
            state.finding_count = len(findings)

            feedbacks = self._generate_feedback(findings)
            state.feedback_count = len(feedbacks)
            state.stages_completed.append("FEEDBACK")

            # Stage 4: Knowledge
            state.status = CycleStatus.UPDATING_KNOWLEDGE.value
            knowledge_count = self._update_knowledge(findings)
            state.knowledge_updates = knowledge_count
            state.stages_completed.append("KNOWLEDGE")

            # Stage 5: Proposals
            state.status = CycleStatus.GENERATING_PROPOSALS.value
            proposal_count = self._discover_proposals(feedbacks)
            state.proposal_count = proposal_count
            state.stages_completed.append("PROPOSALS")

            # Stage 6: Validation (only for existing executable experiments)
            state.status = CycleStatus.VALIDATING.value
            exp_count, val_count, promo_count = self._run_validations()
            state.experiment_count = exp_count
            state.validation_count = val_count
            state.promotion_eligible_count = promo_count
            state.stages_completed.append("VALIDATION")

            # Complete
            state.status = CycleStatus.COMPLETED.value
            state.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        except Exception as e:
            state.status = CycleStatus.FAILED.value
            state.blocked_reason = f"{type(e).__name__}: {e}"
            state.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.error(f"[CONTINUOUS] Cycle failed: {e}")

        self._store.save(state)
        return state

    # ─── STAGE IMPLEMENTATIONS ────────────────────────────────────────────────

    def _detect_data_changes(self) -> tuple[str, str, dict[str, int]]:
        """Detect whether new research-relevant data exists."""
        # Get current population sizes from latest run manifest
        current_sizes: dict[str, int] = {}
        latest_run = self._load_latest_run_manifest()

        if latest_run:
            # Use question-level sample sizes as proxy
            current_sizes["latest_run_questions"] = latest_run.get("questions_executed", 0)

        # Get execution dataset size
        exe_path = Path("data/research/research_universe.jsonl")
        if exe_path.exists():
            with open(exe_path, encoding="utf-8") as f:
                current_sizes["execution_records"] = sum(1 for line in f if line.strip())

        # Compare with previous state
        prev = self._store.load_latest()
        prev_sizes = prev.current_population_sizes if prev else {}

        if not prev_sizes:
            return TriggerStatus.NEW_DATA_READY.value, "First research cycle (no previous state)", current_sizes

        # Check for meaningful changes
        prev_exe = prev_sizes.get("execution_records", 0)
        curr_exe = current_sizes.get("execution_records", 0)
        new_records = curr_exe - prev_exe

        if new_records >= _MIN_NEW_RECORDS:
            return (
                TriggerStatus.NEW_DATA_READY.value,
                f"Execution population increased from {prev_exe} to {curr_exe} (+{new_records} records)",
                current_sizes,
            )
        elif new_records > 0:
            return (
                TriggerStatus.NEW_DATA_BELOW_THRESHOLD.value,
                f"Only {new_records} new records (threshold: {_MIN_NEW_RECORDS})",
                current_sizes,
            )
        else:
            return TriggerStatus.NO_NEW_DATA.value, "No new execution records detected", current_sizes

    def _run_research(self) -> str:
        """Execute the research bank using existing orchestrator."""
        from research_engine.v10.runner.orchestrator import ResearchExecutionOrchestrator
        orch = ResearchExecutionOrchestrator()
        manifest, _ = orch.execute_all()
        return manifest.run_id

    def _load_all_findings(self) -> list[dict[str, Any]]:
        """Load all latest findings."""
        findings = []
        if _QUESTIONS_DIR.exists():
            for qdir in sorted(_QUESTIONS_DIR.iterdir()):
                latest = qdir / "latest.json"
                if latest.exists():
                    try:
                        findings.append(json.loads(latest.read_text(encoding="utf-8")))
                    except Exception:
                        continue
        return findings

    def _generate_feedback(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Generate feedback from findings using Item 9."""
        from research_engine.v10.feedback.generator import FeedbackGenerator
        from research_engine.v10.feedback.persistence import FeedbackStore

        gen = FeedbackGenerator()
        feedbacks = gen.from_run(findings)
        store = FeedbackStore()
        store.save_batch(feedbacks)
        return [fb.to_dict() for fb in feedbacks]

    def _update_knowledge(self, findings: list[dict[str, Any]]) -> int:
        """Update knowledge state using Item 10."""
        from research_engine.v10.knowledge.engine import KnowledgeEngine
        from research_engine.v10.knowledge.store import KnowledgeStore

        engine = KnowledgeEngine()
        items = engine.synthesise_from_findings(findings)
        store = KnowledgeStore()
        store.save_batch(items)
        return len(items)

    def _discover_proposals(self, feedbacks: list[dict[str, Any]]) -> int:
        """Discover proposal-eligible feedback and create proposals."""
        from research_engine.v10.proposals.generator import ProposalFactory
        from research_engine.v10.proposals.store import ProposalStore

        factory = ProposalFactory()
        store = ProposalStore()
        count = 0

        for fb in feedbacks:
            if fb.get("proposal_eligible"):
                proposal = factory.from_feedback(fb)
                store.save_proposal(proposal.to_dict())
                count += 1

        return count

    def _run_validations(self) -> tuple[int, int, int]:
        """Run available experiments/validations. Returns (experiments, validations, promotions)."""
        # In the current implementation, experiments require explicit candidate
        # configuration and population filters which are not automatically derivable.
        # This stage is a placeholder that counts existing validated proposals.
        from research_engine.v10.proposals.store import ProposalStore
        store = ProposalStore()
        proposals = store.list_proposals()

        validated = 0
        promoted = 0
        for pid in proposals:
            v = store.load_validation(pid)
            if v and v.get("status") == "VALIDATED":
                validated += 1
            p = store.load_promotion(pid)
            if p and p.get("eligible"):
                promoted += 1

        return 0, validated, promoted  # 0 new experiments in automatic mode

    def _load_latest_run_manifest(self) -> dict[str, Any] | None:
        if not _RUNS_DIR.exists():
            return None
        runs = sorted(_RUNS_DIR.glob("*.json"), reverse=True)
        if not runs:
            return None
        try:
            return json.loads(runs[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    # ─── STATUS ───────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Get current continuous research state."""
        state = self._store.load_latest()
        if state:
            return state.to_dict()
        return {"status": "NO_PREVIOUS_CYCLE", "message": "No continuous research cycle has been run yet."}

    def history(self) -> list[dict[str, Any]]:
        """Get all historical cycle states."""
        return [s.to_dict() for s in self._store.load_history()]
