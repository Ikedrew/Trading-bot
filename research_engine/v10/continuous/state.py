"""
Continuous Research State.

Persistent state for the continuous research cycle.
Tracks what has been done, what changed, and where the cycle is.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIR = Path("reports/research/continuous")
_STATE_FILE = _STATE_DIR / "state.json"


class TriggerStatus(str, Enum):
    NO_NEW_DATA = "NO_NEW_DATA"
    NEW_DATA_BELOW_THRESHOLD = "NEW_DATA_BELOW_THRESHOLD"
    NEW_DATA_READY = "NEW_DATA_READY"
    FORCE_RUN = "FORCE_RUN"
    BLOCKED = "BLOCKED"


class CycleStatus(str, Enum):
    DETECTED = "DETECTED"
    RESEARCHING = "RESEARCHING"
    ANALYSING = "ANALYSING"
    UPDATING_KNOWLEDGE = "UPDATING_KNOWLEDGE"
    GENERATING_PROPOSALS = "GENERATING_PROPOSALS"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NO_ACTION = "NO_ACTION"


@dataclass
class CycleState:
    """Persistent state of one continuous research cycle."""
    cycle_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    trigger_status: str = TriggerStatus.NO_NEW_DATA.value
    trigger_reason: str = ""

    # Data state
    previous_population_sizes: dict[str, int] = field(default_factory=dict)
    current_population_sizes: dict[str, int] = field(default_factory=dict)
    data_changes: dict[str, Any] = field(default_factory=dict)

    # Research
    research_run_id: str = ""
    finding_count: int = 0
    feedback_count: int = 0
    knowledge_updates: int = 0

    # Proposals
    proposal_count: int = 0
    experiment_count: int = 0
    validation_count: int = 0
    promotion_eligible_count: int = 0

    # Status
    status: str = CycleStatus.DETECTED.value
    blocked_reason: str = ""
    stages_completed: list[str] = field(default_factory=list)

    # Governance
    governance_note: str = (
        "This continuous research cycle analyses evidence and produces "
        "governed research artifacts. It cannot directly modify, deploy, "
        "activate, or execute changes to the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trigger_status": self.trigger_status,
            "trigger_reason": self.trigger_reason,
            "previous_population_sizes": self.previous_population_sizes,
            "current_population_sizes": self.current_population_sizes,
            "data_changes": self.data_changes,
            "research_run_id": self.research_run_id,
            "finding_count": self.finding_count,
            "feedback_count": self.feedback_count,
            "knowledge_updates": self.knowledge_updates,
            "proposal_count": self.proposal_count,
            "experiment_count": self.experiment_count,
            "validation_count": self.validation_count,
            "promotion_eligible_count": self.promotion_eligible_count,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "stages_completed": self.stages_completed,
            "governance_note": self.governance_note,
        }


class CycleStateStore:
    """Persists continuous research cycle state."""

    def __init__(self, state_dir: Path | str | None = None):
        self._dir = Path(state_dir) if state_dir else _STATE_DIR

    def save(self, state: CycleState) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "history").mkdir(exist_ok=True)

        state_dict = state.to_dict()

        # Latest
        latest = self._dir / "state.json"
        latest.write_text(json.dumps(state_dict, indent=2, default=str), encoding="utf-8")

        # Immutable history
        if state.cycle_id:
            hist = self._dir / "history" / f"{state.cycle_id}.json"
            if not hist.exists():
                hist.write_text(json.dumps(state_dict, indent=2, default=str), encoding="utf-8")

        return latest

    def load_latest(self) -> CycleState | None:
        latest = self._dir / "state.json"
        if not latest.exists():
            return None
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            return self._reconstruct(data)
        except Exception:
            return None

    def load_history(self) -> list[CycleState]:
        history_dir = self._dir / "history"
        if not history_dir.exists():
            return []
        states = []
        for f in sorted(history_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = self._reconstruct(data)
                if s:
                    states.append(s)
            except Exception:
                continue
        return states

    def _reconstruct(self, data: dict[str, Any]) -> CycleState:
        return CycleState(
            cycle_id=data.get("cycle_id", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            trigger_status=data.get("trigger_status", ""),
            trigger_reason=data.get("trigger_reason", ""),
            previous_population_sizes=data.get("previous_population_sizes", {}),
            current_population_sizes=data.get("current_population_sizes", {}),
            data_changes=data.get("data_changes", {}),
            research_run_id=data.get("research_run_id", ""),
            finding_count=data.get("finding_count", 0),
            feedback_count=data.get("feedback_count", 0),
            knowledge_updates=data.get("knowledge_updates", 0),
            proposal_count=data.get("proposal_count", 0),
            experiment_count=data.get("experiment_count", 0),
            validation_count=data.get("validation_count", 0),
            promotion_eligible_count=data.get("promotion_eligible_count", 0),
            status=data.get("status", ""),
            blocked_reason=data.get("blocked_reason", ""),
            stages_completed=data.get("stages_completed", []),
        )
