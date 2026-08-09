"""
Validation Lab — Replay Engine.

Evaluates performance of historical trades under modified parameters.
The baseline dataset remains frozen — candidate changes are applied
as filters/transformations on the existing research universe.

For stop/risk parameter changes: recalculates R-multiple using new parameters.
For filter changes: includes/excludes trades based on new criteria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import compute_metrics

_UNIVERSE_FILE = "data/research/research_universe.jsonl"


class ReplayEngine:
    """
    Replays historical data with candidate parameter modifications.

    Supports:
        - Stop distance changes (recalculate R from new stop)
        - Filter changes (include/exclude based on new criteria)
        - Threshold changes (re-evaluate score/confidence gates)
    """

    def __init__(self, universe_file: str | None = None):
        self._universe_file = Path(universe_file or _UNIVERSE_FILE)
        self._events: list[dict] | None = None

    @property
    def events(self) -> list[dict]:
        if self._events is None:
            self._events = self._load()
        return self._events

    def baseline_metrics(self, filters: dict[str, str] | None = None) -> dict[str, Any]:
        """Compute baseline metrics from the frozen universe."""
        population = self._apply_filters(self.events, filters)
        return self._compute(population)

    def candidate_metrics(
        self,
        changes: dict[str, Any],
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Compute candidate metrics by applying proposed changes.

        Change types supported:
            - "stop_multiplier": float — adjusts R-multiple by ratio
            - "score_threshold": float — filters out trades below threshold
            - "regime_filter": str — only include trades in specified regime
            - "session_filter": str — only include trades in specified session
        """
        population = self._apply_filters(self.events, filters)
        modified = self._apply_changes(population, changes)
        return self._compute(modified)

    def _apply_filters(self, events: list[dict], filters: dict[str, str] | None) -> list[dict]:
        """Apply segmentation filters to population."""
        if not filters:
            return events

        from research_engine.v10.segmentation_engine import ResearchSegmenter
        seg = ResearchSegmenter(universe_file=str(self._universe_file))
        seg._events = events  # Use loaded events directly
        return seg.filter(**filters)

    def _apply_changes(self, events: list[dict], changes: dict[str, Any]) -> list[dict]:
        """Apply candidate parameter changes to the population."""
        result = []

        for e in events:
            modified = _deep_copy_event(e)
            ex = modified.get("execution", {})
            dec = modified.get("decision", {})
            mkt = modified.get("market", {})

            # Stop multiplier: adjusts R-multiple proportionally
            if "stop_multiplier" in changes:
                ratio = changes["stop_multiplier"]
                if ratio > 0:
                    original_r = ex.get("r_multiple", 0)
                    # Wider stop = smaller R magnitude (both wins and losses)
                    ex["r_multiple"] = round(original_r / ratio, 4)

            # Score threshold: exclude trades below new threshold
            if "score_threshold" in changes:
                threshold = changes["score_threshold"]
                if (dec.get("score") or 0) < threshold:
                    continue  # Trade would not have been taken

            # Regime filter: only include specific regime
            if "regime_filter" in changes:
                if mkt.get("regime", "").upper() != changes["regime_filter"].upper():
                    continue

            # Session filter
            if "session_filter" in changes:
                if mkt.get("session", "").upper() != changes["session_filter"].upper():
                    continue

            result.append(modified)

        return result

    def _compute(self, events: list[dict]) -> dict[str, Any]:
        """Compute performance metrics from events."""
        if not events:
            return {"count": 0, "sample_size": 0}

        flat = []
        for e in events:
            ex = e.get("execution", {})
            flat.append({
                "realised_r": ex.get("r_multiple", 0),
                "final_pnl": ex.get("net_realised_pnl", 0),
            })

        metrics = compute_metrics(flat)
        metrics["sample_size"] = metrics["count"]
        return metrics

    def _load(self) -> list[dict]:
        if not self._universe_file.exists():
            return []
        events = []
        for line in self._universe_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events


def _deep_copy_event(event: dict) -> dict:
    """Create a shallow-enough copy to allow modification without affecting original."""
    return {
        "trade_id": event.get("trade_id"),
        "execution": dict(event.get("execution", {})),
        "decision": dict(event.get("decision", {})),
        "market": dict(event.get("market", {})),
        "strategy": dict(event.get("strategy", {})),
        "quality": dict(event.get("quality", {})),
    }
