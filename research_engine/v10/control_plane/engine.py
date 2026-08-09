"""
Control Plane Engine — State management and indexing.

Coordinates (but does NOT absorb) the universe builders, question bank,
population resolver, and research products.

Responsibilities:
    - Build and index the current state of all universes
    - Index all questions and their lifecycle status
    - Track research runs and their manifests
    - Provide navigation from control centre to individual products
    - Persist and restore control plane state
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.control_plane.models import (
    ControlPlaneState,
    GrowthLimits,
    QuestionLifecycle,
    QuestionProductIndex,
    ResearchRunManifest,
    UniverseHealth,
)
from research_engine.v10.universes.models import Universe

logger = logging.getLogger(__name__)

_STATE_FILE = Path("reports/research/control_plane_state.json")
_QUESTIONS_DIR = Path("reports/research/questions")


class ControlPlaneEngine:
    """
    Central coordination engine for the research system.

    Does NOT perform research analysis. Only indexes and orchestrates.
    """

    def __init__(
        self,
        state_file: Path | str | None = None,
        questions_dir: Path | str | None = None,
    ):
        self._state_file = Path(state_file) if state_file else _STATE_FILE
        self._questions_dir = Path(questions_dir) if questions_dir else _QUESTIONS_DIR
        self._state = ControlPlaneState()
        self._runs: list[ResearchRunManifest] = []

    @property
    def state(self) -> ControlPlaneState:
        return self._state

    # ─── UNIVERSE INDEXING ────────────────────────────────────────────────────

    def index_universes(
        self,
        builders: dict[Universe, Any] | None = None,
    ) -> None:
        """
        Index universe health from built universe builders.

        Args:
            builders: dict of Universe → built UniverseBuilder instances.
                      If None, builds from default paths.
        """
        if builders is None:
            builders = self._build_default_universes()

        self._state.universes = []
        for universe, builder in builders.items():
            if not builder.is_built:
                continue
            meta = builder.metadata
            self._state.universes.append(UniverseHealth(
                universe_id=universe.value,
                status="VALID" if meta.record_count > 0 else "EMPTY",
                record_count=meta.record_count,
                population_count=len(meta.populations_available),
                last_build_timestamp=meta.generation_timestamp,
                content_hash=meta.content_hash,
            ))

    def _build_default_universes(self) -> dict[Universe, Any]:
        """Build all four universes from default paths."""
        from research_engine.v10.universes import (
            ExecutionUniverseBuilder,
            DecisionUniverseBuilder,
            MarketUniverseBuilder,
            StrategyUniverseBuilder,
        )
        builders = {}
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
                logger.warning(f"[CONTROL] Failed to build {utype.value}: {e}")
        return builders

    # ─── QUESTION INDEXING ────────────────────────────────────────────────────

    def index_questions(
        self,
        questions: tuple[Any, ...] | None = None,
    ) -> None:
        """
        Index all questions from the question bank, including product health.

        Args:
            questions: Tuple of NewEngineQuestion instances.
                       If None, loads from the canonical question bank.
        """
        if questions is None:
            from research_engine.v10.universes.question_bank import QUESTION_BANK
            questions = QUESTION_BANK

        from research_engine.v10.control_plane.question_products import (
            QuestionProductManager,
        )
        product_mgr = QuestionProductManager(base_dir=self._questions_dir)

        self._state.questions = []
        active = 0
        blocked = 0

        for q in questions:
            from research_engine.v10.universes.models import QuestionStatus
            if q.status == QuestionStatus.BLOCKED:
                lifecycle = QuestionLifecycle.ACTIVE
                blocked += 1
            else:
                lifecycle = QuestionLifecycle.ACTIVE
                active += 1

            # Check product health
            health = product_mgr.product_health(q.question_id)
            has_run = health["has_latest_finding"]
            last_run_ts = ""
            finding_outcome = ""
            history_count = health["history_count"]

            if has_run:
                lifecycle = QuestionLifecycle.RUN
                finding_outcome = health["latest_outcome"]
                latest = product_mgr.get_latest_finding(q.question_id)
                if latest:
                    last_run_ts = latest.get("run_timestamp", "")

            # Angle string
            angle_parts = [u.value[:4] for u in q.required_universes]
            angle_str = "+".join(angle_parts)

            self._state.questions.append(QuestionProductIndex(
                question_id=q.question_id,
                title=q.title,
                lifecycle=lifecycle,
                angle_primary=angle_str,
                last_run_timestamp=last_run_ts,
                latest_finding_path=str(
                    self._questions_dir / q.question_id / "latest.json"
                ) if has_run else "",
                history_count=history_count,
                finding_outcome=finding_outcome,
                blocked_reason=(
                    q.status.value if q.status != QuestionStatus.READY else ""
                ),
            ))

        self._state.questions_active = active
        self._state.questions_blocked = blocked
        self._state.questions_run = sum(
            1 for q in self._state.questions
            if q.lifecycle == QuestionLifecycle.RUN
        )

    # ─── RUN MANAGEMENT ───────────────────────────────────────────────────────

    def register_run(self, manifest: ResearchRunManifest) -> None:
        """Register a completed research run."""
        self._runs.append(manifest)
        self._state.latest_run = manifest
        self._state.last_run_id = manifest.run_id
        self._state.last_run_timestamp = manifest.timestamp

    # ─── STATE PERSISTENCE ────────────────────────────────────────────────────

    def save_state(self) -> None:
        """Persist the current control plane state to JSON."""
        self._state.last_updated = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(self._state.to_dict(), f, indent=2, default=str)

    def load_state(self) -> bool:
        """Load control plane state from disk. Returns True if loaded."""
        if not self._state_file.exists():
            return False
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._state.engine_version = data.get("engine_version", "1.0.0")
            self._state.last_updated = data.get("last_updated", "")
            self._state.last_run_id = data.get("last_run_id", "")
            self._state.last_run_timestamp = data.get("last_run_timestamp", "")
            self._state.questions_active = data.get("questions_active", 0)
            self._state.questions_blocked = data.get("questions_blocked", 0)
            self._state.questions_run = data.get("questions_run", 0)
            return True
        except Exception:
            return False

    # ─── NAVIGATION ───────────────────────────────────────────────────────────

    def get_questions_by_angle(self, angle: str) -> list[QuestionProductIndex]:
        """Get all questions that involve a specific angle."""
        return [
            q for q in self._state.questions
            if angle.upper()[:4] in q.angle_primary
        ]

    def get_questions_by_lifecycle(
        self, lifecycle: QuestionLifecycle
    ) -> list[QuestionProductIndex]:
        """Get all questions in a specific lifecycle state."""
        return [q for q in self._state.questions if q.lifecycle == lifecycle]

    def get_question_product_path(self, question_id: str) -> Path:
        """Get the filesystem path for a question's product directory."""
        return self._questions_dir / question_id
