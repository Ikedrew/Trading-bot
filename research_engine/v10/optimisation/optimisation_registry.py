"""
Optimisation Bridge — Registry.

Stores hypotheses and candidates with status tracking.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.optimisation.models import (
    ResearchHypothesis, OptimisationCandidate, ValidationPlan,
)

logger = logging.getLogger(__name__)

_REGISTRY_DIR = "data/research/optimisation"


class OptimisationRegistry:
    """
    Central registry for hypotheses and optimisation candidates.

    Storage: data/research/optimisation/
    """

    def __init__(self, registry_dir: str | None = None):
        self._dir = Path(registry_dir or _REGISTRY_DIR)
        self._hypotheses: dict[str, ResearchHypothesis] = {}
        self._candidates: dict[str, OptimisationCandidate] = {}
        self._plans: dict[str, ValidationPlan] = {}

    # ─── HYPOTHESES ───────────────────────────────────────────

    def add_hypothesis(self, hypothesis: ResearchHypothesis) -> None:
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis

    def get_hypothesis(self, hypothesis_id: str) -> ResearchHypothesis | None:
        return self._hypotheses.get(hypothesis_id)

    def list_hypotheses(self, status: str | None = None) -> list[ResearchHypothesis]:
        if status:
            return [h for h in self._hypotheses.values() if h.status == status]
        return list(self._hypotheses.values())

    def update_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        h = self._hypotheses.get(hypothesis_id)
        if h:
            h.status = status

    # ─── CANDIDATES ───────────────────────────────────────────

    def add_candidate(self, candidate: OptimisationCandidate) -> None:
        self._candidates[candidate.candidate_id] = candidate

    def get_candidate(self, candidate_id: str) -> OptimisationCandidate | None:
        return self._candidates.get(candidate_id)

    def list_candidates(self, status: str | None = None) -> list[OptimisationCandidate]:
        if status:
            return [c for c in self._candidates.values() if c.status == status]
        return list(self._candidates.values())

    def update_candidate_status(self, candidate_id: str, status: str) -> None:
        c = self._candidates.get(candidate_id)
        if c:
            c.status = status

    # ─── VALIDATION PLANS ─────────────────────────────────────

    def add_plan(self, plan: ValidationPlan) -> None:
        self._plans[plan.candidate_id] = plan

    def get_plan(self, candidate_id: str) -> ValidationPlan | None:
        return self._plans.get(candidate_id)

    # ─── PERSISTENCE ──────────────────────────────────────────

    def save(self) -> str:
        """Persist registry to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "hypotheses": {k: v.to_dict() for k, v in self._hypotheses.items()},
            "candidates": {k: v.to_dict() for k, v in self._candidates.items()},
            "plans": {k: v.to_dict() for k, v in self._plans.items()},
        }
        path = self._dir / "registry.json"
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return str(path)

    def load(self) -> None:
        """Load registry from disk."""
        path = self._dir / "registry.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.get("hypotheses", {}).items():
                self._hypotheses[k] = ResearchHypothesis(**{
                    f: v[f] for f in ResearchHypothesis.__dataclass_fields__ if f in v
                })
            for k, v in data.get("candidates", {}).items():
                self._candidates[k] = OptimisationCandidate(**{
                    f: v[f] for f in OptimisationCandidate.__dataclass_fields__ if f in v
                })
            for k, v in data.get("plans", {}).items():
                self._plans[k] = ValidationPlan(**{
                    f: v[f] for f in ValidationPlan.__dataclass_fields__ if f in v
                })
        except (json.JSONDecodeError, TypeError):
            logger.warning("[OPT_REGISTRY] Failed to load registry")
