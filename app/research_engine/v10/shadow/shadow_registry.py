"""
Shadow Optimisation — Persistent registry for active/completed shadow tests.

Storage: data/research/shadow/
Survives process restarts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.shadow.models import ShadowCandidate, ShadowComparison, ShadowStatus

logger = logging.getLogger(__name__)

_SHADOW_DIR = "data/research/shadow"


class ShadowRegistry:
    """Persistent storage for shadow candidates and their comparisons."""

    def __init__(self, shadow_dir: str | None = None):
        self._dir = Path(shadow_dir or _SHADOW_DIR)
        self._candidates: dict[str, ShadowCandidate] = {}
        self._comparisons: dict[str, list[ShadowComparison]] = {}
        self._load()

    # ─── CANDIDATES ───────────────────────────────────────────

    def add_candidate(self, candidate: ShadowCandidate) -> None:
        self._candidates[candidate.shadow_id] = candidate
        self._comparisons.setdefault(candidate.shadow_id, [])
        self._persist_candidates()

    def get_candidate(self, shadow_id: str) -> ShadowCandidate | None:
        return self._candidates.get(shadow_id)

    def list_active(self) -> list[ShadowCandidate]:
        return [c for c in self._candidates.values() if c.status == ShadowStatus.ACTIVE]

    def list_all(self) -> list[ShadowCandidate]:
        return list(self._candidates.values())

    def update_status(self, shadow_id: str, status: str) -> None:
        c = self._candidates.get(shadow_id)
        if c:
            c.status = status
            self._persist_candidates()

    # ─── COMPARISONS ──────────────────────────────────────────

    def add_comparison(self, comparison: ShadowComparison) -> None:
        self._comparisons.setdefault(comparison.shadow_id, []).append(comparison)
        # Update metrics
        c = self._candidates.get(comparison.shadow_id)
        if c:
            c.metrics["completed_comparisons"] = len(self._comparisons[comparison.shadow_id])
        self._persist_comparisons(comparison.shadow_id)
        self._persist_candidates()

    def get_comparisons(self, shadow_id: str) -> list[ShadowComparison]:
        return self._comparisons.get(shadow_id, [])

    # ─── PERSISTENCE ──────────────────────────────────────────

    def _persist_candidates(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / "candidates.jsonl"
        lines = [json.dumps(c.to_dict(), default=str) for c in self._candidates.values()]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    def _persist_comparisons(self, shadow_id: str) -> None:
        comp_dir = self._dir / "comparisons"
        comp_dir.mkdir(parents=True, exist_ok=True)
        path = comp_dir / f"{shadow_id}.jsonl"
        lines = [json.dumps(c.to_dict(), default=str) for c in self._comparisons.get(shadow_id, [])]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    def _load(self) -> None:
        # Load candidates
        path = self._dir / "candidates.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        c = ShadowCandidate.from_dict(json.loads(line))
                        self._candidates[c.shadow_id] = c
                        self._comparisons.setdefault(c.shadow_id, [])
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Load comparisons
        comp_dir = self._dir / "comparisons"
        if comp_dir.exists():
            for f in comp_dir.glob("*.jsonl"):
                shadow_id = f.stem
                self._comparisons.setdefault(shadow_id, [])
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            self._comparisons[shadow_id].append(
                                ShadowComparison.from_dict(json.loads(line))
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass
