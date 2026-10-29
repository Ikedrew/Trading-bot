"""
Campaign Engine — Memory (research history persistence).

Remembers previous campaign executions for longitudinal tracking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

_MEMORY_DIR = "data/research/campaign_memory"


class CampaignMemory:
    """Stores and retrieves campaign execution history."""

    def __init__(self, memory_dir: str | None = None):
        self._dir = Path(memory_dir or _MEMORY_DIR)

    def record(self, campaign_id: str, summary: dict[str, Any]) -> None:
        """Record a campaign execution."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{campaign_id}.jsonl"
        entry = {"timestamp": timestamp_now(), **summary}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def get_history(self, campaign_id: str) -> list[dict[str, Any]]:
        """Get all previous runs for a campaign."""
        path = self._dir / f"{campaign_id}.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries

    def previous_run_count(self, campaign_id: str) -> int:
        return len(self.get_history(campaign_id))

    def latest(self, campaign_id: str) -> dict[str, Any] | None:
        history = self.get_history(campaign_id)
        return history[-1] if history else None

    def summary(self) -> dict[str, Any]:
        """Summary of all campaign history."""
        if not self._dir.exists():
            return {}
        result = {}
        for f in self._dir.glob("*.jsonl"):
            cid = f.stem
            history = self.get_history(cid)
            result[cid] = {
                "previous_runs": len(history),
                "last_run": history[-1].get("timestamp", "") if history else "",
            }
        return result
