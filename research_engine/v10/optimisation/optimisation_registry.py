"""
Optimisation Bridge — Registry.

Stores hypotheses and candidates with status tracking.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from research_engine.v10.optimisation.models import (
    ResearchHypothesis, OptimisationCandidate, ValidationPlan,
)

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
        existing = self._hypotheses.get(hypothesis.hypothesis_id)
        if existing is not None and existing.to_dict() != hypothesis.to_dict():
            raise ValueError("HYPOTHESIS_IDENTITY_COLLISION")
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
        existing = self._candidates.get(candidate.candidate_id)
        if existing is not None and existing.to_dict() != candidate.to_dict():
            raise ValueError("CANDIDATE_IDENTITY_COLLISION")
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
            if c.status == status:
                return
            c.status = status
            from research_engine.v10.base import timestamp_now
            c.status_history.append({"status": status, "timestamp": timestamp_now()})

    def bind_shadow_candidate(
        self, candidate_id: str, *, policy_id: str, treatment_hash: str,
        binding: dict[str, Any],
    ) -> None:
        """Record an explicit prospective shadow binding; never live approval."""
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise ValueError("CANDIDATE_NOT_FOUND")
        if candidate.shadow_binding:
            if candidate.shadow_binding != binding:
                raise ValueError("SHADOW_BINDING_CONFLICT")
            return
        if candidate.status not in {"FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE"}:
            raise ValueError("CANDIDATE_NOT_FORWARD_VALIDATED")
        if not policy_id or not treatment_hash or binding.get("live_approved") is not False:
            raise ValueError("SHADOW_BINDING_IDENTITY_INVALID")
        candidate.policy_id = policy_id
        candidate.treatment_hash = treatment_hash
        candidate.shadow_binding = dict(binding)
        if candidate.status != "SHADOW_VALIDATION_ACTIVE":
            candidate.status = "SHADOW_VALIDATION_ACTIVE"
            candidate.status_history.append({
                "status": candidate.status,
                "timestamp": str(binding.get("activated_at") or ""),
                "evidence": str(binding.get("forward_validation_record") or ""),
            })

    # ─── VALIDATION PLANS ─────────────────────────────────────

    def add_plan(self, plan: ValidationPlan) -> None:
        self._plans[plan.candidate_id] = plan

    def get_plan(self, candidate_id: str) -> ValidationPlan | None:
        return self._plans.get(candidate_id)

    # ─── PERSISTENCE ──────────────────────────────────────────

    def save(self) -> str:
        """Persist registry atomically; failures are never swallowed."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "hypotheses": {k: v.to_dict() for k, v in self._hypotheses.items()},
            "candidates": {k: v.to_dict() for k, v in self._candidates.items()},
            "plans": {k: v.to_dict() for k, v in self._plans.items()},
        }
        path = self._dir / "registry.json"
        temporary = path.with_name(path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, sort_keys=True, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return str(path)

    def load(self) -> None:
        """Load registry from disk."""
        path = self._dir / "registry.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {"hypotheses", "candidates", "plans"}:
                raise ValueError("OPTIMISATION_REGISTRY_STRUCTURE_INVALID")
            if not all(isinstance(data[name], dict) for name in data):
                raise ValueError("OPTIMISATION_REGISTRY_SECTION_INVALID")
            for k, v in data.get("hypotheses", {}).items():
                row = ResearchHypothesis(**{
                    f: v[f] for f in ResearchHypothesis.__dataclass_fields__ if f in v
                })
                if row.hypothesis_id != k:
                    raise ValueError("HYPOTHESIS_REGISTRY_KEY_MISMATCH")
                self._hypotheses[k] = row
            for k, v in data.get("candidates", {}).items():
                row = OptimisationCandidate(**{
                    f: v[f] for f in OptimisationCandidate.__dataclass_fields__ if f in v
                })
                if row.candidate_id != k:
                    raise ValueError("CANDIDATE_REGISTRY_KEY_MISMATCH")
                self._candidates[k] = row
            for k, v in data.get("plans", {}).items():
                row = ValidationPlan(**{
                    f: v[f] for f in ValidationPlan.__dataclass_fields__ if f in v
                })
                if row.candidate_id != k or k not in self._candidates:
                    raise ValueError("VALIDATION_PLAN_REGISTRY_KEY_MISMATCH")
                self._plans[k] = row
            if any(
                row.hypothesis_id and row.hypothesis_id not in self._hypotheses
                for row in self._candidates.values()
            ):
                raise ValueError("CANDIDATE_HYPOTHESIS_DEPENDENCY_MISSING")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("OPTIMISATION_REGISTRY_CORRUPT") from exc
